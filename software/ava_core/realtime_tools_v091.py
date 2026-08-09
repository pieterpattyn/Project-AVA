"""Project AVA v0.9.1 - reliable Home Assistant control feedback.

Home Assistant service calls may succeed before the state endpoint reflects the
new value. v0.9 read the entity back immediately, which could make AVA claim a
successful light command had failed. This wrapper keeps v0.9 intact and patches
only post-command verification.
"""

import time

import realtime_tools_v09 as v09


base = v09.base

VERIFY_TIMEOUT_SECONDS = 3.0
VERIFY_INTERVAL_SECONDS = 0.15


def _expected_state(action, previous_state):
    previous = str(previous_state or "").casefold()
    if action == "turn_on":
        return "on"
    if action == "turn_off":
        return "off"
    if action == "toggle":
        if previous == "on":
            return "off"
        if previous == "off":
            return "on"
    return None


def _tool_ha_control_v091(arguments):
    action = str(arguments.get("action") or "").strip()
    if action not in {"turn_on", "turn_off", "toggle"}:
        return {"ok": False, "error": f"Niet-ondersteunde actie: {action}"}

    resolved = v09._resolve_entity(
        arguments.get("entity"),
        allowed_domains=v09.CONTROL_DOMAINS,
    )
    if not resolved.get("ok"):
        return resolved

    previous_state = resolved["state"]
    entity_id = str(previous_state.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0]
    if domain not in v09.CONTROL_DOMAINS:
        return {
            "ok": False,
            "error": (
                f"Besturing van domein '{domain}' is niet toegestaan. "
                "Alleen light en switch zijn ingeschakeld."
            ),
        }

    service_data = {"entity_id": entity_id}
    brightness_pct = arguments.get("brightness_pct")
    if brightness_pct is not None:
        if domain != "light" or action != "turn_on":
            return {
                "ok": False,
                "error": "brightness_pct is alleen geldig voor een lamp bij turn_on.",
            }
        try:
            brightness_pct = int(brightness_pct)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Ongeldige helderheid."}
        if not 1 <= brightness_pct <= 100:
            return {"ok": False, "error": "Helderheid moet tussen 1 en 100 liggen."}
        service_data["brightness_pct"] = brightness_pct

    # A successful HTTP response means Home Assistant accepted the service call.
    # State propagation can lag behind that response, especially for groups and
    # bridged devices, so do not interpret the first stale GET as a failure.
    v09._ha_request(
        f"/api/services/{domain}/{action}",
        method="POST",
        payload=service_data,
    )

    expected = _expected_state(action, previous_state.get("state"))
    deadline = time.monotonic() + VERIFY_TIMEOUT_SECONDS
    fresh_state = previous_state
    verified = False

    while time.monotonic() < deadline:
        try:
            candidate = v09._ha_request(f"/api/states/{entity_id}")
            if isinstance(candidate, dict):
                fresh_state = candidate
                current = str(candidate.get("state") or "").casefold()
                if expected is None or current == expected:
                    verified = True
                    break
        except Exception:
            # The service call was already accepted. A transient read failure
            # should not retroactively turn that into a failed command.
            pass
        time.sleep(VERIFY_INTERVAL_SECONDS)

    result = {
        "ok": True,
        "action": action,
        "command_accepted": True,
        "verified": verified,
        "expected_state": expected,
        "entity": v09._entity_summary(fresh_state),
    }

    if not verified:
        result["status"] = (
            "Commando door Home Assistant aanvaard; nieuwe toestand nog niet "
            "bevestigd binnen de verificatietijd."
        )

    return result


# execute_tool_v09 resolves this module global dynamically.
v09._tool_ha_control = _tool_ha_control_v091

base.TOOL_INSTRUCTIONS += """
Bij Home Assistant-besturing betekent command_accepted=true dat Home Assistant het commando heeft aanvaard.
Als verified=true, bevestig de nieuwe toestand kort en stellig.
Als verified=false maar command_accepted=true, zeg niet dat de bediening mislukt is. Zeg dat het commando verstuurd is maar dat de statusbevestiging nog achterloopt.
Gebruik de state die in het toolresultaat staat; trek geen mislukking af uit een oude toestand wanneer verified=false.
"""


async def realtime_worker_v091(bridge):
    print("Project AVA v0.9.1 - Home Assistant Verified Control")
    await v09.realtime_worker_v09(bridge)


base.realtime_worker = realtime_worker_v091


if __name__ == "__main__":
    raise SystemExit(base.main())
