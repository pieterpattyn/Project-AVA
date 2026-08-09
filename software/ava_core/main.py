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
- Antwoord meestal kort: één tot drie zinnen.
- Geef eerst het directe antwoord; voeg alleen uitleg toe als die nuttig is.
- Gebruik gewone spreektaal en vermijd lange opsommingen.
- Gebruik geen markdown, emoji of speciale opmaak in gesproken antwoorden.
- Begin een antwoord niet met je eigen naam.
- Noem jezelf alleen AVA wanneer je naam inhoudelijk relevant is.
- Behandel de gebruiker als iemand die technisch onderlegd is.

Context:
- Dit is Project AVA.
- Je draait momenteel als prototype op een Raspberry Pi.
- Je kunt luisteren, nadenken, spreken en een avatar op een scherm aansturen.
- Permanente opslag van persoonlijke gegevens is nog niet geïmplementeerd.
- Zeg dus niet dat je iets tussen sessies zult onthouden tenzij dat echt is toegevoegd.
- Verzin geen shellcommando's of hardwaremogelijkheden om ontbrekende functies te simuleren.
- Doe nooit alsof je hardwarefuncties kunt uitvoeren die nog niet daadwerkelijk zijn aangesloten.
""".strip()


TTS_INSTRUCTIONS = (
    "Spreek natuurlijk en rustig in standaard Belgisch-Nederlands. "
    "Gebruik een consistente Nederlandstalige uitspraak en intonatie. "
    "De naam AVA wordt uitgesproken als 'Ava', met een duidelijke korte A aan het begin, "
    "niet als 'Eva'. Spreek technisch klinkende woorden helder uit."
)

TRANSCRIPTION_PROMPT = "Belgisch-Nederlands gesprek. De assistent heet AVA."


class AVA:
    SAMPLE_RATE = 16000
    MIC_NAME = "C270"
    RECORDING_FILE = "/tmp/ava_input.wav"
    SPEECH_FILE = "/tmp/ava_reply.wav"
    MAX_HISTORY_MESSAGES = 10

    BLOCK_DURATION = 0.1
    SILENCE_TO_STOP = 0.65
    WAIT_FOR_SPEECH_TIMEOUT = 30.0
    MAX_UTTERANCE_DURATION = 20.0
    PRE_ROLL_DURATION = 0.35
    CALIBRATION_DURATION = 1.5
    START_CONFIRMATION_BLOCKS = 2
    MIN_RECORDING_DURATION = 0.4

    def __init__(self):
        self.state = AVAState.IDLE
        self.client = OpenAI()
        self.microphone = self.find_microphone(self.MIC_NAME)
        self.history = []
        self.noise_floor = 0.0
        self.start_threshold = 0.04
        self.end_threshold = 0.025
        self.calibrate_noise_floor()

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

    def calibrate_noise_floor(self):
        print("Calibrating microphone noise floor. Keep quiet for a moment...")

        block_size = int(self.SAMPLE_RATE * self.BLOCK_DURATION)
        block_count = max(1, int(self.CALIBRATION_DURATION / self.BLOCK_DURATION))
        levels = []

        with sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            device=self.microphone,
            blocksize=block_size,
        ) as stream:
            for _ in range(block_count):
                block, overflowed = stream.read(block_size)
                if overflowed:
                    print("Audio input overflow during calibration.")
                levels.append(self.audio_level(block))

        self.noise_floor = float(np.percentile(levels, 70))
        self.start_threshold = max(0.04, self.noise_floor + 0.03, self.noise_floor * 1.20)
        self.end_threshold = max(0.025, self.noise_floor + 0.012, self.noise_floor * 1.08)

        print(
            "Noise floor: "
            f"{self.noise_floor:.3f} | "
            f"start: {self.start_threshold:.3f} | "
            f"end: {self.end_threshold:.3f}"
        )

    def record_audio(self):
        self.set_state(AVAState.LISTENING)
        print("Waiting for speech...")

        block_size = int(self.SAMPLE_RATE * self.BLOCK_DURATION)
        pre_roll_blocks = max(1, int(self.PRE_ROLL_DURATION / self.BLOCK_DURATION))
        pre_roll = []
        recorded = []
        speech_started = False
        speech_confirmation = 0
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

                    if level >= self.start_threshold:
                        speech_confirmation += 1
                    else:
                        speech_confirmation = 0

                    if speech_confirmation >= self.START_CONFIRMATION_BLOCKS:
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

                    if level < self.end_threshold:
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
        duration = len(audio) / self.SAMPLE_RATE

        if duration < self.MIN_RECORDING_DURATION:
            print(f"Ignoring very short audio event ({duration:.2f}s).")
            return False

        with wave.open(self.RECORDING_FILE, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(audio.tobytes())

        print(f"Microphone RMS peak: {peak:.3f}")
        print(f"Recorded: {duration:.1f} seconds")
        return True

    @staticmethod
    def transcript_is_valid(text):
        normalized = " ".join(text.lower().strip().split())
        if len(normalized) < 2:
            return False

        prompt_echo_markers = (
            "belgisch-nederlands gesprek",
            "de naam van de assistent is ava",
            "de assistent heet ava",
            "dit is standaard nederlands uit belgie",
            "verwacht nederlandse zinnen en woorden",
        )
        return not any(marker in normalized for marker in prompt_echo_markers)

    def transcribe(self):
        started = time.monotonic()
        self.set_state(AVAState.THINKING)
        print("Transcribing...")

        with open(self.RECORDING_FILE, "rb") as audio_file:
            transcript = self.client.audio.transcriptions.create(
                model="gpt-4o-transcribe",
                file=audio_file,
                language="nl",
                prompt=TRANSCRIPTION_PROMPT,
            )

        text = transcript.text.strip()
        print(f"AVA heard: {text}")
        print(f"Transcription latency: {time.monotonic() - started:.2f}s")
        return text

    def think(self, text):
        started = time.monotonic()
        print("Thinking...")

        conversation = [
            {"role": "system", "content": AVA_PERSONALITY},
            *self.history,
            {"role": "user", "content": text},
        ]

        response = self.client.responses.create(
            model="gpt-5.1",
            input=conversation,
            reasoning={"effort": "none"},
            max_output_tokens=160,
        )

        reply = response.output_text.strip()

        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": reply})
        self.history = self.history[-self.MAX_HISTORY_MESSAGES :]

        print(f"AVA: {reply}")
        print(f"Thinking latency: {time.monotonic() - started:.2f}s")
        return reply

    def speak(self, text, turn_started):
        started = time.monotonic()
        self.set_state(AVAState.SPEAKING)
        print("Generating speech...")

        with self.client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
            instructions=TTS_INSTRUCTIONS,
            response_format="wav",
        ) as audio_response:
            audio_response.stream_to_file(self.SPEECH_FILE)

        generated = time.monotonic()
        print(f"TTS generation latency: {generated - started:.2f}s")
        print(f"Time to speech start: {generated - turn_started:.2f}s")
        print("Speaking...")

        subprocess.run(
            [
                "aplay",
                "-q",
                "-D",
                "plughw:0,0",
                self.SPEECH_FILE,
            ],
            check=True,
        )

    def conversation_once(self):
        if not self.record_audio():
            return

        turn_started = time.monotonic()
        text = self.transcribe()

        if not self.transcript_is_valid(text):
            print("Ignoring empty or suspicious transcription.")
            return

        reply = self.think(text)
        self.speak(reply, turn_started)
        print(f"Full turn including playback: {time.monotonic() - turn_started:.2f}s")

    def start(self):
        print("Project AVA")
        print("AVA Core v0.5.1 - Latency Tune")
        print("--------------------------------")
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
