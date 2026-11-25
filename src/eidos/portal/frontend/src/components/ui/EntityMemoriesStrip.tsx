import * as React from "react";
import {
  Brain,
  CheckCircle2,
  AlertTriangle,
  Clock,
  HelpCircle,
  ArrowRight,
} from "lucide-react";
import { requestSpecNavigation } from "@/lib/specNav";
import {
  formatDisplayTitle,
  formatHumanProse,
  formatMemoryFreshness,
  formatMemoryFreshnessReason,
  useEntityLabels,
} from "@/lib/presentation";
import type { PortalMemory } from "@/types";

export interface EntityMemoriesStripProps {
  entityId?: string | null;
  memories?: PortalMemory[];
  onBeforeNavigate?: () => void;
  compact?: boolean;
  headingLevel?: "h3" | "h4" | "h5";
}

export function getAnchoredActiveMemories(
  memories: PortalMemory[] | undefined,
  entityId?: string | null
): PortalMemory[] {
  const target = String(entityId || "").trim().toUpperCase();
  if (!target || !Array.isArray(memories) || memories.length === 0) {
    return [];
  }
  return memories.filter((mem) => {
    if (!mem || mem.is_archived || mem.status !== "active") return false;
    const entities = mem.targets?.entities || [];
    return entities.some((e) => String(e || "").trim().toUpperCase() === target);
  });
}

function FreshnessIcon({ freshness }: { freshness?: string }) {
  switch ((freshness || "fresh").toLowerCase()) {
    case "stale":
      return <AlertTriangle aria-hidden="true" className="h-3 w-3 text-amber-500 shrink-0" />;
    case "expired":
      return <Clock aria-hidden="true" className="h-3 w-3 text-rose-500 shrink-0" />;
    case "unverifiable":
      return <HelpCircle aria-hidden="true" className="h-3 w-3 text-slate-400 shrink-0" />;
    default:
      return <CheckCircle2 aria-hidden="true" className="h-3 w-3 text-emerald-500 shrink-0" />;
  }
}

export function EntityMemoriesStrip({
  entityId,
  memories,
  onBeforeNavigate,
  compact = false,
  headingLevel = "h3",
}: EntityMemoriesStripProps) {
  const labelIndex = useEntityLabels();
  const matched = React.useMemo(
    () => getAnchoredActiveMemories(memories, entityId),
    [memories, entityId]
  );

  if (matched.length === 0) return null;
  const HeadingTag = headingLevel;

  return (
    <div
      data-entity-memories-strip="true"
      className={compact ? "pt-1.5 border-t border-border/50 space-y-1.5" : "space-y-2"}
    >
      <HeadingTag
        className={
          compact
            ? "text-[10px] font-bold text-teal-500 uppercase tracking-wider flex items-center gap-1"
            : "text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5"
        }
      >
        <Brain aria-hidden="true" className={compact ? "h-3 w-3" : "h-4 w-4 text-teal-500"} />
        <span>Team Memories ({matched.length})</span>
      </HeadingTag>
      <div className="flex flex-wrap gap-1.5">
        {matched.map((mem) => {
          const title = formatHumanProse(formatDisplayTitle(mem.title, mem.id), labelIndex);
          const freshnessLabel = formatMemoryFreshness(mem.freshness);
          const reason = formatMemoryFreshnessReason(mem.freshness_reason, labelIndex);
          const tooltip = reason ? `${title} — ${freshnessLabel}: ${reason}` : `${title} (${freshnessLabel})`;
          return (
            <button
              key={mem.id}
              type="button"
              data-memory-ref={mem.id}
              onClick={() => {
                onBeforeNavigate?.();
                requestSpecNavigation(mem.id);
              }}
              title={tooltip}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-teal-500/10 hover:bg-teal-500/20 text-foreground border border-teal-500/25 text-[11px] font-medium transition-colors cursor-pointer max-w-full"
            >
              <FreshnessIcon freshness={mem.freshness} />
              <span className="truncate max-w-[260px]">{title}</span>
              <span className="text-[10px] text-muted-foreground shrink-0">
                {freshnessLabel}
              </span>
              <ArrowRight aria-hidden="true" className="h-3 w-3 text-teal-500 shrink-0" />
            </button>
          );
        })}
      </div>
    </div>
  );
}

