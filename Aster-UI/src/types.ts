// Shared types for the Aster desktop app.
// Everything here is intentionally backend-agnostic — when a real API replaces
// the mock data in `src/mock/`, these are the shapes it should return.

export type TechLevel = "basic" | "advanced";

export interface AsterModel {
  id: string;
  name: string;
  description: string;
}

export interface UserProfile {
  name: string;
  techLevel: TechLevel;
  model: AsterModel | null;
  onboardingComplete: boolean;
}

export type SectionId =
  | "dashboard"
  | "voice-chat"
  | "memory"
  | "socials"
  | "skills"
  | "notes"
  | "activity-log"
  | "settings";

export interface NavSection {
  id: SectionId;
  label: string;
}

// ── Dashboard ───────────────────────────────────────────────────────────────

export interface DashboardStat {
  id: string;
  label: string;
  value: string;
  hint?: string;
}

export interface RecentActivityEntry {
  id: string;
  label: string;
  timestamp: string;
}

// ── Voice & Chat ────────────────────────────────────────────────────────────

export interface ChatMessage {
  id: string;
  role: "user" | "aster";
  text: string;
  timestamp: string;
}

// ── Memory ──────────────────────────────────────────────────────────────────

export interface MemoryFact {
  id: string;
  text: string;
  savedAt: string;
  source: "conversation" | "discord" | "manual";
}

// ── Connected Socials ───────────────────────────────────────────────────────

export type SocialPlatform = "spotify" | "discord" | "telegram";

export interface SocialConnection {
  id: SocialPlatform;
  name: string;
  description: string;
  connected: boolean;
  detail?: string;
}

// ── Google (Gmail + Calendar) ───────────────────────────────────────────────

export type GoogleAccessTier = "limited" | "partial" | "autonomous";

export interface GoogleStatus {
  configured: boolean;
  connected: boolean;
  needs_reconnect: boolean;
  tier: GoogleAccessTier;
  reauth_needed: boolean;
  unread_count: number;
}

// ── Skills & Automation ─────────────────────────────────────────────────────

export interface Skill {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
}

// ── Notes & Reminders ────────────────────────────────────────────────────────

export interface Note {
  id: string;
  text: string;
  createdAt: string;
}

export interface Reminder {
  id: string;
  label: string;
  time: string;
  kind: "timer" | "alarm";
}

// ── Activity Log ────────────────────────────────────────────────────────────

export interface ActivityEntry {
  id: string;
  timestamp: string;
  level: "info" | "warn" | "error";
  message: string;
}

// ── Persona ──────────────────────────────────────────────────────────────────

export interface Persona {
  id: string;
  name: string;
  builtin: boolean;
}

// ── Voice ────────────────────────────────────────────────────────────────────

export interface KokoroVoice {
  id: string;
  label: string;
  gender?: "male" | "female";
  accent?: "American" | "British";
  /** True for user-provided .pt clones from Aster_Vault/TTS-Voices/ */
  custom?: boolean;
  /** Absolute path on disk (returned by backend for custom voices only) */
  path?: string;
}

// ── LLM Settings ─────────────────────────────────────────────────────────────

export interface LlmSettings {
  temperature: number;
  top_p: number;
  top_k: number;
  context_window: number;
  kv_cache_type: string;
}

// ── Persona builder form ──────────────────────────────────────────────────────

export interface PersonaFormAnswers {
  role_description: string;
  communication_style: string;
  quirks: string;
  formality: "butler" | "buddy" | "neutral";
  gender: "male" | "female" | "neutral";
}

// ── Voice + Persona Presets ───────────────────────────────────────────────────

export interface Preset {
  id: string;
  name: string;
  persona: string;
  voice: string;
  builtin: boolean;
}
