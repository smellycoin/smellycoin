from __future__ import annotations

import sys
import os
import time
import platform
from datetime import datetime
from typing import Dict, Any

from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
import psutil  # type: ignore

from .gui_core import SoloMinerCore
from core.config import get_config


PRIMARY = "#FFD000"
BG = "#000000"
TEXT = "#BFBFBF"
ACCENT = "#FFC400"
CARD = "#0b0b0b"
BORDER = "#242424"
GREEN = "#00ff66"
RED = "#ff2640"
AMBER = "#ffcc00"
CRT = "#00ff88"
SCROLL_HL = "#101010"
FONT_FAMILY = '"IBM Plex Mono","JetBrains Mono","Fira Mono","Consolas","Roboto Mono",monospace"'
FONTS_COOL = ["IBM Plex Mono","JetBrains Mono","Fira Mono","Cascadia Mono","Inconsolata","Ubuntu Mono","Source Code Pro","Noto Sans Mono"]
SLOGANS = [
    "a fair shot for everyone",
    "compute for the people",
    "open hash, open future",
    "built for resilience",
    "proof-of-work, proof-of-fairness",
    "a fair network for all miners",
    "built for simplicity",
    "easy for all"
]


def smelly_palette() -> str:
    # Clean, high-contrast yellow-on-black. Footer matches body. Pure text buttons (no dark fill).
    return f"""
    QWidget {{
        background-color: {BG};
        color: {AMBER};
        font-family: {FONT_FAMILY};
        font-size: 10.5pt;
        selection-background-color: #1c1c1c;
        selection-color: {PRIMARY};
    }}
    QMainWindow, QFrame, QGroupBox {{
        background-color: {CARD};
        border: 1px solid {BORDER};
        color: {AMBER};
    }}
    /* Center group titles */
    QGroupBox::title {{
        subcontrol-origin: margin; subcontrol-position: top center;
        padding: 2px 10px;
        color: {PRIMARY};
        font-weight: 800; letter-spacing: .6px;
        background: transparent;
        border: none;
        margin-top: -10px;
    }}
    /* Tabs with extra spacing below to separate from content */
    QTabWidget::pane {{ border: 1px solid {BORDER}; background: {BG}; margin-top: 8px; }}
    QTabBar::tab {{
        background: transparent; color: {PRIMARY};
        padding: 6px 14px; border: none; margin: 0 6px;
    }}
    QTabBar::tab:hover {{ background: transparent; color: {ACCENT}; }}
    QTabBar::tab:selected {{ background: transparent; color: {ACCENT}; text-decoration: underline; }}
    QLabel#title {{
        color: {PRIMARY}; font-weight: 800; font-size: 12pt; letter-spacing: 1.2px; background: transparent; border: none; padding:0px;
    }}
    /* Pure text buttons: no background box; only color changes */
    QPushButton {{
        background: transparent; color: {PRIMARY}; border: none; padding: 6px 10px;
    }}
    QPushButton:hover {{ color: {ACCENT}; text-decoration: underline; }}
    QPushButton:pressed {{ color: {PRIMARY}; }}
    /* Header titlebar toolbuttons keep their micro boxes -> switch to text-only look */
    QToolButton {{
        background: transparent; border: none; color: {PRIMARY}; padding: 2px 6px;
    }}
    QToolButton:hover {{ color: {ACCENT}; }}
    /* Inputs: yellow text, yellow border */
    QLineEdit, QSpinBox {{
        background: #070707; color: {PRIMARY}; border: 1px solid {PRIMARY}; padding: 6px 8px; border-radius: 4px;
    }}
    QPlainTextEdit {{
        background: #070707; color: {PRIMARY}; border: 1px solid {PRIMARY};
    }}
    /* Table-like labels on left remain yellow */
    QLabel {{ color: {AMBER}; }}
    QScrollBar:vertical, QScrollBar:horizontal {{
        background: {CARD}; border: 1px solid {BORDER}; margin: 0px;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: {SCROLL_HL}; border: 1px solid #333; min-height: 18px; min-width: 18px;
    }}
    QScrollBar::handle:hover {{ background: #161616; border-color: {PRIMARY}; }}
    QProgressBar {{
        background: transparent; border: 1px solid {PRIMARY}; color: {PRIMARY}; text-align: center; height: 14px; border-radius:4px;
    }}
    QProgressBar::chunk {{ background-color: {PRIMARY}; border-radius:4px; }}
    QStatusBar {{ background: {CARD}; color: {AMBER}; border-top: 1px solid {BORDER}; }}
    """


class LogView(QtWidgets.QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.setWordWrapMode(QtGui.QTextOption.NoWrap)
        monofont = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        monofont.setPointSize(10)
        self.setFont(monofont)

    @QtCore.Slot(str, str)
    def append_log(self, level: str, msg: str):
        color = {"info": TEXT, "warn": "#ffae00", "error": "#ff3b3b"}.get(level, TEXT)
        self.appendHtml(f'<pre style="margin:0;color:{color};">{QtGui.QGuiApplication.translate("log", msg)}</pre>')


class DashboardTab(QtWidgets.QWidget):
    startClicked = QtCore.Signal()
    stopClicked = QtCore.Signal()
    restartClicked = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        # Top compact bar with title, clock, sysinfo
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("SMELLY-Miner  //  SOLO")
        title.setObjectName("title")
        title.setContentsMargins(0, 0, 0, 0)
        title.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self.labClock = QtWidgets.QLabel("--:--:--")
        self.labClock.setStyleSheet(f"color:{CRT};")
        self.labClock.setFixedHeight(18)
        self.labSys = QtWidgets.QLabel("sys: —")
        self.labSys.setStyleSheet(f"color:{AMBER};")
        self.labSys.setFixedHeight(18)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.labClock)
        top.addSpacing(12)
        top.addWidget(self.labSys)
        layout.addLayout(top)

        # Controls row (spaced, borderless buttons)
        ctr = QtWidgets.QHBoxLayout()
        ctr.setSpacing(10)
        self.btnStart = QtWidgets.QPushButton("▶ START")
        self.btnStop = QtWidgets.QPushButton("■ STOP")
        self.btnRestart = QtWidgets.QPushButton("↻ RESTART")
        for b in (self.btnStart, self.btnStop, self.btnRestart):
            b.setCursor(QtCore.Qt.PointingHandCursor)
            ctr.addWidget(b)
        ctr.addStretch(1)
        layout.addLayout(ctr)

        # Status grid (2 columns)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setContentsMargins(14, 10, 14, 10)

        def add_row(r: int, label: str):
            l = QtWidgets.QLabel(label)
            l.setStyleSheet(f"color:{AMBER};")
            l.setFixedWidth(160)
            v = QtWidgets.QLabel("—")
            v.setMinimumWidth(420)
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            grid.addWidget(l, r, 0)
            grid.addWidget(v, r, 1)
            return v

        self.labBackend = add_row(0, "backend")
        self.labConn = add_row(1, "status")
        self.labTicket = add_row(2, "ticket ttl(ms)")
        self.labWindow = add_row(3, "nonce window")
        self.labHashrate = add_row(4, "hashrate(H/s)")
        self.labNear = add_row(5, "near-targets✓")
        self.labBlocks = add_row(6, "blocks✓")
        self.labPrev = add_row(7, "prev")
        self.labTarget = add_row(8, "target")
        self.labVersion = add_row(9, "version")

        box = QtWidgets.QGroupBox("telemetry")
        bx = QtWidgets.QVBoxLayout()
        bx.setContentsMargins(8, 6, 8, 6)
        bx.addLayout(grid)
        box.setLayout(bx)
        layout.addWidget(box)

        # Per-thread hashrate bars
        self.rateBars = QtWidgets.QGroupBox("per-thread")
        vb = QtWidgets.QVBoxLayout()
        vb.setContentsMargins(14, 10, 14, 10)
        self.listBars = QtWidgets.QVBoxLayout()
        self.listBars.setSpacing(6)
        vb.addLayout(self.listBars)
        self.rateBars.setLayout(vb)
        layout.addWidget(self.rateBars)
        layout.addStretch(1)

        self.btnStart.clicked.connect(self.startClicked.emit)
        self.btnStop.clicked.connect(self.stopClicked.emit)
        self.btnRestart.clicked.connect(self.restartClicked.emit)

        # Timer for clock and sys info
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _tick(self):
        self.labClock.setText(datetime.now().strftime("%H:%M:%S"))
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            self.labSys.setText(f"sys: CPU {cpu:.0f}% MEM {mem:.0f}%")
        except Exception:
            self.labSys.setText("sys")

    def update_rates(self, total: float, per_thread: Dict[str, float]):
        self.labHashrate.setText(f"{int(total)}")
        # clear and rebuild bars (max 16)
        while self.listBars.count():
            item = self.listBars.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for name, rate in sorted(per_thread.items())[:16]:
            hb = QtWidgets.QHBoxLayout()
            lbl = QtWidgets.QLabel(f"thread-{name}")
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 2000)
            bar.setValue(int(min(2000.0, rate)))
            bar.setFormat(f"{int(rate)} H/s")
            hb.addWidget(lbl, 0)
            hb.addWidget(bar, 1)
            wrap = QtWidgets.QWidget()
            wrap.setLayout(hb)
            self.listBars.addWidget(wrap)


class ConfigTab(QtWidgets.QWidget):
    applyClicked = QtCore.Signal(dict)

    def __init__(self, defaults: Dict[str, Any]):
        super().__init__()
        self.defaults = defaults
        self._build_ui()

    def _build_ui(self):
        form = QtWidgets.QFormLayout()
        self.inHost = QtWidgets.QLineEdit(str(self.defaults.get("host", "127.0.0.1")))
        self.inPort = QtWidgets.QSpinBox()
        self.inPort.setMaximum(65535)
        self.inPort.setValue(int(self.defaults.get("port", 28445)))
        self.inAddress = QtWidgets.QLineEdit(str(self.defaults.get("addr", "SMELLY_SOLO")))
        self.inThreads = QtWidgets.QSpinBox()
        self.inThreads.setMinimum(1)
        self.inThreads.setMaximum(256)
        self.inThreads.setValue(int(self.defaults.get("threads", 4)))

        form.addRow("Node Host", self.inHost)
        form.addRow("Node RPC Port", self.inPort)
        form.addRow("Miner Address", self.inAddress)
        form.addRow("CPU Threads", self.inThreads)

        btns = QtWidgets.QHBoxLayout()
        self.btnApply = QtWidgets.QPushButton("Save & Apply")
        btns.addStretch(1)
        btns.addWidget(self.btnApply)

        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)
        v.addLayout(form)
        v.addLayout(btns)

        group = QtWidgets.QGroupBox("Configuration")
        group_lay = QtWidgets.QVBoxLayout(group)
        group_lay.setContentsMargins(20, 18, 20, 18)
        group_lay.addWidget(wrap)

        top = QtWidgets.QVBoxLayout(self)
        top.setContentsMargins(16, 14, 16, 16)
        top.setSpacing(12)
        top.addWidget(group)
        top.addStretch(1)

        self.btnApply.clicked.connect(self._apply)

    def _apply(self):
        cfg = {
            "host": self.inHost.text().strip() or "127.0.0.1",
            "port": int(self.inPort.value()),
            "addr": self.inAddress.text().strip() or "SMELLY_SOLO",
            "threads": int(self.inThreads.value()),
        }
        self.applyClicked.emit(cfg)


class ChainTab(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)
        self.info = QtWidgets.QLabel("Chain metrics will appear here (height, latest header, mempool, recent jobs).")
        lay.addWidget(self.info)
        lay.addStretch(1)


class SystemTab(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        lay = QtWidgets.QFormLayout(self)
        self.labCPU = QtWidgets.QLabel("—")
        self.labETA = QtWidgets.QLabel("—")
        self.labBackend = QtWidgets.QLabel("argon2id")
        lay.addRow("CPU Threads Use", self.labCPU)
        lay.addRow("ETA (share/block)", self.labETA)
        lay.addRow("Backend", self.labBackend)


class TitleBar(QtWidgets.QWidget):
    def __init__(self, parent: "MainWindow"):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(30)
        self.setAutoFillBackground(False)
        self.setStyleSheet(f"background:{BG}; border-bottom:1px solid {BORDER};")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(10)

        self.icon = QtWidgets.QLabel()
        self.icon.setPixmap(parent._smelly_icon().pixmap(14, 14))
        self.title = QtWidgets.QLabel("SMELLY-Miner  //  ARGON2ID")
        self.title.setStyleSheet(f"color:{PRIMARY}; font-weight:800; background:transparent; border:none;")
        self.title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        lay.addWidget(self.icon)
        lay.addWidget(self.title)
        lay.addStretch(1)

        # Glyphs: triangular minimize, square maximize, X close with even spacing
        def mk_btn(txt: str, w: int = 28) -> QtWidgets.QToolButton:
            b = QtWidgets.QToolButton()
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedSize(w, 20)
            b.setStyleSheet(f"QToolButton{{color:{PRIMARY}; background:transparent; border:none;}}"
                            f"QToolButton:hover{{color:{ACCENT};}}")
            b.setText(txt)
            return b

        self.btnMin = mk_btn("▾")   # triangle-style
        self.btnMax = mk_btn("▢")   # thin square
        self.btnClose = mk_btn("✖") # sharper X

        btns = QtWidgets.QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(8)
        btns.addWidget(self.btnMin)
        btns.addWidget(self.btnMax)
        btns.addWidget(self.btnClose)
        wrap = QtWidgets.QWidget()
        wrap.setLayout(btns)
        lay.addWidget(wrap)

        self.btnMin.clicked.connect(self.parent.showMinimized)
        self.btnMax.clicked.connect(self._toggle_max)
        self.btnClose.clicked.connect(self.parent.close)

        self._drag_pos = None

    def _toggle_max(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        if e.button() == QtCore.Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.parent.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        if self._drag_pos is not None and e.buttons() & QtCore.Qt.LeftButton:
            self.parent.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent):
        self._drag_pos = None
        e.accept()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        # Frameless window with custom title bar
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)

        self.setWindowTitle("SMELLY-Miner SOLO")
        self.setMinimumSize(920, 560)
        self.setWindowIcon(self._smelly_icon())
        self.setStyleSheet(smelly_palette())

        # Wrap central content with border
        wrapper = QtWidgets.QWidget()
        wrapper.setStyleSheet(f"background:{CARD}; border:1px solid {BORDER};")
        outer = QtWidgets.QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Custom titlebar
        self.titleBar = TitleBar(self)
        outer.addWidget(self.titleBar)

        # Main content
        content = QtWidgets.QWidget()
        content_lay = QtWidgets.QVBoxLayout(content)
        content_lay.setContentsMargins(16, 14, 16, 16)
        content_lay.setSpacing(14)

        # Defaults from config
        cfg = get_config()
        defaults = {
            "host": cfg.get("network.rpc_host", "127.0.0.1"),
            "port": int(cfg.get("network.rpc_port", 28445)),
            "addr": cfg.get("solo.default_address", "SMELLY_SOLO"),
            "threads": int(cfg.get("solo.threads", 4)),
        }
        # Tabs
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setMinimumHeight(420)
        self.tabDash = DashboardTab()
        self.tabLogs = LogView()
        self.tabConfig = ConfigTab(defaults)
        self.tabChain = ChainTab()
        self.tabSystem = SystemTab()
        self.tabs.addTab(self.tabDash, "dash")
        self.tabs.addTab(self.tabLogs, "logs")
        self.tabs.addTab(self.tabConfig, "config")
        self.tabs.addTab(self.tabChain, "chain")
        self.tabs.addTab(self.tabSystem, "system")

        content_lay.addWidget(self.tabs)
        outer.addWidget(content)

        self.setCentralWidget(wrapper)

        # Engine (solo)
        self.engine = SoloMinerCore()

        # wiring
        self.tabDash.startClicked.connect(self._on_start)
        self.tabDash.stopClicked.connect(self._on_stop)
        self.tabDash.restartClicked.connect(self._on_restart)
        self.tabConfig.applyClicked.connect(self._on_apply_cfg)

        # Bind engine callbacks -> Qt slots via lambdas
        self.engine.on_log = lambda lvl, msg: self.tabLogs.append_log(lvl, msg)
        self.engine.on_status = lambda st: self._on_status(st)
        self.engine.on_rates = lambda total, per: self.tabDash.update_rates(total, {str(k): float(v) for k, v in per.items()})
        self.engine.on_ticket = lambda t: self._on_ticket(t)
        self.engine.on_accepts = lambda near, blocks: self._on_accepts(near, blocks)
        self.engine.on_error = lambda msg: self.tabLogs.append_log("error", msg)

        # Window settings
        self._settings = QtCore.QSettings("SMELLY", "SMELLY-Miner-SOLO")
        g = self._settings.value("geometry")
        if g:
            self.restoreGeometry(g)

        # Boot splash overlay (single-shot, guarded fade)
        self._session_font = FONTS_COOL[QtCore.QRandomGenerator.global_().bounded(len(FONTS_COOL))]
        self._splash = SplashOverlay(self, fixed_font=self._session_font)
        self._splash.show()
        # Trigger only one fade after 300ms; SplashOverlay itself also fades on first paint/show
        QtCore.QTimer.singleShot(300, self._splash.fade_out)

    # Allow double-click on titlebar to maximize/restore
    def mouseDoubleClickEvent(self, e: QtGui.QMouseEvent):
        if e.position().y() <= self.titleBar.height():
            self.titleBar._toggle_max()
            e.accept()
        else:
            super().mouseDoubleClickEvent(e)

    def closeEvent(self, e: QtGui.QCloseEvent):
        try:
            self._settings.setValue("geometry", self.saveGeometry())
        except Exception:
            pass
        try:
            self.engine.stop(join=True)
        except Exception:
            pass
        super().closeEvent(e)

    def _on_start(self):
        self.engine.start()

    def _on_stop(self):
        self.engine.stop()

    def _on_restart(self):
        self.engine.restart()

    def _on_apply_cfg(self, cfg: Dict[str, Any]):
        self.tabLogs.append_log("info", "Configuration saved. Restarting miner...")
        try:
            self.engine.set_config(
                host=cfg.get("host"),
                port=cfg.get("port"),
                addr=cfg.get("addr"),
                threads=cfg.get("threads"),
            )
        except Exception as e:
            self.tabLogs.append_log("error", f"apply config failed: {e}")
        self.engine.stop(join=True)
        self.engine.start()

    @QtCore.Slot(str)
    def _on_status(self, st: str):
        self.tabDash.labConn.setText(st)

    @QtCore.Slot(str)
    def _on_backend(self, be: str):
        self.tabDash.labBackend.setText(be)

    # Solo GUI: we don't measure latency; keep method for compatibility (unused)
    @QtCore.Slot(float)
    def _on_latency(self, ms: float):
        pass

    @QtCore.Slot(float, dict)
    def _on_rates(self, total: float, per_thread: Dict[str, float]):
        self.tabDash.update_rates(total, per_thread)

    # Solo GUI: map ticket info instead of job
    def _on_ticket(self, t: Dict[str, Any]):
        try:
            payload = t or {}
            valid_to = int(payload.get("valid_to", 0))
            ttl = max(0, valid_to - int(time.time() * 1000))
            self.tabDash.labTicket.setText(str(ttl))
            self.tabDash.labWindow.setText(f"{payload.get('nonce_start', 0)}..+{payload.get('nonce_window', 0)}")
            self.tabDash.labPrev.setText((payload.get("prev") or "")[:24] + "...")
            self.tabDash.labTarget.setText((payload.get("target") or "")[:24] + "...")
            self.tabDash.labVersion.setText(str(payload.get("version", 1)))
        except Exception:
            pass

    def _on_accepts(self, near: int, blocks: int):
        self.tabDash.labNear.setText(str(near))
        self.tabDash.labBlocks.setText(str(blocks))

    @QtCore.Slot(str)
    def _on_error(self, msg: str):
        self.tabLogs.append_log("error", msg)

    def _smelly_icon(self) -> QtGui.QIcon:
        # Build a simple in-memory yellow/black icon
        pix = QtGui.QPixmap(64, 64)
        pix.fill(QtGui.QColor(BG))
        p = QtGui.QPainter(pix)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor(PRIMARY), 4))
        p.drawRect(8, 8, 48, 48)
        p.drawLine(16, 40, 48, 24)
        p.end()
        icon = QtGui.QIcon(pix)
        return icon


class SplashOverlay(QtWidgets.QWidget):
    """
    Safer splash overlay:
    - Single-shot fade with reentrancy guard
    - Uses QWidget::deleteLater only after removing event filter and clearing parent
    - No cross-thread parenting; all timers are singleShot on the GUI thread
    """
    def __init__(self, parent: QtWidgets.QWidget, fixed_font: str):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._fixed_font = fixed_font
        self.setAutoFillBackground(False)
        self.setWindowOpacity(1.0)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.resize(parent.size())
        parent.installEventFilter(self)
        self._anim: QtCore.QPropertyAnimation | None = None
        self._fading = False
        self._destroyed = False
        print("[SMELLY-GUI] SplashOverlay created with font:", self._fixed_font, flush=True)

    def eventFilter(self, obj, ev):
        if obj is self.parent():
            if isinstance(ev, QtGui.QResizeEvent):
                self.resize(obj.size())
            if isinstance(ev, QtGui.QShowEvent):
                print("[SMELLY-GUI] Parent show event -> schedule splash fade", flush=True)
                QtCore.QTimer.singleShot(10, self.fade_out)
            if isinstance(ev, QtGui.QPaintEvent):
                QtCore.QTimer.singleShot(1, self.fade_out)
        return False

    @QtCore.Slot()
    def fade_out(self):
        if self._destroyed or self._fading:
            return
        if not self.isVisible():
            return
        self._fading = True
        print("[SMELLY-GUI] Splash starting fade", flush=True)
        # Stop any existing animation cleanly
        if self._anim is not None:
            try:
                self._anim.stop()
            except Exception:
                pass
            self._anim = None
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(160)
        try:
            anim.setStartValue(self.windowOpacity())
        except RuntimeError:
            return
        anim.setEndValue(0.0)

        def _finalize():
            if self._destroyed:
                return
            print("[SMELLY-GUI] Splash fade complete -> hiding", flush=True)
            try:
                self.hide()
            except Exception:
                pass
            # Remove filter and detach safely
            try:
                p = self.parent()
                if p:
                    p.removeEventFilter(self)
            except Exception:
                pass
            # Clear animation ref
            self._anim = None
            # Defer deletion to end of event loop
            self._destroyed = True
            try:
                self.setParent(None)
            except Exception:
                pass
            self.deleteLater()

        anim.finished.connect(_finalize)
        self._anim = anim
        anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def paintEvent(self, e: QtGui.QPaintEvent):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(0, 0, 0))
        f = QtGui.QFont(self._fixed_font, 24, QtGui.QFont.Black)
        p.setFont(f)
        p.setPen(QtGui.QColor(PRIMARY))
        p.drawText(self.rect(), QtCore.Qt.AlignCenter, "SMELLYCOIN")
        p.end()


def launch_gui():
    # Enable UTF-8 in Windows console for glyphs
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass

    print("[SMELLY-GUI] Launching GUI...", flush=True)
    app = QtWidgets.QApplication(sys.argv or ["smelly_gui"])
    # Set application name/org for QSettings isolation
    app.setApplicationName("SMELLY-Miner-SOLO")
    app.setOrganizationName("SMELLY")

    slogan = SLOGANS[QtCore.QRandomGenerator.global_().bounded(len(SLOGANS))]
    print("[SMELLY-GUI] Chosen slogan:", slogan, flush=True)
    win = MainWindow()
    print("[SMELLY-GUI] MainWindow created", flush=True)
    # footer label across all tabs
    footer = QtWidgets.QLabel(f"SMELLYCOIN — {slogan}")
    footer.setStyleSheet(f"color:{AMBER}; padding:6px 8px; background:{CARD};")
    footer.setAlignment(QtCore.Qt.AlignCenter)
    win.setStatusBar(QtWidgets.QStatusBar())
    win.statusBar().setSizeGripEnabled(False)
    win.statusBar().addPermanentWidget(footer, 1)
    win.show()
    print("[SMELLY-GUI] Window shown", flush=True)
    # Safety: ensure fade after 2s if still visible (single-shot, guarded inside SplashOverlay)
    def _safety():
        print("[SMELLY-GUI] Safety timer firing (2s)", flush=True)
        sp = getattr(win, "_splash", None)
        if isinstance(sp, SplashOverlay):
            sp.fade_out()
    QtCore.QTimer.singleShot(2000, _safety)
    return app.exec()

# Allow module execution via `python -m apps.miner.smelly_gui`
if __name__ == "__main__":
    sys.exit(launch_gui())
