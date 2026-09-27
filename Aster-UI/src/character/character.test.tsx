import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AsterCharacter } from "./AsterCharacter";
import { AsterCompanion } from "./AsterCompanion";
import { resolveAsterMood } from "./AsterMoodProvider";
import { COMPANION_PLACEMENT } from "./placement";
import { ASTER_SPRITES, MOOD_LABEL, type AsterMood } from "./sprites";
import { AppShell } from "../shell/AppShell";
import { AVAILABLE_MODELS } from "../mock/models";
import type { SectionId, UserProfile } from "../types";

const ALL_MOODS: AsterMood[] = [
  "calm", "working", "alert", "music", "success",
  "pouty", "jumping", "thinking", "confused", "tired",
];

describe("sprites", () => {
  it("has layered sprite metadata and a label for all 10 moods", () => {
    for (const mood of ALL_MOODS) {
      const meta = ASTER_SPRITES[mood];
      expect(meta.body).toBeTruthy();
      expect(meta.w).toBeGreaterThan(0);
      expect(meta.h).toBeGreaterThan(0);
      expect(MOOD_LABEL[mood]).toBeTruthy();
    }
    // celebration poses are whole-sprite; everything else is a head/body puppet
    for (const mood of ALL_MOODS) {
      if (mood === "success" || mood === "jumping") {
        expect(ASTER_SPRITES[mood].head).toBeUndefined();
      } else {
        expect(ASTER_SPRITES[mood].head).toBeTruthy();
      }
    }
  });
});

describe("resolveAsterMood", () => {
  const inputs = {
    offline: false,
    streaming: false,
    transient: null,
    base: "calm" as AsterMood,
    hour: 12,
  };

  it("offline wins over everything (pouty)", () => {
    expect(
      resolveAsterMood({ ...inputs, offline: true, streaming: true, transient: "success" }),
    ).toBe("pouty");
  });

  it("streaming shows thinking over transients and base", () => {
    expect(resolveAsterMood({ ...inputs, streaming: true, transient: "success" })).toBe("thinking");
  });

  it("a transient reaction beats the base sentiment", () => {
    expect(resolveAsterMood({ ...inputs, base: "working", transient: "confused" })).toBe("confused");
  });

  it("falls through to the base sentiment", () => {
    expect(resolveAsterMood({ ...inputs, base: "music" })).toBe("music");
  });

  it("calm turns tired late at night; active moods do not", () => {
    expect(resolveAsterMood({ ...inputs, hour: 2 })).toBe("tired");
    expect(resolveAsterMood({ ...inputs, hour: 23 })).toBe("tired");
    expect(resolveAsterMood({ ...inputs, hour: 12 })).toBe("calm");
    expect(resolveAsterMood({ ...inputs, base: "working", hour: 2 })).toBe("working");
  });
});

describe("AsterCharacter transitions", () => {
  it("plays the old pose's exit before swapping (no instant switch)", async () => {
    const { rerender } = render(<AsterCharacter mood="calm" />);
    expect(screen.getByAltText("Aster (calm)")).toBeInTheDocument();

    rerender(<AsterCharacter mood="thinking" />);
    // still the old pose, now in its exit phase
    const stage = document.querySelector(".aster-stage");
    expect(screen.getByAltText("Aster (calm)")).toBeInTheDocument();
    expect(stage).toHaveAttribute("data-phase", "exit");

    // after the exit completes, the new pose enters
    expect(await screen.findByAltText("Aster (thinking)")).toBeInTheDocument();
    expect(stage).toHaveAttribute("data-phase", "enter");

    // and eventually settles into idle loops
    await waitFor(() => expect(stage).toHaveAttribute("data-phase", "idle"), { timeout: 2500 });
  });

  it("renders tired as the sleeping scene (bed, not the standing sprite)", () => {
    render(<AsterCharacter mood="tired" />);
    expect(screen.getByRole("img", { name: "Aster (tired)" })).toBeInTheDocument();
    expect(document.querySelector(".aster-sleep-blanket")).toBeInTheDocument();
    expect(screen.queryByAltText("Aster (tired)")).not.toBeInTheDocument();
  });
});

describe("companion placement", () => {
  it("defines a distinct spot for every page except home", () => {
    const sections: SectionId[] = [
      "voice-chat", "memory", "socials", "skills", "notes", "activity-log", "settings",
    ];
    for (const section of sections) {
      expect(COMPANION_PLACEMENT[section as keyof typeof COMPANION_PLACEMENT]).toBeTruthy();
    }
  });

  it("renders with the default calm mood outside a provider", () => {
    render(<AsterCompanion section="memory" />);
    expect(screen.getByTestId("aster-companion")).toBeInTheDocument();
    // default style is the pixel canvas renderer (role img via aria-label)
    expect(screen.getByRole("img", { name: "Aster (calm)", hidden: true })).toBeInTheDocument();
  });
});

describe("one Aster per page (shell integration)", () => {
  const profile: UserProfile = {
    name: "Mohamed",
    techLevel: "advanced",
    model: AVAILABLE_MODELS[0],
    onboardingComplete: true,
  };

  it("home shows only the hero scene; other pages show only the companion", async () => {
    render(
      <AppShell
        profile={profile}
        onUpdateName={vi.fn()}
        onUpdateTechLevel={vi.fn()}
        onUpdateModelId={vi.fn()}
        onResetOnboarding={vi.fn()}
      />,
    );

    expect(screen.getByTestId("aster-hero")).toBeInTheDocument();
    expect(screen.queryByTestId("aster-companion")).not.toBeInTheDocument();
    expect(screen.queryAllByAltText(/^Aster \(/)).toHaveLength(0);

    const nav = screen.getByRole("navigation");
    await userEvent.click(within(nav).getByText("Memory"));
    expect(await screen.findByTestId("aster-companion")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByTestId("aster-hero")).not.toBeInTheDocument(),
    );
  });
});
