import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

AVA_CORE = Path(__file__).resolve().parents[1]
if str(AVA_CORE) not in sys.path:
    sys.path.insert(0, str(AVA_CORE))

import realtime_v11 as ava


class WakeWordHandoffTests(unittest.TestCase):
    def test_launch_waits_for_wake_then_starts_v1(self):
        detector = AsyncMock(return_value="hey_ava")
        with patch.object(ava, "wait_for_wake", detector), patch.object(
            ava.v1, "main", return_value=42
        ) as realtime_main:
            result = ava.launch("tcp://127.0.0.1:10400", "hey_ava", "C270")

        detector.assert_awaited_once_with(
            "tcp://127.0.0.1:10400", "hey_ava", "C270"
        )
        realtime_main.assert_called_once_with()
        self.assertEqual(result, 42)

    def test_launch_does_not_start_v1_when_wake_fails(self):
        detector = AsyncMock(side_effect=RuntimeError("server down"))
        with patch.object(ava, "wait_for_wake", detector), patch.object(
            ava.v1, "main"
        ) as realtime_main:
            with self.assertRaisesRegex(RuntimeError, "server down"):
                ava.launch("tcp://127.0.0.1:10400", "hey_ava", "C270")

        realtime_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
