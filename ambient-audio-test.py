"""ambient-audio-test.py — standalone mock test for Tier-1b ambient room audio.

Runs WITHOUT sounddevice / webrtcvad / a microphone / the SpeechBrain model /
llama-server. It stubs the CPU voice classifier (so no model loads) and drives
synthetic probability vectors through the real EMA + label-mapping + fusion code
in tools/emotion_recognition.py, plus the owner-gate / call-pause logic in
tools/ambient_audio.py, to verify:

  * the 4-class IEMOCAP -> 5-label mapping via the EMA argmax path
  * EMA smoothing damps a single off-utterance
  * the confidence gate holds the previous read on a low-confidence read
  * a clip shorter than the minimum holds the previous read
  * fusion order text > face > ambient-voice (the brain's _with_face composition)
  * the awareness ambient block renders a "Voice tone:" line only when enabled
  * the owner gate drops non-Mohamed speech, the LiveKit-call pause skips, and a
    neutral read is not fed into the sustained-mood window
  * the disabled path is a hard no-op (always 'neutral')

Usage:  python ambient-audio-test.py        (exit code 0 = all passed)
"""

import sys
import types
from datetime import datetime

import numpy as np
import torch

# ── Import config with emotion disabled so eager model loads are skipped, then
#    re-enable + force the ambient flags we need for the logic under test. ──────
import config
config.EMOTION_ENABLED = False
import tools.emotion_recognition as er          # noqa: E402
import tools.awareness as aw                    # noqa: E402

config.EMOTION_ENABLED = True
config.AMBIENT_AUDIO_ENABLED = True
config.EMOTION_MIN_CONFIDENCE = 0.5
config.AMBIENT_VOICE_EMA_ALPHA = 0.4
config.AMBIENT_VOICE_GATE_OWNER = True
config.AMBIENT_MIN_UTTERANCE_SECONDS = 2.0
config.AMBIENT_WINDOW_FEED_COOLDOWN = 0.0
config.OWNER_NAME = "Mohamed"

# 4-class IEMOCAP label order (we own this map in the test).
IDX_TO_RAW = {0: "neu", 1: "ang", 2: "hap", 3: "sad"}
RAW_TO_IDX = {v: k for k, v in IDX_TO_RAW.items()}


# ── Stub the CPU classifier: classify_batch returns whatever we queue. ─────────
class _FakeVoiceClf:
    next_probs = None
    def classify_batch(self, waveform):
        p = np.asarray(_FakeVoiceClf.next_probs, dtype=float)
        idx = int(p.argmax())
        out_prob = torch.tensor(p).unsqueeze(0)          # (1, 4)
        score = torch.tensor([float(p[idx])])            # (1,)
        index = torch.tensor([idx])
        text_lab = [IDX_TO_RAW[idx]]
        return out_prob, score, index, text_lab


er._cpu_voice_clf = _FakeVoiceClf()
er._cpu_voice_ind2lab = dict(IDX_TO_RAW)

# A 2-second clip clears the min-utterance guard; a 0.5 s clip trips it.
LONG = np.zeros(int(16000 * 2.0), dtype=np.float32)
SHORT = np.zeros(int(16000 * 0.5), dtype=np.float32)


def _probs(raw, peak=1.0):
    v = np.full(4, (1.0 - peak) / 3.0, dtype=float)
    v[RAW_TO_IDX[raw]] = peak
    return v


def _feed(raw, peak=1.0, audio=LONG):
    _FakeVoiceClf.next_probs = _probs(raw, peak)
    return er.detect_ambient_voice_emotion(audio)


# ── Test harness ───────────────────────────────────────────────────────────────
_results = []


def check(cond, label):
    _results.append((bool(cond), label))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


# ── 1. Label mapping (4 raw -> normalized), cold-start EMA each time ────────────
print("\nLabel mapping (4-class IEMOCAP -> 5-label vocab)")
EXPECT = {"hap": "happy", "sad": "sad", "ang": "frustrated", "neu": "neutral"}
for raw, norm in EXPECT.items():
    er.reset_ambient_voice_mood()
    got = _feed(raw)
    check(got == norm, f"{raw} -> {norm} (got {got})")


# ── 2. EMA smoothing damps a single off-utterance ──────────────────────────────
print("\nEMA smoothing")
er.reset_ambient_voice_mood()
for _ in range(4):
    _feed("hap")                                # settle EMA on happy
check(er.get_ambient_voice_mood() == "happy", "steady happy reads -> happy")
got = _feed("sad")                              # one off-read at alpha=0.4
check(got == "happy", "single sad blip is damped -> stays happy")

er.reset_ambient_voice_mood()
config.AMBIENT_VOICE_EMA_ALPHA = 1.0
for _ in range(3):
    _feed("hap")
check(_feed("sad") == "sad", "alpha=1.0 (no smoothing) -> blip flips to sad")
config.AMBIENT_VOICE_EMA_ALPHA = 0.4


# ── 3. Confidence gate holds the previous read ─────────────────────────────────
print("\nConfidence gate")
er.reset_ambient_voice_mood()
_feed("hap")                                    # confident happy
_FakeVoiceClf.next_probs = np.full(4, 0.25)     # uniform -> max 0.25 < 0.5 threshold
got = er.detect_ambient_voice_emotion(LONG)
check(got == "happy", "low-confidence read holds previous read (happy)")


# ── 4. Short clip holds the previous read ──────────────────────────────────────
print("\nShort-clip guard")
er.reset_ambient_voice_mood()
_feed("sad")
got = _feed("hap", audio=SHORT)                 # below min-utterance seconds
check(got == "sad", "clip < min seconds -> holds last read (sad)")


# ── 5. Fusion order: text > face > ambient-voice (brain's _with_face) ───────────
print("\nFusion order (text > face > ambient-voice)")
def _compose(text, face, voice):
    return er.fuse_face(er.fuse_face(text, face), voice)
check(_compose("happy", "sad", "frustrated") == "happy", "confident text wins over face+voice")
check(_compose("neutral", "sad", "frustrated") == "sad", "neutral text -> face fills in")
check(_compose("neutral", "neutral", "frustrated") == "frustrated", "neutral text+face -> voice fills in")
check(_compose("neutral", "neutral", "neutral") == "neutral", "all neutral -> neutral")


# ── 6. Ambient block renders 'Voice tone:' only when enabled ───────────────────
print("\nAwareness ambient block")
aw.current_context["last_capture"] = datetime.now()
er.reset_ambient_voice_mood()
_feed("ang")                                    # ambient voice = frustrated
config.AMBIENT_AUDIO_ENABLED = True
block = aw._ambient_block() or ""
check("Voice tone: frustrated" in block, "enabled -> ambient block shows 'Voice tone:'")
config.AMBIENT_AUDIO_ENABLED = False
block_off = aw._ambient_block() or ""
check("Voice tone" not in block_off, "disabled -> no 'Voice tone:' line")
config.AMBIENT_AUDIO_ENABLED = True


# ── 7. Owner gate + call-pause + window feed (tools/ambient_audio._process_utterance) ──
print("\nOwner gate / call pause / window feed")
import tools.ambient_audio as aa                # noqa: E402

# Fake webrtc_bridge + voice_recognition so no LiveKit / ECAPA load.
_call_active = {"v": False}
_fake_bridge = types.ModuleType("webrtc_bridge")
_fake_bridge.call_is_active = lambda: _call_active["v"]
sys.modules["webrtc_bridge"] = _fake_bridge

_speaker = {"v": "Mohamed"}
_fake_vr = types.ModuleType("tools.voice_recognition")
_fake_vr.identify_speaker = lambda audio, sr=16000: _speaker["v"]
sys.modules["tools.voice_recognition"] = _fake_vr

# Track detect + window-feed calls without running the real model.
_detect_calls = {"n": 0}
_fed = []
_real_detect = er.detect_ambient_voice_emotion
_mood_out = {"v": "frustrated"}
er.detect_ambient_voice_emotion = lambda audio, sr=16000: (_detect_calls.__setitem__("n", _detect_calls["n"] + 1) or _mood_out["v"])
er.log_mood_turn = lambda mood, context="": _fed.append((mood, context))

CLIP = np.zeros(int(16000 * 3.0), dtype=np.float32)

# (a) LiveKit call active -> skip entirely.
_call_active["v"] = True
_detect_calls["n"] = 0
aa._process_utterance(CLIP)
check(_detect_calls["n"] == 0, "call active -> utterance skipped (no detect)")
_call_active["v"] = False

# (b) Non-owner speaker -> dropped.
_speaker["v"] = "Friend"
_detect_calls["n"] = 0
aa._process_utterance(CLIP)
check(_detect_calls["n"] == 0, "non-owner speech dropped (no detect)")

# (c) Owner, non-neutral mood -> processed and fed to the window.
_speaker["v"] = "Mohamed"
_mood_out["v"] = "frustrated"
_detect_calls["n"] = 0
_fed.clear()
aa._process_utterance(CLIP)
check(_detect_calls["n"] == 1, "owner speech -> detect runs once")
check(_fed == [("frustrated", "(ambient room)")], "non-neutral owner read fed to mood window")

# (d) Owner, neutral mood -> not fed.
_mood_out["v"] = "neutral"
_fed.clear()
aa._process_utterance(CLIP)
check(_fed == [], "neutral read is NOT fed to the mood window")

# restore
er.detect_ambient_voice_emotion = _real_detect


# ── 8. Disabled path is a hard no-op ───────────────────────────────────────────
print("\nDisabled no-op")
er._ambient_voice_mood = "sad"
config.AMBIENT_AUDIO_ENABLED = False
check(er.get_ambient_voice_mood() == "neutral", "disabled -> get_ambient_voice_mood() == neutral")
check(er.detect_ambient_voice_emotion(LONG) == "neutral", "disabled -> detect == neutral")
config.AMBIENT_AUDIO_ENABLED = True
check(er.get_ambient_voice_mood() == "sad", "re-enabled -> accessor returns the held read")


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
print("All ambient-audio checks passed.")
sys.exit(0)
