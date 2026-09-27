"""Tests for Level-1 streaming TTS (local_tts.py chunk streaming).

Uses a fake Kokoro pipeline and a fake AudioEmitter — no LiveKit room, no GPU.
KokoroChunkedStream._run is driven directly with a stub tts instance.
"""
import asyncio
import concurrent.futures
import threading

import numpy as np
import pytest

import config
import local_tts


def _fake_pipeline(segments):
    """Kokoro-shaped pipeline: call → generator of (graphemes, phonemes, audio)."""
    def pipeline(text, voice=None, speed=None):
        for seg in segments:
            yield ("g", "p", seg)
    return pipeline


def _seg(n=100, value=0.5):
    return np.full(n, value, dtype=np.float32)


class FakeEmitter:
    def __init__(self):
        self.initialized = False
        self.pushes = []
        self.flushed = False

    def initialize(self, **kwargs):
        self.initialized = True

    def push(self, data: bytes):
        self.pushes.append(data)

    def flush(self):
        self.flushed = True


class StubTTS:
    """Duck-typed stand-in for LocalKokoroTTS — never loads Kokoro."""
    sample_rate = 24000
    num_channels = 1
    voice = "af_nova"

    def __init__(self, pipeline):
        self._pipeline = pipeline
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    @property
    def pipeline(self):
        return self._pipeline


def _make_stream(tts_stub):
    """Build a KokoroChunkedStream without running livekit's __init__ —
    _run only touches self._tts and self.input_text."""
    stream = object.__new__(local_tts.KokoroChunkedStream)
    stream._tts = tts_stub
    return stream


def _run(stream, text="hello world"):
    class _S:
        pass
    # input_text is a property on livekit's ChunkedStream; bypass with a slot
    object.__setattr__(stream, "_input_text_override", text)
    local_tts.KokoroChunkedStream.input_text = property(lambda self: self._input_text_override)
    emitter = FakeEmitter()
    asyncio.run(stream._run(emitter))
    return emitter


# ── _pcm_chunks generator ────────────────────────────────────────────────────

def test_pcm_chunks_yields_one_chunk_per_segment():
    stop = threading.Event()
    chunks = list(local_tts._pcm_chunks(_fake_pipeline([_seg(), _seg(), _seg()]),
                                        "text", "voice", 1.0, stop))
    assert len(chunks) == 3
    assert all(isinstance(c, bytes) and len(c) == 200 for c in chunks)  # 100 samples × int16


def test_pcm_chunks_skips_none_segments():
    stop = threading.Event()
    chunks = list(local_tts._pcm_chunks(_fake_pipeline([_seg(), None, _seg()]),
                                        "text", "voice", 1.0, stop))
    assert len(chunks) == 2


def test_pcm_chunks_stop_event_halts_between_segments():
    stop = threading.Event()
    gen = local_tts._pcm_chunks(_fake_pipeline([_seg(), _seg(), _seg()]),
                                "text", "voice", 1.0, stop)
    first = next(gen)
    assert first
    stop.set()
    assert list(gen) == []


def test_pcm_chunks_none_pipeline_yields_nothing():
    assert list(local_tts._pcm_chunks(None, "t", "v", 1.0, threading.Event())) == []


def test_pcm_chunks_error_pipeline_reported_not_raised(monkeypatch):
    """A failing pipeline must be caught and reported via diagnostics.send_error
    (never raised) — and must never reach the real Telegram bot."""
    import tools.diagnostics as diag

    reported = []
    monkeypatch.setattr(diag, "send_error", lambda ctx, exc: reported.append((ctx, exc)))

    def exploding(text, voice=None, speed=None):
        raise RuntimeError("boom")
        yield  # pragma: no cover
    chunks = list(local_tts._pcm_chunks(exploding, "t", "v", 1.0, threading.Event()))
    assert chunks == []
    assert reported and reported[0][0] == "Kokoro TTS synthesis (call)"


def test_pcm_conversion_clips_and_scales():
    data = local_tts._to_pcm_bytes(np.array([2.0, -2.0, 0.0], dtype=np.float32))
    arr = np.frombuffer(data, dtype=np.int16)
    assert arr[0] == 32767 and arr[1] == -32767 and arr[2] == 0


# ── _run: streaming vs blocking ──────────────────────────────────────────────

def test_streaming_run_pushes_per_segment(monkeypatch):
    monkeypatch.setattr(config, "TTS_CHUNK_STREAMING", True)
    stub = StubTTS(_fake_pipeline([_seg(), _seg(), _seg()]))
    emitter = _run(_make_stream(stub))
    assert emitter.initialized
    assert len(emitter.pushes) == 3  # one push per Kokoro segment, not one big blob
    assert emitter.flushed


def test_streaming_run_empty_pipeline_still_flushes(monkeypatch):
    monkeypatch.setattr(config, "TTS_CHUNK_STREAMING", True)
    stub = StubTTS(_fake_pipeline([]))
    emitter = _run(_make_stream(stub))
    assert emitter.pushes == []
    assert emitter.flushed  # flush-always contract preserved


def test_streaming_run_none_pipeline_still_flushes(monkeypatch):
    monkeypatch.setattr(config, "TTS_CHUNK_STREAMING", True)
    stub = StubTTS(None)
    emitter = _run(_make_stream(stub))
    assert emitter.pushes == []
    assert emitter.flushed


def test_flag_off_uses_single_concatenated_push(monkeypatch):
    monkeypatch.setattr(config, "TTS_CHUNK_STREAMING", False)
    stub = StubTTS(_fake_pipeline([_seg(), _seg(), _seg()]))
    emitter = _run(_make_stream(stub))
    assert len(emitter.pushes) == 1  # legacy collect-then-push
    assert len(emitter.pushes[0]) == 600  # 3 × 100 samples × int16
    assert emitter.flushed
