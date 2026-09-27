"""tools/ambient_audio.py — Tier-1b ambient room voice-emotion monitoring.

A dedicated, opt-in daemon that passively listens to the room through a local
microphone so Aster reads Mohamed's vocal tone even when he isn't addressing it
(e.g. upset on the phone, venting to a friend). VAD-gated, owner-gated via the
ECAPA speaker model, paused during LiveKit calls, CPU-only. Default OFF.

Pipeline:
  sounddevice 16k mono → webrtcvad 30ms frames → accumulate a speech utterance
  → (flush on trailing silence, if >= AMBIENT_MIN_UTTERANCE_SECONDS)
  → skip while a LiveKit call is active (webrtc_bridge.call_is_active())
  → identify_speaker(buf) == OWNER_NAME?   (rejects friends / Aster's TTS / music)
  → detect_ambient_voice_emotion(buf)      (CPU wav2vec2, EMA-smoothed)
  → feed the sustained-mood window (cooldowned) so silent-period check-ins fire

Nothing here touches the GPU, the webcam, or llama-server. All failures are
swallowed — the daemon must never crash the process. The microphone is opened
only while AMBIENT_AUDIO_ENABLED is true; toggling it off closes the device.
"""

import time

import numpy as np

import config

# Frame geometry — webrtcvad needs 10/20/30 ms mono 16-bit PCM frames.
_SAMPLE_RATE = 16000
_FRAME_MS = 30
_FRAME_SAMPLES = int(_SAMPLE_RATE * _FRAME_MS / 1000)          # 480 samples
_SILENCE_FLUSH_FRAMES = int(0.6 * 1000 / _FRAME_MS)            # ~0.6 s of silence ends an utterance
_MAX_UTTERANCE_FRAMES = int(12.0 * 1000 / _FRAME_MS)           # hard cap so the buffer can't grow forever
_STALE_RESET_SECONDS = 600                                     # drop a held tone after 10 min of no owner speech

# Module state
_last_window_feed = 0.0       # monotonic ts of the last sustained-mood-window feed (cooldown)
_last_owner_utterance = 0.0   # monotonic ts of the last accepted owner utterance (stale reset)


def _lazy_imports():
    """Import the optional audio deps on demand. Raises if either is missing."""
    import sounddevice as sd       # PortAudio
    import webrtcvad               # tiny C VAD
    return sd, webrtcvad


def _process_utterance(audio: "np.ndarray") -> None:
    """Owner-gate one captured utterance and fold its vocal tone into the mood
    state. `audio` is float32 mono at 16 kHz. Best-effort; never raises."""
    global _last_window_feed, _last_owner_utterance
    try:
        duration = len(audio) / _SAMPLE_RATE
        if duration < config.AMBIENT_MIN_UTTERANCE_SECONDS:
            return

        # Pause during a live LiveKit call — that path already reads Mohamed's
        # voice (and the speakers would echo Aster's own TTS back into the mic).
        try:
            import webrtc_bridge
            if webrtc_bridge.call_is_active():
                return
        except Exception:
            pass

        # Owner gate — only trust tone we can attribute to Mohamed. Rejects a
        # visiting friend's voice, Aster's TTS, and background media in one check.
        if config.AMBIENT_VOICE_GATE_OWNER:
            try:
                from tools.voice_recognition import identify_speaker
                speaker = identify_speaker(audio, _SAMPLE_RATE)
            except Exception:
                speaker = None
            if speaker != config.OWNER_NAME:
                return

        from tools import emotion_recognition as er
        mood = er.detect_ambient_voice_emotion(audio, _SAMPLE_RATE)
        _last_owner_utterance = time.monotonic()

        # Feed the sustained-mood window (deque + trend JSONL) so a sustained
        # ambient mood can drive proactive check-ins — throttled so frequent
        # utterances don't flood get_mood_streak().
        if mood and mood != "neutral":
            now = time.monotonic()
            if now - _last_window_feed >= config.AMBIENT_WINDOW_FEED_COOLDOWN:
                _last_window_feed = now
                try:
                    er.log_mood_turn(mood, "(ambient room)")
                except Exception:
                    pass
    except Exception as e:
        print(f"[Ambient Audio] utterance processing error: {e}")


def _run_capture_session(sd, webrtcvad) -> None:
    """Open the mic and stream utterances until ambient audio is toggled off or
    an error bubbles up. Returns cleanly when AMBIENT_AUDIO_ENABLED flips false."""
    vad = webrtcvad.Vad(int(config.AMBIENT_VAD_AGGRESSIVENESS))
    voiced: list[bytes] = []
    triggered = False
    num_silent = 0

    print(f"[Ambient Audio] mic open — listening (device={config.AMBIENT_AUDIO_DEVICE}, "
          f"gate_owner={config.AMBIENT_VOICE_GATE_OWNER}).")
    with sd.RawInputStream(samplerate=_SAMPLE_RATE, channels=1, dtype="int16",
                           blocksize=_FRAME_SAMPLES, device=config.AMBIENT_AUDIO_DEVICE) as stream:
        while config.AMBIENT_AUDIO_ENABLED:
            data, _overflowed = stream.read(_FRAME_SAMPLES)
            frame = bytes(data)
            if len(frame) < _FRAME_SAMPLES * 2:   # short read (e.g. on close) — skip
                continue

            try:
                is_speech = vad.is_speech(frame, _SAMPLE_RATE)
            except Exception:
                is_speech = False

            if not triggered:
                if is_speech:
                    triggered = True
                    voiced = [frame]
                    num_silent = 0
                else:
                    # Idle silence — drop a stale held tone after a long quiet gap.
                    if (_last_owner_utterance
                            and time.monotonic() - _last_owner_utterance > _STALE_RESET_SECONDS):
                        try:
                            from tools import emotion_recognition as er
                            er.reset_ambient_voice_mood()
                        except Exception:
                            pass
            else:
                voiced.append(frame)
                num_silent = 0 if is_speech else num_silent + 1
                if num_silent > _SILENCE_FLUSH_FRAMES or len(voiced) >= _MAX_UTTERANCE_FRAMES:
                    pcm = b"".join(voiced)
                    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    _process_utterance(audio)
                    triggered = False
                    voiced = []
                    num_silent = 0
    print("[Ambient Audio] mic closed.")


def ambient_audio_daemon() -> None:
    """Daemon entry point (started from main.py). Idles with no mic open until
    AMBIENT_AUDIO_ENABLED; opens the device while enabled; reopens after errors."""
    print("[Aster Core] Ambient Audio subsystem initialized (idle until enabled).")
    while True:
        if not config.AMBIENT_AUDIO_ENABLED:
            time.sleep(2)
            continue
        try:
            sd, webrtcvad = _lazy_imports()
        except Exception as e:
            print(f"[Ambient Audio] dependencies unavailable ({e}); install "
                  f"`sounddevice webrtcvad`. Disabling ambient audio.")
            config.AMBIENT_AUDIO_ENABLED = False
            continue
        try:
            _run_capture_session(sd, webrtcvad)
        except Exception as e:
            print(f"[Ambient Audio] capture session error: {e}")
            time.sleep(5)
        finally:
            # Clear any held tone when the mic stops so it doesn't go stale.
            try:
                from tools import emotion_recognition as er
                er.reset_ambient_voice_mood()
            except Exception:
                pass
