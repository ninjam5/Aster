import { Mic, MicOff, Phone, PhoneOff } from "lucide-react";

type CallState = "idle" | "connecting" | "connected";

type CallControlsProps = {
  callState: CallState;
  muted: boolean;
  onToggleCall: () => void;
  onToggleMute: () => void;
};

export default function CallControls({
  callState,
  muted,
  onToggleCall,
  onToggleMute,
}: CallControlsProps) {
  const inCall = callState !== "idle";
  const connecting = callState === "connecting";

  return (
    <div className="flex items-center justify-center gap-3">
      <button
        type="button"
        onClick={onToggleCall}
        disabled={connecting}
        className={[
          "flex items-center gap-2 rounded-full px-6 py-2.5 text-sm font-semibold tracking-wide transition",
          "border-2 disabled:opacity-60",
          inCall
            ? "border-rose-400/60 bg-rose-500/15 text-rose-200 hover:bg-rose-500/25"
            : "border-cyan-400/60 bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/25",
        ].join(" ")}
      >
        {inCall ? <PhoneOff size={16} /> : <Phone size={16} />}
        {connecting ? "Connecting…" : inCall ? "End Call" : "Start Call"}
      </button>

      <button
        type="button"
        onClick={onToggleMute}
        disabled={callState !== "connected"}
        className={[
          "flex items-center gap-2 rounded-full px-5 py-2.5 text-sm font-semibold tracking-wide transition",
          "border-2 border-slate-600 bg-slate-800/70 text-slate-200 hover:border-slate-400",
          "disabled:cursor-not-allowed disabled:opacity-40",
          muted ? "!border-amber-400/70 !text-amber-200" : "",
        ].join(" ")}
      >
        {muted ? <MicOff size={16} /> : <Mic size={16} />}
        {muted ? "Muted" : "Mute"}
      </button>
    </div>
  );
}
