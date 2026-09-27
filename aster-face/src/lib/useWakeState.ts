import { useEffect, useState } from "react";
import { LOGS_WS_URL } from "./api";

/**
 * Tracks Aster's wake-word state by listening for `{ type: "wake", awake }`
 * events on the face-server logs WebSocket. Defaults to asleep (false).
 */
export function useWakeState(): boolean {
  const [awake, setAwake] = useState(false);

  useEffect(() => {
    let alive = true;
    let socket: WebSocket | null = null;
    let retry: number | undefined;

    const connect = () => {
      socket = new WebSocket(LOGS_WS_URL);

      socket.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data) as {
            type?: string;
            awake?: unknown;
          };
          if (payload.type === "wake" && alive) {
            setAwake(Boolean(payload.awake));
          }
        } catch {
          /* ignore non-JSON frames */
        }
      };

      socket.onclose = () => {
        if (!alive) return;
        setAwake(false);
        retry = window.setTimeout(connect, 2000);
      };

      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      alive = false;
      if (retry !== undefined) window.clearTimeout(retry);
      socket?.close();
    };
  }, []);

  return awake;
}
