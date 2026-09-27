import { useEffect, useRef, useState } from "react";
import { useLogsSocket } from "../lib/useLogsSocket";

const MAX_LINES = 150;

type LogLine = { text: string; sys: boolean };

export default function LogConsole() {
  const [lines, setLines] = useState<LogLine[]>([
    { text: "Waiting for Aster's log stream…", sys: true },
  ]);
  const endRef = useRef<HTMLDivElement | null>(null);

  useLogsSocket("terminal", (payload) => {
    const text =
      typeof payload.data === "string"
        ? payload.data
        : String(payload.data ?? "");
    if (text.trim()) {
      setLines((prev) => [...prev.slice(-(MAX_LINES - 1)), { text, sys: false }]);
    }
  });

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [lines]);

  return (
    <div className="flex h-full flex-col overflow-hidden border-t border-slate-800 bg-slate-950/80">
      <div className="px-3 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">
        Aster Logs
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-2 font-mono text-[11px] leading-snug">
        {lines.map((l, i) => (
          <div
            key={i}
            className={l.sys ? "text-cyan-500/70" : "text-slate-400"}
            style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
          >
            {l.text}
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
