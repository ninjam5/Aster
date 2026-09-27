import type { ActivityEntry } from "../types";

export const MOCK_ACTIVITY_LOG: ActivityEntry[] = [
  { id: "1", timestamp: "09:05:12", level: "info", message: "Tool executed: play_spotify_track(\"lo-fi focus mix\")" },
  { id: "2", timestamp: "09:02:48", level: "info", message: "Admin brain turn completed in 1.4s" },
  { id: "3", timestamp: "08:58:03", level: "warn", message: "Discord rate limit nearing threshold for #general" },
  { id: "4", timestamp: "08:40:55", level: "info", message: "Awareness daemon: ambient scan complete, mood=focused" },
  { id: "5", timestamp: "08:12:21", level: "error", message: "Spotify token refresh failed, retried successfully" },
  { id: "6", timestamp: "07:31:09", level: "info", message: "Wake word detected: \"hey aster\"" },
];

// Extra lines the Activity Log panel cycles through to simulate a live stream
// (purely client-side — no network calls). Timestamp/id are generated fresh
// each time one is appended; see ActivityLog.tsx.
export const STREAM_POOL: Pick<ActivityEntry, "level" | "message">[] = [
  { level: "info", message: "Vision: webcam frame analyzed, no action needed" },
  { level: "info", message: "Memory: recalled 3 facts for current context" },
  { level: "warn", message: "Focus Mode: distraction pattern detected, nudging gently" },
  { level: "info", message: "Telegram: heartbeat ping acknowledged" },
  { level: "info", message: "Sentry Mode: scan complete, no unrecognized faces" },
];
