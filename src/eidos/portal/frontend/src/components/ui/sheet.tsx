import * as React from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
  title?: string;
  subtitle?: string;
  className?: string;
  maxWidth?: "sm" | "md" | "lg" | "xl" | "2xl" | "3xl" | "4xl" | "5xl" | "6xl" | "full";
}

export function Sheet({
  open,
  onOpenChange,
  children,
  title,
  subtitle,
  className,
  maxWidth = "5xl",
}: SheetProps) {
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && open) {
        onOpenChange(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onOpenChange]);

  // Lock body scroll when modal is open
  React.useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  if (!open) return null;

  const maxWidthClass =
    maxWidth === "sm"
      ? "max-w-sm"
      : maxWidth === "md"
      ? "max-w-md"
      : maxWidth === "lg"
      ? "max-w-lg"
      : maxWidth === "xl"
      ? "max-w-xl"
      : maxWidth === "2xl"
      ? "max-w-2xl"
      : maxWidth === "3xl"
      ? "max-w-3xl"
      : maxWidth === "4xl"
      ? "max-w-4xl"
      : maxWidth === "full"
      ? "max-w-[98vw] h-[96vh]"
      : "max-w-[94vw] 2xl:max-w-[1550px] h-[95vh]"; // expansive, roomy reading window

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 overflow-y-auto">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/75 backdrop-blur-xs transition-opacity animate-in fade-in duration-200"
        onClick={() => onOpenChange(false)}
      />

      {/* Centered Popup Dialog with Bigger Width and Height */}
      <div
        className={cn(
          "relative z-50 flex flex-col w-full max-h-[95vh] bg-card border border-border md-elevation-5 rounded-2xl shadow-2xl animate-in zoom-in-95 fade-in duration-200 overflow-hidden my-auto",
          maxWidthClass,
          className
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border/80 px-6 py-4 bg-muted/30">
          <div className="space-y-0.5 min-w-0 pr-4">
            {title && (
              <h2 className="text-base sm:text-lg font-bold text-foreground tracking-tight truncate">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="text-xs text-muted-foreground font-mono truncate">
                {subtitle}
              </p>
            )}
          </div>
          <button
            onClick={() => onOpenChange(false)}
            className="rounded-full p-2 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer shrink-0"
            aria-label="Close dialog"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 sm:px-8 py-6 space-y-6">
          {children}
        </div>
      </div>
    </div>
  );
}
