# AVA Architecture

## Purpose

AVA is a modular AI home assistant running on a Raspberry Pi-based console.

The system must support:

- Voice interaction
- Visual feedback through an avatar
- Home Assistant integration
- Camera input
- Cloud and local AI providers
- Future mobile hardware, such as a Roomba-based platform

## Core Principles

1. Modular components
2. Home Assistant remains the source of truth for home automation
3. AI providers must be replaceable
4. The display and avatar must be independent from the AI engine
5. Local processing is preferred where practical
6. Cloud services may be used where they offer clear benefits

## Functional Modules

### Listen

Responsible for:

- Microphone input
- Wake word detection
- Voice activity detection
- Audio preprocessing

Possible implementations:

- openWakeWord
- Whisper
- Cloud speech-to-text
- Home Assistant Assist pipeline

### Think

Responsible for:

- Language model interaction
- Intent interpretation
- Conversation context
- Tool and service selection

Possible implementations:

- OpenAI
- Gemini
- Local models through Ollama
- py-xiaozhi backend
- OpenVoiceOS

### Speak

Responsible for:

- Text-to-speech
- Audio playback
- Voice selection
- Lip-sync data for the avatar

Possible implementations:

- Piper
- OpenAI TTS
- Gemini TTS
- Other cloud voice services

### Display

Responsible for:

- Avatar
- Listening state
- Thinking state
- Speaking state
- Home Assistant dashboard
- Camera and notification views

### See

Responsible for:

- Webcam input
- Image capture
- Object recognition
- QR code recognition
- Future person detection

### Control

Responsible for:

- Home Assistant integration
- MQTT
- Device control
- Smart-home events
- Future Roomba movement commands

### Remember

Responsible for:

- User preferences
- Conversation summaries
- System state
- Optional long-term memory

## Initial Hardware

- Raspberry Pi 4B, 4 GB RAM
- DFRobot 7-inch DSI capacitive touchscreen
- Logitech C270 webcam with microphone
- External speakers through the 3.5 mm audio output
- Optional HiFiBerry DAC+ in a later phase

## Initial Deployment Model

The Raspberry Pi acts as a thin client.

The Pi handles:

- Audio input and output
- Camera
- Display
- Avatar
- Home Assistant interface
- Wake word detection

Heavy AI processing may initially run in the cloud.

## First Milestone

AVA v0.2 - First Words

Goals:

- Detect microphone input
- Send speech to an AI service
- Receive a response
- Play the response through the speakers
- Display basic listening and speaking states
