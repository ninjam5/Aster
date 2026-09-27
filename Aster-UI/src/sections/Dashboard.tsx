import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Activity, Brain, Clock, Share2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "../components/ui/Card";
import { DASHBOARD_STATS, RECENT_ACTIVITY } from "../mock/dashboard";
import { useStats } from "../api/useStats";
import { staggerContainer, staggerItem } from "../theme/motion";
import { AsterHeroCard } from "../character/AsterHeroCard";
import type { DashboardStat, UserProfile } from "../types";

interface DashboardProps {
  profile: UserProfile;
}

// Visual identity per stat — keyed by the fixed mock ids in src/mock/dashboard.ts.
const STAT_STYLE: Record<string, { icon: LucideIcon; tone: string }> = {
  status: { icon: Activity, tone: "emerald" },
  memories: { icon: Brain, tone: "sky" },
  socials: { icon: Share2, tone: "violet" },
  uptime: { icon: Clock, tone: "amber" },
};

const toneClasses: Record<string, string> = {
  emerald: "bg-emerald-500/15 text-emerald-300",
  sky: "bg-sky-500/15 text-sky-300",
  violet: "bg-violet-500/15 text-violet-300",
  amber: "bg-amber-500/15 text-amber-300",
};

// Animates the leading integer in a stat value (e.g. "128" or "2 / 3") counting
// up from 0 on mount; any non-numeric value (e.g. "Online") renders as-is.
function useCountUp(target: number | null, duration = 700) {
  const [value, setValue] = useState(target === null ? 0 : 0);

  useEffect(() => {
    if (target === null) return;
    let start: number | null = null;
    let raf = 0;

    const step = (ts: number) => {
      if (start === null) start = ts;
      const progress = Math.min((ts - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(eased * target));
      if (progress < 1) raf = requestAnimationFrame(step);
    };

    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return value;
}

function StatValue({ value }: { value: string }) {
  const match = value.match(/^(\d+)(.*)$/);
  const numeric = match ? Number(match[1]) : null;
  const suffix = match ? match[2] : "";
  const count = useCountUp(numeric);

  return <>{numeric === null ? value : `${count}${suffix}`}</>;
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function Dashboard({ profile }: DashboardProps) {
  const greetingName = profile.name || "there";
  const stats = useStats();

  const displayStats: DashboardStat[] = stats
    ? [
        { id: "status",   label: "Status",   value: stats.status === "online" ? "Online" : "Offline", hint: "Brain running" },
        { id: "memories", label: "Memories", value: String(stats.memory_count), hint: "Facts stored" },
        { id: "socials",  label: "Socials",  value: `${Object.values(stats.socials).filter(Boolean).length} / 3`, hint: "Services connected" },
        { id: "uptime",   label: "Uptime",   value: formatUptime(stats.uptime_seconds) },
      ]
    : DASHBOARD_STATS;

  return (
    <motion.div variants={staggerContainer} initial="initial" animate="animate">
      <motion.div variants={staggerItem} className="mb-6">
        <AsterHeroCard name={greetingName} modelName={profile.model?.name} />
      </motion.div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {displayStats.map((stat: DashboardStat) => {
          const style = STAT_STYLE[stat.id];
          return (
            <motion.div key={stat.id} variants={staggerItem}>
              <Card interactive>
                <div className="flex items-center gap-3">
                  {style && (
                    <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${toneClasses[style.tone]}`}>
                      <style.icon size={18} />
                    </div>
                  )}
                  <p className="text-xs uppercase tracking-wide text-on-surface-variant/60">{stat.label}</p>
                </div>
                <p className="mt-2 font-headline-md text-headline-md text-on-surface">
                  <StatValue value={stat.value} />
                </p>
                {stat.hint && <p className="mt-1 text-xs text-on-surface-variant/50">{stat.hint}</p>}
              </Card>
            </motion.div>
          );
        })}
      </div>

      <motion.div variants={staggerItem}>
        <Card className="mt-6">
          <h2 className="font-label-lg text-label-lg text-on-surface">Recent activity</h2>
          <motion.ul
            variants={staggerContainer}
            initial="initial"
            animate="animate"
            className="mt-3 divide-y divide-outline-custom"
          >
            {RECENT_ACTIVITY.map((entry) => (
              <motion.li
                key={entry.id}
                variants={staggerItem}
                className="flex items-center justify-between py-2.5 text-body-md"
              >
                <span className="text-on-surface">{entry.label}</span>
                <span className="text-xs text-on-surface-variant/50">{entry.timestamp}</span>
              </motion.li>
            ))}
          </motion.ul>
        </Card>
      </motion.div>
    </motion.div>
  );
}
