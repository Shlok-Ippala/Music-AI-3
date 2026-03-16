#!/usr/bin/env python3
"""
REAPER AI Assistant

Side panel overlay using Dear PyGui + OpenAI-compatible API to control REAPER DAW.
"""

import os
import sys
import json
import asyncio
import inspect
import re
import threading
import ctypes
from pathlib import Path

import subprocess
import shutil
import webview
import litellm

IS_MAC = sys.platform == "darwin"

import reaper_tools
import music_theory

# --- .env loader ---

def load_dotenv():
    """Load environment variables from .env file if present."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


# --- Tool schema generation ---

TYPE_MAP = {
    int: "integer",
    float: "number",
    str: "string",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _parse_arg_docs(docstring: str) -> dict:
    arg_docs = {}
    in_args = False
    current_param = None
    current_desc_lines = []

    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped == "Args:":
            in_args = True
            continue
        elif stripped in ("Returns:", "Raises:", "Note:", "Notes:", "Example:", "Examples:"):
            if current_param:
                arg_docs[current_param] = " ".join(current_desc_lines).strip()
            in_args = False
            continue
        if not in_args:
            continue
        match = re.match(r"^(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$", stripped)
        if match:
            if current_param:
                arg_docs[current_param] = " ".join(current_desc_lines).strip()
            current_param = match.group(1)
            current_desc_lines = [match.group(2)] if match.group(2) else []
        elif current_param and stripped:
            current_desc_lines.append(stripped)

    if current_param:
        arg_docs[current_param] = " ".join(current_desc_lines).strip()
    return arg_docs


def _get_json_type(annotation):
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        args = getattr(annotation, "__args__", None)
        if args:
            return {"type": "array", "items": {"type": TYPE_MAP.get(args[0], "string")}}
        return {"type": "array", "items": {}}
    if annotation is list:
        return {"type": "array", "items": {}}
    return {"type": TYPE_MAP.get(annotation, "string")}


ALLOWED_TOOLS = {
    # Track management
    "get_track_count", "get_all_tracks", "insert_track", "delete_track",
    "set_track_name", "set_track_volume", "set_track_pan",
    "set_track_mute", "set_track_solo", "set_track_color",
    # FX
    "track_fx_add_by_name", "track_fx_delete", "track_fx_get_list",
    "track_fx_set_param", "track_fx_get_param", "track_fx_get_num_params",
    "track_fx_get_param_name",
    # MIDI (beat-based only)
    "create_midi_item_beats", "add_midi_notes_batch_beats",
    "get_midi_notes", "clear_midi_item", "delete_midi_note",
    # Audio items
    "insert_audio_file", "get_track_items", "get_item_info",
    "set_item_position", "set_item_length", "delete_item",
    # Transport & project
    "play", "stop", "pause", "set_tempo", "get_tempo",
    "set_time_signature", "save_project", "get_project_summary",
    # Routing
    "create_send", "create_bus",
    # Markers
    "add_marker", "add_region", "get_markers",
    # Mixing helpers
    "add_eq", "add_compressor", "add_limiter",
    # Undo
    "undo", "redo",
    # Music theory helpers
    "get_chord_progression", "get_bass_line", "get_drum_pattern", "get_melody",
}


def _schema_for_func(name: str, func) -> dict:
    """Build an OpenAI function-calling schema from a Python function."""
    sig = inspect.signature(func)
    doc = inspect.getdoc(func) or ""
    description = re.split(r"\n\s*(Args|Returns|Raises|Note|Example):", doc)[0].strip()
    arg_docs = _parse_arg_docs(doc)
    properties = {}
    required = []
    for param_name, param in sig.parameters.items():
        prop = _get_json_type(param.annotation)
        if param_name in arg_docs:
            prop["description"] = arg_docs[param_name]
        properties[param_name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def generate_tool_schemas() -> list:
    schemas = []
    # REAPER tools
    for name, func in reaper_tools.TOOLS.items():
        if name not in ALLOWED_TOOLS:
            continue
        schemas.append(_schema_for_func(name, func))
    # Music theory tools
    for name, func in music_theory.MUSIC_TOOLS.items():
        if name not in ALLOWED_TOOLS:
            continue
        schemas.append(_schema_for_func(name, func))
    return schemas


# --- Tool execution ---

def _instrument_for_track_name(track_name: str) -> str | None:
    """Given a track name, return the correct plugin to auto-load."""
    name = track_name.lower()
    plugins = load_plugins().get("instruments", [])
    if not plugins:
        return None

    RULES = [
        (["drum", "kick", "snare", "hat", "perc", "beat"], ["MT-PowerDrumKit", "Drum"]),
        (["bass"],                                          ["4Front Bass", "Ample Bass", "Bass"]),
        (["piano", "keys", "keyboard", "grand", "upright"],["Upright Piano", "Piano"]),
        (["chord", "pad", "harmony", "Rhodes"],            ["Upright Piano", "Piano", "Surge XT"]),
        (["melody", "lead", "synth", "arp"],               ["Upright Piano", "Piano", "Surge XT"]),
    ]

    for keywords, priorities in RULES:
        if any(k in name for k in keywords):
            for p in priorities:
                for plugin in plugins:
                    if p.lower() in plugin.lower():
                        return plugin

    return None


def _resolve_plugin_name(fx_name: str) -> str:
    """Fuzzy-match fx_name against plugins.json and return the exact name."""
    plugins = load_plugins()
    all_plugins = plugins.get("instruments", []) + plugins.get("effects", [])
    if not all_plugins:
        return fx_name
    # Exact match — already correct
    if fx_name in all_plugins:
        return fx_name
    # Case-insensitive exact match
    fx_lower = fx_name.lower()
    for p in all_plugins:
        if p.lower() == fx_lower:
            return p
    # Substring match — AI passed a shortened name
    for p in all_plugins:
        if fx_lower in p.lower() or p.lower() in fx_lower:
            return p
    # Keyword match — score by number of words in common
    fx_words = set(fx_lower.replace("(", " ").replace(")", " ").split())
    best, best_score = fx_name, 0
    for p in all_plugins:
        p_words = set(p.lower().replace("(", " ").replace(")", " ").split())
        score = len(fx_words & p_words)
        if score > best_score:
            best, best_score = p, score
    if best_score > 0:
        return best
    return fx_name


async def execute_tool(tool_name: str, tool_input: dict) -> dict:
    func = reaper_tools.TOOLS.get(tool_name) or music_theory.MUSIC_TOOLS.get(tool_name)
    if func is None:
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}
    try:
        # Auto-correct plugin names before sending to REAPER
        if tool_name == "track_fx_add_by_name" and "fx_name" in tool_input:
            resolved = _resolve_plugin_name(tool_input["fx_name"])
            if resolved != tool_input["fx_name"]:
                print(f"[Plugin] Resolved '{tool_input['fx_name']}' → '{resolved}'")
            tool_input = {**tool_input, "fx_name": resolved}

        # Music theory tools are sync; REAPER tools are async
        if inspect.iscoroutinefunction(func):
            result = await func(**tool_input)
        else:
            result = func(**tool_input)

        # After inserting a track, auto-load the correct instrument based on track name
        if tool_name == "insert_track" and result.get("ok"):
            track_name = tool_input.get("name", "")
            track_idx  = result.get("index", 0)
            plugin = _instrument_for_track_name(track_name)
            if plugin:
                print(f"[AutoInstrument] '{track_name}' → '{plugin}'")
                fx_result = await reaper_tools.TOOLS["track_fx_add_by_name"](
                    track_index=track_idx, fx_name=plugin
                )
                fx_index = fx_result.get("fx_index", fx_result.get("index", 0))
                await reaper_tools.reaper_call("TrackFX_SetEnabled", track_idx, fx_index, True)

        # After adding an FX manually, force-enable it too
        if tool_name == "track_fx_add_by_name" and result.get("ok"):
            fx_index = result.get("fx_index", result.get("index", -1))
            track_index = tool_input.get("track_index", 0)
            if fx_index is not None and fx_index >= 0:
                await reaper_tools.reaper_call("TrackFX_SetEnabled", track_index, fx_index, True)

        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- System prompt ---

def load_plugins():
    """Load user's available plugins from plugins.json."""
    plugins_path = Path(__file__).parent / "plugins.json"
    if plugins_path.exists():
        try:
            data = json.loads(plugins_path.read_text())
            return {
                "instruments": data.get("instruments", []),
                "effects": data.get("effects", []),
            }
        except Exception:
            pass
    return {"instruments": [], "effects": []}


def build_system_prompt():
    """Build system prompt with available plugins injected."""
    plugins = load_plugins()
    plugin_section = ""
    if plugins["instruments"] or plugins["effects"]:
        plugin_section = "\n## AVAILABLE PLUGINS (use these exact names with track_fx_add_by_name)\n"
        if plugins["instruments"]:
            plugin_section += "Instruments:\n" + "\n".join(f"- {p}" for p in plugins["instruments"]) + "\n"
        if plugins["effects"]:
            plugin_section += "Effects:\n" + "\n".join(f"- {p}" for p in plugins["effects"]) + "\n"
        plugin_section += "\nALWAYS use plugins from this list. Use the EXACT name shown above. Do NOT guess plugin names.\n"

    return BASE_SYSTEM_PROMPT.replace("{PLUGINS}", plugin_section)


BASE_SYSTEM_PROMPT = """You are a REAPER DAW music production assistant. You help producers build professional, layered tracks.

The current project state is provided at the start of each message. ALWAYS work incrementally - add to the existing project.
{PLUGINS}
## CRITICAL RULES
1. For ORIGINAL compositions, ALWAYS use get_chord_progression, get_bass_line, get_drum_pattern, and get_melody to generate notes — the music theory tools handle correct notes, voicings, and humanization.
2. For SPECIFIC/KNOWN songs (e.g. "play Happy Birthday", "play Twinkle Twinkle Little Star"), you MUST write the MIDI notes yourself using your knowledge of the melody. Use the correct tempo, time signature, and key for that song. Use the MIDI REFERENCE section for pitch numbers. Pass notes directly to add_midi_notes_batch_beats.
3. To add MIDI notes, you MUST use add_midi_notes_batch_beats. Pass the notes array directly.
4. To create MIDI items, use create_midi_item_beats.
5. NEVER use add_midi_note (singular). It does not exist in your tools.
6. NEVER use duplicate_item to repeat patterns. Instead, generate all notes for all bars directly.
7. For instruments, use track_fx_add_by_name with the EXACT plugin name from the available plugins list.
8. When adding new tracks, ALWAYS append to the end by calling insert_track() with NO index argument (or index=current track count). NEVER insert at a middle index unless the user explicitly asks to insert between specific tracks (e.g. "insert between track 2 and 3"). Inserting at a middle index shifts all subsequent track indices and will break references to existing tracks.
9. NEVER modify, overwrite, or delete existing tracks unless the user explicitly asks to edit a specific track (e.g. "edit track 2", "change the bass track").

## COMPOSING WORKFLOW (follow this order)
1. **set_tempo(bpm)** — choose appropriate tempo for genre
2. **get_chord_progression(genre, key, bars)** — get chord voicings. Save the returned notes for the chords/pads track.
3. **For each layer, repeat:**
   a. insert_track(name) — create the track (always appends to end)
   b. track_fx_add_by_name(track_index, fx_name) — use the EXACT name from INSTRUMENT ASSIGNMENT table:
      - Drums track → fx_name="VST3i: MT-PowerDrumKit (MANDA AUDIO) (16 out)"
      - Bass track → fx_name="VSTi: 4Front Bass Module (4Front)"
      - Chords/Pads track → fx_name="VST3i: Upright Piano (99Sounds) (32 out)"
      - Melody/Lead track → fx_name="VST3i: Upright Piano (99Sounds) (32 out)"
      - Piano track → fx_name="VST3i: Upright Piano (99Sounds) (32 out)"
   b2. Configure synth parameters ONLY for ReaSynth (see SYNTH CONFIGURATION). Skip for Vital, 4Front Bass, MT-PowerDrumKit, Upright Piano.
   c. create_midi_item_beats(track_index, position_beats=0, length_beats=TOTAL_BEATS, tempo=BPM)
   d. Get notes from the appropriate music theory tool:
      - **Drums**: get_drum_pattern(genre, bars) — humanized drums with fills
      - **Bass**: get_bass_line(key, genre, bars, progression=PROGRESSION_STRING) — pass the progression string from step 2 so bass follows the same chords
      - **Chords/Pads**: use the notes from get_chord_progression (step 2)
      - **Melody/Piano**: get_melody(key, genre, bars, density, progression=PROGRESSION_STRING) — pass the progression string from step 2
   e. add_midi_notes_batch_beats(track_index, item_index=0, tempo=BPM, notes=NOTES_FROM_TOOL)
4. **FX chain**: Add ReaEQ + ReaComp on every track AFTER the instrument is already added. Add ReaLimit on the master. Order MUST be: instrument first, then EQ, then Comp.

Note format: each note is {pitch, start_beat, length_beats, velocity, channel}. Drums use channel 9.

## SYNTH CONFIGURATION
Only configure parameters for **ReaSynth**. NEVER call track_fx_set_param on Vital, 4Front Bass Module, MT-PowerDrumKit, or Upright Piano — they load with good defaults and setting random params will silence them.

**ReaSynth only** (fx_index=0):
- Param 0 (Attack): 0.01-0.05 for plucks, 0.3-0.5 for pads
- Param 1 (Decay): 0.3-0.6
- Param 2 (Sustain): 0.5-0.8 for sustained sounds, 0.2-0.4 for plucks
- Param 3 (Release): 0.2-0.5
- Param 4 (Waveform): 0.0=sine, 0.25=triangle, 0.5=square, 0.75=sawtooth
- Param 5 (Filter/Cutoff): 0.3-0.6

## INSTRUMENT ASSIGNMENT (MANDATORY — no exceptions)
You MUST use exactly these plugins for each track type. Do NOT substitute or invent names.

| Track type | Plugin name to pass to track_fx_add_by_name |
|---|---|
| Drums | VST3i: MT-PowerDrumKit (MANDA AUDIO) (16 out) |
| Bass | VSTi: 4Front Bass Module (4Front) |
| Chords / Pads | VST3i: Upright Piano (99Sounds) (32 out) |
| Melody / Lead | VST3i: Upright Piano (99Sounds) (32 out) |
| Piano | VST3i: Upright Piano (99Sounds) (32 out) |
| Any other synth | VSTi: ReaSynth (Cockos) |

NEVER use a plugin name not in this table. NEVER guess or shorten a plugin name.

## PRODUCTION GUIDELINES
- **Drums**: ALWAYS use `VST3i: MT-PowerDrumKit (MANDA AUDIO) (16 out)`. The drum pattern tool provides kick, snare, hi-hats with ghost notes and fills.
- **Bass**: ALWAYS use `VSTi: 4Front Bass Module (4Front)`. Instrument in octave 2-3 range. The bass tool follows chord roots with rhythmic variation.
- **Chords/Pads**: ALWAYS use `VST3i: Upright Piano (99Sounds) (32 out)`. Chord progression tool provides voice-led voicings with strummed feel.
- **Melody/Lead**: ALWAYS use `VST3i: Upright Piano (99Sounds) (32 out)`. The melody tool uses chord tones on strong beats and scale tones on weak beats. Use density="sparse" for ambient, "medium" for standard, "dense" for busy.
- **Piano**: ALWAYS use `VST3i: Upright Piano (99Sounds) (32 out)`.
- **Timing**: Convert "seconds" requests to bars (e.g. 30 sec at 120 BPM = 16 bars = 64 beats).
- Build at least 4 layers (drums, bass, chords, melody) unless the user asks for something specific.

## GENRE DEFAULTS
- **Hip-hop/Trap**: 70-90 BPM, key of minor (Cm, Am, Dm), dense hi-hats
- **Lo-fi**: 75-85 BPM, key of major (C, F, Eb), sparse melody
- **Pop**: 100-120 BPM, key of major (C, G, D), medium density melody
- **R&B**: 85-95 BPM, key of major (Eb, Ab, Bb), sparse melody
- **Rock**: 110-140 BPM, key of major or minor (E, A, G), dense melody

## TONE & MOOD → KEY SELECTION
Genre defaults above give a starting key range. When the user also describes a mood or emotion, **use that to pick the specific key within or outside that range**. Mood overrides the default if there is a clear mismatch (e.g. a genre that defaults to major but the user asks for something dark → use minor).

Mood → key guidance:
- **Dark / menacing / evil / aggressive**: Low minor keys — Cm, F#m, Em
- **Spooky / eerie / haunted / mysterious**: Minor keys — Dm, Bm, Em (avoid major)
- **Sad / melancholic / emotional**: Minor keys — Am, Fm, Cm
- **Tense / suspenseful / cinematic**: Minor with tension — Dm, Em, Bm
- **Romantic / sensual / smooth**: Warm major — Eb, Ab, Bb major
- **Happy / uplifting / bright**: Bright major — C, G, D major
- **Dreamy / floaty / chill**: Soft major — F, Bb, Eb major
- **Energetic / hype**: Driving minor — Fm, Cm, Gm
- **Spiritual / soulful**: Eb major, Ab major, F minor

When mood and genre both inform the key, choose the specific key that satisfies both — don't just pick the genre default and ignore the mood.

## MIDI REFERENCE (for manual adjustments only)
Notes: C4=60, D4=62, E4=64, F4=65, G4=67, A4=69, B4=71, C5=72. Octave=+12.
Drums (GM/channel 9): Kick=36, Snare=38, Closed HH=42, Open HH=46, Crash=49, Ride=51
Beats: whole=4, half=2, quarter=1, eighth=0.5, sixteenth=0.25

Be concise. Describe what you built briefly — list the progression, layers, key, and tempo."""


# --- Agentic loop (litellm) ---

MODEL = os.getenv("AI_MODEL", "gemini/gemini-2.5-flash")

TOOL_DESCRIPTIONS = {
    "set_tempo": "Setting tempo...",
    "insert_track": "Creating new track...",
    "track_fx_add_by_name": "Loading instrument plugin...",
    "track_fx_set_param": "Configuring synth parameters...",
    "create_midi_item_beats": "Creating MIDI clip...",
    "add_midi_notes_batch_beats": "Adding notes...",
    "get_chord_progression": "Generating chord progression...",
    "get_bass_line": "Generating bass line...",
    "get_drum_pattern": "Generating drum pattern...",
    "get_melody": "Generating melody...",
    "add_eq": "Adding EQ...",
    "add_compressor": "Adding compressor...",
    "add_limiter": "Adding limiter...",
    "play": "Starting playback...",
    "save_project": "Saving project...",
}


async def run_agentic_loop(messages, tool_schemas, status_callback=None, chat_callback=None, should_stop=None):
    """Run the litellm tool-calling loop. Modifies messages list in-place."""
    response = await litellm.acompletion(
        model=MODEL,
        messages=messages,
        tools=tool_schemas,
    )

    msg = response.choices[0].message
    messages.append(msg)

    while msg.tool_calls:
        if should_stop and should_stop():
            raise asyncio.CancelledError("Stopped by user")
        if status_callback:
            status_callback(f"Executing {len(msg.tool_calls)} tool(s)...")

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            if status_callback:
                status_callback(f"-> {name}")
            friendly = TOOL_DESCRIPTIONS.get(name, f"Running {name}...")
            if chat_callback:
                chat_callback(friendly)
            print(f"[Tool] {name}({json.dumps(args)[:200]})")

            result = await execute_tool(name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

        response = await litellm.acompletion(
            model=MODEL,
            messages=messages,
            tools=tool_schemas,
        )
        msg = response.choices[0].message
        messages.append(msg)

    return msg.content or ""


# --- pywebview UI ---

def get_screen_size():
    """Get screen dimensions, cross-platform."""
    if hasattr(ctypes, 'windll'):
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    try:
        import subprocess
        out = subprocess.check_output(["python3", "-c",
            "import tkinter; r=tkinter.Tk(); print(r.winfo_screenwidth(), r.winfo_screenheight()); r.destroy()"],
            stderr=subprocess.DEVNULL)
        w, h = out.decode().strip().split()
        return int(w), int(h)
    except Exception:
        return 1920, 1080


class JsAPI:
    """Exposes Python methods to JS via pywebview."""
    def __init__(self, app):
        self._app = app

    def send_message(self, text):
        if self._app.is_processing:
            return
        # Check if it's an MP3 path
        path = text.strip()
        if path.lower().endswith(('.mp3', '.wav', '.flac', '.m4a', '.ogg')) and Path(path).is_file():
            self._app.is_processing = True
            self._app.stop_requested = False
            asyncio.run_coroutine_threadsafe(
                self._app._process_audio(path), self._app.async_loop
            )
        else:
            self._app.is_processing = True
            self._app.stop_requested = False
            asyncio.run_coroutine_threadsafe(
                self._app._process_message(text), self._app.async_loop
            )

    def open_file(self):
        result = self._app.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=('Audio Files (*.mp3;*.wav;*.flac;*.m4a;*.ogg)', 'All files (*.*)')
        )
        if result and len(result) > 0:
            return result[0]
        return None

    def stop(self):
        self._app.on_stop()

    def clear(self):
        self._app.clear_chat()


TRANSCRIBE_SCRIPT = """
import sys, json, pathlib, os
_real_stdout = sys.stdout
sys.stdout = sys.stderr

import numpy as np
import librosa

import scipy.signal
if not hasattr(scipy.signal, 'gaussian'):
    import scipy.signal.windows
    scipy.signal.gaussian = scipy.signal.windows.gaussian

file_path = sys.argv[1]

# ── Load audio ───────────────────────────────────────────────
y, sr = librosa.load(file_path, mono=True)

# ── Tempo detection ──────────────────────────────────────────
tempo_arr, _ = librosa.beat.beat_track(y=y, sr=sr, units='time')
detected_tempo = float(tempo_arr[0]) if hasattr(tempo_arr, '__len__') else float(tempo_arr)
if not (40 <= detected_tempo <= 240):
    detected_tempo = 120.0

# ── Key detection ─────────────────────────────────────────────
chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
note_names = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
detected_key = note_names[int(np.argmax(chroma.mean(axis=1)))]

# ── Piano detection ──────────────────────────────────────────
# Piano characteristics: sharp onsets, tonal (low flatness),
# stable pitch per note (no vibrato), moderate ZCR
onset_env    = librosa.onset.onset_strength(y=y, sr=sr)
mean_onset   = float(np.mean(onset_env))
flatness     = float(np.mean(librosa.feature.spectral_flatness(y=y)))
zcr          = float(np.mean(librosa.feature.zero_crossing_rate(y=y)))
harmonics, _ = librosa.effects.hpss(y)
harmonic_ratio = float(np.mean(np.abs(harmonics)) / (np.mean(np.abs(y)) + 1e-8))

# Score: high onset strength, low flatness, high harmonic content → piano
piano_score = 0
if mean_onset > 2.0:   piano_score += 1   # sharp percussive attacks
if flatness < 0.05:    piano_score += 2   # very tonal
if harmonic_ratio > 0.6: piano_score += 2 # highly harmonic
if zcr < 0.08:         piano_score += 1   # smooth waveform

# Also check filename for hints
fname_lower = pathlib.Path(file_path).stem.lower()
if any(w in fname_lower for w in ('piano','pno','keys','keyboard','grand','upright')):
    piano_score += 3

is_piano = piano_score >= 4

# ── Quantize helper ──────────────────────────────────────────
def quantize(sec, tempo, grid=0.25):
    beat = sec * tempo / 60.0
    return round(round(beat / grid) * grid, 4)

MIN_DURATION  = 0.05
MIN_AMPLITUDE = 0.12

events = []

if is_piano:
    # ── Piano-specific model (bytedance) ─────────────────────
    try:
        from piano_transcription_inference import PianoTranscription, load_audio, sample_rate
        transcriptor = PianoTranscription(device='cpu', checkpoint_path=None)
        audio, _ = load_audio(file_path, sr=sample_rate, mono=True)
        transcribed = transcriptor.transcribe(audio, None)
        for note in transcribed.get('note_event_list', []):
            start_s = float(note['onset_time'])
            end_s   = float(note.get('offset_time', start_s + 0.5))
            pitch   = int(note['midi_note'])
            vel     = int(note.get('velocity', 80))
            amp     = vel / 127.0
            if (end_s - start_s) < MIN_DURATION: continue
            start_b = quantize(start_s, detected_tempo)
            dur_b   = quantize(end_s, detected_tempo) - start_b
            if dur_b <= 0: dur_b = 0.25
            events.append([start_s, end_s, pitch, amp, start_b, dur_b])
        model_used = 'piano-transcription'
    except Exception as e:
        is_piano = False  # fall through to basic-pitch
        print(f"piano-transcription failed ({e}), falling back to basic-pitch", file=sys.stderr)

if not is_piano:
    # ── General model (basic-pitch) ───────────────────────────
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH
    onnx_path = pathlib.Path(ICASSP_2022_MODEL_PATH).parent / "nmp.onnx"
    model_path = str(onnx_path) if onnx_path.exists() else ICASSP_2022_MODEL_PATH
    _, _, note_events = predict(file_path, model_path,
        onset_threshold=0.6, frame_threshold=0.4,
        minimum_note_length=100,
        minimum_frequency=librosa.midi_to_hz(36),
        maximum_frequency=librosa.midi_to_hz(96),
        multiple_pitch_bends=False)
    for e in note_events:
        start_s, end_s, pitch, amp = float(e[0]), float(e[1]), int(e[2]), float(e[3])
        if (end_s - start_s) < MIN_DURATION: continue
        if amp < MIN_AMPLITUDE: continue
        start_b = quantize(start_s, detected_tempo)
        dur_b   = quantize(end_s, detected_tempo) - start_b
        if dur_b <= 0: dur_b = 0.25
        events.append([start_s, end_s, pitch, amp, start_b, dur_b])
    model_used = 'basic-pitch'

# ── Instrument type detection ────────────────────────────────
# Use audio features + note stats to classify what instrument this is

pitches = [int(e[2]) for e in events]
avg_pitch = float(np.mean(pitches)) if pitches else 60.0

# Polyphony: count simultaneous notes (overlapping time windows)
poly_count = 0
if len(events) > 1:
    for i, e in enumerate(events[:200]):
        overlaps = sum(1 for f in events[:200] if f is not e
                       and f[0] < e[1] and f[1] > e[0])
        poly_count = max(poly_count, overlaps)

# Spectral centroid — low = bass, high = treble
spec_centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))

# Percussive ratio (for drums)
_, percussive = librosa.effects.hpss(y)
perc_ratio = float(np.mean(np.abs(percussive)) / (np.mean(np.abs(y)) + 1e-8))

# Classify
fname_lower = pathlib.Path(file_path).stem.lower()

if perc_ratio > 0.6 or any(w in fname_lower for w in ('drum','beat','kit','perc')):
    instrument_type = 'drums'
elif is_piano:
    instrument_type = 'piano'
elif avg_pitch < 48 and spec_centroid < 600 and poly_count <= 1:
    instrument_type = 'bass'
elif avg_pitch < 55 and poly_count <= 2:
    instrument_type = 'bass'
elif poly_count >= 3 or any(w in fname_lower for w in ('chord','pad','keys','piano')):
    instrument_type = 'piano'
else:
    instrument_type = 'melody'

sys.stdout = _real_stdout
print(json.dumps({
    "tempo": detected_tempo,
    "key": detected_key,
    "is_piano": is_piano,
    "model": model_used,
    "instrument_type": instrument_type,
    "avg_pitch": avg_pitch,
    "events": events,
}))
"""


class ReaperAIApp:
    def __init__(self):
        self.messages = []
        self.tool_schemas = None
        self.async_loop = None
        self.is_processing = False
        self.stop_requested = False
        self.window = None
        self.connected_msg = None

    def _js(self, call: str):
        if self.window:
            self.window.evaluate_js(call)

    def add_message(self, role: str, text: str):
        self._js(f'addMessage({json.dumps(role)}, {json.dumps(text)})')

    def set_status(self, text: str):
        self._js(f'setStatus({json.dumps(text)})')

    def _transcribe_audio(self, file_path: str) -> dict:
        """Run basic-pitch via python3.12 subprocess. Returns {tempo, key, events}."""
        py312 = shutil.which("python3.12")
        if not py312:
            raise RuntimeError("python3.12 not found — install it to use audio transcription")
        result = subprocess.run(
            [py312, "-c", TRANSCRIBE_SCRIPT, file_path],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            raise RuntimeError(f"basic-pitch failed: {result.stderr[-600:]}")
        stdout = result.stdout.strip()
        if not stdout:
            raise RuntimeError("basic-pitch returned no output")
        return json.loads(stdout)

    def _pick_instrument(self, instrument_type: str) -> str | None:
        """Deterministically map instrument type → best available plugin."""
        plugins = load_plugins().get("instruments", [])
        if not plugins:
            return None

        # Priority lists per type — first match wins
        PRIORITY = {
            "drums":  ["MT-PowerDrumKit", "DrumKit", "Drum"],
            "bass":   ["4Front Bass", "Bass"],
            "piano":  ["Upright Piano", "Piano", "Vital", "ReaSynth"],
            "melody": ["Vital", "ReaSynth", "Splice"],
        }

        candidates = PRIORITY.get(instrument_type, PRIORITY["melody"])
        for keyword in candidates:
            for plugin in plugins:
                if keyword.lower() in plugin.lower():
                    return plugin

        # Fallback: first non-drum plugin
        for plugin in plugins:
            if "drum" not in plugin.lower() and "percussion" not in plugin.lower():
                return plugin

        return plugins[0]

    async def _process_audio(self, file_path: str):
        """Transcribe an audio file to MIDI and load it into REAPER."""
        try:
            self._js('setTranscribing(true)')
            self.set_status("Analysing audio...")

            result = await asyncio.get_event_loop().run_in_executor(
                None, self._transcribe_audio, file_path
            )
            raw_events = result.get("events", [])
            detected_tempo = result.get("tempo", 120.0)
            detected_key   = result.get("key", "C")
            model_used      = result.get("model", "basic-pitch")
            is_piano        = result.get("is_piano", False)
            instrument_type = result.get("instrument_type", "melody")

            if not raw_events:
                self.add_message('error', "No notes detected in audio file.")
                return

            # Use detected tempo — set it in REAPER too
            self.set_status(f"Detected {detected_tempo:.0f} BPM, key of {detected_key}...")
            await reaper_tools.TOOLS["set_tempo"](bpm=detected_tempo)

            # Build notes from pre-quantized beats in the script output
            # e = [start_s, end_s, pitch, amp, start_beat, dur_beat]
            notes = [
                {
                    "pitch": int(e[2]),
                    "start_beat": float(e[4]),
                    "length_beats": max(float(e[5]), 0.125),
                    "velocity": min(127, max(40, int(float(e[3]) * 127))),
                    "channel": 0,
                }
                for e in raw_events
            ]

            total_beats = max(n["start_beat"] + n["length_beats"] for n in notes) + 2

            self.set_status("Picking instrument...")
            instrument = self._pick_instrument(instrument_type)

            self.set_status("Building REAPER track...")
            track_name = Path(file_path).stem
            track_result = await reaper_tools.TOOLS["insert_track"](name=track_name)
            track_idx = track_result.get("index", 0)

            if instrument:
                await reaper_tools.TOOLS["track_fx_add_by_name"](
                    track_index=track_idx, fx_name=instrument
                )

            await reaper_tools.TOOLS["create_midi_item_beats"](
                track_index=track_idx, position_beats=0,
                length_beats=total_beats, tempo=detected_tempo
            )

            await reaper_tools.TOOLS["add_midi_notes_batch_beats"](
                track_index=track_idx, item_index=0,
                tempo=detected_tempo, notes=notes
            )

            summary = (
                f"Transcribed \"{track_name}\"\n"
                f"{len(notes)} notes · {detected_tempo:.0f} BPM · Key of {detected_key}\n"
                f"Model: {model_used} · Detected: {instrument_type}\n"
                f"Instrument: {instrument or '(none)'}"
            )
            self.add_message('ai', summary)
            self.set_status("Ready")

        except Exception as e:
            self.add_message('error', f"Transcription failed: {e}")
            self.set_status("Error")
            print(f"[Audio Error] {e}")
        finally:
            self.is_processing = False
            self._js('setTranscribing(false)')
            self._js('setProcessing(false)')

    async def _process_message(self, user_input: str):
        try:
            self.set_status("Reading project state...")
            try:
                project_state = await reaper_tools.TOOLS["get_project_summary"]()
                augmented = f"[Current REAPER project state: {json.dumps(project_state)}]\n\n{user_input}"
            except Exception:
                augmented = user_input

            self.messages.append({"role": "user", "content": augmented})

            response = await run_agentic_loop(
                self.messages, self.tool_schemas,
                status_callback=self.set_status,
                chat_callback=lambda msg: self.add_message('tool', msg),
                should_stop=lambda: self.stop_requested,
            )

            self.add_message('ai', response)
            self.set_status("Ready")

        except asyncio.CancelledError:
            self.add_message('status', 'Stopped.')
            self.set_status("Ready")

        except Exception as e:
            self.add_message('error', f'Error: {e}')
            self.set_status("Error")
            print(f"[Error] {e}")

        finally:
            self.is_processing = False
            self._js('setProcessing(false)')

    def clear_chat(self):
        if self.is_processing:
            return
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        self._js('clearMessages()')
        self.set_status("Chat cleared")

    def on_stop(self):
        if self.is_processing:
            self.stop_requested = True
            self.set_status("Stopping...")

    async def initialize_backend(self):
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not gemini_key:
            self.add_message('error', 'GEMINI_API_KEY not set in .env file.')
            return False
        os.environ.setdefault("GEMINI_API_KEY", gemini_key)

        self.set_status("Generating tool schemas...")
        self.tool_schemas = generate_tool_schemas()
        self.messages = [{"role": "system", "content": build_system_prompt()}]

        model_short = MODEL.split("/")[-1]
        self._js(f'setModelLabel({json.dumps(model_short)})')
        self.add_message('status', f'{len(self.tool_schemas)} tools loaded')
        self.set_status("Ready")
        self._js('setProcessing(false)')
        return True

    def on_window_loaded(self):
        self.set_status("Initializing...")
        asyncio.run_coroutine_threadsafe(
            self.initialize_backend(), self.async_loop
        )

    def run(self):
        load_dotenv()
        screen_w, screen_h = get_screen_size()

        def run_async_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self.async_loop = asyncio.new_event_loop()
        threading.Thread(target=run_async_loop, args=(self.async_loop,), daemon=True).start()

        html_path = Path(__file__).parent / "ui" / "index.html"
        y_pos = 25 if IS_MAC else 0
        h_offset = 100 if IS_MAC else 40

        self.window = webview.create_window(
            'REAPER AI',
            url=str(html_path),
            js_api=JsAPI(self),
            width=420,
            height=screen_h - h_offset,
            on_top=True,
            resizable=True,
        )
        self.window.events.loaded += self.on_window_loaded
        webview.start()


if __name__ == "__main__":
    app = ReaperAIApp()
    app.run()
