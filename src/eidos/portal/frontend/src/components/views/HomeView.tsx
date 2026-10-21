import * as React from "react";
import {
  CheckCircle2,
  AlertTriangle,
  Layers,
  Box,
  ShieldCheck,
  GitPullRequest,
  ArrowRight,
  Lightbulb,
  FileWarning,
  Link2Off,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { requestSpecNavigation } from "@/lib/specNav";
import type { PortalData } from "@/types";

export interface HomeViewProps {
  data: PortalData;
  onNavigateTab: (tab: any) => void;
  onInspectSpec: (spec: any) => void;
}

export function HomeView({ data, onNavigateTab, onInspectSpec }: HomeViewProps) {
  const { overview, validation } = data;
  const [diagnosticsOpen, setDiagnosticsOpen] = React.useState(false);
  const activeDeltasCount =
    (data.deltas?.proposed?.length || 0) +
    (data.deltas?.in_review?.length || 0) +
    (data.deltas?.approved?.length || 0);
  const appliedDeltasCount = data.deltas?.applied?.length || 0;
  const schemaErrorEntries = Object.entries(validation?.schema_errors || {});
  const brokenRefs = validation?.broken_references || [];
  const invalidSpecs = (data.specs || []).filter((s) => s.is_valid === false);

  const metricCards = [
    {
      label: "Living Specifications",
      value: overview.total_specs,
      subtext: `${overview.valid_specs} Validated`,
      icon: Layers,
      color: "text-blue-600 dark:text-blue-400",
      bg: "bg-blue-500/10 dark:bg-blue-400/15",
      tab: "catalog",
    },
    {
      label: "C4 Containers & Units",
      value: overview.components_count,
      subtext: "Deployable Units",
      icon: Box,
      color: "text-indigo-600 dark:text-indigo-400",
      bg: "bg-indigo-500/10 dark:bg-indigo-400/15",
      tab: "architecture",
    },
    {
      label: "Active SpecDeltas",
      value: activeDeltasCount,
      subtext: `${appliedDeltasCount} Applied • ${overview.deltas_count} Total`,
      icon: GitPullRequest,
      color: "text-purple-600 dark:text-purple-400",
      bg: "bg-purple-500/10 dark:bg-purple-400/15",
      tab: "deltas",
    },
    {
      label: "Technical Explorations",
      value: overview.explorations_count ?? (data.explorations?.length || 0),
      subtext: "Discovery Spikes",
      icon: Lightbulb,
      color: "text-amber-600 dark:text-amber-400",
      bg: "bg-amber-500/10 dark:bg-amber-400/15",
      tab: "explorations",
    },
    {
      label: "Invariants Enforced",
      value: data.invariants?.length || 0,
      subtext: "POL-002 Traceability",
      icon: ShieldCheck,
      color: "text-emerald-600 dark:text-emerald-400",
      bg: "bg-emerald-500/10 dark:bg-emerald-400/15",
      tab: "business",
    },
  ];

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Top Banner */}
      <div className="rounded-3xl p-6 sm:p-8 bg-gradient-to-r from-blue-600/10 via-purple-600/10 to-transparent border border-border/70 relative overflow-hidden">
        <div className="max-w-3xl space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs font-semibold px-3 py-1 rounded-full bg-primary/20 text-primary border border-primary/30">
              {overview.system_id}
            </span>
            <button
              type="button"
              onClick={() => setDiagnosticsOpen(true)}
              className="cursor-pointer transition-transform hover:scale-105"
              title="Open Validation & Governance Diagnostics Drawer"
            >
              <Badge variant={overview.is_clean ? "success" : "warning"}>
                {overview.is_clean ? "Zero Drift Verified" : "Drift Attention"}
              </Badge>
            </button>
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-foreground tracking-tight">
            {overview.system_title}
          </h1>
          <p className="text-sm text-muted-foreground leading-relaxed">
            Git-native living architecture documentation synthesized continuously
            from machine-readable AsciiDoc specifications, Arc42/C4 container models,
            and W3C DTCG design tokens. Zero dead documentation, zero ADRs.
          </p>
        </div>
      </div>

      {/* Metrics Row (UIC-PORTAL-METRIC-CARD) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
        {metricCards.map((m, idx) => {
          const Icon = m.icon;
          return (
            <Card
              key={idx}
              className="cursor-pointer hover:border-primary/40 group transition-all"
              onClick={() => onNavigateTab(m.tab)}
            >
              <CardContent className="p-5 flex items-center justify-between">
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">
                    {m.label}
                  </p>
                  <p className="text-2xl font-bold tracking-tight text-foreground font-mono">
                    {m.value}
                  </p>
                  <p className="text-[11px] text-muted-foreground flex items-center gap-1">
                    {m.subtext}
                  </p>
                </div>
                <div
                  className={`h-12 w-12 rounded-2xl flex items-center justify-center shrink-0 ${m.bg} ${m.color} group-hover:scale-110 transition-transform`}
                >
                  <Icon className="h-6 w-6" />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Core Architectural Pillars */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card className="lg:col-span-2">
          <div className="p-6 space-y-4">
            <h2 className="text-base font-bold text-foreground tracking-tight flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-primary" />
              Living System Invariants
            </h2>
            <div className="space-y-3">
              {(data.invariants || []).slice(0, 4).map((inv, i) => (
                <div
                  key={i}
                  className="p-3.5 rounded-xl border border-border/60 bg-muted/20 flex flex-col sm:flex-row sm:items-center justify-between gap-2 hover:bg-muted/40 transition-colors"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-semibold text-primary">
                        {inv.id}
                      </span>
                      <span className="text-xs font-medium text-foreground">
                        {inv.title || inv.category}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-1">
                      {inv.rule}
                    </p>
                  </div>
                  {inv.test_binding && (
                    <span className="text-[10px] font-mono text-muted-foreground px-2 py-0.5 rounded bg-muted/60 shrink-0">
                      {inv.test_binding}
                    </span>
                  )}
                </div>
              ))}
            </div>
            <div className="pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onNavigateTab("catalog")}
                className="gap-2 text-xs"
              >
                Inspect All Invariants & Specifications
                <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </Card>

        {/* Quick System Actions */}
        <Card>
          <div className="p-6 space-y-4">
            <h2 className="text-base font-bold text-foreground tracking-tight">
              Governance & Workflows
            </h2>
            <div className="space-y-3 text-xs text-muted-foreground">
              <div className="p-3 rounded-xl bg-muted/30 border border-border/60">
                <p className="font-semibold text-foreground mb-1">Direct Mode</p>
                <p>Routine local changes co-evolve code and living specifications in the same commit.</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/30 border border-border/60">
                <p className="font-semibold text-foreground mb-1">Planned Mode (SpecDeltas)</p>
                <p>High-impact, breaking mutations are scoped via formal SpecDeltas before application.</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/30 border border-border/60">
                <p className="font-semibold text-foreground mb-1">Zero ADR Policy</p>
                <p>Architecture evolves directly in living specs without static decision record graveyards.</p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDiagnosticsOpen(true)}
                className="w-full text-xs gap-1.5 mt-1"
              >
                <ShieldCheck className="h-3.5 w-3.5 text-primary" />
                Inspect Validation &amp; Governance Diagnostics
              </Button>
            </div>
          </div>
        </Card>
      </div>

      {/* Validation & Governance Diagnostics Drawer */}
      <Sheet
        open={diagnosticsOpen}
        onOpenChange={setDiagnosticsOpen}
        title="Validation & Governance Diagnostics"
        subtitle={`${overview.valid_specs}/${overview.total_specs} Specifications Valid`}
        maxWidth="3xl"
      >
        <div className="space-y-4 text-xs">
          <div className="p-4 rounded-2xl border border-border/80 bg-muted/20 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              {overview.is_clean ? (
                <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0" />
              ) : (
                <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0" />
              )}
              <div>
                <p className="font-bold text-foreground">
                  {overview.is_clean
                    ? "All Living Specifications & Cross-References Verified"
                    : "Schema or Reference Attention Required"}
                </p>
                <p className="text-muted-foreground">
                  {overview.valid_specs} of {overview.total_specs} documents conform to schema contracts •{" "}
                  {brokenRefs.length} broken cross-reference(s)
                </p>
              </div>
            </div>
            <Badge variant={overview.is_clean ? "success" : "warning"}>
              {overview.is_clean ? "CLEAN" : "ATTENTION"}
            </Badge>
          </div>

          {schemaErrorEntries.length > 0 && (
            <div className="space-y-2">
              <h4 className="font-bold text-rose-400 uppercase tracking-wider flex items-center gap-1.5">
                <FileWarning className="h-3.5 w-3.5" />
                Schema Validation Errors ({schemaErrorEntries.length})
              </h4>
              <div className="space-y-2">
                {schemaErrorEntries.map(([path, errs]) => (
                  <div
                    key={path}
                    className="p-3 rounded-xl border border-rose-500/30 bg-rose-500/5 space-y-1"
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setDiagnosticsOpen(false);
                        requestSpecNavigation(path);
                      }}
                      className="font-mono font-bold text-rose-400 hover:underline cursor-pointer text-left"
                    >
                      {path}
                    </button>
                    <ul className="list-disc pl-4 text-muted-foreground space-y-0.5">
                      {(errs || []).map((msg, i) => (
                        <li key={i}>{msg}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          )}

          {brokenRefs.length > 0 && (
            <div className="space-y-2">
              <h4 className="font-bold text-amber-400 uppercase tracking-wider flex items-center gap-1.5">
                <Link2Off className="h-3.5 w-3.5" />
                Broken Cross-References ({brokenRefs.length})
              </h4>
              <div className="space-y-1.5">
                {brokenRefs.map((ref, idx) => (
                  <div
                    key={idx}
                    className="p-2.5 rounded-xl border border-amber-500/30 bg-amber-500/5 font-mono text-[11px] text-foreground"
                  >
                    {ref}
                  </div>
                ))}
              </div>
            </div>
          )}

          {invalidSpecs.length > 0 && schemaErrorEntries.length === 0 && (
            <div className="space-y-2">
              <h4 className="font-bold text-rose-400 uppercase tracking-wider">
                Invalid Specification Documents ({invalidSpecs.length})
              </h4>
              {invalidSpecs.map((sp) => (
                <div
                  key={sp.id}
                  onClick={() => {
                    setDiagnosticsOpen(false);
                    onInspectSpec(sp);
                  }}
                  className="p-3 rounded-xl border border-rose-500/30 bg-rose-500/5 cursor-pointer hover:bg-rose-500/10"
                >
                  <div className="font-mono font-bold text-rose-400">
                    {sp.id} ({sp.path})
                  </div>
                  {(sp.errors || []).map((e, i) => (
                    <p key={i} className="text-muted-foreground mt-0.5">
                      {e}
                    </p>
                  ))}
                </div>
              ))}
            </div>
          )}

          {overview.is_clean && schemaErrorEntries.length === 0 && brokenRefs.length === 0 && (
            <div className="p-6 rounded-2xl border border-emerald-500/20 bg-emerald-500/5 text-center space-y-1.5">
              <p className="font-semibold text-emerald-400">
                Zero Schema Errors &amp; Zero Broken Cross-References
              </p>
              <p className="text-muted-foreground">
                Every living specification in this repository satisfies structural validation and invariant binding gates.
              </p>
            </div>
          )}
        </div>
      </Sheet>
    </div>
  );
}
