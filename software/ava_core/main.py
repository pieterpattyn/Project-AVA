import subprocess
import time
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
- Begin een antwoord niet met je eigen naam.
- Noem jezelf alleen AVA wanneer je naam inhoudelijk relevant is.
- Behandel de gebruiker als iemand die technisch onderlegd is.

Context:
- Dit is Project AVA.
- Je draait momenteel als prototype op een Raspberry Pi.
- Je kunt luisteren, nadenken, spreken en een avatar op een scherm aansturen.
- Doe nooit alsof je hardwarefuncties kunt uitvoeren die nog niet daadwerkelijk zijn aangesloten.
""".strip()


TTS_INSTRUCTIONS = (
    "Spreek natuurlijk en rustig in standaard Belgisch-Nederlands. "
    "Gebruik een consistente Nederlandstalige uitspraak en intonatie. "
    "De naam AVA wordt uitgesproken als 'Ava', met een duidelijke korte A aan het begin, "
    "niet als 'Eva'. Spreek technisch klinkende woorden helder uit."
)


class AVA:
    SAMPLE_RATE = 16000
    MIC_NAME = "C270"
    RECORDING_FILE = "/tmp/ava_input.wav"
    SPEECH_FILE = "/tmp/ava_reply.mp3"
    MAX_HISTORY_MESSAGES = 12

    BLOCK_DURATION = 0.1
    START_THRESHOLD = 0.035
    END_THRESHOLD = 0.025
    SILENCE_TO_STOP = 0.9
    WAIT_FOR_SPEECH_TIMEOUT = 30.0
    MAX_UTTERANCE_DURATION = 20.0
    PRE_ROLL_DURATION = 0.4

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

    @staticmethod
    def audio_level(block):
        samples = block.astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(samples * samples)))

    def record_audio(self):
        self.set_state(AVAState.LISTENING)
        print("Waiting for speech...")

        block_size = int(self.SAMPLE_RATE * self.BLOCK_DURATION)
        pre_roll_blocks = max(1, int(self.PRE_ROLL_DURATION / self.BLOCK_DURATION))
        pre_roll = []
        recorded = []
        speech_started = False
        silence_duration = 0.0
        wait_started = time.monotonic()
        speech_started_at = None
        peak = 0.0

        with sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            device=self.microphone,
            blocksize=block_size,
        ) as stream:
            while True:
                block, overflowed = stream.read(block_size)
                if overflowed:
                    print("Audio input overflow detected.")

                block = block.copy()
                level = self.audio_level(block)
                peak = max(peak, level)

                if not speech_started:
                    pre_roll.append(block)
                    if len(pre_roll) > pre_roll_blocks:
                        pre_roll.pop(0)

                    if level >= self.START_THRESHOLD:
                        speech_started = True
                        speech_started_at = time.monotonic()
                        recorded.extend(pre_roll)
                        pre_roll.clear()
                        print("Speech detected.")
                    elif time.monotonic() - wait_started >= self.WAIT_FOR_SPEECH_TIMEOUT:
                        print("No speech detected within timeout.")
                        return False
                else:
                    recorded.append(block)

                    if level < self.END_THRESHOLD:
                        silence_duration += self.BLOCK_DURATION
                    else:
                        silence_duration = 0.0

                    utterance_duration = time.monotonic() - speech_started_at
                    if silence_duration >= self.SILENCE_TO_STOP:
                        break
                    if utterance_duration >= self.MAX_UTTERANCE_DURATION:
                        print("Maximum speech duration reached.")
                        break

        audio = np.concatenate(recorded, axis=0)

        with wave.open(self.RECORDING_FILE, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(audio.tobytes())

        print(f"Microphone RMS peak: {peak:.3f}")
        print(f"Recorded: {len(audio) / self.SAMPLE_RATE:.1f} seconds")
        return True

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
            instructions=TTS_INSTRUCTIONS,
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
        if not self.record_audio():
            return

        text = self.transcribe()

        if not text.strip():
            print("No speech detected.")
            return

        reply = self.think(text)
        self.speak(reply)

    def start(self):
        print("Project AVA")
        print("AVA Core v0.4 - Natural Conversation")
        print("-------------------------------------")
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
