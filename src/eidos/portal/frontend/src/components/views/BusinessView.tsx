import * as React from "react";
import {
  Briefcase,
  UserCheck,
  ShieldCheck,
  CheckCircle2,
  ArrowRight,
  Search,
  X,
  FileCode,
  Lock,
  Layers,
  AlertTriangle,
  ChevronRight,
  BookOpen,
  ExternalLink,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { EntityMemoriesStrip } from "@/components/ui/EntityMemoriesStrip";
import { hasSpecBodyOrChunk } from "@/lib/specChunks";
import { requestSpecNavigation } from "@/lib/specNav";
import {
  formatDisplayTitle,
  formatHumanProse,
  formatSeverity,
  formatStatus,
  humanizeInvariantTitle,
  humanizeToken,
  resolveEntityTitle,
  useEntityLabels,
} from "@/lib/presentation";
import { cn } from "@/lib/utils";
import type {
  PortalData,
  SpecDomain,
  SpecCapability,
  SpecPersona,
  SpecPolicy,
  SpecInvariant,
} from "@/types";

export interface BusinessViewProps {
  data: PortalData;
  initialSelectedId?: string;
  initialSelectedNonce?: number;
  onClearSelected?: () => void;
}

type SelectedBusinessEntity =
  | { type: "domain"; item: SpecDomain }
  | { type: "capability"; item: SpecCapability }
  | { type: "policy"; item: SpecPolicy }
  | { type: "persona"; item: SpecPersona }
  | { type: "invariant"; item: SpecInvariant };

export function BusinessView({
  data,
  initialSelectedId,
  initialSelectedNonce,
  onClearSelected,
}: BusinessViewProps) {
  const { domains, capabilities, personas, policies, invariants } = data;
  const labelIndex = useEntityLabels();

  const [selectedEntity, setSelectedEntity] =
    React.useState<SelectedBusinessEntity | null>(null);
  const [filterCategory, setFilterCategory] = React.useState<
    "all" | "policies" | "domains" | "personas" | "invariants"
  >("all");
  const [searchQuery, setSearchQuery] = React.useState<string>("");

  React.useEffect(() => {
    if (!initialSelectedId) return;
    const pol = (policies || []).find((p) => p.id === initialSelectedId);
    if (pol) {
      setSelectedEntity({ type: "policy", item: pol });
      return;
    }
    const dom = (domains || []).find((d) => d.id === initialSelectedId);
    if (dom) {
      setSelectedEntity({ type: "domain", item: dom });
      return;
    }
    const cap = (capabilities || []).find((c) => c.id === initialSelectedId);
    if (cap) {
      setSelectedEntity({ type: "capability", item: cap });
      return;
    }
    const per = (personas || []).find((p) => p.id === initialSelectedId);
    if (per) {
      setSelectedEntity({ type: "persona", item: per });
      return;
    }
    const inv = (invariants || []).find((i) => i.id === initialSelectedId);
    if (inv) {
      setSelectedEntity({ type: "invariant", item: inv });
      return;
    }
  }, [initialSelectedId, initialSelectedNonce, policies, domains, capabilities, personas, invariants]);

  // Quick lookup maps for cross-entity references
  const domainsById = React.useMemo(() => {
    const map = new Map<string, SpecDomain>();
    domains.forEach((d) => map.set(d.id, d));
    return map;
  }, [domains]);

  const personasById = React.useMemo(() => {
    const map = new Map<string, SpecPersona>();
    personas.forEach((p) => {
      map.set(p.id, p);
      if (p.path) map.set(p.path, p);
    });
    return map;
  }, [personas]);

  const policiesById = React.useMemo(() => {
    const map = new Map<string, SpecPolicy>();
    policies.forEach((p) => {
      map.set(p.id, p);
      if (p.path) map.set(p.path, p);
    });
    return map;
  }, [policies]);

  const query = searchQuery.toLowerCase().trim();

  const filteredPolicies = React.useMemo(() => {
    if (!query) return policies;
    return policies.filter(
      (p) =>
        p.id.toLowerCase().includes(query) ||
        p.title.toLowerCase().includes(query) ||
        p.category.toLowerCase().includes(query) ||
        p.severity?.toLowerCase().includes(query) ||
        p.invariants?.some(
          (inv) =>
            inv.id.toLowerCase().includes(query) ||
            inv.rule.toLowerCase().includes(query)
        )
    );
  }, [policies, query]);

  const filteredDomains = React.useMemo(() => {
    if (!query) return domains;
    return domains.filter(
      (d) =>
        d.id.toLowerCase().includes(query) ||
        d.title.toLowerCase().includes(query) ||
        d.mission?.toLowerCase().includes(query) ||
        d.classification?.toLowerCase().includes(query) ||
        d.boundaries?.responsibilities?.some((r) => r.toLowerCase().includes(query))
    );
  }, [domains, query]);

  const filteredPersonas = React.useMemo(() => {
    if (!query) return personas;
    return personas.filter(
      (p) =>
        p.id.toLowerCase().includes(query) ||
        p.title.toLowerCase().includes(query) ||
        p.role?.toLowerCase().includes(query) ||
        p.archetype?.toLowerCase().includes(query) ||
        p.goals?.some((g) => g.toLowerCase().includes(query))
    );
  }, [personas, query]);

  const filteredInvariants = React.useMemo(() => {
    const list = invariants || [];
    if (!query) return list;
    return list.filter(
      (inv) =>
        inv.id.toLowerCase().includes(query) ||
        humanizeInvariantTitle(inv).toLowerCase().includes(query) ||
        inv.rule.toLowerCase().includes(query) ||
        (inv.category && inv.category.toLowerCase().includes(query)) ||
        (inv.test_binding && inv.test_binding.toLowerCase().includes(query))
    );
  }, [invariants, query]);

  const resolvePolicy = (ref: string): SpecPolicy | undefined => {
    if (policiesById.has(ref)) return policiesById.get(ref);
    for (const [id, pol] of policiesById.entries()) {
      if (ref.includes(id) || (pol.path && ref.includes(pol.path))) {
        return pol;
      }
    }
    return undefined;
  };

  const resolvePersona = (ref: string): SpecPersona | undefined => {
    if (personasById.has(ref)) return personasById.get(ref);
    for (const [id, per] of personasById.entries()) {
      if (ref.includes(id) || (per.path && ref.includes(per.path))) {
        return per;
      }
    }
    return undefined;
  };

  const openInFullReader = (entityId: string) => {
    setSelectedEntity(null);
    onClearSelected?.();
    requestSpecNavigation(entityId, { mode: "reader" });
  };

  const drawerSubtitle = React.useMemo(() => {
    if (!selectedEntity) return undefined;
    switch (selectedEntity.type) {
      case "domain":
        return `${humanizeToken(selectedEntity.item.classification || "Core")} Domain`;
      case "capability":
        return resolveEntityTitle(selectedEntity.item.domain, labelIndex, "Product Capability");
      case "policy":
        return `${humanizeToken(selectedEntity.item.category)} Policy`;
      case "persona":
        return selectedEntity.item.role || humanizeToken(selectedEntity.item.archetype);
      case "invariant":
        return selectedEntity.item.source_title || humanizeToken(selectedEntity.item.category);
    }
  }, [selectedEntity, labelIndex]);

  return (
    <div className="space-y-8 animate-in fade-in duration-200">
      {/* Compact Borderless Editorial Header & Filter Bar */}
      <header className="pb-5 border-b border-border/70 space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="space-y-1">
            <h1 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight">
              Business &amp; Product Governance
            </h1>
            <p className="text-xs text-muted-foreground max-w-2xl leading-relaxed">
              Product domains, customer capabilities, governance policies, user personas, and
              automated system rules.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>
              <strong className="text-foreground">{domains.length}</strong> Domains
            </span>
            <span className="text-border">•</span>
            <span>
              <strong className="text-foreground">{capabilities.length}</strong> Capabilities
            </span>
            <span className="text-border">•</span>
            <span>
              <strong className="text-foreground">{policies.length}</strong> Policies
            </span>
            <span className="text-border">•</span>
            <span>
              <strong className="text-foreground">{personas.length}</strong> Personas
            </span>
            <span className="text-border">•</span>
            <span>
              <strong className="text-foreground">{(invariants || []).length}</strong> Rules
            </span>
          </div>
        </div>

        {/* Filter Tabs & Search Bar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
          <div
            id="businessCategoryFilter"
            className="flex flex-wrap items-center gap-1 p-0.5 bg-muted/40 rounded-lg border border-border/60 text-xs"
          >
            {[
              { key: "all" as const, label: "All" },
              { key: "domains" as const, label: "Domains & Capabilities" },
              { key: "policies" as const, label: "Policies" },
              { key: "personas" as const, label: "Personas" },
              { key: "invariants" as const, label: "Rules" },
            ].map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setFilterCategory(tab.key)}
                className={cn(
                  "px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer",
                  filterCategory === tab.key
                    ? "bg-card text-foreground shadow-xs font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="relative flex-1 sm:max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            <input
              id="businessSearchInput"
              type="text"
              placeholder="Search domains, capabilities, policies..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-8.5 pr-8 py-1.5 text-xs rounded-lg bg-muted/40 border border-border/80 focus:border-primary focus:outline-hidden transition-colors"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                aria-label="Clear filter"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground cursor-pointer"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </header>

      {/* SECTION 1: PRODUCT DOMAINS & CAPABILITIES (Clean Divided Outline) */}
      {(filterCategory === "all" || filterCategory === "domains") && (
        <section id="businessDomainsSection" className="space-y-4">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-base sm:text-lg font-bold text-foreground tracking-tight flex items-center gap-2">
              <Briefcase className="h-4 w-4 text-primary shrink-0" aria-hidden="true" />
              <span>Product Domains &amp; Capabilities</span>
              <span className="text-xs font-normal text-muted-foreground">
                ({filteredDomains.length})
              </span>
            </h2>
          </div>

          <div className="divide-y divide-border/70 border-y border-border/70">
            {filteredDomains.map((dom) => {
              const domCaps = capabilities.filter(
                (c) => c.domain === dom.id || dom.capabilities?.includes(c.id)
              );

              return (
                <div key={dom.id} className="py-5 space-y-3">
                  {/* Domain Header Row */}
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1 min-w-0 flex-1 max-w-3xl">
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setSelectedEntity({ type: "domain", item: dom })}
                          className="text-base font-bold text-foreground hover:text-primary transition-colors text-left cursor-pointer"
                        >
                          {formatDisplayTitle(dom.title, dom.id)}
                        </button>
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-[10px] font-semibold",
                            dom.classification === "core"
                              ? "bg-purple-500/10 text-purple-500 dark:text-purple-400 border-purple-500/20"
                              : "bg-emerald-500/10 text-emerald-500 dark:text-emerald-400 border-emerald-500/20"
                          )}
                        >
                          {humanizeToken(dom.classification || "Core")}
                        </Badge>
                      </div>
                      {dom.mission && (
                        <p className="text-xs sm:text-sm text-muted-foreground leading-relaxed">
                          {dom.mission}
                        </p>
                      )}
                    </div>

                    <button
                      type="button"
                      onClick={() => setSelectedEntity({ type: "domain", item: dom })}
                      className="text-xs font-medium text-primary hover:underline cursor-pointer shrink-0 inline-flex items-center gap-1"
                    >
                      <span>Read Domain</span>
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>

                  {/* Capabilities Nested Divided List (No Nested Cards) */}
                  {domCaps.length > 0 && (
                    <div className="pl-4 border-l-2 border-border/60 divide-y divide-border/50">
                      {domCaps.map((cap) => {
                        const capInvariants =
                          cap.invariants || cap.technical?.invariants || [];
                        return (
                          <button
                            key={cap.id}
                            type="button"
                            onClick={() =>
                              setSelectedEntity({ type: "capability", item: cap })
                            }
                            className="w-full py-2.5 px-2 -mx-2 rounded-lg hover:bg-muted/40 transition-colors cursor-pointer flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-left group"
                          >
                            <div className="min-w-0 flex-1 space-y-0.5">
                              <div className="flex flex-wrap items-center gap-2">
                                <Layers className="h-3.5 w-3.5 text-emerald-500 shrink-0" aria-hidden="true" />
                                <span className="text-xs sm:text-sm font-semibold text-foreground group-hover:text-primary transition-colors">
                                  {formatDisplayTitle(cap.title, cap.id)}
                                </span>
                                {cap.status && (
                                  <Badge variant="outline" className="text-[10px] py-0">
                                    {formatStatus(cap.status)}
                                  </Badge>
                                )}
                              </div>
                              {cap.summary && (
                                <p className="text-xs text-muted-foreground line-clamp-1 pl-5.5">
                                  {cap.summary}
                                </p>
                              )}
                            </div>

                            <div className="flex items-center gap-3 shrink-0 pl-5.5 sm:pl-0 text-[11px]">
                              {capInvariants.length > 0 && (
                                <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium">
                                  <CheckCircle2 className="h-3 w-3 shrink-0" aria-hidden="true" />
                                  {capInvariants.length} {capInvariants.length === 1 ? "rule" : "rules"}
                                </span>
                              )}
                              <ArrowRight className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary group-hover:translate-x-0.5 transition-all" aria-hidden="true" />
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* SECTION 2: GOVERNANCE POLICIES (Scannable Divided List) */}
      {(filterCategory === "all" || filterCategory === "policies") && (
        <section id="businessPoliciesSection" className="space-y-4">
          <h2 className="text-base sm:text-lg font-bold text-foreground tracking-tight flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-blue-500 shrink-0" aria-hidden="true" />
            <span>Governance Policies</span>
            <span className="text-xs font-normal text-muted-foreground">
              ({filteredPolicies.length})
            </span>
          </h2>

          <div className="divide-y divide-border/70 border-y border-border/70">
            {filteredPolicies.map((pol) => {
              const boundCapabilities = capabilities.filter((c) =>
                c.business?.policies?.some((pRef) => pRef.includes(pol.id))
              );
              const ruleCount = pol.invariants?.length || 0;

              return (
                <button
                  key={pol.id}
                  type="button"
                  onClick={() => setSelectedEntity({ type: "policy", item: pol })}
                  className="w-full py-4 px-2 -mx-2 rounded-lg hover:bg-muted/30 transition-colors cursor-pointer text-left flex flex-col sm:flex-row sm:items-start justify-between gap-3 group"
                >
                  <div className="space-y-1.5 min-w-0 flex-1 max-w-3xl">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-bold text-foreground group-hover:text-primary transition-colors">
                        {formatDisplayTitle(pol.title, pol.id)}
                      </span>
                      <Badge
                        variant="outline"
                        className="text-[10px] font-semibold bg-blue-500/10 text-blue-500 dark:text-blue-400 border-blue-500/20"
                      >
                        {humanizeToken(pol.category)}
                      </Badge>
                      {pol.severity && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-500/10 text-rose-500 dark:text-rose-400 inline-flex items-center gap-1">
                          <Lock className="h-2.5 w-2.5" aria-hidden="true" />
                          {formatSeverity(pol.severity)}
                        </span>
                      )}
                    </div>
                    {pol.invariants && pol.invariants.length > 0 && (
                      <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2">
                        {pol.invariants[0].rule}
                      </p>
                    )}
                  </div>

                  <div className="flex items-center gap-3 text-[11px] text-muted-foreground shrink-0 sm:pt-0.5">
                    <span>
                      {boundCapabilities.length > 0
                        ? `${boundCapabilities.length} ${boundCapabilities.length === 1 ? "capability" : "capabilities"}`
                        : "System-wide"}
                    </span>
                    <span className="text-border">•</span>
                    <span>
                      {ruleCount} {ruleCount === 1 ? "rule" : "rules"}
                    </span>
                    <ChevronRight className="h-3.5 w-3.5 group-hover:text-primary transition-colors" aria-hidden="true" />
                  </div>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {/* SECTION 3: PERSONAS & STAKEHOLDERS (Compact Divided Rows) */}
      {(filterCategory === "all" || filterCategory === "personas") && (
        <section id="businessPersonasSection" className="space-y-4">
          <h2 className="text-base sm:text-lg font-bold text-foreground tracking-tight flex items-center gap-2">
            <UserCheck className="h-4 w-4 text-purple-500 shrink-0" aria-hidden="true" />
            <span>Personas &amp; Stakeholders</span>
            <span className="text-xs font-normal text-muted-foreground">
              ({filteredPersonas.length})
            </span>
          </h2>

          <div className="divide-y divide-border/70 border-y border-border/70">
            {filteredPersonas.map((per) => (
              <button
                key={per.id}
                type="button"
                onClick={() => setSelectedEntity({ type: "persona", item: per })}
                className="w-full py-3.5 px-2 -mx-2 rounded-lg hover:bg-muted/30 transition-colors cursor-pointer text-left flex flex-col sm:flex-row sm:items-center justify-between gap-2 group"
              >
                <div className="min-w-0 flex-1 space-y-1 max-w-3xl">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-bold text-foreground group-hover:text-purple-500 dark:group-hover:text-purple-400 transition-colors">
                      {formatDisplayTitle(per.title, per.id)}
                    </span>
                    <Badge variant="outline" className="text-[10px]">
                      {humanizeToken(per.archetype)}
                    </Badge>
                    {per.role && (
                      <span className="text-xs text-muted-foreground">• {per.role}</span>
                    )}
                  </div>
                  {per.goals && per.goals.length > 0 && (
                    <p className="text-xs text-muted-foreground line-clamp-1">
                      {per.goals.slice(0, 2).join(" • ")}
                    </p>
                  )}
                </div>
                <ChevronRight className="h-3.5 w-3.5 text-muted-foreground group-hover:text-purple-400 shrink-0 transition-colors" aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
      )}

      {/* SECTION 4: SYSTEM RULES & CHECKS (Semantic Sticky-Header Responsive Table) */}
      {(filterCategory === "all" || filterCategory === "invariants") && (
        <section id="businessInvariantsSection" className="space-y-4">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-base sm:text-lg font-bold text-foreground tracking-tight flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-teal-500 shrink-0" aria-hidden="true" />
              <span>System Rules &amp; Checks</span>
              <span className="text-xs font-normal text-muted-foreground">
                ({filteredInvariants.length})
              </span>
            </h2>
          </div>

          <div
            tabIndex={0}
            role="region"
            aria-label="System rules and automated checks"
            className="table-wrapper border-y border-border/70 max-h-[640px]"
          >
            <table id="businessRulesTable" className="w-full text-left text-xs">
              <thead className="bg-background/95 backdrop-blur-xs border-b border-border text-muted-foreground font-semibold uppercase tracking-wider text-[10px]">
                <tr>
                  <th scope="col" className="py-2.5 px-3 w-4/12">Rule</th>
                  <th scope="col" className="py-2.5 px-3 w-4/12">Guarantee</th>
                  <th scope="col" className="py-2.5 px-3 w-2/12">Source</th>
                  <th scope="col" className="py-2.5 px-3 w-2/12">Verification</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {filteredInvariants.map((inv, idx) => (
                  <tr
                    key={`${inv.id}-${idx}`}
                    onClick={() => setSelectedEntity({ type: "invariant", item: inv })}
                    className="hover:bg-muted/30 cursor-pointer transition-colors group"
                  >
                    <th scope="row" className="py-2.5 px-3 font-semibold text-foreground group-hover:text-teal-500 transition-colors align-top">
                      <div className="space-y-1">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedEntity({ type: "invariant", item: inv });
                          }}
                          className="text-left font-semibold text-foreground group-hover:text-teal-500 transition-colors cursor-pointer"
                        >
                          {humanizeInvariantTitle(inv)}
                        </button>
                        <div className="flex flex-wrap gap-1">
                          {inv.category && (
                            <Badge variant="outline" className="text-[9px] py-0">
                              {humanizeToken(inv.category)}
                            </Badge>
                          )}
                          {inv.severity && (
                            <Badge variant="outline" className="text-[9px] py-0">
                              {formatSeverity(inv.severity)}
                            </Badge>
                          )}
                        </div>
                      </div>
                    </th>
                    <td className="py-2.5 px-3 text-muted-foreground leading-relaxed align-top">
                      <p className="line-clamp-2">{formatHumanProse(inv.rule, labelIndex)}</p>
                    </td>
                    <td className="py-2.5 px-3 text-muted-foreground align-top truncate max-w-[180px]">
                      {inv.source_title || "—"}
                    </td>
                    <td className="py-2.5 px-3 align-top whitespace-nowrap">
                      {inv.test_binding && inv.test_binding !== "N/A" ? (
                        <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium text-[11px]">
                          <CheckCircle2 className="h-3 w-3 shrink-0" aria-hidden="true" />
                          Test Verified
                        </span>
                      ) : (
                        <span className="text-muted-foreground text-[11px]">Declared</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* DETAIL INSPECTION DRAWER (De-Carded Editorial Layout + Collapsible Metadata) */}
      <Sheet
        open={Boolean(selectedEntity)}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedEntity(null);
            onClearSelected?.();
          }
        }}
        title={
          selectedEntity?.type === "invariant"
            ? humanizeInvariantTitle(selectedEntity.item)
            : selectedEntity
            ? formatDisplayTitle(selectedEntity.item.title, selectedEntity.item.id)
            : "Details"
        }
        subtitle={drawerSubtitle}
        maxWidth="6xl"
      >
        {selectedEntity && (
          <div className="space-y-5 text-xs text-foreground">
            {/* POLICY DETAIL DRAWER */}
            {selectedEntity.type === "policy" && (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-border/60">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="primary">Policy</Badge>
                    <Badge variant="outline">{humanizeToken(selectedEntity.item.category)}</Badge>
                    {selectedEntity.item.severity && (
                      <Badge variant="warning">
                        {formatSeverity(selectedEntity.item.severity)}
                      </Badge>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => openInFullReader(selectedEntity.item.id)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer transition-colors"
                  >
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Open in Full Reader</span>
                  </button>
                </div>

                {selectedEntity.item.invariants && selectedEntity.item.invariants.length > 0 && (
                  <div className="space-y-2">
                    <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <ShieldCheck className="h-3.5 w-3.5 text-blue-500" aria-hidden="true" />
                      Enforced Policy Rules ({selectedEntity.item.invariants.length})
                    </h4>
                    <div className="divide-y divide-border/60 border-l-2 border-blue-500 pl-4 space-y-2">
                      {selectedEntity.item.invariants.map((inv, idx) => (
                        <div key={inv.id || idx} className="pt-2 first:pt-0 space-y-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-semibold text-foreground">
                              {humanizeInvariantTitle(inv as SpecInvariant)}
                            </span>
                            {inv.test_binding && (
                              <span className="text-[11px] text-emerald-500 font-medium">
                                Test Verified
                              </span>
                            )}
                          </div>
                          <p className="text-muted-foreground leading-relaxed">
                            "{formatHumanProse(inv.rule, labelIndex)}"
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {(() => {
                  const boundCaps = capabilities.filter((c) =>
                    c.business?.policies?.some((pRef) =>
                      pRef.includes(selectedEntity.item.id)
                    )
                  );
                  if (boundCaps.length === 0) return null;
                  return (
                    <details className="portal-disclosure border-y border-border/60 py-2.5">
                      <summary className="flex items-center gap-2 text-xs font-semibold text-foreground select-none">
                        <ChevronRight className="disclosure-chevron h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                        <span>Related Capabilities ({boundCaps.length})</span>
                      </summary>
                      <div className="pt-2.5 pl-5 flex flex-wrap gap-2">
                        {boundCaps.map((c) => (
                          <button
                            key={c.id}
                            type="button"
                            onClick={() =>
                              setSelectedEntity({ type: "capability", item: c })
                            }
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-muted/50 hover:bg-primary/10 hover:text-primary border border-border/60 text-xs font-medium cursor-pointer transition-colors"
                          >
                            <span>{formatDisplayTitle(c.title, c.id)}</span>
                            <ArrowRight className="h-3 w-3" aria-hidden="true" />
                          </button>
                        ))}
                      </div>
                    </details>
                  );
                })()}

                {hasSpecBodyOrChunk(selectedEntity.item.id, selectedEntity.item.body) && (
                  <div className="space-y-2 pt-1">
                    <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                      Policy Specification
                    </h4>
                    <AsciiDocRenderer
                      specId={selectedEntity.item.id}
                      content={selectedEntity.item.body || ""}
                      measure="full"
                      hideLeadingTitle={true}
                    />
                  </div>
                )}
              </div>
            )}

            {/* DOMAIN DETAIL DRAWER */}
            {selectedEntity.type === "domain" && (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-border/60">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="primary">Domain</Badge>
                    <Badge variant="outline">
                      {humanizeToken(selectedEntity.item.classification || "Core")}
                    </Badge>
                  </div>
                  <button
                    type="button"
                    onClick={() => openInFullReader(selectedEntity.item.id)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer transition-colors"
                  >
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Open in Full Reader</span>
                  </button>
                </div>

                {selectedEntity.item.mission && (
                  <p className="text-sm text-foreground/90 leading-relaxed max-w-3xl">
                    {selectedEntity.item.mission}
                  </p>
                )}

                {/* Collapsible Structured Scope & Capabilities */}
                {(selectedEntity.item.boundaries?.responsibilities?.length ||
                  selectedEntity.item.boundaries?.out_of_scope?.length) && (
                  <details className="portal-disclosure border-y border-border/60 py-2.5">
                    <summary className="flex items-center gap-2 text-xs font-semibold text-foreground select-none">
                      <ChevronRight className="disclosure-chevron h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                      <span>Scope &amp; Boundaries</span>
                    </summary>
                    <div className="pt-3 pl-5 grid grid-cols-1 md:grid-cols-2 gap-4">
                      {selectedEntity.item.boundaries?.responsibilities && (
                        <div className="space-y-1.5">
                          <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-500">
                            In Scope
                          </span>
                          <ul className="space-y-1 list-disc pl-4 text-xs text-foreground/90">
                            {selectedEntity.item.boundaries.responsibilities.map((r, i) => (
                              <li key={i}>{r}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {selectedEntity.item.boundaries?.out_of_scope && (
                        <div className="space-y-1.5">
                          <span className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                            Out of Scope
                          </span>
                          <ul className="space-y-1 list-disc pl-4 text-xs text-muted-foreground">
                            {selectedEntity.item.boundaries.out_of_scope.map((o, i) => (
                              <li key={i}>{o}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </details>
                )}

                {(() => {
                  const domCaps = capabilities.filter(
                    (c) =>
                      c.domain === selectedEntity.item.id ||
                      selectedEntity.item.capabilities?.includes(c.id)
                  );
                  if (domCaps.length === 0) return null;
                  return (
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[11px] font-semibold text-muted-foreground">
                        Capabilities:
                      </span>
                      {domCaps.map((c) => (
                        <button
                          key={c.id}
                          type="button"
                          onClick={() =>
                            setSelectedEntity({ type: "capability", item: c })
                          }
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-muted/40 hover:bg-primary/10 hover:text-primary border border-border/60 text-xs font-medium cursor-pointer transition-colors"
                        >
                          <span>{formatDisplayTitle(c.title, c.id)}</span>
                          <ArrowRight className="h-3 w-3" aria-hidden="true" />
                        </button>
                      ))}
                    </div>
                  );
                })()}

                <EntityMemoriesStrip
                  entityId={selectedEntity.item.id}
                  memories={data.memories}
                  onBeforeNavigate={() => {
                    setSelectedEntity(null);
                    onClearSelected?.();
                  }}
                />

                {hasSpecBodyOrChunk(selectedEntity.item.id, selectedEntity.item.body) && (
                  <div className="space-y-2 pt-2 border-t border-border/60">
                    <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                      Domain Specification
                    </h4>
                    <AsciiDocRenderer
                      specId={selectedEntity.item.id}
                      content={selectedEntity.item.body || ""}
                      measure="full"
                      hideLeadingTitle={true}
                    />
                  </div>
                )}
              </div>
            )}

            {/* CAPABILITY DETAIL DRAWER */}
            {selectedEntity.type === "capability" && (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-border/60">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="primary">Capability</Badge>
                    {selectedEntity.item.domain && (
                      <button
                        type="button"
                        onClick={() => {
                          const dom = domainsById.get(selectedEntity.item.domain);
                          if (dom) setSelectedEntity({ type: "domain", item: dom });
                        }}
                        className="text-[11px] font-medium px-2.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors cursor-pointer"
                      >
                        {resolveEntityTitle(selectedEntity.item.domain, labelIndex)}
                      </button>
                    )}
                    {selectedEntity.item.status && (
                      <Badge variant="outline">{formatStatus(selectedEntity.item.status)}</Badge>
                    )}
                    {selectedEntity.item.version && (
                      <Badge variant="outline">v{selectedEntity.item.version}</Badge>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => openInFullReader(selectedEntity.item.id)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer transition-colors"
                  >
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Open in Full Reader</span>
                  </button>
                </div>

                {(selectedEntity.item.business?.jtbd || selectedEntity.item.summary) && (
                  <p className="text-sm text-foreground/90 leading-relaxed max-w-3xl">
                    {selectedEntity.item.business?.jtbd || selectedEntity.item.summary}
                  </p>
                )}

                {/* Collapsible Structured Links & Verified Rules */}
                {(() => {
                  const invs =
                    selectedEntity.item.invariants ||
                    selectedEntity.item.technical?.invariants ||
                    [];
                  const hasLinks = Boolean(
                    selectedEntity.item.technical?.contracts?.length ||
                      selectedEntity.item.technical?.containers?.length ||
                      selectedEntity.item.technical?.components?.length ||
                      selectedEntity.item.business?.personas?.length ||
                      selectedEntity.item.business?.policies?.length
                  );
                  if (invs.length === 0 && !hasLinks) return null;
                  return (
                    <div className="divide-y divide-border/60 border-y border-border/60">
                      {invs.length > 0 && (
                        <details className="portal-disclosure py-2.5" open>
                          <summary className="flex items-center gap-2 text-xs font-semibold text-foreground select-none">
                            <ChevronRight className="disclosure-chevron h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" aria-hidden="true" />
                            <span>Verified Rules ({invs.length})</span>
                          </summary>
                          <div className="pt-2.5 pl-5 divide-y divide-border/50">
                            {invs.map((inv, idx) => (
                              <div key={inv.id || idx} className="py-2 first:pt-0 space-y-0.5">
                                <div className="flex items-center justify-between gap-2">
                                  <span className="font-semibold text-foreground">
                                    {humanizeInvariantTitle(inv as SpecInvariant)}
                                  </span>
                                  {inv.test_binding && inv.test_binding !== "N/A" && (
                                    <span className="text-[10px] text-emerald-500 font-medium shrink-0">
                                      Test Verified
                                    </span>
                                  )}
                                </div>
                                <p className="text-muted-foreground leading-relaxed">
                                  {formatHumanProse(inv.rule, labelIndex)}
                                </p>
                              </div>
                            ))}
                          </div>
                        </details>
                      )}

                      {hasLinks && (
                        <details className="portal-disclosure py-2.5">
                          <summary className="flex items-center gap-2 text-xs font-semibold text-foreground select-none">
                            <ChevronRight className="disclosure-chevron h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                            <FileCode className="h-3.5 w-3.5 text-primary shrink-0" aria-hidden="true" />
                            <span>Architecture, Personas &amp; Policies</span>
                          </summary>
                          <div className="pt-3 pl-5 space-y-3">
                            {selectedEntity.item.technical?.contracts && selectedEntity.item.technical.contracts.length > 0 && (
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[11px] text-muted-foreground mr-1">Contracts:</span>
                                {selectedEntity.item.technical.contracts.map((ctr, i) => (
                                  <button
                                    key={i}
                                    type="button"
                                    onClick={() => {
                                      setSelectedEntity(null);
                                      requestSpecNavigation(String(ctr));
                                    }}
                                    className="text-[11px] px-2 py-0.5 rounded bg-muted/60 hover:bg-primary/15 text-primary font-medium cursor-pointer"
                                  >
                                    {resolveEntityTitle(String(ctr), labelIndex)}
                                  </button>
                                ))}
                              </div>
                            )}
                            {selectedEntity.item.technical?.containers && selectedEntity.item.technical.containers.length > 0 && (
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[11px] text-muted-foreground mr-1">Containers:</span>
                                {selectedEntity.item.technical.containers.map((c, i) => (
                                  <button
                                    key={i}
                                    type="button"
                                    onClick={() => {
                                      setSelectedEntity(null);
                                      requestSpecNavigation(String(c));
                                    }}
                                    className="text-[11px] px-2 py-0.5 rounded bg-muted/60 hover:bg-primary/15 text-primary font-medium cursor-pointer"
                                  >
                                    {resolveEntityTitle(String(c), labelIndex)}
                                  </button>
                                ))}
                              </div>
                            )}
                            {selectedEntity.item.technical?.components && selectedEntity.item.technical.components.length > 0 && (
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[11px] text-muted-foreground mr-1">Components:</span>
                                {selectedEntity.item.technical.components.map((c, i) => (
                                  <button
                                    key={i}
                                    type="button"
                                    onClick={() => {
                                      setSelectedEntity(null);
                                      requestSpecNavigation(String(c));
                                    }}
                                    className="text-[11px] px-2 py-0.5 rounded bg-muted/60 hover:bg-primary/15 text-foreground font-medium cursor-pointer"
                                  >
                                    {resolveEntityTitle(String(c), labelIndex)}
                                  </button>
                                ))}
                              </div>
                            )}
                            {selectedEntity.item.business?.personas && selectedEntity.item.business.personas.length > 0 && (
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[11px] text-muted-foreground mr-1">Personas:</span>
                                {selectedEntity.item.business.personas.map((pRef, i) => {
                                  const persona = resolvePersona(pRef);
                                  return (
                                    <button
                                      key={i}
                                      type="button"
                                      onClick={() => {
                                        if (persona) setSelectedEntity({ type: "persona", item: persona });
                                      }}
                                      className="px-2 py-0.5 rounded text-[11px] font-medium bg-purple-500/10 text-purple-500 dark:text-purple-400 hover:bg-purple-500/20 cursor-pointer"
                                    >
                                      {persona ? formatDisplayTitle(persona.title, persona.id) : resolveEntityTitle(pRef, labelIndex)}
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                            {selectedEntity.item.business?.policies && selectedEntity.item.business.policies.length > 0 && (
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[11px] text-muted-foreground mr-1">Policies:</span>
                                {selectedEntity.item.business.policies.map((pRef, i) => {
                                  const policy = resolvePolicy(pRef);
                                  return (
                                    <button
                                      key={i}
                                      type="button"
                                      onClick={() => {
                                        if (policy) setSelectedEntity({ type: "policy", item: policy });
                                      }}
                                      className="px-2 py-0.5 rounded text-[11px] font-medium bg-blue-500/10 text-blue-500 dark:text-blue-400 hover:bg-blue-500/20 cursor-pointer"
                                    >
                                      {policy ? formatDisplayTitle(policy.title, policy.id) : resolveEntityTitle(pRef, labelIndex)}
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        </details>
                      )}
                    </div>
                  );
                })()}

                <EntityMemoriesStrip
                  entityId={selectedEntity.item.id}
                  memories={data.memories}
                  onBeforeNavigate={() => {
                    setSelectedEntity(null);
                    onClearSelected?.();
                  }}
                />

                {hasSpecBodyOrChunk(selectedEntity.item.id, selectedEntity.item.body) && (
                  <div className="space-y-2 pt-1">
                    <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                      Capability Specification
                    </h4>
                    <AsciiDocRenderer
                      specId={selectedEntity.item.id}
                      content={selectedEntity.item.body || ""}
                      measure="full"
                      hideLeadingTitle={true}
                    />
                  </div>
                )}
              </div>
            )}

            {/* PERSONA DETAIL DRAWER */}
            {selectedEntity.type === "persona" && (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-border/60">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="primary">Persona</Badge>
                    <Badge variant="outline">{humanizeToken(selectedEntity.item.archetype)}</Badge>
                    {selectedEntity.item.role && (
                      <span className="text-xs text-muted-foreground">{selectedEntity.item.role}</span>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => openInFullReader(selectedEntity.item.id)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer transition-colors"
                  >
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Open in Full Reader</span>
                  </button>
                </div>

                {(selectedEntity.item.goals?.length ||
                  selectedEntity.item.pain_points?.length ||
                  selectedEntity.item.interaction_modes?.length) && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pb-4 border-b border-border/60">
                    {selectedEntity.item.goals && selectedEntity.item.goals.length > 0 && (
                      <div className="space-y-1.5">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-purple-500 dark:text-purple-400">
                          Goals &amp; Desired Outcomes
                        </span>
                        <ul className="space-y-1 list-disc pl-4 text-xs text-foreground/90">
                          {selectedEntity.item.goals.map((goal, i) => (
                            <li key={i}>{goal}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {selectedEntity.item.pain_points && selectedEntity.item.pain_points.length > 0 && (
                      <div className="space-y-1.5">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-rose-500 dark:text-rose-400 flex items-center gap-1">
                          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                          Pain Points &amp; Challenges
                        </span>
                        <ul className="space-y-1 list-disc pl-4 text-xs text-foreground/90">
                          {selectedEntity.item.pain_points.map((pain, i) => (
                            <li key={i}>{pain}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {hasSpecBodyOrChunk(selectedEntity.item.id, selectedEntity.item.body) && (
                  <div className="space-y-2">
                    <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                      Persona Profile
                    </h4>
                    <AsciiDocRenderer
                      specId={selectedEntity.item.id}
                      content={selectedEntity.item.body || ""}
                      measure="full"
                      hideLeadingTitle={true}
                    />
                  </div>
                )}
              </div>
            )}

            {/* RULE / INVARIANT DETAIL DRAWER */}
            {selectedEntity.type === "invariant" && (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="primary">Verified Rule</Badge>
                  {selectedEntity.item.category && (
                    <Badge variant="outline">{humanizeToken(selectedEntity.item.category)}</Badge>
                  )}
                  {selectedEntity.item.severity && (
                    <Badge variant="warning">{formatSeverity(selectedEntity.item.severity)}</Badge>
                  )}
                  {selectedEntity.item.test_binding && selectedEntity.item.test_binding !== "N/A" && (
                    <Badge variant="success">Test Verified</Badge>
                  )}
                </div>

                <div className="space-y-1.5">
                  <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <CheckCircle2 className="h-3.5 w-3.5 text-teal-500" aria-hidden="true" />
                    Rule Statement
                  </h4>
                  <blockquote className="pl-4 py-1 border-l-3 border-teal-500 text-sm font-medium text-foreground leading-relaxed max-w-3xl">
                    "{formatHumanProse(selectedEntity.item.rule, labelIndex)}"
                  </blockquote>
                </div>

                {selectedEntity.item.source_title && (
                  <p className="text-xs text-muted-foreground pt-2 border-t border-border/60">
                    <span className="font-semibold text-foreground">Defined in: </span>
                    {selectedEntity.item.source_title}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </Sheet>
    </div>
  );
}
