import { useEffect, useRef } from "react";
import { LOGS_WS_URL } from "./api";

type Listener = (payload: Record<string, unknown>) => void;

// One shared WebSocket for all consumers in the same renderer, keyed by URL.
// Each call to useLogsSocket subscribes to a specific event type (or "*" for all).
// The socket is opened on first subscriber and closed when the last one unmounts.

const _sockets = new Map<
  string,
  {
    ws: WebSocket | null;
    listeners: Map<string, Set<Listener>>;
    retryTimer: ReturnType<typeof setTimeout> | null;
    retryCount: number;
    refCount: number;
  }
>();

function _getState(url: string) {
  if (!_sockets.has(url)) {
    _sockets.set(url, {
      ws: null,
      listeners: new Map(),
      retryTimer: null,
      retryCount: 0,
      refCount: 0,
    });
  }
  return _sockets.get(url)!;
}

function _connect(url: string) {
  const state = _getState(url);
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;

  const ws = new WebSocket(url);
  state.ws = ws;

  ws.onopen = () => {
    state.retryCount = 0;
  };

  ws.onmessage = (ev) => {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(ev.data) as Record<string, unknown>;
    } catch {
      return;
    }
    const type = typeof payload.type === "string" ? payload.type : "*";
    // Notify exact-type listeners
    state.listeners.get(type)?.forEach((cb) => cb(payload));
    // Notify wildcard listeners
    if (type !== "*") state.listeners.get("*")?.forEach((cb) => cb(payload));
  };

  ws.onclose = () => {
    state.ws = null;
    if (state.refCount <= 0) return;
    // Exponential backoff with jitter: base 1s, cap 30s
    const delay = Math.min(1000 * Math.pow(1.6, state.retryCount), 30000);
    const jitter = Math.random() * 500;
    state.retryCount++;
    state.retryTimer = setTimeout(() => _connect(url), delay + jitter);
  };

  ws.onerror = () => ws.close();
}

function _subscribe(url: string, type: string, cb: Listener): () => void {
  const state = _getState(url);
  state.refCount++;

  if (!state.listeners.has(type)) state.listeners.set(type, new Set());
  state.listeners.get(type)!.add(cb);

  _connect(url);

  return () => {
    state.listeners.get(type)?.delete(cb);
    state.refCount--;
    if (state.refCount <= 0) {
      if (state.retryTimer !== null) {
        clearTimeout(state.retryTimer);
        state.retryTimer = null;
      }
      state.ws?.close();
      state.ws = null;
      state.retryCount = 0;
    }
  };
}

/**
 * Subscribe to a single event type on the shared /api/logs WebSocket.
 * All consumers in the same renderer share one connection.
 * `type` is the `payload.type` string to match, or `"*"` for all messages.
 */
export function useLogsSocket(
  type: string,
  cb: (payload: Record<string, unknown>) => void,
): void {
  const cbRef = useRef(cb);
  cbRef.current = cb;

  useEffect(() => {
    const stable: Listener = (p) => cbRef.current(p);
    return _subscribe(LOGS_WS_URL, type, stable);
  }, [type]);
}
