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
  Activity,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { requestSpecNavigation } from "@/lib/specNav";
import {
  formatDisplayTitle,
  humanizeInvariantTitle,
  resolveEntityTitle,
  useEntityLabels,
} from "@/lib/presentation";
import type { PortalData, SpecDriftStatus } from "@/types";

export interface HomeViewProps {
  data: PortalData;
  driftStatus?: SpecDriftStatus | null;
  isLiveMode?: boolean;
  onNavigateTab: (tab: any) => void;
  onInspectSpec: (spec: any) => void;
}

function normalizeSchemaErrorPath(rawPath: string): string {
  const slash = (rawPath || "").replace(/\\/g, "/");
  const idx = slash.lastIndexOf("/eidos/");
  if (idx >= 0) return slash.slice(idx + 1);
  return slash.replace(/^\.\//, "");
}

export function HomeView({
  data,
  driftStatus,
  isLiveMode = false,
  onNavigateTab,
  onInspectSpec,
}: HomeViewProps) {
  const { overview, validation } = data;
  const labelIndex = useEntityLabels();
  const [diagnosticsOpen, setDiagnosticsOpen] = React.useState(false);
  const activeDeltasCount =
    (data.deltas?.proposed?.length || 0) +
    (data.deltas?.in_review?.length || 0) +
    (data.deltas?.approved?.length || 0);
  const appliedDeltasCount = data.deltas?.applied?.length || 0;
  const schemaErrorEntries = Object.entries(validation?.schema_errors || {});
  const brokenRefs = validation?.broken_references || [];
  const advisoryWarnings = validation?.warnings || [];
  const invalidSpecs = (data.specs || []).filter((s) => s.is_valid === false);

  const driftClean = driftStatus
    ? Boolean(driftStatus.is_clean && !driftStatus.drift_detected && !driftStatus.error)
    : Boolean(overview.is_clean);
  const driftImpactedFiles = [
    ...(driftStatus?.changed_files || []),
    ...(driftStatus?.unmapped_files || []),
  ];
  const driftTouchedComponents = driftStatus?.touched_components || [];

  const metricCards = [
    {
      label: "Specifications",
      value: overview.total_specs,
      subtext: `${overview.valid_specs} Verified`,
      icon: Layers,
      color: "text-blue-600 dark:text-blue-400",
      bg: "bg-blue-500/10 dark:bg-blue-400/15",
      tab: "catalog",
    },
    {
      label: "System Containers",
      value: overview.components_count,
      subtext: "Services & Apps",
      icon: Box,
      color: "text-indigo-600 dark:text-indigo-400",
      bg: "bg-indigo-500/10 dark:bg-indigo-400/15",
      tab: "architecture",
    },
    {
      label: "Active Changes",
      value: activeDeltasCount,
      subtext: `${appliedDeltasCount} Applied • ${overview.deltas_count} Total`,
      icon: GitPullRequest,
      color: "text-purple-600 dark:text-purple-400",
      bg: "bg-purple-500/10 dark:bg-purple-400/15",
      tab: "deltas",
    },
    {
      label: "Explorations",
      value: overview.explorations_count ?? (data.explorations?.length || 0),
      subtext: "Research & Spikes",
      icon: Lightbulb,
      color: "text-amber-600 dark:text-amber-400",
      bg: "bg-amber-500/10 dark:bg-amber-400/15",
      tab: "explorations",
    },
    {
      label: "Verified Rules",
      value: data.invariants?.length || 0,
      subtext: "Automated Checks",
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
            <button
              type="button"
              onClick={() => setDiagnosticsOpen(true)}
              className="cursor-pointer transition-transform hover:scale-105"
              title="Open Validation & Governance Diagnostics"
            >
              <Badge variant={overview.is_clean ? "success" : "warning"}>
                {overview.is_clean ? "All Checks Passing" : "Needs Attention"}
              </Badge>
            </button>
            {advisoryWarnings.length > 0 && (
              <button
                type="button"
                onClick={() => setDiagnosticsOpen(true)}
                className="cursor-pointer transition-transform hover:scale-105"
                title="Inspect Advisory Validation Warnings"
              >
                <Badge variant="warning">
                  {advisoryWarnings.length}{" "}
                  {advisoryWarnings.length === 1 ? "Advisory Warning" : "Advisory Warnings"}
                </Badge>
              </button>
            )}
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-foreground tracking-tight">
            {formatDisplayTitle(overview.system_title, overview.system_id)}
          </h1>
          <p className="text-sm text-muted-foreground leading-relaxed">
            Always-current system documentation covering product domains, architecture,
            design systems, and planned changes.
          </p>
        </div>
      </div>

      {/* Live Workspace Status Card */}
      <Card id="workspaceDriftCard" className="border-border/80">
        <CardContent className="p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <div
              className={`h-10 w-10 rounded-2xl flex items-center justify-center shrink-0 ${
                driftClean
                  ? "bg-emerald-500/10 text-emerald-500"
                  : "bg-amber-500/10 text-amber-500"
              }`}
            >
              <Activity className="h-5 w-5" />
            </div>
            <div className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-bold text-foreground tracking-tight">
                  Documentation Health
                </h2>
                <Badge variant={driftClean ? "success" : "warning"}>
                  {driftStatus
                    ? driftStatus.error
                      ? "Check Failed"
                      : driftClean
                      ? "Up to Date"
                      : "Updates Needed"
                    : overview.is_clean
                    ? "Verified"
                    : "Needs Attention"}
                </Badge>
                {isLiveMode && (
                  <span className="text-[11px] font-medium text-emerald-500">
                    Live
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                {driftStatus?.error
                  ? "Unable to complete the workspace sync check."
                  : driftStatus
                  ? driftClean
                    ? "Source code, automated checks, and documentation are fully in sync."
                    : `${driftImpactedFiles.length} changed ${driftImpactedFiles.length === 1 ? "file" : "files"} across ${driftTouchedComponents.length} ${driftTouchedComponents.length === 1 ? "area" : "areas"} need documentation updates.`
                  : overview.is_clean
                  ? "All documents and links are verified with zero issues."
                  : "Some documents or references require attention."}
              </p>
              {driftTouchedComponents.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  {driftTouchedComponents.slice(0, 6).map((cid) => {
                    const compTitle = resolveEntityTitle(cid, labelIndex);
                    return (
                      <button
                        key={cid}
                        type="button"
                        onClick={() => requestSpecNavigation(cid)}
                        className="text-[11px] font-medium px-2.5 py-0.5 rounded-md bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 border border-amber-500/25 cursor-pointer"
                      >
                        {compTitle}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {driftStatus && (
              <span className="text-xs px-2.5 py-1 rounded-xl bg-muted/50 text-muted-foreground border border-border/60">
                {driftImpactedFiles.length} Changed {driftImpactedFiles.length === 1 ? "File" : "Files"}
              </span>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDiagnosticsOpen(true)}
              className="text-xs"
            >
              Details
            </Button>
          </div>
        </CardContent>
      </Card>

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
                  <p className="text-2xl font-bold tracking-tight text-foreground">
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

      {/* Governance Health Scorecard */}
      {data.governance_health && (
        <Card id="governanceHealthCard" className="border-border/80">
          <CardContent className="p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="space-y-0.5">
                <h2 className="text-sm font-bold text-foreground tracking-tight">
                  Governance Health Scorecard
                </h2>
                <p className="text-xs text-muted-foreground">
                  Deterministic repository coverage across source ownership, documented surfaces, contracts, and test-backed rules.
                </p>
              </div>
              <Badge variant={data.governance_health.validation.is_clean ? "success" : "warning"}>
                {data.governance_health.validation.warning_count > 0
                  ? `${data.governance_health.validation.warning_count} Advisory ${
                      data.governance_health.validation.warning_count === 1 ? "Warning" : "Warnings"
                    }`
                  : "Zero Advisory Warnings"}
              </Badge>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 text-xs">
              <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                <p className="text-muted-foreground font-medium">Source Ownership</p>
                <p className="text-lg font-bold text-foreground">
                  {data.governance_health.source_ownership.coverage_pct.toFixed(1)}%
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {data.governance_health.source_ownership.owned_code_files} /{" "}
                  {data.governance_health.source_ownership.total_code_files} files
                </p>
              </div>
              <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                <p className="text-muted-foreground font-medium">Public Surfaces</p>
                <p className="text-lg font-bold text-foreground">
                  {data.governance_health.public_surface.coverage_pct.toFixed(1)}%
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {(data.governance_health.public_surface.total_components ?? 0) > 0
                    ? `${data.governance_health.public_surface.documented_components ?? 0} / ${data.governance_health.public_surface.total_components} components`
                    : `${data.governance_health.public_surface.documented_entities} / ${data.governance_health.public_surface.total_entities} documented`}
                </p>
              </div>
              <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                <p className="text-muted-foreground font-medium">Verified Contracts</p>
                <p className="text-lg font-bold text-foreground">
                  {data.governance_health.contracts.coverage_pct.toFixed(1)}%
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {data.governance_health.contracts.verified_with_spec_file} /{" "}
                  {data.governance_health.contracts.total_active} bound
                </p>
              </div>
              <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                <p className="text-muted-foreground font-medium">Verified Capabilities</p>
                <p className="text-lg font-bold text-foreground">
                  {data.governance_health.capabilities.coverage_pct.toFixed(1)}%
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {data.governance_health.capabilities.bound_invariants} bound checks
                </p>
              </div>
              <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                <p className="text-muted-foreground font-medium">Boundary Debt</p>
                <p className="text-lg font-bold text-foreground">
                  {data.governance_health.ratchet_debt.total_max_imports}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {data.governance_health.ratchet_debt.entry_count} baseline{" "}
                  {data.governance_health.ratchet_debt.entry_count === 1 ? "entry" : "entries"}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Core Architectural Pillars */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card className="lg:col-span-2">
          <div className="p-6 space-y-4">
            <h2 className="text-base font-bold text-foreground tracking-tight flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-primary" />
              System Rules &amp; Checks
            </h2>
            <div className="space-y-3">
              {(data.invariants || []).slice(0, 4).map((inv, i) => (
                <div
                  key={inv.id || i}
                  className="p-3.5 rounded-xl border border-border/60 bg-muted/20 flex flex-col sm:flex-row sm:items-center justify-between gap-2 hover:bg-muted/40 transition-colors"
                >
                  <div className="space-y-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-foreground">
                        {humanizeInvariantTitle(inv)}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-1">
                      {inv.rule}
                    </p>
                  </div>
                  {inv.test_binding && inv.test_binding !== "N/A" && (
                    <Badge
                      variant="outline"
                      className="text-[10px] text-emerald-500 border-emerald-500/30 shrink-0"
                    >
                      Test Verified
                    </Badge>
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
                Browse All Rules &amp; Documents
                <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </Card>

        {/* How Documentation Stays Current */}
        <Card>
          <div className="p-6 space-y-4">
            <h2 className="text-base font-bold text-foreground tracking-tight">
              How Documentation Stays Current
            </h2>
            <div className="space-y-3 text-xs text-muted-foreground">
              <div className="p-3 rounded-xl bg-muted/30 border border-border/60">
                <p className="font-semibold text-foreground mb-1">Routine Updates</p>
                <p>Day-to-day code fixes and refinements update their documentation together in the same change.</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/30 border border-border/60">
                <p className="font-semibold text-foreground mb-1">Planned Changes</p>
                <p>Larger cross-cutting improvements are proposed, reviewed, and tracked before rollout.</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/30 border border-border/60">
                <p className="font-semibold text-foreground mb-1">Living Decisions</p>
                <p>Design rationale and trade-offs stay attached directly to the system areas they govern.</p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDiagnosticsOpen(true)}
                className="w-full text-xs gap-1.5 mt-1"
              >
                <ShieldCheck className="h-3.5 w-3.5 text-primary" />
                Open Validation &amp; Governance Diagnostics
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
        subtitle={`${overview.valid_specs} of ${overview.total_specs} Documents Verified`}
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
                    ? "All Documents & Links Verified"
                    : "Document or Link Attention Required"}
                </p>
                <p className="text-muted-foreground">
                  {overview.valid_specs} of {overview.total_specs} documents verified •{" "}
                  {brokenRefs.length} broken {brokenRefs.length === 1 ? "link" : "links"}
                  {advisoryWarnings.length > 0
                    ? ` • ${advisoryWarnings.length} advisory ${advisoryWarnings.length === 1 ? "warning" : "warnings"}`
                    : ""}
                </p>
              </div>
            </div>
            <Badge variant={overview.is_clean ? "success" : "warning"}>
              {overview.is_clean ? "Healthy" : "Needs Attention"}
            </Badge>
          </div>

          {schemaErrorEntries.length > 0 && (
            <div className="space-y-2">
              <h4 className="font-bold text-rose-400 uppercase tracking-wider flex items-center gap-1.5">
                <FileWarning className="h-3.5 w-3.5" />
                Validation Issues ({schemaErrorEntries.length})
              </h4>
              <div className="space-y-2">
                {schemaErrorEntries.map(([path, errs]) => {
                  const normPath = normalizeSchemaErrorPath(path);
                  const docTitle = resolveEntityTitle(normPath, labelIndex);
                  return (
                    <div
                      key={path}
                      className="p-3 rounded-xl border border-rose-500/30 bg-rose-500/5 space-y-1"
                    >
                      <button
                        type="button"
                        onClick={() => {
                          setDiagnosticsOpen(false);
                          requestSpecNavigation(normPath);
                        }}
                        className="font-semibold text-rose-400 hover:underline cursor-pointer text-left"
                      >
                        {docTitle}
                      </button>
                      <ul className="list-disc pl-4 text-muted-foreground space-y-0.5">
                        {(errs || []).map((msg, i) => (
                          <li key={i}>{msg}</li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {brokenRefs.length > 0 && (
            <div className="space-y-2">
              <h4 className="font-bold text-amber-400 uppercase tracking-wider flex items-center gap-1.5">
                <Link2Off className="h-3.5 w-3.5" />
                Broken Links ({brokenRefs.length})
              </h4>
              <div className="space-y-1.5">
                {brokenRefs.map((ref, idx) => (
                  <div
                    key={idx}
                    className="p-2.5 rounded-xl border border-amber-500/30 bg-amber-500/5 text-xs text-foreground"
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
                Documents Needing Attention ({invalidSpecs.length})
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
                  <div className="font-semibold text-rose-400">
                    {formatDisplayTitle(sp.title, sp.id)}
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

          {advisoryWarnings.length > 0 && (
            <details
              open
              className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 space-y-2"
            >
              <summary className="font-bold text-amber-400 uppercase tracking-wider flex items-center gap-1.5 cursor-pointer select-none">
                <AlertTriangle className="h-3.5 w-3.5" />
                Advisory Warnings ({advisoryWarnings.length})
              </summary>
              <div className="space-y-1.5 pt-1">
                {advisoryWarnings.map((warn, idx) => (
                  <div
                    key={idx}
                    className="p-2.5 rounded-lg border border-amber-500/20 bg-background/60 text-xs text-foreground break-words"
                  >
                    {warn}
                  </div>
                ))}
              </div>
            </details>
          )}

          {overview.is_clean && schemaErrorEntries.length === 0 && brokenRefs.length === 0 && (
            <div className="p-6 rounded-2xl border border-emerald-500/20 bg-emerald-500/5 text-center space-y-1.5">
              <p className="font-semibold text-emerald-400">
                Zero Validation Issues &amp; Zero Broken Links
              </p>
              <p className="text-muted-foreground">
                Every document in this repository passes all structural and verification checks.
              </p>
            </div>
          )}
        </div>
      </Sheet>
    </div>
  );
}
