# Project AVA Roadmap

## Completed checkpoints

- **v0.1-v0.4** - hardware, speech and avatar foundations
- **v0.5.1** - stable non-Realtime fallback conversation stack
- **v0.6.1** - Realtime audio plus local speech guard
- **v0.7.2** - stable persistent memory
- **v0.8.2** - current time/weather tools plus robust residence memory
- **v0.9.3** - verified Home Assistant light/switch control and brightness
- **v1.0** - consolidated stable Realtime runtime

These files remain in the repository as historical checkpoints so regressions can be isolated without rewriting history. Civilization has enough archaeology already.

## v1.0 - Stable baseline

Primary entry point:

```text
software/ava_core/realtime_v1.py
```

Completed release goals:

- Removed the runtime chain of v0.8/v0.9 monkey-patch wrappers
- Kept one clear Realtime production entry point
- Preserved proven audio, avatar and local speech-guard behaviour
- Preserved persistent name/preference/residence memory
- Preserved current time and weather tools
- Preserved Home Assistant discovery/state tools
- Preserved verified `light`/`switch` control and brightness
- Serialized Realtime tool follow-ups correctly
- Added regression tests for memory, Home Assistant verification and tool routing
- Added deterministic silent tool routing so tool turns do not speak a preamble
- Passed live Raspberry Pi validation with memory, weather and Home Assistant control
- Corrected version/startup documentation

Regression command:

```bash
python -m unittest discover -s software/ava_core/tests -v
```

Production launch:

```bash
python software/ava_core/realtime_v1.py
```

## After v1.0

### Wake word

Keep the microphone path local and only open a conversational turn after the wake-word gate.

### Barge-in

Allow the user to interrupt AVA while audio is playing. This requires removing the current microphone suppression during playback and adding safe response cancellation.

### Vision

Add controlled webcam capture and image understanding without coupling camera code to the audio loop.

### Home Assistant expansion

Potential next domains include media players, climate and covers. Locks, alarms and other safety-sensitive domains require explicit policy and confirmation rules before write access is enabled.

### Packaging and startup

Move from manual Python launch toward a clean service/launcher while preserving debuggable console logs.

### Mobile hardware

Keep movement/robot-body control isolated behind explicit tools and safety constraints.
