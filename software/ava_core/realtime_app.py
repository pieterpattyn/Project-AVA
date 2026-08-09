import asyncio
import base64
import json
import os
import queue
import re
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from openai import AsyncOpenAI
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
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
Gebruik persistente geheugencontext als betrouwbare lokale context van Project AVA.
Als de lokale geheugenlaag meldt dat iets opgeslagen, aangepast of verwijderd is, dan is dat werkelijk gebeurd.
Zeg nooit dat permanente opslag onmogelijk is wanneer de lokale geheugenlaag een geheugenactie bevestigd heeft.
Herhaal je eigen vorige antwoord niet tenzij de gebruiker daar expliciet om vraagt.
""".strip()


class LocalMemory:
    def __init__(self, path):
        self.path = path
        self.data = {"name": None, "preferences": {}, "facts": []}
        self.load()

    def load(self):
        migrated = False

        try:
            if not self.path.exists():
                return

            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                return

            facts = loaded.get("facts", [])
            preferences = loaded.get("preferences", {})
            raw_name = loaded.get("name")

            self.data["facts"] = [
                str(fact).strip()
                for fact in facts
                if str(fact).strip()
            ][:MAX_MEMORY_FACTS]

            if isinstance(preferences, dict):
                self.data["preferences"] = {
                    str(key).strip().casefold(): str(value).strip()
                    for key, value in preferences.items()
                    if str(key).strip() and str(value).strip()
                }

            if isinstance(raw_name, str) and raw_name.strip():
                name = raw_name.strip()

                # Repair the malformed value created by v0.7 when a transcript such
                # as "Mijn naam is Pieter en mijn favoriete kleur is roze" was
                # captured as one giant name.
                legacy_match = re.match(
                    r"^(.+?)\s+en\s+mijn\s+favoriete\s+(.+?)\s+is\s+(.+)$",
                    name,
                    re.IGNORECASE,
                )
                if legacy_match:
                    clean_name = legacy_match.group(1).strip(" .,!?:;\"'")
                    topic = legacy_match.group(2).strip(" .,!?:;\"'").casefold()
                    value = legacy_match.group(3).strip(" .,!?:;\"'")
                    self.data["name"] = clean_name or None
                    if topic and value and topic not in self.data["preferences"]:
                        self.data["preferences"][topic] = value
                    migrated = True
                else:
                    self.data["name"] = name

            if migrated:
                self.save()
                print("Memory migration: oude samengestelde naam hersteld.")

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

    def set_preference(self, topic, value):
        topic_clean = topic.strip(" .,!?:;\"'").casefold()
        value_clean = value.strip(" .,!?:;\"'")
        if not topic_clean or not value_clean:
            return False

        changed = self.data["preferences"].get(topic_clean) != value_clean
        self.data["preferences"][topic_clean] = value_clean
        if changed:
            self.save()
        return changed

    def forget_preference(self, topic):
        topic_clean = topic.strip(" .,!?:;\"'").casefold()
        if topic_clean not in self.data["preferences"]:
            return False
        del self.data["preferences"][topic_clean]
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
            fact
            for fact in old
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

        for topic, value in self.data["preferences"].items():
            lines.append(f"Voorkeur van de gebruiker: favoriete {topic} = {value}.")

        for fact in self.data["facts"]:
            lines.append(f"Onthouden feit: {fact}.")

        if not lines:
            return "Er zijn nog geen persistente herinneringen opgeslagen."
        return "\n".join(lines)

    def summary(self):
        name = self.data.get("name") or "onbekend"
        return (
            f"naam={name}, voorkeuren={len(self.data['preferences'])}, "
            f"feiten={len(self.data['facts'])}"
        )


def process_memory_request(transcript, memory):
    text = transcript.strip()
    lower = text.casefold()
    actions = []
    specific_memory_handled = False

    if any(
        phrase in lower
        for phrase in ("vergeet mijn naam", "vergeet hoe ik heet", "wis mijn naam")
    ):
        changed = memory.forget_name()
        actions.append("naam verwijderd" if changed else "naam was niet opgeslagen")
        specific_memory_handled = True

    # Stop a name at a natural conjunction/question boundary. This prevents
    # "Pieter en mijn favoriete kleur is roze" from becoming somebody's surname.
    name_match = re.search(
        r"\b(?:mijn naam is|ik heet)\s+"
        r"([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*"
        r"(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*){0,4}?)"
        r"(?=\s+(?:en|maar|want|mijn|wat|hoe|welke|waar|wanneer)\b|[.!?,]|$)",
        text,
        re.IGNORECASE,
    )
    if name_match:
        name = name_match.group(1).strip()
        if name:
            name = name[0].upper() + name[1:]
            changed = memory.set_name(name)
            actions.append(
                f"naam opgeslagen: {name}" if changed else f"naam bevestigd: {name}"
            )
            specific_memory_handled = True

    # Examples:
    # "Mijn favoriete kleur is blauw"
    # "Onthoud dat mijn favoriete kleur rood is"
    # "Mijn favoriete kleur is nu groen"
    preference_match = re.search(
        r"\b(?:onthoud(?:t)?\s+dat\s+)?mijn\s+favoriete\s+"
        r"([\wÀ-ÖØ-öø-ÿ' -]{2,40}?)\s+(?:is\s+nu|is|wordt)\s+"
        r"(.+?)(?=\s+(?:en|maar|want)\b|[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if preference_match:
        topic = preference_match.group(1).strip()
        value = preference_match.group(2).strip()
        changed = memory.set_preference(topic, value)
        actions.append(
            f"favoriete {topic} aangepast naar {value}"
            if changed
            else f"favoriete {topic} stond al op {value}"
        )
        specific_memory_handled = True

    change_preference_match = re.search(
        r"\b(?:verander|wijzig|pas)\s+(?:mijn\s+)?favoriete\s+"
        r"([\wÀ-ÖØ-öø-ÿ' -]{2,40}?)\s+(?:aan\s+)?(?:naar|in)\s+"
        r"(.+?)(?=\s+(?:en|maar|want)\b|[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if change_preference_match:
        topic = change_preference_match.group(1).strip()
        value = change_preference_match.group(2).strip()
        changed = memory.set_preference(topic, value)
        actions.append(
            f"favoriete {topic} aangepast naar {value}"
            if changed
            else f"favoriete {topic} stond al op {value}"
        )
        specific_memory_handled = True

    forget_preference_match = re.search(
        r"\bvergeet\s+(?:wat\s+)?mijn\s+favoriete\s+"
        r"([\wÀ-ÖØ-öø-ÿ' -]{2,40}?)(?:\s+is)?(?:[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if forget_preference_match:
        topic = forget_preference_match.group(1).strip()
        changed = memory.forget_preference(topic)
        actions.append(
            f"favoriete {topic} verwijderd"
            if changed
            else f"favoriete {topic} was niet opgeslagen"
        )
        specific_memory_handled = True

    if not specific_memory_handled:
        remember_match = re.search(r"\bonthoud(?:t)?\s+dat\s+(.+)", text, re.IGNORECASE)
        if remember_match:
            fact = remember_match.group(1).strip()
            changed = memory.add_fact(fact)
            actions.append(
                f"feit opgeslagen: {fact}"
                if changed
                else f"feit stond al in geheugen: {fact}"
            )

        forget_match = re.search(r"\bvergeet\s+dat\s+(.+)", text, re.IGNORECASE)
        if forget_match:
            target = forget_match.group(1).strip()
            changed = memory.forget_fact(target)
            actions.append(
                f"feit verwijderd: {target}"
                if changed
                else f"geen passend feit gevonden: {target}"
            )

    return actions


def build_instructions(memory):
    return (
        BASE_INSTRUCTIONS
        + "\n\nPersistente geheugencontext:\n"
        + memory.context_text()
    )


def build_response_instructions(memory, memory_actions):
    instructions = build_instructions(memory)
    if memory_actions:
        instructions += (
            "\n\nLokale geheugenactie voor deze beurt:\n- "
            + "\n- ".join(memory_actions)
            + "\nBevestig de geheugenactie kort en correct als dat relevant is. "
            "Beweer niet dat deze actie niet permanent opgeslagen kan worden."
        )
    return instructions


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
                "instructions": build_instructions(memory),
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

        print("Project AVA v0.7.2 - Reliable Memory Updates")
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
                        f"Local voice evidence: {voice_duration:.2f}s | peak: {peak:.3f}"
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

                    # Replace the server-created raw audio item with one validated
                    # text item. Keeping both made the model see some turns twice.
                    try:
                        await connection.conversation.item.delete(item_id=event.item_id)
                    except Exception as error:
                        print(f"Conversation cleanup warning: {error}")

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

                    memory_actions = process_memory_request(transcript, memory)
                    for action in memory_actions:
                        print(f"Memory: {action}")

                    # Realtime session updates require the session type even when
                    # only instructions are changed. Keep future turns in sync.
                    if memory_actions:
                        await connection.session.update(
                            session={
                                "type": "realtime",
                                "instructions": build_instructions(memory),
                            }
                        )

                    await connection.conversation.item.create(
                        item={
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": transcript}],
                        }
                    )

                    # Also provide the fresh memory as response-level instructions.
                    # This makes the current turn deterministic even before a
                    # session.updated event has come back from the server.
                    await connection.response.create(
                        response={
                            "instructions": build_response_instructions(
                                memory,
                                memory_actions,
                            )
                        }
                    )

                elif event.type == "response.output_audio.delta":
                    if not first_audio_seen:
                        first_audio_seen = True
                        bridge.setState("speaking")
                        if last_speech_stopped is not None:
                            print(
                                f"AVA first audio latency: "
                                f"{time.monotonic() - last_speech_stopped:.2f}s"
                            )
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

    # Give Python regular control so Ctrl+C from SSH is handled promptly.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    worker = threading.Thread(
        target=run_realtime_thread,
        args=(bridge,),
        daemon=True,
    )
    worker.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
