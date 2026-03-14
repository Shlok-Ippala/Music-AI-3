#!/usr/bin/env python3
"""
REAPER AI Assistant

Side panel overlay using Dear PyGui + Backboard.io to control REAPER DAW.
Single unified LLM with tool-calling for all music production tasks.
"""

import os
import sys
import json
import asyncio
import inspect
import re
import threading
import ctypes
import queue
from pathlib import Path

import dearpygui.dearpygui as dpg
from backboard import BackboardClient

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
        return {"type": "array"}
    return {"type": TYPE_MAP.get(annotation, "string")}


SKIP_TOOLS = {
    "zoom_to_selection", "zoom_to_project",
    "create_midi_item", "add_midi_notes_batch",  # replaced by beat-based versions
    "add_midi_note",  # force LLM to use batch
    "duplicate_item",  # force LLM to generate all notes directly
}


def generate_tool_schemas() -> list:
    schemas = []
    for name, func in reaper_tools.TOOLS.items():
        if name in SKIP_TOOLS:
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

SYSTEM_PROMPT = """You are a REAPER DAW music production assistant. You help producers build tracks incrementally.

The current project state is provided at the start of each message. ALWAYS work incrementally - add to the existing project.

## CRITICAL RULES
1. To add MIDI notes, you MUST use add_midi_notes_batch_beats. Generate ALL notes in a SINGLE call.
2. To create MIDI items, use create_midi_item_beats.
3. NEVER use add_midi_note (singular). It does not exist in your tools.
4. NEVER use duplicate_item to repeat patterns. Instead, generate all notes for all bars directly.
5. For instruments, use track_fx_add_by_name with "ReaSynth" (for synths) or standard names.

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


# --- Agentic loop ---

class BrokenThreadError(Exception):
    """Raised when a Backboard thread is in a broken state and needs replacement."""
    pass


def _validate_response(response):
    """Raise BrokenThreadError if response looks like an error, not real LLM output.

    Checks every string field on the response object for error indicators.
    This avoids fragile field-name guessing (content vs message vs other).
    """
    for attr in vars(response) if hasattr(response, '__dict__') else []:
        val = getattr(response, attr, None)
        if isinstance(val, str) and len(val) > 20:
            if "tool_call" in val and "invalid_request_error" in val:
                raise BrokenThreadError(val[:300])
            if "LLM Error" in val or "LLM API Error" in val:
                raise BrokenThreadError(val[:300])
    # Also check if response is a raw dict (some SDK versions)
    if isinstance(response, dict):
        text = json.dumps(response)
        if "tool_call" in text and "invalid_request_error" in text:
            raise BrokenThreadError(text[:300])


async def run_agentic_loop(client, thread_id, user_message, status_callback=None):
    response = await client.add_message(
        thread_id=thread_id, content=user_message, stream=False,
    )
    _validate_response(response)

    while response.status == "REQUIRES_ACTION" and response.tool_calls:
        if status_callback:
            status_callback(f"Executing {len(response.tool_calls)} tool(s)...")
        tool_outputs = []
        for tc in response.tool_calls:
            try:
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    tc_id = tc.get("id", "")
                    name = func.get("name", "")
                    raw_args = func.get("parsed_arguments") or func.get("arguments", "{}")
                    args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
                else:
                    tc_id = tc.id
                    name = tc.function.name
                    args = tc.function.parsed_arguments
                    if not isinstance(args, dict):
                        args = json.loads(args) if args else {}
                if status_callback:
                    status_callback(f"-> {name}")
                print(f"[Tool] {name}({json.dumps(args)[:200]})")
                result = await execute_tool(name, args)
            except Exception as e:
                print(f"[Tool Error] {e}")
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                name = "unknown"
                result = {"ok": False, "error": str(e)}
            tool_outputs.append({
                "tool_call_id": tc_id,
                "output": json.dumps(result),
            })
        print(f"[Submit] {len(tool_outputs)} tool outputs, run_id={response.run_id}")
        try:
            response = await client.submit_tool_outputs(
                thread_id=thread_id, run_id=response.run_id, tool_outputs=tool_outputs,
            )
        except Exception as e:
            print(f"[Submit Error] {e}, retrying once...")
            try:
                response = await client.submit_tool_outputs(
                    thread_id=thread_id, run_id=response.run_id, tool_outputs=tool_outputs,
                )
            except Exception:
                raise BrokenThreadError(f"submit_tool_outputs failed: {e}")
        _validate_response(response)

    return response.content


# --- Dear PyGui UI ---

# Colors
COLOR_BG = (30, 30, 35, 255)
COLOR_USER = (130, 180, 255)
COLOR_AI = (220, 220, 220)
COLOR_STATUS = (150, 150, 150)
COLOR_HEADER = (100, 150, 255)
COLOR_INPUT_BG = (45, 45, 50, 255)

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
        self.assistant_id = None
        self.thread_id = None
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

            try:
                response = await run_agentic_loop(
                    self.client, self.thread_id, augmented,
                    status_callback=self.set_status,
                )
            except BrokenThreadError as e:
                print(f"[Recovery] Broken thread detected: {e}")
                self.set_status("Recovering — creating new thread...")
                thread = await self.client.create_thread(
                    assistant_id=self.assistant_id
                )
                self.thread_id = thread.thread_id
                self.add_chat_message("Thread recovered. Retrying...", COLOR_STATUS)
                response = await run_agentic_loop(
                    self.client, self.thread_id, augmented,
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
        """Set up Backboard client and assistant."""
        api_key = os.environ.get("BACKBOARD_API_KEY")
        if not api_key:
            self.add_chat_message(
                "BACKBOARD_API_KEY not set. Add it to .env file.", (255, 100, 100)
            )
            return False

        self.set_status("Connecting to Backboard...")
        self.client = BackboardClient(api_key=api_key)

        self.set_status("Generating tool schemas...")
        self.tool_schemas = generate_tool_schemas()

        self.set_status("Creating assistant...")
        assistant = await self.client.create_assistant(
            name="REAPER Assistant", system_prompt=SYSTEM_PROMPT,
            tools=self.tool_schemas,
        )
        self.assistant_id = assistant.assistant_id
        thread = await self.client.create_thread(
            assistant_id=self.assistant_id
        )
        self.thread_id = thread.thread_id

        self.set_status("Ready")
        self.add_chat_message(
            f"Connected. {len(self.tool_schemas)} REAPER tools loaded.", COLOR_STATUS
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
