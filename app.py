#!/usr/bin/env python3
"""
REAPER AI Assistant

Side panel overlay using Dear PyGui + OpenAI-compatible API to control REAPER DAW.
"""

import os
import json
import asyncio
import inspect
import re
import threading
import ctypes
import queue
from pathlib import Path

import dearpygui.dearpygui as dpg
from openai import AsyncOpenAI

import reaper_tools

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
}


def generate_tool_schemas() -> list:
    schemas = []
    for name, func in reaper_tools.TOOLS.items():
        if name not in ALLOWED_TOOLS:
            continue
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
        schema = {
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
        schemas.append(schema)
    return schemas


# --- Tool execution ---

async def execute_tool(tool_name: str, tool_input: dict) -> dict:
    func = reaper_tools.TOOLS.get(tool_name)
    if func is None:
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}
    try:
        return await func(**tool_input)
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


BASE_SYSTEM_PROMPT = """You are a REAPER DAW music production assistant. You help producers build tracks incrementally.

The current project state is provided at the start of each message. ALWAYS work incrementally - add to the existing project.
{PLUGINS}
## CRITICAL RULES
1. To add MIDI notes, you MUST use add_midi_notes_batch_beats. Generate ALL notes in a SINGLE call.
2. To create MIDI items, use create_midi_item_beats.
3. NEVER use add_midi_note (singular). It does not exist in your tools.
4. NEVER use duplicate_item to repeat patterns. Instead, generate all notes for all bars directly.
5. For instruments, use track_fx_add_by_name with the EXACT plugin name from the available plugins list.

## ADDING NOTES - STEP BY STEP
1. set_tempo(bpm) - set the project tempo
2. insert_track(index, name) - create the track
3. track_fx_add_by_name(track_index, fx_name) - add instrument
4. create_midi_item_beats(track_index, position_beats=0, length_beats=TOTAL_BEATS, tempo=BPM)
5. add_midi_notes_batch_beats(track_index, item_index=0, tempo=BPM, notes=[...all notes...])

Each note in the notes array: (pitch, start_beat, length_beats, velocity, channel)
Example - 4-bar kick (four-on-the-floor at 120 BPM):
add_midi_notes_batch_beats(track_index=0, item_index=0, tempo=120, notes=[
  (pitch=36, start_beat=0, length_beats=0.5, velocity=95),
  (pitch=36, start_beat=1, length_beats=0.5, velocity=95),
  (pitch=36, start_beat=2, length_beats=0.5, velocity=95),
  (pitch=36, start_beat=3, length_beats=0.5, velocity=95),
  ... (pitch=36, start_beat=4 through 15, same pattern)
])

## MIDI REFERENCE
Notes: C4=60, D4=62, E4=64, F4=65, G4=67, A4=69, B4=71, C5=72. Octave=+12.
Drums (channel 9): Kick=36, Snare=38, Closed HH=42, Open HH=46, Clap=39, Crash=49, Ride=51
Beats: whole=4, half=2, quarter=1, eighth=0.5, sixteenth=0.25
Chords from root: Major=(0,4,7) Minor=(0,3,7) Maj7=(0,4,7,11) Min7=(0,3,7,10) Dom7=(0,4,7,10)

Be concise. Describe what you did briefly."""


# --- Agentic loop (OpenAI Chat Completions) ---

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


async def run_agentic_loop(client, messages, tool_schemas, status_callback=None):
    """Run the OpenAI tool-calling loop. Modifies messages list in-place."""
    response = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tool_schemas,
    )

    msg = response.choices[0].message
    messages.append(msg)

    while msg.tool_calls:
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
    """Get screen dimensions using ctypes (Windows)."""
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


class ReaperAIApp:
    def __init__(self):
        self.client = None
        self.messages = []
        self.tool_schemas = None
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

            self.messages.append({"role": "user", "content": augmented})

            response = await run_agentic_loop(
                self.client, self.messages, self.tool_schemas,
                status_callback=self.set_status,
            )

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
        """Set up OpenAI client and tools."""
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            self.add_chat_message(
                "OPENAI_API_KEY not set. Add it to .env file.", (255, 100, 100)
            )
            return False

        self.set_status("Initializing...")
        self.client = AsyncOpenAI(api_key=api_key)

        self.set_status("Generating tool schemas...")
        self.tool_schemas = generate_tool_schemas()

        self.messages = [{"role": "system", "content": build_system_prompt()}]

        self.set_status("Ready")
        self.add_chat_message(
            f"Connected. {len(self.tool_schemas)} REAPER tools loaded. Model: {MODEL}", COLOR_STATUS
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
            dpg.add_text("REAPER AI", color=COLOR_HEADER)
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
