import type { Note, Reminder } from "../types";

export const MOCK_NOTES: Note[] = [
  { id: "1", text: "Look into a standing desk", createdAt: "2026-06-12 10:14" },
  { id: "2", text: "Ask Aster to summarize weekly Discord activity", createdAt: "2026-06-09 16:40" },
];

export const MOCK_REMINDERS: Reminder[] = [
  { id: "1", label: "Team standup", time: "Today, 10:00 AM", kind: "alarm" },
  { id: "2", label: "Stretch break", time: "In 22 minutes", kind: "timer" },
];
