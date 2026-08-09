"""Project AVA v1.1 phase B - wake-word handoff runtime.

This is deliberately a thin integration layer around the proven v1.0 runtime.
It waits locally for the custom ``hey_ava`` Wyoming/openWakeWord detection and
only then starts the existing v1.0 Realtime application.

Phase B proves the wake-word -> Realtime handoff without modifying
``realtime_v1.py``. The assistant remains awake after the handoff until the
process is stopped; returning to idle/wake mode after a completed turn is the
next phase.
"""

import argparse
import asyncio

import realtime_v1 as v1
import wakeword_probe as wake


VERSION = "1.1-dev"


def build_parser():
    parser = argparse.ArgumentParser(
        description="Project AVA v1.1 wake-word handoff runtime"
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
    return parser


async def wait_for_wake(uri, wake_word, microphone):
    return await wake.detect_once(uri, wake_word, microphone)


def launch(uri, wake_word, microphone):
    print(f"Project AVA v{VERSION}")
    print("State: idle - lokaal wachten op 'Hey AVA'.")
    detected = asyncio.run(wait_for_wake(uri, wake_word, microphone))
    print(f"Wake accepted: {detected}")
    print("Starting proven AVA v1.0 Realtime runtime...")
    return v1.main()


def main():
    args = build_parser().parse_args()
    try:
        return launch(args.wake_uri, args.wake_word, args.microphone)
    except KeyboardInterrupt:
        print("\nAVA v1.1 gestopt.")
        return 130
    except Exception as error:
        print(f"AVA v1.1 wake handoff fout: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
