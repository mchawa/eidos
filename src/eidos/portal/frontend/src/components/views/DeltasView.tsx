import * as React from "react";
import { GitPullRequest, Clock, CheckCircle2, AlertCircle, ArrowRight, FileDiff } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import type { PortalData, SpecDelta } from "@/types";

export interface DeltasViewProps {
  data: PortalData;
}

export function DeltasView({ data }: DeltasViewProps) {
  const [selectedDelta, setSelectedDelta] = React.useState<SpecDelta | null>(null);
  const [filterText, setFilterText] = React.useState<string>("");

  const columns: Array<{
    key: "proposed" | "in_review" | "approved" | "applied";
    label: string;
    icon: any;
    color: string;
    bg: string;
  }> = [
    {
      key: "proposed",
      label: "Proposed",
      icon: Clock,
      color: "text-amber-500",
      bg: "bg-amber-500/10",
    },
    {
      key: "in_review",
      label: "In Review",
      icon: AlertCircle,
      color: "text-blue-500",
      bg: "bg-blue-500/10",
    },
    {
      key: "approved",
      label: "Approved",
      icon: GitPullRequest,
      color: "text-purple-500",
      bg: "bg-purple-500/10",
    },
    {
      key: "applied",
      label: "Applied",
      icon: CheckCircle2,
      color: "text-emerald-500",
      bg: "bg-emerald-500/10",
    },
  ];

  const deltasByStatus = React.useMemo(() => {
    const raw = data.deltas || { proposed: [], in_review: [], approved: [], applied: [] };
    const q = filterText.toLowerCase();

    const filterList = (list: SpecDelta[]) => {
      if (!q) return list || [];
      return (list || []).filter(
        (d) =>
          d.id.toLowerCase().includes(q) ||
          d.title.toLowerCase().includes(q) ||
          (d.motivation && d.motivation.toLowerCase().includes(q))
      );
    };

    return {
      proposed: filterList(raw.proposed),
      in_review: filterList(raw.in_review),
      approved: filterList(raw.approved),
      applied: filterList(raw.applied),
    };
  }, [data.deltas, filterText]);

  return (
    <div className="space-y-4 animate-in fade-in duration-200">
      {/* Header & Filter */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 rounded-2xl bg-card border border-border md-elevation-1">
        <div>
          <h2 className="text-base font-bold text-foreground tracking-tight">
            SpecDelta Mutation Board (Planned Mode)
          </h2>
          <p className="text-xs text-muted-foreground">
            Auditable architectural mutations governed by formal SpecDeltas (`DLT-*`).
          </p>
        </div>
        <input
          type="text"
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          placeholder="Filter deltas..."
          className="bg-muted/50 border border-border text-xs rounded-xl px-3 py-1.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary w-48 sm:w-64"
        />
      </div>

      {/* Kanban Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 items-start">
        {columns.map((col) => {
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
                  <div className="py-8 text-center text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-xl">
                    No {col.label.toLowerCase()} deltas
                  </div>
                ) : (
                  list.map((delta) => (
                    <Card
                      key={delta.id}
                      className="p-3.5 space-y-2 cursor-pointer hover:border-primary/50 transition-all hover:md-elevation-2 group"
                      onClick={() => setSelectedDelta(delta)}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-xs font-bold text-primary group-hover:underline">
                          {delta.id}
                        </span>
                        <Badge variant="outline" className="text-[10px]">
                          {delta.kind}
                        </Badge>
                      </div>
                      <h4 className="text-xs font-medium text-foreground line-clamp-2 leading-relaxed">
                        {delta.title}
                      </h4>
                      {delta.mutations && delta.mutations.length > 0 && (
                        <div className="pt-2 flex items-center gap-1.5 text-[10px] text-muted-foreground font-mono">
                          <FileDiff className="h-3 w-3 text-primary" />
                          <span>{delta.mutations.length} declared mutation(s)</span>
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

      {/* Delta Detail Sheet */}
      <Sheet
        open={Boolean(selectedDelta)}
        onOpenChange={(open) => !open && setSelectedDelta(null)}
        title={selectedDelta?.title || "SpecDelta Details"}
        subtitle={selectedDelta?.id}
      >
        {selectedDelta && (
          <div className="space-y-5">
            <div className="flex flex-wrap gap-2">
              <Badge variant="primary">{selectedDelta.status}</Badge>
              <Badge variant="outline">{selectedDelta.kind}</Badge>
            </div>

            <div className="space-y-2">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                Motivation & Strategic Intent
              </h4>
              <p className="text-xs text-muted-foreground leading-relaxed p-3 rounded-xl bg-muted/20 border border-border/60">
                {selectedDelta.motivation || "Standard architectural evolution."}
              </p>
            </div>

            <div className="space-y-2">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center justify-between">
                <span>Declared Mutations</span>
                <span className="font-mono text-muted-foreground">
                  ({selectedDelta.mutations?.length || 0})
                </span>
              </h4>
              <div className="space-y-2">
                {(selectedDelta.mutations || []).map((m, i) => (
                  <div
                    key={i}
                    className="p-3 rounded-xl border border-border/70 bg-muted/30 text-xs space-y-1"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-primary uppercase font-mono text-[10px] px-1.5 py-0.5 rounded bg-primary/10">
                        {m.action}
                      </span>
                      <span className="font-mono font-semibold text-foreground">
                        {m.target_component ||
                          m.target_capability ||
                          m.target_domain ||
                          m.target_screen ||
                          "target"}
                      </span>
                    </div>
                    {m.details && (
                      <p className="text-muted-foreground text-[11px] leading-relaxed">
                        {m.details}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </Sheet>
    </div>
  );
}
