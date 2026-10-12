import * as React from "react";
import {
  Briefcase,
  UserCheck,
  ShieldCheck,
  CheckCircle2,
  ArrowRight,
  Search,
  Filter,
  X,
  FileCode,
  Terminal,
  ExternalLink,
  Lock,
  Layers,
  Sparkles,
  AlertTriangle,
  ChevronRight,
  BookOpen,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { cn } from "@/lib/utils";
import type {
  PortalData,
  SpecDomain,
  SpecCapability,
  SpecPersona,
  SpecPolicy,
} from "@/types";

export interface BusinessViewProps {
  data: PortalData;
}

type SelectedBusinessEntity =
  | { type: "domain"; item: SpecDomain }
  | { type: "capability"; item: SpecCapability }
  | { type: "policy"; item: SpecPolicy }
  | { type: "persona"; item: SpecPersona };

export function BusinessView({ data }: BusinessViewProps) {
  const { domains, capabilities, personas, policies, invariants } = data;

  const [selectedEntity, setSelectedEntity] =
    React.useState<SelectedBusinessEntity | null>(null);
  const [filterCategory, setFilterCategory] = React.useState<
    "all" | "policies" | "domains" | "personas"
  >("all");
  const [searchQuery, setSearchQuery] = React.useState<string>("");

  // Quick lookup maps for cross-entity references
  const domainsById = React.useMemo(() => {
    const map = new Map<string, SpecDomain>();
    domains.forEach((d) => map.set(d.id, d));
    return map;
  }, [domains]);

  const capabilitiesById = React.useMemo(() => {
    const map = new Map<string, SpecCapability>();
    capabilities.forEach((c) => map.set(c.id, c));
    return map;
  }, [capabilities]);

  const personasById = React.useMemo(() => {
    const map = new Map<string, SpecPersona>();
    personas.forEach((p) => {
      map.set(p.id, p);
      // Also index by slug/filename if referenced as paths
      if (p.path) {
        map.set(p.path, p);
      }
    });
    return map;
  }, [personas]);

  const policiesById = React.useMemo(() => {
    const map = new Map<string, SpecPolicy>();
    policies.forEach((p) => {
      map.set(p.id, p);
      if (p.path) {
        map.set(p.path, p);
      }
    });
    return map;
  }, [policies]);

  // Filtering logic
  const query = searchQuery.toLowerCase().trim();

  const filteredPolicies = React.useMemo(() => {
    if (!query) return policies;
    return policies.filter(
      (p) =>
        p.id.toLowerCase().includes(query) ||
        p.title.toLowerCase().includes(query) ||
        p.rule.toLowerCase().includes(query) ||
        p.category.toLowerCase().includes(query) ||
        p.enforcement?.mechanism?.toLowerCase().includes(query)
    );
  }, [policies, query]);

  const filteredDomains = React.useMemo(() => {
    if (!query) return domains;
    return domains.filter(
      (d) =>
        d.id.toLowerCase().includes(query) ||
        d.name.toLowerCase().includes(query) ||
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
        p.name.toLowerCase().includes(query) ||
        p.role?.toLowerCase().includes(query) ||
        p.archetype?.toLowerCase().includes(query) ||
        p.goals?.some((g) => g.toLowerCase().includes(query))
    );
  }, [personas, query]);

  // Helper to resolve policy reference from path or ID
  const resolvePolicy = (ref: string): SpecPolicy | undefined => {
    if (policiesById.has(ref)) return policiesById.get(ref);
    for (const [id, pol] of policiesById.entries()) {
      if (ref.includes(id) || (pol.path && ref.includes(pol.path))) {
        return pol;
      }
    }
    return undefined;
  };

  // Helper to resolve persona reference from path or ID
  const resolvePersona = (ref: string): SpecPersona | undefined => {
    if (personasById.has(ref)) return personasById.get(ref);
    for (const [id, per] of personasById.entries()) {
      if (ref.includes(id) || (per.path && ref.includes(per.path))) {
        return per;
      }
    }
    return undefined;
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Top Banner & Strategic Description */}
      <div className="p-5 sm:p-6 rounded-3xl bg-card border border-border/80 md-elevation-1 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs font-semibold px-2.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                DDD Strategic Layer
              </span>
              <span className="text-xs text-muted-foreground font-mono">
                SCR-PORTAL-BUSINESS
              </span>
            </div>
            <h2 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight">
              Business Architecture & Living Governance
            </h2>
            <p className="text-xs text-muted-foreground max-w-3xl leading-relaxed">
              Domain-Driven Design (DDD) Bounded Contexts (`DOM-*`), Product Capabilities (`CAP-*`),
              non-negotiable System Governance Policies (`POL-*`), and Actor Personas (`PER-*`).
              Click any element to inspect its full living specification and test bindings.
            </p>
          </div>

          {/* Quick Metrics Strip */}
          <div className="flex flex-wrap items-center gap-2">
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
              onClick={() => setFilterCategory("personas")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-purple-500/20 bg-purple-500/10 text-purple-500 dark:text-purple-400 text-xs font-medium hover:bg-purple-500/20 transition-colors cursor-pointer"
            >
              <UserCheck className="h-3.5 w-3.5" />
              <span>{personas.length} Personas</span>
            </button>
          </div>
        </div>

        {/* Filter Tabs & Search Bar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-3 border-t border-border/60">
          {/* Segmented Filter Pills */}
          <div
            id="businessCategoryFilter"
            className="flex items-center gap-1 p-1 bg-muted/30 rounded-xl border border-border/60 text-xs"
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
              onClick={() => setFilterCategory("policies")}
              className={cn(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer flex items-center gap-1.5",
                filterCategory === "policies"
                  ? "bg-card text-foreground shadow-xs font-semibold text-blue-500 dark:text-blue-400"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <ShieldCheck className="h-3 w-3" />
              <span>System Policies (`POL-*`)</span>
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
              Domains & Capabilities
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
              Actor Personas
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

      {/* SECTION 1: SYSTEM GOVERNANCE POLICIES (POL-*) */}
      {(filterCategory === "all" || filterCategory === "policies") && (
        <div id="businessPoliciesSection" className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-blue-500" />
              <span>System Governance & Invariant Policies (`POL-*`)</span>
              <Badge variant="outline" className="text-[10px] font-mono">
                {filteredPolicies.length} Active
              </Badge>
            </h3>
            <span className="text-xs text-muted-foreground hidden sm:inline">
              Mandatory quality gates and zero-drift invariants
            </span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {filteredPolicies.map((pol) => {
              const boundCapabilities = capabilities.filter((c) =>
                c.business?.policies?.some((pRef) => pRef.includes(pol.id))
              );

              return (
                <Card
                  key={pol.id}
                  onClick={() => setSelectedEntity({ type: "policy", item: pol })}
                  className="p-6 sm:p-7 space-y-4 cursor-pointer hover:border-primary/60 hover:shadow-lg transition-all duration-200 group border border-border/80 bg-card rounded-2xl relative overflow-hidden"
                >
                  <div className="absolute top-0 right-0 w-32 h-32 bg-primary/5 rounded-bl-full pointer-events-none group-hover:bg-primary/10 transition-colors" />

                  {/* Policy Header */}
                  <div className="flex items-start justify-between gap-3">
                    <span className="font-mono text-xs sm:text-sm font-bold text-primary px-2.5 py-1 rounded-lg bg-primary/10 border border-primary/25">
                      {pol.id}
                    </span>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-xs font-semibold uppercase px-2 py-0.5">
                        {pol.category}
                      </Badge>
                      {pol.enforcement?.severity && (
                        <span className="px-2 py-0.5 rounded-md text-xs font-mono font-bold uppercase bg-rose-500/15 text-rose-400 border border-rose-500/30 flex items-center gap-1.5">
                          <Lock className="h-3 w-3" />
                          {pol.enforcement.severity}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Title */}
                  <h4 className="text-base sm:text-lg font-bold text-foreground leading-snug group-hover:text-primary transition-colors">
                    {pol.title}
                  </h4>

                  {/* Invariant Rule Callout */}
                  <div className="p-4 rounded-xl bg-muted/50 dark:bg-muted/30 border-l-4 border-l-primary border border-border/60 space-y-1">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-primary block">
                      Invariant Rule
                    </span>
                    <p className="text-sm sm:text-[15px] font-medium text-foreground leading-relaxed">
                      "{pol.rule}"
                    </p>
                  </div>

                  {/* Enforcement Mechanism */}
                  {pol.enforcement?.mechanism && (
                    <div className="space-y-1 pt-1">
                      <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider block">
                        Enforcement Gate
                      </span>
                      <p className="text-xs sm:text-sm text-foreground/80 leading-relaxed">
                        {pol.enforcement.mechanism}
                      </p>
                    </div>
                  )}

                  {/* Card Footer */}
                  <div className="pt-3 border-t border-border/60 flex items-center justify-between text-xs text-muted-foreground">
                    <span className="text-xs font-mono text-muted-foreground">
                      {boundCapabilities.length > 0
                        ? `${boundCapabilities.length} bound capability`
                        : "System-wide Governance Policy"}
                    </span>
                    <span className="text-primary text-xs sm:text-sm font-semibold flex items-center gap-1.5 group-hover:translate-x-1 transition-transform">
                      <span>Inspect Policy</span>
                      <ArrowRight className="h-3.5 w-3.5" />
                    </span>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* SECTION 2: BOUNDED PRODUCT DOMAINS & CAPABILITIES (DOM-* & CAP-*) */}
      {(filterCategory === "all" || filterCategory === "domains") && (
        <div id="businessDomainsSection" className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
              <Briefcase className="h-4 w-4 text-primary" />
              <span>Bounded Product Domains & Capabilities (`DOM-*`, `CAP-*`)</span>
              <Badge variant="outline" className="text-[10px] font-mono">
                {filteredDomains.length} Domains
              </Badge>
            </h3>
            <span className="text-xs text-muted-foreground hidden sm:inline">
              Click any domain or capability card to inspect its living specification
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredDomains.map((dom) => {
              const domCaps = capabilities.filter(
                (c) => c.domain === dom.id || dom.capabilities?.includes(c.id)
              );

              return (
                <Card
                  key={dom.id}
                  className="p-5 space-y-4 border border-border/80 bg-card hover:border-primary/40 transition-colors shadow-xs"
                >
                  {/* Domain Header */}
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-bold text-primary">
                          {dom.id}
                        </span>
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-[10px] uppercase font-semibold",
                            dom.classification === "core"
                              ? "bg-purple-500/10 text-purple-400 border-purple-500/20"
                              : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                          )}
                        >
                          {dom.classification || "Domain"}
                        </Badge>
                      </div>
                      <h3 className="text-base font-bold text-foreground">
                        {dom.name}
                      </h3>
                    </div>

                    <button
                      type="button"
                      onClick={() => setSelectedEntity({ type: "domain", item: dom })}
                      className="px-2.5 py-1 rounded-lg text-xs font-medium bg-muted/60 hover:bg-muted text-foreground transition-colors cursor-pointer shrink-0 flex items-center gap-1"
                    >
                      <span>Inspect Domain</span>
                      <ChevronRight className="h-3 w-3" />
                    </button>
                  </div>

                  {/* Domain Mission / Vision */}
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    {dom.mission}
                  </p>

                  {/* In-Scope Responsibilities Preview */}
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

                  {/* Bound Capabilities Sub-List */}
                  <div className="space-y-2 pt-2 border-t border-border/60">
                    <div className="flex items-center justify-between">
                      <h4 className="text-[11px] font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                        <Layers className="h-3 w-3 text-emerald-400" />
                        Bound Capabilities ({domCaps.length})
                      </h4>
                      <span className="text-[10px] text-muted-foreground">
                        Click capability to inspect
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
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-xs font-semibold text-primary group-hover:underline">
                                  {cap.id}
                                </span>
                                {cap.status && (
                                  <Badge
                                    variant="outline"
                                    className="text-[9px] uppercase px-1 py-0"
                                  >
                                    {cap.status}
                                  </Badge>
                                )}
                              </div>
                              <span className="text-muted-foreground group-hover:text-primary transition-colors flex items-center text-[10px] font-medium">
                                Details <ArrowRight className="h-3 w-3 ml-0.5" />
                              </span>
                            </div>

                            <h5 className="text-xs font-bold text-foreground group-hover:text-primary transition-colors">
                              {cap.name || cap.title}
                            </h5>

                            {cap.summary && (
                              <p className="text-[11px] text-muted-foreground line-clamp-2 leading-relaxed">
                                {cap.summary}
                              </p>
                            )}

                            {capInvariants.length > 0 && (
                              <div className="flex items-center gap-1.5 text-[10px] text-emerald-500 dark:text-emerald-400 font-mono pt-1">
                                <CheckCircle2 className="h-3 w-3 shrink-0" />
                                <span>
                                  {capInvariants.length} bound technical invariant(s)
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

      {/* SECTION 3: TARGET ACTOR PERSONAS (PER-*) */}
      {(filterCategory === "all" || filterCategory === "personas") && (
        <div id="businessPersonasSection" className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
              <UserCheck className="h-4 w-4 text-purple-400" />
              <span>Target Actor Personas (`PER-*`)</span>
              <Badge variant="outline" className="text-[10px] font-mono">
                {filteredPersonas.length} Personas
              </Badge>
            </h3>
            <span className="text-xs text-muted-foreground hidden sm:inline">
              Human stakeholders and autonomous AI agents
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredPersonas.map((per) => (
              <Card
                key={per.id}
                onClick={() => setSelectedEntity({ type: "persona", item: per })}
                className="p-5 space-y-3 cursor-pointer hover:border-purple-400/60 hover:shadow-lg transition-all duration-150 group border border-border/80 bg-card"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-bold text-purple-400 group-hover:underline">
                    {per.id}
                  </span>
                  <Badge variant="outline" className="text-[10px]">
                    {per.archetype}
                  </Badge>
                </div>

                <div className="space-y-0.5">
                  <h4 className="text-sm font-bold text-foreground group-hover:text-purple-400 transition-colors">
                    {per.name}
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
                  <span>Inspect Persona</span>
                  <ArrowRight className="h-3 w-3 ml-1" />
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* DETAIL INSPECTION DRAWER (SHEET) */}
      <Sheet
        open={Boolean(selectedEntity)}
        onOpenChange={(open) => !open && setSelectedEntity(null)}
        title={
          selectedEntity?.type === "policy"
            ? selectedEntity.item.title
            : selectedEntity?.type === "domain"
            ? selectedEntity.item.name
            : selectedEntity?.type === "capability"
            ? selectedEntity.item.name || selectedEntity.item.title || "Capability"
            : selectedEntity?.item.name || "Persona"
        }
        subtitle={selectedEntity?.item.id}
        maxWidth="6xl"
      >
        {selectedEntity && (
          <div className="space-y-5 text-xs text-foreground">
            {/* POLICY DETAIL DRAWER */}
            {selectedEntity.type === "policy" && (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="primary">Policy</Badge>
                  <Badge variant="outline">{selectedEntity.item.category}</Badge>
                  {selectedEntity.item.enforcement?.severity && (
                    <Badge variant="warning">
                      {selectedEntity.item.enforcement.severity}
                    </Badge>
                  )}
                  {selectedEntity.item.path && (
                    <span className="font-mono text-[11px] text-muted-foreground px-2 py-0.5 rounded bg-muted/40">
                      {selectedEntity.item.path}
                    </span>
                  )}
                </div>

                {/* Invariant Rule Statement */}
                <div className="space-y-1.5">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <ShieldCheck className="h-3.5 w-3.5 text-blue-500" />
                    Non-Negotiable Invariant Rule
                  </h4>
                  <div className="p-3.5 rounded-xl bg-blue-500/10 border-l-3 border-blue-500 text-xs font-medium text-foreground leading-relaxed">
                    "{selectedEntity.item.rule}"
                  </div>
                </div>

                {/* Enforcement Mechanism */}
                {selectedEntity.item.enforcement && (
                  <div className="space-y-1.5">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <Lock className="h-3.5 w-3.5 text-rose-500" />
                      Enforcement Mechanism & Quality Gate
                    </h4>
                    <div className="p-3 rounded-xl bg-muted/30 border border-border/80 text-xs text-muted-foreground leading-relaxed">
                      {selectedEntity.item.enforcement.mechanism}
                    </div>
                  </div>
                )}

                {/* Bound Capabilities */}
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
                        Bound Capabilities ({boundCaps.length})
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
                            <span className="font-mono font-semibold text-primary">
                              {c.id}
                            </span>
                            <span className="text-xs font-medium text-foreground">
                              {c.name || c.title}
                            </span>
                            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* Specification Body */}
                {selectedEntity.item.body && (
                  <div className="space-y-2 pt-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      Living Specification Rationale & Body
                    </h4>
                    <AsciiDocRenderer content={selectedEntity.item.body} />
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
                    {selectedEntity.item.classification || "core"}
                  </Badge>
                  {selectedEntity.item.path && (
                    <span className="font-mono text-[11px] text-muted-foreground px-2 py-0.5 rounded bg-muted/40">
                      {selectedEntity.item.path}
                    </span>
                  )}
                </div>

                {/* Vision / Mission */}
                <div className="space-y-1.5">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                    Domain Vision & Strategic Mission
                  </h4>
                  <p className="p-3.5 rounded-xl bg-muted/20 border border-border/80 text-xs text-foreground leading-relaxed">
                    {selectedEntity.item.mission}
                  </p>
                </div>

                {/* Bounded Context Responsibilities */}
                {selectedEntity.item.boundaries?.responsibilities && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      In-Scope Domain Responsibilities
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
                      Explicitly Out of Scope (Delegated)
                    </h4>
                    <ul className="space-y-1.5">
                      {selectedEntity.item.boundaries.out_of_scope.map((o, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 p-2 rounded-lg bg-muted/30 border border-border/60 text-xs text-muted-foreground"
                        >
                          <span className="text-muted-foreground font-mono">✕</span>
                          <span>{o}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Technical Architecture Mapping */}
                {selectedEntity.item.technical_mapping && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      Technical Architecture Mapping
                    </h4>
                    <div className="p-3 rounded-xl bg-muted/30 border border-border/80 space-y-2">
                      {selectedEntity.item.technical_mapping.components && (
                        <div>
                          <span className="text-[10px] font-bold text-muted-foreground uppercase">
                            Mapped Code Components
                          </span>
                          <div className="flex flex-wrap gap-1.5 mt-1">
                            {selectedEntity.item.technical_mapping.components.map((c, i) => (
                              <span
                                key={i}
                                className="font-mono text-[10px] px-2 py-0.5 rounded bg-card border border-border text-primary"
                              >
                                {c}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      {selectedEntity.item.technical_mapping.dependencies && (
                        <div>
                          <span className="text-[10px] font-bold text-muted-foreground uppercase">
                            Domain Dependencies
                          </span>
                          <div className="flex flex-wrap gap-1.5 mt-1">
                            {selectedEntity.item.technical_mapping.dependencies.map((d, i) => (
                              <span
                                key={i}
                                className="font-mono text-[10px] px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20"
                              >
                                {d}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Bound Capabilities in this Domain */}
                {(() => {
                  const domCaps = capabilities.filter(
                    (c) =>
                      c.domain === selectedEntity.item.id ||
                      selectedEntity.item.capabilities?.includes(c.id)
                  );
                  return (
                    <div className="space-y-2 pt-2 border-t border-border/60">
                      <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center justify-between">
                        <span>Bound Capabilities</span>
                        <span className="font-mono text-muted-foreground">
                          ({domCaps.length})
                        </span>
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
                            <div className="space-y-0.5">
                              <span className="font-mono font-semibold text-primary block">
                                {c.id}
                              </span>
                              <span className="text-xs font-bold text-foreground group-hover:text-primary">
                                {c.name || c.title}
                              </span>
                            </div>
                            <ArrowRight className="h-4 w-4 text-muted-foreground group-hover:text-primary group-hover:translate-x-0.5 transition-transform" />
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* Specification Body */}
                {selectedEntity.item.body && (
                  <div className="space-y-2 pt-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      Living Specification Body
                    </h4>
                    <AsciiDocRenderer content={selectedEntity.item.body} />
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
                      className="font-mono text-[11px] px-2.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors cursor-pointer"
                    >
                      {selectedEntity.item.domain}
                    </button>
                  )}
                  {selectedEntity.item.status && (
                    <Badge variant="outline">{selectedEntity.item.status}</Badge>
                  )}
                  {selectedEntity.item.version && (
                    <Badge variant="outline">v{selectedEntity.item.version}</Badge>
                  )}
                </div>

                {/* Summary & JTBD */}
                <div className="space-y-1.5">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                    Customer Job-to-be-Done (JTBD)
                  </h4>
                  <p className="p-3.5 rounded-xl bg-muted/20 border border-border/80 text-xs text-foreground leading-relaxed">
                    {selectedEntity.item.business?.jtbd ||
                      selectedEntity.item.summary ||
                      "Strategic capability anchor."}
                  </p>
                </div>

                {/* Technical Invariants Matrix */}
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
                          Technical Invariants & Test Bindings
                        </span>
                        <span className="font-mono text-muted-foreground">
                          ({invs.length})
                        </span>
                      </h4>
                      <div className="space-y-2">
                        {invs.map((inv, idx) => (
                          <div
                            key={idx}
                            className="p-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 space-y-1.5 text-xs"
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-mono font-bold text-emerald-500 dark:text-emerald-400">
                                {inv.id}
                              </span>
                              <Badge
                                variant="outline"
                                className="text-[9px] text-emerald-500 border-emerald-500/30"
                              >
                                POL-002 Verified
                              </Badge>
                            </div>
                            <p className="text-foreground/90 font-medium leading-relaxed">
                              {inv.rule}
                            </p>
                            {inv.test_binding && (
                              <div className="pt-1 flex items-center gap-1 text-[10px] text-muted-foreground font-mono">
                                <Terminal className="h-3 w-3 shrink-0" />
                                <span className="truncate">{inv.test_binding}</span>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* Interface Contracts & CLI Commands */}
                {selectedEntity.item.technical?.contracts?.interfaces && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <FileCode className="h-3.5 w-3.5 text-primary" />
                      Interface Contracts & CLI Interfaces
                    </h4>
                    <div className="p-3 rounded-xl bg-muted/30 border border-border/80 space-y-1.5">
                      {selectedEntity.item.technical.contracts.interfaces.map(
                        (iface, i) => (
                          <div
                            key={i}
                            className="font-mono text-[11px] p-1.5 rounded bg-card border border-border text-foreground"
                          >
                            {iface}
                          </div>
                        )
                      )}
                    </div>
                  </div>
                )}

                {/* Business Alignment: Target Personas & Policies */}
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
                              <span>{persona ? persona.name : pRef.split("/").pop()?.replace(".adoc", "")}</span>
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
                              <span>{policy ? policy.title : pRef.split("/").pop()?.replace(".adoc", "")}</span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>

                {/* Specification Body */}
                {selectedEntity.item.body && (
                  <div className="space-y-2 pt-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      Living Specification Body
                    </h4>
                    <AsciiDocRenderer content={selectedEntity.item.body} />
                  </div>
                )}
              </div>
            )}

            {/* PERSONA DETAIL DRAWER */}
            {selectedEntity.type === "persona" && (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="primary">Actor Persona</Badge>
                  <Badge variant="outline">{selectedEntity.item.archetype}</Badge>
                  {selectedEntity.item.path && (
                    <span className="font-mono text-[11px] text-muted-foreground px-2 py-0.5 rounded bg-muted/40">
                      {selectedEntity.item.path}
                    </span>
                  )}
                </div>

                {/* Role & Name */}
                <div className="p-3.5 rounded-xl bg-muted/20 border border-border/80 space-y-1">
                  <h4 className="text-sm font-bold text-foreground">
                    {selectedEntity.item.name}
                  </h4>
                  <p className="text-xs text-purple-400 font-medium">
                    {selectedEntity.item.role}
                  </p>
                </div>

                {/* Goals */}
                {selectedEntity.item.goals && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      Strategic Goals & Desired Outcomes
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

                {/* Pain Points */}
                {selectedEntity.item.pain_points && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider text-rose-400">
                      Pain Points & Architectural Friction
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

                {/* Interaction Modes */}
                {selectedEntity.item.interaction_modes && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      System Interaction Modes
                    </h4>
                    <ul className="space-y-1.5">
                      {selectedEntity.item.interaction_modes.map((mode, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 p-2 rounded-lg bg-card border border-border text-xs text-foreground/90 font-mono text-[11px]"
                        >
                          <Terminal className="h-3.5 w-3.5 text-primary mt-0.5 shrink-0" />
                          <span>{mode}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Specification Body */}
                {selectedEntity.item.body && (
                  <div className="space-y-2 pt-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      Living Specification Profile
                    </h4>
                    <AsciiDocRenderer content={selectedEntity.item.body} />
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
