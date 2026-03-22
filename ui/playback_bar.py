"""
PlaybackBar — waveform display + transport controls.

Play button acts as Play when stopped, Stop when playing (no pause).
Auto-play toggle fires playback automatically on load_file().
Volume is driven externally via set_volume().
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget


# ── Background waveform loader ────────────────────────────────────────────────

class WaveformLoader(QThread):
    loaded = Signal(list, float)   # peaks, duration_seconds
    N_COLS = 600

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            data, sr = sf.read(self.path, always_2d=True)
            duration = len(data) / sr
            mono = data.mean(axis=1)
            block = max(1, len(mono) // self.N_COLS)
            peaks: list[float] = []
            for i in range(self.N_COLS):
                chunk = mono[i * block:(i + 1) * block]
                if len(chunk):
                    peaks.append(float(np.max(np.abs(chunk))))
            if peaks:
                mx = max(peaks) or 1.0
                peaks = [v / mx for v in peaks]
            self.loaded.emit(peaks, duration)
        except Exception as exc:
            print(f"[WaveformLoader] {exc}")
            self.loaded.emit([], 0.0)


# ── Waveform canvas ───────────────────────────────────────────────────────────

class WaveformWidget(QWidget):
    seek_requested = Signal(float)   # 0.0–1.0

    _PLAYED   = QColor("#C96000")
    _UNPLAYED = QColor("#3e3e3e")
    _HEAD     = QColor("#E07820")
    _BG       = QColor("#1a1a1a")
    _EMPTY    = QColor("#2e2e2e")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._peaks: list[float] = []
        self._position: float = 0.0
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to seek")

    def set_peaks(self, peaks: list[float]):
        self._peaks = peaks
        self.update()

    def set_position(self, pos: float):
        self._position = max(0.0, min(1.0, pos))
        self.update()

    def clear(self):
        self._peaks = []
        self._position = 0.0
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        mid = h / 2
        p.fillRect(0, 0, w, h, self._BG)

        if not self._peaks:
            p.setPen(QPen(self._EMPTY, 1))
            p.drawLine(0, int(mid), w, int(mid))
            p.end()
            return

        pos_x = self._position * w
        n = len(self._peaks)
        col_w = w / n
        for i, peak in enumerate(self._peaks):
            x = i * col_w
            bar_h = max(1, int(peak * mid * 0.88))
            color = self._PLAYED if (x + col_w / 2) <= pos_x else self._UNPLAYED
            p.fillRect(int(x), int(mid - bar_h), max(1, int(col_w)), bar_h * 2, color)

        p.setPen(QPen(self._HEAD, 1))
        p.drawLine(int(pos_x), 0, int(pos_x), h)
        p.end()

    def mousePressEvent(self, event):
        if self._peaks and self.width() > 0:
            self.seek_requested.emit(
                max(0.0, min(1.0, event.position().x() / self.width()))
            )


# ── Playback bar ──────────────────────────────────────────────────────────────

class PlaybackBar(QWidget):
    """
    [▶/■]  [AUTO]  [~~~~waveform~~~~]  0:03 / 0:08
    """

    _BTN = """
        QPushButton {
            background: #C86000; color: #fff;
            border: 1px solid #E07010; border-radius: 4px;
            padding: 3px 12px; font-size: 15px; min-width: 34px;
        }
        QPushButton:hover   { background: #E07010; border-color: #F08020; }
        QPushButton:pressed { background: #9a4a00; }
        QPushButton:disabled { background: #5A2A00; color: #7a4a20; border-color: #4a2000; }
    """

    _AUTO_OFF = """
        QPushButton {
            background: #252525; color: #555;
            border: 1px solid #333; border-radius: 4px;
            padding: 3px 10px; font-size: 11px; font-weight: bold;
        }
        QPushButton:hover { border-color: #666; color: #888; }
    """

    _AUTO_ON = """
        QPushButton {
            background: #4A1E00; color: #E07820;
            border: 1px solid #C86000; border-radius: 4px;
            padding: 3px 10px; font-size: 11px; font-weight: bold;
        }
        QPushButton:hover { background: #5A2A00; }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms: int = 0
        self._current_path: str = ""
        self._loader: WaveformLoader | None = None
        self._auto_play: bool = False

        self.player = QMediaPlayer()
        self.audio_out = QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.audio_out.setVolume(0.8)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        # Play / Stop toggle
        self.play_btn = QPushButton("▶")
        self.play_btn.setToolTip("Play  /  Stop")
        self.play_btn.setStyleSheet(self._BTN)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle)
        layout.addWidget(self.play_btn)

        # Auto-play toggle
        self.auto_btn = QPushButton("AUTO")
        self.auto_btn.setToolTip("Auto-play on file selection")
        self.auto_btn.setCheckable(True)
        self.auto_btn.setStyleSheet(self._AUTO_OFF)
        self.auto_btn.toggled.connect(self._on_auto_toggled)
        layout.addWidget(self.auto_btn)

        # Waveform
        self.waveform = WaveformWidget()
        self.waveform.seek_requested.connect(self._seek)
        layout.addWidget(self.waveform, 1)

        # Time
        self.time_lbl = QLabel("—:—— / —:——")
        self.time_lbl.setStyleSheet(
            "color: #555; font-family: 'SF Mono', Menlo, monospace; font-size: 11px;"
        )
        self.time_lbl.setFixedWidth(98)
        self.time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.time_lbl)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_file(self, path: str):
        if path == self._current_path:
            return
        self._current_path = path
        self._hard_stop()
        self.waveform.clear()
        self.time_lbl.setText("—:—— / —:——")
        self.play_btn.setEnabled(False)

        self.player.setSource(QUrl.fromLocalFile(path))

        if self._loader and self._loader.isRunning():
            self._loader.terminate()
            self._loader.wait()
        self._loader = WaveformLoader(path)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.start()

    def set_volume(self, v: float):
        self.audio_out.setVolume(v)

    def stop_playback(self):
        self._hard_stop()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_auto_toggled(self, checked: bool):
        self._auto_play = checked
        self.auto_btn.setStyleSheet(self._AUTO_ON if checked else self._AUTO_OFF)

    def _on_loaded(self, peaks: list, _dur: float):
        self.waveform.set_peaks(peaks)
        self.play_btn.setEnabled(bool(peaks))

    def _on_media_status(self, status):
        # Fire auto-play once the source is fully loaded
        if status == QMediaPlayer.LoadedMedia and self._auto_play:
            self.player.play()

    def _toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            # Stop and rewind
            self._hard_stop()
        else:
            self.player.play()

    def _hard_stop(self):
        self.player.stop()
        self.waveform.set_position(0.0)

    def _seek(self, ratio: float):
        if self._duration_ms > 0:
            self.player.setPosition(int(ratio * self._duration_ms))

    def _on_position(self, ms: int):
        if self._duration_ms > 0:
            self.waveform.set_position(ms / self._duration_ms)
            self.time_lbl.setText(f"{self._fmt(ms)}  /  {self._fmt(self._duration_ms)}")

    def _on_duration(self, ms: int):
        self._duration_ms = ms

    def _on_state(self, state):
        self.play_btn.setText("■" if state == QMediaPlayer.PlayingState else "▶")

    @staticmethod
    def _fmt(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"
