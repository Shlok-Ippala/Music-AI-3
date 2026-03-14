#!/usr/bin/env python3
"""
REAPER AI Assistant

Standalone app that uses Backboard.io to control REAPER DAW.
Two-LLM pipeline: Composer (generates TOON music specs) + Executor (calls REAPER tools).
"""

import os
import sys
import json
import asyncio
import inspect
import re
from pathlib import Path

from backboard import BackboardClient
from toon import encode as toon_encode, decode as toon_decode

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
    """Parse Google-style docstring Args section into a dict of param_name -> description."""
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
    """Convert a Python type annotation to a JSON schema type dict."""
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}

    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        args = getattr(annotation, "__args__", None)
        if args:
            return {"type": "array", "items": {"type": TYPE_MAP.get(args[0], "string")}}
        return {"type": "array"}

    return {"type": TYPE_MAP.get(annotation, "string")}


# Tools to skip (least essential, keeps us under the 128 tool limit)
SKIP_TOOLS = {"zoom_to_selection", "zoom_to_project"}


def generate_tool_schemas() -> list:
    """Generate OpenAI-format tool schemas for Backboard from registered REAPER tool functions."""
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
    """Execute a REAPER tool by name with the given input."""
    func = reaper_tools.TOOLS.get(tool_name)
    if func is None:
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}
    try:
        return await func(**tool_input)
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- System prompts ---

COMPOSER_SYSTEM_PROMPT = """You are a music composition assistant. Given a user's request, generate a complete musical specification in TOON format (Token-Oriented Object Notation).

CRITICAL: Include EVERY note of the entire song. Do not abbreviate, skip sections, or say "repeat". Write out every single note.

Output ONLY the TOON specification, no other text. Use this exact format:

tempo: [BPM]
time_sig: [e.g. 4/4 or 3/4]
track: [track name]
instrument: [instrument name, e.g. Upright Piano]
notes[N](pitch,start,length,velocity):
[MIDI pitch],[start time in beats],[duration in beats],[velocity 0-127]
[next note...]
...

Rules:
- Use standard MIDI pitch numbers (60 = middle C / C4, 62 = D4, 64 = E4, 65 = F4, 67 = G4, 69 = A4, 71 = B4, 72 = C5)
- Start times are in beats from the beginning (beat 0)
- Lengths are in beats (1 = quarter note, 0.5 = eighth note, 2 = half note, etc.)
- Velocity range: 60-100 for normal playing
- N must equal the exact number of note rows
- Include the COMPLETE song from start to finish"""

EXECUTOR_SYSTEM_PROMPT = """You are a REAPER DAW assistant that executes music production tasks.
You have access to tools for creating tracks, adding MIDI notes, managing FX, mixing, and transport.

IMPORTANT: When adding MIDI notes, ALWAYS use add_midi_notes_batch to add ALL notes in a single call.
NEVER use add_midi_note individually for multiple notes — it is too slow and error-prone.

When given a detailed music specification, execute it precisely:
1. Set the tempo with set_tempo
2. Create the track with insert_track, then add the instrument with track_fx_add_by_name
3. Create a MIDI item with create_midi_item
4. Add ALL notes in ONE call using add_midi_notes_batch
5. Set cursor to 0 and play

For non-music tasks, use the appropriate tools directly.
Be concise in your responses."""

# Keywords that suggest the user wants music composition
COMPOSITION_KEYWORDS = [
    "melody", "song", "music", "tune", "play me", "compose", "create a",
    "make a", "write a", "happy birthday", "twinkle", "jingle", "chord",
    "progression", "beat", "drum pattern", "bass line", "riff",
]


def needs_composition(user_message: str) -> bool:
    """Check if the user's message requires the composer LLM."""
    lower = user_message.lower()
    return any(keyword in lower for keyword in COMPOSITION_KEYWORDS)


# --- Composer LLM ---

async def compose_music_spec(client: BackboardClient, composer_thread_id: str, user_message: str) -> str:
    """Use the composer LLM to generate a TOON music specification."""
    print("  [Composing music specification...]")

    response = await client.add_message(
        thread_id=composer_thread_id,
        content=user_message,
        stream=False,
    )

    toon_spec = response.content
    print(f"  [Composer output: {len(toon_spec)} chars]")
    return toon_spec


def parse_toon_spec(toon_spec: str) -> dict:
    """Parse a TOON music specification into a structured dict."""
    result = {
        "tempo": 120,
        "time_sig": "4/4",
        "track": "Piano",
        "instrument": "Upright Piano",
        "notes": [],
    }

    lines = toon_spec.strip().splitlines()
    in_notes = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if in_notes:
            # Parse note row: pitch,start,length,velocity
            parts = line.split(",")
            if len(parts) >= 4:
                try:
                    result["notes"].append({
                        "pitch": int(float(parts[0].strip())),
                        "start": float(parts[1].strip()),
                        "length": float(parts[2].strip()),
                        "velocity": int(float(parts[3].strip())),
                    })
                except (ValueError, IndexError):
                    continue
            continue

        # Check for notes header (supports notes[N]:, notes[N]{...}, notes[N](...) syntax)
        if "notes[" in line:
            in_notes = True
            continue

        # Parse key: value lines
        if ":" in line and not line.startswith("notes"):
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "tempo":
                try:
                    result["tempo"] = float(value)
                except ValueError:
                    pass
            elif key == "time_sig":
                result["time_sig"] = value
            elif key == "track":
                result["track"] = value
            elif key == "instrument":
                result["instrument"] = value

    return result


async def execute_music_spec(spec: dict):
    """Execute a parsed music spec directly via REAPER tools (no LLM needed)."""
    tempo = spec["tempo"]
    beat_duration = 60.0 / tempo

    # Convert notes from beats to seconds
    notes = []
    for note in spec["notes"]:
        notes.append({
            "pitch": note["pitch"],
            "start_position": round(note["start"] * beat_duration, 4),
            "length": round(note["length"] * beat_duration, 4),
            "velocity": note["velocity"],
        })

    # Calculate total duration
    if notes:
        last_note = max(notes, key=lambda n: n["start_position"] + n["length"])
        total_seconds = last_note["start_position"] + last_note["length"] + 1.0
    else:
        total_seconds = 10.0

    # 1. Set tempo
    print(f"  -> set_tempo(bpm={tempo})")
    await reaper_tools.TOOLS["set_tempo"](bpm=tempo)

    # 2. Get current track count to know the index
    result = await reaper_tools.TOOLS["get_track_count"]()
    track_index = result.get("ret", 0)

    # 3. Insert track
    print(f"  -> insert_track(name={spec['track']})")
    await reaper_tools.TOOLS["insert_track"](index=track_index, name=spec["track"])

    # 4. Add instrument
    print(f"  -> track_fx_add_by_name(fx_name={spec['instrument']})")
    await reaper_tools.TOOLS["track_fx_add_by_name"](
        track_index=track_index, fx_name=spec["instrument"]
    )

    # 5. Create MIDI item
    print(f"  -> create_midi_item(length={round(total_seconds, 2)})")
    await reaper_tools.TOOLS["create_midi_item"](
        track_index=track_index, position=0, length=total_seconds
    )

    # 6. Add all notes in one batch
    print(f"  -> add_midi_notes_batch({len(notes)} notes)")
    await reaper_tools.TOOLS["add_midi_notes_batch"](
        track_index=track_index, item_index=0, notes=notes
    )

    # 7. Play from beginning
    print("  -> set_cursor_position(0)")
    await reaper_tools.TOOLS["set_cursor_position"](position=0)
    print("  -> play()")
    await reaper_tools.TOOLS["play"]()

    return (
        f"Created track '{spec['track']}' with {spec['instrument']} at {tempo} BPM. "
        f"Added {len(notes)} notes ({round(total_seconds, 1)}s). Playing now."
    )


# --- Agentic loop ---

async def run_agentic_loop(
    client: BackboardClient,
    thread_id: str,
    user_message: str,
):
    """Run the agentic tool-use loop until the LLM gives a final text response."""
    response = await client.add_message(
        thread_id=thread_id,
        content=user_message,
        stream=False,
    )

    while response.status == "REQUIRES_ACTION" and response.tool_calls:
        print(f"  [{len(response.tool_calls)} tool call(s)]")

        tool_outputs = []
        for tc in response.tool_calls:
            # Handle both object and dict formats
            if isinstance(tc, dict):
                func = tc.get("function", {})
                tc_id = tc.get("id", "")
                name = func.get("name", "")
                args = func.get("parsed_arguments") or json.loads(func.get("arguments", "{}"))
            else:
                tc_id = tc.id
                name = tc.function.name
                args = tc.function.parsed_arguments
            print(f"  -> {name}({json.dumps(args, indent=None)[:80]})")
            result = await execute_tool(name, args)
            tool_outputs.append({
                "tool_call_id": tc_id,
                "output": json.dumps(result),
            })

        response = await client.submit_tool_outputs(
            thread_id=thread_id,
            run_id=response.run_id,
            tool_outputs=tool_outputs,
        )

    return response.content


# --- Main ---

async def main():
    load_dotenv()

    api_key = os.environ.get("BACKBOARD_API_KEY")
    if not api_key:
        print("Error: BACKBOARD_API_KEY not set.")
        print("Set it as an environment variable or add it to a .env file.")
        sys.exit(1)

    client = BackboardClient(api_key=api_key)

    print("Generating tool schemas...")
    tool_schemas = generate_tool_schemas()
    print(f"Loaded {len(tool_schemas)} REAPER tools.")

    print("Setting up assistants...")

    # Composer assistant — no tools, generates TOON specs
    composer_assistant = await client.create_assistant(
        name="Music Composer",
        system_prompt=COMPOSER_SYSTEM_PROMPT,
    )
    composer_thread = await client.create_thread(assistant_id=composer_assistant.assistant_id)

    # Executor assistant — has REAPER tools
    executor_assistant = await client.create_assistant(
        name="REAPER Executor",
        system_prompt=EXECUTOR_SYSTEM_PROMPT,
        tools=tool_schemas,
    )
    print("\nREAPER AI Assistant")
    print("Type your message, or 'quit' to exit.\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        try:
            if needs_composition(user_input):
                # Two-step pipeline: Composer LLM → Direct REAPER execution
                toon_spec = await compose_music_spec(
                    client, composer_thread.thread_id, user_input
                )
                print(f"  [TOON spec preview: {toon_spec[:200]}...]")

                spec = parse_toon_spec(toon_spec)
                print(f"  [Parsed: {len(spec['notes'])} notes, {spec['tempo']} BPM, {spec['instrument']}]")

                if not spec["notes"]:
                    print("\nError: Composer returned no notes. Raw output:")
                    print(toon_spec)
                    print()
                    continue

                response = await execute_music_spec(spec)
            else:
                # Fresh thread per request to avoid corrupted tool call state
                executor_thread = await client.create_thread(assistant_id=executor_assistant.assistant_id)
                response = await run_agentic_loop(
                    client, executor_thread.thread_id, user_input
                )

            print(f"\n{response}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
