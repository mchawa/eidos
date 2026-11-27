import * as React from "react";
import {
  Brain,
  CheckCircle2,
  AlertTriangle,
  Clock,
  HelpCircle,
  Archive,
  Award,
  Filter,
  ArrowRight,
  BookOpen,
  ExternalLink,
  Link2,
  Copy,
  ShieldAlert,
  Sparkles,
  Terminal,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { hasSpecBodyOrChunk } from "@/lib/specChunks";
import { requestSpecNavigation } from "@/lib/specNav";
import {
  formatDisplayTitle,
  formatHumanProse,
  formatMemoryCategory,
  formatMemoryFreshness,
  formatMemoryFreshnessReason,
  formatMemoryScopePath,
  formatStatus,
  resolveEntityTitle,
  useEntityLabels,
} from "@/lib/presentation";
import { cn } from "@/lib/utils";
import type { PortalData, PortalMemory } from "@/types";

export interface MemoryViewProps {
  data: PortalData;
  initialSelectedId?: string;
  initialSelectedNonce?: number;
  onSelectMemory?: (memoryId: string | null) => void;
  onClearSelected?: () => void;
}

export function isMemoryArchived(mem: PortalMemory): boolean {
  return Boolean(
    mem.is_archived ||
      mem.status === "archived" ||
      (mem.relative_path && mem.relative_path.replace(/\\/g, "/").includes("memory/archive/")) ||
      (mem.path && mem.path.replace(/\\/g, "/").includes("memory/archive/"))
  );
}

function getFreshnessBadge(mem: PortalMemory) {
  const archived = isMemoryArchived(mem);
  if (mem.status === "promoted") {
    return (
      <Badge variant="primary" className="flex items-center gap-1">
        <Award aria-hidden="true" className="h-3 w-3" />
        <span>Promoted</span>
      </Badge>
    );
  }
  if (archived || mem.status === "archived") {
    return (
      <Badge variant="outline" className="flex items-center gap-1 border-slate-500/30 text-slate-400">
        <Archive aria-hidden="true" className="h-3 w-3" />
        <span>Archived</span>
      </Badge>
    );
  }
  if (mem.status === "draft") {
    return (
      <Badge variant="outline" className="flex items-center gap-1">
        <Clock aria-hidden="true" className="h-3 w-3" />
        <span>Draft</span>
      </Badge>
    );
  }

  switch ((mem.freshness || "fresh").toLowerCase()) {
    case "stale":
      return (
        <Badge variant="warning" className="flex items-center gap-1">
          <AlertTriangle aria-hidden="true" className="h-3 w-3" />
          <span>{formatMemoryFreshness("stale")}</span>
        </Badge>
      );
    case "expired":
      return (
        <Badge variant="danger" className="flex items-center gap-1">
          <Clock aria-hidden="true" className="h-3 w-3" />
          <span>{formatMemoryFreshness("expired")}</span>
        </Badge>
      );
    case "unverifiable":
      return (
        <Badge variant="outline" className="flex items-center gap-1 text-muted-foreground">
          <HelpCircle aria-hidden="true" className="h-3 w-3" />
          <span>{formatMemoryFreshness("unverifiable")}</span>
        </Badge>
      );
    default:
      return (
        <Badge variant="success" className="flex items-center gap-1">
          <CheckCircle2 aria-hidden="true" className="h-3 w-3" />
          <span>{formatMemoryFreshness("fresh")}</span>
        </Badge>
      );
  }
}

function getCategoryBadge(category: string) {
  switch ((category || "feedback").toLowerCase()) {
    case "project":
      return <Badge variant="primary">{formatMemoryCategory("project")}</Badge>;
    case "reference":
      return <Badge variant="outline">{formatMemoryCategory("reference")}</Badge>;
    default:
      return <Badge variant="default">{formatMemoryCategory("feedback")}</Badge>;
  }
}

export function MemoryView({
  data,
  initialSelectedId,
  initialSelectedNonce,
  onSelectMemory,
  onClearSelected,
}: MemoryViewProps) {
  const labelIndex = useEntityLabels();
  const [selectedMemId, setSelectedMemId] = React.useState<string | null>(
    initialSelectedId || null
  );
  const [filterText, setFilterText] = React.useState<string>("");
  const [categoryFilter, setCategoryFilter] = React.useState<string>("all");
  const [stateFilter, setStateFilter] = React.useState<string>("all");

  const rawMemories = data.memories || [];
  const audit = data.memory_audit;

  React.useEffect(() => {
    if (initialSelectedId) {
      setSelectedMemId(initialSelectedId);
    }
  }, [initialSelectedId, initialSelectedNonce]);

  const selectedMem = React.useMemo(
    () => (selectedMemId ? rawMemories.find((m) => m.id === selectedMemId) || null : null),
    [rawMemories, selectedMemId]
  );

  const openMemory = React.useCallback(
    (memId: string) => {
      setSelectedMemId(memId);
      onSelectMemory?.(memId);
    },
    [onSelectMemory]
  );

  const closeMemory = React.useCallback(() => {
    setSelectedMemId(null);
    onClearSelected?.();
  }, [onClearSelected]);

  const counts = React.useMemo(() => {
    const rec: Record<string, number> = {
      all: rawMemories.length,
      feedback: 0,
      project: 0,
      reference: 0,
      fresh: 0,
      stale: 0,
      expired: 0,
      unverifiable: 0,
      draft: 0,
      promoted: 0,
      archived: 0,
    };
    rawMemories.forEach((m) => {
      const cat = (m.category || "feedback").toLowerCase();
      if (cat in rec) rec[cat] += 1;
      const archived = isMemoryArchived(m);
      if (m.status === "promoted") {
        rec.promoted += 1;
      } else if (m.status === "draft") {
        rec.draft += 1;
      } else if (archived) {
        rec.archived += 1;
      } else if (m.status === "active") {
        const f = (m.freshness || "fresh").toLowerCase();
        if (f in rec) rec[f] += 1;
      }
    });
    return rec;
  }, [rawMemories]);

  const filteredMemories = React.useMemo(() => {
    const q = filterText.trim().toLowerCase();
    return rawMemories.filter((mem) => {
      const cat = (mem.category || "feedback").toLowerCase();
      if (categoryFilter !== "all" && cat !== categoryFilter) {
        return false;
      }

      const archived = isMemoryArchived(mem);
      if (stateFilter === "promoted") {
        if (mem.status !== "promoted") return false;
      } else if (stateFilter === "draft") {
        if (mem.status !== "draft") return false;
      } else if (stateFilter === "archived") {
        if (!archived || mem.status === "promoted" || mem.status === "draft") return false;
      } else if (stateFilter !== "all") {
        if (archived || mem.status !== "active" || (mem.freshness || "fresh").toLowerCase() !== stateFilter) {
          return false;
        }
      }

      if (!q) return true;
      const entities = mem.targets?.entities || [];
      const paths = mem.targets?.paths || [];
      return (
        mem.id.toLowerCase().includes(q) ||
        mem.title.toLowerCase().includes(q) ||
        (mem.summary && mem.summary.toLowerCase().includes(q)) ||
        (mem.status && mem.status.toLowerCase().includes(q)) ||
        formatStatus(mem.status).toLowerCase().includes(q) ||
        formatMemoryCategory(mem.category).toLowerCase().includes(q) ||
        formatMemoryFreshness(mem.freshness).toLowerCase().includes(q) ||
        entities.some(
          (e) =>
            e.toLowerCase().includes(q) ||
            resolveEntityTitle(e, labelIndex).toLowerCase().includes(q)
        ) ||
        paths.some((p) => p.toLowerCase().includes(q)) ||
        (mem.promoted_to
          ? mem.promoted_to.toLowerCase().includes(q) ||
            resolveEntityTitle(mem.promoted_to, labelIndex).toLowerCase().includes(q)
          : false)
      );
    });
  }, [rawMemories, filterText, categoryFilter, stateFilter, labelIndex]);

  const duplicatePairs = audit?.duplicate_pairs || [];
  const specOverlaps = audit?.spec_overlaps || [];
  const hasAuditHighlights = duplicatePairs.length > 0 || specOverlaps.length > 0;
  const attentionCount = (counts.stale || 0) + (counts.expired || 0);

  return (
    <div id="teamMemoryView" className="space-y-6 animate-in fade-in duration-200">
      {/* Compact Borderless Header & Filter Controls */}
      <header className="space-y-4 pb-5 border-b border-border/70">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-2">
                <Brain aria-hidden="true" className="h-5 w-5 text-teal-500" />
                <span>Team Operational Memory</span>
              </h1>
              <span className="text-xs px-2 py-0.5 rounded-full bg-teal-500/10 text-teal-600 dark:text-teal-400 font-semibold">
                {rawMemories.length} {rawMemories.length === 1 ? "Note" : "Notes"}
              </span>
              {attentionCount > 0 && (
                <Badge variant="warning" className="flex items-center gap-1">
                  <AlertTriangle aria-hidden="true" className="h-3 w-3" />
                  <span>
                    {attentionCount} {attentionCount === 1 ? "Needs Review" : "Need Review"}
                  </span>
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Shared engineering lessons, time-bounded project context, and external pointers stored in Git. Freshness diagnostics are advisory and never block validation.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <label htmlFor="memorySearchInput" className="sr-only">
              Search team memories
            </label>
            <input
              id="memorySearchInput"
              type="search"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Search team notes, targets, categories..."
              className="bg-muted/40 border border-border/80 text-xs rounded-lg px-3 py-1.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary w-full sm:w-72"
            />
          </div>
        </div>

        {/* Category & Freshness Filter Groups */}
        {rawMemories.length > 0 && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by category">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1 mr-1">
                <Filter aria-hidden="true" className="h-3 w-3" /> Category:
              </span>
              {[
                { key: "all", label: `All (${counts.all})` },
                { key: "feedback", label: `Feedback (${counts.feedback})` },
                { key: "project", label: `Project Context (${counts.project})` },
                { key: "reference", label: `Reference (${counts.reference})` },
              ].map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  aria-pressed={categoryFilter === opt.key}
                  onClick={() => setCategoryFilter(opt.key)}
                  className={cn(
                    "px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer border",
                    categoryFilter === opt.key
                      ? "bg-primary text-primary-foreground border-primary font-semibold"
                      : "border-border/50 bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by freshness and status">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1 mr-1">
                <Sparkles aria-hidden="true" className="h-3 w-3" /> Freshness:
              </span>
              {[
                { key: "all", label: `All (${counts.all})` },
                { key: "fresh", label: `Fresh (${counts.fresh})` },
                { key: "stale", label: `Needs Verification (${counts.stale})` },
                { key: "expired", label: `Expired (${counts.expired})` },
                ...(counts.unverifiable > 0
                  ? [{ key: "unverifiable", label: `History Unavailable (${counts.unverifiable})` }]
                  : []),
                ...(counts.draft > 0
                  ? [{ key: "draft", label: `Draft (${counts.draft})` }]
                  : []),
                ...(counts.promoted > 0
                  ? [{ key: "promoted", label: `Promoted (${counts.promoted})` }]
                  : []),
                { key: "archived", label: `Archived (${counts.archived})` },
              ].map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  aria-pressed={stateFilter === opt.key}
                  onClick={() => setStateFilter(opt.key)}
                  className={cn(
                    "px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer border",
                    stateFilter === opt.key
                      ? "bg-teal-500/15 text-teal-600 dark:text-teal-400 border-teal-500/35 font-semibold"
                      : "border-border/50 bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </header>

      {/* Non-Blocking Hygiene & Overlap Highlights */}
      {hasAuditHighlights && (
        <Card id="memoryAuditHighlights" className="border-amber-500/30 bg-amber-500/5 p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
              <ShieldAlert aria-hidden="true" className="h-4 w-4 text-amber-500" />
              <span>Advisory Hygiene Highlights</span>
            </h2>
            <Badge variant="outline" className="text-[10px]">
              Non-blocking
            </Badge>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            {duplicatePairs.length > 0 && (
              <div className="p-3.5 rounded-2xl bg-card border border-border/70 space-y-2">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <Copy aria-hidden="true" className="h-3.5 w-3.5 text-amber-500" />
                  <span>Potential Duplicate Notes ({duplicatePairs.length})</span>
                </div>
                <ul className="space-y-1.5">
                  {duplicatePairs.map((pair, idx) => {
                    const titleA = formatHumanProse(
                      resolveEntityTitle(pair.memory_a, labelIndex, "Team Note"),
                      labelIndex
                    );
                    const titleB = formatHumanProse(
                      resolveEntityTitle(pair.memory_b, labelIndex, "Team Note"),
                      labelIndex
                    );
                    return (
                      <li key={idx} className="flex flex-wrap items-center gap-1.5 text-muted-foreground">
                        <button
                          type="button"
                          onClick={() => openMemory(pair.memory_a)}
                          className="text-primary hover:underline font-medium cursor-pointer text-left"
                        >
                          {titleA}
                        </button>
                        <span>and</span>
                        <button
                          type="button"
                          onClick={() => openMemory(pair.memory_b)}
                          className="text-primary hover:underline font-medium cursor-pointer text-left"
                        >
                          {titleB}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            {specOverlaps.length > 0 && (
              <div className="p-3.5 rounded-2xl bg-card border border-border/70 space-y-2">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <Link2 aria-hidden="true" className="h-3.5 w-3.5 text-primary" />
                  <span>Formal Specification Overlaps ({specOverlaps.length})</span>
                </div>
                <ul className="space-y-1.5">
                  {specOverlaps.map((ov, idx) => {
                    const memTitle = formatHumanProse(
                      resolveEntityTitle(ov.memory_id, labelIndex, "Team Note"),
                      labelIndex
                    );
                    const specTitle = formatHumanProse(
                      resolveEntityTitle(
                        ov.spec_id,
                        labelIndex,
                        ov.spec_title ? formatDisplayTitle(ov.spec_title, ov.spec_id) : "Formal Rule"
                      ),
                      labelIndex
                    );
                    return (
                      <li key={idx} className="flex flex-wrap items-center gap-1.5 text-muted-foreground">
                        <button
                          type="button"
                          onClick={() => openMemory(ov.memory_id)}
                          className="text-foreground hover:text-primary hover:underline font-medium cursor-pointer text-left"
                        >
                          {memTitle}
                        </button>
                        <span>overlaps</span>
                        <button
                          type="button"
                          onClick={() => requestSpecNavigation(ov.spec_id)}
                          className="text-primary hover:underline font-medium cursor-pointer text-left"
                        >
                          {specTitle}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Screen-reader polite status announcement for filters */}
      <p className="sr-only" aria-live="polite">
        Showing {filteredMemories.length} of {rawMemories.length} team operational memories.
      </p>

      {/* Empty State or Memory Cards Grid */}
      {rawMemories.length === 0 ? (
        <div
          id="memoryEmptyState"
          className="rounded-3xl border border-dashed border-border/80 bg-card/50 p-8 sm:p-10 text-center space-y-4"
        >
          <div className="mx-auto h-12 w-12 rounded-2xl bg-teal-500/10 text-teal-500 flex items-center justify-center">
            <Brain aria-hidden="true" className="h-6 w-6" />
          </div>
          <div className="space-y-1.5 max-w-xl mx-auto">
            <h2 className="text-base font-bold text-foreground">
              No Team Memories Recorded Yet
            </h2>
            <p className="text-xs text-muted-foreground leading-relaxed">
              Capture recurring engineering lessons, build or test quirks, time-bounded project milestones, and external operational pointers in Git so teammates and coding assistants share the same context.
            </p>
          </div>
          <div className="max-w-2xl mx-auto rounded-2xl bg-muted/40 border border-border/70 p-4 text-left space-y-2">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
              <Terminal aria-hidden="true" className="h-3.5 w-3.5 text-teal-500" />
              <span>Quickstart Command</span>
            </div>
            <pre className="text-xs font-mono text-foreground overflow-x-auto whitespace-pre-wrap break-words">
              <code>
                eidos memory add "Use real temporary databases in integration tests" --category feedback --summary "Avoid mocking storage queries in test suites."
              </code>
            </pre>
          </div>
        </div>
      ) : filteredMemories.length === 0 ? (
        <div className="py-12 text-center text-sm text-muted-foreground border-y border-dashed border-border/80">
          No team memories match your current filter.
        </div>
      ) : (
        <div className="divide-y divide-border/70 border-y border-border/70">
          {filteredMemories.map((mem) => {
            const targetEntities = mem.targets?.entities || [];
            const targetPathsCount = (mem.targets?.paths || []).length;
            const displayTitle = formatHumanProse(formatDisplayTitle(mem.title, mem.id), labelIndex);
            const cleanSummary = formatHumanProse(mem.summary, labelIndex);
            const cleanReason = formatMemoryFreshnessReason(mem.freshness_reason, labelIndex);
            const showReasonCallout =
              !isMemoryArchived(mem) &&
              mem.status === "active" &&
              mem.freshness !== "fresh" &&
              Boolean(cleanReason);
            const promotedTitle = mem.promoted_to
              ? resolveEntityTitle(mem.promoted_to, labelIndex, "Formal Specification")
              : "";

            return (
              <article
                key={mem.id}
                data-memory-card={mem.id}
                className="py-4 px-2 -mx-2 rounded-lg hover:bg-muted/25 transition-colors space-y-2.5 group"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="space-y-1 min-w-0 flex-1 max-w-3xl">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-sm font-bold text-foreground leading-snug">
                        <button
                          type="button"
                          onClick={() => openMemory(mem.id)}
                          className="text-left group-hover:text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-sm cursor-pointer transition-colors"
                        >
                          {displayTitle}
                        </button>
                      </h2>
                      {getCategoryBadge(mem.category)}
                      {getFreshnessBadge(mem)}
                    </div>
                    {cleanSummary && (
                      <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2">
                        {cleanSummary}
                      </p>
                    )}
                  </div>

                  <div className="flex items-center gap-3 text-[11px] text-muted-foreground shrink-0">
                    {mem.verified_at && <span>Verified {mem.verified_at}</span>}
                    <button
                      type="button"
                      onClick={() => openMemory(mem.id)}
                      aria-label={`Read note: ${displayTitle}`}
                      className="inline-flex items-center gap-1 text-primary font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-sm text-xs cursor-pointer"
                    >
                      <span>Read Note</span>
                      <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>

                {/* Advisory Freshness Inline Notice */}
                {showReasonCallout && (
                  <div
                    className={cn(
                      "pl-3 py-1 border-l-2 text-xs leading-relaxed flex items-start gap-2 max-w-3xl",
                      mem.freshness === "expired"
                        ? "border-red-500 text-red-600 dark:text-red-400"
                        : mem.freshness === "stale"
                        ? "border-amber-500 text-amber-600 dark:text-amber-400"
                        : "border-border text-muted-foreground"
                    )}
                  >
                    {mem.freshness === "expired" ? (
                      <Clock aria-hidden="true" className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    ) : mem.freshness === "stale" ? (
                      <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    ) : (
                      <HelpCircle aria-hidden="true" className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    )}
                    <span>{cleanReason}</span>
                  </div>
                )}

                {/* Inline Targets & Promoted Cross-Links */}
                {(targetEntities.length > 0 || targetPathsCount > 0 || (mem.promoted_to && promotedTitle)) && (
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    {mem.promoted_to && promotedTitle && (
                      <button
                        type="button"
                        onClick={() => requestSpecNavigation(mem.promoted_to!)}
                        className="font-medium px-2 py-0.5 rounded-md bg-primary/10 hover:bg-primary/20 text-primary inline-flex items-center gap-1 transition-colors cursor-pointer"
                        title={`Open ${promotedTitle}`}
                      >
                        <Award aria-hidden="true" className="h-3 w-3 shrink-0" />
                        <span className="truncate max-w-[240px]">Promoted to {promotedTitle}</span>
                      </button>
                    )}
                    {targetEntities.slice(0, 4).map((entId) => {
                      const entTitle = resolveEntityTitle(entId, labelIndex, "Target Area");
                      return (
                        <button
                          key={entId}
                          type="button"
                          onClick={() => requestSpecNavigation(entId)}
                          title={`Open ${entTitle}`}
                          className="font-medium px-2 py-0.5 rounded-md bg-muted/50 hover:bg-primary/15 text-foreground hover:text-primary transition-colors cursor-pointer max-w-[200px] truncate"
                        >
                          {entTitle}
                        </button>
                      );
                    })}
                    {targetEntities.length > 4 && (
                      <span className="text-muted-foreground">
                        +{targetEntities.length - 4} more
                      </span>
                    )}
                    {targetPathsCount > 0 && (
                      <span className="text-muted-foreground">
                        • {targetPathsCount} {targetPathsCount === 1 ? "scoped path" : "scoped paths"}
                      </span>
                    )}
                    {mem.expires_at && (
                      <span className="text-muted-foreground">• Expires {mem.expires_at}</span>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      {/* Memory Detail Drawer */}
      <Sheet
        open={Boolean(selectedMem)}
        onOpenChange={(open) => {
          if (!open) {
            closeMemory();
          }
        }}
        title={
          selectedMem
            ? formatHumanProse(formatDisplayTitle(selectedMem.title, selectedMem.id), labelIndex)
            : "Team Memory"
        }
        subtitle={
          selectedMem
            ? `${formatMemoryCategory(selectedMem.category)} • ${formatStatus(selectedMem.status)}`
            : undefined
        }
        maxWidth="5xl"
      >
        {selectedMem && (() => {
          const targetEntities = selectedMem.targets?.entities || [];
          const targetPaths = selectedMem.targets?.paths || [];
          const cleanSummary = formatHumanProse(selectedMem.summary, labelIndex);
          const cleanReason = formatMemoryFreshnessReason(selectedMem.freshness_reason, labelIndex);
          const promotedTitle = selectedMem.promoted_to
            ? resolveEntityTitle(selectedMem.promoted_to, labelIndex, "Formal Specification")
            : "";

          return (
            <div className="space-y-5">
              {/* Metadata Badges & Reader Escalation */}
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  {getCategoryBadge(selectedMem.category)}
                  {getFreshnessBadge(selectedMem)}
                  {selectedMem.verified_at && (
                    <Badge variant="outline">Verified {selectedMem.verified_at}</Badge>
                  )}
                  {selectedMem.expires_at && (
                    <Badge variant="outline">Expires {selectedMem.expires_at}</Badge>
                  )}
                </div>

                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    const id = selectedMem.id;
                    closeMemory();
                    requestSpecNavigation(id, { mode: "reader" });
                  }}
                  className="h-7 text-[11px] gap-1.5"
                >
                  <ExternalLink aria-hidden="true" className="h-3 w-3" />
                  <span>Open in Full Reader</span>
                </Button>
              </div>

              {/* Summary */}
              {cleanSummary && (
                <div className="p-4 rounded-2xl bg-teal-500/5 border border-teal-500/20 text-xs sm:text-sm text-foreground leading-relaxed">
                  {cleanSummary}
                </div>
              )}

              {/* Freshness Diagnostic Detail */}
              {cleanReason && !isMemoryArchived(selectedMem) && selectedMem.status === "active" && (
                <div className="p-3.5 rounded-2xl bg-muted/30 border border-border/70 text-xs space-y-1">
                  <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
                    Freshness Diagnostic
                  </span>
                  <p className="text-foreground leading-relaxed">{cleanReason}</p>
                </div>
              )}

              {/* Promoted Formal Specification */}
              {selectedMem.promoted_to && promotedTitle && (
                <div className="space-y-2">
                  <h3 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <Award aria-hidden="true" className="h-4 w-4 text-primary" />
                    <span>Promoted Specification</span>
                  </h3>
                  <button
                    type="button"
                    onClick={() => {
                      const dest = selectedMem.promoted_to!;
                      closeMemory();
                      requestSpecNavigation(dest);
                    }}
                    className="inline-flex items-center gap-2 px-3 py-1.5 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary border border-primary/30 text-xs font-semibold cursor-pointer transition-colors"
                  >
                    <span>{promotedTitle}</span>
                    <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}

              {/* Anchored Target Entities & Path Scope */}
              {(targetEntities.length > 0 || targetPaths.length > 0) && (
                <div className="space-y-2">
                  <h3 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <Link2 aria-hidden="true" className="h-4 w-4 text-teal-500" />
                    <span>Anchored Scope</span>
                  </h3>
                  <div className="p-3.5 rounded-2xl bg-muted/20 border border-border/70 space-y-2.5 text-xs">
                    {targetEntities.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {targetEntities.map((entId) => {
                          const entTitle = resolveEntityTitle(entId, labelIndex, "Target Area");
                          return (
                            <button
                              key={entId}
                              type="button"
                              onClick={() => {
                                closeMemory();
                                requestSpecNavigation(entId);
                              }}
                              title={`Open ${entTitle}`}
                              className="px-2.5 py-1 rounded-lg bg-card hover:bg-primary/10 text-primary border border-border hover:border-primary/40 font-medium cursor-pointer transition-colors"
                            >
                              {entTitle}
                            </button>
                          );
                        })}
                      </div>
                    )}
                    {targetPaths.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {targetPaths.map((tp, idx) => (
                          <span
                            key={idx}
                            className="px-2.5 py-0.5 rounded-md bg-muted/50 text-muted-foreground border border-border/60 text-[11px] font-medium"
                          >
                            {formatMemoryScopePath(tp, labelIndex)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Full Rendered AsciiDoc Note */}
              {hasSpecBodyOrChunk(selectedMem.id, selectedMem.body) && (
                <div className="space-y-2 pt-2 border-t border-border/60">
                  <h3 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <BookOpen aria-hidden="true" className="h-3.5 w-3.5 text-teal-500" />
                    <span>Full Operational Note</span>
                  </h3>
                  <AsciiDocRenderer
                    measure="full"
                    hideLeadingTitle={true}
                    specId={selectedMem.id}
                    content={selectedMem.body || ""}
                  />
                </div>
              )}
            </div>
          );
        })()}
      </Sheet>
    </div>
  );
}
