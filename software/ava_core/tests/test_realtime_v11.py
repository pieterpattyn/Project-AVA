import sys
import unittest
from pathlib import Path

AVA_CORE = Path(__file__).resolve().parents[1]
if str(AVA_CORE) not in sys.path:
    sys.path.insert(0, str(AVA_CORE))

import realtime_v11 as ava


class SessionIdleTrackerTests(unittest.TestCase):
    def make_tracker(self):
        tracker = ava.SessionIdleTracker(
            first_command_timeout=12,
            followup_timeout=8,
            speech_grace=30,
        )
        tracker.start_session()
        return tracker

    def test_first_listening_arms_first_command_timeout(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "thinking", now=100.0)
        self.assertEqual(tracker.deadline(), 112.0)
        self.assertFalse(tracker.expired(now=111.9))
        self.assertTrue(tracker.expired(now=112.0))

    def test_thinking_clears_idle_deadline(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "thinking", now=100.0)
        tracker.observe("thinking", "listening", now=102.0)
        self.assertIsNone(tracker.deadline())
        self.assertFalse(tracker.expired(now=999.0))

    def test_response_then_listening_uses_followup_timeout(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "thinking", now=100.0)
        tracker.observe("thinking", "listening", now=101.0)
        tracker.observe("speaking", "thinking", now=102.0)
        tracker.observe("listening", "speaking", now=105.0)
        self.assertEqual(tracker.deadline(), 113.0)

    def test_redundant_listening_call_grants_speech_grace(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "thinking", now=100.0)
        tracker.observe("listening", "listening", now=110.0)
        self.assertEqual(tracker.deadline(), 140.0)
        self.assertFalse(tracker.expired(now=139.9))

    def test_stop_session_disables_timeout(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "thinking", now=100.0)
        tracker.stop_session()
        self.assertIsNone(tracker.deadline())
        self.assertFalse(tracker.expired(now=1000.0))


if __name__ == "__main__":
    unittest.main()
