"""
Music theory engine for generating musically correct MIDI data.

Provides chord progressions, bass lines, drum patterns, and melodies
as pre-computed MIDI note lists ready for add_midi_notes_batch_beats.
"""

import random

# --- Data ---

NOTE_MAP = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

CHORD_TYPES = {
    "maj":   (0, 4, 7),
    "min":   (0, 3, 7),
    "maj7":  (0, 4, 7, 11),
    "min7":  (0, 3, 7, 10),
    "dom7":  (0, 4, 7, 10),
    "dim":   (0, 3, 6),
    "aug":   (0, 4, 8),
    "sus2":  (0, 2, 7),
    "sus4":  (0, 5, 7),
    "min9":  (0, 3, 7, 10, 14),
    "maj9":  (0, 4, 7, 11, 14),
    "add9":  (0, 4, 7, 14),
    "6":     (0, 4, 7, 9),
    "min6":  (0, 3, 7, 9),
}

SCALE_TYPES = {
    "major":             [0, 2, 4, 5, 7, 9, 11],
    "minor":             [0, 2, 3, 5, 7, 8, 10],
    "dorian":            [0, 2, 3, 5, 7, 9, 10],
    "mixolydian":        [0, 2, 4, 5, 7, 9, 10],
    "pentatonic_major":  [0, 2, 4, 7, 9],
    "pentatonic_minor":  [0, 3, 5, 7, 10],
    "blues":             [0, 3, 5, 6, 7, 10],
}

# Scale degree to semitones from root (for major key)
MAJOR_DEGREES = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}
# For minor key (natural minor)
MINOR_DEGREES = {1: 0, 2: 2, 3: 3, 4: 5, 5: 7, 6: 8, 7: 10}

# Chord progressions per genre
# Each entry: list of (degree, quality) tuples
PROGRESSIONS = {
    "pop": [
        [(1, "maj"), (5, "maj"), (6, "min"), (4, "maj")],        # I-V-vi-IV
        [(1, "maj"), (4, "maj"), (5, "maj"), (4, "maj")],        # I-IV-V-IV
        [(6, "min"), (4, "maj"), (1, "maj"), (5, "maj")],        # vi-IV-I-V
        [(1, "maj"), (6, "min"), (4, "maj"), (5, "maj")],        # I-vi-IV-V
    ],
    "lofi": [
        [(2, "min7"), (5, "dom7"), (1, "maj7"), (6, "min7")],    # ii7-V7-Imaj7-vi7
        [(1, "maj7"), (3, "min7"), (6, "min7"), (2, "min7")],    # Imaj7-iii7-vi7-ii7
        [(1, "maj7"), (6, "min7"), (2, "min7"), (5, "dom7")],    # Imaj7-vi7-ii7-V7
        [(4, "maj7"), (3, "min7"), (2, "min7"), (1, "maj7")],    # IVmaj7-iii7-ii7-Imaj7
    ],
    "hiphop": [
        [(1, "min"), (6, "maj"), (3, "maj"), (7, "maj")],        # i-VI-III-VII
        [(1, "min"), (4, "min"), (7, "maj"), (3, "maj")],        # i-iv-VII-III
        [(1, "min"), (4, "min"), (5, "min"), (4, "min")],        # i-iv-v-iv
        [(1, "min7"), (6, "maj"), (7, "dom7"), (3, "maj")],      # i7-VI-VII7-III
    ],
    "rnb": [
        [(1, "maj7"), (3, "min7"), (6, "min7"), (5, "dom7")],    # Imaj7-iii7-vi7-V7
        [(2, "min9"), (5, "dom7"), (1, "maj7"), (4, "maj7")],    # ii9-V7-Imaj7-IVmaj7
        [(1, "maj7"), (6, "min7"), (2, "min7"), (5, "dom7")],    # Imaj7-vi7-ii7-V7
    ],
    "rock": [
        [(1, "maj"), (4, "maj"), (5, "maj"), (1, "maj")],        # I-IV-V-I
        [(1, "maj"), (5, "maj"), (4, "maj"), (1, "maj")],        # I-V-IV-I
        [(1, "min"), (7, "maj"), (6, "maj"), (5, "maj")],        # i-VII-VI-V
    ],
}

# Drum patterns: dict of genre -> {instrument: [(beat_offset, velocity_base), ...]} per bar (4 beats)
DRUM_PATTERNS = {
    "pop": {
        "kick":  [(0, 110), (2, 105)],
        "snare": [(1, 100), (3, 105)],
        "hihat": [(i * 0.5, 80) for i in range(8)],
    },
    "lofi": {
        "kick":  [(0, 90), (1.5, 70), (2.75, 75)],
        "snare": [(1, 85), (3, 80)],
        "hihat": [(i * 0.5, 65) for i in range(8)],
    },
    "hiphop": {
        "kick":  [(0, 115), (0.75, 80), (2, 110), (3.25, 85)],
        "snare": [(1, 105), (3, 110)],
        "hihat": [(i * 0.25, 75) for i in range(16)],  # sixteenths
    },
    "rnb": {
        "kick":  [(0, 95), (2, 90), (2.75, 70)],
        "snare": [(1, 90), (3, 85)],
        "hihat": [(i * 0.5, 60) for i in range(8)],
    },
    "rock": {
        "kick":  [(0, 115), (2, 110)],
        "snare": [(1, 110), (3, 115)],
        "hihat": [(i * 0.5, 90) for i in range(8)],
    },
}

# Drum MIDI note numbers (GM)
DRUM_MIDI = {
    "kick": 36, "snare": 38, "hihat": 42, "open_hihat": 46,
    "clap": 39, "rim": 37, "crash": 49, "ride": 51,
    "low_tom": 41, "mid_tom": 47, "high_tom": 50,
}

# Genre -> default scale type for melodies
GENRE_SCALES = {
    "pop": "major",
    "lofi": "dorian",
    "hiphop": "pentatonic_minor",
    "rnb": "dorian",
    "rock": "pentatonic_minor",
}

# Genre -> whether progressions use minor key degrees
GENRE_MINOR = {
    "pop": False,
    "lofi": False,
    "hiphop": True,
    "rnb": False,
    "rock": False,
}


# --- Helpers ---

def _note_to_midi(note_name: str, octave: int) -> int:
    """Convert note name + octave to MIDI pitch. C4 = 60."""
    return NOTE_MAP[note_name] + (octave + 1) * 12


def _root_from_key(key: str) -> int:
    """Get semitone value (0-11) from key string like 'C', 'F#', 'Bb'."""
    return NOTE_MAP[key]


def _degree_to_semitones(degree: int, minor: bool = False) -> int:
    """Convert scale degree (1-7) to semitones from root."""
    table = MINOR_DEGREES if minor else MAJOR_DEGREES
    return table[degree]


def _build_chord_pitches(root_midi: int, quality: str) -> list[int]:
    """Build chord MIDI pitches from root and quality."""
    intervals = CHORD_TYPES.get(quality, CHORD_TYPES["maj"])
    return [root_midi + i for i in intervals]


def _voice_lead(prev_pitches: list[int], next_root: int, next_quality: str) -> list[int]:
    """Voice lead next chord to minimize movement from previous chord."""
    intervals = CHORD_TYPES.get(next_quality, CHORD_TYPES["maj"])
    # Try all inversions and pick the one closest to prev center
    if not prev_pitches:
        return [next_root + i for i in intervals]

    prev_center = sum(prev_pitches) / len(prev_pitches)
    best = None
    best_dist = float("inf")

    for inv in range(len(intervals)):
        # Rotate intervals for inversion
        rotated = intervals[inv:] + tuple(i + 12 for i in intervals[:inv])
        # Try placing root at different octaves near prev_center
        for octave_shift in [-12, 0, 12]:
            pitches = [next_root + octave_shift + i for i in rotated]
            center = sum(pitches) / len(pitches)
            dist = abs(center - prev_center)
            # Ensure pitches are in playable range (48-84 for chords)
            if all(48 <= p <= 84 for p in pitches) and dist < best_dist:
                best_dist = dist
                best = pitches

    return best or [next_root + i for i in intervals]


def _humanize_velocity(base: int, prev_vel: int = None) -> int:
    """Vary velocity, ensuring it differs from previous."""
    offset = random.randint(5, 15) * random.choice([-1, 1])
    vel = max(25, min(127, base + offset))
    if prev_vel is not None and vel == prev_vel:
        vel = max(25, min(127, vel + random.choice([-7, 7])))
    return vel


def _humanize_timing(beat: float, amount: float = 0.03) -> float:
    """Add micro-timing offset, clamped to >= 0."""
    return max(0.0, beat + random.uniform(-amount, amount))


def _get_scale_pitches(root_semitone: int, scale_name: str, octave: int, num_octaves: int = 2) -> list[int]:
    """Get all scale pitches across octave range."""
    intervals = SCALE_TYPES.get(scale_name, SCALE_TYPES["major"])
    base = root_semitone + (octave + 1) * 12
    pitches = []
    for oct in range(num_octaves):
        for i in intervals:
            pitches.append(base + oct * 12 + i)
    return pitches


# --- Main Tool Functions ---

def get_chord_progression(genre: str, key: str, bars: int) -> dict:
    """Generate a chord progression with MIDI note data for a genre, key, and number of bars.

    Returns chord voicings as MIDI notes ready for add_midi_notes_batch_beats.
    Uses common progressions for the genre with proper voice leading between chords.

    Args:
        genre: Music genre (pop, lofi, hiphop, rnb, rock).
        key: Musical key root note (C, C#, D, Eb, E, F, F#, G, Ab, A, Bb, B).
        bars: Total number of bars to generate.

    Returns:
        Object with progression name and notes list for add_midi_notes_batch_beats.
    """
    genre = genre.lower().replace("-", "").replace(" ", "")
    if genre == "lo-fi" or genre == "lo_fi":
        genre = "lofi"
    if genre == "hip-hop" or genre == "hip_hop" or genre == "trap":
        genre = "hiphop"

    progs = PROGRESSIONS.get(genre, PROGRESSIONS["pop"])
    prog = random.choice(progs)
    root_semitone = _root_from_key(key)
    is_minor = GENRE_MINOR.get(genre, False)

    beats_per_bar = 4
    chords_in_prog = len(prog)
    # Repeat progression to fill bars
    total_chord_slots = bars
    bars_per_chord = max(1, bars // chords_in_prog)

    notes = []
    prev_pitches = []
    chord_names = []

    for bar in range(bars):
        chord_idx = (bar // bars_per_chord) % chords_in_prog
        degree, quality = prog[chord_idx]
        chord_root = root_semitone + _degree_to_semitones(degree, is_minor)

        # Place chord root in octave 4 range (MIDI 60-72)
        chord_root_midi = 60 + (chord_root % 12)

        if prev_pitches:
            pitches = _voice_lead(prev_pitches, chord_root_midi, quality)
        else:
            pitches = _build_chord_pitches(chord_root_midi, quality)
            # Ensure in range
            while any(p > 84 for p in pitches):
                pitches = [p - 12 for p in pitches]
            while any(p < 48 for p in pitches):
                pitches = [p + 12 for p in pitches]

        prev_pitches = pitches
        beat_start = bar * beats_per_bar
        prev_vel = None

        for i, pitch in enumerate(pitches):
            vel = _humanize_velocity(85, prev_vel)
            prev_vel = vel
            # Stagger chord notes slightly for strummed feel
            stagger = i * random.uniform(0.01, 0.03)
            notes.append({
                "pitch": pitch,
                "start_beat": round(_humanize_timing(beat_start, 0.02) + stagger, 4),
                "length_beats": round(beats_per_bar - 0.1, 4),
                "velocity": vel,
                "channel": 0,
            })

        # Build chord name for description
        degree_names = {0: "C", 1: "C#", 2: "D", 3: "Eb", 4: "E", 5: "F",
                        6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}
        chord_note_name = degree_names[chord_root % 12]
        chord_names.append(f"{chord_note_name}{quality}")

    # Deduplicate consecutive chord names for description
    unique_prog = []
    for name in chord_names[:chords_in_prog]:
        unique_prog.append(name)

    return {
        "ok": True,
        "progression": " | ".join(unique_prog),
        "total_notes": len(notes),
        "notes": notes,
    }


def get_bass_line(key: str, genre: str, bars: int) -> dict:
    """Generate a bass line that follows the chord progression for a genre.

    Creates rhythmically varied bass notes in the low octave range,
    matching the chord roots with passing tones and approach notes.

    Args:
        key: Musical key root note (C, C#, D, Eb, E, F, F#, G, Ab, A, Bb, B).
        genre: Music genre (pop, lofi, hiphop, rnb, rock).
        bars: Total number of bars to generate.

    Returns:
        Object with notes list for add_midi_notes_batch_beats.
    """
    genre = genre.lower().replace("-", "").replace(" ", "")
    if genre in ("lo-fi", "lo_fi", "lofi"):
        genre = "lofi"
    if genre in ("hip-hop", "hip_hop", "trap"):
        genre = "hiphop"

    progs = PROGRESSIONS.get(genre, PROGRESSIONS["pop"])
    prog = random.choice(progs)
    root_semitone = _root_from_key(key)
    is_minor = GENRE_MINOR.get(genre, False)

    beats_per_bar = 4
    bars_per_chord = max(1, bars // len(prog))
    chords_in_prog = len(prog)

    notes = []
    prev_vel = None

    # Genre-specific bass rhythm patterns (beat offsets within a bar, note lengths)
    if genre == "hiphop":
        patterns = [
            [(0, 1.5), (2, 1.0), (3.25, 0.5)],
            [(0, 1.0), (1.5, 0.75), (2.5, 1.0)],
        ]
    elif genre == "lofi":
        patterns = [
            [(0, 1.5), (2, 1.5)],
            [(0, 1.0), (1.5, 0.5), (2, 1.5)],
            [(0, 2.0), (2.5, 1.0)],
        ]
    elif genre == "rnb":
        patterns = [
            [(0, 1.0), (1.5, 0.75), (2.5, 1.0)],
            [(0, 1.5), (2, 0.75), (3, 0.75)],
        ]
    else:  # pop, rock
        patterns = [
            [(0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0)],
            [(0, 1.5), (2, 1.5)],
            [(0, 1.0), (1, 0.5), (2, 1.0), (3, 0.5)],
        ]

    for bar in range(bars):
        chord_idx = (bar // bars_per_chord) % chords_in_prog
        degree, quality = prog[chord_idx]
        chord_root = root_semitone + _degree_to_semitones(degree, is_minor)

        # Bass in octave 2 (MIDI 36-47)
        bass_root = 36 + (chord_root % 12)
        fifth = bass_root + 7
        if fifth > 48:
            fifth -= 12

        beat_start = bar * beats_per_bar
        pattern = random.choice(patterns)

        for i, (offset, length) in enumerate(pattern):
            # Use root primarily, fifth on some passing notes
            if i == 0:
                pitch = bass_root
            elif i == len(pattern) - 1 and random.random() < 0.3:
                # Approach note: one semitone below next chord root
                next_chord_idx = ((bar + 1) // bars_per_chord) % chords_in_prog
                next_degree, _ = prog[next_chord_idx]
                next_root = root_semitone + _degree_to_semitones(next_degree, is_minor)
                next_bass = 36 + (next_root % 12)
                pitch = next_bass - 1  # chromatic approach from below
            elif random.random() < 0.4:
                pitch = fifth
            else:
                pitch = bass_root

            vel = _humanize_velocity(90 if offset == 0 else 78, prev_vel)
            prev_vel = vel

            notes.append({
                "pitch": pitch,
                "start_beat": round(_humanize_timing(beat_start + offset, 0.02), 4),
                "length_beats": round(length, 4),
                "velocity": vel,
                "channel": 0,
            })

    return {
        "ok": True,
        "total_notes": len(notes),
        "notes": notes,
    }


def get_drum_pattern(genre: str, bars: int) -> dict:
    """Generate a humanized drum pattern for a genre with fills at section transitions.

    Includes kick, snare, hi-hats with ghost notes, velocity variation,
    and micro-timing. Adds drum fills at every 4th bar.

    Args:
        genre: Music genre (pop, lofi, hiphop, rnb, rock).
        bars: Total number of bars to generate.

    Returns:
        Object with notes list for add_midi_notes_batch_beats (channel 9 for GM drums).
    """
    genre = genre.lower().replace("-", "").replace(" ", "")
    if genre in ("lo-fi", "lo_fi", "lofi"):
        genre = "lofi"
    if genre in ("hip-hop", "hip_hop", "trap"):
        genre = "hiphop"

    pattern = DRUM_PATTERNS.get(genre, DRUM_PATTERNS["pop"])
    notes = []
    prev_vel = {"kick": None, "snare": None, "hihat": None}

    for bar in range(bars):
        beat_start = bar * 4
        is_fill_bar = (bar + 1) % 4 == 0  # Fill on bars 4, 8, 12, 16...
        is_intro = bar < 2  # Sparse intro

        # --- Kick ---
        if not (is_intro and random.random() < 0.3):
            for offset, vel_base in pattern["kick"]:
                # Skip some kicks in fill bars for variation
                if is_fill_bar and offset >= 2 and random.random() < 0.4:
                    continue
                vel = _humanize_velocity(vel_base, prev_vel["kick"])
                prev_vel["kick"] = vel
                timing = _humanize_timing(beat_start + offset, 0.03)
                notes.append({
                    "pitch": DRUM_MIDI["kick"],
                    "start_beat": round(timing, 4),
                    "length_beats": 0.25,
                    "velocity": vel,
                    "channel": 9,
                })

        # --- Snare ---
        for offset, vel_base in pattern["snare"]:
            vel = _humanize_velocity(vel_base, prev_vel["snare"])
            prev_vel["snare"] = vel
            notes.append({
                "pitch": DRUM_MIDI["snare"],
                "start_beat": round(_humanize_timing(beat_start + offset, 0.02), 4),
                "length_beats": 0.25,
                "velocity": vel,
                "channel": 9,
            })
            # Ghost notes
            if random.random() < 0.35:
                ghost_offset = offset + random.choice([0.25, 0.75, -0.25])
                if 0 <= ghost_offset < 4:
                    ghost_vel = random.randint(30, 50)
                    notes.append({
                        "pitch": DRUM_MIDI["snare"],
                        "start_beat": round(beat_start + ghost_offset, 4),
                        "length_beats": 0.15,
                        "velocity": ghost_vel,
                        "channel": 9,
                    })

        # --- Hi-hats ---
        if not is_intro or bar == 1:
            for i, (offset, vel_base) in enumerate(pattern["hihat"]):
                # Swing: push every other hit forward
                swing = 0.0
                if i % 2 == 1:
                    swing = random.uniform(0.05, 0.15)

                # Occasional open hihat
                if random.random() < 0.08:
                    pitch = DRUM_MIDI["open_hihat"]
                    vel_base = vel_base + 10
                else:
                    pitch = DRUM_MIDI["hihat"]

                vel = _humanize_velocity(vel_base, prev_vel["hihat"])
                prev_vel["hihat"] = vel
                timing = beat_start + offset + swing
                notes.append({
                    "pitch": pitch,
                    "start_beat": round(_humanize_timing(timing, 0.01), 4),
                    "length_beats": 0.2,
                    "velocity": vel,
                    "channel": 9,
                })

        # --- Drum fill on last beat of fill bars ---
        if is_fill_bar:
            fill_type = random.choice(["tom_descend", "snare_roll", "buildup"])
            fill_start = beat_start + 3  # Last beat of bar

            if fill_type == "tom_descend":
                toms = [DRUM_MIDI["high_tom"], DRUM_MIDI["mid_tom"], DRUM_MIDI["low_tom"]]
                for j, tom in enumerate(toms):
                    notes.append({
                        "pitch": tom,
                        "start_beat": round(fill_start + j * 0.25, 4),
                        "length_beats": 0.2,
                        "velocity": random.randint(95, 115),
                        "channel": 9,
                    })
                # Crash on next bar's beat 1
                notes.append({
                    "pitch": DRUM_MIDI["crash"],
                    "start_beat": round(beat_start + 4, 4),
                    "length_beats": 1.0,
                    "velocity": random.randint(100, 120),
                    "channel": 9,
                })
            elif fill_type == "snare_roll":
                for j in range(4):
                    notes.append({
                        "pitch": DRUM_MIDI["snare"],
                        "start_beat": round(fill_start + j * 0.25, 4),
                        "length_beats": 0.15,
                        "velocity": random.randint(70, 100),
                        "channel": 9,
                    })
            elif fill_type == "buildup":
                for j in range(6):
                    notes.append({
                        "pitch": DRUM_MIDI["snare"] if j % 2 == 0 else DRUM_MIDI["high_tom"],
                        "start_beat": round(fill_start + j * (1.0 / 6), 4),
                        "length_beats": 0.12,
                        "velocity": min(127, 70 + j * 10),
                        "channel": 9,
                    })

    return {
        "ok": True,
        "total_notes": len(notes),
        "notes": notes,
    }


def get_melody(key: str, genre: str, bars: int, density: str = "medium") -> dict:
    """Generate a melody line that fits the chord progression and scale.

    Uses chord tones on strong beats and scale passing tones on weak beats.
    Melody contour follows natural phrasing with varied rhythms.

    Args:
        key: Musical key root note (C, C#, D, Eb, E, F, F#, G, Ab, A, Bb, B).
        genre: Music genre (pop, lofi, hiphop, rnb, rock).
        bars: Total number of bars to generate.
        density: Note density - sparse, medium, or dense.

    Returns:
        Object with notes list for add_midi_notes_batch_beats.
    """
    genre_clean = genre.lower().replace("-", "").replace(" ", "")
    if genre_clean in ("lo-fi", "lo_fi", "lofi"):
        genre_clean = "lofi"
    if genre_clean in ("hip-hop", "hip_hop", "trap"):
        genre_clean = "hiphop"

    progs = PROGRESSIONS.get(genre_clean, PROGRESSIONS["pop"])
    prog = random.choice(progs)
    root_semitone = _root_from_key(key)
    is_minor = GENRE_MINOR.get(genre_clean, False)
    scale_name = GENRE_SCALES.get(genre_clean, "major")

    # Get scale pitches in melody range (octave 5, MIDI 72-84)
    scale_pitches = _get_scale_pitches(root_semitone, scale_name, 4, 2)
    # Filter to comfortable melody range
    scale_pitches = [p for p in scale_pitches if 60 <= p <= 84]

    beats_per_bar = 4
    bars_per_chord = max(1, bars // len(prog))

    # Density controls notes per bar
    if density == "sparse":
        notes_per_bar_range = (1, 3)
        length_choices = [1.0, 1.5, 2.0]
    elif density == "dense":
        notes_per_bar_range = (4, 7)
        length_choices = [0.25, 0.5, 0.75]
    else:  # medium
        notes_per_bar_range = (2, 5)
        length_choices = [0.5, 0.75, 1.0, 1.5]

    notes = []
    prev_pitch = random.choice(scale_pitches[len(scale_pitches)//3 : 2*len(scale_pitches)//3])
    prev_vel = None

    for bar in range(bars):
        chord_idx = (bar // bars_per_chord) % len(prog)
        degree, quality = prog[chord_idx]
        chord_root = root_semitone + _degree_to_semitones(degree, is_minor)
        chord_root_midi = 60 + (chord_root % 12)
        chord_pitches = _build_chord_pitches(chord_root_midi, quality)
        # Also include octave above
        chord_pitches_extended = chord_pitches + [p + 12 for p in chord_pitches]
        chord_pitches_in_range = [p for p in chord_pitches_extended if 60 <= p <= 84]

        beat_start = bar * beats_per_bar
        num_notes = random.randint(*notes_per_bar_range)

        # Intro bars: sparse
        if bar < 2:
            num_notes = max(1, num_notes - 1)

        current_beat = 0.0
        for n in range(num_notes):
            if current_beat >= beats_per_bar:
                break

            # Strong beats (0, 2): prefer chord tones
            is_strong = current_beat % 2 < 0.5
            if is_strong and chord_pitches_in_range:
                # Pick chord tone nearest to prev_pitch
                pitch = min(chord_pitches_in_range, key=lambda p: abs(p - prev_pitch))
            else:
                # Scale tone, step-wise motion from prev_pitch
                nearby = [p for p in scale_pitches if abs(p - prev_pitch) <= 4]
                if nearby:
                    pitch = random.choice(nearby)
                else:
                    pitch = min(scale_pitches, key=lambda p: abs(p - prev_pitch))

            # Occasionally leap (for interest)
            if random.random() < 0.15 and chord_pitches_in_range:
                pitch = random.choice(chord_pitches_in_range)

            length = random.choice(length_choices)
            # Don't overflow bar
            length = min(length, beats_per_bar - current_beat)

            vel_base = 90 if is_strong else 75
            vel = _humanize_velocity(vel_base, prev_vel)
            prev_vel = vel

            notes.append({
                "pitch": pitch,
                "start_beat": round(_humanize_timing(beat_start + current_beat, 0.02), 4),
                "length_beats": round(length, 4),
                "velocity": vel,
                "channel": 0,
            })

            prev_pitch = pitch
            current_beat += length
            # Small gap between notes
            current_beat += random.choice([0, 0, 0.25])

    return {
        "ok": True,
        "total_notes": len(notes),
        "notes": notes,
    }


# Registry for app.py to discover these tools
MUSIC_TOOLS = {
    "get_chord_progression": get_chord_progression,
    "get_bass_line": get_bass_line,
    "get_drum_pattern": get_drum_pattern,
    "get_melody": get_melody,
}
