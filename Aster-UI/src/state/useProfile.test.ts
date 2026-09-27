import { act, renderHook } from "@testing-library/react";
import { useProfile } from "./useProfile";
import { AVAILABLE_MODELS } from "../mock/models";

describe("useProfile", () => {
  it("starts with a blank, incomplete profile", () => {
    const { result } = renderHook(() => useProfile());
    expect(result.current.profile).toEqual({
      name: "",
      techLevel: "basic",
      model: null,
      onboardingComplete: false,
    });
  });

  it("updates fields and completes onboarding", () => {
    const { result } = renderHook(() => useProfile());
    act(() => {
      result.current.setName("Mohamed");
      result.current.setTechLevel("advanced");
      result.current.setModel(AVAILABLE_MODELS[1]);
      result.current.completeOnboarding();
    });
    expect(result.current.profile.name).toBe("Mohamed");
    expect(result.current.profile.techLevel).toBe("advanced");
    expect(result.current.profile.model?.id).toBe(AVAILABLE_MODELS[1].id);
    expect(result.current.profile.onboardingComplete).toBe(true);
  });

  it("persists to localStorage and rehydrates on a fresh hook", () => {
    const { result, unmount } = renderHook(() => useProfile());
    act(() => {
      result.current.setName("Sam");
      result.current.completeOnboarding();
    });
    unmount();

    const { result: result2 } = renderHook(() => useProfile());
    expect(result2.current.profile.name).toBe("Sam");
    expect(result2.current.profile.onboardingComplete).toBe(true);
  });

  it("resetOnboarding clears the profile", () => {
    const { result } = renderHook(() => useProfile());
    act(() => {
      result.current.setName("Sam");
      result.current.completeOnboarding();
    });
    act(() => {
      result.current.resetOnboarding();
    });
    expect(result.current.profile.onboardingComplete).toBe(false);
    expect(result.current.profile.name).toBe("");
  });
});
