#!/usr/bin/env python3
"""Project AVA remote speech-to-text client.

Sends an audio file to an OpenAI-compatible transcription endpoint such as
Speaches and prints only the transcript text to stdout.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://73.10.11.99:8000"
DEFAULT_MODEL = "whisper-1"


def _multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----ava-stt-{uuid.uuid4().hex}"
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode(),
            b"\r\n",
        ])

    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.extend([
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{file_path.name}"\r\n'
        ).encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        file_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])

    return b"".join(parts), boundary


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: remote_stt.py AUDIO [LANGUAGE] [MODEL]", file=sys.stderr)
        return 2

    audio = Path(sys.argv[1]).expanduser().resolve()
    language = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else "nl"
    model = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else DEFAULT_MODEL

    if not audio.is_file():
        print(f"audio file not found: {audio}", file=sys.stderr)
        return 2

    base_url = os.environ.get("AVA_STT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/v1/audio/transcriptions"

    body, boundary = _multipart({"model": model, "language": language}, audio)
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    try:
        with urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except Exception as exc:
        print(f"remote STT failed: {exc}", file=sys.stderr)
        return 1

    text = str(payload.get("text", "")).strip()
    if not text:
        print("remote STT returned an empty transcript", file=sys.stderr)
        return 1

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
