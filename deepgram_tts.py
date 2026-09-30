"""Deepgram Flux TTS adapter for the LiveKit call path.

Drop-in replacement for ``LocalKokoroTTS`` when ``voice.tts_provider: deepgram``.
Speaks the Deepgram Flux TTS endpoint (``POST /v2/speak``, ``linear16``). The
bridge always has the full reply text in hand when ``_speak()`` runs, so the
one-shot ``synthesize()`` path is all it needs — no token streaming required.

Why this exists instead of the plugin's TTS class: the installed
``livekit-plugins-deepgram`` (1.5.7, pinned to ``livekit-agents`` 1.5.7) only
ships the Aura voices on ``/v1/speak``. Flux TTS — which carries the British
"Colin" voice — lives on ``/v2/speak`` and only arrived in a later plugin
release that would drag ``livekit-agents`` from 1.5.x to 1.8.x, risking the
whole agent-server API the bridge is built on. Rather than take that upgrade,
this adapter implements the small REST surface directly.

Protocol: https://developers.deepgram.com/docs/flux-tts/quickstart
"""
from __future__ import annotations

import urllib.parse

import aiohttp
from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

BASE_URL = "https://api.deepgram.com/v2/speak"
NUM_CHANNELS = 1
_ENCODING = "linear16"
_MIME_TYPE = "audio/pcm"


def _report_synthesis_error(text: str, e: Exception) -> None:
    import traceback

    msg = f"[Aster TTS] Deepgram Flux synthesis error for {text[:40]!r}: {e}\n{traceback.format_exc()}"
    print(msg)
    try:
        from tools.diagnostics import send_error

        send_error("Deepgram Flux TTS synthesis (call)", e)
    except Exception:
        pass


class DeepgramFluxTTS(tts.TTS):
    """Deepgram Flux TTS (/v2/speak) as a LiveKit TTS for the call bridge."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        sample_rate: int = 24000,
        base_url: str = BASE_URL,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        if not api_key:
            raise ValueError("Deepgram API key required for Flux TTS.")
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._session = http_session
        print(f"[Aster Voice] Using Deepgram Flux TTS voice: {model}")

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "Deepgram"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _request_url(self) -> str:
        params = {
            "model": self._model,
            "encoding": _ENCODING,
            "container": "none",
            "sample_rate": self.sample_rate,
            "mip_opt_out": "false",
        }
        return f"{self._base_url}?{urllib.parse.urlencode(params)}"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "FluxChunkedStream":
        return FluxChunkedStream(tts_instance=self, text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


class FluxChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance: DeepgramFluxTTS, text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts_instance, input_text=text, conn_options=conn_options)
        self._tts = tts_instance

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=NUM_CHANNELS,
            mime_type=_MIME_TYPE,
        )
        try:
            await self._synthesize_to(output_emitter)
        except Exception as e:
            # Report once, then PROPAGATE. Swallowing here would masquerade a
            # failure as silence (the base marks any attempt with >=1 pushed
            # frame as success, and would otherwise turn a total failure into a
            # pointless retry storm). A non-APIError propagates without retry;
            # the bridge logs it and the call survives to the next turn.
            _report_synthesis_error(self.input_text, e)
            raise
        finally:
            # Flush whatever audio was produced (a no-op when there is none).
            output_emitter.flush()

    async def _synthesize_to(self, output_emitter: tts.AudioEmitter) -> None:
        session = self._tts._ensure_session()
        # Bound the WHOLE request (connect + body), not just connect: a server
        # that accepts then stalls must not block the response pipeline while it
        # holds the bridge's audio lock.
        timeout = aiohttp.ClientTimeout(
            total=self._conn_options.timeout,
            sock_connect=self._conn_options.timeout,
        )
        async with session.post(
            self._tts._request_url(),
            headers={
                "Authorization": f"Token {self._tts._api_key}",
                "Content-Type": "application/json",
            },
            json={"text": self.input_text},
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Deepgram Flux TTS HTTP {resp.status}: {body[:200]}")
            async for chunk in resp.content.iter_chunked(8192):
                if chunk:
                    output_emitter.push(chunk)
