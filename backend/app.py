#!/usr/bin/env python3
"""
REAPER AI Assistant

Side panel overlay using Dear PyGui + OpenAI-compatible API to control REAPER DAW.
"""

import os
import json
import asyncio
import threading
import ctypes
import queue
import platform
import subprocess
import traceback
from pathlib import Path

import dearpygui.dearpygui as dpg
import railtracks as rt
from railtracks.llm import MessageHistory, UserMessage

import reaper_tools
import music_theory
import plugin_scanner
import plugin_installer
import logger

# --- .env loader ---

def load_dotenv():
    """Load environment variables from .env file (checks backend/ then project root)."""
    for candidate in [Path(__file__).parent / ".env", Path(__file__).parent.parent / ".env"]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
            break


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


# --- Railtracks agent setup ---

def _build_rt_flow(system_prompt: str, api_key: str, model: str) -> rt.Flow:
    """Build a Railtracks Flow with all REAPER + music theory tools."""
    tool_nodes = [
        rt.function_node(func)
        for name, func in {**reaper_tools.TOOLS, **music_theory.MUSIC_TOOLS}.items()
        if name in ALLOWED_TOOLS
    ]

    # Strip "models/" prefix for GeminiLLM (litellm expects "gemini-2.5-flash" not "models/gemini-2.5-flash")
    model_name = model.removeprefix("models/")
    llm = rt.llm.GeminiLLM(model_name=model_name, api_key=api_key)

    # Hook into the LLM to log every decision the agent makes
    llm.add_post_hook(logger.llm_decision_hook)

    ReaperAgent = rt.agent_node(
        "REAPER AI",
        tool_nodes=tool_nodes,
        llm=llm,
        system_message=system_prompt,
    )
    return rt.Flow(
        name="REAPER Flow",
        entry_point=ReaperAgent,
        broadcast_callback=logger.log_broadcast,
    )


# --- System prompt ---

def load_plugins():
    """Load user's available plugins from plugins.json."""
    plugins_path = next(
        (p for p in [Path(__file__).parent / "plugins.json", Path(__file__).parent.parent / "plugins.json"] if p.exists()),
        Path(__file__).parent / "plugins.json"
    )
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
8. When adding new tracks, ALWAYS append to the end by calling insert_track() with NO index argument (or index=current track count). NEVER insert at a middle index unless the user explicitly asks to insert between specific tracks. Inserting at a middle index shifts all subsequent track indices and will break references to existing tracks.
9. NEVER modify, overwrite, or delete existing tracks unless the user explicitly asks to edit a specific track.

## COMPOSING WORKFLOW (follow this order)
1. **set_tempo(bpm)** — choose appropriate tempo for genre
2. **get_chord_progression(genre, key, bars)** — get chord voicings. Save the returned notes AND the progression string for the chords/pads track.
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
- **Lo-fi**: 75-85 BPM, key of major (C, F, Eb), jazzy chords, sparse melody
- **Pop**: 100-120 BPM, key of major (C, G, D), medium density melody
- **R&B**: 85-95 BPM, key of major (Eb, Ab, Bb), sparse melody
- **Rock**: 110-140 BPM, key of major or minor (E, A, G), dense melody

## MIDI REFERENCE (for manual adjustments only)
Notes: C4=60, D4=62, E4=64, F4=65, G4=67, A4=69, B4=71, C5=72. Octave=+12.
Drums (GM/channel 9): Kick=36, Snare=38, Closed HH=42, Open HH=46, Crash=49, Ride=51
Beats: whole=4, half=2, quarter=1, eighth=0.5, sixteenth=0.25

Be concise. Describe what you built briefly — list the progression, layers, key, and tempo."""


# --- Model config ---

MODEL = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")

TOOL_DESCRIPTIONS = {
    "set_tempo": "Setting tempo...",
    "insert_track": "Creating track...",
    "track_fx_add_by_name": "Loading plugin...",
    "track_fx_set_param": "Configuring synth...",
    "create_midi_item_beats": "Creating MIDI clip...",
    "add_midi_notes_batch_beats": "Writing notes...",
    "get_chord_progression": "Generating chords...",
    "get_bass_line": "Generating bass line...",
    "get_drum_pattern": "Generating drum pattern...",
    "get_melody": "Generating melody...",
    "add_eq": "Adding EQ...",
    "add_compressor": "Adding compressor...",
    "add_limiter": "Adding limiter...",
    "play": "Starting playback...",
    "save_project": "Saving project...",
}


# --- Dear PyGui UI ---

# Colors
COLOR_BG       = (22, 22, 28, 255)
COLOR_USER     = (120, 180, 255)
COLOR_AI       = (210, 210, 210)
COLOR_STATUS   = (120, 120, 135)
COLOR_HEADER   = (110, 160, 255)
COLOR_TOOL     = (100, 200, 140)
COLOR_DIVIDER  = (50, 50, 60)
COLOR_INPUT_BG = (32, 32, 40)

PANEL_WIDTH     = 420
PANEL_MIN_WIDTH = 320
PANEL_MIN_HEIGHT = 400


def get_screen_size():
    """Get screen dimensions cross-platform."""
    system = platform.system()
    if system == "Windows":
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    elif system == "Darwin":
        try:
            result = subprocess.run(
                ["osascript", "-e", "tell application \"Finder\" to get bounds of window of desktop"],
                capture_output=True, text=True, timeout=3,
            )
            parts = result.stdout.strip().split(", ")
            if len(parts) == 4:
                return int(parts[2]), int(parts[3])
        except Exception:
            pass
        return 1920, 1080
    else:
        return 1920, 1080


class ReaperAIApp:
    def __init__(self):
        self.rt_flow = None
        self.conversation_log = []   # list of (role, text) for context injection
        self.async_loop = None
        self.is_processing = False
        self.message_count = 0
        self.ui_queue = queue.Queue()
        self.uploaded_file_path = None

    def add_chat_message(self, text, color, prefix=""):
        """Thread-safe: queue a chat message for the main thread."""
        self.ui_queue.put(("chat", text, color, prefix))

    def set_status(self, text):
        """Thread-safe: queue a status update for the main thread."""
        self.ui_queue.put(("status", text))

    def _set_input_enabled(self, enabled):
        """Thread-safe: queue input state change for the main thread."""
        self.ui_queue.put(("input_enabled", enabled))

    def _drain_ui_queue(self):
        """Process pending UI updates. Called from main render loop."""
        while not self.ui_queue.empty():
            try:
                cmd = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if cmd[0] == "chat":
                _, text, color, prefix = cmd
                self.message_count += 1
                full_text = f"{prefix}{text}" if prefix else text
                wrap_w = max(200, dpg.get_viewport_width() - 48)
                dpg.add_text(
                    full_text, parent="chat_history", wrap=wrap_w,
                    color=color, tag=f"msg_{self.message_count}",
                )
                dpg.add_spacer(height=6, parent="chat_history")
                dpg.set_y_scroll("chat_history", dpg.get_y_scroll_max("chat_history") + 200)
            elif cmd[0] == "status":
                text = cmd[1]
                dpg.set_value("status_text", text)
                if text == "Ready":
                    dpg.configure_item("dot_indicator", color=(60, 180, 100))
                elif text in ("Error",):
                    dpg.configure_item("dot_indicator", color=(220, 80, 80))
                else:
                    dpg.configure_item("dot_indicator", color=(220, 160, 40))
            elif cmd[0] == "input_enabled":
                enabled = cmd[1]
                dpg.configure_item("send_btn", enabled=enabled)
                dpg.configure_item("input_field", enabled=enabled)
                if enabled:
                    dpg.focus_item("input_field")

    def on_scan_plugins(self, sender=None, app_data=None):
        """Scan REAPER plugin cache and update plugins.json."""
        self.set_status("Scanning plugins...")
        try:
            result = plugin_scanner.scan_plugins()
            api_key = os.environ.get("GEMINI_API_KEY", "")
            self.rt_flow = _build_rt_flow(build_system_prompt(), api_key, MODEL)
            self.add_chat_message(
                f"Scanned: {result['instruments_found']} instruments, "
                f"{result['effects_found']} effects. Agent updated.",
                COLOR_STATUS,
            )
            self.set_status("Ready")
        except Exception as e:
            self.add_chat_message(f"Scan error: {e}", (255, 100, 100))
            self.set_status("Ready")

    def on_install_plugins(self, sender=None, app_data=None):
        """Auto-discover and install plugins from Downloads/Desktop."""
        self.set_status("Looking for plugins...")
        self._set_input_enabled(False)

        def run():
            try:
                result = plugin_installer.install_all()
                if not result["installed"] and not result["failed"]:
                    self.add_chat_message(
                        "No new plugins found in Downloads or Desktop.", COLOR_STATUS
                    )
                else:
                    for name in result["installed"]:
                        self.add_chat_message(f"Installed: {name}", (100, 220, 100))
                    for item in result["failed"]:
                        self.add_chat_message(
                            f"Failed: {item['name']} — {item['error']}", (255, 100, 100)
                        )
                    if result["installed"]:
                        # Rebuild agent with newly scanned plugins
                        api_key = os.environ.get("GEMINI_API_KEY", "")
                        self.rt_flow = _build_rt_flow(build_system_prompt(), api_key, MODEL)
                        self.add_chat_message(
                            f"Done. {result['installed_count']} plugin(s) installed. "
                            "Rescan REAPER (Preferences → Plug-ins → VST → Re-scan) "
                            "then click Scan.",
                            COLOR_STATUS,
                        )
            except Exception as e:
                self.add_chat_message(f"Install error: {e}", (255, 100, 100))
            finally:
                self.set_status("Ready")
                self._set_input_enabled(True)

        import threading
        threading.Thread(target=run, daemon=True).start()

    # --- Song to MIDI ---

    def open_file_dialog(self, sender=None, app_data=None):
        """Open macOS Finder dialog to select an MP3 file."""
        script = '''
        tell application "Finder"
            activate
            set theFile to choose file with prompt "Select MP3 File" of type {"mp3"}
            return POSIX path of theFile
        end tell
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                file_path = result.stdout.strip()
                self.uploaded_file_path = file_path
                dpg.set_value("mp3_file_path", file_path)
                self.add_chat_message(f"MP3 selected: {Path(file_path).name}", COLOR_STATUS)
                self.set_status("MP3 loaded — ready to transcribe")
            else:
                self.add_chat_message("No file selected.", COLOR_STATUS)
        except subprocess.TimeoutExpired:
            self.add_chat_message("File dialog timed out.", (255, 100, 100))
        except Exception as e:
            self.add_chat_message(f"Error opening dialog: {e}", (255, 100, 100))

    def on_upload_mp3(self, sender=None, app_data=None):
        """Validate manually typed MP3 path."""
        file_path = dpg.get_value("mp3_file_path").strip()
        if not file_path:
            self.add_chat_message("Enter an MP3 file path.", (255, 100, 100))
            return
        if not file_path.endswith(".mp3"):
            self.add_chat_message("File must be an .mp3.", (255, 100, 100))
            return
        if not Path(file_path).exists():
            self.add_chat_message(f"File not found: {file_path}", (255, 100, 100))
            return
        self.uploaded_file_path = file_path
        self.add_chat_message(f"MP3 loaded: {Path(file_path).name}", COLOR_STATUS)
        self.set_status("MP3 ready for transcription")

    def audio_to_midi_notes_with_tempo(self, file_path):
        """Transcribe audio to MIDI notes using Spotify's basic-pitch (via python3.12 subprocess)."""
        import subprocess, json, shutil

        py312 = shutil.which("python3.12")
        if not py312:
            self.add_chat_message("python3.12 not found. Install it via brew.", (255, 100, 100))
            return None

        self.add_chat_message(f"Running neural transcription on {Path(file_path).name}...", COLOR_STATUS)
        self.add_chat_message("This may take 10–30s depending on song length.", COLOR_STATUS)

        script = """
import sys, json, pathlib, os

# Redirect stdout to stderr so basic-pitch progress messages don't pollute our JSON output
_real_stdout = sys.stdout
sys.stdout = sys.stderr

# Patch scipy.signal.gaussian removed in scipy >= 1.8
import scipy.signal
if not hasattr(scipy.signal, 'gaussian'):
    import scipy.signal.windows
    scipy.signal.gaussian = scipy.signal.windows.gaussian

from basic_pitch.inference import predict
from basic_pitch import ICASSP_2022_MODEL_PATH

# Prefer ONNX model (version-independent); TF saved model breaks on TF 2.16+
onnx_path = pathlib.Path(ICASSP_2022_MODEL_PATH).parent / "nmp.onnx"
model_path = str(onnx_path) if onnx_path.exists() else ICASSP_2022_MODEL_PATH

file_path = sys.argv[1]
_, _, note_events = predict(
    file_path,
    model_path,
    onset_threshold=0.5,
    frame_threshold=0.3,
    minimum_note_length=80,
    minimum_frequency=40.0,
    maximum_frequency=2000.0,
    multiple_pitch_bends=False,
)
# note_events: list of (start_sec, end_sec, pitch_midi, amplitude, pitch_bends)
events = [[float(e[0]), float(e[1]), int(e[2]), float(e[3])] for e in note_events]
sys.stdout = _real_stdout
print(json.dumps(events))
"""
        try:
            result = subprocess.run(
                [py312, "-c", script, file_path],
                capture_output=True, text=True, timeout=120
            )
            stdout = result.stdout.strip()

            if result.returncode != 0 or not stdout:
                real_errors = [l for l in result.stderr.splitlines()
                               if not l.startswith("WARNING") and not l.startswith("INFO")]
                err = real_errors[-1].strip() if real_errors else "(no output)"
                self.add_chat_message(f"Transcription failed: {err}", (255, 100, 100))
                print("=== basic-pitch stderr ===\n" + result.stderr)
                print("=== basic-pitch stdout ===\n" + result.stdout)
                return None

            note_events = json.loads(stdout)
            if not note_events:
                self.add_chat_message("No notes detected in audio.", (255, 200, 100))
                return None

            duration_sec = note_events[-1][1]
            notes_per_sec = len(note_events) / max(duration_sec, 1)
            tempo = max(60, min(180, int(notes_per_sec * 30)))

            self.add_chat_message(
                f"Detected {len(note_events)} notes over {duration_sec:.1f}s (~{tempo} BPM)",
                COLOR_STATUS
            )

            beats_per_second = tempo / 60.0
            notes = []
            for start_sec, end_sec, pitch, amplitude in note_events:
                start_beat = round(start_sec * beats_per_second, 3)
                length_beats = max(0.25, round((end_sec - start_sec) * beats_per_second, 3))
                velocity = max(1, min(127, int(amplitude * 127)))
                notes.append({
                    "pitch": pitch,
                    "start_beat": start_beat,
                    "length_beats": length_beats,
                    "velocity": velocity,
                    "channel": 0,
                })

            return notes, float(tempo)

        except subprocess.TimeoutExpired:
            self.add_chat_message("Transcription timed out (>120s).", (255, 100, 100))
            return None
        except Exception as e:
            self.add_chat_message(f"Transcription error: {e}", (255, 100, 100))
            traceback.print_exc()
            return None

    async def _pick_instrument_for_audio(self, file_path, notes, tempo) -> str | None:
        """Use the AI to pick the best available instrument for the transcribed audio."""
        available = load_plugins().get("instruments", [])
        EXCLUDE = {"MT-PowerDrumKit", "Ample Percussion Cloudrum", "Ample Bass P Lite II"}
        melodic = [p for p in available if p not in EXCLUDE]

        if not melodic:
            return None

        # Gather signal info to give the AI context
        pitches = [n["pitch"] for n in notes]
        avg_pitch = sum(pitches) / len(pitches) if pitches else 60
        pitch_range = max(pitches) - min(pitches) if pitches else 0
        file_name = Path(file_path).stem if file_path else "unknown"

        prompt = (
            f"You are choosing a VST instrument plugin for a MIDI transcription.\n\n"
            f"Audio file: '{file_name}'\n"
            f"Tempo: {tempo:.0f} BPM\n"
            f"Notes detected: {len(notes)}\n"
            f"Average MIDI pitch: {avg_pitch:.0f} (60=C4, 69=A4)\n"
            f"Pitch range: {pitch_range} semitones\n\n"
            f"Available instruments (choose EXACTLY one of these names):\n"
            + "\n".join(f"- {p}" for p in melodic) +
            f"\n\nRespond with ONLY the exact plugin name, nothing else."
        )

        try:
            from railtracks import MessageHistory, UserMessage
            result = await self.rt_flow.ainvoke(MessageHistory([UserMessage(prompt)]))
            chosen = result.last_message.content.strip().strip('"').strip("'")
            # Validate it's actually in our list
            if chosen in melodic:
                return chosen
            # Try case-insensitive match
            for p in melodic:
                if p.lower() == chosen.lower():
                    return p
        except Exception:
            pass

        # Fallback: simple heuristic if AI fails
        for preferred in ["Upright Piano", "Surge XT", "Splice INSTRUMENT"]:
            if preferred in melodic:
                return preferred
        return melodic[0] if melodic else None

    async def transcribe_mp3_to_midi(self):
        """Convert loaded MP3 to MIDI and add to the REAPER project."""
        if not self.uploaded_file_path:
            self.add_chat_message("No MP3 file loaded.", (255, 100, 100))
            self.is_processing = False
            self._set_input_enabled(True)
            return
        try:
            self.set_status("Converting MP3 to MIDI...")
            result = self.audio_to_midi_notes_with_tempo(self.uploaded_file_path)
            if not result:
                return

            notes, detected_tempo = result
            self.add_chat_message(f"Extracted {len(notes)} notes at {detected_tempo:.1f} BPM", COLOR_STATUS)

            test = await reaper_tools.TOOLS["get_project_summary"]()
            if not test.get("ok"):
                self.add_chat_message(f"REAPER not connected: {test.get('error')}", (255, 100, 100))
                return

            count_result = await reaper_tools.TOOLS["get_track_count"]()
            track_count = count_result.get("count", 0)

            insert_result = await reaper_tools.TOOLS["insert_track"](track_count, "MP3 Transcription")
            if not insert_result.get("ok"):
                self.add_chat_message(f"Failed to create track: {insert_result.get('error')}", (255, 100, 100))
                return

            midi_track = track_count

            # Ask the AI to pick the best instrument for this audio
            chosen = await self._pick_instrument_for_audio(
                self.uploaded_file_path, notes, detected_tempo
            )
            if chosen:
                fx_result = await reaper_tools.TOOLS["track_fx_add_by_name"](midi_track, chosen)
                if fx_result.get("ok"):
                    self.add_chat_message(f"Loaded instrument: {chosen}", COLOR_AI)
                else:
                    self.add_chat_message(f"Could not load {chosen}: {fx_result.get('error')}", (255, 200, 100))
            else:
                self.add_chat_message(
                    "No suitable instrument found in your plugins for this audio. "
                    "Consider installing Upright Piano or Surge XT.",
                    (255, 200, 100)
                )

            total_length = max(n["start_beat"] + n["length_beats"] for n in notes) + 1

            item_result = await reaper_tools.TOOLS["create_midi_item_beats"](
                midi_track, 0, total_length, detected_tempo
            )
            if not item_result.get("ok"):
                self.add_chat_message(f"Failed to create MIDI item: {item_result.get('error')}", (255, 100, 100))
                return

            notes_result = await reaper_tools.TOOLS["add_midi_notes_batch_beats"](
                midi_track, 0, detected_tempo, notes
            )
            if notes_result.get("ok"):
                self.add_chat_message(f"Done — {len(notes)} notes added to REAPER.", COLOR_AI)
                self.set_status("Conversion complete")
            else:
                self.add_chat_message(f"Failed to add notes: {notes_result.get('error')}", (255, 100, 100))

        except Exception as e:
            self.add_chat_message(f"Error: {e}", (255, 100, 100))
            self.set_status("Error")
            traceback.print_exc()
        finally:
            self.is_processing = False
            self._set_input_enabled(True)

    def on_transcribe_button(self, sender=None, app_data=None):
        """Handle transcribe button click."""
        if self.is_processing:
            return
        self.is_processing = True
        self._set_input_enabled(False)
        asyncio.run_coroutine_threadsafe(self.transcribe_mp3_to_midi(), self.async_loop)

    def on_send(self, sender=None, app_data=None):
        """Handle send button or Enter key."""
        if self.is_processing:
            return

        user_input = dpg.get_value("input_field").strip()
        if not user_input:
            return

        # Clear input and show user message
        dpg.set_value("input_field", "")
        self.add_chat_message(user_input, COLOR_USER, prefix="You: ")
        logger.log_user(user_input)

        # Process in background
        self.is_processing = True
        self._set_input_enabled(False)
        self.set_status("Thinking...")

        asyncio.run_coroutine_threadsafe(
            self._process_message(user_input), self.async_loop
        )

    async def _process_message(self, user_input):
        """Process a user message (runs in async thread)."""
        try:
            # Inject current project state as context
            self.set_status("Reading project state...")
            try:
                project_state = await reaper_tools.TOOLS["get_project_summary"]()
                project_ctx = f"[Current REAPER project state: {json.dumps(project_state)}]\n\n"
            except Exception:
                project_ctx = ""

            # Embed last few turns as context so the agent remembers the conversation
            # (avoids passing full MessageHistory to ainvoke which breaks Gemini multi-turn)
            conv_ctx = ""
            if self.conversation_log:
                recent = self.conversation_log[-4:]  # last 2 turns
                conv_ctx = "Recent conversation:\n" + "\n".join(
                    f"  {role}: {text[:300]}" for role, text in recent
                ) + "\n\n"

            augmented = f"{project_ctx}{conv_ctx}User: {user_input}"

            self.set_status("Thinking...")
            result = await self.rt_flow.ainvoke(MessageHistory([UserMessage(augmented)]))
            response = result.text

            # Store turn for next message's context
            self.conversation_log.append(("User", user_input))
            self.conversation_log.append(("Assistant", response))

            self.add_chat_message(response, COLOR_AI, prefix="AI: ")
            logger.log_ai(response)
            self.set_status("Ready")

        except Exception as e:
            self.add_chat_message(f"Error: {e}", (255, 100, 100))
            self.set_status("Error")
            traceback.print_exc()

        finally:
            self.is_processing = False
            self._set_input_enabled(True)

    def on_input_enter(self, sender, app_data):
        """Handle Enter key in input field."""
        self.on_send()

    async def initialize_backend(self):
        """Set up Railtracks agent and tools."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            self.add_chat_message(
                "GEMINI_API_KEY not set. Add it to .env file.", (255, 100, 100)
            )
            return False

        # Patch all tools with terminal logging
        reaper_tools.TOOLS = logger.patch_tools(reaper_tools.TOOLS)
        music_theory.MUSIC_TOOLS = logger.patch_tools(music_theory.MUSIC_TOOLS)
        logger.log_status("Tool logging enabled")

        self.set_status("Installing new plugins...")
        try:
            install = plugin_installer.install_all()
            if install["installed"]:
                for name in install["installed"]:
                    logger.log_status(f"Installed: {name}")
        except Exception:
            pass

        self.set_status("Scanning plugins...")
        try:
            scan = plugin_scanner.scan_plugins()
            plugin_info = f"{scan['instruments_found']} instruments, {scan['effects_found']} effects"
        except Exception:
            plugin_info = "plugin scan failed"

        self.set_status("Building Railtracks agent...")
        try:
            self.rt_flow = _build_rt_flow(build_system_prompt(), api_key, MODEL)
            self.conversation_log = []
        except Exception as e:
            self.add_chat_message(f"Agent build failed: {e}", (255, 100, 100))
            self.set_status("Error")
            return False

        tool_count = sum(1 for n in ALLOWED_TOOLS if n in reaper_tools.TOOLS or n in music_theory.MUSIC_TOOLS)
        self.set_status("Ready")
        self.add_chat_message(
            f"Connected via Railtracks. {tool_count} tools loaded. "
            f"{plugin_info}. Model: {MODEL}",
            COLOR_STATUS,
        )
        self._set_input_enabled(True)
        return True

    def run(self):
        """Launch the Dear PyGui overlay."""
        load_dotenv()

        screen_w, screen_h = get_screen_size()

        dpg.create_context()

        # Global font/theme
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg,      COLOR_BG[:3],        category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg,       (28, 28, 35),        category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Button,        (50, 70, 120),       category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 100, 180),      category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (90, 130, 220),      category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg,       (38, 38, 50),        category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,(48, 48, 65),        category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,   (22, 22, 28),        category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (60, 60, 80),        category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Border,        (50, 50, 65),        category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Separator,     (50, 50, 65),        category=dpg.mvThemeCat_Core)
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,  8,  category=dpg.mvThemeCat_Core)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,   6,  category=dpg.mvThemeCat_Core)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,   12, 10, category=dpg.mvThemeCat_Core)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,     8,  6,  category=dpg.mvThemeCat_Core)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding,    8,  5,  category=dpg.mvThemeCat_Core)

        with dpg.window(tag="primary", no_title_bar=True, no_move=True, no_resize=True):
            # ── Header ──────────────────────────────────────────
            with dpg.group(horizontal=True):
                dpg.add_text("◈ REAPER AI", color=COLOR_HEADER)
                dpg.add_spacer(width=-175)
                dpg.add_button(label="⟳ Scan",    callback=self.on_scan_plugins,    width=80)
                dpg.add_button(label="↓ Install", callback=self.on_install_plugins, width=80)

            # Model + status on same line
            with dpg.group(horizontal=True):
                model_short = MODEL.replace("models/", "")
                dpg.add_text(f"  {model_short}", color=(70, 90, 130))
                dpg.add_spacer(width=8)
                dpg.add_text("●", color=(60, 180, 100), tag="dot_indicator")
                dpg.add_text("Starting up...", tag="status_text", color=COLOR_STATUS)

            dpg.add_separator()

            # ── Song to MIDI ─────────────────────────────────────
            dpg.add_text("Song → MIDI", color=COLOR_HEADER)
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag="mp3_file_path", hint="Browse or paste MP3 path...",
                    width=-70, enabled=True,
                )
                dpg.add_button(label="Browse", width=62, callback=self.open_file_dialog)
            dpg.add_button(
                label="Transcribe to MIDI", width=-1,
                callback=self.on_transcribe_button,
            )
            dpg.add_separator()

            # ── Chat history ─────────────────────────────────────
            with dpg.child_window(
                tag="chat_history", autosize_x=True, height=-100,
                border=False,
            ):
                dpg.add_text("Initializing...", color=COLOR_STATUS, tag="init_msg")

            dpg.add_separator()

            # ── Input row ────────────────────────────────────────
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag="input_field", hint="Ask AI to build something...",
                    width=-70, on_enter=True, callback=self.on_input_enter,
                    enabled=False, multiline=False,
                )
                dpg.add_button(
                    tag="send_btn", label="Send", width=62,
                    callback=self.on_send, enabled=False,
                )

        dpg.bind_theme(global_theme)

        # Viewport setup — resizable
        dpg.create_viewport(
            title="REAPER AI",
            width=PANEL_WIDTH,
            height=screen_h - 40,
            x_pos=screen_w - PANEL_WIDTH - 10,
            y_pos=0,
            always_on_top=True,
            resizable=True,
            min_width=PANEL_MIN_WIDTH,
            min_height=PANEL_MIN_HEIGHT,
            clear_color=COLOR_BG,
        )

        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("primary", True)

        # Start async event loop in background thread
        def run_async_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self.async_loop = asyncio.new_event_loop()
        async_thread = threading.Thread(
            target=run_async_loop, args=(self.async_loop,), daemon=True
        )
        async_thread.start()

        # Initialize backend
        asyncio.run_coroutine_threadsafe(
            self.initialize_backend(), self.async_loop
        )

        # Render loop
        while dpg.is_dearpygui_running():
            self._drain_ui_queue()
            dpg.render_dearpygui_frame()

        dpg.destroy_context()


if __name__ == "__main__":
    app = ReaperAIApp()
    app.run()
