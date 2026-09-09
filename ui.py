"""
ECG-Format-Converter — Desktop Graphical Interface.

A lightweight, non-blocking PySide6 GUI frontend for the `ExecutionRunner` 
engine. Provides batch configuration, real-time background task execution,
interactive 12-lead signal inspection via Matplotlib, and tabular run summaries.

Architecture & Responsibilities:
--------------------------------
• UI & Execution Decoupling: Contains zero signal-processing or format conversion
  logic. Gathers user parameters, synchronizes configuration defaults, and
  dispatches tasks asynchronously to background worker threads (`QThread`).
• Non-Blocking Execution:
    - `ScanWorker`: Recursively inspects input paths, tallies supported formats
      (WFDB, DICOM, XML, HL7, CSV, ASC, MAT), and detects rate-less inputs.
    - `ProcessWorker`: Drives `runner.execution_runner.ExecutionRunner` with real-time
      cancellation hooks, progress monitoring, and memory-retained signals for plotting.
• Dark-Themed Design: Native rendering with zero external asset dependencies;
  draws custom vector glyphs, dynamic path-based icons, and responsive layouts.

Application Layout:
-------------------
+-----------------------------------------------------------------------------+
| Header Strip:                                                               |
+------------------------------+----------------------------------------------+
| Settings Pane (Left, 440px+) | Results & Visualization Pane (Right)         |
|                              |                                              |
| • Input: File/Dir & Scan Info| • Status Chips (Processed / Skipped / Failed)|
| • Output: Target Dir, Format | • Real-Time Progress Bar                     |
| • Filter Pipeline: Notch,    | • Tabbed Views:                              |
|   Bandpass, Wavelet, EMD     |   - [Activity]  Live execution log stream    |
| • Sampling & Resampling      |   - [Waveforms] 6x2 standard 12-lead grid    |
| • Privacy & Metadata Mapping |   - [Summary]   Interactive metadata table   |
|                              |                                              |
| [ ▶ Start Processing ] [ ✕ ] | [ Open Output Dir ] [ Open Summary CSV ]     |
+------------------------------+----------------------------------------------+

Keyboard Shortcuts:
-------------------
• Ctrl+O         : Browse input directory / file
• Ctrl+Shift+O   : Browse output directory
• Ctrl+R         : Start batch processing
• Esc            : Request graceful task cancellation

Usage:
------
    python3 ui.py
"""
import os
import sys
from pathlib import Path

try:
    from PySide6.QtCore import (
        QPointF, QRect, QRegularExpression, Qt, QThread, Signal, QUrl,
    )
    from PySide6.QtGui import (
        QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QKeySequence, QPainter,
        QPainterPath, QPalette, QPen, QPixmap, QRegularExpressionValidator,
        QShortcut,
    )
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGroupBox,
        QHBoxLayout, QHeaderView, QLabel, QListView, QMainWindow, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
        QStyle, QStyleOptionButton, QTableWidget, QTableWidgetItem,
        QTabWidget, QToolButton, QVBoxLayout, QWidget,
    )
except ImportError:
    print("The GUI needs PySide6. Install everything with:\n"
          "    pip install -r requirements.txt")
    raise SystemExit(1)

import matplotlib
matplotlib.use("QtAgg")
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator

if not getattr(sys, "frozen", False):
    _root = str(Path(__file__).resolve().parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

from ecgproc import __version__
from ecgproc.constants import FINAL_LEADS, SUPPORTED_EXTENSIONS
from ecgproc.files import find_files
from runner.execution_runner import ExecutionRunner, SUMMARY_COLUMNS

# ------
# Design
# ------

BG = "#0B0F14"          # page
SURFACE = "#111823"     # panels
SURFACE2 = "#0D141D"    # inputs, log, plots
BORDER = "#223041"
TEXT = "#E8EEF4"
MUTED = "#8FA0B2"
ACCENT = "#35D399"      # trace green
ACCENT_HOVER = "#4AE2AC"
ACCENT_PRESSED = "#2BB985"
ON_ACCENT = "#05130C"
WARN = "#F5B454"
ERROR = "#F87171"
GRID_MAJOR = "#22303F"
GRID_MINOR = "#161F2B"
HOVER = "#16202C"        # hovered rows, inputs, popup items
BORDER_HOVER = "#2E4157"
SELECTED = "#173026"     # popup selection: accent-tinted
DISABLED_TEXT = "#5A6B7D"

MONO = "'Consolas','Menlo','DejaVu Sans Mono',monospace"

QSS = f"""
QMainWindow {{ background: {BG}; }}
#Header {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#AppTitle {{ font-size: 17px; font-weight: 700; color: {TEXT}; }}
#AppSub {{ color: {MUTED}; font-size: 12px; }}
#Version {{ color: {MUTED}; font-family: {MONO}; font-size: 12px; }}

QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 8px; background: {SURFACE};
    margin-top: 12px; padding: 12px 10px 8px 10px; font-weight: 600; font-size: 12px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}

QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 7px 12px; color: {TEXT};
}}
QPushButton:hover {{ background: #16202C; border-color: #2E4157; }}
QPushButton:pressed {{ background: #0E1520; }}
QPushButton:disabled {{ color: #5A6B7D; border-color: #1A2531; }}

QPushButton#primary {{
    background: {ACCENT}; color: {ON_ACCENT}; border: none;
    padding: 11px 14px; font-size: 14px; font-weight: 700;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#primary:disabled {{ background: #1E3A2F; color: #5E7A6C; }}

QToolButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 4px 8px; color: {MUTED};
}}
QToolButton:hover {{ color: {ERROR}; border-color: #2E4157; }}

QProgressBar {{
    border: 1px solid {BORDER}; border-radius: 6px; background: {SURFACE2};
    text-align: center; color: {TEXT}; font-family: {MONO}; font-size: 12px; height: 22px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

QPlainTextEdit#log {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 6px;
    color: #C6D3E0; font-family: {MONO}; font-size: 12px; padding: 6px;
}}

QLabel#chip_ok, QLabel#chip_warn, QLabel#chip_err {{
    border-radius: 10px; padding: 3px 12px; font-family: {MONO}; font-size: 12px;
}}
QLabel#chip_ok   {{ color: {ACCENT}; background: #10241C; border: 1px solid #2E5C48; }}
QLabel#chip_warn {{ color: {WARN};  background: #241D10; border: 1px solid #5C4A22; }}
QLabel#chip_err  {{ color: {ERROR}; background: #241010; border: 1px solid #5C2E2E; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 7px 16px;
    border: 1px solid transparent; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{ color: {TEXT}; background: {SURFACE}; border-color: {BORDER}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QTableWidget {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 6px;
    gridline-color: {GRID_MAJOR}; font-size: 12px;
}}
QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; border-right: 1px solid {GRID_MAJOR}; padding: 4px 8px;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QCheckBox {{ spacing: 9px; color: {TEXT}; }}
QCheckBox:disabled {{ color: {DISABLED_TEXT}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid #35485E; border-radius: 4px; background: {SURFACE2};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; background: #142031; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:checked:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QCheckBox::indicator:disabled {{ border-color: #1E2A38; background: #0E141C; }}
QCheckBox::indicator:checked:disabled {{ background: #1E3A2F; border-color: #2A4C3D; }}

QComboBox {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 5px 8px; color: {TEXT}; min-height: 20px;
}}
QComboBox:hover {{ border-color: {BORDER_HOVER}; background: #101A26; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox:on {{ border-color: {ACCENT}; background: #101A26; }}
QComboBox:disabled {{
    color: {DISABLED_TEXT}; border-color: #1A2531; background: #0B1119;
}}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ border: none; background: transparent; width: 24px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
/* Editable combo: the inner line edit draws its own frame and background,
   which would sit as a pale box inside the rounded field. */
QComboBox QLineEdit {{
    background: transparent; border: none; padding: 0; margin: 0; color: {TEXT};
    selection-background-color: {SELECTED}; selection-color: {ACCENT};
}}
QComboBox QLineEdit:disabled {{ color: {DISABLED_TEXT}; }}

/* Popup list: light text on an accent-tinted row — never the near-black
   that selection-color: {ON_ACCENT} produced on the closed control's palette. */
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER_HOVER}; border-radius: 8px;
    padding: 4px; outline: 0; color: {TEXT};
    selection-background-color: {SELECTED}; selection-color: {ACCENT};
}}
QComboBox QAbstractItemView::item {{
    min-height: 26px; padding: 3px 8px; border-radius: 5px;
    color: {TEXT}; background: transparent;
}}
QComboBox QAbstractItemView::item:hover {{ background: {HOVER}; color: {TEXT}; }}
QComboBox QAbstractItemView::item:selected {{ background: {SELECTED}; color: {ACCENT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 4px; min-height: 28px; margin: 0 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {BORDER_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 4px; min-width: 28px; margin: 2px 0;
}}
QScrollBar::handle:horizontal:hover {{ background: {BORDER_HOVER}; }}

QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}
QStatusBar {{ background: {SURFACE}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
"""

# One heartbeat
HEARTBEAT = [
    (0.00, 0.00), (0.14, 0.00), (0.19, 0.16), (0.24, 0.00), (0.30, 0.00),
    (0.33, -0.16), (0.365, 1.00), (0.40, -0.44), (0.44, 0.00), (0.55, 0.00),
    (0.62, 0.26), (0.70, 0.00), (1.00, 0.00),
]


def _pulse_path(x, y, w, h):
    """Trace the HEARTBEAT points as a path fitted to the box (x, y, w, h)."""
    path = QPainterPath()
    base = y + h * 0.62
    amp = h * 0.52
    pts = [(x + px * w, base - py * amp) for px, py in HEARTBEAT]
    path.moveTo(*pts[0])
    for p in pts[1:]:
        path.lineTo(*p)
    return path


def make_app_icon():
    """Build the window and taskbar icon: dark rounded tile, ECG grid, one green beat."""
    pm = QPixmap(256, 256)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(BG))
    p.drawRoundedRect(8, 8, 240, 240, 52, 52)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(GRID_MAJOR), 3))
    for i in range(1, 4):
        p.drawLine(8, 8 + i * 60, 248, 8 + i * 60)
        p.drawLine(8 + i * 60, 8, 8 + i * 60, 248)
    pen = QPen(QColor(ACCENT), 16, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.drawPath(_pulse_path(28, 40, 200, 160))
    p.end()
    return QIcon(pm)


# --------------------
# Custom-drawn widgets
# --------------------


class PulseWidget(QWidget):
    """Static single-heartbeat trace shown next to the title in the header."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(56, 30)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(ACCENT), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.drawPath(_pulse_path(2, 2, self.width() - 4, self.height() - 6))
        p.end()


class CheckBox(QCheckBox):
    """QCheckBox that paints its tick mark."""

    def _indicator_rect(self):
        opt = QStyleOptionButton()
        opt.initFrom(self)
        rect = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, opt, self)
        if rect.isEmpty():
            side = 18
            rect = QRect(0, (self.height() - side) // 2, side, side)
        return rect

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.checkState() != Qt.Checked:
            return
        r = self._indicator_rect()
        x, y, w, h = r.x(), r.y(), r.width(), r.height()
        tick = QPainterPath()
        tick.moveTo(x + w * 0.26, y + h * 0.52)
        tick.lineTo(x + w * 0.43, y + h * 0.70)
        tick.lineTo(x + w * 0.75, y + h * 0.30)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(ON_ACCENT if self.isEnabled() else "#5E7A6C"), 2.0,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(tick)
        p.end()


class Combo(QComboBox):
    """QComboBox with state-reactive vector arrows and fixed-height popup items."""

    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView()
        view.setUniformItemSizes(True)
        self.setView(view)

    def paintEvent(self, event):
        super().paintEvent(event)
        edit = self.lineEdit()
        if not self.isEnabled():
            color = QColor(DISABLED_TEXT)
        elif (self.hasFocus() or self.view().isVisible()
                or (edit is not None and edit.hasFocus())):
            color = QColor(ACCENT)
        else:
            color = QColor(MUTED)
        cx = self.width() - 14.0
        cy = self.height() / 2.0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(cx - 4.0, cy - 2.0), QPointF(cx, cy + 2.2))
        p.drawLine(QPointF(cx, cy + 2.2), QPointF(cx + 4.0, cy - 2.0))
        p.end()


class RateCombo(Combo):
    """Editable Combo for a sampling rate in Hz:"""

    PRESETS = (100, 128, 200, 250, 256, 360, 500, 512, 1000, 1024)
    MIN_HZ, MAX_HZ = 1, 10000

    def __init__(self, value=500, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        for hz in self.PRESETS:
            self.addItem(f"{hz} Hz", hz)
        self.lineEdit().setValidator(QRegularExpressionValidator(
            QRegularExpression(r"\d{0,5}\s*[Hh]?[Zz]?"), self))
        self.lineEdit().editingFinished.connect(self.commit)
        self._last_valid = 500
        self.set_value(value)

    def value(self):
        digits = "".join(ch for ch in self.currentText() if ch.isdigit())
        return int(digits) if digits else 0

    def rate(self):
        return min(max(self.value() or self._last_valid, self.MIN_HZ), self.MAX_HZ)

    def commit(self):
        hz = self.rate()
        self._last_valid = hz
        text = f"{hz} Hz"
        if text != self.currentText():
            self.lineEdit().setText(text)
        return hz

    def set_value(self, hz):
        hz = int(hz or 0)
        if hz:
            self._last_valid = hz
        index = self.findData(hz)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            self.setCurrentIndex(-1)
            self.setEditText(f"{hz} Hz" if hz else "")


class ElidedLabel(QLabel):
    """QLabel for file paths: middle-elides to the current width, full text in the tooltip."""

    def __init__(self, placeholder=""):
        super().__init__(placeholder)
        self._full = ""
        self._placeholder = placeholder
        self.setMinimumWidth(60)

    def set_full_text(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self._apply()

    def full_text(self):
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def _apply(self):
        if not self._full:
            super().setText(self._placeholder)
            return
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.ElideMiddle, max(60, self.width() - 4)))


# -------
# Workers
# -------


class ScanWorker(QThread):
    """Counts the processable files under a path, off the GUI thread."""

    sig_done = Signal(int, int, dict)  # generation, total, counts

    def __init__(self, generation, path):
        super().__init__()
        self.generation = generation
        self.path = path

    def run(self):
        counts = {}
        total = 0
        try:
            files = find_files(Path(self.path), SUPPORTED_EXTENSIONS)
            total = len(files)
            for f in files:
                ext = f.suffix.lower().lstrip(".")
                kind = "wfdb" if ext in ("dat", "hea") else ext
                counts[kind] = counts.get(kind, 0) + 1
        except Exception:
            pass
        self.sig_done.emit(self.generation, total, counts)


class ProcessWorker(QThread):
    """Runs one ExecutionRunner batch off the GUI thread."""

    sig_progress = Signal(int, int, str)          # done, total, current file
    sig_counts = Signal(dict)                     # success/skipped/failed
    sig_log = Signal(str)
    sig_finished = Signal(object, object, object, dict, bool)
    sig_error = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from ecgproc import stats as run_stats
            s = self.settings
            runner = ExecutionRunner(path_source=s["input"], path_sink=s["output"])
            runner.output_format = s["format"]
            runner.pipeline = s["pipeline"]
            runner.notch = s["notch"]
            if s.get("emd_method"):
                runner.emd_options["method"] = s["emd_method"]
            runner.default_fs = s["default_fs"]
            runner.resample = s["resample"]
            runner.resample_method = s["resample_method"]
            runner.anonymize = s["anonymize"]
            runner.override = s["override"]
            runner.metadata_csv = s["metadata_csv"]
            runner.keep_signals = True

            def progress(done, total, name):
                self.sig_progress.emit(done, total, name)
                self.sig_counts.emit(run_stats.snapshot())

            records = runner.run(
                progress_cb=progress,
                log_cb=self.sig_log.emit,
                cancel_cb=lambda: self._cancelled,
            )
            self.sig_counts.emit(runner.stats)
            summary_path = str(runner.summary_path) if runner.summary_path else None
            self.sig_finished.emit(records, list(runner.summary), summary_path,
                                   runner.stats, runner.cancelled)
        except Exception as exc:
            self.sig_error.emit(str(exc))


# -----------
# Main window
# -----------

FORMAT_ITEMS = [
    ("CSV (.csv)", "csv"),
    ("XML (.xml)", "xml"),
    ("DICOM (.dcm)", "dcm"),
    ("HL7 aECG (.hl7)", "hl7"),
    ("WFDB (.dat + .hea)", "wfdb"),
    ("MATLAB (.mat)", "mat"),
    ("ASC (.asc)", "asc"),
]


class MainWindow(QMainWindow):
    """The application window: settings on the left, results on the right."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ECG-Format-Converter")
        self.setMinimumSize(1240, 760)
        self.resize(1520, 880)
        self.setAcceptDrops(True)

        self._records = {}
        self._summary = []
        self._summary_path = None
        self._output_dir = ""
        self._metadata_csv = ""
        self._worker = None
        self._scan_generation = 0
        self._scanners = []                 # in-flight scans; see _set_input
        self._rateless_files = 0            # inputs that carry no sampling rate

        defaults = ExecutionRunner()  # reads config.ini for initial values

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(16, 14, 16, 12)
        body.setSpacing(16)
        outer.addLayout(body, 1)

        body.addWidget(self._build_left(defaults))
        body.addLayout(self._build_right(), 1)

        self.statusBar().showMessage("Ready")
        self._apply_defaults(defaults)
        self._wire_shortcuts()
        self._refresh_start_enabled()

    # Header
    def _build_header(self):
        header = QWidget(objectName="Header")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)
        lay.addWidget(PulseWidget())
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(QLabel("ECG-Format-Converter", objectName="AppTitle"))
        titles.addWidget(QLabel("Convert, filter, resample, and anonymize 12-lead ECG recordings",
                                objectName="AppSub"))
        lay.addLayout(titles)
        lay.addStretch(1)
        lay.addWidget(QLabel(f"v{__version__}", objectName="Version"))
        return header

    # Left column
    def _group(self, title):
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.setSpacing(6)
        lay.setContentsMargins(12, 10, 12, 10)
        return box, lay

    GUTTER = 12

    def _build_left(self, defaults):
        """Build the scrollable settings column and the Start/Cancel bar under it."""
        column = QWidget()
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._scroll = scroll
        panel = QWidget()
        self._panel = panel
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, self.GUTTER, 0)
        lay.setSpacing(8)
        scroll.setWidget(panel)
        col.addWidget(scroll, 1)

        # Input
        box, g = self._group("Input")
        row = QHBoxLayout()
        self.btn_in_folder = QPushButton("Select folder…")
        self.btn_in_file = QPushButton("Select file…")
        row.addWidget(self.btn_in_folder)
        row.addWidget(self.btn_in_file)
        g.addLayout(row)
        self.lbl_input = ElidedLabel("No input selected")
        self.lbl_input.setStyleSheet(f"color: {MUTED};")
        g.addWidget(self.lbl_input)
        self.lbl_scan = QLabel("Drop a file/folder in this window")
        self.lbl_scan.setStyleSheet(f"color: {MUTED}; font-family: {MONO}; font-size: 11px;")
        self.lbl_scan.setWordWrap(True)
        g.addWidget(self.lbl_scan)
        lay.addWidget(box)

        # Output
        box, g = self._group("Output")
        self.btn_out_folder = QPushButton("Select output folder…")
        g.addWidget(self.btn_out_folder)
        self.lbl_output = ElidedLabel("No output folder selected")
        self.lbl_output.setStyleSheet(f"color: {MUTED};")
        g.addWidget(self.lbl_output)
        row = QHBoxLayout()
        row.addWidget(QLabel("Format"))
        self.cmb_format = Combo()
        for label, data in FORMAT_ITEMS:
            self.cmb_format.addItem(label, data)
        row.addWidget(self.cmb_format, 1)
        g.addLayout(row)
        self.chk_override = CheckBox("Overwrite existing outputs")
        self.chk_override.setToolTip("Unchecked: files whose output already exists are skipped")
        g.addWidget(self.chk_override)
        lay.addWidget(box)

        # Pipeline
        box, g = self._group("Filtering pipeline")
        note = QLabel("Choose one or more filters to apply to the signals")
        note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        g.addWidget(note)
        row = QHBoxLayout()
        self.chk_notch = CheckBox("Notch filter")
        self.chk_notch.setToolTip("Removes mains interference at the selected frequency")
        self.cmb_notch = Combo()
        self.cmb_notch.addItem("50 Hz", 50)
        self.cmb_notch.addItem("60 Hz", 60)
        self.cmb_notch.setEnabled(False)
        row.addWidget(self.chk_notch, 1)
        row.addWidget(self.cmb_notch)
        g.addLayout(row)
        self.chk_bandpass = CheckBox("Bandpass 0.5-40 Hz")
        self.chk_bandpass.setToolTip("3rd-order Butterworth, zero-phase")
        g.addWidget(self.chk_bandpass)
        self.chk_wavelet = CheckBox("Wavelet baseline removal")
        self.chk_wavelet.setToolTip("db4 decomposition, level 8, soft thresholding of the approximation coefficients")
        g.addWidget(self.chk_wavelet)
        row = QHBoxLayout()
        self.chk_emd = CheckBox("EMD denoise")
        self.chk_emd.setToolTip(
            "Empirical Mode Decomposition. Thresholds the noise components, keeps the QRS\n"
            "complexes at full bandwidth and removes the baseline trend. Slow on long records.")
        self.cmb_emd = Combo()
        self.cmb_emd.addItem("Standard", "emd")
        self.cmb_emd.addItem("Ensemble", "eemd")
        self.cmb_emd.addItem("CEEMDAN", "ceemdan")
        self.cmb_emd.setToolTip("Ensemble and CEEMDAN separate the components better "
                                "but take far longer.")
        self.cmb_emd.setEnabled(False)
        row.addWidget(self.chk_emd, 1)
        row.addWidget(self.cmb_emd)
        g.addLayout(row)
        lay.addWidget(box)

        # Sampling
        box, g = self._group("Sampling")
        row = QHBoxLayout()
        self.chk_fs = CheckBox("Input sampling rate")
        self.chk_fs.setToolTip(
            "Input sampling rate for headerless files (CSV, ASC, MAT).\n"
            "Select a preset or type any value in Hz. Existing rates are preserved."
)
        self.cmb_fs = RateCombo(500)
        self.cmb_fs.setEnabled(False)
        self.cmb_fs.setToolTip("Pick a common rate or type any value in Hz.")
        row.addWidget(self.chk_fs, 1)
        row.addWidget(self.cmb_fs)
        g.addLayout(row)
        self.lbl_fs_hint = QLabel()
        self.lbl_fs_hint.setWordWrap(True)
        hint_font = QFont(self.lbl_fs_hint.font())
        hint_font.setPixelSize(11)
        self.lbl_fs_hint.setFont(hint_font)
        self.lbl_fs_hint.setMinimumHeight(2 * QFontMetrics(hint_font).lineSpacing())
        self.lbl_fs_hint.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        g.addWidget(self.lbl_fs_hint)
        self._refresh_fs_hint()
        row = QHBoxLayout()
        self.chk_resample = CheckBox("Resample to")
        self.cmb_resample = RateCombo(500)
        self.cmb_resample.setEnabled(False)
        self.cmb_resample.setToolTip("Pick a target rate or type any value in Hz.")
        self.cmb_resample_method = Combo()
        self.cmb_resample_method.addItem("FFT", "fft")
        self.cmb_resample_method.addItem("Interpolation", "interpolation")
        self.cmb_resample_method.setEnabled(False)
        row.addWidget(self.chk_resample)
        row.addWidget(self.cmb_resample)
        row.addWidget(self.cmb_resample_method, 1)
        g.addLayout(row)
        lay.addWidget(box)

        # Privacy & metadata ------------------------------------------------
        box, g = self._group("Privacy and metadata")
        self.chk_anonymize = CheckBox("Anonymize outputs")
        self.chk_anonymize.setToolTip("Removes patient ID, first/last name, and birth date.")
        g.addWidget(self.chk_anonymize)
        row = QHBoxLayout()
        self.btn_metadata = QPushButton("Metadata CSV…")
        self.btn_metadata.setToolTip("Optional, for .asc inputs: maps sex and age by the numeric ID "
                                     "at the start of each file name. Needs ID, sex, and age columns.")
        self.btn_metadata_clear = QToolButton()
        self.btn_metadata_clear.setText("✕")
        self.btn_metadata_clear.setToolTip("Clear the metadata CSV")
        self.btn_metadata_clear.setEnabled(False)
        row.addWidget(self.btn_metadata, 1)
        row.addWidget(self.btn_metadata_clear)
        g.addLayout(row)
        self.lbl_metadata = ElidedLabel("No metadata CSV")
        self.lbl_metadata.setStyleSheet(f"color: {MUTED};")
        g.addWidget(self.lbl_metadata)
        lay.addWidget(box)

        lay.addStretch(1)

        # Run
        bar = scroll.verticalScrollBar().sizeHint().width() or 12
        run = QVBoxLayout()
        run.setContentsMargins(0, 0, self.GUTTER + bar, 0)
        run.setSpacing(8)
        self.btn_start = QPushButton("▶  Start processing", objectName="primary")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        run.addWidget(self.btn_start)
        run.addWidget(self.btn_cancel)
        col.addLayout(run)
        column.setFixedWidth(max(440, panel.minimumSizeHint().width() + self.GUTTER + bar))

        # Connections
        self.btn_in_folder.clicked.connect(self._pick_input_folder)
        self.btn_in_file.clicked.connect(self._pick_input_file)
        self.btn_out_folder.clicked.connect(self._pick_output_folder)
        self.btn_metadata.clicked.connect(self._pick_metadata)
        self.btn_metadata_clear.clicked.connect(self._clear_metadata)
        self.chk_notch.toggled.connect(self.cmb_notch.setEnabled)
        self.chk_emd.toggled.connect(self.cmb_emd.setEnabled)
        self.chk_fs.toggled.connect(self.cmb_fs.setEnabled)
        self.chk_fs.toggled.connect(self._refresh_fs_hint)
        self.cmb_fs.currentTextChanged.connect(self._refresh_fs_hint)
        self.cmb_format.currentIndexChanged.connect(self._refresh_fs_hint)
        self.chk_resample.toggled.connect(self.cmb_resample.setEnabled)
        self.chk_resample.toggled.connect(self.cmb_resample_method.setEnabled)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_cancel.clicked.connect(self._on_cancel)
        return column

    # Right column

    def _build_right(self):
        """Build the results pane: count chips, progress bar, Activity/Waveforms/Summary tabs."""
        right = QVBoxLayout()
        right.setSpacing(10)

        chips = QHBoxLayout()
        self.chip_ok = QLabel("0 processed", objectName="chip_ok")
        self.chip_warn = QLabel("0 skipped", objectName="chip_warn")
        self.chip_err = QLabel("0 failed", objectName="chip_err")
        chips.addWidget(self.chip_ok)
        chips.addWidget(self.chip_warn)
        chips.addWidget(self.chip_err)
        chips.addStretch(1)
        right.addLayout(chips)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("idle")
        right.addWidget(self.progress)

        self.tabs = QTabWidget()
        right.addWidget(self.tabs, 1)

        # Activity
        self.log = QPlainTextEdit(objectName="log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(8000)
        self.tabs.addTab(self.log, "Activity")

        # Waveforms
        wf = QWidget()
        wl = QVBoxLayout(wf)
        wl.setContentsMargins(10, 10, 10, 10)
        wl.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(QLabel("Record"))
        self.cmb_records = Combo()
        self.cmb_records.setMinimumWidth(260)
        self.cmb_records.currentTextChanged.connect(self._plot_record)
        row.addWidget(self.cmb_records, 1)
        self.btn_save_plot = QPushButton("Save plot…")
        self.btn_save_plot.setEnabled(False)
        self.btn_save_plot.clicked.connect(self._save_plot)
        row.addWidget(self.btn_save_plot)
        wl.addLayout(row)
        self.fig = Figure(figsize=(9, 7), dpi=100)
        self.fig.patch.set_facecolor(BG)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setStyleSheet(f"background-color: {BG};")
        wl.addWidget(self.canvas, 1)
        self.tabs.addTab(wf, "Waveforms")

        # Summary
        sm = QWidget()
        sl = QVBoxLayout(sm)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.setSpacing(8)
        self.table = QTableWidget(0, len(SUMMARY_COLUMNS))
        self.table.setHorizontalHeaderLabels(SUMMARY_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(110)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        sl.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_open_out = QPushButton("Open output folder")
        self.btn_open_out.setEnabled(False)
        self.btn_open_out.clicked.connect(self._open_output)
        self.btn_open_summary = QPushButton("Open ecg_summary.csv")
        self.btn_open_summary.setEnabled(False)
        self.btn_open_summary.clicked.connect(self._open_summary)
        row.addWidget(self.btn_open_out)
        row.addWidget(self.btn_open_summary)
        row.addStretch(1)
        sl.addLayout(row)
        self.tabs.addTab(sm, "Summary")
        return right

    # Helpers

    def _apply_defaults(self, defaults):
        """Set every control to the config.ini value carried by the `defaults` runner."""
        idx = self.cmb_format.findData(defaults.output_format)
        if idx >= 0:
            self.cmb_format.setCurrentIndex(idx)
        self.chk_override.setChecked(defaults.override)
        self.chk_notch.setChecked("notch" in defaults.pipeline)
        self.cmb_notch.setCurrentIndex(0 if defaults.notch == 50 else 1)
        self.chk_bandpass.setChecked("bandpass" in defaults.pipeline)
        self.chk_wavelet.setChecked("wavelet" in defaults.pipeline)
        self.chk_emd.setChecked("emd" in defaults.pipeline)
        idx = self.cmb_emd.findData(defaults.emd_options.get("method", "emd"))
        if idx >= 0:
            self.cmb_emd.setCurrentIndex(idx)
        if defaults.default_fs:
            self.chk_fs.setChecked(True)
            self.cmb_fs.set_value(int(defaults.default_fs))
        if defaults.resample:
            self.chk_resample.setChecked(True)
            self.cmb_resample.set_value(int(defaults.resample))
        self.cmb_resample_method.setCurrentIndex(
            0 if defaults.resample_method == "fft" else 1)
        self.chk_anonymize.setChecked(defaults.anonymize)
        # Input and output stay empty on launch — the user picks both.

    def _refresh_fs_hint(self):
        """Say what the fallback rate does for the files actually selected."""
        n = self._rateless_files
        plural = "s" if n != 1 else ""
        if self.cmb_format.currentData() == "wfdb" and not self.chk_fs.isChecked():
            color = WARN
            subject = (f"The {n} selected file{plural} that store no sampling rate"
                       if n else "Any file that stores no sampling rate")
            text = (f"WFDB output writes the sampling rate into the header. {subject} "
                    "will be skipped unless you set one here. Files that carry their "
                    "own rate are unaffected.")
        elif n and not self.chk_fs.isChecked():
            color = WARN
            text = (f"{n} selected file{plural} store{'' if n != 1 else 's'} no sampling rate. "
                    f"{'They' if n != 1 else 'It'} will still be converted, but filtering and "
                    "resampling are skipped without a rate.")
        elif n:
            color = MUTED
            text = (f"{n} selected file{plural} store{'' if n != 1 else 's'} no sampling rate and "
                    f"will be read at {self.cmb_fs.rate()} Hz.")
        else:
            color = MUTED
            text = ("CSV and ASC files never store a sampling rate; filtering and resampling "
                    "need one. Files that carry their own always keep it.")
        self.lbl_fs_hint.setText(text)
        self.lbl_fs_hint.setStyleSheet(f"color: {color};")

    def _wire_shortcuts(self):
        """Bind the window-level keyboard shortcuts."""
        for keys, fn in (("Ctrl+O", self._pick_input_folder),
                         ("Ctrl+Shift+O", self._pick_output_folder),
                         ("Ctrl+R", self._on_start),
                         ("Esc", self._on_cancel)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.activated.connect(fn)

    def _append_log(self, text):
        """Add one line to the Activity tab."""
        self.log.appendPlainText(str(text))

    def _set_input(self, path):
        """Accept a new input path and kick off a fresh background scan of it."""
        self.lbl_input.set_full_text(path)
        self.lbl_scan.setText("Scanning…")
        self._scan_generation += 1
        scanner = ScanWorker(self._scan_generation, path)
        scanner.sig_done.connect(self._on_scan_done)
        # Hold the reference until the thread really has finished: dropping a
        # running QThread on the floor (picking a second folder while the first
        # is still being scanned) aborts the process.
        self._scanners.append(scanner)
        scanner.finished.connect(lambda s=scanner: self._retire_scanner(s))
        scanner.start()
        self._refresh_start_enabled()

    def _retire_scanner(self, scanner):
        """Drop a finished scan thread."""
        if scanner in self._scanners:
            self._scanners.remove(scanner)
        scanner.deleteLater()

    def _set_output(self, path):
        """Accept a new output folder."""
        self._output_dir = path
        self.lbl_output.set_full_text(path)
        self._refresh_start_enabled()

    def _on_scan_done(self, generation, total, counts):
        """Show what the scan found, unless a newer scan has already superseded it."""
        if generation != self._scan_generation:
            return
        if total == 0:
            self.lbl_scan.setText("No processable files found "
                                  "(mat, wfdb, xml, hl7, csv, asc, dcm).")
        else:
            parts = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            self.lbl_scan.setText(f"{total} file{'s' if total != 1 else ''} ready · {parts}")
        self._rateless_files = counts.get("csv", 0) + counts.get("asc", 0)
        self._refresh_fs_hint()
        self._refresh_start_enabled()

    def _refresh_start_enabled(self):
        """Start is live only with both paths chosen and no run in flight."""
        running = self._worker is not None and self._worker.isRunning()
        ready = bool(self.lbl_input.full_text()) and bool(self._output_dir)
        self.btn_start.setEnabled(ready and not running)

    # Pickers

    def _pick_input_folder(self):
        start = self.lbl_input.full_text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select input folder", start)
        if path:
            self._set_input(path)

    def _pick_input_file(self):
        start = self.lbl_input.full_text() or str(Path.home())
        exts = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Select ECG file", start,
                                              f"ECG files ({exts});;All files (*)")
        if path:
            self._set_input(path)

    def _pick_output_folder(self):
        start = self._output_dir or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if path:
            self._set_output(path)

    def _pick_metadata(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select metadata CSV",
                                              str(Path.home()), "CSV files (*.csv);;All files (*)")
        if path:
            self._metadata_csv = path
            self.lbl_metadata.set_full_text(path)
            self.btn_metadata_clear.setEnabled(True)

    def _clear_metadata(self):
        self._metadata_csv = ""
        self.lbl_metadata.set_full_text("")
        self.btn_metadata_clear.setEnabled(False)

    # Drag-and-drop

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            return
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._set_input(path)

    # Run

    def _gather_settings(self):
        """Read every control into the settings dict ProcessWorker expects."""
        fmt = self.cmb_format.currentData()
        pipeline = [name for name, box in (
            ("notch", self.chk_notch), ("bandpass", self.chk_bandpass),
            ("wavelet", self.chk_wavelet), ("emd", self.chk_emd),
        ) if box.isChecked()]
        return {
            "input": self.lbl_input.full_text(),
            "output": self._output_dir,
            "format": fmt,
            "pipeline": pipeline,
            "notch": self.cmb_notch.currentData(),
            "emd_method": self.cmb_emd.currentData(),
            "default_fs": self.cmb_fs.commit() if self.chk_fs.isChecked() else None,
            "resample": self.cmb_resample.commit() if self.chk_resample.isChecked() else None,
            "resample_method": self.cmb_resample_method.currentData(),
            "anonymize": self.chk_anonymize.isChecked(),
            "override": self.chk_override.isChecked(),
            "metadata_csv": self._metadata_csv or None,
        }

    def _on_start(self):
        """Validate the settings, clear the last run's results, and start the worker."""
        if self._worker is not None and self._worker.isRunning():
            return
        s = self._gather_settings()
        if not s["input"] or not Path(s["input"]).exists():
            QMessageBox.warning(self, "ECG-Format-Converter", "Select an input file or folder first.")
            return
        if not s["output"]:
            QMessageBox.warning(self, "ECG-Format-Converter", "Select an output folder first.")
            return
        if s["format"] == "wfdb" and not s["default_fs"]:
            if QMessageBox.question(
                    self, "ECG-Format-Converter",
                    "WFDB output needs a sampling rate for every file.\n\n"
                    "Files that store their own rate are fine, but any that do not "
                    "(CSV, ASC, some MAT) will be skipped. Tick \"Sampling rate\" to "
                    "give them one.\n\nStart anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        Path(s["output"]).mkdir(parents=True, exist_ok=True)

        self._records = {}
        self._summary = []
        self._summary_path = None
        self.cmb_records.blockSignals(True)
        self.cmb_records.clear()
        self.cmb_records.blockSignals(False)
        self.btn_save_plot.setEnabled(False)
        self.table.setRowCount(0)
        self._set_counts({"success": 0, "skipped": 0, "failed": 0})

        pipe = " -> ".join(s["pipeline"]) if s["pipeline"] else "none"
        self._append_log("─" * 64)
        self._append_log(f"Run · format {s['format']} · pipeline {pipe}"
                         + (f" · resample {s['resample']} Hz ({s['resample_method']})" if s["resample"] else "")
                         + (" · anonymize" if s["anonymize"] else ""))
        self._append_log(f"Input:  {s['input']}")
        self._append_log(f"Output: {s['output']}")

        self._panel.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("starting…")
        self.statusBar().showMessage("Processing…")
        self.tabs.setCurrentIndex(0)

        self._worker = ProcessWorker(s)
        self._worker.sig_progress.connect(self._on_progress)
        self._worker.sig_counts.connect(self._set_counts)
        self._worker.sig_log.connect(self._append_log)
        self._worker.sig_finished.connect(self._on_finished)
        self._worker.sig_error.connect(self._on_error)
        self._worker.start()

    def _on_cancel(self):
        """Ask a running batch to stop; it finishes the current file first."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.statusBar().showMessage("Cancelling…")

    def _on_progress(self, done, total, name):
        """Advance the progress bar; `name` is the file about to be processed."""
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        if name:
            self.progress.setFormat(f"{done + 1}/{total} · {name}")
        else:
            self.progress.setFormat(f"{done}/{total}")

    def _set_counts(self, counts):
        """Update the processed/skipped/failed chips."""
        self.chip_ok.setText(f"{counts.get('success', 0)} processed")
        self.chip_warn.setText(f"{counts.get('skipped', 0)} skipped")
        self.chip_err.setText(f"{counts.get('failed', 0)} failed")

    def _on_finished(self, records, summary, summary_path, stats, cancelled):
        """Store the batch result, fill the table, and jump to the most useful tab."""
        self._teardown_worker()
        self._records = records or {}
        self._summary = summary or []
        self._summary_path = summary_path
        self._set_counts(stats)
        self._fill_summary_table()

        state = "Cancelled" if cancelled else "Done"
        self._append_log(f"{state} · {stats['success']} processed · "
                         f"{stats['skipped']} skipped · {stats['failed']} failed")
        if summary_path:
            self._append_log(f"Summary: {summary_path}")
        self.statusBar().showMessage(f"{state} · {stats['success']} processed · "
                                     f"{stats['skipped']} skipped · {stats['failed']} failed")
        self.progress.setFormat(state.lower())

        self.btn_open_out.setEnabled(bool(self._output_dir))
        self.btn_open_summary.setEnabled(bool(summary_path))

        if self._records:
            self.cmb_records.blockSignals(True)
            self.cmb_records.addItems(sorted(self._records))
            self.cmb_records.blockSignals(False)
            self.cmb_records.setCurrentIndex(0)
            self._plot_record(self.cmb_records.currentText())
            self.tabs.setCurrentIndex(1)
        elif self._summary:
            self.tabs.setCurrentIndex(2)

        if not cancelled and stats["failed"] and not stats["success"]:
            QMessageBox.warning(self, "ECG-Format-Converter",
                                "Every file failed. See the Activity tab for details.")

    def _on_error(self, message):
        """Report a batch that failed outright."""
        self._teardown_worker()
        self._append_log(f"Error: {message}")
        self.statusBar().showMessage("Error")
        self.progress.setFormat("error")
        QMessageBox.critical(self, "ECG-Format-Converter", message)

    def _teardown_worker(self):
        """Drop the finished worker and re-enable the settings panel."""
        if self._worker is not None:
            # The finishing signal is emitted from inside run(), so the thread
            # may not have unwound yet; deleting it here would abort.
            self._worker.wait(5000)
            self._worker.deleteLater()
            self._worker = None
        self._panel.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._refresh_start_enabled()

    # Output

    def _fill_summary_table(self):
        """Render the summary rows in SUMMARY_COLUMNS order, read-only."""
        self.table.setRowCount(len(self._summary))
        for r, entry in enumerate(self._summary):
            for c, col in enumerate(SUMMARY_COLUMNS):
                val = entry.get(col, "")
                item = QTableWidgetItem("" if val is None else str(val))
                self.table.setItem(r, c, item)
        self.table.resizeColumnToContents(0)

    def _open_output(self):
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_dir))

    def _open_summary(self):
        if self._summary_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._summary_path))

    # Waveforms

    def _plot_record(self, rec_id):
        """Draw one record as 12 leads on a 6x2 ECG grid, I-AVF left and V1-V6 right."""
        self.fig.clf()
        rec = self._records.get(rec_id)
        if not rec:
            self.btn_save_plot.setEnabled(False)
            self.canvas.draw_idle()
            return
        sig = np.asarray(rec["signals"])
        meta = rec.get("metadata") or {}
        try:
            fs = float(meta.get("fs") or meta.get("sampling_freq") or 0)
        except (TypeError, ValueError):
            fs = 0.0
        n = sig.shape[1] if sig.ndim == 2 else 0
        if n == 0:
            self.btn_save_plot.setEnabled(False)
            self.canvas.draw_idle()
            return
        t = (np.arange(n) / fs) if fs else np.arange(n)

        axes = self.fig.subplots(6, 2, sharex=True)
        for col in range(2):
            for row in range(6):
                i = col * 6 + row
                ax = axes[row][col]
                ax.plot(t, sig[i], color=ACCENT, linewidth=0.8)
                ax.set_facecolor(SURFACE2)
                for sp in ax.spines.values():
                    sp.set_color(BORDER)
                    sp.set_linewidth(0.6)
                if fs:
                    ax.xaxis.set_major_locator(MultipleLocator(1.0 if t[-1] > 6 else 0.5))
                    ax.xaxis.set_minor_locator(MultipleLocator(0.2))
                ax.minorticks_on()
                ax.grid(which="major", color=GRID_MAJOR, linewidth=0.6)
                ax.grid(which="minor", color=GRID_MINOR, linewidth=0.4)
                ax.tick_params(colors=MUTED, labelsize=7, length=2)
                ax.margins(x=0)
                ax.text(0.012, 0.95, FINAL_LEADS[i], transform=ax.transAxes,
                        color="#B9C7D6", fontsize=8, fontweight="bold", va="top")
        for col in range(2):
            axes[5][col].set_xlabel("seconds" if fs else "samples",
                                    color=MUTED, fontsize=8)
        self.fig.subplots_adjust(left=0.055, right=0.995, top=0.99,
                                 bottom=0.075, hspace=0.35, wspace=0.11)
        self.canvas.draw_idle()
        self.btn_save_plot.setEnabled(True)

    def _save_plot(self):
        """Save the current 12-lead figure as a PNG, keeping the dark background."""
        rec = self.cmb_records.currentText()
        if not rec:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save plot",
                                              str(Path(self._output_dir or ".") / f"{rec}_12lead.png"),
                                              "PNG image (*.png)")
        if path:
            self.fig.savefig(path, dpi=200, facecolor=self.fig.get_facecolor())
            self._append_log(f"Saved plot: {path}")

    # Close

    def closeEvent(self, event):
        """Cancel a running batch and give it a moment to unwind before quitting."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(4000)
        for scanner in list(self._scanners):
            scanner.wait(2000)
        event.accept()


def build_palette():
    """The dark palette used by the app."""
    pal = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT,
        QPalette.Base: SURFACE2, QPalette.AlternateBase: SURFACE,
        QPalette.Text: TEXT, QPalette.Button: SURFACE,
        QPalette.ButtonText: TEXT, QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: ON_ACCENT, QPalette.ToolTipBase: SURFACE,
        QPalette.ToolTipText: TEXT, QPalette.PlaceholderText: MUTED,
        QPalette.Link: "#6FB7FF", QPalette.BrightText: ERROR,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#5A6B7D"))
    return pal


def main():
    """Start the app: Fusion style, dark palette, QSS, generated icon. Returns the exit code."""
    app = QApplication(sys.argv)
    app.setApplicationName("ECG-Format-Converter")
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)
    app.setWindowIcon(make_app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())