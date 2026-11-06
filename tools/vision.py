import base64
import cv2
import face_recognition
import json
import os
import re
import threading
import time
import importlib
from typing import Any

import numpy as np
import pyautogui

import config

try:
    mss = importlib.import_module("mss")
except Exception:
    mss = None


# --- FACIAL RECOGNITION INITIALIZATION ---
KNOWN_FACES_DIR = config.FACES_DIR
known_face_encodings = []
known_face_names = []


def load_known_faces():
    print("[Aster Internal: Booting Facial Recognition Engine...]")
    if not os.path.exists(KNOWN_FACES_DIR):
        os.makedirs(KNOWN_FACES_DIR)
        print("[Aster Internal: Faces vault created. Add images to enable recognition.]")
        return

    for filename in os.listdir(KNOWN_FACES_DIR):
        if filename.lower().endswith((".png", ".jpg", ".jpeg")):
            filepath = os.path.join(KNOWN_FACES_DIR, filename)
            name = os.path.splitext(filename)[0]

            try:
                image = face_recognition.load_image_file(filepath)
                encodings = face_recognition.face_encodings(image)
                if encodings:
                    known_face_encodings.append(encodings[0])
                    known_face_names.append(name)
                    print(f"  -> Learned face: {name}")
            except Exception as e:
                print(f"[Aster Internal: Failed to load face {filename} - {e}]")


# Run this once when the module imports.
load_known_faces()


def capture_webcam_base64(camera_index=0):
    """Captures a frame, identifies known faces, and returns (base64_string, detected_names)."""
    try:
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            return "Error: Could not access the webcam.", []

        for _ in range(5):
            cap.read()

        ret, frame = cap.read()
        cap.release()

        if not ret:
            return "Error: Failed to read frame from webcam.", []

        # Convert BGR (OpenCV) to RGB (face_recognition)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        detected_names = []
        face_locations = face_recognition.face_locations(rgb_frame)
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

        for encoding in face_encodings:
            matches = face_recognition.compare_faces(known_face_encodings, encoding, tolerance=0.5)
            name = "Unknown Person"

            if True in matches:
                first_match_index = matches.index(True)
                name = known_face_names[first_match_index]

            detected_names.append(name)

        _, buffer = cv2.imencode(".jpg", frame)
        img_base64 = base64.b64encode(buffer).decode("utf-8")

        return img_base64, detected_names

    except Exception as e:
        return f"Error: Webcam capture failed - {str(e)}", []


# ============================================================================
# NATIVE MULTIMODAL VISION — direct mmproj injection
# ============================================================================

def capture_frame_base64(camera_index=0) -> str | None:
    """Captures a webcam frame and returns raw base64 for multimodal injection.

    Unlike capture_webcam_base64(), this does NOT run face_recognition or any
    detection — the raw frame is sent directly to the model's mmproj for analysis.
    """
    try:
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            return None
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None
        _, buffer = cv2.imencode(".jpg", frame)
        return base64.b64encode(buffer).decode("utf-8")
    except Exception:
        return None


def _downscale_bgr(frame_bgr, max_width: int):
    """Shrink a BGR frame so its width is at most max_width (aspect preserved).

    The vision encoder tiles/downsamples internally, so detail above ~1280px is
    largely discarded anyway — capping here removes encode cost for free.
    max_width <= 0 disables the resize.
    """
    if max_width and frame_bgr.shape[1] > max_width:
        scale = max_width / frame_bgr.shape[1]
        frame_bgr = cv2.resize(
            frame_bgr,
            (max_width, max(1, int(frame_bgr.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return frame_bgr


def capture_screen_base64(max_width: int | None = None) -> str | None:
    """Captures the primary monitor and returns raw base64 for multimodal injection.

    The raw screenshot is sent directly to the mmproj.
    Downscales to `config.SCREENSHOT_MAX_WIDTH` (default 1280) before JPEG encoding;
    pass `max_width=0` to keep the full physical resolution (e.g. a user-facing
    screenshot artifact rather than an LLM input).
    """
    if mss is None:
        return None
    if max_width is None:
        max_width = getattr(config, "SCREENSHOT_MAX_WIDTH", 1280)
    try:
        with mss.mss() as sct:
            if len(sct.monitors) < 2:
                return None
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)
        frame_bgr = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        frame_bgr = _downscale_bgr(frame_bgr, max_width)
        ok, buffer = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return None
        return base64.b64encode(buffer).decode("utf-8")
    except Exception:
        return None


# ============================================================================
# COLD STORAGE VISION — Archive images before VRAM flush
# ============================================================================
COLD_STORAGE_DIR = os.path.join("Aster_Vault", "images")


def save_image_to_cold_storage(base64_data: str, prefix: str = "img") -> str | None:
    """Decodes a Base64 image string and saves it as a JPEG in the cold storage vault.

    Returns the relative file path on success, or None on failure.
    """
    if not base64_data:
        return None

    try:
        os.makedirs(COLD_STORAGE_DIR, exist_ok=True)

        import time
        timestamp = int(time.time() * 1000)
        filename = f"{prefix}_{timestamp}.jpg"
        filepath = os.path.join(COLD_STORAGE_DIR, filename)

        image_bytes = base64.b64decode(base64_data)

        # Validate it's a decodable image before writing
        np_arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img is None:
            print(f"[Aster Internal: Cold storage save failed — invalid image data]")
            return None

        cv2.imwrite(filepath, img)
        print(f"[Aster Internal: Image archived to cold storage: {filepath}]")

        # Prune oldest files beyond a cap to prevent unbounded disk growth
        _prune_cold_storage(COLD_STORAGE_DIR, max_files=100)

        return filepath
    except Exception as e:
        print(f"[Aster Internal: Cold storage save error: {e}]")
        return None


def _prune_cold_storage(directory: str, max_files: int = 100) -> None:
    try:
        files = [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, f))
        ]
        if len(files) <= max_files:
            return
        files.sort(key=os.path.getmtime)
        for old in files[:len(files) - max_files]:
            os.remove(old)
            print(f"[Aster Internal: Pruned old cold-storage image: {old}]")
    except Exception as e:
        print(f"[Aster Internal: Cold storage prune error: {e}]")


def re_examine_image(filepath: str, specific_question: str) -> str:
    """One-shot visual inference on a cold-storage image.

    Loads the image from disk, converts it to Base64, sends it to the LLM
    with the user's specific question in a stateless 1-turn context, and
    returns the text answer. The tensor is never persisted in the main
    conversation history — VRAM stays clean.
    """
    if not filepath or not os.path.exists(filepath):
        return f"Error: Image file not found at '{filepath}'."

    if not specific_question or not specific_question.strip():
        return "Error: re_examine_image requires a specific_question about the image."

    try:
        import config as _cfg

        # Read image and convert to Base64
        img = cv2.imread(filepath)
        if img is None:
            return f"Error: Could not decode image at '{filepath}' — file may be corrupted."

        _, buffer = cv2.imencode(".jpg", img)
        raw_b64 = base64.b64encode(buffer).decode("utf-8")

        # Sanitize Base64: strip data URI prefixes, newlines, and whitespace
        # that cause tokenization overflow in the C++ server parser
        clean_b64 = raw_b64.split(",", 1)[-1] if "," in raw_b64 else raw_b64
        clean_b64 = clean_b64.replace("\n", "").replace("\r", "").strip()

        # Stateless 1-turn inference — image never enters main conversation
        one_shot_msg = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean_b64}"}},
                    {"type": "text", "text": specific_question.strip()},
                ],
            }
        ]

        from core.brain import _execute_llm_completion
        answer = _execute_llm_completion(messages=one_shot_msg, temperature=0.2).get("content") or ""

        if not answer:
            return "Error: Visual inference returned an empty response."

        return answer

    except Exception as e:
        return f"Error: Visual re-examination failed — {e}"


def start_screen_watcher(target_text: str) -> str:
    """Spins up a background daemon to monitor the screen for specific text."""
    import time

    def watcher_worker():
        import config  # Imported locally to prevent circular dependencies
        target_lower = target_text.lower()

        # Loop every 10 seconds, max 1 hour (360 iterations)
        for _ in range(360):
            time.sleep(10)
            try:
                b64 = capture_screen_base64()
                if not b64:
                    continue

                screen_msgs = [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": "Extract all visible text from this screen."}
                    ]
                }]
                from core.brain import _execute_llm_completion
                screen_data = _execute_llm_completion(messages=screen_msgs, temperature=0.1).get("content") or ""

                if not screen_data:
                    continue

                if target_lower in screen_data.lower():
                    msg = f"\U0001f441\ufe0f [Aster Sentry] Screen Watcher Alert: Target '{target_text}' detected on screen."
                    if config.bot and config.AUTHORIZED_CHAT_ID:
                        config.bot.send_message(config.AUTHORIZED_CHAT_ID, msg)

                    # --- MEMORY INJECTION ---
                    try:
                        # QA round 3: route through the single write choke point. This
                        # used to append straight to memory.md, bypassing the Phase-2
                        # gate and the ChromaDB dual-write (so BM25 saw lines that
                        # dense recall did not). The gate classifies this action log as
                        # junk and skips it — which is the intended filtering.
                        from core.memory import memorize_fact
                        memorize_fact(
                            f"SYSTEM EVENT: The Screen Watcher daemon detected "
                            f"'{target_text}' on the screen and alerted {config.OWNER_NAME}.",
                            source="vision",
                        )
                    except Exception as mem_e:
                        print(f"[Aster Sentry] Memory injection failed: {mem_e}")
                    # ------------------------

                    break  # Kill thread upon success
            except Exception as e:
                print(f"[Aster Sentry] Screen Watcher Error: {e}")
                break

    # Spin up the daemon thread
    watcher_thread = threading.Thread(target=watcher_worker, daemon=True)
    watcher_thread.start()
    return f"[System Note: Screen watcher daemon successfully started. Monitoring every 10 seconds for '{target_text}'. You will be notified via Telegram when it appears.]"


# ============================================================================
# SCREEN CACHE BACKWARD COMPATIBILITY
# ============================================================================
def invalidate_screen_cache() -> None:
    """No-op kept for caller/test backward compatibility."""
    pass


def capture_screen_small_gray(width: int = 240) -> np.ndarray | None:
    """Tiny grayscale snapshot of the primary monitor, for before/after
    action diffing. Direct mss grab (no JPEG round-trip, no cursor)."""
    if mss is None:
        return None
    try:
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[1])
            frame = np.array(raw)[:, :, :3]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h = max(1, int(gray.shape[0] * width / gray.shape[1]))
        return cv2.resize(gray, (width, h), interpolation=cv2.INTER_AREA)
    except Exception:
        return None


def screens_differ(before: np.ndarray | None, after: np.ndarray | None,
                   block: int = 8, threshold: float = 12.0) -> bool:
    """Blockwise change detector for post-action verification.

    Splits the small grayscale frames into block×block cells and flags a
    change when ANY cell's mean absolute difference exceeds `threshold` —
    a whole-image mean would dilute a toggled checkbox to nothing. Errs
    toward True (missing/odd frames count as "changed") so callers only
    warn on a *confident* no-change."""
    if before is None or after is None or before.shape != after.shape:
        return True
    diff = cv2.absdiff(before, after).astype(np.float32)
    h, w = diff.shape
    bh, bw = h // block, w // block
    if bh == 0 or bw == 0:
        return bool(diff.mean() > threshold)
    blocks = diff[: bh * block, : bw * block].reshape(bh, block, bw, block).mean(axis=(1, 3))
    return bool(blocks.max() > threshold)


# ============================================================================
# DIRECT QWEN 3.6 VISION GROUNDING
# ============================================================================

def _parse_qwen_grounding(raw_text: str) -> tuple[int, int, list[int] | None] | None:
    """Parse normalized 0..1000 coordinates from Qwen vision output.

    Supports:
      - JSON with 'point', 'point_2d', or 'coordinate': [x, y] -> (x, y, None)
      - JSON with 'box_2d': [ymin, xmin, ymax, xmax] -> center ((xmin+xmax)//2, (ymin+ymax)//2, box)
      - Native Qwen box tag: <|box_start|>(ymin, xmin), (ymax, xmax)<|box_end|>
      - Standalone 4-element [ymin, xmin, ymax, xmax] or 2-element [x, y] list.

    Returns (center_x, center_y, box_coords) or None if not found or out of bounds.
    """
    if not raw_text or not raw_text.strip():
        return None

    lowered = raw_text.lower()
    if ('"found": false' in lowered or '"found":false' in lowered or
            "'found': false" in lowered or "'found':false" in lowered):
        return None
    if re.search(r'["\']found["\']\s*:\s*false', raw_text, re.IGNORECASE):
        return None

    def _extract_from_obj(obj: Any) -> tuple[int, int, list[int] | None] | None:
        if isinstance(obj, dict):
            if obj.get("found") is False or str(obj.get("found")).lower() == "false":
                return None
            for key in ("point", "point_2d", "coordinate"):
                val = obj.get(key)
                if isinstance(val, (list, tuple)) and len(val) >= 2:
                    try:
                        x, y = int(val[0]), int(val[1])
                        if 0 <= x <= 1000 and 0 <= y <= 1000:
                            return (x, y, None)
                    except (ValueError, TypeError):
                        pass
                elif isinstance(val, dict) and "x" in val and "y" in val:
                    try:
                        x, y = int(val["x"]), int(val["y"])
                        if 0 <= x <= 1000 and 0 <= y <= 1000:
                            return (x, y, None)
                    except (ValueError, TypeError):
                        pass
            if "box_2d" in obj:
                val = obj["box_2d"]
                if isinstance(val, (list, tuple)) and len(val) >= 4:
                    try:
                        ymin, xmin, ymax, xmax = [int(v) for v in val[:4]]
                        if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                                0 <= ymin <= 1000 and 0 <= ymax <= 1000):
                            cx = (xmin + xmax) // 2
                            cy = (ymin + ymax) // 2
                            if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                                return (cx, cy, [ymin, xmin, ymax, xmax])
                    except (ValueError, TypeError):
                        pass
            for sub_val in obj.values():
                if isinstance(sub_val, (dict, list, tuple)):
                    sub_res = _extract_from_obj(sub_val)
                    if sub_res is not None:
                        return sub_res
        elif isinstance(obj, (list, tuple)):
            if obj and isinstance(obj[0], (dict, list, tuple)):
                for item in obj:
                    sub_res = _extract_from_obj(item)
                    if sub_res is not None:
                        return sub_res
            elif len(obj) >= 4:
                try:
                    ymin, xmin, ymax, xmax = [int(v) for v in obj[:4]]
                    if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                            0 <= ymin <= 1000 and 0 <= ymax <= 1000):
                        cx = (xmin + xmax) // 2
                        cy = (ymin + ymax) // 2
                        if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                            return (cx, cy, [ymin, xmin, ymax, xmax])
                except (ValueError, TypeError):
                    pass
            elif len(obj) >= 2:
                try:
                    x, y = int(obj[0]), int(obj[1])
                    if 0 <= x <= 1000 and 0 <= y <= 1000:
                        return (x, y, None)
                except (ValueError, TypeError):
                    pass
        return None

    # 1. Clean markdown code fences if present
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)

    # 2. Try JSON parsing
    try:
        parsed = json.loads(clean)
        res = _extract_from_obj(parsed)
        if res is not None:
            return res
    except Exception:
        pass

    # 3. Look for JSON objects inside raw_text
    for m in re.finditer(r'\{[^{}]*\}', raw_text):
        try:
            parsed = json.loads(m.group(0))
            res = _extract_from_obj(parsed)
            if res is not None:
                return res
        except Exception:
            pass

    # 4. Check for Qwen box tag: <|box_start|>(ymin, xmin), (ymax, xmax)<|box_end|>
    box_tag_match = re.search(
        r'<\|box_start\|>\s*\(?\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\)?\s*,\s*\(?\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\)?\s*<\|box_end\|>',
        raw_text
    )
    if box_tag_match:
        ymin, xmin, ymax, xmax = [int(g) for g in box_tag_match.groups()]
        if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                0 <= ymin <= 1000 and 0 <= ymax <= 1000):
            cx = (xmin + xmax) // 2
            cy = (ymin + ymax) // 2
            if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                return (cx, cy, [ymin, xmin, ymax, xmax])

    # 5. Regex for key patterns if JSON was malformed
    pt_match = re.search(r'"(?:point|point_2d|coordinate)"\s*:\s*\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw_text)
    if pt_match:
        x, y = int(pt_match.group(1)), int(pt_match.group(2))
        if 0 <= x <= 1000 and 0 <= y <= 1000:
            return (x, y, None)

    box_key_match = re.search(r'"box_2d"\s*:\s*\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw_text)
    if box_key_match:
        ymin, xmin, ymax, xmax = [int(g) for g in box_key_match.groups()]
        if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                0 <= ymin <= 1000 and 0 <= ymax <= 1000):
            cx = (xmin + xmax) // 2
            cy = (ymin + ymax) // 2
            if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                return (cx, cy, [ymin, xmin, ymax, xmax])

    # 6. Standalone 4-element list: [ymin, xmin, ymax, xmax]
    list4_match = re.search(r'\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw_text)
    if list4_match:
        ymin, xmin, ymax, xmax = [int(g) for g in list4_match.groups()]
        if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                0 <= ymin <= 1000 and 0 <= ymax <= 1000):
            cx = (xmin + xmax) // 2
            cy = (ymin + ymax) // 2
            if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                return (cx, cy, [ymin, xmin, ymax, xmax])

    # 7. Standalone 2-element list: [x, y]
    list2_match = re.search(r'\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw_text)
    if list2_match:
        x, y = int(list2_match.group(1)), int(list2_match.group(2))
        if 0 <= x <= 1000 and 0 <= y <= 1000:
            return (x, y, None)

    return None


def _qwen_vision_locate(goal: str, b64_img: str | None = None) -> dict | None:
    """Locate a UI element via direct Qwen 3.6 vision grounding."""
    try:
        if b64_img is None:
            b64_img = capture_screen_base64()
        if not b64_img:
            return None

        prompt = (
            f'You are a computer vision UI grounding model.\n'
            f'Look at the screenshot and find the exact UI element: "{goal}".\n'
            f'If the element is NOT visible on the screen, return ONLY: {{"found": false}}\n'
            f'If visible, return ONLY a JSON object with the center coordinate normalized to a 1000x1000 grid:\n'
            f'{{"found": true, "target": "{goal}", "point": [x, y]}}\n'
            f'Where x is the horizontal coordinate (0-1000, left to right) and y is the vertical coordinate (0-1000, top to bottom).\n'
            f'Do not include markdown or reasoning outside the JSON.'
        )

        from core.brain import _execute_llm_completion
        resp = _execute_llm_completion(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            temperature=0.1,
            n_predict=1500,
        )

        raw = (resp.get("content") or "").strip()
        if not raw:
            raw = (resp.get("reasoning_content") or "").strip()

        parsed = _parse_qwen_grounding(raw)
        if parsed is None:
            return None

        x_norm, y_norm, _ = parsed
        logical_w, logical_h = pyautogui.size()
        lx = int((x_norm / 1000.0) * logical_w)
        ly = int((y_norm / 1000.0) * logical_h)
        print(f'[Locator] Qwen vision match: {goal!r} -> ({lx},{ly})')
        return {"x": lx, "y": ly, "source": "qwen-vision", "text": goal, "score": 0.9}
    except Exception as e:
        print(f'[Locator] Qwen vision grounding failed: {e}')
        return None


def _qwen_vision_locate_boxes(goal: str, b64_img: str | None = None) -> list[dict]:
    """Locate all visible instances of `goal` on screen as logical-pixel boxes."""
    try:
        if b64_img is None:
            b64_img = capture_screen_base64()
        if not b64_img:
            return []

        prompt = (
            f'You are a computer vision UI grounding model.\n'
            f'Look at the screenshot and find all visible instances of the UI element: "{goal}".\n'
            f'Return ONLY a JSON array of objects with the bounding boxes normalized to a 1000x1000 grid:\n'
            f'[{{"label": "{goal}", "box_2d": [ymin, xmin, ymax, xmax]}}]\n'
            f'Where ymin, xmin, ymax, xmax are normalized to 0-1000.\n'
            f'If no instances are visible on the screen, return ONLY: []\n'
            f'Do not include markdown or reasoning outside the JSON.'
        )

        from core.brain import _execute_llm_completion
        resp = _execute_llm_completion(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            temperature=0.1,
            n_predict=1500,
        )

        raw = (resp.get("content") or "").strip()
        if not raw:
            raw = (resp.get("reasoning_content") or "").strip()

        if not raw:
            return []

        logical_w, logical_h = pyautogui.size()

        boxes: list[list[int]] = []

        # 1. Try parsing JSON array directly or clean markdown fences
        clean = raw
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s*```$", "", clean)

        try:
            parsed_json = json.loads(clean)
            if isinstance(parsed_json, list):
                for item in parsed_json:
                    if isinstance(item, dict):
                        b = item.get("box_2d") or item.get("box")
                        if isinstance(b, (list, tuple)) and len(b) >= 4:
                            boxes.append([int(v) for v in b[:4]])
                    elif isinstance(item, (list, tuple)) and len(item) >= 4:
                        boxes.append([int(v) for v in item[:4]])
            elif isinstance(parsed_json, dict):
                b = parsed_json.get("box_2d") or parsed_json.get("box")
                if isinstance(b, (list, tuple)) and len(b) >= 4:
                    boxes.append([int(v) for v in b[:4]])
        except Exception:
            pass

        # 2. Also regex search for box_2d or Qwen box tokens if JSON did not find any
        if not boxes:
            for m in re.finditer(r'"box_2d"\s*:\s*\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw):
                boxes.append([int(g) for g in m.groups()])

        if not boxes:
            for m in re.finditer(
                r'<\|box_start\|>\s*\(?\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\)?\s*,\s*\(?\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\)?\s*<\|box_end\|>',
                raw
            ):
                boxes.append([int(g) for g in m.groups()])

        matches: list[dict] = []
        for b in boxes:
            ymin, xmin, ymax, xmax = b
            if not (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and 0 <= ymin <= 1000 and 0 <= ymax <= 1000):
                continue
            left = int((xmin / 1000.0) * logical_w)
            top = int((ymin / 1000.0) * logical_h)
            width = max(1, int(((xmax - xmin) / 1000.0) * logical_w))
            height = max(1, int(((ymax - ymin) / 1000.0) * logical_h))
            matches.append({"box": (left, top, width, height), "score": 0.9, "text": goal})

        # 3. If no box_2d was found, try fallback to single grounding
        if not matches:
            single = _parse_qwen_grounding(raw)
            if single:
                x_norm, y_norm, box_coords = single
                if box_coords:
                    ymin, xmin, ymax, xmax = box_coords
                    left = int((xmin / 1000.0) * logical_w)
                    top = int((ymin / 1000.0) * logical_h)
                    width = max(1, int(((xmax - xmin) / 1000.0) * logical_w))
                    height = max(1, int(((ymax - ymin) / 1000.0) * logical_h))
                    matches.append({"box": (left, top, width, height), "score": 0.9, "text": goal})
                else:
                    lx = int((x_norm / 1000.0) * logical_w)
                    ly = int((y_norm / 1000.0) * logical_h)
                    left = max(0, lx - 30)
                    top = max(0, ly - 18)
                    matches.append({"box": (left, top, 60, 36), "score": 0.9, "text": goal})

        # Deduplicate boxes whose centers are within 10 logical px
        deduped: list[dict] = []
        for m in matches:
            cx = m['box'][0] + m['box'][2] / 2
            cy = m['box'][1] + m['box'][3] / 2
            if any(abs(cx - (d['box'][0] + d['box'][2] / 2)) < 10
                   and abs(cy - (d['box'][1] + d['box'][3] / 2)) < 10 for d in deduped):
                continue
            deduped.append(m)

        print(f'[Locator] boxed: {len(deduped)} match(es) for {repr(goal)}')
        return deduped
    except Exception as e:
        print(f'[Locator] Qwen vision locate boxes failed: {e}')
        return []


def locate_ui_element_ex(goal: str) -> dict | None:
    """Locate a UI element by natural language description — DOM, UIA, or Qwen Vision Grounding.

    Track -1 — DOM motor (Stage 1): role-aware UIA shortlist.
    Track 0  — Windows UI Automation accessibility tree (tools/uia.py).
    Track 1  — Direct Qwen 3.6 Vision Grounding.

    Returns {"x", "y", "source", "text", "score", ...} in logical pixels, or None.
    `source` is one of dom-win | uia | qwen-vision.
    """
    # Track -1 (DOM motor, Stage 1): role-aware UIA shortlist + exact-match
    # fallback over ONE shared walk. Off by default; on a miss the exact-match
    # pass reuses the same candidates so Track 0 never walks the tree twice.
    uia_done = False
    try:
        import config as _config
        if getattr(_config, "USE_DOM_MOTOR", False):
            from tools.dom import windows_locate_cached
            result, raw_nodes, desk = windows_locate_cached(goal)
            if result:
                return result
            try:
                from tools.uia import uia_locate_candidates
                result = uia_locate_candidates(goal, raw_nodes, desk)
                if result:
                    return result
                if raw_nodes or desk:
                    uia_done = True  # the tree was already walked this call
            except Exception as e:
                print(f'[Locator] UIA candidate reuse failed: {e}')
    except Exception as e:
        print(f'[Locator] DOM motor track unavailable: {e}')

    # Track 0: ask the OS before reading pixels
    if not uia_done:
        try:
            from tools.uia import uia_locate
            result = uia_locate(goal)
            if result:
                return result
        except Exception as e:
            print(f'[Locator] UIA track unavailable: {e}')

    # Track 1: Direct Qwen 3.6 Vision Grounding
    return _qwen_vision_locate(goal)


def locate_ui_element(goal: str) -> tuple[int, int] | None:
    """Back-compat wrapper around locate_ui_element_ex: (x, y) or None."""
    result = locate_ui_element_ex(goal)
    return (result['x'], result['y']) if result else None


def locate_ui_elements_boxed(goal: str, threshold: float = 0.5) -> list[dict]:
    """Locate ALL on-screen matches for `goal`, returned as logical-pixel boxes.

    Delegates directly to Direct Qwen 3.6 Vision Grounding.
    Returns [{"box": (left, top, width, height), "score": 0.9, "text": goal}, ...].
    """
    return _qwen_vision_locate_boxes(goal)




# ============================================================================
# LAYER 3 — VERIFICATION
# ============================================================================
def verify_action_result(goal: str) -> str:
    """After a click, captures the screen and asks the model if the action succeeded.

    Returns a string starting with SUCCESS or FAILED followed by explanation.
    """
    time.sleep(0.8)  # Wait for UI to settle
    b64 = capture_screen_base64()
    if not b64:
        return 'Could not verify — screen capture failed.'

    from core.brain import _execute_llm_completion
    verify_msgs = [{
        'role': 'user',
        'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}},
            {'type': 'text', 'text': (
                f'I just tried to: {goal}\n'
                'Looking at the current screen, did the action succeed? '
                'Reply in one sentence starting with SUCCESS or FAILED.'
            )}
        ]
    }]
    return _execute_llm_completion(messages=verify_msgs, temperature=0.1, n_predict=80).get("content") or ""
