import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { easing } from "../../theme/motion";

interface NavItemProps {
  icon: LucideIcon;
  label: string;
  active: boolean;
  onClick: () => void;
}

export function NavItem({ icon: Icon, label, active, onClick }: NavItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative flex w-full items-center gap-3 rounded-input px-3 py-2.5 text-left font-label-lg text-label-lg transition-colors duration-150 ${
        active
          ? "bg-accent-blue/15 text-on-surface"
          : "text-on-surface-variant hover:bg-surface-elevated hover:text-on-surface"
      }`}
    >
      {active && (
        <motion.span
          layoutId="nav-active-indicator"
          transition={{ duration: 0.25, ease: easing }}
          className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-accent-blue"
        />
      )}
      <Icon size={18} className={active ? "text-primary" : ""} />
      <span>{label}</span>
    </button>
  );
}
