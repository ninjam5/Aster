"""Locator eval harness — measure the GUI locator tracks on YOUR screens.

This is how locator-tuning decisions get made with data instead of launch-blog
claims: Direct Qwen 3.6 Vision Grounding hit rates and latency.

Usage (from the repo root):

  python engine_testing/locator_eval.py capture <name>
      Screenshot the primary monitor into engine_testing/locator_cases/<name>.png.
      Then add one or more entries for it to cases.json (see below).

  python engine_testing/locator_eval.py run
      Score Direct Qwen 3.6 Vision Grounding on every labeled case.

  python engine_testing/locator_eval.py live "<goal>"
      Run the FULL locate (including UIA, which needs the live
      tree and can't run on stored images) against the current screen.
      Moves the mouse to the match as visual proof — never clicks.

Cases file: engine_testing/locator_cases/cases.json
  [
    {"image": "spotify.png", "goal": "play button", "expected_box": [860, 940, 60, 60]},
    ...
  ]
  expected_box is [left, top, width, height] in the image's own pixel space.
  A track scores a hit when its returned point falls inside the box.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locator_cases")
CASES_FILE = os.path.join(CASES_DIR, "cases.json")


def _load_cases() -> list[dict]:
    if not os.path.exists(CASES_FILE):
        print(f"No cases file at {CASES_FILE}.")
        print('Create it with entries like: [{"image": "x.png", "goal": "play button", "expected_box": [l, t, w, h]}]')
        return []
    with open(CASES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def cmd_capture(name: str) -> None:
    import cv2
    import numpy as np
    import mss as mss_mod

    os.makedirs(CASES_DIR, exist_ok=True)
    with mss_mod.mss() as sct:
        raw = sct.grab(sct.monitors[1])
        frame = np.array(raw)[:, :, :3]
    path = os.path.join(CASES_DIR, f"{name}.png")
    cv2.imwrite(path, frame)
    print(f"Saved {path} ({frame.shape[1]}x{frame.shape[0]}).")
    print(f"Now add labeled goals for it to {CASES_FILE}.")


def _point_in_box(pt: tuple, box: list) -> bool:
    l, t, w, h = box
    return l <= pt[0] <= l + w and t <= pt[1] <= t + h


def cmd_run(with_captions: bool = False, with_pixel: bool = True) -> None:
    import base64
    import cv2
    import pyautogui

    from tools.vision import _qwen_vision_locate

    cases = _load_cases()
    if not cases:
        return

    stats = {"qwen_vision": [0, 0, 0.0]}  # hits, total, seconds

    for case in cases:
        img_path = os.path.join(CASES_DIR, case["image"])
        frame = cv2.imread(img_path)
        if frame is None:
            print(f"SKIP {case['image']}: unreadable")
            continue
        h, w = frame.shape[:2]

        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not ok:
            print(f"SKIP {case['image']}: failed to encode JPEG")
            continue
        b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

        # Mock pyautogui.size to the image dimensions for offline eval so returned (x, y) is in image pixel space
        orig_size = pyautogui.size
        pyautogui.size = lambda: (w, h)
        try:
            t0 = time.time()
            r = _qwen_vision_locate(case["goal"], b64_img=b64)
            dt = time.time() - t0
        finally:
            pyautogui.size = orig_size

        stats["qwen_vision"][1] += 1
        stats["qwen_vision"][2] += dt
        hit = bool(r and _point_in_box((r["x"], r["y"]), case["expected_box"]))
        stats["qwen_vision"][0] += hit
        loc_str = f" -> ({r['x']},{r['y']})" if r else " -> None"
        print(f"{case['image']:24s} {case['goal']!r:36s} "
              f"Qwen: {'HIT ' if hit else 'miss'} ({dt:.2f}s){loc_str}")

    print("\n=== Results ===")
    hits, total, secs = stats["qwen_vision"]
    if total:
        print(f"Qwen Vision: {hits}/{total} hits ({100 * hits / total:.0f}%), "
              f"avg {secs / total:.2f}s per case")
    print("\nNote: UIA (Track 0) needs a live tree — use `live \"<goal>\"` to test it.")


def cmd_live(goal: str) -> None:
    import pyautogui
    from tools.vision import locate_ui_element_ex

    t0 = time.time()
    r = locate_ui_element_ex(goal)
    dt = time.time() - t0
    if r is None:
        print(f"No match for {goal!r} ({dt:.1f}s)")
        return
    print(f"Match: {r} ({dt:.1f}s)")
    pyautogui.moveTo(r["x"], r["y"], duration=0.4)  # point at it, never click
    print("Mouse moved to the match for visual confirmation (no click).")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
    elif args[0] == "capture" and len(args) > 1:
        cmd_capture(args[1])
    elif args[0] == "run":
        cmd_run("--captions" in args, with_pixel="--no-pixel" not in args)
    elif args[0] == "live" and len(args) > 1:
        cmd_live(args[1])
    else:
        print(__doc__)
