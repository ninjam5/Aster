import os
import re
import asyncio
import numpy as np
import site
import threading
import concurrent.futures
from livekit.agents import stt
from livekit import rtc
from livekit.agents.types import APIConnectOptions, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given
from faster_whisper import WhisperModel

WHISPER_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "whisper-models", "medium.en"
)

# ── shared Whisper instance ───────────────────────────────────────────────────
# A single medium.en model serves BOTH the Telegram voice handler and the
# LiveKit call STT. Loading it twice wastes ~1 GB of VRAM during a call.
_shared_whisper: WhisperModel | None = None
_whisper_lock = threading.Lock()
_dll_handles: list = []

# ── unknown-voice LiveKit notification ───────────────────────────────────────
# Throttle: send at most one Telegram alert per 5-minute window so a long call
# with a persistent unknown speaker doesn't spam the phone.
_last_unknown_notify: float = 0.0
_UNKNOWN_NOTIFY_COOLDOWN: float = 300.0  # seconds


def _notify_unknown_livekit_voice(audio: np.ndarray) -> None:
    """Save the utterance as the pending temp voice and alert via Telegram.
    Called from inside _transcribe() (thread-pool worker) — kept non-blocking."""
    global _last_unknown_notify
    import time
    now = time.time()
    if now - _last_unknown_notify < _UNKNOWN_NOTIFY_COOLDOWN:
        return
    _last_unknown_notify = now
    try:
        import torch
        import torchaudio
        from tools.voice_recognition import TEMP_VOICE_PATH
        waveform = torch.from_numpy(audio).unsqueeze(0)  # (1, samples)
        torchaudio.save(TEMP_VOICE_PATH, waveform, 16000)
    except Exception as e:
        print(f"[Aster Internal: Could not save unknown-voice temp file — {e}]")
        return
    try:
        import config
        if config.TELEGRAM_AVAILABLE and config.bot:
            config.bot.send_message(
                config.AUTHORIZED_CHAT_ID,
                "[Aster: Unknown voice detected on the call — /enroll <name> to save this profile.]",
            )
    except Exception as e:
        print(f"[Aster Internal: Telegram notify failed — {e}]")


def _prepare_cuda_runtime_paths() -> None:
    """Ensure CUDA/cuBLAS/cuDNN DLL directories are visible to the process on Windows."""
    nvidia_roots = []
    for base in site.getsitepackages():
        candidate = os.path.join(base, "nvidia")
        if os.path.isdir(candidate):
            nvidia_roots.append(candidate)

    dll_dirs = []
    for root in nvidia_roots:
        for subdir in ("cuda_runtime", "cuda_nvrtc", "cublas", "cudnn"):
            bin_dir = os.path.join(root, subdir, "bin")
            if os.path.isdir(bin_dir):
                dll_dirs.append(bin_dir)

    if dll_dirs:
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ";".join(dll_dirs + [current_path])
        if hasattr(os, "add_dll_directory"):
            for d in dll_dirs:
                try:
                    _dll_handles.append(os.add_dll_directory(d))
                except OSError:
                    pass


def get_whisper_model() -> WhisperModel:
    """Lazily load (once) and return the process-wide shared Whisper model."""
    global _shared_whisper
    if _shared_whisper is not None:
        return _shared_whisper
    with _whisper_lock:
        if _shared_whisper is None:
            print("[Aster Ears] Booting shared Faster-Whisper (medium.en, 8-bit CUDA)...")
            _prepare_cuda_runtime_paths()
            _shared_whisper = WhisperModel(
                WHISPER_MODEL_PATH,
                device="cuda",
                compute_type="int8",
            )
    return _shared_whisper


def release_whisper_model() -> None:
    """Unload the shared Whisper model from VRAM (called when a LiveKit call ends)."""
    global _shared_whisper
    with _whisper_lock:
        _shared_whisper = None
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    print("[Aster Ears] Faster-Whisper unloaded from VRAM.")


# ── one-shot transcription for the Telegram voice-note path ───────────────────
# The CUDA singleton is preferred (the launchers reserve ~1.4 GB VRAM for it via
# --n-cpu-moe 26); this CPU instance is the fallback when the CUDA load fails.
_cpu_whisper: WhisperModel | None = None
_cpu_whisper_lock = threading.Lock()


def _get_cpu_whisper() -> WhisperModel:
    """Lazily load (once) the CPU fallback Whisper model.

    Kept resident after the first voice note (~1.5 GB RAM, zero VRAM) so
    subsequent notes skip the cold-load cost. Used only when no LiveKit call
    is holding the CUDA instance."""
    global _cpu_whisper
    if _cpu_whisper is None:
        with _cpu_whisper_lock:
            if _cpu_whisper is None:
                print("[Aster Ears] Booting CPU Faster-Whisper (medium.en, 8-bit) for voice notes...")
                _cpu_whisper = WhisperModel(
                    WHISPER_MODEL_PATH,
                    device="cpu",
                    compute_type="int8",
                )
    return _cpu_whisper


def format_transcript(text: str, speaker: str | None = None,
                      mood: str | None = None) -> str:
    """Attach the canonical [Speaker: X] / [Mood: Y] tags in front of a transcript."""
    tags = ""
    if speaker:
        tags += f"[Speaker: {speaker}] "
    if mood:
        tags += f"[Mood: {mood}] "
    return f"{tags}{text}".strip()


def transcribe_file(path: str) -> str:
    """Transcribe a 16 kHz mono WAV file to text.

    Prefers the shared CUDA model — the launchers reserve VRAM for it
    (`--n-cpu-moe 26`), so voice notes and LiveKit calls share one GPU instance
    that stays resident. If the CUDA load fails (e.g. VRAM lost to another app),
    falls back to the lazy CPU instance rather than failing the turn.
    """
    model = _shared_whisper
    if model is None:
        try:
            model = get_whisper_model()
        except Exception as exc:
            print(f"[Aster Ears] CUDA Faster-Whisper unavailable ({exc}) \u2014 using the CPU instance.")
            model = _get_cpu_whisper()
    segments, _ = model.transcribe(
        path,
        beam_size=5,
        language="en",
        condition_on_previous_text=False,
        initial_prompt="Aster is an AI assistant.",
    )
    text = " ".join(segment.text for segment in segments).strip()
    # Whisper commonly mishears "Aster" as "Esther" — correct it.
    text = re.sub(r'\bEsther\b', 'Aster', text)
    text = re.sub(r'\besther\b', 'aster', text)
    return text


class LocalWhisperSTT(stt.STT):
    def __init__(self):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        # Reuse the process-wide shared model (no second VRAM copy).
        self._whisper_model = get_whisper_model()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Hold the voice-emotion model for the call's lifetime so each _transcribe
        # call doesn't load/offload per utterance.  Released in aclose().
        try:
            from tools.emotion_recognition import acquire_voice_emotion_model
            acquire_voice_emotion_model()
        except Exception:
            pass

    @property
    def model(self) -> str:
        return "medium.en"

    @property
    def provider(self) -> str:
        return "faster-whisper-local"

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        del conn_options

        frame = rtc.combine_audio_frames(buffer)
        audio = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0

        # Downmix multi-channel input to mono for Whisper.
        if frame.num_channels > 1:
            audio = audio.reshape(-1, frame.num_channels).mean(axis=1)

        # Faster-Whisper expects 16kHz float32 when passing raw numpy audio.
        if frame.sample_rate != 16000 and audio.size:
            target_len = int(round(audio.shape[0] * 16000 / frame.sample_rate))
            if target_len > 0:
                src_x = np.linspace(0.0, 1.0, num=audio.shape[0], endpoint=False)
                dst_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
                audio = np.interp(dst_x, src_x, audio).astype(np.float32)

        whisper_language = str(language) if is_given(language) else "en"

        def _transcribe() -> str:
            segments, _ = self._whisper_model.transcribe(
                audio,
                beam_size=5,
                language=whisper_language,
                condition_on_previous_text=False,
                initial_prompt="Aster is an AI assistant.",
            )
            text = " ".join(segment.text for segment in segments).strip()
            # Whisper commonly mishears "Aster" as "Esther" — correct it.
            text = re.sub(r'\bEsther\b', 'Aster', text)
            text = re.sub(r'\besther\b', 'aster', text)
            transcript = text  # clean words — fed to the text-emotion fusion below
            speaker = None
            try:
                from tools.voice_recognition import identify_and_maybe_learn
                speaker = identify_and_maybe_learn(audio, sample_rate=16000)
                if not speaker:
                    _notify_unknown_livekit_voice(audio)
            except Exception:
                pass
            # Fused mood — voice prosody (Tier 1, model warm for the call via the
            # STT's outer acquire) + transcript content (Tier 0, CPU).  Confident
            # text content overrides prosody so high-arousal anger isn't misread
            # as 'happy' (see emotion_recognition.fuse_moods).
            mood = None
            try:
                from tools.emotion_recognition import detect_combined_emotion
                mood = detect_combined_emotion(audio, transcript, sample_rate=16000)
            except Exception:
                pass
            return format_transcript(text, speaker=speaker, mood=mood)

        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(self._pool, _transcribe)

        if text:
            print(f"\n[Aster Heard]: {text}")

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=whisper_language, text=text)],
        )

    async def aclose(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._whisper_model = None
        # Drop the voice-emotion model's call-lifetime hold.
        try:
            from tools.emotion_recognition import release_voice_emotion_model
            release_voice_emotion_model()
        except Exception:
            pass
