from enum import Enum

import numpy as np
import sounddevice as sd


class AVAState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class AVA:
    SAMPLE_RATE = 16000

    def __init__(self):
        self.state = AVAState.IDLE
        self.microphone = self.find_microphone("C270")

    def set_state(self, state: AVAState):
        self.state = state
        print(f"AVA state: {self.state.value}")

    def find_microphone(self, name):
        for index, device in enumerate(sd.query_devices()):
            if name.lower() in device["name"].lower() and device["max_input_channels"] > 0:
                print(f"Microphone: {device['name']} (device {index})")
                return index

        raise RuntimeError(f"Microphone containing '{name}' not found.")

    def listen(self, duration=5):
        self.set_state(AVAState.LISTENING)
        print(f"Listening for {duration} seconds...")

        audio = sd.rec(
            int(duration * self.SAMPLE_RATE),
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=self.microphone,
        )

        sd.wait()

        peak = float(np.max(np.abs(audio)))
        print(f"Microphone peak: {peak:.3f}")

        self.set_state(AVAState.IDLE)

    def start(self):
        print("Project AVA")
        print("AVA Core v0.2 - First Words")
        print("----------------------------")

        self.set_state(AVAState.IDLE)
        self.listen()


if __name__ == "__main__":
    AVA().start()
