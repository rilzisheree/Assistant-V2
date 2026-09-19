from __future__ import annotations

import json
import html
import math
import os
import platform
import re
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QDate, QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPoint,
    QPointF,
    QPropertyAnimation, QRect, QRectF, QSize, QTime, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut, QTextOption,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QScrollArea, QTimeEdit,
    QGraphicsOpacityEffect, QMainWindow, QPushButton, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QMenu,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "MARK LIV"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 1180, 760
_MIN_W,     _MIN_H     = 980, 620
_LEFT_W  = 176
_RIGHT_W = 304

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    # Near-black glass with a cold, restrained blue signal colour. These
    # values are shared by both the painted HUD and the Qt panels so the
    # surface reads as one calm instrument instead of a neon dashboard.
    BG        = "#010308"
    PANEL     = "#040910"
    PANEL2    = "#07111c"
    BORDER    = "#0d1b2a"
    BORDER_B  = "#1e3a53"
    BORDER_A  = "#142b40"
    PRI       = "#a9bdcf"
    PRI_DIM   = "#58738b"
    PRI_GHO   = "#081827"
    ACC       = "#c5d5df"
    ACC2      = "#6689a4"
    GREEN     = "#91aebd"
    GREEN_D   = "#4e6b82"
    RED       = "#d97783"
    MUTED_C   = "#8f6474"
    TEXT      = "#c4d4df"
    TEXT_DIM  = "#536b7e"
    TEXT_MED  = "#8299aa"
    WHITE     = "#e3ebf0"
    DARK      = "#02050a"
    BAR_BG    = "#0a1826"

# Keys tied to the accent colour — semantic error colour stays fixed.
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}
_COLOR_KEYS = tuple(
    key for key, value in vars(C).items()
    if key.isupper() and isinstance(value, str) and value.startswith("#")
)
_COLOR_DEFAULTS: dict[str, str] = {key: getattr(C, key) for key in _COLOR_KEYS}
_MONOCHROME_MODE = False

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]

# Monochrome is a designed black-first palette rather than a colour filter.
# Deep blue is reserved for depth, active states, and the core's energy.
_MONOCHROME_PALETTE: dict[str, str] = {
    "BG":       "#020407",
    "PANEL":    "#05080c",
    "PANEL2":   "#0a1118",
    "BORDER":   "#101a23",
    "BORDER_B": "#294052",
    "BORDER_A": "#1d3444",
    "PRI":      "#bbc9d2",
    "PRI_DIM":  "#6d8595",
    "PRI_GHO":  "#0d1d29",
    "ACC":      "#cbd7dd",
    "ACC2":     "#7d99a9",
    "GREEN":    "#a1b8c2",
    "GREEN_D":  "#5c7785",
    "RED":      "#aa7a83",
    "MUTED_C":  "#876b74",
    "TEXT":     "#cbd8de",
    "TEXT_DIM": "#657b87",
    "TEXT_MED": "#91a5ae",
    "WHITE":    "#e7eef1",
    "DARK":     "#030609",
    "BAR_BG":   "#0d1b25",
}
_MONOCHROME_HEXES = frozenset(value.lower() for value in _MONOCHROME_PALETTE.values())


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def _monochrome_hex(hex_value: str) -> str:
    """Map an arbitrary UI colour to restrained black-blue monochrome."""
    import colorsys

    color = QColor(hex_value)
    if not color.isValid():
        return hex_value
    canonical = color.name().lower()
    if canonical in _MONOCHROME_HEXES:
        return canonical

    red, green, blue = color.red(), color.green(), color.blue()
    _hue, saturation, value = colorsys.rgb_to_hsv(
        red / 255.0, green / 255.0, blue / 255.0,
    )
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0
    if value < 0.08:
        level = round(luminance * 255)
        return f"#{level:02x}{max(0, level - 1):02x}{min(255, level + 3):02x}"
    if saturation < 0.08:
        level = round(luminance * 255)
        return f"#{level:02x}{level:02x}{min(255, level + 2):02x}"

    # Keep coloured source elements recognisable as active accents, but pull
    # their hue into the same low-saturation blue family and cap brightness.
    red, green, blue = colorsys.hsv_to_rgb(
        0.59, min(0.30, max(0.12, saturation * 0.55)),
        min(0.82, value * 0.92),
    )
    return "#{:02x}{:02x}{:02x}".format(
        round(red * 255), round(green * 255), round(blue * 255),
    )


def _monochrome_stylesheet(stylesheet: str) -> str:
    """Retheme stylesheet literals while preserving the existing CSS."""
    stylesheet = re.sub(
        r"#[0-9a-fA-F]{6}(?![0-9a-fA-F])",
        lambda match: _monochrome_hex(match.group(0)),
        stylesheet,
    )

    def _mono_rgb(match: re.Match) -> str:
        red, green, blue = (int(match.group(index)) for index in (1, 2, 3))
        mapped = _monochrome_hex(f"#{red:02x}{green:02x}{blue:02x}")
        color = QColor(mapped)
        return (f"rgba({color.red()}, {color.green()}, {color.blue()}"
                f"{match.group(4) or ''})")

    return re.sub(
        r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(\s*,\s*[^)]+)?\)",
        _mono_rgb,
        stylesheet,
    )


def apply_ui_theme(accent_hex: str, monochrome: bool) -> bool:
    """Reset the palette, apply the selected accent, then optionally use mono."""
    global _MONOCHROME_MODE
    for key, value in _COLOR_DEFAULTS.items():
        setattr(C, key, value)
    if not apply_ui_accent(accent_hex or DEFAULT_UI_COLOR):
        return False
    if monochrome:
        for key, value in _MONOCHROME_PALETTE.items():
            setattr(C, key, value)
    _MONOCHROME_MODE = monochrome
    return True


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    if _MONOCHROME_MODE:
        c = QColor(_monochrome_hex(c.name()))
    c.setAlpha(a)
    return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name
        self._telemetry = {
            "CPU": ("--", 0.0), "MEM": ("--", 0.0), "GPU": ("N/A", 0.0),
            "NET": ("--", 0.0), "TMP": ("N/A", 0.0),
        }

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; the avatar object is kept either way so switching
        # back is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style
            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "face"
        self._core_phase = 0.0
        # Keep the reactor's motion in a deliberately narrow, low-speed range.
        # The value is eased toward its state target in _step() so a state
        # change can never make the internal elements jump into a spin.
        self._core_rotation = 0.46
        # Opacity/scale progress for the small top-center speaking orb.
        self._speaking_orb = 0.0

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def set_telemetry(self, snapshot: dict) -> None:
        """Update the small live readouts embedded in the orb."""
        try:
            cpu = float(snapshot.get("cpu", 0.0))
            mem = float(snapshot.get("mem", 0.0))
            net = float(snapshot.get("net", 0.0))
            gpu = float(snapshot.get("gpu", -1.0))
            tmp = float(snapshot.get("tmp", -1.0))
            net_text = (f"{net * 1024:.0f}KB/s" if net < 1.0
                        else f"{net:.1f}MB/s")
            self._telemetry = {
                "CPU": (f"{cpu:.0f}%", max(0.0, min(100.0, cpu))),
                "MEM": (f"{mem:.0f}%", max(0.0, min(100.0, mem))),
                "GPU": (f"{gpu:.0f}%" if gpu >= 0 else "N/A",
                        max(0.0, min(100.0, gpu)) if gpu >= 0 else 0.0),
                "NET": (net_text, max(0.0, min(100.0, net * 10))),
                "TMP": (f"{tmp:.0f}°C" if tmp >= 0 else "N/A",
                        max(0.0, min(100.0, tmp)) if tmp >= 0 else 0.0),
            }
            self.update()
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant JARVIS
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))
        rotation_target = 0.46
        if self.state in ("THINKING", "PROCESSING"):
            rotation_target = 0.58
        elif self.speaking:
            rotation_target = 0.50
        self._core_rotation += (
            rotation_target - self._core_rotation
        ) * min(1.0, max(0.0, dt) * 3.0)

        # A short ease-in/ease-out gives the speaking indicator a materialised
        # feel instead of making it pop in and out with the audio state.
        orb_target = 1.0 if self.speaking else 0.0
        self._speaking_orb += (
            orb_target - self._speaking_orb
        ) * min(1.0, max(0.0, dt) * 11.0)

        if self._avatar is not None and self.hud_style == "face":
            self._avatar.step(dt, amp, speaking=self.speaking,
                              muted=self.muted, state=self.state,
                              v_open=v_open, v_wide=v_wide or 0.0,
                              v_level=v_level, v_seq=v_seq,
                              v_hop=(sched[2] if sched is not None else 0.02))
        else:
            # Fallback core: slow "breathing" base target, lifted by the level.
            if now - self._last_t > (0.12 if self.speaking else 0.5):
                if self.speaking:
                    self._base_scale = 1.03
                    self._base_halo  = 122.0
                elif self.muted:
                    self._base_scale = random.uniform(0.998, 1.002)
                    self._base_halo  = random.uniform(15, 28)
                else:
                    self._base_scale = random.uniform(1.001, 1.008)
                    self._base_halo  = random.uniform(48, 68)
                self._last_t = now

            if self.muted:
                self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
            elif self.speaking:
                self._tgt_scale = self._base_scale + amp * 0.13
                self._tgt_halo  = self._base_halo  + amp * 95.0
            else:
                self._tgt_scale = self._base_scale + amp * 0.06
                self._tgt_halo  = self._base_halo  + amp * 75.0

            sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
            self._scale += (self._tgt_scale - self._scale) * sp
            self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or self._speaking_orb > 0.01 or amp > 0.02
                  or self.state in ("THINKING", "PROCESSING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core_orb(self, p: QPainter, cx: float, cy: float, r: float,
                        W: float = 0.0, H: float = 0.0, mini: bool = False):
        """Paint the calm volumetric intelligence core.

        This deliberately avoids the old reactor language: no radar circles,
        targeting marks, rotating rings, or concentric technical scales. The
        animation is carried by slow internal wisps and fine particles inside
        one translucent glass-like volume.
        """
        main, acc = self._core_colours()
        t = self._core_phase
        amp = self._amp_disp
        state = self.state.upper()
        # Internal motion is intentionally independent of microphone level and
        # speaking state. Those states may brighten the orb, but never turn it
        # into a loading spinner.
        motion = 0.22 + (self._core_rotation - 0.46) * 0.22
        orb_r = (r * 0.72 if mini
                 else min(r * 0.72, max(78.0, min(W, H) * 0.34)))
        orb = QRectF(cx - orb_r, cy - orb_r, orb_r * 2, orb_r * 2)

        def mix(col: QColor, amount: float) -> QColor:
            amount = max(0.0, min(1.0, amount))
            base = QColor(C.BG)
            return QColor(
                int(base.red() + (col.red() - base.red()) * amount),
                int(base.green() + (col.green() - base.green()) * amount),
                int(base.blue() + (col.blue() - base.blue()) * amount),
            )

        # Wide, low-opacity aura: atmospheric depth without a neon halo.
        aura = QRadialGradient(cx - orb_r * 0.08, cy - orb_r * 0.12, orb_r * 1.35)
        aura.setColorAt(0.0, qcol(main.name(), 70 + int(amp * 24)))
        aura.setColorAt(0.42, qcol("#234c78", 34))
        aura.setColorAt(0.78, qcol("#172746", 18))
        aura.setColorAt(1.0, qcol(C.BG, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(aura))
        p.drawEllipse(QRectF(cx - orb_r * 1.35, cy - orb_r * 1.35,
                             orb_r * 2.7, orb_r * 2.7))

        # The single glass volume. A cool highlight at upper-left and an
        # deep-blue falloff at the lower-right makes it read as dimensional.
        sphere = QRadialGradient(cx - orb_r * 0.28, cy - orb_r * 0.34,
                                 orb_r * 1.18)
        sphere.setColorAt(0.00, qcol(C.WHITE, 115))
        sphere.setColorAt(0.10, qcol(main.name(), 108))
        sphere.setColorAt(0.34, qcol("#2c5c86", 92))
        sphere.setColorAt(0.66, qcol("#172d4a", 180))
        sphere.setColorAt(0.88, qcol("#07111e", 232))
        sphere.setColorAt(1.00, qcol(C.BG, 245))
        p.setBrush(QBrush(sphere))
        p.drawEllipse(orb)

        # Fine internal energy flow clipped to the sphere. The paths drift
        # slowly so the core feels alive without turning into a screen saver.
        sphere_path = QPainterPath()
        sphere_path.addEllipse(orb)
        p.save()
        p.setClipPath(sphere_path)

        for k, (offset, width, colour, opacity) in enumerate((
            (-0.46, 1.7, main, 105),
            (0.03, 1.15, acc, 82),
            (0.39, 1.35, qcol(C.ACC2), 62),
        )):
            path = QPainterPath()
            for i in range(31):
                x = cx - orb_r * 1.18 + i * (orb_r * 2.36 / 30.0)
                phase = t * motion * (1.0 + k * 0.14) + i * 0.29 + k * 1.4
                y = cy + offset * orb_r + math.sin(phase) * orb_r * 0.11
                y += math.sin(phase * 0.47 + 1.8) * orb_r * 0.055
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(colour, width))
            pen_col = mix(colour, 0.5)
            pen_col.setAlpha(opacity + int(amp * 24))
            p.setPen(QPen(pen_col, width))
            p.drawPath(path)

        # A second set of diagonal filaments adds volume while keeping the
        # visual language organic instead of mechanical.
        for k in range(2):
            path = QPainterPath()
            for i in range(24):
                y = cy - orb_r * 1.1 + i * (orb_r * 2.2 / 23.0)
                phase = t * 0.25 + i * 0.42 + k * 2.3
                x = cx + (k - 0.5) * orb_r * 0.72 + math.sin(phase) * orb_r * 0.16
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            filament = mix(acc if k else main, 0.48)
            filament.setAlpha(38 + int(amp * 18))
            p.setPen(QPen(filament, 1.0))
            p.drawPath(path)

        # Holographic substrate: faint scanlines and offset translucent
        # contours make the volume feel layered without outlining another
        # circle around it.
        scan = QColor(C.PRI)
        scan.setAlpha(10)
        p.setPen(QPen(scan, 0.55))
        for row in range(-8, 9):
            y = cy + row * orb_r * 0.105
            p.drawLine(QLineF(cx - orb_r * 0.96, y,
                              cx + orb_r * 0.96, y + math.sin(row) * 0.9))

        for layer in range(3):
            layer_path = QPainterPath()
            phase = t * (0.08 + layer * 0.025) + layer * 1.8
            for i in range(13):
                a = -math.pi * 0.92 + i * math.pi * 1.84 / 12.0
                rr = orb_r * (0.42 + layer * 0.09)
                px = cx + math.cos(a + phase * 0.04) * rr
                py = cy + math.sin(a) * rr * (0.48 + layer * 0.08)
                px += math.sin(phase + i * 0.7) * orb_r * 0.025
                py += math.cos(phase * 0.8 + i) * orb_r * 0.018
                if i == 0:
                    layer_path.moveTo(px, py)
                else:
                    layer_path.lineTo(px, py)
            layer_col = mix(acc if layer == 1 else main, 0.34)
            layer_col.setAlpha(22 + layer * 7)
            p.setPen(QPen(layer_col, 0.75))
            p.drawPath(layer_path)

        # Short segmented arcs are used as internal processor layers. Each
        # segment is isolated and offset, so this cannot read as a HUD ring.
        for i in range(16):
            seed = i * 2.43 + 0.7
            rr_x = orb_r * (0.36 + (i % 4) * 0.075)
            rr_y = rr_x * (0.48 + (i % 3) * 0.08)
            box = QRectF(cx - rr_x, cy - rr_y, rr_x * 2, rr_y * 2)
            start = int((seed * 83 + t * (0.22 if i % 2 else -0.16)) * 16)
            span = int((7 + (i % 5) * 4) * 16)
            arc_col = mix(acc if i % 5 == 0 else main, 0.42)
            arc_col.setAlpha(42 + (i % 4) * 8)
            p.setPen(QPen(arc_col, 0.8 + (i % 3) * 0.25))
            p.drawArc(box, start, span)

        # Faint signal waveforms cross only small portions of the volume.
        for wave in range(2):
            signal = QPainterPath()
            y0 = cy + (wave - 0.5) * orb_r * 0.38
            for i in range(25):
                px = cx - orb_r * 0.77 + i * (orb_r * 1.54 / 24.0)
                phase = i * 0.72 + t * (0.34 + wave * 0.12)
                py = y0 + math.sin(phase) * orb_r * (0.028 + amp * 0.018)
                if i == 0:
                    signal.moveTo(px, py)
                else:
                    signal.lineTo(px, py)
            signal_col = mix(acc if wave else main, 0.40)
            signal_col.setAlpha(38 + int(amp * 20))
            p.setPen(QPen(signal_col, 0.7))
            p.drawPath(signal)

        # Microscopic circuitry: compact orthogonal traces branch from
        # asymmetrical points instead of forming a symmetrical technical grid.
        for i in range(18):
            seed = i * 1.93 + 0.3
            px = cx + math.cos(seed) * orb_r * (0.18 + (i % 4) * 0.13)
            py = cy + math.sin(seed * 1.41) * orb_r * 0.48
            horizontal = orb_r * (0.045 + (i % 3) * 0.022)
            vertical = orb_r * (0.035 + (i % 4) * 0.014)
            direction = -1 if i % 2 else 1
            circuit = QPainterPath()
            circuit.moveTo(px, py)
            circuit.lineTo(px + horizontal * direction, py)
            circuit.lineTo(px + horizontal * direction,
                           py + vertical * (1 if i % 3 else -1))
            circuit_col = mix(acc if i % 6 == 0 else main, 0.38)
            circuit_col.setAlpha(54 + (i % 3) * 8)
            p.setPen(QPen(circuit_col, 0.75))
            p.drawPath(circuit)
            p.setBrush(QBrush(circuit_col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(px - 1.0, py - 1.0, 2.0, 2.0))

        # Organic branching structures suggest computation rather than
        # electricity: each trunk splits into two soft, irregular branches.
        for root in range(5):
            root_phase = root * 1.31 + t * motion * 0.42
            start_x = cx + math.cos(root_phase) * orb_r * 0.08
            start_y = cy + math.sin(root_phase) * orb_r * 0.10
            for branch in range(2):
                path = QPainterPath()
                path.moveTo(start_x, start_y)
                px, py = start_x, start_y
                for depth in range(5):
                    phase = root_phase + branch * 2.2 + depth * 0.72
                    px += math.cos(phase) * orb_r * (0.12 + depth * 0.018)
                    py += math.sin(phase * 1.13) * orb_r * (0.10 + depth * 0.014)
                    path.lineTo(px, py)
                branch_col = mix(acc if root % 2 else main, 0.46)
                branch_col.setAlpha(42 + int(amp * 28))
                p.setPen(QPen(branch_col, 1.0 + (root % 2) * 0.35))
                p.drawPath(path)

        # Deterministic particles: tiny points drift along the internal flow
        # and never jump because their seed is derived from their index.
        # A few hundred points create density without turning the orb into
        # noisy static at the app's normal size.
        for i in range(360):
            seed = i * 1.61803398875
            radial = 0.10 + (i % 17) / 22.0
            angle = seed + t * (0.06 + (i % 7) * 0.009) * motion
            wobble = math.sin(t * 0.42 + seed * 1.7) * (0.035 + amp * 0.025)
            px = cx + math.cos(angle) * orb_r * (radial + wobble)
            py = cy + math.sin(angle * 1.21) * orb_r * (radial * 0.72 + wobble)
            if ((px - cx) / orb_r) ** 2 + ((py - cy) / orb_r) ** 2 <= 0.88:
                col = acc if i % 7 == 0 else (main if i % 3 else qcol(C.ACC2))
                dot = mix(col, 0.68)
                dot.setAlpha(48 + int(92 * (0.5 + 0.5 * math.sin(seed + t * motion))))
                size = 0.55 + (i % 4) * 0.30
                p.setBrush(QBrush(dot))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(px - size, py - size, size * 2, size * 2))

        # Small node clusters breathe in and out around the core. The cluster
        # positions are stable, while their spread and brightness vary slowly.
        for cluster in range(8):
            seed = cluster * 2.17
            spread = 0.10 + 0.035 * math.sin(t * 0.55 + seed)
            node_x = cx + math.cos(seed + t * 0.05) * orb_r * (0.24 + cluster % 3 * 0.16)
            node_y = cy + math.sin(seed * 1.37 + t * 0.04) * orb_r * 0.46
            for member in range(5):
                a = seed + member * 1.256 + t * 0.08
                px = node_x + math.cos(a) * orb_r * spread
                py = node_y + math.sin(a) * orb_r * spread
                node = mix(acc if cluster % 2 else main, 0.82)
                node.setAlpha(95 + int(35 * (0.5 + 0.5 * math.sin(seed + t))))
                p.setBrush(QBrush(node))
                p.drawEllipse(QRectF(px - 1.2, py - 1.2, 2.4, 2.4))

        # Soft glass glint across the upper-left surface.
        glint = QPainterPath()
        glint.moveTo(cx - orb_r * 0.62, cy - orb_r * 0.48)
        glint.cubicTo(cx - orb_r * 0.30, cy - orb_r * 0.78,
                      cx + orb_r * 0.12, cy - orb_r * 0.72,
                      cx + orb_r * 0.40, cy - orb_r * 0.51)
        shine = QColor(C.WHITE)
        shine.setAlpha(72)
        p.setPen(QPen(shine, 2.2))
        p.drawPath(glint)
        p.restore()

        # The small speaking indicator uses the same core volume and internal
        # energy field, but omits the full-size telemetry/callout layer that
        # would be unreadable at indicator scale.
        if mini:
            return

        # Live system telemetry is part of the core geometry, not a separate
        # dashboard. Each readout gets a different anchor and a tiny meter so
        # the group stays asymmetric and compact.
        telemetry = list(self._telemetry.items())
        telemetry_layout = (
            (-2.48, -0.66, -1), (-2.02, 0.56, -1),
            (-0.70, -0.76, 1), (0.22, 0.70, 1), (1.18, 0.54, 1),
        )
        p.setFont(QFont("Courier New", 5, QFont.Weight.Bold))
        for (label, (value, pct)), (angle, y_bias, side) in zip(
                telemetry, telemetry_layout):
            tx = cx + math.cos(angle) * orb_r * 0.88
            ty = cy + y_bias * orb_r * 0.60
            edge_x = cx + math.cos(angle) * orb_r * 0.98
            edge_y = cy + math.sin(angle) * orb_r * 0.74
            tele_col = mix(acc if label in ("GPU", "TMP") else main, 0.44)
            tele_col.setAlpha(135)
            p.setPen(QPen(tele_col, 0.7))
            p.drawLine(QLineF(edge_x, edge_y, tx, ty))
            p.setBrush(QBrush(tele_col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(edge_x - 1.1, edge_y - 1.1, 2.2, 2.2))

            box_w = 48.0
            box_x = tx if side > 0 else tx - box_w
            text_col = mix(acc if label in ("GPU", "TMP") else main, 0.65)
            text_col.setAlpha(180)
            p.setPen(QPen(text_col, 1))
            p.drawText(QRectF(box_x, ty - 7, box_w, 7),
                       Qt.AlignmentFlag.AlignLeft if side > 0
                       else Qt.AlignmentFlag.AlignRight, f"{label} {value}")
            meter_w = 22.0
            meter_x = box_x if side > 0 else box_x + box_w - meter_w
            meter_y = ty + 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(mix(main, 0.20)))
            p.drawRect(QRectF(meter_x, meter_y, meter_w, 1.5))
            if pct > 0:
                p.setBrush(QBrush(text_col))
                p.drawRect(QRectF(meter_x, meter_y,
                                  meter_w * min(1.0, pct / 100.0), 1.5))

        # Subtle identity mark inside the volume, never giant text over it.
        name = self._assistant_name or "LUA"
        font = QFont("Segoe UI", max(11, int(orb_r * 0.115)),
                     QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        p.setFont(font)
        identity = QColor(C.WHITE)
        identity.setAlpha(182)
        p.setPen(QPen(identity, 1))
        p.drawText(orb, Qt.AlignmentFlag.AlignCenter, name)

        # Ambient depth field around the volume. These points fade quickly
        # toward the edge and are deliberately irregular rather than radial.
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(150):
            seed = 9.7 + i * 2.371
            distance = orb_r * (1.12 + (i % 19) / 16.0)
            angle = seed + t * (0.018 + (i % 4) * 0.004)
            px = cx + math.cos(angle) * distance
            py = cy + math.sin(angle * 1.17) * distance * 0.68
            if 5 < px < W - 5 and 6 < py < H - 6:
                fade = max(0.0, 1.0 - distance / (orb_r * 2.45))
                ambient = mix(main if i % 4 else acc, 0.38)
                ambient.setAlpha(int(80 * fade) + (i % 3) * 5)
                size = 0.45 + (i % 3) * 0.28
                p.setBrush(QBrush(ambient))
                p.drawEllipse(QRectF(px - size, py - size, size * 2, size * 2))

        # Surface fragments and micro data traces. They never connect into a
        # circle; each is a short, isolated mark embedded in the orb's field.
        for i in range(34):
            seed = i * 2.49 + 0.4
            distance = orb_r * (0.98 + (i % 5) * 0.045)
            angle = seed + t * (0.025 if i % 2 else -0.018)
            px = cx + math.cos(angle) * distance
            py = cy + math.sin(angle) * distance * 0.72
            tangent = angle + math.pi * 0.5
            length = 3.0 + (i % 4) * 2.0
            fragment = mix(acc if i % 5 == 0 else main, 0.46)
            fragment.setAlpha(70 + (i % 4) * 10)
            p.setPen(QPen(fragment, 0.8 + (i % 3) * 0.25))
            p.drawLine(QLineF(px, py,
                              px + math.cos(tangent) * length,
                              py + math.sin(tangent) * length))
            if i % 6 == 0:
                p.setBrush(QBrush(fragment))
                p.drawEllipse(QRectF(px - 1.4, py - 1.4, 2.8, 2.8))

        # A few external diagnostic traces sit close to the orb. They are
        # deliberately short and uneven, providing technical context without
        # turning the surrounding space into a dashboard.
        p.setFont(QFont("Courier New", 5))
        for i in range(12):
            seed = 1.1 + i * 2.71
            edge_r = orb_r * (1.02 + (i % 3) * 0.045)
            angle = seed + t * (0.012 if i % 2 else -0.009)
            ex = cx + math.cos(angle) * edge_r
            ey = cy + math.sin(angle) * edge_r * 0.72
            length = orb_r * (0.055 + (i % 3) * 0.018)
            outward = 1 if math.cos(angle) >= 0 else -1
            tx = ex + outward * length
            ty = ey + math.sin(seed * 1.7) * orb_r * 0.045
            trace_col = mix(acc if i % 5 == 0 else main, 0.34)
            trace_col.setAlpha(68 + (i % 3) * 8)
            p.setPen(QPen(trace_col, 0.7))
            p.drawLine(QLineF(ex, ey, tx, ty))
            p.setBrush(QBrush(trace_col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(ex - 1.15, ey - 1.15, 2.3, 2.3))
            telemetry = f"{(i * 17 + 4):02d}.{(i * 7 + 3):02d}"
            tele_col = mix(main, 0.30)
            tele_col.setAlpha(92)
            p.setPen(QPen(tele_col, 1))
            text_x = tx + (3 if outward > 0 else -29)
            p.drawText(QRectF(text_x, ty - 4, 26, 8),
                       Qt.AlignmentFlag.AlignLeft, telemetry)

        # Sparse contextual callouts sit outside the volume and stay within the
        # central workspace. Their lines point inward without becoming a HUD.
        labels = [
            ("PROCESSING" if state in ("THINKING", "PROCESSING")
             else "READY", "42%" if state in ("THINKING", "PROCESSING")
             else "MODEL / LIVE", -1, -0.62),
            ("VOICE ACTIVE" if self.speaking else "VOICE STANDBY",
             "AUDIO CHANNEL", 1, -0.62),
            ("MEMORY", "READY", -1, -0.02),
            ("CONTEXT", "SYNCED", 1, -0.02),
            ("LATENCY", "LIVE", -1, 0.57),
            ("LUA", "ONLINE", 1, 0.57),
        ]
        card_w = max(94.0, min(126.0, orb_r * 0.74))
        card_h = 30.0
        for i, (title, detail, side, y_ratio) in enumerate(labels):
            # Keep the callouts attached to the same six anchors, with only a
            # quiet two-pixel orbit so the HUD feels alive without drifting.
            panel_phase = t * 0.42 + i * 1.07
            orbit_dx = math.sin(panel_phase) * min(2.2, orb_r * 0.012)
            orbit_dy = math.cos(panel_phase * 0.83) * min(1.7, orb_r * 0.009)
            card_x = cx + side * (orb_r + card_w * 0.62) + orbit_dx
            card_y = cy + y_ratio * orb_r - card_h * 0.5 + orbit_dy
            if side < 0:
                card_x -= card_w
                line_x = card_x + card_w
            else:
                line_x = card_x
            card_x = max(6.0, min(W - card_w - 6.0, card_x))
            card_y = max(8.0, min(H - card_h - 8.0, card_y))
            line_x = card_x + card_w if side < 0 else card_x
            anchor_x = cx + side * orb_r * 0.74 + orbit_dx * 0.35
            anchor_y = cy + y_ratio * orb_r + orbit_dy * 0.35
            connector = mix(acc if title == "VOICE ACTIVE" else main, 0.42)
            connector.setAlpha(100)
            p.setPen(QPen(connector, 1.0))
            p.drawLine(QLineF(line_x, card_y + card_h * 0.5,
                              anchor_x, anchor_y))

            card = qcol(C.PANEL2)
            card.setAlpha(164)
            p.setBrush(QBrush(card))
            border = mix(main, 0.32)
            border.setAlpha(120)
            p.setPen(QPen(border, 1.0))
            p.drawRoundedRect(QRectF(card_x, card_y, card_w, card_h), 7, 7)

            p.setBrush(QBrush(QColor(0, 0, 0, 0)))
            p.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
            text_col = QColor(C.WHITE)
            text_col.setAlpha(190)
            p.setPen(QPen(text_col, 1))
            p.drawText(QRectF(card_x + 15, card_y + 4, card_w - 19, 10),
                       Qt.AlignmentFlag.AlignLeft, title)
            dot_col = QColor(C.GREEN if title in ("VOICE ACTIVE", "LUA")
                             else C.PRI)
            dot_col.setAlpha(180)
            p.setBrush(QBrush(dot_col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(card_x + 7, card_y + 7, 4, 4))
            detail_col = mix(acc if title == "VOICE ACTIVE" else main, 0.72)
            detail_col.setAlpha(190)
            p.setPen(QPen(detail_col, 1))
            p.setFont(QFont("Courier New", 5))
            p.drawText(QRectF(card_x + 15, card_y + 16, card_w - 19, 9),
                       Qt.AlignmentFlag.AlignLeft, detail)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                    W: float = 0.0, H: float = 0.0):
        """Draw the reactor at (cx, cy) with outer radius r, using the whole
        canvas (W x H) for the marks that frame it."""
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            """Pre-mix onto the background instead of asking Qt to composite.
            The raster engine's opaque path is several times faster than its
            translucent one, and everything here is a line or an arc."""
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. The atmosphere. One radial gradient doing what a stack of discs did
        #    badly: a wide, soft body of light that gives the thing presence
        #    before any detail is read. This single element decides whether the
        #    HUD looks vast or looks small, so it is drawn first and drawn big.
        # Concentrated rather than spread: a gradient reaching the outer rim
        # washes the whole disc a flat dim blue and reads as fog. Ending it at
        # two thirds leaves it a body of light with somewhere to fall off to,
        # which is what makes it look lit rather than tinted.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # The reference uses a luminous, physical centrepiece rather than a
        # thin technical outline. Draw this after the atmosphere so the glow
        # remains visible beneath the orbit lines.
        p.setPen(Qt.PenStyle.NoPen)
        for gr, alpha in ((r * 1.05, 22), (r * 0.82, 34), (r * 0.48, 48)):
            bloom = QRadialGradient(cx, cy, gr)
            bloom.setColorAt(0.0, qcol(main.name(), alpha))
            bloom.setColorAt(0.62, qcol(main.name(), max(1, alpha // 3)))
            bloom.setColorAt(1.0, qcol(main.name(), 0))
            p.setBrush(QBrush(bloom))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))

        core_r = r * 0.34
        core = QRadialGradient(cx - core_r * 0.18, cy - core_r * 0.22, core_r * 1.25)
        core.setColorAt(0.0, qcol(C.WHITE, 95))
        core.setColorAt(0.22, qcol(main.name(), 72))
        core.setColorAt(0.70, qcol(C.PANEL2, 180))
        core.setColorAt(1.0, qcol(C.BG, 245))
        p.setBrush(QBrush(core))
        p.setPen(QPen(qcol(main.name(), 155), 2.5))
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, core_r * 2, core_r * 2))

        # 2. Frame marks at the corners of the whole canvas, not of the circle.
        #    They are what set the scale: the eye reads the reactor as filling
        #    the room rather than sitting in the middle of it.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair across the full canvas, broken around the core so it
        #    frames the reactor rather than crossing it.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
        #    grey band at this size, and restraint is what made the original
        #    look expensive.
        for rr, a in ((1.00, 0.68), (0.93, 0.32), (0.68, 0.24)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 2.0 if rr == 1.0 else 1.1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
        #    with shorter minors between them.
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)

        # 6. Sweeping arcs. Long spans, not dashes. The eased rate is kept
        # deliberately low; listening and speaking can change brightness and
        # waveform energy without causing a sudden rotation jump.
        rate = self._core_rotation
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.95, 4.5),
                (0.845, 82,  3, -1, main, 0.64, 2.2),
                (0.760, 150, 1, +1, acc,  0.72, 3.0),
                (0.660, 64,  4, -1, main, 0.48, 1.8),
                (0.545, 128, 2, +1, main, 0.52, 2.0))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                          int(span * 16))

        # 7. The voice, as a ring of graduations that grow with it. Kept out at
        #    a wide radius so it never crowds the middle.
        n = 60
        ring = r * 0.415
        spikes = []
        for i in range(n):
            a = math.radians(i * (360.0 / n))
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = r * (idle + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                 cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)

        # 8. The inner ring the name sits in.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

        # 9. The name, sized from the string rather than from the radius alone:
        #    "J.A.R.V.I.S" and a name someone renamed to "MAX" are very
        #    different widths, and a fixed fraction of r spills one of them past
        #    the ring it is supposed to sit inside.
        name = self._assistant_name or ""
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)

    def _paint_speaking_orb(self, p: QPainter, cx: float, W: float, H: float):
        """Paint the restrained top-center indicator for JARVIS's own voice.

        It deliberately calls the same core renderer as the main reactor. The
        only difference is scale and the omission of full-size readouts, which
        would not be legible in this small indicator.
        """
        progress = max(0.0, min(1.0, self._speaking_orb))
        if progress <= 0.005:
            return

        eased = progress * progress * (3.0 - 2.0 * progress)
        fw = min(W, H)
        radius = max(18.0, min(34.0, fw * 0.045))
        pulse = 1.0 + (0.025 * math.sin(self._core_phase * 1.7)
                       if self.speaking else 0.0)
        scale = (0.82 + 0.18 * eased) * pulse
        orb_y = max(radius + 6.0, min(70.0, H * 0.10))

        p.save()
        p.translate(cx, orb_y)
        p.scale(scale, scale)
        p.setOpacity(eased)
        self._paint_core_orb(p, 0.0, 0.0, radius, W, H, mini=True)
        p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # A broad, low-contrast wash gives the HUD the depth of the reference
        # glass panels while keeping the animated centrepiece fully visible.
        atmosphere = QLinearGradient(0, 0, W, H)
        atmosphere.setColorAt(0.0, qcol(C.PRI_GHO))
        atmosphere.setColorAt(0.48, qcol(C.BG, 0))
        atmosphere.setColorAt(1.0, qcol("#071d35"))
        p.fillRect(self.rect(), QBrush(atmosphere))
        p.setPen(QPen(qcol(C.BORDER, 110), 1))
        p.drawLine(QLineF(0, 32, W, 32))
        p.drawLine(QLineF(0, H - 34, W, H - 34))

        # grid dots — blitted from a cached layer; rebuilt only when the size
        # or the theme's ghost colour changes (so live re-theming still works).
        _gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != _gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key   = _gkey
        p.drawPixmap(0, 0, self._grid_cache)

        # ── holographic head ────────────────────────────────────────────────
        # Sized to the band between the top of the canvas and the status line,
        # capped by width, so it fills the HUD at any window size — including
        # fullscreen — without ever colliding with the status text below.
        _sy_status = cy + fw * 0.40
        if self._avatar is not None and self.hud_style == "face":
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r_head = min(fw * 0.355, _band_h / (self._avatar.SPAN + 0.08))
            _head_cy = _band_t + (_band_h - self._avatar.SPAN * _r_head) / 2.0 + _r_head

            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                elif self.state in ("THINKING", "PROCESSING"):
                    _acc = qcol(C.ACC2)
                elif self.state == "LISTENING":
                    _acc = qcol(C.GREEN)
                else:
                    _acc = qcol(C.PRI)
            self._avatar.paint(p, cx, _head_cy, _r_head, _main, _acc, qcol(C.BG))

        # reactor core — the other centrepiece, and the fallback if the head
        # could not be built. There is no third path: the old face.png branch
        # was unreachable (no such file ships) and the bare orb it fell through
        # to is what this replaces.
        else:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.46, _band_h / 2.0)
            self._paint_core_orb(p, cx, _band_t + _band_h / 2.0, _r, W, _band_h)

        # status text
        sy = _sy_status
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform — reacts to the real audio level (mic while listening,
        # JARVIS's own voice while speaking). Falls back to a gentle idle
        # ripple when there's no sound. _amp_disp is the smoothed 0–1 level.
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7      # center-weighted hump
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()   # end deterministically so the backing store never flushes an active painter

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2, 210)))
        p.setPen(QPen(qcol(C.BORDER_A, 210), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 9, 9)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#8faec4"), "video":   ("🎬", "#728aa2"),
    "audio":   ("🎵", "#8d8bad"), "pdf":     ("📄", "#b97882"),
    "word":    ("📝", "#829db7"), "excel":   ("📊", "#759e8c"),
    "code":    ("💻", "#9eb0bf"), "archive": ("📦", "#9b836f"),
    "pptx":    ("📊", "#a8877e"), "text":    ("📃", "#a4adb5"),
    "data":    ("🔧", "#7ea6bd"), "unknown": ("📎", "#78838d"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        for path in paths:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#102238" if z._drag_over else ("#0a1522" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,C.PRI_GHO),"mac":(C.ACC2,C.PRI_GHO),"linux":(C.GREEN,C.PRI_GHO)}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.DARK}; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(qcol("#00060a"), 2))
        p.setBrush(QBrush(qcol("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str, bool)  # name, user, colour, voice, monochrome
    _OW, _OH = 400, 632

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", monochrome=False, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: {C.DARK}; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── Monochrome mode ─────────────────────────────────────────────────
        lay.addSpacing(4)
        self._mono_check = QCheckBox("MONOCHROME MODE")
        self._mono_check.setChecked(bool(monochrome))
        self._mono_check.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mono_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mono_check.setStyleSheet(f"""
            QCheckBox {{
                color: {C.TEXT_DIM};
                background: transparent;
                spacing: 7px;
            }}
            QCheckBox:hover {{ color: {C.TEXT}; }}
            QCheckBox::indicator {{
                width: 13px; height: 13px;
                border: 1px solid {C.BORDER};
                border-radius: 2px;
                background: transparent;
            }}
            QCheckBox::indicator:checked {{
                background: {C.PRI};
                border: 1px solid {C.PRI};
            }}
        """)
        lay.addWidget(self._mono_check)
        mono_desc = _lbl(
            "Switch the interface between colour and black-and-white.",
            7, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft,
        )
        mono_desc.setWordWrap(True)
        lay.addWidget(mono_desc)

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#b8cce0   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(
            name, user, self._sel_color or DEFAULT_UI_COLOR,
            self._sel_voice, self._mono_check.isChecked(),
        )
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI_GHO}; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: {C.BORDER}; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class SpeakingOrbOverlay(QWidget):
    """Global, mouse-through copy of the HUD's speaking orb.

    The orb is deliberately a separate top-level tool window.  Keeping the
    source HudCanvas lets the overlay use the exact same renderer, palette, and
    live audio state as the main interface without duplicating any visual
    logic.  It has no parent on purpose: a child widget would be clipped to
    Project Zero's window and would disappear behind other applications.
    """

    _SIZE = 104
    _TOP_MARGIN = 10

    def __init__(self, source: "HudCanvas", owner: "MainWindow"):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self._source = source
        self._owner = owner
        self._progress = 0.0
        self._target = 0.0
        self._scale = 0.82
        self._scale_target = 0.82
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(1.0)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)

    def _screen(self):
        """Use the app window's display, with the primary screen as fallback."""
        try:
            screen = self._owner.screen()
            if screen is not None:
                return screen
        except Exception:
            pass
        try:
            return QApplication.primaryScreen()
        except Exception:
            return None

    def reposition(self) -> None:
        screen = self._screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.left() + (area.width() - self.width()) // 2,
            area.top() + self._TOP_MARGIN,
        )

    def set_speaking(self, speaking: bool) -> None:
        """Start or finish the overlay animation from the TTS state."""
        self._target = 1.0 if speaking else 0.0
        self._scale_target = 1.0 if speaking else 0.82
        if speaking:
            self.reposition()
            self.show()
            self.raise_()
            self._timer.start()
        elif not self.isVisible():
            self._progress = 0.0
            self.update()
        elif not self._timer.isActive():
            self._timer.start()

    def _step(self) -> None:
        # Ease the materialisation independently from the audio amplitude so
        # quiet speech still keeps a stable, visible desktop presence.
        self._progress += (self._target - self._progress) * 0.18
        self._scale += (self._scale_target - self._scale) * 0.18
        if self._target == 0.0 and self._progress < 0.005:
            self._progress = 0.0
            self._scale = 0.82
            self.hide()
            self._timer.stop()
            return
        self.update()

    def paintEvent(self, _event) -> None:
        if self._progress <= 0.005:
            return
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        eased = self._progress * self._progress * (3.0 - 2.0 * self._progress)
        pulse = (
            1.0 + 0.025 * math.sin(self._source._core_phase * 1.7)
            if self._source.speaking else 1.0
        )
        scale = self._scale * pulse
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.scale(scale, scale)
        p.setOpacity(eased)
        # This is the same renderer and mini mode used by the in-HUD copy.
        self._source._paint_core_orb(
            p, 0.0, 0.0, 34.0, self.width(), self.height(), mini=True
        )
        p.end()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: {C.DARK}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: {C.DARK}; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont("Courier New", 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: {C.DARK}; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont("Courier New", 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: {C.DARK}; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemindersSettingsOverlay(QWidget):
    """Compact Settings → Reminders panel backed by the native scheduler."""

    _OW, _OH = 720, 620

    def __init__(
        self,
        get_reminders=None,
        save_reminder=None,
        update_reminder=None,
        toggle_reminder=None,
        delete_reminder=None,
        test_escalation=None,
        parent=None,
    ):
        super().__init__(parent)
        self._get_reminders = get_reminders
        self._save_reminder = save_reminder
        self._update_reminder = update_reminder
        self._toggle_reminder = toggle_reminder
        self._delete_reminder = delete_reminder
        self._test_escalation = test_escalation
        self._editing_id = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemindersSettingsOverlay {{
                background: rgba(0, 6, 10, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
            QDateEdit, QTimeEdit, QComboBox {{
                background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 6px;
            }}
            QDateEdit:focus, QTimeEdit:focus, QComboBox:focus {{
                border-color: {C.PRI};
            }}
            QCheckBox {{ color: {C.TEXT_MED}; spacing: 5px; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(7)
        root.addWidget(self._label("⏰  SETTINGS  /  REMINDERS", 12, True, C.PRI))

        form = QFrame()
        form.setStyleSheet(
            f"QFrame {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; "
            "border-radius: 4px; }"
        )
        fl = QVBoxLayout(form)
        fl.setContentsMargins(10, 8, 10, 8)
        fl.setSpacing(5)

        row = QHBoxLayout()
        row.addWidget(self._label("REMINDER", 8, True, C.TEXT_DIM))
        self._message = QLineEdit()
        self._message.setPlaceholderText("What should I remind you about?")
        self._message.setFixedHeight(29)
        row.addWidget(self._message, 1)
        fl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(self._label("DATE", 8, True, C.TEXT_DIM))
        self._date = QDateEdit(QDate.currentDate())
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("yyyy-MM-dd")
        row.addWidget(self._date)
        row.addWidget(self._label("TIME", 8, True, C.TEXT_DIM))
        self._time = QTimeEdit(QTime.currentTime())
        self._time.setDisplayFormat("HH:mm")
        row.addWidget(self._time)
        row.addWidget(self._label("SCHEDULE", 8, True, C.TEXT_DIM))
        self._frequency = QComboBox()
        self._frequency.addItems(["Once", "Daily", "Weekly"])
        self._frequency.currentTextChanged.connect(self._schedule_changed)
        row.addWidget(self._frequency)
        fl.addLayout(row)

        self._days_row = QWidget()
        days_layout = QHBoxLayout(self._days_row)
        days_layout.setContentsMargins(0, 0, 0, 0)
        days_layout.setSpacing(4)
        days_layout.addWidget(self._label("DAYS", 8, True, C.TEXT_DIM))
        self._day_checks = []
        for label, day in (
            ("MON", 0), ("TUE", 1), ("WED", 2), ("THU", 3),
            ("FRI", 4), ("SAT", 5), ("SUN", 6),
        ):
            check = QCheckBox(label)
            check.setProperty("day", day)
            self._day_checks.append(check)
            days_layout.addWidget(check)
        days_layout.addStretch(1)
        fl.addWidget(self._days_row)

        esc_row = QHBoxLayout()
        self._escalation = QCheckBox("ENABLE ESCALATION")
        self._escalation.setToolTip(
            "Uses the existing acknowledgement → WhatsApp → Messenger flow."
        )
        esc_row.addWidget(self._escalation)
        esc_row.addWidget(self._label("WhatsApp: Baba  •  Messenger: شريف كمال",
                                       8, color=C.TEXT_DIM))
        esc_row.addStretch(1)
        fl.addLayout(esc_row)

        buttons = QHBoxLayout()
        self._save_btn = QPushButton("▸  ADD REMINDER")
        self._save_btn.setFixedHeight(29)
        self._save_btn.clicked.connect(self._save)
        buttons.addWidget(self._save_btn)
        clear = QPushButton("CLEAR")
        clear.setFixedHeight(29)
        clear.clicked.connect(self._clear_form)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        fl.addLayout(buttons)
        root.addWidget(form)

        root.addWidget(self._label("SCHEDULED REMINDERS", 9, True, C.PRI_DIM))
        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._list_host = QWidget()
        self._list_host.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(4)
        self._list_scroll.setWidget(self._list_host)
        root.addWidget(self._list_scroll, 1)

        self._test_status = self._label("No escalation test running.", 8, color=C.TEXT_DIM)
        self._test_status.setWordWrap(True)
        root.addWidget(self._test_status)
        bottom = QHBoxLayout()
        test_btn = QPushButton("⚠  TEST ESCALATION")
        test_btn.setFixedHeight(31)
        test_btn.setStyleSheet(
            f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
            f"border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}"
            f"QPushButton:hover {{ border-color: {C.PRI}; }}"
        )
        test_btn.clicked.connect(self._confirm_test)
        bottom.addWidget(test_btn)
        close = QPushButton("CLOSE")
        close.setFixedHeight(31)
        close.clicked.connect(self.hide)
        bottom.addWidget(close)
        root.addLayout(bottom)
        self._schedule_changed(self._frequency.currentText())
        self.refresh()

    def _label(self, text, size=9, bold=False, color=None):
        label = QLabel(str(text))
        label.setFont(QFont("Courier New", size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
        label.setStyleSheet(f"color: {color or C.TEXT}; background: transparent;")
        return label

    def _schedule_changed(self, value):
        self._days_row.setVisible(str(value).lower() == "weekly")

    def _payload(self):
        from datetime import datetime
        frequency = self._frequency.currentText().lower()
        target = datetime(
            self._date.date().year(), self._date.date().month(),
            self._date.date().day(), self._time.time().hour(),
            self._time.time().minute(),
        )
        return {
            "message": self._message.text().strip(),
            "target": target.isoformat(),
            "frequency": frequency,
            "weekdays": [
                int(check.property("day")) for check in self._day_checks
                if check.isChecked()
            ],
            "escalation": {
                "enabled": self._escalation.isChecked(),
                "whatsapp_contact_name": "Baba",
                "messenger_contact_name": "شريف كمال",
                "initial_message": "",
            },
        }

    def _save(self):
        if not self._message.text().strip():
            self._test_status.setText("Enter reminder text first.")
            return
        try:
            payload = self._payload()
            result = (
                self._update_reminder(self._editing_id, payload)
                if self._editing_id and self._update_reminder
                else self._save_reminder(payload) if self._save_reminder else None
            )
            if result is None:
                raise RuntimeError("Reminder service is not available.")
            self._clear_form()
            self.refresh()
            self._test_status.setText("Reminder saved.")
        except Exception as exc:
            self._test_status.setText(f"Could not save reminder: {exc}")

    def _clear_form(self):
        self._editing_id = None
        self._message.clear()
        self._date.setDate(QDate.currentDate())
        self._time.setTime(QTime.currentTime())
        self._frequency.setCurrentText("Once")
        for check in self._day_checks:
            check.setChecked(False)
        self._escalation.setChecked(False)
        self._save_btn.setText("▸  ADD REMINDER")

    def _edit(self, record):
        from datetime import datetime
        self._editing_id = str(record.get("id"))
        self._message.setText(str(record.get("message", "")))
        next_trigger = record.get("next_trigger")
        target = (
            datetime.fromisoformat(str(next_trigger))
            if next_trigger else datetime.now()
        )
        self._date.setDate(QDate(target.year, target.month, target.day))
        self._time.setTime(QTime(target.hour, target.minute))
        self._frequency.setCurrentText(str(record.get("frequency", "once")).title())
        selected = set(record.get("weekdays") or [])
        for check in self._day_checks:
            check.setChecked(int(check.property("day")) in selected)
        self._escalation.setChecked(
            bool((record.get("escalation") or {}).get("enabled"))
        )
        self._save_btn.setText("▸  UPDATE REMINDER")

    def _row(self, record):
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; "
            "border-radius: 3px; }"
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 5, 6, 5)
        layout.setSpacing(7)
        enabled = bool(record.get("enabled"))
        text = str(record.get("message") or "Reminder")
        freq = str(record.get("frequency", "once")).title()
        next_at = record.get("next_trigger")
        try:
            from datetime import datetime
            next_label = datetime.fromisoformat(str(next_at)).strftime("%b %d, %H:%M")
        except Exception:
            next_label = "completed"
        label = self._label(
            f"{text}\n{freq}  •  next {next_label}  •  "
            f"{'ESCALATION ON' if (record.get('escalation') or {}).get('enabled') else 'normal'}",
            8, color=C.TEXT if enabled else C.TEXT_DIM,
        )
        label.setMinimumWidth(250)
        layout.addWidget(label, 1)
        toggle = QPushButton("ON" if enabled else "OFF")
        toggle.setFixedSize(45, 25)
        toggle.setCheckable(True)
        toggle.setChecked(enabled)
        toggle.toggled.connect(
            lambda value, rid=str(record.get("id")): self._toggle(rid, value)
        )
        layout.addWidget(toggle)
        edit = QPushButton("EDIT")
        edit.setFixedSize(48, 25)
        edit.clicked.connect(lambda _=False, item=dict(record): self._edit(item))
        layout.addWidget(edit)
        delete = QPushButton("DELETE")
        delete.setFixedSize(58, 25)
        delete.clicked.connect(
            lambda _=False, rid=str(record.get("id")): self._delete(rid)
        )
        layout.addWidget(delete)
        return row

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        records = self._get_reminders() if self._get_reminders else []
        if not records:
            self._list_layout.addWidget(
                self._label("No reminders configured.", 8, color=C.TEXT_DIM)
            )
        else:
            for record in records:
                self._list_layout.addWidget(self._row(record))
        self._list_layout.addStretch(1)

    def _toggle(self, reminder_id, enabled):
        try:
            if self._toggle_reminder:
                self._toggle_reminder(reminder_id, enabled)
            self.refresh()
        except Exception as exc:
            self._test_status.setText(f"Could not change reminder: {exc}")

    def _delete(self, reminder_id):
        answer = QMessageBox.question(
            self, "Delete reminder", "Delete this reminder and its scheduled job?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if self._delete_reminder:
                self._delete_reminder(reminder_id)
            self.refresh()
        except Exception as exc:
            self._test_status.setText(f"Could not delete reminder: {exc}")

    def _confirm_test(self):
        answer = QMessageBox.question(
            self, "Run Escalation Test?",
            "This immediately opens WhatsApp, messages Baba, and attempts the "
            "Messenger escalation without normal timeout waits.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._test_status.setText("ESCALATION TEST  → starting…")
        if self._test_escalation:
            self._test_escalation({
                "enabled": True,
                "whatsapp_contact_name": "Baba",
                "messenger_contact_name": "شريف كمال",
                "initial_message": "",
            })

    def set_test_progress(self, stage: str, detail: str = ""):
        self._test_status.setText(
            f"ESCALATION TEST  → {stage}"
            + (f"\n{detail}" if detail else "")
        )
        self._test_status.setStyleSheet(
            f"color: {C.PRI if not stage.startswith('FAIL') else '#ff6b6b'}; "
            "background: transparent;"
        )


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: {C.WHITE}; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: {C.PRI_GHO};
            border: 2px solid {C.BORDER_A};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            f"color: {C.GREEN}; background: {C.PRI_GHO}; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class _ActivityOverlay(QFrame):
    """Compact activity panel with a dedicated, draggable header surface."""

    position_changed = pyqtSignal(QPoint)


class _ActivityHeader(QWidget):
    """Header-only drag handle so the activity content remains interactive."""

    def __init__(self, overlay: _ActivityOverlay):
        super().__init__(overlay)
        self._overlay = overlay
        self._drag_origin: QPoint | None = None
        self._start_pos: QPoint | None = None
        self.setFixedHeight(20)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to move activity panel")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._start_pos = self._overlay.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and self._start_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_origin
            parent = self._overlay.parentWidget()
            if parent is not None:
                x = self._start_pos.x() + delta.x()
                y = self._start_pos.y() + delta.y()
                max_x = max(10, parent.width() - self._overlay.width() - 10)
                max_y = max(66, parent.height() - self._overlay.height() - 12)
                self._overlay.move(
                    max(10, min(max_x, x)),
                    max(66, min(max_y, y)),
                )
                self._overlay.position_changed.emit(self._overlay.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None
            self._start_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _reminder_progress_sig = pyqtSignal(str, str)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()
        self._ui_color = (_cfg.get("ui_color") or DEFAULT_UI_COLOR).strip().lower()
        self._monochrome_mode = bool(_cfg.get("monochrome_mode", False))
        self._monochrome_style_cache: dict[QWidget, str] = {}

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        apply_ui_theme(self._ui_color, self._monochrome_mode)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.get_reminders = None
        self.on_save_reminder = None
        self.on_update_reminder = None
        self.on_toggle_reminder = None
        self.on_delete_reminder = None
        self.on_test_escalation = None
        self._reminders_overlay = None
        self._reminder_progress_sig.connect(self._on_reminder_progress)
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._attachments: list[str] = []
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        central = QWidget()
        central.setStyleSheet(f"""
            QWidget {{
                background: {C.BG};
                color: {C.TEXT};
                font-family: "Segoe UI", "Noto Sans", sans-serif;
            }}
            QToolTip {{
                color: {C.WHITE};
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                padding: 5px 8px;
            }}
            QSplitter::handle {{
                background: {C.BORDER};
            }}
        """)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        # The legacy monitor remains built and updated for compatibility, but
        # no longer consumes permanent screen space. Its values are rendered
        # in the orb and in the optional diagnostic overlay.
        self._left_panel.setParent(central)
        self._left_panel.hide()

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # This is a real desktop overlay, not a child of the HUD.  It remains
        # visible when this window is behind another application.
        self._speaking_orb_overlay = SpeakingOrbOverlay(self.hud, self)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet(f"""
            background: {C.BG};
            border: 1px solid {C.BORDER_A};
        """)
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER_A};
                height: 6px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)

        self._right_panel = self._build_right_panel()
        # Keep the activity log and file controls alive as functional sources;
        # their presentation is now contextual rather than a fixed side rail.
        self._right_panel.setParent(central)
        self._right_panel.hide()
        center_col = QWidget()
        center_col.setStyleSheet("background: transparent;")
        center_lay = QVBoxLayout(center_col)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(0)
        center_lay.addWidget(self._center_split, stretch=1)
        center_lay.addWidget(self._build_command_dock(), stretch=0,
                             alignment=Qt.AlignmentFlag.AlignHCenter)
        body.addWidget(center_col, stretch=1)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)
        self._build_context_overlays()
        self._log._sig.connect(self._on_activity_event)
        if self._monochrome_mode:
            self._apply_monochrome_stylesheets()

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()
        self._position_context_overlays()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "_speaking_orb_overlay"):
            self._speaking_orb_overlay.reposition()

    def closeEvent(self, event):
        if hasattr(self, "_speaking_orb_overlay"):
            self._speaking_orb_overlay.close()
        super().closeEvent(event)

    def _build_context_overlays(self):
        """Build compact, on-demand views for information formerly in rails."""
        cw = self.centralWidget()
        panel_style = f"""
            QFrame#ContextPanel {{
                background: rgba(2, 9, 16, 244);
                border: 1px solid {C.BORDER_A};
                border-radius: 9px;
            }}
            QLabel {{ background: transparent; }}
            QTextEdit {{
                background: rgba(1, 6, 12, 224);
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 5px;
            }}
        """

        self._activity_overlays: list[_ActivityOverlay] = []
        self._activity_active_overlay: _ActivityOverlay | None = None
        self._activity_last_position: QPoint | None = None
        self._activity_sequence = 0
        self._activity_panel_style = panel_style

        self._diagnostic_overlay = QFrame(cw)
        self._diagnostic_overlay.setObjectName("ContextPanel")
        self._diagnostic_overlay.setStyleSheet(panel_style)
        self._diagnostic_overlay.setFixedSize(238, 158)
        diag_lay = QVBoxLayout(self._diagnostic_overlay)
        diag_lay.setContentsMargins(10, 8, 10, 9)
        diag_lay.setSpacing(4)
        diag_hdr = QHBoxLayout()
        diag_title = QLabel("◈  SYSTEM DIAGNOSTICS")
        diag_title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        diag_title.setStyleSheet(f"color: {C.PRI};")
        diag_hdr.addWidget(diag_title)
        diag_hdr.addStretch()
        diag_close = QPushButton("×")
        diag_close.setFixedSize(18, 18)
        diag_close.setFont(QFont("Courier New", 10))
        diag_close.setCursor(Qt.CursorShape.PointingHandCursor)
        diag_close.setStyleSheet(
            f"QPushButton {{ color: {C.TEXT_DIM}; background: transparent; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; }}"
            f"QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI}; }}"
        )
        diag_close.clicked.connect(lambda: self._toggle_diagnostics(False))
        diag_hdr.addWidget(diag_close)
        diag_lay.addLayout(diag_hdr)
        self._diagnostic_rows: dict[str, QLabel] = {}
        for key in ("CPU", "MEM", "GPU", "NET", "TMP", "UPTIME"):
            row = QLabel(f"{key:<6} --")
            row.setFont(QFont("Courier New", 7))
            row.setStyleSheet(f"color: {C.TEXT_MED};")
            self._diagnostic_rows[key] = row
            diag_lay.addWidget(row)
        self._diagnostic_overlay.hide()

    def _create_activity_overlay(self, initial_line: str) -> _ActivityOverlay:
        """Create one independent activity window for one conversation."""
        overlay = _ActivityOverlay(self.centralWidget())
        overlay.setObjectName("ContextPanel")
        overlay.setStyleSheet(self._activity_panel_style)
        overlay.setMinimumSize(348, 142)
        overlay.setMaximumSize(560, 420)
        overlay.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        overlay._activity_lines = [initial_line]
        overlay._activity_user_position = None
        overlay._activity_target_rect = None
        overlay._activity_hiding = False
        overlay._activity_animation = None
        overlay._activity_opacity = QGraphicsOpacityEffect(overlay)
        overlay._activity_opacity.setOpacity(1.0)
        overlay.setGraphicsEffect(overlay._activity_opacity)
        overlay.position_changed.connect(
            lambda position, panel=overlay:
                self._remember_activity_position(panel, position)
        )

        activity_lay = QVBoxLayout(overlay)
        activity_lay.setContentsMargins(10, 8, 10, 9)
        activity_lay.setSpacing(4)
        activity_hdr = _ActivityHeader(overlay)
        activity_hdr_lay = QHBoxLayout(activity_hdr)
        activity_hdr_lay.setContentsMargins(0, 0, 0, 0)
        activity_hdr_lay.setSpacing(4)

        self._activity_sequence += 1
        activity_title = QLabel(
            f"◈  ACTIVITY / {self._activity_sequence:02d}"
        )
        activity_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        activity_title_font = QFont("Courier New", 8, QFont.Weight.Bold)
        activity_title_font.setPointSizeF(7.8)
        activity_title.setFont(activity_title_font)
        activity_title.setStyleSheet(f"color: {C.PRI};")
        activity_hdr_lay.addWidget(activity_title)
        activity_hdr_lay.addStretch()

        activity_state = QLabel("PERSISTENT")
        activity_state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        activity_state_font = QFont("Courier New")
        activity_state_font.setPointSizeF(5.5)
        activity_state.setFont(activity_state_font)
        activity_state.setStyleSheet(f"color: {C.TEXT_DIM};")
        activity_hdr_lay.addWidget(activity_state)

        close_activity = QPushButton("×")
        close_activity.setFixedSize(18, 18)
        close_activity.setFont(QFont("Courier New", 10))
        close_activity.setCursor(Qt.CursorShape.PointingHandCursor)
        close_activity.setStyleSheet(
            f"QPushButton {{ color: {C.TEXT_DIM}; background: transparent; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; }}"
            f"QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI}; }}"
        )
        close_activity.clicked.connect(
            lambda _checked=False, panel=overlay:
                self._close_activity_overlay(panel)
        )
        activity_hdr_lay.addWidget(close_activity)
        activity_lay.addWidget(activity_hdr)

        display = QTextEdit()
        display.setReadOnly(True)
        display_font = QFont("Courier New")
        display_font.setPointSizeF(6.7)
        display.setFont(display_font)
        display.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        display.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )
        display.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        display.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        overlay._activity_display = display
        activity_lay.addWidget(display, stretch=1)

        self._activity_overlays.append(overlay)
        self._render_activity_display(overlay)
        self._resize_activity_overlay(overlay)
        self._place_activity_overlay(overlay)
        overlay.hide()
        return overlay

    def _remember_activity_position(
        self, overlay: _ActivityOverlay, position: QPoint
    ):
        overlay._activity_user_position = self._clamp_activity_position(
            overlay, position
        )
        if overlay._activity_animation is None:
            overlay._activity_target_rect = QRect(overlay.geometry())

    def _clamp_activity_position(
        self, overlay: _ActivityOverlay, position: QPoint
    ) -> QPoint:
        cw = self.centralWidget()
        if cw is None:
            return position
        max_x = max(10, cw.width() - overlay.width() - 10)
        max_y = max(66, cw.height() - overlay.height() - 12)
        return QPoint(
            max(10, min(max_x, position.x())),
            max(66, min(max_y, position.y())),
        )

    def _place_activity_overlay(self, overlay: _ActivityOverlay):
        """Choose a close, safe, non-stacked position around the core."""
        cw = self.centralWidget()
        if cw is None:
            return
        w, h = cw.width(), cw.height()
        core_x, core_y = w // 2, max(220, min(h // 2 - 15, h - 260))
        base_x = core_x - overlay.width() // 2
        base_y = core_y - overlay.height() // 2
        offsets = [
            (-430, -175), (275, -175),
            (-475, -5), (320, -5),
            (-405, 155), (245, 155),
            (-170, -245), (105, 220),
            (-545, -120), (390, -120),
            (-535, 75), (380, 75),
        ]
        random.shuffle(offsets)
        core_rect = QRect(core_x - 155, core_y - 145, 310, 290)
        occupied = [
            other.geometry().adjusted(-14, -14, 14, 14)
            for other in self._activity_overlays
            if other is not overlay and other.isVisible()
        ]
        candidates = []
        for dx, dy in offsets:
            position = self._clamp_activity_position(
                overlay, QPoint(base_x + dx, base_y + dy)
            )
            if self._activity_last_position == position:
                continue
            rect = QRect(position, overlay.size())
            if rect.bottom() > h - 130:
                continue
            existing_overlap = sum(
                rect.intersected(other).width()
                * rect.intersected(other).height()
                for other in occupied
                if rect.intersects(other)
            )
            core_overlap = (
                rect.intersected(core_rect).width()
                * rect.intersected(core_rect).height()
                if rect.intersects(core_rect) else 0
            )
            candidates.append((position, existing_overlap, core_overlap))

        if not candidates:
            position = self._clamp_activity_position(
                overlay, QPoint(base_x, base_y)
            )
            if self._activity_last_position == position:
                position = self._clamp_activity_position(
                    overlay, position + QPoint(24, 24)
                )
        else:
            separated = [candidate for candidate in candidates
                         if candidate[1] == 0]
            pool = separated or candidates
            position = min(
                pool,
                key=lambda candidate: (
                    candidate[1],
                    candidate[2],
                    random.random(),
                ),
            )[0]
        overlay._activity_user_position = position
        self._activity_last_position = QPoint(position)
        overlay.move(position)
        overlay._activity_target_rect = QRect(overlay.geometry())

    def _position_context_overlays(self):
        if not hasattr(self, "_activity_overlays"):
            return
        cw = self.centralWidget()
        w, h = cw.width(), cw.height()
        for overlay in list(self._activity_overlays):
            overlay._activity_user_position = self._clamp_activity_position(
                overlay, overlay._activity_user_position or overlay.pos()
            )
            overlay.move(overlay._activity_user_position)
            if (not overlay.isVisible()
                    and overlay._activity_animation is None):
                overlay._activity_target_rect = QRect(overlay.geometry())

        diag_w = self._diagnostic_overlay.width()
        self._diagnostic_overlay.move(
            max(10, min(w - diag_w - 10, 22)),
            max(66, min(h - self._diagnostic_overlay.height() - 12, 78)),
        )

    def _resize_activity_overlay(self, overlay: _ActivityOverlay):
        """Fit one activity panel to its content without letting it run away."""
        cw = self.centralWidget()
        if cw is None:
            return

        min_w, min_h = 348, 142
        max_w = min(560, max(min_w, cw.width() - 20))
        max_h = min(420, max(min_h, cw.height() - 78))
        display = overlay._activity_display
        metrics = display.fontMetrics()
        longest_line = max(
            (
                metrics.horizontalAdvance(part)
                for entry in overlay._activity_lines
                for part in entry.splitlines()
            ),
            default=0,
        )
        target_w = max(min_w, min(max_w, longest_line + 48))

        overlay.setMinimumSize(0, 0)
        overlay.setMaximumSize(16777215, 16777215)
        overlay.resize(target_w, min_h)
        document = display.document()
        document.setTextWidth(max(1, target_w - 32))
        document_height = document.documentLayout().documentSize().height()
        target_h = max(
            min_h,
            min(max_h, math.ceil(document_height + 56)),
        )
        overlay.resize(target_w, target_h)
        overlay.setMinimumSize(min_w, min_h)
        overlay.setMaximumSize(max_w, max_h)
        if overlay._activity_user_position is not None:
            overlay._activity_user_position = self._clamp_activity_position(
                overlay, overlay._activity_user_position
            )
            overlay.move(overlay._activity_user_position)

    def _render_activity_display(self, overlay: _ActivityOverlay):
        """Render one activity conversation with compact hierarchy."""
        rows = []
        for entry in overlay._activity_lines:
            if entry.startswith("YOU     "):
                label, color = "YOU", C.ACC
                body = entry[8:]
            elif entry.startswith("AI      "):
                label, color = "AI", C.PRI
                body = entry[8:]
            else:
                label, color = "SYSTEM", C.TEXT_MED
                body = entry[8:] if entry.startswith("SYSTEM  ") else entry
            body_html = html.escape(body).replace("\n", "<br/>")
            rows.append(
                f'<div style="line-height:115%; margin:0 0 3px 0;">'
                f'<span style="color:{color}; font-weight:600;">'
                f'{label:<6}</span>{body_html}</div>'
            )
        overlay._activity_display.setHtml("".join(rows))
        overlay._activity_display.moveCursor(
            overlay._activity_display.textCursor().MoveOperation.End
        )

    def _stop_activity_animation(self, overlay: _ActivityOverlay):
        animation = overlay._activity_animation
        if animation is not None:
            animation.stop()
            overlay._activity_animation = None

    def _animate_activity_in(self, overlay: _ActivityOverlay):
        self._stop_activity_animation(overlay)
        if overlay._activity_target_rect is not None:
            overlay.setFixedSize(overlay._activity_target_rect.size())
            overlay.setGeometry(overlay._activity_target_rect)
        else:
            self._position_context_overlays()
        final_rect = QRect(overlay.geometry())
        start_w = max(1, int(final_rect.width() * 0.965))
        start_h = max(1, int(final_rect.height() * 0.965))
        start_rect = QRect(
            final_rect.x() + 8,
            final_rect.y() + 6,
            start_w,
            start_h,
        )
        overlay._activity_target_rect = QRect(final_rect)
        overlay.setMinimumSize(0, 0)
        overlay.setMaximumSize(16777215, 16777215)
        overlay.setGeometry(start_rect)
        overlay._activity_opacity.setOpacity(0.0)
        overlay.show()
        overlay.raise_()

        animation = QParallelAnimationGroup(self)
        geometry = QPropertyAnimation(overlay, b"geometry", animation)
        geometry.setDuration(230)
        geometry.setStartValue(start_rect)
        geometry.setEndValue(final_rect)
        geometry.setEasingCurve(QEasingCurve.Type.OutCubic)
        opacity = QPropertyAnimation(
            overlay._activity_opacity, b"opacity", animation
        )
        opacity.setDuration(190)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.addAnimation(geometry)
        animation.addAnimation(opacity)
        overlay._activity_animation = animation

        def _finished():
            if overlay._activity_animation is not animation:
                return
            overlay.setGeometry(final_rect)
            self._resize_activity_overlay(overlay)
            overlay._activity_opacity.setOpacity(1.0)
            overlay._activity_animation = None

        animation.finished.connect(_finished)
        animation.start()

    def _animate_activity_out(self, overlay: _ActivityOverlay):
        self._stop_activity_animation(overlay)
        final_rect = QRect(overlay.geometry())
        end_w = max(1, int(final_rect.width() * 0.965))
        end_h = max(1, int(final_rect.height() * 0.965))
        end_rect = QRect(
            final_rect.x() + 8,
            final_rect.y() + 6,
            end_w,
            end_h,
        )
        overlay.setMinimumSize(0, 0)
        overlay.setMaximumSize(16777215, 16777215)

        animation = QParallelAnimationGroup(self)
        geometry = QPropertyAnimation(overlay, b"geometry", animation)
        geometry.setDuration(150)
        geometry.setStartValue(final_rect)
        geometry.setEndValue(end_rect)
        geometry.setEasingCurve(QEasingCurve.Type.InCubic)
        opacity = QPropertyAnimation(
            overlay._activity_opacity, b"opacity", animation
        )
        opacity.setDuration(125)
        opacity.setStartValue(overlay._activity_opacity.opacity())
        opacity.setEndValue(0.0)
        opacity.setEasingCurve(QEasingCurve.Type.InCubic)
        animation.addAnimation(geometry)
        animation.addAnimation(opacity)
        overlay._activity_animation = animation

        def _finished():
            if overlay._activity_animation is not animation:
                return
            overlay.hide()
            overlay.setGeometry(final_rect)
            self._resize_activity_overlay(overlay)
            overlay._activity_opacity.setOpacity(1.0)
            overlay._activity_animation = None
            overlay._activity_hiding = False
            if overlay in self._activity_overlays:
                self._activity_overlays.remove(overlay)
            if self._activity_active_overlay is overlay:
                self._activity_active_overlay = None
            overlay.deleteLater()

        animation.finished.connect(_finished)
        animation.start()

    def _close_activity_overlay(self, overlay: _ActivityOverlay):
        if overlay not in self._activity_overlays:
            return
        if not overlay.isVisible():
            self._stop_activity_animation(overlay)
            self._activity_overlays.remove(overlay)
            if self._activity_active_overlay is overlay:
                self._activity_active_overlay = None
            overlay.deleteLater()
            return
        overlay._activity_hiding = True
        self._animate_activity_out(overlay)

    def _on_activity_event(self, text: str):
        """Route each conversation into its own persistent activity window."""
        if not text or not hasattr(self, "_activity_overlays"):
            return
        raw = str(text).strip()
        if raw.startswith("You:"):
            line = "YOU     " + raw[4:].strip()
            starts_conversation = True
        elif raw.startswith("ERR:"):
            line = "SYSTEM  ! " + raw[4:].strip()
            starts_conversation = False
        elif raw.startswith("AI:"):
            line = "AI      " + raw[3:].strip()
            starts_conversation = False
        else:
            line = "SYSTEM  " + raw
            starts_conversation = False

        active = self._activity_active_overlay
        if (starts_conversation or active is None
                or active not in self._activity_overlays
                or active._activity_hiding):
            active = self._create_activity_overlay(line)
            self._activity_active_overlay = active
        else:
            active._activity_lines.append(line)
            self._render_activity_display(active)
            self._resize_activity_overlay(active)
            self._position_context_overlays()

        if not active.isVisible() or active._activity_hiding:
            active._activity_hiding = False
            self._animate_activity_in(active)
        else:
            active.show()
            active.raise_()

    def _toggle_diagnostics(self, show: bool | None = None):
        if not hasattr(self, "_diagnostic_overlay"):
            return
        show = (not self._diagnostic_overlay.isVisible()
                if show is None else bool(show))
        if hasattr(self, "_monitor_btn"):
            self._monitor_btn.setChecked(show)
        if show:
            self._position_context_overlays()
            self._diagnostic_overlay.show()
            self._diagnostic_overlay.raise_()
        else:
            self._diagnostic_overlay.hide()

    def _update_metrics(self):
        snap = _metrics.snapshot()
        self.hud.set_telemetry(snap)

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")

        if hasattr(self, "_diagnostic_rows"):
            values = {
                "CPU": f"{snap['cpu']:.0f}%",
                "MEM": f"{snap['mem']:.0f}%",
                "GPU": f"{snap['gpu']:.0f}%" if snap["gpu"] >= 0 else "N/A",
                "NET": (f"{snap['net'] * 1024:.0f}KB/s"
                        if snap["net"] < 1.0 else f"{snap['net']:.1f}MB/s"),
                "TMP": f"{snap['tmp']:.0f}°C" if snap["tmp"] >= 0 else "N/A",
                "UPTIME": self._uptime_lbl.text().replace("UP  ", ""),
            }
            for key, value in values.items():
                self._diagnostic_rows[key].setText(f"{key:<6} {value}")


    def _build_header(self) -> QWidget:
        w = QWidget()
        self._header = w
        w.setFixedHeight(58)
        w.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                        stop:0 {C.DARK}, stop:0.5 {C.PRI_GHO},
                                        stop:1 {C.DARK});
            border-bottom: 1px solid {C.BORDER_B};
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 0, 18, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge("◈  Project: Zero", C.PRI_DIM))
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(QFont("Courier New", 11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                background: rgba(10, 37, 59, 180);
                border: 1px solid {C.BORDER_A}; border-radius: 7px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)
        self._monitor_btn = QPushButton("SYS")
        self._monitor_btn.setCheckable(True)
        self._monitor_btn.setFixedSize(42, 26)
        self._monitor_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._monitor_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._monitor_btn.setToolTip("Open system diagnostics")
        self._monitor_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: rgba(10, 37, 59, 150);
                border: 1px solid {C.BORDER_A}; border-radius: 7px;
            }}
            QPushButton:hover, QPushButton:checked {{
                color: {C.PRI}; border-color: {C.PRI};
                background: {C.PRI_GHO};
            }}
        """)
        self._monitor_btn.clicked.connect(self._toggle_diagnostics)
        lay.addWidget(self._monitor_btn)
        lay.addStretch()

        self._sub_lbl = QLabel("")
        self._sub_lbl.setParent(w)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont("Courier New", 7))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._sub_lbl.hide()
        return w

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"""
            background: rgba(6, 21, 38, 235);
            border-right: 1px solid {C.BORDER_A};
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 14, 12, 14)
        lay.setSpacing(8)

        hdr = QLabel("◈ SYS MONITOR")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER_A}; padding-bottom: 7px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: rgba(16, 44, 69, 190); border: 1px solid {C.BORDER_A}; border-radius: 8px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",  C.GREEN),
            ("SEC\nCLEARED",     C.PRI),
            ("PROTOCOL\n" + APP_PROTOCOL,   C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 7px; padding: 7px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"""
            background: rgba(6, 21, 38, 225);
            border-left: 1px solid {C.BORDER_A};
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                            f"letter-spacing: 1px;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        # The command controls live in the centred glass dock under the HUD,
        # matching the reference composition. They remain the same widgets
        # and callbacks; only their visual placement changes.
        self._command_row = self._build_input_row()

        self._interrupt_btn = QPushButton("INTERRUPT  [ESC]")
        self._interrupt_btn.setFixedHeight(32)
        self._interrupt_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(15, 35, 54, 90); color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 16px;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                background: rgba(74, 128, 155, 70);
                color: {C.WHITE}; border: 1px solid {C.BORDER_B};
            }}
            QPushButton:pressed {{
                background: rgba(74, 128, 155, 110);
            }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)

        self._mute_btn = QPushButton("MIC ACTIVE")
        self._mute_btn.setFixedHeight(32)
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()

        lay.addStretch(1)
        return w

    def _build_command_dock(self) -> QWidget:
        """Slim, unified glass command rail below the animated HUD."""
        dock = QWidget()
        dock.setObjectName("CommandDock")
        dock.setFixedHeight(54)
        dock.setMaximumWidth(748)
        dock.setStyleSheet(f"""
            QWidget#CommandDock {{
                background: rgba(9, 29, 47, 238);
                border: 1px solid {C.BORDER_A};
                border-radius: 27px;
            }}
        """)
        lay = QHBoxLayout(dock)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        self._interrupt_btn.setFixedWidth(120)
        self._interrupt_btn.setFixedHeight(32)
        lay.addWidget(self._interrupt_btn)

        lay.addLayout(self._command_row, stretch=1)

        self._mute_btn.setFixedWidth(138)
        self._mute_btn.setFixedHeight(32)
        lay.addWidget(self._mute_btn)
        return dock

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: {C.DARK}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        file_btn = QPushButton("▣  OPEN FILE")
        file_btn.setFixedHeight(26)
        file_btn.setFont(QFont("Courier New", 7))
        file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        file_btn.setStyleSheet(_BTN_STYLE_DIM)
        file_btn.clicked.connect(self._open_file_dialog)
        lay.addWidget(file_btn)

        fs_btn = QPushButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont("Courier New", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        self._brief_btn = QPushButton()
        self._brief_btn.setFixedHeight(26)
        self._brief_btn.setFont(QFont("Courier New", 7))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        # ── Wake word ──────────────────────────────────────────────────────────
        self._wake_btn = QPushButton()
        self._wake_btn.setFixedHeight(26)
        self._wake_btn.setFont(QFont("Courier New", 7))
        self._wake_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        lay.addWidget(self._wake_btn)

        self._wake_sleep_btn = QPushButton()
        self._wake_sleep_btn.setFixedHeight(26)
        self._wake_sleep_btn.setFont(QFont("Courier New", 7))
        self._wake_sleep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        lay.addWidget(self._wake_sleep_btn)
        # Neutral placeholder now; the real state (which may load the model to
        # check readiness) is resolved lazily the first time the drawer opens.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = QPushButton()
        self._ptt_btn.setFixedHeight(26)
        self._ptt_btn.setFont(QFont("Courier New", 7))
        self._ptt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        lay.addWidget(self._ptt_btn)

        self._refresh_talk_btns()

        self._hud_btn = QPushButton()
        self._hud_btn.setFixedHeight(26)
        self._hud_btn.setFont(QFont("Courier New", 7))
        self._hud_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        lay.addWidget(self._hud_btn)
        self._refresh_hud_btn()

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        reminders_btn = QPushButton("⏰  REMINDERS")
        reminders_btn.setFixedHeight(26)
        reminders_btn.setFont(QFont("Courier New", 7))
        reminders_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reminders_btn.setStyleSheet(_BTN_STYLE_DIM)
        reminders_btn.clicked.connect(self._open_reminders)
        lay.addWidget(reminders_btn)

        w.adjustSize()
        return w

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._refresh_wake_btns()   # resolve wake state on open (lazy)
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _position_quick_drawer(self):
        if not hasattr(self, '_quick_drawer'):
            return
        _W = 220
        self._quick_drawer.setFixedWidth(_W)
        self._quick_drawer.adjustSize()
        self._quick_drawer.setGeometry(12, 54, _W, self._quick_drawer.sizeHint().height())

    def _open_file_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Attach files to {self._assistant_name}",
            str(Path.home()),
            "All files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.doc *.docx *.txt *.md *.ppt *.pptx);;"
            "Data (*.csv *.xls *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)"
        )
        for path in paths:
            self._on_file_selected(path)
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)
            self._quick_drawer.hide()

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(0)
        shell = QWidget()
        shell.setObjectName("CommandInputShell")
        shell.setFixedHeight(34)
        shell.setStyleSheet(f"""
            QWidget#CommandInputShell {{
                background: rgba(15, 44, 66, 235);
                border: 1px solid {C.BORDER_A};
                border-radius: 17px;
            }}
        """)
        shell_lay = QHBoxLayout(shell)
        shell_lay.setContentsMargins(10, 2, 4, 2)
        shell_lay.setSpacing(2)

        attach = QPushButton("📎")
        attach.setFixedSize(28, 28)
        attach.setFont(QFont("Segoe UI Emoji", 11) if _OS == "Windows" else QFont("Arial", 11))
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setToolTip("Attach one or more files")
        attach.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI_DIM};
                border: none; border-radius: 14px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO};
                color: {C.PRI};
            }}
            QPushButton:pressed {{ background: {C.BORDER}; }}
        """)
        attach.clicked.connect(self._choose_attachments)
        self._attachment_btn = attach
        shell_lay.addWidget(attach)

        self._attachment_summary = QPushButton()
        self._attachment_summary.setFixedHeight(26)
        self._attachment_summary.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attachment_summary.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._attachment_summary.setToolTip("Attached files")
        self._attachment_summary.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 12px;
                padding: 0 7px;
            }}
            QPushButton:hover {{
                background: {C.BORDER}; border-color: {C.BORDER_B};
            }}
        """)
        self._attachment_summary.clicked.connect(self._show_attachment_menu)
        self._attachment_summary.hide()
        shell_lay.addWidget(self._attachment_summary)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command...")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(28)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: transparent; color: {C.WHITE};
                border: none; padding: 3px 4px;
            }}
            QLineEdit:focus {{ border: none; }}
        """)
        self._input.returnPressed.connect(self._send)
        shell_lay.addWidget(self._input, stretch=1)

        send = QPushButton("›")
        send.setFixedSize(28, 28)
        send.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: none; border-radius: 14px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO};
                color: {C.WHITE};
            }}
        """)
        send.clicked.connect(self._send)
        shell_lay.addWidget(send)
        row.addWidget(shell)
        return row

    def _choose_attachments(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Attach files to {self._assistant_name}",
            str(Path.home()),
            "All files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.doc *.docx *.txt *.md *.ppt *.pptx);;"
            "Data (*.csv *.xls *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)"
        )
        for path in paths:
            self._on_file_selected(path)

    def _refresh_attachment_controls(self):
        files = [Path(path) for path in self._attachments]
        if not files:
            self._attachment_summary.hide()
            self._attachment_summary.setToolTip("Attached files")
            return
        if len(files) == 1:
            label = f"📎 {files[0].name}"
        else:
            label = f"📎 {len(files)} files"
        # Keep the command rail compact while exposing the actual names in the
        # tooltip and in the remove menu.
        self._attachment_summary.setText(label[:24] + ("…" if len(label) > 24 else ""))
        self._attachment_summary.setToolTip(
            "\n".join(f"{i}. {path.name}" for i, path in enumerate(files, 1))
        )
        self._attachment_summary.show()

    def _show_attachment_menu(self):
        if not self._attachments:
            return
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER_B};
                padding: 4px;
            }}
            QMenu::item {{ padding: 5px 10px; }}
            QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.WHITE}; }}
        """)
        for index, path in enumerate(self._attachments):
            action = menu.addAction(f"Remove  {Path(path).name}")
            action.triggered.connect(
                lambda _checked=False, i=index: self._remove_attachment(i)
            )
        menu.addSeparator()
        clear = menu.addAction("Remove all attachments")
        clear.triggered.connect(self._clear_attachments)
        menu.exec(self._attachment_summary.mapToGlobal(
            self._attachment_summary.rect().bottomLeft()
        ))

    def _remove_attachment(self, index: int):
        if 0 <= index < len(self._attachments):
            removed = Path(self._attachments.pop(index))
            self._refresh_attachment_controls()
            self._log.append_log(f"FILE: {removed.name} removed from message")
            if self._attachments:
                self._current_file = self._attachments[0]
            else:
                self._current_file = None

    def _clear_attachments(self):
        if self._attachments:
            self._log.append_log(
                f"FILE: removed {len(self._attachments)} attachment(s) from message"
            )
        self._attachments.clear()
        self._current_file = None
        self._refresh_attachment_controls()

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: rgba(10, 32, 53, 235);
                border: 1px solid {C.BORDER_A};
                border-radius: 10px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Courier New", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: rgba(16, 44, 69, 210); color: {col};
                border: 1px solid {edge}; border-radius: 7px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: rgba(10, 32, 53, 235);
                border: 1px solid {C.BORDER_A};
                border-radius: 10px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Courier New", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Courier New", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Courier New", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"""
            background: {C.DARK};
            border-top: 1px solid {C.BORDER_A};
        """)
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addStretch()
        return w

    def _on_file_selected(self, path: str):
        if path not in self._attachments:
            self._attachments.append(path)
        self._current_file = self._attachments[0]
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._refresh_attachment_controls()
        if hasattr(self, "_file_hint"):
            self._file_hint.setText(
                f"{icon}  {p.name}  ·  {size}  ·  Attached to the next command"
            )
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI_GHO}; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: {C.BORDER}; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: {C.PRI_GHO}; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: {C.BORDER}; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: {C.PRI_GHO}; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: {C.BORDER}; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        face = get_hud_style() == "face"
        # Neither state is "off", so both read as active — this is a choice
        # between two things, not a switch with a disabled side.
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._hud_btn.setText("🧑  HUD: ANIMATED FACE" if face
                              else "◉  HUD: REACTOR CORE")
        self._hud_btn.setStyleSheet(style)
        self._hud_btn.setToolTip(
            "An animated head that speaks your words and shows what JARVIS is "
            "doing. Tap to switch to the reactor core."
            if face else
            "A reactor core that turns with the state and moves with your voice. "
            "Tap to switch to the animated head.")

    def _toggle_hud_style(self):
        """Swap the centrepiece. Both objects stay in memory, so the change is
        instant and switching back costs nothing."""
        from memory.config_manager import get_hud_style, save_hud_style
        want = "core" if get_hud_style() == "face" else "face"
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(
            "SYS: HUD switched to the animated face." if want == "face"
            else "SYS: HUD switched to the reactor core.")

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI_GHO}; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: {C.BORDER}; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            bool(cfg.get("monochrome_mode", False)),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        hex_color = (hex_color or DEFAULT_UI_COLOR).strip().lower()
        if self._monochrome_mode and self._ui_color != hex_color:
            old_underlying = self._ui_color
            old = current_palette()
            apply_ui_theme(old_underlying, False)
            old_color_palette = current_palette()
            apply_ui_theme(hex_color, False)
            new_color_palette = current_palette()
            mapping = {
                old_color_palette[k].lower(): new_color_palette[k].lower()
                for k in old_color_palette
                if old_color_palette[k].lower() != new_color_palette[k].lower()
            }
            for widget, stylesheet in list(self._monochrome_style_cache.items()):
                updated = stylesheet
                for old_hex, new_hex in mapping.items():
                    updated = updated.replace(old_hex, new_hex)
                self._monochrome_style_cache[widget] = updated
            apply_ui_theme(hex_color, True)
            self._ui_color = hex_color
            return
        old = current_palette()
        if apply_ui_theme(hex_color, self._monochrome_mode):
            retheme_all_widgets(old, current_palette())
            self._ui_color = hex_color

    def _set_monochrome_mode(self, enabled: bool):
        """Switch the palette and existing stylesheets without changing layout."""
        enabled = bool(enabled)
        if enabled == self._monochrome_mode:
            return

        if enabled:
            apply_ui_theme(self._ui_color, True)
            self._apply_monochrome_stylesheets()
        else:
            apply_ui_theme(self._ui_color, False)
            for widget, stylesheet in self._monochrome_style_cache.items():
                try:
                    widget.setStyleSheet(stylesheet)
                except RuntimeError:
                    pass
            self._monochrome_style_cache.clear()
        self._monochrome_mode = enabled
        for widget in QApplication.instance().allWidgets():
            widget.update()

    def _apply_monochrome_stylesheets(self):
        """Cache and desaturate existing widget stylesheets."""
        self._monochrome_style_cache = {}
        for widget in QApplication.instance().allWidgets():
            stylesheet = widget.styleSheet()
            if not stylesheet:
                continue
            self._monochrome_style_cache[widget] = stylesheet
            try:
                widget.setStyleSheet(_monochrome_stylesheet(stylesheet))
            except RuntimeError:
                pass

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = "", monochrome: bool = False):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._sub_lbl.setText("")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_theme(ui_color, self._monochrome_mode):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI
                self._ui_color = ui_color.strip().lower()

        self._set_monochrome_mode(monochrome)

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            data["monochrome_mode"] = self._monochrome_mode
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    def _open_reminders(self):
        cw = self.centralWidget()
        ov = RemindersSettingsOverlay(
            get_reminders=self.get_reminders,
            save_reminder=self.on_save_reminder,
            update_reminder=self.on_update_reminder,
            toggle_reminder=self.on_toggle_reminder,
            delete_reminder=self.on_delete_reminder,
            test_escalation=self.on_test_escalation,
            parent=cw,
        )
        ow = RemindersSettingsOverlay._OW
        oh = min(RemindersSettingsOverlay._OH, cw.height() - 16)
        ov.setGeometry(
            max(8, (cw.width() - ow) // 2),
            max(8, (cw.height() - oh) // 2),
            min(ow, cw.width() - 16),
            oh,
        )
        ov.show()
        ov.raise_()
        self._reminders_overlay = ov

    def _on_reminder_progress(self, stage: str, detail: str):
        if self._reminders_overlay and self._reminders_overlay.isVisible():
            self._reminders_overlay.set_test_progress(stage, detail)

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("MIC MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(87, 33, 62, 85); color: {C.MUTED_C};
                    border: 1px solid rgba(255, 111, 156, 150);
                    border-radius: 16px; padding: 0 10px;
                }}
                QPushButton:hover {{ background: rgba(87, 33, 62, 130); }}
            """)
        else:
            self._mute_btn.setText("MIC ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI_GHO}; color: {C.GREEN};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 16px; padding: 0 10px;
                }}
                QPushButton:hover {{ background: {C.BORDER}; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt and not self._attachments:
            return
        if not txt:
            txt = "Please review the attached file(s) and tell me what they contain."
        self._input.clear()
        if self._attachments:
            names = ", ".join(Path(path).name for path in self._attachments)
            self._log.append_log(f"You: {txt}  [attached: {names}]")
            attachment_block = json.dumps(
                [
                    {
                        "path": path,
                        "name": Path(path).name,
                        "type": Path(path).suffix.lstrip("."),
                    }
                    for path in self._attachments
                ],
                ensure_ascii=False,
            )
            txt = (
                "[ATTACHED_FILES]\n"
                f"{attachment_block}\n"
                "[/ATTACHED_FILES]\n\n"
                f"{txt}\n\n"
                "Use the file_processor tool for each attached file when answering "
                "about its contents. Do not just acknowledge the file."
            )
        else:
            self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        self._speaking_orb_overlay.set_speaking(state == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "JARVIS") or "JARVIS"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._attachments[0] if self._win._attachments else None

    @property
    def current_files(self) -> list[str]:
        return list(self._win._attachments)

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def get_reminders(self):
        return self._win.get_reminders

    @get_reminders.setter
    def get_reminders(self, cb):
        self._win.get_reminders = cb

    @property
    def on_save_reminder(self):
        return self._win.on_save_reminder

    @on_save_reminder.setter
    def on_save_reminder(self, cb):
        self._win.on_save_reminder = cb

    @property
    def on_update_reminder(self):
        return self._win.on_update_reminder

    @on_update_reminder.setter
    def on_update_reminder(self, cb):
        self._win.on_update_reminder = cb

    @property
    def on_toggle_reminder(self):
        return self._win.on_toggle_reminder

    @on_toggle_reminder.setter
    def on_toggle_reminder(self, cb):
        self._win.on_toggle_reminder = cb

    @property
    def on_delete_reminder(self):
        return self._win.on_delete_reminder

    @on_delete_reminder.setter
    def on_delete_reminder(self, cb):
        self._win.on_delete_reminder = cb

    @property
    def on_test_escalation(self):
        return self._win.on_test_escalation

    @on_test_escalation.setter
    def on_test_escalation(self, cb):
        self._win.on_test_escalation = cb

    def set_reminder_test_progress(self, stage: str, detail: str = ""):
        self._win._reminder_progress_sig.emit(str(stage), str(detail))

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")