import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Eye, Hand, Radar, ShieldAlert, Target } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Card } from "../components/ui/Card";
import { Toggle } from "../components/ui/Toggle";
import { asterGet, asterPatch } from "../api/client";
import { cardHover, staggerContainer, staggerItem } from "../theme/motion";
import type { Skill } from "../types";

// One representative icon per skill — keyed by the fixed backend ids.
const SKILL_ICON: Record<string, LucideIcon> = {
  vision: Eye,
  intervention: Target,
  awareness: Radar,
  gesture: Hand,
  sentry: ShieldAlert,
};

export function Skills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    asterGet<Skill[]>("/api/skills")
      .then((data) => setSkills(data))
      .catch(() => {/* backend offline — leave empty */})
      .finally(() => setLoading(false));
  }, []);

  const toggle = (id: string) => {
    const skill = skills.find((s) => s.id === id);
    if (!skill || id === "vision") return;
    const next = !skill.enabled;
    setSkills((prev) => prev.map((s) => (s.id === id ? { ...s, enabled: next } : s)));
    asterPatch<{ id: string; enabled: boolean }>(`/api/skills/${id}`, { enabled: next })
      .then((res) => {
        setSkills((prev) => prev.map((s) => (s.id === res.id ? { ...s, enabled: res.enabled } : s)));
      })
      .catch(() => {
        setSkills((prev) => prev.map((s) => (s.id === id ? { ...s, enabled: skill.enabled } : s)));
      });
  };

  if (loading) {
    return (
      <div>
        <SectionHeader title="Skills & Automation" description="Turn Aster's proactive abilities on or off." />
        <p className="text-on-surface-variant/50 text-sm">Loading skills…</p>
      </div>
    );
  }

  return (
    <motion.div variants={staggerContainer} initial="initial" animate="animate">
      <motion.div variants={staggerItem}>
        <SectionHeader
          title="Skills & Automation"
          description="Turn Aster's proactive abilities on or off."
        />
      </motion.div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {skills.map((skill) => {
          const Icon = SKILL_ICON[skill.id];
          const nonTogglable = skill.id === "vision";
          return (
            <motion.div key={skill.id} variants={staggerItem} whileHover={cardHover}>
              <Card className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <div className="relative mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-elevated">
                    {Icon && <Icon size={18} className={skill.enabled ? "text-primary" : "text-on-surface-variant/50"} />}
                    <span
                      className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-surface-card ${
                        skill.enabled ? "bg-accent-blue animate-aster-glow" : "bg-on-surface-variant/30"
                      }`}
                    />
                  </div>
                  <div>
                    <h3 className="font-label-lg text-label-lg text-on-surface">{skill.name}</h3>
                    <p className="mt-1 text-body-md text-on-surface-variant/70">
                      {skill.enabled ? skill.description : `Off — turn on to: ${skill.description.toLowerCase()}`}
                    </p>
                  </div>
                </div>
                <Toggle
                  checked={skill.enabled}
                  onChange={() => toggle(skill.id)}
                  label={skill.name}
                  disabled={nonTogglable}
                />
              </Card>
            </motion.div>
          );
        })}
      </div>
    </motion.div>
  );
}
