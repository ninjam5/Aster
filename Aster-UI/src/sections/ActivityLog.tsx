import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Card } from "../components/ui/Card";
import { ASTER_WS_BASE } from "../api/config";
import { staggerContainer, staggerItem } from "../theme/motion";
import type { ActivityEntry } from "../types";

const levelClasses: Record<ActivityEntry["level"], string> = {
  info: "text-on-surface-variant",
  warn: "text-amber-300",
  error: "text-error-text",
};

export function ActivityLog() {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let ws: WebSocket;
    let retryTimer: ReturnType<typeof setTimeout>;

    function connect() {
      ws = new WebSocket(`${ASTER_WS_BASE}/api/logs`);

      ws.onmessage = (ev) => {
        const event = JSON.parse(ev.data as string) as {
          type: string;
          data?: string;
          user?: string;
          msg?: string;
          ts: number;
        };

        let message: string;
        if (event.type === "terminal" && event.data) {
          message = event.data;
        } else if (event.type === "discord") {
          message = `[Discord → ${event.user}] ${event.msg}`;
        } else {
          return;
        }

        const level: ActivityEntry["level"] =
          /error/i.test(message) ? "error" :
          /warn/i.test(message)  ? "warn"  : "info";

        setEntries((prev) => [
          ...prev.slice(-499),
          {
            id: `ws-${event.ts}-${Math.random()}`,
            timestamp: new Date(event.ts).toTimeString().slice(0, 8),
            level,
            message,
          },
        ]);
      };

      ws.onclose = () => {
        retryTimer = setTimeout(connect, 2000);
      };
    }

    connect();
    return () => {
      ws.close();
      clearTimeout(retryTimer);
    };
  }, []);

  // Autoscroll to the newest line whenever one arrives.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  return (
    <div>
      <SectionHeader title="Activity Log" description="A live look at what Aster is doing." />

      <Card className="bg-[#0c0c0c]">
        <motion.div
          ref={scrollRef}
          variants={staggerContainer}
          initial="initial"
          animate="animate"
          className="aster-scroll max-h-[480px] space-y-1.5 overflow-y-auto font-mono text-xs"
        >
          {entries.length === 0 && (
            <p className="text-on-surface-variant/40 italic">Connecting to Aster…</p>
          )}
          <AnimatePresence initial={false}>
            {entries.map((entry) => (
              <motion.div
                key={entry.id}
                layout
                variants={staggerItem}
                initial="initial"
                animate="animate"
                className="flex gap-3"
              >
                <span className="text-on-surface-variant/40">{entry.timestamp}</span>
                <span className={levelClasses[entry.level]}>{entry.message}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        </motion.div>
      </Card>
    </div>
  );
}
