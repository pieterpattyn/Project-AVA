# AVA wake-word models

Project AVA v1.1 uses a local Wyoming openWakeWord service.

Place the custom openWakeWord TensorFlow Lite model here as:

```text
models/wakeword/hey_ava.tflite
```

The model is intentionally not fabricated or substituted with a different wake word. `Hey AVA` needs a real trained openWakeWord model before the v1.1 wake gate is connected to the stable v1.0 runtime.

The official Home Assistant/openWakeWord training flow produces both `.tflite` and `.onnx`; AVA's Wyoming service uses the `.tflite` file.
