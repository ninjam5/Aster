import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, MessageSquare, Music2, Send } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Card } from "../components/ui/Card";
import { Chip } from "../components/ui/Chip";
import { MOCK_SOCIALS } from "../mock/socials";
import { asterGet } from "../api/client";
import { cardHover, scaleIn, staggerContainer, staggerItem } from "../theme/motion";
import type { SocialConnection } from "../types";
import { GoogleCard } from "./GoogleCard";

// Per-platform brand identity — tokens defined in tailwind.config.js.
const SOCIAL_STYLE: Record<SocialConnection["id"], { icon: LucideIcon; border: string; iconBg: string; iconText: string }> = {
  spotify: { icon: Music2, border: "border-social-spotify/50", iconBg: "bg-social-spotify/15", iconText: "text-social-spotify" },
  discord: { icon: MessageSquare, border: "border-social-discord/50", iconBg: "bg-social-discord/15", iconText: "text-social-discord" },
  telegram: { icon: Send, border: "border-social-telegram/50", iconBg: "bg-social-telegram/15", iconText: "text-social-telegram" },
};

export function Socials() {
  const [socials, setSocials] = useState<SocialConnection[]>(MOCK_SOCIALS);

  useEffect(() => {
    const load = () => {
      asterGet<SocialConnection[]>("/api/socials")
        .then((data) => setSocials(data))
        .catch(() => {/* keep mock as fallback */});
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  return (
    <motion.div variants={staggerContainer} initial="initial" animate="animate">
      <motion.div variants={staggerItem}>
        <SectionHeader
          title="Connected Socials"
          description="Manage which services Aster can use on your behalf."
        />
      </motion.div>

      <motion.div variants={staggerItem} className="mb-4">
        <GoogleCard />
      </motion.div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {socials.map((social) => {
          const style = SOCIAL_STYLE[social.id];
          return (
            <motion.div key={social.id} variants={staggerItem} whileHover={cardHover}>
              <Card
                className={`flex items-start justify-between gap-4 transition-colors duration-300 ${
                  social.connected ? style.border : ""
                }`}
              >
                <div className="flex items-start gap-3">
                  <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${style.iconBg}`}>
                    <style.icon size={20} className={style.iconText} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="font-label-lg text-label-lg text-on-surface">{social.name}</h3>
                      <Chip tone={social.connected ? "success" : "neutral"}>
                        <span className="flex items-center gap-1">
                          <AnimatePresence mode="wait" initial={false}>
                            {social.connected && (
                              <motion.span
                                key="check"
                                variants={scaleIn}
                                initial="initial"
                                animate="animate"
                                exit="exit"
                                className="inline-flex"
                              >
                                <CheckCircle2 size={12} />
                              </motion.span>
                            )}
                          </AnimatePresence>
                          {social.connected ? "Connected" : "Not connected"}
                        </span>
                      </Chip>
                    </div>
                    <p className="mt-1 text-body-md text-on-surface-variant/70">{social.description}</p>
                    {social.detail && social.connected && (
                      <p className="mt-1 text-xs text-on-surface-variant/50">{social.detail}</p>
                    )}
                  </div>
                </div>
                <p className="text-xs text-on-surface-variant/40 shrink-0">
                  {social.connected ? "Active" : "Offline"}
                </p>
              </Card>
            </motion.div>
          );
        })}
      </div>
    </motion.div>
  );
}
