import * as React from "react";
import {
  Lightbulb,
  Compass,
  CheckCircle2,
  Clock,
  XCircle,
  ArrowRight,
  GitPullRequest,
  Gauge,
  BookOpen,
  Filter,
  ChevronRight,
  ExternalLink,
  Archive,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { hasSpecBodyOrChunk } from "@/lib/specChunks";
import { requestSpecNavigation } from "@/lib/specNav";
import {
  formatDisplayTitle,
  formatStatus,
  humanizeToken,
  resolveEntityTitle,
  useEntityLabels,
} from "@/lib/presentation";
import { cn } from "@/lib/utils";
import type { PortalData, SpecExploration } from "@/types";

export interface ExplorationsViewProps {
  data: PortalData;
  onNavigateDelta?: (deltaId: string) => void;
  initialSelectedId?: string;
  initialSelectedNonce?: number;
  onClearSelected?: () => void;
}

export function getExplorationDeltas(exp: SpecExploration): string[] {
  const deltas: string[] = [];
  if (Array.isArray(exp.resulting_deltas)) {
    exp.resulting_deltas.forEach((d) => {
      if (d && !deltas.includes(d)) deltas.push(d);
    });
  }
  if (Array.isArray(exp.decision?.resulting_deltas)) {
    exp.decision!.resulting_deltas.forEach((d) => {
      if (d && !deltas.includes(d)) deltas.push(d);
    });
  }
  return deltas;
}

export function getExplorationFindings(exp: SpecExploration): string {
  return exp.findings || exp.decision?.learnings || exp.decision?.findings || "";
}

export function getExplorationRecommendation(exp: SpecExploration): string {
  return exp.recommendation || exp.decision?.recommendation || "";
}

export function isExplorationArchived(exp: SpecExploration): boolean {
  return Boolean(
    exp.is_archived ||
      exp.status === "archived" ||
      (exp.path && exp.path.replace(/\\/g, "/").includes("explorations/archive/"))
  );
}

export function ExplorationsView({
  data,
  onNavigateDelta,
  initialSelectedId,
  initialSelectedNonce,
  onClearSelected,
}: ExplorationsViewProps) {
  const labelIndex = useEntityLabels();
  const [selectedExp, setSelectedExp] = React.useState<SpecExploration | null>(null);
  const [filterText, setFilterText] = React.useState<string>("");
  const [statusFilter, setStatusFilter] = React.useState<string>("all");

  const rawExplorations = data.explorations || [];

  React.useEffect(() => {
    if (initialSelectedId && rawExplorations.length > 0) {
      const match = rawExplorations.find((e) => e.id === initialSelectedId);
      if (match) {
        setSelectedExp(match);
      }
    }
  }, [initialSelectedId, initialSelectedNonce, rawExplorations]);

  // Filtered explorations
  const filteredExplorations = React.useMemo(() => {
    const q = filterText.toLowerCase();
    return rawExplorations.filter((exp) => {
      const archived = isExplorationArchived(exp);
      if (statusFilter === "archived") {
        if (!archived) return false;
      } else if (statusFilter !== "all") {
        if (archived || exp.status !== statusFilter) return false;
      }
      if (!q) return true;
      const findings = getExplorationFindings(exp).toLowerCase();
      const recommendation = getExplorationRecommendation(exp).toLowerCase();
      const deltas = getExplorationDeltas(exp);
      return (
        exp.id.toLowerCase().includes(q) ||
        exp.title.toLowerCase().includes(q) ||
        (exp.hypothesis && exp.hypothesis.toLowerCase().includes(q)) ||
        findings.includes(q) ||
        recommendation.includes(q) ||
        deltas.some(
          (d) =>
            d.toLowerCase().includes(q) ||
            resolveEntityTitle(d, labelIndex).toLowerCase().includes(q)
        )
      );
    });
  }, [rawExplorations, filterText, statusFilter, labelIndex]);

  // Counts by status
  const counts = React.useMemo(() => {
    const record: Record<string, number> = {
      all: rawExplorations.length,
      adopted: 0,
      in_progress: 0,
      proposed: 0,
      discarded: 0,
      archived: 0,
    };
    rawExplorations.forEach((e) => {
      if (isExplorationArchived(e)) {
        record.archived = (record.archived || 0) + 1;
      } else {
        const s = e.status || "proposed";
        record[s] = (record[s] || 0) + 1;
      }
    });
    return record;
  }, [rawExplorations]);

  const getStatusBadge = (status: string, isArchived?: boolean) => {
    const baseBadge = (() => {
      switch (status) {
        case "adopted":
          return (
            <Badge variant="success" className="flex items-center gap-1">
              <CheckCircle2 className="h-3 w-3" />
              Adopted
            </Badge>
          );
        case "in_progress":
          return (
            <Badge variant="primary" className="flex items-center gap-1">
              <Compass className="h-3 w-3 animate-spin" />
              In Progress
            </Badge>
          );
        case "proposed":
          return (
            <Badge variant="warning" className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              Proposed
            </Badge>
          );
        case "discarded":
          return (
            <Badge variant="danger" className="flex items-center gap-1">
              <XCircle className="h-3 w-3" />
              Discarded
            </Badge>
          );
        case "archived":
          return null;
        default:
          return (
            <Badge variant="outline">
              {formatStatus(status)}
            </Badge>
          );
      }
    })();

    if (!isArchived && status !== "archived") {
      return baseBadge;
    }

    return (
      <span className="inline-flex items-center gap-1.5">
        <Badge variant="outline" className="flex items-center gap-1 border-slate-500/30 text-slate-400">
          <Archive className="h-3 w-3" />
          Archived
        </Badge>
        {baseBadge}
      </span>
    );
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Compact Borderless Header & Filters */}
      <header className="space-y-4 pb-5 border-b border-border/70">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-2">
                <Lightbulb className="h-5 w-5 text-amber-500" aria-hidden="true" />
                Explorations &amp; Research
              </h1>
              <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 font-semibold">
                {rawExplorations.length}
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Research investigations, experiments, and findings that inform future system changes.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Search hypotheses, research, learnings..."
              className="bg-muted/40 border border-border/80 text-xs rounded-lg px-3 py-1.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary w-full sm:w-72"
            />
          </div>
        </div>

        {/* Status Filter Buttons */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1 mr-1">
            <Filter className="h-3 w-3" aria-hidden="true" /> Status:
          </span>
          <button
            type="button"
            onClick={() => setStatusFilter("all")}
            className={cn(
              "px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer",
              statusFilter === "all"
                ? "bg-primary text-primary-foreground font-semibold"
                : "bg-muted/40 text-muted-foreground hover:text-foreground hover:bg-muted"
            )}
          >
            All ({counts.all})
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter("adopted")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer border",
              statusFilter === "adopted"
                ? "bg-emerald-500/15 text-emerald-500 dark:text-emerald-400 border-emerald-500/30 font-bold"
                : "border-border/50 bg-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <CheckCircle2 className="h-3 w-3 text-emerald-500" aria-hidden="true" />
            <span>Adopted ({counts.adopted || 0})</span>
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter("proposed")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer border",
              statusFilter === "proposed"
                ? "bg-amber-500/15 text-amber-500 dark:text-amber-400 border-amber-500/30 font-bold"
                : "border-border/50 bg-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Clock className="h-3 w-3 text-amber-500" aria-hidden="true" />
            <span>Proposed ({counts.proposed || 0})</span>
          </button>
          {(counts.in_progress || 0) > 0 && (
            <button
              type="button"
              onClick={() => setStatusFilter("in_progress")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer border",
                statusFilter === "in_progress"
                  ? "bg-blue-500/15 text-blue-500 dark:text-blue-400 border-blue-500/30 font-bold"
                  : "border-border/50 bg-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              <Compass className="h-3 w-3 text-blue-500" aria-hidden="true" />
              <span>In Progress ({counts.in_progress})</span>
            </button>
          )}
          {(counts.discarded || 0) > 0 && (
            <button
              type="button"
              onClick={() => setStatusFilter("discarded")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer border",
                statusFilter === "discarded"
                  ? "bg-rose-500/15 text-rose-500 dark:text-rose-400 border-rose-500/30 font-bold"
                  : "border-border/50 bg-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              <XCircle className="h-3 w-3 text-rose-500" aria-hidden="true" />
              <span>Discarded ({counts.discarded})</span>
            </button>
          )}
          <button
            type="button"
            onClick={() => setStatusFilter("archived")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all cursor-pointer border",
              statusFilter === "archived"
                ? "bg-slate-500/15 text-slate-500 dark:text-slate-400 border-slate-500/30 font-bold"
                : "border-border/50 bg-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Archive className="h-3 w-3 text-slate-400" aria-hidden="true" />
            <span>Archived ({counts.archived || 0})</span>
          </button>
        </div>
      </header>

      {/* Scannable Divided List of Explorations */}
      {filteredExplorations.length === 0 ? (
        <div className="py-12 text-center text-sm text-muted-foreground border-y border-dashed border-border/80">
          No explorations match your filter.
        </div>
      ) : (
        <div className="divide-y divide-border/70 border-y border-border/70">
          {filteredExplorations.map((exp) => {
            const deltas = getExplorationDeltas(exp);
            const findings = getExplorationFindings(exp);
            const recommendation = getExplorationRecommendation(exp);
            const exitCriteriaCount = Array.isArray(exp.exit_criteria)
              ? exp.exit_criteria.length
              : exp.exit_criteria
              ? Object.keys(exp.exit_criteria).length
              : 0;
            return (
              <div
                key={exp.id}
                role="button"
                tabIndex={0}
                onClick={() => setSelectedExp(exp)}
                onKeyDown={(e) => {
                  if (e.target !== e.currentTarget) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedExp(exp);
                  }
                }}
                className="py-4 px-2 -mx-2 rounded-lg cursor-pointer hover:bg-muted/30 focus-visible:outline-2 focus-visible:outline-primary transition-colors flex flex-col gap-2 group"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="space-y-1 min-w-0 flex-1 max-w-3xl">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-bold text-foreground group-hover:text-primary transition-colors">
                        {formatDisplayTitle(exp.title, exp.id)}
                      </h3>
                      {getStatusBadge(exp.status, isExplorationArchived(exp))}
                    </div>
                    <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2">
                      {exp.hypothesis || "Investigating feasibility and design trade-offs."}
                    </p>
                  </div>

                  <div className="flex items-center gap-2 text-xs shrink-0">
                    {exitCriteriaCount > 0 && (
                      <span className="text-[11px] text-muted-foreground">
                        {exitCriteriaCount} {exitCriteriaCount === 1 ? "criterion" : "criteria"}
                      </span>
                    )}
                    <ChevronRight className="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" aria-hidden="true" />
                  </div>
                </div>

                {(findings || (recommendation && recommendation !== exp.status && recommendation !== findings) || deltas.length > 0) && (
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                    <div className="min-w-0 flex-1 max-w-3xl">
                      {findings && (
                        <p className="line-clamp-1 italic">"{findings}"</p>
                      )}
                    </div>
                    {deltas.length > 0 && (
                      <div className="flex flex-wrap items-center gap-1.5 shrink-0">
                        {deltas.map((deltaId) => {
                          const deltaTitle = resolveEntityTitle(deltaId, labelIndex, "Resulting Change");
                          return (
                            <button
                              key={deltaId}
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onNavigateDelta?.(deltaId);
                              }}
                              className="text-[11px] font-medium px-2 py-0.5 rounded-md bg-purple-500/10 hover:bg-purple-500/20 text-purple-600 dark:text-purple-400 border border-purple-500/20 flex items-center gap-1 transition-colors cursor-pointer max-w-[240px]"
                              title={`Open ${deltaTitle}`}
                            >
                              <GitPullRequest className="h-3 w-3 shrink-0" aria-hidden="true" />
                              <span className="truncate">{deltaTitle}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Exploration Detail Modal */}
      <Sheet
        open={Boolean(selectedExp)}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedExp(null);
            onClearSelected?.();
          }
        }}
        title={selectedExp ? formatDisplayTitle(selectedExp.title, selectedExp.id) : "Exploration"}
        subtitle={selectedExp ? formatStatus(selectedExp.status) : undefined}
        maxWidth="6xl"
      >
        {selectedExp && (() => {
          const deltas = getExplorationDeltas(selectedExp);
          const findings = getExplorationFindings(selectedExp);
          const recommendation = getExplorationRecommendation(selectedExp);
          const outcome = selectedExp.decision?.outcome || "";
          const hasCriteria = Array.isArray(selectedExp.exit_criteria)
            ? selectedExp.exit_criteria.length > 0
            : Boolean(
                selectedExp.exit_criteria &&
                  Object.keys(selectedExp.exit_criteria).length > 0
              );
          return (
            <div className="space-y-5">
              {/* Top Meta Badges & Reader Escalation */}
              <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-border/60">
                <div className="flex flex-wrap items-center gap-2">
                  {getStatusBadge(selectedExp.status, isExplorationArchived(selectedExp))}
                  {outcome && (
                    <Badge variant="outline" className="text-xs">
                      Outcome: {formatStatus(outcome)}
                    </Badge>
                  )}
                  {deltas.map((deltaId) => {
                    const deltaTitle = resolveEntityTitle(deltaId, labelIndex, "Resulting Change");
                    return (
                      <button
                        key={deltaId}
                        type="button"
                        onClick={() => onNavigateDelta?.(deltaId)}
                        className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md bg-purple-500/10 hover:bg-purple-500/20 text-purple-600 dark:text-purple-400 border border-purple-500/20 text-xs font-medium cursor-pointer max-w-sm"
                        title={`Open ${deltaTitle}`}
                      >
                        <GitPullRequest className="h-3 w-3 shrink-0" aria-hidden="true" />
                        <span className="truncate">{deltaTitle}</span>
                        <ArrowRight className="h-3 w-3 shrink-0" aria-hidden="true" />
                      </button>
                    );
                  })}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    const id = selectedExp.id;
                    setSelectedExp(null);
                    onClearSelected?.();
                    requestSpecNavigation(id, { mode: "reader" });
                  }}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer transition-colors"
                >
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Open in Full Reader</span>
                </button>
              </div>

              {/* Core Hypothesis */}
              {selectedExp.hypothesis && (
                <blockquote className="pl-4 py-1 border-l-3 border-amber-500 text-sm text-foreground/90 leading-relaxed max-w-3xl">
                  {selectedExp.hypothesis}
                </blockquote>
              )}

              {/* Decision, Findings & Collapsible Success Criteria */}
              {(findings || recommendation || hasCriteria) && (
                <div className="divide-y divide-border/60 border-y border-border/60 text-xs">
                  {(findings || (recommendation && recommendation !== outcome)) && (
                    <div className="py-3 space-y-1.5 max-w-3xl">
                      {findings && (
                        <p className="text-foreground leading-relaxed italic">
                          "{findings}"
                        </p>
                      )}
                      {recommendation && recommendation !== outcome && (
                        <p className="text-muted-foreground leading-relaxed">
                          <span className="font-semibold text-foreground">Recommendation: </span>
                          {recommendation}
                        </p>
                      )}
                    </div>
                  )}

                  {hasCriteria && (
                    <details className="portal-disclosure py-2.5">
                      <summary className="flex items-center gap-2 text-xs font-semibold text-foreground select-none">
                        <ChevronRight className="disclosure-chevron h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                        <Gauge className="h-3.5 w-3.5 text-indigo-400 shrink-0" aria-hidden="true" />
                        <span>Success Criteria &amp; Benchmarks</span>
                      </summary>
                      <div className="pt-2.5 pl-5">
                        {Array.isArray(selectedExp.exit_criteria) ? (
                          <ul className="list-disc pl-4 space-y-1 text-xs text-foreground/90">
                            {selectedExp.exit_criteria.map((crit, idx) => (
                              <li key={idx}>{crit}</li>
                            ))}
                          </ul>
                        ) : (
                          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            {Object.entries(selectedExp.exit_criteria || {}).map(([key, val]) => (
                              <div key={key} className={cn(key === "benchmark" && "sm:col-span-2")}>
                                <dt className="text-[10px] font-semibold text-muted-foreground uppercase">
                                  {humanizeToken(key)}
                                </dt>
                                <dd className="text-xs text-foreground mt-0.5">
                                  {typeof val === "object" ? JSON.stringify(val) : String(val)}
                                </dd>
                              </div>
                            ))}
                          </dl>
                        )}
                      </div>
                    </details>
                  )}
                </div>
              )}

              {/* Full Document */}
              {hasSpecBodyOrChunk(selectedExp.id, selectedExp.body) && (
                <div className="space-y-2 pt-1">
                  <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <BookOpen className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                    Full Exploration Document
                  </h4>
                  <AsciiDocRenderer
                    specId={selectedExp.id}
                    content={selectedExp.body || ""}
                    measure="full"
                    hideLeadingTitle={true}
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
