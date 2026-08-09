import subprocess
import wave
from enum import Enum

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


class AVAState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class AVA:
    SAMPLE_RATE = 16000
    RECORDING_DURATION = 5
    MIC_NAME = "C270"
    RECORDING_FILE = "/tmp/ava_input.wav"
    SPEECH_FILE = "/tmp/ava_reply.mp3"

    def __init__(self):
        self.state = AVAState.IDLE
        self.client = OpenAI()
        self.microphone = self.find_microphone(self.MIC_NAME)

    def set_state(self, state):
        self.state = state
        print(f"\nAVA state: {state.value}")

    def find_microphone(self, name):
        for index, device in enumerate(sd.query_devices()):
            if (
                name.lower() in device["name"].lower()
                and device["max_input_channels"] > 0
            ):
                print(
                    f"Microphone: {device['name']} "
                    f"(device {index})"
                )
                return index

        raise RuntimeError(
            f"Microphone containing '{name}' not found."
        )

    def record_audio(self):
        self.set_state(AVAState.LISTENING)
        print(
            f"Listening for {self.RECORDING_DURATION} seconds..."
        )

        audio = sd.rec(
            int(self.RECORDING_DURATION * self.SAMPLE_RATE),
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            device=self.microphone,
        )

        sd.wait()

        with wave.open(self.RECORDING_FILE, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(audio.tobytes())

        peak = float(
            np.max(np.abs(audio.astype(np.float32))) / 32768.0
        )
        print(f"Microphone peak: {peak:.3f}")

    def transcribe(self):
        self.set_state(AVAState.THINKING)
        print("Transcribing...")

        with open(self.RECORDING_FILE, "rb") as audio_file:
            transcript = self.client.audio.transcriptions.create(
                model="gpt-4o-transcribe",
                file=audio_file,
                language="nl",
                prompt=(
                    "Dit is standaard Nederlands uit België. "
                    "De assistent heet AVA. "
                    "Verwacht nederlandse zinnen en woorden zoals: "
                    "AVA, stel jezelf voor, wie je bent, wat kun je, "
                    "zet het licht aan, hoe is het weer."
                 )
            )

        print(f"AVA heard: {transcript.text}")
        return transcript.text

    def think(self, text):
        print("Thinking...")

        response = self.client.responses.create(
            model="gpt-5-mini",
            input=[
                {
                    "role": "system",
                    "content": (
                        "Je bent AVA, een slimme huisassistent. "
                        "Antwoord kort, natuurlijk en in het Nederlands. "
                        "Je bent rustig, analytisch en hebt droge humor."
                    ),
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
        )

        print(f"AVA: {response.output_text}")
        return response.output_text

    def speak(self, text):
        self.set_state(AVAState.SPEAKING)

        with self.client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        ) as audio_response:
            audio_response.stream_to_file(self.SPEECH_FILE)

        print("Speaking...")

        subprocess.run(
            [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                self.SPEECH_FILE,
            ],
            check=True,
        )

    def conversation_once(self):
        self.record_audio()
        text = self.transcribe()
        reply = self.think(text)
        self.speak(reply)
        self.set_state(AVAState.IDLE)

    def start(self):
        print("Project AVA")
        print("AVA Core v0.2 - First Words")
        print("----------------------------")

        self.set_state(AVAState.IDLE)
        self.conversation_once()


if __name__ == "__main__":
    AVA().start()
