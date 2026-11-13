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


_LAST_RAW_SCREENSHOT_BGR: np.ndarray | None = None


def get_raw_screenshot_bgr() -> np.ndarray | None:
    """Returns the latest uncompressed full-resolution raw BGR frame, or captures a fresh one."""
    global _LAST_RAW_SCREENSHOT_BGR
    if _LAST_RAW_SCREENSHOT_BGR is not None:
        return _LAST_RAW_SCREENSHOT_BGR
    if mss is None:
        return None
    try:
        with mss.mss() as sct:
            if len(sct.monitors) < 2:
                return None
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)
        frame_bgr = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        _LAST_RAW_SCREENSHOT_BGR = frame_bgr.copy()
        return _LAST_RAW_SCREENSHOT_BGR
    except Exception:
        return None


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
        global _LAST_RAW_SCREENSHOT_BGR
        _LAST_RAW_SCREENSHOT_BGR = frame_bgr.copy()
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
        import core.brain as _brain
        user_msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean_b64}"}},
                {"type": "text", "text": f"{specific_question.strip()} Do NOT call any tools — reply with plain text only."},
            ],
        }
        one_shot_msg = []
        brain_msgs = getattr(_brain, "messages", None)
        if isinstance(brain_msgs, list) and brain_msgs and isinstance(brain_msgs[0], dict) and brain_msgs[0].get("role") == "system":
            one_shot_msg.append(brain_msgs[0])
        one_shot_msg.append(user_msg)

        answer = _brain._execute_llm_completion(
            messages=one_shot_msg,
            tools=getattr(_brain, "ADMIN_TOOLS", None),
            temperature=0.2,
            n_predict=1500,
            enable_thinking=False,
        ).get("content") or ""

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
                screen_data = _execute_llm_completion(messages=screen_msgs, temperature=0.1, n_predict=1500, enable_thinking=False).get("content") or ""

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
    """Clear cached raw screenshot buffer after mouse or keyboard actions."""
    global _LAST_RAW_SCREENSHOT_BGR
    _LAST_RAW_SCREENSHOT_BGR = None


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
      - JSON with 'box_2d', 'bbox_2d', or 'box': [xmin, ymin, xmax, ymax] -> center ((xmin+xmax)//2, (ymin+ymax)//2, box)
      - Native Qwen box tag: <|box_start|>(xmin, ymin), (xmax, ymax)<|box_end|>
      - Standalone 4-element [xmin, ymin, xmax, ymax] or 2-element [x, y] list.

    Returns (center_x, center_y, [xmin, ymin, xmax, ymax]) or None if not found or out of bounds.
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
            for bkey in ("box_2d", "bbox_2d", "box"):
                if bkey in obj:
                    val = obj[bkey]
                    if isinstance(val, (list, tuple)) and len(val) >= 4:
                        try:
                            xmin, ymin, xmax, ymax = [int(v) for v in val[:4]]
                            if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                                    0 <= ymin <= 1000 and 0 <= ymax <= 1000):
                                if xmin > xmax:
                                    xmin, xmax = xmax, xmin
                                if ymin > ymax:
                                    ymin, ymax = ymax, ymin
                                cx = (xmin + xmax) // 2
                                cy = (ymin + ymax) // 2
                                if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                                    return (cx, cy, [xmin, ymin, xmax, ymax])
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
                    xmin, ymin, xmax, ymax = [int(v) for v in obj[:4]]
                    if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                            0 <= ymin <= 1000 and 0 <= ymax <= 1000):
                        if xmin > xmax:
                            xmin, xmax = xmax, xmin
                        if ymin > ymax:
                            ymin, ymax = ymax, ymin
                        cx = (xmin + xmax) // 2
                        cy = (ymin + ymax) // 2
                        if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                            return (cx, cy, [xmin, ymin, xmax, ymax])
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

    # 4. Check for Qwen box tag: <|box_start|>(xmin, ymin), (xmax, ymax)<|box_end|>
    box_tag_match = re.search(
        r'<\|box_start\|>\s*\(?\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\)?\s*,\s*\(?\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\)?\s*<\|box_end\|>',
        raw_text
    )
    if box_tag_match:
        xmin, ymin, xmax, ymax = [int(g) for g in box_tag_match.groups()]
        if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                0 <= ymin <= 1000 and 0 <= ymax <= 1000):
            if xmin > xmax:
                xmin, xmax = xmax, xmin
            if ymin > ymax:
                ymin, ymax = ymax, ymin
            cx = (xmin + xmax) // 2
            cy = (ymin + ymax) // 2
            if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                return (cx, cy, [xmin, ymin, xmax, ymax])

    # 5. Regex for key patterns if JSON was malformed
    pt_match = re.search(r'"(?:point|point_2d|coordinate)"\s*:\s*\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw_text)
    if pt_match:
        x, y = int(pt_match.group(1)), int(pt_match.group(2))
        if 0 <= x <= 1000 and 0 <= y <= 1000:
            return (x, y, None)

    box_key_match = re.search(r'"(?:box_2d|bbox_2d|box)"\s*:\s*\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw_text)
    if box_key_match:
        xmin, ymin, xmax, ymax = [int(g) for g in box_key_match.groups()]
        if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                0 <= ymin <= 1000 and 0 <= ymax <= 1000):
            if xmin > xmax:
                xmin, xmax = xmax, xmin
            if ymin > ymax:
                ymin, ymax = ymax, ymin
            cx = (xmin + xmax) // 2
            cy = (ymin + ymax) // 2
            if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                return (cx, cy, [xmin, ymin, xmax, ymax])

    # 6. Standalone 4-element list: [xmin, ymin, xmax, ymax]
    list4_match = re.search(r'\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw_text)
    if list4_match:
        xmin, ymin, xmax, ymax = [int(g) for g in list4_match.groups()]
        if (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and
                0 <= ymin <= 1000 and 0 <= ymax <= 1000):
            if xmin > xmax:
                xmin, xmax = xmax, xmin
            if ymin > ymax:
                ymin, ymax = ymax, ymin
            cx = (xmin + xmax) // 2
            cy = (ymin + ymax) // 2
            if 0 <= cx <= 1000 and 0 <= cy <= 1000:
                return (cx, cy, [xmin, ymin, xmax, ymax])

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
            f'Disambiguation rule for chat/contact lists (e.g. Discord, Telegram, Instagram): if multiple entries share the same name (such as two "Ob" rows because one is a 1-on-1 user and the other is a 2-member group chat with that person), ALWAYS select the 1-on-1 user row that has a custom photographic/illustrated profile avatar (and/or a username handle next to the name) and a single-line name, and NEVER select the row that has a solid-colored circle with a default 2-person group silhouette icon or "2 Members" / "N Members" subtitle (unless a group chat was explicitly requested).\n'
            f'If the element is NOT visible on the screen, return ONLY: {{"found": false}}\n'
            f'If visible, return ONLY a JSON object with the center coordinate normalized to a 1000x1000 grid:\n'
            f'{{"found": true, "target": "{goal}", "point": [x, y]}}\n'
            f'Where x is the horizontal coordinate (0-1000, left to right) and y is the vertical coordinate (0-1000, top to bottom).\n'
            f'Do not include markdown or reasoning outside the JSON. Do NOT call any tools — return ONLY the JSON output.'
        )

        import core.brain as _brain
        user_msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                {"type": "text", "text": prompt},
            ],
        }
        messages = []
        brain_msgs = getattr(_brain, "messages", None)
        if isinstance(brain_msgs, list) and brain_msgs and isinstance(brain_msgs[0], dict) and brain_msgs[0].get("role") == "system":
            messages.append(brain_msgs[0])
        messages.append(user_msg)

        resp = _brain._execute_llm_completion(
            messages=messages,
            tools=getattr(_brain, "ADMIN_TOOLS", None),
            temperature=0.1,
            n_predict=1500,
            enable_thinking=False,
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
            b64_img = capture_screen_base64(max_width=0)
        if not b64_img:
            return []

        prompt = (
            f'You are a computer vision UI grounding model.\n'
            f'Look at the screenshot and find all visible instances of the UI element: "{goal}".\n'
            f'Return ONLY a JSON array of objects with the bounding boxes normalized to a 1000x1000 grid:\n'
            f'[{{"label": "{goal}", "box_2d": [xmin, ymin, xmax, ymax]}}]\n'
            f'Where xmin,xmax are horizontal (0-1000, left to right) and ymin,ymax are vertical (0-1000, top to bottom).\n'
            f'If no instances are visible on the screen, return ONLY: []\n'
            f'Do not include markdown or reasoning outside the JSON. Do NOT call any tools — return ONLY the JSON output.'
        )

        import core.brain as _brain
        user_msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                {"type": "text", "text": prompt},
            ],
        }
        messages = []
        brain_msgs = getattr(_brain, "messages", None)
        if isinstance(brain_msgs, list) and brain_msgs and isinstance(brain_msgs[0], dict) and brain_msgs[0].get("role") == "system":
            messages.append(brain_msgs[0])
        messages.append(user_msg)

        resp = _brain._execute_llm_completion(
            messages=messages,
            tools=getattr(_brain, "ADMIN_TOOLS", None),
            temperature=0.1,
            n_predict=1500,
            enable_thinking=False,
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
                        b = item.get("box_2d") or item.get("bbox_2d") or item.get("box")
                        if isinstance(b, (list, tuple)) and len(b) >= 4:
                            boxes.append([int(v) for v in b[:4]])
                    elif isinstance(item, (list, tuple)) and len(item) >= 4:
                        boxes.append([int(v) for v in item[:4]])
            elif isinstance(parsed_json, dict):
                b = parsed_json.get("box_2d") or parsed_json.get("bbox_2d") or parsed_json.get("box")
                if isinstance(b, (list, tuple)) and len(b) >= 4:
                    boxes.append([int(v) for v in b[:4]])
        except Exception:
            pass

        # 2. Also regex search for box_2d or Qwen box tokens if JSON did not find any
        if not boxes:
            for m in re.finditer(r'"(?:box_2d|bbox_2d|box)"\s*:\s*\[\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\]', raw):
                boxes.append([int(g) for g in m.groups()])

        if not boxes:
            for m in re.finditer(
                r'<\|box_start\|>\s*\(?\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\)?\s*,\s*\(?\s*(\d{1,4})\s*,\s*(\d{1,4})\s*\)?\s*<\|box_end\|>',
                raw
            ):
                boxes.append([int(g) for g in m.groups()])

        matches: list[dict] = []
        for b in boxes:
            xmin, ymin, xmax, ymax = b
            if not (0 <= xmin <= 1000 and 0 <= xmax <= 1000 and 0 <= ymin <= 1000 and 0 <= ymax <= 1000):
                continue
            if xmin > xmax:
                xmin, xmax = xmax, xmin
            if ymin > ymax:
                ymin, ymax = ymax, ymin
            if (xmax - xmin) > 500 or (ymax - ymin) > 500:
                continue
            left = int((xmin / 1000.0) * logical_w)
            top = int((ymin / 1000.0) * logical_h)
            width = max(16, int(((xmax - xmin) / 1000.0) * logical_w))
            height = max(16, int(((ymax - ymin) / 1000.0) * logical_h))
            matches.append({"box": (left, top, width, height), "score": 0.9, "text": goal, "source": "qwen-vision"})

        # 3. If no box_2d was found, try fallback to single grounding
        if not matches:
            single = _parse_qwen_grounding(raw)
            if single:
                x_norm, y_norm, box_coords = single
                if box_coords:
                    xmin, ymin, xmax, ymax = box_coords
                    if (xmax - xmin) <= 500 and (ymax - ymin) <= 500:
                        left = int((xmin / 1000.0) * logical_w)
                        top = int((ymin / 1000.0) * logical_h)
                        width = max(16, int(((xmax - xmin) / 1000.0) * logical_w))
                        height = max(16, int(((ymax - ymin) / 1000.0) * logical_h))
                        matches.append({"box": (left, top, width, height), "score": 0.9, "text": goal, "source": "qwen-vision"})
                else:
                    lx = int((x_norm / 1000.0) * logical_w)
                    ly = int((y_norm / 1000.0) * logical_h)
                    left = max(0, lx - 30)
                    top = max(0, ly - 18)
                    matches.append({"box": (left, top, 60, 36), "score": 0.9, "text": goal, "source": "qwen-vision"})

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


AUTHENTIC_LAUNCHER_TYPES = {
    "ListItemControl",
    "ButtonControl",
    "ToolBarControl",
    "MenuItemControl",
    "HyperlinkControl",
    "ImageControl",
    "TreeItemControl",
}

STATIC_TEXT_TYPES = {
    "TextControl",
    "StaticText",
    "DocumentControl",
}

LAUNCHER_TARGET_KEYWORDS = {
    "icon", "app", "application", "shortcut", "launcher", "desktop", "screen"
}

DESKTOP_TARGET_KEYWORDS = {"desktop", "wallpaper"}
DESKTOP_TARGET_PHRASES = ("on desktop", "on the desktop", "desktop icon", "desktop shortcut", "desktop app")


def is_desktop_target(goal: str) -> bool:
    """Return True if goal specifically targets the Windows desktop or wallpaper."""
    if not goal:
        return False
    g = goal.lower()
    if any(p in g for p in DESKTOP_TARGET_PHRASES):
        return True
    words = set(re.findall(r"[a-z0-9']+", g))
    return bool(words & DESKTOP_TARGET_KEYWORDS)


TASKBAR_TARGET_PHRASES = (
    "taskbar", "task bar", "system tray", "systray", "tray",
    "start button", "start menu", "notification area", "action center", "clock",
)
TASKBAR_TARGET_KEYWORDS = {"taskbar", "systray"}


def is_taskbar_target(goal: str) -> bool:
    """Return True if goal targets the Windows taskbar, start button, or system tray."""
    if not goal:
        return False
    g = goal.lower()
    if any(p in g for p in TASKBAR_TARGET_PHRASES):
        return True
    words = set(re.findall(r"[a-z0-9']+", g))
    return bool(words & TASKBAR_TARGET_KEYWORDS) or ("start" in words and "button" in words)


def is_launcher_or_desktop_target(goal: str) -> bool:
    """Return True if goal asks for an icon, app, shortcut, launcher, desktop, or screen element."""
    if not goal:
        return False
    words = set(re.findall(r"[a-z0-9']+", goal.lower()))
    return bool(words & LAUNCHER_TARGET_KEYWORDS)


def _is_acceptable_uia_result(result: dict | None, goal: str) -> bool:
    """Validate whether a UIA match is trustworthy or should fall through to Qwen Vision Grounding.

    Fallthrough conditions:
    1. If a desktop target is requested and control_source is explicitly from an active window (not desktop).
    2. If an icon/app/desktop target is requested and the match is NOT an authentic OS launcher control
       (e.g., UIA returned static text or chat bubbles).
    3. If the match is static text with low confidence (score < 0.85).
    """
    if not result:
        return False
    res_type = result.get("type", "")
    score = float(result.get("score", 0.0))
    ctrl_source = result.get("control_source") or result.get("source", "uia")

    if is_desktop_target(goal) and ctrl_source in ("window", "active_window", "foreground", "telegram"):
        return False

    is_launcher_req = is_launcher_or_desktop_target(goal)
    if is_launcher_req and res_type not in AUTHENTIC_LAUNCHER_TYPES:
        return False

    if res_type in STATIC_TEXT_TYPES and score < 0.85:
        return False

    return True


def locate_ui_element_ex(goal: str) -> dict | None:
    """Locate a UI element by natural language description — DOM, UIA, or Qwen Vision Grounding.

    Track -1 — DOM motor (Stage 1): role-aware UIA shortlist.
    Track 0  — Windows UI Automation accessibility tree (tools/uia.py).
    Track 1  — Direct Qwen 3.6 Vision Grounding.

    Returns {"x", "y", "source", "text", "score", ...} in logical pixels, or None.
    `source` is one of dom-win | uia | qwen-vision.
    """
    try:
        from tools import overlay as _overlay
        _overlay.set_island_state("locating", f"Locating '{goal[:34]}'...")
    except Exception:
        pass

    # Track -1 (DOM motor, Stage 1): role-aware UIA shortlist + exact-match
    # fallback over ONE shared walk. Off by default; on a miss the exact-match
    # pass reuses the same candidates so Track 0 never walks the tree twice.
    uia_done = False
    try:
        import config as _config
        if getattr(_config, "USE_DOM_MOTOR", False):
            from tools.dom import windows_locate_cached
            result, raw_nodes, desk = windows_locate_cached(goal)
            if result and _is_acceptable_uia_result(result, goal):
                return result
            try:
                from tools.uia import uia_locate_candidates
                result = uia_locate_candidates(goal, raw_nodes, desk)
                if result and _is_acceptable_uia_result(result, goal):
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
                if _is_acceptable_uia_result(result, goal):
                    return result
                print(f"[Locator] UIA match {result.get('text')!r} [{result.get('type')}] "
                      f"rejected for goal {goal!r} (low-confidence static text or zero authentic launcher controls); "
                      f"falling through to Qwen vision.")
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

    First checks Track 0 (Windows UIA exact bounding rectangles) for fast,
    pixel-accurate OS control rects. If UIA finds no strong matches, delegates
    to Direct Qwen 3.6 Vision Grounding (`_qwen_vision_locate_boxes`).
    Returns [{"box": (left, top, width, height), "score": ..., "text": ...}, ...].
    """
    try:
        from tools import overlay as _overlay
        _overlay.set_island_state("locating", f"Locating '{goal[:34]}'...")
    except Exception:
        pass

    # Track 0: Check Windows UIA accessibility tree for exact OS rectangles first
    try:
        from tools.uia import uia_nodes
        from tools.locator_common import parse_goal, score_text, spatial_weight
        candidates, desktop_size = uia_nodes()
        if candidates and desktop_size and desktop_size[0] > 0 and desktop_size[1] > 0:
            desk_w, desk_h = desktop_size
            logical_w, logical_h = pyautogui.size()
            scale_x = logical_w / desk_w if desk_w else 1.0
            scale_y = logical_h / desk_h if desk_h else 1.0
            parsed = parse_goal(goal)
            uia_threshold = max(0.75, float(threshold))

            is_desktop_req = is_desktop_target(goal)
            is_taskbar_req = is_taskbar_target(goal)
            has_desktop_source = any(c.get("source") == "desktop" for c in candidates)

            uia_matches: list[dict] = []
            for cand in candidates:
                cand_source = cand.get("source", "uia")
                # When goal is a desktop target, strictly accept only candidates from desktop listview
                if is_desktop_req:
                    if has_desktop_source and cand_source != "desktop":
                        continue
                    if not has_desktop_source and cand_source in ("window", "active_window", "foreground", "telegram"):
                        continue

                cand_type = cand.get("type", "")
                score = score_text(cand.get("name", ""), parsed, cand_type)
                if parsed["type_hints"] and cand_type in parsed["type_hints"]:
                    score = min(1.0, score + 0.05)
                x1, y1, x2, y2 = cand["rect"]
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                score *= spatial_weight(cx, cy, desk_w, desk_h, parsed["spatial"])

                # Check if candidate is from taskbar (Shell_TrayWnd)
                is_taskbar_cand = (
                    cand_source == "taskbar"
                    or cand.get("window") == "Shell_TrayWnd"
                    or (desk_h > 0 and y1 >= desk_h - 60)
                )
                if is_taskbar_req and is_taskbar_cand:
                    score = min(1.0, score + 0.25)

                if score < uia_threshold:
                    continue
                left = int(x1 * scale_x)
                top = int(y1 * scale_y)
                width = max(8, int((x2 - x1) * scale_x))
                height = max(8, int((y2 - y1) * scale_y))
                # Ignore giant container panes covering more than half the screen
                if width > logical_w * 0.5 or height > logical_h * 0.5:
                    continue
                uia_matches.append({
                    "box": (left, top, width, height),
                    "score": round(score, 2),
                    "text": cand.get("name", goal),
                    "source": "uia",
                    "control_source": cand_source,
                    "type": cand_type,
                    "is_taskbar": is_taskbar_cand,
                })

            if uia_matches:
                if is_desktop_req:
                    # Strict filtering for desktop listview candidates; prune internal window controls
                    if has_desktop_source:
                        desktop_matches = [m for m in uia_matches if m.get("control_source") == "desktop"]
                        if not desktop_matches:
                            print(f"[Locator] boxed (UIA): zero desktop candidates for {goal!r} "
                                  f"(pruned {len(uia_matches)} internal application controls); "
                                  f"falling through to Qwen vision.")
                            uia_matches = []
                        else:
                            uia_matches = desktop_matches

            if uia_matches and is_taskbar_req:
                # Prioritize taskbar controls from Shell_TrayWnd
                taskbar_matches = [m for m in uia_matches if m.get("control_source") == "taskbar" or m.get("is_taskbar")]
                if taskbar_matches:
                    uia_matches = taskbar_matches

            if uia_matches:
                is_launcher_req = is_launcher_or_desktop_target(goal)
                if is_launcher_req:
                    # Demand authentic OS launcher controls; prune any passive/static text
                    authentic = [m for m in uia_matches if m.get("type") in AUTHENTIC_LAUNCHER_TYPES]
                    if not authentic:
                        print(f"[Locator] boxed (UIA): zero authentic launcher controls for {goal!r} "
                              f"(found {len(uia_matches)} static/non-launcher matches); falling through to Qwen vision.")
                        uia_matches = []
                    else:
                        uia_matches = authentic
                else:
                    # Prune low-confidence static text (< 0.85)
                    uia_matches = [
                        m for m in uia_matches
                        if not (m.get("type") in STATIC_TEXT_TYPES and m.get("score", 0.0) < 0.85)
                    ]
                    if not uia_matches:
                        print(f"[Locator] boxed (UIA): only low-confidence static text for {goal!r}; "
                              f"falling through to Qwen vision.")

            if uia_matches:
                uia_matches.sort(key=lambda m: m["score"], reverse=True)
                deduped_uia: list[dict] = []
                for m in uia_matches:
                    mcx = m["box"][0] + m["box"][2] / 2
                    mcy = m["box"][1] + m["box"][3] / 2
                    if any(abs(mcx - (d["box"][0] + d["box"][2] / 2)) < 10
                           and abs(mcy - (d["box"][1] + d["box"][3] / 2)) < 10 for d in deduped_uia):
                        continue
                    deduped_uia.append(m)
                print(f"[Locator] boxed (UIA): {len(deduped_uia)} match(es) for {goal!r}")
                return deduped_uia
    except Exception as e:
        print(f"[Locator] UIA boxed track unavailable: {e}")

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

    import core.brain as _brain
    user_msg = {
        'role': 'user',
        'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}},
            {'type': 'text', 'text': (
                f'I just tried to: {goal}\n'
                'Looking at the current screen, did the action succeed? '
                'Reply in one sentence starting with SUCCESS or FAILED. '
                'Do NOT call any tools — reply with plain text only.'
            )}
        ]
    }
    verify_msgs = []
    brain_msgs = getattr(_brain, "messages", None)
    if isinstance(brain_msgs, list) and brain_msgs and isinstance(brain_msgs[0], dict) and brain_msgs[0].get("role") == "system":
        verify_msgs.append(brain_msgs[0])
    verify_msgs.append(user_msg)
    return _brain._execute_llm_completion(
        messages=verify_msgs,
        tools=getattr(_brain, "ADMIN_TOOLS", None),
        temperature=0.1,
        n_predict=300,
        enable_thinking=False,
    ).get("content") or ""



# ============================================================================
# EXTENDED MOUSE PRIMITIVES & DYNAMIC HIGH-RESOLUTION FOVEATED VISION
# ============================================================================
def hover_ui_element(goal: str) -> dict | None:
    """Locate a UI element and move the mouse cursor over it without clicking."""
    loc = locate_ui_element_ex(goal)
    if not loc:
        return None
    try:
        from tools import motor as _motor
        _motor.human_move_to(loc["x"], loc["y"])
    except Exception:
        pyautogui.moveTo(loc["x"], loc["y"], duration=0.2)
    time.sleep(0.3)
    return loc


def drag_and_drop_ui_elements(source_goal: str, target_goal: str, duration: float = 0.5) -> tuple[dict | None, dict | None]:
    """Locate source and target elements, then click and drag smoothly between them."""
    loc_src = locate_ui_element_ex(source_goal)
    if not loc_src:
        return None, None
    loc_dst = locate_ui_element_ex(target_goal)
    if not loc_dst:
        return loc_src, None
    try:
        from tools import motor as _motor
        _motor.human_move_to(loc_src["x"], loc_src["y"])
    except Exception:
        pyautogui.moveTo(loc_src["x"], loc_src["y"], duration=0.2)
    time.sleep(0.1)
    pyautogui.mouseDown(button="left")
    time.sleep(0.1)
    try:
        from tools import motor as _motor
        _motor.human_move_to(loc_dst["x"], loc_dst["y"], duration=max(0.2, duration))
    except Exception:
        pyautogui.moveTo(loc_dst["x"], loc_dst["y"], duration=max(0.2, duration))
    time.sleep(0.1)
    pyautogui.mouseUp(button="left")
    time.sleep(0.3)
    invalidate_screen_cache()
    return loc_src, loc_dst


def click_at_coordinates(x: int, y: int, clicks: int = 1, button: str = "left") -> bool:
    """Clicks directly at logical screen coordinates without running element locators."""
    logical_w, logical_h = pyautogui.size()
    cx = max(0, min(logical_w, int(x)))
    cy = max(0, min(logical_h, int(y)))
    btn = button.lower() if button.lower() in ("left", "right", "middle") else "left"
    try:
        from tools import motor as _motor
        _motor.human_click(cx, cy, clicks=max(1, clicks), button=btn)
    except Exception:
        pyautogui.click(cx, cy, clicks=max(1, clicks), button=btn)
    invalidate_screen_cache()
    return True


def zoom_and_read_region(region_query: str, padding: int = 60) -> str:
    """Extracts an uncompressed, 1:1 native-resolution crop around a region and transcribes fine detail."""
    raw = get_raw_screenshot_bgr()
    if raw is None:
        return "Screen capture unavailable."

    # 1. Coarse locate in logical desktop space
    matches = _qwen_vision_locate_boxes(region_query)
    if matches:
        left, top, width, height = matches[0]["box"]
    else:
        single = locate_ui_element_ex(region_query)
        if single:
            left, top, width, height = max(0, single["x"] - 150), max(0, single["y"] - 100), 300, 200
        else:
            return f"Could not visually isolate region '{region_query}' to zoom in."

    # 2. Convert logical coordinates to raw physical pixels
    logical_w, logical_h = pyautogui.size()
    phys_h, phys_w = raw.shape[:2]
    sx = phys_w / logical_w if logical_w else 1.0
    sy = phys_h / logical_h if logical_h else 1.0

    px1 = max(0, int((left - padding) * sx))
    py1 = max(0, int((top - padding) * sy))
    px2 = min(phys_w, int((left + width + padding) * sx))
    py2 = min(phys_h, int((top + height + padding) * sy))

    if (px2 - px1) < 10 or (py2 - py1) < 10:
        return f"Target region '{region_query}' was too small or off-screen."

    # 3. 1:1 uncompressed crop
    patch_bgr = raw[py1:py2, px1:px2]
    ok, buf = cv2.imencode(".jpg", patch_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ok:
        return "Failed to encode high-resolution patch."
    patch_b64 = base64.b64encode(buf).decode("utf-8")

    # 4. Multimodal transcription request to Qwen
    import core.brain as _brain
    prompt = (
        f"You are inspecting an uncompressed 1:1 high-resolution zoom of the screen region: '{region_query}'.\n"
        f"Transcribe all visible text exactly as written. Describe any timestamps, numbers, status badges, buttons, or fine details visible in this region."
    )
    user_msg = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{patch_b64}"}},
            {"type": "text", "text": prompt},
        ],
    }
    messages = []
    brain_msgs = getattr(_brain, "messages", None)
    if isinstance(brain_msgs, list) and brain_msgs and isinstance(brain_msgs[0], dict) and brain_msgs[0].get("role") == "system":
        messages.append(brain_msgs[0])
    messages.append(user_msg)

    resp = _brain._execute_llm_completion(
        messages=messages,
        tools=getattr(_brain, "ADMIN_TOOLS", None),
        temperature=0.1,
        n_predict=600,
        enable_thinking=False,
    )
    text_out = (resp.get("content") or "").strip()
    if not text_out:
        text_out = (resp.get("reasoning_content") or "").strip()
    return text_out or "No readable text or details could be resolved in this zoomed region."
