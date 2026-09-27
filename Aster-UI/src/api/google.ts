import { asterGet, asterPost } from "./client";
import type { GoogleAccessTier, GoogleStatus } from "../types";

export async function fetchGoogleStatus(): Promise<GoogleStatus> {
  return asterGet<GoogleStatus>("/api/google/status");
}

export async function connectGoogleAccount(): Promise<{ ok: boolean }> {
  return asterPost<{ ok: boolean }>("/api/google/connect");
}

export async function setGoogleTier(
  tier: GoogleAccessTier,
): Promise<{ ok: boolean; tier: GoogleAccessTier; needs_reconnect: boolean }> {
  return asterPost<{ ok: boolean; tier: GoogleAccessTier; needs_reconnect: boolean }>(
    "/api/google/tier",
    { tier },
  );
}
