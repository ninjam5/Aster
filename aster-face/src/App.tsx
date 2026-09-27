import { useCallback, useEffect, useRef, useState } from "react";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { Maximize2, Mic, MicOff, Phone, PhoneOff } from "lucide-react";
import AsterFace, {
  type FaceState,
  type Sentiment,
} from "./components/AsterFace";
import CallControls from "./components/CallControls";
import LogConsole from "./components/LogConsole";
import AsterRoom from "./livekit/AsterRoom";
import { fetchToken } from "./lib/api";
import { useWakeState } from "./lib/useWakeState";
import { useSentiment } from "./lib/useSentiment";
import {
  type AsterState,
  type ControlAction,
  currentLabel,
  emitAsterState,
  listenAsterState,
  listenControl,
  restoreMain,
  sendControl,
  watchMinimize,
} from "./lib/tauriWindow";

type CallState = "idle" | "connecting" | "connected";

// ── mini always-on-top circle (shown while the main window is minimized) ──────
function MiniReactor() {
  const [s, setS] = useState<AsterState>({
    level: 0,
    state: "idle",
    sentiment: "calm",
    muted: false,
    callState: "idle",
  });
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let un: UnlistenFn | undefined;
    void listenAsterState(setS).then((fn) => (un = fn));
    return () => un?.();
  }, []);

  // The window is always 200×200 (transparent). The visible shape is the inner
  // div that CSS-animates between a 140px circle and a 200px rounded square.
  return (
    <div className="relative h-full w-full">
      <div
        data-tauri-drag-region=""
        title="Drag to move Aster"
        className="absolute flex cursor-grab flex-col items-center justify-center overflow-hidden border border-cyan-400/30 bg-slate-950/85 active:cursor-grabbing"
        style={{
          borderRadius: expanded ? "18px" : "50%",
          width: expanded ? "200px" : "140px",
          height: expanded ? "200px" : "140px",
          top: expanded ? "0px" : "30px",
          left: expanded ? "0px" : "30px",
          transition:
            "width 0.22s ease-out, height 0.22s ease-out, " +
            "border-radius 0.22s ease-out, top 0.22s ease-out, left 0.22s ease-out",
        }}
        onMouseEnter={() => setExpanded(true)}
        onMouseLeave={() => setExpanded(false)}
      >
        <button
          type="button"
          onClick={() => void restoreMain()}
          title="Restore Aster"
          className="absolute right-2 top-2 z-10 rounded-full bg-slate-800/80 p-1 text-cyan-300 hover:bg-slate-700"
        >
          <Maximize2 size={12} />
        </button>

        <div style={{ pointerEvents: "none" }}>
          <AsterFace
            state={s.state}
            level={s.level}
            sentiment={s.sentiment}
            size={100}
          />
        </div>

        {/* button panel — collapses to zero height when not expanded */}
        <div
          style={{
            maxHeight: expanded ? "84px" : "0px",
            overflow: "hidden",
            transition: "max-height 0.22s ease-out",
          }}
        >
          <div
            className="flex flex-col items-center gap-2 pt-2"
            style={{
              opacity: expanded ? 1 : 0,
              transition: "opacity 0.15s ease",
              pointerEvents: expanded ? "auto" : "none",
            }}
          >
            <button
              type="button"
              onClick={() =>
                sendControl(
                  s.callState === "connected" ? "endCall" : "startCall",
                )
              }
              className="flex items-center gap-1 rounded-full bg-cyan-900/70 px-3 py-1 text-xs text-cyan-300 hover:bg-cyan-800"
            >
              {s.callState === "connected" ? (
                <PhoneOff size={12} />
              ) : (
                <Phone size={12} />
              )}
              {s.callState === "connected" ? "End Call" : "Start Call"}
            </button>
            <button
              type="button"
              onClick={() => sendControl("toggleMute")}
              disabled={s.callState !== "connected"}
              className="flex items-center gap-1 rounded-full bg-slate-800/70 px-3 py-1 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-40"
            >
              {s.muted ? <MicOff size={12} /> : <Mic size={12} />}
              {s.muted ? "Unmute" : "Mute"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── main companion window ─────────────────────────────────────────────────────
function MainWindow() {
  const [callState, setCallState] = useState<CallState>("idle");
  const [token, setToken] = useState<string | null>(null);
  const [serverUrl, setServerUrl] = useState<string>("");
  const [muted, setMuted] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [logHeight, setLogHeight] = useState(176);

  useEffect(() => {
    document.documentElement.classList.add("dark");
  }, []);

  // mirror minimize → mini circle
  useEffect(() => {
    let off: (() => void) | undefined;
    void watchMinimize().then((fn) => (off = fn));
    return () => off?.();
  }, []);

  const awake = useWakeState();
  const sentiment = useSentiment();

  const faceState: FaceState =
    callState === "idle"
      ? "idle"
      : callState === "connecting"
        ? "connecting"
        : awake
          ? "connected"
          : "asleep";

  // mood colour only applies during a connected call — idle/connecting stay blue
  const faceSentiment: Sentiment =
    callState === "connected" ? sentiment : "calm";

  // keep refs for use inside stable callbacks
  const faceStateRef = useRef(faceState);
  faceStateRef.current = faceState;
  const faceSentimentRef = useRef(faceSentiment);
  faceSentimentRef.current = faceSentiment;
  const callStateRef = useRef(callState);
  callStateRef.current = callState;
  const mutedRef = useRef(muted);
  mutedRef.current = muted;

  // broadcast full state to the mini window on every relevant change
  useEffect(() => {
    emitAsterState(
      { level: 0, state: faceState, sentiment: faceSentiment, muted, callState },
      true,
    );
  }, [faceState, faceSentiment, muted, callState]);

  const handleLevel = useCallback((v: number) => {
    setLevel(v);
    emitAsterState({
      level: v,
      state: faceStateRef.current,
      sentiment: faceSentimentRef.current,
      muted: mutedRef.current,
      callState: callStateRef.current,
    });
  }, []);

  const startCall = useCallback(async () => {
    setError(null);
    setCallState("connecting");
    try {
      const { token: t, ws_url } = await fetchToken();
      setToken(t);
      setServerUrl(ws_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach Aster.");
      setCallState("idle");
    }
  }, []);

  const endCall = useCallback(() => {
    setToken(null);
    setServerUrl("");
    setMuted(false);
    setLevel(0);
    setCallState("idle");
  }, []);

  // handle call/mute actions forwarded from the mini window
  useEffect(() => {
    let un: UnlistenFn | undefined;
    void listenControl((action: ControlAction) => {
      if (action === "startCall") void startCall();
      else if (action === "endCall") endCall();
      else if (action === "toggleMute") setMuted((m) => !m);
    }).then((fn) => (un = fn));
    return () => un?.();
  }, [startCall, endCall]);

  // ── log panel resize ────────────────────────────────────────────────────────
  const dragRef = useRef<{ y: number; h: number } | null>(null);
  const onResizeDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragRef.current = { y: e.clientY, h: logHeight };
    const onMove = (ev: PointerEvent) => {
      if (!dragRef.current) return;
      const dh = dragRef.current.y - ev.clientY;
      const next = dragRef.current.h + dh;
      setLogHeight(Math.max(72, Math.min(window.innerHeight - 240, next)));
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const status =
    callState === "idle"
      ? "Aster is offline"
      : callState === "connecting"
        ? "Waking Aster…"
        : !awake
          ? "Asleep — say “Hey Aster”"
          : level > 0.12
            ? "Speaking"
            : "Standing by";

  return (
    <div className="flex h-full w-full flex-col bg-slate-950 text-slate-100">
      <main className="flex min-h-0 flex-1 flex-col items-center justify-center gap-5 px-6">
        <AsterFace state={faceState} level={level} sentiment={faceSentiment} />

        <div className="text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-cyan-300">
            {status}
          </p>
          {error && <p className="mt-1 text-xs text-rose-400">{error}</p>}
        </div>

        <CallControls
          callState={callState}
          muted={muted}
          onToggleCall={callState === "idle" ? startCall : endCall}
          onToggleMute={() => setMuted((m) => !m)}
        />
      </main>

      {/* resize handle */}
      <div
        onPointerDown={onResizeDown}
        className="group flex h-2.5 shrink-0 cursor-ns-resize items-center justify-center bg-slate-900"
      >
        <div className="h-1 w-10 rounded-full bg-slate-700 group-hover:bg-cyan-500/60" />
      </div>

      <div className="shrink-0" style={{ height: logHeight }}>
        <LogConsole />
      </div>

      {token && serverUrl && (
        <AsterRoom
          token={token}
          serverUrl={serverUrl}
          muted={muted}
          onConnected={() => setCallState("connected")}
          onDisconnected={endCall}
          onError={(msg) => {
            setError(msg);
            endCall();
          }}
          onLevel={handleLevel}
        />
      )}
    </div>
  );
}

export default function App() {
  return currentLabel() === "mini" ? <MiniReactor /> : <MainWindow />;
}
