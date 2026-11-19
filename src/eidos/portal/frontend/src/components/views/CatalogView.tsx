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
  Users,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { SpecReader } from "@/components/ui/SpecReader";
import {
  formatDisplayTitle,
  formatSpecType,
  resolveEntityTitle,
  useEntityLabels,
} from "@/lib/presentation";
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
  parentSpec?: SpecItem;
  specs: SpecItem[];
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
    name: "Domains & Capabilities",
    description: "Product areas, capabilities, and business rules",
    icon: Briefcase,
    color: "text-indigo-500",
    badgeBg: "bg-indigo-500/10 text-indigo-500 border-indigo-500/20",
    types: ["product/domain", "product/capability"],
  },
  {
    id: "architecture",
    name: "Architecture & Systems",
    description: "System structure, containers, components, and interfaces",
    icon: Network,
    color: "text-blue-500",
    badgeBg: "bg-blue-500/10 text-blue-500 border-blue-500/20",
    types: ["tech/architecture", "tech/container", "tech/component", "tech/contract"],
  },
  {
    id: "design",
    name: "Design Systems & Experience",
    description: "Design tokens, client apps, screens, UI components, and user flows",
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
    name: "Overview & Policies",
    description: "System overview and organization-wide governance policies",
    icon: ShieldCheck,
    color: "text-emerald-500",
    badgeBg: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
    types: ["product/system", "product/policy"],
  },
  {
    id: "personas_research",
    name: "Personas & Explorations",
    description: "User personas and research explorations",
    icon: Lightbulb,
    color: "text-amber-500",
    badgeBg: "bg-amber-500/10 text-amber-500 border-amber-500/20",
    types: ["product/persona", "product/exploration"],
  },
  {
    id: "deltas",
    name: "Change Proposals",
    description: "Planned changes and system evolution history",
    icon: GitBranch,
    color: "text-purple-500",
    badgeBg: "bg-purple-500/10 text-purple-500 border-purple-500/20",
    types: ["delta/mutation"],
  },
];

export function CatalogView({ data, focusSpecId, focusNonce, onFocusHandled }: CatalogViewProps) {
  const labelIndex = useEntityLabels();
  const [searchQuery, setSearchQuery] = React.useState("");
  const [selectedType, setSelectedType] = React.useState("all");
  const [viewMode, setViewMode] = React.useState<"hierarchy" | "flat">("hierarchy");
  const [inspectedSpec, setInspectedSpec] = React.useState<SpecItem | null>(null);
  const searchInputRef = React.useRef<HTMLInputElement>(null);

  // Bind '/' shortcut to focus #catalogSearchInput when no text input is active
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const active = document.activeElement;
      if (
        active instanceof HTMLInputElement ||
        active instanceof HTMLTextAreaElement ||
        active instanceof HTMLSelectElement ||
        (active as HTMLElement | null)?.isContentEditable
      ) {
        return;
      }
      e.preventDefault();
      searchInputRef.current?.focus();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Open the spec targeted by an xref:/<<ID>>/link:*.adoc[] click from the AsciiDoc reader.
  React.useEffect(() => {
    if (!focusSpecId) return;
    const target = data.specs.find((s) => s.id === focusSpecId);
    if (target) setInspectedSpec(target);
    onFocusHandled?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSpecId, focusNonce]);

  const specTypes = React.useMemo(() => {
    const set = new Set<string>();
    data.specs.forEach((s) => set.add(s.type));
    return Array.from(set).sort((a, b) =>
      formatSpecType(a).localeCompare(formatSpecType(b))
    );
  }, [data.specs]);

  const specSearchMap = React.useMemo(() => {
    const map = new Map<string, string>();
    data.specs.forEach((s) => {
      const headings = (s.body || "")
        .split("\n")
        .filter((line) => line.startsWith("="))
        .join(" ");
      const fmSummary = [
        s.frontmatter?.responsibility,
        s.frontmatter?.summary,
        s.frontmatter?.mission,
        s.frontmatter?.rule,
      ]
        .filter(Boolean)
        .join(" ");
      map.set(
        s.id,
        `${s.id} ${s.title} ${s.path} ${s.type} ${formatSpecType(s.type)} ${headings} ${fmSummary}`.toLowerCase()
      );
    });
    return map;
  }, [data.specs]);

  const filteredSpecs = React.useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    return data.specs.filter((s) => {
      const matchesType = selectedType === "all" || s.type === selectedType;
      const matchesSearch =
        !q ||
        (specSearchMap.get(s.id)?.includes(q) ?? false) ||
        s.id.toLowerCase().includes(q) ||
        s.title.toLowerCase().includes(q) ||
        s.path.toLowerCase().includes(q);
      return matchesType && matchesSearch;
    });
  }, [data.specs, searchQuery, selectedType, specSearchMap]);

  const resolveSpecSection = React.useCallback(
    (spec: SpecItem): string => {
      const fm = spec.frontmatter || {};
      if (fm.domain) return resolveEntityTitle(String(fm.domain), labelIndex);
      if (fm.container) return resolveEntityTitle(String(fm.container), labelIndex);
      if (fm.design_system) return resolveEntityTitle(String(fm.design_system), labelIndex);
      const cat = SPEC_CATEGORIES.find((c) => c.types.includes(spec.type));
      return cat?.name || "General";
    },
    [labelIndex]
  );

  const hierarchicalBranches = React.useMemo<HierarchyBranch[]>(() => {
    return SPEC_CATEGORIES.map((cat) => {
      const catSpecs = filteredSpecs.filter((s) => cat.types.includes(s.type));
      const subgroups: SubGroupNode[] = [];

      if (cat.id === "domains") {
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
            name: formatDisplayTitle(dom.title, dom.id),
            badge: "Domain",
            icon: Briefcase,
            parentSpec: dom,
            specs: domCaps,
          });
        });

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
            name: formatDisplayTitle(ds.title, ds.id),
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
            name: formatDisplayTitle(clt.title, clt.id),
            badge: "Client App",
            icon: Smartphone,
            parentSpec: clt,
            specs: [...clientScreens, ...clientFlows],
          });
        });

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
            name: "Other Design Documents",
            badge: "Design",
            icon: Palette,
            specs: leftover,
          });
        }
      } else if (cat.id === "architecture") {
        const archOverview = catSpecs.filter((s) => s.type === "tech/architecture");
        const containers = catSpecs.filter((s) => s.type === "tech/container");
        const components = catSpecs.filter((s) => s.type === "tech/component");
        const contracts = catSpecs.filter((s) => s.type === "tech/contract");

        archOverview.forEach((arch) => {
          subgroups.push({
            id: `subgroup-${arch.id}`,
            name: formatDisplayTitle(arch.title, arch.id),
            badge: "Architecture",
            icon: Network,
            parentSpec: arch,
            specs: [],
          });
        });

        if (containers.length > 0) {
          subgroups.push({
            id: "subgroup-c4-containers",
            name: "System Containers",
            badge: "Containers",
            icon: Box,
            specs: containers,
          });
        }

        if (components.length > 0) {
          subgroups.push({
            id: "arch-components",
            name: "Internal Components",
            badge: "Components",
            icon: Layers,
            specs: components,
          });
        }

        if (contracts.length > 0) {
          subgroups.push({
            id: "subgroup-c4-contracts",
            name: "Interface Contracts",
            badge: "Contracts",
            icon: FileCode,
            specs: contracts,
          });
        }
      } else if (cat.id === "governance") {
        const systemSpecs = catSpecs.filter((s) => s.type === "product/system");
        const policySpecs = catSpecs.filter((s) => s.type === "product/policy");

        systemSpecs.forEach((sys) => {
          subgroups.push({
            id: `subgroup-${sys.id}`,
            name: formatDisplayTitle(sys.title, sys.id),
            badge: "System Overview",
            icon: ShieldCheck,
            parentSpec: sys,
            specs: [],
          });
        });

        if (policySpecs.length > 0) {
          subgroups.push({
            id: "subgroup-policies",
            name: "Governance Policies",
            badge: "Policies",
            icon: ShieldCheck,
            specs: policySpecs,
          });
        }
      } else if (cat.id === "personas_research") {
        const personas = catSpecs.filter((s) => s.type === "product/persona");
        const explorations = catSpecs.filter((s) => s.type === "product/exploration");

        if (personas.length > 0) {
          subgroups.push({
            id: "subgroup-personas",
            name: "Personas",
            badge: "Personas",
            icon: Users,
            specs: personas,
          });
        }

        if (explorations.length > 0) {
          subgroups.push({
            id: "subgroup-explorations",
            name: "Explorations & Research",
            badge: "Explorations",
            icon: Lightbulb,
            specs: explorations,
          });
        }
      } else if (cat.id === "deltas") {
        const isArch = (s: SpecItem) =>
          Boolean(
            s.path && s.path.replace(/\\/g, "/").includes("deltas/archive/")
          );
        const archived = catSpecs.filter((s) => isArch(s));
        const applied = catSpecs.filter(
          (s) => !isArch(s) && s.frontmatter?.status?.toLowerCase() === "applied"
        );
        const pending = catSpecs.filter(
          (s) => !isArch(s) && s.frontmatter?.status?.toLowerCase() !== "applied"
        );

        if (applied.length > 0) {
          subgroups.push({
            id: "subgroup-deltas-applied",
            name: `Applied Changes (${applied.length})`,
            badge: "Applied",
            icon: GitBranch,
            specs: applied,
          });
        }

        if (pending.length > 0) {
          subgroups.push({
            id: "subgroup-deltas-pending",
            name: `Open Change Proposals (${pending.length})`,
            badge: "Open",
            icon: GitBranch,
            specs: pending,
          });
        }

        if (archived.length > 0) {
          subgroups.push({
            id: "subgroup-deltas-archived",
            name: `Archived Changes (${archived.length})`,
            badge: "Archived",
            icon: GitBranch,
            specs: archived,
          });
        }
      }

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

  const [expandedNodes, setExpandedNodes] = React.useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    SPEC_CATEGORIES.forEach((c) => {
      initial[c.id] = true;
    });
    return initial;
  });

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
              ref={searchInputRef}
              id="catalogSearchInput"
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search documents..."
              className="w-full bg-muted/40 border border-border text-xs rounded-xl pl-9 pr-14 py-2 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <kbd
              id="catalogSearchKbd"
              className="absolute right-3 top-1/2 -translate-y-1/2 font-mono text-[10px] text-muted-foreground bg-card border border-border/80 px-1.5 py-0.5 rounded"
            >
              /
            </kbd>
          </div>

          {/* Category Filter */}
          <div className="tree-select-wrapper">
            <select
              id="catalogTypeFilter"
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              className="bg-muted/40 border border-border text-xs rounded-xl px-3 py-2 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="all">All Categories ({data.specs.length})</option>
              {specTypes.map((t) => (
                <option key={t} value={t}>
                  {formatSpecType(t)}
                </option>
              ))}
            </select>
          </div>

          {/* Match pill count */}
          <span
            id="catalogSearchMatchPill"
            className="text-xs text-muted-foreground px-2.5 py-1 rounded-full bg-muted/60"
          >
            {filteredSpecs.length} {filteredSpecs.length === 1 ? "document" : "documents"}
          </span>
        </div>

        {/* View Mode & Hierarchy Controls */}
        <div className="flex items-center gap-2 text-xs">
          <div className="flex items-center bg-muted/60 p-0.5 rounded-xl border border-border/60">
            <button
              onClick={() => setViewMode("hierarchy")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                viewMode === "hierarchy"
                  ? "bg-card text-foreground font-semibold md-elevation-1"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="Tree View"
            >
              <ListTree className="h-3.5 w-3.5 text-primary" />
              <span>Tree View</span>
            </button>
            <button
              onClick={() => setViewMode("flat")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                viewMode === "flat"
                  ? "bg-card text-foreground font-semibold md-elevation-1"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="List View"
            >
              <TableIcon className="h-3.5 w-3.5" />
              <span>List View</span>
            </button>
          </div>

          <span className="text-border">|</span>

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

      {/* Documents TreeGrid Table */}
      <Card className="p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table id="specsTreeGridTable" className="w-full text-left text-xs">
            <thead className="bg-muted/40 border-b border-border text-muted-foreground font-semibold uppercase tracking-wider text-[10px]">
              <tr>
                <th className="py-3 px-4 w-5/12">Document</th>
                <th className="py-3 px-4 w-2/12">Category</th>
                <th className="py-3 px-4 w-3/12">Section</th>
                <th className="py-3 px-4 w-2/12">Status</th>
              </tr>
            </thead>
            <tbody id="specsTreeGridBody" className="divide-y divide-border/60">
              {filteredSpecs.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-12 text-center text-muted-foreground">
                    No documents match "{searchQuery}".
                  </td>
                </tr>
              ) : viewMode === "hierarchy" ? (
                hierarchicalBranches.map((branch) => {
                  if (branch.total === 0) return null;
                  const isCatExpanded = Boolean(expandedNodes[branch.id]);

                  return (
                    <React.Fragment key={branch.id}>
                      {/* LEVEL 1: Category Header Row */}
                      <tr
                        onClick={() => toggleTreeGridRow(branch.id)}
                        className="bg-muted/30 hover:bg-muted/60 cursor-pointer select-none transition-colors border-t-2 border-border/80"
                      >
                        <td colSpan={3} className="py-2.5 px-4">
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
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="font-bold text-xs text-foreground tracking-tight">
                                {branch.name}
                              </span>
                              <span className="text-[11px] text-muted-foreground hidden sm:inline truncate">
                                • {branch.description}
                              </span>
                            </div>
                          </div>
                        </td>
                        <td className="py-2.5 px-4">
                          <div className="flex items-center gap-2">
                            <span className="text-[11px] px-2 py-0.5 rounded-full bg-primary/10 text-primary font-semibold">
                              {branch.total} {branch.total === 1 ? "item" : "items"}
                            </span>
                            <span className="text-emerald-500 text-[11px] font-semibold">
                              {branch.validCount}/{branch.total} Valid
                            </span>
                          </div>
                        </td>
                      </tr>

                      {/* LEVEL 2 & 3: Subgroups and Child Documents */}
                      {isCatExpanded &&
                        branch.subgroups.map((subgroup) => {
                          const isSgExpanded = expandedNodes[subgroup.id] !== false;
                          const hasChildren = subgroup.specs.length > 0;
                          const SgIcon = subgroup.icon || Folder;

                          return (
                            <React.Fragment key={subgroup.id}>
                              {/* LEVEL 2: SubGroup / Parent Entity Row */}
                              <tr className="hover:bg-muted/30 transition-colors border-t border-border/40 group">
                                <td className="py-2 px-4 pl-9">
                                  <div className="flex items-center gap-2">
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
                                      <span className="w-4" />
                                    )}
                                    <SgIcon className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                    {subgroup.parentSpec ? (
                                      <div
                                        onClick={() => setInspectedSpec(subgroup.parentSpec!)}
                                        className="flex items-center gap-2 cursor-pointer hover:underline min-w-0"
                                      >
                                        <span className="text-foreground font-semibold truncate max-w-md group-hover:text-primary transition-colors">
                                          {formatDisplayTitle(subgroup.parentSpec.title, subgroup.parentSpec.id)}
                                        </span>
                                      </div>
                                    ) : (
                                      <span className="font-semibold text-foreground text-xs">
                                        {subgroup.name}
                                      </span>
                                    )}
                                    {hasChildren && (
                                      <span className="text-[11px] text-muted-foreground">
                                        ({subgroup.specs.length})
                                      </span>
                                    )}
                                  </div>
                                </td>
                                <td className="py-2 px-4">
                                  {subgroup.parentSpec ? (
                                    <Badge variant="outline" className="text-[10px]">
                                      {formatSpecType(subgroup.parentSpec.type)}
                                    </Badge>
                                  ) : (
                                    <span className="text-[11px] text-muted-foreground">
                                      {subgroup.badge || "Group"}
                                    </span>
                                  )}
                                </td>
                                <td className="py-2 px-4 text-muted-foreground text-[11px] truncate max-w-xs">
                                  {subgroup.parentSpec
                                    ? branch.name
                                    : `${subgroup.specs.length} ${subgroup.specs.length === 1 ? "document" : "documents"}`}
                                </td>
                                <td className="py-2 px-4">
                                  {subgroup.parentSpec ? (
                                    subgroup.parentSpec.is_valid ? (
                                      <span className="inline-flex items-center gap-1 text-emerald-500 font-semibold text-[11px]">
                                        <CheckCircle2 className="h-3 w-3" />
                                        Valid
                                      </span>
                                    ) : (
                                      <span className="inline-flex items-center gap-1 text-red-500 font-semibold text-[11px]">
                                        <AlertCircle className="h-3 w-3" />
                                        Needs Attention
                                      </span>
                                    )
                                  ) : (
                                    <span className="text-muted-foreground text-[11px]">
                                      {subgroup.specs.filter((s) => s.is_valid).length}/{subgroup.specs.length} Valid
                                    </span>
                                  )}
                                </td>
                              </tr>

                              {/* LEVEL 3: Leaf Documents under Subgroup */}
                              {isSgExpanded &&
                                hasChildren &&
                                subgroup.specs.map((spec) => (
                                  <tr
                                    key={spec.id}
                                    onClick={() => setInspectedSpec(spec)}
                                    className="hover:bg-muted/40 cursor-pointer transition-colors group"
                                  >
                                    <td className="py-2 px-4 pl-16">
                                      <div className="flex items-center gap-2 min-w-0">
                                        <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0 group-hover:text-primary transition-colors" />
                                        <span className="text-foreground font-medium truncate max-w-md group-hover:text-primary transition-colors">
                                          {formatDisplayTitle(spec.title, spec.id)}
                                        </span>
                                      </div>
                                    </td>
                                    <td className="py-2 px-4">
                                      <Badge variant="outline" className="text-[10px]">
                                        {formatSpecType(spec.type)}
                                      </Badge>
                                    </td>
                                    <td className="py-2 px-4 text-muted-foreground text-[11px] truncate max-w-xs">
                                      {subgroup.name}
                                    </td>
                                    <td className="py-2 px-4">
                                      {spec.is_valid ? (
                                        <span className="inline-flex items-center gap-1 text-emerald-500 font-semibold text-[11px]">
                                          <CheckCircle2 className="h-3 w-3" />
                                          Valid
                                        </span>
                                      ) : (
                                        <span className="inline-flex items-center gap-1 text-red-500 font-semibold text-[11px]">
                                          <AlertCircle className="h-3 w-3" />
                                          Needs Attention
                                        </span>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                            </React.Fragment>
                          );
                        })}
                    </React.Fragment>
                  );
                })
              ) : (
                // List View
                filteredSpecs.map((spec) => (
                  <tr
                    key={spec.id}
                    onClick={() => setInspectedSpec(spec)}
                    className="hover:bg-muted/30 cursor-pointer transition-colors group"
                  >
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-2 min-w-0">
                        <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0 group-hover:text-primary transition-colors" />
                        <span className="text-foreground font-medium truncate max-w-md group-hover:text-primary transition-colors">
                          {formatDisplayTitle(spec.title, spec.id)}
                        </span>
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <Badge variant="outline" className="text-[10px]">
                        {formatSpecType(spec.type)}
                      </Badge>
                    </td>
                    <td className="py-3 px-4 text-muted-foreground text-[11px] truncate max-w-sm">
                      {resolveSpecSection(spec)}
                    </td>
                    <td className="py-3 px-4">
                      {spec.is_valid ? (
                        <span className="inline-flex items-center gap-1 text-emerald-500 font-semibold text-[11px]">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          Valid
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-red-500 font-semibold text-[11px]">
                          <AlertCircle className="h-3.5 w-3.5" />
                          Needs Attention
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

      {/* Document Inspector Sheet */}
      <Sheet
        open={Boolean(inspectedSpec)}
        onOpenChange={(open) => !open && setInspectedSpec(null)}
        title={inspectedSpec ? formatDisplayTitle(inspectedSpec.title, inspectedSpec.id) : "Document"}
        subtitle={inspectedSpec ? formatSpecType(inspectedSpec.type) : undefined}
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
