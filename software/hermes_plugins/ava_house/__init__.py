"""Hermes plugin exposing Project AVA house audio tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from software.ava_house.tapo_talk import CAMERA_HOSTS, speak_room

CAMERA_HOSTS["living"] = "73.10.11.66"


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
        room = str(params.get("room", "")).strip().lower()
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
