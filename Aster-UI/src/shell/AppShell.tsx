import { useState } from "react";
import { Sidebar } from "./Sidebar";
import { BodyRouter } from "./BodyRouter";
import { AsterMoodProvider } from "../character/AsterMoodProvider";
import { AsterCompanion } from "../character/AsterCompanion";
import type { SectionId, TechLevel, UserProfile } from "../types";

interface AppShellProps {
  profile: UserProfile;
  onUpdateName: (name: string) => void;
  onUpdateTechLevel: (techLevel: TechLevel) => void;
  onUpdateModelId: (modelId: string) => void;
  onResetOnboarding: () => void;
}

export function AppShell({
  profile,
  onUpdateName,
  onUpdateTechLevel,
  onUpdateModelId,
  onResetOnboarding,
}: AppShellProps) {
  const [active, setActive] = useState<SectionId>("dashboard");

  return (
    <AsterMoodProvider>
      <div className="grid h-full grid-cols-[minmax(220px,1fr)_3fr] bg-background">
        <Sidebar profile={profile} active={active} onSelect={setActive} />
        <BodyRouter
          active={active}
          profile={profile}
          onUpdateName={onUpdateName}
          onUpdateTechLevel={onUpdateTechLevel}
          onUpdateModelId={onUpdateModelId}
          onResetOnboarding={onResetOnboarding}
        />
      </div>
      {/* The companion follows you everywhere except home (the hero scene owns
          that page). Keyed by section so each page change replays his entrance
          choreography at that page's spot. */}
      {active !== "dashboard" && <AsterCompanion key={active} section={active} />}
    </AsterMoodProvider>
  );
}
