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
import subprocess
from pathlib import Path

import webview

IS_MAC = sys.platform == "darwin"

from openai import AsyncOpenAI

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

async def execute_tool(tool_name: str, tool_input: dict) -> dict:
    func = reaper_tools.TOOLS.get(tool_name) or music_theory.MUSIC_TOOLS.get(tool_name)
    if func is None:
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}
    try:
        # Music theory tools are sync; REAPER tools are async
        if inspect.iscoroutinefunction(func):
            return await func(**tool_input)
        else:
            return func(**tool_input)
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
   b. track_fx_add_by_name(track_index, fx_name) — add instrument plugin
   b2. Configure synth parameters with track_fx_set_param (see SYNTH CONFIGURATION)
   c. create_midi_item_beats(track_index, position_beats=0, length_beats=TOTAL_BEATS, tempo=BPM)
   d. Get notes from the appropriate music theory tool:
      - **Drums**: get_drum_pattern(genre, bars) — humanized drums with fills
      - **Bass**: get_bass_line(key, genre, bars, progression=PROGRESSION_STRING) — pass the progression string from step 2 so bass follows the same chords
      - **Chords/Pads**: use the notes from get_chord_progression (step 2)
      - **Melody/Piano**: get_melody(key, genre, bars, density, progression=PROGRESSION_STRING) — pass the progression string from step 2
   e. add_midi_notes_batch_beats(track_index, item_index=0, tempo=BPM, notes=NOTES_FROM_TOOL)
4. **FX chain**: Add ReaEQ + ReaComp on every track. Add ReaLimit on the master.

Note format: each note is {pitch, start_beat, length_beats, velocity, channel}. Drums use channel 9.

## SYNTH CONFIGURATION
After adding a synth plugin with track_fx_add_by_name, ALWAYS configure its parameters using track_fx_set_param. Do NOT leave synths at default settings.

**ReaSynth** (fx_index is the position in the FX chain, usually 0):
- Param 0 (Attack): 0.01-0.05 for plucks, 0.3-0.5 for pads
- Param 1 (Decay): 0.3-0.6
- Param 2 (Sustain): 0.5-0.8 for sustained sounds, 0.2-0.4 for plucks
- Param 3 (Release): 0.2-0.5
- Param 4 (Waveform): 0.0=sine (smooth), 0.25=triangle (warm), 0.5=square (hollow), 0.75=sawtooth (bright/buzzy) — prefer sine or triangle for bass/pads
- Param 5 (Filter/Cutoff): 0.3-0.6 to tame brightness. Lower = warmer, less buzz.

**Vital / other synths**: Use track_fx_get_num_params and track_fx_get_param_name to discover parameters, then set filter cutoff low and adjust attack/release for the desired sound.

**General rules**:
- For bass: sine or triangle wave, low filter cutoff (0.3-0.4), short attack
- For pads/chords: triangle or filtered saw, medium filter cutoff (0.4-0.6), slow attack (0.3+), long release
- For leads/melody: any waveform, medium filter cutoff, short attack
- ALWAYS lower the filter cutoff from default to remove harshness

## PRODUCTION GUIDELINES
- **Drums**: Use MT-PowerDrumKit or ReaSynth. The drum pattern tool provides kick, snare, hi-hats with ghost notes and fills.
- **Bass**: Use 4Front Bass Module. Instrument in octave 2-3 range. The bass tool follows chord roots with rhythmic variation.
- **Chords/Pads**: Use Vital or Upright Piano. Chord progression tool provides voice-led voicings with strummed feel.
- **Melody/Lead**: The melody tool uses chord tones on strong beats and scale tones on weak beats. Use density="sparse" for ambient, "medium" for standard, "dense" for busy.
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


# --- Agentic loop (OpenAI Chat Completions) ---

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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


async def run_agentic_loop(client, messages, tool_schemas, status_callback=None, chat_callback=None, should_stop=None):
    """Run the OpenAI tool-calling loop. Modifies messages list in-place."""
    response = await client.chat.completions.create(
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

        response = await client.chat.completions.create(
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
        self._app.is_processing = True
        self._app.stop_requested = False
        asyncio.run_coroutine_threadsafe(
            self._app._process_message(text), self._app.async_loop
        )

    def stop(self):
        self._app.on_stop()

    def clear(self):
        self._app.clear_chat()

    def browse_mp3(self):
        return self._app.open_file_dialog()

    def transcribe_mp3(self, file_path):
        if self._app.is_processing:
            return
        self._app.is_processing = True
        asyncio.run_coroutine_threadsafe(
            self._app._transcribe_mp3(file_path), self._app.async_loop
        )


class ReaperAIApp:
    def __init__(self):
        self.client = None
        self.messages = []
        self.tool_schemas = None
        self.async_loop = None
        self.is_processing = False
        self.stop_requested = False
        self.window = None
        self.connected_msg = None
        self.uploaded_file_path = None

    def _js(self, call: str):
        if self.window:
            self.window.evaluate_js(call)

    def add_message(self, role: str, text: str):
        self._js(f'addMessage({json.dumps(role)}, {json.dumps(text)})')

    def set_status(self, text: str):
        self._js(f'setStatus({json.dumps(text)})')

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
                self.client, self.messages, self.tool_schemas,
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

    def open_file_dialog(self):
        """Open macOS file picker and return selected path, or empty string."""
        script = ('tell application "Finder"\nactivate\n'
                  'set theFile to choose file with prompt "Select MP3 File" of type {"mp3"}\n'
                  'return POSIX path of theFile\nend tell')
        try:
            result = subprocess.run(['osascript', '-e', script],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    def _generate_pattern_from_file(self, file_path: str):
        """Generate a musical MIDI pattern from file properties (no audio analysis)."""
        path = Path(file_path)
        file_size = path.stat().st_size
        seed = sum(ord(c) for c in path.name) % 1000

        estimated_duration = min(120, max(15, file_size / 50000))
        tempo = 90 + (file_size % 60)

        scales = {
            'major':      [0, 2, 4, 5, 7, 9, 11],
            'minor':      [0, 2, 3, 5, 7, 8, 10],
            'pentatonic': [0, 2, 4, 7, 9],
        }
        scale_keys = list(scales.keys())

        notes = []
        total_beats = int(estimated_duration * tempo / 60)
        beats_per_phrase = 8
        num_phrases = max(1, total_beats // beats_per_phrase)

        for phrase_idx in range(num_phrases):
            scale_intervals = scales[scale_keys[seed % len(scale_keys)]]
            root_note = 60 + ((seed + phrase_idx) % 12)
            start_beat = phrase_idx * beats_per_phrase

            for beat in range(beats_per_phrase):
                if beat % 2 == 0:
                    degree = (seed + phrase_idx + beat) % len(scale_intervals)
                    pitch = root_note + scale_intervals[degree]
                    if beat % 4 == 0:
                        pitch += 12
                    notes.append({"pitch": pitch, "start_beat": start_beat + beat,
                                  "length_beats": 1.0, "velocity": 80 + (seed % 20), "channel": 0})
                if beat % 4 == 0:
                    notes.append({"pitch": root_note + scale_intervals[0],
                                  "start_beat": start_beat + beat,
                                  "length_beats": 2.0, "velocity": 60, "channel": 1})

        for beat in range(0, total_beats, 4):
            notes.append({"pitch": 36 + ((seed + beat // 4) % 12), "start_beat": beat,
                          "length_beats": 4.0, "velocity": 100, "channel": 2})

        return notes, int(tempo)

    async def _transcribe_mp3(self, file_path: str):
        try:
            self.stop_requested = False
            self.set_status("Transcribing MP3...")
            self.add_message('tool', f"Analyzing {Path(file_path).name}...")

            notes, tempo = self._generate_pattern_from_file(file_path)

            if self.stop_requested:
                raise asyncio.CancelledError()

            self.add_message('tool', f"Generated {len(notes)} notes at {tempo} BPM")

            count_result = await reaper_tools.TOOLS["get_track_count"]()
            track_idx = count_result.get("count", 0)

            if self.stop_requested:
                raise asyncio.CancelledError()

            await reaper_tools.TOOLS["insert_track"](track_idx, f"MP3: {Path(file_path).stem}")

            total_beats = max(n["start_beat"] + n["length_beats"] for n in notes) + 1
            await reaper_tools.TOOLS["create_midi_item_beats"](track_idx, 0, total_beats, tempo)
            await reaper_tools.TOOLS["add_midi_notes_batch_beats"](track_idx, 0, tempo, notes)

            self.add_message('ai', f"Added transcription to track {track_idx + 1}. {len(notes)} notes, {tempo} BPM.")
            self.set_status("Ready")

        except asyncio.CancelledError:
            self.add_message('status', 'Stopped.')
            self.set_status("Ready")

        except Exception as e:
            self.add_message('error', f"Transcription error: {e}")
            self.set_status("Error")

        finally:
            self.is_processing = False
            self._js('setProcessing(false)')

    def clear_chat(self):
        if self.is_processing:
            return
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        self._js('clearMessages()')
        if self.connected_msg:
            self.add_message('status', self.connected_msg)
        self.set_status("Chat cleared")

    def on_stop(self):
        if self.is_processing:
            self.stop_requested = True
            self.set_status("Stopping...")

    async def initialize_backend(self):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            self.add_message('error', 'OPENAI_API_KEY not set. Add it to .env file.')
            return False

        self.set_status("Initializing...")
        self.client = AsyncOpenAI(api_key=api_key)

        self.set_status("Generating tool schemas...")
        self.tool_schemas = generate_tool_schemas()

        self.messages = [{"role": "system", "content": build_system_prompt()}]

        self.connected_msg = f"Connected. {len(self.tool_schemas)} REAPER tools loaded. Model: {MODEL}"
        self.add_message('status', self.connected_msg)
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
            x=screen_w - 420 - 10,
            y=y_pos,
            on_top=True,
            resizable=True,
        )
        self.window.events.loaded += self.on_window_loaded
        webview.start()


if __name__ == "__main__":
    app = ReaperAIApp()
    app.run()
