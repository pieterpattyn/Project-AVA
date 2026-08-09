import asyncio
import base64
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from openai import AsyncOpenAI


load_dotenv()

MODEL = "gpt-realtime-2"
MIC_NAME = "C270"
INPUT_SAMPLE_RATE = 24000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_MS = 20
OUTPUT_DEVICE = "plughw:0,0"

INSTRUCTIONS = """
Je bent AVA, een persoonlijke slimme assistent.
Spreek natuurlijk, kort en in standaard Belgisch-Nederlands.
Je bent intelligent, analytisch, direct en betrouwbaar, met subtiele droge humor.
Begin antwoorden niet met je eigen naam.
De naam AVA spreek je uit als 'Ava', niet als 'Eva'.
Geef meestal antwoord in een tot drie korte zinnen.
""".strip()


def find_microphone(name):
    for index, device in enumerate(sd.query_devices()):
        if name.lower() in device["name"].lower() and device["max_input_channels"] > 0:
            print(f"Microphone: {device['name']} (device {index})")
            return index
    raise RuntimeError(f"Microphone containing '{name}' not found.")


class PCMPlayer:
    def __init__(self):
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.process = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def add(self, data):
        self.queue.put(data)

    def clear(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

    def stop(self):
        self.stop_event.set()
        self.queue.put(None)
        self.thread.join(timeout=2)

    def _run(self):
        import subprocess

        self.process = subprocess.Popen(
            [
                "aplay",
                "-q",
                "-D",
                OUTPUT_DEVICE,
                "-t",
                "raw",
                "-f",
                "S16_LE",
                "-r",
                str(OUTPUT_SAMPLE_RATE),
                "-c",
                "1",
            ],
            stdin=subprocess.PIPE,
        )

        try:
            while not self.stop_event.is_set():
                data = self.queue.get()
                if data is None:
                    break
                if self.process.stdin:
                    self.process.stdin.write(data)
                    self.process.stdin.flush()
        finally:
            if self.process.stdin:
                self.process.stdin.close()
            self.process.wait(timeout=2)


async def main():
    microphone = find_microphone(MIC_NAME)
    client = AsyncOpenAI()
    player = PCMPlayer()
    player.start()

    chunk_frames = int(INPUT_SAMPLE_RATE * CHUNK_MS / 1000)
    loop = asyncio.get_running_loop()
    mic_queue = asyncio.Queue(maxsize=100)
    last_speech_stopped = None
    first_audio_seen = False

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"Audio status: {status}")
        data = bytes(indata)

        def enqueue():
            if not mic_queue.full():
                mic_queue.put_nowait(data)

        loop.call_soon_threadsafe(enqueue)

    async with client.realtime.connect(model=MODEL) as connection:
        await connection.session.update(
            session={
                "type": "realtime",
                "model": MODEL,
                "instructions": INSTRUCTIONS,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": INPUT_SAMPLE_RATE},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 500,
                            "create_response": True,
                            "interrupt_response": True,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": OUTPUT_SAMPLE_RATE},
                        "voice": "marin",
                    },
                },
            }
        )

        print("Project AVA - Realtime test")
        print("Speak naturally. Ctrl+C stops the test.")
        print("Connected. Listening...")

        async def send_microphone():
            with sd.RawInputStream(
                samplerate=INPUT_SAMPLE_RATE,
                blocksize=chunk_frames,
                device=microphone,
                channels=CHANNELS,
                dtype="int16",
                callback=audio_callback,
            ):
                while True:
                    data = await mic_queue.get()
                    encoded = base64.b64encode(data).decode("ascii")
                    await connection.input_audio_buffer.append(audio=encoded)

        async def receive_events():
            nonlocal last_speech_stopped, first_audio_seen

            async for event in connection:
                if event.type == "input_audio_buffer.speech_started":
                    print("\nYou: [speaking]")
                    first_audio_seen = False
                    player.clear()

                elif event.type == "input_audio_buffer.speech_stopped":
                    last_speech_stopped = time.monotonic()
                    print("You: [finished]")

                elif event.type == "response.output_audio.delta":
                    if not first_audio_seen:
                        first_audio_seen = True
                        if last_speech_stopped is not None:
                            latency = time.monotonic() - last_speech_stopped
                            print(f"AVA first audio latency: {latency:.2f}s")
                        print("AVA: [speaking]")
                    player.add(base64.b64decode(event.delta))

                elif event.type == "response.output_audio_transcript.delta":
                    print(event.delta, end="", flush=True)

                elif event.type == "response.output_audio_transcript.done":
                    print()

                elif event.type == "response.done":
                    print("Listening...")

                elif event.type == "error":
                    print(f"Realtime error: {event.error.message}")

        sender = asyncio.create_task(send_microphone())
        receiver = asyncio.create_task(receive_events())

        try:
            await asyncio.gather(sender, receiver)
        finally:
            sender.cancel()
            receiver.cancel()
            player.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nRealtime test stopped.")
