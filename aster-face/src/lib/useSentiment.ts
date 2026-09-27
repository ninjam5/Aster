import { useEffect, useState } from "react";
import { LOGS_WS_URL } from "./api";
import type { Sentiment } from "../components/AsterFace";

const TRANSIENT: Sentiment[] = ["success", "music"];
const REVERT_MS = 4000;
const VALID: Sentiment[] = ["calm", "working", "alert", "music", "success"];

/**
 * Tracks Aster's conversational mood by listening for
 * `{ type: "sentiment", sentiment }` events on the face-server logs WebSocket.
 * `success` and `music` are transient — they ease back to `calm` after ~4s.
 * Defaults to `calm`.
 */
export function useSentiment(): Sentiment {
  const [sentiment, setSentiment] = useState<Sentiment>("calm");

  useEffect(() => {
    let alive = true;
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let revert: number | undefined;

    const connect = () => {
      socket = new WebSocket(LOGS_WS_URL);

      socket.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data) as {
            type?: string;
            sentiment?: unknown;
          };
          if (payload.type !== "sentiment" || !alive) return;

          const next = VALID.includes(payload.sentiment as Sentiment)
            ? (payload.sentiment as Sentiment)
            : "calm";

          if (revert !== undefined) window.clearTimeout(revert);
          setSentiment(next);
          if (TRANSIENT.includes(next)) {
            revert = window.setTimeout(() => {
              if (alive) setSentiment("calm");
            }, REVERT_MS);
          }
        } catch {
          /* ignore non-JSON frames */
        }
      };

      socket.onclose = () => {
        if (!alive) return;
        setSentiment("calm");
        retry = window.setTimeout(connect, 2000);
      };

      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      alive = false;
      if (retry !== undefined) window.clearTimeout(retry);
      if (revert !== undefined) window.clearTimeout(revert);
      socket?.close();
    };
  }, []);

  return sentiment;
}
