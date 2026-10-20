import * as React from "react";
import {
  Search,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  AlertCircle,
  FileText,
  Folder,
  FolderOpen,
  Network,
  Briefcase,
  GitBranch,
  Palette,
  ShieldCheck,
  Lightbulb,
  ListTree,
  Table as TableIcon,
  Box,
  Layers,
  FileCode,
  Smartphone,
  Eye,
  Workflow,
  Sparkles,
  Users,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { SpecReader } from "@/components/ui/SpecReader";
import { cn } from "@/lib/utils";
import type { PortalData, SpecItem } from "@/types";

export interface CatalogViewProps {
  data: PortalData;
  /** Spec ID to open in the inspector (set by in-portal xref navigation). */
  focusSpecId?: string;
  focusNonce?: number;
  onFocusHandled?: () => void;
}

export interface SpecCategoryDefinition {
  id: string;
  name: string;
  description: string;
  icon: any;
  color: string;
  badgeBg: string;
  types: string[];
}

export interface SubGroupNode {
  id: string;
  name: string;
  badge?: string;
  description?: string;
  icon?: any;
  parentSpec?: SpecItem; // When the node itself is a specification (e.g. Domain, Client, System)
  specs: SpecItem[]; // Leaf child specifications
}

export interface HierarchyBranch {
  id: string;
  name: string;
  description: string;
  icon: any;
  color: string;
  badgeBg: string;
  types: string[];
  subgroups: SubGroupNode[];
  total: number;
  validCount: number;
}

const SPEC_CATEGORIES: SpecCategoryDefinition[] = [
  {
    id: "domains",
    name: "Bounded Domains & Capabilities",
    description: "Strategic DDD bounded contexts, product capabilities, and invariants",
    icon: Briefcase,
    color: "text-indigo-500",
    badgeBg: "bg-indigo-500/10 text-indigo-500 border-indigo-500/20",
    types: ["product/domain", "product/capability"],
  },
  {
    id: "architecture",
    name: "Architecture & Technology",
    description: "System Arc42 topology, C4 containers, and interface contracts",
    icon: Network,
    color: "text-blue-500",
    badgeBg: "bg-blue-500/10 text-blue-500 border-blue-500/20",
    types: ["tech/architecture", "tech/container", "tech/component", "tech/contract"],
  },
  {
    id: "design",
    name: "Design Systems & Surfaces",
    description: "W3C DTCG tokens, multi-client applications, screens, UI components, and flows",
    icon: Palette,
    color: "text-pink-500",
    badgeBg: "bg-pink-500/10 text-pink-500 border-pink-500/20",
    types: [
      "design/system",
      "design/client",
      "design/screen",
      "design/ui-component",
      "design/flow",
    ],
  },
  {
    id: "governance",
    name: "Governance, Policies & System",
    description: "Global system definitions and living invariant policies",
    icon: ShieldCheck,
    color: "text-emerald-500",
    badgeBg: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
    types: ["product/system", "product/policy"],
  },
  {
    id: "personas_research",
    name: "Personas & Explorations",
    description: "Actor archetypes, JTBD goals, and experimental technical spikes",
    icon: Lightbulb,
    color: "text-amber-500",
    badgeBg: "bg-amber-500/10 text-amber-500 border-amber-500/20",
    types: ["product/persona", "product/exploration"],
  },
  {
    id: "deltas",
    name: "SpecDelta Mutations",
    description: "Formal planned architectural mutations and migrations",
    icon: GitBranch,
    color: "text-purple-500",
    badgeBg: "bg-purple-500/10 text-purple-500 border-purple-500/20",
    types: ["delta/mutation"],
  },
];

export function CatalogView({ data, focusSpecId, focusNonce, onFocusHandled }: CatalogViewProps) {
  const [searchQuery, setSearchQuery] = React.useState("");
  const [selectedType, setSelectedType] = React.useState("all");
  const [viewMode, setViewMode] = React.useState<"hierarchy" | "flat">("hierarchy");
  const [inspectedSpec, setInspectedSpec] = React.useState<SpecItem | null>(null);

  // Open the spec targeted by an xref:/<<ID>>/link:*.adoc[] click from the AsciiDoc reader.
  React.useEffect(() => {
    if (!focusSpecId) return;
    const target = data.specs.find((s) => s.id === focusSpecId);
    if (target) setInspectedSpec(target);
    onFocusHandled?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSpecId, focusNonce]);

  // Group specs by type
  const specTypes = React.useMemo(() => {
    const set = new Set<string>();
    data.specs.forEach((s) => set.add(s.type));
    return Array.from(set).sort();
  }, [data.specs]);

  // Filter specs
  const filteredSpecs = React.useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    return data.specs.filter((s) => {
      const matchesType = selectedType === "all" || s.type === selectedType;
      const matchesSearch =
        !q ||
        s.id.toLowerCase().includes(q) ||
        s.title.toLowerCase().includes(q) ||
        s.path.toLowerCase().includes(q);
      return matchesType && matchesSearch;
    });
  }, [data.specs, searchQuery, selectedType]);

  // Construct Multi-Level Hierarchy (Category -> SubGroup/Parent Entity -> Leaf Specifications)
  const hierarchicalBranches = React.useMemo<HierarchyBranch[]>(() => {
    return SPEC_CATEGORIES.map((cat) => {
      const catSpecs = filteredSpecs.filter((s) => cat.types.includes(s.type));
      const subgroups: SubGroupNode[] = [];

      if (cat.id === "domains") {
        // Multi-level: Domain (DOM-*) -> Bound Capabilities (CAP-*)
        const domainSpecs = catSpecs.filter((s) => s.type === "product/domain");
        const capSpecs = catSpecs.filter((s) => s.type === "product/capability");
        const assignedCaps = new Set<string>();

        domainSpecs.forEach((dom) => {
          const domCaps = capSpecs.filter(
            (c) =>
              c.frontmatter?.domain === dom.id ||
              c.path.includes(dom.id.toLowerCase().replace("dom-", ""))
          );
          domCaps.forEach((c) => assignedCaps.add(c.id));

          subgroups.push({
            id: `subgroup-${dom.id}`,
            name: dom.title || dom.id,
            badge: "Domain",
            icon: Briefcase,
            description: dom.frontmatter?.classification || "Bounded Context",
            parentSpec: dom,
            specs: domCaps,
          });
        });

        // Any orphan capabilities
        const orphanCaps = capSpecs.filter((c) => !assignedCaps.has(c.id));
        if (orphanCaps.length > 0) {
          subgroups.push({
            id: "subgroup-orphan-capabilities",
            name: "Other Capabilities",
            badge: "Capabilities",
            icon: Layers,
            specs: orphanCaps,
          });
        }
      } else if (cat.id === "design") {
        // Multi-level:
        // 1. Design Systems (DS-*) -> UI Components (UIC-*)
        // 2. Client Applications (CLT-*) -> Screens (SCR-*) & Flows (FLOW-*)
        const dsSpecs = catSpecs.filter((s) => s.type === "design/system");
        const uicSpecs = catSpecs.filter((s) => s.type === "design/ui-component");
        const clientSpecs = catSpecs.filter((s) => s.type === "design/client");
        const screenSpecs = catSpecs.filter((s) => s.type === "design/screen");
        const flowSpecs = catSpecs.filter((s) => s.type === "design/flow");

        dsSpecs.forEach((ds) => {
          const dsDir = ds.path ? ds.path.substring(0, ds.path.lastIndexOf("/")) : "";
          const boundUics = uicSpecs.filter(
            (u) =>
              ds.id.toLowerCase() in (u.frontmatter || {}) ||
              (Boolean(dsDir) && u.path.startsWith(`${dsDir}/`)) ||
              u.frontmatter?.design_system === ds.id
          );
          subgroups.push({
            id: `subgroup-${ds.id}`,
            name: ds.title || ds.id,
            badge: "Design System",
            icon: Palette,
            parentSpec: ds,
            specs: boundUics,
          });
        });

        clientSpecs.forEach((clt) => {
          const cltDir = clt.path ? clt.path.substring(0, clt.path.lastIndexOf("/")) : "";
          const cltScreensList: string[] = Array.isArray(clt.frontmatter?.screens)
            ? clt.frontmatter.screens
            : [];
          const cltFlowsList: string[] = Array.isArray(clt.frontmatter?.flows)
            ? clt.frontmatter.flows
            : [];
          const clientScreens = screenSpecs.filter(
            (sc) =>
              cltScreensList.some((ref) => ref === sc.id || sc.path.endsWith(ref)) ||
              (Boolean(cltDir) && sc.path.startsWith(`${cltDir}/`))
          );
          const clientFlows = flowSpecs.filter(
            (fl) =>
              cltFlowsList.some((ref) => ref === fl.id || fl.path.endsWith(ref)) ||
              (Boolean(cltDir) && fl.path.startsWith(`${cltDir}/`)) ||
              (Array.isArray(fl.frontmatter?.steps) &&
                fl.frontmatter.steps.some((st: any) => st?.client === clt.id))
          );
          subgroups.push({
            id: `subgroup-${clt.id}`,
            name: clt.title || clt.id,
            badge: "Client Surface",
            icon: Smartphone,
            parentSpec: clt,
            specs: [...clientScreens, ...clientFlows],
          });
        });

        // Any leftover design specs not bound
        const boundIds = new Set(
          subgroups.flatMap((g) => [
            ...(g.parentSpec ? [g.parentSpec.id] : []),
            ...g.specs.map((s) => s.id),
          ])
        );
        const leftover = catSpecs.filter((s) => !boundIds.has(s.id));
        if (leftover.length > 0) {
          subgroups.push({
            id: "subgroup-other-design",
            name: "Other Design Specifications",
            badge: "Design",
            icon: Palette,
            specs: leftover,
          });
        }
      } else if (cat.id === "architecture") {
        // Multi-level:
        // 1. System Architecture (ARCH-*)
        // 2. C4 Containers (CNT-*)
        // 3. C4 Components (CMP-*)
        // 4. Interface Contracts (CTR-*)
        const archOverview = catSpecs.filter((s) => s.type === "tech/architecture");
        const containers = catSpecs.filter((s) => s.type === "tech/container");
        const components = catSpecs.filter((s) => s.type === "tech/component");
        const contracts = catSpecs.filter((s) => s.type === "tech/contract");

        if (archOverview.length > 0) {
          archOverview.forEach((arch) => {
            subgroups.push({
              id: `subgroup-${arch.id}`,
              name: arch.title || arch.id,
              badge: "Arc42 Architecture",
              icon: Network,
              parentSpec: arch,
              specs: [],
            });
          });
        }

        if (containers.length > 0) {
          subgroups.push({
            id: "subgroup-c4-containers",
            name: "C4 Level 2 Containers & Deployable Units",
            badge: "Containers",
            icon: Box,
            specs: containers,
          });
        }

        if (components.length > 0) {
          subgroups.push({
            id: "arch-components",
            name: "Components (C4 L3)",
            badge: "Components",
            icon: Layers,
            specs: components,
          });
        }

        if (contracts.length > 0) {
          subgroups.push({
            id: "subgroup-c4-contracts",
            name: "Interface & Protocol Contracts (CTR-*)",
            badge: "Contracts",
            icon: FileCode,
            specs: contracts,
          });
        }
      } else if (cat.id === "governance") {
        // Multi-level:
        // 1. System Definition (SYS-*)
        // 2. Non-Negotiable System Policies (POL-*)
        const systemSpecs = catSpecs.filter((s) => s.type === "product/system");
        const policySpecs = catSpecs.filter((s) => s.type === "product/policy");

        systemSpecs.forEach((sys) => {
          subgroups.push({
            id: `subgroup-${sys.id}`,
            name: sys.title || sys.id,
            badge: "System Definition",
            icon: ShieldCheck,
            parentSpec: sys,
            specs: [],
          });
        });

        if (policySpecs.length > 0) {
          subgroups.push({
            id: "subgroup-policies",
            name: "Living System Policies (POL-*)",
            badge: "Policies",
            icon: ShieldCheck,
            specs: policySpecs,
          });
        }
      } else if (cat.id === "personas_research") {
        // Multi-level:
        // 1. Actor Personas (PER-*)
        // 2. Technical Explorations & Spikes (EXP-*)
        const personas = catSpecs.filter((s) => s.type === "product/persona");
        const explorations = catSpecs.filter((s) => s.type === "product/exploration");

        if (personas.length > 0) {
          subgroups.push({
            id: "subgroup-personas",
            name: "Target Actor Personas (Human & AI Agents)",
            badge: "Personas",
            icon: Users,
            specs: personas,
          });
        }

        if (explorations.length > 0) {
          subgroups.push({
            id: "subgroup-explorations",
            name: "Hypothesis Spikes & Technical Explorations",
            badge: "Explorations",
            icon: Lightbulb,
            specs: explorations,
          });
        }
      } else if (cat.id === "deltas") {
        // Multi-level: Group by status (Applied vs In-Review/Proposed)
        const applied = catSpecs.filter(
          (s) => s.frontmatter?.status?.toLowerCase() === "applied"
        );
        const pending = catSpecs.filter(
          (s) => s.frontmatter?.status?.toLowerCase() !== "applied"
        );

        if (applied.length > 0) {
          subgroups.push({
            id: "subgroup-deltas-applied",
            name: `Applied SpecDeltas (${applied.length})`,
            badge: "Applied",
            icon: GitBranch,
            specs: applied,
          });
        }

        if (pending.length > 0) {
          subgroups.push({
            id: "subgroup-deltas-pending",
            name: `Proposed & In-Review SpecDeltas (${pending.length})`,
            badge: "Pending",
            icon: GitBranch,
            specs: pending,
          });
        }
      }

      // Compute counts
      const allSubSpecs: SpecItem[] = [];
      subgroups.forEach((sg) => {
        if (sg.parentSpec) allSubSpecs.push(sg.parentSpec);
        sg.specs.forEach((s) => allSubSpecs.push(s));
      });
      const validCount = allSubSpecs.filter((s) => s.is_valid).length;

      return {
        ...cat,
        subgroups,
        total: allSubSpecs.length,
        validCount,
      };
    });
  }, [filteredSpecs]);

  // Track expanded state for both Level 1 (categories) and Level 2 (subgroups)
  const [expandedNodes, setExpandedNodes] = React.useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    SPEC_CATEGORIES.forEach((c) => {
      initial[c.id] = true;
    });
    return initial;
  });

  // Expand all branches and subgroups when user types a search query
  React.useEffect(() => {
    if (searchQuery.trim()) {
      const allOpen: Record<string, boolean> = {};
      hierarchicalBranches.forEach((b) => {
        allOpen[b.id] = true;
        b.subgroups.forEach((sg) => {
          allOpen[sg.id] = true;
        });
      });
      setExpandedNodes(allOpen);
    }
  }, [searchQuery, hierarchicalBranches]);

  const toggleTreeGridRow = (nodeKey: string) => {
    setExpandedNodes((prev) => ({
      ...prev,
      [nodeKey]: !prev[nodeKey],
    }));
  };

  const expandAllTreeGrid = () => {
    const next: Record<string, boolean> = {};
    hierarchicalBranches.forEach((b) => {
      next[b.id] = true;
      b.subgroups.forEach((sg) => {
        next[sg.id] = true;
      });
    });
    setExpandedNodes(next);
  };

  const collapseAllTreeGrid = () => {
    setExpandedNodes({});
  };

  return (
    <div className="space-y-4 animate-in fade-in duration-200">
      {/* Top Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 rounded-2xl bg-card border border-border md-elevation-1">
        <div className="flex flex-wrap items-center gap-3 flex-1">
          {/* Search Box */}
          <div className="relative flex-1 min-w-[200px] max-w-md">
            <Search className="h-4 w-4 text-muted-foreground absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              id="catalogSearchInput"
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search specifications by ID, title, or path..."
              className="w-full bg-muted/40 border border-border text-xs rounded-xl pl-9 pr-14 py-2 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <kbd
              id="catalogSearchKbd"
              className="absolute right-3 top-1/2 -translate-y-1/2 font-mono text-[10px] text-muted-foreground bg-card border border-border/80 px-1.5 py-0.5 rounded"
            >
              /
            </kbd>
          </div>

          {/* Type Filter */}
          <div className="tree-select-wrapper">
            <select
              id="catalogTypeFilter"
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              className="bg-muted/40 border border-border text-xs rounded-xl px-3 py-2 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="all">All Spec Types ({data.specs.length})</option>
              {specTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          {/* Match pill count */}
          <span
            id="catalogSearchMatchPill"
            className="text-xs font-mono text-muted-foreground px-2.5 py-1 rounded-full bg-muted/60"
          >
            {filteredSpecs.length} spec(s)
          </span>
        </div>

        {/* View Mode & Hierarchy Controls */}
        <div className="flex items-center gap-2 text-xs">
          {/* View Mode Switcher */}
          <div className="flex items-center bg-muted/60 p-0.5 rounded-xl border border-border/60">
            <button
              onClick={() => setViewMode("hierarchy")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                viewMode === "hierarchy"
                  ? "bg-card text-foreground font-semibold md-elevation-1"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="Multi-Level Hierarchical Tree"
            >
              <ListTree className="h-3.5 w-3.5 text-primary" />
              <span>Multi-Level Hierarchy</span>
            </button>
            <button
              onClick={() => setViewMode("flat")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                viewMode === "flat"
                  ? "bg-card text-foreground font-semibold md-elevation-1"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="Flat Table View"
            >
              <TableIcon className="h-3.5 w-3.5" />
              <span>Flat</span>
            </button>
          </div>

          <span className="text-border">|</span>

          {/* Expand / Collapse Controls */}
          <div className="flex items-center gap-1">
            <button
              onClick={expandAllTreeGrid}
              className="px-2.5 py-1 rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer text-xs"
            >
              Expand All
            </button>
            <button
              onClick={collapseAllTreeGrid}
              className="px-2.5 py-1 rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer text-xs"
            >
              Collapse All
            </button>
          </div>
        </div>
      </div>

      {/* Specifications TreeGrid Table */}
      <Card className="p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table id="specsTreeGridTable" className="w-full text-left text-xs">
            <thead className="bg-muted/40 border-b border-border text-muted-foreground font-semibold uppercase tracking-wider text-[10px]">
              <tr>
                <th className="py-3 px-4 w-5/12">Specification Hierarchy & Document</th>
                <th className="py-3 px-4 w-2/12">Type</th>
                <th className="py-3 px-4 w-3/12">File Path</th>
                <th className="py-3 px-4 w-2/12">Schema Conformance</th>
              </tr>
            </thead>
            <tbody id="specsTreeGridBody" className="divide-y divide-border/60">
              {filteredSpecs.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-12 text-center text-muted-foreground">
                    No specifications match your search query "{searchQuery}".
                  </td>
                </tr>
              ) : viewMode === "hierarchy" ? (
                // MULTI-LEVEL HIERARCHICAL TREE VIEW:
                // Level 1: Category Branch
                // Level 2: SubGroup / Parent Entity
                // Level 3: Leaf Specifications
                hierarchicalBranches.map((branch) => {
                  if (branch.total === 0) return null;
                  const isCatExpanded = Boolean(expandedNodes[branch.id]);
                  const Icon = branch.icon;

                  return (
                    <React.Fragment key={branch.id}>
                      {/* LEVEL 1: Category Header Row */}
                      <tr
                        onClick={() => toggleTreeGridRow(branch.id)}
                        className="bg-muted/30 hover:bg-muted/60 cursor-pointer select-none transition-colors border-t-2 border-border/80"
                      >
                        <td colSpan={2} className="py-2.5 px-4">
                          <div className="flex items-center gap-2.5">
                            <span className="p-1 rounded-md text-muted-foreground hover:text-foreground transition-colors">
                              {isCatExpanded ? (
                                <ChevronDown className="h-4 w-4" />
                              ) : (
                                <ChevronRight className="h-4 w-4" />
                              )}
                            </span>
                            <div className={cn("p-1.5 rounded-lg shrink-0", branch.badgeBg)}>
                              {isCatExpanded ? (
                                <FolderOpen className="h-4 w-4" />
                              ) : (
                                <Folder className="h-4 w-4" />
                              )}
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="font-bold text-xs text-foreground tracking-tight">
                                {branch.name}
                              </span>
                              <span className="text-[11px] text-muted-foreground hidden sm:inline">
                                • {branch.description}
                              </span>
                            </div>
                          </div>
                        </td>
                        <td className="py-2.5 px-4 font-mono text-[11px] text-muted-foreground">
                          {branch.types.join(", ") || "various"}
                        </td>
                        <td className="py-2.5 px-4">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary font-semibold">
                              {branch.total} spec(s)
                            </span>
                            <span className="text-emerald-500 font-mono text-[10px] font-semibold">
                              {branch.validCount}/{branch.total} Valid
                            </span>
                          </div>
                        </td>
                      </tr>

                      {/* LEVEL 2 & 3: Subgroups and Child Specs */}
                      {isCatExpanded &&
                        branch.subgroups.map((subgroup, sgIdx) => {
                          const isSgExpanded =
                            expandedNodes[subgroup.id] !== false; // default open
                          const hasChildren = subgroup.specs.length > 0;
                          const SgIcon = subgroup.icon || Folder;
                          const isLastSg = sgIdx === branch.subgroups.length - 1;

                          return (
                            <React.Fragment key={subgroup.id}>
                              {/* LEVEL 2: SubGroup / Parent Entity Row */}
                              <tr className="hover:bg-muted/30 transition-colors border-t border-border/40 group">
                                <td className="py-2 px-4 pl-8">
                                  <div className="flex items-center gap-2">
                                    <span className="text-muted-foreground/60 font-mono text-xs select-none shrink-0">
                                      {isLastSg ? "└── " : "├── "}
                                    </span>
                                    {hasChildren ? (
                                      <button
                                        type="button"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          toggleTreeGridRow(subgroup.id);
                                        }}
                                        className="p-0.5 rounded text-muted-foreground hover:text-foreground cursor-pointer"
                                      >
                                        {isSgExpanded ? (
                                          <ChevronDown className="h-3.5 w-3.5" />
                                        ) : (
                                          <ChevronRight className="h-3.5 w-3.5" />
                                        )}
                                      </button>
                                    ) : (
                                      <span className="w-3.5" />
                                    )}
                                    <SgIcon className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                    {subgroup.parentSpec ? (
                                      <div
                                        onClick={() => setInspectedSpec(subgroup.parentSpec!)}
                                        className="flex items-center gap-2 cursor-pointer hover:underline"
                                      >
                                        <span className="font-mono font-bold text-primary text-xs">
                                          {subgroup.parentSpec.id}
                                        </span>
                                        <span className="text-foreground font-semibold truncate max-w-xs">
                                          {subgroup.parentSpec.title}
                                        </span>
                                      </div>
                                    ) : (
                                      <span className="font-semibold text-foreground text-xs">
                                        {subgroup.name}
                                      </span>
                                    )}
                                    {subgroup.badge && (
                                      <Badge variant="outline" className="text-[9px] uppercase px-1 py-0">
                                        {subgroup.badge}
                                      </Badge>
                                    )}
                                    {hasChildren && (
                                      <span className="text-[10px] text-muted-foreground font-mono">
                                        ({subgroup.specs.length})
                                      </span>
                                    )}
                                  </div>
                                </td>
                                <td className="py-2 px-4">
                                  {subgroup.parentSpec ? (
                                    <Badge variant="outline" className="text-[10px]">
                                      {subgroup.parentSpec.type}
                                    </Badge>
                                  ) : (
                                    <span className="font-mono text-[11px] text-muted-foreground">
                                      group
                                    </span>
                                  )}
                                </td>
                                <td className="py-2 px-4 font-mono text-muted-foreground text-[11px] truncate max-w-xs">
                                  {subgroup.parentSpec?.path || `${subgroup.specs.length} spec(s)`}
                                </td>
                                <td className="py-2 px-4">
                                  {subgroup.parentSpec ? (
                                    subgroup.parentSpec.is_valid ? (
                                      <span className="inline-flex items-center gap-1 text-emerald-500 font-semibold font-mono text-[11px]">
                                        <CheckCircle2 className="h-3 w-3" />
                                        VALID
                                      </span>
                                    ) : (
                                      <span className="inline-flex items-center gap-1 text-red-500 font-semibold font-mono text-[11px]">
                                        <AlertCircle className="h-3 w-3" />
                                        INVALID
                                      </span>
                                    )
                                  ) : (
                                    <span className="text-muted-foreground font-mono text-[10px]">
                                      {subgroup.specs.filter((s) => s.is_valid).length}/{subgroup.specs.length} valid
                                    </span>
                                  )}
                                </td>
                              </tr>

                              {/* LEVEL 3: Leaf Specifications under Subgroup */}
                              {isSgExpanded &&
                                hasChildren &&
                                subgroup.specs.map((spec, specIdx) => {
                                  const isLastLeaf = specIdx === subgroup.specs.length - 1;
                                  return (
                                    <tr
                                      key={spec.id}
                                      onClick={() => setInspectedSpec(spec)}
                                      className="hover:bg-muted/40 cursor-pointer transition-colors group"
                                    >
                                      <td className="py-2 px-4 pl-16">
                                        <div className="flex items-center gap-2">
                                          <span className="text-muted-foreground/50 font-mono text-xs select-none shrink-0">
                                            {isLastLeaf ? "└── " : "├── "}
                                          </span>
                                          <FileText className="h-3 w-3 text-muted-foreground shrink-0 group-hover:text-primary transition-colors" />
                                          <span className="font-mono font-bold text-primary group-hover:underline">
                                            {spec.id}
                                          </span>
                                          <span className="text-foreground font-medium truncate max-w-sm">
                                            {spec.title}
                                          </span>
                                        </div>
                                      </td>
                                      <td className="py-2 px-4">
                                        <Badge variant="outline" className="text-[10px]">
                                          {spec.type}
                                        </Badge>
                                      </td>
                                      <td className="py-2 px-4 font-mono text-muted-foreground text-[11px] truncate max-w-xs">
                                        {spec.path}
                                      </td>
                                      <td className="py-2 px-4">
                                        {spec.is_valid ? (
                                          <span className="inline-flex items-center gap-1 text-emerald-500 font-semibold font-mono text-[11px]">
                                            <CheckCircle2 className="h-3 w-3" />
                                            VALID
                                          </span>
                                        ) : (
                                          <span className="inline-flex items-center gap-1 text-red-500 font-semibold font-mono text-[11px]">
                                            <AlertCircle className="h-3 w-3" />
                                            INVALID
                                          </span>
                                        )}
                                      </td>
                                    </tr>
                                  );
                                })}
                            </React.Fragment>
                          );
                        })}
                    </React.Fragment>
                  );
                })
              ) : (
                // Flat Table View
                filteredSpecs.map((spec) => (
                  <tr
                    key={spec.id}
                    onClick={() => setInspectedSpec(spec)}
                    className="hover:bg-muted/30 cursor-pointer transition-colors group"
                  >
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-primary group-hover:underline">
                          {spec.id}
                        </span>
                        <span className="text-foreground font-medium truncate max-w-xs">
                          {spec.title}
                        </span>
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <Badge variant="outline" className="text-[10px]">
                        {spec.type}
                      </Badge>
                    </td>
                    <td className="py-3 px-4 font-mono text-muted-foreground text-[11px] truncate max-w-sm">
                      {spec.path}
                    </td>
                    <td className="py-3 px-4">
                      {spec.is_valid ? (
                        <span className="inline-flex items-center gap-1 text-emerald-500 font-semibold font-mono text-[11px]">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          VALID
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-red-500 font-semibold font-mono text-[11px]">
                          <AlertCircle className="h-3.5 w-3.5" />
                          INVALID
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Specification Inspector Sheet */}
      <Sheet
        open={Boolean(inspectedSpec)}
        onOpenChange={(open) => !open && setInspectedSpec(null)}
        title={inspectedSpec?.title || "Specification"}
        subtitle={inspectedSpec?.id}
        maxWidth="6xl"
      >
        {inspectedSpec && (
          <SpecReader
            spec={inspectedSpec}
            onClose={() => setInspectedSpec(null)}
            isModal={true}
          />
        )}
      </Sheet>
    </div>
  );
}
