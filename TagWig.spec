# -*- mode: python ; coding: utf-8 -*-
#
# TagWig.spec  —  PyInstaller build spec for macOS .app bundle
#
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# ── Data files bundled inside the app ─────────────────────────────────────────
datas = [
    # App assets (logo, icons)
    ("assets", "assets"),
]

# librosa ships data files (audio examples, etc.) that must travel with it
datas += collect_data_files("librosa")
datas += collect_data_files("audioread")
datas += collect_data_files("soundfile")  # libsndfile dylib reference

# ── Hidden imports that PyInstaller's static analyser misses ──────────────────
hiddenimports = [
    # PySide6 platform plugin
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",
    # librosa sub-modules loaded dynamically
    "librosa.core",
    "librosa.beat",
    "librosa.feature",
    "librosa.util",
    "librosa.filters",
    "librosa.effects",
    "numba",
    "numba.core",
    "numba.typed",
    "scipy.signal",
    "scipy.fft",
    "scipy.special",
    "sklearn",
    "sklearn.utils",
    # pydub back-ends
    "pydub",
    "pydub.utils",
    # mutagen formats
    "mutagen.id3",
    "mutagen.wave",
    "mutagen.aiff",
    "mutagen.flac",
    # sounddevice / soundfile
    "soundfile",
    "cffi",
    "_cffi_backend",
]

hiddenimports += collect_submodules("librosa")
hiddenimports += collect_submodules("scipy")
hiddenimports += collect_submodules("sklearn")
hiddenimports += collect_submodules("numba")

# ── Analysis ───────────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=["/Users/marulamusic/Projects/TagWig"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Things we definitely don't need — keeps the bundle smaller
        "tkinter",
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
        "test",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── One-file executable (used by BUNDLE below) ─────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TagWig",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window
    disable_windowed_traceback=False,
    target_arch="arm64",    # Apple Silicon native
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TagWig",
)

# ── macOS .app Bundle ──────────────────────────────────────────────────────────
app = BUNDLE(
    coll,
    name="TagWig.app",
    icon="assets/TagWig.icns",
    bundle_identifier="com.marulamusic.tagwig",
    version="1.0.0",
    info_plist={
        "NSPrincipalClass":             "NSApplication",
        "NSHighResolutionCapable":      True,
        "NSMicrophoneUsageDescription": "TagWig uses the microphone for audio analysis.",
        "CFBundleDisplayName":          "TagWig",
        "CFBundleShortVersionString":   "1.0.0",
        "CFBundleVersion":              "1.0.0",
        # Allow reading files dragged in from anywhere
        "NSDocumentsFolderUsageDescription": "TagWig reads and converts audio samples.",
        "NSDesktopFolderUsageDescription":   "TagWig reads and converts audio samples.",
        "NSDownloadsFolderUsageDescription": "TagWig reads and converts audio samples.",
    },
)
