"""Project AVA v0.9 - Home Assistant tools on top of the stable v0.8.2 stack.

This wrapper leaves the known-good realtime/audio/memory/weather code untouched and
adds a small Home Assistant REST bridge. Configuration is read from .env:

    HOME_ASSISTANT_URL=http://homeassistant.local:8123
    HOME_ASSISTANT_TOKEN=<long-lived access token>

v0.9 deliberately limits device control to lights and switches. State lookup can
read any Home Assistant entity. Broader control can be added once this baseline is
proven on the real installation.
"""

import asyncio
import difflib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request

import realtime_tools_v082 as v082


base = v082.base
_ORIGINAL_EXECUTE_TOOL = base.execute_tool
_ORIGINAL_REALTIME_WORKER = base.realtime_worker

CONTROL_DOMAINS = {"light", "switch"}
MAX_ENTITY_RESULTS = 12


HOME_ASSISTANT_TOOLS = [
    {
        "type": "function",
        "name": "home_assistant_get_state",
        "description": (
            "Lees de actuele toestand van een Home Assistant-entiteit. Gebruik een "
            "natuurlijke naam zoals 'lamp keuken', 'temperatuur woonkamer' of een "
            "exact entity_id. Deze tool mag alle domeinen lezen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": (
                        "Natuurlijke naam, friendly name of exact Home Assistant "
                        "entity_id van het apparaat of de sensor."
                    ),
                }
            },
            "required": ["entity"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "home_assistant_find_entities",
        "description": (
            "Zoek Home Assistant-entiteiten en hun friendly names. Gebruik dit als "
            "een apparaatnaam onduidelijk is of wanneer de gebruiker vraagt welke "
            "lampen, schakelaars of sensoren beschikbaar zijn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optionele zoektekst, bijvoorbeeld 'keuken'.",
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Optioneel Home Assistant-domein, bijvoorbeeld light, switch "
                        "of sensor."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "home_assistant_control",
        "description": (
            "Bedien een Home Assistant-lamp of schakelaar. In v0.9 zijn alleen de "
            "domeinen light en switch toegestaan. Gebruik turn_on, turn_off of "
            "toggle. Voor lampen kan brightness_pct optioneel worden meegegeven."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": (
                        "Natuurlijke naam, friendly name of exact entity_id van de "
                        "lamp of schakelaar."
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": ["turn_on", "turn_off", "toggle"],
                    "description": "Uit te voeren actie.",
                },
                "brightness_pct": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": (
                        "Optionele helderheid in procent voor light bij turn_on."
                    ),
                },
            },
            "required": ["entity", "action"],
            "additionalProperties": False,
        },
    },
]


def _ha_config():
    url = str(os.getenv("HOME_ASSISTANT_URL") or "").strip().rstrip("/")
    token = str(os.getenv("HOME_ASSISTANT_TOKEN") or "").strip()

    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        url = "http://" + url

    return url, token


def _ha_config_error():
    url, token = _ha_config()
    missing = []
    if not url:
        missing.append("HOME_ASSISTANT_URL")
    if not token:
        missing.append("HOME_ASSISTANT_TOKEN")
    if not missing:
        return None
    return (
        "Home Assistant is nog niet geconfigureerd. Ontbrekend in .env: "
        + ", ".join(missing)
    )


def _ha_request(path, method="GET", payload=None, timeout=6):
    config_error = _ha_config_error()
    if config_error:
        raise RuntimeError(config_error)

    url, token = _ha_config()
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Project-AVA/0.9",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            details = error.read().decode("utf-8", errors="replace").strip()
        except Exception:
            details = ""
        message = f"Home Assistant HTTP {error.code}"
        if error.code == 401:
            message += ": token ongeldig of niet gemachtigd"
        elif error.code == 404:
            message += ": endpoint of entiteit niet gevonden"
        if details:
            message += f" ({details[:180]})"
        raise RuntimeError(message) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Home Assistant niet bereikbaar: {error.reason}") from error


def _normalise_name(value):
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = text.replace("_", " ").replace(".", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _entity_summary(state):
    attributes = state.get("attributes") or {}
    entity_id = str(state.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    summary = {
        "entity_id": entity_id,
        "friendly_name": attributes.get("friendly_name") or entity_id,
        "domain": domain,
        "state": state.get("state"),
    }

    for key in (
        "unit_of_measurement",
        "device_class",
        "temperature",
        "current_temperature",
        "humidity",
    ):
        if key in attributes:
            summary[key] = attributes.get(key)

    brightness = attributes.get("brightness")
    if brightness is not None:
        try:
            summary["brightness_pct"] = round(float(brightness) * 100 / 255)
        except (TypeError, ValueError):
            pass

    return summary


def _all_states():
    states = _ha_request("/api/states")
    if not isinstance(states, list):
        raise RuntimeError("Home Assistant gaf geen geldige statenlijst terug.")
    return states


def _candidate_score(query, state):
    entity_id = str(state.get("entity_id") or "")
    attributes = state.get("attributes") or {}
    friendly_name = str(attributes.get("friendly_name") or "")
    object_id = entity_id.split(".", 1)[1] if "." in entity_id else entity_id

    q = _normalise_name(query)
    entity_norm = _normalise_name(entity_id)
    friendly_norm = _normalise_name(friendly_name)
    object_norm = _normalise_name(object_id)

    if not q:
        return 0.0
    if q in {entity_norm, friendly_norm, object_norm}:
        return 1.0
    if q and (q in friendly_norm or q in object_norm):
        return 0.92

    q_tokens = set(q.split())
    candidate_tokens = set((friendly_norm + " " + object_norm).split())
    overlap = len(q_tokens & candidate_tokens) / max(1, len(q_tokens))
    sequence = max(
        difflib.SequenceMatcher(None, q, friendly_norm).ratio(),
        difflib.SequenceMatcher(None, q, object_norm).ratio(),
    )
    return 0.65 * overlap + 0.35 * sequence


def _resolve_entity(query, allowed_domains=None):
    query = str(query or "").strip()
    if not query:
        return {
            "ok": False,
            "error": "Geen Home Assistant-entiteit opgegeven.",
        }

    states = _all_states()
    if allowed_domains:
        states = [
            state
            for state in states
            if str(state.get("entity_id") or "").split(".", 1)[0] in allowed_domains
        ]

    exact_id = next(
        (
            state
            for state in states
            if str(state.get("entity_id") or "").casefold() == query.casefold()
        ),
        None,
    )
    if exact_id:
        return {"ok": True, "state": exact_id}

    ranked = sorted(
        ((_candidate_score(query, state), state) for state in states),
        key=lambda item: item[0],
        reverse=True,
    )
    ranked = [(score, state) for score, state in ranked if score >= 0.48]

    if not ranked:
        return {
            "ok": False,
            "error": f"Geen duidelijke Home Assistant-match voor '{query}'.",
        }

    top_score, top_state = ranked[0]
    if len(ranked) > 1 and ranked[1][0] >= top_score - 0.07:
        candidates = [_entity_summary(state) for _, state in ranked[:5]]
        return {
            "ok": False,
            "error": f"Meerdere Home Assistant-entiteiten passen bij '{query}'.",
            "candidates": candidates,
        }

    return {"ok": True, "state": top_state}


def _ha_ping():
    result = _ha_request("/api/")
    return {"ok": True, "result": result}


def _tool_ha_get_state(arguments):
    resolved = _resolve_entity(arguments.get("entity"))
    if not resolved.get("ok"):
        return resolved

    return {
        "ok": True,
        "entity": _entity_summary(resolved["state"]),
    }


def _tool_ha_find_entities(arguments):
    query = str(arguments.get("query") or "").strip()
    domain = str(arguments.get("domain") or "").strip().casefold()

    states = _all_states()
    if domain:
        states = [
            state
            for state in states
            if str(state.get("entity_id") or "").split(".", 1)[0].casefold() == domain
        ]

    if query:
        ranked = sorted(
            ((_candidate_score(query, state), state) for state in states),
            key=lambda item: item[0],
            reverse=True,
        )
        states = [state for score, state in ranked if score >= 0.25]
    else:
        states = sorted(
            states,
            key=lambda state: _normalise_name(
                (state.get("attributes") or {}).get("friendly_name")
                or state.get("entity_id")
                or ""
            ),
        )

    results = [_entity_summary(state) for state in states[:MAX_ENTITY_RESULTS]]
    return {
        "ok": True,
        "count": len(results),
        "entities": results,
        "truncated": len(states) > len(results),
    }


def _tool_ha_control(arguments):
    action = str(arguments.get("action") or "").strip()
    if action not in {"turn_on", "turn_off", "toggle"}:
        return {"ok": False, "error": f"Niet-ondersteunde actie: {action}"}

    resolved = _resolve_entity(
        arguments.get("entity"),
        allowed_domains=CONTROL_DOMAINS,
    )
    if not resolved.get("ok"):
        return resolved

    state = resolved["state"]
    entity_id = str(state.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0]
    if domain not in CONTROL_DOMAINS:
        return {
            "ok": False,
            "error": (
                f"Besturing van domein '{domain}' is in AVA v0.9 niet toegestaan. "
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

    _ha_request(
        f"/api/services/{domain}/{action}",
        method="POST",
        payload=service_data,
    )

    try:
        fresh_state = _ha_request(f"/api/states/{entity_id}")
    except Exception:
        fresh_state = state

    return {
        "ok": True,
        "action": action,
        "entity": _entity_summary(fresh_state),
    }


async def execute_tool_v09(name, arguments):
    if name == "home_assistant_get_state":
        return await asyncio.to_thread(_tool_ha_get_state, arguments)
    if name == "home_assistant_find_entities":
        return await asyncio.to_thread(_tool_ha_find_entities, arguments)
    if name == "home_assistant_control":
        return await asyncio.to_thread(_tool_ha_control, arguments)
    return await _ORIGINAL_EXECUTE_TOOL(name, arguments)


async def realtime_worker_v09(bridge):
    print("Project AVA v0.9 - Home Assistant")
    config_error = _ha_config_error()
    if config_error:
        print(f"Home Assistant: niet geconfigureerd ({config_error})")
        print("Tijd/weer blijven werken; Home Assistant-tools geven een setupmelding.")
    else:
        try:
            await asyncio.to_thread(_ha_ping)
            url, _ = _ha_config()
            print(f"Home Assistant: verbonden met {url}")
        except Exception as error:
            print(f"Home Assistant preflight warning: {error}")

    await _ORIGINAL_REALTIME_WORKER(bridge)


base.TOOLS.extend(HOME_ASSISTANT_TOOLS)
base.execute_tool = execute_tool_v09
base.TOOL_INSTRUCTIONS += """
Je beschikt ook over Home Assistant-tools.
Gebruik home_assistant_get_state voor actuele apparaat- of sensorstatus.
Gebruik home_assistant_find_entities als de bedoelde entiteit onduidelijk is of de gebruiker vraagt welke entiteiten beschikbaar zijn.
Gebruik home_assistant_control alleen om lampen en schakelaars te bedienen; v0.9 staat bewust geen andere Home Assistant-domeinen toe.
Als de gebruiker vraagt een lamp of schakelaar aan, uit of om te schakelen, voer de tool meteen uit en bevestig alleen wat werkelijk gelukt is.
Als een Home Assistant-tool een fout of meerdere kandidaten teruggeeft, doe geen gok en vraag om verduidelijking.
Spreek niet eerst 'ik ga het even doen'; voer de tool uit en geef daarna één kort antwoord.
"""
base.realtime_worker = realtime_worker_v09


if __name__ == "__main__":
    raise SystemExit(base.main())
