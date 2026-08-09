# Project AVA Roadmap

## Completed checkpoints

- **v0.1-v0.4** - hardware, speech and avatar foundations
- **v0.5.1** - stable non-Realtime fallback conversation stack
- **v0.6.1** - Realtime audio plus local speech guard
- **v0.7.2** - stable persistent memory
- **v0.8.2** - current time/weather tools plus robust residence memory
- **v0.9.3** - verified Home Assistant light/switch control and brightness

These files remain in the repository as historical checkpoints so regressions can be isolated without rewriting history. Civilization has enough archaeology already.

## v1.0-rc1 - Consolidation

Primary entry point:

```text
software/ava_core/realtime_v1.py
```

Goals:

- Remove the runtime chain of v0.8/v0.9 monkey-patch wrappers
- Keep one clear Realtime production entry point
- Preserve the proven audio, avatar and local speech-guard behaviour
- Preserve persistent name/preference/residence memory
- Preserve current time and weather tools
- Preserve Home Assistant discovery/state tools
- Preserve verified `light`/`switch` control and brightness
- Serialize Realtime tool follow-ups correctly
- Add regression tests for memory and Home Assistant verification helpers
- Correct version/startup documentation

### v1.0 release gate

Before renaming the release candidate to v1.0, run:

```bash
python -m unittest discover -s software/ava_core/tests -v
python software/ava_core/realtime_v1.py
```

Then live-test at minimum:

1. Normal conversation
2. Name and preference recall
3. Residence recall and `weer bij mij`
4. Current date/time
5. Home Assistant entity lookup
6. Light on/off
7. Brightness at multiple percentages
8. Clean Ctrl+C shutdown

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
