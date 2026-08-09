import asyncio
import base64
import json
import os
import queue
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from openai import AsyncOpenAI
from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine


load_dotenv()

MODEL = "gpt-realtime-2"
MIC_NAME = "C270"
OUTPUT_NAME = "Headphones"
API_SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_MS = 20
PLAYBACK_PREBUFFER_MS = 650
CALIBRATION_DURATION = 1.5
MIN_VOICE_DURATION = 0.18
MEMORY_FILE = Path.home() / ".local" / "share" / "project-ava" / "memory.json"
MAX_MEMORY_FACTS = 40
TRANSCRIPTION_PROMPT = (
    "Belgisch-Nederlands gesprek. De assistent heet AVA. "
    "Veel voorkomende zinnen zijn: Hey AVA, hoe is het met je, wat versta je nu?"
)

BASE_INSTRUCTIONS = """
Je bent AVA, een persoonlijke slimme assistent.
Spreek altijd in natuurlijk standaard Belgisch-Nederlands, tenzij de gebruiker expliciet een andere taal vraagt.
Je bent intelligent, analytisch, direct en betrouwbaar, met subtiele droge humor.
Begin antwoorden niet met je eigen naam.
De naam AVA spreek je uit als 'Ava', niet als 'Eva'.
Geef meestal antwoord in een tot drie korte zinnen.
Baseer je antwoord op de meest recente tekstboodschap van de gebruiker.
Als er persistente geheugencontext wordt meegegeven, behandel die als betrouwbare lokale context van Project AVA.
Zeg nooit dat je iets permanent onthoudt tenzij de lokale geheugenlaag dat daadwerkelijk heeft opgeslagen.
""".strip()


class LocalMemory:
    def __init__(self, path):
        self.path = path
        self.data = {"name": None, "facts": []}
        self.load()

    def load(self):
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    name = loaded.get("name")
                    facts = loaded.get("facts", [])
                    self.data["name"] = name if isinstance(name, str) and name.strip() else None
                    self.data["facts"] = [
                        str(fact).strip()
                        for fact in facts
                        if str(fact).strip()
                    ][:MAX_MEMORY_FACTS]
        except (OSError, json.JSONDecodeError) as error:
            print(f"Memory load warning: {error}")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def set_name(self, name):
        cleaned = name.strip(" .,!?:;\"'")
        if not cleaned:
            return False
        changed = self.data.get("name") != cleaned
        self.data["name"] = cleaned
        if changed:
            self.save()
        return changed

    def forget_name(self):
        if not self.data.get("name"):
            return False
        self.data["name"] = None
        self.save()
        return True

    def add_fact(self, fact):
        cleaned = fact.strip(" .")
        if not cleaned:
            return False
        existing = {item.casefold() for item in self.data["facts"]}
        if cleaned.casefold() in existing:
            return False
        self.data["facts"].append(cleaned)
        self.data["facts"] = self.data["facts"][-MAX_MEMORY_FACTS:]
        self.save()
        return True

    def forget_fact(self, target):
        cleaned = target.strip(" .").casefold()
        if not cleaned:
            return False

        old = self.data["facts"]
        new = [
            fact for fact in old
            if cleaned not in fact.casefold() and fact.casefold() not in cleaned
        ]
        changed = len(new) != len(old)
        if changed:
            self.data["facts"] = new
            self.save()
        return changed

    def context_text(self):
        lines = []
        if self.data.get("name"):
            lines.append(f"De gebruiker heet {self.data['name']}.")
        for fact in self.data["facts"]:
            lines.append(f"Onthouden feit: {fact}.")
        if not lines:
            return "Er zijn nog geen persistente herinneringen opgeslagen."
        return "\n".join(lines)

    def summary(self):
        name = self.data.get("name") or "onbekend"
        return f"naam={name}, feiten={len(self.data['facts'])}"


def process_memory_request(transcript, memory):
    text = transcript.strip()
    lower = text.casefold()

    forget_name_phrases = (
        "vergeet mijn naam",
        "vergeet hoe ik heet",
        "wis mijn naam",
    )
    if any(phrase in lower for phrase in forget_name_phrases):
        changed = memory.forget_name()
        return "naam verwijderd" if changed else "naam was niet opgeslagen"

    remember_match = re.search(r"\bonthoud(?:t)?\s+dat\s+(.+)", text, re.IGNORECASE)
    if remember_match:
        fact = remember_match.group(1).strip()
        changed = memory.add_fact(fact)
        return f"feit opgeslagen: {fact}" if changed else f"feit stond al in geheugen: {fact}"

    forget_match = re.search(r"\bvergeet\s+dat\s+(.+)", text, re.IGNORECASE)
    if forget_match:
        target = forget_match.group(1).strip()
        changed = memory.forget_fact(target)
        return f"feit verwijderd: {target}" if changed else f"geen passend feit gevonden: {target}"

    name_match = re.search(
        r"\b(?:mijn naam is|ik heet)\s+([A-Za-zÀ-ÖØ-öø-ÿ' -]{2,40}?)(?:[.!?,]|$)",
        text,
        re.IGNORECASE,
    )
    if name_match:
        name = " ".join(part.capitalize() for part in name_match.group(1).split())
        changed = memory.set_name(name)
        return f"naam opgeslagen: {name}" if changed else f"naam bevestigd: {name}"

    return None


class AvatarBridge(QObject):
    stateChanged = Signal()

    def __init__(self):
        super().__init__()
        self._state = "idle"

    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Slot(str)
    def setState(self, state):
        if state != self._state:
            self._state = state
            print(f"AVA state: {state}")
            self.stateChanged.emit()


def configure_wayland_from_ssh():
    if not os.environ.get("WAYLAND_DISPLAY"):
        runtime_dir = f"/run/user/{os.getuid()}"
        wayland_socket = Path(runtime_dir) / "wayland-0"
        if wayland_socket.exists():
            os.environ.setdefault("XDG_RUNTIME_DIR", runtime_dir)
            os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
            os.environ.setdefault("QT_QPA_PLATFORM", "wayland")


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


def audio_rms(data):
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if not len(samples):
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def calibrate_microphone(microphone, sample_rate):
    print("Calibrating local speech guard. Keep quiet for a moment...")
    block_frames = max(1, int(sample_rate * CHUNK_MS / 1000))
    block_count = max(1, int(CALIBRATION_DURATION * 1000 / CHUNK_MS))
    levels = []

    with sd.RawInputStream(
        samplerate=sample_rate,
        blocksize=block_frames,
        device=microphone,
        channels=CHANNELS,
        dtype="int16",
    ) as stream:
        for _ in range(block_count):
            data, overflowed = stream.read(block_frames)
            if overflowed:
                print("Audio overflow during local calibration.")
            levels.append(audio_rms(bytes(data)))

    noise_floor = float(np.percentile(levels, 70))
    voice_threshold = max(0.05, noise_floor + 0.025, noise_floor * 1.30)
    peak_threshold = max(0.08, voice_threshold + 0.015)
    print(
        f"Local noise floor: {noise_floor:.3f} | "
        f"voice: {voice_threshold:.3f} | peak: {peak_threshold:.3f}"
    )
    return voice_threshold, peak_threshold


def transcript_is_valid(text):
    normalized = " ".join(text.lower().strip().split())
    if len(normalized) < 2:
        return False

    ghost_markers = (
        "belgisch-nederlands gesprek",
        "de assistent heet ava",
        "de naam van de assistent is ava",
        "veel voorkomende zinnen zijn",
    )
    return not any(marker in normalized for marker in ghost_markers)


class PCMPlayer:
    RESPONSE_DONE = object()

    def __init__(self, output_device, output_rate, bridge):
        self.output_device = output_device
        self.output_rate = output_rate
        self.bridge = bridge
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
                        self.bridge.setState("listening")

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
                            self.bridge.setState("listening")
                    continue

                converted = resample_pcm16_mono(item, API_SAMPLE_RATE, self.output_rate)

                with buffer_lock:
                    pcm_buffer.extend(converted)
                    if not playback_started.is_set() and len(pcm_buffer) >= prebuffer_bytes:
                        playback_started.set()

        self.playback_active.clear()


async def realtime_worker(bridge):
    memory = LocalMemory(MEMORY_FILE)
    print(f"Persistent memory loaded: {memory.summary()}")
    print(f"Memory file: {MEMORY_FILE}")

    microphone, mic_device = find_microphone(MIC_NAME)
    mic_rate = int(mic_device["default_samplerate"])
    print(f"Microphone native sample rate: {mic_rate} Hz")

    voice_threshold, peak_threshold = calibrate_microphone(microphone, mic_rate)

    output_device, output_info = find_output(OUTPUT_NAME)
    output_rate = int(output_info["default_samplerate"])
    print(f"Output native sample rate: {output_rate} Hz")

    client = AsyncOpenAI()
    player = PCMPlayer(output_device, output_rate, bridge)
    player.start()

    chunk_frames = max(1, int(mic_rate * CHUNK_MS / 1000))
    loop = asyncio.get_running_loop()
    mic_queue = asyncio.Queue(maxsize=100)
    last_speech_stopped = None
    first_audio_seen = False

    turn_lock = threading.Lock()
    server_turn_active = threading.Event()
    turn_voice_blocks = 0
    turn_peak = 0.0
    pending_turns = deque()

    def audio_callback(indata, frames, time_info, status):
        nonlocal turn_voice_blocks, turn_peak

        if status:
            print(f"Audio status: {status}")

        if player.playback_active.is_set():
            return

        data = bytes(indata)
        level = audio_rms(data)

        if server_turn_active.is_set():
            with turn_lock:
                turn_peak = max(turn_peak, level)
                if level >= voice_threshold:
                    turn_voice_blocks += 1

        def enqueue():
            if not mic_queue.full():
                mic_queue.put_nowait(data)

        loop.call_soon_threadsafe(enqueue)

    async with client.realtime.connect(model=MODEL) as connection:
        await connection.session.update(
            session={
                "type": "realtime",
                "model": MODEL,
                "instructions": BASE_INSTRUCTIONS,
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
                            "type": "semantic_vad",
                            "eagerness": "low",
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

        print("Project AVA v0.7 - Realtime + Persistent Memory")
        print("Connected. Speak naturally. Ctrl+C stops the process.")
        bridge.setState("listening")

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
            nonlocal turn_voice_blocks, turn_peak

            async for event in connection:
                if event.type == "input_audio_buffer.speech_started":
                    with turn_lock:
                        turn_voice_blocks = 0
                        turn_peak = 0.0
                    server_turn_active.set()
                    bridge.setState("listening")
                    print("\nYou: [speaking]")
                    first_audio_seen = False

                elif event.type == "input_audio_buffer.speech_stopped":
                    server_turn_active.clear()
                    last_speech_stopped = time.monotonic()

                    with turn_lock:
                        voice_blocks = turn_voice_blocks
                        peak = turn_peak

                    voice_duration = voice_blocks * CHUNK_MS / 1000.0
                    pending_turns.append((voice_duration, peak))
                    bridge.setState("thinking")
                    print("You: [finished]")
                    print(
                        f"Local voice evidence: {voice_duration:.2f}s | "
                        f"peak: {peak:.3f}"
                    )

                elif event.type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.transcript.strip()
                    print(f"You heard as: {transcript}")

                    if pending_turns:
                        voice_duration, peak = pending_turns.popleft()
                    else:
                        voice_duration, peak = 0.0, 0.0

                    local_voice_ok = (
                        voice_duration >= MIN_VOICE_DURATION
                        and peak >= peak_threshold
                    )

                    if not local_voice_ok:
                        print(
                            "Ignored weak/non-speech trigger "
                            f"(voice {voice_duration:.2f}s, peak {peak:.3f})."
                        )
                        bridge.setState("listening")
                        continue

                    if not transcript_is_valid(transcript):
                        print("Ignored ghost/empty transcription. Listening...")
                        bridge.setState("listening")
                        continue

                    memory_result = process_memory_request(transcript, memory)
                    if memory_result:
                        print(f"Memory: {memory_result}")

                    memory_context = memory.context_text()
                    authoritative_text = (
                        "[Persistente geheugencontext van Project AVA]\n"
                        f"{memory_context}\n"
                    )
                    if memory_result:
                        authoritative_text += (
                            "[Lokale geheugenactie is al uitgevoerd]\n"
                            f"{memory_result}\n"
                        )
                    authoritative_text += (
                        "[Meest recente boodschap van de gebruiker]\n"
                        f"{transcript}"
                    )

                    await connection.conversation.item.create(
                        item={
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": authoritative_text}],
                        }
                    )
                    await connection.response.create()

                elif event.type == "response.output_audio.delta":
                    if not first_audio_seen:
                        first_audio_seen = True
                        bridge.setState("speaking")
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

                elif event.type == "error":
                    print(f"Realtime error: {event.error.message}")
                    bridge.setState("idle")

        sender = asyncio.create_task(send_microphone())
        receiver = asyncio.create_task(receive_events())

        try:
            await asyncio.gather(sender, receiver)
        finally:
            sender.cancel()
            receiver.cancel()
            player.stop()
            bridge.setState("idle")


def run_realtime_thread(bridge):
    try:
        asyncio.run(realtime_worker(bridge))
    except Exception as error:
        print(f"Realtime AVA stopped: {error}")
        bridge.setState("idle")


def main():
    configure_wayland_from_ssh()

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    bridge = AvatarBridge()
    engine.rootContext().setContextProperty("avatarBridge", bridge)

    qml_file = Path(__file__).with_name("Avatar.qml")
    engine.load(QUrl.fromLocalFile(str(qml_file)))

    if not engine.rootObjects():
        raise RuntimeError("Could not load AVA avatar UI.")

    worker = threading.Thread(
        target=run_realtime_thread,
        args=(bridge,),
        daemon=True,
    )
    worker.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
