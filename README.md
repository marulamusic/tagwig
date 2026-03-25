# TagWig

**TagWig** is a sample management tool for music producers using Bitwig Studio. Drag in your audio files, enrich them with metadata, generate clean Bitwig-friendly filenames, convert to lossless formats, and import everything into a tidy organized library — all in one workflow.

![TagWig Icon](assets/marula_logo.png)

---

## What it does

- **Organizes** samples into a structured folder hierarchy that mirrors Bitwig's category browser
- **Tags** samples with BPM, key, category, type, custom tags, and group labels
- **Converts** any audio format (WAV, AIFF, MP3, FLAC, OGG, M4A) to lossless AIFF, WAV, or FLAC
- **Embeds** metadata directly into file tags (ID3 for AIFF/WAV, Vorbis comments for FLAC) so Bitwig's browser can search them
- **Generates** structured filenames from a configurable token format (number, name, tags, BPM, key, category, label, type)
- **Detects** BPM and key automatically in the background using librosa
- **Re-tags** already-imported files from the library panel without re-converting
- **Relocates** files to a different category folder if their category is changed during editing

---

## Interface Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Logo]  [Set Library]  [Settings]  [Re-tag Library]  Library: …  │  Toolbar
├──────────────┬──────────────────────────────────────────────────────┤
│  Library     │  ┌─ Drop Zone ──────────────────────────────────┐   │
│  Tree        │  │  Drag audio files or folders here            │   │
│              │  └──────────────────────────────────────────────┘   │
│  ▶ Drums     │  ┌─ Queue Table ────────────────────────────────┐   │
│    Kicks     │  │  Original File │ Category │ New Name │ Status │   │
│    Snares    │  │  ──────────────┼──────────┼──────────┼────── │   │
│    …         │  │  kick_raw.wav  │ Drums/Ki │ 0001_kic │ Ready │   │
│  ▶ Bass      │  └──────────────────────────────────────────────┘   │
│  ▶ Synth     │  ┌─ Playback ───────────────────────────────────┐   │
│  …           │  │  ▶  AUTO  [~~~waveform~~~~~~~~~~~~~~~~~~~]  0:00 │
│  ──────────  │  └──────────────────────────────────────────────┘   │
│  Files       ├──────────────────────────────────────────────────────┤
│  in folder   │  [Generate Name]   [Clear]   [Import All]           │
│  kick.aif    │  Name ____________  Category ▾  Type ▾              │
│  snare.aif   │  Tags ____________  BPM ___  Key ___  Label ______  │
│              │  [Bitwig quick tags grid]                            │
│              │  [Custom tags grid]                                  │
└──────────────┴──────────────────────────────────────────────────────┘
│  Status bar                                                          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Getting Started

### Requirements

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/) in your `PATH` (for MP3/M4A/OGG conversion)

### Run from source

```bash
git clone https://github.com/marulamusic/tagwig.git
cd tagwig
pip install -r requirements.txt
python main.py
```

### macOS app bundle

Pre-built `.app` bundles (Apple Silicon) are available on the [Releases](https://github.com/marulamusic/tagwig/releases) page. Download `TagWig.app`, move it to your Applications folder, and open it.

### Windows

Build from source on a Windows machine (see [Building](#building) below), or download the pre-built `.zip` from Releases.

---

## Workflow

### 1. Set your library folder

Click **Set Library Folder** and choose where you want your organized samples to live. TagWig will create the full folder structure inside it automatically.

### 2. Drop files into the queue

Drag audio files or entire folders onto the drop zone. Supported formats: `.wav`, `.aif`, `.aiff`, `.mp3`, `.flac`, `.ogg`, `.m4a`, `.mp4`.

TagWig immediately:
- Reads any existing metadata from the files
- Parses BPM and key from the filename if they are embedded there
- Launches background analysis to detect BPM (for Loops) and key via librosa

### 3. Edit metadata in the tag editor

Select one or more rows in the queue table to load them into the tag editor below:

| Field | Notes |
|---|---|
| **Name** | The filename stem. Affects single rows only. |
| **Category** | Folder destination inside your library (e.g., Drums / Kicks). Applied to all selected rows. |
| **Type** | One-Shot / Loop / Other. Triggers BPM detection when set to Loop. |
| **Tags** | Comma-separated list. Use the quick-tag buttons or type freely. |
| **BPM** | Auto-detected for loops; edit manually if needed. |
| **Key** | e.g., `Am`, `Cmaj`, `F#`. Auto-detected; edit manually. |
| **Group / Label** | Adds a named subfolder inside the category folder (e.g., `808`). This subfolder name also becomes a Bitwig-browsable tag. |

**Quick-tag buttons** let you toggle standard Bitwig tags and your own custom tags with a single click. Buttons light up when the tag is active for the selected file.

### 4. Generate filenames

Click **Generate Name from Tags** to build structured filenames from your configured format (number, name, tags, BPM, key, etc.). Numbers increment per category/label group, continuing from the last file already in that folder.

### 5. Preview and audition

Click any file in the queue or the library panel to load it into the waveform player. Click anywhere on the waveform to seek. Enable **AUTO** to start playback automatically whenever a file loads.

### 6. Import

Click **Import All**. TagWig converts each queued file to your chosen format (AIFF, WAV, or FLAC), embeds the metadata, writes the file to the correct folder, and saves a record to its internal database.

---

## Library Panel

Once files are imported the left panel becomes your library browser.

- **Folder tree** (top): navigate the category folder structure
- **File list** (bottom): see all audio files in the selected folder

**Right-click** any file for:
- **Edit Tags…** — opens the full tag editor for that file
- **Reveal in Finder** — shows the file in macOS Finder

**Double-click** any file to open the edit dialog directly.

### Editing existing files

The edit dialog lets you change any field and apply the update without re-converting the audio. TagWig rewrites only the embedded metadata (ID3 / Vorbis) in place.

**If you change the Category**, TagWig will:
1. Generate a new filename with the next available number in the target folder
2. Physically move the file to the new category folder
3. Update the database record
4. Refresh the library tree

---

## Settings

Open **Settings** from the toolbar to configure:

### Output format

| Format | Tags | Best for |
|---|---|---|
| **AIFF** | ID3 v2 | Bitwig (best compatibility) |
| **WAV** | ID3 v2 | Universal — works everywhere |
| **FLAC** | Vorbis comments | Open standard, slightly smaller |

### Filename format

Add tokens to the active format and drag them into the order you want:

`# Number` · `Name` · `Category` · `Type` · `Key` · `BPM` · `Tags` · `Label`

Choose a separator: `_` `-` `.` `,`

A live preview updates as you configure.

### Always write tags to metadata

When this checkbox is enabled, all tags are embedded into the file's metadata even if the **Tags** token is not part of the filename. This lets you keep filenames short while still making full tag metadata searchable in your DAW.

### Bitwig tag registration

For custom tags to appear as filterable tags in Bitwig's browser they must be registered in Bitwig's internal index. TagWig shows the registration status of your custom tags and provides a **Register Tags with Bitwig** button.

After registering:
1. Quit Bitwig Studio completely
2. Re-open Bitwig
3. Rescan your library

> **Note:** Bitwig builds its tag cross-reference table at startup. Changes to the index during a running session won't be visible until Bitwig restarts.

### Custom tags

From the main window, **right-click the Custom Tags header** (or use the edit button) to open a plain-text editor. Enter one tag per line. Tags you define here appear as quick-select buttons in the tag editor and the library edit dialog.

---

## Bitwig Integration

TagWig's folder structure is designed to match Bitwig's sample browser categories:

| TagWig Category | Bitwig Browser |
|---|---|
| Drums / Kicks | Kick |
| Drums / Snares | Snare |
| Bass / One-Shots | Bass |
| Synth / Leads | Lead |
| Synth / Pads | Pad |
| FX / Atmospheres | Atmosphere |
| Vocals / Phrases | Vocal |
| … | … |

**How filename tags work in Bitwig:**

Bitwig tokenises filenames on separators (`_`, `-`, `.`) and cross-references each token against its tag index. A token appears as a filterable tag in the browser only if it exists in both `tags.ids` and `file-name-words.ids`. Built-in tags (808, 909, dark, bright, etc.) are always registered. Custom tags need to be registered via the Settings dialog.

**Label/Group subfolders** are also tokenised by Bitwig as folder names, making them another route for custom tags without needing index registration.

---

## BPM and Key Detection

Detection runs automatically in the background when files are added to the queue.

| Method | Speed | Trigger |
|---|---|---|
| Read source file metadata | Instant | Always |
| Parse BPM/key from filename | Instant | Always |
| librosa beat tracking | Slow (up to 60s) | Type = Loop AND bpm = 0 |
| librosa chroma analysis | Slow (up to 30s) | Key field is empty |

Results populate the tag editor automatically. You can always override them manually.

---

## Undo / Redo

- **Cmd+Z** / **Ctrl+Z** — Undo
- **Cmd+Shift+Z** / **Ctrl+Y** — Redo

Undo tracks all changes made in the tag editor: field edits, tag button toggles, category changes, and generated names. The undo stack is per-session (up to 100 steps) and does not persist after the app closes.

---

## Building

### macOS (.app bundle, Apple Silicon)

```bash
pip install pyinstaller
pyinstaller TagWig.spec --noconfirm
# Output: dist/TagWig.app
```

### Windows (.exe folder)

Run on a Windows machine with Python 3.11+:

```bat
build-windows.bat
REM Output: dist\TagWig\TagWig.exe
```

---

## Project Structure

```
TagWig/
├── main.py                  # Entry point
├── requirements.txt
├── TagWig.spec              # PyInstaller macOS build spec
├── TagWig-windows.spec      # PyInstaller Windows build spec
├── build.sh                 # macOS rebuild script
├── build-windows.bat        # Windows build script
├── assets/
│   └── marula_logo.png
├── ui/
│   ├── main_window.py       # Main application window
│   ├── settings_dialog.py   # Settings / naming format dialog
│   └── playback_bar.py      # Waveform player widget
└── core/
    ├── database.py          # SQLite persistence
    ├── converter.py         # Audio conversion + metadata writing
    ├── detector.py          # BPM/key detection (librosa)
    ├── organizer.py         # Folder structure, filename generation
    └── bitwig_tags.py       # Bitwig index registration utilities
```

---

## License

GPL v3 — see [LICENSE](LICENSE).

Built by [Marula Music](https://marulamusic.com).
