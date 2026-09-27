import type { DashboardStat, RecentActivityEntry } from "../types";

export const DASHBOARD_STATS: DashboardStat[] = [
  { id: "status", label: "Aster status", value: "Online", hint: "Idle — listening for the wake word" },
  { id: "memories", label: "Remembered facts", value: "128", hint: "Across all conversations" },
  { id: "socials", label: "Connected services", value: "2 / 3", hint: "Spotify, Discord connected" },
  { id: "uptime", label: "Uptime", value: "3h 42m", hint: "Since last restart" },
];

export const RECENT_ACTIVITY: RecentActivityEntry[] = [
  { id: "1", label: "Played \"Lo-fi Focus Mix\" on Spotify", timestamp: "2 min ago" },
  { id: "2", label: "Remembered: \"Prefers dark mode everywhere\"", timestamp: "18 min ago" },
  { id: "3", label: "Replied to a Discord DM from Tiger", timestamp: "1 hour ago" },
  { id: "4", label: "Set a reminder for \"Team standup\"", timestamp: "3 hours ago" },
];
