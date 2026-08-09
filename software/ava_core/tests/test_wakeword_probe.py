import json
import sys
import unittest
from pathlib import Path

AVA_CORE = Path(__file__).resolve().parents[1]
if str(AVA_CORE) not in sys.path:
    sys.path.insert(0, str(AVA_CORE))

import wakeword_probe as wake


class WakeWordProbeTests(unittest.TestCase):
    def test_parse_tcp_uri(self):
        self.assertEqual(
            wake.parse_tcp_uri("tcp://127.0.0.1:10400"),
            ("127.0.0.1", 10400),
        )

    def test_parse_tcp_uri_rejects_non_tcp(self):
        with self.assertRaises(ValueError):
            wake.parse_tcp_uri("http://127.0.0.1:10400")

    def test_audio_chunk_header_has_payload_length(self):
        header = wake._event_header(
            "audio-chunk",
            {"rate": 16000, "width": 2, "channels": 1},
            payload_length=320,
        )
        self.assertEqual(header["type"], "audio-chunk")
        self.assertEqual(header["payload_length"], 320)
        self.assertEqual(header["data"]["rate"], 16000)
        json.dumps(header)


if __name__ == "__main__":
    unittest.main()
