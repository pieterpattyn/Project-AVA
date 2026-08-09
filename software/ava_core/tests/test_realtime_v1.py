import json
import sys
import tempfile
import unittest
from pathlib import Path

AVA_CORE = Path(__file__).resolve().parents[1]
if str(AVA_CORE) not in sys.path:
    sys.path.insert(0, str(AVA_CORE))

import realtime_v1 as ava


class MemoryTests(unittest.TestCase):
    def make_memory(self, initial=None):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "memory.json"
        if initial is not None:
            path.write_text(json.dumps(initial), encoding="utf-8")
        return ava.LocalMemory(path)

    def test_canonical_location_removes_correction_suffix(self):
        self.assertEqual(ava._canonical_location("Hooglede zonder n"), "Hooglede")

    def test_residence_is_single_valued(self):
        memory = self.make_memory(
            {"name": "Pieter", "preferences": {}, "facts": ["ik woon in Brugge"]}
        )
        actions = ava.process_memory_request("Ik woon in Hooglede.", memory)
        self.assertIn("woonplaats aangepast naar Hooglede", actions)
        self.assertEqual(memory.data["facts"], ["ik woon in Hooglede"])

    def test_end_letter_correction(self):
        memory = self.make_memory(
            {"name": None, "preferences": {}, "facts": ["ik woon in Hoogleden"]}
        )
        ava.process_memory_request("Dat is zonder n achteraan.", memory)
        self.assertEqual(memory.data["facts"], ["ik woon in Hooglede"])

    def test_favorite_change_is_single_valued(self):
        memory = self.make_memory(
            {"name": None, "preferences": {"kleur": "rood"}, "facts": []}
        )
        ava.process_memory_request(
            "Ik wil mijn favoriete kleur opnieuw veranderen naar blauw.", memory
        )
        self.assertEqual(memory.data["preferences"]["kleur"], "blauw")


class HomeAssistantVerificationTests(unittest.TestCase):
    def test_expected_toggle_state(self):
        self.assertEqual(ava._expected_state("toggle", "off"), "on")
        self.assertEqual(ava._expected_state("toggle", "on"), "off")

    def test_brightness_verification(self):
        state = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"brightness": 102},
        }
        verified, state_ok, brightness_ok, reported = ava._verification(
            state, "on", 40
        )
        self.assertTrue(verified)
        self.assertTrue(state_ok)
        self.assertTrue(brightness_ok)
        self.assertEqual(reported, 40)

    def test_entity_name_exact_match_scores_highest(self):
        state = {
            "entity_id": "light.beneden_binnen",
            "state": "off",
            "attributes": {"friendly_name": "Beneden binnen"},
        }
        self.assertEqual(ava._candidate_score("Beneden binnen", state), 1.0)


class ToolRoutingTests(unittest.TestCase):
    def test_weather_turn_requires_tool(self):
        self.assertTrue(ava._turn_likely_needs_tool("Wat is het weer bij mij?"))

    def test_home_assistant_direct_control_requires_tool(self):
        self.assertTrue(ava._turn_likely_needs_tool("Zet beneden binnen aan."))

    def test_brightness_turn_requires_tool(self):
        self.assertTrue(ava._turn_likely_needs_tool("Zet de helderheid op 40 procent."))

    def test_current_time_requires_tool(self):
        self.assertTrue(ava._turn_likely_needs_tool("Hoe laat is het?"))

    def test_memory_question_stays_on_conversation_path(self):
        self.assertFalse(ava._turn_likely_needs_tool("Wat is mijn favoriete kleur?"))


if __name__ == "__main__":
    unittest.main()
