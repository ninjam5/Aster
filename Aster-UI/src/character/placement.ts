// Where the companion Aster lives on each page — varied on purpose so he feels
// like he inhabits the app rather than being a sticker in one corner.
// `left` percentages account for the sidebar (1fr of a 1fr:3fr grid = 25%).
// A negative bottom makes him peek up over the bottom edge.
import type { CSSProperties } from "react";
import type { SectionId } from "../types";

export interface CompanionPlacement {
  style: CSSProperties;
  /** tailwind size classes for the character box */
  size: string;
  /** mirror him so he faces the page content */
  flip?: boolean;
}

export const COMPANION_PLACEMENT: Record<Exclude<SectionId, "dashboard">, CompanionPlacement> = {
  // standing beside the conversation, clear of the message input
  "voice-chat":   { style: { left: "calc(25% + 24px)", bottom: 96 }, size: "h-28 w-28", flip: true },
  // browsing the memory shelf, bottom-right
  memory:         { style: { right: 28, bottom: 14 }, size: "h-24 w-24" },
  // hanging out on the left for a change
  socials:        { style: { left: "calc(25% + 28px)", bottom: 14 }, size: "h-24 w-24", flip: true },
  skills:         { style: { right: 32, bottom: 14 }, size: "h-24 w-24" },
  notes:          { style: { left: "calc(25% + 28px)", bottom: 14 }, size: "h-20 w-20", flip: true },
  // peeking up over the bottom edge, watching the console scroll by
  "activity-log": { style: { right: 48, bottom: -36 }, size: "h-24 w-24" },
  settings:       { style: { right: 28, bottom: 14 }, size: "h-20 w-20" },
};
