import { useCallback, useEffect, useState } from "react";
import type { AsterModel, TechLevel, UserProfile } from "../types";

const STORAGE_KEY = "aster.profile.v1";

const DEFAULT_PROFILE: UserProfile = {
  name: "",
  techLevel: "basic",
  model: null,
  onboardingComplete: false,
};

function loadProfile(): UserProfile {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PROFILE;
    const parsed = JSON.parse(raw) as Partial<UserProfile>;
    return { ...DEFAULT_PROFILE, ...parsed };
  } catch {
    return DEFAULT_PROFILE;
  }
}

function persistProfile(profile: UserProfile) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
  } catch {
    // localStorage unavailable (private mode, etc.) — fail silently, app still works in-session.
  }
}

export interface UseProfileResult {
  profile: UserProfile;
  setName: (name: string) => void;
  setTechLevel: (techLevel: TechLevel) => void;
  setModel: (model: AsterModel) => void;
  completeOnboarding: () => void;
  resetOnboarding: () => void;
}

export function useProfile(): UseProfileResult {
  const [profile, setProfile] = useState<UserProfile>(loadProfile);

  useEffect(() => {
    persistProfile(profile);
  }, [profile]);

  const setName = useCallback((name: string) => {
    setProfile((prev) => ({ ...prev, name }));
  }, []);

  const setTechLevel = useCallback((techLevel: TechLevel) => {
    setProfile((prev) => ({ ...prev, techLevel }));
  }, []);

  const setModel = useCallback((model: AsterModel) => {
    setProfile((prev) => ({ ...prev, model }));
  }, []);

  const completeOnboarding = useCallback(() => {
    setProfile((prev) => ({ ...prev, onboardingComplete: true }));
  }, []);

  const resetOnboarding = useCallback(() => {
    setProfile(DEFAULT_PROFILE);
  }, []);

  return { profile, setName, setTechLevel, setModel, completeOnboarding, resetOnboarding };
}
