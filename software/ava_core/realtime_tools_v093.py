"""Project AVA v0.9.3 - v0.9.2 startup hotfix.

v0.9.2 references MIN_VOICE_DURATION through realtime_tools_app (`base`), but
that module does not re-export the constant. Import it from realtime_app and
attach it to the shared base module before v0.9.2's worker starts.
"""

import realtime_app as core
import realtime_tools_v092 as v092


v092.base.MIN_VOICE_DURATION = core.MIN_VOICE_DURATION


async def realtime_worker_v093(bridge):
    print("Project AVA v0.9.3 - v0.9.2 Startup Hotfix")
    await v092.realtime_worker_v092(bridge)


v092.base.realtime_worker = realtime_worker_v093


if __name__ == "__main__":
    raise SystemExit(v092.base.main())
