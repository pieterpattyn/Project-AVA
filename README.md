# Project AVA

**Project AVA** is an open-source AI home assistant built around Raspberry Pi.

The goal is not to create another chatbot, but a modular digital home companion that combines speech, a visual avatar, persistent memory, Home Assistant and cloud/local AI services.

## Current milestone

**AVA v1.0-rc1**

The first release candidate consolidates the proven v0.9.3 stack into one production entry point:

- OpenAI Realtime voice conversation
- PySide6/QML avatar states
- Persistent local memory
- Current date/time and weather tools
- Home Assistant discovery and state lookup
- Verified light/switch control
- Verified light brightness control
- Serialized Realtime tool responses
- Local speech guard and ghost-transcript filtering

The earlier `realtime_tools_v08x` and `realtime_tools_v09x` files remain as historical checkpoints. New development should target:

```text
software/ava_core/realtime_v1.py
```

## Run

From the project virtual environment:

```bash
git pull
python software/ava_core/realtime_v1.py
```

Home Assistant credentials are read from the local `.env` file:

```env
OPENAI_API_KEY=...
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=...
```

Never commit `.env` or access tokens.

## Tests

Pure memory/entity/verification logic has a small regression suite:

```bash
python -m unittest discover -s software/ava_core/tests -v
```

The final release gate is still a live Raspberry Pi test because audio devices, Realtime streaming and Home Assistant service calls cannot be meaningfully proven by unit tests alone. Humans have somehow invented hardware.

## Current hardware

- Raspberry Pi 4B, 4 GB
- DFRobot 7-inch DSI capacitive touchscreen
- Logitech C270 webcam + microphone
- External speakers
- Home Assistant

## Design principles

- Modular by design
- Home Assistant remains the source of truth for home automation
- Local-first where practical
- Cloud services where they add clear value
- AI-provider components should remain replaceable
- Preserve known-good checkpoints during risky experiments

## Next after v1.0

The release candidate should be tested as a whole before adding another major feature. Post-v1 candidates include:

- Wake word
- Barge-in / interruption while AVA speaks
- Camera/vision integration
- Broader Home Assistant domains with explicit safety rules
- Cleaner packaging/service startup
- Mobile/robot body experiments

See `docs/Roadmap.md` and `docs/Architecture.md` for details.

---

*"A smart home deserves a smart companion."*
