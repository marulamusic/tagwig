import json
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime


def _resource_path(relative: str) -> Path:
    """Return absolute path to a bundled resource.

    Works both when running from source (relative to the project root) and
    when frozen by PyInstaller (resources are unpacked to sys._MEIPASS).
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative
    # Running from source — project root is two levels up from this file
    return Path(__file__).parent.parent / relative

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QLineEdit, QComboBox, QFileDialog,
    QHeaderView, QAbstractItemView, QMessageBox, QSpinBox,
    QToolBar, QStatusBar, QCheckBox, QSlider, QFrame, QGridLayout,
    QListWidget, QListWidgetItem, QMenu, QDialog, QDialogButtonBox,
    QScrollArea,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QPixmap

from ui.playback_bar import PlaybackBar

from core.database import Database
from ui.settings_dialog import SettingsDialog
from core.converter import convert_audio, retag_file, FORMAT_EXTENSION
from core.detector import read_source_tags, parse_filename_tags, detect_bpm, detect_key
from core.organizer import (
    CATEGORIES, FILE_EXTENSION,
    get_target_path, unique_path, ensure_library_structure,
    build_filename_from_format, count_existing_files,
    DEFAULT_FORMAT_TOKENS, DEFAULT_SEPARATOR,
)


# ── Background re-tag worker ──────────────────────────────────────────────────

class RetagWorker(QThread):
    """Rewrites ID3 tags on all .aif files in the library from the DB records."""

    progress = Signal(int, int, str)   # (done, total, current_file)
    finished = Signal(int, int)        # (success_count, fail_count)

    def __init__(self, jobs: list[tuple], parent=None):
        """jobs: list of (library_path, metadata_dict)"""
        super().__init__(parent)
        self._jobs = jobs

    def run(self):
        total = len(self._jobs)
        ok = fail = 0
        for i, (path, meta) in enumerate(self._jobs):
            success, _ = retag_file(path, meta)
            if success:
                ok += 1
            else:
                fail += 1
            self.progress.emit(i + 1, total, Path(path).name)
        self.finished.emit(ok, fail)


# ── Background library re-tag worker ─────────────────────────────────────────

class _LibraryRetagWorker(QThread):
    """Rewrites ID3/Vorbis tags on a specific list of library files."""

    finished = Signal(int)   # number of files successfully retagged
    error    = Signal(str)   # error message on unexpected failure

    def __init__(self, jobs: list[tuple], parent=None):
        """jobs: list of (library_path, metadata_dict)"""
        super().__init__(parent)
        self._jobs = jobs

    def run(self):
        try:
            ok = 0
            for path, meta in self._jobs:
                success, _ = retag_file(path, meta)
                if success:
                    ok += 1
            self.finished.emit(ok)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Background BPM / Key detection worker ─────────────────────────────────────

class DetectionWorker(QThread):
    """
    Runs BPM and key detection in a background thread for a batch of files.

    For each item:
      - BPM analysis is only run when item type is 'Loop' AND bpm == 0
      - Key analysis is run whenever key is empty

    Emits detected(row_idx, bpm, key) as results arrive.
    bpm == -1 means "no BPM detected / not applicable".
    key == "" means "no key detected".
    """

    detected = Signal(int, int, str)   # (row_idx, bpm, key)

    def __init__(self, jobs: list[tuple], parent=None):
        """jobs: list of (row_idx, path, item_type, needs_bpm, needs_key)"""
        super().__init__(parent)
        self._jobs = jobs

    def run(self):
        for row_idx, path, item_type, needs_bpm, needs_key in self._jobs:
            bpm_result = -1
            key_result = ""

            if needs_bpm and item_type.lower() == "loop":
                val = detect_bpm(path)
                if val is not None:
                    bpm_result = val

            if needs_key:
                val = detect_key(path)
                if val:
                    key_result = val

            if bpm_result != -1 or key_result:
                self.detected.emit(row_idx, bpm_result, key_result)


# ── Undo / Redo stack ─────────────────────────────────────────────────────────

class _UndoStack:
    """
    Records before/after snapshots of import_queue rows.
    Each entry: (before, after) where each is [(row_idx, item_dict_copy), ...]
    """
    def __init__(self, max_size: int = 100):
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._max = max_size

    def push(self, before: list, after: list):
        self._undo.append((before, after))
        if len(self._undo) > self._max:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return None
        before, after = self._undo.pop()
        self._redo.append((before, after))
        return before

    def redo(self):
        if not self._redo:
            return None
        before, after = self._redo.pop()
        self._undo.append((before, after))
        return after

    @property
    def can_undo(self): return bool(self._undo)
    @property
    def can_redo(self): return bool(self._redo)


# ── Background conversion worker ──────────────────────────────────────────────

class ConvertWorker(QThread):
    progress = Signal(int, str)   # queue_index, status
    finished = Signal()

    def __init__(self, jobs: list):
        super().__init__()
        self.jobs = jobs
        self._running = True

    def run(self):
        for job in self.jobs:
            if not self._running:
                break
            self.progress.emit(job["queue_index"], "Converting…")
            ok, err = convert_audio(
                job["source_path"],
                job["target_path"],
                out_format=job.get("out_format", "aif"),
                metadata=job.get("metadata"),
            )
            self.progress.emit(job["queue_index"], "Done" if ok else f"Error: {err[:40]}")
        self.finished.emit()

    def stop(self):
        self._running = False


# ── Drag-and-drop zone ────────────────────────────────────────────────────────

class DropZone(QLabel):
    files_dropped = Signal(list)

    SUPPORTED = {".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a", ".mp4"}

    _IDLE = """
        QLabel {
            border: 2px dashed #484848; border-radius: 8px;
            color: #666; font-size: 14px; background-color: #242424;
        }
    """
    _HOVER = """
        QLabel {
            border: 2px dashed #E07820; border-radius: 8px;
            color: #E07820; font-size: 14px; background-color: #2A1400;
        }
    """

    def __init__(self):
        super().__init__("Drop audio files or folders here")
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(64)
        self.setStyleSheet(self._IDLE)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._HOVER)

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._IDLE)

    def dropEvent(self, event):
        self.setStyleSheet(self._IDLE)
        paths = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                for ext in self.SUPPORTED:
                    paths.extend(str(f) for f in p.rglob(f"*{ext}"))
            elif p.suffix.lower() in self.SUPPORTED:
                paths.append(str(p))
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    STATUS_COLORS = {
        "Ready":   "#888888",
        "Done":    "#4caf50",
        "Error":   "#f44336",
        "Converting…": "#ff9800",
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TagWig")
        self.resize(1280, 860)

        import os
        if sys.platform == "win32":
            _base = os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming")
            db_dir = Path(_base) / "TagWig"
        elif sys.platform == "darwin":
            db_dir = Path.home() / "Library/Application Support/TagWig"
        else:
            db_dir = Path.home() / ".tagwig"
        db_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(str(db_dir / "library.db"))

        self.library_root: str = self.db.get_setting("library_root", "")
        self.import_queue: list[dict] = []
        self.worker: ConvertWorker | None = None
        self._detect_worker: DetectionWorker | None = None
        self._pending_jobs: dict[int, dict] = {}
        self._block_name_sync = False

        # Custom tags (user-editable, persisted to DB)
        saved_custom = self.db.get_setting("custom_tags", "")
        self._custom_tags: list[str] = (
            json.loads(saved_custom) if saved_custom else list(self.DEFAULT_CUSTOM_TAGS)
        )

        # Naming format — loaded from DB, editable via Settings dialog
        saved_tokens = self.db.get_setting("naming_tokens", "")
        self._format_tokens: list = (
            json.loads(saved_tokens) if saved_tokens else list(DEFAULT_FORMAT_TOKENS)
        )
        self._format_separator: str = self.db.get_setting("naming_separator", DEFAULT_SEPARATOR)
        self._output_format: str = self.db.get_setting("output_format", "aif")
        self._force_id3_tags: bool = self.db.get_setting("force_id3_tags", "1") == "1"
        self._undo_stack = _UndoStack()

        self._apply_style()
        self._build_ui()
        self._refresh_tree()

        # Undo / Redo — standard Mac shortcuts
        QShortcut(QKeySequence.StandardKey.Undo, self, self._undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, self._redo)

    # ── Styling ───────────────────────────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet("""
            * { font-family: -apple-system, "SF Pro Text", "Helvetica Neue", sans-serif;
                font-size: 13px; }
            QMainWindow, QWidget { background-color: #1c1c1c; color: #e0e0e0; }
            QSplitter::handle { background-color: #333; }

            QPushButton {
                background-color: #C86000; color: #fff;
                border: 1px solid #E07010; border-radius: 5px; padding: 5px 14px;
            }
            QPushButton:hover   { background-color: #E07010; border-color: #F08020; }
            QPushButton:pressed { background-color: #9a4a00; border-color: #C86000; }
            QPushButton:disabled { background-color: #5A2A00; color: #7a4a20;
                                   border-color: #4a2000; }

            QLineEdit, QComboBox, QSpinBox {
                background-color: #2a2a2a; border: 1px solid #444;
                border-radius: 4px; padding: 4px 8px; color: #e0e0e0;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #E07010; }
            QComboBox::drop-down { border: none; padding-right: 6px; }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a; selection-background-color: #7A3A00;
            }

            QTreeWidget { background-color: #222; border: none;
                          border-right: 1px solid #333; }
            QTreeWidget::item { padding: 2px 0; }
            QTreeWidget::item:selected { background-color: #7A3A00; }

            QTableWidget { background-color: #222; border: none;
                           gridline-color: #2e2e2e; }
            QTableWidget::item { padding: 3px 8px; }
            QTableWidget::item:selected { background-color: #7A3A00; }

            QHeaderView::section {
                background-color: #1c1c1c; color: #666; border: none;
                border-bottom: 1px solid #333; padding: 5px 8px;
                font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
            }
            QToolBar { background-color: #242424; border-bottom: 1px solid #333;
                       padding: 4px 8px; spacing: 6px; }
            QStatusBar { background-color: #161616; color: #555;
                         border-top: 1px solid #2a2a2a; font-size: 12px; }
        """)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_toolbar()

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        h_split = QSplitter(Qt.Horizontal)
        h_split.setHandleWidth(1)
        root_layout.addWidget(h_split)
        h_split.addWidget(self._build_sidebar())
        h_split.addWidget(self._build_main_panel())
        h_split.setSizes([220, 1060])

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._update_status()

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)

        # Marula Music logo
        logo_path = _resource_path("assets/marula_logo.png")
        if logo_path.exists():
            logo_lbl = QLabel()
            pix = QPixmap(str(logo_path))
            pix = pix.scaledToHeight(28, Qt.SmoothTransformation)
            logo_lbl.setPixmap(pix)
            logo_lbl.setContentsMargins(4, 0, 10, 0)
            tb.addWidget(logo_lbl)
            sep = QFrame()
            sep.setFrameShape(QFrame.VLine)
            sep.setStyleSheet("color: #333;")
            tb.addWidget(sep)

        btn = QPushButton("Set Library Folder")
        btn.clicked.connect(self._pick_library)
        tb.addWidget(btn)

        naming_btn = QPushButton("Settings")
        naming_btn.clicked.connect(self._open_naming_settings)
        tb.addWidget(naming_btn)

        retag_btn = QPushButton("Re-tag Library")
        retag_btn.setToolTip(
            "Rewrites ID3 tags on every .aif file already in your library\n"
            "using the latest tag format (fixes files imported with older versions).\n"
            "Requires a Bitwig library rescan afterwards."
        )
        retag_btn.clicked.connect(self._retag_library)
        tb.addWidget(retag_btn)
        tb.addSeparator()

        self.library_label = QLabel(
            self.library_root if self.library_root else "No library folder set"
        )
        self.library_label.setStyleSheet("color: #666; font-size: 12px; padding: 0 6px;")
        tb.addWidget(self.library_label)

        # Push volume to the right
        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().horizontalPolicy(),
            spacer.sizePolicy().verticalPolicy(),
        )
        from PySide6.QtWidgets import QSizePolicy as QSP
        spacer.setSizePolicy(QSP.Expanding, QSP.Preferred)
        tb.addWidget(spacer)

        vol_lbl = QLabel("🔊")
        vol_lbl.setStyleSheet("color: #666; font-size: 14px;")
        tb.addWidget(vol_lbl)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.setToolTip("Master preview volume")
        self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px; background: #3a3a3a; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; margin: -5px 0;
                background: #E07820; border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #C86000; border-radius: 2px;
            }
        """)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        tb.addWidget(self.volume_slider)

        self.addToolBar(tb)

    def _build_sidebar(self):
        widget = QWidget()
        widget.setMinimumWidth(180)
        widget.setMaximumWidth(300)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        v_split = QSplitter(Qt.Vertical)
        v_split.setHandleWidth(1)

        # ── Folder tree ───────────────────────────────────────────────────────
        tree_pane = QWidget()
        tree_layout = QVBoxLayout(tree_pane)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(0)
        tree_layout.addWidget(self._section_header("Library"))

        self.library_tree = QTreeWidget()
        self.library_tree.setHeaderHidden(True)
        self.library_tree.setIndentation(14)
        self.library_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.library_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.library_tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        tree_layout.addWidget(self.library_tree)
        v_split.addWidget(tree_pane)

        # ── File list ─────────────────────────────────────────────────────────
        file_pane = QWidget()
        file_layout = QVBoxLayout(file_pane)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(0)
        file_layout.addWidget(self._section_header("Files in folder"))

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setStyleSheet("""
            QListWidget { background-color: #1e1e1e; border: none; }
            QListWidget::item { padding: 3px 10px; color: #aaa; font-size: 12px; }
            QListWidget::item:selected { background-color: #7A3A00; color: #fff; }
            QListWidget::item:hover { background-color: #2a2a2a; }
        """)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._on_file_list_context_menu)
        self.file_list.itemClicked.connect(self._on_file_list_clicked)
        self.file_list.itemDoubleClicked.connect(self._on_file_list_double_clicked)
        file_layout.addWidget(self.file_list)
        v_split.addWidget(file_pane)

        v_split.setSizes([400, 280])
        layout.addWidget(v_split)
        return widget

    def _build_main_panel(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        v_split = QSplitter(Qt.Vertical)
        v_split.setHandleWidth(1)
        layout.addWidget(v_split)
        v_split.addWidget(self._build_queue_panel())
        v_split.addWidget(self._build_tag_editor())
        v_split.setSizes([520, 300])
        return widget

    def _build_queue_panel(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(8)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._on_files_dropped)
        layout.addWidget(self.drop_zone)

        row = QHBoxLayout()
        lbl = QLabel("IMPORT QUEUE")
        lbl.setStyleSheet("color: #555; font-size: 11px; font-weight: bold; letter-spacing: 1px;")

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setFixedWidth(68)
        self.clear_btn.clicked.connect(self._clear_queue)

        self.import_btn = QPushButton("Import All")
        self.import_btn.setFixedWidth(96)
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._start_import)

        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(self.clear_btn)
        row.addWidget(self.import_btn)
        layout.addLayout(row)

        # Queue table — New Name column is directly editable
        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(4)
        self.queue_table.setHorizontalHeaderLabels(["Original File", "Category", "New Name  (double-click to edit)", "Status"])
        hh = self.queue_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        self.queue_table.setColumnWidth(1, 190)
        self.queue_table.setColumnWidth(3, 110)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setAlternatingRowColors(False)
        self.queue_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.queue_table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.queue_table)

        # Playback bar
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: #2e2e2e;")
        layout.addWidget(divider)

        self.playback_bar = PlaybackBar()
        self.playback_bar.setFixedHeight(62)
        self.playback_bar.setStyleSheet("background-color: #1a1a1a;")
        layout.addWidget(self.playback_bar)

        return widget

    # Bitwig's built-in browser tags — shown as a quick-select matrix
    BITWIG_TAGS = [
        ["acoustic", "analog",   "digital",  "rhythmic"],
        ["fast",     "slow",     "hard",     "soft"    ],
        ["bright",   "dark",     "clean",    "dirty"   ],
        ["glide",    "mono",     "poly",     "chord"   ],
    ]

    # Default custom quick-tag buttons (user-editable, no vocabulary restriction)
    DEFAULT_CUSTOM_TAGS = [
        "808", "909", "707", "cr78",
        "detuned", "electric", "layered", "metallic",
        "noisy", "wet", "mod", "fx",
        "harmonic", "lofi", "sub", "heavy",
    ]

    def _build_tag_editor(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(8)

        # ── Header ────────────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        lbl = QLabel("TAG EDITOR")
        lbl.setStyleSheet("color: #555; font-size: 11px; font-weight: bold; letter-spacing: 1px;")

        self.gen_name_btn = QPushButton("Generate Name from Tags")
        self.gen_name_btn.setFixedWidth(192)
        self.gen_name_btn.setEnabled(False)
        self.gen_name_btn.setToolTip("Build Bitwig-friendly filename using the active naming format")
        self.gen_name_btn.clicked.connect(self._generate_name)

        header_row.addWidget(lbl)
        header_row.addStretch()
        header_row.addWidget(self.gen_name_btn)
        layout.addLayout(header_row)

        # ── Main fields row ───────────────────────────────────────────────────
        fields = QHBoxLayout()
        fields.setSpacing(20)

        # Left: name + tags
        left = QVBoxLayout()
        left.setSpacing(4)

        left.addWidget(self._field_label("Name"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g.  kick  or  dark-kick")
        self.name_edit.editingFinished.connect(self._on_editor_name_finished)
        left.addWidget(self.name_edit)

        left.addWidget(self._field_label("Tags  (comma separated)"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("e.g.  dark, punchy, 909, analogue")
        self.tags_edit.editingFinished.connect(self._on_editor_tags_finished)
        left.addWidget(self.tags_edit)
        left.addStretch()
        fields.addLayout(left, 3)

        # Right: category / type / bpm / key / label
        right = QVBoxLayout()
        right.setSpacing(4)

        right.addWidget(self._field_label("Category  (determines folder)"))
        self.category_combo = QComboBox()
        for cat in CATEGORIES:
            self.category_combo.addItem(cat)
        self.category_combo.currentTextChanged.connect(self._on_editor_category_changed)
        right.addWidget(self.category_combo)

        right.addWidget(self._field_label("Type"))
        self.type_combo = QComboBox()
        for t in ("—", "One-Shot", "Loop", "Other"):
            self.type_combo.addItem(t)
        self.type_combo.currentTextChanged.connect(self._on_editor_type_changed)
        right.addWidget(self.type_combo)

        meta = QHBoxLayout()
        meta.setSpacing(10)
        bpm_col = QVBoxLayout()
        bpm_col.setSpacing(3)
        bpm_col.addWidget(self._field_label("BPM"))
        self.bpm_spin = QSpinBox()
        self.bpm_spin.setRange(0, 300)
        self.bpm_spin.setSpecialValueText("—")
        self.bpm_spin.setFixedWidth(72)
        self.bpm_spin.editingFinished.connect(self._on_editor_bpm_finished)
        bpm_col.addWidget(self.bpm_spin)
        meta.addLayout(bpm_col)

        key_col = QVBoxLayout()
        key_col.setSpacing(3)
        key_col.addWidget(self._field_label("Key"))
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("e.g. Am")
        self.key_edit.setFixedWidth(72)
        self.key_edit.editingFinished.connect(self._on_editor_key_finished)
        key_col.addWidget(self.key_edit)
        meta.addLayout(key_col)
        meta.addStretch()
        right.addLayout(meta)

        right.addWidget(self._field_label("Group / Label"))
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("e.g.  808, Loopmasters, Vintage")
        self.label_edit.setToolTip(
            "Used as a subfolder name inside the category folder.\n"
            "Bitwig reads subfolder names as custom browsable tags —\n"
            "so  Kicks/808/file.aif  will show '808' in Bitwig's tag browser.\n\n"
            "Use this for drum machine models (808, 909, 707), pack names,\n"
            "or any grouping you want to filter by in Bitwig."
        )
        self.label_edit.editingFinished.connect(self._on_editor_label_finished)
        right.addWidget(self.label_edit)

        _chk_css = """
            QCheckBox { color: #666; font-size: 11px; }
            QCheckBox::indicator { width: 13px; height: 13px; border-radius: 2px; }
            QCheckBox::indicator:unchecked { background: #2a2a2a; border: 1px solid #444; }
            QCheckBox::indicator:checked   { background: #C86000; border: 1px solid #E07010; }
        """
        self.label_subfolder_check = QCheckBox("Create group subfolder  (enables Bitwig tag)")
        self.label_subfolder_check.setStyleSheet(_chk_css)
        self.label_subfolder_check.setToolTip(
            "Places files in  Category / Group /  instead of  Category /\n"
            "The subfolder name becomes a custom tag in Bitwig's browser.\n"
            "Numbering restarts from 0001 per group subfolder."
        )
        right.addWidget(self.label_subfolder_check)
        right.addStretch()
        fields.addLayout(right, 2)
        layout.addLayout(fields)

        # ── Bitwig quick-tags matrix ──────────────────────────────────────────
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: #2a2a2a;")
        layout.addWidget(divider)

        bw_header = QLabel("BITWIG TAGS")
        bw_header.setStyleSheet(
            "color: #555; font-size: 11px; font-weight: bold; letter-spacing: 1px;"
        )
        layout.addWidget(bw_header)

        tag_btn_css = """
            QPushButton {
                background: #242424; color: #666;
                border: 1px solid #333; border-radius: 4px;
                padding: 3px 0; font-size: 11px;
            }
            QPushButton:hover   { border-color: #666; color: #aaa; }
            QPushButton:checked {
                background: #4A1E00; color: #E07820;
                border-color: #C86000;
            }
        """

        self._bitwig_tag_btns: dict[str, QPushButton] = {}
        grid = QGridLayout()
        grid.setSpacing(5)
        for r, row_tags in enumerate(self.BITWIG_TAGS):
            for c, tag in enumerate(row_tags):
                btn = QPushButton(tag)
                btn.setCheckable(True)
                btn.setFocusPolicy(Qt.NoFocus)
                btn.setStyleSheet(tag_btn_css)
                btn.toggled.connect(lambda checked, t=tag: self._on_bitwig_tag_toggled(t, checked))
                self._bitwig_tag_btns[tag] = btn
                grid.addWidget(btn, r, c)
        layout.addLayout(grid)

        # ── Custom tags ───────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2e2e2e; margin-top: 4px; margin-bottom: 2px;")
        layout.addWidget(sep)

        custom_header = QHBoxLayout()
        custom_lbl = QLabel("CUSTOM TAGS")
        custom_lbl.setStyleSheet("color: #555; font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        edit_custom_btn = QPushButton("Edit…")
        edit_custom_btn.setFixedWidth(52)
        edit_custom_btn.setFocusPolicy(Qt.NoFocus)
        edit_custom_btn.setStyleSheet("""
            QPushButton { font-size: 11px; padding: 2px 6px; }
        """)
        edit_custom_btn.clicked.connect(self._open_custom_tags_dialog)
        custom_header.addWidget(custom_lbl)
        custom_header.addStretch()
        custom_header.addWidget(edit_custom_btn)
        layout.addLayout(custom_header)

        self._custom_tag_btns: dict[str, QPushButton] = {}
        self._custom_grid = QGridLayout()
        self._custom_grid.setSpacing(5)
        self._rebuild_custom_tag_grid()
        layout.addLayout(self._custom_grid)

        return widget

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _section_header(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet("""
            QLabel { color: #555; font-size: 11px; font-weight: bold; letter-spacing: 1px;
                     padding: 8px 12px; background-color: #1e1e1e;
                     border-bottom: 1px solid #2e2e2e; }
        """)
        return lbl

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #555; font-size: 11px;")
        return lbl

    def _non_editable_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    # ── Library ───────────────────────────────────────────────────────────────

    def _pick_library(self):
        path = QFileDialog.getExistingDirectory(self, "Select Library Root Folder")
        if path:
            self.library_root = path
            self.db.save_setting("library_root", path)
            self.library_label.setText(path)
            ensure_library_structure(path)
            self._refresh_tree()
            self._update_import_btn()
            self.status_bar.showMessage(f"Library set: {path}", 5000)

    def _open_naming_settings(self):
        dlg = SettingsDialog(
            current_tokens=self._format_tokens,
            current_separator=self._format_separator,
            current_format=self._output_format,
            custom_tags=list(self._custom_tags),
            force_id3_tags=self._force_id3_tags,
            parent=self,
        )
        dlg.format_saved.connect(self._on_format_saved)
        dlg.exec()

    def _on_format_saved(self, tokens: list, separator: str, out_format: str, force_id3: bool):
        self._format_tokens = tokens
        self._format_separator = separator
        self._output_format = out_format
        self._force_id3_tags = force_id3
        self.db.save_setting("naming_tokens", json.dumps(tokens))
        self.db.save_setting("naming_separator", separator)
        self.db.save_setting("output_format", out_format)
        self.db.save_setting("force_id3_tags", "1" if force_id3 else "0")
        fmt_label = {"aif": "AIFF", "wav": "WAV", "flac": "FLAC"}.get(out_format, out_format.upper())
        self.status_bar.showMessage(f"Settings saved — output format: {fmt_label}", 4000)

    def _retag_library(self):
        """Rewrite ID3 tags on every .aif file in the library from DB records."""
        if not self.library_root:
            QMessageBox.warning(self, "No Library", "Set a library folder first.")
            return

        # Build job list from all .aif files found on disk, matched to DB records
        samples = self.db.get_all_samples()
        jobs = []
        missing = 0
        for s in samples:
            p = Path(s.get("library_path", ""))
            if p.exists() and p.suffix.lower() in (".aif", ".aiff"):
                meta = {
                    "name":     s.get("name", ""),
                    "tags":     s.get("tags", ""),
                    "bpm":      s.get("bpm", 0),
                    "key":      s.get("key", ""),
                    "label":    s.get("label", ""),
                    "category": s.get("category", ""),
                }
                jobs.append((str(p), meta))
            else:
                missing += 1

        if not jobs:
            QMessageBox.information(self, "Re-tag Library",
                "No library files found in the database.\n"
                "Import files first so TagWig knows their metadata.")
            return

        reply = QMessageBox.question(
            self, "Re-tag Library",
            f"This will rewrite ID3 tags on {len(jobs)} file(s) in your library\n"
            f"({missing} DB records had no matching file on disk).\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._retag_worker = RetagWorker(jobs, parent=self)
        self._retag_worker.progress.connect(
            lambda done, total, name: self.status_bar.showMessage(
                f"Re-tagging {done}/{total}: {name}"
            )
        )
        self._retag_worker.finished.connect(self._on_retag_finished)
        self._retag_worker.start()
        self.status_bar.showMessage(f"Re-tagging {len(jobs)} files…")

    def _on_retag_finished(self, ok: int, fail: int):
        self.status_bar.showMessage(
            f"Re-tag complete — {ok} updated, {fail} failed. "
            "Rescan your library in Bitwig to pick up the new tags.", 10000
        )

    def _refresh_tree(self):
        self.library_tree.clear()
        if not self.library_root or not Path(self.library_root).exists():
            return
        root = Path(self.library_root)
        root_item = QTreeWidgetItem([root.name])
        root_item.setData(0, Qt.UserRole, str(root))
        self.library_tree.addTopLevelItem(root_item)
        self._populate_tree(root, root_item)
        root_item.setExpanded(True)

    def _populate_tree(self, path: Path, parent: QTreeWidgetItem):
        try:
            for child in sorted(path.iterdir()):
                if child.is_dir():
                    item = QTreeWidgetItem([child.name])
                    item.setData(0, Qt.UserRole, str(child))
                    parent.addChild(item)
                    self._populate_tree(child, item)
        except PermissionError:
            pass

    def _on_tree_selection_changed(self):
        items = self.library_tree.selectedItems()
        if not items:
            return
        path = items[0].data(0, Qt.UserRole)
        if path:
            self._populate_file_list(Path(path))

    def _populate_file_list(self, folder: Path):
        self.file_list.clear()
        if not folder.exists():
            return
        audio_exts = {".aif", ".aiff", ".wav", ".mp3", ".flac", ".ogg"}
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in audio_exts:
                li = QListWidgetItem(f.name)
                li.setData(Qt.UserRole, str(f))
                self.file_list.addItem(li)

    def _on_file_list_clicked(self, list_item: QListWidgetItem):
        path = list_item.data(Qt.UserRole)
        if path:
            self.playback_bar.load_file(path)

    def _on_file_list_double_clicked(self, list_item: QListWidgetItem):
        paths = [i.data(Qt.UserRole) for i in self.file_list.selectedItems() if i.data(Qt.UserRole)]
        if paths:
            self._open_library_edit_dialog(paths)

    def _on_tree_context_menu(self, pos):
        item = self.library_tree.itemAt(pos)
        path = item.data(0, Qt.UserRole) if item else self.library_root
        if not path:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #2a2a2a; border: 1px solid #3a3a3a; color: #e0e0e0; }
            QMenu::item:selected { background: #7A3A00; }
        """)
        act = menu.addAction("Open in Finder")
        act.triggered.connect(lambda: subprocess.Popen(["open", path]))
        menu.exec(self.library_tree.viewport().mapToGlobal(pos))

    def _on_file_list_context_menu(self, pos):
        selected = [i.data(Qt.UserRole) for i in self.file_list.selectedItems() if i.data(Qt.UserRole)]
        item = self.file_list.itemAt(pos)
        if not item and not selected:
            return
        path = (item.data(Qt.UserRole) if item else None) or (selected[0] if selected else None)
        if not path:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #2a2a2a; border: 1px solid #3a3a3a; color: #e0e0e0; }
            QMenu::item:selected { background: #7A3A00; }
        """)

        paths_to_edit = selected if selected else [path]
        label = f"Edit Tags…" if len(paths_to_edit) == 1 else f"Edit Tags for {len(paths_to_edit)} files…"
        edit_act = menu.addAction(label)
        edit_act.triggered.connect(lambda: self._open_library_edit_dialog(paths_to_edit))

        menu.addSeparator()
        finder_act = menu.addAction("Reveal in Finder")
        finder_act.triggered.connect(lambda: subprocess.Popen(["open", "-R", path]))
        menu.exec(self.file_list.viewport().mapToGlobal(pos))

    # ── Library tag editing ───────────────────────────────────────────────────

    def _open_library_edit_dialog(self, paths: list[str]):
        """Open an edit-tags dialog for one or more already-imported library files."""
        from PySide6.QtWidgets import QPlainTextEdit

        # Look up DB records for all paths
        records = []
        for p in paths:
            row = self.db.get_sample_by_path(p)
            records.append(row)  # may be None if not in DB

        # Determine initial field values.
        # For multi-select: show shared value if all agree, else blank (mixed).
        def _shared(field, default=""):
            vals = [r[field] for r in records if r and r.get(field) is not None]
            if not vals:
                return default
            return vals[0] if len(set(str(v) for v in vals)) == 1 else ""

        init_name  = _shared("name")  if len(paths) == 1 else ""
        init_cat   = _shared("category", "Uncategorised")
        init_tags  = _shared("tags",  "")
        init_bpm   = _shared("bpm",   0)
        init_key   = _shared("key",   "")
        init_label = _shared("label", "")

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Tags" if len(paths) == 1 else f"Edit Tags — {len(paths)} files")
        dlg.setMinimumWidth(500)
        dlg.setStyleSheet(self.styleSheet())
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        menu_style = """
            QMenu { background: #2a2a2a; border: 1px solid #3a3a3a; color: #e0e0e0; }
            QMenu::item:selected { background: #7A3A00; }
        """

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet("color: #666; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;")
            return l

        def _field(placeholder="", value=""):
            f = QLineEdit()
            f.setPlaceholderText(placeholder)
            f.setText(str(value) if value else "")
            f.setStyleSheet(
                "QLineEdit { background: #2a2a2a; border: 1px solid #444; border-radius: 4px;"
                " color: #e0e0e0; padding: 5px 8px; font-size: 13px; }"
                "QLineEdit:focus { border-color: #C86000; }"
            )
            return f

        # File path info
        if len(paths) == 1:
            info = QLabel(Path(paths[0]).name)
            info.setStyleSheet("color: #555; font-size: 11px;")
            info.setWordWrap(True)
            layout.addWidget(info)
        else:
            info = QLabel(f"{len(paths)} files selected")
            info.setStyleSheet("color: #555; font-size: 11px;")
            layout.addWidget(info)

        # Name (single file only)
        if len(paths) == 1:
            layout.addWidget(_lbl("NAME"))
            name_edit = _field("filename stem", init_name)
            layout.addWidget(name_edit)
        else:
            name_edit = None

        # Category
        layout.addWidget(_lbl("CATEGORY"))
        cat_combo = QComboBox()
        cat_combo.addItems(list(CATEGORIES.keys()))
        if init_cat in CATEGORIES:
            cat_combo.setCurrentText(init_cat)
        cat_combo.setStyleSheet(
            "QComboBox { background: #2a2a2a; border: 1px solid #444; border-radius: 4px;"
            " color: #e0e0e0; padding: 5px 8px; font-size: 13px; }"
            "QComboBox:focus { border-color: #C86000; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox QAbstractItemView { background: #2a2a2a; color: #e0e0e0;"
            " selection-background-color: #7A3A00; border: 1px solid #3a3a3a; }"
        )
        layout.addWidget(cat_combo)

        # Tags
        layout.addWidget(_lbl("TAGS"))
        tags_edit = _field("dark, punchy, 909, …", init_tags)
        layout.addWidget(tags_edit)

        # Bitwig quick-tag buttons
        def _make_tag_grid(tags_list, cols=4):
            grid_w = QWidget()
            grid = QGridLayout(grid_w)
            grid.setSpacing(4)
            grid.setContentsMargins(0, 0, 0, 0)
            btns = {}
            btn_css = """
                QPushButton { background:#222; color:#777; border:1px solid #3a3a3a;
                    border-radius:4px; padding:3px 8px; font-size:11px; }
                QPushButton:hover { border-color:#666; color:#aaa; }
                QPushButton:checked { background:#1A3A00; color:#7EC820; border-color:#4A8800; }
            """
            for i, tag in enumerate(tags_list):
                b = QPushButton(tag)
                b.setCheckable(True)
                b.setFocusPolicy(Qt.NoFocus)
                b.setStyleSheet(btn_css)
                btns[tag] = b
                grid.addWidget(b, i // cols, i % cols)
            return grid_w, btns

        def _sync_btns(btns_dict, text):
            active = {t.strip().lower() for t in text.split(",") if t.strip()}
            for tag, btn in btns_dict.items():
                btn.blockSignals(True)
                btn.setChecked(tag.lower() in active)
                btn.blockSignals(False)

        def _on_tag_toggled(tag, checked, edit_field, btns_dict):
            current = [t.strip() for t in edit_field.text().split(",") if t.strip()]
            if checked and tag not in current:
                current.append(tag)
            elif not checked and tag in current:
                current.remove(tag)
            edit_field.setText(", ".join(current))
            _sync_btns(btns_dict, edit_field.text())

        # Bitwig grid
        all_bw_tags = [t for row in self.BITWIG_TAGS for t in row]
        bw_grid_w, bw_btns = _make_tag_grid(all_bw_tags, cols=4)
        layout.addWidget(bw_grid_w)

        # Custom tags
        if self._custom_tags:
            custom_grid_w, custom_btns = _make_tag_grid(self._custom_tags, cols=4)
            layout.addWidget(custom_grid_w)
        else:
            custom_btns = {}

        all_btns = {**bw_btns, **custom_btns}
        _sync_btns(all_btns, init_tags)

        tags_edit.textChanged.connect(lambda t: _sync_btns(all_btns, t))
        for tag, btn in all_btns.items():
            btn.toggled.connect(lambda checked, t=tag: _on_tag_toggled(t, checked, tags_edit, all_btns))

        # BPM + Key row
        bk_row = QHBoxLayout()
        bk_row.setSpacing(12)
        bpm_col = QVBoxLayout()
        bpm_col.addWidget(_lbl("BPM"))
        bpm_edit = _field("0", str(init_bpm) if init_bpm else "")
        bpm_col.addWidget(bpm_edit)
        key_col = QVBoxLayout()
        key_col.addWidget(_lbl("KEY"))
        key_edit = _field("Am", init_key)
        key_col.addWidget(key_edit)
        bk_row.addLayout(bpm_col)
        bk_row.addLayout(key_col)
        layout.addLayout(bk_row)

        # Label
        layout.addWidget(_lbl("GROUP / LABEL"))
        label_edit = _field("e.g. Loopmasters", init_label)
        layout.addWidget(label_edit)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        save_btn = QPushButton("Save & Retag")
        save_btn.setStyleSheet(
            "QPushButton { background:#C86000; border-color:#E07010; color:#fff;"
            " border-radius:5px; padding:5px 16px; font-size:13px; }"
            "QPushButton:hover { background:#E07010; }"
        )
        save_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return

        # Build updated metadata
        new_tags  = tags_edit.text().strip()
        new_bpm   = int(bpm_edit.text().strip() or 0)
        new_key   = key_edit.text().strip()
        new_label = label_edit.text().strip()
        new_name  = name_edit.text().strip() if name_edit else None
        new_cat   = cat_combo.currentText()
        # For single file we accept a user-edited name; for multi keep each file's own name
        use_new_name = bool(name_edit and new_name)

        # Update DB and retag each file (moving to new category folder if changed)
        jobs = []
        for i, p in enumerate(paths):
            rec = records[i]
            if not rec:
                continue

            old_cat    = rec.get("category", "")
            final_path = p
            final_name = new_name if use_new_name else rec.get("name", "")

            # ── Relocate when category changed ────────────────────────────────
            if self.library_root and new_cat and new_cat != old_cat:
                label_sub = (
                    new_label.strip().replace(" ", "-")
                    if self.label_subfolder_check.isChecked() and new_label.strip()
                    else ""
                )
                num = count_existing_files(self.library_root, new_cat, label_sub) + 1
                ext = Path(p).suffix
                new_stem = build_filename_from_format(
                    tokens=self._format_tokens,
                    separator=self._format_separator,
                    name=final_name,
                    tags=new_tags,
                    bpm=new_bpm,
                    key=new_key,
                    category=new_cat,
                    label=new_label,
                    number=num,
                )
                if not new_stem:
                    new_stem = final_name or Path(p).stem
                raw_target = get_target_path(
                    self.library_root, new_cat, new_stem + ext,
                    label_subfolder=label_sub,
                )
                dest = unique_path(raw_target)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
                final_path = str(dest)
                final_name = dest.stem

            meta = {
                "name":         final_name,
                "category":     new_cat if new_cat else old_cat,
                "tags":         new_tags,
                "bpm":          new_bpm,
                "key":          new_key,
                "label":        new_label,
                "library_path": final_path,
            }
            self.db.update_sample_tags(rec["id"], meta)
            jobs.append((final_path, meta))

        if not jobs:
            return

        # Retag files in background; refresh tree so any relocation is reflected
        worker = _LibraryRetagWorker(jobs)
        worker.finished.connect(lambda n: (
            self.status_bar.showMessage(f"Retagged {n} file(s) successfully.", 4000),
            self._refresh_tree(),
        ))
        worker.error.connect(lambda e: self.status_bar.showMessage(f"Retag error: {e}", 6000))
        worker.start()
        self._lib_retag_worker = worker  # keep reference

    # ── Queue ─────────────────────────────────────────────────────────────────

    def _on_files_dropped(self, paths: list[str]):
        existing = {item["source_path"] for item in self.import_queue}
        added_indices: list[int] = []

        for path in paths:
            if path in existing:
                continue
            p = Path(path)

            # ── Fast metadata extraction (synchronous) ─────────────────────
            meta = read_source_tags(path)
            fn_meta = parse_filename_tags(p.name)
            bpm = meta.get("bpm") or fn_meta.get("bpm") or 0
            key = meta.get("key") or fn_meta.get("key") or ""

            item = {
                "source_path": path,
                "name": p.stem,
                "category": "Uncategorised",
                "type": "—",
                "tags": "",
                "bpm": bpm,
                "key": key,
                "label": "",
                "status": "Ready",
            }
            idx = len(self.import_queue)
            self.import_queue.append(item)
            self._append_table_row(idx, item)
            added_indices.append(idx)

        if added_indices:
            self._update_import_btn()
            self.gen_name_btn.setEnabled(True)
            self._update_status()
            self._launch_detection_worker(added_indices)

    def _launch_detection_worker(self, indices: list[int]):
        """Kick off background BPM/key analysis for rows that still need it."""
        jobs = []
        for idx in indices:
            item = self.import_queue[idx]
            needs_bpm = item["bpm"] == 0
            needs_key = item["key"] == ""
            if needs_bpm or needs_key:
                jobs.append((idx, item["source_path"], item["type"], needs_bpm, needs_key))
        if not jobs:
            return

        # Cancel previous worker if still running
        if self._detect_worker and self._detect_worker.isRunning():
            self._detect_worker.quit()

        self._detect_worker = DetectionWorker(jobs)
        self._detect_worker.detected.connect(self._on_detection_result)
        self._detect_worker.start()

    def _on_detection_result(self, row_idx: int, bpm: int, key: str):
        """Called from background thread when BPM/key analysis finishes for a file."""
        if row_idx >= len(self.import_queue):
            return
        item = self.import_queue[row_idx]
        if bpm != -1 and item["bpm"] == 0:
            item["bpm"] = bpm
        if key and not item["key"]:
            item["key"] = key

        # Update the editor fields if this row is currently selected
        selected = self._selected_rows()
        if selected and selected[0] == row_idx:
            self._block_name_sync = True
            if bpm != -1 and bpm > 0:
                self.bpm_spin.setValue(bpm)
            if key:
                self.key_edit.setText(key)
            self._block_name_sync = False

    def _append_table_row(self, idx: int, item: dict):
        self._block_name_sync = True
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)

        self.queue_table.setItem(row, 0, self._non_editable_item(Path(item["source_path"]).name))

        combo = QComboBox()
        combo.setStyleSheet("QComboBox { background-color: #2a2a2a; border: 1px solid #3e3e3e; }")
        for cat in CATEGORIES:
            combo.addItem(cat)
        combo.setCurrentText(item["category"])
        combo.currentTextChanged.connect(lambda txt, i=idx: self._row_category_changed(i, txt))
        self.queue_table.setCellWidget(row, 1, combo)

        name_item = QTableWidgetItem(item["name"])
        name_item.setToolTip("Double-click to rename directly")
        self.queue_table.setItem(row, 2, name_item)

        self.queue_table.setItem(row, 3, self._non_editable_item(""))
        self._set_status_cell(row, item["status"])
        self._block_name_sync = False

    def _set_status_cell(self, row: int, status: str):
        key = status.split(":")[0] if ":" in status else status
        cell = self._non_editable_item(status)
        cell.setTextAlignment(Qt.AlignCenter)
        cell.setForeground(QColor(self.STATUS_COLORS.get(key, "#888")))
        self.queue_table.setItem(row, 3, cell)

    def _row_category_changed(self, idx: int, text: str):
        """Inline combo change — propagate to all selected rows."""
        selected = self._selected_rows()
        rows = selected if idx in selected else [idx]
        before = self._snapshot(rows)
        for row in rows:
            if row >= len(self.import_queue):
                continue
            self.import_queue[row]["category"] = text
            if row != idx:
                combo = self.queue_table.cellWidget(row, 1)
                if combo:
                    combo.blockSignals(True)
                    combo.setCurrentText(text)
                    combo.blockSignals(False)
        self._undo_stack.push(before, self._snapshot(rows))
        # Keep the tag editor in sync
        self._block_name_sync = True
        self.category_combo.setCurrentText(text)
        self._block_name_sync = False

    def _on_table_item_changed(self, table_item: QTableWidgetItem):
        """Sync direct in-table name edits back to the queue data (with undo)."""
        if self._block_name_sync:
            return
        if table_item.column() != 2:
            return
        row = table_item.row()
        if row < len(self.import_queue):
            new_name = table_item.text().strip()
            if new_name and new_name != self.import_queue[row]["name"]:
                before = [(row, dict(self.import_queue[row]))]
                self.import_queue[row]["name"] = new_name
                self._undo_stack.push(before, [(row, dict(self.import_queue[row]))])
                selected = self._selected_rows()
                if selected and selected[0] == row:
                    self._block_name_sync = True
                    self.name_edit.setText(new_name)
                    self._block_name_sync = False

    def _clear_queue(self):
        if self.worker and self.worker.isRunning():
            return
        self.import_queue.clear()
        self.queue_table.setRowCount(0)
        self._pending_jobs.clear()
        self.gen_name_btn.setEnabled(False)
        self._update_import_btn()
        self._update_status()

    # ── Tag editor ────────────────────────────────────────────────────────────

    def _on_selection_changed(self):
        rows = sorted({i.row() for i in self.queue_table.selectedItems()})
        if not rows:
            return
        item = self.import_queue[rows[0]]
        self._block_name_sync = True
        self.name_edit.setText(item["name"])
        self.tags_edit.setText(item["tags"])
        self.category_combo.setCurrentText(item["category"])
        self.type_combo.setCurrentText(item.get("type", "—"))
        self.bpm_spin.setValue(item.get("bpm", 0))
        self.key_edit.setText(item.get("key", ""))
        self.label_edit.setText(item.get("label", ""))
        self._block_name_sync = False
        self._sync_bitwig_buttons(item["tags"])
        # Load preview for the first selected file
        self.playback_bar.load_file(item["source_path"])

    # ── Immediate multi-row field propagation ─────────────────────────────────

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.queue_table.selectedItems()})

    def _snapshot(self, rows: list[int]) -> list[tuple]:
        return [(r, dict(self.import_queue[r])) for r in rows if r < len(self.import_queue)]

    def _on_editor_category_changed(self, text: str):
        """Propagate category change to ALL selected rows immediately."""
        if self._block_name_sync:
            return
        rows = self._selected_rows()
        if not rows:
            return
        before = self._snapshot(rows)
        for row in rows:
            self.import_queue[row]["category"] = text
            combo = self.queue_table.cellWidget(row, 1)
            if combo:
                combo.blockSignals(True)
                combo.setCurrentText(text)
                combo.blockSignals(False)
        self._undo_stack.push(before, self._snapshot(rows))

    def _on_editor_type_changed(self, text: str):
        """Propagate type change to ALL selected rows immediately.
        If set to Loop, kick off BPM detection for rows that don't have one yet."""
        if self._block_name_sync:
            return
        rows = self._selected_rows()
        if not rows:
            return
        before = self._snapshot(rows)
        for row in rows:
            self.import_queue[row]["type"] = text
        self._undo_stack.push(before, self._snapshot(rows))

        # If type just became Loop, run BPM detection on rows still missing it
        if text.lower() == "loop":
            self._launch_detection_worker(rows)

    def _on_editor_name_finished(self):
        """Apply name field to the single selected row (name is per-file)."""
        if self._block_name_sync:
            return
        rows = self._selected_rows()
        if len(rows) != 1:
            return
        name = self.name_edit.text().strip()
        if not name:
            return
        before = self._snapshot(rows)
        self.import_queue[rows[0]]["name"] = name
        self._block_name_sync = True
        self.queue_table.item(rows[0], 2).setText(name)
        self._block_name_sync = False
        self._undo_stack.push(before, self._snapshot(rows))

    def _on_editor_tags_finished(self):
        """Propagate tags field to ALL selected rows."""
        if self._block_name_sync:
            return
        rows = self._selected_rows()
        if not rows:
            return
        value = self.tags_edit.text().strip()
        before = self._snapshot(rows)
        for row in rows:
            self.import_queue[row]["tags"] = value
        self._sync_bitwig_buttons(value)
        self._undo_stack.push(before, self._snapshot(rows))

    def _on_editor_bpm_finished(self):
        """Propagate BPM to ALL selected rows."""
        if self._block_name_sync:
            return
        rows = self._selected_rows()
        if not rows:
            return
        value = self.bpm_spin.value()
        before = self._snapshot(rows)
        for row in rows:
            self.import_queue[row]["bpm"] = value
        self._undo_stack.push(before, self._snapshot(rows))

    def _on_editor_key_finished(self):
        """Propagate key field to ALL selected rows."""
        if self._block_name_sync:
            return
        rows = self._selected_rows()
        if not rows:
            return
        value = self.key_edit.text().strip()
        before = self._snapshot(rows)
        for row in rows:
            self.import_queue[row]["key"] = value
        self._undo_stack.push(before, self._snapshot(rows))

    def _on_editor_label_finished(self):
        """Propagate label field to ALL selected rows."""
        if self._block_name_sync:
            return
        rows = self._selected_rows()
        if not rows:
            return
        value = self.label_edit.text().strip()
        before = self._snapshot(rows)
        for row in rows:
            self.import_queue[row]["label"] = value
        self._undo_stack.push(before, self._snapshot(rows))

    def _on_bitwig_tag_toggled(self, tag: str, checked: bool):
        """Toggle a Bitwig tag, persist to all selected rows, keep table focused."""
        current = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        if checked and tag not in current:
            current.append(tag)
        elif not checked and tag in current:
            current.remove(tag)
        new_tags = ", ".join(current)
        self.tags_edit.setText(new_tags)
        rows = self._selected_rows()
        before = self._snapshot(rows)
        for row in rows:
            if row < len(self.import_queue):
                self.import_queue[row]["tags"] = new_tags
        self._undo_stack.push(before, self._snapshot(rows))
        # Return focus to the queue so arrow keys keep navigating
        self.queue_table.setFocus()

    def _rebuild_custom_tag_grid(self):
        """Clear and repopulate the custom tag button grid from self._custom_tags."""
        # Remove old buttons
        while self._custom_grid.count():
            item = self._custom_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._custom_tag_btns.clear()

        tag_btn_css = """
            QPushButton {
                background: #222; color: #777; border: 1px solid #3a3a3a;
                border-radius: 4px; padding: 3px 10px; font-size: 12px;
            }
            QPushButton:hover   { border-color: #666; color: #aaa; }
            QPushButton:checked {
                background: #1A3A00; color: #7EC820;
                border-color: #4A8800;
            }
        """
        cols = 4
        for i, tag in enumerate(self._custom_tags):
            btn = QPushButton(tag)
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setStyleSheet(tag_btn_css)
            btn.toggled.connect(lambda checked, t=tag: self._on_bitwig_tag_toggled(t, checked))
            self._custom_tag_btns[tag] = btn
            self._custom_grid.addWidget(btn, i // cols, i % cols)

    def _open_custom_tags_dialog(self):
        """Open a simple text editor to define custom quick-tag buttons."""
        from PySide6.QtWidgets import QPlainTextEdit

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Custom Tags")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(self.styleSheet())
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)

        lbl = QLabel("Enter your custom tags, one per line.\n"
                     "These appear as quick-tag buttons and are written to file\n"
                     "metadata for search in Bitwig and other DAWs.")
        lbl.setStyleSheet("color: #999; font-size: 12px;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("808\n909\ncrispy\nmy-label\n...")
        text_edit.setPlainText("\n".join(self._custom_tags))
        text_edit.setStyleSheet(
            "QPlainTextEdit { background: #2a2a2a; border: 1px solid #444; "
            "border-radius: 4px; color: #e0e0e0; padding: 6px; font-size: 13px; }"
        )
        layout.addWidget(text_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.setStyleSheet("QPushButton { min-width: 80px; }")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() == QDialog.Accepted:
            tags = [t.strip().lower() for t in text_edit.toPlainText().splitlines() if t.strip()]
            seen: set = set()
            self._custom_tags = [t for t in tags if not (t in seen or seen.add(t))]
            self.db.save_setting("custom_tags", json.dumps(self._custom_tags))
            self._rebuild_custom_tag_grid()

    def _sync_bitwig_buttons(self, tags_text: str):
        """Reflect tags_text in both the Bitwig and custom quick-tag button states."""
        active = {t.strip().lower() for t in tags_text.split(",") if t.strip()}
        for tag, btn in self._bitwig_tag_btns.items():
            btn.blockSignals(True)
            btn.setChecked(tag.lower() in active)
            btn.blockSignals(False)
        for tag, btn in self._custom_tag_btns.items():
            btn.blockSignals(True)
            btn.setChecked(tag.lower() in active)
            btn.blockSignals(False)

    def _on_volume_changed(self, value: int):
        self.playback_bar.set_volume(value / 100.0)

    def _apply_to_selected(self):
        rows = self._selected_rows()
        if not rows:
            return
        single = len(rows) == 1
        before = self._snapshot(rows)
        for row in rows:
            self._write_tags_to_row(row, update_name=single)
        self._undo_stack.push(before, self._snapshot(rows))

    def _apply_to_all(self):
        rows = list(range(len(self.import_queue)))
        before = self._snapshot(rows)
        for row in rows:
            self._write_tags_to_row(row, update_name=False)
        self._undo_stack.push(before, self._snapshot(rows))

    def _write_tags_to_row(self, row: int, update_name: bool):
        item = self.import_queue[row]
        if update_name:
            name = self.name_edit.text().strip()
            if name:
                item["name"] = name
        item["category"] = self.category_combo.currentText()
        item["type"] = self.type_combo.currentText()
        item["tags"] = self.tags_edit.text().strip()
        item["bpm"] = self.bpm_spin.value()
        item["key"] = self.key_edit.text().strip()
        item["label"] = self.label_edit.text().strip()

        # Sync table
        self._block_name_sync = True
        self.queue_table.item(row, 2).setText(item["name"])
        combo = self.queue_table.cellWidget(row, 1)
        if combo:
            combo.setCurrentText(item["category"])
        self._block_name_sync = False

    def _label_subfolder_for(self, item: dict) -> str:
        """Return the sanitised label subfolder name, or '' if not enabled."""
        if self.label_subfolder_check.isChecked() and item.get("label", "").strip():
            return item["label"].strip().replace(" ", "-")
        return ""

    def _generate_name(self):
        """
        Build Bitwig-friendly filenames for selected rows (or all if none selected).

        Numbers are assigned per (category, label-subfolder) group, continuing
        from however many files already exist in that folder.
        """
        rows = sorted({i.row() for i in self.queue_table.selectedItems()})
        if not rows:
            rows = list(range(len(self.import_queue)))
        before = self._snapshot(rows)

        # Find starting count for every (category, subfolder) group
        group_next: dict[tuple, int] = {}
        for row in rows:
            item = self.import_queue[row]
            key = (item["category"], self._label_subfolder_for(item))
            if key not in group_next:
                cat, sub = key
                group_next[key] = count_existing_files(self.library_root, cat, sub)

        # Assign sequential numbers within each group
        for row in rows:
            item = self.import_queue[row]
            key = (item["category"], self._label_subfolder_for(item))
            group_next[key] += 1
            number = group_next[key]

            generated = build_filename_from_format(
                tokens=self._format_tokens,
                separator=self._format_separator,
                name=item["name"],
                tags=item["tags"],
                bpm=item.get("bpm", 0),
                key=item.get("key", ""),
                category=item["category"],
                type=item.get("type", ""),
                label=item.get("label", ""),
                number=number,
            )
            stem = Path(generated).stem
            item["name"] = stem
            self._block_name_sync = True
            self.queue_table.item(row, 2).setText(stem)
            self._block_name_sync = False

        self._undo_stack.push(before, self._snapshot(rows))

    # ── Import / conversion ───────────────────────────────────────────────────

    def _start_import(self):
        if not self.library_root:
            QMessageBox.warning(self, "No Library", "Please set a library folder first.")
            return

        ext = FORMAT_EXTENSION.get(self._output_format, ".aif")
        jobs = []
        for idx, item in enumerate(self.import_queue):
            if item["status"] != "Ready":
                continue
            raw_target = get_target_path(
                self.library_root,
                item["category"],
                item["name"] + ext,
                label_subfolder=self._label_subfolder_for(item),
            )
            target = unique_path(raw_target)
            jobs.append({
                "queue_index": idx,
                "source_path": item["source_path"],
                "target_path": str(target),
                "out_format":  self._output_format,
                "metadata": {
                    "name":     item["name"],
                    "category": item["category"],
                    # Write tags to metadata if force_id3_tags is on, OR if
                    # the Tags token is included in the active naming format
                    # (in which case they're already in the filename anyway).
                    "tags":     item["tags"] if (
                        self._force_id3_tags or "tags" in self._format_tokens
                    ) else "",
                    "bpm":      item.get("bpm", 0),
                    "key":      item.get("key", ""),
                    "label":    item.get("label", ""),
                    "type":     item.get("type", ""),
                },
            })
            self._pending_jobs[idx] = {"target_path": str(target), "item": item}

        if not jobs:
            return

        self.import_btn.setEnabled(False)
        self.worker = ConvertWorker(jobs)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_import_finished)
        self.worker.start()

    def _on_progress(self, queue_index: int, status: str):
        self.import_queue[queue_index]["status"] = status
        self._set_status_cell(queue_index, status)

        if status == "Done":
            job = self._pending_jobs.get(queue_index, {})
            item = self.import_queue[queue_index]
            self.db.save_sample({
                "original_path": item["source_path"],
                "library_path":  job.get("target_path", ""),
                "name":          item["name"],
                "category":      item["category"],
                "tags":          item["tags"],
                "bpm":           item.get("bpm", 0),
                "key":           item.get("key", ""),
                "label":         item.get("label", ""),
                "date_added":    datetime.now().isoformat(),
            })

        self._update_status()

    def _on_import_finished(self):
        self.import_btn.setEnabled(True)
        self._refresh_tree()
        self._update_status()
        self.status_bar.showMessage("Import complete.", 5000)

    # ── Misc ──────────────────────────────────────────────────────────────────

    def _update_import_btn(self):
        ready = any(i["status"] == "Ready" for i in self.import_queue)
        self.import_btn.setEnabled(bool(self.library_root) and ready)

    def _update_status(self):
        total = len(self.import_queue)
        if total == 0:
            self.status_bar.showMessage("Ready — drag audio files into the queue")
            return
        done  = sum(1 for i in self.import_queue if i["status"] == "Done")
        ready = sum(1 for i in self.import_queue if i["status"] == "Ready")
        self.status_bar.showMessage(
            f"{total} files in queue  ·  {ready} ready  ·  {done} imported"
        )

    # ── Undo / Redo ───────────────────────────────────────────────────────────

    def _undo(self):
        state = self._undo_stack.undo()
        if state:
            self._restore_state(state)
            self.status_bar.showMessage("Undo", 2000)

    def _redo(self):
        state = self._undo_stack.redo()
        if state:
            self._restore_state(state)
            self.status_bar.showMessage("Redo", 2000)

    def _restore_state(self, state: list[tuple]):
        """Apply a list of (row, item_dict) snapshots back to the queue and table."""
        self._block_name_sync = True
        for row, item_copy in state:
            if row >= len(self.import_queue):
                continue
            self.import_queue[row].update(item_copy)
            # Refresh name column
            name_cell = self.queue_table.item(row, 2)
            if name_cell:
                name_cell.setText(item_copy["name"])
            # Refresh inline category combo
            combo = self.queue_table.cellWidget(row, 1)
            if combo:
                combo.blockSignals(True)
                combo.setCurrentText(item_copy["category"])
                combo.blockSignals(False)
            self._set_status_cell(row, item_copy["status"])
        self._block_name_sync = False
        # Refresh tag editor for current selection
        self._on_selection_changed()

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
            self.worker.wait()
        self.playback_bar.stop_playback()
        self.db.close()
        super().closeEvent(event)
