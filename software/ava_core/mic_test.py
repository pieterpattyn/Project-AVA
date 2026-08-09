import sounddevice as sd
import numpy as np

SAMPLE_RATE = 16000
DURATION = 5

print("Recording for 5 seconds...")

audio = sd.rec(
    int(DURATION * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="float32",
)

sd.wait()

peak = float(np.max(np.abs(audio)))

print(f"Done. Peak level: {peak:.3f}")

if peak < 0.01:
    print("Warning: microphone level is very low.")
else:
    print("Microphone input looks good.")
