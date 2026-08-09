# Project AVA - Home Assistant setup

AVA v0.9 adds a small REST bridge to Home Assistant while keeping the stable v0.8.2 realtime/audio/memory/weather stack unchanged.

## 1. Create a Home Assistant token

In Home Assistant, open your user profile and create a **Long-Lived Access Token**. Copy it immediately and keep it private.

## 2. Configure Project AVA

Add these variables to the local `.env` file on the Raspberry Pi:

```env
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=PASTE_YOUR_LONG_LIVED_ACCESS_TOKEN_HERE
```

Use the actual Home Assistant URL if `homeassistant.local` is not reachable from the Pi.

Do not commit `.env` or the token to Git. Project AVA already ignores `.env`.

## 3. Run v0.9

```bash
git pull
python software/ava_core/realtime_tools_v09.py
```

At startup AVA performs a small Home Assistant API preflight. A healthy setup prints something similar to:

```text
Project AVA v0.9 - Home Assistant
Home Assistant: verbonden met http://homeassistant.local:8123
```

## Supported v0.9 Home Assistant tools

### Read state

AVA can read any Home Assistant entity state by friendly name or `entity_id`.

Examples:

- "Wat is de temperatuur in de woonkamer?"
- "Staat de lamp in de keuken aan?"
- "Wat is de status van sensor.woonkamer_temperatuur?"

### Find entities

AVA can search entity names when the requested device is unclear.

Examples:

- "Welke lampen ken je?"
- "Welke sensoren heb ik in de keuken?"

### Control

For the first v0.9 baseline, write access is deliberately limited to `light` and `switch` domains. Supported actions are `turn_on`, `turn_off` and `toggle`. Lights can also use a brightness percentage.

Examples:

- "Doe de keukenlamp aan."
- "Zet de keukenlamp op 30 procent."
- "Doe de koffiemachine uit." (only when it is exposed as a Home Assistant `switch`)

Locks, alarm systems, covers and other domains are not controlled in v0.9. They can be added later after the basic entity matching and service-call flow has been proven stable on the real installation.

## API behaviour

AVA reads entity state with Home Assistant's REST `/api/states` endpoints and controls real devices through `/api/services/<domain>/<service>`. Authentication uses the standard `Authorization: Bearer <token>` header.
