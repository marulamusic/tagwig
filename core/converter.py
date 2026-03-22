from pydub import AudioSegment
from pathlib import Path
from mutagen.aiff import AIFF
from mutagen.id3 import TIT2, TCON, TBPM, TKEY, TXXX, TPUB


SUPPORTED_FORMATS = {".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a", ".mp4"}


def convert_to_aiff(input_path: str, output_path: str, metadata: dict | None = None) -> tuple[bool, str]:
    """
    Convert any supported audio file to AIFF and write ID3 tags.
    Returns (success, error_message).
    """
    try:
        src = Path(input_path)
        dst = Path(output_path)

        fmt = src.suffix.lower().lstrip(".")
        if fmt in ("aif", "aiff"):
            fmt = "aiff"

        audio = AudioSegment.from_file(str(src), format=fmt)
        dst.parent.mkdir(parents=True, exist_ok=True)
        audio.export(str(dst), format="aiff")

        if metadata:
            _write_id3_tags(str(dst), metadata)

        return True, ""
    except Exception as e:
        return False, str(e)


def _write_id3_tags(path: str, metadata: dict):
    """Embed ID3 tags into an AIFF file."""
    try:
        audio = AIFF(path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags

        if metadata.get("name"):
            tags.add(TIT2(encoding=3, text=metadata["name"]))
        if metadata.get("category"):
            tags.add(TCON(encoding=3, text=metadata["category"]))
        if metadata.get("bpm") and int(metadata["bpm"]) > 0:
            tags.add(TBPM(encoding=3, text=str(int(metadata["bpm"]))))
        if metadata.get("key"):
            tags.add(TKEY(encoding=3, text=metadata["key"]))
        if metadata.get("tags"):
            tags.add(TXXX(encoding=3, desc="SampleTags", text=metadata["tags"]))
        if metadata.get("label"):
            tags.add(TPUB(encoding=3, text=metadata["label"]))

        audio.save()
    except Exception as e:
        print(f"ID3 tag write error: {e}")
