import {
  Home,
  Mic,
  Brain,
  Share2,
  Wrench,
  StickyNote,
  Terminal,
  Settings as SettingsIcon,
  Sparkles,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { NavSection, SectionId, UserProfile } from "../types";
import { NavItem } from "../components/ui/NavItem";

const NAV_SECTIONS: (NavSection & { icon: LucideIcon })[] = [
  { id: "dashboard", label: "Home", icon: Home },
  { id: "voice-chat", label: "Voice & Chat", icon: Mic },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "socials", label: "Connected Socials", icon: Share2 },
  { id: "skills", label: "Skills & Automation", icon: Wrench },
  { id: "notes", label: "Notes & Reminders", icon: StickyNote },
  { id: "activity-log", label: "Activity Log", icon: Terminal },
  { id: "settings", label: "Settings", icon: SettingsIcon },
];

interface SidebarProps {
  profile: UserProfile;
  active: SectionId;
  onSelect: (id: SectionId) => void;
}

export function Sidebar({ profile, active, onSelect }: SidebarProps) {
  return (
    <aside className="flex h-full flex-col border-r border-outline-custom bg-surface-card">
      <div className="flex items-center gap-2 px-4 pb-4 pt-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-accent-blue/15">
          <Sparkles size={18} className="text-primary" />
        </div>
        <span className="font-headline-md text-headline-md text-on-surface">Aster</span>
      </div>

      <nav className="aster-scroll flex-1 space-y-1 overflow-y-auto px-3 py-2">
        {NAV_SECTIONS.map((section) => (
          <NavItem
            key={section.id}
            icon={section.icon}
            label={section.label}
            active={active === section.id}
            onClick={() => onSelect(section.id)}
          />
        ))}
      </nav>

      <div className="m-3 flex items-center gap-3 rounded-input bg-surface-elevated p-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-blue/20 text-sm font-semibold text-primary">
          {profile.name ? profile.name[0]!.toUpperCase() : "?"}
        </div>
        <div className="min-w-0">
          <p className="truncate text-body-md text-on-surface">{profile.name || "Friend"}</p>
          <p className="truncate text-xs capitalize text-on-surface-variant/60">
            {profile.techLevel} user
          </p>
        </div>
      </div>
    </aside>
  );
}

export { NAV_SECTIONS };
