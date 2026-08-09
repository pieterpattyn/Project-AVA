# Project AVA v1.1 - Wake Word

## Goal

AVA v1.1 adds a local wake-word gate using the phrase **Hey AVA**.

The proven v1.0 runtime remains untouched while the wake-word path is integrated in stages. This keeps the release baseline available if experimental gating code misbehaves, because microphones and asynchronous state machines apparently enjoy drama.

## Architecture

```text
C270 microphone
      |
      v
Project AVA wake-word client
      |
      | 16 kHz / 16-bit mono PCM over Wyoming TCP
      v
wyoming-openwakeword container
      |
      v
hey_ava.tflite
```

The wake-word inference stack runs in a separate container. This keeps Python/TensorFlow/ONNX wake-word dependencies out of AVA's stable Python 3.13 virtual environment.

The Wyoming server listens only on localhost by default:

```text
tcp://127.0.0.1:10400
```

## Why Wyoming openWakeWord

The Wyoming protocol is a small streaming protocol used by Home Assistant voice services. The openWakeWord Wyoming server accepts 16 kHz, 16-bit mono PCM and emits a detection event when a configured wake-word model fires.

A separate service is useful on AVA's Debian 13 / Python 3.13 Raspberry Pi because the direct openWakeWord Python dependency stack has had ARM64/Python-version compatibility problems, while Wyoming openWakeWord packages the inference engine separately.

## Custom Hey AVA model

Project AVA uses a custom model trained for:

```text
hey ava
```

The trained TensorFlow Lite model lives at:

```text
models/wakeword/hey_ava.tflite
```

The model was validated on the target AVA Raspberry Pi with repeated successful standalone detections before Realtime integration began.

## Start the local wake-word server

From the repository root:

```bash
docker compose -f docker-compose.wakeword.yml up -d
```

Inspect it with:

```bash
docker compose -f docker-compose.wakeword.yml ps
docker logs ava-wakeword --tail 100
```

Expected port mapping:

```text
127.0.0.1:10400->10400/tcp
```

## Phase A - standalone detection

Run:

```bash
python software/ava_core/wakeword_probe.py
```

Expected flow:

```text
Wake-word server: 127.0.0.1:10400
Wake word: hey_ava
Microphone: C270 HD WEBCAM: USB Audio ...
Luisteren... zeg 'Hey AVA'. Ctrl+C stopt.
DETECTED: hey_ava
```

This probe exits after one detection. Phase A proves the microphone -> resampler -> Wyoming -> custom model path without touching OpenAI Realtime or the v1.0 audio loop.

## Phase B - wake-to-Realtime handoff

Phase B adds:

```text
software/ava_core/realtime_v11.py
```

It deliberately does not modify `realtime_v1.py`.

Current phase-B flow:

```text
idle
  -> local Hey AVA detection
  -> release wake-word microphone stream
  -> start proven v1.0 Realtime runtime
  -> normal v1.0 conversation until Ctrl+C
```

Run it with:

```bash
python software/ava_core/realtime_v11.py
```

Expected startup flow:

```text
Project AVA v1.1-dev
State: idle - lokaal wachten op 'Hey AVA'.
Wake-word server: 127.0.0.1:10400
Luisteren... zeg 'Hey AVA'. Ctrl+C stopt.
DETECTED: hey_ava
Wake accepted: hey_ava
Starting proven AVA v1.0 Realtime runtime...
Project AVA v1.0
...
```

Phase B is intentionally a handoff smoke test. Once awakened, AVA remains in the existing v1.0 continuous conversation mode.

## Phase C - final wake gate

The next integration step is the real state machine:

```text
idle -> Hey AVA -> listening -> thinking -> speaking -> idle
```

Phase C will:

- keep idle microphone audio local only;
- open Realtime user audio only after wake detection;
- return to wake-word idle after a completed interaction or timeout;
- keep the avatar visible while idle;
- add a short pre-roll buffer so commands spoken immediately after `Hey AVA` do not lose their first syllable.

This stage is deferred until the phase-B handoff is proven on the actual Pi.

## Configuration

Wake-word code supports these environment variables:

```env
AVA_WAKEWORD_URI=tcp://127.0.0.1:10400
AVA_WAKEWORD_NAME=hey_ava
```

The defaults already match the Project AVA v1.1 setup.
