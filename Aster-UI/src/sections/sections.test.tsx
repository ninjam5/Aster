import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach } from "vitest";
import { Dashboard } from "./Dashboard";
import { VoiceChat } from "./VoiceChat";
import { Memory } from "./Memory";
import { Socials } from "./Socials";
import { Skills } from "./Skills";
import { Notes } from "./Notes";
import { ActivityLog } from "./ActivityLog";
import { Settings } from "./Settings";
import { ChatSessionProvider } from "../state/useChatSession";
import { AVAILABLE_MODELS } from "../mock/models";
import type { UserProfile } from "../types";

// Mock livekit so VoiceChat call buttons work without a real LiveKit server.
// useLiveKitCall now accepts a roomRef parameter — the mock ignores it.
vi.mock("../api/useLiveKitCall", () => ({
  useLiveKitCall: () => ({
    startCall: vi.fn().mockImplementation((onStateChange: (v: boolean) => void) => {
      onStateChange(true);
      return Promise.resolve();
    }),
    endCall: vi.fn().mockImplementation((onStateChange: (v: boolean) => void) => {
      onStateChange(false);
      return Promise.resolve();
    }),
    setMuted: vi.fn().mockResolvedValue(undefined),
  }),
}));

// Mock the SSE chat stream so VoiceChat send delivers a synchronous reply.
vi.mock("../api/useChatStream", () => ({
  useChatStream: () => ({
    sendMessage: vi.fn().mockImplementation(
      (_text: string, onChunk: (c: string) => void, onDone: () => void) => {
        onChunk("Mocked ");
        onChunk("reply from Aster.");
        onDone();
        return Promise.resolve();
      },
    ),
  }),
}));

const MOCK_SKILLS_RESPONSE = [
  { id: "vision",       name: "Vision",          description: "Always on",        enabled: true  },
  { id: "intervention", name: "Focus Mode",       description: "Focus mode",       enabled: false },
  { id: "awareness",    name: "Awareness",        description: "Ambient check-ins",enabled: true  },
  { id: "gesture",      name: "Gesture Control",  description: "Gestures",         enabled: false },
  { id: "sentry",       name: "Sentry Mode",      description: "Sentry",           enabled: false },
];

const MOCK_MEMORIES_RESPONSE = [
  { id: "mem-0", text: "Prefers dark mode everywhere", savedAt: "2024-01-15 14:23", source: "conversation" },
  { id: "mem-1", text: "Birthday is in October",       savedAt: "2024-01-10 09:00", source: "conversation" },
];

const MOCK_NOTES_RESPONSE = [
  { id: "note-0", text: "Look into a standing desk", createdAt: "2024-01-15 14:23" },
];

const MOCK_SOCIALS_RESPONSE = [
  { id: "spotify",  name: "Spotify",  description: "Music",  connected: true,  detail: "Mohamed" },
  { id: "discord",  name: "Discord",  description: "DMs",    connected: true,  detail: null      },
  { id: "telegram", name: "Telegram", description: "Remote", connected: false, detail: null      },
];

function makeFetchMock() {
  return vi.fn().mockImplementation((url: string, options?: RequestInit) => {
    const path = String(url).replace("http://127.0.0.1:8000", "").split("?")[0];
    const query = String(url).includes("?q=") ? decodeURIComponent(String(url).split("?q=")[1] ?? "") : "";
    const method = (options?.method ?? "GET").toUpperCase();

    if (path === "/api/stats")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "online", memory_count: 42, uptime_seconds: 3600, socials: { spotify: true, discord: true, telegram: false } }) });

    if (path === "/api/memory" && method === "GET") {
      const filtered = query ? MOCK_MEMORIES_RESPONSE.filter((m) => m.text.toLowerCase().includes(query.toLowerCase())) : MOCK_MEMORIES_RESPONSE;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(filtered) });
    }
    if (path === "/api/memory" && method === "POST")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, id: "mem-new" }) });

    if (path === "/api/skills" && method === "GET")
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_SKILLS_RESPONSE) });
    if (path.startsWith("/api/skills/") && method === "PATCH") {
      const skillId = path.split("/")[3];
      const body = JSON.parse(options?.body as string);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ id: skillId, enabled: body.enabled }) });
    }

    if (path === "/api/notes" && method === "GET")
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_NOTES_RESPONSE) });
    if (path === "/api/notes" && method === "POST")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, id: "note-new" }) });

    if (path === "/api/socials")
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_SOCIALS_RESPONSE) });

    if (path === "/api/persona")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ active: "jarvis", personas: [{ id: "jarvis", name: "Jarvis", builtin: true }, { id: "gogi", name: "Gogi", builtin: true }] }) });
    if (path === "/api/voices")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ active: "af_bella", voices: [{ id: "af_bella", label: "Bella", gender: "female", accent: "American" }] }) });
    if (path === "/api/llm-settings")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ temperature: 1.0, top_p: 0.95, top_k: 64, context_window: 60000, kv_cache_type: "kvarn4" }) });
    if (path === "/api/presets")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ presets: [{ id: "jarvis_default", name: "Jarvis — British Butler", persona: "jarvis", voice: "bm_george", builtin: true }] }) });

    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  });
}

// Minimal WebSocket stub — no messages ever arrive; onclose is never called.
class MockWebSocket {
  onmessage: null = null;
  onclose: (() => void) | null = null;
  close() {}
}

const profile: UserProfile = {
  name: "Mohamed",
  techLevel: "advanced",
  model: AVAILABLE_MODELS[0],
  onboardingComplete: true,
};

describe("section panels render with mock data", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", makeFetchMock());
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  it("Dashboard greets the user by name", () => {
    render(<Dashboard profile={profile} />);
    expect(screen.getByText("Welcome back, Mohamed")).toBeInTheDocument();
    expect(screen.getByText("Recent activity")).toBeInTheDocument();
  });

  it("VoiceChat toggles call state", async () => {
    render(<ChatSessionProvider><VoiceChat /></ChatSessionProvider>);
    expect(screen.getByText("Not in a call")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Start Call/ }));
    expect(screen.getByText("On a call with Aster")).toBeInTheDocument();
  });

  it("VoiceChat appends a message and an Aster reply on send", async () => {
    render(<ChatSessionProvider><VoiceChat /></ChatSessionProvider>);
    await userEvent.type(screen.getByPlaceholderText(/Message Aster/), "remind me at 5");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByText("remind me at 5")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Mocked reply from Aster/)).toBeInTheDocument());
  });

  it("Memory filters facts by search query", async () => {
    render(<Memory />);
    await waitFor(() => expect(screen.getByText("Prefers dark mode everywhere")).toBeInTheDocument());
    await userEvent.type(screen.getByPlaceholderText("Search memories…"), "birthday");
    await waitFor(() =>
      expect(screen.queryByText("Prefers dark mode everywhere")).not.toBeInTheDocument(),
    { timeout: 1500 });
    expect(screen.getByText("Birthday is in October")).toBeInTheDocument();
  });

  it("Memory adds a new fact and forgets it again", async () => {
    render(<Memory />);
    await waitFor(() => expect(screen.getByText("Prefers dark mode everywhere")).toBeInTheDocument());
    await userEvent.type(screen.getByPlaceholderText("Teach Aster something new…"), "Likes oat milk");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("Likes oat milk")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Forget: Likes oat milk" }));
    await waitFor(() => expect(screen.queryByText("Likes oat milk")).not.toBeInTheDocument());
  });

  it("Socials shows connection status (read-only)", async () => {
    render(<Socials />);
    await waitFor(() => expect(screen.getByText("Spotify")).toBeInTheDocument());
    expect(screen.getByText("Telegram")).toBeInTheDocument();
    // No Connect/Disconnect buttons — purely read-only status display.
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disconnect" })).not.toBeInTheDocument();
  });

  it("Skills loads and shows toggleable capabilities", async () => {
    render(<Skills />);
    await waitFor(() => expect(screen.getByRole("switch", { name: "Vision" })).toBeInTheDocument());
    const visionToggle = screen.getByRole("switch", { name: "Vision" });
    expect(visionToggle).toHaveAttribute("aria-checked", "true");
    // Vision is non-togglable — clicking should not change state.
    expect(visionToggle).toBeDisabled();
  });

  it("Skills toggles a non-vision capability", async () => {
    render(<Skills />);
    await waitFor(() => expect(screen.getByRole("switch", { name: "Sentry Mode" })).toBeInTheDocument());
    const sentryToggle = screen.getByRole("switch", { name: "Sentry Mode" });
    expect(sentryToggle).toHaveAttribute("aria-checked", "false");
    await userEvent.click(sentryToggle);
    await waitFor(() => expect(sentryToggle).toHaveAttribute("aria-checked", "true"));
  });

  it("Notes lists notes and reminders", async () => {
    render(<Notes />);
    await waitFor(() => expect(screen.getByText("Look into a standing desk")).toBeInTheDocument());
    expect(screen.getByText("Upcoming")).toBeInTheDocument();
  });

  it("Notes adds a new note", async () => {
    render(<Notes />);
    await userEvent.type(screen.getByPlaceholderText("Jot something down…"), "Buy coffee beans");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByText("Buy coffee beans")).toBeInTheDocument();
  });

  it("ActivityLog shows connecting placeholder while backend is unreachable", () => {
    render(<ActivityLog />);
    expect(screen.getByText(/Connecting to Aster/)).toBeInTheDocument();
  });

  it("Settings reset requires confirmation before calling back", async () => {
    const onResetOnboarding = vi.fn();
    render(
      <Settings
        profile={profile}
        onUpdateName={vi.fn()}
        onUpdateTechLevel={vi.fn()}
        onUpdateModel={vi.fn()}
        onResetOnboarding={onResetOnboarding}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Reset onboarding" }));
    expect(onResetOnboarding).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Yes, reset onboarding" }));
    expect(onResetOnboarding).toHaveBeenCalledOnce();
  });

  it("Settings reset confirmation can be cancelled", async () => {
    const onResetOnboarding = vi.fn();
    render(
      <Settings
        profile={profile}
        onUpdateName={vi.fn()}
        onUpdateTechLevel={vi.fn()}
        onUpdateModel={vi.fn()}
        onResetOnboarding={onResetOnboarding}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Reset onboarding" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onResetOnboarding).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Yes, reset onboarding" })).not.toBeInTheDocument(),
    );
  });
});
