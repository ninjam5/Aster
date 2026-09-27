/**
 * AsterMoodProvider — single source of truth for the character's mood.
 *
 * One WebSocket to face_server's /api/logs (same stream the Activity Log and
 * the aster-face reactor use) feeds every character on screen:
 *   - `{type:"sentiment"}` events map 1:1 onto the top sprite row
 *     (calm / working / alert / music / success — see tools/sentiment.py).
 *   - `{type:"wake", awake:true}` → brief "alert" (heard the wake word).
 *   - error-looking terminal lines → brief "confused".
 * Local signals fill in the rest: chat streaming → "thinking", socket down →
 * "pouty", late-night calm → "tired".
 */
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ASTER_WS_BASE } from "../api/config";
import { useOptionalChatSession } from "../state/useChatSession";
import type { AsterMood } from "./sprites";

const TRANSIENT_MS = 4000;
const BASE_MOODS: AsterMood[] = ["calm", "working", "alert", "music"];
const ALL_MOODS: AsterMood[] = [
  "calm", "working", "alert", "music", "success",
  "pouty", "jumping", "thinking", "confused", "tired",
];

// Dev/preview affordance: ?asterMood=<mood> pins the character to one mood so
// entrances and idle loops can be inspected without driving real events.
function moodOverride(): AsterMood | null {
  if (typeof window === "undefined") return null;
  const value = new URLSearchParams(window.location.search).get("asterMood");
  return ALL_MOODS.includes(value as AsterMood) ? (value as AsterMood) : null;
}

export interface MoodInputs {
  offline: boolean;
  streaming: boolean;
  transient: AsterMood | null;
  base: AsterMood;
  hour: number;
}

// Precedence: offline > streaming > transient celebration/reaction > base
// sentiment, with idle calm turning sleepy during late-night hours.
// eslint-disable-next-line react-refresh/only-export-components
export function resolveAsterMood({ offline, streaming, transient, base, hour }: MoodInputs): AsterMood {
  if (offline) return "pouty";
  if (streaming) return "thinking";
  if (transient) return transient;
  if (base === "calm" && (hour >= 23 || hour < 6)) return "tired";
  return base;
}

const AsterMoodContext = createContext<AsterMood | null>(null);

export function AsterMoodProvider({ children }: { children: ReactNode }) {
  const chat = useOptionalChatSession();
  const streaming = chat?.streaming ?? false;

  const [base, setBase] = useState<AsterMood>("calm");
  const [transient, setTransient] = useState<AsterMood | null>(null);
  const [offline, setOffline] = useState(false);
  const [, setMinute] = useState(0); // re-render tick so the tired window engages while idle

  const revertTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (typeof WebSocket === "undefined") return;

    let alive = true;
    let socket: WebSocket | null = null;
    let retry: number | undefined;

    const flash = (mood: AsterMood) => {
      if (revertTimer.current !== undefined) window.clearTimeout(revertTimer.current);
      setTransient(mood);
      revertTimer.current = window.setTimeout(() => {
        if (alive) setTransient(null);
      }, TRANSIENT_MS);
    };

    const connect = () => {
      socket = new WebSocket(`${ASTER_WS_BASE}/api/logs`);

      socket.onopen = () => {
        if (alive) setOffline(false);
      };

      socket.onmessage = (ev) => {
        if (!alive) return;
        try {
          const event = JSON.parse(ev.data as string) as {
            type?: string;
            sentiment?: string;
            awake?: boolean;
            data?: string;
          };

          if (event.type === "sentiment") {
            if (event.sentiment === "success") {
              // two celebration sprites — vary which one plays
              flash(Math.random() < 0.5 ? "success" : "jumping");
            } else if (BASE_MOODS.includes(event.sentiment as AsterMood)) {
              setBase(event.sentiment as AsterMood);
              setTransient(null);
            }
          } else if (event.type === "wake" && event.awake) {
            flash("alert");
          } else if (event.type === "terminal" && event.data && /error|exception|traceback/i.test(event.data)) {
            flash("confused");
          }
        } catch {
          /* ignore non-JSON frames */
        }
      };

      socket.onclose = () => {
        if (!alive) return;
        setOffline(true);
        retry = window.setTimeout(connect, 2000);
      };

      socket.onerror = () => socket?.close();
    };

    connect();
    const minuteTick = window.setInterval(() => {
      if (alive) setMinute((m) => m + 1);
    }, 60_000);

    return () => {
      alive = false;
      if (retry !== undefined) window.clearTimeout(retry);
      if (revertTimer.current !== undefined) window.clearTimeout(revertTimer.current);
      window.clearInterval(minuteTick);
      socket?.close();
    };
  }, []);

  const mood =
    moodOverride() ??
    resolveAsterMood({
      offline,
      streaming,
      transient,
      base,
      hour: new Date().getHours(),
    });

  return <AsterMoodContext.Provider value={mood}>{children}</AsterMoodContext.Provider>;
}

/** Current character mood; safe outside the provider (defaults to "calm"). */
// eslint-disable-next-line react-refresh/only-export-components
export function useAsterMood(): AsterMood {
  return useContext(AsterMoodContext) ?? "calm";
}
