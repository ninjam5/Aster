import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AppShell } from "./AppShell";
import { AVAILABLE_MODELS } from "../mock/models";
import type { UserProfile } from "../types";

const profile: UserProfile = {
  name: "Mohamed",
  techLevel: "advanced",
  model: AVAILABLE_MODELS[0],
  onboardingComplete: true,
};

function renderShell(overrides: Partial<Parameters<typeof AppShell>[0]> = {}) {
  return render(
    <AppShell
      profile={profile}
      onUpdateName={vi.fn()}
      onUpdateTechLevel={vi.fn()}
      onUpdateModelId={vi.fn()}
      onResetOnboarding={vi.fn()}
      {...overrides}
    />,
  );
}

describe("AppShell", () => {
  it("shows all 8 nav sections and the user chip", () => {
    renderShell();
    const nav = screen.getByRole("navigation");
    for (const label of [
      "Home",
      "Voice & Chat",
      "Memory",
      "Connected Socials",
      "Skills & Automation",
      "Notes & Reminders",
      "Activity Log",
      "Settings",
    ]) {
      expect(within(nav).getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("Mohamed")).toBeInTheDocument();
  });

  it("defaults to the dashboard panel", () => {
    renderShell();
    expect(screen.getByText("Welcome back, Mohamed")).toBeInTheDocument();
  });

  it("switches the body when a nav item is clicked", async () => {
    renderShell();
    const nav = screen.getByRole("navigation");
    await userEvent.click(within(nav).getByText("Memory"));
    expect(
      await screen.findByText("What Aster remembers about you, across every conversation."),
    ).toBeInTheDocument();
  });
});
