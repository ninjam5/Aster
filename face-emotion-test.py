"""face-emotion-test.py — standalone mock test for Tier-2 facial emotion (HSEmotion).

Runs WITHOUT onnxruntime / a webcam / llama-server. It stubs the recognizer (so no
ONNX model is loaded) and the face-crop helper (so no real detection runs), then
drives synthetic probability vectors through the real EMA + label-mapping + fusion
code in tools/emotion_recognition.py to verify:

  * the 8-class AffectNet → 5-label mapping (_FACE_RAW_TO_NORMALIZED)
  * EMA smoothing damps a single off-frame
  * the confidence gate holds the previous read on a low-confidence frame
  * a missed detection (no face) holds the last read (doesn't wipe to neutral)
  * fuse_face policy (base content wins; neutral base → face; both neutral → neutral)
  * the awareness ambient block renders a "Face read:" line only when enabled
  * the disabled path is a hard no-op (always 'neutral')

Usage:  python face-emotion-test.py        (exit code 0 = all passed)
"""

import sys
from datetime import datetime

import numpy as np

# ── Import config with emotion disabled so the eager model loads are skipped,
#    then re-enable + force the face flags we need for the logic under test. ────
import config
config.EMOTION_ENABLED = False
import tools.emotion_recognition as er          # noqa: E402
import tools.vision as vision                   # noqa: E402
import tools.awareness as aw                    # noqa: E402

config.EMOTION_ENABLED = True
config.FACE_EMOTION_ENABLED = True
config.FACE_EMOTION_MIN_CONFIDENCE = 0.5
config.FACE_EMOTION_EMA_ALPHA = 0.4

# 8-class AffectNet label order (matches HSEmotion's enet_*_8 idx_to_class).
IDX_TO_CLASS = {0: "Anger", 1: "Contempt", 2: "Disgust", 3: "Fear",
                4: "Happiness", 5: "Neutral", 6: "Sadness", 7: "Surprise"}
NAME_TO_IDX = {v: k for k, v in IDX_TO_CLASS.items()}


# ── Stub the recognizer: predict_emotions returns whatever vector we queue. ─────
class _FakeClf:
    next_scores = None
    def predict_emotions(self, crop, logits=True):
        s = _FakeClf.next_scores
        idx = int(np.asarray(s).argmax())
        return IDX_TO_CLASS[idx], s


er._face_clf = _FakeClf()
er._face_idx_to_class = dict(IDX_TO_CLASS)

# Stub the crop helper so detection always "finds a face" (we control the scores).
_HAVE_FACE = {"v": True}
vision.get_primary_face_crop_rgb = lambda frame: (np.zeros((10, 10, 3), np.uint8)
                                                  if _HAVE_FACE["v"] else None)


def _onehot(name, peak=1.0):
    v = np.full(8, (1.0 - peak) / 7.0, dtype=float)
    v[NAME_TO_IDX[name]] = peak
    return v


def _feed(name, peak=1.0):
    _FakeClf.next_scores = _onehot(name, peak)
    return er.detect_face_emotion("dummy-frame")


# ── Test harness ───────────────────────────────────────────────────────────────
_results = []


def check(cond, label):
    _results.append((bool(cond), label))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


# ── 1. Label mapping (all 8 raw → 5 normalized), cold-start EMA each time ───────
print("\nLabel mapping (8-class AffectNet -> 5-label vocab)")
EXPECT = {
    "Anger": "frustrated", "Contempt": "frustrated", "Disgust": "frustrated",
    "Fear": "anxious", "Happiness": "happy", "Neutral": "neutral",
    "Sadness": "sad", "Surprise": "neutral",
}
for raw, norm in EXPECT.items():
    er.reset_face_mood()
    got = _feed(raw)
    check(got == norm, f"{raw} -> {norm} (got {got})")


# ── 2. EMA smoothing damps a single off-frame ──────────────────────────────────
print("\nEMA smoothing")
er.reset_face_mood()
for _ in range(4):
    _feed("Happiness")                      # settle EMA on happy
check(er.get_face_mood() == "happy", "steady happy frames -> happy")
got = _feed("Sadness")                      # one off-frame at alpha=0.4
check(got == "happy", "single sad blip is damped -> stays happy")

# Contrast: with alpha=1.0 (no smoothing) the same blip flips immediately.
er.reset_face_mood()
config.FACE_EMOTION_EMA_ALPHA = 1.0
for _ in range(3):
    _feed("Happiness")
check(_feed("Sadness") == "sad", "alpha=1.0 (no smoothing) -> blip flips to sad")
config.FACE_EMOTION_EMA_ALPHA = 0.4


# ── 3. Confidence gate holds the previous read ─────────────────────────────────
print("\nConfidence gate")
er.reset_face_mood()
_feed("Happiness")                          # confident happy
low = np.full(8, 0.125, dtype=float)        # uniform → max 0.125 < 0.5 threshold
_FakeClf.next_scores = low
got = er.detect_face_emotion("dummy-frame")
check(got == "happy", "low-confidence frame holds previous read (happy)")


# ── 4. Missed detection (no face) holds last read ──────────────────────────────
print("\nMissed detection")
er.reset_face_mood()
_feed("Sadness")
_HAVE_FACE["v"] = False
got = er.detect_face_emotion("dummy-frame")
_HAVE_FACE["v"] = True
check(got == "sad", "no face this frame -> holds last read (sad), not neutral")


# ── 5. fuse_face policy ─────────────────────────────────────────────────────────
print("\nfuse_face policy")
check(er.fuse_face("happy", "sad") == "happy", "confident base content wins over face")
check(er.fuse_face("neutral", "sad") == "sad", "neutral base -> face read fills in")
check(er.fuse_face("neutral", "neutral") == "neutral", "both neutral -> neutral")
check(er.fuse_face("frustrated", "neutral") == "frustrated", "base kept when face neutral")


# ── 6. Ambient block renders 'Face read:' only when enabled ────────────────────
print("\nAwareness ambient block")
aw.current_context["last_capture"] = datetime.now()
aw.current_context["face_mood"] = "frustrated"
config.FACE_EMOTION_ENABLED = True
block = aw._ambient_block() or ""
check("Face read: frustrated" in block, "enabled -> ambient block shows 'Face read:'")
config.FACE_EMOTION_ENABLED = False
block_off = aw._ambient_block() or ""
check("Face read" not in block_off, "disabled -> no 'Face read:' line")
config.FACE_EMOTION_ENABLED = True


# ── 7. Disabled path is a hard no-op ───────────────────────────────────────────
print("\nDisabled no-op")
er._face_mood = "sad"
config.FACE_EMOTION_ENABLED = False
check(er.get_face_mood() == "neutral", "disabled -> get_face_mood() == neutral")
check(er.detect_face_emotion("dummy-frame") == "neutral", "disabled -> detect == neutral")
config.FACE_EMOTION_ENABLED = True
check(er.get_face_mood() == "sad", "re-enabled -> accessor returns the held read")


# ── Summary ─────────────────────────────────────────────────────────────────────
passed = sum(1 for ok, _ in _results if ok)
total = len(_results)
print(f"\n{'=' * 60}\n{passed}/{total} checks passed")
if passed != total:
    print("FAILURES:")
    for ok, label in _results:
        if not ok:
            print(f"  - {label}")
    sys.exit(1)
print("All facial-emotion checks passed.")
sys.exit(0)
