"""Project AVA v0.9.2 - serialized Realtime tools + verified brightness."""

import asyncio
import base64
import json
import threading
import time
from collections import deque

import sounddevice as sd
from openai import AsyncOpenAI

import realtime_tools_v091 as v091

base = v091.base
v09 = v091.v09

VERIFY_TIMEOUT_SECONDS = 4.0
VERIFY_INTERVAL_SECONDS = 0.15
BRIGHTNESS_TOLERANCE_PCT = 2


def _expected_state(action, previous_state):
    previous = str(previous_state or "").casefold()
    if action == "turn_on":
        return "on"
    if action == "turn_off":
        return "off"
    if action == "toggle":
        if previous == "on":
            return "off"
        if previous == "off":
            return "on"
    return None


def _brightness_pct_from_state(state):
    if not isinstance(state, dict):
        return None
    raw = (state.get("attributes") or {}).get("brightness")
    if raw is None:
        return None
    try:
        return round(float(raw) * 100 / 255)
    except (TypeError, ValueError):
        return None


def _matching_service_state(service_result, entity_id):
    if not isinstance(service_result, list):
        return None
    matches = [
        state for state in service_result
        if isinstance(state, dict)
        and str(state.get("entity_id") or "").casefold() == entity_id.casefold()
    ]
    return matches[-1] if matches else None


def _verification(candidate, expected_state, requested_brightness):
    current_state = str((candidate or {}).get("state") or "").casefold()
    state_verified = expected_state is None or current_state == expected_state
    reported_brightness = _brightness_pct_from_state(candidate)

    if requested_brightness is None:
        brightness_verified = None
        overall = state_verified
    else:
        brightness_verified = (
            reported_brightness is not None
            and abs(reported_brightness - requested_brightness)
            <= BRIGHTNESS_TOLERANCE_PCT
        )
        overall = state_verified and brightness_verified

    return overall, state_verified, brightness_verified, reported_brightness


def _tool_ha_control_v092(arguments):
    action = str(arguments.get("action") or "").strip()
    if action not in {"turn_on", "turn_off", "toggle"}:
        return {"ok": False, "error": f"Niet-ondersteunde actie: {action}"}

    resolved = v09._resolve_entity(
        arguments.get("entity"),
        allowed_domains=v09.CONTROL_DOMAINS,
    )
    if not resolved.get("ok"):
        return resolved

    previous_state = resolved["state"]
    entity_id = str(previous_state.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0]
    if domain not in v09.CONTROL_DOMAINS:
        return {
            "ok": False,
            "error": (
                f"Besturing van domein '{domain}' is niet toegestaan. "
                "Alleen light en switch zijn ingeschakeld."
            ),
        }

    service_data = {"entity_id": entity_id}
    requested_brightness = arguments.get("brightness_pct")
    if requested_brightness is not None:
        if domain != "light" or action != "turn_on":
            return {
                "ok": False,
                "error": "brightness_pct is alleen geldig voor een lamp bij turn_on.",
            }
        try:
            requested_brightness = int(requested_brightness)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Ongeldige helderheid."}
        if not 1 <= requested_brightness <= 100:
            return {"ok": False, "error": "Helderheid moet tussen 1 en 100 liggen."}
        service_data["brightness_pct"] = requested_brightness

    service_result = v09._ha_request(
        f"/api/services/{domain}/{action}",
        method="POST",
        payload=service_data,
    )

    expected_state = _expected_state(action, previous_state.get("state"))
    fresh_state = _matching_service_state(service_result, entity_id) or previous_state

    verified, state_verified, brightness_verified, reported_brightness = _verification(
        fresh_state,
        expected_state,
        requested_brightness,
    )

    deadline = time.monotonic() + VERIFY_TIMEOUT_SECONDS
    while not verified and time.monotonic() < deadline:
        try:
            candidate = v09._ha_request(f"/api/states/{entity_id}")
            if isinstance(candidate, dict):
                fresh_state = candidate
                (
                    verified,
                    state_verified,
                    brightness_verified,
                    reported_brightness,
                ) = _verification(candidate, expected_state, requested_brightness)
                if verified:
                    break
        except Exception:
            pass
        time.sleep(VERIFY_INTERVAL_SECONDS)

    result = {
        "ok": True,
        "action": action,
        "command_accepted": True,
        "verified": verified,
        "state_verified": state_verified,
        "expected_state": expected_state,
        "entity": v09._entity_summary(fresh_state),
    }

    if requested_brightness is not None:
        result["brightness_requested_pct"] = requested_brightness
        result["brightness_reported_pct"] = reported_brightness
        result["brightness_verified"] = bool(brightness_verified)

    if not verified:
        if state_verified and requested_brightness is not None:
            result["status"] = (
                "Home Assistant heeft het lichtcommando uitgevoerd, maar de "
                "gevraagde helderheid is nog niet bevestigd door de entiteitsstatus."
            )
        else:
            result["status"] = (
                "Commando door Home Assistant aanvaard; nieuwe toestand nog niet "
                "bevestigd binnen de verificatietijd."
            )

    return result


v09._tool_ha_control = _tool_ha_control_v092

base.TOOL_INSTRUCTIONS += """
Bij een duidelijke opdracht zoals 'zet Beneden binnen aan' roep je home_assistant_control direct aan; zoek niet eerst via home_assistant_find_entities.
Gebruik home_assistant_find_entities alleen als de gebruiker om een lijst vraagt of als home_assistant_control/home_assistant_get_state meldt dat de naam dubbelzinnig is.
Als verified=true, bevestig alleen de waarden die door het Home Assistant-toolresultaat bevestigd zijn.
Als brightness_requested_pct aanwezig is maar brightness_verified=false, zeg dat het helderheidscommando aanvaard is maar dat Home Assistant de gevraagde helderheid nog niet bevestigt.
Verzin nooit dat helderheid gewijzigd is als brightness_verified=false.
Roep tools zonder gesproken tussenzin aan en geef pas na het uiteindelijke toolresultaat een compact antwoord.
"""


async def realtime_worker_v092(bridge):
    print("Project AVA v0.9.2 - Serialized Tools + Brightness Verification")

    config_error = v09._ha_config_error()
    if config_error:
        print(f"Home Assistant: niet geconfigureerd ({config_error})")
    else:
        try:
            await asyncio.to_thread(v09._ha_ping)
            url, _ = v09._ha_config()
            print(f"Home Assistant: verbonden met {url}")
        except Exception as error:
            print(f"Home Assistant preflight warning: {error}")

    memory = base.LocalMemory(base.MEMORY_FILE)
    print(f"Persistent memory loaded: {memory.summary()}")
    print(f"Memory file: {base.MEMORY_FILE}")

    microphone, mic_device = base.find_microphone(base.MIC_NAME)
    mic_rate = int(mic_device["default_samplerate"])
    print(f"Microphone native sample rate: {mic_rate} Hz")
    voice_threshold, peak_threshold = base.calibrate_microphone(microphone, mic_rate)

    output_device, output_info = base.find_output(base.OUTPUT_NAME)
    output_rate = int(output_info["default_samplerate"])
    print(f"Output native sample rate: {output_rate} Hz")

    client = AsyncOpenAI()
    player = base.PCMPlayer(output_device, output_rate, bridge)
    player.start()

    chunk_frames = max(1, int(mic_rate * base.CHUNK_MS / 1000))
    loop = asyncio.get_running_loop()
    mic_queue = asyncio.Queue(maxsize=100)
    last_speech_stopped = None
    first_audio_seen = False

    turn_lock = threading.Lock()
    server_turn_active = threading.Event()
    turn_voice_blocks = 0
    turn_peak = 0.0
    pending_turns = deque()
    tool_followup_pending = False

    def audio_callback(indata, frames, time_info, status):
        nonlocal turn_voice_blocks, turn_peak

        if status:
            print(f"Audio status: {status}")
        if player.playback_active.is_set():
            return

        data = bytes(indata)
        level = base.audio_rms(data)

        if server_turn_active.is_set():
            with turn_lock:
                turn_peak = max(turn_peak, level)
                if level >= voice_threshold:
                    turn_voice_blocks += 1

        def enqueue():
            if not mic_queue.full():
                mic_queue.put_nowait(data)

        loop.call_soon_threadsafe(enqueue)

    async with client.realtime.connect(model=base.MODEL) as connection:
        await connection.session.update(
            session={
                "type": "realtime",
                "model": base.MODEL,
                "instructions": base.build_tool_instructions(memory),
                "output_modalities": ["audio"],
                "tools": base.TOOLS,
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": base.API_SAMPLE_RATE},
                        "noise_reduction": {"type": "far_field"},
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": "nl",
                            "prompt": (
                                "Belgisch-Nederlands gesprek. De assistent heet AVA. "
                                "Veel voorkomende zinnen zijn: hoe laat is het, wat is "
                                "het weer, zet beneden binnen aan?"
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
                        "format": {"type": "audio/pcm", "rate": base.API_SAMPLE_RATE},
                        "voice": "marin",
                    },
                },
            }
        )

        print("Tools: tijd/datum + weer + Home Assistant")
        print("Connected. Speak naturally. Ctrl+C stops the process.")
        bridge.setState("listening")

        async def send_microphone():
            with sd.RawInputStream(
                samplerate=mic_rate,
                blocksize=chunk_frames,
                device=microphone,
                channels=base.CHANNELS,
                dtype="int16",
                callback=audio_callback,
            ):
                while True:
                    data = await mic_queue.get()
                    if player.playback_active.is_set():
                        continue
                    data = base.resample_pcm16_mono(
                        data, mic_rate, base.API_SAMPLE_RATE
                    )
                    encoded = base64.b64encode(data).decode("ascii")
                    await connection.input_audio_buffer.append(audio=encoded)

        async def receive_events():
            nonlocal last_speech_stopped, first_audio_seen
            nonlocal turn_voice_blocks, turn_peak, tool_followup_pending

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
                    voice_duration = voice_blocks * base.CHUNK_MS / 1000.0
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
                        voice_duration >= base.MIN_VOICE_DURATION
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

                    if not base.transcript_is_valid(transcript):
                        print("Ignored ghost/empty transcription. Listening...")
                        bridge.setState("listening")
                        continue

                    memory_actions = base.process_memory_request(transcript, memory)
                    for action in memory_actions:
                        print(f"Memory: {action}")

                    if memory_actions:
                        await connection.session.update(
                            session={
                                "type": "realtime",
                                "instructions": base.build_tool_instructions(memory),
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
                            "instructions": base.build_tool_instructions(
                                memory, memory_actions
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
                    result = await base.execute_tool(event.name, arguments)
                    print(
                        "Tool result: "
                        + json.dumps(
                            result, ensure_ascii=False, separators=(",", ":")
                        )
                    )
                    await connection.conversation.item.create(
                        item={
                            "type": "function_call_output",
                            "call_id": event.call_id,
                            "output": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    tool_followup_pending = True

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
                    if first_audio_seen:
                        player.finish_response()

                    if tool_followup_pending:
                        tool_followup_pending = False
                        first_audio_seen = False
                        bridge.setState("thinking")
                        await connection.response.create(
                            response={
                                "instructions": base.build_tool_instructions(memory)
                            }
                        )

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


base.realtime_worker = realtime_worker_v092

if __name__ == "__main__":
    raise SystemExit(base.main())
