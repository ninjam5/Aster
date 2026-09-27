import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "ghost" | "outline" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  children: ReactNode;
}

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-accent-blue text-white hover:opacity-100 opacity-90 disabled:opacity-50",
  ghost:
    "bg-transparent text-on-surface-variant hover:text-on-surface hover:bg-surface-elevated",
  outline:
    "bg-transparent border border-outline-custom text-on-surface hover:bg-surface-elevated",
  danger:
    "bg-error-container text-error-text hover:opacity-100 opacity-90",
};

export function Button({
  variant = "primary",
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`h-button_height inline-flex items-center justify-center gap-2 rounded-input px-5 font-label-lg text-label-lg font-medium transition-all duration-200 hover:scale-[1.015] active:scale-[0.97] disabled:cursor-not-allowed disabled:hover:scale-100 ${variantClasses[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
