import type { AsterModel } from "../types";

export const AVAILABLE_MODELS: AsterModel[] = [
  {
    id: "qwen-3.6-35b-a3b",
    name: "Qwen 3.6 35B-A3B (MoE)",
    description: "Fast & efficient — the current engine",
  },
  {
    id: "gemma-4-12b",
    name: "Gemma 4 12B",
    description: "Larger, more capable — needs more VRAM",
  },
  {
    id: "qwen-3.6-35b-a3b-mtp",
    name: "Qwen 3.6 35B-A3B + MTP",
    description: "Most capable — best for high-end GPUs",
  },
];

// Used by the "Basic User" onboarding path to simulate auto-detection.
export const AUTO_DETECTED_MODEL: AsterModel = AVAILABLE_MODELS[0];
