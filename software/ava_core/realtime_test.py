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
OUTPUT_NAME = "Headphones"
API_SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_MS = 20
PLAYBACK_PREBUFFER_MS = 650
TRANSCRIPTION_PROMPT = "Belgisch-Nederlands gesprek. De assistent heet AVA."

INSTRUCTIONS = """
Je bent AVA, een persoonlijke slimme assistent.
Spreek altijd in natuurlijk standaard Belgisch-Nederlands, tenzij de gebruiker expliciet een andere taal vraagt.
Je bent intelligent, analytisch, direct en betrouwbaar, met subtiele droge humor.
Begin antwoorden niet met je eigen naam.
De naam AVA spreek je uit als 'Ava', niet als 'Eva'.
Geef meestal antwoord in een tot drie korte zinnen.
""".strip()


def find_microphone(name):
    for index, device in enumerate(sd.query_devices()):
        if name.lower() in device["name"].lower() and device["max_input_channels"] > 0:
            print(f"Microphone: {device['name']} (device {index})")
            return index, device
    raise RuntimeError(f"Microphone containing '{name}' not found.")


def find_output(name):
    candidates = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] > 0:
            candidates.append((index, device))
            if name.lower() in device["name"].lower():
                print(f"Output: {device['name']} (device {index})")
                return index, device

    if candidates:
        index, device = candidates[0]
        print(f"Output fallback: {device['name']} (device {index})")
        return index, device

    raise RuntimeError("No audio output device found.")


def resample_pcm16_mono(data, source_rate, target_rate):
    if source_rate == target_rate:
        return data

    samples = np.frombuffer(data, dtype=np.int16)
    if len(samples) < 2:
        return data

    target_length = max(1, round(len(samples) * target_rate / source_rate))
    source_positions = np.arange(len(samples), dtype=np.float64)
    target_positions = np.linspace(0, len(samples) - 1, target_length)
    converted = np.interp(target_positions, source_positions, samples).astype(np.int16)
    return converted.tobytes()


def transcript_is_valid(text):
    normalized = " ".join(text.lower().strip().split())
    if len(normalized) < 2:
        return False

    ghost_markers = (
        "belgisch-nederlands gesprek",
        "de assistent heet ava",
        "de naam van de assistent is ava",
    )
    return not any(marker in normalized for marker in ghost_markers)


class PCMPlayer:
    RESPONSE_DONE = object()

    def __init__(self, output_device, output_rate):
        self.output_device = output_device
        self.output_rate = output_rate
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.playback_active = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def add(self, data):
        self.playback_active.set()
        self.queue.put(data)

    def finish_response(self):
        self.queue.put(self.RESPONSE_DONE)

    def clear(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        self.playback_active.clear()

    def stop(self):
        self.stop_event.set()
        self.queue.put(None)
        self.thread.join(timeout=2)

    def _run(self):
        buffer_lock = threading.Lock()
        pcm_buffer = bytearray()
        response_done = threading.Event()
        playback_started = threading.Event()
        prebuffer_bytes = int(self.output_rate * 2 * PLAYBACK_PREBUFFER_MS / 1000)

        def output_callback(outdata, frames, time_info, status):
            needed = frames * 2
            chunk = b""

            with buffer_lock:
                if playback_started.is_set():
                    take = min(needed, len(pcm_buffer))
                    if take:
                        chunk = bytes(pcm_buffer[:take])
                        del pcm_buffer[:take]

                    if response_done.is_set() and not pcm_buffer:
                        playback_started.clear()
                        response_done.clear()
                        self.playback_active.clear()

            if len(chunk) < needed:
                chunk += b"\x00" * (needed - len(chunk))

            outdata[:] = chunk

        with sd.RawOutputStream(
            samplerate=self.output_rate,
            device=self.output_device,
            channels=1,
            dtype="int16",
            blocksize=max(1, int(self.output_rate * 0.04)),
            latency="high",
            callback=output_callback,
        ):
            while not self.stop_event.is_set():
                item = self.queue.get()
                if item is None:
                    break

                if item is self.RESPONSE_DONE:
                    response_done.set()
                    with buffer_lock:
                        if not pcm_buffer:
                            playback_started.clear()
                            response_done.clear()
                            self.playback_active.clear()
                    continue

                converted = resample_pcm16_mono(
                    item,
                    API_SAMPLE_RATE,
                    self.output_rate,
                )

                with buffer_lock:
                    pcm_buffer.extend(converted)
                    if not playback_started.is_set() and len(pcm_buffer) >= prebuffer_bytes:
                        playback_started.set()

        self.playback_active.clear()


async def main():
    microphone, mic_device = find_microphone(MIC_NAME)
    mic_rate = int(mic_device["default_samplerate"])
    print(f"Microphone native sample rate: {mic_rate} Hz")

    output_device, output_info = find_output(OUTPUT_NAME)
    output_rate = int(output_info["default_samplerate"])
    print(f"Output native sample rate: {output_rate} Hz")

    client = AsyncOpenAI()
    player = PCMPlayer(output_device, output_rate)
    player.start()

    chunk_frames = max(1, int(mic_rate * CHUNK_MS / 1000))
    loop = asyncio.get_running_loop()
    mic_queue = asyncio.Queue(maxsize=100)
    last_speech_stopped = None
    first_audio_seen = False

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"Audio status: {status}")

        if player.playback_active.is_set():
            return

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
                        "format": {"type": "audio/pcm", "rate": API_SAMPLE_RATE},
                        "noise_reduction": {"type": "far_field"},
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": "nl",
                            "prompt": TRANSCRIPTION_PROMPT,
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 500,
                            "create_response": False,
                            "interrupt_response": False,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": API_SAMPLE_RATE},
                        "voice": "marin",
                    },
                },
            }
        )

        print("Project AVA - Realtime guarded-response test")
        print("Speak naturally. Ctrl+C stops the test.")
        print("Connected. Listening...")

        async def send_microphone():
            with sd.RawInputStream(
                samplerate=mic_rate,
                blocksize=chunk_frames,
                device=microphone,
                channels=CHANNELS,
                dtype="int16",
                callback=audio_callback,
            ):
                while True:
                    data = await mic_queue.get()

                    if player.playback_active.is_set():
                        continue

                    data = resample_pcm16_mono(data, mic_rate, API_SAMPLE_RATE)
                    encoded = base64.b64encode(data).decode("ascii")
                    await connection.input_audio_buffer.append(audio=encoded)

        async def receive_events():
            nonlocal last_speech_stopped, first_audio_seen

            async for event in connection:
                if event.type == "input_audio_buffer.speech_started":
                    print("\nYou: [speaking]")
                    first_audio_seen = False

                elif event.type == "input_audio_buffer.speech_stopped":
                    last_speech_stopped = time.monotonic()
                    print("You: [finished]")

                elif event.type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.transcript.strip()
                    print(f"You heard as: {transcript}")

                    if transcript_is_valid(transcript):
                        await connection.response.create()
                    else:
                        print("Ignored ghost/empty transcription. Listening...")

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
                    player.finish_response()
                    print("Listening after playback...")

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
