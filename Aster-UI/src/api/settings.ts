import { asterGet, asterPost } from "./client";
import { ASTER_API_BASE } from "./config";
import type { Persona, KokoroVoice, LlmSettings, PersonaFormAnswers, Preset } from "../types";

// ── Persona ──────────────────────────────────────────────────────────────────

export async function fetchPersonas(): Promise<{ active: string; personas: Persona[] }> {
  return asterGet<{ active: string; personas: Persona[] }>("/api/persona");
}

export async function selectPersona(id: string): Promise<void> {
  await asterPost("/api/persona/select", { id });
}

export async function createPersona(name: string, body: string): Promise<{ id: string }> {
  return asterPost<{ id: string }>("/api/persona/create", { name, body });
}

export async function generatePersona(
  name: string,
  answers: PersonaFormAnswers,
): Promise<{ id: string; body: string }> {
  return asterPost<{ id: string; body: string }>("/api/persona/generate", { name, answers });
}

export async function deletePersona(id: string): Promise<void> {
  const res = await fetch(`${ASTER_API_BASE}/api/persona/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
}

// ── Voice ────────────────────────────────────────────────────────────────────

export async function fetchVoices(): Promise<{ active: string; voices: KokoroVoice[] }> {
  return asterGet<{ active: string; voices: KokoroVoice[] }>("/api/voices");
}

export async function selectVoice(voice: string): Promise<void> {
  await asterPost("/api/voice/select", { voice });
}

/** Synthesise a short WAV sample for the given voice and return an object URL
 *  suitable for playing via the Web Audio API. Remember to URL.revokeObjectURL
 *  after the audio finishes to avoid memory leaks. */
export async function previewVoice(voice: string): Promise<string> {
  const res = await fetch(`${ASTER_API_BASE}/api/voice/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ voice }),
  });
  if (!res.ok) throw new Error(await res.text());
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// ── Presets ──────────────────────────────────────────────────────────────────

export async function fetchPresets(): Promise<{ presets: Preset[] }> {
  return asterGet<{ presets: Preset[] }>("/api/presets");
}

export async function createPreset(
  name: string,
  persona: string,
  voice: string,
): Promise<{ id: string }> {
  return asterPost<{ id: string }>("/api/presets", { name, persona, voice });
}

export async function selectPreset(
  id: string,
): Promise<{ persona: string; voice: string }> {
  return asterPost<{ persona: string; voice: string }>("/api/preset/select", { id });
}

export async function deletePreset(id: string): Promise<void> {
  const res = await fetch(`${ASTER_API_BASE}/api/presets/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
}

// ── LLM Settings ─────────────────────────────────────────────────────────────

export async function fetchLlmSettings(): Promise<LlmSettings> {
  return asterGet<LlmSettings>("/api/llm-settings");
}

export async function updateLlmSettings(
  patch: Partial<LlmSettings>,
): Promise<{ ok: boolean; restart_required: boolean }> {
  return asterPost<{ ok: boolean; restart_required: boolean }>("/api/llm-settings", patch);
}
