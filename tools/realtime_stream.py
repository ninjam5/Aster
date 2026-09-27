import asyncio
import queue
import re
import time
from typing import Any

from fastapi import WebSocket


_MAX_QUEUE_SIZE = 500
_event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
_stream_clients: set[WebSocket] = set()


def _clean_text(value: Any, max_len: int = 280) -> str:
    text = str(value if value is not None else "")
    text = re.sub(r"\[NATIVE_[A-Z_]+:.*?\]", "[NATIVE_PAYLOAD]", text, flags=re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _normalize_event(payload: dict[str, Any]) -> dict[str, Any]:
    event = dict(payload or {})
    event.setdefault("type", "terminal")
    event.setdefault("ts", int(time.time() * 1000))
    return event


def publish_event(payload: dict[str, Any]) -> None:
    event = _normalize_event(payload)
    try:
        _event_queue.put_nowait(event)
    except queue.Full:
        try:
            _event_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            _event_queue.put_nowait(event)
        except queue.Full:
            pass


def publish_terminal(text: Any) -> None:
    publish_event({"type": "terminal", "data": _clean_text(text)})


def publish_discord(user: str, msg: Any) -> None:
    publish_event(
        {
            "type": "discord",
            "user": str(user or "Discord").strip() or "Discord",
            "msg": _clean_text(msg),
        }
    )


def publish_telemetry(data: dict[str, Any]) -> None:
    publish_event({"type": "telemetry", "data": data})


def publish_wake(awake: bool) -> None:
    publish_event({"type": "wake", "awake": bool(awake)})


def publish_sentiment(sentiment: str) -> None:
    publish_event({"type": "sentiment", "sentiment": str(sentiment or "calm")})


async def register_stream_client(websocket: WebSocket) -> None:
    await websocket.accept()
    _stream_clients.add(websocket)


async def unregister_stream_client(websocket: WebSocket) -> None:
    _stream_clients.discard(websocket)


async def stream_dispatch_loop() -> None:
    while True:
        event = await asyncio.to_thread(_event_queue.get)

        if not _stream_clients:
            continue

        disconnected: list[WebSocket] = []
        for websocket in tuple(_stream_clients):
            try:
                await websocket.send_json(event)
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            _stream_clients.discard(websocket)