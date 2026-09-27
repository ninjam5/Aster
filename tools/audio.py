import gc
import os
import threading
import time
import numpy as np
import config

# ── Kokoro voice catalogue ────────────────────────────────────────────────────
# Built-in voices available in Kokoro 82M (lang_code="a").
# IDs match what KPipeline accepts as the `voice=` argument.
# "af_" = American female, "am_" = American male,
# "bf_" = British female, "bm_" = British male.
KOKORO_VOICES: list[dict] = [
    {"id": "af_heart",    "label": "Heart",    "gender": "female", "accent": "American"},
    {"id": "af_bella",    "label": "Bella",    "gender": "female", "accent": "American"},
    {"id": "af_sarah",    "label": "Sarah",    "gender": "female", "accent": "American"},
    {"id": "af_nicole",   "label": "Nicole",   "gender": "female", "accent": "American"},
    {"id": "af_sky",      "label": "Sky",      "gender": "female", "accent": "American"},
    {"id": "af_kore",     "label": "Kore",     "gender": "female", "accent": "American"},
    {"id": "af_nova",     "label": "Nova",     "gender": "female", "accent": "American"},
    {"id": "am_adam",     "label": "Adam",     "gender": "male",   "accent": "American"},
    {"id": "am_michael",  "label": "Michael",  "gender": "male",   "accent": "American"},
    {"id": "am_echo",     "label": "Echo",     "gender": "male",   "accent": "American"},
    {"id": "am_puck",     "label": "Puck",     "gender": "male",   "accent": "American"},
    {"id": "bf_emma",     "label": "Emma",     "gender": "female", "accent": "British"},
    {"id": "bf_isabella", "label": "Isabella", "gender": "female", "accent": "British"},
    {"id": "bm_george",   "label": "George",   "gender": "male",   "accent": "British"},
    {"id": "bm_lewis",    "label": "Lewis",    "gender": "male",   "accent": "British"},
]

# ── shared Kokoro instance ────────────────────────────────────────────────────
# A single Kokoro pipeline serves BOTH the Telegram voice-note tool and the
# LiveKit voice call. It is lazy-loaded on first use and offloaded from VRAM
# once no consumer holds it (ref-counted via acquire/release).
kokoro_pipeline = None
sf = None
_kokoro_users = 0
_kokoro_lock = threading.RLock()

print("[Aster Core] Kokoro TTS 82M Voice Engine registered (lazy — loads on first use).")


def acquire_kokoro_pipeline():
    """Load Kokoro if needed, register a user, and return the shared pipeline (or None).

    Tries CUDA first; if KPipeline init or a probe synthesis raises (e.g. VRAM OOM
    alongside llama-server), falls back to CPU so voice is never silently lost.
    """
    global kokoro_pipeline, sf, _kokoro_users
    with _kokoro_lock:
        if kokoro_pipeline is None:
            try:
                import soundfile as _sf
                from kokoro import KPipeline
                sf = _sf
            except ImportError as e:
                _report_kokoro_error("Kokoro/soundfile package missing", e)
                return None

            for device in ("cuda", "cpu"):
                try:
                    print(f"[Aster Core] Booting Kokoro TTS 82M Voice Engine (device={device})...")
                    kokoro_pipeline = KPipeline(lang_code="a")
                    print("[Aster Core] Kokoro TTS Engine Online.")
                    break
                except Exception as e:
                    print(f"[Aster Core] Kokoro boot on {device} failed: {e}")
                    if device == "cpu":
                        _report_kokoro_error("Kokoro TTS Boot Failed on all devices", e)
                    kokoro_pipeline = None

        if kokoro_pipeline is not None:
            _kokoro_users += 1
        return kokoro_pipeline


def _report_kokoro_error(label: str, exc: Exception) -> None:
    """Send a loud, traceback-bearing error through the diagnostic relay."""
    import traceback
    msg = f"[Aster Core] {label}: {exc}\n{traceback.format_exc()}"
    print(msg)
    try:
        from tools.diagnostics import send_error
        send_error(label, exc)
    except Exception:
        pass


def release_kokoro_pipeline():
    """Drop a user; once none remain, offload Kokoro from VRAM."""
    global kokoro_pipeline, _kokoro_users
    with _kokoro_lock:
        if _kokoro_users > 0:
            _kokoro_users -= 1
        if _kokoro_users <= 0 and kokoro_pipeline is not None:
            kokoro_pipeline = None
            _kokoro_users = 0
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            print("[Aster Core] Kokoro TTS offloaded from VRAM (no active users).")


def generate_kokoro_voice(text):
    """Generates TTS audio using Kokoro 82M and returns the payload tag + text."""
    pipeline = acquire_kokoro_pipeline()
    if pipeline is None:
        return "[System Error: Kokoro TTS engine offline.]"

    print(f"\n[Aster Audio: Kokoro synthesis initialized]")
    generation_start = time.time()
    try:
        temp_dir = os.path.join("Aster_Vault", "Voice", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        temp_audio_path = os.path.join(temp_dir, f"temp_voice_{int(time.time())}.wav")

        # Resolve voice: use config.VOICE_NAME (settable at runtime via /api/voice/select).
        # resolve_voice_arg maps custom voice ids to their .pt file paths.
        voice_path = resolve_voice_arg(config.VOICE_NAME)

        # Synthesize with Kokoro
        generator = pipeline(text, voice=voice_path, speed=config.VOICE_SPEED)
        chunks = [audio for _, _, audio in generator if audio is not None]

        if not chunks:
            return "[System Error: Kokoro returned no audio chunks.]"

        audio_data = np.concatenate(chunks).astype(np.float32)
        sf.write(temp_audio_path, audio_data, 24000)

        elapsed = round(time.time() - generation_start, 2)
        print(f"[Aster Audio: Kokoro audio generated in {elapsed}s]")
        print(f"[Aster Audio: Saved to {temp_audio_path}]")

        # Return the payload tag AND the text so Aster remembers what he said
        return f"[NATIVE_AUDIO_PAYLOAD:{temp_audio_path}] {text}"

    except Exception as e:
        print(f"[Aster Internal: Kokoro TTS Generation Failed - {e}]")
        return f"[System Error: Kokoro voice synthesis failed - {str(e)}]"
    finally:
        # one-shot use — drop our hold so Kokoro can offload if idle
        release_kokoro_pipeline()


def list_custom_voices() -> list[dict]:
    """Return a list of custom Kokoro voice clones found on disk.

    Scans CUSTOM_VOICES_DIR (Aster_Vault/TTS-Voices/) for *.pt files, plus
    legacy aster.pt / aster2.pt in the repo root for back-compat.  Each entry
    uses the file stem as the voice id and is flagged custom=True so the
    frontend can render it differently from built-in voices.
    """
    found: dict[str, dict] = {}  # keyed by stem to deduplicate

    # Primary location: Aster_Vault/TTS-Voices/
    try:
        vault_dir = config.CUSTOM_VOICES_DIR
        if os.path.isdir(vault_dir):
            for fname in os.listdir(vault_dir):
                if fname.lower().endswith(".pt"):
                    stem = fname[:-3]
                    path = os.path.abspath(os.path.join(vault_dir, fname))
                    found[stem] = {
                        "id": stem,
                        "label": stem.replace("_", " ").title(),
                        "custom": True,
                        "path": path,
                    }
    except Exception:
        pass

    # Legacy: repo-root aster.pt / aster2.pt (back-compat, lower priority)
    for legacy in ("aster2.pt", "aster.pt"):
        if os.path.isfile(legacy) and legacy[:-3] not in found:
            stem = legacy[:-3]
            found[stem] = {
                "id": stem,
                "label": stem.replace("_", " ").title(),
                "custom": True,
                "path": os.path.abspath(legacy),
            }

    return list(found.values())


def resolve_voice_arg(voice: str) -> str:
    """Return the argument Kokoro's pipeline(voice=...) expects for *voice*.

    - If *voice* already ends in ".pt", treat it as a file path and return it.
    - If *voice* matches a custom voice id on disk, return its absolute path.
    - Otherwise assume it's a built-in id (e.g. "af_bella") and return as-is.

    This is the single source of truth for voice resolution used by
    generate_kokoro_voice, synthesize_preview, and local_tts._resolve_voice.
    """
    if voice.endswith(".pt"):
        return voice  # already a path

    for cv in list_custom_voices():
        if cv["id"] == voice:
            return cv["path"]

    return voice  # built-in id


def synthesize_preview(voice: str, text: str = "Hey, this is Aster. Just giving you a quick listen so you can choose your favourite voice.") -> str:
    """Synthesise a short audio sample for the given voice and return the WAV file path.

    Used by the Settings / onboarding voice-preview endpoint.  Reuses the
    shared Kokoro pipeline (acquire/release) exactly like generate_kokoro_voice.
    Returns the absolute path to the written WAV, or raises on failure.
    """
    pipeline = acquire_kokoro_pipeline()
    if pipeline is None:
        raise RuntimeError("Kokoro TTS engine offline — cannot synthesize preview.")

    try:
        temp_dir = os.path.join("Aster_Vault", "Voice", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        out_path = os.path.join(temp_dir, f"preview_{voice}_{int(time.time())}.wav")

        voice_arg = resolve_voice_arg(voice)
        generator = pipeline(text, voice=voice_arg, speed=config.VOICE_SPEED)
        chunks = [audio for _, _, audio in generator if audio is not None]

        if not chunks:
            raise RuntimeError("Kokoro returned no audio chunks for preview.")

        audio_data = np.concatenate(chunks).astype(np.float32)

        # Import soundfile lazily (already loaded by acquire_kokoro_pipeline
        # which sets the module-level `sf` reference).
        global sf
        if sf is None:
            import soundfile as _sf
            sf = _sf
        sf.write(out_path, audio_data, 24000)
        return out_path

    except Exception:
        raise
    finally:
        release_kokoro_pipeline()
