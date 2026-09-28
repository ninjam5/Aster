import base64
import cv2
import face_recognition
import re
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


def _analyze_frame_with_llm(img_b64: str) -> str | None:
    """Send a webcam frame to the vision model for scene analysis via native REST endpoint."""
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

        from core.brain import _execute_llm_completion
        return _execute_llm_completion(messages=one_shot_msg, temperature=0.1, n_predict=20).get("content") or ""
    except Exception as e:
        print(f"[Sentry Vision] Vision analysis failed: {e}")
        return None


def _face_match_report(img_b64: str):
    """(text report of the face matches, known names) from a webcam frame — no LLM.

    Returns ("", []) when the frame cannot be decoded. "no faces visible" when it holds
    no faces at all (which is itself a complete answer: EMPTY).
    """
    try:
        import base64
        import numpy as np
        raw = img_b64.split(",", 1)[-1] if "," in img_b64 else img_b64
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(raw), np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return "", []
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb)
        if not locations:
            return "no faces visible", []
        encodings = face_recognition.face_encodings(rgb, locations)
    except Exception as e:
        print(f"[Sentry] face report failed: {e}")
        return "", []

    lines = []
    for i, encoding in enumerate(encodings, 1):
        if known_face_encodings:
            distances = face_recognition.face_distance(known_face_encodings, encoding)
            best = int(distances.argmin())
            lines.append(f"face {i}: closest enrolled face is '{known_face_names[best]}' "
                         f"(distance {distances[best]:.2f}; a match is usually under 0.5)")
        else:
            lines.append(f"face {i}: no known faces are enrolled")
    return "; ".join(lines), list(known_face_names)


def _classify_frame_from_faces(img_b64: str):
    """Text-only Sentry classification (ID 4): owner name / KNOWN:<name> / UNKNOWN / EMPTY.

    Uses the `face_recognition` match text instead of a vision prefill, which is the
    expensive path on this install. Returns None on ANY doubt so the caller falls back
    to the vision call — exactly today's behaviour.

    Kernel off returns None BEFORE any face work: with Laya disabled this route must
    not add a per-sweep face pass to installs that never opted in.
    """
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return None
    except Exception:
        return None
    report, names = _face_match_report(img_b64)
    if report == "no faces visible":
        return "EMPTY"
    if not report:
        return None
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return None
        # Key space is RESERVED: A = owner, U/E/Z = the special labels, and known
        # people get a disjoint range. (An earlier version used B, C, D… for names and
        # then wrote "E" for EMPTY, so with >=4 enrolled faces the 4th person's option
        # was silently overwritten by "EMPTY" and became unselectable.)
        label_of = {"A": config.OWNER_NAME.upper()}
        criteria = {"A": f"the owner, {config.OWNER_NAME}"}
        owner_lower = config.OWNER_NAME.lower()
        known = [n for n in names if str(n).lower() != owner_lower]
        person_keys = "BCDFGHIJKLMNOPQRSTVWXY"   # no A, E, U or Z
        for key, name in zip(person_keys, known):
            label_of[key] = f"KNOWN:{name}"
            criteria[key] = f"a known person: {name}"
        label_of["U"] = "UNKNOWN"
        criteria["U"] = "an unrecognized person (no enrolled face matches)"
        label_of["E"] = "EMPTY"
        criteria["E"] = "no people are visible"
        criteria["Z"] = "cannot tell from this report"
        verdict = system1.choose(
            "Who is visible in this webcam frame, based on the face-match report?",
            criteria, key="sentry", state={"report": report}, min_margin=0.5)
    except Exception:
        return None
    if verdict.get("escalate"):
        return None
    return label_of.get(verdict.get("choice"))


def execute_sentry_sweep():
    global WAITING_FOR_ID
    if WAITING_FOR_ID or not telegram_bot:
        return

    try:
        # Capture raw frame as base64 for native vision
        img_b64 = capture_frame_base64()
        if not img_b64:
            return

        # Route 0 (ID 4): classify from the face-match text first — no vision prefill.
        # Falls through to the vision call on any doubt.
        analysis = _classify_frame_from_faces(img_b64)
        if not analysis:
            analysis = _analyze_frame_with_llm(img_b64)   # Route 1: native vision
        if analysis:
            analysis_upper = analysis.upper()
            current_time = time.time()

            # QA 2026-09-28: check KNOWN: FIRST. The owner test used to run first as a
            # bare substring, so a known person whose name embeds the owner's ("Dana"
            # when the owner is "Dan") was swallowed as "owner home" and never alerted.
            if analysis_upper.startswith("KNOWN:"):
                name = analysis.split(":", 1)[1].strip().title()
                last_seen = notification_cooldowns.get(name, 0)
                if current_time - last_seen > COOLDOWN_SECONDS:
                    telegram_bot.send_message(chat_id, f"[Sentry Alert] {name} is currently in the room.")
                    notification_cooldowns[name] = current_time
                return

            if re.search(rf"\b{re.escape(config.OWNER_NAME.upper())}\b", analysis_upper):
                # Owner is home. No alert.
                return

            if "UNKNOWN" in analysis_upper:
                # Unknown person detected — snap pic and alert
                WAITING_FOR_ID = True
                temp_path = os.path.join(config.VAULT_DIR, "temp_intruder.jpg")
                # QA round 2: use the MODULE-LEVEL base64. A local `import base64` here
                # shadowed it for the whole function, so Route 2 (below) hit
                # "cannot access local variable 'base64'" and the fallback was dead code.
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
                if name.lower() == config.OWNER_NAME.lower():
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
