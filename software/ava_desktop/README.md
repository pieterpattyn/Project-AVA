# AVA Desktop

AVA Desktop is the PC-facing shell for Project AVA.

The first milestone deliberately keeps the responsibilities small:

- show AVA as a state-driven desktop avatar;
- show whether the local RTX AI stack is available;
- start/stop the local `ava-llm` and `ava-stt` containers from one button;
- provide the UI states that the upcoming local voice loop will drive.

The Hermes LXC remains AVA's central agent/orchestrator. The desktop app is intended to become one local face/ear/voice for that same agent, not a second independent brain.

## Current states

- `idle`
- `listening`
- `thinking`
- `speaking`

The **Praat** button is currently a UI smoke test only. It briefly enters the listening state. Real microphone -> Whisper -> Hermes -> TTS wiring is the next milestone.

## Local AI status

By default the app probes:

- Speaches: `http://127.0.0.1:8000/health`
- Ollama: `http://127.0.0.1:11434/api/tags`

The **RTX AI aan/uit** button controls these Docker containers:

- `ava-llm`
- `ava-stt`

On Windows it invokes Docker through WSL distro `Ubuntu-24.04`.
On WSL/Linux it calls Docker directly.

Optional overrides:

```text
AVA_WSL_DISTRO=Ubuntu-24.04
AVA_STT_HEALTH_URL=http://127.0.0.1:8000/health
AVA_LLM_HEALTH_URL=http://127.0.0.1:11434/api/tags
```

## Run

Create a small Python environment on the PC/WSL and install the desktop dependency:

```bash
python -m venv .venv-desktop
. .venv-desktop/bin/activate
pip install -r software/ava_desktop/requirements.txt
python software/ava_desktop/app.py
```

On native Windows PowerShell:

```powershell
py -m venv .venv-desktop
.\.venv-desktop\Scripts\Activate.ps1
pip install -r software\ava_desktop\requirements.txt
python software\ava_desktop\app.py
```

Press **Esc** to close the window.
