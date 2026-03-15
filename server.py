#!/usr/bin/env python3
"""
Aria FastAPI Backend

Connects the React frontend to the Python AI + REAPER bridge.
Run with: uvicorn server:app --reload --port 8000
"""

import os
import json
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from backboard import BackboardClient
from backboard.models import ToolOutput

import reaper_tools
from audio_upload_gui import upload_handler
from app import (
    load_dotenv,
    load_session,
    save_session,
    generate_tool_schemas,
    get_project_context,
    build_message_with_context,
    build_preferences_context,
    execute_tool,
    clear_pending_tool_calls,
    check_reaper_bridge,
    SYSTEM_PROMPT,
)


# --- Global state ---

class AppState:
    client: Optional[BackboardClient] = None
    assistant_id: Optional[str] = None
    thread_id: Optional[str] = None
    session: dict = {}
    bridge_connected: bool = False

state = AppState()


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    api_key = os.environ.get("BACKBOARD_API_KEY")
    if not api_key:
        raise RuntimeError("BACKBOARD_API_KEY not set in .env")

    tool_schemas = generate_tool_schemas()[:128]
    session = load_session()

    async with BackboardClient(api_key=api_key) as client:
        state.client = client
        state.session = session

        assistant_id = session.get("assistant_id")
        thread_id = session.get("thread_id")

        if assistant_id:
            try:
                await client.update_assistant(
                    assistant_id,
                    system_prompt=SYSTEM_PROMPT,
                    tools=tool_schemas,
                )
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

        if thread_id:
            try:
                await client.get_thread(thread_id)
                await clear_pending_tool_calls(client, thread_id)
            except Exception:
                thread_id = None

        if not thread_id:
            thread = await client.create_thread(assistant_id=assistant_id)
            thread_id = str(thread.thread_id)

        state.assistant_id = assistant_id
        state.thread_id = thread_id
        session["assistant_id"] = assistant_id
        session["thread_id"] = thread_id
        save_session(session)

        state.bridge_connected = await check_reaper_bridge()
        print(f"\nAria server ready on http://localhost:8000")
        print(f"Bridge: {'✓ connected' if state.bridge_connected else '✗ not connected'}\n")

        yield

    save_session(state.session)


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Helpers ---

def track_color(name: str) -> str:
    n = name.lower()
    if any(w in n for w in ["kick", "snare", "drum", "hat", "perc", "clap", "tom"]):
        return "#EF4444"
    if any(w in n for w in ["bass"]):
        return "#3B82F6"
    if any(w in n for w in ["piano", "keys", "chord", "harmony", "pad"]):
        return "#10B981"
    if any(w in n for w in ["lead", "melody", "synth", "arp"]):
        return "#06B6D4"
    if any(w in n for w in ["vocal", "vox", "voice", "sing"]):
        return "#A855F7"
    if any(w in n for w in ["guitar", "gtr", "strings"]):
        return "#F59E0B"
    return "#7C3AED"


async def get_full_status() -> dict:
    context_json = await get_project_context()
    try:
        context = json.loads(context_json)
    except Exception:
        context = {}

    tracks = []
    try:
        result = await asyncio.wait_for(reaper_tools.TOOLS["get_all_tracks"](), timeout=3.0)
        raw_tracks = result.get("tracks", [])
        for i, t in enumerate(raw_tracks):
            if isinstance(t, dict):
                name = t.get("name", f"Track {i}")
                vol_linear = t.get("volume", 1.0) or 1.0
                vol_pct = min(100, int(vol_linear * 100))
                tracks.append({
                    "index": i,
                    "name": name,
                    "color": track_color(name),
                    "volume": vol_pct,
                    "muted": bool(t.get("muted", False)),
                    "solo": bool(t.get("solo", False)),
                    "type": "midi",
                })
    except Exception:
        pass

    playing = False
    recording = False
    try:
        ps = await asyncio.wait_for(reaper_tools.TOOLS["get_play_state"](), timeout=2.0)
        state_val = ps.get("ret", 0) or 0
        playing = state_val == 1
        recording = state_val == 5
    except Exception:
        pass

    return {
        "tempo": context.get("tempo", 120),
        "time_sig": context.get("time_sig", "4/4"),
        "track_count": context.get("tracks", len(tracks)),
        "tracks": tracks,
        "bridge_connected": state.bridge_connected,
        "playing": playing,
        "recording": recording,
    }


# --- REST Endpoints ---

@app.get("/status")
async def get_status():
    return await get_full_status()


@app.post("/play")
async def play():
    return await execute_tool("play", {})


@app.post("/stop")
async def stop():
    return await execute_tool("stop", {})


@app.post("/record")
async def record_endpoint():
    return await execute_tool("record", {})


@app.post("/track")
async def add_track(body: dict):
    name = body.get("name", "New Track")
    try:
        count_result = await execute_tool("get_track_count", {})
        index = int(count_result.get("ret", 0) or 0)
    except Exception:
        index = 0
    return await execute_tool("insert_track", {"index": index, "name": name})


@app.post("/undo")
async def undo():
    return await execute_tool("undo", {})


@app.get("/health")
async def health():
    bridge = await check_reaper_bridge()
    state.bridge_connected = bridge
    return {"status": "ok", "bridge": bridge}


# --- WebSocket Chat ---

async def run_agentic_loop_ws(websocket: WebSocket, message: str):
    client = state.client
    thread_id = state.thread_id

    response = await client.add_message(thread_id, message)

    while True:
        last = response.messages[-1] if hasattr(response, "messages") else response
        status = getattr(last, "status", "").upper()

        if status == "REQUIRES_ACTION" and getattr(last, "tool_calls", None):
            outputs = []
            for tc in last.tool_calls:
                name = tc.function.name
                args = tc.function.parsed_arguments
                args_preview = json.dumps(args)
                if len(args_preview) > 60:
                    args_preview = args_preview[:57] + "..."

                await websocket.send_json({
                    "type": "tool_call",
                    "name": name,
                    "args": args_preview,
                })

                result = await execute_tool(name, args)
                outputs.append(ToolOutput(tool_call_id=tc.id, output=json.dumps(result)))

            response = await client.submit_tool_outputs(thread_id, last.run_id, outputs)

        else:
            content = getattr(last, "content", "") or ""
            await websocket.send_json({"type": "message", "content": content})

            try:
                project_data = await get_full_status()
                await websocket.send_json({"type": "project_update", "data": project_data})
            except Exception:
                pass
            break


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            user_message = data.get("content", "").strip()
            if not user_message:
                continue

            try:
                context_json = await get_project_context()
                full_message = build_message_with_context(context_json, user_message)
                prefs = build_preferences_context(state.session)
                if prefs:
                    full_message = prefs + full_message
            except Exception:
                full_message = user_message

            await websocket.send_json({"type": "thinking"})

            try:
                await run_agentic_loop_ws(websocket, full_message)
            except Exception as e:
                await websocket.send_json({"type": "error", "content": str(e)})

    except WebSocketDisconnect:
        pass


# --- Audio Upload Endpoints ---

@app.post("/api/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    """Upload an audio file."""
    try:
        metadata = await upload_handler.save_upload(file)
        return {"success": True, "metadata": metadata}
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(e)}
        )

@app.get("/api/uploads")
async def list_uploads():
    """List all uploaded audio files."""
    try:
        uploads = upload_handler.list_uploads()
        return {"success": True, "uploads": uploads}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.get("/api/uploads/{filename}")
async def get_upload_info(filename: str):
    """Get metadata for a specific uploaded file."""
    try:
        metadata = upload_handler.get_upload_info(filename)
        if metadata:
            return {"success": True, "metadata": metadata}
        else:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "File not found"}
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.delete("/api/uploads/{filename}")
async def delete_upload(filename: str):
    """Delete an uploaded audio file."""
    try:
        deleted = upload_handler.delete_upload(filename)
        if deleted:
            return {"success": True, "message": "File deleted successfully"}
        else:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "File not found"}
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )
