import { motion, AnimatePresence } from "framer-motion";
import type { SectionId, TechLevel, UserProfile } from "../types";
import { fade } from "../theme/motion";
import { Dashboard } from "../sections/Dashboard";
import { VoiceChat } from "../sections/VoiceChat";
import { Memory } from "../sections/Memory";
import { Socials } from "../sections/Socials";
import { Skills } from "../sections/Skills";
import { Notes } from "../sections/Notes";
import { ActivityLog } from "../sections/ActivityLog";
import { Settings } from "../sections/Settings";

interface BodyRouterProps {
  active: SectionId;
  profile: UserProfile;
  onUpdateName: (name: string) => void;
  onUpdateTechLevel: (techLevel: TechLevel) => void;
  onUpdateModelId: (modelId: string) => void;
  onResetOnboarding: () => void;
}

export function BodyRouter({
  active,
  profile,
  onUpdateName,
  onUpdateTechLevel,
  onUpdateModelId,
  onResetOnboarding,
}: BodyRouterProps) {
  return (
    <main className="aster-scroll h-full flex-1 overflow-y-auto p-8">
      <AnimatePresence mode="wait">
        <motion.div key={active} variants={fade} initial="initial" animate="animate" exit="exit">
          {active === "dashboard" && <Dashboard profile={profile} />}
          {active === "voice-chat" && <VoiceChat />}
          {active === "memory" && <Memory />}
          {active === "socials" && <Socials />}
          {active === "skills" && <Skills />}
          {active === "notes" && <Notes />}
          {active === "activity-log" && <ActivityLog />}
          {active === "settings" && (
            <Settings
              profile={profile}
              onUpdateName={onUpdateName}
              onUpdateTechLevel={onUpdateTechLevel}
              onUpdateModel={onUpdateModelId}
              onResetOnboarding={onResetOnboarding}
            />
          )}
        </motion.div>
      </AnimatePresence>
    </main>
  );
}
