import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PixelAsterCharacter } from "./PixelAsterCharacter";
import {
  GRID_H,
  GRID_W,
  PAL,
  PIXEL_MOODS,
  PIXEL_SPRITES,
  type Grid,
} from "./pixelSprites";
import { buildTransition, PIXEL_ENTER, PIXEL_EXIT } from "./pixelTimelines";
import {
  readCharacterStyle,
  writeCharacterStyle,
} from "../useCharacterStyle";
import { AsterCompanion } from "../AsterCompanion";
import { Settings } from "../../sections/Settings";
import type { AsterMood } from "../sprites";
import type { UserProfile } from "../../types";
import { AVAILABLE_MODELS } from "../../mock/models";

const ALL_MOODS: AsterMood[] = [
  "calm", "working", "alert", "music", "success",
  "pouty", "jumping", "thinking", "confused", "tired",
];

function expectValidGrid(g: Grid, label: string) {
  expect(g, label).toHaveLength(GRID_H);
  for (const row of g) {
    expect(row, label).toHaveLength(GRID_W);
    for (const ch of row) {
      if (ch === ".") continue;
      expect(PAL[ch], `${label}: unknown palette char "${ch}"`).toBeTruthy();
    }
  }
}

describe("pixel sprites", () => {
  it("has a well-formed 26×34 grid for every mood, palette chars only", () => {
    for (const mood of ALL_MOODS) {
      expectValidGrid(PIXEL_SPRITES[mood], `sprite ${mood}`);
      expect(PIXEL_MOODS[mood]).toBeTruthy();
    }
  });

  it("moods are visually distinct (no two idle grids identical)", () => {
    const flat = ALL_MOODS.map((m) => PIXEL_SPRITES[m].map((r) => r.join("")).join("\n"));
    expect(new Set(flat).size).toBe(ALL_MOODS.length);
  });
});

describe("pixel transition timelines", () => {
  it("defines an entrance and an exit for every mood", () => {
    for (const mood of ALL_MOODS) {
      expect(PIXEL_ENTER[mood].length, `enter ${mood}`).toBeGreaterThan(0);
      expect(PIXEL_EXIT[mood].length, `exit ${mood}`).toBeGreaterThan(0);
    }
  });

  it("every step shows a valid keyframe grid for a positive duration", () => {
    for (const mood of ALL_MOODS) {
      for (const [kind, steps] of [["enter", PIXEL_ENTER[mood]], ["exit", PIXEL_EXIT[mood]]] as const) {
        steps.forEach((step, i) => {
          expect(step.ms, `${kind} ${mood}[${i}].ms`).toBeGreaterThan(0);
          expectValidGrid(step.g, `${kind} ${mood}[${i}].g`);
          if (step.desk) expectValidGrid(step.desk, `${kind} ${mood}[${i}].desk`);
        });
      }
    }
  });

  it("buildTransition plays the old mood's exit then the new mood's entrance", () => {
    const steps = buildTransition("working", "pouty");
    expect(steps).toEqual([...PIXEL_EXIT.working, ...PIXEL_ENTER.pouty]);
  });

  it("the working entrance includes the headphone gag and the desk pop-up", () => {
    const steps = PIXEL_ENTER.working;
    // at least one step with a rising desk overlay
    expect(steps.some((s) => s.desk && s.deskDy && s.deskDy[0] > s.deskDy[1])).toBe(true);
    // multiple character keyframes before the desk appears (the headphone bit)
    const firstDesk = steps.findIndex((s) => s.desk);
    expect(firstDesk).toBeGreaterThanOrEqual(3);
  });
});

describe("PixelAsterCharacter", () => {
  it("renders an accessible canvas for the current mood", () => {
    render(<PixelAsterCharacter mood="calm" />);
    const canvas = screen.getByRole("img", { name: "Aster (calm)" });
    expect(canvas.tagName).toBe("CANVAS");
  });

  it("tracks mood prop changes without crashing (jsdom has no 2D context)", () => {
    const { rerender } = render(<PixelAsterCharacter mood="calm" />);
    for (const mood of ALL_MOODS) {
      rerender(<PixelAsterCharacter mood={mood} />);
      expect(screen.getByRole("img", { name: `Aster (${mood})` })).toBeInTheDocument();
    }
  });
});

describe("character style switch", () => {
  it("defaults to pixel and persists changes", () => {
    expect(readCharacterStyle()).toBe("pixel");
    writeCharacterStyle("cartoon");
    expect(readCharacterStyle()).toBe("cartoon");
    expect(localStorage.getItem("aster.characterStyle.v1")).toBe("cartoon");
  });

  it("companion renders the pixel canvas by default", () => {
    render(<AsterCompanion section="memory" />);
    expect(document.querySelector("[data-pixel-aster] canvas")).toBeInTheDocument();
    expect(screen.queryByAltText(/^Aster \(/)).not.toBeInTheDocument();
  });

  it("companion renders the cartoon puppet when the style is cartoon", () => {
    writeCharacterStyle("cartoon");
    render(<AsterCompanion section="memory" />);
    expect(document.querySelector("[data-pixel-aster]")).not.toBeInTheDocument();
    expect(screen.getByAltText("Aster (calm)")).toBeInTheDocument();
  });

  it("Settings exposes the character-style picker and switches live", async () => {
    const profile: UserProfile = {
      name: "Mohamed",
      techLevel: "basic",
      model: AVAILABLE_MODELS[0],
      onboardingComplete: true,
    };
    render(
      <Settings
        profile={profile}
        onUpdateName={vi.fn()}
        onUpdateTechLevel={vi.fn()}
        onUpdateModel={vi.fn()}
        onResetOnboarding={vi.fn()}
      />,
    );
    const picker = screen.getByLabelText("Character style", { selector: "select" });
    expect(picker).toHaveValue("pixel");
    await userEvent.selectOptions(picker, "cartoon");
    expect(readCharacterStyle()).toBe("cartoon");
  });
});
