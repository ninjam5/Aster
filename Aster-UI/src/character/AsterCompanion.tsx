/**
 * AsterCompanion — the small Aster that lives on every page except home
 * (home has the hero scene instead — one Aster per page). His spot, size and
 * facing change per section (see placement.ts) so he inhabits each page
 * rather than being pinned to one corner. Non-interactive: pointer-events
 * none, so he never blocks clicks. Remounts per section (key in AppShell),
 * which replays his entrance choreography at the new spot.
 *
 * Two renderers share the placement contract: the 8-bit pixel canvas
 * (default) and the original cartoon puppet — chosen in Settings →
 * Appearance (useCharacterStyle).
 */
import { AsterCharacter } from "./AsterCharacter";
import { PixelAsterCharacter } from "./pixel/PixelAsterCharacter";
import { useAsterMood } from "./AsterMoodProvider";
import { useCharacterStyle } from "./useCharacterStyle";
import { COMPANION_PLACEMENT } from "./placement";
import type { SectionId } from "../types";

interface AsterCompanionProps {
  section: Exclude<SectionId, "dashboard">;
}

export function AsterCompanion({ section }: AsterCompanionProps) {
  const mood = useAsterMood();
  const [style] = useCharacterStyle();
  const placement = COMPANION_PLACEMENT[section];

  const Character = style === "pixel" ? PixelAsterCharacter : AsterCharacter;

  return (
    <div
      className="pointer-events-none fixed z-40"
      style={placement.style}
      aria-hidden="true"
      data-testid="aster-companion"
    >
      <Character
        mood={mood}
        flip={placement.flip}
        animateFirstMount
        className={`${placement.size} drop-shadow-[0_6px_14px_rgba(0,0,0,0.5)]`}
      />
    </div>
  );
}
