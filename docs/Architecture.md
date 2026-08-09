# AVA Architecture

## Purpose

AVA is a modular AI home assistant running on a Raspberry Pi-based console.

The current release candidate combines:

- Realtime voice interaction
- Visual avatar state
- Persistent local user memory
- Current date/time and weather tools
- Home Assistant state and control tools

Future modules include vision, wake word, barge-in and mobile hardware.

## Core principles

1. Home Assistant remains the source of truth for home automation.
2. Audio, display, memory and tools remain separable components.
3. AI-provider-specific code should stay behind a narrow runtime boundary.
4. Local processing is preferred where practical.
5. Cloud services are used where they provide a clear capability or latency advantage.
6. Safety-sensitive write tools require explicit scope and verification.
7. Known-good checkpoints are preserved while experimental branches evolve.

## Current v1.0-rc1 runtime

Primary entry point:

```text
software/ava_core/realtime_v1.py
```

`realtime_v1.py` directly imports reusable hardware/audio/avatar primitives from `realtime_app.py`, but it no longer depends on the historical `realtime_tools_v08x` or `realtime_tools_v09x` monkey-patch chain.

### Listen

- Logitech C270 USB microphone
- Native 48 kHz capture
- Resampling to 24 kHz API PCM
- Server semantic VAD
- Local RMS/peak speech guard
- Ghost-transcript rejection
- Raw server audio item replaced by the validated transcript as authoritative user input

During AVA playback the microphone path is currently suppressed. This keeps the baseline stable but means barge-in is not yet supported.

### Think

- OpenAI Realtime session
- Persistent conversation context
- Local persistent memory context
- Function/tool selection
- Serialized tool follow-up responses so only one Realtime response is active at a time

### Speak

- Realtime PCM audio output
- Callback-driven `sounddevice` playback
- Raspberry Pi headphone output
- Prebuffered playback for stable audio
- Avatar state transitions for listening/thinking/speaking

### Display

- PySide6
- QML avatar (`Avatar.qml`)
- Full-screen state-driven interface

### Remember

Persistent file:

```text
~/.local/share/project-ava/memory.json
```

Current memory supports:

- User name
- Single-valued preferences
- Free-form facts
- Single-valued residence
- Residence cleanup/canonicalization
- Natural-language preference changes

Stored user facts describe the user, not AVA. Response instructions explicitly preserve that person distinction.

### Tools

Current tools:

- `get_current_datetime`
- `get_weather`
- `home_assistant_get_state`
- `home_assistant_find_entities`
- `home_assistant_control`

Home Assistant write access is currently limited to `light` and `switch`.

Control feedback verifies the resulting state instead of assuming that an accepted service call is instantly reflected by Home Assistant. Brightness commands separately verify the requested and reported percentage.

### See

The C270 camera is physically available but is not part of v1.0-rc1 runtime logic yet. Vision should be added as a separate capture/tool layer rather than being embedded directly into the audio loop.

## Historical checkpoints

The older realtime/tool files remain in the repository as regression checkpoints. They are no longer intended as the normal startup path once v1.0-rc1 passes its release gate.

The stable non-Realtime v0.5.1 path is also retained as a fallback reference.

## Release gate

The v1.0 release candidate must pass both pure regression tests and live Raspberry Pi validation. Unit tests cover deterministic memory, entity matching and verification logic; live tests cover audio devices, Realtime streaming, avatar behaviour and real Home Assistant service calls.

See `docs/Roadmap.md` for the live test checklist.
