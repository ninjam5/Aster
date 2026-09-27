import type { HTMLAttributes, ReactNode } from "react";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  /** Adds a gentle hover lift + shadow for cards that represent a clickable/toggle-able row. */
  interactive?: boolean;
}

export function Card({ className = "", interactive = false, children, ...rest }: CardProps) {
  return (
    <div
      className={`rounded-card border border-outline-custom bg-surface-card p-4 ${
        interactive
          ? "transition-all duration-200 hover:-translate-y-0.5 hover:border-accent-blue/40 hover:shadow-lg hover:shadow-black/20"
          : ""
      } ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}
