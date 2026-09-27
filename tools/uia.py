"""Track 0 UI locator — Windows UI Automation accessibility tree.

Instead of guessing at pixels with OCR, ask Windows what's on screen: every
accessible element exposes its exact Name, ControlType, and clickable rect.
Deterministic, ~0.1-2 s, zero GPU. Falls back to None on ANY failure so the
caller (tools/vision.py locate_ui_element) can drop to the OCR/YOLO tracks —
this module must never crash the brain.

Coverage notes (verified live 2026-07-11):
- Native Win32/WPF/UWP apps and the taskbar expose full trees immediately.
- Chromium/Electron apps (Chrome, Edge, Spotify, Discord, VS Code) enable
  their accessibility layer lazily on the FIRST UIA query: the first walk may
  see only an empty "Chrome Legacy Window" pane. Walking is itself the wake-up
  call, so we retry once after a short sleep when the tree looks sparse.
- Games/canvas-rendered surfaces expose nothing → OCR/YOLO fallback.
- Elevated (admin) windows are unreadable from a non-elevated process; the
  secure desktop (UAC prompt / lock screen) is off-limits entirely.
"""
import ctypes
import time

from tools.locator_common import parse_goal, score_text, spatial_weight

try:
    import uiautomation as auto
except Exception:
    auto = None

# Controls where Ctrl+A-then-paste is safe (it's a form field, not a document).
EDITABLE_CONTROL_TYPES = {"EditControl", "ComboBoxControl"}

_WALK_BUDGET_S = 2.0          # max time walking the foreground window tree
_TASKBAR_BUDGET_S = 0.8       # max time walking the taskbar tree
_SPARSE_RETRY_THRESHOLD = 12  # fewer named controls than this → Chromium warm-up retry
_SCORE_THRESHOLD = 0.70       # UIA names are exact strings; demand a strong match
_MAX_DEPTH = 50


def get_foreground_window_title() -> str:
    """Title of the current foreground window ('' on failure). Cheap Win32
    ground truth used by smart_* tool results — no UIA involvement."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def _com_scope():
    """COM apartment for the calling thread. execute_tool() runs on whichever
    thread the interface lives on (CLI main, Telegram, LiveKit...), and UIA
    requires CoInitialize per thread. The initializer is balanced, so using it
    on an already-initialized thread is harmless."""
    return auto.UIAutomationInitializerInThread(debug=False)


def _walk_named_controls(top, budget_s: float) -> list[dict]:
    """Collect visible, named controls under `top` within a time budget."""
    out: list[dict] = []
    deadline = time.time() + budget_s
    try:
        for ctrl, _depth in auto.WalkControl(top, includeTop=True, maxDepth=_MAX_DEPTH):
            if time.time() > deadline:
                break
            try:
                name = ctrl.Name
                if not name or ctrl.IsOffscreen:
                    continue
                r = ctrl.BoundingRectangle
                if r.right - r.left <= 0 or r.bottom - r.top <= 0:
                    continue
                out.append({
                    "name": name,
                    "type": ctrl.ControlTypeName,
                    "rect": (r.left, r.top, r.right, r.bottom),
                })
            except Exception:
                continue
    except Exception:
        pass
    return out


def _collect_candidates() -> tuple[list[dict], tuple[int, int]]:
    """Foreground window + taskbar controls, plus the physical desktop size."""
    root = auto.GetRootControl()
    rr = root.BoundingRectangle
    desktop_size = (rr.right - rr.left, rr.bottom - rr.top)

    candidates: list[dict] = []

    fg = auto.GetForegroundControl()
    top = fg.GetTopLevelControl() if fg else None
    if top is not None:
        candidates = _walk_named_controls(top, _WALK_BUDGET_S)
        if len(candidates) < _SPARSE_RETRY_THRESHOLD:
            # Chromium/Electron warm-up: the first walk switches the app's
            # accessibility layer on; the tree fills in moments later.
            time.sleep(0.8)
            candidates = _walk_named_controls(top, _WALK_BUDGET_S)

    # Taskbar (Start, pinned apps, tray) — a separate top-level window.
    try:
        for child in root.GetChildren():
            if child.ClassName == "Shell_TrayWnd":
                candidates.extend(_walk_named_controls(child, _TASKBAR_BUDGET_S))
                break
    except Exception:
        pass

    return candidates, desktop_size


def uia_nodes() -> tuple[list[dict], tuple[int, int]]:
    """All visible named controls + desktop size, for the DOM motor shortlist.

    Same walk as uia_locate but returns the raw candidates so tools/dom.py can
    filter/rank them itself. Never raises: ([], (0, 0)) when UIA is unavailable.
    """
    if auto is None:
        return [], (0, 0)
    try:
        with _com_scope():
            return _collect_candidates()
    except Exception as e:
        print(f"[Locator] UIA node walk failed: {e}")
        return [], (0, 0)


def uia_locate_candidates(goal: str, candidates, desktop_size) -> dict | None:
    """Exact-score pre-collected candidates (same contract as uia_locate).

    Split out so the DOM motor can reuse ONE walk for both its shortlist and
    this exact-scoring fallback instead of walking the tree twice per locate.
    """
    if not goal or not goal.strip() or not candidates or not desktop_size:
        return None
    try:
        desk_w, desk_h = desktop_size
        parsed = parse_goal(goal)

        import pyautogui
        logical_w, logical_h = pyautogui.size()
        # UIA rects are physical desktop pixels; pyautogui may be DPI-
        # virtualized. On 100% scaling these are equal (scale = 1.0).
        scale_x = logical_w / desk_w if desk_w else 1.0
        scale_y = logical_h / desk_h if desk_h else 1.0

        best: dict | None = None
        best_score = 0.0
        for cand in candidates:
            score = score_text(cand["name"], parsed)
            if parsed["type_hints"] and cand["type"] in parsed["type_hints"]:
                score = min(1.0, score + 0.05)
            x1, y1, x2, y2 = cand["rect"]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            score *= spatial_weight(cx, cy, desk_w, desk_h, parsed["spatial"])
            if score > best_score:
                best_score = score
                best = cand

        if best is None or best_score < _SCORE_THRESHOLD:
            print(f"[Locator] UIA track: no match (best={best_score:.2f})")
            return None

        x1, y1, x2, y2 = best["rect"]
        cx = int((x1 + x2) / 2 * scale_x)
        cy = int((y1 + y2) / 2 * scale_y)
        print(f"[Locator] UIA match: {best['name']!r} [{best['type']}] "
              f"score={best_score:.2f} -> ({cx},{cy})")
        return {
            "x": cx, "y": cy, "source": "uia",
            "text": best["name"], "score": round(best_score, 2),
            "type": best["type"],
        }
    except Exception as e:
        print(f"[Locator] UIA track error: {e}")
        return None


def uia_locate(goal: str) -> dict | None:
    """Find the on-screen element best matching `goal` via the UIA tree.

    Returns {"x", "y", "source": "uia", "text", "score", "type"} in LOGICAL
    pixels (ready for pyautogui), or None if UIA is unavailable, errors, or
    nothing scores above threshold.
    """
    if auto is None or not goal or not goal.strip():
        return None
    try:
        with _com_scope():
            candidates, desktop_size = _collect_candidates()
            return uia_locate_candidates(goal, candidates, desktop_size)
    except Exception as e:
        print(f"[Locator] UIA track error: {e}")
        return None


def focused_control_type() -> str | None:
    """ControlTypeName of the keyboard-focused element, or None when UIA
    can't tell. Used by smart_type to decide whether Ctrl+A is safe."""
    if auto is None:
        return None
    try:
        with _com_scope():
            foc = auto.GetFocusedControl()
            return foc.ControlTypeName if foc is not None else None
    except Exception:
        return None
