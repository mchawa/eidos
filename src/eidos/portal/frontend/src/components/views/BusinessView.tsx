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
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { EntityMemoriesStrip } from "@/components/ui/EntityMemoriesStrip";
import { hasSpecBodyOrChunk } from "@/lib/specChunks";
import { requestSpecNavigation } from "@/lib/specNav";
import {
  formatDisplayTitle,
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
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Top Banner & Strategic Description */}
      <div className="p-5 sm:p-6 rounded-3xl bg-card border border-border/80 md-elevation-1 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-1">
            <h2 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight">
              Business &amp; Product Governance
            </h2>
            <p className="text-xs text-muted-foreground max-w-3xl leading-relaxed">
              Product domains, customer capabilities, governance policies, user personas, and
              automated system rules. Select any item to inspect its details.
            </p>
          </div>

          {/* Quick Metrics Strip */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setFilterCategory("domains")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-primary/20 bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 transition-colors cursor-pointer"
            >
              <Briefcase className="h-3.5 w-3.5" />
              <span>{domains.length} Domains</span>
            </button>
            <button
              type="button"
              onClick={() => setFilterCategory("domains")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-emerald-500/20 bg-emerald-500/10 text-emerald-500 dark:text-emerald-400 text-xs font-medium hover:bg-emerald-500/20 transition-colors cursor-pointer"
            >
              <Layers className="h-3.5 w-3.5" />
              <span>{capabilities.length} Capabilities</span>
            </button>
            <button
              type="button"
              onClick={() => setFilterCategory("policies")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-blue-500/20 bg-blue-500/10 text-blue-500 dark:text-blue-400 text-xs font-medium hover:bg-blue-500/20 transition-colors cursor-pointer"
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              <span>{policies.length} Policies</span>
            </button>
            <button
              type="button"
              onClick={() => setFilterCategory("personas")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-purple-500/20 bg-purple-500/10 text-purple-500 dark:text-purple-400 text-xs font-medium hover:bg-purple-500/20 transition-colors cursor-pointer"
            >
              <UserCheck className="h-3.5 w-3.5" />
              <span>{personas.length} Personas</span>
            </button>
            <button
              type="button"
              onClick={() => setFilterCategory("invariants")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-teal-500/20 bg-teal-500/10 text-teal-500 dark:text-teal-400 text-xs font-medium hover:bg-teal-500/20 transition-colors cursor-pointer"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              <span>{(invariants || []).length} Rules</span>
            </button>
          </div>
        </div>

        {/* Filter Tabs & Search Bar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-3 border-t border-border/60">
          {/* Segmented Filter Pills */}
          <div
            id="businessCategoryFilter"
            className="flex flex-wrap items-center gap-1 p-1 bg-muted/30 rounded-xl border border-border/60 text-xs"
          >
            <button
              type="button"
              onClick={() => setFilterCategory("all")}
              className={cn(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                filterCategory === "all"
                  ? "bg-card text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              All Items
            </button>
            <button
              type="button"
              onClick={() => setFilterCategory("domains")}
              className={cn(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                filterCategory === "domains"
                  ? "bg-card text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              Domains &amp; Capabilities
            </button>
            <button
              type="button"
              onClick={() => setFilterCategory("policies")}
              className={cn(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer flex items-center gap-1.5",
                filterCategory === "policies"
                  ? "bg-card text-foreground shadow-xs font-semibold text-blue-500 dark:text-blue-400"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <ShieldCheck className="h-3 w-3" />
              <span>Policies</span>
            </button>
            <button
              type="button"
              onClick={() => setFilterCategory("personas")}
              className={cn(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                filterCategory === "personas"
                  ? "bg-card text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              Personas
            </button>
            <button
              type="button"
              onClick={() => setFilterCategory("invariants")}
              className={cn(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer flex items-center gap-1.5",
                filterCategory === "invariants"
                  ? "bg-card text-foreground shadow-xs font-semibold text-teal-500 dark:text-teal-400"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <CheckCircle2 className="h-3 w-3" />
              <span>Rules</span>
            </button>
          </div>

          {/* Search Input */}
          <div className="relative flex-1 sm:max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <input
              id="businessSearchInput"
              type="text"
              placeholder="Search domains, capabilities, policies..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-8.5 pr-8 py-1.5 text-xs rounded-xl bg-card border border-border/80 focus:border-primary focus:outline-hidden transition-colors"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* SECTION 1: PRODUCT DOMAINS & CAPABILITIES */}
      {(filterCategory === "all" || filterCategory === "domains") && (
        <div id="businessDomainsSection" className="space-y-6 pt-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2">
            <div className="space-y-1">
              <h3 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-3">
                <Briefcase className="h-6 w-6 text-primary shrink-0" />
                <span>Product Domains &amp; Capabilities</span>
                <Badge variant="outline" className="text-xs px-2 py-0.5">
                  {filteredDomains.length} Domains
                </Badge>
              </h3>
              <p className="text-sm text-muted-foreground">
                Core product areas, customer capabilities, and governing rules
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {filteredDomains.map((dom) => {
              const domCaps = capabilities.filter(
                (c) => c.domain === dom.id || dom.capabilities?.includes(c.id)
              );

              return (
                <Card
                  key={dom.id}
                  className="p-5 sm:p-6 space-y-4 border border-border/80 bg-card hover:border-primary/40 transition-colors shadow-xs"
                >
                  {/* Domain Header */}
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-[10px] font-semibold",
                            dom.classification === "core"
                              ? "bg-purple-500/10 text-purple-400 border-purple-500/20"
                              : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                          )}
                        >
                          {humanizeToken(dom.classification || "Core")}
                        </Badge>
                      </div>
                      <h3 className="text-base font-bold text-foreground">
                        {formatDisplayTitle(dom.title, dom.id)}
                      </h3>
                    </div>

                    <button
                      type="button"
                      onClick={() => setSelectedEntity({ type: "domain", item: dom })}
                      className="px-2.5 py-1 rounded-lg text-xs font-medium bg-muted/60 hover:bg-muted text-foreground transition-colors cursor-pointer shrink-0 flex items-center gap-1"
                    >
                      <span>View Domain</span>
                      <ChevronRight className="h-3 w-3" />
                    </button>
                  </div>

                  {/* Domain Mission */}
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    {dom.mission}
                  </p>

                  {/* Core Responsibilities Preview */}
                  {dom.boundaries?.responsibilities && (
                    <div className="space-y-1.5 text-xs">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                        Core Responsibilities
                      </span>
                      <ul className="space-y-1">
                        {dom.boundaries.responsibilities.slice(0, 2).map((resp, i) => (
                          <li
                            key={i}
                            className="flex items-start gap-1.5 text-[11px] text-muted-foreground leading-normal"
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-primary mt-1.5 shrink-0" />
                            <span className="line-clamp-2">{resp}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Capabilities Sub-List */}
                  <div className="space-y-2 pt-3.5 border-t-2 border-border/80">
                    <div className="flex items-center justify-between">
                      <h4 className="text-[11px] font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                        <Layers className="h-3 w-3 text-emerald-400" />
                        Capabilities ({domCaps.length})
                      </h4>
                      <span className="text-[10px] text-muted-foreground">
                        Select to read details
                      </span>
                    </div>

                    <div className="space-y-2">
                      {domCaps.map((cap) => {
                        const capInvariants =
                          cap.invariants || cap.technical?.invariants || [];

                        return (
                          <div
                            key={cap.id}
                            onClick={() =>
                              setSelectedEntity({ type: "capability", item: cap })
                            }
                            className="p-3 rounded-xl border border-border/80 bg-muted/20 hover:bg-muted/50 hover:border-primary/50 transition-all cursor-pointer space-y-1.5 group"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <h5 className="text-xs font-bold text-foreground group-hover:text-primary transition-colors">
                                {formatDisplayTitle(cap.title, cap.id)}
                              </h5>
                              <div className="flex items-center gap-1.5 shrink-0">
                                {cap.status && (
                                  <Badge
                                    variant="outline"
                                    className="text-[9px] px-1.5 py-0"
                                  >
                                    {formatStatus(cap.status)}
                                  </Badge>
                                )}
                                <span className="text-muted-foreground group-hover:text-primary transition-colors flex items-center text-[10px] font-medium">
                                  Details <ArrowRight className="h-3 w-3 ml-0.5" />
                                </span>
                              </div>
                            </div>

                            {cap.summary && (
                              <p className="text-[11px] text-muted-foreground line-clamp-2 leading-relaxed">
                                {cap.summary}
                              </p>
                            )}

                            {capInvariants.length > 0 && (
                              <div className="flex items-center gap-1.5 text-[10px] text-emerald-500 dark:text-emerald-400 pt-1">
                                <CheckCircle2 className="h-3 w-3 shrink-0" />
                                <span>
                                  {capInvariants.length} verified {capInvariants.length === 1 ? "rule" : "rules"}
                                </span>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* SECTION 2: GOVERNANCE POLICIES */}
      {(filterCategory === "all" || filterCategory === "policies") && (
        <div
          id="businessPoliciesSection"
          className={cn(
            "space-y-6",
            filterCategory === "all" ? "pt-12 mt-12 border-t-2 border-border/80" : "pt-6"
          )}
        >
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2">
            <div className="space-y-1">
              <h3 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-3">
                <ShieldCheck className="h-6 w-6 text-blue-500 shrink-0" />
                <span>Governance Policies</span>
                <Badge variant="outline" className="text-xs px-2 py-0.5">
                  {filteredPolicies.length} Active
                </Badge>
              </h3>
              <p className="text-sm text-muted-foreground">
                Mandatory quality standards, compliance constraints, and system-wide rules
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {filteredPolicies.map((pol) => {
              const boundCapabilities = capabilities.filter((c) =>
                c.business?.policies?.some((pRef) => pRef.includes(pol.id))
              );
              const ruleCount = pol.invariants?.length || 0;

              return (
                <Card
                  key={pol.id}
                  className="p-5 sm:p-6 space-y-4 border border-border/80 bg-card hover:border-primary/40 transition-colors shadow-xs"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <Badge
                          variant="outline"
                          className="text-[10px] font-semibold bg-blue-500/10 text-blue-400 border-blue-500/20"
                        >
                          {humanizeToken(pol.category)}
                        </Badge>
                        {pol.severity && (
                          <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold bg-rose-500/15 text-rose-400 border border-rose-500/30 flex items-center gap-1">
                            <Lock className="h-2.5 w-2.5" />
                            {formatSeverity(pol.severity)}
                          </span>
                        )}
                      </div>
                      <h3 className="text-base font-bold text-foreground">
                        {formatDisplayTitle(pol.title, pol.id)}
                      </h3>
                    </div>

                    <button
                      type="button"
                      onClick={() => setSelectedEntity({ type: "policy", item: pol })}
                      className="px-2.5 py-1 rounded-lg text-xs font-medium bg-muted/60 hover:bg-muted text-foreground transition-colors cursor-pointer shrink-0 flex items-center gap-1"
                    >
                      <span>View Policy</span>
                      <ChevronRight className="h-3 w-3" />
                    </button>
                  </div>

                  {/* Policy Rule Callout */}
                  {pol.invariants && pol.invariants.length > 0 && (
                    <div className="p-3.5 rounded-xl border border-border/80 bg-muted/20 space-y-2">
                      <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                        <div className="flex items-center gap-1.5">
                          <ShieldCheck className="h-3.5 w-3.5 text-blue-400" />
                          <span>Enforced Rules ({pol.invariants.length})</span>
                        </div>
                        {pol.invariants[0].test_binding && (
                          <span className="text-emerald-500 normal-case font-medium">
                            Test Verified
                          </span>
                        )}
                      </div>
                      <p className="text-xs sm:text-sm font-medium text-foreground leading-relaxed">
                        "{pol.invariants[0].rule}"
                      </p>
                    </div>
                  )}

                  {/* Card Footer */}
                  <div className="pt-3.5 border-t-2 border-border/80 flex items-center justify-between text-xs text-muted-foreground">
                    <span className="text-[11px]">
                      {boundCapabilities.length > 0
                        ? `Applies to ${boundCapabilities.length} ${boundCapabilities.length === 1 ? "capability" : "capabilities"}`
                        : "System-wide Governance Policy"}
                    </span>
                    <span className="text-[11px]">
                      {ruleCount} {ruleCount === 1 ? "rule" : "rules"}
                    </span>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* SECTION 3: PERSONAS & STAKEHOLDERS */}
      {(filterCategory === "all" || filterCategory === "personas") && (
        <div
          id="businessPersonasSection"
          className={cn(
            "space-y-6",
            filterCategory === "all" ? "pt-12 mt-12 border-t-2 border-border/80" : "pt-6"
          )}
        >
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2">
            <div className="space-y-1">
              <h3 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-3">
                <UserCheck className="h-6 w-6 text-purple-400 shrink-0" />
                <span>Personas &amp; Stakeholders</span>
                <Badge variant="outline" className="text-xs px-2 py-0.5">
                  {filteredPersonas.length} Personas
                </Badge>
              </h3>
              <p className="text-sm text-muted-foreground">
                Human stakeholders, engineering teams, and automated assistants using the system
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {filteredPersonas.map((per) => (
              <Card
                key={per.id}
                onClick={() => setSelectedEntity({ type: "persona", item: per })}
                className="p-5 sm:p-6 space-y-4 cursor-pointer hover:border-purple-400/60 hover:shadow-lg transition-all duration-150 group border border-border/80 bg-card rounded-2xl"
              >
                <div className="flex items-center justify-between">
                  <Badge variant="outline" className="text-[10px]">
                    {humanizeToken(per.archetype)}
                  </Badge>
                </div>

                <div className="space-y-0.5">
                  <h4 className="text-sm font-bold text-foreground group-hover:text-purple-400 transition-colors">
                    {formatDisplayTitle(per.title, per.id)}
                  </h4>
                  <p className="text-xs text-muted-foreground">{per.role}</p>
                </div>

                {per.goals && per.goals.length > 0 && (
                  <div className="space-y-1 pt-1">
                    <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                      Key Goals
                    </span>
                    <ul className="text-[11px] text-muted-foreground list-disc pl-4 space-y-0.5">
                      {per.goals.slice(0, 2).map((g, idx) => (
                        <li key={idx} className="line-clamp-1 leading-snug">
                          {g}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="pt-2 border-t border-border/60 flex items-center justify-end text-purple-400 text-xs font-semibold group-hover:translate-x-0.5 transition-transform">
                  <span>View Persona</span>
                  <ArrowRight className="h-3 w-3 ml-1" />
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* SECTION 4: SYSTEM RULES & CHECKS */}
      {(filterCategory === "all" || filterCategory === "invariants") && (
        <div
          id="businessInvariantsSection"
          className={cn(
            "space-y-6",
            filterCategory === "all" ? "pt-12 mt-12 border-t-2 border-border/80" : "pt-6"
          )}
        >
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2">
            <div className="space-y-1">
              <h3 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-3">
                <CheckCircle2 className="h-6 w-6 text-teal-500 shrink-0" />
                <span>System Rules &amp; Checks</span>
                <Badge variant="outline" className="text-xs px-2 py-0.5">
                  {filteredInvariants.length} Rules
                </Badge>
              </h3>
              <p className="text-sm text-muted-foreground">
                Verified product, design, and architecture rules backed by automated tests
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredInvariants.map((inv, idx) => (
              <Card
                key={`${inv.id}-${idx}`}
                onClick={() => setSelectedEntity({ type: "invariant", item: inv })}
                className="p-4 space-y-2.5 cursor-pointer hover:border-teal-500/60 hover:shadow-md transition-all duration-150 group border border-border/80 bg-card rounded-2xl"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-bold text-foreground group-hover:text-teal-500 transition-colors">
                    {humanizeInvariantTitle(inv)}
                  </span>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {inv.category && (
                      <Badge variant="outline" className="text-[10px]">
                        {humanizeToken(inv.category)}
                      </Badge>
                    )}
                    {inv.severity && (
                      <Badge variant="outline" className="text-[10px]">
                        {formatSeverity(inv.severity)}
                      </Badge>
                    )}
                  </div>
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2">
                  {inv.rule}
                </p>
                <div className="pt-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                  {inv.source_title && <span className="truncate">{inv.source_title}</span>}
                  {inv.test_binding && inv.test_binding !== "N/A" && (
                    <span className="inline-flex items-center gap-1 text-emerald-500 font-medium shrink-0">
                      <CheckCircle2 className="h-3 w-3 shrink-0" />
                      Test Verified
                    </span>
                  )}
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* DETAIL INSPECTION DRAWER (SHEET) */}
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
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="primary">Policy</Badge>
                  <Badge variant="outline">{humanizeToken(selectedEntity.item.category)}</Badge>
                  {selectedEntity.item.severity && (
                    <Badge variant="warning">
                      {formatSeverity(selectedEntity.item.severity)}
                    </Badge>
                  )}
                </div>

                {/* Policy Rules */}
                {selectedEntity.item.invariants && selectedEntity.item.invariants.length > 0 && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <ShieldCheck className="h-3.5 w-3.5 text-blue-500" />
                      Enforced Policy Rules ({selectedEntity.item.invariants.length})
                    </h4>
                    <div className="space-y-2">
                      {selectedEntity.item.invariants.map((inv, idx) => (
                        <div
                          key={inv.id || idx}
                          className="p-3.5 rounded-xl bg-blue-500/10 border-l-3 border-blue-500 text-xs space-y-1.5"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-bold text-blue-400">
                              {humanizeInvariantTitle(inv as SpecInvariant)}
                            </span>
                            {inv.test_binding && (
                              <Badge variant="outline" className="text-[10px] text-emerald-500 border-emerald-500/30">
                                Test Verified
                              </Badge>
                            )}
                          </div>
                          <p className="font-medium text-foreground leading-relaxed">
                            "{inv.rule}"
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Related Capabilities */}
                {(() => {
                  const boundCaps = capabilities.filter((c) =>
                    c.business?.policies?.some((pRef) =>
                      pRef.includes(selectedEntity.item.id)
                    )
                  );
                  if (boundCaps.length === 0) return null;
                  return (
                    <div className="space-y-2">
                      <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                        <Layers className="h-3.5 w-3.5 text-primary" />
                        Related Capabilities ({boundCaps.length})
                      </h4>
                      <div className="space-y-2">
                        {boundCaps.map((c) => (
                          <button
                            key={c.id}
                            type="button"
                            onClick={() =>
                              setSelectedEntity({ type: "capability", item: c })
                            }
                            className="w-full text-left p-2.5 rounded-xl bg-muted/20 hover:bg-muted/50 border border-border/70 flex items-center justify-between transition-colors cursor-pointer"
                          >
                            <span className="text-xs font-semibold text-foreground">
                              {formatDisplayTitle(c.title, c.id)}
                            </span>
                            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* Document Body */}
                {hasSpecBodyOrChunk(selectedEntity.item.id, selectedEntity.item.body) && (
                  <div className="space-y-2 pt-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      Policy Details
                    </h4>
                    <AsciiDocRenderer specId={selectedEntity.item.id} content={selectedEntity.item.body || ""} />
                  </div>
                )}
              </div>
            )}

            {/* DOMAIN DETAIL DRAWER */}
            {selectedEntity.type === "domain" && (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="primary">Domain</Badge>
                  <Badge variant="outline">
                    {humanizeToken(selectedEntity.item.classification || "Core")}
                  </Badge>
                </div>

                {/* Vision / Mission */}
                <div className="space-y-1.5">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                    Domain Vision &amp; Mission
                  </h4>
                  <p className="p-3.5 rounded-xl bg-muted/20 border border-border/80 text-xs text-foreground leading-relaxed">
                    {selectedEntity.item.mission}
                  </p>
                </div>

                {/* Core Responsibilities */}
                {selectedEntity.item.boundaries?.responsibilities && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      In-Scope Responsibilities
                    </h4>
                    <ul className="space-y-1.5">
                      {selectedEntity.item.boundaries.responsibilities.map((r, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 p-2 rounded-lg bg-emerald-500/5 border border-emerald-500/20 text-xs text-foreground/90"
                        >
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 mt-0.5 shrink-0" />
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Out of Scope */}
                {selectedEntity.item.boundaries?.out_of_scope && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider text-muted-foreground">
                      Out of Scope
                    </h4>
                    <ul className="space-y-1.5">
                      {selectedEntity.item.boundaries.out_of_scope.map((o, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 p-2 rounded-lg bg-muted/30 border border-border/60 text-xs text-muted-foreground"
                        >
                          <span className="text-muted-foreground">✕</span>
                          <span>{o}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Capabilities in this Domain */}
                {(() => {
                  const domCaps = capabilities.filter(
                    (c) =>
                      c.domain === selectedEntity.item.id ||
                      selectedEntity.item.capabilities?.includes(c.id)
                  );
                  return (
                    <div className="space-y-2 pt-2 border-t border-border/60">
                      <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center justify-between">
                        <span>Capabilities</span>
                        <span className="text-muted-foreground">({domCaps.length})</span>
                      </h4>
                      <div className="space-y-2">
                        {domCaps.map((c) => (
                          <button
                            key={c.id}
                            type="button"
                            onClick={() =>
                              setSelectedEntity({ type: "capability", item: c })
                            }
                            className="w-full text-left p-3 rounded-xl bg-muted/20 hover:bg-muted/50 border border-border/70 flex items-center justify-between transition-colors cursor-pointer group"
                          >
                            <span className="text-xs font-bold text-foreground group-hover:text-primary">
                              {formatDisplayTitle(c.title, c.id)}
                            </span>
                            <ArrowRight className="h-4 w-4 text-muted-foreground group-hover:text-primary group-hover:translate-x-0.5 transition-transform" />
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* Domain Team Memories */}
                <EntityMemoriesStrip
                  entityId={selectedEntity.item.id}
                  memories={data.memories}
                  onBeforeNavigate={() => {
                    setSelectedEntity(null);
                    onClearSelected?.();
                  }}
                />

                {/* Full Document */}
                {hasSpecBodyOrChunk(selectedEntity.item.id, selectedEntity.item.body) && (
                  <div className="space-y-2 pt-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      Domain Details
                    </h4>
                    <AsciiDocRenderer specId={selectedEntity.item.id} content={selectedEntity.item.body || ""} />
                  </div>
                )}
              </div>
            )}

            {/* CAPABILITY DETAIL DRAWER */}
            {selectedEntity.type === "capability" && (
              <div className="space-y-4">
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

                {/* Purpose & Customer Outcome */}
                <div className="space-y-1.5">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                    Purpose &amp; Customer Outcome
                  </h4>
                  <p className="p-3.5 rounded-xl bg-muted/20 border border-border/80 text-xs text-foreground leading-relaxed">
                    {selectedEntity.item.business?.jtbd ||
                      selectedEntity.item.summary ||
                      "Core product capability."}
                  </p>
                </div>

                {/* Verified Rules */}
                {(() => {
                  const invs =
                    selectedEntity.item.invariants ||
                    selectedEntity.item.technical?.invariants ||
                    [];
                  if (invs.length === 0) return null;
                  return (
                    <div className="space-y-2">
                      <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center justify-between">
                        <span className="flex items-center gap-1.5 text-emerald-400">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          Verified Rules
                        </span>
                        <span className="text-muted-foreground">({invs.length})</span>
                      </h4>
                      <div className="space-y-2">
                        {invs.map((inv, idx) => (
                          <div
                            key={inv.id || idx}
                            className="p-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 space-y-1.5 text-xs"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-bold text-emerald-500 dark:text-emerald-400">
                                {humanizeInvariantTitle(inv as SpecInvariant)}
                              </span>
                              <Badge
                                variant="outline"
                                className="text-[10px] text-emerald-500 border-emerald-500/30 shrink-0"
                              >
                                Test Verified
                              </Badge>
                            </div>
                            <p className="text-foreground/90 font-medium leading-relaxed">
                              {inv.rule}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* Interface Contracts */}
                {selectedEntity.item.technical?.contracts && selectedEntity.item.technical.contracts.length > 0 && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <FileCode className="h-3.5 w-3.5 text-primary" />
                      Interface Contracts
                    </h4>
                    <div className="p-3 rounded-xl bg-muted/30 border border-border/80 flex flex-wrap gap-1.5">
                      {selectedEntity.item.technical.contracts.map((ctr, i) => (
                        <button
                          key={i}
                          type="button"
                          onClick={() => {
                            setSelectedEntity(null);
                            requestSpecNavigation(String(ctr));
                          }}
                          className="text-[11px] px-2.5 py-0.5 rounded bg-card border border-border hover:border-primary/50 text-primary font-semibold cursor-pointer transition-colors"
                        >
                          {resolveEntityTitle(String(ctr), labelIndex)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Realizing System Architecture */}
                {((selectedEntity.item.technical?.containers && selectedEntity.item.technical.containers.length > 0) ||
                  (selectedEntity.item.technical?.components && selectedEntity.item.technical.components.length > 0)) && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <Layers className="h-3.5 w-3.5 text-primary" />
                      Supporting Architecture
                    </h4>
                    <div className="p-3 rounded-xl bg-muted/30 border border-border/80 space-y-2">
                      {selectedEntity.item.technical.containers && selectedEntity.item.technical.containers.length > 0 && (
                        <div>
                          <span className="text-[10px] font-bold text-muted-foreground uppercase">
                            System Containers
                          </span>
                          <div className="flex flex-wrap gap-1.5 mt-1">
                            {selectedEntity.item.technical.containers.map((c, i) => (
                              <button
                                key={i}
                                type="button"
                                onClick={() => {
                                  setSelectedEntity(null);
                                  requestSpecNavigation(String(c));
                                }}
                                className="text-[11px] px-2.5 py-0.5 rounded bg-card border border-border hover:border-primary/50 text-primary font-semibold cursor-pointer transition-colors"
                              >
                                {resolveEntityTitle(String(c), labelIndex)}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      {selectedEntity.item.technical.components && selectedEntity.item.technical.components.length > 0 && (
                        <div>
                          <span className="text-[10px] font-bold text-muted-foreground uppercase">
                            Internal Components
                          </span>
                          <div className="flex flex-wrap gap-1.5 mt-1">
                            {selectedEntity.item.technical.components.map((c, i) => (
                              <button
                                key={i}
                                type="button"
                                onClick={() => {
                                  setSelectedEntity(null);
                                  requestSpecNavigation(String(c));
                                }}
                                className="text-[11px] px-2.5 py-0.5 rounded bg-card border border-border hover:border-primary/50 text-foreground cursor-pointer transition-colors"
                              >
                                {resolveEntityTitle(String(c), labelIndex)}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Capability Team Memories */}
                <EntityMemoriesStrip
                  entityId={selectedEntity.item.id}
                  memories={data.memories}
                  onBeforeNavigate={() => {
                    setSelectedEntity(null);
                    onClearSelected?.();
                  }}
                />

                {/* Personas & Policies */}
                <div className="space-y-3 pt-2 border-t border-border/60">
                  {selectedEntity.item.business?.personas && (
                    <div className="space-y-1.5">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                        Target Personas
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {selectedEntity.item.business.personas.map((pRef, i) => {
                          const persona = resolvePersona(pRef);
                          return (
                            <button
                              key={i}
                              type="button"
                              onClick={() => {
                                if (persona)
                                  setSelectedEntity({ type: "persona", item: persona });
                              }}
                              className="px-2.5 py-1 rounded-lg text-xs font-medium bg-purple-500/10 text-purple-400 border border-purple-500/20 hover:bg-purple-500/20 transition-colors cursor-pointer flex items-center gap-1"
                            >
                              <UserCheck className="h-3 w-3" />
                              <span>{persona ? formatDisplayTitle(persona.title, persona.id) : resolveEntityTitle(pRef, labelIndex)}</span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {selectedEntity.item.business?.policies && (
                    <div className="space-y-1.5">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                        Governing Policies
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {selectedEntity.item.business.policies.map((pRef, i) => {
                          const policy = resolvePolicy(pRef);
                          return (
                            <button
                              key={i}
                              type="button"
                              onClick={() => {
                                if (policy)
                                  setSelectedEntity({ type: "policy", item: policy });
                              }}
                              className="px-2.5 py-1 rounded-lg text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20 transition-colors cursor-pointer flex items-center gap-1"
                            >
                              <ShieldCheck className="h-3 w-3" />
                              <span>{policy ? formatDisplayTitle(policy.title, policy.id) : resolveEntityTitle(pRef, labelIndex)}</span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>

                {/* Full Document */}
                {hasSpecBodyOrChunk(selectedEntity.item.id, selectedEntity.item.body) && (
                  <div className="space-y-2 pt-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      Capability Details
                    </h4>
                    <AsciiDocRenderer specId={selectedEntity.item.id} content={selectedEntity.item.body || ""} />
                  </div>
                )}
              </div>
            )}

            {/* PERSONA DETAIL DRAWER */}
            {selectedEntity.type === "persona" && (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="primary">Persona</Badge>
                  <Badge variant="outline">{humanizeToken(selectedEntity.item.archetype)}</Badge>
                </div>

                <div className="p-3.5 rounded-xl bg-muted/20 border border-border/80 space-y-1">
                  <h4 className="text-sm font-bold text-foreground">
                    {formatDisplayTitle(selectedEntity.item.title, selectedEntity.item.id)}
                  </h4>
                  <p className="text-xs text-purple-400 font-medium">
                    {selectedEntity.item.role}
                  </p>
                </div>

                {selectedEntity.item.goals && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      Goals &amp; Desired Outcomes
                    </h4>
                    <ul className="space-y-1.5">
                      {selectedEntity.item.goals.map((goal, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 p-2 rounded-lg bg-muted/30 border border-border/60 text-xs text-foreground/90"
                        >
                          <CheckCircle2 className="h-3.5 w-3.5 text-purple-400 mt-0.5 shrink-0" />
                          <span>{goal}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {selectedEntity.item.pain_points && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider text-rose-400">
                      Pain Points &amp; Challenges
                    </h4>
                    <ul className="space-y-1.5">
                      {selectedEntity.item.pain_points.map((pain, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 p-2 rounded-lg bg-rose-500/5 border border-rose-500/20 text-xs text-foreground/90"
                        >
                          <AlertTriangle className="h-3.5 w-3.5 text-rose-500 mt-0.5 shrink-0" />
                          <span>{pain}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {selectedEntity.item.interaction_modes && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      How They Interact
                    </h4>
                    <ul className="space-y-1.5">
                      {selectedEntity.item.interaction_modes.map((mode, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 p-2 rounded-lg bg-card border border-border text-xs text-foreground/90"
                        >
                          <ArrowRight className="h-3.5 w-3.5 text-primary mt-0.5 shrink-0" />
                          <span>{mode}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {hasSpecBodyOrChunk(selectedEntity.item.id, selectedEntity.item.body) && (
                  <div className="space-y-2 pt-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      Persona Profile
                    </h4>
                    <AsciiDocRenderer specId={selectedEntity.item.id} content={selectedEntity.item.body || ""} />
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
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <CheckCircle2 className="h-3.5 w-3.5 text-teal-500" />
                    Rule Statement
                  </h4>
                  <div className="p-3.5 rounded-xl bg-teal-500/10 border-l-3 border-teal-500 text-xs font-medium text-foreground leading-relaxed">
                    "{selectedEntity.item.rule}"
                  </div>
                </div>

                {selectedEntity.item.source_title && (
                  <div className="p-3 rounded-xl bg-muted/30 border border-border/80 text-xs text-muted-foreground">
                    <span className="font-semibold text-foreground">Defined in: </span>
                    {selectedEntity.item.source_title}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Sheet>
    </div>
  );
}
