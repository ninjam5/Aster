import { AVAILABLE_MODELS, AUTO_DETECTED_MODEL } from "./models";
import { DASHBOARD_STATS, RECENT_ACTIVITY } from "./dashboard";
import { MOCK_CHAT } from "./chat";
import { MOCK_MEMORIES } from "./memories";
import { MOCK_SOCIALS } from "./socials";
import { MOCK_SKILLS } from "./skills";
import { MOCK_NOTES, MOCK_REMINDERS } from "./notes";
import { MOCK_ACTIVITY_LOG } from "./activity";

describe("mock data fixtures", () => {
  it("exposes the three planned models with unique ids", () => {
    expect(AVAILABLE_MODELS).toHaveLength(3);
    const ids = AVAILABLE_MODELS.map((m) => m.id);
    expect(new Set(ids).size).toBe(3);
    expect(ids).toContain("gemma-4-e4b");
    expect(AUTO_DETECTED_MODEL).toEqual(AVAILABLE_MODELS[0]);
  });

  it("every fixture is non-empty and uses unique ids", () => {
    const collections = [
      DASHBOARD_STATS,
      RECENT_ACTIVITY,
      MOCK_CHAT,
      MOCK_MEMORIES,
      MOCK_SOCIALS,
      MOCK_SKILLS,
      MOCK_NOTES,
      MOCK_REMINDERS,
      MOCK_ACTIVITY_LOG,
    ];
    for (const c of collections) {
      expect(c.length).toBeGreaterThan(0);
      const ids = c.map((item) => item.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it("socials cover spotify, discord and telegram", () => {
    expect(MOCK_SOCIALS.map((s) => s.id).sort()).toEqual(["discord", "spotify", "telegram"]);
  });

  it("chat roles are limited to user/aster", () => {
    for (const msg of MOCK_CHAT) {
      expect(["user", "aster"]).toContain(msg.role);
    }
  });
});
