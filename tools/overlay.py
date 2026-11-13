"""QML Overlay Controller for Aster.

Manages the top Dynamic Island HUD, Ghost Cursor motion trails,
sonar pulse ripples, and laser target highlights via PySide6 & QML.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("Aster.Overlay")

# Thread-safe in-memory state dictionary
_state_lock = threading.Lock()
_overlay_state: dict[str, Any] = {
    "island_state": "idle",
    "island_text": "",
    "island_subtext": "",
    "assist_mode": False,
    "ghost_pos": None,            # tuple (x, y) or None
    "ghost_label": "",
    "active_highlights": [],      # list of normalized dicts [{"x":.., "y":.., "width":.., "height":.., "label":..}]
    "last_pulse": None,           # tuple (x, y, label) or None
    "is_running": False,
}

_daemon_thread: Optional[threading.Thread] = None
_ready_event = threading.Event()
_bridge = None
_view = None
_app = None


def get_overlay_state() -> dict[str, Any]:
    """Return a thread-safe snapshot of current overlay state."""
    with _state_lock:
        return {
            "island_state": _overlay_state["island_state"],
            "island_text": _overlay_state["island_text"],
            "island_subtext": _overlay_state["island_subtext"],
            "assist_mode": _overlay_state["assist_mode"],
            "ghost_pos": _overlay_state["ghost_pos"],
            "ghost_label": _overlay_state["ghost_label"],
            "active_highlights": list(_overlay_state["active_highlights"]),
            "last_pulse": _overlay_state["last_pulse"],
            "is_running": _overlay_state["is_running"],
        }


def is_overlay_running() -> bool:
    """Check if the overlay Qt event loop is active."""
    with _state_lock:
        return bool(_overlay_state["is_running"] and _daemon_thread and _daemon_thread.is_alive())


def _ensure_daemon_running() -> None:
    """Auto-start overlay daemon if enabled and not already running (skipped under pytest)."""
    if not is_overlay_running() and "PYTEST_CURRENT_TEST" not in os.environ:
        try:
            import config
            if getattr(config, "OVERLAY_ENABLED", True):
                start_overlay_daemon()
        except Exception:
            pass


def _normalize_box(item: Any) -> Optional[dict[str, Any]]:
    """Convert arbitrary box formats into standard dict format."""
    try:
        if isinstance(item, dict):
            if "box" in item:
                b = item["box"]
                if isinstance(b, (tuple, list)) and len(b) >= 4:
                    x, y, w, h = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                    lbl = str(item.get("label") or item.get("text") or "")
                    return {
                        "x": x,
                        "y": y,
                        "left": x,
                        "top": y,
                        "width": max(1, w),
                        "height": max(1, h),
                        "label": lbl,
                    }
            if "rect" in item:
                r = item["rect"]
                if isinstance(r, (tuple, list)) and len(r) >= 4:
                    x1, y1, x2, y2 = int(r[0]), int(r[1]), int(r[2]), int(r[3])
                    left = min(x1, x2)
                    top = min(y1, y2)
                    w = max(1, abs(x2 - x1))
                    h = max(1, abs(y2 - y1))
                    lbl = str(item.get("label") or item.get("text") or item.get("name") or "")
                    return {
                        "x": left,
                        "y": top,
                        "left": left,
                        "top": top,
                        "width": w,
                        "height": h,
                        "label": lbl,
                    }
            if ("x" in item or "left" in item) and ("y" in item or "top" in item):
                x = int(item.get("x", item.get("left", 0)))
                y = int(item.get("y", item.get("top", 0)))
                w = int(item.get("width", item.get("w", 0)))
                h = int(item.get("height", item.get("h", 0)))
                lbl = str(item.get("label") or item.get("text") or "")
                return {
                    "x": x,
                    "y": y,
                    "left": x,
                    "top": y,
                    "width": max(1, w),
                    "height": max(1, h),
                    "label": lbl,
                }
        elif isinstance(item, (tuple, list)) and len(item) >= 4:
            x, y, w, h = int(item[0]), int(item[1]), int(item[2]), int(item[3])
            lbl = str(item[4]) if len(item) > 4 else ""
            return {
                "x": x,
                "y": y,
                "left": x,
                "top": y,
                "width": max(1, w),
                "height": max(1, h),
                "label": lbl,
            }
    except Exception as e:
        logger.debug(f"[Overlay] Box normalization error: {e}")
    return None


def set_island_state(state: str, text: str = "", subtext: str = "", auto_idle_sec: float = 0.0) -> None:
    """Set the top Dynamic Island state and text.

    States: "idle", "listening", "thinking", "locating", "acting", "speaking", "assist"
    """
    with _state_lock:
        _overlay_state["island_state"] = str(state)
        _overlay_state["island_text"] = str(text)
        _overlay_state["island_subtext"] = str(subtext)

    _ensure_daemon_running()

    global _bridge
    if _bridge is not None and is_overlay_running():
        try:
            if hasattr(_bridge, "sigIslandState"):
                _bridge.sigIslandState.emit(str(state), str(text), str(subtext), float(auto_idle_sec))
            elif hasattr(_bridge, "sig_island_state"):
                _bridge.sig_island_state.emit(str(state), str(text), str(subtext), float(auto_idle_sec))
        except Exception as e:
            logger.debug(f"[Overlay] Error emitting sigIslandState: {e}")


def set_assist_mode_badge(active: bool) -> None:
    """Toggle the glowing cyan [ ASSIST ] badge on the Dynamic Island."""
    with _state_lock:
        _overlay_state["assist_mode"] = bool(active)

    _ensure_daemon_running()

    global _bridge
    if _bridge is not None and is_overlay_running():
        try:
            if hasattr(_bridge, "sigAssistBadge"):
                _bridge.sigAssistBadge.emit(bool(active))
            elif hasattr(_bridge, "sig_assist_badge"):
                _bridge.sig_assist_badge.emit(bool(active))
        except Exception as e:
            logger.debug(f"[Overlay] Error emitting sigAssistBadge: {e}")


def move_ghost_cursor(x: int, y: int, label: str = "", duration_ms: int = 380, pulse: bool = True) -> None:
    """Animate the ghost cursor to screen position (x, y) with motion trail and optional pulse."""
    with _state_lock:
        _overlay_state["ghost_pos"] = (int(x), int(y))
        _overlay_state["ghost_label"] = str(label)

    _ensure_daemon_running()

    global _bridge
    if _bridge is not None and is_overlay_running():
        try:
            if hasattr(_bridge, "sigGhostMove"):
                _bridge.sigGhostMove.emit(int(x), int(y), str(label), int(duration_ms), bool(pulse))
            elif hasattr(_bridge, "sig_ghost_move"):
                _bridge.sig_ghost_move.emit(int(x), int(y), str(label), int(duration_ms), bool(pulse))
        except Exception as e:
            logger.debug(f"[Overlay] Error emitting sigGhostMove: {e}")


def hide_ghost_cursor() -> None:
    """Fade out the ghost cursor."""
    with _state_lock:
        _overlay_state["ghost_pos"] = None
        _overlay_state["ghost_label"] = ""

    global _bridge
    if _bridge is not None and is_overlay_running():
        try:
            if hasattr(_bridge, "sigGhostHide"):
                _bridge.sigGhostHide.emit()
            elif hasattr(_bridge, "sig_ghost_hide"):
                _bridge.sig_ghost_hide.emit()
        except Exception as e:
            logger.debug(f"[Overlay] Error emitting sigGhostHide: {e}")


def show_highlights(boxes: list[Any] | Any, duration: float = 5.0, move_ghost: bool = True) -> None:
    """Render laser bounding boxes with corner HUD brackets and floating labels."""
    if not boxes:
        return
    if isinstance(boxes, dict):
        boxes = [boxes]
    elif isinstance(boxes, (tuple, list)) and len(boxes) >= 4 and isinstance(boxes[0], (int, float)):
        boxes = [boxes]

    normalized: list[dict[str, Any]] = []
    for b in boxes:
        nb = _normalize_box(b)
        if nb:
            normalized.append(nb)

    with _state_lock:
        _overlay_state["active_highlights"] = list(normalized)
        if move_ghost and normalized:
            first = normalized[0]
            cx = first["x"] + first["width"] // 2
            cy = first["y"] + first["height"] // 2
            _overlay_state["ghost_pos"] = (cx, cy)
            _overlay_state["ghost_label"] = first.get("label", "")

    _ensure_daemon_running()

    global _bridge
    if _bridge is not None and is_overlay_running():
        try:
            if hasattr(_bridge, "sigShowHighlights"):
                _bridge.sigShowHighlights.emit(normalized, float(duration), bool(move_ghost))
            elif hasattr(_bridge, "sig_show_highlights"):
                _bridge.sig_show_highlights.emit(normalized, float(duration), bool(move_ghost))
        except Exception as e:
            logger.debug(f"[Overlay] Error emitting sigShowHighlights: {e}")


def clear_highlights() -> None:
    """Clear all active bounding box highlights."""
    with _state_lock:
        _overlay_state["active_highlights"] = []

    global _bridge
    if _bridge is not None and is_overlay_running():
        try:
            if hasattr(_bridge, "sigClearHighlights"):
                _bridge.sigClearHighlights.emit()
            elif hasattr(_bridge, "sig_clear_highlights"):
                _bridge.sig_clear_highlights.emit()
        except Exception as e:
            logger.debug(f"[Overlay] Error emitting sigClearHighlights: {e}")


def pulse_at(x: int, y: int, label: str = "") -> None:
    """Spawn a 700ms Jarvis Cyan target lock-on ring at (x, y)."""
    with _state_lock:
        _overlay_state["last_pulse"] = (int(x), int(y), str(label))

    _ensure_daemon_running()

    global _bridge
    if _bridge is not None and is_overlay_running():
        try:
            if hasattr(_bridge, "sigPulse"):
                _bridge.sigPulse.emit(int(x), int(y), str(label))
            elif hasattr(_bridge, "sig_pulse"):
                _bridge.sig_pulse.emit(int(x), int(y), str(label))
        except Exception as e:
            logger.debug(f"[Overlay] Error emitting sigPulse: {e}")


def _run_qt_app(force_capture_visible: bool, qml_path: str) -> None:
    """Main function executing on the Qt daemon thread."""
    global _bridge, _view, _app

    try:
        from PySide6.QtCore import QObject, Qt, QUrl, Signal
        from PySide6.QtGui import QColor, QGuiApplication, QSurfaceFormat
        from PySide6.QtQuick import QQuickView
    except Exception as e:
        logger.warning(f"[Overlay] PySide6 import failed: {e}")
        _ready_event.set()
        return

    # Set up alpha buffer for native per-pixel transparency
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    _app = QGuiApplication.instance()
    if _app is None:
        _app = QGuiApplication(sys.argv)

    # QObject Bridge class for cross-thread signals (CamelCase primary + snake_case aliases)
    class OverlayBridge(QObject):
        sigIslandState = Signal(str, str, str, float)
        sigAssistBadge = Signal(bool)
        sigGhostMove = Signal(int, int, str, int, bool)
        sigGhostHide = Signal()
        sigShowHighlights = Signal(list, float, bool)
        sigClearHighlights = Signal()
        sigPulse = Signal(int, int, str)
        sigQuit = Signal()

        sig_island_state = sigIslandState
        sig_assist_badge = sigAssistBadge
        sig_ghost_move = sigGhostMove
        sig_ghost_hide = sigGhostHide
        sig_show_highlights = sigShowHighlights
        sig_clear_highlights = sigClearHighlights
        sig_pulse = sigPulse
        sig_quit = sigQuit

    _bridge = OverlayBridge()
    _bridge.sigQuit.connect(_app.quit)

    _view = QQuickView()
    _view.setColor(QColor(0, 0, 0, 0))
    _view.setFlags(
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.WindowTransparentForInput
        | Qt.WindowType.Tool
    )
    _view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)

    screen = _app.primaryScreen()
    if screen:
        _view.setGeometry(screen.geometry())

    _view.rootContext().setContextProperty("overlayBridge", _bridge)
    _view.setSource(QUrl.fromLocalFile(qml_path))
    _view.show()

    # Windows Win32 extended style application
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = int(_view.winId())
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080

            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd,
                GWL_EXSTYLE,
                style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            )
            # Ensure layered window attributes are explicitly initialized to full 255 alpha
            try:
                LWA_ALPHA = 0x00000002
                ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
            except Exception:
                pass

            # WDA_EXCLUDEFROMCAPTURE (0x00000011) hides overlay from screenshots
            if not force_capture_visible:
                exclude_capture = True
                try:
                    import config
                    exclude_capture = getattr(config, "OVERLAY_EXCLUDE_CAPTURE", True)
                except Exception:
                    pass

                if exclude_capture:
                    WDA_EXCLUDEFROMCAPTURE = 0x00000011
                    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
                else:
                    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0)
            else:
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0)
        except Exception as e:
            logger.debug(f"[Overlay] Win32 style config error: {e}")

    with _state_lock:
        _overlay_state["is_running"] = True

    root = _view.rootObject()
    if root is not None:
        try:
            import config
            idle_hide = bool(getattr(config, "OVERLAY_IDLE_HIDE", False))
            idle_hide_sec = float(getattr(config, "OVERLAY_IDLE_HIDE_SECONDS", 15.0))
            root.setProperty("idleAutoHide", idle_hide)
            root.setProperty("idleAutoHideMs", int(idle_hide_sec * 1000))
        except Exception as e:
            logger.debug(f"[Overlay] Config property setup error: {e}")

    try:
        import config
        init_assist = bool(getattr(config, "ASSIST_MODE", False))
        with _state_lock:
            _overlay_state["assist_mode"] = init_assist
        if hasattr(_bridge, "sigAssistBadge"):
            _bridge.sigAssistBadge.emit(init_assist)
        elif hasattr(_bridge, "sig_assist_badge"):
            _bridge.sig_assist_badge.emit(init_assist)
    except Exception:
        pass

    _ready_event.set()

    # Qt GUI Event Loop
    _app.exec()

    with _state_lock:
        _overlay_state["is_running"] = False


def start_overlay_daemon(force_capture_visible: bool = False) -> bool:
    """Start the overlay daemon thread. Returns True if started or already running."""
    # Check config
    try:
        import config
        if not getattr(config, "OVERLAY_ENABLED", True):
            logger.debug("[Overlay] OVERLAY_ENABLED is False; not starting.")
            return False
    except Exception:
        pass

    if is_overlay_running():
        return True

    qml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overlay.qml")
    if not os.path.exists(qml_path):
        logger.warning(f"[Overlay] QML file not found: {qml_path}")
        return False

    global _daemon_thread, _ready_event
    _ready_event.clear()

    _daemon_thread = threading.Thread(
        target=_run_qt_app,
        args=(force_capture_visible, qml_path),
        name="AsterOverlayDaemon",
        daemon=True,
    )
    _daemon_thread.start()

    ready = _ready_event.wait(timeout=3.5)
    return ready and is_overlay_running()


def stop_overlay_daemon() -> None:
    """Stop the overlay daemon and close the window."""
    global _bridge, _daemon_thread
    if _bridge is not None:
        try:
            if hasattr(_bridge, "sigQuit"):
                _bridge.sigQuit.emit()
            elif hasattr(_bridge, "sig_quit"):
                _bridge.sig_quit.emit()
        except Exception:
            pass

    if _daemon_thread is not None and _daemon_thread.is_alive():
        _daemon_thread.join(timeout=2.0)

    with _state_lock:
        _overlay_state["is_running"] = False
