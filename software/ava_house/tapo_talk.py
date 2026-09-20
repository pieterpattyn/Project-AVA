#!/usr/bin/env python3
"""Local TP-Link Tapo camera talkback client for Project AVA.

This module talks directly to the camera's local TCP/8800 stream endpoint,
opens a Tapo talk session, and sends PCMA/8000 audio wrapped in the MPEG-TS
format accepted by Tapo speakers.

Credentials are never stored in this file. Set TAPO_CLOUD_PASSWORD in the
runtime environment or let the CLI prompt securely.

Protocol validation for the MPEG-TS framing was informed by the Apache-2.0
HomeSec project (lan17/homesec).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import os
import re
import secrets
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import shutil
from dataclasses import dataclass

TS_PACKET_SIZE = 188
PAT_PID = 0x0000
PMT_PID = 0x1000
AUDIO_PID = 0x0100
STREAM_TYPE_PCMA_TAPO = 0x90
AUDIO_STREAM_ID = 0xC0
MPEGTS_CLOCK = 90_000
AUDIO_RATE = 8_000
PTS_MASK = (1 << 33) - 1

CAMERA_HOSTS = {
    "bureau": "73.10.11.64",
    "living": "73.10.11.66",
    "garage": "73.10.11.68",
    "bar": "73.10.11.65",
}


def _md5_text(*parts: str) -> str:
    return hashlib.md5(":".join(parts).encode()).hexdigest()


def _header_value(auth: str, name: str) -> str:
    match = re.search(rf'{re.escape(name)}="([^"]+)"', auth)
    return match.group(1) if match else ""


def _read_http_headers(reader) -> tuple[str, dict[str, str]]:
    status = reader.readline().decode("latin1").strip()
    headers: dict[str, str] = {}
    while True:
        line = reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        key, value = line.decode("latin1").split(":", 1)
        headers[key.lower()] = value.strip()
    return status, headers


def _mpeg2_crc32(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def _section(table_id: int, body: bytes) -> bytes:
    length = len(body) + 4
    raw = bytes([table_id, 0xB0 | ((length >> 8) & 0x0F), length & 0xFF]) + body
    return raw + _mpeg2_crc32(raw).to_bytes(4, "big")


def _pat() -> bytes:
    body = (
        (1).to_bytes(2, "big")
        + b"\xc1\x00\x00"
        + (1).to_bytes(2, "big")
        + bytes([0xE0 | ((PMT_PID >> 8) & 0x1F), PMT_PID & 0xFF])
    )
    return _section(0x00, body)


def _pmt() -> bytes:
    body = (
        (1).to_bytes(2, "big")
        + b"\xc1\x00\x00"
        + bytes([0xE0 | ((AUDIO_PID >> 8) & 0x1F), AUDIO_PID & 0xFF])
        + b"\xf0\x00"
        + bytes([STREAM_TYPE_PCMA_TAPO, 0xE0 | ((AUDIO_PID >> 8) & 0x1F), AUDIO_PID & 0xFF])
        + b"\xf0\x00"
    )
    return _section(0x02, body)


def _encode_pts(pts: int) -> bytes:
    pts &= PTS_MASK
    return bytes([
        0x21 | (((pts >> 30) & 0x07) << 1),
        (pts >> 22) & 0xFF,
        (((pts >> 15) & 0x7F) << 1) | 1,
        (pts >> 7) & 0xFF,
        ((pts & 0x7F) << 1) | 1,
    ])


def _encode_pcr(base: int) -> bytes:
    return (((base & PTS_MASK) << 15) | (0x3F << 9)).to_bytes(6, "big")


@dataclass
class TapoTSMuxer:
    continuity: dict[int, int]
    pts: int = 0
    remainder: int = 0

    def __init__(self) -> None:
        self.continuity = {}

    def _packet(self, pid: int, payload: bytes, start: bool, pcr: int | None = None) -> bytes:
        cc = self.continuity.get(pid, 0) & 0x0F
        self.continuity[pid] = (cc + 1) & 0x0F
        max_payload = 176 if pcr is not None else 184
        if len(payload) > max_payload:
            raise ValueError("TS payload too large")

        second = (0x40 if start else 0) | ((pid >> 8) & 0x1F)

        if pcr is None and len(payload) == 184:
            return bytes([0x47, second, pid & 0xFF, 0x10 | cc]) + payload

        adaptation_length = 183 - len(payload)
        flags = 0x10 if pcr is not None else 0x00
        pcr_bytes = _encode_pcr(pcr) if pcr is not None else b""
        stuffing = adaptation_length - 1 - len(pcr_bytes)
        if stuffing < 0:
            raise ValueError("TS adaptation field too small")

        packet = (
            bytes([0x47, second, pid & 0xFF, 0x30 | cc, adaptation_length, flags])
            + pcr_bytes
            + (b"\xff" * stuffing)
            + payload
        )
        if len(packet) != TS_PACKET_SIZE:
            raise ValueError(f"Invalid TS packet size: {len(packet)}")
        return packet

    def _packetize(self, pid: int, payload: bytes, *, pcr: int | None = None) -> bytes:
        out = bytearray()
        offset = 0
        first = True
        while offset < len(payload):
            max_payload = 176 if first and pcr is not None else 184
            chunk = payload[offset:offset + max_payload]
            offset += len(chunk)
            out += self._packet(pid, chunk, start=first, pcr=pcr if first else None)
            first = False
        return bytes(out)

    def header(self) -> bytes:
        return self._packetize(PAT_PID, b"\x00" + _pat()) + self._packetize(PMT_PID, b"\x00" + _pmt())

    def audio(self, pcma: bytes) -> bytes:
        pts = self.pts
        units = len(pcma) * MPEGTS_CLOCK + self.remainder
        inc, self.remainder = divmod(units, AUDIO_RATE)
        self.pts = (self.pts + inc) & PTS_MASK

        pts_bytes = _encode_pts(pts)
        pes_header = bytes([0x80, 0x80, len(pts_bytes)]) + pts_bytes
        packet_len = len(pes_header) + len(pcma)
        pes = (
            b"\x00\x00\x01"
            + bytes([AUDIO_STREAM_ID])
            + packet_len.to_bytes(2, "big")
            + pes_header
            + pcma
        )
        return self._packetize(AUDIO_PID, pes, pcr=pts)


_ALAW_SEG_END = (0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF)


def _linear_to_alaw(sample: int) -> int:
    mask = 0xD5 if sample >= 0 else 0x55
    if sample < 0:
        sample = -sample - 8
    sample = min(sample, 32767)

    segment = 8
    for idx, end in enumerate(_ALAW_SEG_END):
        if sample <= end:
            segment = idx
            break

    if segment >= 8:
        value = 0x7F
    else:
        value = segment << 4
        if segment < 2:
            value |= (sample >> 4) & 0x0F
        else:
            value |= (sample >> (segment + 3)) & 0x0F
    return value ^ mask


def pcm16le_to_pcma(pcm: bytes) -> bytes:
    if len(pcm) % 2:
        raise ValueError("PCM16LE length must be even")
    return bytes(_linear_to_alaw(sample) for (sample,) in struct.iter_unpack("<h", pcm))


class TapoTalkClient:
    def __init__(self, host: str, cloud_password: str, port: int = 8800, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.cloud_password = cloud_password
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.reader = None
        self.session_id: str | None = None

    def _send(self, data: str | bytes) -> None:
        if self.sock is None:
            raise RuntimeError("Not connected")
        self.sock.sendall(data.encode() if isinstance(data, str) else data)

    def connect(self) -> str:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self.reader = self.sock.makefile("rb")

        base_headers = (
            f"POST /stream HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Content-Type: multipart/mixed; boundary=--client-stream-boundary--\r\n"
            "Connection: keep-alive\r\n"
        )

        self._send(base_headers + "\r\n")
        status, headers = _read_http_headers(self.reader)
        if "401" not in status:
            raise RuntimeError(f"Expected Digest challenge, got {status}")

        challenge = headers.get("www-authenticate", "")
        realm = _header_value(challenge, "realm")
        nonce = _header_value(challenge, "nonce")
        opaque = _header_value(challenge, "opaque")
        qop = _header_value(challenge, "qop") or "auth"

        hashed_password = hashlib.sha256(self.cloud_password.encode()).hexdigest().upper()
        username = "admin"
        nc = "00000001"
        cnonce = secrets.token_hex(16)
        ha1 = _md5_text(username, realm, hashed_password)
        ha2 = _md5_text("POST", "/stream")
        response = _md5_text(ha1, nonce, nc, cnonce, qop, ha2)

        authorization = (
            f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
            f'uri="/stream", qop={qop}, nc={nc}, cnonce="{cnonce}", '
            f'response="{response}"'
        )
        if opaque:
            authorization += f', opaque="{opaque}", algorithm=MD5'

        self._send(base_headers + f"Authorization: {authorization}\r\n\r\n")
        status, _ = _read_http_headers(self.reader)
        if "200" not in status:
            raise RuntimeError(f"Tapo authentication failed: {status}")

        request = json.dumps(
            {
                "params": {"talk": {"mode": "aec"}, "method": "get"},
                "seq": 3,
                "type": "request",
            },
            separators=(",", ":"),
        ).encode()

        self._send(
            b"----client-stream-boundary--\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(request)}\r\n\r\n".encode()
            + request
            + b"\r\n"
        )

        while True:
            line = self.reader.readline()
            if not line:
                raise RuntimeError("Camera closed connection before talk setup completed")
            if not line.startswith(b"----device-stream-boundary--"):
                continue

            part_headers: dict[str, str] = {}
            while True:
                line = self.reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                key, value = line.decode("latin1").split(":", 1)
                part_headers[key.lower()] = value.strip()

            length = int(part_headers.get("content-length", "0"))
            payload = self.reader.read(length)
            result = json.loads(payload)
            params = result.get("params", {})
            if params.get("error_code") != 0:
                raise RuntimeError(f"Tapo talk setup rejected: {result}")
            self.session_id = str(params["session_id"])
            return self.session_id

    def send_mp2t(self, payload: bytes) -> None:
        if self.session_id is None:
            raise RuntimeError("Talk session not open")
        self._send(
            b"----client-stream-boundary--\r\n"
            b"Content-Type: audio/mp2t\r\n"
            b"X-If-Encrypt: 0\r\n"
            + f"X-Session-Id: {self.session_id}\r\n".encode()
            + f"Content-Length: {len(payload)}\r\n\r\n".encode()
            + payload
            + b"\r\n"
        )

    def close(self) -> None:
        try:
            if self.reader is not None:
                self.reader.close()
        finally:
            if self.sock is not None:
                self.sock.close()
        self.reader = None
        self.sock = None
        self.session_id = None


def send_tone(client: TapoTalkClient, frequency: float, seconds: float, amplitude: int = 7000) -> None:
    mux = TapoTSMuxer()
    client.send_mp2t(mux.header())

    frame_samples = 160
    total_samples = max(1, int(AUDIO_RATE * seconds))

    for start in range(0, total_samples, frame_samples):
        count = min(frame_samples, total_samples - start)
        pcm = bytearray()
        for n in range(start, start + count):
            sample = int(amplitude * math.sin(2 * math.pi * frequency * n / AUDIO_RATE))
            pcm += struct.pack("<h", sample)
        client.send_mp2t(mux.audio(pcm16le_to_pcma(bytes(pcm))))
        time.sleep(count / AUDIO_RATE)


def send_pcm16le(client: TapoTalkClient, pcm: bytes, preroll_ms: int = 500) -> None:
    """Send mono 8 kHz signed 16-bit little-endian PCM in real time.

    A short silent preroll gives the camera speaker/audio path time to wake up,
    preventing the first word(s) from being clipped.
    """
    if len(pcm) % 2:
        pcm = pcm[:-1]

    if preroll_ms > 0:
        silence_samples = int(AUDIO_RATE * preroll_ms / 1000)
        pcm = (b"\x00\x00" * silence_samples) + pcm

    mux = TapoTSMuxer()
    client.send_mp2t(mux.header())

    frame_bytes = 160 * 2  # 20 ms at 8 kHz, S16LE mono
    for offset in range(0, len(pcm), frame_bytes):
        frame = pcm[offset:offset + frame_bytes]
        if not frame:
            break
        client.send_mp2t(mux.audio(pcm16le_to_pcma(frame)))
        time.sleep((len(frame) // 2) / AUDIO_RATE)


def edge_tts_to_pcm16le(text: str, voice: str) -> bytes:
    """Generate Edge TTS speech and convert it to mono 8 kHz S16LE PCM."""
    edge_tts = os.environ.get("EDGE_TTS_BIN") or shutil.which("edge-tts")
    if not edge_tts:
        hermes_edge_tts = "/home/hermes/.hermes/hermes-agent/venv/bin/edge-tts"
        if os.path.exists(hermes_edge_tts):
            edge_tts = hermes_edge_tts
    if not edge_tts:
        raise RuntimeError("edge-tts executable not found")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg executable not found")

    with tempfile.TemporaryDirectory(prefix="ava-tapo-tts-") as tmp:
        mp3 = os.path.join(tmp, "speech.mp3")
        subprocess.run(
            [edge_tts, "--voice", voice, "--text", text, "--write-media", mp3],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                mp3,
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                str(AUDIO_RATE),
                "pipe:1",
            ],
            check=True,
            stdout=subprocess.PIPE,
        )
        return result.stdout


def _load_tapo_password() -> str:
    password = os.environ.get("TAPO_CLOUD_PASSWORD")
    if password is None:
        password_file = Path(
            os.environ.get(
                "TAPO_CLOUD_PASSWORD_FILE",
                "~/.config/ava/tapo_cloud_password",
            )
        ).expanduser()
        if password_file.is_file():
            password = password_file.read_text(encoding="utf-8").rstrip("\r\n")
    if password is None:
        password = getpass.getpass("Tapo cloud password: ")
    return password


def speak_room(
    room: str,
    text: str,
    *,
    voice: str = "nl-BE-DenaNeural",
    preroll_ms: int = 500,
    port: int = 8800,
) -> None:
    """Speak text through the configured Tapo camera for a room."""
    try:
        host = CAMERA_HOSTS[room]
    except KeyError as exc:
        known = ", ".join(sorted(CAMERA_HOSTS))
        raise ValueError(f"Unknown room/camera '{room}'. Known: {known}") from exc

    password = _load_tapo_password()
    client = TapoTalkClient(host, password, port=port)
    try:
        client.connect()
        pcm = edge_tts_to_pcm16le(text, voice)
        send_pcm16le(client, pcm, preroll_ms=preroll_ms)
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Send local talkback audio to a TP-Link Tapo camera")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--camera", choices=sorted(CAMERA_HOSTS), help="Configured camera name")
    target.add_argument("--host", help="Camera IP or hostname")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--tone", type=float, default=650.0, help="Test tone frequency in Hz")
    parser.add_argument("--seconds", type=float, default=1.0)
    parser.add_argument("--amplitude", type=int, default=7000)
    parser.add_argument("--text", help="Speak this text instead of a test tone")
    parser.add_argument("--voice", default="nl-BE-DenaNeural", help="Edge TTS voice")
    parser.add_argument(
        "--preroll-ms",
        type=int,
        default=500,
        help="Silence before speech so the camera speaker can wake up",
    )
    args = parser.parse_args()

    host = CAMERA_HOSTS[args.camera] if args.camera else args.host

    password = _load_tapo_password()

    client = TapoTalkClient(host, password, port=args.port)
    try:
        session = client.connect()
        print(f"AUTH OK, session {session}")
        if args.text:
            print(f"TTS: {args.voice}")
            pcm = edge_tts_to_pcm16le(args.text, args.voice)
            send_pcm16le(client, pcm, preroll_ms=args.preroll_ms)
            print("SPRAAK VERSTUURD")
        else:
            send_tone(client, args.tone, args.seconds, args.amplitude)
            print("TESTTOON VERSTUURD")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
