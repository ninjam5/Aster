import type { LucideIcon } from "lucide-react";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
}

export function EmptyState({ icon: Icon, title, description }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-outline-custom px-6 py-12 text-center">
      <Icon size={32} className="text-on-surface-variant/50" />
      <p className="font-label-lg text-label-lg text-on-surface">{title}</p>
      {description && (
        <p className="max-w-sm text-body-md text-on-surface-variant/70">{description}</p>
      )}
    </div>
  );
}
