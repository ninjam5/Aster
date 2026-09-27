import { useEffect, useState } from "react";
import { asterGet } from "./client";

export interface AsterStats {
  status: string;
  memory_count: number;
  uptime_seconds: number;
  socials: { spotify: boolean; discord: boolean; telegram: boolean };
}

export function useStats(intervalMs = 30_000) {
  const [stats, setStats] = useState<AsterStats | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await asterGet<AsterStats>("/api/stats");
        if (!cancelled) setStats(data);
      } catch {
        // backend not running — keep showing previous value or null
      }
    }

    load();
    const id = setInterval(load, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return stats;
}
