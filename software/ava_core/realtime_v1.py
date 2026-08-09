"""Project AVA v1.0 - consolidated Realtime assistant.

Single production entry point for the proven v0.9.3 feature set:
- OpenAI Realtime voice + avatar
- persistent local memory, including canonical residence handling
- current date/time and weather tools
- Home Assistant discovery, state lookup, light/switch control
- verified state and brightness feedback
- serialized tool follow-up responses
- deterministic silent tool routing before spoken follow-up

The earlier v0.8/v0.9 wrapper files remain in the repository as historical
checkpoints, but this module does not import or monkey-patch them.
"""

import asyncio
import base64
import difflib
import json
import os
import re
import signal
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sounddevice as sd
from openai import AsyncOpenAI
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

import realtime_app as core


VERSION = "1.0"
CONTROL_DOMAINS = {"light", "switch"}
MAX_ENTITY_RESULTS = 12
VERIFY_TIMEOUT_SECONDS = 4.0
VERIFY_INTERVAL_SECONDS = 0.15
BRIGHTNESS_TOLERANCE_PCT = 2

WEEKDAYS_NL = (
    "maandag", "dinsdag", "woensdag", "donderdag",
    "vrijdag", "zaterdag", "zondag",
)
MONTHS_NL = (
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
)
WEATHER_CODES_NL = {
    0: "helder",
    1: "overwegend helder",
    2: "gedeeltelijk bewolkt",
    3: "bewolkt",
    45: "mist",
    48: "aanvriezende mist",
    51: "lichte motregen",
    53: "motregen",
    55: "dichte motregen",
    56: "lichte aanvriezende motregen",
    57: "aanvriezende motregen",
    61: "lichte regen",
    63: "regen",
    65: "zware regen",
    66: "lichte aanvriezende regen",
    67: "zware aanvriezende regen",
    71: "lichte sneeuw",
    73: "sneeuw",
    75: "zware sneeuw",
    77: "sneeuwkorrels",
    80: "lichte regenbuien",
    81: "regenbuien",
    82: "zware regenbuien",
    85: "lichte sneeuwbuien",
    86: "zware sneeuwbuien",
    95: "onweer",
    96: "onweer met lichte hagel",
    99: "onweer met zware hagel",
}

_FILLER_FACTS = {
    "goed",
    "dat goed",
    "dit goed",
    "het goed",
    "goed onthouden",
}


TOOLS = [
    {
        "type": "function",
        "name": "get_current_datetime",
        "description": (
            "Geeft de echte huidige lokale datum en tijd. Gebruik dit voor vragen "
            "over hoe laat het nu is, welke dag of datum het vandaag is, of de tijd "
            "in een opgegeven IANA-tijdzone. Raad de huidige tijd nooit zelf."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": (
                        "Optionele IANA-tijdzone, bijvoorbeeld Europe/Brussels of "
                        "America/New_York. Laat weg voor de lokale tijd van AVA."
                    ),
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_weather",
        "description": (
            "Geeft het actuele weer en de verwachting voor vandaag op een genoemde "
            "plaats via Open-Meteo. Gebruik dit voor actuele weersvragen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Plaatsnaam, bijvoorbeeld Brugge, Gent of Brussel.",
                }
            },
            "required": ["location"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "home_assistant_get_state",
        "description": (
            "Lees de actuele toestand van een Home Assistant-entiteit. Gebruik een "
            "natuurlijke naam, friendly name of exact entity_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Naam, friendly name of exact entity_id.",
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
            "Zoek Home Assistant-entiteiten. Gebruik dit voor lijsten of wanneer "
            "een naam echt onduidelijk is."
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
                    "description": "Optioneel domein, bijvoorbeeld light of sensor.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "home_assistant_control",
        "description": (
            "Bedien een Home Assistant-lamp of schakelaar. Alleen light en switch "
            "zijn toegestaan. Gebruik turn_on, turn_off of toggle. Voor lampen kan "
            "brightness_pct worden meegegeven bij turn_on."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Naam, friendly name of exact entity_id.",
                },
                "action": {
                    "type": "string",
                    "enum": ["turn_on", "turn_off", "toggle"],
                },
                "brightness_pct": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["entity", "action"],
            "additionalProperties": False,
        },
    },
]


TOOL_INSTRUCTIONS = """
Je beschikt over tools voor actuele tijd/datum, weer en Home Assistant.
Gebruik get_current_datetime altijd voor de huidige tijd, dag of datum.
Gebruik get_weather altijd voor actueel weer.
Voor vragen als 'wat is het weer bij mij' gebruik je de opgeslagen woonplaats exact zoals die in de persistente geheugencontext staat.
Een opgeslagen woonplaats beschrijft de gebruiker, niet AVA. Zeg dus 'jij woont in ...' en niet 'ik woon in ...'.
Bij een duidelijke opdracht zoals 'zet Beneden binnen aan' roep je home_assistant_control direct aan; zoek niet eerst via home_assistant_find_entities.
Gebruik home_assistant_find_entities alleen als de gebruiker om een lijst vraagt of als een Home Assistant-tool meldt dat de naam dubbelzinnig is.
Gebruik home_assistant_control alleen voor lampen en schakelaars.
Als verified=true, bevestig alleen waarden die door het toolresultaat bevestigd zijn.
Als brightness_requested_pct aanwezig is maar brightness_verified=false, zeg dat het helderheidscommando aanvaard is maar nog niet bevestigd.
Als command_accepted=true maar verified=false, zeg niet dat de bediening mislukt is. Zeg dat het commando is verstuurd maar dat statusbevestiging achterloopt.
Toolselectie gebeurt stil: geef nooit een gesproken tussenzin vóór een toolcall.
Geef na het uiteindelijke toolresultaat één compact gesproken antwoord.
Verzin nooit actuele informatie of een succesvolle apparaatstatus.
""".strip()


def _normalise_fact(text):
    return str(text).strip(" .,!?:;\"'").casefold()


def _canonical_location(location):
    cleaned = str(location).strip(" .,!?:;\"'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(
        r"\s*,?\s+(?:zonder|met)\s+[A-Za-zÀ-ÖØ-öø-ÿ]"
        r"(?:\s+(?:achteraan|op\s+het\s+einde|aan\s+het\s+einde))?\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    spelled = re.search(
        r"\b((?:[A-Za-zÀ-ÖØ-öø-ÿ]\s*-\s*){3,}"
        r"[A-Za-zÀ-ÖØ-öø-ÿ])\b",
        cleaned,
    )
    if spelled:
        cleaned = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", spelled.group(1))

    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:]


def _residence_from_memory(memory):
    for fact in memory.data.get("facts", []):
        match = re.match(r"^ik\s+woon\s+in\s+(.+)$", str(fact).strip(), re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _remove_filler_facts(memory):
    old = list(memory.data.get("facts", []))
    new = [fact for fact in old if _normalise_fact(fact) not in _FILLER_FACTS]
    if new != old:
        memory.data["facts"] = new
        memory.save()
        print("Memory cleanup: betekenisloos legacy-feit verwijderd.")


def _repair_residence_facts(memory):
    old = list(memory.data.get("facts", []))
    new = []
    changed = False
    residence_seen = False

    for fact in old:
        text = str(fact).strip()
        match = re.match(r"^ik\s+woon\s+in\s+(.+)$", text, re.IGNORECASE)
        if not match:
            new.append(text)
            continue

        canonical_location = _canonical_location(match.group(1))
        if not canonical_location:
            changed = True
            continue

        canonical_fact = f"ik woon in {canonical_location}"
        if residence_seen:
            changed = True
            continue

        residence_seen = True
        new.append(canonical_fact)
        if canonical_fact != text:
            changed = True

    if changed:
        memory.data["facts"] = new
        memory.save()
        print("Memory migration: woonplaats opgeschoond.")


class LocalMemory(core.LocalMemory):
    def load(self):
        super().load()
        _remove_filler_facts(self)
        _repair_residence_facts(self)


def _set_residence(memory, location):
    location = _canonical_location(location)
    if not location:
        return False

    canonical = f"ik woon in {location}"
    old = list(memory.data.get("facts", []))
    new = []

    for fact in old:
        normalised = _normalise_fact(fact)
        if normalised in _FILLER_FACTS:
            continue
        if re.match(r"^(?:ik|de gebruiker)\s+woon\w*\s+(?:in|te)\b", normalised):
            continue
        if normalised.startswith("mijn woonplaats is "):
            continue
        new.append(str(fact).strip())

    if canonical.casefold() not in {fact.casefold() for fact in new}:
        new.append(canonical)

    changed = new != old
    if changed:
        memory.data["facts"] = new
        memory.save()
    return changed


def _extract_residence(text):
    patterns = (
        r"\b([A-Za-zÀ-ÖØ-öø-ÿ'’-]{2,40})\s+is\s+(?:de\s+)?"
        r"(?:gemeente|plaats|stad|dorp)\s+waar\s+ik\s+woon\b",
        r"\bik\s+woon\s+(?:in|te)\s+"
        r"([A-Za-zÀ-ÖØ-öø-ÿ'’ -]{2,60}?)(?=[.!?,]|\s+(?:en|maar|want)\b|$)",
        r"\bmijn\s+woonplaats\s+is\s+"
        r"([A-Za-zÀ-ÖØ-öø-ÿ'’ -]{2,60}?)(?=[.!?,]|\s+(?:en|maar|want)\b|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _canonical_location(match.group(1))
    return ""


def _extract_favorite_change(text):
    match = re.search(
        r"\bmijn\s+favoriete\s+"
        r"([\wÀ-ÖØ-öø-ÿ' -]{2,40}?)\s+"
        r"(?:nogmaals\s+|opnieuw\s+|weer\s+)?"
        r"(?:veranderen|wijzigen|aanpassen|verander|wijzig|pas)\s+"
        r"(?:naar|in)\s+(.+?)(?=[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _apply_end_letter_correction(transcript, memory):
    current = _residence_from_memory(memory)
    if not current:
        return None

    remove_match = re.search(
        r"\bzonder\s+([A-Za-zÀ-ÖØ-öø-ÿ])\s+"
        r"(?:achteraan|op\s+het\s+einde|aan\s+het\s+einde)\b",
        transcript,
        re.IGNORECASE,
    )
    if remove_match:
        letter = remove_match.group(1)
        if current.casefold().endswith(letter.casefold()):
            corrected = current[:-1]
            changed = _set_residence(memory, corrected)
            return (
                f"woonplaats aangepast naar {corrected}"
                if changed
                else f"woonplaats bevestigd: {corrected}"
            )

    add_match = re.search(
        r"\bmet\s+([A-Za-zÀ-ÖØ-öø-ÿ])\s+"
        r"(?:achteraan|op\s+het\s+einde|aan\s+het\s+einde)\b",
        transcript,
        re.IGNORECASE,
    )
    if add_match:
        letter = add_match.group(1)
        if not current.casefold().endswith(letter.casefold()):
            corrected = current + letter.lower()
            changed = _set_residence(memory, corrected)
            return (
                f"woonplaats aangepast naar {corrected}"
                if changed
                else f"woonplaats bevestigd: {corrected}"
            )

    return None


def process_memory_request(transcript, memory):
    actions = list(core.process_memory_request(transcript, memory) or [])
    _remove_filler_facts(memory)
    actions = [
        action
        for action in actions
        if not re.search(
            r"feit (?:opgeslagen|stond al in geheugen):\s*(?:goed|dat goed|dit goed)[.!]?$",
            action,
            re.IGNORECASE,
        )
    ]

    residence = _extract_residence(transcript)
    if residence:
        changed = _set_residence(memory, residence)
        actions = [action for action in actions if not action.startswith("woonplaats ")]
        actions.append(
            f"woonplaats aangepast naar {residence}"
            if changed
            else f"woonplaats bevestigd: {residence}"
        )

    favorite_change = _extract_favorite_change(transcript)
    if favorite_change:
        topic, value = favorite_change
        changed = memory.set_preference(topic, value)
        if not any(
            existing.casefold().startswith(f"favoriete {topic}".casefold())
            for existing in actions
        ):
            actions.append(
                f"favoriete {topic} aangepast naar {value}"
                if changed
                else f"favoriete {topic} stond al op {value}"
            )

    correction = _apply_end_letter_correction(transcript, memory)
    if correction:
        actions = [action for action in actions if not action.startswith("woonplaats ")]
        actions.append(correction)

    before = _residence_from_memory(memory)
    canonical = _canonical_location(before)
    if before and canonical and canonical != before:
        if _set_residence(memory, canonical):
            actions = [action for action in actions if not action.startswith("woonplaats ")]
            actions.append(f"woonplaats aangepast naar {canonical}")

    return actions


def build_tool_instructions(memory, memory_actions=None):
    instructions = core.build_instructions(memory) + "\n\n" + TOOL_INSTRUCTIONS
    if memory_actions:
        instructions += (
            "\n\nLokale geheugenactie voor deze beurt:\n- "
            + "\n- ".join(memory_actions)
            + "\nDeze geheugenactie is lokaal uitgevoerd en persistent opgeslagen. "
            "Bevestig ze kort als dat relevant is."
        )
    return instructions


def tool_current_datetime(arguments):
    timezone_name = str(arguments.get("timezone") or "").strip()
    if timezone_name:
        try:
            now = datetime.now(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            return {"ok": False, "error": f"Onbekende tijdzone: {timezone_name}"}
    else:
        now = datetime.now().astimezone()
        timezone_name = str(now.tzinfo)

    return {
        "ok": True,
        "weekday": WEEKDAYS_NL[now.weekday()],
        "date": f"{now.day} {MONTHS_NL[now.month - 1]} {now.year}",
        "time": now.strftime("%H:%M:%S"),
        "timezone": timezone_name,
        "iso": now.isoformat(),
    }


def _http_json(url, timeout=6):
    request = urllib.request.Request(url, headers={"User-Agent": f"Project-AVA/{VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def tool_weather_sync(arguments):
    location = str(arguments.get("location") or "").strip()
    if not location:
        return {"ok": False, "error": "Geen plaats opgegeven."}

    try:
        geo_query = urllib.parse.urlencode(
            {"name": location, "count": 1, "language": "nl", "format": "json"}
        )
        geo = _http_json(f"https://geocoding-api.open-meteo.com/v1/search?{geo_query}")
        results = geo.get("results") or []
        if not results:
            return {"ok": False, "error": f"Plaats niet gevonden: {location}"}

        place = results[0]
        forecast_query = urllib.parse.urlencode(
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "timezone": "auto",
                "forecast_days": 1,
                "current": (
                    "temperature_2m,apparent_temperature,precipitation,"
                    "weather_code,wind_speed_10m"
                ),
                "daily": (
                    "temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
            }
        )
        forecast = _http_json(
            f"https://api.open-meteo.com/v1/forecast?{forecast_query}"
        )
        current = forecast.get("current") or {}
        daily = forecast.get("daily") or {}
        weather_code = current.get("weather_code")

        display_location = place.get("name") or location
        admin1 = place.get("admin1") or ""
        country = place.get("country") or ""
        if admin1 and admin1.casefold() != display_location.casefold():
            display_location += f", {admin1}"
        if country:
            display_location += f", {country}"

        return {
            "ok": True,
            "location": display_location,
            "timezone": place.get("timezone") or "auto",
            "observed_at": current.get("time"),
            "condition": WEATHER_CODES_NL.get(
                weather_code, f"weercode {weather_code}"
            ),
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "precipitation_mm": current.get("precipitation"),
            "wind_kmh": current.get("wind_speed_10m"),
            "today_min_c": (daily.get("temperature_2m_min") or [None])[0],
            "today_max_c": (daily.get("temperature_2m_max") or [None])[0],
            "today_precipitation_probability_pct": (
                daily.get("precipitation_probability_max") or [None]
            )[0],
            "source": "Open-Meteo",
        }
    except Exception as error:
        return {"ok": False, "error": f"Weerdienst niet bereikbaar: {error}"}


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
    return "Home Assistant is nog niet geconfigureerd. Ontbrekend in .env: " + ", ".join(missing)


def _ha_request(path, method="GET", payload=None, timeout=6):
    config_error = _ha_config_error()
    if config_error:
        raise RuntimeError(config_error)

    url, token = _ha_config()
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"Project-AVA/{VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
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
    text = text.casefold().replace("_", " ").replace(".", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _turn_likely_needs_tool(transcript):
    """High-precision local routing for turns that require current/device tools.

    Tool turns start as text-only Realtime responses with tool_choice=required.
    That makes tool selection silent by construction. Non-tool turns explicitly
    disable tools and stay on the low-latency audio path.
    """

    text = _normalise_name(transcript)
    if not text:
        return False

    patterns = (
        r"\bhoe laat\b",
        r"\bwelke (?:dag|datum)\b",
        r"\bwat is (?:de )?datum\b",
        r"\bdatum van vandaag\b",
        r"\btijd in\b",
        r"\bweer\b",
        r"\bweers(?:verwachting|voorspelling)\b",
        r"\bregent\b",
        r"\bregen\b",
        r"\bwind\b",
        r"\btemperatuur buiten\b",
        r"\bhoe warm\b.*\bbuiten\b",
        r"\bhome assistant\b",
        r"\bhelderheid\b",
        r"\b(?:lamp|lampen|licht|lichten|schakelaar|schakelaars|switch|sensor|sensoren)\b",
        r"\b(?:zet|doe|schakel|maak)\b.+\b(?:aan|uit)\b",
        r"\bstaat\b.+\b(?:aan|uit)\b",
        r"\bstatus van\b",
        r"\btemperatuur (?:in|van) (?:de )?[a-z0-9]+\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


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
    if q in friendly_norm or q in object_norm:
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
        return {"ok": False, "error": "Geen Home Assistant-entiteit opgegeven."}

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
        return {
            "ok": False,
            "error": f"Meerdere Home Assistant-entiteiten passen bij '{query}'.",
            "candidates": [_entity_summary(state) for _, state in ranked[:5]],
        }
    return {"ok": True, "state": top_state}


def _tool_ha_get_state(arguments):
    resolved = _resolve_entity(arguments.get("entity"))
    if not resolved.get("ok"):
        return resolved
    return {"ok": True, "entity": _entity_summary(resolved["state"])}


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


def _brightness_pct_from_state(state):
    if not isinstance(state, dict):
        return None
    raw = (state.get("attributes") or {}).get("brightness")
    if raw is None:
        return None
    try:
        return round(float(raw) * 100 / 255)
    except (TypeError, ValueError):
        return None


def _matching_service_state(service_result, entity_id):
    if not isinstance(service_result, list):
        return None
    matches = [
        state
        for state in service_result
        if isinstance(state, dict)
        and str(state.get("entity_id") or "").casefold() == entity_id.casefold()
    ]
    return matches[-1] if matches else None


def _verification(candidate, expected_state, requested_brightness):
    current_state = str((candidate or {}).get("state") or "").casefold()
    state_verified = expected_state is None or current_state == expected_state
    reported_brightness = _brightness_pct_from_state(candidate)

    if requested_brightness is None:
        brightness_verified = None
        overall = state_verified
    else:
        brightness_verified = (
            reported_brightness is not None
            and abs(reported_brightness - requested_brightness)
            <= BRIGHTNESS_TOLERANCE_PCT
        )
        overall = state_verified and brightness_verified

    return overall, state_verified, brightness_verified, reported_brightness


def _tool_ha_control(arguments):
    action = str(arguments.get("action") or "").strip()
    if action not in {"turn_on", "turn_off", "toggle"}:
        return {"ok": False, "error": f"Niet-ondersteunde actie: {action}"}

    resolved = _resolve_entity(arguments.get("entity"), allowed_domains=CONTROL_DOMAINS)
    if not resolved.get("ok"):
        return resolved

    previous_state = resolved["state"]
    entity_id = str(previous_state.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0]
    service_data = {"entity_id": entity_id}

    requested_brightness = arguments.get("brightness_pct")
    if requested_brightness is not None:
        if domain != "light" or action != "turn_on":
            return {
                "ok": False,
                "error": "brightness_pct is alleen geldig voor een lamp bij turn_on.",
            }
        try:
            requested_brightness = int(requested_brightness)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Ongeldige helderheid."}
        if not 1 <= requested_brightness <= 100:
            return {"ok": False, "error": "Helderheid moet tussen 1 en 100 liggen."}
        service_data["brightness_pct"] = requested_brightness

    service_result = _ha_request(
        f"/api/services/{domain}/{action}",
        method="POST",
        payload=service_data,
    )

    expected_state = _expected_state(action, previous_state.get("state"))
    fresh_state = _matching_service_state(service_result, entity_id) or previous_state
    verified, state_verified, brightness_verified, reported_brightness = _verification(
        fresh_state, expected_state, requested_brightness
    )

    deadline = time.monotonic() + VERIFY_TIMEOUT_SECONDS
    while not verified and time.monotonic() < deadline:
        try:
            candidate = _ha_request(f"/api/states/{entity_id}")
            if isinstance(candidate, dict):
                fresh_state = candidate
                (
                    verified,
                    state_verified,
                    brightness_verified,
                    reported_brightness,
                ) = _verification(candidate, expected_state, requested_brightness)
                if verified:
                    break
        except Exception:
            pass
        time.sleep(VERIFY_INTERVAL_SECONDS)

    result = {
        "ok": True,
        "action": action,
        "command_accepted": True,
        "verified": verified,
        "state_verified": state_verified,
        "expected_state": expected_state,
        "entity": _entity_summary(fresh_state),
    }
    if requested_brightness is not None:
        result["brightness_requested_pct"] = requested_brightness
        result["brightness_reported_pct"] = reported_brightness
        result["brightness_verified"] = bool(brightness_verified)

    if not verified:
        if state_verified and requested_brightness is not None:
            result["status"] = (
                "Home Assistant heeft het lichtcommando uitgevoerd, maar de "
                "gevraagde helderheid is nog niet bevestigd door de entiteitsstatus."
            )
        else:
            result["status"] = (
                "Commando door Home Assistant aanvaard; nieuwe toestand nog niet "
                "bevestigd binnen de verificatietijd."
            )
    return result


async def execute_tool(name, arguments):
    if name == "get_current_datetime":
        return tool_current_datetime(arguments)
    if name == "get_weather":
        return await asyncio.to_thread(tool_weather_sync, arguments)
    if name == "home_assistant_get_state":
        return await asyncio.to_thread(_tool_ha_get_state, arguments)
    if name == "home_assistant_find_entities":
        return await asyncio.to_thread(_tool_ha_find_entities, arguments)
    if name == "home_assistant_control":
        return await asyncio.to_thread(_tool_ha_control, arguments)
    return {"ok": False, "error": f"Onbekende tool: {name}"}


def _ha_preflight():
    result = _ha_request("/api/")
    return {"ok": True, "result": result}


async def realtime_worker(bridge):
    print(f"Project AVA v{VERSION}")
    config_error = _ha_config_error()
    if config_error:
        print(f"Home Assistant: niet geconfigureerd ({config_error})")
        print("Tijd/weer blijven werken; Home Assistant-tools geven een setupmelding.")
    else:
        try:
            await asyncio.to_thread(_ha_preflight)
            url, _ = _ha_config()
            print(f"Home Assistant: verbonden met {url}")
        except Exception as error:
            print(f"Home Assistant preflight warning: {error}")

    memory = LocalMemory(core.MEMORY_FILE)
    print(f"Persistent memory loaded: {memory.summary()}")
    print(f"Memory file: {core.MEMORY_FILE}")

    microphone, mic_device = core.find_microphone(core.MIC_NAME)
    mic_rate = int(mic_device["default_samplerate"])
    print(f"Microphone native sample rate: {mic_rate} Hz")
    voice_threshold, peak_threshold = core.calibrate_microphone(microphone, mic_rate)

    output_device, output_info = core.find_output(core.OUTPUT_NAME)
    output_rate = int(output_info["default_samplerate"])
    print(f"Output native sample rate: {output_rate} Hz")

    client = AsyncOpenAI()
    player = core.PCMPlayer(output_device, output_rate, bridge)
    player.start()

    chunk_frames = max(1, int(mic_rate * core.CHUNK_MS / 1000))
    loop = asyncio.get_running_loop()
    mic_queue = asyncio.Queue(maxsize=100)
    last_speech_stopped = None
    first_audio_seen = False

    turn_lock = threading.Lock()
    server_turn_active = threading.Event()
    turn_voice_blocks = 0
    turn_peak = 0.0
    pending_turns = deque()
    tool_followup_pending = False
    current_turn_tool_mode = False

    def audio_callback(indata, frames, time_info, status):
        nonlocal turn_voice_blocks, turn_peak
        if status:
            print(f"Audio status: {status}")
        if player.playback_active.is_set():
            return

        data = bytes(indata)
        level = core.audio_rms(data)
        if server_turn_active.is_set():
            with turn_lock:
                turn_peak = max(turn_peak, level)
                if level >= voice_threshold:
                    turn_voice_blocks += 1

        def enqueue():
            if not mic_queue.full():
                mic_queue.put_nowait(data)

        loop.call_soon_threadsafe(enqueue)

    async with client.realtime.connect(model=core.MODEL) as connection:
        await connection.session.update(
            session={
                "type": "realtime",
                "model": core.MODEL,
                "instructions": build_tool_instructions(memory),
                "output_modalities": ["audio"],
                "tools": TOOLS,
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": core.API_SAMPLE_RATE},
                        "noise_reduction": {"type": "far_field"},
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": "nl",
                            "prompt": (
                                "Belgisch-Nederlands gesprek. De assistent heet AVA. "
                                "Veel voorkomende zinnen zijn: hoe laat is het, wat is "
                                "het weer, zet beneden binnen aan?"
                            ),
                        },
                        "turn_detection": {
                            "type": "semantic_vad",
                            "eagerness": "low",
                            "create_response": False,
                            "interrupt_response": False,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": core.API_SAMPLE_RATE},
                        "voice": "marin",
                    },
                },
            }
        )

        print("Tools: tijd/datum + weer + Home Assistant")
        print("Connected. Speak naturally. Ctrl+C stops the process.")
        bridge.setState("listening")

        async def send_microphone():
            with sd.RawInputStream(
                samplerate=mic_rate,
                blocksize=chunk_frames,
                device=microphone,
                channels=core.CHANNELS,
                dtype="int16",
                callback=audio_callback,
            ):
                while True:
                    data = await mic_queue.get()
                    if player.playback_active.is_set():
                        continue
                    data = core.resample_pcm16_mono(
                        data, mic_rate, core.API_SAMPLE_RATE
                    )
                    encoded = base64.b64encode(data).decode("ascii")
                    await connection.input_audio_buffer.append(audio=encoded)

        async def receive_events():
            nonlocal last_speech_stopped, first_audio_seen
            nonlocal turn_voice_blocks, turn_peak, tool_followup_pending
            nonlocal current_turn_tool_mode

            async for event in connection:
                if event.type == "input_audio_buffer.speech_started":
                    with turn_lock:
                        turn_voice_blocks = 0
                        turn_peak = 0.0
                    server_turn_active.set()
                    bridge.setState("listening")
                    print("\nYou: [speaking]")
                    first_audio_seen = False

                elif event.type == "input_audio_buffer.speech_stopped":
                    server_turn_active.clear()
                    last_speech_stopped = time.monotonic()
                    with turn_lock:
                        voice_blocks = turn_voice_blocks
                        peak = turn_peak
                    voice_duration = voice_blocks * core.CHUNK_MS / 1000.0
                    pending_turns.append((voice_duration, peak))
                    bridge.setState("thinking")
                    print("You: [finished]")
                    print(
                        f"Local voice evidence: {voice_duration:.2f}s | peak: {peak:.3f}"
                    )

                elif event.type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.transcript.strip()
                    print(f"You heard as: {transcript}")
                    if pending_turns:
                        voice_duration, peak = pending_turns.popleft()
                    else:
                        voice_duration, peak = 0.0, 0.0

                    local_voice_ok = (
                        voice_duration >= core.MIN_VOICE_DURATION
                        and peak >= peak_threshold
                    )

                    try:
                        await connection.conversation.item.delete(item_id=event.item_id)
                    except Exception as error:
                        print(f"Conversation cleanup warning: {error}")

                    if not local_voice_ok:
                        print(
                            "Ignored weak/non-speech trigger "
                            f"(voice {voice_duration:.2f}s, peak {peak:.3f})."
                        )
                        bridge.setState("listening")
                        continue

                    if not core.transcript_is_valid(transcript):
                        print("Ignored ghost/empty transcription. Listening...")
                        bridge.setState("listening")
                        continue

                    memory_actions = process_memory_request(transcript, memory)
                    for action in memory_actions:
                        print(f"Memory: {action}")

                    if memory_actions:
                        await connection.session.update(
                            session={
                                "type": "realtime",
                                "instructions": build_tool_instructions(memory),
                            }
                        )

                    await connection.conversation.item.create(
                        item={
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": transcript}],
                        }
                    )

                    current_turn_tool_mode = _turn_likely_needs_tool(transcript)
                    response_options = {
                        "instructions": build_tool_instructions(memory, memory_actions),
                    }
                    if current_turn_tool_mode:
                        # Silent tool-selection pass. The Realtime API supports
                        # per-response output_modalities and tool_choice overrides.
                        response_options["output_modalities"] = ["text"]
                        response_options["tool_choice"] = "required"
                    else:
                        # Keep ordinary conversation fast and prevent surprise
                        # toolcalls from creating spoken preambles.
                        response_options["output_modalities"] = ["audio"]
                        response_options["tool_choice"] = "none"

                    await connection.response.create(response=response_options)

                elif event.type == "response.function_call_arguments.done":
                    bridge.setState("thinking")
                    try:
                        arguments = json.loads(event.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}

                    print(f"Tool call: {event.name} {arguments}")
                    result = await execute_tool(event.name, arguments)
                    print(
                        "Tool result: "
                        + json.dumps(
                            result, ensure_ascii=False, separators=(",", ":")
                        )
                    )
                    await connection.conversation.item.create(
                        item={
                            "type": "function_call_output",
                            "call_id": event.call_id,
                            "output": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    tool_followup_pending = True

                elif event.type == "response.output_audio.delta":
                    if not first_audio_seen:
                        first_audio_seen = True
                        bridge.setState("speaking")
                        if last_speech_stopped is not None:
                            print(
                                "AVA first audio latency: "
                                f"{time.monotonic() - last_speech_stopped:.2f}s"
                            )
                        print("AVA: [speaking]")
                    player.add(base64.b64decode(event.delta))

                elif event.type == "response.output_audio_transcript.delta":
                    print(event.delta, end="", flush=True)

                elif event.type == "response.output_audio_transcript.done":
                    print()

                elif event.type == "response.done":
                    if first_audio_seen:
                        player.finish_response()

                    if tool_followup_pending:
                        tool_followup_pending = False
                        current_turn_tool_mode = False
                        first_audio_seen = False
                        bridge.setState("thinking")
                        await connection.response.create(
                            response={
                                "instructions": build_tool_instructions(memory),
                                "output_modalities": ["audio"],
                                "tool_choice": "none",
                            }
                        )
                    elif current_turn_tool_mode:
                        # A required tool response should normally contain at least
                        # one function call. If the server ever completes without one,
                        # fail gracefully with a spoken explanation rather than silence.
                        current_turn_tool_mode = False
                        first_audio_seen = False
                        bridge.setState("thinking")
                        await connection.response.create(
                            response={
                                "instructions": (
                                    build_tool_instructions(memory)
                                    + "\n\nEr werd geen toolresultaat geproduceerd. "
                                    "Leg kort uit dat de actuele actie of informatie "
                                    "niet kon worden opgehaald."
                                ),
                                "output_modalities": ["audio"],
                                "tool_choice": "none",
                            }
                        )

                elif event.type == "error":
                    print(f"Realtime error: {event.error.message}")
                    bridge.setState("idle")

        sender = asyncio.create_task(send_microphone())
        receiver = asyncio.create_task(receive_events())

        try:
            await asyncio.gather(sender, receiver)
        finally:
            sender.cancel()
            receiver.cancel()
            player.stop()
            bridge.setState("idle")


def run_realtime_thread(bridge):
    try:
        asyncio.run(realtime_worker(bridge))
    except Exception as error:
        print(f"Realtime AVA stopped: {error}")
        bridge.setState("idle")


def main():
    core.configure_wayland_from_ssh()

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    bridge = core.AvatarBridge()
    engine.rootContext().setContextProperty("avatarBridge", bridge)

    qml_file = Path(__file__).with_name("Avatar.qml")
    engine.load(QUrl.fromLocalFile(str(qml_file)))
    if not engine.rootObjects():
        raise RuntimeError("Could not load AVA avatar UI.")

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    worker = threading.Thread(
        target=run_realtime_thread,
        args=(bridge,),
        daemon=True,
    )
    worker.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())