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


AVA_PERSONALITY = """
Je bent AVA, een persoonlijke slimme assistent die op een Raspberry Pi draait.
Je spreekt standaard Nederlands uit België.

Karakter:
- Intelligent, analytisch, kalm en betrouwbaar.
- Direct en eerlijk. Je verzint geen feiten als je iets niet weet.
- Je hebt droge, subtiele humor, maar forceert geen grapjes.
- Je bent vriendelijk zonder overdreven beleefd of onderdanig te zijn.
- Je antwoorden klinken natuurlijk wanneer ze hardop worden uitgesproken.
- Je bent nieuwsgierig wanneer dat nuttig is, maar stelt niet onnodig vragen.
- Je bent een assistent met een eigen herkenbaar karakter, geen generieke chatbot.

Spreekstijl:
- Antwoord meestal kort: één tot vier zinnen.
- Gebruik gewone spreektaal en vermijd lange opsommingen.
- Gebruik geen markdown, emoji of speciale opmaak in gesproken antwoorden.
- Noem jezelf AVA.
- Behandel de gebruiker als iemand die technisch onderlegd is.

Context:
- Dit is Project AVA.
- Je draait momenteel als prototype op een Raspberry Pi.
- Je kunt luisteren, nadenken, spreken en een avatar op een scherm aansturen.
- Doe nooit alsof je hardwarefuncties kunt uitvoeren die nog niet daadwerkelijk zijn aangesloten.
""".strip()


class AVA:
    SAMPLE_RATE = 16000
    RECORDING_DURATION = 5
    MIC_NAME = "C270"
    RECORDING_FILE = "/tmp/ava_input.wav"
    SPEECH_FILE = "/tmp/ava_reply.mp3"
    MAX_HISTORY_MESSAGES = 12

    def __init__(self):
        self.state = AVAState.IDLE
        self.client = OpenAI()
        self.microphone = self.find_microphone(self.MIC_NAME)
        self.history = []

    def set_state(self, state):
        self.state = state
        print(f"\nAVA state: {state.value}")

    def find_microphone(self, name):
        for index, device in enumerate(sd.query_devices()):
            if (
                name.lower() in device["name"].lower()
                and device["max_input_channels"] > 0
            ):
                print(f"Microphone: {device['name']} (device {index})")
                return index

        raise RuntimeError(f"Microphone containing '{name}' not found.")

    def record_audio(self):
        self.set_state(AVAState.LISTENING)
        print(f"Listening for {self.RECORDING_DURATION} seconds...")

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

        peak = float(np.max(np.abs(audio.astype(np.float32))) / 32768.0)
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
                    "Verwacht Nederlandse zinnen en woorden zoals: "
                    "AVA, stel jezelf voor, wie ben je, wat kun je, "
                    "zet het licht aan, hoe is het weer."
                ),
            )

        print(f"AVA heard: {transcript.text}")
        return transcript.text

    def think(self, text):
        print("Thinking...")

        conversation = [
            {"role": "system", "content": AVA_PERSONALITY},
            *self.history,
            {"role": "user", "content": text},
        ]

        response = self.client.responses.create(
            model="gpt-5-mini",
            input=conversation,
        )

        reply = response.output_text.strip()

        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": reply})
        self.history = self.history[-self.MAX_HISTORY_MESSAGES :]

        print(f"AVA: {reply}")
        return reply

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

        if not text.strip():
            print("No speech detected.")
            return

        reply = self.think(text)
        self.speak(reply)

    def start(self):
        print("Project AVA")
        print("AVA Core v0.3 - First Personality")
        print("----------------------------------")
        print("Press Ctrl+C to stop AVA.")

        self.set_state(AVAState.IDLE)

        try:
            while True:
                try:
                    self.conversation_once()
                except Exception as error:
                    print(f"\nConversation error: {error}")
                finally:
                    self.set_state(AVAState.IDLE)
        except KeyboardInterrupt:
            print("\nStopping AVA...")
            self.set_state(AVAState.IDLE)
            print("Goodbye.")


if __name__ == "__main__":
    AVA().start()
