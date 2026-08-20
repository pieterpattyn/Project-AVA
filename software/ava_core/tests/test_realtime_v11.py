import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

AVA_CORE = Path(__file__).resolve().parents[1]
if str(AVA_CORE) not in sys.path:
    sys.path.insert(0, str(AVA_CORE))

import realtime_v11 as ava


class CalibrationCacheTests(unittest.TestCase):
    def test_prepare_calibrates_only_once(self):
        cache = ava.CalibrationCache("C270")
        cache._original_calibrator = Mock(return_value=(0.05, 0.08))

        with patch.object(
            ava.v1.core,
            "find_microphone",
            return_value=(1, {"default_samplerate": 48000}),
        ) as find_microphone:
            first = cache.prepare()
            second = cache.prepare()

        self.assertEqual(first, (0.05, 0.08))
        self.assertEqual(second, (0.05, 0.08))
        find_microphone.assert_called_once_with("C270")
        cache._original_calibrator.assert_called_once_with(1, 48000)

    def test_cached_calibrator_reuses_startup_thresholds(self):
        cache = ava.CalibrationCache("C270")
        cache._sample_rate = 48000
        cache._thresholds = (0.064, 0.080)
        cache._original_calibrator = Mock(return_value=(0.1, 0.2))

        result = cache.calibrate(7, 48000)

        self.assertEqual(result, (0.064, 0.080))
        cache._original_calibrator.assert_not_called()

    def test_cached_calibrator_refreshes_if_sample_rate_changes(self):
        cache = ava.CalibrationCache("C270")
        cache._sample_rate = 48000
        cache._thresholds = (0.064, 0.080)
        cache._original_calibrator = Mock(return_value=(0.07, 0.09))

        result = cache.calibrate(7, 44100)

        self.assertEqual(result, (0.07, 0.09))
        self.assertEqual(cache._sample_rate, 44100)
        cache._original_calibrator.assert_called_once_with(7, 44100)


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
        tracker.observe("listening", "waking", now=100.0)
        self.assertEqual(tracker.deadline(), 112.0)
        self.assertFalse(tracker.expired(now=111.9))
        self.assertTrue(tracker.expired(now=112.0))

    def test_thinking_clears_idle_deadline(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "waking", now=100.0)
        tracker.observe("thinking", "listening", now=102.0)
        self.assertIsNone(tracker.deadline())
        self.assertFalse(tracker.expired(now=999.0))

    def test_waking_does_not_arm_idle_timeout(self):
        tracker = self.make_tracker()
        tracker.observe("waking", "idle", now=100.0)
        self.assertIsNone(tracker.deadline())
        self.assertFalse(tracker.expired(now=1000.0))

    def test_response_then_listening_uses_followup_timeout(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "waking", now=100.0)
        tracker.observe("thinking", "listening", now=101.0)
        tracker.observe("speaking", "thinking", now=102.0)
        tracker.observe("listening", "speaking", now=105.0)
        self.assertEqual(tracker.deadline(), 113.0)

    def test_redundant_listening_call_grants_speech_grace(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "waking", now=100.0)
        tracker.observe("listening", "listening", now=110.0)
        self.assertEqual(tracker.deadline(), 140.0)
        self.assertFalse(tracker.expired(now=139.9))

    def test_stop_session_disables_timeout(self):
        tracker = self.make_tracker()
        tracker.observe("listening", "waking", now=100.0)
        tracker.stop_session()
        self.assertIsNone(tracker.deadline())
        self.assertFalse(tracker.expired(now=1000.0))


if __name__ == "__main__":
    unittest.main()
