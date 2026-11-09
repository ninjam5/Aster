import aiohttp
import asyncio
import contextlib
import os
import re
import time
from collections.abc import Coroutine
from typing import Any

import numpy as np
from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, stt
from livekit.agents.types import APIConnectOptions
from livekit.agents.worker import AgentServer
from livekit.plugins import silero

# The Deepgram plugin self-registers on import (Plugin.register_plugin) and that
# MUST happen on the main thread — a lazy import inside the job entrypoint raises
# "Plugins must be registered on the main thread". So import it here at module
# load (top-level, like silero). Guarded so a local-only build without the plugin
# still boots; _build_stt/_build_tts fall back to local when it is None.
try:
    from livekit.plugins import deepgram
except Exception as _deepgram_import_error:  # pragma: no cover - depends on install
    deepgram = None
    print(f"[Aster Voice] Deepgram plugin unavailable ({_deepgram_import_error}); cloud voice disabled.")

from core.brain import process_user_input
from local_stt import LocalWhisperSTT, release_whisper_model
from local_tts import LocalKokoroTTS
from tools import realtime_stream
import config as _config


# Sourced from secrets.yaml (via config.py) — blank when LiveKit isn't
# configured. Only set the env vars when we actually have real values so the
# LiveKit SDK's own "not configured" errors surface clearly instead of it
# silently trying to connect with empty strings.
LIVEKIT_CONFIGURED = bool(
    _config._secret("livekit", "url", default="")
    and _config._secret("livekit", "api_key", default="")
    and _config._secret("livekit", "api_secret", default="")
)
if LIVEKIT_CONFIGURED:
    os.environ.setdefault("LIVEKIT_URL", _config._secret("livekit", "url", default=""))
    os.environ.setdefault("LIVEKIT_API_KEY", _config._secret("livekit", "api_key", default=""))
    os.environ.setdefault("LIVEKIT_API_SECRET", _config._secret("livekit", "api_secret", default=""))


STT_CONN_OPTIONS = APIConnectOptions(max_retry=2, retry_interval=0.5, timeout=60.0)
TTS_CONN_OPTIONS = APIConnectOptions(max_retry=2, retry_interval=0.5, timeout=120.0)


# Reference to the live bridge + its event loop, so other threads (e.g. the
# Intervention daemon) can push an unprompted turn into an active call.
_active_bridge: "ManualWebRTCBridge | None" = None
_active_loop: asyncio.AbstractEventLoop | None = None


def _build_stt(http_session: Any = None, **kwargs: Any):
    """Pick the STT engine for a call. Returns (base_stt, vad, streaming_stt).

    local    → Faster-Whisper + Silero VAD wrapped in a StreamAdapter.
    deepgram → Deepgram Flux (v2 API) is streaming-only, so it is used directly
               with no StreamAdapter/VAD.
    Kept module-level so it can be exercised by tests without a JobContext.
    """
    if _config.STT_PROVIDER == "deepgram":
        if deepgram is None:
            print("[Aster Voice] ERROR: deepgram plugin missing — falling back to local STT.")
        else:
            dg_kwargs: dict[str, Any] = {
                "model": _config.DEEPGRAM_STT_MODEL,
                "sample_rate": 16000,
                "keyterm": list(_config.DEEPGRAM_STT_KEYTERMS),
                "api_key": _config.DEEPGRAM_API_KEY,
            }
            if http_session is not None:
                dg_kwargs["http_session"] = http_session
            base = deepgram.STTv2(**dg_kwargs)
            print(f"[Aster Ears] STT: Deepgram Flux ({_config.DEEPGRAM_STT_MODEL}).")
            return base, None, base
    base = LocalWhisperSTT()
    vad = silero.VAD.load()
    return base, vad, stt.StreamAdapter(stt=base, vad=vad)


def _build_tts(http_session: Any = None, **kwargs: Any):
    """Pick the TTS engine for a call (local Kokoro or Deepgram Flux TTS)."""
    if _config.TTS_PROVIDER == "deepgram":
        try:
            from deepgram_tts import DeepgramFluxTTS

            return DeepgramFluxTTS(
                model=_config.DEEPGRAM_TTS_MODEL,
                api_key=_config.DEEPGRAM_API_KEY,
                http_session=http_session,
            )
        except Exception as e:
            print(f"[Aster Voice] ERROR: Deepgram Flux TTS unavailable ({e}) — falling back to local TTS.")
    return LocalKokoroTTS()



class ManualWebRTCBridge:
    def __init__(self, ctx: JobContext):
        self._ctx = ctx
        self._room = ctx.room

        # STT/TTS engine selection (config.STT_PROVIDER/TTS_PROVIDER, resolved
        # fail-closed in config.py). "local" is byte-identical to the original
        # Whisper+Kokoro path; "deepgram" uses Deepgram Flux in the cloud.
        try:
            self._http_session = aiohttp.ClientSession()
        except Exception:
            self._http_session = None

        try:
            from livekit.agents import utils
            utils.http_context._new_session_ctx()
        except Exception:
            pass

        try:
            self._base_stt, self._vad, self._streaming_stt = _build_stt(http_session=self._http_session)
        except TypeError:
            self._base_stt, self._vad, self._streaming_stt = _build_stt()

        try:
            self._tts = _build_tts(http_session=self._http_session)
        except TypeError:
            self._tts = _build_tts()
        # Snapshot the STT engine so teardown releases the right resources even
        # if config is mutated at runtime.
        self._stt_is_local = _config.STT_PROVIDER == "local"

        self._audio_source = rtc.AudioSource(
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
        )
        self._audio_track = rtc.LocalAudioTrack.create_audio_track("aster-voice", self._audio_source)
        self._audio_track_pub = None

        self._stop_event = asyncio.Event()
        self._closed = False
        self._response_lock = asyncio.Lock()
        self._response_epoch = 0
        self._response_task: asyncio.Task[None] | None = None
        self._brain_task: asyncio.Task[str] | None = None
        self._tts_task: asyncio.Task[None] | None = None
        self._audio_lock = asyncio.Lock()

        self._track_tasks: dict[str, asyncio.Task[None]] = {}
        self._track_owner: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

        # Utterance coalescing — buffer fragments until the user pauses long enough.
        self._pending_transcript: str = ""
        self._debounce_task: asyncio.Task[None] | None = None

        # Wake-word — agent starts asleep; while asleep, audio is routed to the
        # openWakeWord CPU detector instead of the GPU STT. Re-mutes after
        # WAKE_INACTIVITY_TIMEOUT seconds of silence.
        self._awake = not _config.WAKE_WORD_ENABLED
        self._last_activity = time.monotonic()
        self._stt_final_at = 0.0
        self._wake_detector: Any = None

    async def start(self) -> None:
        print("\n[Aster LiveKit] Agent initializing... connecting to WebRTC room.")

        await self._ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        await self._publish_local_tts_track()
        self._register_room_handlers()
        await self._bootstrap_existing_participants()

        print("[Aster LiveKit] WebRTC bridge online. Listening for speech.")

        if _config.WAKE_WORD_ENABLED:
            self._wake_detector = self._create_wake_detector()
            if self._wake_detector is None:
                # Init failed — degrade gracefully to always-listening.
                self._awake = True
            else:
                realtime_stream.publish_wake(False)
                print(
                    f"[Aster Wake] Asleep — listening for wake word "
                    f"('{_config.WAKE_WORD_MODEL}')."
                )
                self._spawn(self._inactivity_watchdog(), name="wake-inactivity-watchdog")

        await self._stop_event.wait()

    async def aclose(self, *_: str) -> None:
        if self._closed:
            return

        self._closed = True
        self._stop_event.set()

        await self._cancel_active_response(reason="shutdown", clear_audio=True)

        for track_sid in list(self._track_tasks.keys()):
            await self._stop_track_pipeline(track_sid)

        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

        if self._audio_track_pub is not None and self._room.isconnected():
            with contextlib.suppress(Exception):
                await self._room.local_participant.unpublish_track(self._audio_track_pub.sid)

        with contextlib.suppress(Exception):
            await self._audio_source.aclose()
        with contextlib.suppress(Exception):
            await self._streaming_stt.aclose()
        # On the cloud path _streaming_stt IS _base_stt — close it only once.
        if self._base_stt is not self._streaming_stt:
            with contextlib.suppress(Exception):
                await self._base_stt.aclose()
        with contextlib.suppress(Exception):
            await self._tts.aclose()

        if getattr(self, "_http_session", None) is not None and not self._http_session.closed:
            with contextlib.suppress(Exception):
                await self._http_session.close()

        self._wake_detector = None

        # Aggressively release all model references to free VRAM
        self._streaming_stt = None
        self._base_stt = None
        self._vad = None
        self._tts = None

        # Only the local Whisper path holds a CUDA model to unload.
        if self._stt_is_local:
            release_whisper_model()

        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        print("[Aster LiveKit] Session closed. VRAM released.")

    async def _publish_local_tts_track(self) -> None:
        self._audio_track_pub = await self._room.local_participant.publish_track(self._audio_track)

    def _register_room_handlers(self) -> None:
        @self._room.on("participant_connected")
        def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
            self._spawn(
                self._ensure_audio_subscription(participant),
                name=f"participant-connected-{participant.identity}",
            )

        @self._room.on("participant_disconnected")
        def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            self._spawn(
                self._stop_participant_pipelines(participant.sid),
                name=f"participant-disconnected-{participant.identity}",
            )
            # When the last human leaves, end the session so VRAM is released.
            # The SDK removes the participant from remote_participants before firing
            # this event, so a length of 0 means no one is left.
            remaining = list(self._room.remote_participants.values())
            if not remaining and not self._closed:
                print("[Aster LiveKit] Last participant left — ending session.")
                self._stop_event.set()

        @self._room.on("track_published")
        def _on_track_published(
            publication: rtc.RemoteTrackPublication,
            _participant: rtc.RemoteParticipant,
        ) -> None:
            with contextlib.suppress(Exception):
                publication.set_subscribed(True)

        @self._room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if not isinstance(track, rtc.RemoteAudioTrack):
                return

            self._spawn(
                self._start_track_pipeline(track, publication, participant),
                name=f"track-subscribed-{participant.identity}",
            )

        @self._room.on("track_unsubscribed")
        def _on_track_unsubscribed(
            _track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            _participant: rtc.RemoteParticipant,
        ) -> None:
            self._spawn(
                self._stop_track_pipeline(publication.sid),
                name=f"track-unsubscribed-{publication.sid}",
            )

        @self._room.on("reconnecting")
        def _on_reconnecting() -> None:
            print("[Aster LiveKit] Reconnecting to room...")

        @self._room.on("reconnected")
        def _on_reconnected() -> None:
            print("[Aster LiveKit] Reconnected to room.")
            self._spawn(self._bootstrap_existing_participants(), name="rebootstrap-participants")

        @self._room.on("disconnected")
        def _on_disconnected(*_args) -> None:
            print("[Aster LiveKit] Room disconnected.")
            self._stop_event.set()

    async def _bootstrap_existing_participants(self) -> None:
        for participant in self._room.remote_participants.values():
            await self._ensure_audio_subscription(participant)

    async def _ensure_audio_subscription(self, participant: rtc.RemoteParticipant) -> None:
        for publication in participant.track_publications.values():
            with contextlib.suppress(Exception):
                publication.set_subscribed(True)

            track = publication.track
            if isinstance(track, rtc.RemoteAudioTrack):
                await self._start_track_pipeline(track, publication, participant)

    async def _start_track_pipeline(
        self,
        track: rtc.RemoteAudioTrack,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        track_sid = publication.sid or getattr(track, "sid", "") or f"{participant.sid}-audio"
        existing = self._track_tasks.get(track_sid)
        if existing is not None and not existing.done():
            return

        task = self._spawn(
            self._run_track_pipeline(track_sid, track, participant.identity),
            name=f"audio-pipeline-{track_sid}",
        )
        self._track_tasks[track_sid] = task
        self._track_owner[track_sid] = participant.sid

        def _cleanup(done_task: asyncio.Task[None]) -> None:
            self._track_tasks.pop(track_sid, None)
            self._track_owner.pop(track_sid, None)

        task.add_done_callback(_cleanup)

    async def _stop_track_pipeline(self, track_sid: str) -> None:
        task = self._track_tasks.pop(track_sid, None)
        self._track_owner.pop(track_sid, None)
        if task is None:
            return

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _stop_participant_pipelines(self, participant_sid: str) -> None:
        to_stop = [sid for sid, owner_sid in self._track_owner.items() if owner_sid == participant_sid]
        for track_sid in to_stop:
            await self._stop_track_pipeline(track_sid)

    async def _run_track_pipeline(
        self,
        track_sid: str,
        track: rtc.RemoteAudioTrack,
        participant_identity: str,
    ) -> None:
        print(f"[Aster LiveKit] Consuming audio track {track_sid} from {participant_identity}.")

        audio_stream = rtc.AudioStream.from_track(track=track, sample_rate=16000, num_channels=1)
        recognize_stream = self._streaming_stt.stream(conn_options=STT_CONN_OPTIONS)

        async def _forward_audio() -> None:
            # While asleep, audio goes to the openWakeWord CPU detector and the
            # GPU STT stream is left idle. Once awake, frames flow to the STT.
            wake_leftover = np.empty(0, dtype=np.int16)
            async for event in audio_stream:
                if not self._awake and self._wake_detector is not None:
                    wake_leftover = self._process_wake_audio(event.frame, wake_leftover)
                else:
                    recognize_stream.push_frame(event.frame)
            recognize_stream.end_input()

        async def _consume_stt_events() -> None:
            async for speech_event in recognize_stream:
                if speech_event.type == stt.SpeechEventType.START_OF_SPEECH:
                    # User resumed speaking — cancel the debounce so we don't fire
                    # the brain mid-sentence. If Aster is already replying, barge-in.
                    self._cancel_debounce()
                    await self._cancel_active_response(reason="barge-in", clear_audio=True)
                    continue

                if speech_event.type != stt.SpeechEventType.FINAL_TRANSCRIPT:
                    continue
                if not speech_event.alternatives:
                    continue

                transcript = speech_event.alternatives[0].text.strip()
                if not transcript:
                    continue

                self._last_activity = time.monotonic()
                self._stt_final_at = self._last_activity

                # Coalesce: accumulate fragments; the brain fires only after
                # UTTERANCE_DEBOUNCE seconds of silence.
                self._pending_transcript = (
                    (self._pending_transcript + " " + transcript).strip()
                    if self._pending_transcript
                    else transcript
                )
                print(f"[Aster Heard] ({participant_identity}): {transcript}")
                self._restart_debounce()

        try:
            await asyncio.gather(_forward_audio(), _consume_stt_events())
        finally:
            with contextlib.suppress(Exception):
                await audio_stream.aclose()
            with contextlib.suppress(Exception):
                await recognize_stream.aclose()

    async def _start_response_pipeline(self, transcript: str) -> None:
        tasks_to_wait: list[asyncio.Task[Any]] = []
        async with self._response_lock:
            self._response_epoch += 1
            response_epoch = self._response_epoch
            tasks_to_wait = self._cancel_response_tasks_locked(clear_audio=False)

            response_task = asyncio.create_task(
                self._run_response_pipeline(transcript, response_epoch),
                name=f"response-pipeline-{response_epoch}",
            )
            self._response_task = response_task
            self._background_tasks.add(response_task)
            response_task.add_done_callback(self._background_tasks.discard)
            response_task.add_done_callback(self._on_response_done)

        await self._await_task_cancellations(tasks_to_wait)

    async def _cancel_active_response(self, *, reason: str, clear_audio: bool) -> None:
        tasks_to_wait: list[asyncio.Task[Any]] = []
        async with self._response_lock:
            self._response_epoch += 1
            tasks_to_wait = self._cancel_response_tasks_locked(clear_audio=clear_audio)

        if tasks_to_wait:
            print(f"[Aster LiveKit] Interrupting active response ({reason}).")
        await self._await_task_cancellations(tasks_to_wait)

    async def _run_response_pipeline(self, transcript: str, response_epoch: int) -> None:
        brain_task: asyncio.Task[str] | None = None
        tts_task: asyncio.Task[None] | None = None

        loop = asyncio.get_running_loop()
        t_pipeline_start = time.monotonic()
        if self._stt_final_at:
            print(f"[Aster Perf] STT-final -> response start: "
                  f"{(t_pipeline_start - self._stt_final_at) * 1000:.0f}ms "
                  f"(debounce + addressed gate)")

        def _status_callback(status_text: str) -> None:
            clean_status = self._clean_response(status_text)
            if not clean_status:
                return

            try:
                asyncio.run_coroutine_threadsafe(
                    self._speak_status_ack(clean_status, response_epoch),
                    loop,
                )
            except Exception:
                pass

        try:
            # Keep the synchronous orchestrator off the RTC loop thread.
            brain_task = asyncio.create_task(
                asyncio.to_thread(process_user_input, transcript, _status_callback),
                name=f"brain-to-thread-{response_epoch}",
            )
            async with self._response_lock:
                if response_epoch != self._response_epoch:
                    brain_task.cancel()
                    return
                self._brain_task = brain_task

            response_text = await brain_task
            t_brain_done = time.monotonic()

            cleaned_response = self._clean_response(response_text)
            if not cleaned_response:
                elapsed = (t_brain_done - t_pipeline_start) * 1000
                print(f"[Aster Perf] Brain responded in {elapsed:.0f}ms (empty response)")
                return

            async with self._response_lock:
                if response_epoch != self._response_epoch:
                    return

            tts_task = asyncio.create_task(
                self._speak(cleaned_response, response_epoch),
                name=f"tts-playback-{response_epoch}",
            )
            async with self._response_lock:
                if response_epoch != self._response_epoch:
                    tts_task.cancel()
                    return
                self._tts_task = tts_task

            await tts_task
            t_tts_done = time.monotonic()

            elapsed_total = (t_tts_done - t_pipeline_start) * 1000
            elapsed_brain = (t_brain_done - t_pipeline_start) * 1000
            elapsed_tts = (t_tts_done - t_brain_done) * 1000
            print(f"[Aster Perf] Brain: {elapsed_brain:.0f}ms | TTS: {elapsed_tts:.0f}ms | Total: {elapsed_total:.0f}ms")

        except asyncio.CancelledError:
            raise
        finally:
            async with self._response_lock:
                if brain_task is not None and self._brain_task is brain_task:
                    self._brain_task = None
                if tts_task is not None and self._tts_task is tts_task:
                    self._tts_task = None

    async def _speak_status_ack(self, text: str, response_epoch: int) -> None:
        if response_epoch != self._response_epoch:
            return
        await self._speak(text, response_epoch)

    async def _speak(self, text: str, response_epoch: int) -> None:
        _t_speak0 = time.monotonic()
        synth_stream = self._tts.synthesize(text, conn_options=TTS_CONN_OPTIONS)
        _first = True
        try:
            async with self._audio_lock:
                async for synthesized in synth_stream:
                    if _first:
                        _first = False
                        print(f"[Aster Perf] TTS first frame: "
                              f"{(time.monotonic() - _t_speak0) * 1000:.0f}ms")
                    if response_epoch != self._response_epoch:
                        return
                    await self._audio_source.capture_frame(synthesized.frame)

                if response_epoch == self._response_epoch:
                    await self._audio_source.wait_for_playout()
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                self._audio_source.clear_queue()
            raise
        finally:
            with contextlib.suppress(Exception):
                await synth_stream.aclose()

    def _cancel_response_tasks_locked(self, *, clear_audio: bool) -> list[asyncio.Task[Any]]:
        tasks_to_wait: list[asyncio.Task[Any]] = []

        for task in (self._tts_task, self._brain_task, self._response_task):
            if task is None or task.done():
                continue
            task.cancel()
            tasks_to_wait.append(task)

        self._tts_task = None
        self._brain_task = None
        self._response_task = None

        if clear_audio:
            with contextlib.suppress(Exception):
                self._audio_source.clear_queue()

        return tasks_to_wait

    async def _await_task_cancellations(self, tasks: list[asyncio.Task[Any]]) -> None:
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _on_response_done(self, task: asyncio.Task[None]) -> None:
        if self._response_task is task:
            self._response_task = None
        self._log_task_exception(task)

    def _cancel_debounce(self) -> None:
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = None

    def _restart_debounce(self) -> None:
        self._cancel_debounce()
        task = asyncio.create_task(
            self._flush_pending_transcript(),
            name="utterance-debounce",
        )
        self._debounce_task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _flush_pending_transcript(self) -> None:
        await asyncio.sleep(_config.UTTERANCE_DEBOUNCE)
        transcript = self._pending_transcript.strip()
        if not transcript:
            self._debounce_task = None
            return
        print(f"[Aster LiveKit] Coalesced turn: '{transcript}'")
        # ID 19: once awake, EVERY final transcript used to become a full 15-round admin
        # turn — including a side conversation or TV audio caught inside the wake
        # timeout. Conservative: anything but a confident "not for me" is sent.
        # Run the gate OFF the event loop: Laya is a synchronous CPU pass (~0.31 s
        # resident, up to ~35 s on a cold load) and this is the interactive RTC loop —
        # blocking it stalls audio, STT, wake detection and barge-in.
        # QA 2026-09-28: clear the pending transcript only AFTER the gate. A barge-in
        # cancels this coroutine (START_OF_SPEECH cancels the debounce task), and the
        # old order cleared it first, so a cancelled gate silently dropped the turn.
        try:
            addressed = await asyncio.to_thread(_addressed_to_aster, transcript)
        except asyncio.CancelledError:
            self._pending_transcript = transcript   # barge-in: keep it for the retry
            raise
        self._pending_transcript = ""
        self._debounce_task = None
        if not addressed:
            print(f"[Aster LiveKit] Ignored — not addressed to Aster: '{transcript[:60]}'")
            return
        await self._start_response_pipeline(transcript)

    @staticmethod
    def _create_wake_detector() -> Any:
        """Build an openWakeWord detector, or None if it cannot be created."""
        try:
            import openwakeword
            from openwakeword.model import Model

            model_spec = _config.WAKE_WORD_MODEL
            is_custom = model_spec.lower().endswith((".onnx", ".tflite"))
            # Idempotent — fetches the shared feature models (and the named
            # pretrained model) once, then runs fully offline.
            openwakeword.utils.download_models([] if is_custom else [model_spec])
            detector = Model(wakeword_models=[model_spec], inference_framework="onnx")
            print("[Aster Wake] openWakeWord engine ready.")
            return detector
        except Exception as exc:
            print(
                f"[Aster Wake] openWakeWord unavailable ({exc}). "
                "Starting awake — wake word disabled."
            )
            return None

    def _process_wake_audio(self, frame: rtc.AudioFrame, leftover: np.ndarray) -> np.ndarray:
        """Feed 16 kHz mono PCM into openWakeWord in fixed-size steps. Returns
        the unconsumed tail to prepend next call. Wakes Aster on detection."""
        samples = np.frombuffer(frame.data, dtype=np.int16)
        buf = np.concatenate((leftover, samples)) if leftover.size else samples

        step = 1280  # openWakeWord expects 80 ms (1280-sample) chunks at 16 kHz
        idx = 0
        detected = False
        while buf.size - idx >= step:
            scores = self._wake_detector.predict(buf[idx:idx + step])
            if any(score >= _config.WAKE_WORD_THRESHOLD for score in scores.values()):
                detected = True
            idx += step

        if detected:
            self._wake_detector.reset()
            self._wake_up()
            return np.empty(0, dtype=np.int16)
        return np.array(buf[idx:], dtype=np.int16)

    def _wake_up(self) -> None:
        self._awake = True
        self._last_activity = time.monotonic()
        print("[Aster Wake] Awake.")
        realtime_stream.publish_wake(True)

    def _go_to_sleep(self) -> None:
        self._awake = False
        self._pending_transcript = ""
        self._cancel_debounce()
        if self._wake_detector is not None:
            with contextlib.suppress(Exception):
                self._wake_detector.reset()
        print("[Aster Wake] Asleep (timeout).")
        realtime_stream.publish_wake(False)

    async def _inactivity_watchdog(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(15)
            if self._awake and (
                time.monotonic() - self._last_activity >= _config.WAKE_INACTIVITY_TIMEOUT
            ):
                self._go_to_sleep()

    @staticmethod
    def _clean_response(response_text: str) -> str:
        if not response_text:
            return ""

        cleaned = re.sub(r"\[NATIVE_AUDIO_PAYLOAD:.*?\]\s*", "", response_text, flags=re.DOTALL)
        return cleaned.strip()

    def _spawn(self, coro: Coroutine[Any, Any, None], *, name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(self._log_task_exception)
        return task

    @staticmethod
    def _log_task_exception(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            print(f"[Aster LiveKit] Background task failed: {exc}")


# ---------------------------------------------------------------------------
# AgentServer — v1.5.x pattern with agent_name for Agent Console discovery
# ---------------------------------------------------------------------------
server = AgentServer()


@server.rtc_session(agent_name="aster")
async def _entrypoint(ctx: JobContext) -> None:
    global _active_bridge, _active_loop
    bridge = ManualWebRTCBridge(ctx)
    ctx.add_shutdown_callback(bridge.aclose)
    _active_bridge = bridge
    _active_loop = asyncio.get_running_loop()
    try:
        await bridge.start()
    finally:
        if _active_bridge is bridge:
            _active_bridge = None
            _active_loop = None
        await bridge.aclose()


_ASTER_NAME_RE = re.compile(r"\baster\b", re.IGNORECASE)


def _addressed_to_aster(transcript: str) -> bool:
    """Is this utterance actually meant for Aster? (ID 19 of laya-integration.md)

    Cheap exact check first: if the transcript names Aster, it is for him — no model
    call. Word-bounded so "master", "disaster", "faster" and "plaster" do not count.
    Otherwise a conservative Laya gate at a STRICTER margin than the usual 0.25.

    FAIL-OPEN by design: kernel off, low margin, a failure or an unrecognized verdict
    all return True (send it to the brain). Suppressing a real request is the worse
    error, so the only suppression is a confident, logged verdict.
    """
    text = str(transcript or "").strip()
    if not text:
        return False
    if _ASTER_NAME_RE.search(text):
        return True
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return True
        verdict = system1.choose(
            "Is this utterance addressed to the AI assistant Aster, or is it speech "
            "between other people / from a TV or recording?",
            {
                "A": "addressed to the assistant — a question, request or command for Aster",
                "B": ("a conversation between other people, or speech from media — "
                      "not intended for the assistant"),
            },
            key="addressed",
            state={"transcript": text[:400]},
            min_margin=0.6,   # stricter than the 0.25 default: suppression is a real loss
        )
    except Exception:
        return True
    if verdict.get("escalate"):
        return True
    if verdict.get("choice") == "B":
        # Log the evidence — a wrong suppression is otherwise invisible.
        print(f"[Aster LiveKit] Gate judged NOT addressed (margin "
              f"{verdict.get('margin')}, dist {verdict.get('distribution')}).")
        return False
    return True


class LocalWebSocketVoiceBridge(ManualWebRTCBridge):
    """Direct local WebSocket voice call bridge (ws://127.0.0.1:8000/api/voice/ws).

    Mainstream desktop call transport replacing LiveKit for local use (zero
    subprocess fork, zero WebRTC cloud round-trip). Shares the exact STT/TTS
    builders, Silero VAD, openWakeWord, Laya ID-19 addressed-gate, utterance
    coalescing debounce, barge-in cancellation, and VRAM teardown as
    ManualWebRTCBridge.

    Wire protocol:
      * Client -> Server binary: 16 kHz mono int16 little-endian PCM frames
      * Client -> Server text  : JSON {"type": "mute", "muted": bool} | {"type": "ping"} | {"type": "hangup"}
      * Server -> Client binary: 24 kHz mono int16 little-endian PCM frames (TTS output)
      * Server -> Client text  : JSON {"type": "ready"|"clear_audio"|"speaking"|"pong", ...}
    """

    def __init__(self, websocket: Any):
        import json as _json
        self._json = _json
        self._ws = websocket
        self._ctx = None
        self._room = None

        try:
            self._http_session = aiohttp.ClientSession()
        except Exception:
            self._http_session = None

        try:
            from livekit.agents import utils
            utils.http_context._new_session_ctx()
        except Exception:
            pass

        try:
            self._base_stt, self._vad, self._streaming_stt = _build_stt(http_session=self._http_session)
        except TypeError:
            self._base_stt, self._vad, self._streaming_stt = _build_stt()

        try:
            self._tts = _build_tts(http_session=self._http_session)
        except TypeError:
            self._tts = _build_tts()
        self._stt_is_local = _config.STT_PROVIDER == "local"

        self._audio_source = None
        self._audio_track = None
        self._audio_track_pub = None

        self._stop_event = asyncio.Event()
        self._closed = False
        self._muted = False
        self._playout_until = 0.0
        self._ws_send_lock = asyncio.Lock()

        self._response_lock = asyncio.Lock()
        self._response_epoch = 0
        self._response_task: asyncio.Task[None] | None = None
        self._brain_task: asyncio.Task[str] | None = None
        self._tts_task: asyncio.Task[None] | None = None
        self._audio_lock = asyncio.Lock()

        self._track_tasks: dict[str, asyncio.Task[None]] = {}
        self._track_owner: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

        self._pending_transcript: str = ""
        self._debounce_task: asyncio.Task[None] | None = None

        self._awake = not _config.WAKE_WORD_ENABLED
        self._last_activity = time.monotonic()
        self._stt_final_at = 0.0
        self._wake_detector: Any = None

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._closed or self._ws is None:
            return
        with contextlib.suppress(Exception):
            async with self._ws_send_lock:
                await self._ws.send_text(self._json.dumps(payload))

    async def _send_bytes(self, data: bytes) -> None:
        if self._closed or self._ws is None or not data:
            return
        with contextlib.suppress(Exception):
            async with self._ws_send_lock:
                await self._ws.send_bytes(data)

    def _handle_control_message(self, raw_text: str) -> None:
        try:
            data = self._json.loads(raw_text)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        msg_type = data.get("type")
        if msg_type == "mute":
            self._muted = bool(data.get("muted", False))
        elif msg_type == "ping":
            self._spawn(self._send_json({"type": "pong"}), name="ws-pong")
        elif msg_type == "hangup":
            self._stop_event.set()

    async def start(self) -> None:
        print("\n[Aster Voice WS] Direct local WebSocket call connected.")
        await self._send_json({
            "type": "ready",
            "sample_rate_in": 16000,
            "sample_rate_out": getattr(self._tts, "sample_rate", 24000),
            "channels": getattr(self._tts, "num_channels", 1),
            "awake": self._awake,
        })

        if _config.WAKE_WORD_ENABLED:
            self._wake_detector = self._create_wake_detector()
            if self._wake_detector is None:
                self._awake = True
            else:
                realtime_stream.publish_wake(False)
                print(
                    f"[Aster Wake] Asleep — listening for wake word "
                    f"('{_config.WAKE_WORD_MODEL}')."
                )
                self._spawn(self._inactivity_watchdog(), name="wake-inactivity-watchdog")
        else:
            realtime_stream.publish_wake(True)

        await self._run_ws_pipeline()

    async def _run_ws_pipeline(self) -> None:
        try:
            from livekit.agents import utils
            if utils.http_context._ContextVar.get(None) is None:
                utils.http_context._new_session_ctx()
        except Exception:
            pass
        if getattr(self._streaming_stt, "_session", None) is None and getattr(self, "_http_session", None) is not None:
            self._streaming_stt._session = self._http_session
        recognize_stream = self._streaming_stt.stream(conn_options=STT_CONN_OPTIONS)

        async def _receive_ws_audio() -> None:
            wake_leftover = np.empty(0, dtype=np.int16)
            try:
                while not self._stop_event.is_set() and not self._closed:
                    msg = await self._ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    raw_bytes = msg.get("bytes")
                    if raw_bytes:
                        if self._muted:
                            continue
                        if len(raw_bytes) % 2 != 0:
                            raw_bytes = raw_bytes[:-1]
                        if not raw_bytes:
                            continue
                        frame = rtc.AudioFrame(
                            data=raw_bytes,
                            sample_rate=16000,
                            num_channels=1,
                            samples_per_channel=len(raw_bytes) // 2,
                        )
                        if not self._awake and self._wake_detector is not None:
                            wake_leftover = self._process_wake_audio(frame, wake_leftover)
                        else:
                            recognize_stream.push_frame(frame)
                        continue
                    text_data = msg.get("text")
                    if text_data:
                        self._handle_control_message(text_data)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                with contextlib.suppress(Exception):
                    recognize_stream.end_input()
                self._stop_event.set()

        async def _consume_stt_events() -> None:
            try:
                async for speech_event in recognize_stream:
                    if speech_event.type == stt.SpeechEventType.START_OF_SPEECH:
                        self._cancel_debounce()
                        await self._cancel_active_response(reason="barge-in", clear_audio=True)
                        continue

                    if speech_event.type != stt.SpeechEventType.FINAL_TRANSCRIPT:
                        continue
                    if not speech_event.alternatives:
                        continue

                    transcript = speech_event.alternatives[0].text.strip()
                    if not transcript:
                        continue

                    self._last_activity = time.monotonic()
                    self._stt_final_at = self._last_activity

                    self._pending_transcript = (
                        (self._pending_transcript + " " + transcript).strip()
                        if self._pending_transcript
                        else transcript
                    )
                    print(f"[Aster Heard] (local-ws): {transcript}")
                    self._restart_debounce()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._closed:
                    print(f"[Aster Voice WS] STT stream ended: {exc}")

        rx_task = asyncio.create_task(_receive_ws_audio(), name="ws-audio-rx")
        stt_task = asyncio.create_task(_consume_stt_events(), name="ws-stt-events")
        stop_wait = asyncio.create_task(self._stop_event.wait(), name="ws-stop-wait")
        try:
            await asyncio.wait(
                {rx_task, stt_task, stop_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (rx_task, stt_task, stop_wait):
                if not t.done():
                    t.cancel()
            await asyncio.gather(rx_task, stt_task, stop_wait, return_exceptions=True)
            with contextlib.suppress(Exception):
                await recognize_stream.aclose()

    def _cancel_response_tasks_locked(self, *, clear_audio: bool) -> list[asyncio.Task[Any]]:
        tasks_to_wait = super()._cancel_response_tasks_locked(clear_audio=False)
        if clear_audio:
            self._playout_until = 0.0
            self._spawn(self._send_json({"type": "clear_audio"}), name="ws-clear-audio")
        return tasks_to_wait

    async def _speak(self, text: str, response_epoch: int) -> None:
        _t_speak0 = time.monotonic()
        synth_stream = self._tts.synthesize(text, conn_options=TTS_CONN_OPTIONS)
        _first = True
        try:
            async with self._audio_lock:
                await self._send_json({"type": "speaking", "speaking": True})
                async for synthesized in synth_stream:
                    if _first:
                        _first = False
                        print(f"[Aster Perf] TTS first frame: "
                              f"{(time.monotonic() - _t_speak0) * 1000:.0f}ms")
                    if response_epoch != self._response_epoch or self._closed:
                        return
                    pcm_bytes = synthesized.frame.data.tobytes()
                    if not pcm_bytes:
                        continue
                    samples = synthesized.frame.samples_per_channel or (len(pcm_bytes) // 2)
                    dur = samples / float(getattr(self._tts, "sample_rate", 24000) or 24000)
                    now = time.monotonic()
                    if self._playout_until < now:
                        self._playout_until = now
                    self._playout_until += dur
                    await self._send_bytes(pcm_bytes)

                if response_epoch == self._response_epoch and not self._closed:
                    remaining = self._playout_until - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            self._playout_until = 0.0
            await self._send_json({"type": "clear_audio"})
            raise
        finally:
            with contextlib.suppress(Exception):
                await self._send_json({"type": "speaking", "speaking": False})
            with contextlib.suppress(Exception):
                await synth_stream.aclose()

    async def aclose(self, *_: str) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()

        await self._cancel_active_response(reason="shutdown", clear_audio=False)

        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

        with contextlib.suppress(Exception):
            await self._streaming_stt.aclose()
        if self._base_stt is not self._streaming_stt:
            with contextlib.suppress(Exception):
                await self._base_stt.aclose()
        with contextlib.suppress(Exception):
            await self._tts.aclose()

        self._wake_detector = None
        self._streaming_stt = None
        self._base_stt = None
        self._vad = None
        self._tts = None

        if getattr(self, "_http_session", None) is not None and not self._http_session.closed:
            with contextlib.suppress(Exception):
                await self._http_session.close()

        if self._stt_is_local:
            release_whisper_model()

        with contextlib.suppress(Exception):
            await self._ws.close()
        self._ws = None

        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        print("[Aster Voice WS] Session closed. VRAM released.")


async def handle_voice_websocket(websocket: Any) -> None:
    """FastAPI WebSocket handler for /api/voice/ws."""
    global _active_bridge, _active_loop
    await websocket.accept()
    if _active_bridge is not None and not _active_bridge._closed:
        with contextlib.suppress(Exception):
            await _active_bridge.aclose()
    bridge = LocalWebSocketVoiceBridge(websocket)
    _active_bridge = bridge
    _active_loop = asyncio.get_running_loop()
    try:
        await bridge.start()
    finally:
        if _active_bridge is bridge:
            _active_bridge = None
            _active_loop = None
        await bridge.aclose()


def call_is_active() -> bool:
    """True when a LiveKit call is live with a remote participant connected."""
    bridge = _active_bridge
    if bridge is None or bridge._closed:
        return False
    try:
        if isinstance(bridge, LocalWebSocketVoiceBridge):
            return True
        return bridge._room.isconnected() and bool(bridge._room.remote_participants)
    except Exception:
        return False


def speak_intervention(text: str) -> bool:
    """Push an unprompted turn into the active call (brain → TTS over the call).

    Reuses the existing response pipeline with its epoch/lock safety — no manual
    audio injection. Returns False if there is no usable call to speak into.
    """
    bridge = _active_bridge
    loop = _active_loop
    if bridge is None or loop is None or bridge._closed:
        return False
    try:
        bridge._awake = True  # light up the reactor face
        asyncio.run_coroutine_threadsafe(
            bridge._start_response_pipeline(text), loop
        )
        return True
    except Exception as e:
        print(f"[Aster Intervention] speak_intervention failed: {e}")
        return False


def start_agent() -> None:
    """Start the agent server via the LiveKit CLI (supports dev/start/console modes)."""
    cli.run_app(server)


def start_agent_background() -> None:
    """Start the agent server in a background thread (for main.py integration).

    Uses server.run(devmode=False) directly instead of cli.run_app() so it runs in-process without forking.
    since cli.run_app() is designed to be the main entrypoint.
    """
    if not LIVEKIT_CONFIGURED:
        print("[Aster Voice] LiveKit not configured (see secrets.yaml) — voice call disabled.")
        return
    asyncio.run(server.run(devmode=False))


if __name__ == "__main__":
    start_agent()
