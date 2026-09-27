"""Assist Mode — screen highlight overlay.

Dims the whole screen and leaves the located element(s) at full brightness
inside a bright highlight box. Used by the `highlight_on_screen` admin tool and
the `/find` Telegram command. Pure stdlib tkinter — no extra dependencies.
"""

import threading

# Color used as the transparency key — the located element's interior is painted
# this color so it becomes fully see-through (real screen shows at full brightness).
_KEY_COLOR = "#FF00FF"
_HIGHLIGHT_COLOR = "#FFD24A"  # gold outline frame around each match
_DIM_ALPHA = 0.80             # window-wide opacity → desktop dimmed to ~20%

_lock = threading.Lock()
_active_root = None  # tkinter root of the live overlay, if any


def _run_overlay(boxes, duration):
    """Build and run the tkinter overlay. Must execute entirely on one thread."""
    global _active_root
    try:
        import tkinter as tk
    except Exception as e:
        print(f"[Assist] tkinter unavailable: {e}")
        return

    try:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", _DIM_ALPHA)
        try:
            root.attributes("-transparentcolor", _KEY_COLOR)
        except tk.TclError:
            pass  # non-Windows: degrade gracefully to a plain dim (no holes)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"{sw}x{sh}+0+0")

        canvas = tk.Canvas(root, width=sw, height=sh, bg="black", highlightthickness=0)
        canvas.pack()

        for item in boxes:
            try:
                left, top, width, height = item["box"]
            except (KeyError, ValueError, TypeError):
                continue
            x1, y1, x2, y2 = left, top, left + width, top + height
            # Hole: interior painted in the transparency key → full brightness
            canvas.create_rectangle(x1, y1, x2, y2, fill=_KEY_COLOR, outline="")
            # Bright frame just outside the element
            canvas.create_rectangle(
                x1 - 3, y1 - 3, x2 + 3, y2 + 3,
                outline=_HIGHLIGHT_COLOR, width=3,
            )

        def _dismiss(_event=None):
            try:
                root.destroy()
            except Exception:
                pass

        root.bind("<Key>", _dismiss)
        root.bind("<Button>", _dismiss)
        root.after(int(duration * 1000), _dismiss)

        with _lock:
            _active_root = root

        root.focus_force()
        root.mainloop()
    except Exception as e:
        print(f"[Assist] Overlay error: {e}")
    finally:
        with _lock:
            if _active_root is root:
                _active_root = None


def highlight_regions(boxes, duration=6.0):
    """Show a dim overlay highlighting `boxes`; non-blocking.

    `boxes` is a list of dicts with a "box": (left, top, width, height) entry in
    logical screen pixels (as returned by vision.locate_ui_elements_boxed).
    Auto-dismisses after `duration`s or on any key/click. Any prior overlay is
    replaced. A tkinter failure is logged and swallowed — never crashes callers.
    """
    if not boxes:
        return
    # Replace any overlay currently on screen.
    with _lock:
        prior = _active_root
    if prior is not None:
        try:
            prior.after(0, prior.destroy)
        except Exception:
            pass

    threading.Thread(
        target=_run_overlay, args=(list(boxes), duration), daemon=True
    ).start()


if __name__ == "__main__":
    # Smoke test: dim the screen, highlight one box, auto-fade after 6s.
    highlight_regions([{"box": (400, 300, 220, 60), "score": 1.0, "text": "test"}])
    import time
    time.sleep(8)
