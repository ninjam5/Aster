import type { SocialConnection } from "../types";

export const MOCK_SOCIALS: SocialConnection[] = [
  {
    id: "spotify",
    name: "Spotify",
    description: "Play music, control playback, manage playlists",
    connected: true,
    detail: "Connected as mohamed_s",
  },
  {
    id: "discord",
    name: "Discord",
    description: "Let Aster reply to your friends' DMs on your behalf",
    connected: true,
    detail: "9 contacts whitelisted",
  },
  {
    id: "telegram",
    name: "Telegram",
    description: "Chat with Aster remotely, get alerts and voice notes",
    connected: false,
  },
];
