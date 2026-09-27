"""User emotion recognition — two-tier pipeline.

Tier 0 (text, CPU, eager):
    j-hartmann/emotion-english-distilroberta-base
    7-class → normalized 5-label (happy / sad / frustrated / anxious / neutral).
    Loaded at module import. Zero VRAM cost.

Tier 1 (voice, CUDA, lazy / ref-counted):
    speechbrain/emotion-recognition-wav2vec2-IEMOCAP
    4-class (hap / sad / ang / neu) → normalized 4-label.
    Loaded on first voice I/O, offloaded when no consumer holds it.
    Lifecycle mirrors tools/audio.py Kokoro acquire / release.

Normalized label set (user-facing):
    happy | sad | frustrated | anxious | neutral

Both tiers always return a label string — never None.
'neutral' is the safe fallback for any error, short audio, or low-confidence read.

Raw → normalized mapping:

    Text model (7-class)          Voice model (IEMOCAP 4-class)
    joy           → happy         hap  → happy
    sadness       → sad           sad  → sad
    anger/disgust → frustrated    ang  → frustrated
    fear          → anxious       (none — voice can't detect anxious)
    neutral/surp. → neutral       neu  → neutral

Integration notes:
- detect_text_emotion()  is the single call for typed text (CLI / Telegram / captions).
- detect_voice_emotion() self-manages acquire/release; safe to call anywhere.
  During a LiveKit call the STT holds an outer acquire to keep the model warm between
  utterances — each _transcribe call then just bumps refcount 1→2→1.
"""

from __future__ import annotations

import gc
import io
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import numpy as np
import torch
import config

# ── Shared constants ──────────────────────────────────────────────────────────
MIN_UTTERANCE_SECONDS = 1.0   # shorter clips → neutral (same guard as voice ID)
VALID_MOODS = frozenset({"happy", "sad", "frustrated", "anxious", "neutral"})

# ── Label normalization tables ────────────────────────────────────────────────
_TEXT_RAW_TO_NORMALIZED: dict[str, str] = {
    "joy":      "happy",
    "sadness":  "sad",
    "anger":    "frustrated",
    "disgust":  "frustrated",
    "fear":     "anxious",
    "neutral":  "neutral",
    "surprise": "neutral",
}

_VOICE_RAW_TO_NORMALIZED: dict[str, str] = {
    "hap": "happy",
    "sad": "sad",
    "ang": "frustrated",
    "neu": "neutral",
}

# ── Tier 0 — text model (CPU, eager) ─────────────────────────────────────────
_text_pipe = None
_text_pipe_lock = threading.Lock()


def load_text_emotion_model():
    """Load the text-emotion HuggingFace pipeline to CPU.  Called once at module import."""
    global _text_pipe
    with _text_pipe_lock:
        if _text_pipe is not None:
            return
        if not config.EMOTION_ENABLED:
            print("[Aster Mood] Text emotion model disabled by config.")
            return
        try:
            from transformers import pipeline as hf_pipeline
            print(f"[Aster Mood] Loading text emotion model ({config.EMOTION_TEXT_MODEL}) to CPU...")

            # transformers ≥ 4.47 blocks torch.load on .bin pickles unless torch ≥ 2.6
            # (CVE-2025-32434).  The j-hartmann model ships .bin only (no safetensors).
            # The CVE concerns untrusted pickle files; this is a published HuggingFace
            # model so the risk is not applicable here.
            #
            # modeling_utils imports the guard with `from ... import check_torch_load_is_safe`
            # so we must patch the name in transformers.modeling_utils (not import_utils).
            _orig_check = None
            try:
                import transformers.modeling_utils as _tmu
                _orig_check = _tmu.__dict__.get("check_torch_load_is_safe")
                if _orig_check is not None:
                    _tmu.check_torch_load_is_safe = lambda: None
            except Exception:
                pass

            try:
                _text_pipe = hf_pipeline(
                    "text-classification",
                    model=config.EMOTION_TEXT_MODEL,
                    top_k=None,
                    device=-1,          # CPU only — zero VRAM cost
                    truncation=True,
                    max_length=512,
                )
            finally:
                # Always restore — even if the pipeline call fails
                if _orig_check is not None:
                    try:
                        import transformers.modeling_utils as _tmu
                        _tmu.check_torch_load_is_safe = _orig_check
                    except Exception:
                        pass

            print("[Aster Mood] Text emotion model online.")
        except Exception as e:
            print(f"[Aster Mood] Text emotion model failed to load: {e}")
            _text_pipe = None


def detect_text_emotion(text: str) -> str:
    """Detect mood from plain text.  Always returns a normalized label string.

    Strips leading [Speaker: …] and [Mood: …] tags before analysis so only the
    actual human text is scored.  Returns 'neutral' on any error or if the model
    is unavailable / disabled.
    """
    if not config.EMOTION_ENABLED or _text_pipe is None:
        return "neutral"
    if not text or not text.strip():
        return "neutral"

    # Strip known inline tags before classification
    clean = text.strip()
    import re
    clean = re.sub(r"^\s*\[Speaker:[^\]]*\]\s*", "", clean)
    clean = re.sub(r"^\s*\[Mood:[^\]]*\]\s*", "", clean)
    clean = clean.strip()
    if not clean:
        return "neutral"

    try:
        results = _text_pipe(clean)
        # pipeline returns a list of lists when top_k=None → flatten one level
        if results and isinstance(results[0], list):
            results = results[0]
        if not results:
            return "neutral"

        # Find highest-scoring label
        best = max(results, key=lambda x: x["score"])
        if best["score"] < config.EMOTION_MIN_CONFIDENCE:
            return "neutral"
        raw_label = best["label"].lower()
        return _TEXT_RAW_TO_NORMALIZED.get(raw_label, "neutral")

    except Exception as e:
        print(f"[Aster Mood] Text emotion detection error: {e}")
        return "neutral"


# ── Tier 1 — voice model (CUDA, lazy, ref-counted) ───────────────────────────
_voice_clf = None
_voice_users = 0
_voice_clf_lock = threading.RLock()


def _prepare_speechbrain_imports():
    """Make the IEMOCAP wav2vec2 model importable on this machine.

    The model's hparams reference the *deprecated* dotted path
    ``speechbrain.lobes.models.huggingface_transformers.wav2vec2``.  SpeechBrain
    1.1 resolves that through a lazy ``DeprecatedModuleRedirect`` whose import
    machinery incidentally drags in optional integrations we don't have
    installed (``k2_fsa`` → the k2 library, ``nlp`` → flair).  Worse, the
    resolver (``pydoc.locate`` inside hyperpyyaml) walks module attributes under
    ``inspect.py``, which trips SpeechBrain's own LazyModule guard if torch /
    transformers aren't already fully imported.  We neutralise all of it here:

      1. Eager-import ``torch.distributed.tensor`` + ``transformers`` so the lazy
         attribute walk can't trigger them half-loaded under ``inspect.py``.
      2. Register harmless stub modules for the optional integrations we lack so
         their lazy import returns the stub instead of executing (and failing).
      3. Pre-import the *real* HuggingFace integration module and alias it under
         the deprecated path, so ``pydoc.locate`` finds it cached and never
         touches the broken redirect.

    All best-effort; every step is individually guarded so a partial environment
    never hard-crashes the loader (it just falls through to the normal failure
    path, which returns 'neutral')."""
    import sys, types, importlib
    # 1. Eager-import the heavy deps the lazy walk would otherwise trigger.
    try:
        import torch.distributed.tensor  # noqa: F401
    except Exception:
        pass
    try:
        import transformers  # noqa: F401
    except Exception:
        pass
    try:
        import speechbrain.integrations as _sb_int
        # 2. Stub optional integrations that need uninstalled libs (k2 / flair).
        for _sub in ("k2_fsa", "nlp"):
            _key = f"speechbrain.integrations.{_sub}"
            _existing = sys.modules.get(_key)
            if _existing is None or type(_existing).__name__ == "LazyModule":
                _stub = types.ModuleType(_key)
                _stub.__path__ = []          # behave as a package for submodule access
                sys.modules[_key] = _stub
                setattr(_sb_int, _sub, _stub)
        sys.modules.setdefault(
            "speechbrain.k2_integration", sys.modules["speechbrain.integrations.k2_fsa"]
        )
        # 3. Pre-import the real HF integration, alias it under the deprecated path.
        _hf = importlib.import_module("speechbrain.integrations.huggingface")
        _w2v = importlib.import_module("speechbrain.integrations.huggingface.wav2vec2")
        sys.modules["speechbrain.lobes.models.huggingface_transformers"] = _hf
        sys.modules["speechbrain.lobes.models.huggingface_transformers.wav2vec2"] = _w2v
    except Exception as e:
        print(f"[Aster Mood] SpeechBrain import-prep warning: {e}")


def acquire_voice_emotion_model():
    """Load the voice-emotion classifier to CUDA if not already loaded, increment
    the user count.  Safe to call from multiple threads.  Returns the classifier
    or None on failure."""
    global _voice_clf, _voice_users
    with _voice_clf_lock:
        if _voice_clf is None:
            if not config.EMOTION_ENABLED:
                return None
            try:
                _prepare_speechbrain_imports()
                from speechbrain.inference.interfaces import foreign_class
                print(f"[Aster Mood] Loading voice emotion model ({config.EMOTION_VOICE_MODEL}) to CUDA...")

                # transformers ≥ 4.47 blocks torch.load on .bin checkpoints unless
                # torch ≥ 2.6 (CVE-2025-32434).  The wav2vec2 backbone ships .bin
                # only; this is a published HuggingFace model so the untrusted-pickle
                # risk the CVE concerns doesn't apply.  Patch the guard for the load
                # only (same approach as the text model), restore in finally.
                import transformers.modeling_utils as _tmu
                _orig_check = _tmu.__dict__.get("check_torch_load_is_safe")
                if _orig_check is not None:
                    _tmu.check_torch_load_is_safe = lambda: None
                try:
                    _voice_clf = foreign_class(
                        source=config.EMOTION_VOICE_MODEL,
                        pymodule_file="custom_interface.py",
                        classname="CustomEncoderWav2vec2Classifier",
                        savedir=f"pretrained_models/{config.EMOTION_VOICE_MODEL.split('/')[-1]}",
                        run_opts={"device": "cuda:0"},
                    )
                finally:
                    if _orig_check is not None:
                        try:
                            _tmu.check_torch_load_is_safe = _orig_check
                        except Exception:
                            pass
                print("[Aster Mood] Voice emotion model online (CUDA).")
            except Exception as e:
                print(f"[Aster Mood] Voice emotion model failed to load: {e}")
                _voice_clf = None
                return None
        if _voice_clf is not None:
            _voice_users += 1
        return _voice_clf


def release_voice_emotion_model():
    """Drop a consumer's hold.  When the count reaches zero the model is offloaded
    from VRAM and CUDA cache is flushed."""
    global _voice_clf, _voice_users
    with _voice_clf_lock:
        if _voice_users > 0:
            _voice_users -= 1
        if _voice_users <= 0 and _voice_clf is not None:
            _voice_clf = None
            _voice_users = 0
            gc.collect()
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            print("[Aster Mood] Voice emotion model offloaded from VRAM (no active users).")


def detect_voice_emotion(audio, sample_rate: int = 16000) -> str:
    """Detect mood from raw audio.  Self-manages acquire / release lifecycle.

    audio: np.float32 mono 1-D array at `sample_rate` Hz, OR raw WAV bytes.
    Always returns a normalized label string.  'neutral' on any error or short clip.

    Call graph during a LiveKit call:
        STT.__init__  → acquire  (count 1, model loads)
        _transcribe() → detect_voice_emotion → acquire/release  (count 1→2→1, no offload)
        STT.aclose()  → release  (count 1→0, model offloads)

    Call graph for a one-shot Telegram voice note:
        detect_voice_emotion → acquire/release  (count 0→1→0, load then offload)
    """
    if not config.EMOTION_ENABLED:
        return "neutral"

    clf = acquire_voice_emotion_model()
    if clf is None:
        return "neutral"

    try:
        import torchaudio

        if isinstance(audio, (bytes, bytearray)):
            waveform, sr = torchaudio.load(io.BytesIO(bytes(audio)))
            if sr != 16000:
                waveform = torchaudio.transforms.Resample(sr, 16000)(waveform)
            if waveform.shape[0] > 1:
                waveform = waveform.mean(dim=0, keepdim=True)
        elif isinstance(audio, np.ndarray):
            waveform = torch.from_numpy(audio).unsqueeze(0)   # (1, time)
        else:
            return "neutral"

        # Guard against clips too short to be reliable
        duration = waveform.shape[-1] / 16000
        if duration < MIN_UTTERANCE_SECONDS:
            return "neutral"

        norm, conf, _vec = _classify_voice_waveform(clf, waveform, "cuda:0")
        if conf < config.EMOTION_MIN_CONFIDENCE:
            return "neutral"
        return norm

    except Exception as e:
        print(f"[Aster Mood] Voice emotion detection error: {e}")
        return "neutral"
    finally:
        release_voice_emotion_model()


def _classify_voice_waveform(clf, waveform, device: str):
    """Shared inference for both the CUDA and the ambient-CPU voice paths.

    Moves the waveform to `device`, runs the SpeechBrain classifier, and returns
    ``(normalized_label, confidence, prob_vector)``. `prob_vector` is a 1-D numpy
    array of class probabilities (or None if the backend didn't expose it). The
    caller owns confidence-gating and any smoothing — this keeps the CUDA path's
    behavior identical to before the extraction.
    """
    waveform = waveform.to(device)
    with torch.no_grad():
        out_prob, score, index, text_lab = clf.classify_batch(waveform)
    raw_label = text_lab[0] if isinstance(text_lab, (list, tuple)) else str(text_lab)
    raw_label = raw_label.lower().strip()
    conf = score[0].item() if hasattr(score, "__getitem__") else float(score)
    norm = _VOICE_RAW_TO_NORMALIZED.get(raw_label, "neutral")
    try:
        vec = np.asarray(out_prob[0].detach().cpu().numpy(), dtype=float).ravel()
    except Exception:
        vec = None
    return norm, conf, vec


# ── Fusion — combine text content (Tier 0) with voice prosody (Tier 1) ───────
def fuse_moods(text_mood: str, voice_mood: str) -> str:
    """Combine a text-derived and a voice-derived mood into a single label.

    Both inputs are already confidence-gated (each is 'neutral' when its own
    model was unsure).  Policy (approved with the user):

      1. Confident text content wins.  The *words* are the stronger, less
         ambiguous signal and override prosody.  This is the fix for the IEMOCAP
         voice model reading high-arousal anger as 'happy': the angry words
         ("make them pay") label correctly even when the tone fools the audio
         model.
      2. When text is neutral but the voice is charged, trust the prosody — the
         "I'm fine" said-sadly case that text alone would miss.
      3. Both neutral → neutral.
    """
    if text_mood and text_mood != "neutral":
        return text_mood
    if voice_mood and voice_mood != "neutral":
        return voice_mood
    return "neutral"


def detect_combined_emotion(audio, transcript: str | None = None,
                            sample_rate: int = 16000) -> str:
    """Fuse voice prosody (Tier 1) with transcript content (Tier 0) for a voice
    message.  This is the entry point both voice paths should call.

    audio      : np.float32 mono array OR raw WAV bytes (prosody read).
    transcript : the recognised words, if available (content read).  When None
                 or empty this degrades gracefully to voice-only.

    Always returns a normalized label; 'neutral' on any failure.
    """
    if not config.EMOTION_ENABLED:
        return "neutral"
    voice_mood = detect_voice_emotion(audio, sample_rate=sample_rate)
    text_mood = "neutral"
    if transcript and transcript.strip():
        text_mood = detect_text_emotion(transcript)
    return fuse_moods(text_mood, voice_mood)


# ── Tier 2 — face model (CPU, eager, HSEmotion ONNX) ─────────────────────────
# AffectNet 8-class EfficientNet served via onnxruntime (CPU only — onnxruntime
# is already present for the wake-word stack, and the ONNX models sidestep the
# timm-version fragility that breaks the torch `hsemotion` package). Reads facial
# affect from the webcam frame the Awareness daemon already captures (no extra
# camera open). Surfaced ambiently as "Face read:" AND fused into the per-turn
# [Mood:] tag. Opt-in / default OFF (privacy + the dep/weights load on demand).
#
# Raw → normalized (8-class AffectNet):
#   Happiness → happy   Sadness → sad   Anger/Disgust/Contempt → frustrated
#   Fear → anxious      Surprise/Neutral → neutral
_FACE_RAW_TO_NORMALIZED: dict[str, str] = {
    "happiness": "happy",
    "sadness":   "sad",
    "anger":     "frustrated",
    "disgust":   "frustrated",
    "contempt":  "frustrated",
    "fear":      "anxious",
    "surprise":  "neutral",
    "neutral":   "neutral",
}

_face_clf = None
_face_clf_lock = threading.Lock()
_face_idx_to_class: dict[int, str] | None = None
_face_ema = None                  # smoothed np.ndarray probability vector, or None
_face_mood = "neutral"            # latest smoothed label (accessor: get_face_mood)
_face_state_lock = threading.Lock()


def _ensure_face_model_cached(model_name: str) -> None:
    """Pre-fetch the HSEmotion ONNX weights to ~/.hsemotion if missing.

    Works around a bug in hsemotion-onnx's own downloader (it calls
    ``urllib.request.urlretrieve`` after only ``import urllib``, so its
    auto-download raises ``AttributeError``). We fetch with the correct import so
    the recognizer always finds the file cached and never hits that path.
    """
    import urllib.request
    cache_dir = os.path.join(os.path.expanduser("~"), ".hsemotion")
    os.makedirs(cache_dir, exist_ok=True)
    fpath = os.path.join(cache_dir, model_name + ".onnx")
    if os.path.isfile(fpath):
        return
    url = ("https://github.com/HSE-asavchenko/face-emotion-recognition/blob/main/"
           "models/affectnet_emotions/onnx/" + model_name + ".onnx?raw=true")
    print(f"[Aster Mood] Downloading face emotion model {model_name}...")
    urllib.request.urlretrieve(url, fpath)


def load_face_emotion_model():
    """Load the HSEmotion ONNX face classifier (CPU). Called at module import and
    again when the runtime toggle flips on. Graceful on a missing dep / failure —
    never crashes import; detection then just returns 'neutral'."""
    global _face_clf, _face_idx_to_class
    with _face_clf_lock:
        if _face_clf is not None:
            return
        if not (config.EMOTION_ENABLED and config.FACE_EMOTION_ENABLED):
            return
        try:
            _ensure_face_model_cached(config.FACE_EMOTION_MODEL)
            from hsemotion_onnx.facial_emotions import HSEmotionRecognizer
            print(f"[Aster Mood] Loading face emotion model ({config.FACE_EMOTION_MODEL}) to CPU...")
            _face_clf = HSEmotionRecognizer(model_name=config.FACE_EMOTION_MODEL)
            _face_idx_to_class = dict(_face_clf.idx_to_class)
            print("[Aster Mood] Face emotion model online (CPU).")
        except Exception as e:
            print(f"[Aster Mood] Face emotion model failed to load: {e}")
            _face_clf = None


def detect_face_emotion(frame) -> str:
    """Detect facial mood from a webcam frame; EMA-smoothed across reads.

    `frame` may be a BGR numpy array OR a base64 JPEG string (the form the
    Awareness daemon already holds — no decode needed by the caller). Returns a
    normalized label. On a missed face detection it *holds* the previous read
    (one blank frame shouldn't wipe the mood); 'neutral' on hard failure /
    disabled. Updates the module-level smoothed read consumed by get_face_mood().
    """
    global _face_ema, _face_mood
    if not (config.EMOTION_ENABLED and config.FACE_EMOTION_ENABLED):
        return "neutral"
    if _face_clf is None:
        load_face_emotion_model()
        if _face_clf is None:
            return "neutral"
    try:
        from tools.vision import get_primary_face_crop_rgb
        crop = get_primary_face_crop_rgb(frame)
        if crop is None:
            return get_face_mood()                  # no face this frame — hold last read

        _, scores = _face_clf.predict_emotions(crop, logits=False)
        vec = np.asarray(scores, dtype=float).ravel()
        if vec.size == 0:
            return get_face_mood()

        with _face_state_lock:
            if _face_ema is None or _face_ema.shape != vec.shape:
                _face_ema = vec
            else:
                a = float(config.FACE_EMOTION_EMA_ALPHA)
                _face_ema = a * vec + (1.0 - a) * _face_ema
            idx = int(_face_ema.argmax())
            conf = float(_face_ema[idx])
            if conf >= config.FACE_EMOTION_MIN_CONFIDENCE:
                raw = (_face_idx_to_class or {}).get(idx, "neutral").lower()
                _face_mood = _FACE_RAW_TO_NORMALIZED.get(raw, "neutral")
            # else: below confidence — hold the previous _face_mood
            return _face_mood

    except Exception as e:
        print(f"[Aster Mood] Face emotion detection error: {e}")
        return get_face_mood()


def get_face_mood() -> str:
    """Latest EMA-smoothed facial mood label — cheap, no inference. 'neutral'
    when face emotion is disabled. Used by the ambient block + per-turn fusion."""
    if not (config.EMOTION_ENABLED and config.FACE_EMOTION_ENABLED):
        return "neutral"
    with _face_state_lock:
        return _face_mood


def reset_face_mood() -> None:
    """Clear the smoothing state and reset to neutral — called when the user
    leaves the frame so a stale read doesn't linger after they're gone."""
    global _face_ema, _face_mood
    with _face_state_lock:
        _face_ema = None
        _face_mood = "neutral"


def fuse_face(base_mood: str, face_mood: str) -> str:
    """Fold an ambient facial read into a text/voice-derived mood.

    Extends the fuse_moods policy: the confident *base* content that accompanies
    THIS turn (the words, or voice prosody) wins; the ambient face read is a
    fallback used only when the base is neutral — the "sitting there looking down
    while typing 'ok'" case. Neutral + neutral → neutral. A no-op when face
    emotion is off (face_mood is then always 'neutral').
    """
    if base_mood and base_mood != "neutral":
        return base_mood
    if face_mood and face_mood != "neutral":
        return face_mood
    return "neutral"


# ── Tier 1b — ambient room voice (CPU, opt-in) ───────────────────────────────
# A CPU copy of the same wav2vec2 voice-emotion model, used only by the
# ambient-audio daemon (tools/ambient_audio.py) for passive room listening. It
# is held resident in CPU RAM (~400 MB, no ref-count churn) and is entirely
# separate from the CUDA `_voice_clf` the LiveKit/Telegram paths ref-count — so
# those paths are untouched. State + smoothing mirror the Tier-2 face pattern.
_cpu_voice_clf = None
_cpu_voice_clf_lock = threading.Lock()
_cpu_voice_ind2lab: dict[int, str] | None = None  # vec index → raw label, for EMA argmax

_ambient_voice_ema = None              # smoothed np.ndarray probability vector, or None
_ambient_voice_mood = "neutral"        # latest smoothed label (accessor: get_ambient_voice_mood)
_ambient_voice_lock = threading.Lock()


def _get_cpu_voice_clf():
    """Lazy-load the voice-emotion classifier to CPU, held resident. Returns the
    classifier or None on failure (then ambient detection just returns 'neutral').
    Reuses the same SpeechBrain import-prep + CVE-2025-32434 load patch as the
    CUDA path, but pins device to CPU."""
    global _cpu_voice_clf, _cpu_voice_ind2lab
    with _cpu_voice_clf_lock:
        if _cpu_voice_clf is not None:
            return _cpu_voice_clf
        if not config.EMOTION_ENABLED:
            return None
        try:
            _prepare_speechbrain_imports()
            from speechbrain.inference.interfaces import foreign_class
            print(f"[Aster Mood] Loading ambient voice emotion model ({config.EMOTION_VOICE_MODEL}) to CPU...")
            import transformers.modeling_utils as _tmu
            _orig_check = _tmu.__dict__.get("check_torch_load_is_safe")
            if _orig_check is not None:
                _tmu.check_torch_load_is_safe = lambda: None
            try:
                _cpu_voice_clf = foreign_class(
                    source=config.EMOTION_VOICE_MODEL,
                    pymodule_file="custom_interface.py",
                    classname="CustomEncoderWav2vec2Classifier",
                    savedir=f"pretrained_models/{config.EMOTION_VOICE_MODEL.split('/')[-1]}",
                    run_opts={"device": "cpu"},
                )
            finally:
                if _orig_check is not None:
                    try:
                        _tmu.check_torch_load_is_safe = _orig_check
                    except Exception:
                        pass
            # Build the vec-index → raw-label map for EMA argmax decoding. Best
            # effort: if unavailable we fall back to label-only reads below.
            try:
                _le = _cpu_voice_clf.hparams.label_encoder
                _cpu_voice_ind2lab = {int(i): str(_le.ind2lab[i]) for i in range(len(_le.ind2lab))}
            except Exception:
                _cpu_voice_ind2lab = None
            print("[Aster Mood] Ambient voice emotion model online (CPU).")
        except Exception as e:
            print(f"[Aster Mood] Ambient voice emotion model failed to load: {e}")
            _cpu_voice_clf = None
        return _cpu_voice_clf


def detect_ambient_voice_emotion(audio, sample_rate: int = 16000) -> str:
    """Read vocal tone from a passive room utterance on CPU; EMA-smoothed across
    reads (mirrors detect_face_emotion). Returns a normalized label. Holds the
    previous read on a short clip / low confidence; 'neutral' on hard failure or
    when ambient/emotion is disabled. Updates the read consumed by
    get_ambient_voice_mood(). Owner-gating + window-feed live in the daemon."""
    global _ambient_voice_ema, _ambient_voice_mood
    if not (config.EMOTION_ENABLED and config.AMBIENT_AUDIO_ENABLED):
        return "neutral"
    clf = _get_cpu_voice_clf()
    if clf is None:
        return "neutral"
    try:
        import torchaudio
        if isinstance(audio, (bytes, bytearray)):
            waveform, sr = torchaudio.load(io.BytesIO(bytes(audio)))
            if sr != 16000:
                waveform = torchaudio.transforms.Resample(sr, 16000)(waveform)
            if waveform.shape[0] > 1:
                waveform = waveform.mean(dim=0, keepdim=True)
        elif isinstance(audio, np.ndarray):
            waveform = torch.from_numpy(audio).unsqueeze(0)
        else:
            return get_ambient_voice_mood()

        if waveform.shape[-1] / 16000 < MIN_UTTERANCE_SECONDS:
            return get_ambient_voice_mood()                 # too short — hold last read

        norm, conf, vec = _classify_voice_waveform(clf, waveform, "cpu")

        with _ambient_voice_lock:
            # Preferred path: EMA-smooth the probability vector (damps a blip).
            if vec is not None and vec.size and _cpu_voice_ind2lab is not None:
                if _ambient_voice_ema is None or _ambient_voice_ema.shape != vec.shape:
                    _ambient_voice_ema = vec
                else:
                    a = float(config.AMBIENT_VOICE_EMA_ALPHA)
                    _ambient_voice_ema = a * vec + (1.0 - a) * _ambient_voice_ema
                idx = int(_ambient_voice_ema.argmax())
                sconf = float(_ambient_voice_ema[idx])
                if sconf >= config.EMOTION_MIN_CONFIDENCE:
                    raw = _cpu_voice_ind2lab.get(idx, "neutral").lower().strip()
                    _ambient_voice_mood = _VOICE_RAW_TO_NORMALIZED.get(raw, "neutral")
                # else: below confidence — hold previous _ambient_voice_mood
            else:
                # Fallback: no vector/label-map — gate on the single read's conf.
                if conf >= config.EMOTION_MIN_CONFIDENCE:
                    _ambient_voice_mood = norm
            return _ambient_voice_mood

    except Exception as e:
        print(f"[Aster Mood] Ambient voice emotion detection error: {e}")
        return get_ambient_voice_mood()


def get_ambient_voice_mood() -> str:
    """Latest EMA-smoothed ambient vocal-tone label — cheap, no inference.
    'neutral' when ambient audio is disabled. Used by the awareness ambient block
    and the per-turn [Mood:] fusion."""
    if not (config.EMOTION_ENABLED and config.AMBIENT_AUDIO_ENABLED):
        return "neutral"
    with _ambient_voice_lock:
        return _ambient_voice_mood


def reset_ambient_voice_mood() -> None:
    """Clear the ambient smoothing state and reset to neutral."""
    global _ambient_voice_ema, _ambient_voice_mood
    with _ambient_voice_lock:
        _ambient_voice_ema = None
        _ambient_voice_mood = "neutral"


# ── Mood-trend memory (roadmap Idea 3) ───────────────────────────────────────
# Two pieces with one entry point:
#   1. An in-memory rolling window of recent moods → get_sustained_mood(), the
#      debounce gate Ideas 1 & 2 will consume (act on a *sustained* mood, never a
#      single blip).
#   2. A durable per-turn JSONL log (Aster_Vault/emotion_log.jsonl) read by
#      tools/mood_memory.py to fold periodic summaries into long-term memory.
# log_mood_turn() feeds both. Everything here is best-effort and never raises.
_mood_window = deque(maxlen=64)          # only the tail is ever inspected
_mood_window_lock = threading.Lock()
_mood_log_lock = threading.Lock()

# ── Streak coordination (shared by Idea 1 + Idea 2) ──────────────────────────
# A "streak" is one continuous run of the same sustained non-neutral mood.
# _streak_id bumps every time the sustained mood *changes* (including to None),
# so each consumer can act at most once per streak by remembering the last id it
# handled. _last_touch is a global timestamp of the most recent mood-driven
# nudge from *either* system, so they can't fire back-to-back.
_streak_lock = threading.Lock()
_streak_id = 0
_streak_mood: str | None = None
_last_touch: float | None = None         # None = no mood nudge has fired yet


def record_mood(mood: str) -> None:
    """Push one mood onto the in-memory debounce window."""
    with _mood_window_lock:
        _mood_window.append(mood)


def get_sustained_mood(min_turns: int | None = None) -> str | None:
    """Return the mood if the last `min_turns` recorded moods are the *same
    non-neutral* label, else None.

    This is the shared foundation for the proactive (Idea 1) and ambient-action
    (Idea 2) features: it converts the noisy per-turn signal into a debounced
    "Mohamed has genuinely been X for a while now" read. Neutral never counts as
    sustained (we don't act on the absence of a mood).
    """
    if min_turns is None:
        min_turns = config.MOOD_SUSTAIN_TURNS
    if min_turns < 1:
        return None
    with _mood_window_lock:
        if len(_mood_window) < min_turns:
            return None
        tail = list(_mood_window)[-min_turns:]
    first = tail[0]
    if first == "neutral":
        return None
    return first if all(m == first for m in tail) else None


def get_mood_streak(min_turns: int | None = None) -> tuple[int, str | None]:
    """Return ``(streak_id, sustained_mood)`` — the debounce gate plus a stable
    id for the *current* sustained-mood run.

    The id increments whenever the sustained mood transitions to a different
    value (a new mood, or back to None/neutral). Consumers (Idea 1 / Idea 2)
    store the last id they acted on and skip re-firing while it's unchanged, so a
    mood sustained over many turns produces exactly one action per system.
    """
    global _streak_id, _streak_mood
    mood = get_sustained_mood(min_turns)
    with _streak_lock:
        if mood != _streak_mood:
            _streak_mood = mood
            _streak_id += 1
        return _streak_id, mood


def note_mood_touch() -> None:
    """Record that a mood-driven nudge just fired (from either system)."""
    global _last_touch
    with _streak_lock:
        _last_touch = time.monotonic()


def mood_touch_recent(within_seconds: float) -> bool:
    """True if any mood-driven nudge fired within the last `within_seconds`."""
    with _streak_lock:
        if _last_touch is None:
            return False
        return (time.monotonic() - _last_touch) < within_seconds


def log_mood_turn(mood: str, context: str = "") -> None:
    """Record one real user turn's mood: update the in-memory window AND append
    a line to the durable JSONL log. Cheap, thread-safe, never raises.

    `context` is a short snippet of the user's message (already tag-stripped by
    the caller) so a human skimming the log / summary can see what the mood was
    about. It is truncated and newline-flattened here.
    """
    if not (config.EMOTION_ENABLED and config.MOOD_TREND_ENABLED):
        return
    mood = (mood or "neutral").strip().lower()
    if mood not in VALID_MOODS:
        mood = "neutral"

    # In-memory debounce window updates even if the disk write fails.
    record_mood(mood)

    try:
        snippet = " ".join(str(context).split())[:160]
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "mood": mood,
            "context": snippet,
        }
        with _mood_log_lock:
            with open(config.MOOD_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[Aster Mood] mood-log append failed: {e}")


def read_mood_log(since_hours: float | None = None) -> list[dict]:
    """Read the JSONL mood log into a list of dicts (oldest first).

    If `since_hours` is given, only entries newer than that are returned.
    Malformed / unparseable lines are skipped. Never raises — returns [] on any
    failure or a missing log.
    """
    path = config.MOOD_LOG_PATH
    if not os.path.exists(path):
        return []
    cutoff = datetime.now() - timedelta(hours=since_hours) if since_hours else None
    try:
        with _mood_log_lock:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
    except Exception:
        return []

    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec["ts"])
        except Exception:
            continue
        if cutoff is not None and ts < cutoff:
            continue
        out.append(rec)
    return out


def prune_mood_log(retention_days: int | None = None) -> int:
    """Drop log entries older than `retention_days` (rewrites the file once).
    Returns the number of entries kept. Best-effort; never raises.

    Called from the flush cycle (every few turns), not the per-turn hot path.
    """
    if retention_days is None:
        retention_days = config.MOOD_LOG_RETENTION_DAYS
    path = config.MOOD_LOG_PATH
    if retention_days <= 0 or not os.path.exists(path):
        return 0
    kept = read_mood_log(since_hours=retention_days * 24)
    try:
        with _mood_log_lock:
            with open(path, "w", encoding="utf-8") as f:
                for rec in kept:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[Aster Mood] mood-log prune failed: {e}")
    return len(kept)


# ── Eager boot: load text model now (Tier 0) ─────────────────────────────────
# Tier 1 (voice) is intentionally lazy — it loads only on first voice I/O.
# This deviation from the project's "CUDA + eager" default is intentional:
# the text model is tiny and CPU-only; the voice model costs ~300 MB VRAM that
# should only be held during active voice I/O.
load_text_emotion_model()
# Tier 2 (face) is CPU + eager like Tier 0, but no-ops here when face emotion is
# disabled (the default) — it loads on the first toggle-on / detect call instead.
load_face_emotion_model()
