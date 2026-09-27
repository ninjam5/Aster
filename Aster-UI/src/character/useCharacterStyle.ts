/**
 * useCharacterStyle — which renderer draws Aster around the app:
 * "pixel" (8-bit canvas sprites with choreographed transitions) or
 * "cartoon" (the original layered-PNG puppet). Persisted to localStorage;
 * a window event keeps every consumer (companion + Settings) in sync
 * within the tab.
 */
import { useEffect, useState } from "react";

export type CharacterStyle = "pixel" | "cartoon";

const STORAGE_KEY = "aster.characterStyle.v1";
const CHANGE_EVENT = "aster-character-style";

export function readCharacterStyle(): CharacterStyle {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw === "cartoon" ? "cartoon" : "pixel";
  } catch {
    return "pixel";
  }
}

export function writeCharacterStyle(style: CharacterStyle): void {
  try {
    localStorage.setItem(STORAGE_KEY, style);
  } catch {
    /* storage unavailable — style just won't persist */
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

export function useCharacterStyle(): [CharacterStyle, (style: CharacterStyle) => void] {
  const [style, setStyle] = useState<CharacterStyle>(readCharacterStyle);

  useEffect(() => {
    const sync = () => setStyle(readCharacterStyle());
    window.addEventListener(CHANGE_EVENT, sync);
    window.addEventListener("storage", sync); // cross-tab
    return () => {
      window.removeEventListener(CHANGE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  return [style, writeCharacterStyle];
}
