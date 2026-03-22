import json
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QFrame,
    QAbstractItemView, QDialogButtonBox, QSizePolicy, QRadioButton,
    QButtonGroup, QGroupBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from core.organizer import build_filename_from_format


# ── Token definitions ─────────────────────────────────────────────────────────

TOKENS = [
    ("number",   "# Number",      "#3a6ea8"),
    ("name",     "Name",          "#4a7a4a"),
    ("category", "Category",      "#7a4a7a"),
    ("type",     "Type",          "#7a6a3a"),
    ("key",      "Key / Pitch",   "#3a7a7a"),
    ("bpm",      "BPM",           "#7a4a4a"),
    ("tags",     "Tags",          "#5a5a7a"),
    ("label",    "Label",         "#6a5a3a"),
]

TOKEN_BY_KEY = {k: (label, color) for k, label, color in TOKENS}

SEPARATORS = [
    ("_", "Underscore  ( _ )"),
    ("-", "Hyphen  ( - )"),
    (".", "Dot  ( . )"),
    (",", "Comma  ( , )"),
]

# Output format options: (key, display label, description)
OUTPUT_FORMATS = [
    ("aif",  "AIFF",  "ID3 tags in AIFF container  —  best Bitwig compatibility"),
    ("wav",  "WAV",   "ID3 tags in RIFF/WAV container  —  most universal format"),
    ("flac", "FLAC",  "Vorbis comment tags  —  open standard, lossless"),
]

# Sample values used for the live preview
PREVIEW_DATA = {
    "number":   42,
    "name":     "kick",
    "tags":     "dark, punchy, 909",
    "bpm":      120,
    "key":      "Am",
    "category": "Drums / Kicks",
    "type":     "One-Shot",
    "label":    "Loopmasters",
}


class TokenChip(QPushButton):
    """A coloured pill button representing one naming token."""

    def __init__(self, key: str, label: str, color: str):
        super().__init__(label)
        self.token_key = key
        self._color = color
        self._active = True
        self._refresh_style()
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_active(self, active: bool):
        self._active = active
        self._refresh_style()

    def _refresh_style(self):
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self._color};
                    color: #fff;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-size: 12px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {self._color}cc;
                }}
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #2a2a2a;
                    color: #444;
                    border: 1px solid #333;
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-size: 12px;
                }
            """)
        self.setEnabled(self._active)


class SettingsDialog(QDialog):
    # tokens list, separator char, output format key ('aif'/'wav'/'flac')
    format_saved = Signal(list, str, str)

    def __init__(self, current_tokens: list, current_separator: str,
                 current_format: str = "aif", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        self.setModal(True)

        self._active_tokens: list[str] = list(current_tokens)
        self._separator: str = current_separator
        self._output_format: str = current_format
        self._chips: dict[str, TokenChip] = {}
        self._format_radios: dict[str, QRadioButton] = {}

        self._build_ui()
        self._populate_active_list()
        self._refresh_chips()
        self._refresh_preview()

        self.setStyleSheet("""
            QDialog, QWidget { background-color: #1c1c1c; color: #e0e0e0;
                font-family: -apple-system, "SF Pro Text", sans-serif; font-size: 13px; }
            QGroupBox {
                border: 1px solid #333; border-radius: 6px;
                margin-top: 10px; padding: 10px 12px 12px 12px;
                color: #555; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; subcontrol-position: top left;
                padding: 0 6px; left: 10px;
            }
            QListWidget { background-color: #252525; border: 1px solid #3a3a3a;
                          border-radius: 6px; }
            QListWidget::item { padding: 6px 10px; border-bottom: 1px solid #2e2e2e; }
            QListWidget::item:selected { background-color: #7A3A00; }
            QListWidget::item:hover { background-color: #2e2e2e; }
            QComboBox { background-color: #2a2a2a; border: 1px solid #444;
                        border-radius: 4px; padding: 4px 8px; color: #e0e0e0; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #2a2a2a;
                                          selection-background-color: #7A3A00; }
            QPushButton { background-color: #333; color: #e0e0e0;
                border: 1px solid #4a4a4a; border-radius: 5px; padding: 5px 14px; }
            QPushButton:hover { background-color: #3e3e3e; }
            QPushButton#primary { background-color: #C86000; border-color: #E07010; color: #fff; }
            QPushButton#primary:hover { background-color: #E07010; }
            QFrame#divider { background-color: #333; }
            QLabel#preview_value {
                background-color: #252525; border: 1px solid #3a3a3a;
                border-radius: 6px; padding: 10px 14px;
                color: #E07820; font-family: "SF Mono", Menlo, monospace; font-size: 13px;
            }
            QRadioButton { color: #ccc; spacing: 8px; }
            QRadioButton::indicator { width: 14px; height: 14px; border-radius: 7px; }
            QRadioButton::indicator:unchecked {
                background: #2a2a2a; border: 1px solid #555;
            }
            QRadioButton::indicator:checked {
                background: #C86000; border: 2px solid #E07010;
            }
        """)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # ── Output Format ──────────────────────────────────────────────────
        fmt_group = QGroupBox("OUTPUT FORMAT")
        fmt_layout = QVBoxLayout(fmt_group)
        fmt_layout.setSpacing(10)

        btn_group = QButtonGroup(self)
        for key, label, desc in OUTPUT_FORMATS:
            row = QHBoxLayout()
            row.setSpacing(12)
            radio = QRadioButton(label)
            radio.setChecked(key == self._output_format)
            radio.toggled.connect(lambda checked, k=key: self._on_format_changed(k, checked))
            btn_group.addButton(radio)
            self._format_radios[key] = radio
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: #555; font-size: 11px;")
            row.addWidget(radio)
            row.addWidget(desc_lbl)
            row.addStretch()
            fmt_layout.addLayout(row)

        layout.addWidget(fmt_group)

        # ── Naming Format ──────────────────────────────────────────────────
        naming_group = QGroupBox("NAMING FORMAT")
        naming_layout = QVBoxLayout(naming_group)
        naming_layout.setSpacing(12)

        naming_layout.addWidget(self._small_label(
            "AVAILABLE TOKENS   —   click to add  ·  double-click active item to remove"
        ))
        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        for key, label, color in TOKENS:
            chip = TokenChip(key, label, color)
            chip.clicked.connect(lambda _, k=key: self._add_token(k))
            self._chips[key] = chip
            chip_row.addWidget(chip)
        chip_row.addStretch()
        naming_layout.addLayout(chip_row)

        naming_layout.addWidget(self._small_label("ACTIVE FORMAT   —   drag to reorder"))
        self.active_list = QListWidget()
        self.active_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.active_list.setDefaultDropAction(Qt.MoveAction)
        self.active_list.setFixedHeight(160)
        self.active_list.itemDoubleClicked.connect(self._remove_token)
        self.active_list.model().rowsMoved.connect(self._on_reorder)
        naming_layout.addWidget(self.active_list)

        sep_row = QHBoxLayout()
        sep_row.addWidget(self._small_label("SEPARATOR"))
        sep_row.addSpacing(10)
        self.sep_combo = QComboBox()
        for value, lbl in SEPARATORS:
            self.sep_combo.addItem(lbl, userData=value)
        for i, (value, _) in enumerate(SEPARATORS):
            if value == self._separator:
                self.sep_combo.setCurrentIndex(i)
        self.sep_combo.currentIndexChanged.connect(self._on_separator_changed)
        self.sep_combo.setFixedWidth(220)
        sep_row.addWidget(self.sep_combo)
        sep_row.addStretch()
        naming_layout.addLayout(sep_row)

        naming_layout.addWidget(self._small_label("PREVIEW"))
        self.preview_label = QLabel()
        self.preview_label.setObjectName("preview_value")
        self.preview_label.setWordWrap(True)
        naming_layout.addWidget(self.preview_label)

        layout.addWidget(naming_group)

        # ── Buttons ────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _small_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #555; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;")
        return lbl

    def _populate_active_list(self):
        self.active_list.clear()
        for key in self._active_tokens:
            self._add_list_item(key)

    def _add_list_item(self, key: str):
        label, color = TOKEN_BY_KEY.get(key, (key, "#555"))
        item = QListWidgetItem(f"  ≡   {label}")
        item.setData(Qt.UserRole, key)
        item.setForeground(self._hex_to_color(color))
        font = QFont()
        font.setPointSize(13)
        item.setFont(font)
        self.active_list.addItem(item)

    @staticmethod
    def _hex_to_color(hex_color: str):
        from PySide6.QtGui import QColor
        return QColor(hex_color).lighter(160)

    def _refresh_chips(self):
        active = set(self._active_tokens)
        for key, chip in self._chips.items():
            chip.set_active(key not in active)

    def _refresh_preview(self):
        ext = {"aif": ".aif", "wav": ".wav", "flac": ".flac"}.get(self._output_format, ".aif")
        filename = build_filename_from_format(
            tokens=self._active_tokens,
            separator=self._separator,
            **PREVIEW_DATA,
        ) + ext
        self.preview_label.setText(filename)

    def _sync_active_tokens(self):
        self._active_tokens = [
            self.active_list.item(i).data(Qt.UserRole)
            for i in range(self.active_list.count())
        ]

    # ── Interactions ──────────────────────────────────────────────────────────

    def _on_format_changed(self, key: str, checked: bool):
        if checked:
            self._output_format = key
            self._refresh_preview()

    def _add_token(self, key: str):
        if key in self._active_tokens:
            return
        self._active_tokens.append(key)
        self._add_list_item(key)
        self._refresh_chips()
        self._refresh_preview()

    def _remove_token(self, item: QListWidgetItem):
        row = self.active_list.row(item)
        self.active_list.takeItem(row)
        self._sync_active_tokens()
        self._refresh_chips()
        self._refresh_preview()

    def _on_reorder(self):
        self._sync_active_tokens()
        self._refresh_preview()

    def _on_separator_changed(self):
        self._separator = self.sep_combo.currentData()
        self._refresh_preview()

    def _save(self):
        self._sync_active_tokens()
        self.format_saved.emit(self._active_tokens, self._separator, self._output_format)
        self.accept()


# Backward-compat alias
NamingFormatDialog = SettingsDialog
