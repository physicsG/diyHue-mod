import requests
import logManager
logging = logManager.logger.get_logger(__name__)

def set_light(light, data):
    """
    1) For each linked child, figure out the child's IP, version, etc.
    2) Batch them by IP, then do a single request per IP.
    """

    # If the "virtual" light has no children, do nothing
    if "linked_lights" not in light.protocol_cfg:
        logging.warning(f"[virtual] No 'linked_lights' configured for {light.name}.")
        return

    # We'll track sub-light updates in a dict keyed by the child's (ip, version, mac)
    # or possibly just (ip). Here we assume (ip).
    batch_updates = {}  # e.g. { "192.168.2.60:80": {"lights": {1: {...}, 2: {...}}} }

    # Mark the virtual parent as unreachable until proven otherwise
    light.state["reachable"] = False

    # STEP A: Collect updates
    from configManager.configHandler import bridgeConfig
    conf = bridgeConfig.yaml_config
    child_ids = light.protocol_cfg["linked_lights"]

    for child_id in child_ids:
        child = conf["lights"].get(child_id)
        if not child:
            continue

        # In a normal approach, you'd do child.setV1State(...). But that can spawn multiple requests.
        # Instead, we'll parse the new_state ourselves & apply it to each child's sub-light data.

        ip = child.protocol_cfg.get("ip")
        light_nr = child.protocol_cfg.get("light_nr")

        if not ip or not light_nr:
            # fallback if missing something
            continue

        # We'll store minimal subset of fields. 
        # If new_state includes "on", "bri", "xy", etc., put them in a sub-dict
        # for example:
        sub_light_data = {}

        for k in ["on", "bri", "hue", "sat", "xy", "ct", "colormode"]:
            if k in data:
                sub_light_data[k] = data[k]

        # Add to batch dict
        if ip not in batch_updates:
            batch_updates[ip] = { "lights": {} }
        batch_updates[ip]["lights"][light_nr] = sub_light_data

    # STEP B: Send each batch in a single PUT to the device’s /state endpoint
    for device_ip, data_to_send in batch_updates.items():
        try:
            # e.g. requests.put(f"http://{device_ip}/state", json=data_to_send, timeout=2)
            # Some native_multi expects a structure: { "lights": { "1": {...}, "2": {...} } }
            r = requests.put(f"http://{device_ip}/state", json=data_to_send, timeout=2)
            r.raise_for_status()  # raise error if 4xx/5xx
            # If we succeed, we can mark parent reachable
            light.state["reachable"] = True
        except Exception as e:
            logging.warning(f"[virtual] Batch update to {device_ip} failed: {e}")

def get_light_state(light):
    """
    Aggregated get_light_state() for a 'virtual' light referencing multiple sub-lights.

    1. Group children by IP (native_multi device).
    2. One GET request per IP to retrieve all sub-lights' states.
    3. Update each child's in-memory state from the response.
    4. Aggregate child's states into the virtual parent's overall state.
    """
    # If this virtual light has no children, just return parent’s last known state
    if "linked_lights" not in light.protocol_cfg:
        logging.warning(f"[virtual] No 'linked_lights' for {light.name}.")
        return light.state

    # We'll need to look up child lights from the global config
    from configManager.configHandler import bridgeConfig
    conf = bridgeConfig.yaml_config  # typically a dict with conf["lights"]

    # Group child lights by IP
    ip_map = {}  # { "192.168.2.60:80": [(child_light_obj, sub_light_number), ...], ... }

    for cid in light.protocol_cfg["linked_lights"]:
        child_light = conf["lights"].get(cid)
        if not child_light:
            continue

        ip = child_light.protocol_cfg.get("ip")
        ln = child_light.protocol_cfg.get("light_nr")
        if not ip or not ln:
            # If missing IP or light_nr, skip or handle differently
            continue

        if ip not in ip_map:
            ip_map[ip] = []
        ip_map[ip].append((child_light, ln))

    # For each IP group, do a single GET to fetch the entire JSON state
    for device_ip, child_pairs in ip_map.items():
        try:
            resp = requests.get(f"http://{device_ip}/state", timeout=3)
            resp.raise_for_status()
            device_data = resp.json()  # expected: { "lights": { "1": {...}, "2": {...}, ... } }

            # Update each child's .state from the response
            lights_data = device_data.get("lights", {})
            for (child_obj, ln) in child_pairs:
                sub_dict = lights_data.get(str(ln), {})
                # For example, sub_dict might have keys: "on", "bri", "xy", "sat", etc.
                # Copy relevant fields into child_obj.state
                for key in ("on", "bri", "hue", "sat", "xy", "ct", "colormode", "reachable"):
                    if key in sub_dict:
                        child_obj.state[key] = sub_dict[key]

                # If the device doesn’t provide "reachable", assume True if request succeeded
                if "reachable" not in sub_dict:
                    child_obj.state["reachable"] = True

        except Exception as e:
            logging.warning(f"[virtual] GET state failed for {device_ip}: {e}")
            # Mark all children in that IP group unreachable if we want
            for (child_obj, _) in child_pairs:
                child_obj.state["reachable"] = False

    # Now that each child’s .state is updated, let's unify them into the parent's .state.
    # Example aggregator: parent "on" if ANY child is on; brightness = average of all kids, etc.
    on_count = 0
    total_bri = 0
    reachable_count = 0
    child_count = 0

    for cid in light.protocol_cfg["linked_lights"]:
        child_light = conf["lights"].get(cid)
        if not child_light:
            continue
        child_count += 1

        if child_light.state.get("on"):
            on_count += 1
        if "bri" in child_light.state:
            total_bri += child_light.state["bri"]
        # For reachability, if at least one is reachable, we can call the parent reachable
        if child_light.state.get("reachable", False):
            reachable_count += 1

    if child_count > 0:
        parent_on = (on_count > 0)
        parent_bri = int(total_bri / child_count)
        parent_reachable = (reachable_count > 0)
        light.state["on"] = parent_on
        light.state["bri"] = parent_bri
        light.state["reachable"] = parent_reachable

    return light.state
