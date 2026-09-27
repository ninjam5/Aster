import { useEffect } from "react";
import { motion } from "framer-motion";
import { fadeSlideUp } from "../theme/motion";
import { AUTO_DETECTED_MODEL } from "../mock/models";
import type { AsterModel } from "../types";

interface DetectingStepProps {
  onDetected: (model: AsterModel) => void;
}

export function DetectingStep({ onDetected }: DetectingStepProps) {
  useEffect(() => {
    const timeout = setTimeout(() => onDetected(AUTO_DETECTED_MODEL), 1400);
    return () => clearTimeout(timeout);
  }, [onDetected]);

  return (
    <motion.div
      variants={fadeSlideUp}
      initial="initial"
      animate="animate"
      exit="exit"
      className="flex w-full max-w-sm flex-col items-center text-center"
    >
      <div className="mb-4 h-10 w-10 animate-spin rounded-full border-2 border-white/20 border-t-accent-blue" />
      <h1 className="font-headline-lg text-headline-lg text-white/90">Detecting your hardware…</h1>
      <p className="mt-2 text-body-md text-on-surface-variant/70">
        Picking the best Aster model for your machine.
      </p>
    </motion.div>
  );
}
