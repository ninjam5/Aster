import cv2
import face_recognition
import time
import threading
import os
import config
from tools.vision import known_face_encodings, known_face_names, load_known_faces, capture_frame_base64

SENTRY_ACTIVE = False
SENTRY_INTERVAL = 5
WAITING_FOR_ID = False
telegram_bot = None
chat_id = None

# Cooldown dictionary to prevent spamming notifications for the same person
notification_cooldowns = {}
COOLDOWN_SECONDS = 300 # 5 minutes


def _analyze_frame_with_gemma(img_b64: str) -> str | None:
    """Send a webcam frame to Gemma for scene analysis via native REST endpoint."""
    try:
        # Sanitize Base64: strip data URI prefixes, newlines, and whitespace
        clean_b64 = img_b64.split(",", 1)[-1] if "," in img_b64 else img_b64
        clean_b64 = clean_b64.replace("\n", "").replace("\r", "").strip()

        one_shot_msg = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean_b64}"}},
                {"type": "text", "text": (
                    "Analyze this webcam frame. Answer ONLY with one of these categories:\n"
                    f"1. {config.OWNER_NAME.upper()} — if {config.OWNER_NAME} (the owner) is visible\n"
                    f"2. KNOWN:<name> — if a known person other than {config.OWNER_NAME} is visible (e.g., KNOWN:George)\n"
                    "3. UNKNOWN — if an unrecognized person is visible\n"
                    "4. EMPTY — if no people are visible\n"
                    "Be concise. One line only."
                )},
            ],
        }]

        from core.brain import _execute_gemma_completion
        return _execute_gemma_completion(messages=one_shot_msg, temperature=0.1, n_predict=20).get("content") or ""
    except Exception as e:
        print(f"[Sentry Gemma] Vision analysis failed: {e}")
        return None


def execute_sentry_sweep():
    global WAITING_FOR_ID
    if WAITING_FOR_ID or not telegram_bot:
        return

    try:
        # Capture raw frame as base64 for Gemma native vision
        img_b64 = capture_frame_base64()
        if not img_b64:
            return

        # Route 1: Use Gemma native vision via REST endpoint
        analysis = _analyze_frame_with_gemma(img_b64)
        if analysis:
            analysis_upper = analysis.upper()
            current_time = time.time()

            if config.OWNER_NAME.upper() in analysis_upper:
                # Owner is home. No alert.
                return

            if analysis_upper.startswith("KNOWN:"):
                name = analysis.split(":", 1)[1].strip().title()
                last_seen = notification_cooldowns.get(name, 0)
                if current_time - last_seen > COOLDOWN_SECONDS:
                    telegram_bot.send_message(chat_id, f"[Sentry Alert] {name} is currently in the room.")
                    notification_cooldowns[name] = current_time
                return

            if "UNKNOWN" in analysis_upper:
                # Unknown person detected — snap pic and alert
                WAITING_FOR_ID = True
                temp_path = os.path.join(config.VAULT_DIR, "temp_intruder.jpg")
                # Decode base64 back to image for Telegram
                import base64
                img_bytes = base64.b64decode(img_b64)
                with open(temp_path, "wb") as f:
                    f.write(img_bytes)

                with open(temp_path, "rb") as photo:
                    telegram_bot.send_photo(
                        chat_id,
                        photo,
                        caption="\U0001F6A8 [SENTRY ALERT] Unknown person detected. Who is this? (Reply with just their name)"
                    )
                return

            # EMPTY or unrecognized response — no action
            return

        # Route 2: Fallback to face_recognition if engine not loaded or returned None
        rgb_frame = cv2.cvtColor(
            cv2.imdecode(
                __import__("numpy").frombuffer(base64.b64decode(img_b64), __import__("numpy").uint8),
                cv2.IMREAD_COLOR,
            ),
            cv2.COLOR_BGR2RGB,
        )
        face_locations = face_recognition.face_locations(rgb_frame)
        if not face_locations:
            return

        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
        current_time = time.time()

        for encoding in face_encodings:
            matches = face_recognition.compare_faces(known_face_encodings, encoding, tolerance=0.5)

            if True in matches:
                name = known_face_names[matches.index(True)]
                if name.lower() == "mohamed":
                    continue
                last_seen = notification_cooldowns.get(name, 0)
                if current_time - last_seen > COOLDOWN_SECONDS:
                    telegram_bot.send_message(chat_id, f"[Sentry Alert] {name} is currently in the room.")
                    notification_cooldowns[name] = current_time
            else:
                WAITING_FOR_ID = True
                temp_path = os.path.join(config.VAULT_DIR, "temp_intruder.jpg")
                cv2.imwrite(temp_path, cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR))
                with open(temp_path, "rb") as photo:
                    telegram_bot.send_photo(
                        chat_id,
                        photo,
                        caption="\U0001F6A8 [SENTRY ALERT] Unknown person detected. Who is this? (Reply with just their name)"
                    )
                return

    except Exception as e:
        print(f"[Aster SENTRY Error: {e}]")


def sentry_daemon():
    print(f"[Aster Core] Sentry Subsystem initialized.")
    while True:
        if SENTRY_ACTIVE:
            execute_sentry_sweep()
        time.sleep(SENTRY_INTERVAL)
