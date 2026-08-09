import asyncio
import base64
import json
import os
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sounddevice as sd
from openai import AsyncOpenAI
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from realtime_app import (
    API_SAMPLE_RATE,
    CHANNELS,
    CHUNK_MS,
    MEMORY_FILE,
    MIC_NAME,
    MODEL,
    OUTPUT_NAME,
    AvatarBridge,
    LocalMemory,
    PCMPlayer,
    audio_rms,
    build_instructions,
    calibrate_microphone,
    configure_wayland_from_ssh,
    find_microphone,
    find_output,
    process_memory_request,
    resample_pcm16_mono,
    transcript_is_valid,
)


TOOLS = [
    {
        "type": "function",
        "name": "get_current_datetime",
        "description": (
            "Geeft de echte huidige lokale datum en tijd. Gebruik dit voor vragen "
            "over hoe laat het nu is, welke dag of datum het vandaag is, of de tijd "
            "in een opgegeven IANA-tijdzone. Raad de huidige tijd nooit zelf."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": (
                        "Optionele IANA-tijdzone, bijvoorbeeld Europe/Brussels of "
                        "America/New_York. Laat weg voor de lokale tijd van AVA."
                    ),
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_weather",
        "description": (
            "Geeft het actuele weer en de verwachting voor vandaag op een genoemde "
            "plaats via Open-Meteo. Gebruik dit voor actuele weersvragen. Als de "
            "gebruiker geen plaats noemt en er geen plaats uit de context bekend is, "
            "vraag dan eerst om de plaats in plaats van te gokken."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Plaatsnaam, bijvoorbeeld Brugge, Gent of Brussel.",
                }
            },
            "required": ["location"],
            "additionalProperties": False,
        },
    },
]


TOOL_INSTRUCTIONS = """
Je beschikt over lokale tools voor actuele informatie.
Gebruik get_current_datetime altijd voor de huidige tijd, dag of datum; verzin die informatie niet.
Gebruik get_weather altijd voor actueel weer; verzin geen weersomstandigheden.
Baseer je antwoord na een tool call uitsluitend op het teruggegeven toolresultaat.
Als een tool een fout meldt, leg dat kort uit en vraag alleen om ontbrekende informatie als dat nodig is.
""".strip()


WEEKDAYS_NL = (
    "maandag",
    "dinsdag",
    "woensdag",
    "donderdag",
    "vrijdag",
    "zaterdag",
    "zondag",
)

MONTHS_NL = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)

WEATHER_CODES_NL = {
    0: "helder",
    1: "overwegend helder",
    2: "gedeeltelijk bewolkt",
    3: "bewolkt",
    45: "mist",
    48: "aanvriezende mist",
    51: "lichte motregen",
    53: "motregen",
    55: "dichte motregen",
    56: "lichte aanvriezende motregen",
    57: "aanvriezende motregen",
    61: "lichte regen",
    63: "regen",
    65: "zware regen",
    66: "lichte aanvriezende regen",
    67: "zware aanvriezende regen",
    71: "lichte sneeuw",
    73: "sneeuw",
    75: "zware sneeuw",
    77: "sneeuwkorrels",
    80: "lichte regenbuien",
    81: "regenbuien",
    82: "zware regenbuien",
    85: "lichte sneeuwbuien",
    86: "zware sneeuwbuien",
    95: "onweer",
    96: "onweer met lichte hagel",
    99: "onweer met zware hagel",
}


def build_tool_instructions(memory, memory_actions=None):
    instructions = build_instructions(memory) + "\n\n" + TOOL_INSTRUCTIONS
    if memory_actions:
        instructions += (
            "\n\nLokale geheugenactie voor deze beurt:\n- "
            + "\n- ".join(memory_actions)
            + "\nDeze geheugenactie is lokaal uitgevoerd en persistent opgeslagen. "
            "Bevestig ze kort als dat relevant is."
        )
    return instructions


def tool_current_datetime(arguments):
    timezone_name = str(arguments.get("timezone") or "").strip()

    if timezone_name:
        try:
            now = datetime.now(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            return {
                "ok": False,
                "error": f"Onbekende tijdzone: {timezone_name}",
            }
    else:
        now = datetime.now().astimezone()
        timezone_name = str(now.tzinfo)

    return {
        "ok": True,
        "weekday": WEEKDAYS_NL[now.weekday()],
        "date": f"{now.day} {MONTHS_NL[now.month - 1]} {now.year}",
        "time": now.strftime("%H:%M:%S"),
        "timezone": timezone_name,
        "iso": now.isoformat(),
    }


def _http_json(url, timeout=6):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Project-AVA/0.8"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def tool_weather_sync(arguments):
    location = str(arguments.get("location") or "").strip()
    if not location:
        return {"ok": False, "error": "Geen plaats opgegeven."}

    try:
        geo_query = urllib.parse.urlencode(
            {
                "name": location,
                "count": 1,
                "language": "nl",
                "format": "json",
            }
        )
        geo = _http_json(
            f"https://geocoding-api.open-meteo.com/v1/search?{geo_query}"
        )
        results = geo.get("results") or []
        if not results:
            return {
                "ok": False,
                "error": f"Plaats niet gevonden: {location}",
            }

        place = results[0]
        latitude = place["latitude"]
        longitude = place["longitude"]
        timezone_name = place.get("timezone") or "auto"

        forecast_query = urllib.parse.urlencode(
            {
                "latitude": latitude,
                "longitude": longitude,
                "timezone": "auto",
                "forecast_days": 1,
                "current": (
                    "temperature_2m,apparent_temperature,precipitation,"
                    "weather_code,wind_speed_10m"
                ),
                "daily": (
                    "temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
            }
        )
        forecast = _http_json(
            f"https://api.open-meteo.com/v1/forecast?{forecast_query}"
        )

        current = forecast.get("current") or {}
        daily = forecast.get("daily") or {}
        weather_code = current.get("weather_code")

        country = place.get("country") or ""
        admin1 = place.get("admin1") or ""
        display_location = place.get("name") or location
        if admin1 and admin1.casefold() != display_location.casefold():
            display_location += f", {admin1}"
        if country:
            display_location += f", {country}"

        return {
            "ok": True,
            "location": display_location,
            "timezone": timezone_name,
            "observed_at": current.get("time"),
            "condition": WEATHER_CODES_NL.get(weather_code, f"weercode {weather_code}"),
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "precipitation_mm": current.get("precipitation"),
            "wind_kmh": current.get("wind_speed_10m"),
            "today_min_c": (daily.get("temperature_2m_min") or [None])[0],
            "today_max_c": (daily.get("temperature_2m_max") or [None])[0],
            "today_precipitation_probability_pct": (
                daily.get("precipitation_probability_max") or [None]
            )[0],
            "source": "Open-Meteo",
        }
    except Exception as error:
        return {
            "ok": False,
            "error": f"Weerdienst niet bereikbaar: {error}",
        }


async def execute_tool(name, arguments):
    if name == "get_current_datetime":
        return tool_current_datetime(arguments)
    if name == "get_weather":
        return await asyncio.to_thread(tool_weather_sync, arguments)
    return {"ok": False, "error": f"Onbekende tool: {name}"}


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
                "instructions": build_tool_instructions(memory),
                "output_modalities": ["audio"],
                "tools": TOOLS,
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": API_SAMPLE_RATE},
                        "noise_reduction": {"type": "far_field"},
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": "nl",
                            "prompt": (
                                "Belgisch-Nederlands gesprek. De assistent heet AVA. "
                                "Veel voorkomende zinnen zijn: Hey AVA, hoe is het met je, "
                                "hoe laat is het, wat is het weer?"
                            ),
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

        print("Project AVA v0.8 - Realtime Tools")
        print("Tools: current date/time + current weather")
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
                        voice_duration >= 0.18
                        and peak >= peak_threshold
                    )

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

                    if memory_actions:
                        await connection.session.update(
                            session={
                                "type": "realtime",
                                "instructions": build_tool_instructions(memory),
                            }
                        )

                    await connection.conversation.item.create(
                        item={
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": transcript}],
                        }
                    )
                    await connection.response.create(
                        response={
                            "instructions": build_tool_instructions(
                                memory,
                                memory_actions,
                            )
                        }
                    )

                elif event.type == "response.function_call_arguments.done":
                    bridge.setState("thinking")
                    try:
                        arguments = json.loads(event.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}

                    print(f"Tool call: {event.name} {arguments}")
                    result = await execute_tool(event.name, arguments)
                    print(
                        "Tool result: "
                        + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                    )

                    await connection.conversation.item.create(
                        item={
                            "type": "function_call_output",
                            "call_id": event.call_id,
                            "output": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    await connection.response.create(
                        response={
                            "instructions": build_tool_instructions(memory)
                        }
                    )

                elif event.type == "response.output_audio.delta":
                    if not first_audio_seen:
                        first_audio_seen = True
                        bridge.setState("speaking")
                        if last_speech_stopped is not None:
                            print(
                                "AVA first audio latency: "
                                f"{time.monotonic() - last_speech_stopped:.2f}s"
                            )
                        print("AVA: [speaking]")
                    player.add(base64.b64decode(event.delta))

                elif event.type == "response.output_audio_transcript.delta":
                    print(event.delta, end="", flush=True)

                elif event.type == "response.output_audio_transcript.done":
                    print()

                elif event.type == "response.done":
                    # A tool-only response has no audio. Keep the avatar thinking;
                    # the function output above triggers the spoken follow-up.
                    if first_audio_seen:
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
