#!/usr/bin/env python3
"""
REAPER AI Assistant

Standalone app that uses the Claude API to control REAPER DAW.
Replaces the MCP-based architecture with direct Claude API tool use.
"""

import os
import sys
import json
import asyncio
import inspect
import re
from pathlib import Path

from anthropic import Anthropic

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

        # Check if this is a new parameter line (param_name: description)
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

    # Handle generic types like list[int]
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        args = getattr(annotation, "__args__", None)
        if args:
            return {"type": "array", "items": {"type": TYPE_MAP.get(args[0], "string")}}
        return {"type": "array"}

    return {"type": TYPE_MAP.get(annotation, "string")}


def generate_tool_schemas() -> list:
    """Generate Claude API tool schemas from registered REAPER tool functions."""
    schemas = []

    for name, func in reaper_tools.TOOLS.items():
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""

        # Description is everything before Args:/Returns:
        description = re.split(r"\n\s*(Args|Returns|Raises|Note|Example):", doc)[0].strip()

        # Parse per-parameter docs
        arg_docs = _parse_arg_docs(doc)

        # Build properties from signature
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
            "name": name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
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


# --- Agentic loop ---

SYSTEM_PROMPT = """You are a music production assistant controlling REAPER DAW.
You have access to tools that let you create and manipulate tracks, MIDI items and notes,
audio items, FX plugins, sends, automation, markers, regions, and transport controls.

Use these tools to help the user with their music production tasks. When executing
multi-step operations, call all necessary tools before responding.

Be concise in your responses. Focus on what you did and the result."""

MODEL = "claude-opus-4-20250514"


async def run_agentic_loop(
    client: Anthropic,
    tool_schemas: list,
    user_message: str,
    conversation_history: list,
):
    """Run the agentic tool-use loop until Claude gives a final text response."""
    conversation_history.append({"role": "user", "content": user_message})

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=tool_schemas,
            messages=conversation_history,
        )

        # Add assistant response to history
        conversation_history.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract text blocks as the final response
            texts = [block.text for block in response.content if block.type == "text"]
            return "\n".join(texts)

        if response.stop_reason == "tool_use":
            # Execute all tool calls
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  -> {block.name}({json.dumps(block.input, indent=None)[:80]})")
                    result = await execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )

            conversation_history.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason
            return f"Unexpected stop reason: {response.stop_reason}"


# --- Main ---

async def main():
    load_dotenv()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set.")
        print("Set it as an environment variable or add it to a .env file.")
        sys.exit(1)

    client = Anthropic(api_key=api_key)

    print("Generating tool schemas...")
    tool_schemas = generate_tool_schemas()
    print(f"Loaded {len(tool_schemas)} REAPER tools.")

    print("\nREAPER AI Assistant")
    print("Type your message, or 'quit' to exit.\n")

    conversation_history = []

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
            response = await run_agentic_loop(
                client, tool_schemas, user_input, conversation_history
            )
            print(f"\n{response}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
