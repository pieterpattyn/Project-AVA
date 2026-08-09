# Project AVA

**Project AVA** is an open-source AI home assistant built around Raspberry Pi.

The goal is not to create another chatbot, but a modular digital home companion that combines speech, a visual avatar, persistent memory, Home Assistant and cloud/local AI services.

## Current milestone

**AVA v1.0**

The v1.0 release consolidates the proven v0.9.3 stack into one production entry point and has passed both the regression suite and live Raspberry Pi/Home Assistant validation.

Current baseline:

- OpenAI Realtime voice conversation
- PySide6/QML avatar states
- Persistent local memory
- Current date/time and weather tools
- Home Assistant discovery and state lookup
- Verified light/switch control
- Verified light brightness control
- Serialized Realtime tool responses
- Silent deterministic tool routing before spoken follow-up
- Local speech guard and ghost-transcript filtering

The earlier `realtime_tools_v08x` and `realtime_tools_v09x` files remain as historical checkpoints. New development targets:

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

Pure memory/entity/verification/tool-routing logic has a regression suite:

```bash
python -m unittest discover -s software/ava_core/tests -v
```

The live Raspberry Pi test remains important after changes that touch audio devices, Realtime streaming, tool routing or Home Assistant service calls. Unit tests are useful, but sadly they still cannot switch on a physical lamp by moral authority alone.

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

## Tool routing

v1.0 separates ordinary conversation from current/device tool turns before creating a Realtime response.

Likely tool turns such as weather, current date/time and Home Assistant commands use a silent text-only tool-selection pass with tools required. Only after the tool result is available does AVA create an audio response, with further tool use disabled for that spoken follow-up. Normal conversation stays on the direct low-latency audio path with tools disabled.

This makes the no-preamble rule structural instead of merely asking the model to behave itself, which history has shown is an optimistic software architecture.

## Next after v1.0

Post-v1 candidates include:

- Wake word
- Barge-in / interruption while AVA speaks
- Camera/vision integration
- Broader Home Assistant domains with explicit safety rules
- Cleaner packaging/service startup
- Mobile/robot body experiments

See `docs/Roadmap.md` and `docs/Architecture.md` for details.

---

*"A smart home deserves a smart companion."*
