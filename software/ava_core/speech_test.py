import subprocess
import wave
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

SAMPLE_RATE = 16000
DURATION = 5
MIC_NAME = "C270"
FILENAME = "/tmp/ava_speech_test.wav"


def find_microphone(name):
    for index, device in enumerate(sd.query_devices()):
        if (
            name.lower() in device["name"].lower()
            and device["max_input_channels"] > 0
        ):
            return index

    raise RuntimeError(f"Microphone containing '{name}' not found.")


microphone = find_microphone(MIC_NAME)

print("AVA is listening...")

audio = sd.rec(
    int(DURATION * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="int16",
    device=microphone,
)

sd.wait()

with wave.open(FILENAME, "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(SAMPLE_RATE)
    wav.writeframes(audio.tobytes())

print("Sending audio to OpenAI...")

client = OpenAI()

with open(FILENAME, "rb") as audio_file:
    transcript = client.audio.transcriptions.create(
        model="gpt-4o-transcribe",
        file=audio_file,
        language="nl",
    )

print()
print("AVA heard:")
print(transcript.text)
response = client.responses.create(
    model="gpt-5-mini",
    input=[
        {
            "role": "system",
            "content": (
                "Je bent AVA, een slimme huisassistent. "
                "Antwoord kort, natuurlijk en in het Nederlands."
            ),
        },
        {
            "role": "user",
            "content": transcript.text,
        },
    ],
)

print()
print("AVA replies:")
print(response.output_text)
speech_file = "/tmp/ava_reply.mp3"

with client.audio.speech.with_streaming_response.create(
    model="gpt-4o-mini-tts",
    voice="alloy",
    input=response.output_text,
) as audio_response:
    audio_response.stream_to_file(speech_file)

print("AVA is speaking...")
import subprocess

subprocess.run(
    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", speech_file],
    check=True,
)
