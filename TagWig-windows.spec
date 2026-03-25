# -*- mode: python ; coding: utf-8 -*-
#
# TagWig-windows.spec  —  PyInstaller build spec for Windows .exe
#
# Run on a Windows machine with:
#   pip install -r requirements.txt
#   pip install pyinstaller
#   pyinstaller TagWig-windows.spec --noconfirm
#
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = [
    ("assets", "assets"),
]
datas += collect_data_files("librosa")
datas += collect_data_files("audioread")
datas += collect_data_files("soundfile")

hiddenimports = [
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",
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
    "pydub",
    "pydub.utils",
    "mutagen.id3",
    "mutagen.wave",
    "mutagen.aiff",
    "mutagen.flac",
    "soundfile",
    "cffi",
    "_cffi_backend",
]

hiddenimports += collect_submodules("librosa")
hiddenimports += collect_submodules("scipy")
hiddenimports += collect_submodules("sklearn")
hiddenimports += collect_submodules("numba")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TagWig",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,               # UPX compression available on Windows
    console=False,          # no terminal window
    disable_windowed_traceback=False,
    # icon="assets/TagWig.ico",   # uncomment once you have a .ico file
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TagWig",
)
