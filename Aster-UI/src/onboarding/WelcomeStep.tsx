import { motion } from "framer-motion";
import { Cpu, Sparkles } from "lucide-react";
import type { TechLevel } from "../types";
import { fadeSlideUp } from "../theme/motion";

interface WelcomeStepProps {
  onSelect: (techLevel: TechLevel) => void;
}

export function WelcomeStep({ onSelect }: WelcomeStepProps) {
  return (
    <motion.div
      variants={fadeSlideUp}
      initial="initial"
      animate="animate"
      exit="exit"
      className="flex w-full max-w-sm flex-col items-center text-center"
    >
      <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-accent-blue/15">
        <Sparkles size={40} className="text-primary" />
      </div>
      <h1 className="font-headline-lg text-headline-lg text-white/90">
        Welcome to the Aster framework
      </h1>
      <p className="mt-2 text-body-md text-on-surface-variant/70">
        Let's set things up. First — how would you describe yourself?
      </p>

      <div className="mt-8 flex w-full flex-col gap-3">
        <button
          type="button"
          onClick={() => onSelect("basic")}
          className="group flex items-center gap-4 rounded-card border border-outline-custom bg-surface-card p-4 text-left transition-all hover:border-accent-blue/50 hover:bg-surface-elevated active:scale-[0.99]"
        >
          <Sparkles size={22} className="text-primary" />
          <span>
            <span className="block font-label-lg text-label-lg text-on-surface">Basic User</span>
            <span className="block text-body-md text-on-surface-variant/60">
              Just get me set up — pick the best defaults for me
            </span>
          </span>
        </button>

        <button
          type="button"
          onClick={() => onSelect("advanced")}
          className="group flex items-center gap-4 rounded-card border border-outline-custom bg-surface-card p-4 text-left transition-all hover:border-accent-blue/50 hover:bg-surface-elevated active:scale-[0.99]"
        >
          <Cpu size={22} className="text-primary" />
          <span>
            <span className="block font-label-lg text-label-lg text-on-surface">Advanced User</span>
            <span className="block text-body-md text-on-surface-variant/60">
              Let me choose the model Aster runs on
            </span>
          </span>
        </button>
      </div>
    </motion.div>
  );
}
