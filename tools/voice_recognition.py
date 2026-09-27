import io
import os
import time
import numpy as np
import torch

import config

KNOWN_VOICES_DIR     = os.path.join(config.VAULT_DIR, "Voices")
TEMP_VOICE_PATH      = os.path.join(config.VAULT_DIR, "temp_unknown_voice.wav")

# ── Thresholds ────────────────────────────────────────────────────────────────
# SIMILARITY_THRESHOLD : minimum cosine score to accept a speaker ID (gate).
# LEARN_THRESHOLD      : stricter gate — only store a sample when we're confident
#                        enough that a false-positive won't poison the centroid.
# MAX_SAMPLES_PER_SPEAKER : cap per speaker (manual seed never counts against cap).
# MIN_LEARN_SECONDS    : ignore short utterances (filler/cough) for accumulation.
SIMILARITY_THRESHOLD     = 0.35
LEARN_THRESHOLD          = 0.50
MAX_SAMPLES_PER_SPEAKER  = 15
MIN_LEARN_SECONDS        = 2.5

known_voice_embeddings: list = []   # torch.Tensor centroids, on CUDA
known_voice_names: list[str] = []
_classifier = None


# ── Classifier ────────────────────────────────────────────────────────────────

def _load_classifier():
    global _classifier
    if _classifier is None:
        from speechbrain.inference.speaker import EncoderClassifier

        import sys, types
        try:
            import speechbrain.integrations as _sb_int
            _orig_ga = getattr(_sb_int, "__getattr__", None)
            if _orig_ga is not None:
                def _safe_ga(name: str):
                    try:
                        return _orig_ga(name)
                    except Exception:
                        stub = types.ModuleType(f"speechbrain.integrations.{name}")
                        sys.modules[f"speechbrain.integrations.{name}"] = stub
                        setattr(_sb_int, name, stub)
                        return stub
                _sb_int.__getattr__ = _safe_ga
            for _key in list(sys.modules.keys()):
                if _key.startswith("speechbrain.integrations."):
                    _mod = sys.modules[_key]
                    if type(_mod).__name__ == "LazyModule":
                        sys.modules[_key] = types.ModuleType(_key)
        except Exception as e:
            print(f"[Aster Internal: SpeechBrain lazy-module patch warning — {e}]")

        print("[Aster Internal: Loading ECAPA-TDNN speaker model to CUDA...]")
        _classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir="pretrained_models/spkrec-ecapa-voxceleb",
            run_opts={"device": "cuda:0"},
        )
    return _classifier


# ── Embedding helper ──────────────────────────────────────────────────────────

def _embed_waveform(waveform: torch.Tensor) -> "torch.Tensor | None":
    """Encode a (1, samples) 16kHz waveform → unit-norm 192-d embedding on CUDA.
    Returns None for silent audio (zero-norm guard prevents NaN embeddings)."""
    classifier = _load_classifier()
    waveform = waveform.to("cuda:0")
    with torch.no_grad():
        emb = classifier.encode_batch(waveform).squeeze()
        norm = emb.norm()
        if norm == 0:
            return None
        return emb / norm


def _load_waveform_from_path(filepath: str) -> "torch.Tensor | None":
    """Load a WAV file → (1, samples) float32 tensor at 16kHz. Returns None on error."""
    import torchaudio
    try:
        waveform, sr = torchaudio.load(filepath)
        if sr != 16000:
            waveform = torchaudio.transforms.Resample(sr, 16000)(waveform)
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        return waveform
    except Exception as e:
        print(f"[Aster Internal: Failed to load {filepath} — {e}]")
        return None


# ── Migration: flat Voices/*.wav → per-speaker subfolder ─────────────────────

def _migrate_legacy_flat_files() -> None:
    """Move any legacy Voices/{Name}.wav flat files into Voices/{Name}/{Name}.wav."""
    for entry in os.listdir(KNOWN_VOICES_DIR):
        if not entry.lower().endswith(".wav"):
            continue
        flat_path = os.path.join(KNOWN_VOICES_DIR, entry)
        if not os.path.isfile(flat_path):
            continue
        name = os.path.splitext(entry)[0]
        speaker_dir = os.path.join(KNOWN_VOICES_DIR, name)
        os.makedirs(speaker_dir, exist_ok=True)
        dest = os.path.join(speaker_dir, entry)
        try:
            os.rename(flat_path, dest)
            print(f"  [Voice Migration] {entry} → {name}/{entry}")
        except Exception as e:
            print(f"[Aster Internal: Migration failed for {entry} — {e}]")


# ── Speaker file helpers ──────────────────────────────────────────────────────

def _speaker_dir(name: str) -> str:
    return os.path.join(KNOWN_VOICES_DIR, name)


def _speaker_wavs(name: str) -> list[str]:
    """Return sorted list of all .wav paths for a speaker (oldest first)."""
    d = _speaker_dir(name)
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".wav")
    )


def _is_auto_sample(filepath: str) -> bool:
    """Auto-captured files contain '__' in their stem; manual seeds do not."""
    return "__" in os.path.splitext(os.path.basename(filepath))[0]


def _build_centroid(name: str) -> "torch.Tensor | None":
    """Embed all WAVs for a speaker and return their L2-normalised mean, or None."""
    embs = []
    for path in _speaker_wavs(name):
        wf = _load_waveform_from_path(path)
        if wf is None:
            continue
        emb = _embed_waveform(wf)
        if emb is not None:
            embs.append(emb)
    if not embs:
        return None
    centroid = torch.stack(embs).mean(dim=0)
    norm = centroid.norm()
    if norm == 0:
        return None
    return centroid / norm


# ── Public load ───────────────────────────────────────────────────────────────

def load_known_voices() -> None:
    """Scan Voices/ subdirectories, build one centroid per speaker, populate globals.
    Called once at module import and again on hot-reload."""
    global known_voice_embeddings, known_voice_names
    known_voice_embeddings.clear()
    known_voice_names.clear()

    print("[Aster Internal: Booting Voice Recognition Engine...]")

    try:
        import torchaudio  # noqa: F401
        try:
            import kokoro  # noqa: F401
        except Exception:
            pass
        from speechbrain.inference.speaker import EncoderClassifier  # noqa: F401
    except ImportError as e:
        print(f"[Aster Internal: Voice recognition unavailable — missing package: {e}]")
        print("[Aster Internal: Run: python -m pip install speechbrain]")
        return

    if not os.path.exists(KNOWN_VOICES_DIR):
        os.makedirs(KNOWN_VOICES_DIR)
        print("[Aster Internal: Voices vault created. Add speaker subfolders to enable recognition.]")
        return

    # Move any legacy flat files into per-speaker subfolders.
    _migrate_legacy_flat_files()

    _load_classifier()  # warm up once before the per-speaker loop

    for entry in sorted(os.listdir(KNOWN_VOICES_DIR)):
        speaker_dir = os.path.join(KNOWN_VOICES_DIR, entry)
        if not os.path.isdir(speaker_dir):
            continue
        name = entry
        centroid = _build_centroid(name)
        if centroid is None:
            print(f"  [Voice Warning] No usable WAVs for speaker '{name}' — skipping.")
            continue
        n_samples = len(_speaker_wavs(name))
        known_voice_embeddings.append(centroid)
        known_voice_names.append(name)
        print(f"  -> Learned voice: {name} ({n_samples} sample{'s' if n_samples != 1 else ''})")


# ── Core identification ────────────────────────────────────────────────────────

def _audio_to_waveform(audio, sample_rate: int = 16000) -> "tuple[torch.Tensor, float] | tuple[None, float]":
    """Convert raw bytes or np.float32 array to (waveform_tensor, duration_s)."""
    import torchaudio
    if isinstance(audio, (bytes, bytearray)):
        waveform, sr = torchaudio.load(io.BytesIO(bytes(audio)))
        if sr != 16000:
            waveform = torchaudio.transforms.Resample(sr, 16000)(waveform)
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
    elif isinstance(audio, np.ndarray):
        waveform = torch.from_numpy(audio).unsqueeze(0)
        if sample_rate != 16000:
            waveform = torchaudio.transforms.Resample(sample_rate, 16000)(waveform)
    else:
        return None, 0.0
    duration = waveform.shape[-1] / 16000
    return waveform, duration


def identify_speaker(audio, sample_rate: int = 16000) -> "str | None":
    """Identify speaker; returns enrolled name or None. Back-compat entry point."""
    if not known_voice_embeddings:
        return None
    try:
        waveform, duration = _audio_to_waveform(audio, sample_rate)
        if waveform is None or duration < 1.0:
            return None
        emb = _embed_waveform(waveform)
        if emb is None:
            return None
        best_score = -1.0
        best_name = None
        for centroid, name in zip(known_voice_embeddings, known_voice_names):
            score = torch.dot(emb, centroid).item()
            if score > best_score:
                best_score = score
                best_name = name
        return best_name if best_score >= SIMILARITY_THRESHOLD else None
    except Exception as e:
        print(f"[Aster Internal: Voice recognition error — {e}]")
        return None


# ── Auto-accumulation ─────────────────────────────────────────────────────────

def accumulate_sample(audio, name: str) -> None:
    """Save a new auto-captured sample for `name`, enforce the cap, update centroid.
    Never raises — caller is never interrupted."""
    try:
        import torchaudio
        speaker_dir_path = _speaker_dir(name)
        os.makedirs(speaker_dir_path, exist_ok=True)

        ts = int(time.time())
        filename = f"{name}__{ts}.wav"
        dest = os.path.join(speaker_dir_path, filename)

        waveform, _ = _audio_to_waveform(audio)
        if waveform is None:
            return
        torchaudio.save(dest, waveform.cpu(), 16000)

        # Enforce cap — only rotate auto-captured files (those with '__' in stem).
        all_wavs = _speaker_wavs(name)
        if len(all_wavs) > MAX_SAMPLES_PER_SPEAKER:
            auto_wavs = [p for p in all_wavs if _is_auto_sample(p)]
            # all_wavs is sorted oldest-first, so auto_wavs[0] is the oldest auto sample.
            if auto_wavs:
                try:
                    os.remove(auto_wavs[0])
                except Exception:
                    pass

        # Rebuild centroid for this speaker only.
        new_centroid = _build_centroid(name)
        if new_centroid is not None:
            try:
                idx = known_voice_names.index(name)
                known_voice_embeddings[idx] = new_centroid
            except ValueError:
                # Speaker wasn't in the list yet (shouldn't happen, but handle gracefully).
                known_voice_embeddings.append(new_centroid)
                known_voice_names.append(name)

        n = len(_speaker_wavs(name))
        print(f"[Voice Learn] Saved sample for '{name}' ({n}/{MAX_SAMPLES_PER_SPEAKER})")
    except Exception as e:
        print(f"[Aster Internal: accumulate_sample failed — {e}]")


def identify_and_maybe_learn(audio, sample_rate: int = 16000) -> "str | None":
    """Identify speaker; if confidence >= LEARN_THRESHOLD and duration >= MIN_LEARN_SECONDS,
    auto-accumulate the sample. Returns the speaker name or None."""
    if not known_voice_embeddings:
        return None
    try:
        waveform, duration = _audio_to_waveform(audio, sample_rate)
        if waveform is None or duration < 1.0:
            return None
        emb = _embed_waveform(waveform)
        if emb is None:
            return None
        best_score = -1.0
        best_name = None
        for centroid, name in zip(known_voice_embeddings, known_voice_names):
            score = torch.dot(emb, centroid).item()
            if score > best_score:
                best_score = score
                best_name = name
        if best_score < SIMILARITY_THRESHOLD:
            return None
        # Auto-accumulate only when very confident and utterance is long enough.
        if best_score >= LEARN_THRESHOLD and duration >= MIN_LEARN_SECONDS:
            accumulate_sample(audio, best_name)
        return best_name
    except Exception as e:
        print(f"[Aster Internal: identify_and_maybe_learn error — {e}]")
        return None


# Eager load at module import — mirrors load_known_faces() in tools/vision.py.
# Fail closed: a transient import-time failure (e.g. MemoryError while
# llama-server is still allocating its KV cache during a simultaneous boot)
# must NOT take down the whole app. Empty globals ⇒ identification returns None.
try:
    load_known_voices()
except Exception as _e:
    print(f"[Aster Internal: Voice recognition eager load failed "
          f"({type(_e).__name__}: {_e}); continuing without speaker ID. "
          f"Call load_known_voices() to retry.]")
