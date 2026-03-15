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
from pathlib import Path

import dearpygui.dearpygui as dpg
import railtracks as rt
from railtracks.llm import MessageHistory, UserMessage, AssistantMessage

import reaper_tools
import music_theory
import plugin_scanner
import plugin_installer

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
    tool_funcs = [
        func for name, func in reaper_tools.TOOLS.items() if name in ALLOWED_TOOLS
    ] + [
        func for name, func in music_theory.MUSIC_TOOLS.items() if name in ALLOWED_TOOLS
    ]

    # Strip "models/" prefix for GeminiLLM (litellm expects "gemini-2.5-flash" not "models/gemini-2.5-flash")
    model_name = model.removeprefix("models/")
    llm = rt.llm.GeminiLLM(model_name=model_name, api_key=api_key)

    ReaperAgent = rt.agent_node(
        "REAPER AI",
        tool_nodes=tool_funcs,
        llm=llm,
        system_message=system_prompt,
    )
    return rt.Flow(name="REAPER Flow", entry_point=ReaperAgent)


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
1. ALWAYS use get_chord_progression, get_bass_line, get_drum_pattern, and get_melody to generate notes. NEVER compute MIDI note numbers yourself — the music theory tools handle correct notes, voicings, and humanization.
2. To add MIDI notes, you MUST use add_midi_notes_batch_beats. Pass the notes array from the music theory tools directly.
3. To create MIDI items, use create_midi_item_beats.
4. NEVER use add_midi_note (singular). It does not exist in your tools.
5. NEVER use duplicate_item to repeat patterns. Instead, generate all notes for all bars directly.
6. For instruments, use track_fx_add_by_name with the EXACT plugin name from the available plugins list.

## COMPOSING WORKFLOW (follow this order)
1. **set_tempo(bpm)** — choose appropriate tempo for genre
2. **get_chord_progression(genre, key, bars)** — get chord voicings. Save the returned notes for the chords/pads track.
3. **For each layer, repeat:**
   a. insert_track(index, name) — create the track
   b. track_fx_add_by_name(track_index, fx_name) — add instrument plugin
   b2. Configure synth parameters with track_fx_set_param (see SYNTH CONFIGURATION)
   c. create_midi_item_beats(track_index, position_beats=0, length_beats=TOTAL_BEATS, tempo=BPM)
   d. Get notes from the appropriate music theory tool:
      - **Drums**: get_drum_pattern(genre, bars) — humanized drums with fills
      - **Bass**: get_bass_line(key, genre, bars) — bass following chord roots
      - **Chords/Pads**: use the notes from get_chord_progression (step 2)
      - **Melody**: get_melody(key, genre, bars, density) — chord-tone melody
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
- **Bass**: Instrument in octave 2-3 range. The bass tool follows chord roots with rhythmic variation.
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


# --- Dear PyGui UI ---

# Colors
COLOR_BG = (30, 30, 35, 255)
COLOR_USER = (130, 180, 255)
COLOR_AI = (220, 220, 220)
COLOR_STATUS = (150, 150, 150)
COLOR_HEADER = (100, 150, 255)
PANEL_WIDTH = 380
PANEL_MIN_WIDTH = 300


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
        self.rt_history = MessageHistory([])
        self.async_loop = None
        self.is_processing = False
        self.message_count = 0
        self.ui_queue = queue.Queue()

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
                dpg.add_text(
                    full_text, parent="chat_history", wrap=PANEL_WIDTH - 40,
                    color=color, tag=f"msg_{self.message_count}",
                )
                dpg.add_spacer(height=5, parent="chat_history")
                dpg.set_y_scroll("chat_history", dpg.get_y_scroll_max("chat_history") + 100)
            elif cmd[0] == "status":
                dpg.set_value("status_text", cmd[1])
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
                augmented = f"[Current REAPER project state: {json.dumps(project_state)}]\n\n{user_input}"
            except Exception:
                augmented = user_input

            self.rt_history.append(UserMessage(augmented))

            self.set_status("Thinking...")
            result = await self.rt_flow.ainvoke(self.rt_history)
            response = result.text

            self.rt_history.append(AssistantMessage(response))
            self.add_chat_message(response, COLOR_AI, prefix="AI: ")
            self.set_status("Ready")

        except Exception as e:
            self.add_chat_message(f"Error: {e}", (255, 100, 100))
            self.set_status("Error")
            print(f"[Error] {e}")

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

        self.set_status("Scanning plugins...")
        try:
            scan = plugin_scanner.scan_plugins()
            plugin_info = f"{scan['instruments_found']} instruments, {scan['effects_found']} effects"
        except Exception:
            plugin_info = "plugin scan failed"

        self.set_status("Building Railtracks agent...")
        self.rt_flow = _build_rt_flow(build_system_prompt(), api_key, MODEL)
        self.rt_history = MessageHistory([])

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

        # Create the main window content
        with dpg.window(tag="primary", no_title_bar=True, no_move=True, no_resize=True):
            # Header
            with dpg.group(horizontal=True):
                dpg.add_text("REAPER AI", color=COLOR_HEADER)
                dpg.add_button(
                    label="Scan", callback=self.on_scan_plugins,
                    width=50,
                )
                dpg.add_button(
                    label="Install", callback=self.on_install_plugins,
                    width=55,
                )
            dpg.add_separator()

            # Chat history (scrollable)
            with dpg.child_window(
                tag="chat_history", autosize_x=True, height=-70,
                border=False,
            ):
                dpg.add_text("Initializing...", color=COLOR_STATUS, tag="init_msg")

            dpg.add_separator()

            # Status bar
            dpg.add_text("Starting up...", tag="status_text", color=COLOR_STATUS)

            # Input row
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag="input_field", hint="Type a message...",
                    width=-60, on_enter=True, callback=self.on_input_enter,
                    enabled=False,
                )
                dpg.add_button(
                    tag="send_btn", label="Send", width=55,
                    callback=self.on_send, enabled=False,
                )

        # Viewport setup
        dpg.create_viewport(
            title="REAPER AI",
            width=PANEL_WIDTH,
            height=screen_h - 40,
            x_pos=screen_w - PANEL_WIDTH - 10,
            y_pos=0,
            always_on_top=True,
            resizable=False,
            min_width=PANEL_MIN_WIDTH,
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
