from pydub import AudioSegment
from pathlib import Path
from mutagen.aiff import AIFF
from mutagen.wave import WAVE
from mutagen.flac import FLAC
from mutagen.id3 import TIT2, TCON, TBPM, TKEY, TPUB


SUPPORTED_FORMATS = {".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a", ".mp4"}

# Map output format key → file extension
FORMAT_EXTENSION = {
    "aif":  ".aif",
    "wav":  ".wav",
    "flac": ".flac",
}


def convert_audio(input_path: str, output_path: str,
                  out_format: str = "aif",
                  metadata: dict | None = None) -> tuple[bool, str]:
    """
    Convert any supported audio file to the chosen output format and write tags.
    out_format: 'aif', 'wav', or 'flac'
    Returns (success, error_message).
    """
    try:
        src = Path(input_path)
        dst = Path(output_path)

        in_fmt = src.suffix.lower().lstrip(".")
        if in_fmt in ("aif", "aiff"):
            in_fmt = "aiff"

        pydub_fmt = {"aif": "aiff", "wav": "wav", "flac": "flac"}.get(out_format, "aiff")

        audio = AudioSegment.from_file(str(src), format=in_fmt)
        dst.parent.mkdir(parents=True, exist_ok=True)
        audio.export(str(dst), format=pydub_fmt)

        if metadata:
            _write_tags(str(dst), out_format, metadata)

        return True, ""
    except Exception as e:
        return False, str(e)


# Keep the old name as an alias so nothing else breaks
def convert_to_aiff(input_path: str, output_path: str,
                    metadata: dict | None = None) -> tuple[bool, str]:
    return convert_audio(input_path, output_path, out_format="aif", metadata=metadata)


def retag_file(path: str, metadata: dict) -> tuple[bool, str]:
    """
    Rewrite tags on an already-converted file without re-converting audio.
    Format is inferred from the file extension.
    Returns (success, error_message).
    """
    try:
        p = Path(path)
        if not p.exists():
            return False, f"File not found: {path}"
        ext = p.suffix.lower()
        fmt = {".aif": "aif", ".aiff": "aif", ".wav": "wav", ".flac": "flac"}.get(ext, "aif")
        _write_tags(path, fmt, metadata)
        return True, ""
    except Exception as e:
        return False, str(e)


# Backward-compat alias
def retag_aif_file(path: str, metadata: dict) -> tuple[bool, str]:
    return retag_file(path, metadata)


# ── Tag writers ───────────────────────────────────────────────────────────────

def _write_tags(path: str, fmt: str, metadata: dict):
    if fmt == "flac":
        _write_flac_tags(path, metadata)
    elif fmt == "wav":
        _write_wav_tags(path, metadata)
    else:
        _write_aiff_tags(path, metadata)


def _write_aiff_tags(path: str, metadata: dict):
    """Embed ID3 tags into an AIFF file."""
    try:
        audio = AIFF(path)
        if audio.tags is None:
            audio.add_tags()
        else:
            audio.tags.clear()
        _populate_id3(audio.tags, metadata)
        audio.save()
    except Exception as e:
        print(f"ID3 tag write error: {e}")


def _write_wav_tags(path: str, metadata: dict):
    """Embed ID3 tags into a WAV/RIFF file."""
    try:
        audio = WAVE(path)
        if audio.tags is None:
            audio.add_tags()
        else:
            audio.tags.clear()
        _populate_id3(audio.tags, metadata)
        audio.save()
    except Exception as e:
        print(f"WAV ID3 tag write error: {e}")


def _write_flac_tags(path: str, metadata: dict):
    """Write Vorbis comment tags into a FLAC file."""
    try:
        audio = FLAC(path)
        audio.clear()   # remove existing Vorbis comments

        if metadata.get("name"):
            audio["title"] = [metadata["name"]]
        if metadata.get("bpm") and int(metadata["bpm"]) > 0:
            audio["bpm"] = [str(int(metadata["bpm"]))]
        if metadata.get("key"):
            audio["key"] = [metadata["key"]]
        if metadata.get("label"):
            audio["organization"] = [metadata["label"]]

        # Each tag becomes a separate GENRE entry — most players show all of them
        if metadata.get("tags"):
            tag_list = [t.strip() for t in metadata["tags"].split(",") if t.strip()]
            if tag_list:
                audio["genre"] = tag_list

        audio.save()
    except Exception as e:
        print(f"FLAC tag write error: {e}")


def _populate_id3(tags, metadata: dict):
    """Fill an ID3 tag object (AIFF or WAV) with metadata fields."""
    if metadata.get("name"):
        tags.add(TIT2(encoding=3, text=metadata["name"]))
    if metadata.get("bpm") and int(metadata["bpm"]) > 0:
        tags.add(TBPM(encoding=3, text=str(int(metadata["bpm"]))))
    if metadata.get("key"):
        tags.add(TKEY(encoding=3, text=metadata["key"]))
    if metadata.get("label"):
        tags.add(TPUB(encoding=3, text=metadata["label"]))

    # Custom tags → TCON so Bitwig and other DAWs can read them
    if metadata.get("tags"):
        tcon_values = [t.strip() for t in metadata["tags"].split(",") if t.strip()]
        if tcon_values:
            tags.add(TCON(encoding=3, text=tcon_values))
