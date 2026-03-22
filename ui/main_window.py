import json
import subprocess
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QLineEdit, QComboBox, QFileDialog,
    QHeaderView, QAbstractItemView, QMessageBox, QSpinBox,
    QToolBar, QStatusBar, QCheckBox, QSlider, QFrame, QGridLayout,
    QListWidget, QListWidgetItem, QMenu,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut

from ui.playback_bar import PlaybackBar

from core.database import Database
from ui.settings_dialog import NamingFormatDialog
from core.converter import convert_to_aiff
from core.organizer import (
    CATEGORIES, FILE_EXTENSION,
    get_target_path, unique_path, ensure_library_structure,
    build_filename_from_format, count_existing_files,
    DEFAULT_FORMAT_TOKENS, DEFAULT_SEPARATOR,
)


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
            ok, err = convert_to_aiff(
                job["source_path"],
                job["target_path"],
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

        db_dir = Path.home() / ".tagwig"
        db_dir.mkdir(exist_ok=True)
        self.db = Database(str(db_dir / "library.db"))

        self.library_root: str = self.db.get_setting("library_root", "")
        self.import_queue: list[dict] = []
        self.worker: ConvertWorker | None = None
        self._pending_jobs: dict[int, dict] = {}
        self._block_name_sync = False

        # Naming format — loaded from DB, editable via Settings dialog
        saved_tokens = self.db.get_setting("naming_tokens", "")
        self._format_tokens: list = (
            json.loads(saved_tokens) if saved_tokens else list(DEFAULT_FORMAT_TOKENS)
        )
        self._format_separator: str = self.db.get_setting("naming_separator", DEFAULT_SEPARATOR)
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
        btn = QPushButton("Set Library Folder")
        btn.clicked.connect(self._pick_library)
        tb.addWidget(btn)

        naming_btn = QPushButton("Naming Format…")
        naming_btn.clicked.connect(self._open_naming_settings)
        tb.addWidget(naming_btn)
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
        self.file_list.setStyleSheet("""
            QListWidget { background-color: #1e1e1e; border: none; }
            QListWidget::item { padding: 3px 10px; color: #aaa; font-size: 12px; }
            QListWidget::item:selected { background-color: #7A3A00; color: #fff; }
            QListWidget::item:hover { background-color: #2a2a2a; }
        """)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._on_file_list_context_menu)
        self.file_list.itemClicked.connect(self._on_file_list_clicked)
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

        self.apply_all_btn = QPushButton("Apply to All")
        self.apply_all_btn.setFixedWidth(100)
        self.apply_all_btn.setEnabled(False)
        self.apply_all_btn.clicked.connect(self._apply_to_all)

        self.apply_btn = QPushButton("Apply to Selected")
        self.apply_btn.setFixedWidth(130)
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply_to_selected)

        header_row.addWidget(lbl)
        header_row.addStretch()
        header_row.addWidget(self.gen_name_btn)
        header_row.addWidget(self.apply_all_btn)
        header_row.addWidget(self.apply_btn)
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
        left.addWidget(self.name_edit)

        left.addWidget(self._field_label("Tags  (comma separated)"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("e.g.  dark, punchy, 909, analogue")
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
        bpm_col.addWidget(self.bpm_spin)
        meta.addLayout(bpm_col)

        key_col = QVBoxLayout()
        key_col.setSpacing(3)
        key_col.addWidget(self._field_label("Key"))
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("e.g. Am")
        self.key_edit.setFixedWidth(72)
        key_col.addWidget(self.key_edit)
        meta.addLayout(key_col)
        meta.addStretch()
        right.addLayout(meta)

        right.addWidget(self._field_label("Label  (pack creator / publisher)"))
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("e.g.  Loopmasters, Splice, ADSR")
        right.addWidget(self.label_edit)

        _chk_css = """
            QCheckBox { color: #666; font-size: 11px; }
            QCheckBox::indicator { width: 13px; height: 13px; border-radius: 2px; }
            QCheckBox::indicator:unchecked { background: #2a2a2a; border: 1px solid #444; }
            QCheckBox::indicator:checked   { background: #C86000; border: 1px solid #E07010; }
        """
        self.label_subfolder_check = QCheckBox("Create label subfolders")
        self.label_subfolder_check.setStyleSheet(_chk_css)
        self.label_subfolder_check.setToolTip(
            "Places files in  Category / Label /  instead of  Category /\n"
            "Numbering restarts from 0001 per label subfolder."
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
                btn.setFocusPolicy(Qt.NoFocus)   # never steal focus from the queue table
                btn.setStyleSheet(tag_btn_css)
                btn.toggled.connect(lambda checked, t=tag: self._on_bitwig_tag_toggled(t, checked))
                self._bitwig_tag_btns[tag] = btn
                grid.addWidget(btn, r, c)
        layout.addLayout(grid)

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
        dlg = NamingFormatDialog(
            current_tokens=self._format_tokens,
            current_separator=self._format_separator,
            parent=self,
        )
        dlg.format_saved.connect(self._on_format_saved)
        dlg.exec()

    def _on_format_saved(self, tokens: list, separator: str):
        self._format_tokens = tokens
        self._format_separator = separator
        self.db.save_setting("naming_tokens", json.dumps(tokens))
        self.db.save_setting("naming_separator", separator)
        self.status_bar.showMessage("Naming format saved.", 4000)

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
        item = self.file_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #2a2a2a; border: 1px solid #3a3a3a; color: #e0e0e0; }
            QMenu::item:selected { background: #7A3A00; }
        """)
        act = menu.addAction("Open in Finder")
        # Reveal the file itself (not just the folder)
        act.triggered.connect(lambda: subprocess.Popen(["open", "-R", path]))
        menu.exec(self.file_list.viewport().mapToGlobal(pos))

    # ── Queue ─────────────────────────────────────────────────────────────────

    def _on_files_dropped(self, paths: list[str]):
        existing = {item["source_path"] for item in self.import_queue}
        added = 0
        for path in paths:
            if path in existing:
                continue
            p = Path(path)
            item = {
                "source_path": path,
                "name": p.stem,
                "category": "Uncategorised",
                "type": "—",
                "tags": "",
                "bpm": 0,
                "key": "",
                "label": "",
                "status": "Ready",
            }
            idx = len(self.import_queue)
            self.import_queue.append(item)
            self._append_table_row(idx, item)
            added += 1

        if added:
            self._update_import_btn()
            self.apply_all_btn.setEnabled(True)
            self.gen_name_btn.setEnabled(True)
            self._update_status()

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
        self.apply_btn.setEnabled(False)
        self.apply_all_btn.setEnabled(False)
        self.gen_name_btn.setEnabled(False)
        self._update_import_btn()
        self._update_status()

    # ── Tag editor ────────────────────────────────────────────────────────────

    def _on_selection_changed(self):
        rows = sorted({i.row() for i in self.queue_table.selectedItems()})
        if not rows:
            self.apply_btn.setEnabled(False)
            return
        self.apply_btn.setEnabled(True)
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
        """Propagate type change to ALL selected rows immediately."""
        if self._block_name_sync:
            return
        rows = self._selected_rows()
        if not rows:
            return
        before = self._snapshot(rows)
        for row in rows:
            self.import_queue[row]["type"] = text
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

    def _sync_bitwig_buttons(self, tags_text: str):
        """Reflect tags_text in the Bitwig quick-tag button states."""
        active = {t.strip().lower() for t in tags_text.split(",") if t.strip()}
        for tag, btn in self._bitwig_tag_btns.items():
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

        jobs = []
        for idx, item in enumerate(self.import_queue):
            if item["status"] != "Ready":
                continue
            raw_target = get_target_path(
                self.library_root,
                item["category"],
                item["name"] + FILE_EXTENSION,
                label_subfolder=self._label_subfolder_for(item),
            )
            target = unique_path(raw_target)
            jobs.append({
                "queue_index": idx,
                "source_path": item["source_path"],
                "target_path": str(target),
                "metadata": {
                    "name":     item["name"],
                    "category": item["category"],
                    "tags":     item["tags"],
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
