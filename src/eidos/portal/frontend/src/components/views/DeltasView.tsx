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
  Sparkles,
  HelpCircle,
  Tag,
  Crosshair,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { cn } from "@/lib/utils";
import type { PortalData, SpecDelta, SpecMutation } from "@/types";

export interface DeltasViewProps {
  data: PortalData;
  initialDeltaId?: string;
}

export function getMutationTarget(m?: SpecMutation | null): string {
  if (!m) return "Target Specification";
  return (
    m.target_container ||
    m.target_component ||
    m.target_capability ||
    m.target_domain ||
    m.target_contract ||
    m.target_policy ||
    m.target_architecture ||
    m.target_persona ||
    m.target_invariant_id ||
    m.target_design_system ||
    m.target_screen ||
    m.target_ui_component ||
    m.target_client ||
    m.target_flow ||
    m.new_capability_id ||
    m.title ||
    "Target Specification"
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
];

export function DeltasView({ data, initialDeltaId }: DeltasViewProps) {
  const [selectedDelta, setSelectedDelta] = React.useState<SpecDelta | null>(null);
  const [filterText, setFilterText] = React.useState<string>("");
  const [selectedStatusFilter, setSelectedStatusFilter] = React.useState<string>("all");

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
  }, [initialDeltaId, data.deltas]);

  // Dynamically resolve all columns: standard 5 + any custom status in data.deltas
  const columns: ColumnConfig[] = React.useMemo(() => {
    const cols = [...STANDARD_COLUMNS];
    const standardKeys = new Set(cols.map((c) => c.key));

    if (data.deltas) {
      Object.keys(data.deltas).forEach((statusKey) => {
        if (!standardKeys.has(statusKey)) {
          cols.push({
            key: statusKey,
            label: statusKey
              .replace(/_/g, " ")
              .replace(/\b\w/g, (c) => c.toUpperCase()),
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

  // Index deltas by status with search filter
  const deltasByStatus = React.useMemo(() => {
    const raw = data.deltas || {};
    const q = filterText.toLowerCase();

    const filterList = (list: SpecDelta[] = []) => {
      if (!q) return list;
      return list.filter(
        (d) =>
          d.id.toLowerCase().includes(q) ||
          d.title.toLowerCase().includes(q) ||
          (d.motivation && d.motivation.toLowerCase().includes(q)) ||
          (d.kind && d.kind.toLowerCase().includes(q)) ||
          (d.vcs?.branch && d.vcs.branch.toLowerCase().includes(q))
      );
    };

    const record: Record<string, SpecDelta[]> = {};
    columns.forEach((col) => {
      record[col.key] = filterList((raw as any)[col.key] || []);
    });
    return record;
  }, [data.deltas, filterText, columns]);

  // Total count
  const totalCount = React.useMemo(() => {
    return Object.values(deltasByStatus).reduce((acc, list) => acc + list.length, 0);
  }, [deltasByStatus]);

  // Filter columns if user selected a single status pill
  const activeColumns = React.useMemo(() => {
    if (selectedStatusFilter === "all") return columns;
    return columns.filter((c) => c.key === selectedStatusFilter);
  }, [columns, selectedStatusFilter]);

  return (
    <div className="space-y-5 animate-in fade-in duration-200">
      {/* Header & Controls */}
      <div className="space-y-4 p-5 rounded-3xl bg-card border border-border md-elevation-1">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base sm:text-lg font-bold text-foreground tracking-tight flex items-center gap-2">
                <GitBranch className="h-5 w-5 text-primary" />
                SpecDelta Mutation Board (Planned Mode)
              </h2>
              <span className="font-mono text-xs px-2.5 py-0.5 rounded-full bg-primary/10 text-primary font-semibold border border-primary/20">
                {totalCount} Total
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Machine-readable architectural mutations governed by formal SpecDeltas (`DLT-*`). Full status lifecycle visibility.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Filter by ID, title, branch, intent..."
              className="bg-muted/50 border border-border text-xs rounded-xl px-3 py-1.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary w-full sm:w-64"
            />
          </div>
        </div>

        {/* Status Lifecycle Filter Pills */}
        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/60">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1 mr-1">
            <Filter className="h-3 w-3" /> Statuses:
          </span>
          <button
            type="button"
            onClick={() => setSelectedStatusFilter("all")}
            className={cn(
              "px-3 py-1 rounded-xl text-xs font-medium transition-all cursor-pointer",
              selectedStatusFilter === "all"
                ? "bg-primary text-primary-foreground font-semibold shadow-xs"
                : "bg-muted/50 text-muted-foreground hover:text-foreground hover:bg-muted"
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
                  "flex items-center gap-1.5 px-3 py-1 rounded-xl text-xs font-medium transition-all cursor-pointer border",
                  isSelected
                    ? `${col.bg} ${col.color} ${col.borderColor} font-bold shadow-xs`
                    : "border-border/60 bg-muted/30 text-muted-foreground hover:text-foreground hover:bg-muted/60"
                )}
              >
                <Icon className="h-3 w-3" />
                <span>{col.label}</span>
                <span className="font-mono text-[10px] opacity-75">({count})</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Kanban Grid */}
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
            const list = deltasByStatus[col.key] || [];
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
                    <span className="font-bold text-xs text-foreground uppercase tracking-wide">
                      {col.label}
                    </span>
                  </div>
                  <span className="text-xs font-mono font-semibold px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
                    {list.length}
                  </span>
                </div>

                {/* Column Cards */}
                <div className="space-y-2.5 overflow-y-auto max-h-[700px] pr-1">
                  {list.length === 0 ? (
                    <div className="py-12 text-center text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-xl px-4">
                      No {col.label.toLowerCase()} deltas
                    </div>
                  ) : (
                    list.map((delta) => (
                      <Card
                        key={delta.id}
                        className="p-3.5 space-y-2.5 cursor-pointer hover:border-primary/50 transition-all hover:md-elevation-2 group"
                        onClick={() => setSelectedDelta(delta)}
                      >
                        <div className="flex items-center justify-between gap-1">
                          <span className="font-mono text-xs font-bold text-primary group-hover:underline truncate">
                            {delta.id}
                          </span>
                          <Badge variant="outline" className="text-[10px] shrink-0">
                            {delta.kind}
                          </Badge>
                        </div>

                        <h4 className="text-xs font-medium text-foreground line-clamp-2 leading-relaxed">
                          {delta.title}
                        </h4>

                        {delta.vcs?.branch && (
                          <div className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground truncate bg-muted/40 px-2 py-1 rounded-lg">
                            <GitBranch className="h-3 w-3 text-purple-400 shrink-0" />
                            <span className="truncate">{delta.vcs.branch}</span>
                          </div>
                        )}

                        {delta.mutations && delta.mutations.length > 0 && (
                          <div className="pt-1 flex items-center justify-between text-[10px] text-muted-foreground font-mono">
                            <div className="flex items-center gap-1">
                              <FileDiff className="h-3 w-3 text-primary shrink-0" />
                              <span>{delta.mutations.length} mutation(s)</span>
                            </div>
                            {delta.mutations[0] && (
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground truncate max-w-[120px]">
                                {getMutationTarget(delta.mutations[0])}
                              </span>
                            )}
                          </div>
                        )}
                      </Card>
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Delta Detail Sheet (Centered Modal with AsciiDocRenderer) */}
      <Sheet
        open={Boolean(selectedDelta)}
        onOpenChange={(open) => !open && setSelectedDelta(null)}
        title={selectedDelta?.title || "SpecDelta Details"}
        subtitle={selectedDelta?.id}
        maxWidth="6xl"
      >
        {selectedDelta && (
          <div className="space-y-6">
            {/* Header Badges */}
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="primary" className="uppercase font-mono text-[11px]">
                {selectedDelta.status}
              </Badge>
              <Badge variant="outline" className="text-xs">
                Kind: {selectedDelta.kind}
              </Badge>
              {selectedDelta.path && (
                <span className="text-xs font-mono text-muted-foreground bg-muted/50 px-2.5 py-1 rounded-xl">
                  {selectedDelta.path}
                </span>
              )}
            </div>

            {/* VCS Branch Callout */}
            {selectedDelta.vcs?.branch && (
              <div className="p-3.5 rounded-2xl bg-purple-500/10 border border-purple-500/20 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <GitBranch className="h-4 w-4 text-purple-400 shrink-0" />
                  <span className="text-xs font-semibold text-purple-400">Target VCS Branch:</span>
                  <span className="font-mono text-xs font-bold text-foreground">
                    {selectedDelta.vcs.branch}
                  </span>
                </div>
                <code className="text-[10px] font-mono bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded-md">
                  git checkout {selectedDelta.vcs.branch}
                </code>
              </div>
            )}

            {/* Motivation & Strategic Intent */}
            <div className="space-y-2">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5 text-amber-500" />
                Motivation & Strategic Intent
              </h4>
              <p className="text-xs text-muted-foreground leading-relaxed p-3.5 rounded-2xl bg-muted/20 border border-border/60">
                {selectedDelta.motivation || "Standard architectural evolution."}
              </p>
            </div>

            {/* Declared Mutations */}
            <div className="space-y-2.5">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <FileDiff className="h-3.5 w-3.5 text-primary" />
                  Declared Mutations
                </span>
                <span className="font-mono text-muted-foreground text-xs">
                  ({selectedDelta.mutations?.length || 0})
                </span>
              </h4>
              <div className="space-y-2">
                {(selectedDelta.mutations || []).map((m, i) => (
                  <div
                    key={i}
                    className="p-3.5 rounded-2xl border border-border/70 bg-card text-xs space-y-2"
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold text-primary uppercase font-mono text-[10px] px-2 py-0.5 rounded-lg bg-primary/10 border border-primary/20">
                        {m.action}
                      </span>
                      <span className="font-mono font-semibold text-foreground">
                        {getMutationTarget(m)}
                      </span>
                    </div>
                    {m.details && (
                      <p className="text-muted-foreground text-xs leading-relaxed pl-1">
                        {m.details}
                      </p>
                    )}
                    {m.expect && Object.keys(m.expect).length > 0 && (
                      <div className="pt-2 border-t border-border/40 text-[11px] font-mono space-y-1">
                        <span className="text-[10px] text-muted-foreground uppercase font-sans font-semibold tracking-wider">
                          Expected Post-Conditions
                        </span>
                        <div className="flex flex-wrap gap-1.5">
                          {Object.entries(m.expect).map(([k, v]) => (
                            <span
                              key={k}
                              className="px-2 py-0.5 rounded-lg bg-muted/60 text-muted-foreground border border-border/50 text-[10px]"
                            >
                              <span className="text-primary font-semibold">{k}:</span>{" "}
                              {typeof v === "object" ? JSON.stringify(v) : String(v)}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Affects & Blast Radius */}
            {(selectedDelta.affects || selectedDelta.blast_radius) && (
              <div className="space-y-3">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Crosshair className="h-3.5 w-3.5 text-rose-400" />
                  Impact &amp; Blast Radius
                </h4>

                <div className="p-4 rounded-2xl bg-muted/20 border border-border/60 text-xs space-y-3">
                  {/* Strategic Affects */}
                  {selectedDelta.affects && (
                    <div className="space-y-2">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
                        Directly Affected Specifications
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {Object.entries(selectedDelta.affects).map(([category, items]) =>
                          Array.isArray(items) && items.length > 0 ? (
                            items.map((item, idx) => (
                              <Badge key={`${category}-${idx}`} variant="outline" className="font-mono text-[10px]">
                                <span className="text-muted-foreground mr-1 capitalize">{category.slice(0, -1)}:</span>
                                <span className="font-semibold text-foreground">{item}</span>
                              </Badge>
                            ))
                          ) : null
                        )}
                      </div>
                    </div>
                  )}

                  {/* Calculated Blast Radius */}
                  {selectedDelta.blast_radius && (
                    <div className="space-y-2.5 pt-2 border-t border-border/40">
                      {selectedDelta.blast_radius.impact_level && (
                        <div className="flex items-center gap-2">
                          <span className="text-muted-foreground">Impact Level:</span>
                          <Badge variant="outline" className="font-mono text-[10px] uppercase font-bold text-amber-400">
                            {selectedDelta.blast_radius.impact_level}
                          </Badge>
                        </div>
                      )}

                      {selectedDelta.blast_radius.impacted_containers && selectedDelta.blast_radius.impacted_containers.length > 0 && (
                        <div>
                          <span className="text-muted-foreground">Impacted Containers: </span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {selectedDelta.blast_radius.impacted_containers.map((c: string, idx: number) => (
                              <span key={idx} className="font-mono text-[10px] px-2 py-0.5 rounded bg-muted text-primary border border-border/50">
                                {c}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {selectedDelta.blast_radius.impacted_components && selectedDelta.blast_radius.impacted_components.length > 0 && (
                        <div>
                          <span className="text-muted-foreground">Impacted Components: </span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {selectedDelta.blast_radius.impacted_components.map((c: string, idx: number) => (
                              <span key={idx} className="font-mono text-[10px] px-2 py-0.5 rounded bg-muted text-foreground border border-border/50">
                                {c}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {selectedDelta.blast_radius.invariants_to_enforce && selectedDelta.blast_radius.invariants_to_enforce.length > 0 && (
                        <div>
                          <span className="text-muted-foreground">Invariants to Enforce: </span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {selectedDelta.blast_radius.invariants_to_enforce.map((inv: string, idx: number) => (
                              <span key={idx} className="font-mono text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-bold">
                                {inv}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {selectedDelta.blast_radius.impacted_screens && selectedDelta.blast_radius.impacted_screens.length > 0 && (
                        <div>
                          <span className="text-muted-foreground">Impacted Screens: </span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {selectedDelta.blast_radius.impacted_screens.map((scr: string, idx: number) => (
                              <span key={idx} className="font-mono text-[10px] px-2 py-0.5 rounded bg-pink-500/10 text-pink-400 border border-pink-500/20">
                                {scr}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {selectedDelta.blast_radius.impacted_personas && selectedDelta.blast_radius.impacted_personas.length > 0 && (
                        <div>
                          <span className="text-muted-foreground">Impacted Personas: </span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {selectedDelta.blast_radius.impacted_personas.map((p: string, idx: number) => (
                              <span key={idx} className="font-mono text-[10px] px-2 py-0.5 rounded bg-muted text-muted-foreground border border-border/50">
                                {p}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Fallback legacy blast_radius properties */}
                      {selectedDelta.blast_radius.domains && selectedDelta.blast_radius.domains.length > 0 && (
                        <div className="text-xs">
                          <span className="text-muted-foreground">Affected Domains: </span>
                          <span className="font-mono font-semibold text-foreground">
                            {selectedDelta.blast_radius.domains.join(", ")}
                          </span>
                        </div>
                      )}
                      {selectedDelta.blast_radius.components && selectedDelta.blast_radius.components.length > 0 && !selectedDelta.blast_radius.impacted_components && (
                        <div className="text-xs">
                          <span className="text-muted-foreground">Affected Components: </span>
                          <span className="font-mono font-semibold text-foreground">
                            {selectedDelta.blast_radius.components.join(", ")}
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Living Specification Body rendered via AsciiDocRenderer */}
            {selectedDelta.body && (
              <div className="space-y-2 pt-2 border-t border-border/60">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                  Living Specification Document (`DLT-*`)
                </h4>
                <AsciiDocRenderer content={selectedDelta.body} />
              </div>
            )}
          </div>
        )}
      </Sheet>
    </div>
  );
}
