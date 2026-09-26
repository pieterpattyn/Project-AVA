"""Hermes plugin exposing Project AVA house audio tools."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from software.ava_house.tapo_talk import CAMERA_HOSTS, speak_room


ROOM_ALIASES = {
    "woonkamer": "living",
    "living": "living",
    "bureau": "bureau",
    "kantoor": "bureau",
    "garage": "garage",
    "bar": "bar",
}


def _normalize_room(room: str) -> str:
    room = room.strip().lower()
    return ROOM_ALIASES.get(room, room)


def register(ctx):
    schema = {
        "name": "ava_speak_room",
        "description": (
            "Speak a short message aloud in a room through Project AVA's "
            "configured local camera speaker. Use this only when the user "
            "explicitly wants AVA to say or announce something in a room."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "room": {
                    "type": "string",
                    "enum": sorted(CAMERA_HOSTS),
                    "description": "Room where the message should be spoken.",
                },
                "text": {
                    "type": "string",
                    "description": "The exact short message to speak aloud.",
                },
            },
            "required": ["room", "text"],
        },
    }

    def handle_speak_room(params, **kwargs):
        del kwargs
        room = _normalize_room(str(params.get("room", "")))
        text = str(params.get("text", "")).strip()

        if room not in CAMERA_HOSTS:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Unknown room: {room}",
                    "known_rooms": sorted(CAMERA_HOSTS),
                }
            )

        if not text:
            return json.dumps({"success": False, "error": "Text is required."})

        try:
            speak_room(room, text)
        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "room": room,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        return json.dumps({"success": True, "room": room, "spoken": text})

    ctx.register_tool(
        name="ava_speak_room",
        toolset="ava_house",
        schema=schema,
        handler=handle_speak_room,
    )


    call_schema = {
        "name": "ava_call_person",
        "description": (
            "Call or address a person aloud in a specific room through Project AVA. "
            "Use this for requests such as 'roep Wies in de bar' or 'call Pieter in the garage'. "
            "If no extra message is provided, speak the person's name clearly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "room": {
                    "type": "string",
                    "enum": sorted(CAMERA_HOSTS),
                    "description": "Room where the person should be called.",
                },
                "person": {
                    "type": "string",
                    "description": "Name of the person to call.",
                },
                "message": {
                    "type": "string",
                    "description": "Optional extra message after the person's name.",
                },
            },
            "required": ["room", "person"],
        },
    }

    def handle_call_person(params, **kwargs):
        del kwargs
        room = _normalize_room(str(params.get("room", "")))
        person = str(params.get("person", "")).strip()
        message = str(params.get("message", "")).strip()

        if room not in CAMERA_HOSTS:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Unknown room: {room}",
                    "known_rooms": sorted(CAMERA_HOSTS),
                }
            )

        if not person:
            return json.dumps({"success": False, "error": "Person is required."})

        spoken = f"{person}!"
        if message:
            spoken += f" {message}"

        try:
            speak_room(room, spoken)
        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "room": room,
                    "person": person,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        return json.dumps(
            {
                "success": True,
                "room": room,
                "person": person,
                "spoken": spoken,
            }
        )

    ctx.register_tool(
        name="ava_call_person",
        toolset="ava_house",
        schema=call_schema,
        handler=handle_call_person,
    )


    time_schema = {
        "name": "ava_current_time",
        "description": (
            "Get the current local date and time. Use this for questions about the current "
            "time, date, weekday, or 'now'. Defaults to Europe/Brussels for AVA's home."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": (
                        "Optional IANA timezone such as Europe/Brussels or Asia/Shanghai. "
                        "Omit for Europe/Brussels."
                    ),
                },
            },
            "required": [],
        },
    }

    def handle_current_time(params, **kwargs):
        del kwargs
        timezone = str(params.get("timezone", "")).strip() or "Europe/Brussels"
        try:
            now = datetime.now(ZoneInfo(timezone))
        except ZoneInfoNotFoundError:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Unknown timezone: {timezone}",
                }
            )

        return json.dumps(
            {
                "success": True,
                "timezone": timezone,
                "iso": now.isoformat(timespec="seconds"),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "weekday": now.strftime("%A"),
                "utc_offset": now.strftime("%z"),
            }
        )

    ctx.register_tool(
        name="ava_current_time",
        toolset="ava_house",
        schema=time_schema,
        handler=handle_current_time,
    )
