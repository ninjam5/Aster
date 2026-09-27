import type { ChatMessage } from "../types";

export const MOCK_CHAT: ChatMessage[] = [
  { id: "1", role: "user", text: "Hey Aster, what's on my calendar today?", timestamp: "9:02 AM" },
  {
    id: "2",
    role: "aster",
    text: "Good morning! You don't have any calendar integrations connected yet, but I can set reminders for you in the meantime.",
    timestamp: "9:02 AM",
  },
  { id: "3", role: "user", text: "Play something chill while I work", timestamp: "9:05 AM" },
  { id: "4", role: "aster", text: "Playing \"Lo-fi Focus Mix\" on Spotify now.", timestamp: "9:05 AM" },
];
