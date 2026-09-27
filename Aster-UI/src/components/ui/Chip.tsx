import type { ReactNode } from "react";

interface ChipProps {
  children: ReactNode;
  tone?: "neutral" | "accent" | "success" | "warning";
}

const toneClasses: Record<NonNullable<ChipProps["tone"]>, string> = {
  neutral: "bg-surface-elevated text-on-surface-variant",
  accent: "bg-accent-blue/20 text-primary",
  success: "bg-emerald-500/15 text-emerald-300",
  warning: "bg-amber-500/15 text-amber-300",
};

export function Chip({ children, tone = "neutral" }: ChipProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${toneClasses[tone]}`}
    >
      {children}
    </span>
  );
}
