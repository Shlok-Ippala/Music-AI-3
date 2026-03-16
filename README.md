# Reaper AI

An AI assistant that lives inside REAPER and lets you build full tracks by just talking to it.

![REAPER AI Panel](https://img.shields.io/badge/REAPER-7.x-blue) ![Python](https://img.shields.io/badge/Python-3.14-green) ![Model](https://img.shields.io/badge/Gemini-2.5%20Flash-orange)

---

## What it does

You type a prompt. The AI composes a full session inside REAPER — drums, bass, chords, melody — with the right instruments loaded, tempo set, and MIDI written. Everything is editable. Every note is yours to change.

You can also drop any audio file (voice memo, guitar riff, anything) and the app transcribes it directly into MIDI notes inside REAPER using Spotify's basic-pitch neural network.

---

## Features

- **Natural language composition** — describe the vibe, genre, key, BPM and get a full multi-track session
- **Audio to MIDI transcription** — hum a melody, record a riff, drop the file in and it becomes editable MIDI
- **Direct DAW control** — the AI calls 30+ REAPER functions directly (insert tracks, load VSTs, write MIDI, set FX, save project)
- **Music theory engine** — generates chord progressions, bass lines, drum patterns, and melodies with proper voice leading and humanization
- **Smart instrument picker** — automatically selects the right plugin for each track type from your installed VSTs
- **Gemini-style UI** — clean dark side panel that sits alongside your REAPER session

---

## Stack

| Layer | Tech |
|---|---|
| AI Model | Gemini 2.5 Flash via litellm |
| Audio transcription | Spotify basic-pitch (ONNX, python3.12) |
| UI | pywebview + HTML/CSS/JS |
| DAW bridge | HTTP + file-based REAPER bridge |
| Music theory | Custom Python music theory engine |

---

## Setup

### 1. Prerequisites

- [REAPER](https://www.reaper.fm/) 7.x
- Python 3.14
- Python 3.12 (for basic-pitch audio transcription)
- A Gemini API key — get one at [aistudio.google.com](https://aistudio.google.com)

### 2. Install dependencies

```bash
python3.14 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment

Create a `.env` file in the project root:

```
GEMINI_API_KEY=your_key_here
```

### 4. Add your plugins

Edit `plugins.json` to list your installed REAPER plugins using their exact FX browser names:

```json
{
  "instruments": [
    "VST3i: Vital (Vital Audio)",
    "VSTi: 4Front Bass Module (4Front)",
    "VST3i: MT-PowerDrumKit (MANDA AUDIO) (16 out)"
  ],
  "effects": [
    "VST: ReaEQ (Cockos)",
    "VST: ReaComp (Cockos)",
    "VST: ReaLimit (Cockos)"
  ]
}
```

> To find exact plugin names: REAPER → Track FX → Add → search for the plugin → copy the full name shown.

### 5. Start the REAPER bridge

Open REAPER and run the bridge script from `reaper-bridge/`. This lets the AI communicate with REAPER.

### 6. Run the app

```bash
source venv/bin/activate
python app.py
```

The panel opens on the right side of your screen, on top of REAPER.

---

## Usage

### Compose a beat

```
make me a dark trap beat in C minor at 85 BPM
```

```
chill lofi hip hop in F major, nostalgic and rainy day vibes
```

```
cinematic orchestral piece in D minor, tense and suspenseful
```

### Transcribe audio to MIDI

Click the `+` button next to the input bar, select any audio file, and the app transcribes it into MIDI notes on a new REAPER track.

Or paste the file path directly into the input bar.

### Control REAPER directly

```
set the tempo to 120 BPM
add reverb to track 2
mute the bass track
save the project
```

---

## How the composition works

1. AI calls `set_tempo()` for the genre
2. Calls `get_chord_progression()` to generate voice-led chord voicings
3. For each layer (drums, bass, chords, melody):
   - Creates a track and loads the correct VST instrument
   - Calls the appropriate music theory tool to generate notes
   - Writes all notes to a MIDI item via `add_midi_notes_batch_beats()`
4. Adds EQ and compression to every track, limiting on the master

All notes come from the music theory engine which handles correct scale degrees, voice leading, humanized drum timing, and rhythmic variation.

---

## Audio to MIDI

The transcription pipeline runs Spotify's [basic-pitch](https://github.com/spotify/basic-pitch) via a python3.12 subprocess (required because basic-pitch needs numpy which is incompatible with Python 3.14). It uses the ONNX model for reliability, converts the detected note events from seconds to beats, and drops them onto a new REAPER track with an AI-selected instrument.

---

## Project structure

```
app.py              — main app, UI, AI loop, audio transcription
reaper_tools.py     — 30+ REAPER tool functions + HTTP/file bridge
music_theory.py     — chord progressions, bass lines, drum patterns, melodies
plugins.json        — your installed plugins
ui/index.html       — panel UI (pywebview)
reaper-bridge/      — REAPER-side bridge script
.env                — API keys (not committed)
```

---

## Built at a hackathon
