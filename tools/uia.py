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
_TASKBAR_BUDGET_S = 2.0       # max time walking the desktop icons & taskbar tree (170+ items)
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


def _attach_thread_desktop():
    """Ensure current thread is attached to interactive input desktop if needed."""
    try:
        user32 = ctypes.windll.user32
        idesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if idesk:
            user32.SetThreadDesktop(idesk)
    except Exception:
        pass


def _com_scope():
    """COM apartment for the calling thread. execute_tool() runs on whichever
    thread the interface lives on (CLI main, Telegram, LiveKit...), and UIA
    requires CoInitialize per thread. The initializer is balanced, so using it
    on an already-initialized thread is harmless."""
    _attach_thread_desktop()
    return auto.UIAutomationInitializerInThread(debug=False)


def _is_telegram_window(top) -> bool:
    """Recognize when the top-level window is Aster's own control window (Telegram.exe,
    TelegramDesktop, or window title contains 'aster_local' or Aster's chat)."""
    if top is None:
        return False
    try:
        title = (getattr(top, "Name", "") or "").lower()
        fg_title = (get_foreground_window_title() or "").lower()
        if "aster_local" in title or "aster_local" in fg_title or "aster's chat" in title or "aster chat" in title:
            return True
        cls_name = (getattr(top, "ClassName", "") or "").lower()
        if "telegramdesktop" in cls_name or cls_name == "telegram":
            return True
        pid = getattr(top, "ProcessId", 0)
        if pid:
            try:
                import psutil
                pname = psutil.Process(pid).name().lower()
                if "telegram.exe" in pname or pname == "telegram":
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _iter_controls(top, include_top: bool = True, max_depth: int = _MAX_DEPTH):
    """Walk controls under `top` using WalkControl with fallback to GetChildren traversal."""
    if include_top:
        yield top
    if auto and hasattr(auto, "WalkControl"):
        try:
            for item in auto.WalkControl(top, includeTop=False, maxDepth=max_depth):
                yield item[0] if isinstance(item, tuple) else item
            return
        except Exception:
            pass

    def _walk_rec(node, depth):
        if depth > max_depth or not hasattr(node, "GetChildren"):
            return
        try:
            children = node.GetChildren() or []
        except Exception:
            children = []
        for c in children:
            yield c
            yield from _walk_rec(c, depth + 1)

    yield from _walk_rec(top, 1)


def _walk_named_controls(top, budget_s: float, exclude_types: set[str] | None = None, source: str = "window") -> list[dict]:
    """Collect visible, named controls under `top` within a time budget."""
    out: list[dict] = []
    deadline = time.time() + budget_s
    try:
        for ctrl in _iter_controls(top, include_top=True, max_depth=_MAX_DEPTH):
            if time.time() > deadline:
                break
            try:
                type_name = getattr(ctrl, "ControlTypeName", "")
                if exclude_types and type_name in exclude_types:
                    continue
                name = getattr(ctrl, "Name", None)
                if not name or getattr(ctrl, "IsOffscreen", False):
                    continue
                r = getattr(ctrl, "BoundingRectangle", None)
                if r is None or r.right - r.left <= 0 or r.bottom - r.top <= 0:
                    continue
                cand = {
                    "name": name,
                    "type": type_name,
                    "rect": (r.left, r.top, r.right, r.bottom),
                }
                if source:
                    cand["source"] = source
                out.append(cand)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _get_desktop_listview_hwnd() -> int:
    """Win32 lookup for desktop SysListView32 window handle across Progman and WorkerW."""
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowExW.restype = ctypes.c_void_p

        _attach_thread_desktop()

        def _find_lv_under(parent_hwnd):
            if not parent_hwnd:
                return 0
            defview = user32.FindWindowExW(parent_hwnd, None, "SHELLDLL_DefView", None)
            if defview:
                lv = user32.FindWindowExW(defview, None, "SysListView32", None)
                if lv:
                    return lv
            return 0

        # 1. Search Progman ('Program Manager')
        for progman in (
            user32.FindWindowW("Progman", "Program Manager"),
            user32.FindWindowW("Progman", None),
            user32.FindWindowExW(None, None, "Progman", None),
        ):
            lv = _find_lv_under(progman)
            if lv:
                return lv

        # 2. Search WorkerW windows using FindWindowExW
        worker = None
        while True:
            worker = user32.FindWindowExW(None, worker, "WorkerW", None)
            if not worker:
                break
            lv = _find_lv_under(worker)
            if lv:
                return lv

        # 3. EnumWindows fallback across all top-level windows
        target_lv = [0]
        def _enum_cb(hwnd, _):
            buf_cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf_cls, 256)
            buf_text = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf_text, 256)
            if buf_cls.value in ("Progman", "WorkerW") or "Program Manager" in buf_text.value:
                lv = _find_lv_under(hwnd)
                if lv:
                    target_lv[0] = lv
                    return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        return target_lv[0]
    except Exception:
        return 0


def _find_desktop_listview_control(root):
    """Find Progman / WorkerW containing SHELLDLL_DefView -> SysListView32."""
    if root is None:
        return None

    # Tree walk under root: search Progman and WorkerW top-level windows
    try:
        children = root.GetChildren() if hasattr(root, "GetChildren") else []
        for child in children:
            cls = getattr(child, "ClassName", "")
            name = getattr(child, "Name", "")
            if cls in ("Progman", "WorkerW") or name == "Program Manager":
                sub_children = child.GetChildren() if hasattr(child, "GetChildren") else []
                def_view = None
                for sub in sub_children:
                    if getattr(sub, "ClassName", "") == "SHELLDLL_DefView":
                        def_view = sub
                        break
                if def_view is None and hasattr(child, "Control"):
                    try:
                        dv = child.Control(searchDepth=2, ClassName="SHELLDLL_DefView")
                        if dv and getattr(dv, "Exists", lambda *a: True)(0, 0):
                            def_view = dv
                    except Exception:
                        pass
                if def_view is not None:
                    dv_children = def_view.GetChildren() if hasattr(def_view, "GetChildren") else []
                    for lv in dv_children:
                        if getattr(lv, "ClassName", "") == "SysListView32" or getattr(lv, "ControlTypeName", "") == "ListControl":
                            return lv
                    if hasattr(def_view, "Control"):
                        try:
                            lv = def_view.Control(searchDepth=2, ClassName="SysListView32")
                            if lv and getattr(lv, "Exists", lambda *a: True)(0, 0):
                                return lv
                        except Exception:
                            pass
                    return def_view
    except Exception:
        pass

    # Win32 HWND fallback -> ControlFromHandle
    try:
        hwnd = _get_desktop_listview_hwnd()
        if hwnd and auto and hasattr(auto, "ControlFromHandle"):
            ctrl = auto.ControlFromHandle(hwnd)
            if ctrl:
                return ctrl
    except Exception:
        pass

    return None


def _collect_desktop_candidates(root, budget_s: float = _TASKBAR_BUDGET_S) -> list[dict]:
    """Enumerate Windows desktop shortcuts as ListItemControl within budget_s."""
    out: list[dict] = []
    lv = _find_desktop_listview_control(root)
    if lv is None:
        return out

    deadline = time.time() + budget_s
    try:
        for ctrl in _iter_controls(lv, include_top=False, max_depth=_MAX_DEPTH):
            if time.time() > deadline:
                break
            try:
                name = getattr(ctrl, "Name", None)
                if not name or getattr(ctrl, "IsOffscreen", False):
                    continue
                r = getattr(ctrl, "BoundingRectangle", None)
                if r is None or r.right - r.left <= 0 or r.bottom - r.top <= 0:
                    continue
                type_name = getattr(ctrl, "ControlTypeName", "")
                if type_name in ("PaneControl", "ListControl", "WindowControl"):
                    continue
                # Desktop shortcuts are ListItemControl
                cand_type = type_name if type_name else "ListItemControl"
                x1, y1, x2, y2 = r.left, r.top, r.right, r.bottom
                w = x2 - x1
                h = y2 - y1
                # Refine bounding rectangle: icon image is located in the upper portion
                # of the item cell (top ~48px, horizontally centered), text label is below.
                tight_w = min(48, w)
                tight_h = min(48, h)
                icon_rect = (
                    x1 + (w - tight_w) // 2,
                    y1 + 2,
                    x1 + (w + tight_w) // 2,
                    y1 + tight_h,
                )
                out.append({
                    "name": name,
                    "type": cand_type,
                    "rect": icon_rect,
                    "full_rect": (x1, y1, x2, y2),
                    "source": "desktop",
                })
            except Exception:
                continue
    except Exception:
        pass
    return out


def _collect_candidates() -> tuple[list[dict], tuple[int, int]]:
    """Foreground window + taskbar controls + desktop icons, plus physical desktop size."""
    _attach_thread_desktop()
    root = auto.GetRootControl()
    rr = root.BoundingRectangle
    desktop_size = (rr.right - rr.left, rr.bottom - rr.top)

    candidates: list[dict] = []

    fg = auto.GetForegroundControl()
    top = fg.GetTopLevelControl() if fg else None
    if top is not None:
        is_telegram = _is_telegram_window(top)
        exclude_types = {"TextControl"} if is_telegram else None
        candidates = _walk_named_controls(top, _WALK_BUDGET_S, exclude_types=exclude_types, source="window")
        if len(candidates) < _SPARSE_RETRY_THRESHOLD and not is_telegram:
            # Chromium/Electron warm-up: the first walk switches the app's
            # accessibility layer on; the tree fills in moments later.
            time.sleep(0.8)
            candidates = _walk_named_controls(top, _WALK_BUDGET_S, exclude_types=exclude_types, source="window")

    # Taskbar (Start, pinned apps, tray) — a separate top-level window.
    try:
        children = root.GetChildren() if hasattr(root, "GetChildren") else []
        for child in children:
            if getattr(child, "ClassName", "") == "Shell_TrayWnd":
                candidates.extend(_walk_named_controls(child, _TASKBAR_BUDGET_S, source="taskbar"))
                break
    except Exception:
        pass

    # Windows desktop icons: find Progman / WorkerW containing SHELLDLL_DefView -> SysListView32,
    # and walk it within _TASKBAR_BUDGET_S to collect desktop shortcuts as ListItemControl.
    try:
        desktop_icons = _collect_desktop_candidates(root, _TASKBAR_BUDGET_S)
        if desktop_icons:
            existing_rects = {c["rect"] for c in candidates}
            for icon in desktop_icons:
                if icon["rect"] not in existing_rects:
                    candidates.append(icon)
                    existing_rects.add(icon["rect"])
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
        import re
        desk_w, desk_h = desktop_size
        parsed = parse_goal(goal)
        raw_goal = parsed.get("raw", "")
        is_desktop = parsed.get("is_desktop") or bool(re.search(r"\b(?:desktop|wallpaper)\b", raw_goal))

        import pyautogui
        logical_w, logical_h = pyautogui.size()
        # UIA rects are physical desktop pixels; pyautogui may be DPI-
        # virtualized. On 100% scaling these are equal (scale = 1.0).
        scale_x = logical_w / desk_w if desk_w else 1.0
        scale_y = logical_h / desk_h if desk_h else 1.0

        # If a goal queries a desktop icon ('desktop', 'on the desktop', 'desktop icon', 'desktop shortcut'),
        # strictly prioritize candidates with source == 'desktop', and heavily penalize or exclude
        # internal application window controls (such as Electron/Telegram chat text, buttons, and listitems
        # from Antigravity/Chrome/Telegram) so open application windows cannot hijack desktop goals!
        if is_desktop:
            has_desktop = any(c.get("source") == "desktop" for c in candidates)
            if has_desktop:
                candidates = [c for c in candidates if c.get("source") == "desktop"]
            else:
                candidates = [c for c in candidates if c.get("source") not in ("window", "taskbar", "app")]

        best: dict | None = None
        best_score = 0.0
        for cand in candidates:
            cand_src = cand.get("source", "")
            cand_type = cand.get("type", "")
            score = score_text(cand["name"], parsed, cand_type, cand_source=cand_src)
            if parsed["type_hints"] and cand_type in parsed["type_hints"]:
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
        cand_src = best.get("source", "uia")
        print(f"[Locator] UIA match: {best['name']!r} [{best['type']}] (source={cand_src}) "
              f"score={best_score:.2f} -> ({cx},{cy})")
        res = {
            "x": cx, "y": cy, "source": "uia",
            "text": best["name"], "score": round(best_score, 2),
            "type": best["type"],
        }
        if "source" in best:
            res["node_source"] = best["source"]
        if "full_rect" in best:
            res["full_rect"] = best["full_rect"]
        return res
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
