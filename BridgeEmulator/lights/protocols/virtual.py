import logManager
import configManager

logging = logManager.logger.get_logger(__name__)

def set_light(light, data):
    """
    For a 'virtual' light, forward commands to each child in linked_lights.
    """
    if "linked_lights" not in light.protocol_cfg:
        logging.warning(f"[virtual] No 'linked_lights' configured for {light.name}.")
        return

    # Mark the virtual light as unreachable until at least one child is reachable
    light.state["reachable"] = False

    bridgeConfig = configManager.bridgeConfig.yaml_config

    linked_lights = light.protocol_cfg["linked_lights"]
    for linked_id in linked_lights:
        child_light = bridgeConfig["lights"].get(linked_id)
        if child_light:
            try:
                # Forward the same state changes to each child
                child_light.setV1State(data, advertise=False)
                # If any child is reachable, mark the parent as reachable
                if child_light.state.get("reachable", False):
                    light.state["reachable"] = True
            except Exception as e:
                logging.warning(f"[virtual] Error on child {linked_id} for {light.name}: {e}")


def get_light_state(light):
    """
    For a 'virtual' light, we attempt to unify the states of its child lights.

    1. Call child protocol's get_light_state(child_light) if available.
       Otherwise, use child_light.state.
       If that's missing, default to a fallback (on=True, bri=254, reachable=True).

    2. Simple aggregator logic:
       - 'on' is True if ANY child is on.
       - 'bri' is averaged across ALL children.
       - 'reachable' is True if ANY child is reachable.
    """
    if "linked_lights" not in light.protocol_cfg:
        logging.warning(f"[virtual] No 'linked_lights' configured for {light.name}.")
        return light.state  # just return parent's last known state

    linked_lights = light.protocol_cfg["linked_lights"]

    bridgeConfig = configManager.bridgeConfig.yaml_config

    total_bri = 0
    on_count = 0
    reachable_count = 0
    child_count = 0

    for linked_id in linked_lights:
        child_light = bridgeConfig["lights"].get(linked_id)
        if not child_light:
            # Child not found in config. Skip or handle differently if needed.
            continue

        # Attempt to retrieve the child's current state
        child_state = _retrieve_child_state(child_light)

        child_count += 1

        # Is the child "on"?
        if child_state.get("on", False):
            on_count += 1

        # Brightness
        if "bri" in child_state and isinstance(child_state["bri"], int):
            total_bri += child_state["bri"]
        else:
            # If "bri" is missing or invalid, you could default to 254 or skip it
            pass

        # Reachable
        if child_state.get("reachable", True):
            # If there's no explicit reachable, we assume True (per your request)
            reachable_count += 1

    # If no children, just return parent's last known state
    if child_count == 0:
        return light.state

    # If any child is on, the parent is on
    parent_on = (on_count > 0)

    # Calculate average brightness across all children
    avg_bri = 1
    if child_count > 0:
        avg_bri = int(total_bri / child_count)

    # If any child is reachable, parent is reachable
    parent_reachable = (reachable_count > 0)

    # Update the parent's local state
    light.state["on"] = parent_on
    light.state["bri"] = avg_bri
    light.state["reachable"] = parent_reachable

    # Return the updated parent's state
    return light.state


def _retrieve_child_state(child_light):
    """
    Helper function:
    1. Try child's protocol get_light_state() if that function is defined.
    2. Otherwise use child_light.state (the in-memory state).
    3. If even that is missing, default to an always-on, bright, reachable state.
    """
    protocol_name = child_light.protocol
    child_state = None

    # Locate the child's protocol module
    selected_protocol = None
    for protocol_mod in protocols:
        # e.g. "lights.protocols.native_multi"
        if "lights.protocols." + protocol_name == protocol_mod.__name__:
            selected_protocol = protocol_mod
            break

    if selected_protocol and hasattr(selected_protocol, "get_light_state"):
        # We can call the real protocol's get_light_state
        try:
            child_state = selected_protocol.get_light_state(child_light)
        except Exception as e:
            logging.warning(f"[virtual] Error calling get_light_state on child {child_light.name}: {e}")

    # If the protocol didn't have get_light_state or it failed, fallback to child_light.state
    if not child_state:
        child_state = child_light.state

    # If still no valid state, set a fallback
    if not child_state:
        child_state = {
            "on": True,
            "bri": 254,
            "reachable": True
        }

    return child_state
