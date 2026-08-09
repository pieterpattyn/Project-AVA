# AVA Architecture

## Purpose

AVA is a modular AI home assistant running on a Raspberry Pi-based console.

The current stable v1.0 runtime combines:

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
7. Known-good checkpoints are preserved while experimental work evolves.

## Current v1.0 runtime

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

During AVA playback the microphone path is currently suppressed. This keeps the v1.0 baseline stable but means barge-in is not yet supported.

### Think

- OpenAI Realtime session
- Persistent conversation context
- Local persistent memory context
- Function/tool selection
- Serialized tool follow-up responses so only one Realtime response is active at a time
- Local high-precision tool-turn router

#### Silent tool routing

v1.0 no longer relies only on an instruction telling the model not to speak before a tool call.

Before creating a response, AVA classifies the validated user transcript into one of two paths:

1. **Normal conversation** uses direct audio output with tool use disabled.
2. **Likely tool turns** use a text-only response with tool use required.

The text-only tool pass can emit one or more function calls but cannot produce spoken audio. After all tool outputs are attached and that response is complete, AVA creates one compact audio follow-up with further tool use disabled.

This keeps normal conversation low-latency while structurally preventing weather/time/Home Assistant turns from saying things like "ik check even" before actually doing the work.

### Speak

- Realtime PCM audio output
- Callback-driven `sounddevice` playback
- Raspberry Pi headphone output
- Prebuffered playback for stable audio
- Avatar state transitions for listening/thinking/speaking
- Spoken tool answers only after tool results are available

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

The C270 camera is physically available but is not part of v1.0 runtime logic yet. Vision should be added as a separate capture/tool layer rather than being embedded directly into the audio loop.

## Historical checkpoints

The older realtime/tool files remain in the repository as regression checkpoints. They are no longer intended as the normal startup path.

The stable non-Realtime v0.5.1 path is also retained as a fallback reference.

## Validation

v1.0 passed the deterministic regression suite and a live Raspberry Pi validation covering persistent memory, residence-based weather, Home Assistant on/off control, multiple brightness percentages, avatar/audio behaviour and clean shutdown.

After changes to audio, Realtime routing or Home Assistant control, rerun both the unit tests and an appropriate live hardware smoke test.

See `docs/Roadmap.md` for post-v1 work.
