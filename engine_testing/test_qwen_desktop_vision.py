"""Qwen 3.6 35B Desktop Vision & Spatial Grounding Test Harness.

Tests Qwen 3.6 35B-A3B's native visual grounding on real Windows desktop screenshots
via PolyStrata (http://localhost:8080/v1/chat/completions).

Features:
- Tests pure vision (no YOLO, no Tesseract OCR, no UIA).
- Measures coordinate accuracy, JSON output adherence, and inference latency.
- Converts normalized [0..1000] coordinates to physical & logical screen pixels.
- Saves visual verification images with annotated crosshairs and bounding boxes.
- Supports non-destructive mouse hovering (--move-mouse) to visually verify accuracy.

Usage:
  python engine_testing/test_qwen_desktop_vision.py target "<goal>" [--move-mouse] [--prompt-style point|box|action]
  python engine_testing/test_qwen_desktop_vision.py prompt-eval "<goal>"
  python engine_testing/test_qwen_desktop_vision.py suite [--move-mouse]
"""
import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pyautogui
import requests

API_URL = os.getenv("ASTER_LLM_URL", "http://localhost:8080/v1/chat/completions")
HEALTH_URL = os.getenv("ASTER_HEALTH_URL", "http://localhost:8080/health")
OUTPUT_DIR = os.path.join("Aster_Vault", "vision_tests")


def check_engine_health() -> bool:
    """Checks if PolyStrata / llama-server is healthy and ready."""
    try:
        r = requests.get(HEALTH_URL, timeout=1.5)
        if r.status_code == 200:
            return True
    except Exception:
        pass
    try:
        # Fallback check against /v1/models
        r = requests.get("http://localhost:8080/v1/models", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def capture_desktop(image_path: str | None = None) -> tuple[np.ndarray, str, int, int, int, int]:
    """Captures the primary monitor, or loads an existing screenshot if image_path is provided.

    Returns:
        frame_bgr: OpenCV BGR image
        b64_str: Base64 JPEG string
        phys_w: Physical pixel width
        phys_h: Physical pixel height
        log_w: Logical pixel width (pyautogui)
        log_h: Logical pixel height (pyautogui)
    """
    if image_path:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")
        frame_bgr = cv2.imread(image_path)
        if frame_bgr is None:
            raise ValueError(f"Could not read image from {image_path}")
    else:
        with mss.mss() as sct:
            if len(sct.monitors) < 2:
                raise RuntimeError("No monitor detected by mss.")
            monitor = sct.monitors[1]  # primary
            raw = sct.grab(monitor)
            frame_bgra = np.array(raw)
            frame_bgr = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)

    phys_h, phys_w = frame_bgr.shape[:2]
    log_w, log_h = pyautogui.size()

    # Encode to JPEG for LLM vision input (high quality)
    ok, buffer = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise RuntimeError("Failed to encode screen capture to JPEG.")
    b64_str = base64.b64encode(buffer).decode("utf-8")

    return frame_bgr, b64_str, phys_w, phys_h, log_w, log_h


def build_grounding_prompt(goal: str, style: str = "point") -> str:
    """Builds the spatial grounding prompt based on the chosen format."""
    if style == "point":
        return (
            f"You are a computer vision UI grounding model.\n"
            f"Look at the screenshot and find the exact UI element: \"{goal}\".\n"
            f"Return ONLY a JSON object with the center coordinate normalized to a 1000x1000 grid:\n"
            f'{{"target": "{goal}", "point": [x, y]}}\n'
            f"Where x is the horizontal coordinate (0-1000, left to right) and y is the vertical coordinate (0-1000, top to bottom).\n"
            f"Do not include markdown or reasoning outside the JSON."
        )
    elif style == "box":
        return (
            f"You are a computer vision UI grounding model.\n"
            f"Look at the screenshot and locate the exact UI element: \"{goal}\".\n"
            f"Return ONLY a JSON object with the bounding box normalized to a 1000x1000 grid:\n"
            f'{{"target": "{goal}", "box_2d": [xmin, ymin, xmax, ymax]}}\n'
            f"All values must be integers between 0 and 1000.\n"
            f"Do not include markdown or reasoning outside the JSON."
        )
    elif style == "action":
        return (
            f"You are an operating system computer use agent.\n"
            f"Goal: Click on \"{goal}\".\n"
            f"Analyze the screen and generate the computer use action to click the center of the target element.\n"
            f"Coordinates are normalized to a 1000x1000 grid (0-1000).\n"
            f"Output format:\n"
            f'<tool_call>\n'
            f'{{"name": "computer_use", "arguments": {{"action": "click", "coordinate": [x, y]}}}}\n'
            f'</tool_call>'
        )
    else:
        raise ValueError(f"Unknown prompt style: {style}")


def query_qwen_grounding(b64_img: str, prompt: str, temperature: float = 0.1) -> tuple[str, float]:
    """Queries PolyStrata with image + prompt and returns (response_text, latency_sec)."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
            {"type": "text", "text": prompt}
        ]
    }]

    payload = {
        "model": "local",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2000,
        "stream": False
    }

    t0 = time.monotonic()
    resp = requests.post(API_URL, json=payload, timeout=60)
    latency = time.monotonic() - t0
    resp.raise_for_status()

    msg = resp.json()["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()

    # If content is empty (e.g., thinking exhausted tokens or placed in reasoning), use reasoning
    if not content and reasoning:
        content = reasoning
    elif not content and "tool_calls" in msg:
        content = json.dumps(msg["tool_calls"])

    return content, latency


def parse_grounding_coordinates(raw_text: str) -> tuple[int, int, list[int] | None]:
    """Extracts normalized [x, y] center (0-1000) and optional bounding box [xmin, ymin, xmax, ymax].

    Supports:
    - {"point": [x, y]}
    - {"coordinate": [x, y]}
    - {"box_2d": [xmin, ymin, xmax, ymax]}
    - <|box_start|>(xmin, ymin), (xmax, ymax)<|box_end|>
    - [x, y] or [xmin, ymin, xmax, ymax] lists
    """
    # 1. Check for standard JSON with "point", "point_2d", or "coordinate"
    match_point = re.search(r'["\'](?:point|point_2d|coordinate)["\']\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', raw_text)
    if match_point:
        x, y = int(match_point.group(1)), int(match_point.group(2))
        return x, y, None

    # 2. Check for "box_2d" or "bbox_2d" in JSON
    match_box = re.search(r'["\'](?:box_2d|bbox_2d|box)["\']\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', raw_text)
    if match_box:
        xmin, ymin, xmax, ymax = map(int, match_box.groups())
        center_x = (xmin + xmax) // 2
        center_y = (ymin + ymax) // 2
        return center_x, center_y, [xmin, ymin, xmax, ymax]

    # 3. Check for Qwen native box tags: <|box_start|>(xmin, ymin), (xmax, ymax)<|box_end|>
    match_tag = re.search(r'<\|box_start\|>\s*\((\d+)\s*,\s*(\d+)\)\s*,\s*\((\d+)\s*,\s*(\d+)\)\s*<\|box_end\|>', raw_text)
    if match_tag:
        xmin, ymin, xmax, ymax = map(int, match_tag.groups())
        center_x = (xmin + xmax) // 2
        center_y = (ymin + ymax) // 2
        return center_x, center_y, [xmin, ymin, xmax, ymax]

    # 4. Check for tool_call format: "arguments": {"action": "click", "coordinate": [x, y]}
    match_arg = re.search(r'["\']coordinate["\']\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', raw_text)
    if match_arg:
        return int(match_arg.group(1)), int(match_arg.group(2)), None

    # 5. Check for 4-element integer list in text: [xmin, ymin, xmax, ymax]
    match_4list = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', raw_text)
    if match_4list:
        xmin, ymin, xmax, ymax = map(int, match_4list.groups())
        center_x = (xmin + xmax) // 2
        center_y = (ymin + ymax) // 2
        return center_x, center_y, [xmin, ymin, xmax, ymax]

    # 6. Check for 2-element list: [x, y]
    match_2list = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*\]', raw_text)
    if match_2list:
        return int(match_2list.group(1)), int(match_2list.group(2)), None

    raise ValueError(f"Could not parse any coordinates from LLM output:\n{raw_text}")


def annotate_and_save(
    frame_bgr: np.ndarray,
    goal: str,
    x_norm: int,
    y_norm: int,
    box_norm: list[int] | None,
    latency: float,
    raw_text: str,
    phys_w: int,
    phys_h: int,
    log_w: int,
    log_h: int
) -> str:
    """Draws crosshairs, bounding box, and metrics on image and saves to disk."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil_img)

    # Physical coordinates in image
    phys_x = int((x_norm / 1000.0) * phys_w)
    phys_y = int((y_norm / 1000.0) * phys_h)

    # Logical coordinates for OS mouse
    log_x = int((x_norm / 1000.0) * log_w)
    log_y = int((y_norm / 1000.0) * log_h)

    # Draw bounding box if present
    if box_norm:
        xmin, ymin, xmax, ymax = box_norm
        b_x1 = int((xmin / 1000.0) * phys_w)
        b_y1 = int((ymin / 1000.0) * phys_h)
        b_x2 = int((xmax / 1000.0) * phys_w)
        b_y2 = int((ymax / 1000.0) * phys_h)
        draw.rectangle([b_x1, b_y1, b_x2, b_y2], outline="cyan", width=3)

    # Draw target crosshair
    r = 18
    # Outer circle
    draw.ellipse([phys_x - r, phys_y - r, phys_x + r, phys_y + r], outline="red", width=3)
    # Inner dot
    draw.ellipse([phys_x - 3, phys_y - 3, phys_x + 3, phys_y + 3], fill="red")
    # Cross lines
    draw.line([phys_x - r - 8, phys_y, phys_x + r + 8, phys_y], fill="red", width=2)
    draw.line([phys_x, phys_y - r - 8, phys_x, phys_y + r + 8], fill="red", width=2)

    # Label box
    label = f"Target: '{goal}' | Norm: ({x_norm}, {y_norm}) | LogPx: ({log_x}, {log_y}) | Latency: {latency:.2f}s"
    text_bbox = draw.textbbox((phys_x + 22, phys_y - 25), label)
    # Background rectangle for text readability
    draw.rectangle([text_bbox[0] - 4, text_bbox[1] - 4, text_bbox[2] + 4, text_bbox[3] + 4], fill=(15, 23, 42))
    draw.text((phys_x + 22, phys_y - 25), label, fill=(255, 255, 255))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_goal = re.sub(r'[^a-zA-Z0-9_-]', '_', goal)[:20]
    filename = f"qwen_grounding_{clean_goal}_{timestamp}.jpg"
    out_path = os.path.join(OUTPUT_DIR, filename)

    pil_img.save(out_path, quality=92)
    return out_path


def run_target_test(goal: str, style: str = "point", move_mouse: bool = False, image_path: str | None = None) -> dict:
    """Runs a single grounding test for a target element."""
    print(f"\n=======================================================")
    print(f"[Qwen Desktop Vision Test] Target: '{goal}' (style: {style})")
    if image_path:
        print(f"Source Image: {image_path}")
    print(f"=======================================================")

    if not check_engine_health():
        print(f"\n[ERROR] PolyStrata engine is NOT reachable at {HEALTH_URL} or :8080.")
        print(f"Please launch PolyStrata first using Start-all.bat or qwen3.6_polystrata_start.bat.")
        return {"success": False, "error": "Engine not reachable"}

    print("[1/4] Preparing image...")
    frame_bgr, b64_str, phys_w, phys_h, log_w, log_h = capture_desktop(image_path=image_path)
    print(f"      Resolution: Physical = {phys_w}x{phys_h} | Logical = {log_w}x{log_h}")

    print(f"[2/4] Querying Qwen 3.6 35B-A3B via PolyStrata...")
    prompt = build_grounding_prompt(goal, style=style)
    raw_response, latency = query_qwen_grounding(b64_str, prompt)
    print(f"      Latency: {latency:.2f}s")
    print(f"      Raw Model Response:\n---\n{raw_response}\n---")

    print("[3/4] Parsing predicted coordinates...")
    try:
        x_norm, y_norm, box_norm = parse_grounding_coordinates(raw_response)
    except Exception as e:
        print(f"[FAIL] Coordinate parsing failed: {e}")
        return {"success": False, "error": str(e), "raw": raw_response}

    phys_x = int((x_norm / 1000.0) * phys_w)
    phys_y = int((y_norm / 1000.0) * phys_h)
    log_x = int((x_norm / 1000.0) * log_w)
    log_y = int((y_norm / 1000.0) * log_h)

    print(f"      [OK] Normalized: x={x_norm}, y={y_norm} (0..1000)")
    print(f"      [OK] Physical:   x={phys_x}, y={phys_y} px")
    print(f"      [OK] Logical:    x={log_x}, y={log_y} px (mouse target)")
    if box_norm:
        print(f"      [OK] Bounding Box: {box_norm}")

    print("[4/4] Annotating visual verification image...")
    out_path = annotate_and_save(
        frame_bgr, goal, x_norm, y_norm, box_norm, latency, raw_response,
        phys_w, phys_h, log_w, log_h
    )
    print(f"      Saved verification image to: {out_path}")

    if move_mouse:
        print(f"\n[Hover Test] Moving mouse pointer to ({log_x}, {log_y}) in 1 second...")
        time.sleep(1.0)
        pyautogui.moveTo(log_x, log_y, duration=0.8)
        print("      Mouse positioned at predicted target.")

    return {
        "success": True,
        "goal": goal,
        "normalized": [x_norm, y_norm],
        "logical": [log_x, log_y],
        "physical": [phys_x, phys_y],
        "box": box_norm,
        "latency": latency,
        "annotated_image": out_path
    }


def run_prompt_evaluation(goal: str, image_path: str | None = None):
    """Compares different prompt formats against Qwen 3.6 on the exact same screenshot."""
    print(f"\n=======================================================")
    print(f"[Prompt Style Evaluation] Comparing styles for: '{goal}'")
    if image_path:
        print(f"Source Image: {image_path}")
    print(f"=======================================================")

    if not check_engine_health():
        print(f"[ERROR] Engine not reachable at :8080.")
        return

    frame_bgr, b64_str, phys_w, phys_h, log_w, log_h = capture_desktop(image_path=image_path)
    styles = ["point", "box", "action"]
    results = {}

    for style in styles:
        print(f"\n--- Testing style: '{style}' ---")
        prompt = build_grounding_prompt(goal, style=style)
        try:
            raw_text, lat = query_qwen_grounding(b64_str, prompt)
            x_norm, y_norm, box_norm = parse_grounding_coordinates(raw_text)
            results[style] = {
                "ok": True,
                "latency": lat,
                "norm": (x_norm, y_norm),
                "box": box_norm,
                "raw": raw_text.replace("\n", " ")[:120]
            }
            print(f"Result: SUCCESS | Latency: {lat:.2f}s | Coord: ({x_norm}, {y_norm})")
        except Exception as e:
            results[style] = {"ok": False, "error": str(e)}
            print(f"Result: FAILED ({e})")

    print(f"\n================ SUMMARY ================")
    for style, res in results.items():
        status = "PASS" if res.get("ok") else "FAIL"
        lat = f"{res.get('latency', 0):.2f}s" if res.get("ok") else "N/A"
        coord = str(res.get("norm", "N/A"))
        print(f"Style: {style:<8} | Status: {status:<4} | Latency: {lat:<6} | Coord: {coord}")


def run_suite(move_mouse: bool = False, image_path: str | None = None):
    """Runs a standard suite of generic UI targets."""
    suite_targets = [
        "Windows Start button on the taskbar",
        "The system clock / date in the bottom right corner",
        "The active window close button (X)",
        "The browser or main window address/search bar",
        "Desktop taskbar search icon"
    ]
    print(f"Starting generic test suite ({len(suite_targets)} targets)...")
    results = []
    for target in suite_targets:
        res = run_target_test(target, style="point", move_mouse=move_mouse, image_path=image_path)
        results.append(res)
        time.sleep(1.0)

    successes = sum(1 for r in results if r.get("success"))
    print(f"\n=======================================================")
    print(f"Suite Finished: {successes}/{len(suite_targets)} succeeded.")
    print(f"Visual artifacts saved to: {OUTPUT_DIR}/")
    print(f"=======================================================")


def main():
    parser = argparse.ArgumentParser(description="Test Qwen 3.6 Desktop Vision Grounding")
    subparsers = parser.add_subparsers(dest="command")

    # Command: target
    target_parser = subparsers.add_parser("target", help="Test grounding on a specific target")
    target_parser.add_argument("goal", type=str, help="Name or description of UI element to locate")
    target_parser.add_argument("--prompt-style", choices=["point", "box", "action"], default="point")
    target_parser.add_argument("--move-mouse", action="store_true", help="Move cursor to predicted target (no click)")
    target_parser.add_argument("--image", type=str, default=None, help="Path to image file (default: live screenshot)")

    # Command: prompt-eval
    eval_parser = subparsers.add_parser("prompt-eval", help="Compare prompt styles on the same screen")
    eval_parser.add_argument("goal", type=str, help="Target UI element")
    eval_parser.add_argument("--image", type=str, default=None, help="Path to image file (default: live screenshot)")

    # Command: suite
    suite_parser = subparsers.add_parser("suite", help="Run 5 generic desktop tasks")
    suite_parser.add_argument("--move-mouse", action="store_true", help="Move cursor to each match")
    suite_parser.add_argument("--image", type=str, default=None, help="Path to image file (default: live screenshot)")

    args = parser.parse_args()

    if args.command == "target":
        run_target_test(args.goal, style=args.prompt_style, move_mouse=args.move_mouse, image_path=args.image)
    elif args.command == "prompt-eval":
        run_prompt_evaluation(args.goal, image_path=args.image)
    elif args.command == "suite":
        run_suite(move_mouse=args.move_mouse, image_path=args.image)
        run_suite(move_mouse=args.move_mouse)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
