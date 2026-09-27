import type { Skill } from "../types";

export const MOCK_SKILLS: Skill[] = [
  { id: "vision", name: "Vision", description: "Let Aster see your screen and webcam when asked", enabled: true },
  { id: "intervention", name: "Focus Mode", description: "Gently nudge you back on track when you get distracted", enabled: true },
  { id: "awareness", name: "Awareness", description: "Ambient check-ins based on screen & webcam context", enabled: false },
  { id: "gesture", name: "Gesture Control", description: "Control music & system with hand gestures via webcam", enabled: false },
  { id: "sentry", name: "Sentry Mode", description: "Watch for unrecognized faces while you're away", enabled: false },
];
