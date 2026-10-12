import * as React from "react";
import { cn } from "@/lib/utils";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "primary" | "secondary" | "success" | "warning" | "danger" | "outline";
}

export function Badge({
  className,
  variant = "default",
  ...props
}: BadgeProps) {
  const variants = {
    default:
      "bg-muted text-muted-foreground border-transparent",
    primary:
      "bg-primary/15 text-primary border-primary/20",
    secondary:
      "bg-secondary/15 text-secondary border-secondary/20",
    success:
      "bg-emerald-500/15 text-emerald-500 dark:text-emerald-400 border-emerald-500/20",
    warning:
      "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/20",
    danger:
      "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/20",
    outline:
      "bg-transparent text-foreground border-border",
  };

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold tracking-wide transition-colors uppercase font-mono",
        variants[variant],
        className
      )}
      {...props}
    />
  );
}
