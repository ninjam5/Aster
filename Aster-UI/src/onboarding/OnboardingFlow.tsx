import { useState } from "react";
import { AnimatePresence } from "framer-motion";
import type { AsterModel, TechLevel } from "../types";
import { WelcomeStep } from "./WelcomeStep";
import { NameStep } from "./NameStep";
import { ModelStep } from "./ModelStep";
import { DetectingStep } from "./DetectingStep";
import { PersonaStep } from "./PersonaStep";

type Step = "welcome" | "name" | "persona" | "model" | "detecting";

interface OnboardingFlowProps {
  initialName: string;
  onComplete: (data: { name: string; techLevel: TechLevel; model: AsterModel }) => void;
}

export function OnboardingFlow({ initialName, onComplete }: OnboardingFlowProps) {
  const [step, setStep] = useState<Step>("welcome");
  const [techLevel, setTechLevel] = useState<TechLevel>("basic");
  const [name, setName] = useState(initialName);

  return (
    <div className="flex h-full w-full flex-col items-center justify-center overflow-hidden bg-background p-margin_edge">
      <AnimatePresence mode="wait">
        {step === "welcome" && (
          <WelcomeStep
            key="welcome"
            onSelect={(level) => {
              setTechLevel(level);
              setStep("name");
            }}
          />
        )}

        {step === "name" && (
          <NameStep
            key="name"
            initialName={name}
            onContinue={(value) => {
              setName(value);
              // Both basic and advanced paths go through persona selection next.
              setStep("persona");
            }}
          />
        )}

        {step === "persona" && (
          <PersonaStep
            key="persona"
            onContinue={() => {
              // Advanced users pick the model; basic users get auto-detection.
              setStep(techLevel === "advanced" ? "model" : "detecting");
            }}
          />
        )}

        {step === "model" && (
          <ModelStep
            key="model"
            onContinue={(model) => onComplete({ name, techLevel, model })}
          />
        )}

        {step === "detecting" && (
          <DetectingStep
            key="detecting"
            onDetected={(model) => onComplete({ name, techLevel, model })}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
