# AVA Platform Decision

## Decision

For AVA v0.2, use a cloud-first realtime conversation architecture while keeping the client modular and provider-independent.

The first implementation target is OpenAI Realtime, with Gemini Live retained as the primary alternative. py-xiaozhi remains an important reference and potential component source, but AVA will not depend on it as the application framework.

## Why

AVA's highest-priority user experience is fast, natural voice conversation with a responsive on-screen avatar. Heavy AI inference does not need to run on the Raspberry Pi 4. The Pi should remain responsible for audio I/O, display, avatar state, camera access, and local integrations.

## Current candidates

### OpenAI Realtime

Strengths:
- Low-latency realtime voice interaction
- Native speech-to-speech
- WebRTC and WebSocket transport options
- Audio input/output streaming
- Server-side voice activity detection and noise reduction options
- Suitable for a thin-client Raspberry Pi design

Use in AVA:
- First provider to prototype for v0.2

### Gemini Live

Strengths:
- Realtime bidirectional audio
- Native audio output
- Realtime video/image input support
- WebSocket SDK available

Caveat:
- Live API is currently documented as Preview

Use in AVA:
- Primary fallback / comparison provider
- Particularly interesting for later AVA Vision work

### py-xiaozhi

Strengths:
- Explicit Raspberry Pi / ARM support
- Async architecture
- Realtime voice streaming
- PySide6 + QML GUI option
- Camera support
- Offline wake word support
- MCP and IoT integration
- Plugin architecture

Caveats:
- Python requirement is currently 3.10-3.12, while AVA's Raspberry Pi OS uses Debian 13 / Python 3.13 by default
- AVA should not become coupled to the Xiaozhi protocol or backend

Use in AVA:
- Reference implementation
- Candidate source for individual ideas/components
- Possible compatibility adapter later

## Raspberry Pi role

The Raspberry Pi 4B (4 GB) acts as a thin client and local controller.

Local responsibilities:
- microphone capture
- speaker output
- avatar/UI
- wake word later
- camera access
- Home Assistant integration
- local state and configuration

Cloud responsibilities initially:
- speech understanding
- language reasoning
- speech generation

## Conversation state model

AVA UI must expose at least four states from the start:

1. idle
2. listening
3. thinking
4. speaking

The avatar layer must remain independent of the selected AI provider.

## v0.2 goal: First Words

A successful v0.2 prototype must:
- capture microphone audio
- send it to a realtime cloud model
- receive a spoken response with low perceived latency
- play response audio through the speakers
- expose conversation state events to the avatar/UI
- keep provider-specific code behind a replaceable adapter

## Wake word

Wake-word work is deferred until the basic realtime conversation loop is proven. openWakeWord currently has ARM/Python dependency friction on recent Raspberry Pi OS releases, so it should not block v0.2.

## Status

Decision accepted for prototype implementation. Re-evaluate after latency, stability, cost, and integration testing on the Raspberry Pi 4B.