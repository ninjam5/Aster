import base64
import cv2
import difflib
import face_recognition
import json
import os
import re
import threading
import time
import importlib
import requests
from typing import Any

import numpy as np

import config
from tools.locator_common import parse_goal, score_text, spatial_weight

try:
    mss = importlib.import_module("mss")
except Exception:
    mss = None

try:
    import pytesseract
    # Resolve Tesseract executable — check env override then common Windows install paths
    _tess_candidates = [
        os.getenv("TESSERACT_CMD", "").strip(),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for _tess_path in _tess_candidates:
        if _tess_path and os.path.exists(_tess_path):
            pytesseract.pytesseract.tesseract_cmd = _tess_path
            print(f"[Tesseract] Using executable: {_tess_path}")
            break
except Exception:
    pytesseract = None


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


def get_primary_face_crop_rgb(frame_or_b64) -> "np.ndarray | None":
    """Return the largest detected face as an RGB numpy crop, or None.

    Accepts a BGR numpy frame (OpenCV) or a base64 JPEG string (the form the
    Awareness daemon already holds). Reuses face_recognition's HOG detector
    (already loaded here). Used by the Tier-2 facial-emotion model in
    tools/emotion_recognition.py. Best-effort — None on any failure / no face.
    """
    try:
        if isinstance(frame_or_b64, str):
            raw = frame_or_b64.split(",", 1)[-1]
            np_arr = np.frombuffer(base64.b64decode(raw), np.uint8)
            frame_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        else:
            frame_bgr = frame_or_b64
        if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
            return None

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        boxes = face_recognition.face_locations(rgb)   # (top, right, bottom, left)
        if not boxes:
            return None

        top, right, bottom, left = max(boxes, key=lambda b: (b[2] - b[0]) * (b[1] - b[3]))
        h, w = rgb.shape[:2]
        top, left = max(0, top), max(0, left)
        bottom, right = min(h, bottom), min(w, right)
        if bottom <= top or right <= left:
            return None
        return rgb[top:bottom, left:right]
    except Exception:
        return None


def capture_screen_base64() -> str | None:
    """Captures the primary monitor and returns raw base64 for multimodal injection.

    Bypasses YOLOv8/pytesseract — the raw screenshot is sent to the mmproj.
    """
    if mss is None:
        return None
    try:
        with mss.mss() as sct:
            if len(sct.monitors) < 2:
                return None
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)
        frame_bgr = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        _, buffer = cv2.imencode(".jpg", frame_bgr)
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
                        import datetime
                        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with open("Aster_Vault/memory.md", "a", encoding="utf-8") as f:
                            f.write(f"\n- **[{timestamp}]** SYSTEM EVENT: The Screen Watcher daemon successfully detected '{target_text}' on the screen and sent a Telegram alert to {config.OWNER_NAME}.\n")
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
# OMNIPARSER V2 UI LOCATOR — icon_detect (YOLO) + OCR fuzzy match
# ============================================================================
_omniparser_model = None
_omniparser_lock = threading.Lock()


def _get_omniparser():
    global _omniparser_model
    with _omniparser_lock:
        if _omniparser_model is None:
            print('[OmniParser] Loading icon_detect model...')
            try:
                from ultralytics import YOLO
                from huggingface_hub import hf_hub_download
                weights_path = hf_hub_download(
                    repo_id='microsoft/OmniParser-v2.0',
                    filename='icon_detect/model.pt',
                )
                _omniparser_model = YOLO(weights_path)
                _omniparser_model.to('cuda')
                print('[OmniParser] icon_detect loaded.')
            except Exception as e:
                print(f'[OmniParser] Failed to load icon_detect: {e}')
    return _omniparser_model


def _release_omniparser() -> None:
    """Release the OmniParser YOLO model from VRAM immediately after use.

    Track 2 is a rare fallback; holding the model resident wastes ~200 MB VRAM
    between invocations. Released just like the Kokoro pipeline in tools/audio.py.
    """
    global _omniparser_model
    import gc
    with _omniparser_lock:
        if _omniparser_model is not None:
            _omniparser_model = None
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
            print('[OmniParser] icon_detect released from VRAM.')


# ============================================================================
# SCREEN OCR CACHE + ACTION-DIFF HELPERS
# ============================================================================
_SCREEN_CACHE_LOCK = threading.Lock()
_SCREEN_CACHE: dict = {"ts": 0.0, "frame": None, "lines": None}
_SCREEN_CACHE_TTL = 4.0


def invalidate_screen_cache() -> None:
    """Drop the cached screenshot + OCR pass. Called by every GUI action in
    core/brain.py — a click/keypress/scroll changes the screen, so the next
    locate must re-capture instead of matching against a stale frame."""
    with _SCREEN_CACHE_LOCK:
        _SCREEN_CACHE["ts"] = 0.0
        _SCREEN_CACHE["frame"] = None
        _SCREEN_CACHE["lines"] = None


def _extract_ocr_lines(frame_bgr: np.ndarray, physical_w: int, physical_h: int) -> list[dict]:
    """Two full-screen tesseract passes (normal + inverted for dark UIs, on a
    2× upscale), words grouped into lines. Returns [{"text", "box"}] with
    boxes in UPSCALED (2×) physical pixels."""
    from collections import defaultdict

    if pytesseract is None:
        return []

    upscaled = cv2.resize(frame_bgr, (physical_w * 2, physical_h * 2), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)

    out: list[dict] = []
    for gray_v in (gray, cv2.bitwise_not(gray)):
        try:
            data = pytesseract.image_to_data(
                gray_v,
                output_type=pytesseract.Output.DICT,
                config='--oem 3 --psm 11',
            )
        except Exception as e:
            print(f'[Locator] OCR error: {e}')
            continue

        lines: dict = defaultdict(list)
        for i, text in enumerate(data['text']):
            if not text.strip():
                continue
            try:
                conf = int(data['conf'][i])
            except (ValueError, TypeError):
                conf = -1
            if conf < 5:
                continue
            key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
            lines[key].append(i)

        for idxs in lines.values():
            words = [data['text'][i].strip().lower() for i in idxs]
            line_text = ' '.join(w for w in words if w)
            if not line_text.strip():
                continue
            x1 = min(data['left'][i] for i in idxs)
            x2 = max(data['left'][i] + data['width'][i] for i in idxs)
            y1 = min(data['top'][i] for i in idxs)
            y2 = max(data['top'][i] + data['height'][i] for i in idxs)
            out.append({'text': line_text, 'box': (x1, y1, x2, y2)})
    return out


def _get_screen_ocr() -> tuple | None:
    """Capture the screen and OCR it, with a short-TTL cache so consecutive
    locates on an unchanged screen (e.g. click then type) skip the 2-4s
    tesseract pass. Returns (frame_bgr, physical_w, physical_h, lines)."""
    with _SCREEN_CACHE_LOCK:
        if _SCREEN_CACHE["frame"] is not None and time.time() - _SCREEN_CACHE["ts"] < _SCREEN_CACHE_TTL:
            frame = _SCREEN_CACHE["frame"]
            return frame, frame.shape[1], frame.shape[0], _SCREEN_CACHE["lines"]

    b64 = capture_screen_base64()
    if not b64:
        print('[Locator] Screen capture failed.')
        return None
    np_arr = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
    frame_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if frame_bgr is None:
        print('[Locator] Failed to decode screenshot.')
        return None

    physical_h, physical_w = frame_bgr.shape[:2]
    lines = _extract_ocr_lines(frame_bgr, physical_w, physical_h)

    with _SCREEN_CACHE_LOCK:
        _SCREEN_CACHE["ts"] = time.time()
        _SCREEN_CACHE["frame"] = frame_bgr
        _SCREEN_CACHE["lines"] = lines
    return frame_bgr, physical_w, physical_h, lines


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


def _yolo_candidates(frame_bgr: np.ndarray) -> list[dict]:
    """Run OmniParser icon_detect → [{"bbox": (x1,y1,x2,y2), "conf": float}]
    in physical pixels. Empty list when the model is unavailable."""
    model = _get_omniparser()
    if model is None:
        return []
    elements = []
    results = model.predict(source=frame_bgr, conf=0.05, device=0, verbose=False)
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
        xyxy = boxes.xyxy.detach().cpu().numpy()
        confs = boxes.conf.detach().cpu().numpy()
        for box, conf in zip(xyxy, confs):
            x1, y1, x2, y2 = [int(v) for v in box.tolist()]
            if x2 > x1 and y2 > y1:
                elements.append({'bbox': (x1, y1, x2, y2), 'conf': float(conf)})
    return elements


_OCR_SCORE_THRESHOLD = 0.5


def _ocr_best_line(lines: list[dict] | None, parsed: dict, physical_w: int, physical_h: int,
                   scale_x: float, scale_y: float) -> dict | None:
    """Track 1 scoring: best OCR line for a parsed goal, or None below
    threshold. Line boxes are 2×-upscaled physical px; output is logical px."""
    best_line: dict | None = None
    best_score = 0.0
    for line in lines or []:
        score = score_text(line['text'], parsed)
        x1, y1, x2, y2 = line['box']               # upscaled 2× physical px
        cx_p, cy_p = (x1 + x2) / 4, (y1 + y2) / 4  # ÷2 upscale → physical px
        score *= spatial_weight(cx_p, cy_p, physical_w, physical_h, parsed['spatial'])
        if score > best_score:
            best_score = score
            best_line = {'x': int(cx_p * scale_x), 'y': int(cy_p * scale_y), 'text': line['text']}
    if best_line and best_score >= _OCR_SCORE_THRESHOLD:
        print(f'[Locator] OCR match: {best_line["text"]!r} score={best_score:.2f} '
              f'-> ({best_line["x"]},{best_line["y"]})')
        return {'x': best_line['x'], 'y': best_line['y'], 'source': 'ocr',
                'text': best_line['text'], 'score': round(best_score, 2)}
    print(f'[Locator] OCR track: no match (best={best_score:.2f})')
    return None


def _texts_in_box(lines: list[dict] | None, bbox: tuple) -> str:
    """Text label for a YOLO box, read from the CACHED full-screen OCR lines
    (centers falling inside the box) instead of a fresh per-crop tesseract
    call — the old per-crop approach cost ~0.4 s × N boxes (~45 s on a busy
    screen). Line boxes are 2×-upscaled physical px; bbox is physical px."""
    x1, y1, x2, y2 = bbox
    parts = []
    for line in lines or []:
        lx1, ly1, lx2, ly2 = line['box']
        cx, cy = (lx1 + lx2) / 4, (ly1 + ly2) / 4
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            parts.append(line['text'])
    return ' '.join(parts).strip()


# Cap on how many text-less icon crops get sent to the captioner per locate —
# each caption is one multimodal completion (~1-2 s on llama-server).
_CAPTION_MAX_CROPS = 8


def _caption_crop(crop_bgr: np.ndarray) -> str:
    """Describe an icon crop's FUNCTION using the configured captioner.

    Captioner is the resident main LLM (config.ICON_CAPTIONER = "llm") —
    crop is a far easier task than full-screen grounding. "off" disables
    captioning. (The Florence-2 icon_caption experiment was dropped —
    never wired.)"""
    import config as _config
    mode = getattr(_config, 'ICON_CAPTIONER', 'llm')
    if mode == 'off':
        return ''
    try:
        h, w = crop_bgr.shape[:2]
        if max(h, w) < 96:  # tiny icons: upscale so the vision encoder sees detail
            s = 96 / max(h, w)
            crop_bgr = cv2.resize(crop_bgr, (max(1, int(w * s)), max(1, int(h * s))),
                                  interpolation=cv2.INTER_CUBIC)
        ok, buf = cv2.imencode('.jpg', crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            return ''
        b64 = base64.b64encode(buf.tobytes()).decode()
        from core.brain import _execute_llm_completion
        msgs = [{
            'role': 'user',
            'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}},
                {'type': 'text', 'text': (
                    'This is a small icon or button cropped from a computer screen. '
                    'In at most 6 words, what is its function? Answer with only the '
                    "function, e.g. 'close window', 'settings', 'play button'."
                )},
            ],
        }]
        raw = _execute_llm_completion(messages=msgs, temperature=0.1, n_predict=24)
        return (raw.get('content') or '').strip().lower()
    except Exception as e:
        print(f'[Locator] caption error: {e}')
        return ''


_YOLO_SCORE_THRESHOLD = 0.45  # 0.3 let nonsense goals fuzzy-match random elements


def _pixel_fallback_enabled() -> bool:
    """Stage-2 rollback flag for the OmniParser/YOLO Track-2 pixel fallback.

    True (default) = current behavior. False (demoted) = Track 2 never loads
    its model; OCR Track 1 and the DOM motor still cover text/DOM surfaces.
    """
    try:
        import config as _config
        return bool(getattr(_config, 'USE_PIXEL_FALLBACK', True))
    except Exception:
        return True


def _yolo_track(frame_bgr: np.ndarray, parsed: dict, physical_w: int, physical_h: int,
                scale_x: float, scale_y: float, lines: list[dict] | None) -> dict | None:
    """Track 2: OmniParser icon detection.

    Boxes with OCR-readable text (from the cached full-screen pass) are
    matched on that text. When none match, the most confident TEXT-LESS boxes
    (actual icons) are captioned by the multimodal engine and matched on
    their function descriptions — the path that finds a gear icon for goal
    "settings"."""
    elements = _yolo_candidates(frame_bgr)
    print(f'[Locator] YOLO: {len(elements)} elements detected')
    if not elements:
        return None

    scored: list[tuple[float, tuple, str, str]] = []
    textless: list[dict] = []
    for elem in elements:
        text = _texts_in_box(lines, elem['bbox'])
        if not text:
            textless.append(elem)
            continue
        x1, y1, x2, y2 = elem['bbox']
        score = score_text(text, parsed)
        score *= spatial_weight((x1 + x2) / 2, (y1 + y2) / 2, physical_w, physical_h, parsed['spatial'])
        scored.append((score, elem['bbox'], text, 'yolo-ocr'))

    scored.sort(key=lambda s: s[0], reverse=True)

    if not scored or scored[0][0] < _YOLO_SCORE_THRESHOLD:
        # No labeled box matched — caption real icons. Skip huge boxes (panels,
        # images): icons are small.
        area_cap = 0.15 * physical_w * physical_h
        textless = [e for e in textless
                    if (e['bbox'][2] - e['bbox'][0]) * (e['bbox'][3] - e['bbox'][1]) < area_cap]
        textless.sort(key=lambda e: e['conf'], reverse=True)
        for elem in textless[:_CAPTION_MAX_CROPS]:
            x1, y1, x2, y2 = elem['bbox']
            crop = frame_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            caption = _caption_crop(crop)
            if not caption:
                continue
            score = score_text(caption, parsed)
            score *= spatial_weight((x1 + x2) / 2, (y1 + y2) / 2, physical_w, physical_h, parsed['spatial'])
            print(f'[Locator] icon caption: {caption!r} score={score:.2f}')
            scored.append((score, elem['bbox'], caption, 'yolo-caption'))
        scored.sort(key=lambda s: s[0], reverse=True)

    if scored and scored[0][0] >= _YOLO_SCORE_THRESHOLD:
        best_score, bbox, text, source = scored[0]
        x1, y1, x2, y2 = bbox
        cx = int(((x1 + x2) / 2) * scale_x)
        cy = int(((y1 + y2) / 2) * scale_y)
        print(f'[Locator] YOLO match: {text!r} ({source}) score={best_score:.2f} -> ({cx},{cy})')
        return {'x': cx, 'y': cy, 'source': source, 'text': text, 'score': round(best_score, 2)}

    print('[Locator] YOLO track: no match')
    return None


def locate_ui_element_ex(goal: str) -> dict | None:
    """Locate a UI element by natural language description — three tracks.

    Track 0 — Windows UI Automation accessibility tree (tools/uia.py): exact
              element names/rects straight from the OS. Deterministic, fast,
              zero GPU. Covers native apps, the taskbar, and (after a warm-up
              walk) Chromium/Electron apps.
    Track 1 — full-screen pytesseract (2 OCR calls, short-TTL cached): text
              in canvas-rendered surfaces UIA can't see.
    Track 2 — OmniParser YOLO icon_detect + icon captioning: unlabeled icons.

    Returns {"x", "y", "source", "text", "score", ...} in logical pixels, or
    None. `source` is one of dom-win | uia | ocr | yolo-ocr | yolo-caption —
    callers surface it in tool results so the model knows the match provenance.
    """
    import pyautogui

    parsed = parse_goal(goal)

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

    cap = _get_screen_ocr()
    if cap is None:
        return None
    frame_bgr, physical_w, physical_h, lines = cap
    logical_w, logical_h = pyautogui.size()
    scale_x = logical_w / physical_w
    scale_y = logical_h / physical_h

    # Track 1: full-screen OCR lines (cached)
    result = _ocr_best_line(lines, parsed, physical_w, physical_h, scale_x, scale_y)
    if result:
        return result

    # Track 2: YOLO icon detection + captioning (demoted by USE_PIXEL_FALLBACK)
    if not _pixel_fallback_enabled():
        print('[Locator] Track 2 (OmniParser/YOLO) demoted — USE_PIXEL_FALLBACK is off')
        return None
    print('[Locator] Falling back to YOLO icon track...')
    yolo_result = _yolo_track(frame_bgr, parsed, physical_w, physical_h, scale_x, scale_y, lines)
    _release_omniparser()  # free VRAM immediately after each fallback use
    return yolo_result


def locate_ui_element(goal: str) -> tuple[int, int] | None:
    """Back-compat wrapper around locate_ui_element_ex: (x, y) or None."""
    result = locate_ui_element_ex(goal)
    return (result['x'], result['y']) if result else None


def locate_ui_elements_boxed(goal: str, threshold: float = 0.5) -> list[dict]:
    """Locate ALL on-screen matches for `goal`, returned as logical-pixel boxes.

    Unlike locate_ui_element (which returns a single centre point), this keeps
    every match above `threshold` and exposes the full bounding box — used by
    Assist Mode to draw highlight rectangles. Track 1 (pytesseract, cached via
    _get_screen_ocr) handles all text labels; Track 2 (YOLO icon_detect, OCR
    text only — no captioning, highlights need to stay fast) is the fallback
    when Track 1 finds nothing. Each item: {"box": (l, t, w, h), "score", "text"}.
    """
    import pyautogui

    cap = _get_screen_ocr()
    if cap is None:
        return []
    frame_bgr, physical_w, physical_h, lines = cap
    logical_w, logical_h = pyautogui.size()
    scale_x = logical_w / physical_w
    scale_y = logical_h / physical_h

    parsed = parse_goal(goal)
    matches: list[dict] = []

    # Track 1: cached full-screen OCR — collect every line scoring >= threshold
    for line in lines or []:
        score = score_text(line['text'], parsed)
        x1, y1, x2, y2 = line['box']  # upscaled 2× physical px
        score *= spatial_weight((x1 + x2) / 4, (y1 + y2) / 4, physical_w, physical_h, parsed['spatial'])
        if score < threshold:
            continue
        # /2: OCR ran on a 2× upscaled image
        left = int(x1 / 2 * scale_x)
        top = int(y1 / 2 * scale_y)
        width = int((x2 - x1) / 2 * scale_x)
        height = int((y2 - y1) / 2 * scale_y)
        matches.append({'box': (left, top, width, height), 'score': score, 'text': line['text']})

    # Track 2: YOLO icon detection — only if OCR found nothing (demoted by flag)
    if not matches and _pixel_fallback_enabled():
        for elem in _yolo_candidates(frame_bgr):
            text = _texts_in_box(lines, elem['bbox'])
            if not text:
                continue
            x1, y1, x2, y2 = elem['bbox']
            score = score_text(text, parsed)
            score *= spatial_weight((x1 + x2) / 2, (y1 + y2) / 2, physical_w, physical_h, parsed['spatial'])
            if score < _YOLO_SCORE_THRESHOLD:
                continue
            matches.append({
                'box': (int(x1 * scale_x), int(y1 * scale_y),
                        int((x2 - x1) * scale_x), int((y2 - y1) * scale_y)),
                'score': score, 'text': text,
            })
        _release_omniparser()  # free VRAM immediately after each fallback use

    # Dedup near-identical boxes (centres within ~10px) — keep the higher score
    matches.sort(key=lambda m: m['score'], reverse=True)
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
