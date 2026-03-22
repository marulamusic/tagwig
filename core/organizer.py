from pathlib import Path

# Ordered dict: display label -> relative folder path inside library root.
# Groups mirror Bitwig's category browser while keeping a sensible folder hierarchy.
CATEGORIES = {
    # ── Drums ─────────────────────────────────────────────────────────────────
    "Drums / Kicks":          "Drums/Kicks",           # Bitwig: Kick
    "Drums / Snares":         "Drums/Snares",          # Bitwig: Snare
    "Drums / Claps":          "Drums/Claps",           # Bitwig: Clap
    "Drums / Hi-Hats":        "Drums/Hi-Hats",         # Bitwig: Hi-hat
    "Drums / Cymbals":        "Drums/Cymbals",         # Bitwig: Cymbal
    "Drums / Toms":           "Drums/Toms",            # Bitwig: Tom
    "Drums / Percussion":     "Drums/Percussion",      # Bitwig: Percussion
    "Drums / Other":          "Drums/Other",           # Bitwig: Other Drums
    "Drums / Loops":          "Drums/Loops",           # Bitwig: Drum Loop
    # ── Bass ──────────────────────────────────────────────────────────────────
    "Bass / One-Shots":       "Bass/One-Shots",        # Bitwig: Bass
    "Bass / Loops":           "Bass/Loops",
    # ── Synth ─────────────────────────────────────────────────────────────────
    "Synth / Leads":          "Synth/Leads",           # Bitwig: Lead
    "Synth / Pads":           "Synth/Pads",            # Bitwig: Pad
    "Synth / Plucks":         "Synth/Plucks",          # Bitwig: Plucks
    "Synth / Chip":           "Synth/Chip",            # Bitwig: Chip
    "Synth / One-Shots":      "Synth/One-Shots",       # Bitwig: Synth
    "Synth / Loops":          "Synth/Loops",
    # ── Melodic / Acoustic ────────────────────────────────────────────────────
    "Melodic / Piano":        "Melodic/Piano",         # Bitwig: Piano
    "Melodic / Keys":         "Melodic/Keys",          # Bitwig: Keyboards
    "Melodic / Organ":        "Melodic/Organ",         # Bitwig: Organ
    "Melodic / Guitar":       "Melodic/Guitar",        # Bitwig: Guitar
    "Melodic / Strings":      "Melodic/Strings",       # Bitwig: Strings
    "Melodic / Brass":        "Melodic/Brass",         # Bitwig: Brass
    "Melodic / Winds":        "Melodic/Winds",         # Bitwig: Winds
    "Melodic / Pipe":         "Melodic/Pipe",          # Bitwig: Pipe
    "Melodic / Mallet":       "Melodic/Mallet",        # Bitwig: Mallet
    "Melodic / Bell":         "Melodic/Bell",          # Bitwig: Bell
    "Melodic / Ensemble":     "Melodic/Ensemble",      # Bitwig: Ensemble
    "Melodic / Orchestral":   "Melodic/Orchestral",    # Bitwig: Orchestral
    "Melodic / Chords":       "Melodic/Chords",
    "Melodic / One-Shots":    "Melodic/One-Shots",
    "Melodic / Loops":        "Melodic/Loops",
    # ── FX ────────────────────────────────────────────────────────────────────
    "FX / Risers":            "FX/Risers",
    "FX / Impacts":           "FX/Impacts",            # Bitwig: Sound FX
    "FX / Transitions":       "FX/Transitions",        # Bitwig: Transitions
    "FX / Atmospheres":       "FX/Atmospheres",
    "FX / Drones":            "FX/Drones",             # Bitwig: Drone
    "FX / Sound FX":          "FX/Sound FX",           # Bitwig: Sound FX
    # ── Vocals ────────────────────────────────────────────────────────────────
    "Vocals / Phrases":       "Vocals/Phrases",        # Bitwig: Vocal
    "Vocals / Chops":         "Vocals/Chops",
    "Vocals / Loops":         "Vocals/Loops",
    # ── Other ─────────────────────────────────────────────────────────────────
    "Uncategorised":          "Uncategorised",         # Bitwig: Unknown
}


FILE_EXTENSION = ".aif"


def get_target_path(library_root: str, category: str, filename: str,
                    label_subfolder: str = "") -> Path:
    rel = CATEGORIES.get(category, "Uncategorised")
    base = Path(library_root) / rel
    if label_subfolder:
        base = base / label_subfolder
    return base / filename


def unique_path(target: Path) -> Path:
    """Return target path, appending _1, _2 ... if a file already exists."""
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def count_existing_files(library_root: str, category: str,
                         label_subfolder: str = "") -> int:
    """Count how many .aif files already exist in a category (or label sub) folder."""
    if not library_root:
        return 0
    rel = CATEGORIES.get(category, "Uncategorised")
    folder = Path(library_root) / rel
    if label_subfolder:
        folder = folder / label_subfolder
    if not folder.exists():
        return 0
    return len(list(folder.glob(f"*{FILE_EXTENSION}")))


DEFAULT_FORMAT_TOKENS = ["number", "name", "tags", "bpm", "key"]
DEFAULT_SEPARATOR = "_"


def build_filename_from_format(
    tokens: list,
    separator: str,
    name: str = "",
    tags: str = "",
    bpm: int = 0,
    key: str = "",
    category: str = "",
    type: str = "",
    label: str = "",
    number: int = 0,
) -> str:
    """
    Build a filename from an ordered list of token keys and a separator.
    Used by both the generate-name function and the settings dialog preview.
    """
    parts = []

    for token in tokens:
        if token == "number":
            if number > 0:
                parts.append(f"{number:04d}")
        elif token == "name":
            for word in name.split():
                w = word.strip().replace(" ", "-")
                if w:
                    parts.append(w)
        elif token == "category":
            # Use the short leaf name, e.g. "Kicks" not "Drums / Kicks"
            short = category.split("/")[-1].strip().replace(" ", "-")
            if short:
                parts.append(short)
        elif token == "type":
            t = type.strip().replace(" ", "-")
            if t and t != "—":
                parts.append(t)
        elif token == "key":
            if key:
                parts.append(key)
        elif token == "bpm":
            if bpm and bpm > 0:
                parts.append(f"{bpm}bpm")
        elif token == "tags":
            for tag in tags.split(","):
                t = tag.strip().replace(" ", "-")
                if t and t.lower() not in (p.lower() for p in parts):
                    parts.append(t)
        elif token == "label":
            lbl = label.strip().replace(" ", "-")
            if lbl:
                parts.append(lbl)

    return separator.join(p for p in parts if p) + FILE_EXTENSION


def build_filename(name: str, tags: str, bpm: int, key: str, number: int = 0) -> str:
    """Legacy wrapper — uses the default format."""
    return build_filename_from_format(
        tokens=DEFAULT_FORMAT_TOKENS,
        separator=DEFAULT_SEPARATOR,
        name=name, tags=tags, bpm=bpm, key=key, number=number,
    )


def ensure_library_structure(library_root: str):
    root = Path(library_root)
    for rel in CATEGORIES.values():
        (root / rel).mkdir(parents=True, exist_ok=True)
