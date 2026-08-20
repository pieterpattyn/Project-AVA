"""Project AVA v1.1 phase C - wake, converse, sleep, re-arm.

The proven v1.0 Realtime runtime remains untouched. This module owns the
long-lived avatar and repeatedly cycles through:

    idle -> local Hey AVA detection -> v1.0 Realtime session -> idle

A small lifecycle bridge observes AVA state transitions. When Realtime returns
to the listening state and no follow-up arrives before the configured timeout,
the v1.0 coroutine is cancelled cleanly, releasing microphone/output resources
before local wake-word detection is armed again.
"""

import argparse
import asyncio
import signal
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

import realtime_v1 as v1
import wakeword_probe as wake


VERSION = "1.1-dev"
DEFAULT_FIRST_COMMAND_TIMEOUT = 12.0
DEFAULT_FOLLOWUP_TIMEOUT = 8.0
DEFAULT_SPEECH_GRACE = 30.0
REARM_DELAY_SECONDS = 0.45
MONITOR_INTERVAL_SECONDS = 0.05


class SessionIdleTracker:
    """Thread-safe idle timer driven by AvatarBridge state calls.

    ``realtime_v1`` calls ``setState('listening')`` both when it is ready for a
    turn and when semantic VAD reports speech_started. The latter call is often
    redundant because the avatar is already listening. We use that distinction
    to grant a generous speech grace period so a user is never cut off merely
    because they started speaking just before the normal follow-up timeout.
    """

    def __init__(
        self,
        first_command_timeout=DEFAULT_FIRST_COMMAND_TIMEOUT,
        followup_timeout=DEFAULT_FOLLOWUP_TIMEOUT,
        speech_grace=DEFAULT_SPEECH_GRACE,
    ):
        self.first_command_timeout = float(first_command_timeout)
        self.followup_timeout = float(followup_timeout)
        self.speech_grace = float(speech_grace)
        self._lock = threading.Lock()
        self._active = False
        self._had_response = False
        self._deadline = None

    def start_session(self):
        with self._lock:
            self._active = True
            self._had_response = False
            self._deadline = None

    def stop_session(self):
        with self._lock:
            self._active = False
            self._deadline = None

    def observe(self, state, previous_state, now=None):
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            if not self._active:
                return

            if state == "thinking":
                self._deadline = None
                return

            if state == "speaking":
                self._had_response = True
                self._deadline = None
                return

            if state == "idle":
                self._deadline = None
                return

            if state != "listening":
                return

            if previous_state == "listening":
                # A redundant listening call is the v1 semantic-VAD
                # speech_started path. Do not let the normal idle timeout cut
                # through a long utterance.
                self._deadline = now + self.speech_grace
                return

            timeout = (
                self.followup_timeout
                if self._had_response
                else self.first_command_timeout
            )
            self._deadline = now + timeout

    def deadline(self):
        with self._lock:
            return self._deadline

    def expired(self, now=None):
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            return (
                self._active
                and self._deadline is not None
                and now >= self._deadline
            )


class LifecycleBridge(v1.core.AvatarBridge):
    """Normal avatar bridge plus lifecycle observation, including repeats."""

    def __init__(self, tracker):
        super().__init__()
        self.tracker = tracker

    @Slot(str)
    def setState(self, state):
        previous = self._state
        super().setState(state)
        self.tracker.observe(state, previous)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Project AVA v1.1 local wake/sleep lifecycle runtime"
    )
    parser.add_argument(
        "--wake-uri",
        default=wake.DEFAULT_WAKE_URI,
        help=f"Wyoming TCP URI (default: {wake.DEFAULT_WAKE_URI})",
    )
    parser.add_argument(
        "--wake-word",
        default=wake.DEFAULT_WAKE_WORD,
        help=f"Wake-word modelnaam (default: {wake.DEFAULT_WAKE_WORD})",
    )
    parser.add_argument(
        "--microphone",
        default=wake.core.MIC_NAME,
        help=f"Substring van microfoonnaam (default: {wake.core.MIC_NAME})",
    )
    parser.add_argument(
        "--first-command-timeout",
        type=float,
        default=DEFAULT_FIRST_COMMAND_TIMEOUT,
        help=(
            "Seconden om na het wakker worden op de eerste opdracht te wachten "
            f"(default: {DEFAULT_FIRST_COMMAND_TIMEOUT:g})"
        ),
    )
    parser.add_argument(
        "--followup-timeout",
        type=float,
        default=DEFAULT_FOLLOWUP_TIMEOUT,
        help=(
            "Seconden om na een antwoord op vervolgspraak te wachten "
            f"(default: {DEFAULT_FOLLOWUP_TIMEOUT:g})"
        ),
    )
    parser.add_argument(
        "--speech-grace",
        type=float,
        default=DEFAULT_SPEECH_GRACE,
        help=(
            "Maximale spreektijd-grace nadat VAD speech_started meldt "
            f"(default: {DEFAULT_SPEECH_GRACE:g})"
        ),
    )
    return parser


async def wait_for_wake(uri, wake_word, microphone):
    return await wake.detect_once(uri, wake_word, microphone)


async def run_realtime_until_idle(bridge, tracker):
    """Run the proven v1 worker until it ends or the idle tracker expires."""

    tracker.start_session()
    bridge.setState("thinking")
    realtime_task = asyncio.create_task(v1.realtime_worker(bridge))

    try:
        while True:
            if realtime_task.done():
                await realtime_task
                return "runtime-ended"

            if tracker.expired():
                print("Conversation idle timeout: Realtime afsluiten.")
                realtime_task.cancel()
                try:
                    await realtime_task
                except asyncio.CancelledError:
                    pass
                return "idle-timeout"

            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
    finally:
        tracker.stop_session()
        if not realtime_task.done():
            realtime_task.cancel()
            try:
                await realtime_task
            except asyncio.CancelledError:
                pass


async def lifecycle_loop(uri, wake_word, microphone, tracker):
    print(f"Project AVA v{VERSION}")

    while True:
        bridge = tracker.bridge
        bridge.setState("idle")
        print("State: idle - lokaal wachten op 'Hey AVA'.")

        detected = await wait_for_wake(uri, wake_word, microphone)
        print(f"Wake accepted: {detected}")
        print("Starting proven AVA v1.0 Realtime session...")

        try:
            reason = await run_realtime_until_idle(bridge, tracker)
            if reason == "runtime-ended":
                print("Realtime session ended; wake word opnieuw bewapenen.")
        except Exception as error:
            print(f"Realtime session fout: {error}")
        finally:
            tracker.stop_session()
            bridge.setState("idle")

        await asyncio.sleep(REARM_DELAY_SECONDS)


class LifecycleController(SessionIdleTracker):
    """Idle tracker bundled with the persistent avatar bridge."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bridge = LifecycleBridge(self)


def run_lifecycle_thread(uri, wake_word, microphone, tracker):
    try:
        asyncio.run(lifecycle_loop(uri, wake_word, microphone, tracker))
    except Exception as error:
        print(f"AVA v1.1 lifecycle gestopt: {error}")
        tracker.bridge.setState("idle")


def main():
    args = build_parser().parse_args()
    if args.first_command_timeout <= 0:
        raise SystemExit("--first-command-timeout moet groter dan 0 zijn")
    if args.followup_timeout <= 0:
        raise SystemExit("--followup-timeout moet groter dan 0 zijn")
    if args.speech_grace <= 0:
        raise SystemExit("--speech-grace moet groter dan 0 zijn")

    v1.core.configure_wayland_from_ssh()

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    tracker = LifecycleController(
        first_command_timeout=args.first_command_timeout,
        followup_timeout=args.followup_timeout,
        speech_grace=args.speech_grace,
    )
    bridge = tracker.bridge
    engine.rootContext().setContextProperty("avatarBridge", bridge)

    qml_file = Path(v1.__file__).with_name("Avatar.qml")
    engine.load(QUrl.fromLocalFile(str(qml_file)))
    if not engine.rootObjects():
        raise RuntimeError("Could not load AVA avatar UI.")

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    worker = threading.Thread(
        target=run_lifecycle_thread,
        args=(args.wake_uri, args.wake_word, args.microphone, tracker),
        daemon=True,
    )
    worker.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
