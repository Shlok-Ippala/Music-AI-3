#!/usr/bin/env python3
"""
REAPER AI Assistant — powered by Backboard

Uses Backboard's persistent memory and thread management to control REAPER DAW.
Assistant and thread IDs are saved locally so memory persists across sessions.
"""

import os
import sys
import json
import asyncio
import inspect
import re
import argparse
from pathlib import Path

from backboard import BackboardClient
from backboard.models import ToolOutput

import reaper_tools


# --- .env loader ---

def load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


# --- Persistence ---

SESSION_FILE = Path(__file__).parent / ".session.json"

def load_session() -> dict:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except Exception:
            pass
    return {}

def save_session(data: dict):
    SESSION_FILE.write_text(json.dumps(data))


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


def _get_json_type(annotation) -> dict:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}

    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        args = getattr(annotation, "__args__", None)
        if args:
            return {"type": "array", "items": {"type": TYPE_MAP.get(args[0], "string")}}
        return {"type": "array"}

    return {"type": TYPE_MAP.get(annotation, "string")}


def generate_tool_schemas() -> list:
    """Generate Backboard-compatible tool schemas from registered REAPER tools."""
    schemas = []

    for name, func in reaper_tools.TOOLS.items():
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

        schemas.append({
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
        })

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


async def handle_tool_calls(client: BackboardClient, thread_id, tool_calls: list, run_id: str):
    """Execute tool calls and submit results back to Backboard."""
    outputs = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            name = tc["function"]["name"]
            args = json.loads(tc["function"].get("arguments", "{}"))
            tc_id = tc["id"]
        else:
            name = tc.function.name
            args = tc.function.parsed_arguments
            tc_id = tc.id

        print(f"  -> {name}({json.dumps(args)[:80]})")
        result = await execute_tool(name, args)
        outputs.append(ToolOutput(tool_call_id=tc_id, output=json.dumps(result)))

    return await client.submit_tool_outputs(thread_id, run_id, outputs)


# --- REAPER bridge check ---

async def check_reaper_bridge() -> bool:
    """
    Test if the REAPER bridge is responding by calling get_project_name.
    Returns True if responsive, False if not.
    """
    try:
        result = await asyncio.wait_for(reaper_tools.TOOLS["get_project_name"](), timeout=3.0)
        return result.get("ok", False) or "name" in result or "project_name" in result
    except asyncio.TimeoutError:
        return False
    except Exception:
        return False


# --- Project context ---

async def get_project_context() -> str:
    """
    Fetch current REAPER project state and return a compact JSON context string.
    """
    context = {}

    try:
        tempo_result = await asyncio.wait_for(reaper_tools.TOOLS["get_tempo"](), timeout=2.0)
        context["tempo"] = tempo_result.get("tempo") or tempo_result.get("bpm") or tempo_result.get("ret")
    except Exception:
        context["tempo"] = None

    try:
        count_result = await asyncio.wait_for(reaper_tools.TOOLS["get_track_count"](), timeout=2.0)
        context["tracks"] = count_result.get("count") or count_result.get("track_count") or count_result.get("ret")
    except Exception:
        context["tracks"] = None

    try:
        tracks_result = await asyncio.wait_for(reaper_tools.TOOLS["get_all_tracks"](), timeout=2.0)
        track_list = tracks_result.get("tracks", [])
        context["track_names"] = [t.get("name", "") for t in track_list if isinstance(t, dict)]
    except Exception:
        context["track_names"] = []

    try:
        ts_result = await asyncio.wait_for(reaper_tools.TOOLS["get_time_signature"](), timeout=2.0)
        num = ts_result.get("numerator") or ts_result.get("num", 4)
        den = ts_result.get("denominator") or ts_result.get("den", 4)
        context["time_sig"] = f"{int(num)}/{int(den)}"
    except Exception:
        context["time_sig"] = "4/4"

    return json.dumps(context, separators=(",", ":"))


def build_message_with_context(context_json: str, user_message: str) -> str:
    """Prepend project context to the user's message."""
    return f"[PROJECT STATE: {context_json}]\n\n{user_message}"


# --- System prompt ---

SYSTEM_PROMPT = """You are Aria, an expert music producer and AI assistant controlling REAPER DAW. You have deep knowledge of:
- Music theory: scales, chord progressions, rhythm patterns, song structure (intro/verse/chorus/bridge/outro)
- Genre conventions: hip hop (boom bap, trap), lo-fi, pop, electronic, rock, jazz
- Mixing: EQ, compression, reverb, delay, sidechain, gain staging (-18dB LUFS target for mixing)
- MIDI: note numbers (C4=60, velocity 0-127), timing in beats, quantization
- REAPER workflow: track organization, color coding, naming conventions

When creating music:
- Always use musically appropriate note choices (not random pitches)
- Set velocities that feel human (vary between 90-110 for consistency, accent beats at 115-127)
- Use proper song structure unless told otherwise
- Name tracks descriptively
- Color-code track types (drums=red, bass=blue, melody=green, vocals=purple)

When the user gives a vague request like "make a beat", infer genre from context or ask one quick clarifying question. Be concise. Show tool calls as they execute, then give a brief summary of what was created.

The project state is automatically injected before each message so you always know the current state of the project."""


# --- Pending tool call fix ---

async def clear_pending_tool_calls(client: BackboardClient, thread_id: str):
    """
    If the last message in the thread has status REQUIRES_ACTION, submit
    empty error outputs to clear the pending state before starting.
    """
    try:
        thread_data = await client.get_thread(thread_id)
        # Check if thread has messages attribute or we need to fetch messages separately
        messages = getattr(thread_data, "messages", None)

        if messages is None:
            # Try fetching messages directly if available
            return

        if not messages:
            return

        last = messages[-1] if messages else None
        if last is None:
            return

        status = getattr(last, "status", "") or ""
        if status.upper() != "REQUIRES_ACTION":
            return

        tool_calls = getattr(last, "tool_calls", None)
        run_id = getattr(last, "run_id", None)
        if not tool_calls or not run_id:
            return

        print("Clearing pending tool calls from previous session...")
        error_outputs = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_id = tc["id"]
            else:
                tc_id = tc.id
            error_outputs.append(ToolOutput(
                tool_call_id=tc_id,
                output=json.dumps({"ok": False, "error": "Session interrupted — clearing stale tool call."})
            ))

        await client.submit_tool_outputs(thread_id, run_id, error_outputs)
        print("Cleared.")
    except Exception as e:
        # Non-fatal: if we can't clear, just proceed
        print(f"Note: could not check for pending tool calls: {e}")


# --- Streaming agentic loop ---

async def run_agentic_loop(client: BackboardClient, thread_id: str, user_message: str) -> str:
    """
    Run the agentic loop. For tool calls, execute them normally.
    For final text responses, stream the output character by character.
    """
    # First, do a non-streaming call to check if tools are needed
    response = await client.add_message(thread_id, user_message)

    while True:
        last = response.messages[-1] if hasattr(response, "messages") else response
        status = getattr(last, "status", "").upper()

        if status == "REQUIRES_ACTION" and getattr(last, "tool_calls", None):
            response = await handle_tool_calls(client, thread_id, last.tool_calls, last.run_id)
        else:
            # Final response — stream it
            content = getattr(last, "content", None) or ""
            return content


async def run_agentic_loop_streaming(client: BackboardClient, thread_id: str, user_message: str):
    """
    Run the agentic loop with streaming for the final text response.
    Tool call rounds are non-streaming; only the final answer streams.
    """
    # First pass: non-streaming to detect if tools are needed
    response = await client.add_message(thread_id, user_message)

    while True:
        last = response.messages[-1] if hasattr(response, "messages") else response
        status = getattr(last, "status", "").upper()

        if status == "REQUIRES_ACTION" and getattr(last, "tool_calls", None):
            # Execute tools, get next response non-streaming
            response = await handle_tool_calls(client, thread_id, last.tool_calls, last.run_id)
        else:
            # This is the final text response. We already have it — print streaming-style.
            content = getattr(last, "content", None) or ""
            print()  # newline before response
            for char in content:
                print(char, end="", flush=True)
            print("\n")
            return content


# --- Voice input ---

def record_voice_input() -> str | None:
    """
    Record audio from microphone and transcribe using speech_recognition.
    Returns transcribed text or None if failed.
    """
    try:
        import speech_recognition as sr
    except ImportError:
        print("Voice input requires: pip install SpeechRecognition pyaudio")
        return None

    recognizer = sr.Recognizer()
    print("Listening... (speak now, recording for 5 seconds)")

    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)

        print("Transcribing...")
        text = recognizer.recognize_google(audio)
        print(f"Heard: {text}")
        return text
    except sr.WaitTimeoutError:
        print("No speech detected.")
        return None
    except sr.UnknownValueError:
        print("Could not understand audio. Please type your message instead.")
        return None
    except sr.RequestError as e:
        print(f"Speech recognition error: {e}. Please type your message instead.")
        return None
    except Exception as e:
        print(f"Voice input error: {e}")
        return None


# --- Slash commands ---

TEMPLATE_PROMPTS = {
    "hip-hop": (
        "Set the tempo to 90 BPM. Create 4 tracks named Kick, Snare, Hi-Hat, Bass. "
        "On the Kick track create a 2-bar MIDI item and add notes: kick on beats 1 and 3 "
        "(positions 0 and 2 in quarter notes, note C1=36, velocity 110). "
        "On the Snare track add a 2-bar MIDI item with notes on beats 2 and 4 "
        "(positions 1 and 3, note D1=38, velocity 100). "
        "On the Hi-Hat track add a 2-bar MIDI item with 8th notes across 2 bars "
        "(positions 0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5 in each bar, note F#1=42, velocity 90). "
        "On the Bass track add a 2-bar MIDI item with a simple bass pattern. "
        "Color the tracks appropriately: Kick and Snare red, Hi-Hat orange, Bass blue."
    ),
    "lofi": (
        "Set the tempo to 75 BPM. Create 3 tracks named Piano, Drums, Bass. "
        "On the Piano track create a 4-bar MIDI item with a lo-fi jazz chord progression "
        "(try Cmaj7 - Am7 - Fmaj7 - G7, one chord per bar, with lush voicings). "
        "On the Drums track add a simple 2-bar loop with kick on beat 1, snare on beat 3, "
        "and hi-hats on every beat. "
        "On the Bass track add a walking bass line that fits the chord progression. "
        "Color Piano green, Drums red, Bass blue."
    ),
    "pop": (
        "Set the tempo to 120 BPM. Create 5 tracks named Drums, Bass, Chords, Lead, Vocals. "
        "On the Drums track create a 4-bar standard pop drum pattern "
        "(kick on 1 and 3, snare on 2 and 4, hi-hats on every 8th note). "
        "On the Bass track add a simple root-note bass line following a I-V-vi-IV progression in C major. "
        "On the Chords track add a 4-bar chord progression (C - G - Am - F). "
        "Leave Lead and Vocals tracks empty for now. "
        "Color Drums red, Bass blue, Chords green, Lead yellow, Vocals purple."
    ),
}

HELP_TEXT = """
Available commands:
  /help              Show this help message
  /status            Display full project summary
  /undo              Undo the last action in REAPER
  /new               Start a fresh conversation thread (new memory context)
  /template hip-hop  Scaffold a hip hop beat (Kick, Snare, Hi-Hat, Bass at 90 BPM)
  /template lofi     Scaffold a lo-fi track (Piano, Drums, Bass at 75 BPM)
  /template pop      Scaffold a pop song structure (Drums, Bass, Chords, Lead, Vocals at 120 BPM)

Or just type naturally — e.g. "add a reverb to the snare track"
"""


async def handle_slash_command(
    cmd: str,
    client: BackboardClient,
    thread_id: str,
    assistant_id: str,
    session: dict,
    voice_mode: bool,
) -> tuple[str | None, str, dict]:
    """
    Handle a slash command. Returns (output_message, new_thread_id, updated_session).
    output_message is None if the command should be forwarded as a regular AI message.
    """
    parts = cmd.strip().split(None, 2)
    command = parts[0].lower()
    subcommand = parts[1].lower() if len(parts) > 1 else ""

    if command == "/help":
        print(HELP_TEXT)
        return ("", thread_id, session)

    elif command == "/undo":
        result = await execute_tool("undo", {})
        msg = result.get("description") or result.get("message")
        if msg is None:
            if result.get("ok"):
                msg = "Last action undone successfully."
            else:
                msg = result.get("error") or json.dumps(result)
        print(f"Undone: {msg}")
        return ("", thread_id, session)

    elif command == "/status":
        context_json = await get_project_context()
        try:
            data = json.loads(context_json)
            print("\n=== Project Status ===")
            print(f"  Tempo:        {data.get('tempo', 'unknown')} BPM")
            print(f"  Time Sig:     {data.get('time_sig', 'unknown')}")
            print(f"  Track count:  {data.get('tracks', 'unknown')}")
            names = data.get("track_names", [])
            if names:
                print(f"  Tracks:       {', '.join(names)}")
            print("======================\n")
        except Exception:
            print(f"Project state: {context_json}")
        return ("", thread_id, session)

    elif command == "/new":
        thread = await client.create_thread(assistant_id=assistant_id)
        new_thread_id = str(thread.thread_id)
        session["thread_id"] = new_thread_id
        save_session(session)
        print(f"Started new thread {new_thread_id[:8]}...")
        return ("", new_thread_id, session)

    elif command == "/template":
        template_name = subcommand
        if template_name not in TEMPLATE_PROMPTS:
            available = ", ".join(TEMPLATE_PROMPTS.keys())
            print(f"Unknown template '{template_name}'. Available: {available}")
            return ("", thread_id, session)
        # Return None to signal this should be sent as an AI message
        return (None, thread_id, session)

    else:
        print(f"Unknown command '{command}'. Type /help for available commands.")
        return ("", thread_id, session)


# --- Memory seeding ---

async def seed_user_preferences(client: BackboardClient, assistant_id: str, session: dict) -> dict:
    """
    Ask the user 3 onboarding questions and store answers as memories.
    Returns updated session dict.
    """
    print("\nWelcome! Before we start, let me learn a bit about you.\n")

    questions = [
        ("genres", "1. What genres do you produce? (e.g. hip hop, pop, electronic): "),
        ("experience", "2. What's your experience level? (beginner/intermediate/advanced): "),
        ("plugins", "3. Any favorite plugins or sounds? (or press Enter to skip): "),
    ]

    preferences = {}
    for key, prompt in questions:
        try:
            answer = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer:
            preferences[key] = answer

    if preferences:
        # Store each preference as a memory in Backboard
        memory_parts = []
        if preferences.get("genres"):
            memory_parts.append(f"User produces: {preferences['genres']}")
        if preferences.get("experience"):
            memory_parts.append(f"Experience level: {preferences['experience']}")
        if preferences.get("plugins"):
            memory_parts.append(f"Favorite plugins/sounds: {preferences['plugins']}")

        memory_content = ". ".join(memory_parts) + "."

        try:
            await client.add_memory(assistant_id, memory_content, metadata={"type": "user_preferences"})
            print("Preferences saved to assistant memory.\n")
        except Exception as e:
            # Fall back: store in session file
            print(f"Note: could not save to assistant memory ({e}). Saving locally.\n")
            session["user_preferences"] = preferences

        session["onboarding_done"] = True
        save_session(session)

    return session


def build_preferences_context(session: dict) -> str:
    """Build a context string from locally-stored preferences (fallback)."""
    prefs = session.get("user_preferences")
    if not prefs:
        return ""
    parts = []
    if prefs.get("genres"):
        parts.append(f"genres: {prefs['genres']}")
    if prefs.get("experience"):
        parts.append(f"experience: {prefs['experience']}")
    if prefs.get("plugins"):
        parts.append(f"plugins: {prefs['plugins']}")
    if parts:
        return f"[USER PREFERENCES: {', '.join(parts)}]\n\n"
    return ""


# --- Main ---

async def main():
    parser = argparse.ArgumentParser(description="REAPER AI Assistant")
    parser.add_argument("--voice", action="store_true", help="Enable voice input mode")
    args = parser.parse_args()

    load_dotenv()

    api_key = os.environ.get("BACKBOARD_API_KEY")
    if not api_key:
        print("Error: BACKBOARD_API_KEY not set in .env")
        sys.exit(1)

    async with BackboardClient(api_key=api_key) as client:
        tool_schemas = generate_tool_schemas()[:128]
        print(f"Loaded {len(tool_schemas)} REAPER tools.")

        session = load_session()
        assistant_id = session.get("assistant_id")
        thread_id = session.get("thread_id")
        is_new_assistant = False

        # Reuse or create assistant
        if assistant_id:
            try:
                await client.update_assistant(
                    assistant_id,
                    system_prompt=SYSTEM_PROMPT,
                    tools=tool_schemas,
                )
                print(f"Resuming assistant {assistant_id[:8]}...")
            except Exception:
                assistant_id = None

        if not assistant_id:
            assistant = await client.create_assistant(
                name="Aria — REAPER Music Producer",
                system_prompt=SYSTEM_PROMPT,
                tools=tool_schemas,
            )
            assistant_id = str(assistant.assistant_id)
            thread_id = None
            is_new_assistant = True
            print(f"Created assistant {assistant_id[:8]}")

        # Reuse or create thread
        if thread_id:
            try:
                await client.get_thread(thread_id)
                print(f"Resuming thread {thread_id[:8]}...")
                # Fix: clear any pending tool calls from a previous interrupted run
                await clear_pending_tool_calls(client, thread_id)
            except Exception:
                thread_id = None

        if not thread_id:
            thread = await client.create_thread(assistant_id=assistant_id)
            thread_id = str(thread.thread_id)
            print(f"Created thread {thread_id[:8]}")

        session["assistant_id"] = assistant_id
        session["thread_id"] = thread_id
        save_session(session)

        # Memory seeding for new assistants
        if is_new_assistant or not session.get("onboarding_done"):
            session = await seed_user_preferences(client, assistant_id, session)

        # REAPER bridge connection check
        print("Checking REAPER bridge connection...")
        bridge_ok = await check_reaper_bridge()
        if bridge_ok:
            print("REAPER bridge connected.\n")
        else:
            print("\n\u26a0  REAPER bridge not responding. Make sure REAPER is running and the bridge script is active.\n")

        voice_hint = " Press Enter with no text to activate voice input." if args.voice else ""
        print("Aria — REAPER AI Assistant (Backboard)")
        print(f"Type your message, /help for commands, or 'quit' to exit.{voice_hint}\n")

        # Main chat loop
        while True:
            try:
                user_input = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                save_session(session)
                break

            # Voice input: empty Enter triggers recording
            if not user_input and args.voice:
                transcribed = record_voice_input()
                if transcribed:
                    user_input = transcribed
                else:
                    continue

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                save_session(session)
                break

            # Handle slash commands
            if user_input.startswith("/"):
                parts = user_input.split(None, 2)
                command = parts[0].lower()
                subcommand = parts[1].lower() if len(parts) > 1 else ""

                # Check if it's a template (needs AI processing)
                if command == "/template" and subcommand in TEMPLATE_PROMPTS:
                    user_input = TEMPLATE_PROMPTS[subcommand]
                    print(f"Running /template {subcommand}...")
                    # Fall through to normal AI processing below
                else:
                    output, thread_id, session = await handle_slash_command(
                        user_input, client, thread_id, assistant_id, session, args.voice
                    )
                    if output is not None:
                        # Command handled fully (empty string means no output needed)
                        continue
                    # output is None means fall through to AI — shouldn't happen here
                    continue

            # Build message with project context
            try:
                context_json = await get_project_context()
                full_message = build_message_with_context(context_json, user_input)

                # Prepend local preferences if stored as fallback
                prefs_context = build_preferences_context(session)
                if prefs_context:
                    full_message = prefs_context + full_message
            except Exception:
                full_message = user_input

            # Run agentic loop with streaming final response
            try:
                await run_agentic_loop_streaming(client, thread_id, full_message)
            except KeyboardInterrupt:
                print("\n(interrupted)\n")
            except Exception as e:
                print(f"\nError: {e}\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")
