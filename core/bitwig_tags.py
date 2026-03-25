"""
Utilities to register custom tag strings with Bitwig Studio's internal index.

How Bitwig tag discovery works:
  1. When Bitwig scans audio files it tokenises each filename (split on _, -, ., etc.)
  2. Each token is looked up in file-name-words.ids (length-prefixed string list)
     and stored in the sample index as a word-ID.
  3. Tags shown in the browser are tokens whose string also appears in tags.ids.

So to make a custom tag browsable in Bitwig we need to:
  a. Append the tag string to tags.ids (if not already present)
  b. Append the tag string to file-name-words.ids (if not already present)
  c. Make sure the tag string appears as a separate token in the exported filename.

Call register_tags() once; Bitwig will pick up the new tags on the next library rescan.
"""

import os
import struct
import sys
from pathlib import Path


def _find_bitwig_index_dir() -> Path:
    """
    Return Bitwig's index directory for the current OS.

    Bitwig always writes its index to the OS-standard user-data location,
    regardless of where Bitwig itself is installed:
      macOS   ~/Library/Application Support/Bitwig/Bitwig Studio/index
      Windows %APPDATA%\\Bitwig Studio\\index
      Linux   ~/.BitwigStudio/index
    """
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Bitwig/Bitwig Studio/index"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming")
        return Path(appdata) / "Bitwig Studio/index"
    # Linux / other
    return Path.home() / ".BitwigStudio/index"


_BITWIG_INDEX_DIR = _find_bitwig_index_dir()


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _parse_ids(data: bytes) -> list[str]:
    """
    Parse a Bitwig .ids file into a list of strings (sequential IDs).

    Bitwig uses two length-prefix encodings in the same file:
      - High bit clear: length is byte count, string is UTF-8
      - High bit set:   lower 31 bits are UTF-16 code-unit count, string is UTF-16 BE
    """
    items: list[str] = []
    offset = 0
    while offset + 4 <= len(data):
        (raw_len,) = struct.unpack_from(">I", data, offset)
        offset += 4
        if raw_len == 0:
            break
        is_utf16 = bool(raw_len & 0x80000000)
        length = raw_len & 0x7FFFFFFF
        byte_len = length * 2 if is_utf16 else length
        if byte_len == 0 or byte_len > 2000 or offset + byte_len > len(data):
            break
        raw = data[offset: offset + byte_len]
        text = raw.decode("utf-16-be" if is_utf16 else "utf-8", errors="replace")
        items.append(text)
        offset += byte_len
    return items


def _encode_entry(s: str) -> bytes:
    """Encode a single string as a length-prefixed entry."""
    encoded = s.encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


# ── Public API ────────────────────────────────────────────────────────────────

def get_bitwig_index_dir() -> Path | None:
    """Return the Bitwig index directory path if it exists, else None."""
    return _BITWIG_INDEX_DIR if _BITWIG_INDEX_DIR.exists() else None


def get_registered_tags() -> list[str]:
    """Return all tag strings currently registered in Bitwig's tags.ids."""
    path = _BITWIG_INDEX_DIR / "tags.ids"
    if not path.exists():
        return []
    return _parse_ids(path.read_bytes())


# Hardcoded fallback: Bitwig's built-in sample-browser tags (positions 0-206).
# These are the ONLY tags that work for audio file filtering in Bitwig's browser.
_BUILTIN_TAGS_FALLBACK = [
    "metallic", "analog", "soft", "909", "noisy", "mod", "harmonic", "bright",
    "digital", "slow", "acoustic", "electric", "clean", "poly", "dark", "808",
    "chord", "hard", "fx", "rhythmic", "mono", "707", "layered", "dirty",
    "fast", "detuned", "linn", "wet", "glide", "quirky", "wonky", "dubby",
    "organic", "heavy", "epic", "granular", "deep", "loop", "atmo", "broken",
    "sparse", "melodic", "trap", "dubstep", "machine", "dnb", "modern",
    "smooth", "moody", "crash", "ride", "hat", "shaker", "hh", "bass",
    "bassdrum", "techno", "vinyl", "metal", "rim", "spectral", "drill",
    "lofi", "laser", "resonant", "halftime", "dry", "piano", "one-shot",
    "growl", "sub", "acid", "trance", "filter", "ensemble", "orchestral",
    "nostalgic", "arp", "sequence", "stereo", "sweep", "pluck", "tube",
    "dreamy", "happy", "keys", "synth", "electro", "drums", "additive",
    "303", "glitch", "dub", "guitar", "reggae", "delay", "alien", "ambience",
    "atmospheric", "drone", "spacey", "sidechain", "reverb", "space",
    "compressor", "hyper", "ott", "effect", "instrument", "scale",
    "generative", "random", "vocal", "tape", "distortion", "saturation",
    "waveshaper", "expressive", "drumandbass", "neurofunk", "deephouse",
    "house", "edm", "vocoder", "room", "hall", "plate", "synthetic",
    "nonlinear", "spring", "osc", "pad", "basic", "automation",
]


def get_builtin_tags() -> list[str]:
    """
    Return Bitwig's built-in tag vocabulary (positions 0–206 in tags.ids).
    These are the only tags that work for audio file filtering in the browser.
    Falls back to a hardcoded list if tags.ids is unavailable.
    """
    path = _BITWIG_INDEX_DIR / "tags.ids"
    if path.exists():
        all_tags = _parse_ids(path.read_bytes())
        # Position 207 is 'customtag' — the boundary marker.  Everything before
        # it is the built-in sample-browser vocabulary.
        boundary = next(
            (i for i, t in enumerate(all_tags) if t == "customtag"),
            207,
        )
        return all_tags[:boundary]
    return list(_BUILTIN_TAGS_FALLBACK)


def _find_utf16_section_offset(data: bytes) -> int:
    """
    Return the byte offset where the first UTF-16 encoded entry begins,
    or len(data) if there are none.  New ASCII entries should be inserted
    here so they land in the ASCII section that Bitwig's sample-browser
    parser can read (it bails out when it encounters a UTF-16 high-bit marker).
    """
    offset = 0
    while offset + 4 <= len(data):
        (raw_len,) = struct.unpack_from(">I", data, offset)
        if raw_len == 0:
            break
        is_utf16 = bool(raw_len & 0x80000000)
        if is_utf16:
            return offset          # first UTF-16 entry starts here
        length = raw_len & 0x7FFFFFFF
        if length == 0 or length > 2000 or offset + 4 + length > len(data):
            break
        offset += 4 + length
    return len(data)               # no UTF-16 entries; append at end


def register_tags(tag_names: list[str]) -> tuple[list[str], str]:
    """
    Register tag strings with Bitwig's internal index so they appear as
    browsable tags in the browser (after Bitwig rescans the library).

    New entries are inserted *before* any UTF-16 encoded entries so that
    Bitwig's sample-browser ASCII parser can see them.

    Returns (newly_registered_names, error_message).
    error_message is "" on success.
    """
    tags_path  = _BITWIG_INDEX_DIR / "tags.ids"
    words_path = _BITWIG_INDEX_DIR / "file-name-words.ids"

    if not tags_path.exists() or not words_path.exists():
        return [], "Bitwig index directory not found. Is Bitwig installed?"

    try:
        tags_data  = tags_path.read_bytes()
        words_data = words_path.read_bytes()

        existing_tags  = {t.lower() for t in _parse_ids(tags_data)}
        existing_words = {w.lower() for w in _parse_ids(words_data)}

        newly_added: list[str] = []
        new_tag_bytes   = b""
        new_word_bytes  = b""

        for raw in tag_names:
            name = raw.strip().lower()
            if not name:
                continue
            if name not in existing_tags:
                new_tag_bytes += _encode_entry(name)
                existing_tags.add(name)
                newly_added.append(name)
            if name not in existing_words:
                new_word_bytes += _encode_entry(name)
                existing_words.add(name)

        if newly_added:
            # Insert before the UTF-16 section so the ASCII-only sample-browser
            # parser in Bitwig can read the new entries.
            tags_insert  = _find_utf16_section_offset(tags_data)
            words_insert = _find_utf16_section_offset(words_data)

            tags_data  = tags_data[:tags_insert]  + new_tag_bytes  + tags_data[tags_insert:]
            words_data = words_data[:words_insert] + new_word_bytes + words_data[words_insert:]

            # Write atomically: .tmp then rename
            tmp_tags  = tags_path.with_suffix(".ids.tmp")
            tmp_words = words_path.with_suffix(".ids.tmp")
            tmp_tags.write_bytes(tags_data)
            tmp_words.write_bytes(words_data)
            tmp_tags.replace(tags_path)
            tmp_words.replace(words_path)

        return newly_added, ""

    except Exception as exc:
        return [], str(exc)


def tags_in_bitwig(tag_names: list[str]) -> dict[str, bool]:
    """
    Check which of the given tag names are already registered in tags.ids.
    Returns {tag_name: is_registered}.
    """
    registered = {t.lower() for t in get_registered_tags()}
    return {t: t.lower() in registered for t in tag_names}
