import asyncio
import threading
import time
import numpy as np
import concurrent.futures
from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid

import config
from tools.audio import acquire_kokoro_pipeline, release_kokoro_pipeline


def _report_synthesis_error(text: str, e: Exception) -> None:
    import traceback
    msg = f"[Aster TTS] Kokoro synthesis error for {text[:40]!r}: {e}\n{traceback.format_exc()}"
    print(msg)
    try:
        from tools.diagnostics import send_error
        send_error("Kokoro TTS synthesis (call)", e)
    except Exception:
        pass


def _to_pcm_bytes(audio) -> bytes:
    """Kokoro outputs float32 [-1, 1]; LiveKit expects 16-bit PCM bytes."""
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _pcm_chunks(pipeline, text: str, voice, speed, stop_event: threading.Event):
    """Yield int16 PCM bytes per Kokoro segment as they are synthesized.

    Kokoro's pipeline generator already splits text into segments internally —
    this streams each segment out instead of collecting them all (the Level-1
    streaming-TTS win: first audio after the first segment, not the whole
    reply). Checks stop_event between segments so a barge-in cancellation
    stops synthesis promptly. Errors are reported, never raised.
    """
    if pipeline is None:
        print("[Aster TTS] Kokoro pipeline is None — skipping synthesis.")
        return
    try:
        generator = pipeline(text, voice=voice, speed=speed)
        for _, _, audio in generator:
            if stop_event.is_set():
                return
            if audio is None:
                continue
            yield _to_pcm_bytes(audio)
    except Exception as e:
        _report_synthesis_error(text, e)


def _stream_worker(pipeline, text: str, voice, speed, loop, queue: asyncio.Queue,
                   stop_event: threading.Event) -> None:
    """Executor-thread side of chunk streaming: synthesize segments and hand
    each to the event loop; a None sentinel always marks completion."""
    try:
        for chunk in _pcm_chunks(pipeline, text, voice, speed, stop_event):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except RuntimeError:
                return  # event loop closed (call torn down) — stop quietly
    finally:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except RuntimeError:
            pass


class KokoroChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance, text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts_instance, input_text=text, conn_options=conn_options)
        self._tts = tts_instance

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
        )

        if config.TTS_CHUNK_STREAMING:
            await self._run_streaming(output_emitter)
        else:
            await self._run_blocking(output_emitter)

    async def _run_streaming(self, output_emitter: tts.AudioEmitter) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop_event = threading.Event()
        t_start = time.monotonic()
        first_chunk = True

        self._tts._pool.submit(
            _stream_worker,
            self._tts.pipeline, self.input_text, self._tts.voice,
            config.VOICE_SPEED, loop, queue, stop_event,
        )
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if first_chunk:
                    first_chunk = False
                    print(f"[Aster Perf] First audio chunk in {(time.monotonic() - t_start) * 1000:.0f}ms")
                output_emitter.push(chunk)
            # flush() must always be called after initialize() — even on empty
            # audio — or LiveKit throws "no audio frames were pushed".
            output_emitter.flush()
        finally:
            # Cancellation (barge-in) or error: stop the synthesis thread at
            # the next segment boundary instead of burning GPU on dead audio.
            stop_event.set()

    async def _run_blocking(self, output_emitter: tts.AudioEmitter) -> None:
        """Legacy collect-then-push path (runtime.tts_chunk_streaming: false)."""
        loop = asyncio.get_running_loop()
        audio_data = await loop.run_in_executor(
            self._tts._pool,
            self._generate_audio,
            self.input_text,
        )

        if audio_data is not None and len(audio_data) > 0:
            output_emitter.push(_to_pcm_bytes(audio_data))
        output_emitter.flush()

    def _generate_audio(self, text: str):
        pipeline = self._tts.pipeline
        if pipeline is None:
            print("[Aster TTS] Kokoro pipeline is None — skipping synthesis.")
            return None
        try:
            generator = pipeline(text, voice=self._tts.voice, speed=config.VOICE_SPEED)
            audio_chunks = [audio for _, _, audio in generator if audio is not None]
            if not audio_chunks:
                print(f"[Aster TTS] Kokoro returned no audio chunks for: {text[:60]!r}")
                return None
            return np.concatenate(audio_chunks)
        except Exception as e:
            _report_synthesis_error(text, e)
            return None


def _resolve_voice() -> str:
    """Return the Kokoro voice argument for live calls.

    Delegates entirely to tools.audio.resolve_voice_arg(config.VOICE_NAME) so
    the live call path, Telegram/CLI, and voice-preview all use the same logic:
    a custom clone in TTS-Voices/ is returned as its .pt path; a built-in id is
    returned unchanged.  The user's explicit voice selection always wins —
    hard-coded aster.pt precedence has been removed.
    """
    import config as _cfg
    from tools.audio import resolve_voice_arg
    return resolve_voice_arg(_cfg.VOICE_NAME)


class LocalKokoroTTS(tts.TTS):
    def __init__(self):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1
        )
        print("[Aster Voice] Acquiring shared Kokoro TTS engine for call...")
        # Hold the process-wide shared Kokoro pipeline for the call's lifetime.
        pipeline = acquire_kokoro_pipeline()
        if pipeline is None:
            print("[Aster Voice] WARNING: Kokoro pipeline unavailable — call will be mute.")
        self.voice = _resolve_voice()
        print(f"[Aster Voice] Using voice: {self.voice}")
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    @property
    def pipeline(self):
        """The shared Kokoro pipeline (loaded/owned by tools.audio)."""
        import tools.audio as _audio
        return _audio.kokoro_pipeline

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return KokoroChunkedStream(
            tts_instance=self,
            text=text,
            conn_options=conn_options,
        )

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.SynthesizeStream:
        # LiveKit adapter to provide streaming mode over non-streaming synthesize.
        from livekit.agents.tts import StreamAdapter
        return StreamAdapter(tts=self).stream(conn_options=conn_options)

    async def aclose(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        # Drop this call's hold — Kokoro offloads from VRAM if no users remain.
        release_kokoro_pipeline()
