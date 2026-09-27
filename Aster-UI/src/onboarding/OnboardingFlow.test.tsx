import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { OnboardingFlow } from "./OnboardingFlow";

// Mock the settings API so PersonaStep doesn't hit a missing backend.
vi.mock("../api/settings", () => ({
  fetchPersonas: () =>
    Promise.resolve({
      active: "jarvis",
      personas: [
        { id: "jarvis", name: "Jarvis", builtin: true },
        { id: "gogi", name: "Gogi", builtin: true },
      ],
    }),
  fetchVoices: () =>
    Promise.resolve({
      active: "af_bella",
      voices: [{ id: "af_bella", label: "Bella", gender: "female", accent: "American" }],
    }),
  fetchPresets: () =>
    Promise.resolve({
      presets: [
        { id: "jarvis_default", name: "Jarvis — British Butler", persona: "jarvis", voice: "bm_george", builtin: true },
        { id: "gogi_default",   name: "Gogi — Casual Friend",   persona: "gogi",   voice: "am_michael", builtin: true },
      ],
    }),
  selectPersona: () => Promise.resolve(),
  selectVoice: () => Promise.resolve(),
  selectPreset: () => Promise.resolve({ persona: "jarvis", voice: "bm_george" }),
  createPreset: () => Promise.resolve({ id: "new_preset" }),
  deletePreset: () => Promise.resolve(),
  previewVoice: () => Promise.resolve("blob:fake"),
}));

describe("OnboardingFlow", () => {
  it("advanced path: welcome → name → persona → model selection → complete", async () => {
    const onComplete = vi.fn();
    render(<OnboardingFlow initialName="" onComplete={onComplete} />);

    expect(screen.getByText("Welcome to the Aster framework")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Advanced User"));

    await screen.findByText("Tell us your name");
    await userEvent.type(screen.getByPlaceholderText("Your name"), "Mohamed");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    // Persona step appears next (new step for all users)
    await screen.findByText("Choose a persona");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    // Advanced path then shows model selection
    await screen.findByText("Choose a model");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(onComplete).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Mohamed", techLevel: "advanced" }),
    );
    expect(onComplete.mock.calls[0][0].model).toBeTruthy();
  });

  it("basic path: welcome → name → persona → auto-detects, never shows model screen", async () => {
    const onComplete = vi.fn();
    render(<OnboardingFlow initialName="" onComplete={onComplete} />);

    await userEvent.click(screen.getByText("Basic User"));
    await screen.findByText("Tell us your name");
    await userEvent.type(screen.getByPlaceholderText("Your name"), "Sam");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    // Persona step
    await screen.findByText("Choose a persona");
    // No model dropdown at any point on basic path
    expect(screen.queryByText("Choose a model")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    // Goes straight to detecting — never shows model screen
    expect(screen.queryByText("Choose a model")).not.toBeInTheDocument();
    await screen.findByText("Detecting your hardware…");

    await waitFor(
      () =>
        expect(onComplete).toHaveBeenCalledWith(
          expect.objectContaining({ name: "Sam", techLevel: "basic" }),
        ),
      { timeout: 3000 },
    );
  });

  it("disables Continue until a name is entered", async () => {
    render(<OnboardingFlow initialName="" onComplete={vi.fn()} />);
    await userEvent.click(screen.getByText("Advanced User"));
    await screen.findByText("Tell us your name");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });
});
