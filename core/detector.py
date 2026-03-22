"""
core/detector.py — Auto-detection of BPM and musical key.

Priority order for each file:
  1. Read from source file's existing metadata tags (fast, mutagen)
  2. Parse from the filename (fast, regex)
  3. Audio analysis via librosa (slow, runs in background thread)
"""

import re
import numpy as np
from pathlib import Path


# ── Krumhansl-Schmuckler key profiles ─────────────────────────────────────────

_NOTE_NAMES   = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

# Regex patterns compiled once
_BPM_EXPLICIT  = re.compile(r'(?:bpm[\s_\-]?(\d{2,3})|(\d{2,3})[\s_\-]?bpm)', re.IGNORECASE)
_BPM_IMPLICIT  = re.compile(r'(?:^|[_\-\s])(\d{2,3})(?:[_\-\s]|$)')
# Key: note + optional accidental + optional mode suffix (m/min/minor/maj/major)
# Negative lookahead avoids matching "mod", "metal", "mix", etc.
_KEY_PATTERN   = re.compile(
    r'(?:^|[_\-\s])([A-G][#b]?(?:maj(?:or)?|min(?:or)?|m(?!od|et|ix|p))?)(?=[_\-\s]|$)',
    re.IGNORECASE,
)


# ── Source-file metadata ───────────────────────────────────────────────────────

def read_source_tags(path: str) -> dict:
    """
    Read BPM and key from an audio file's existing metadata (ID3, Vorbis, etc.).
    Returns a dict with 'bpm' (int) and/or 'key' (str) if found.
    """
    result: dict = {}
    try:
        from mutagen import File as MuFile
        audio = MuFile(path)
        if audio is None or audio.tags is None:
            return result
        tags = audio.tags

        # BPM — try several common tag keys
        for k in ('TBPM', 'bpm', 'BPM', 'TEMPO', 'tempo'):
            v = tags.get(k)
            if v:
                try:
                    raw = str(v[0] if hasattr(v, '__getitem__') else v).strip()
                    bpm = int(round(float(raw)))
                    if 40 <= bpm <= 300:
                        result['bpm'] = bpm
                        break
                except (ValueError, TypeError):
                    pass

        # Key — try several common tag keys
        for k in ('TKEY', 'initialkey', 'key', 'KEY', 'INITIALKEY'):
            v = tags.get(k)
            if v:
                raw = str(v[0] if hasattr(v, '__getitem__') else v).strip()
                if raw:
                    result['key'] = raw
                    break

    except Exception as e:
        print(f"[detector] source tag read error: {e}")
    return result


# ── Filename parsing ───────────────────────────────────────────────────────────

def parse_filename_tags(filename: str) -> dict:
    """
    Extract BPM and key from a filename using regex heuristics.
    Returns a dict with 'bpm' (int) and/or 'key' (str) if found.
    """
    result: dict = {}
    stem = Path(filename).stem

    # BPM — explicit label first (e.g. 120bpm, bpm120)
    m = _BPM_EXPLICIT.search(stem)
    if m:
        val = int(m.group(1) or m.group(2))
        if 40 <= val <= 250:
            result['bpm'] = val

    # BPM — fall back to standalone 2-3 digit number in typical BPM range
    if 'bpm' not in result:
        for m in _BPM_IMPLICIT.finditer(stem):
            val = int(m.group(1))
            if 60 <= val <= 200:
                result['bpm'] = val
                break

    # Key
    m = _KEY_PATTERN.search(stem)
    if m:
        result['key'] = m.group(1)

    return result


# ── Audio analysis (librosa) ───────────────────────────────────────────────────

def detect_bpm(path: str) -> int | None:
    """
    Estimate BPM via librosa beat tracking (analyses up to 60 s of audio).
    Returns an int, or None on failure.
    """
    try:
        import librosa
        y, sr = librosa.load(path, sr=None, mono=True, duration=60)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = int(round(float(np.atleast_1d(tempo)[0])))
        if 40 <= bpm <= 250:
            return bpm
    except Exception as e:
        print(f"[detector] BPM analysis error: {e}")
    return None


def detect_key(path: str) -> str | None:
    """
    Estimate musical key via chroma CQT + Krumhansl-Schmuckler algorithm
    (analyses up to 30 s of audio).
    Returns a string like 'Am' or 'Cmaj', or None on failure.
    """
    try:
        import librosa
        y, sr = librosa.load(path, sr=None, mono=True, duration=30)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = chroma.mean(axis=1)

        best_score = -np.inf
        best_key: str | None = None

        for i in range(12):
            maj   = _MAJOR_PROFILE[i:] + _MAJOR_PROFILE[:i]
            minor = _MINOR_PROFILE[i:] + _MINOR_PROFILE[:i]

            score_maj = float(np.corrcoef(chroma_mean, maj)[0, 1])
            score_min = float(np.corrcoef(chroma_mean, minor)[0, 1])

            if score_maj > best_score:
                best_score = score_maj
                best_key   = _NOTE_NAMES[i]          # e.g. 'C' (major implied)
            if score_min > best_score:
                best_score = score_min
                best_key   = _NOTE_NAMES[i] + 'm'    # e.g. 'Am'

        return best_key
    except Exception as e:
        print(f"[detector] Key analysis error: {e}")
    return None
