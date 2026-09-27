// Phase machine for choreographed mood changes:
//   idle → exit (old pose plays its exit) → swap → enter (new pose's
//   entrance choreography) → idle (looping ambient animation).
// Enter/exit keyframes live in character.css, keyed by [data-phase][data-mood].
import { useEffect, useState } from "react";
import type { AsterMood } from "./sprites";

export type MoodPhase = "idle" | "exit" | "enter";

const EXIT_MS: Partial<Record<AsterMood, number>> = {
  tired: 320, // the whole bed scene sinks away
  pouty: 300,
};
const DEFAULT_EXIT_MS = 260;

const ENTER_MS: Partial<Record<AsterMood, number>> = {
  working: 950,  // body springs up, head drops on, stars burst
  thinking: 950,
  tired: 1500,   // pillow → flop → covers pulled up
  jumping: 800,
  success: 750,
};
const DEFAULT_ENTER_MS = 600;

export function useMoodTransition(target: AsterMood, animateFirstMount = false) {
  const [display, setDisplay] = useState(target);
  const [phase, setPhase] = useState<MoodPhase>(animateFirstMount ? "enter" : "idle");
  const [pending, setPending] = useState<AsterMood | null>(null);

  // Retarget detected during render (the React-sanctioned "adjust state when
  // props change" pattern): flag the pending mood and start the exit phase.
  if (target !== display && target !== pending) {
    setPending(target);
    setPhase("exit");
  }

  // once the exit finishes, swap sprites and play the entrance
  useEffect(() => {
    if (phase !== "exit" || pending === null) return;
    const t = window.setTimeout(() => {
      setDisplay(pending);
      setPending(null);
      setPhase("enter");
    }, EXIT_MS[display] ?? DEFAULT_EXIT_MS);
    return () => window.clearTimeout(t);
  }, [phase, pending, display]);

  // settle into idle loops once the entrance finishes
  useEffect(() => {
    if (phase !== "enter") return;
    const t = window.setTimeout(
      () => setPhase("idle"),
      ENTER_MS[display] ?? DEFAULT_ENTER_MS,
    );
    return () => window.clearTimeout(t);
  }, [phase, display]);

  return { mood: display, phase };
}
