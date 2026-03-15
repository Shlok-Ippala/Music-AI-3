#!/usr/bin/env python3
"""
Standalone transcription worker using basic-pitch.
Runs in a Python 3.11 venv via subprocess from the main app.

Usage: python transcribe_worker.py <mp3_path>
Output: JSON to stdout with {"notes": [...], "tempo": float}
"""
import os
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
import warnings
warnings.filterwarnings("ignore")

import sys
import json
import librosa
import numpy as np
from basic_pitch.inference import predict


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No file path provided"}))
        sys.exit(1)

    file_path = sys.argv[1]

    # Load audio for tempo detection
    print("STATUS:Loading audio...", file=sys.stderr, flush=True)
    y, sr = librosa.load(file_path, sr=22050, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)
    print(f"STATUS:Audio loaded: {duration:.1f} seconds", file=sys.stderr, flush=True)

    # Detect tempo
    print("STATUS:Detecting tempo...", file=sys.stderr, flush=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, "__len__"):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(tempo)
    print(f"STATUS:Detected tempo: {tempo:.1f} BPM", file=sys.stderr, flush=True)

    # Run basic-pitch polyphonic transcription
    print("STATUS:Running ML transcription (this may take 10-30 seconds)...", file=sys.stderr, flush=True)
    model_output, midi_data, note_events = predict(file_path)

    if not note_events or len(note_events) == 0:
        print(json.dumps({"error": "No notes detected in audio"}))
        sys.exit(1)

    print(f"STATUS:Detected {len(note_events)} note events", file=sys.stderr, flush=True)

    # Convert note_events to beat-based format
    seconds_per_beat = 60.0 / tempo
    notes = []
    for start_sec, end_sec, pitch, amplitude, confidence in note_events:
        length_beats = (end_sec - start_sec) / seconds_per_beat
        if length_beats < 0.05:
            continue
        velocity = max(1, min(127, int(amplitude * 127)))
        notes.append({
            "pitch": int(pitch),
            "start_beat": round(start_sec / seconds_per_beat, 4),
            "length_beats": round(length_beats, 4),
            "velocity": velocity,
            "channel": 0,
        })

    print(f"STATUS:Transcription complete: {len(notes)} notes", file=sys.stderr, flush=True)
    print(json.dumps({"notes": notes, "tempo": tempo}))


if __name__ == "__main__":
    main()
