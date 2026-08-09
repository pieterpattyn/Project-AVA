# Project AVA - Home Assistant setup

AVA v1.0 keeps Home Assistant as the source of truth for smart-home state and control.

## 1. Create a Home Assistant token

In Home Assistant, open your user profile and create a **Long-Lived Access Token**. Copy it immediately and keep it private.

## 2. Configure Project AVA

Add these variables to the local `.env` file on the Raspberry Pi:

```env
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=PASTE_YOUR_LONG_LIVED_ACCESS_TOKEN_HERE
```

Use the actual Home Assistant URL if `homeassistant.local` is not reachable from the Pi.

Do not commit `.env` or the token to Git. Project AVA ignores `.env`.

## 3. Run AVA v1.0

```bash
git pull
python software/ava_core/realtime_v1.py
```

At startup AVA performs a small Home Assistant API preflight. A healthy setup prints something similar to:

```text
Project AVA v1.0
Home Assistant: verbonden met http://homeassistant.local:8123
Tools: tijd/datum + weer + Home Assistant
Connected. Speak naturally. Ctrl+C stops the process.
```

## Supported Home Assistant tools

### Read state

AVA can read any Home Assistant entity state by friendly name or `entity_id`.

Examples:

- "Wat is de temperatuur in de woonkamer?"
- "Staat de lamp in de keuken aan?"
- "Wat is de status van sensor.woonkamer_temperatuur?"

### Find entities

AVA can search entity names when the requested device is unclear or when the user explicitly asks for a list.

Examples:

- "Welke lampen ken je?"
- "Welke sensoren heb ik in de keuken?"

For a clear direct command AVA should skip discovery and call the control tool immediately.

### Control

Write access is deliberately limited to `light` and `switch` domains. Supported actions are `turn_on`, `turn_off` and `toggle`. Lights can also use a brightness percentage.

Examples:

- "Doe de keukenlamp aan."
- "Zet de keukenlamp op 30 procent."
- "Doe de koffiemachine uit." (only when it is exposed as a Home Assistant `switch`)

Locks, alarm systems, covers and other domains are not controlled in v1.0.

## Silent tool routing

Likely Home Assistant commands are routed into a text-only Realtime tool-selection response with tool use required. Because that first response cannot emit audio, AVA cannot speak a filler sentence before the function call.

After the Home Assistant result is available, AVA creates one compact audio follow-up with additional tool use disabled. Ordinary non-tool conversation stays on the direct audio path.

## Verified feedback

A successful Home Assistant service request is not treated as proof that the reported entity state has already caught up. AVA therefore verifies the resulting state for a short period after a control command.

For light brightness, the tool result distinguishes:

- `command_accepted`
- `state_verified`
- `brightness_verified`
- `brightness_requested_pct`
- `brightness_reported_pct`

If Home Assistant accepted a command but the state endpoint still lags, AVA reports that status confirmation is pending instead of falsely claiming that the command failed.

## API behaviour

AVA reads entity state with Home Assistant REST state endpoints and controls devices through `/api/services/<domain>/<service>`. Authentication uses the configured Bearer token.
