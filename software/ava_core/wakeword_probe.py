"""Project AVA v1.1 phase A - standalone local wake-word probe.

This deliberately does not modify the proven v1.0 Realtime runtime yet.
It streams the C270 microphone to a local Wyoming wake-word server and exits
when the requested wake word is detected. The intended custom model name is
``hey_ava``.

The probe implements the small subset of the Wyoming protocol it needs
(JSONL headers plus optional PCM payloads), so AVA's main Python environment
does not need the Wyoming/openWakeWord inference stack installed in it.
"""

import argparse
import asyncio
import json
import os
from urllib.parse import urlparse

import sounddevice as sd

import realtime_app as core


WAKE_SAMPLE_RATE = 16_000
WAKE_SAMPLE_WIDTH = 2
WAKE_CHANNELS = 1
WAKE_FRAME_MS = 80
DEFAULT_WAKE_URI = os.getenv("AVA_WAKEWORD_URI", "tcp://127.0.0.1:10400")
DEFAULT_WAKE_WORD = os.getenv("AVA_WAKEWORD_NAME", "hey_ava")


def parse_tcp_uri(uri):
    parsed = urlparse(str(uri).strip())
    if parsed.scheme != "tcp":
        raise ValueError("Wake-word URI moet tcp://host:port gebruiken.")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("Wake-word URI mist host of poort.")
    return parsed.hostname, parsed.port


def _event_header(event_type, data=None, payload_length=0):
    header = {"type": event_type}
    if data:
        header["data"] = data
    if payload_length:
        header["payload_length"] = int(payload_length)
    return header


async def send_event(writer, event_type, data=None, payload=b""):
    header = _event_header(event_type, data, len(payload))
    writer.write(
        json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    if payload:
        writer.write(payload)
    await writer.drain()


async def read_event(reader):
    line = await reader.readline()
    if not line:
        raise EOFError("Wake-word server heeft de verbinding gesloten.")

    header = json.loads(line.decode("utf-8"))
    data = dict(header.get("data") or {})

    data_length = int(header.get("data_length") or 0)
    if data_length:
        extra = json.loads((await reader.readexactly(data_length)).decode("utf-8"))
        if isinstance(extra, dict):
            data.update(extra)

    payload_length = int(header.get("payload_length") or 0)
    payload = await reader.readexactly(payload_length) if payload_length else b""
    return str(header.get("type") or ""), data, payload


async def detect_once(uri, wake_word, microphone_name=core.MIC_NAME):
    host, port = parse_tcp_uri(uri)
    print(f"Wake-word server: {host}:{port}")
    print(f"Wake word: {wake_word}")

    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as error:
        raise RuntimeError(
            f"Wake-word server niet bereikbaar op {host}:{port}: {error}"
        ) from error

    microphone, mic_info = core.find_microphone(microphone_name)
    mic_rate = int(mic_info["default_samplerate"])
    chunk_frames = max(1, int(mic_rate * WAKE_FRAME_MS / 1000))
    loop = asyncio.get_running_loop()
    audio_queue = asyncio.Queue(maxsize=50)
    finished = asyncio.Event()
    detected_name = {"value": None}
    server_error = {"value": None}

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"Wake audio status: {status}")
        data = bytes(indata)

        def enqueue():
            if not audio_queue.full():
                audio_queue.put_nowait(data)

        loop.call_soon_threadsafe(enqueue)

    async def receive_events():
        try:
            while not finished.is_set():
                event_type, data, _ = await read_event(reader)
                if event_type == "detection":
                    detected_name["value"] = str(data.get("name") or wake_word)
                    finished.set()
                elif event_type == "error":
                    server_error["value"] = str(
                        data.get("text") or data.get("message") or data or "onbekende fout"
                    )
                    finished.set()
        except (EOFError, asyncio.IncompleteReadError) as error:
            if not finished.is_set():
                server_error["value"] = str(error)
                finished.set()

    receiver = asyncio.create_task(receive_events())

    try:
        await send_event(writer, "detect", {"names": [wake_word]})
        await send_event(
            writer,
            "audio-start",
            {
                "rate": WAKE_SAMPLE_RATE,
                "width": WAKE_SAMPLE_WIDTH,
                "channels": WAKE_CHANNELS,
            },
        )

        print("Luisteren... zeg 'Hey AVA'. Ctrl+C stopt.")
        with sd.RawInputStream(
            samplerate=mic_rate,
            blocksize=chunk_frames,
            device=microphone,
            channels=core.CHANNELS,
            dtype="int16",
            callback=audio_callback,
        ):
            while not finished.is_set():
                try:
                    raw = await asyncio.wait_for(audio_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue

                pcm16 = core.resample_pcm16_mono(raw, mic_rate, WAKE_SAMPLE_RATE)
                await send_event(
                    writer,
                    "audio-chunk",
                    {
                        "rate": WAKE_SAMPLE_RATE,
                        "width": WAKE_SAMPLE_WIDTH,
                        "channels": WAKE_CHANNELS,
                    },
                    pcm16,
                )

        try:
            await send_event(writer, "audio-stop")
        except (BrokenPipeError, ConnectionResetError):
            pass

        if server_error["value"]:
            raise RuntimeError(f"Wake-word serverfout: {server_error['value']}")

        if detected_name["value"]:
            print(f"DETECTED: {detected_name['value']}")
            return detected_name["value"]

        raise RuntimeError("Wake-word detectie stopte zonder resultaat.")
    finally:
        finished.set()
        receiver.cancel()
        try:
            await receiver
        except asyncio.CancelledError:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


def build_parser():
    parser = argparse.ArgumentParser(
        description="Project AVA standalone Wyoming wake-word probe"
    )
    parser.add_argument(
        "--uri",
        default=DEFAULT_WAKE_URI,
        help=f"Wyoming TCP URI (default: {DEFAULT_WAKE_URI})",
    )
    parser.add_argument(
        "--wake-word",
        default=DEFAULT_WAKE_WORD,
        help=f"Wake-word modelnaam (default: {DEFAULT_WAKE_WORD})",
    )
    parser.add_argument(
        "--microphone",
        default=core.MIC_NAME,
        help=f"Substring van microfoonnaam (default: {core.MIC_NAME})",
    )
    return parser


def main():
    args = build_parser().parse_args()
    try:
        asyncio.run(detect_once(args.uri, args.wake_word, args.microphone))
    except KeyboardInterrupt:
        print("\nWake-word probe gestopt.")
        return 130
    except Exception as error:
        print(f"Wake-word probe fout: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
