"""Humanized Motor — smooth, natural mouse movement and hybrid cadence typing.

Part of the Clicky Integration for Aster desktop companion.
Provides:
- Eased cubic Bezier curve generation for human-like cursor trajectories.
- Distance-scaled duration calculation (short hops ~0.12s, medium ~0.25s, cross-screen ~0.42s).
- Visual feedback hook to companion overlay (tools.overlay.pulse_at).
- Hybrid natural typing (character-by-character cadence typing with human rhythm
  delays for short ASCII text, instant clipboard paste for large/Unicode strings).
"""

import math
import time
import random
import pyautogui
import pyperclip
import config

__all__ = [
    "ease_out_cubic",
    "bezier_curve_points",
    "compute_move_duration",
    "human_move_to",
    "human_click",
    "should_use_cadence_typing",
    "human_type",
]


def ease_out_cubic(t: float) -> float:
    """Standard cubic ease-out curve: fast start, soft deceleration."""
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


def bezier_curve_points(
    start: tuple[int, int],
    end: tuple[int, int],
    steps: int = 24,
    arc_intensity: float = 0.12,
) -> list[tuple[int, int]]:
    """Compute a smooth quadratic Bezier curve with an eased trajectory.

    Applies a perpendicular control-point offset (arc_intensity * distance)
    so the cursor travels along a graceful natural arc rather than a rigid
    mechanical straight line. Uses ease_out_cubic to sample t values so the cursor
    accelerates quickly and decelerates smoothly into the target coordinate.
    Always guarantees the final point in the returned list is (int(end[0]), int(end[1])).
    """
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    target = (int(round(x1)), int(round(y1)))

    steps = max(1, int(steps))
    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy)

    if dist < 1.0 or steps <= 1:
        return [target]

    # Midpoint of chord
    mx = (x0 + x1) / 2.0
    my = (y0 + y1) / 2.0

    # Perpendicular unit vector (-dy / dist, dx / dist) scaled by (arc_intensity * dist):
    # (-dy / dist) * (arc_intensity * dist) = -dy * arc_intensity
    cx = mx - dy * arc_intensity
    cy = my + dx * arc_intensity

    points: list[tuple[int, int]] = []
    for i in range(1, steps + 1):
        t_linear = i / float(steps)
        u = ease_out_cubic(t_linear)
        inv_u = 1.0 - u
        # Quadratic Bezier formula: (1-u)^2 * P0 + 2*(1-u)*u * P_ctrl + u^2 * P_end
        px = (inv_u ** 2) * x0 + 2.0 * inv_u * u * cx + (u ** 2) * x1
        py = (inv_u ** 2) * y0 + 2.0 * inv_u * u * cy + (u ** 2) * y1
        points.append((int(round(px)), int(round(py))))

    # Guarantee exact final placement
    if not points or points[-1] != target:
        if points:
            points[-1] = target
        else:
            points.append(target)

    return points


def compute_move_duration(
    start: tuple[int, int],
    end: tuple[int, int],
    base_duration: float | None = None,
) -> float:
    """Scales duration by pixel distance.

    Calibrated so:
    - short hops (~50px) take ~0.12s
    - medium hops (~400px) take ~0.25s
    - cross-screen flights (~1500px+) take ~0.42s
    """
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if dist < 1.0:
        return 0.05

    # Nominal curve: d = 0.057 + 0.0095 * sqrt(dist)
    # 50px -> ~0.124s, 400px -> ~0.247s, 1500px -> ~0.425s
    nominal = 0.057 + 0.0095 * math.sqrt(dist)
    if base_duration is not None and base_duration > 0:
        dur = nominal * (base_duration / 0.25)
    else:
        dur = nominal

    return max(0.04, round(dur, 3))


def human_move_to(x: int, y: int, duration: float | None = None) -> None:
    """Move the mouse cursor to (x, y) along a smooth, human-like Bezier arc."""
    tx = int(round(x))
    ty = int(round(y))

    if not getattr(config, "SMOOTH_MOUSE_ENABLED", True):
        pyautogui.moveTo(tx, ty)
        return

    cur_pos = pyautogui.position()
    start = (int(cur_pos[0]), int(cur_pos[1]))
    target = (tx, ty)

    if start == target:
        return

    base_dur = duration if duration is not None else getattr(config, "SMOOTH_MOUSE_DURATION", 0.28)
    total_duration = compute_move_duration(start, target, base_duration=base_dur)

    points = bezier_curve_points(start, target, steps=24)
    if not points:
        pyautogui.moveTo(tx, ty)
        return

    step_dt = total_duration / float(len(points))
    for px, py in points:
        pyautogui.moveTo(px, py, _pause=False)
        time.sleep(step_dt)

    pyautogui.moveTo(tx, ty)


def human_click(
    x: int,
    y: int,
    clicks: int = 1,
    button: str = "left",
    label: str = "",
) -> None:
    """Move smoothly to (x, y), trigger overlay pulse, and execute mouse click."""
    tx = int(round(x))
    ty = int(round(y))

    # Best-effort pulse on companion overlay
    try:
        import tools.overlay
        if hasattr(tools.overlay, "pulse_at"):
            tools.overlay.pulse_at(tx, ty, label=label)
    except Exception:
        pass

    if getattr(config, "SMOOTH_MOUSE_ENABLED", True):
        human_move_to(tx, ty)

    pyautogui.click(tx, ty, clicks=max(1, int(clicks)), button=button)


def should_use_cadence_typing(text: str) -> bool:
    """Determine whether text should be typed character-by-character."""
    if not getattr(config, "HUMAN_TYPING_ENABLED", True):
        return False
    if not text:
        return False
    max_chars = getattr(config, "HUMAN_TYPING_MAX_CHARS", 80)
    if len(text) > max_chars:
        return False
    if "\n" in text or "\r" in text:
        return False
    if not text.isascii():
        return False
    return True


def human_type(text: str, press_enter: bool = False) -> str:
    """Type text either via human-like cadence typing or clipboard paste.

    Returns 'cadence' or 'clipboard' indicating which method was used.
    """
    text_str = str(text) if text is not None else ""
    if should_use_cadence_typing(text_str):
        method = "cadence"
        for ch in text_str:
            pyautogui.write(ch, _pause=False)
            delay = random.uniform(0.020, 0.038)
            if ch in " .,!?;:":
                delay += 0.04
            time.sleep(delay)
    else:
        method = "clipboard"
        pyperclip.copy(text_str)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")

    if press_enter:
        time.sleep(0.18)
        pyautogui.press("enter")

    return method
