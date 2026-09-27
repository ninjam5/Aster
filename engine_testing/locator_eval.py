"""Locator eval harness — measure the GUI locator tracks on YOUR screens.

This is how locator-tuning decisions get made with data instead of launch-blog
claims: OCR vs OmniParser/YOLO hit rates, captioner choice, and the Stage-2
`--no-pixel` A/B that measures coverage lost when the pixel fallback is demoted.

Usage (from the repo root):

  python engine_testing/locator_eval.py capture <name>
      Screenshot the primary monitor into engine_testing/locator_cases/<name>.png.
      Then add one or more entries for it to cases.json (see below).

  python engine_testing/locator_eval.py run [--captions] [--no-pixel]
      Score Track 1 (OCR) and Track 2 (YOLO) on every labeled case.
      --captions also exercises the icon captioner (needs llama-server up).
      --no-pixel skips Track 2 entirely (the USE_PIXEL_FALLBACK=false A/B).

  python engine_testing/locator_eval.py live "<goal>"
      Run the FULL three-track locate (including UIA, which needs the live
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


def cmd_run(with_captions: bool, with_pixel: bool = True) -> None:
    import cv2

    from tools.locator_common import parse_goal
    from tools.vision import _extract_ocr_lines, _ocr_best_line, _yolo_track, _release_omniparser
    import config

    if not with_captions:
        config.ICON_CAPTIONER = "off"  # don't require llama-server for the base run

    cases = _load_cases()
    if not cases:
        return

    stats = {"ocr": [0, 0, 0.0]}  # hits, total, seconds
    if with_pixel:
        stats["yolo"] = [0, 0, 0.0]
    ocr_lines_cache: dict[str, tuple] = {}

    for case in cases:
        img_path = os.path.join(CASES_DIR, case["image"])
        frame = cv2.imread(img_path)
        if frame is None:
            print(f"SKIP {case['image']}: unreadable")
            continue
        h, w = frame.shape[:2]
        parsed = parse_goal(case["goal"])

        # Track 1 — OCR (lines cached per image across its cases)
        if case["image"] not in ocr_lines_cache:
            t0 = time.time()
            lines = _extract_ocr_lines(frame, w, h)
            ocr_lines_cache[case["image"]] = (lines, time.time() - t0)
        lines, ocr_dt = ocr_lines_cache[case["image"]]

        t0 = time.time()
        r = _ocr_best_line(lines, parsed, w, h, 1.0, 1.0)
        dt = (time.time() - t0) + ocr_dt / max(1, sum(1 for c in cases if c["image"] == case["image"]))
        stats["ocr"][1] += 1
        stats["ocr"][2] += dt
        ocr_hit = bool(r and _point_in_box((r["x"], r["y"]), case["expected_box"]))
        stats["ocr"][0] += ocr_hit

        # Track 2 — YOLO (+ optional captions); skipped entirely with --no-pixel
        if with_pixel:
            t0 = time.time()
            r2 = _yolo_track(frame, parsed, w, h, 1.0, 1.0, lines)
            dt2 = time.time() - t0
            stats["yolo"][1] += 1
            stats["yolo"][2] += dt2
            yolo_hit = bool(r2 and _point_in_box((r2["x"], r2["y"]), case["expected_box"]))
            stats["yolo"][0] += yolo_hit
            print(f"{case['image']:24s} {case['goal']!r:36s} "
                  f"OCR: {'HIT ' if ocr_hit else 'miss'} ({dt:.1f}s)  "
                  f"YOLO: {'HIT ' if yolo_hit else 'miss'} ({dt2:.1f}s)")
        else:
            print(f"{case['image']:24s} {case['goal']!r:36s} "
                  f"OCR: {'HIT ' if ocr_hit else 'miss'} ({dt:.1f}s)  "
                  f"YOLO: skipped (--no-pixel)")

    if with_pixel:
        _release_omniparser()
    print("\n=== Results ===")
    for track, (hits, total, secs) in stats.items():
        if total:
            print(f"{track:6s}: {hits}/{total} hits ({100 * hits / total:.0f}%), "
                  f"avg {secs / total:.1f}s per case")
    print("\nNote: UIA (Track 0) needs a live tree — use `live \"<goal>\"` to test it.")
    if not with_captions:
        print("Captioner was OFF; re-run with --captions (llama-server up) to score icon captioning.")


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
