import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from main import AVA


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
            self.stateChanged.emit()


class AVAWithUI(AVA):
    def __init__(self, bridge):
        self.bridge = bridge
        super().__init__()

    def set_state(self, state):
        super().set_state(state)
        self.bridge.setState(state.value)


def conversation_worker(ava):
    try:
        ava.start()
    except Exception as error:
        print(f"AVA worker stopped: {error}")


def configure_wayland_from_ssh():
    if not os.environ.get("WAYLAND_DISPLAY"):
        runtime_dir = f"/run/user/{os.getuid()}"
        wayland_socket = Path(runtime_dir) / "wayland-0"
        if wayland_socket.exists():
            os.environ.setdefault("XDG_RUNTIME_DIR", runtime_dir)
            os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
            os.environ.setdefault("QT_QPA_PLATFORM", "wayland")


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

    ava = AVAWithUI(bridge)
    worker = threading.Thread(
        target=conversation_worker,
        args=(ava,),
        daemon=True,
    )
    worker.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
