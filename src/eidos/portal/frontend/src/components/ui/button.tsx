import * as React from "react";
import { cn } from "@/lib/utils";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "outline" | "ghost" | "tonal" | "danger";
  size?: "sm" | "md" | "lg" | "icon";
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", ...props }, ref) => {
    const variants = {
      primary:
        "bg-primary text-primary-foreground hover:brightness-105 active:brightness-95 md-elevation-1 hover:md-elevation-2",
      secondary:
        "bg-secondary text-secondary-foreground hover:brightness-105 active:brightness-95 md-elevation-1",
      tonal:
        "bg-[var(--md-primary-container)] text-[var(--md-on-primary-container)] hover:brightness-95 active:brightness-90",
      outline:
        "border border-border bg-transparent hover:bg-muted text-foreground",
      ghost:
        "bg-transparent hover:bg-muted text-foreground",
      danger:
        "bg-destructive text-destructive-foreground hover:brightness-105 active:brightness-95",
    };

    const sizes = {
      sm: "h-8 px-3 text-xs rounded-full",
      md: "h-9 px-4 text-sm rounded-full",
      lg: "h-11 px-6 text-base rounded-full",
      icon: "h-9 w-9 p-0 rounded-full flex items-center justify-center",
    };

    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:pointer-events-none md-state-layer cursor-pointer select-none",
          variants[variant],
          sizes[size],
          className
        )}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";
