"""
Aster Gesture Control — Tier 1 (mediapipe Tasks API, v0.10.x+)
Real-time hand gesture daemon via MediaPipe GestureRecognizer (CPU-only).
Activated by /gesture on|off from Telegram. Mutually exclusive with Sentry.

Gesture map:
  Pointing_Up  (continuous)  → Volume = wrist height (top=100, bottom=0)
  Open_Palm    hold 0.5s     → Pause / Play toggle
  Closed_Fist  hold 0.5s     → Pause Spotify
  Swipe right  (fast move)   → Skip track
  Swipe left   (fast move)   → Previous track
  Thumb_Down   hold 1.5s     → Boss key (Win+D)
  Victory      hold 2.0s     → Screen lock
"""

import collections
import os
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python as _mp_python
from mediapipe.tasks.python import vision as _mp_vision

import config
from tools.system import boss_key, set_system_state, set_volume
from tools.media import (
    pause_spotify, resume_spotify,
    skip_spotify_track, previous_spotify_track,
)

# ── public flag ──────────────────────────────────────────────────────────────
GESTURE_ACTIVE = False

# ── model path ───────────────────────────────────────────────────────────────
_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Aster_Vault", "gesture_recognizer.task",
)
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task"
)

# ── module-level state ───────────────────────────────────────────────────────
_recognizer = None
_cap: cv2.VideoCapture | None = None
_cooldowns: dict[str, float] = {}
_hold_start: dict[str, float] = {}
_last_volume: int = -1
_last_ts_ms: int = 0          # monotonic timestamp guard for Tasks API

# ── timing config ────────────────────────────────────────────────────────────
_COOLDOWN_SECS: dict[str, float] = {
    "Open_Palm":   1.5,
    "Closed_Fist": 1.5,
    "swipe_right": 1.0,
    "swipe_left":  1.0,
    "Thumb_Down":  3.0,
    "Victory":     5.0,
}
_HOLD_REQUIRED: dict[str, float] = {
    "Open_Palm":   0.5,
    "Closed_Fist": 0.5,
    "Thumb_Down":  1.5,
    "Victory":     2.0,
}


# ── model bootstrap ──────────────────────────────────────────────────────────

def _download_model() -> None:
    if os.path.exists(_MODEL_PATH):
        return
    print("[Aster Gesture] gesture_recognizer.task not found — downloading (~25 MB)...")
    import requests
    os.makedirs(os.path.dirname(_MODEL_PATH), exist_ok=True)
    resp = requests.get(_MODEL_URL, timeout=120)
    resp.raise_for_status()
    with open(_MODEL_PATH, "wb") as f:
        f.write(resp.content)
    print(f"[Aster Gesture] Model saved → {_MODEL_PATH}")


def _load_engine() -> None:
    global _recognizer
    if _recognizer is not None:
        return
    _download_model()
    options = _mp_vision.GestureRecognizerOptions(
        base_options=_mp_python.BaseOptions(model_asset_path=_MODEL_PATH),
        running_mode=_mp_vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.7,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.6,
    )
    _recognizer = _mp_vision.GestureRecognizer.create_from_options(options)
    print("[Aster Gesture] MediaPipe GestureRecognizer loaded.")


# ── cooldown / hold helpers ──────────────────────────────────────────────────

def _can_fire(name: str) -> bool:
    return (time.time() - _cooldowns.get(name, 0)) > _COOLDOWN_SECS.get(name, 1.5)


def _mark_fired(name: str) -> None:
    _cooldowns[name] = time.time()


def _check_hold(name: str, required: float) -> bool:
    now = time.time()
    if name not in _hold_start:
        _hold_start[name] = now
    return (now - _hold_start[name]) >= required


def _reset_holds_except(keep: str) -> None:
    for key in list(_hold_start.keys()):
        if key != keep:
            del _hold_start[key]


def _clear_all_holds() -> None:
    _hold_start.clear()


# ── action dispatch ──────────────────────────────────────────────────────────

def _execute(gesture_name: str) -> None:
    print(f"[Aster Gesture] Firing: {gesture_name}")
    try:
        if gesture_name == "Open_Palm":
            if config.SPOTIFY_AVAILABLE and config.sp:
                pb = config.sp.current_playback()
                if pb and pb.get("is_playing"):
                    pause_spotify()
                else:
                    resume_spotify()
            else:
                pause_spotify()
        elif gesture_name == "Closed_Fist":
            pause_spotify()
        elif gesture_name == "swipe_right":
            skip_spotify_track()
        elif gesture_name == "swipe_left":
            previous_spotify_track()
        elif gesture_name == "Thumb_Down":
            boss_key()
        elif gesture_name == "Victory":
            set_system_state("lock")
    except Exception as e:
        print(f"[Aster Gesture] Action error ({gesture_name}): {e}")


# ── inner real-time loop ─────────────────────────────────────────────────────

def _gesture_loop() -> None:
    global _last_volume, _last_ts_ms
    wrist_hist: collections.deque = collections.deque(maxlen=15)

    while GESTURE_ACTIVE:
        ret, frame = _cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        # Tasks API VIDEO mode requires strictly increasing timestamps
        ts_ms = int(time.time() * 1000)
        if ts_ms <= _last_ts_ms:
            ts_ms = _last_ts_ms + 1
        _last_ts_ms = ts_ms

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)

        try:
            result = _recognizer.recognize_for_video(mp_image, ts_ms)
        except Exception as e:
            print(f"[Aster Gesture] Recognition error: {e}")
            time.sleep(0.033)
            continue

        if not result.gestures or not result.hand_landmarks:
            wrist_hist.clear()
            _clear_all_holds()
            time.sleep(0.033)
            continue

        gesture_name = result.gestures[0][0].category_name
        lm = result.hand_landmarks[0]   # list of NormalizedLandmark (x, y, z)
        wrist_x = lm[0].x
        wrist_y = lm[0].y
        wrist_hist.append(wrist_x)

        # ── Volume mode: index finger pointing up (continuous) ────────────
        if gesture_name == "Pointing_Up":
            _clear_all_holds()
            vol = max(0, min(100, int((1.0 - wrist_y) * 100)))
            if abs(vol - _last_volume) >= 5:
                set_volume(vol)
                _last_volume = vol
            time.sleep(0.033)
            continue

        # ── No gesture detected ───────────────────────────────────────────
        if gesture_name == "None":
            # Swipe still detectable via wrist history during "None" frames
            if len(wrist_hist) >= 10:
                delta = wrist_hist[-1] - wrist_hist[0]
                if delta > 0.15 and _can_fire("swipe_right"):
                    _clear_all_holds()
                    wrist_hist.clear()
                    _mark_fired("swipe_right")
                    _execute("swipe_right")
                elif delta < -0.15 and _can_fire("swipe_left"):
                    _clear_all_holds()
                    wrist_hist.clear()
                    _mark_fired("swipe_left")
                    _execute("swipe_left")
                else:
                    _clear_all_holds()
            else:
                _clear_all_holds()
            time.sleep(0.033)
            continue

        # ── Hold-required gestures ────────────────────────────────────────
        required = _HOLD_REQUIRED.get(gesture_name)
        if required:
            _reset_holds_except(gesture_name)
            if _check_hold(gesture_name, required) and _can_fire(gesture_name):
                _clear_all_holds()
                _mark_fired(gesture_name)
                _execute(gesture_name)
        else:
            _clear_all_holds()

        time.sleep(0.033)


# ── outer daemon ─────────────────────────────────────────────────────────────

def gesture_daemon() -> None:
    global _cap, _last_volume
    print("[Aster Gesture] Gesture control daemon initialized (inactive).")

    while True:
        if GESTURE_ACTIVE:
            print("[Aster Gesture] Activating — loading engine and opening camera.")
            try:
                _load_engine()
                _cap = cv2.VideoCapture(0)
                if not _cap.isOpened():
                    print("[Aster Gesture] ERROR: Could not open webcam.")
                    time.sleep(2.0)
                    continue
                for _ in range(5):      # warm-up (same as vision.py pattern)
                    _cap.read()
                _last_volume = -1
                _clear_all_holds()
                _gesture_loop()         # blocks until GESTURE_ACTIVE goes False
            except Exception as e:
                print(f"[Aster Gesture] Unexpected error: {e}")
            finally:
                if _cap is not None:
                    _cap.release()
                    _cap = None
                print("[Aster Gesture] Camera released.")
        else:
            time.sleep(0.1)
