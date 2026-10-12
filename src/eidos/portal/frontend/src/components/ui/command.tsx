import * as React from "react";
import { Search, FileText, Box, ShieldCheck, GitCommitHorizontal, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SpecItem, SpecComponent, SpecInvariant, SpecDelta } from "@/types";

export interface CommandItem {
  id: string;
  title: string;
  subtitle?: string;
  category: "components" | "invariants" | "deltas" | "specs";
  raw: any;
}

export interface CommandDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: CommandItem[];
  onSelect: (item: CommandItem) => void;
}

export function CommandDialog({
  open,
  onOpenChange,
  items,
  onSelect,
}: CommandDialogProps) {
  const [query, setQuery] = React.useState("");
  const [selectedIndex, setSelectedIndex] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50);
      setQuery("");
      setSelectedIndex(0);
    }
  }, [open]);

  // Filter items efficiently with query string
  const filteredItems = React.useMemo(() => {
    if (!query.trim()) return items.slice(0, 50);
    const q = query.toLowerCase();
    return items
      .filter(
        (it) =>
          it.id.toLowerCase().includes(q) ||
          it.title.toLowerCase().includes(q) ||
          (it.subtitle && it.subtitle.toLowerCase().includes(q))
      )
      .slice(0, 50);
  }, [items, query]);

  // Keyboard navigation
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!open) {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
          e.preventDefault();
          onOpenChange(true);
        }
        return;
      }

      if (e.key === "Escape") {
        e.preventDefault();
        onOpenChange(false);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % Math.max(1, filteredItems.length));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) =>
          prev === 0 ? Math.max(0, filteredItems.length - 1) : prev - 1
        );
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (filteredItems[selectedIndex]) {
          onSelect(filteredItems[selectedIndex]);
          onOpenChange(false);
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onOpenChange, filteredItems, selectedIndex, onSelect]);

  if (!open) return null;

  const getIcon = (category: string) => {
    switch (category) {
      case "components":
        return <Box className="h-4 w-4 text-blue-400" />;
      case "invariants":
        return <ShieldCheck className="h-4 w-4 text-emerald-400" />;
      case "deltas":
        return <GitCommitHorizontal className="h-4 w-4 text-purple-400" />;
      default:
        return <FileText className="h-4 w-4 text-muted-foreground" />;
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-16 sm:pt-24 px-4">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-xs transition-opacity animate-in fade-in"
        onClick={() => onOpenChange(false)}
      />

      {/* Spotlight Window */}
      <div className="relative z-50 w-full max-w-2xl rounded-2xl bg-card border border-border md-elevation-5 overflow-hidden animate-in zoom-in-95 duration-150 shadow-2xl">
        <div className="flex items-center px-4 border-b border-border bg-muted/20">
          <Search className="h-5 w-5 text-muted-foreground shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            placeholder="Search specifications, C4 components, invariants (CMD+K)..."
            className="w-full bg-transparent px-3 py-3.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
          <kbd className="hidden sm:inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5 text-[10px] font-mono text-muted-foreground border border-border">
            ESC
          </kbd>
        </div>

        <div className="max-h-96 overflow-y-auto p-2 space-y-1">
          {filteredItems.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              No matching specifications found for "{query}".
            </div>
          ) : (
            filteredItems.map((item, idx) => (
              <div
                key={`${item.category}-${item.id}-${idx}`}
                onClick={() => {
                  onSelect(item);
                  onOpenChange(false);
                }}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-xl cursor-pointer text-sm transition-all duration-100",
                  idx === selectedIndex
                    ? "bg-primary/15 text-primary md-state-layer"
                    : "hover:bg-muted text-foreground"
                )}
              >
                <div className="p-1 rounded-md bg-muted/40 shrink-0">
                  {getIcon(item.category)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-xs font-mono">{item.id}</span>
                    <span className="truncate text-foreground/90 font-medium">
                      {item.title}
                    </span>
                  </div>
                  {item.subtitle && (
                    <p className="text-xs text-muted-foreground truncate mt-0.5">
                      {item.subtitle}
                    </p>
                  )}
                </div>
                <span className="text-[10px] font-mono text-muted-foreground uppercase px-2 py-0.5 rounded bg-muted/60">
                  {item.category}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
