"""Project AVA desktop shell.

First desktop milestone:
- state-driven AVA face
- local RTX service status
- one-click local AI start/stop
- cross-platform control from Windows or WSL/Linux

Voice/STT/Hermes conversation wiring is intentionally the next layer.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine


STT_HEALTH_URL = os.getenv("AVA_STT_HEALTH_URL", "http://127.0.0.1:8000/health")
LLM_HEALTH_URL = os.getenv("AVA_LLM_HEALTH_URL", "http://127.0.0.1:11434/api/tags")
WSL_DISTRO = os.getenv("AVA_WSL_DISTRO", "Ubuntu-24.04")
STATUS_INTERVAL_MS = 2500
HTTP_TIMEOUT_SECONDS = 0.7
STT_TRANSCRIBE_URL = os.getenv(
    "AVA_STT_TRANSCRIBE_URL",
    "http://127.0.0.1:8000/v1/audio/transcriptions",
)
STT_MODEL = os.getenv("AVA_STT_MODEL", "Systran/faster-whisper-large-v3")
STT_LANGUAGE = os.getenv("AVA_STT_LANGUAGE", "nl")
AUDIO_SOURCE = os.getenv("AVA_AUDIO_SOURCE", "RDPSource")
RECORD_SECONDS = os.getenv("AVA_RECORD_SECONDS", "5")
AUDIO_FILE = os.getenv("AVA_AUDIO_FILE", "/tmp/ava-desktop-mic.wav")


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _running_in_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(
            encoding="utf-8", errors="ignore"
        ).lower()
    except OSError:
        return False


def _docker_command(action: str) -> list[str]:
    """Return the safest local command for controlling AVA's GPU containers."""

    docker_args = ["docker", action, "ava-llm", "ava-stt"]

    if platform.system() == "Windows":
        shell_cmd = " ".join(docker_args)
        return [
            "wsl.exe",
            "-d",
            WSL_DISTRO,
            "--",
            "bash",
            "-lc",
            shell_cmd,
        ]

    # Native Linux and WSL can talk to the same Docker daemon directly.
    return docker_args


class AvaDesktopBridge(QObject):
    stateChanged = Signal()
    rtxChanged = Signal()
    statusTextChanged = Signal()
    busyChanged = Signal()
    errorTextChanged = Signal()
    transcriptTextChanged = Signal()

    _statusProbeFinished = Signal(bool, bool)
    _controlFinished = Signal(bool, str)
    _speechPhase = Signal(str)
    _speechFinished = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self._state = "idle"
        self._rtx_state = "checking"
        self._status_text = "Lokale AI controleren..."
        self._busy = False
        self._error_text = ""
        self._transcript_text = ""
        self._probe_running = False

        self._statusProbeFinished.connect(self._apply_probe)
        self._controlFinished.connect(self._apply_control_result)
        self._speechPhase.connect(self._apply_speech_phase)
        self._speechFinished.connect(self._apply_speech_result)

        self._timer = QTimer(self)
        self._timer.setInterval(STATUS_INTERVAL_MS)
        self._timer.timeout.connect(self.refreshStatus)
        self._timer.start()

        QTimer.singleShot(100, self.refreshStatus)

    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._state

    @Property(str, notify=rtxChanged)
    def rtxState(self) -> str:
        return self._rtx_state

    @Property(str, notify=statusTextChanged)
    def statusText(self) -> str:
        return self._status_text

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=errorTextChanged)
    def errorText(self) -> str:
        return self._error_text

    @Property(str, notify=transcriptTextChanged)
    def transcriptText(self) -> str:
        return self._transcript_text

    def _set_state(self, value: str) -> None:
        if value != self._state:
            self._state = value
            self.stateChanged.emit()

    def _set_rtx_state(self, value: str) -> None:
        if value != self._rtx_state:
            self._rtx_state = value
            self.rtxChanged.emit()

    def _set_status_text(self, value: str) -> None:
        if value != self._status_text:
            self._status_text = value
            self.statusTextChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit()

    def _set_error_text(self, value: str) -> None:
        if value != self._error_text:
            self._error_text = value
            self.errorTextChanged.emit()

    def _set_transcript_text(self, value: str) -> None:
        if value != self._transcript_text:
            self._transcript_text = value
            self.transcriptTextChanged.emit()

    @Slot()
    def refreshStatus(self) -> None:
        if self._probe_running:
            return

        self._probe_running = True

        def worker() -> None:
            stt_ok = _http_ok(STT_HEALTH_URL)
            llm_ok = _http_ok(LLM_HEALTH_URL)
            self._statusProbeFinished.emit(llm_ok, stt_ok)

        threading.Thread(target=worker, daemon=True).start()

    @Slot(bool, bool)
    def _apply_probe(self, llm_ok: bool, stt_ok: bool) -> None:
        self._probe_running = False

        if llm_ok and stt_ok:
            self._set_rtx_state("local")
            self._set_status_text("LOCAL · Qwen + Whisper")
            self._set_error_text("")
        elif llm_ok or stt_ok:
            self._set_rtx_state("partial")
            missing = "Whisper" if llm_ok else "Qwen"
            self._set_status_text(f"DEELS ACTIEF · {missing} offline")
        else:
            self._set_rtx_state("off")
            self._set_status_text("RTX AI UIT")

    @Slot()
    def toggleRtx(self) -> None:
        if self._busy:
            return

        action = "stop" if self._rtx_state in {"local", "partial"} else "start"
        self._set_busy(True)
        self._set_error_text("")
        self._set_status_text(
            "Lokale AI stoppen..." if action == "stop" else "Lokale AI starten..."
        )

        def worker() -> None:
            try:
                completed = subprocess.run(
                    _docker_command(action),
                    capture_output=True,
                    text=True,
                    timeout=45,
                    check=False,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW
                        if platform.system() == "Windows"
                        else 0
                    ),
                )
                if completed.returncode != 0:
                    message = (completed.stderr or completed.stdout).strip()
                    self._controlFinished.emit(
                        False, message or f"docker {action} faalde"
                    )
                    return
                self._controlFinished.emit(True, "")
            except (OSError, subprocess.SubprocessError) as exc:
                self._controlFinished.emit(False, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    @Slot(bool, str)
    def _apply_control_result(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if not success:
            self._set_error_text(message)
        QTimer.singleShot(400, self.refreshStatus)

    @Slot()
    def startListening(self) -> None:
        if self._busy or self._state != "idle" or self._rtx_state != "local":
            return

        self._set_busy(True)
        self._set_error_text("")
        self._set_transcript_text("")
        self._set_state("listening")

        def worker() -> None:
            audio_path = Path(AUDIO_FILE)

            try:
                audio_path.unlink(missing_ok=True)

                recorded = subprocess.run(
                    [
                        "timeout",
                        RECORD_SECONDS,
                        "parecord",
                        f"--device={AUDIO_SOURCE}",
                        "--file-format=wav",
                        str(audio_path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                if recorded.returncode not in (0, 124):
                    message = (recorded.stderr or recorded.stdout).strip()
                    self._speechFinished.emit(
                        False, message or "Microfoonopname mislukt"
                    )
                    return

                if not audio_path.is_file() or audio_path.stat().st_size < 1000:
                    self._speechFinished.emit(False, "Geen bruikbare audio opgenomen")
                    return

                self._speechPhase.emit("thinking")

                completed = subprocess.run(
                    [
                        "curl",
                        "-sS",
                        "--fail",
                        STT_TRANSCRIBE_URL,
                        "-F",
                        f"file=@{audio_path}",
                        "-F",
                        f"model={STT_MODEL}",
                        "-F",
                        f"language={STT_LANGUAGE}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                if completed.returncode != 0:
                    message = (completed.stderr or completed.stdout).strip()
                    self._speechFinished.emit(
                        False, message or "Whisper-transcriptie mislukt"
                    )
                    return

                payload = json.loads(completed.stdout)
                transcript = str(payload.get("text", "")).strip()

                if not transcript:
                    self._speechFinished.emit(False, "Whisper hoorde geen tekst")
                    return

                self._speechFinished.emit(True, transcript)

            except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
                self._speechFinished.emit(False, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    @Slot(str)
    def _apply_speech_phase(self, state: str) -> None:
        self._set_state(state)

    @Slot(bool, str)
    def _apply_speech_result(self, success: bool, text: str) -> None:
        self._set_busy(False)

        if success:
            self._set_transcript_text(text)
            self._set_error_text("")
        else:
            self._set_error_text(text)

        self._set_state("idle")


def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setApplicationName("AVA Desktop")
    app.setOrganizationName("Project AVA")

    bridge = AvaDesktopBridge()
    engine = QQmlApplicationEngine()
    engine.setInitialProperties({"avaDesktop": bridge})

    qml_path = Path(__file__).with_name("AvatarDesktop.qml")
    engine.load(QUrl.fromLocalFile(str(qml_path)))

    if not engine.rootObjects():
        return 1

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
