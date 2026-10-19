import * as React from "react";
import {
  Lightbulb,
  Compass,
  CheckCircle2,
  Clock,
  HelpCircle,
  XCircle,
  ArrowRight,
  GitPullRequest,
  Target,
  FileQuestion,
  FlaskConical,
  Gauge,
  BookOpen,
  Filter,
  Sparkles,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
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
  if (exp.decision?.resulting_delta_id && !deltas.includes(exp.decision.resulting_delta_id)) {
    deltas.push(exp.decision.resulting_delta_id);
  }
  return deltas;
}

export function getExplorationFindings(exp: SpecExploration): string {
  return exp.findings || exp.decision?.learnings || exp.decision?.findings || "";
}

export function getExplorationRecommendation(exp: SpecExploration): string {
  return exp.recommendation || exp.decision?.recommendation || "";
}

export function ExplorationsView({
  data,
  onNavigateDelta,
  initialSelectedId,
  initialSelectedNonce,
  onClearSelected,
}: ExplorationsViewProps) {
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
      // Status filter
      if (statusFilter !== "all" && exp.status !== statusFilter) {
        return false;
      }
      // Text filter
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
        deltas.some((d) => d.toLowerCase().includes(q))
      );
    });
  }, [rawExplorations, filterText, statusFilter]);

  // Counts by status
  const counts = React.useMemo(() => {
    const record: Record<string, number> = {
      all: rawExplorations.length,
      adopted: 0,
      in_progress: 0,
      proposed: 0,
      discarded: 0,
    };
    rawExplorations.forEach((e) => {
      const s = e.status || "proposed";
      record[s] = (record[s] || 0) + 1;
    });
    return record;
  }, [rawExplorations]);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "adopted":
        return (
          <Badge variant="success" className="capitalize flex items-center gap-1">
            <CheckCircle2 className="h-3 w-3" />
            Adopted
          </Badge>
        );
      case "in_progress":
        return (
          <Badge variant="primary" className="capitalize flex items-center gap-1">
            <Compass className="h-3 w-3 animate-spin" />
            In Progress
          </Badge>
        );
      case "proposed":
        return (
          <Badge variant="warning" className="capitalize flex items-center gap-1">
            <Clock className="h-3 w-3" />
            Proposed
          </Badge>
        );
      case "discarded":
        return (
          <Badge variant="danger" className="capitalize flex items-center gap-1">
            <XCircle className="h-3 w-3" />
            Discarded
          </Badge>
        );
      default:
        return (
          <Badge variant="outline" className="capitalize">
            {status}
          </Badge>
        );
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Top Banner & Filters */}
      <div className="space-y-4 p-5 sm:p-6 rounded-3xl bg-card border border-border md-elevation-1">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base sm:text-lg font-bold text-foreground tracking-tight flex items-center gap-2">
                <Lightbulb className="h-5 w-5 text-amber-500" />
                Technical Explorations & Architectural Spikes (`EXP-*`)
              </h2>
              <span className="font-mono text-xs px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 font-semibold border border-amber-500/20">
                {rawExplorations.length} Tracked
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Hypothesis-driven architectural research, feasibility benchmarks, and spike validations that graduate into formal SpecDeltas.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Search hypotheses, spikes, learnings..."
              className="bg-muted/50 border border-border text-xs rounded-xl px-3 py-1.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary w-full sm:w-72"
            />
          </div>
        </div>

        {/* Status Filter Buttons */}
        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/60">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1 mr-1">
            <Filter className="h-3 w-3" /> Status:
          </span>
          <button
            type="button"
            onClick={() => setStatusFilter("all")}
            className={cn(
              "px-3 py-1 rounded-xl text-xs font-medium transition-all cursor-pointer",
              statusFilter === "all"
                ? "bg-primary text-primary-foreground font-semibold shadow-xs"
                : "bg-muted/40 text-muted-foreground hover:text-foreground hover:bg-muted"
            )}
          >
            All ({counts.all})
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter("adopted")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1 rounded-xl text-xs font-medium transition-all cursor-pointer border",
              statusFilter === "adopted"
                ? "bg-emerald-500/15 text-emerald-500 dark:text-emerald-400 border-emerald-500/30 font-bold shadow-xs"
                : "border-border/60 bg-muted/30 text-muted-foreground hover:text-foreground"
            )}
          >
            <CheckCircle2 className="h-3 w-3 text-emerald-500" />
            <span>Adopted ({counts.adopted || 0})</span>
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter("proposed")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1 rounded-xl text-xs font-medium transition-all cursor-pointer border",
              statusFilter === "proposed"
                ? "bg-amber-500/15 text-amber-500 dark:text-amber-400 border-amber-500/30 font-bold shadow-xs"
                : "border-border/60 bg-muted/30 text-muted-foreground hover:text-foreground"
            )}
          >
            <Clock className="h-3 w-3 text-amber-500" />
            <span>Proposed ({counts.proposed || 0})</span>
          </button>
          {(counts.in_progress || 0) > 0 && (
            <button
              type="button"
              onClick={() => setStatusFilter("in_progress")}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1 rounded-xl text-xs font-medium transition-all cursor-pointer border",
                statusFilter === "in_progress"
                  ? "bg-blue-500/15 text-blue-500 dark:text-blue-400 border-blue-500/30 font-bold shadow-xs"
                  : "border-border/60 bg-muted/30 text-muted-foreground hover:text-foreground"
              )}
            >
              <Compass className="h-3 w-3 text-blue-500" />
              <span>In Progress ({counts.in_progress})</span>
            </button>
          )}
          {(counts.discarded || 0) > 0 && (
            <button
              type="button"
              onClick={() => setStatusFilter("discarded")}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1 rounded-xl text-xs font-medium transition-all cursor-pointer border",
                statusFilter === "discarded"
                  ? "bg-rose-500/15 text-rose-500 dark:text-rose-400 border-rose-500/30 font-bold shadow-xs"
                  : "border-border/60 bg-muted/30 text-muted-foreground hover:text-foreground"
              )}
            >
              <XCircle className="h-3 w-3 text-rose-500" />
              <span>Discarded ({counts.discarded})</span>
            </button>
          )}
        </div>
      </div>

      {/* Explorations Grid */}
      {filteredExplorations.length === 0 ? (
        <div className="py-16 text-center text-sm text-muted-foreground border border-dashed border-border/80 rounded-3xl p-8">
          No explorations match your filter.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {filteredExplorations.map((exp) => {
            const deltas = getExplorationDeltas(exp);
            const findings = getExplorationFindings(exp);
            return (
              <Card
                key={exp.id}
                className="p-5 space-y-4 cursor-pointer hover:border-primary/50 transition-all hover:md-elevation-2 flex flex-col justify-between group"
                onClick={() => setSelectedExp(exp)}
              >
                <div className="space-y-3">
                  {/* Header: ID, Status, Resulting Deltas */}
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-bold text-amber-500 dark:text-amber-400 group-hover:underline">
                        {exp.id}
                      </span>
                      {getStatusBadge(exp.status)}
                    </div>
                    {deltas.length > 0 && (
                      <div className="flex flex-wrap items-center gap-1.5">
                        {deltas.map((deltaId) => (
                          <button
                            key={deltaId}
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onNavigateDelta?.(deltaId);
                            }}
                            className="font-mono text-[11px] px-2 py-0.5 rounded-lg bg-purple-500/10 hover:bg-purple-500/20 text-purple-600 dark:text-purple-400 border border-purple-500/20 flex items-center gap-1 transition-colors cursor-pointer"
                            title={`Jump to SpecDelta ${deltaId}`}
                          >
                            <GitPullRequest className="h-3 w-3" />
                            <span>{deltaId}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Title */}
                  <h3 className="text-sm font-bold text-foreground leading-snug group-hover:text-primary transition-colors">
                    {exp.title}
                  </h3>

                  {/* Hypothesis Callout */}
                  <div className="p-3.5 rounded-2xl bg-amber-500/5 border border-amber-500/15 text-xs text-muted-foreground space-y-1">
                    <div className="flex items-center gap-1.5 font-semibold text-amber-600 dark:text-amber-400 text-[11px] uppercase tracking-wider">
                      <Sparkles className="h-3.5 w-3.5" />
                      Hypothesis
                    </div>
                    <p className="line-clamp-3 leading-relaxed">
                      {exp.hypothesis || "Investigating feasibility and architectural trade-offs."}
                    </p>
                  </div>
                </div>

                {/* Footer Specs & Learnings Preview */}
                <div className="space-y-3 pt-3 border-t border-border/60">
                  {findings && (
                    <p className="text-xs text-muted-foreground line-clamp-2 italic leading-relaxed">
                      "{findings}"
                    </p>
                  )}

                  <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono text-muted-foreground">
                    <div className="flex items-center gap-3">
                      {exp.questions_to_answer && exp.questions_to_answer.length > 0 && (
                        <span className="flex items-center gap-1">
                          <FileQuestion className="h-3 w-3 text-primary" />
                          {exp.questions_to_answer.length} question(s)
                        </span>
                      )}
                      {exp.spikes_to_run && exp.spikes_to_run.length > 0 && (
                        <span className="flex items-center gap-1">
                          <FlaskConical className="h-3 w-3 text-purple-400" />
                          {exp.spikes_to_run.length} spike(s)
                        </span>
                      )}
                    </div>
                    <span className="flex items-center gap-1 text-primary font-sans font-medium group-hover:translate-x-0.5 transition-transform text-xs">
                      Inspect Investigation <ArrowRight className="h-3.5 w-3.5" />
                    </span>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* Exploration Detail Modal (Centered Sheet with AsciiDocRenderer) */}
      <Sheet
        open={Boolean(selectedExp)}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedExp(null);
            onClearSelected?.();
          }
        }}
        title={selectedExp?.title || "Architectural Exploration"}
        subtitle={selectedExp?.id}
        maxWidth="6xl"
      >
        {selectedExp && (() => {
          const deltas = getExplorationDeltas(selectedExp);
          const findings = getExplorationFindings(selectedExp);
          const recommendation = getExplorationRecommendation(selectedExp);
          const outcome = selectedExp.decision?.outcome || "";
          return (
            <div className="space-y-6">
              {/* Top Meta Badges & Resulting Delta Bridge */}
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  {getStatusBadge(selectedExp.status)}
                  {outcome && (
                    <Badge variant="outline" className="font-mono text-xs uppercase">
                      Outcome: {outcome}
                    </Badge>
                  )}
                  {selectedExp.path && (
                    <span className="text-xs font-mono text-muted-foreground bg-muted/50 px-2.5 py-1 rounded-xl">
                      {selectedExp.path}
                    </span>
                  )}
                </div>

                {deltas.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2">
                    {deltas.map((deltaId) => (
                      <button
                        key={deltaId}
                        type="button"
                        onClick={() => {
                          onNavigateDelta?.(deltaId);
                        }}
                        className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-purple-500/10 hover:bg-purple-500/20 text-purple-600 dark:text-purple-400 border border-purple-500/30 text-xs font-mono font-semibold transition-all cursor-pointer"
                        title="Jump to resulting SpecDelta in Planned Mode"
                      >
                        <GitPullRequest className="h-3.5 w-3.5" />
                        <span>Resulting Delta: {deltaId}</span>
                        <ArrowRight className="h-3 w-3" />
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Core Hypothesis */}
              <div className="space-y-2">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Lightbulb className="h-4 w-4 text-amber-500" />
                  Core Hypothesis Under Investigation
                </h4>
                <p className="text-xs sm:text-sm text-foreground leading-relaxed p-4 rounded-2xl bg-amber-500/10 border border-amber-500/20">
                  {selectedExp.hypothesis}
                </p>
              </div>

              {/* Architectural Decision & Empirical Learnings */}
              {(outcome || findings || recommendation) && (
                <div className="space-y-2">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                    Architectural Decision & Empirical Findings
                  </h4>
                  <div className="p-4 rounded-2xl bg-muted/30 border border-border/70 space-y-2 text-xs">
                    {outcome && (
                      <div className="flex items-center gap-2">
                        <span className="text-muted-foreground font-semibold">Outcome:</span>
                        <Badge
                          variant={outcome === "adopted" ? "success" : "outline"}
                          className="capitalize"
                        >
                          {outcome}
                        </Badge>
                      </div>
                    )}
                    {findings && (
                      <p className="text-foreground leading-relaxed pl-1 pt-1 italic">
                        "{findings}"
                      </p>
                    )}
                    {recommendation && recommendation !== outcome && (
                      <p className="text-muted-foreground leading-relaxed pl-1 pt-1">
                        <span className="font-semibold text-foreground">Recommendation: </span>
                        {recommendation}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Questions to Answer */}
              {selectedExp.questions_to_answer && selectedExp.questions_to_answer.length > 0 && (
                <div className="space-y-2.5">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <FileQuestion className="h-4 w-4 text-primary" />
                    Questions to Answer ({selectedExp.questions_to_answer.length})
                  </h4>
                  <div className="space-y-2">
                    {selectedExp.questions_to_answer.map((q, idx) => (
                      <div
                        key={idx}
                        className="p-3 rounded-2xl border border-border/60 bg-muted/20 text-xs flex items-start gap-2.5"
                      >
                        <span className="h-5 w-5 rounded-full bg-primary/10 text-primary font-mono text-[10px] flex items-center justify-center shrink-0 font-bold">
                          {idx + 1}
                        </span>
                        <p className="text-foreground leading-relaxed">{q}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Spikes to Run */}
              {selectedExp.spikes_to_run && selectedExp.spikes_to_run.length > 0 && (
                <div className="space-y-2.5">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <FlaskConical className="h-4 w-4 text-purple-400" />
                    Empirical Spikes to Run ({selectedExp.spikes_to_run.length})
                  </h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {selectedExp.spikes_to_run.map((spike, idx) => (
                      <div
                        key={idx}
                        className="p-3.5 rounded-2xl border border-border/70 bg-card text-xs space-y-1.5"
                      >
                        <div className="font-mono font-bold text-primary flex items-center gap-1.5 text-[11px]">
                          <span className="h-2 w-2 rounded-full bg-primary" />
                          {spike.name}
                        </div>
                        <p className="text-muted-foreground text-[11px] leading-relaxed">
                          {spike.target}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Exit Criteria */}
              {selectedExp.exit_criteria && (
                <div className="space-y-2">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <Gauge className="h-4 w-4 text-indigo-400" />
                    Exit Criteria & Benchmarks
                  </h4>
                  {Array.isArray(selectedExp.exit_criteria) ? (
                    <div className="p-3.5 rounded-2xl bg-muted/20 border border-border/60 text-xs space-y-1.5">
                      {selectedExp.exit_criteria.map((crit, idx) => (
                        <div key={idx} className="font-mono text-xs text-foreground">
                          • {crit}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="p-3.5 rounded-2xl bg-muted/20 border border-border/60 text-xs grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {selectedExp.exit_criteria.max_duration && (
                        <div className="space-y-0.5">
                          <span className="text-[10px] font-semibold text-muted-foreground uppercase">
                            Max Duration
                          </span>
                          <p className="font-mono font-bold text-foreground">
                            {selectedExp.exit_criteria.max_duration}
                          </p>
                        </div>
                      )}
                      {selectedExp.exit_criteria.benchmark && (
                        <div className="space-y-0.5 sm:col-span-2">
                          <span className="text-[10px] font-semibold text-muted-foreground uppercase">
                            Validation Benchmark
                          </span>
                          <p className="font-mono text-xs text-foreground bg-muted/40 p-2 rounded-xl">
                            {selectedExp.exit_criteria.benchmark}
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Full Living Specification Document */}
              {selectedExp.body && (
                <div className="space-y-2 pt-2 border-t border-border/60">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                    Living Specification Document (`EXP-*`)
                  </h4>
                  <AsciiDocRenderer content={selectedExp.body} />
                </div>
              )}
            </div>
          );
        })()}
      </Sheet>
    </div>
  );
}
