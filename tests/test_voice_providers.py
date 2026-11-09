"""Tests for the cloud voice swap — Deepgram Flux STT/TTS behind a flag.

No network, no GPU models, no LiveKit room: the bridge's provider factories
(webrtc_bridge._build_stt/_build_tts) and the hand-rolled Flux TTS adapter
(deepgram_tts.py) are exercised with fakes. The fail-closed resolver and the
keyterm normalizer are tested directly via config._resolve_voice_provider /
config._normalize_keyterms (the real functions config.py uses at import).
"""
import asyncio
import types

import pytest

import config
import deepgram_tts
import webrtc_bridge


class TestProviderResolution:
    def test_local_stays_local(self):
        assert config._resolve_voice_provider("local", "somekey") == "local"

    def test_deepgram_with_key_resolves_to_deepgram(self):
        assert config._resolve_voice_provider("deepgram", "a-real-key") == "deepgram"

    def test_deepgram_without_key_falls_back_to_local(self):
        assert config._resolve_voice_provider("deepgram", "") == "local"
        assert config._resolve_voice_provider("deepgram", None) == "local"

    def test_deepgram_key_whitespace_only_is_not_enough(self):
        assert config._resolve_voice_provider("deepgram", "   ") == "local"
        assert config._resolve_voice_provider("deepgram", "\t\n") == "local"

    def test_request_is_case_insensitive(self):
        # config lowercases the provider; the resolver must too.
        assert config._resolve_voice_provider("Deepgram", "k") == "deepgram"
        assert config._resolve_voice_provider("DEEPGRAM", "  k  ") == "deepgram"

    def test_live_stt_flag_used_the_resolver(self):
        assert config.STT_PROVIDER == config._resolve_voice_provider(
            config.VOICE_STT_PROVIDER, config.DEEPGRAM_API_KEY
        )

    def test_live_tts_flag_used_the_resolver(self):
        assert config.TTS_PROVIDER == config._resolve_voice_provider(
            config.VOICE_TTS_PROVIDER, config.DEEPGRAM_API_KEY
        )

    def test_example_config_ships_local_defaults(self):
        # The shipped template must keep the local engine as the default.
        import os

        import yaml

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "self_config.example.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        voice = cfg.get("voice", {})
        assert voice.get("stt_provider") == "local"
        assert voice.get("tts_provider") == "local"
        assert voice.get("deepgram_stt_model", "").startswith("flux")
        assert voice.get("deepgram_tts_model", "").startswith("flux-")


class TestKeytermNormalization:
    def test_none_defaults_to_aster(self):
        assert config._normalize_keyterms(None) == ["Aster"]

    def test_scalar_string_becomes_single_item(self):
        assert config._normalize_keyterms("Aster") == ["Aster"]

    def test_list_is_cleaned(self):
        assert config._normalize_keyterms(["Aster", "", "  ", "Jarvis"]) == ["Aster", "Jarvis"]

    def test_non_list_non_string_defaults(self):
        assert config._normalize_keyterms(123) == ["Aster"]
        assert config._normalize_keyterms({"a": 1}) == ["Aster"]

    def test_blank_string_yields_empty_not_crash(self):
        assert config._normalize_keyterms("   ") == []

    def test_live_keyterms_are_a_clean_list(self):
        assert isinstance(config.DEEPGRAM_STT_KEYTERMS, list)
        assert all(isinstance(k, str) and k.strip() for k in config.DEEPGRAM_STT_KEYTERMS)


class TestBuildSTT:
    def test_local_path_uses_whisper_and_vad_adapter(self, monkeypatch):
        monkeypatch.setattr(config, "STT_PROVIDER", "local")
        sentinel_base = object()
        fake_vad = object()
        adapter = object()
        monkeypatch.setattr(webrtc_bridge, "LocalWhisperSTT", lambda: sentinel_base)
        monkeypatch.setattr(
            webrtc_bridge.silero, "VAD", types.SimpleNamespace(load=lambda: fake_vad)
        )
        monkeypatch.setattr(
            webrtc_bridge.stt, "StreamAdapter", lambda *, stt, vad: adapter
        )

        base, vad, streaming = webrtc_bridge._build_stt()

        assert base is sentinel_base
        assert vad is fake_vad
        assert streaming is adapter

    def test_deepgram_path_streams_directly_without_vad(self, monkeypatch):
        from livekit.plugins import deepgram

        monkeypatch.setattr(config, "STT_PROVIDER", "deepgram")
        monkeypatch.setattr(config, "DEEPGRAM_API_KEY", "test-key")
        monkeypatch.setattr(config, "DEEPGRAM_STT_MODEL", "flux-general-en")
        monkeypatch.setattr(config, "DEEPGRAM_STT_KEYTERMS", ["Aster"])

        base, vad, streaming = webrtc_bridge._build_stt()

        assert isinstance(base, deepgram.STTv2)
        assert base.model == "flux-general-en"
        assert vad is None            # Flux needs no local VAD
        assert streaming is base      # native streaming — no StreamAdapter

    def test_deepgram_requested_but_plugin_missing_falls_back_local(self, monkeypatch):
        monkeypatch.setattr(config, "STT_PROVIDER", "deepgram")
        monkeypatch.setattr(webrtc_bridge, "deepgram", None)
        sentinel_base = object()
        fake_vad = object()
        adapter = object()
        monkeypatch.setattr(webrtc_bridge, "LocalWhisperSTT", lambda: sentinel_base)
        monkeypatch.setattr(
            webrtc_bridge.silero, "VAD", types.SimpleNamespace(load=lambda: fake_vad)
        )
        monkeypatch.setattr(webrtc_bridge.stt, "StreamAdapter", lambda *, stt, vad: adapter)

        base, vad, streaming = webrtc_bridge._build_stt()

        assert base is sentinel_base and vad is fake_vad and streaming is adapter


class TestBuildTTS:
    def test_local_path_uses_kokoro(self, monkeypatch):
        monkeypatch.setattr(config, "TTS_PROVIDER", "local")
        sentinel = object()
        monkeypatch.setattr(webrtc_bridge, "LocalKokoroTTS", lambda: sentinel)
        assert webrtc_bridge._build_tts() is sentinel

    def test_deepgram_path_uses_flux_tts(self, monkeypatch):
        monkeypatch.setattr(config, "TTS_PROVIDER", "deepgram")
        monkeypatch.setattr(config, "DEEPGRAM_API_KEY", "test-key")
        monkeypatch.setattr(config, "DEEPGRAM_TTS_MODEL", "flux-colin-en")

        tts = webrtc_bridge._build_tts()

        assert isinstance(tts, deepgram_tts.DeepgramFluxTTS)
        assert tts.model == "flux-colin-en"
        assert tts.sample_rate == 24000
        assert tts.num_channels == 1


# ── Flux TTS adapter (deepgram_tts.py) ───────────────────────────────────────

class FakeEmitter:
    def __init__(self):
        self.inits = []
        self.pushes = []
        self.flushed = False

    def initialize(self, **kwargs):
        self.inits.append(kwargs)

    def push(self, data: bytes):
        self.pushes.append(data)

    def flush(self):
        self.flushed = True


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, n):
        for c in self._chunks:
            yield c


class _FakeResp:
    def __init__(self, status=200, chunks=(), body=""):
        self.status = status
        self.content = _FakeContent(chunks)
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.closed = False
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._resp

    async def close(self):
        self.closed = True


class TestDeepgramFluxTTS:
    def test_requires_api_key(self):
        with pytest.raises(ValueError):
            deepgram_tts.DeepgramFluxTTS(model="flux-colin-en", api_key="")

    def test_request_url_carries_model_encoding_and_rate(self):
        t = deepgram_tts.DeepgramFluxTTS(model="flux-colin-en", api_key="k")
        url = t._request_url()
        assert "model=flux-colin-en" in url
        assert "encoding=linear16" in url
        assert "container=none" in url
        assert "sample_rate=24000" in url

    def test_provider_is_deepgram(self):
        t = deepgram_tts.DeepgramFluxTTS(model="flux-colin-en", api_key="k")
        assert t.provider == "Deepgram"

    def test_synthesize_streams_pcm_end_to_end(self):
        from livekit.agents import tts

        resp = _FakeResp(chunks=[b"\x01\x02", b"\x03\x04"])
        sess = _FakeSession(resp)
        t = deepgram_tts.DeepgramFluxTTS(
            model="flux-colin-en", api_key="k", http_session=sess
        )

        async def _collect() -> bytes:
            stream = t.synthesize("hello there")  # must be built under the loop
            assert isinstance(stream, tts.ChunkedStream)
            out = bytearray()
            async for ev in stream:
                out += ev.frame.data.tobytes()
            return bytes(out)

        data = asyncio.run(_collect())

        # The AudioEmitter preserves the pushed PCM and zero-pads to its frame
        # boundary, so the streamed audio starts with exactly what we pushed.
        assert data.startswith(b"\x01\x02\x03\x04")
        assert set(data[4:]) <= {0}
        url, kwargs = sess.calls[0]
        assert "model=flux-colin-en" in url
        assert kwargs["json"] == {"text": "hello there"}
        assert kwargs["headers"]["Authorization"] == "Token k"

    def test_error_is_reported_and_raised_and_always_flushes(self, monkeypatch):
        import tools.diagnostics as diag

        reported = []
        monkeypatch.setattr(diag, "send_error", lambda ctx, exc: reported.append((ctx, exc)))

        sess = _FakeSession(_FakeResp(status=500, body="boom"))
        t = deepgram_tts.DeepgramFluxTTS(
            model="flux-colin-en", api_key="k", http_session=sess
        )
        emitter = FakeEmitter()

        with pytest.raises(RuntimeError):
            asyncio.run(_direct_stream(t, "hi")._run(emitter))

        assert emitter.pushes == []
        assert emitter.flushed  # flush-always contract preserved
        assert reported and reported[0][0] == "Deepgram Flux TTS synthesis (call)"

    def test_real_stream_propagates_failure_without_retry(self, monkeypatch):
        # Through the REAL ChunkedStream base (not the object.__new__ bypass):
        # a failure must surface and must NOT be retried into a duplicate-POST
        # storm (a propagated non-APIError takes one attempt).
        import tools.diagnostics as diag

        monkeypatch.setattr(diag, "send_error", lambda *a, **k: None)
        sess = _FakeSession(_FakeResp(status=500, body="boom"))
        t = deepgram_tts.DeepgramFluxTTS(
            model="flux-colin-en", api_key="k", http_session=sess
        )

        async def _run_stream():
            stream = t.synthesize("hi")  # built under the loop
            async for _ in stream:
                pass

        async def _run_stream():
            stream = t.synthesize("hi")  # built under the loop
            async for _ in stream:
                pass

        with pytest.raises(RuntimeError, match="HTTP 500"):
            asyncio.run(_run_stream())

        assert len(sess.calls) == 1

    def test_empty_body_pushes_nothing_and_flushes(self):
        sess = _FakeSession(_FakeResp(chunks=[]))
        t = deepgram_tts.DeepgramFluxTTS(
            model="flux-colin-en", api_key="k", http_session=sess
        )
        emitter = FakeEmitter()
        asyncio.run(_direct_stream(t, "hi")._run(emitter))
        assert emitter.pushes == []
        assert emitter.flushed

    def test_session_lifecycle_create_and_close(self, monkeypatch):
        created = _FakeSession(_FakeResp(chunks=[]))
        monkeypatch.setattr(
            deepgram_tts.aiohttp, "ClientSession", lambda: created
        )
        t = deepgram_tts.DeepgramFluxTTS(model="flux-colin-en", api_key="k")
        assert t._session is None

        async def _use():
            return t._ensure_session()

        assert asyncio.run(_use()) is created
        asyncio.run(t.aclose())
        assert t._session is None
        assert created.closed is True


class _FakeSttp:
    def __init__(self):
        self.closed = 0

    async def aclose(self):
        self.closed += 1


class _FakeAudioSource:
    def __init__(self):
        self.cleared = 0
        self.closed = 0

    def clear_queue(self):
        self.cleared += 1

    async def aclose(self):
        self.closed += 1


class _FakeRoom:
    def isconnected(self):
        return False


def _bridge_for_aclose(*, stt_is_local: bool):
    """A ManualWebRTCBridge stub with only the attrs aclose() touches, so the
    teardown (double-close guard + release gating) can be tested without a
    JobContext. Returns (bridge, base_stt, streaming_stt) — aclose nulls the
    bridge's own references, so keep external ones to assert on."""
    b = object.__new__(webrtc_bridge.ManualWebRTCBridge)
    b._closed = False
    b._stop_event = asyncio.Event()
    b._response_lock = asyncio.Lock()
    b._response_epoch = 0
    b._audio_lock = asyncio.Lock()
    b._tts_task = None
    b._brain_task = None
    b._response_task = None
    b._track_tasks = {}
    b._track_owner = {}
    b._background_tasks = set()
    b._audio_track_pub = None
    b._room = _FakeRoom()
    b._audio_source = _FakeAudioSource()
    if stt_is_local:
        base, streaming = _FakeSttp(), _FakeSttp()  # StreamAdapter != base
    else:
        base = _FakeSttp()
        streaming = base  # cloud: _streaming_stt IS _base_stt
    b._base_stt = base
    b._streaming_stt = streaming
    b._tts = deepgram_tts.DeepgramFluxTTS(model="flux-colin-en", api_key="k")
    b._vad = None
    b._wake_detector = None
    b._stt_is_local = stt_is_local
    return b, base, streaming


class TestBridgeAclose:
    def test_cloud_path_closes_shared_stt_once_and_skips_whisper_release(self, monkeypatch):
        calls = []
        monkeypatch.setattr(webrtc_bridge, "release_whisper_model", lambda: calls.append(1))
        b, base, streaming = _bridge_for_aclose(stt_is_local=False)

        asyncio.run(b.aclose())

        assert base is streaming
        assert base.closed == 1                 # closed exactly once (no double-close)
        assert calls == []                      # no local Whisper to release

    def test_local_path_closes_both_and_releases_whisper(self, monkeypatch):
        calls = []
        monkeypatch.setattr(webrtc_bridge, "release_whisper_model", lambda: calls.append(1))
        b, base, streaming = _bridge_for_aclose(stt_is_local=True)

        asyncio.run(b.aclose())

        assert base is not streaming
        assert streaming.closed == 1
        assert base.closed == 1
        assert calls == [1]                     # Whisper unloaded on the local path


def _direct_stream(tts_obj, text):
    """Build a FluxChunkedStream without the base __init__ (which spawns tasks
    and needs a running loop) so _run can be driven directly with a fake
    emitter. Same technique as tests/test_tts_streaming.py."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    stream = object.__new__(deepgram_tts.FluxChunkedStream)
    stream._tts = tts_obj
    stream._input_text = text
    stream._conn_options = DEFAULT_API_CONNECT_OPTIONS
    return stream


class _FakeWebSocket:
    def __init__(self, incoming=()):
        self._incoming = list(incoming)
        self.accepted = False
        self.closed = False
        self.sent_text = []
        self.sent_bytes = []

    async def accept(self):
        self.accepted = True

    async def send_text(self, text: str):
        self.sent_text.append(text)

    async def send_bytes(self, data: bytes):
        self.sent_bytes.append(data)

    async def receive(self):
        if self._incoming:
            item = self._incoming.pop(0)
            if callable(item):
                return await item()
            return item
        return {"type": "websocket.disconnect"}

    async def close(self):
        self.closed = True


class _FakeRecognizeStream:
    def __init__(self, events=()):
        self._events = list(events)
        self.pushed_frames = []
        self.input_ended = False
        self.closed = False

    def push_frame(self, frame):
        self.pushed_frames.append(frame)

    def end_input(self):
        self.input_ended = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        await asyncio.sleep(0.05)
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


class _FakeStreamingSTT(_FakeSttp):
    def __init__(self, rec_stream):
        super().__init__()
        self._rec_stream = rec_stream

    def stream(self, **kwargs):
        return self._rec_stream


class _FakeSynthesizedFrame:
    def __init__(self, pcm: bytes, samples: int):
        self.frame = types.SimpleNamespace(
            data=memoryview(pcm),
            samples_per_channel=samples,
        )


class _FakeSynthStream:
    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


class _FakeLocalTTS(_FakeSttp):
    sample_rate = 24000
    num_channels = 1

    def __init__(self, pcm_chunks=(b"\x10\x00" * 24,)):
        super().__init__()
        self._pcm_chunks = pcm_chunks

    def synthesize(self, text: str, **kwargs):
        frames = [
            _FakeSynthesizedFrame(c, len(c) // 2)
            for c in self._pcm_chunks
        ]
        return _FakeSynthStream(frames)


class TestOverhaulDevmodeSmallEnAndLocalWs:
    def test_livekit_devmode_is_off(self, monkeypatch):
        """Step 1: server.run must be called with devmode=False so it never forks."""
        called = {}

        async def _fake_run(*, devmode=True):
            called["devmode"] = devmode

        monkeypatch.setattr(webrtc_bridge, "LIVEKIT_CONFIGURED", True)
        monkeypatch.setattr(webrtc_bridge.server, "run", _fake_run)
        webrtc_bridge.start_agent_background()
        assert called == {"devmode": False}

    def test_whisper_model_defaults_to_small_en(self):
        """Step 3: Faster-Whisper model defaults to small.en in config and local_stt."""
        import local_stt

        assert config.WHISPER_MODEL == "small.en"
        assert local_stt.WHISPER_MODEL_NAME == "small.en"
        stt_stub = object.__new__(local_stt.LocalWhisperSTT)
        assert stt_stub.model == "small.en"

    def test_livekit_benched_by_default_in_config(self):
        """Step 2: LiveKit daemon is benched (disabled) by default; transport is local_ws."""
        assert config.VOICE_TRANSPORT == "local_ws"
        assert config.WEBRTC_BRIDGE_ENABLED is False

    def test_local_ws_bridge_handshake_audio_mute_and_teardown(self, monkeypatch):
        """Step 2: LocalWebSocketVoiceBridge handles binary 16kHz PCM, mute control, and VRAM teardown."""
        import json

        rec = _FakeRecognizeStream()
        base_stt = _FakeSttp()
        streaming_stt = _FakeStreamingSTT(rec)
        fake_tts = _FakeLocalTTS()
        released = []

        monkeypatch.setattr(webrtc_bridge, "_build_stt", lambda: (base_stt, None, streaming_stt))
        monkeypatch.setattr(webrtc_bridge, "_build_tts", lambda: fake_tts)
        monkeypatch.setattr(webrtc_bridge, "release_whisper_model", lambda: released.append(1))
        monkeypatch.setattr(config, "STT_PROVIDER", "local")
        monkeypatch.setattr(config, "WAKE_WORD_ENABLED", False)

        ws = _FakeWebSocket(
            incoming=[
                {"type": "websocket.receive", "bytes": b"\x01\x00" * 160},
                {"type": "websocket.receive", "text": json.dumps({"type": "mute", "muted": True})},
                {"type": "websocket.receive", "bytes": b"\x02\x00" * 160},  # dropped while muted
                {"type": "websocket.receive", "text": json.dumps({"type": "mute", "muted": False})},
                {"type": "websocket.receive", "bytes": b"\x03\x00" * 160},
                {"type": "websocket.receive", "text": json.dumps({"type": "hangup"})},
            ]
        )

        asyncio.run(webrtc_bridge.handle_voice_websocket(ws))

        assert ws.accepted is True
        assert ws.closed is True
        assert len(rec.pushed_frames) == 2
        assert rec.pushed_frames[0].samples_per_channel == 160
        assert rec.pushed_frames[1].samples_per_channel == 160
        ready_msg = json.loads(ws.sent_text[0])
        assert ready_msg["type"] == "ready"
        assert ready_msg["sample_rate_in"] == 16000
        assert ready_msg["sample_rate_out"] == 24000
        assert released == [1]
        assert webrtc_bridge.call_is_active() is False

    def test_local_ws_bridge_speak_and_barge_in_clear_audio(self, monkeypatch):
        """_speak streams binary PCM frames and barge-in sends clear_audio JSON."""
        import json

        rec = _FakeRecognizeStream()
        base_stt = _FakeSttp()
        streaming_stt = _FakeStreamingSTT(rec)
        fake_tts = _FakeLocalTTS(pcm_chunks=[b"\x05\x00" * 48, b"\x06\x00" * 48])

        monkeypatch.setattr(webrtc_bridge, "_build_stt", lambda: (base_stt, None, streaming_stt))
        monkeypatch.setattr(webrtc_bridge, "_build_tts", lambda: fake_tts)
        monkeypatch.setattr(webrtc_bridge, "release_whisper_model", lambda: None)
        monkeypatch.setattr(config, "STT_PROVIDER", "local")
        monkeypatch.setattr(config, "WAKE_WORD_ENABLED", False)

        ws = _FakeWebSocket()
        bridge = webrtc_bridge.LocalWebSocketVoiceBridge(ws)

        async def _exercise():
            webrtc_bridge._active_bridge = bridge
            assert webrtc_bridge.call_is_active() is True
            await bridge._speak("Hello Sir.", bridge._response_epoch)
            await bridge._cancel_active_response(reason="barge-in", clear_audio=True)
            await asyncio.sleep(0.01)
            await bridge.aclose()
            webrtc_bridge._active_bridge = None

        asyncio.run(_exercise())

        assert len(ws.sent_bytes) == 2
        types_sent = [json.loads(t)["type"] for t in ws.sent_text]
        assert "speaking" in types_sent
        assert "clear_audio" in types_sent
        assert webrtc_bridge.call_is_active() is False

    def test_local_ws_bridge_with_deepgram_stt_session_lifecycle(self, monkeypatch):
        """Regression test: Deepgram STT outside LiveKit worker API must not crash on missing http session."""
        import json
        from livekit.plugins import deepgram

        monkeypatch.setattr(config, "STT_PROVIDER", "deepgram")
        monkeypatch.setattr(config, "TTS_PROVIDER", "deepgram")
        monkeypatch.setattr(config, "DEEPGRAM_API_KEY", "mock-deepgram-key")
        monkeypatch.setattr(config, "WAKE_WORD_ENABLED", False)

        ws = _FakeWebSocket(
            incoming=[
                {"type": "websocket.receive", "text": json.dumps({"type": "hangup"})},
            ]
        )

        async def _test():
            bridge = webrtc_bridge.LocalWebSocketVoiceBridge(ws)
            assert getattr(bridge, "_http_session", None) is not None
            # Deepgram STT stream should not raise RuntimeError for missing http_context
            if isinstance(bridge._streaming_stt, deepgram.STTv2):
                stream = bridge._streaming_stt.stream()
                assert stream is not None
                await stream.aclose()
            await bridge.aclose()
            assert bridge._http_session.closed is True

        asyncio.run(_test())
