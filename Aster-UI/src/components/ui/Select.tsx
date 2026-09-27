import { ChevronDown } from "lucide-react";
import type { SelectHTMLAttributes } from "react";

export function Select({ className = "", children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select
        className={`h-touch_target w-full appearance-none rounded-input border-none bg-surface-elevated px-4 pr-10 text-on-surface outline-none transition-all focus:ring-2 focus:ring-accent-blue/50 ${className}`}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown
        size={18}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
      />
    </div>
  );
}
