# Project AVA v1.1 - Wake Word

## Goal

AVA v1.1 adds a local wake-word gate using the phrase **Hey AVA**.

The proven v1.0 runtime remains untouched during phase A. Wake-word detection is first validated as an independent local path before it is allowed to gate the Realtime conversation loop.

## Architecture

```text
C270 microphone
      |
      v
Project AVA wakeword_probe.py
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

A separate service is useful on AVA's Debian 13 / Python 3.13 Raspberry Pi because the direct openWakeWord Python dependency stack has had ARM64/Python-version compatibility problems, while current Wyoming openWakeWord packages the inference engine separately.

## Custom Hey AVA model

There is no trustworthy `Hey AVA` model in the community collection that Project AVA can simply adopt as its baseline, so v1.1 expects a model trained specifically for the phrase.

Home Assistant's current official wake-word guide uses the openWakeWord training environment and Piper-generated speech. It recommends a short 3-4 syllable phrase and produces both `.tflite` and `.onnx` files. Project AVA uses the `.tflite` model.

Train the phrase as:

```text
hey ava
```

Listen carefully to the generated pronunciation before training. The synthetic pronunciation should match how the phrase will actually be spoken.

Place the resulting model at:

```text
models/wakeword/hey_ava.tflite
```

## Start the local wake-word server

From the repository root:

```bash
docker compose -f docker-compose.wakeword.yml up -d
```

Inspect it with:

```bash
docker compose -f docker-compose.wakeword.yml logs -f
```

## Standalone detection test

With the normal Project AVA virtual environment active:

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

This probe exits after one detection. That is deliberate: phase A proves the microphone -> resampler -> Wyoming -> custom model path without touching OpenAI Realtime or the v1.0 audio loop.

## Phase B

Only after standalone detection is reliable will v1.1 connect the wake gate to the assistant runtime.

Planned state flow:

```text
idle -> Hey AVA detected -> listening -> thinking -> speaking -> idle
```

The final gate will keep microphone audio local while AVA is idle. Realtime user audio is opened only after wake detection. A short pre-roll ring buffer will preserve the beginning of commands spoken immediately after `Hey AVA`.

## Configuration

The probe supports these environment variables:

```env
AVA_WAKEWORD_URI=tcp://127.0.0.1:10400
AVA_WAKEWORD_NAME=hey_ava
```

The defaults already match the Project AVA v1.1 setup.
