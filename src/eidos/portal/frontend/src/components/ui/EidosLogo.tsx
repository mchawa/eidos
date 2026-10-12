import * as React from "react";
import { cn } from "@/lib/utils";

export interface EidosLogoProps extends React.SVGProps<SVGSVGElement> {
  className?: string;
  size?: number | string;
}

export function EidosLogo({ className, size = 26, ...props }: EidosLogoProps) {
  const idPrefix = React.useId().replace(/:/g, "");
  const gradId = `eidos-grad-${idPrefix}`;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      fill="none"
      width={size}
      height={size}
      className={cn("shrink-0 select-none", className)}
      aria-label="Eidos Logo"
      role="img"
      {...props}
    >
      <defs>
        <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="50%" stopColor="#6366f1" />
          <stop offset="100%" stopColor="#a855f7" />
        </linearGradient>
      </defs>

      {/* The Eidos Form — Single continuous isometric impossible polygon skeleton */}
      <path
        d="M 16,3 L 27.3,9.5 L 19,14.2 L 27.3,22.5 L 16,29 L 16,19.5 L 4.7,22.5 L 4.7,9.5 L 13,14.3 Z"
        stroke={`url(#${gradId})`}
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill={`url(#${gradId})`}
        fillOpacity="0.14"
      />
    </svg>
  );
}
