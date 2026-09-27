import { useState } from "react";
import { motion } from "framer-motion";
import { fadeSlideUp } from "../theme/motion";
import { Select } from "../components/ui/Select";
import { Button } from "../components/ui/Button";
import { AVAILABLE_MODELS } from "../mock/models";
import type { AsterModel } from "../types";

interface ModelStepProps {
  onContinue: (model: AsterModel) => void;
}

export function ModelStep({ onContinue }: ModelStepProps) {
  const [selectedId, setSelectedId] = useState(AVAILABLE_MODELS[0].id);
  const selected = AVAILABLE_MODELS.find((m) => m.id === selectedId) ?? AVAILABLE_MODELS[0];

  return (
    <motion.div
      variants={fadeSlideUp}
      initial="initial"
      animate="animate"
      exit="exit"
      className="flex w-full max-w-sm flex-col items-center text-center"
    >
      <h1 className="font-headline-lg text-headline-lg text-white/90">Choose a model</h1>
      <p className="mt-2 text-body-md text-on-surface-variant/70">
        You can change this later in Settings.
      </p>

      <div className="mt-8 flex w-full flex-col gap-4">
        <Select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
          {AVAILABLE_MODELS.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name}
            </option>
          ))}
        </Select>
        <p className="text-left text-body-md text-on-surface-variant/60">{selected.description}</p>
        <Button onClick={() => onContinue(selected)}>Continue</Button>
      </div>
    </motion.div>
  );
}
