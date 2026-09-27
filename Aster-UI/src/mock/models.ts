import type { AsterModel } from "../types";

export const AVAILABLE_MODELS: AsterModel[] = [
  {
    id: "gemma-4-e4b",
    name: "Gemma 4 E4B",
    description: "Fast & efficient — recommended for most setups",
  },
  {
    id: "gemma-4-12b",
    name: "Gemma 4 12B",
    description: "Larger, more capable — needs more VRAM",
  },
  {
    id: "qwen-3.6-35b",
    name: "Qwen 3.6 35B",
    description: "Most capable — best for high-end GPUs",
  },
];

// Used by the "Basic User" onboarding path to simulate auto-detection.
export const AUTO_DETECTED_MODEL: AsterModel = AVAILABLE_MODELS[0];
