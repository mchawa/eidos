import * as React from "react";
import {
  GitPullRequest,
  Clock,
  CheckCircle2,
  AlertCircle,
  XCircle,
  FileDiff,
  GitBranch,
  BookOpen,
  Filter,
  HelpCircle,
  Crosshair,
  LayoutGrid,
  ListOrdered,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Archive,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { hasSpecBodyOrChunk } from "@/lib/specChunks";
import { requestSpecNavigation } from "@/lib/specNav";
import {
  formatDeltaKind,
  formatDisplayTitle,
  formatHumanProse,
  formatMutationOperation,
  formatStatus,
  humanizeToken,
  resolveEntityTitle,
  useEntityLabels,
} from "@/lib/presentation";
import { cn } from "@/lib/utils";
import type { PortalData, SpecDelta, SpecMutation } from "@/types";

export interface DeltasViewProps {
  data: PortalData;
  initialDeltaId?: string;
  initialDeltaNonce?: number;
  onClearSelectedDelta?: () => void;
}

const AFFECTS_CATEGORY_LABELS: Record<string, string> = {
  containers: "Containers",
  components: "Components",
  capabilities: "Capabilities",
  contracts: "Contracts",
  domains: "Domains",
  policies: "Policies",
  personas: "Personas",
  design_systems: "Design Systems",
  screens: "Screens",
  ui_components: "UI Components",
  clients: "Clients",
  flows: "Flows",
};

export function formatAffectsCategory(category: string): string {
  if (AFFECTS_CATEGORY_LABELS[category]) {
    return AFFECTS_CATEGORY_LABELS[category];
  }
  return humanizeToken(category);
}

export function getMutationTarget(m?: SpecMutation | null): string {
  if (!m) return "Target Item";
  return m.target || m.title || "Target Item";
}

export function isDeltaArchived(d: SpecDelta): boolean {
  return Boolean(
    d.is_archived ||
      d.status === "archived" ||
      (d.path && d.path.replace(/\\/g, "/").includes("deltas/archive/"))
  );
}

interface ColumnConfig {
  key: string;
  label: string;
  icon: any;
  color: string;
  bg: string;
  borderColor: string;
}

const STANDARD_COLUMNS: ColumnConfig[] = [
  {
    key: "proposed",
    label: "Proposed",
    icon: Clock,
    color: "text-amber-500 dark:text-amber-400",
    bg: "bg-amber-500/10 dark:bg-amber-500/15",
    borderColor: "border-amber-500/20",
  },
  {
    key: "in_review",
    label: "In Review",
    icon: AlertCircle,
    color: "text-blue-500 dark:text-blue-400",
    bg: "bg-blue-500/10 dark:bg-blue-500/15",
    borderColor: "border-blue-500/20",
  },
  {
    key: "approved",
    label: "Approved",
    icon: GitPullRequest,
    color: "text-purple-500 dark:text-purple-400",
    bg: "bg-purple-500/10 dark:bg-purple-500/15",
    borderColor: "border-purple-500/20",
  },
  {
    key: "applied",
    label: "Applied",
    icon: CheckCircle2,
    color: "text-emerald-500 dark:text-emerald-400",
    bg: "bg-emerald-500/10 dark:bg-emerald-500/15",
    borderColor: "border-emerald-500/20",
  },
  {
    key: "rejected",
    label: "Rejected",
    icon: XCircle,
    color: "text-rose-500 dark:text-rose-400",
    bg: "bg-rose-500/10 dark:bg-rose-500/15",
    borderColor: "border-rose-500/20",
  },
  {
    key: "archived",
    label: "Archived",
    icon: Archive,
    color: "text-slate-500 dark:text-slate-400",
    bg: "bg-slate-500/10 dark:bg-slate-500/15",
    borderColor: "border-slate-500/20",
  },
];

const PAGE_SIZE = 25;

export function DeltasView({
  data,
  initialDeltaId,
  initialDeltaNonce,
  onClearSelectedDelta,
}: DeltasViewProps) {
  const labelIndex = useEntityLabels();
  const [selectedDelta, setSelectedDelta] = React.useState<SpecDelta | null>(null);
  const [filterText, setFilterText] = React.useState<string>("");
  const [selectedStatusFilter, setSelectedStatusFilter] = React.useState<string>("all");
  const [viewLayout, setViewLayout] = React.useState<"kanban" | "timeline">("timeline");
  const [appliedVisibleCount, setAppliedVisibleCount] = React.useState<number>(PAGE_SIZE);

  const handleEntityPillClick = (entityId: string) => {
    setSelectedDelta(null);
    onClearSelectedDelta?.();
    requestSpecNavigation(entityId);
  };

  React.useEffect(() => {
    if (initialDeltaId && data.deltas) {
      for (const list of Object.values(data.deltas)) {
        if (Array.isArray(list)) {
          const match = list.find((d) => d.id === initialDeltaId);
          if (match) {
            setSelectedDelta(match);
            break;
          }
        }
      }
    }
  }, [initialDeltaId, initialDeltaNonce, data.deltas]);

  const columns: ColumnConfig[] = React.useMemo(() => {
    const cols = [...STANDARD_COLUMNS];
    const standardKeys = new Set(cols.map((c) => c.key));

    if (data.deltas) {
      Object.keys(data.deltas).forEach((statusKey) => {
        if (!standardKeys.has(statusKey)) {
          cols.push({
            key: statusKey,
            label: formatStatus(statusKey),
            icon: HelpCircle,
            color: "text-zinc-500 dark:text-zinc-400",
            bg: "bg-zinc-500/10 dark:bg-zinc-500/15",
            borderColor: "border-zinc-500/20",
          });
        }
      });
    }

    return cols;
  }, [data.deltas]);

  const deltasByStatus = React.useMemo(() => {
    const raw = data.deltas || {};
    const q = filterText.toLowerCase();

    const matchesQuery = (d: SpecDelta) => {
      if (!q) return true;
      return Boolean(
        d.id.toLowerCase().includes(q) ||
          d.title.toLowerCase().includes(q) ||
          (d.motivation && d.motivation.toLowerCase().includes(q)) ||
          (d.kind && d.kind.toLowerCase().includes(q)) ||
          (d.status && d.status.toLowerCase().includes(q)) ||
          (d.vcs?.branch && d.vcs.branch.toLowerCase().includes(q))
      );
    };

    const record: Record<string, SpecDelta[]> = {};
    columns.forEach((col) => {
      record[col.key] = [];
    });

    const seenArchivedIds = new Set<string>();
    Object.entries(raw).forEach(([statusKey, list]) => {
      if (!Array.isArray(list)) return;
      list.forEach((d) => {
        if (!matchesQuery(d)) return;
        if (isDeltaArchived(d)) {
          if (!seenArchivedIds.has(d.id)) {
            seenArchivedIds.add(d.id);
            record["archived"] = record["archived"] || [];
            record["archived"].push(d);
          }
        } else {
          record[statusKey] = record[statusKey] || [];
          record[statusKey].push(d);
        }
      });
    });

    return record;
  }, [data.deltas, filterText, columns]);

  const totalCount = React.useMemo(() => {
    return Object.values(deltasByStatus).reduce((acc, list) => acc + list.length, 0);
  }, [deltasByStatus]);

  const activeColumns = React.useMemo(() => {
    if (selectedStatusFilter === "all") return columns;
    return columns.filter((c) => c.key === selectedStatusFilter);
  }, [columns, selectedStatusFilter]);

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Compact Borderless Header & Controls */}
      <header className="space-y-4 pb-5 border-b border-border/70">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-2">
                <GitBranch className="h-5 w-5 text-primary" aria-hidden="true" />
                Change Proposals
              </h1>
              <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-semibold">
                {totalCount}
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Track proposed, in-review, approved, and applied changes across the system.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Timeline / Kanban Toggle */}
            <div className="flex items-center bg-muted/50 p-0.5 rounded-lg border border-border/60 text-xs">
              <button
                type="button"
                onClick={() => setViewLayout("timeline")}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer",
                  viewLayout === "timeline"
                    ? "bg-card text-foreground font-semibold shadow-xs"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <ListOrdered className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                <span>Timeline</span>
              </button>
              <button
                type="button"
                onClick={() => setViewLayout("kanban")}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer",
                  viewLayout === "kanban"
                    ? "bg-card text-foreground font-semibold shadow-xs"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <LayoutGrid className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                <span>Board</span>
              </button>
            </div>
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Filter changes by title, summary, or status..."
              className="bg-muted/40 border border-border/80 text-xs rounded-lg px-3 py-1.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary w-full sm:w-64"
            />
          </div>
        </div>

        {/* Status Lifecycle Filter Pills */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1 mr-1">
            <Filter className="h-3 w-3" aria-hidden="true" /> Status:
          </span>
          <button
            type="button"
            onClick={() => setSelectedStatusFilter("all")}
            className={cn(
              "px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer",
              selectedStatusFilter === "all"
                ? "bg-primary text-primary-foreground font-semibold"
                : "bg-muted/40 text-muted-foreground hover:text-foreground hover:bg-muted"
            )}
          >
            All ({totalCount})
          </button>
          {columns.map((col) => {
            const count = (deltasByStatus[col.key] || []).length;
            const Icon = col.icon;
            const isSelected = selectedStatusFilter === col.key;
            return (
              <button
                key={col.key}
                type="button"
                onClick={() => setSelectedStatusFilter(col.key)}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer border",
                  isSelected
                    ? `${col.bg} ${col.color} ${col.borderColor} font-bold`
                    : "border-border/50 bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50"
                )}
              >
                <Icon className="h-3 w-3" aria-hidden="true" />
                <span>{col.label}</span>
                <span className="text-[10px] opacity-75">({count})</span>
              </button>
            );
          })}
        </div>
      </header>

      {/* Chronological Timeline List (Default) or Kanban Board */}
      {viewLayout === "timeline" ? (
        <div className="space-y-4">
          <div className="divide-y divide-border/70 border-y border-border/70">
            {activeColumns
              .flatMap((col) => deltasByStatus[col.key] || [])
              .sort((a, b) => b.id.localeCompare(a.id))
              .slice(0, appliedVisibleCount)
              .map((delta) => {
                const changeCount = delta.mutations?.length || 0;
                const firstTargetTitle = delta.mutations?.[0]
                  ? resolveEntityTitle(getMutationTarget(delta.mutations[0]), labelIndex)
                  : "";
                return (
                  <button
                    key={delta.id}
                    type="button"
                    onClick={() => setSelectedDelta(delta)}
                    className="w-full py-3.5 px-2 -mx-2 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 cursor-pointer hover:bg-muted/30 transition-colors text-left group"
                  >
                    <div className="space-y-1 min-w-0 flex-1 max-w-3xl">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-foreground group-hover:text-primary transition-colors">
                          {formatDisplayTitle(delta.title, delta.id)}
                        </span>
                        {isDeltaArchived(delta) && delta.status.toLowerCase() !== "archived" && (
                          <Badge variant="outline" className="text-[10px] border-slate-500/30 text-slate-400">
                            Archived
                          </Badge>
                        )}
                        <Badge variant="outline" className="text-[10px]">
                          {formatStatus(delta.status)}
                        </Badge>
                        <Badge variant="outline" className="text-[10px]">
                          {formatDeltaKind(delta.kind)}
                        </Badge>
                      </div>
                      {delta.motivation && (
                        <p className="text-xs text-muted-foreground line-clamp-1">
                          {delta.motivation}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-[11px] text-muted-foreground shrink-0">
                      {firstTargetTitle && (
                        <span className="hidden md:inline truncate max-w-[180px] text-muted-foreground/80">
                          {firstTargetTitle}
                        </span>
                      )}
                      <span>
                        {changeCount} {changeCount === 1 ? "change" : "changes"}
                      </span>
                      <ChevronRight className="h-3.5 w-3.5 group-hover:text-primary transition-colors" aria-hidden="true" />
                    </div>
                  </button>
                );
              })}
          </div>
          {activeColumns.flatMap((col) => deltasByStatus[col.key] || []).length >
            appliedVisibleCount && (
            <div className="pt-2 text-center">
              <button
                type="button"
                onClick={() => setAppliedVisibleCount((c) => c + PAGE_SIZE)}
                className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold bg-muted/60 hover:bg-muted text-foreground border border-border/60 cursor-pointer"
              >
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Show More Changes</span>
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto pb-4">
          <div
            className={cn(
              "grid gap-4 items-start min-w-[320px]",
              activeColumns.length === 1
                ? "grid-cols-1 max-w-xl mx-auto"
                : activeColumns.length === 2
                ? "grid-cols-1 md:grid-cols-2"
                : activeColumns.length === 3
                ? "grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
                : activeColumns.length === 4
                ? "grid-cols-1 md:grid-cols-2 lg:grid-cols-4"
                : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5"
            )}
            style={
              activeColumns.length > 5
                ? { gridTemplateColumns: `repeat(${activeColumns.length}, minmax(280px, 1fr))` }
                : undefined
            }
          >
            {activeColumns.map((col) => {
              const fullList = deltasByStatus[col.key] || [];
              const isPaginatedCol = col.key === "applied" || col.key === "archived";
              const list = isPaginatedCol
                ? fullList.slice(0, appliedVisibleCount)
                : fullList;
              const Icon = col.icon;
              return (
                <div
                  key={col.key}
                  className="bg-card/40 rounded-2xl border border-border/80 p-3 flex flex-col space-y-3 min-h-[500px]"
                >
                  {/* Column Header */}
                  <div className="flex items-center justify-between px-2 py-1">
                    <div className="flex items-center gap-2">
                      <div className={`p-1.5 rounded-lg ${col.bg} ${col.color}`}>
                        <Icon className="h-4 w-4" />
                      </div>
                      <span className="font-bold text-xs text-foreground tracking-wide">
                        {col.label}
                      </span>
                    </div>
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
                      {fullList.length}
                    </span>
                  </div>

                  {/* Column Cards */}
                  <div className="space-y-2.5 overflow-y-auto max-h-[700px] pr-1">
                    {list.length === 0 ? (
                      <div className="py-12 text-center text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-xl px-4">
                        No {col.label.toLowerCase()} changes
                      </div>
                    ) : (
                      <>
                        {list.map((delta) => {
                          const changeCount = delta.mutations?.length || 0;
                          const firstTargetTitle = delta.mutations?.[0]
                            ? resolveEntityTitle(getMutationTarget(delta.mutations[0]), labelIndex)
                            : "";
                          return (
                            <Card
                              key={delta.id}
                              className="p-3.5 space-y-2.5 cursor-pointer hover:border-primary/50 transition-all hover:md-elevation-2 group"
                              onClick={() => setSelectedDelta(delta)}
                            >
                              <div className="flex items-center justify-between gap-1">
                                <Badge variant="outline" className="text-[10px] shrink-0">
                                  {formatDeltaKind(delta.kind)}
                                </Badge>
                                {isDeltaArchived(delta) && (
                                  <Badge variant="outline" className="text-[10px] border-slate-500/30 text-slate-400">
                                    {formatStatus(delta.status)}
                                  </Badge>
                                )}
                              </div>

                              <h4 className="text-xs font-semibold text-foreground group-hover:text-primary transition-colors line-clamp-2 leading-relaxed">
                                {formatDisplayTitle(delta.title, delta.id)}
                              </h4>

                              {delta.mutations && delta.mutations.length > 0 && (
                                <div className="pt-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                                  <div className="flex items-center gap-1 shrink-0">
                                    <FileDiff className="h-3 w-3 text-primary shrink-0" />
                                    <span>
                                      {changeCount} {changeCount === 1 ? "change" : "changes"}
                                    </span>
                                  </div>
                                  {firstTargetTitle && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground truncate max-w-[140px]">
                                      {firstTargetTitle}
                                    </span>
                                  )}
                                </div>
                              )}
                            </Card>
                          );
                        })}
                        {isPaginatedCol && fullList.length > appliedVisibleCount && (
                          <button
                            type="button"
                            onClick={() => setAppliedVisibleCount((c) => c + PAGE_SIZE)}
                            className="w-full py-2 rounded-xl text-xs font-semibold bg-muted/60 hover:bg-muted text-foreground border border-border/60 cursor-pointer flex items-center justify-center gap-1"
                          >
                            <ChevronDown className="h-3.5 w-3.5" />
                            <span>
                              Show More ({fullList.length - appliedVisibleCount} remaining)
                            </span>
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Change Detail Sheet */}
      <Sheet
        open={Boolean(selectedDelta)}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedDelta(null);
            onClearSelectedDelta?.();
          }
        }}
        title={selectedDelta ? formatDisplayTitle(selectedDelta.title, selectedDelta.id) : "Change Details"}
        subtitle={
          selectedDelta
            ? `${formatDeltaKind(selectedDelta.kind)} • ${formatStatus(selectedDelta.status)}`
            : undefined
        }
        maxWidth="6xl"
      >
        {selectedDelta && (
          <div className="space-y-5">
            {/* Header Badges & Reader Escalation */}
            <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-border/60">
              <div className="flex flex-wrap items-center gap-2">
                {isDeltaArchived(selectedDelta) && selectedDelta.status.toLowerCase() !== "archived" && (
                  <Badge variant="outline" className="text-[11px] border-slate-500/30 text-slate-400">
                    Archived
                  </Badge>
                )}
                <Badge variant="primary" className="text-[11px]">
                  {formatStatus(selectedDelta.status)}
                </Badge>
                <Badge variant="outline" className="text-xs">
                  {formatDeltaKind(selectedDelta.kind)}
                </Badge>
              </div>
              <button
                type="button"
                onClick={() => {
                  const id = selectedDelta.id;
                  setSelectedDelta(null);
                  onClearSelectedDelta?.();
                  requestSpecNavigation(id, { mode: "reader" });
                }}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer transition-colors"
              >
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Open in Full Reader</span>
              </button>
            </div>

            {/* Summary & Purpose */}
            <p className="text-sm text-foreground/90 leading-relaxed max-w-3xl">
              {selectedDelta.motivation || "Planned system update."}
            </p>

            {/* Collapsible Planned Changes & Affected Areas */}
            <div className="divide-y divide-border/60 border-y border-border/60">
              <details className="portal-disclosure py-2.5" open>
                <summary className="flex items-center gap-2 text-xs font-semibold text-foreground select-none">
                  <ChevronRight className="disclosure-chevron h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                  <FileDiff className="h-3.5 w-3.5 text-primary shrink-0" aria-hidden="true" />
                  <span>Planned Changes ({selectedDelta.mutations?.length || 0})</span>
                </summary>
                <div className="pt-2.5 pl-5 divide-y divide-border/50">
                  {(selectedDelta.mutations || []).map((m, i) => {
                    const rawTarget = getMutationTarget(m);
                    const targetTitle = resolveEntityTitle(rawTarget, labelIndex, "Target Item");
                    return (
                      <div key={i} className="py-2 first:pt-0 text-xs space-y-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-bold text-primary text-[10px] px-1.5 py-0.5 rounded bg-primary/10">
                            {formatMutationOperation(m.operation)}
                          </span>
                          <button
                            type="button"
                            onClick={() => handleEntityPillClick(rawTarget)}
                            className="font-semibold text-foreground hover:text-primary hover:underline cursor-pointer text-left"
                          >
                            {targetTitle}
                          </button>
                        </div>
                        {m.details && (
                          <p className="text-muted-foreground text-xs leading-relaxed">
                            {formatHumanProse(m.details, labelIndex)}
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </details>

              {(selectedDelta.affects || selectedDelta.blast_radius) && (
                <details className="portal-disclosure py-2.5">
                  <summary className="flex items-center gap-2 text-xs font-semibold text-foreground select-none">
                    <ChevronRight className="disclosure-chevron h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                    <Crosshair className="h-3.5 w-3.5 text-rose-400 shrink-0" aria-hidden="true" />
                    <span>Affected Areas &amp; Blast Radius</span>
                  </summary>
                  <div className="pt-3 pl-5 text-xs space-y-3">
                    {selectedDelta.affects && (
                      <div className="flex flex-wrap gap-1.5">
                        {Object.entries(selectedDelta.affects).map(([category, items]) =>
                          Array.isArray(items) && items.length > 0 ? (
                            items.map((item, idx) => (
                              <button
                                key={`${category}-${idx}`}
                                type="button"
                                onClick={() => handleEntityPillClick(String(item))}
                                className="inline-flex items-center rounded-md border border-border/70 px-2 py-0.5 text-[11px] hover:border-primary/50 hover:bg-primary/10 transition-colors cursor-pointer"
                              >
                                <span className="text-muted-foreground mr-1">
                                  {formatAffectsCategory(category)}:
                                </span>
                                <span className="font-semibold text-foreground">
                                  {resolveEntityTitle(String(item), labelIndex)}
                                </span>
                              </button>
                            ))
                          ) : null
                        )}
                      </div>
                    )}

                    {selectedDelta.blast_radius &&
                      Boolean(
                        selectedDelta.blast_radius.impact_level ||
                          selectedDelta.blast_radius.impacted_containers?.length ||
                          selectedDelta.blast_radius.impacted_components?.length ||
                          selectedDelta.blast_radius.invariants_to_enforce?.length ||
                          selectedDelta.blast_radius.impacted_design_systems?.length ||
                          selectedDelta.blast_radius.impacted_screens?.length ||
                          selectedDelta.blast_radius.impacted_personas?.length
                      ) && (
                        <div className="space-y-2 pt-2 border-t border-border/40">
                          {selectedDelta.blast_radius.impact_level && (
                            <div className="flex items-center gap-2">
                              <span className="text-muted-foreground">Impact Level:</span>
                              <Badge variant="outline" className="text-[11px] font-semibold text-amber-400">
                                {humanizeToken(selectedDelta.blast_radius.impact_level)}
                              </Badge>
                            </div>
                          )}
                          {selectedDelta.blast_radius.impacted_containers && selectedDelta.blast_radius.impacted_containers.length > 0 && (
                            <div className="flex flex-wrap items-center gap-1">
                              <span className="text-muted-foreground mr-1">Containers:</span>
                              {selectedDelta.blast_radius.impacted_containers.map((c: string, idx: number) => (
                                <button
                                  key={idx}
                                  type="button"
                                  onClick={() => handleEntityPillClick(c)}
                                  className="text-[11px] px-2 py-0.5 rounded bg-muted hover:bg-primary/15 text-primary cursor-pointer"
                                >
                                  {resolveEntityTitle(c, labelIndex)}
                                </button>
                              ))}
                            </div>
                          )}
                          {selectedDelta.blast_radius.impacted_components && selectedDelta.blast_radius.impacted_components.length > 0 && (
                            <div className="flex flex-wrap items-center gap-1">
                              <span className="text-muted-foreground mr-1">Components:</span>
                              {selectedDelta.blast_radius.impacted_components.map((c: string, idx: number) => (
                                <button
                                  key={idx}
                                  type="button"
                                  onClick={() => handleEntityPillClick(c)}
                                  className="text-[11px] px-2 py-0.5 rounded bg-muted hover:bg-primary/15 text-foreground cursor-pointer"
                                >
                                  {resolveEntityTitle(c, labelIndex)}
                                </button>
                              ))}
                            </div>
                          )}
                          {selectedDelta.blast_radius.invariants_to_enforce && selectedDelta.blast_radius.invariants_to_enforce.length > 0 && (
                            <div className="flex flex-wrap items-center gap-1">
                              <span className="text-muted-foreground mr-1">Rules Verified:</span>
                              {selectedDelta.blast_radius.invariants_to_enforce.map((inv: string, idx: number) => (
                                <button
                                  key={idx}
                                  type="button"
                                  onClick={() => handleEntityPillClick(inv)}
                                  className="text-[11px] px-2 py-0.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-500 dark:text-emerald-400 font-medium cursor-pointer"
                                >
                                  {resolveEntityTitle(inv, labelIndex)}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                  </div>
                </details>
              )}
            </div>

            {/* Full Document */}
            {hasSpecBodyOrChunk(selectedDelta.id, selectedDelta.body) && (
              <div className="space-y-2 pt-1">
                <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <BookOpen className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                  Full Proposal Document
                </h4>
                <AsciiDocRenderer
                  specId={selectedDelta.id}
                  content={selectedDelta.body || ""}
                  measure="full"
                  hideLeadingTitle={true}
                />
              </div>
            )}
          </div>
        )}
      </Sheet>
    </div>
  );
}
