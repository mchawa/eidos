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
  Brain,
  ListTree,
  Table as TableIcon,
  BookOpen,
  Box,
  Layers,
  FileCode,
  Smartphone,
  Users,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { SpecReader } from "@/components/ui/SpecReader";
import { requestSpecNavigation } from "@/lib/specNav";
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
  /** Spec ID to open in the reader (set by in-portal xref / mode='reader' navigation). */
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
    id: "governance",
    name: "Overview & Policies",
    description: "System overview and organization-wide governance policies",
    icon: ShieldCheck,
    color: "text-emerald-500",
    badgeBg: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
    types: ["product/system", "product/policy"],
  },
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
  {
    id: "memory",
    name: "Team Memory",
    description: "Shared operational notes, lessons, and project context",
    icon: Brain,
    color: "text-teal-500",
    badgeBg: "bg-teal-500/10 text-teal-500 border-teal-500/20",
    types: ["agent/memory"],
  },
];

interface OrderedSpecEntry {
  spec: SpecItem;
  branchId: string;
  branchName: string;
  subgroupId: string;
  subgroupName: string;
  isParent: boolean;
}

export function CatalogView({ data, focusSpecId, focusNonce, onFocusHandled }: CatalogViewProps) {
  const labelIndex = useEntityLabels();
  const [searchQuery, setSearchQuery] = React.useState("");
  const [selectedType, setSelectedType] = React.useState("all");
  const [viewMode, setViewMode] = React.useState<"reader" | "hierarchy" | "flat">("reader");
  const [selectedSpecId, setSelectedSpecId] = React.useState<string>(() => {
    if (focusSpecId) return focusSpecId;
    const sys = data.specs.find((s) => s.type === "product/system");
    return sys?.id || data.specs[0]?.id || "";
  });
  const searchInputRef = React.useRef<HTMLInputElement>(null);
  const readerPaneRef = React.useRef<HTMLDivElement>(null);

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

        if (pending.length > 0) {
          subgroups.push({
            id: "subgroup-deltas-pending",
            name: `Open Change Proposals`,
            badge: "Open",
            icon: GitBranch,
            specs: pending,
          });
        }

        if (applied.length > 0) {
          subgroups.push({
            id: "subgroup-deltas-applied",
            name: `Applied Changes`,
            badge: "Applied",
            icon: GitBranch,
            specs: applied,
          });
        }

        if (archived.length > 0) {
          subgroups.push({
            id: "subgroup-deltas-archived",
            name: `Archived Changes`,
            badge: "Archived",
            icon: GitBranch,
            specs: archived,
          });
        }
      } else if (cat.id === "memory") {
        const isArchMem = (s: SpecItem) =>
          Boolean(
            (s.path && s.path.replace(/\\/g, "/").includes("memory/archive/")) ||
              s.frontmatter?.status?.toLowerCase() === "archived" ||
              s.frontmatter?.status?.toLowerCase() === "promoted"
          );
        const archivedMems = catSpecs.filter((s) => isArchMem(s));
        const activeMems = catSpecs.filter((s) => !isArchMem(s));
        if (activeMems.length > 0) {
          subgroups.push({
            id: "subgroup-memory-active",
            name: `Active Team Notes`,
            badge: "Active",
            icon: Brain,
            specs: activeMems,
          });
        }
        if (archivedMems.length > 0) {
          subgroups.push({
            id: "subgroup-memory-archived",
            name: `Archived & Promoted Notes`,
            badge: "Archived",
            icon: Brain,
            specs: archivedMems,
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

  // Flatten ordered entries for breadcrumbs and Prev/Next book navigation
  const orderedSpecs = React.useMemo<OrderedSpecEntry[]>(() => {
    const entries: OrderedSpecEntry[] = [];
    const seen = new Set<string>();
    hierarchicalBranches.forEach((branch) => {
      branch.subgroups.forEach((sg) => {
        if (sg.parentSpec && !seen.has(sg.parentSpec.id)) {
          seen.add(sg.parentSpec.id);
          entries.push({
            spec: sg.parentSpec,
            branchId: branch.id,
            branchName: branch.name,
            subgroupId: sg.id,
            subgroupName: sg.name,
            isParent: true,
          });
        }
        sg.specs.forEach((sp) => {
          if (!seen.has(sp.id)) {
            seen.add(sp.id);
            entries.push({
              spec: sp,
              branchId: branch.id,
              branchName: branch.name,
              subgroupId: sg.id,
              subgroupName: sg.name,
              isParent: false,
            });
          }
        });
      });
    });
    return entries;
  }, [hierarchicalBranches]);

  const [expandedNodes, setExpandedNodes] = React.useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    SPEC_CATEGORIES.forEach((c) => {
      // Keep heavy historical branches collapsed by default in the left docs tree
      initial[c.id] = c.id !== "deltas";
    });
    return initial;
  });

  // Open the spec targeted by an xref:/<<ID>>/link:*.adoc[] or mode='reader' click
  React.useEffect(() => {
    if (!focusSpecId) return;
    const target = data.specs.find((s) => s.id === focusSpecId);
    if (target) {
      setSelectedSpecId(target.id);
      setViewMode("reader");
      const entry = orderedSpecs.find((e) => e.spec.id === target.id);
      if (entry) {
        setExpandedNodes((prev) => ({
          ...prev,
          [entry.branchId]: true,
          [entry.subgroupId]: true,
        }));
      } else {
        if (searchQuery) setSearchQuery("");
        if (selectedType !== "ALL" && target.type !== selectedType) {
          setSelectedType("ALL");
        }
      }
    }
    onFocusHandled?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSpecId, focusNonce]);

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

  const activeSpec = React.useMemo(() => {
    const exact = data.specs.find((s) => s.id === selectedSpecId);
    if (exact) return exact;
    return orderedSpecs[0]?.spec || data.specs[0] || null;
  }, [data.specs, selectedSpecId, orderedSpecs]);

  const activeIndex = React.useMemo(
    () => (activeSpec ? orderedSpecs.findIndex((e) => e.spec.id === activeSpec.id) : -1),
    [orderedSpecs, activeSpec]
  );

  const activeEntry = activeIndex >= 0 ? orderedSpecs[activeIndex] : null;
  const prevEntry = activeIndex > 0 ? orderedSpecs[activeIndex - 1] : null;
  const nextEntry = activeIndex >= 0 && activeIndex < orderedSpecs.length - 1 ? orderedSpecs[activeIndex + 1] : null;

  const selectDocForReading = React.useCallback(
    (specRef: string) => {
      const cleaned = specRef.trim();
      if (!cleaned) return;
      const lower = cleaned.toLowerCase();
      const normPath = cleaned.replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
      const matchedSpec =
        data.specs.find((s) => s.id === cleaned || s.id.toLowerCase() === lower) ||
        data.specs.find((s) => {
          const p = (s.path || "").replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
          return p === normPath || p.endsWith(`/${normPath}`) || normPath.endsWith(`/${p}`);
        });

      if (!matchedSpec) {
        requestSpecNavigation(cleaned, { mode: "reader" });
        return;
      }

      setSelectedSpecId(matchedSpec.id);
      setViewMode("reader");
      const entry = orderedSpecs.find((e) => e.spec.id === matchedSpec.id);
      if (entry) {
        setExpandedNodes((prev) => ({
          ...prev,
          [entry.branchId]: true,
          [entry.subgroupId]: true,
        }));
      } else {
        if (searchQuery) setSearchQuery("");
        if (selectedType !== "ALL" && matchedSpec.type !== selectedType) {
          setSelectedType("ALL");
        }
      }
      if (readerPaneRef.current) {
        readerPaneRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    },
    [data.specs, orderedSpecs, searchQuery, selectedType]
  );

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

  const activeBreadcrumb = React.useMemo(() => {
    if (!activeEntry) return undefined;
    const crumbs = [activeEntry.branchName];
    if (!activeEntry.isParent && activeEntry.subgroupName && activeEntry.subgroupName !== activeEntry.branchName) {
      crumbs.push(activeEntry.subgroupName);
    }
    return crumbs;
  }, [activeEntry]);

  return (
    <div className="space-y-4 animate-in fade-in duration-200">
      {/* Compact Borderless Top Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-border/70">
        <div className="flex flex-wrap items-center gap-2.5 flex-1">
          {/* Search Box */}
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search className="h-3.5 w-3.5 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" aria-hidden="true" />
            <input
              ref={searchInputRef}
              id="catalogSearchInput"
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search documents..."
              className="w-full bg-muted/40 border border-border/80 text-xs rounded-lg pl-8.5 pr-12 py-1.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            {searchQuery ? (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                aria-label="Clear search"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground cursor-pointer"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ) : (
              <kbd
                id="catalogSearchKbd"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 font-mono text-[10px] text-muted-foreground bg-card border border-border/80 px-1.5 py-0.5 rounded"
              >
                /
              </kbd>
            )}
          </div>

          {/* Category Filter */}
          <div className="tree-select-wrapper">
            <label htmlFor="catalogTypeFilter" className="sr-only">
              Category
            </label>
            <select
              id="catalogTypeFilter"
              aria-label="Filter documents by category"
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              className="bg-muted/40 border border-border/80 text-xs rounded-lg px-2.5 py-1.5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
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
            className="text-xs text-muted-foreground px-2 py-0.5 rounded-full bg-muted/50"
          >
            {filteredSpecs.length} {filteredSpecs.length === 1 ? "document" : "documents"}
          </span>
        </div>

        {/* View Mode & Hierarchy Controls */}
        <div className="flex items-center gap-2 text-xs">
          <div className="flex items-center bg-muted/50 p-0.5 rounded-lg border border-border/60">
            <button
              type="button"
              onClick={() => setViewMode("reader")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer",
                viewMode === "reader"
                  ? "bg-card text-foreground font-semibold shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="Docs Reader"
            >
              <BookOpen className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
              <span>Docs Reader</span>
            </button>
            <button
              type="button"
              onClick={() => setViewMode("hierarchy")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer",
                viewMode === "hierarchy"
                  ? "bg-card text-foreground font-semibold shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="Tree Table"
            >
              <ListTree className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Tree</span>
            </button>
            <button
              type="button"
              onClick={() => setViewMode("flat")}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer",
                viewMode === "flat"
                  ? "bg-card text-foreground font-semibold shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="List View"
            >
              <TableIcon className="h-3.5 w-3.5" aria-hidden="true" />
              <span>List</span>
            </button>
          </div>

          {viewMode !== "flat" && (
            <>
              <span className="text-border">|</span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={expandAllTreeGrid}
                  className="px-2 py-1 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer text-xs"
                >
                  Expand All
                </button>
                <button
                  type="button"
                  onClick={collapseAllTreeGrid}
                  className="px-2 py-1 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer text-xs"
                >
                  Collapse All
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {/* MODE 1 (DEFAULT): 3-Pane Docs-Style Split Reader */}
      {viewMode === "reader" ? (
        <div
          id="catalogDocsReader"
          className="flex flex-col lg:flex-row items-start gap-6 min-h-[70vh]"
        >
          {/* Left Pane: Collapsible Documentation Navigation Tree */}
          <aside
            aria-label="Specification directory"
            className="w-full lg:w-64 xl:w-68 shrink-0 lg:sticky lg:top-16 lg:max-h-[calc(100vh-5.5rem)] lg:overflow-y-auto lg:pr-3.5 lg:border-r lg:border-border/60 space-y-2 pb-6"
          >
            {filteredSpecs.length === 0 ? (
              <p className="py-6 text-xs text-muted-foreground text-center">
                No documents match "{searchQuery}".
              </p>
            ) : (
              hierarchicalBranches.map((branch) => {
                if (branch.total === 0) return null;
                const isCatExpanded = Boolean(expandedNodes[branch.id]);
                const BranchIcon = branch.icon;

                return (
                  <div key={branch.id} className="space-y-0.5">
                    <button
                      type="button"
                      aria-expanded={isCatExpanded}
                      onClick={() => toggleTreeGridRow(branch.id)}
                      className="w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg text-xs font-bold text-foreground hover:bg-muted/60 transition-colors cursor-pointer"
                    >
                      <span className="flex items-center gap-2 min-w-0">
                        {isCatExpanded ? (
                          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
                        )}
                        <BranchIcon className={cn("h-3.5 w-3.5 shrink-0", branch.color)} aria-hidden="true" />
                        <span className="truncate">{branch.name}</span>
                      </span>
                      <span className="text-[10px] font-medium text-muted-foreground shrink-0">
                        {branch.total}
                      </span>
                    </button>

                    {isCatExpanded && (
                      <div className="pl-3 space-y-0.5 border-l border-border/50 ml-3">
                        {branch.subgroups.map((subgroup) => {
                          const hasChildren = subgroup.specs.length > 0;
                          const isSgExpanded = expandedNodes[subgroup.id] !== false;
                          const isParentActive = subgroup.parentSpec && activeSpec?.id === subgroup.parentSpec.id;

                          return (
                            <div key={subgroup.id} className="space-y-0.5">
                              <div className="flex items-center gap-1">
                                {hasChildren ? (
                                  <button
                                    type="button"
                                    aria-label={`Toggle ${subgroup.name}`}
                                    aria-expanded={isSgExpanded}
                                    onClick={() => toggleTreeGridRow(subgroup.id)}
                                    className="p-1 rounded text-muted-foreground hover:text-foreground cursor-pointer shrink-0"
                                  >
                                    {isSgExpanded ? (
                                      <ChevronDown className="h-3 w-3" aria-hidden="true" />
                                    ) : (
                                      <ChevronRight className="h-3 w-3" aria-hidden="true" />
                                    )}
                                  </button>
                                ) : (
                                  <span className="w-5 shrink-0" />
                                )}

                                {subgroup.parentSpec ? (
                                  <button
                                    type="button"
                                    onClick={() => selectDocForReading(subgroup.parentSpec!.id)}
                                    aria-current={isParentActive ? "page" : undefined}
                                    className={cn(
                                      "flex-1 text-left px-2 py-1 rounded-md text-xs truncate transition-colors cursor-pointer",
                                      isParentActive
                                        ? "bg-accent text-foreground font-semibold"
                                        : "text-foreground/90 font-medium hover:bg-muted/60 hover:text-foreground"
                                    )}
                                    title={formatDisplayTitle(subgroup.parentSpec.title, subgroup.parentSpec.id)}
                                  >
                                    {formatDisplayTitle(subgroup.parentSpec.title, subgroup.parentSpec.id)}
                                  </button>
                                ) : (
                                  <button
                                    type="button"
                                    onClick={() => toggleTreeGridRow(subgroup.id)}
                                    className="flex-1 text-left px-1.5 py-1 rounded-md text-[11px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground truncate cursor-pointer"
                                  >
                                    {subgroup.name} ({subgroup.specs.length})
                                  </button>
                                )}
                              </div>

                              {isSgExpanded && hasChildren && (
                                <div className="pl-5 space-y-0.5">
                                  {subgroup.specs.map((spec) => {
                                    const isCurrent = activeSpec?.id === spec.id;
                                    const title = formatDisplayTitle(spec.title, spec.id);
                                    return (
                                      <button
                                        key={spec.id}
                                        type="button"
                                        onClick={() => selectDocForReading(spec.id)}
                                        aria-current={isCurrent ? "page" : undefined}
                                        className={cn(
                                          "w-full flex items-center gap-2 px-2 py-1 rounded-md text-xs text-left transition-colors cursor-pointer",
                                          isCurrent
                                            ? "bg-accent text-foreground font-semibold"
                                            : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                                        )}
                                        title={spec.is_valid === false ? `${title} (Needs Attention)` : title}
                                      >
                                        <span
                                          className={cn(
                                            "h-1.5 w-1.5 rounded-full shrink-0",
                                            isCurrent
                                              ? "bg-foreground"
                                              : spec.is_valid === false
                                              ? "bg-red-500"
                                              : "bg-border"
                                          )}
                                          aria-hidden="true"
                                        />
                                        <span className="truncate">{title}</span>
                                        {spec.is_valid === false && (
                                          <span className="sr-only">Needs Attention</span>
                                        )}
                                      </button>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </aside>

          {/* Center & Right Panes: Inline Borderless SpecReader */}
          <div ref={readerPaneRef} className="flex-1 min-w-0 pt-1 scroll-mt-20">
            {activeSpec ? (
              <SpecReader
                spec={activeSpec}
                breadcrumb={activeBreadcrumb}
                prevSpec={
                  prevEntry
                    ? {
                        id: prevEntry.spec.id,
                        title: formatDisplayTitle(prevEntry.spec.title, prevEntry.spec.id),
                        type: prevEntry.spec.type,
                      }
                    : null
                }
                nextSpec={
                  nextEntry
                    ? {
                        id: nextEntry.spec.id,
                        title: formatDisplayTitle(nextEntry.spec.title, nextEntry.spec.id),
                        type: nextEntry.spec.type,
                      }
                    : null
                }
                onNavigateDoc={selectDocForReading}
              />
            ) : (
              <div className="py-16 text-center text-sm text-muted-foreground">
                Select a specification from the directory to start reading.
              </div>
            )}
          </div>
        </div>
      ) : (
        /* MODE 2 & 3: Tabular Tree / List Overview (Clean Borderless Table) */
        <div
          tabIndex={0}
          role="region"
          aria-label="Living specifications directory table"
          className="table-wrapper border-y border-border/70 max-h-[70vh]"
        >
          <table id="specsTreeGridTable" className="w-full text-left text-xs">
            <thead className="bg-background/95 backdrop-blur-xs border-b border-border text-muted-foreground font-semibold uppercase tracking-wider text-[10px]">
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
                        className="bg-muted/25 hover:bg-muted/50 cursor-pointer select-none transition-colors border-t border-border/80"
                      >
                        <td colSpan={3} className="py-2.5 px-4">
                          <button
                            type="button"
                            aria-expanded={isCatExpanded}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleTreeGridRow(branch.id);
                            }}
                            className="flex items-center gap-2.5 w-full text-left cursor-pointer"
                          >
                            <span className="p-1 rounded-md text-muted-foreground hover:text-foreground transition-colors">
                              {isCatExpanded ? (
                                <ChevronDown className="h-4 w-4" aria-hidden="true" />
                              ) : (
                                <ChevronRight className="h-4 w-4" aria-hidden="true" />
                              )}
                            </span>
                            <div className={cn("p-1.5 rounded-lg shrink-0", branch.badgeBg)}>
                              {isCatExpanded ? (
                                <FolderOpen className="h-4 w-4" aria-hidden="true" />
                              ) : (
                                <Folder className="h-4 w-4" aria-hidden="true" />
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
                          </button>
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
                              <tr
                                onClick={!subgroup.parentSpec && hasChildren ? () => toggleTreeGridRow(subgroup.id) : undefined}
                                className={cn(
                                  "hover:bg-muted/30 transition-colors border-t border-border/40 group",
                                  !subgroup.parentSpec && hasChildren && "cursor-pointer"
                                )}
                              >
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
                                      <button
                                        type="button"
                                        onClick={() => selectDocForReading(subgroup.parentSpec!.id)}
                                        className="flex items-center gap-2 cursor-pointer hover:underline min-w-0 text-left"
                                      >
                                        <span className="text-foreground font-semibold truncate max-w-md group-hover:text-primary transition-colors">
                                          {formatDisplayTitle(subgroup.parentSpec.title, subgroup.parentSpec.id)}
                                        </span>
                                      </button>
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
                                    onClick={() => selectDocForReading(spec.id)}
                                    className="hover:bg-muted/40 cursor-pointer transition-colors group"
                                  >
                                    <td className="py-2 px-4 pl-16">
                                      <button
                                        type="button"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          selectDocForReading(spec.id);
                                        }}
                                        className="flex items-center gap-2 min-w-0 text-left cursor-pointer"
                                      >
                                        <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0 group-hover:text-primary transition-colors" aria-hidden="true" />
                                        <span className="text-foreground font-medium truncate max-w-md group-hover:text-primary transition-colors">
                                          {formatDisplayTitle(spec.title, spec.id)}
                                        </span>
                                      </button>
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
                    onClick={() => selectDocForReading(spec.id)}
                    className="hover:bg-muted/30 cursor-pointer transition-colors group"
                  >
                    <td className="py-2.5 px-4">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          selectDocForReading(spec.id);
                        }}
                        className="flex items-center gap-2 min-w-0 text-left cursor-pointer"
                      >
                        <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0 group-hover:text-primary transition-colors" aria-hidden="true" />
                        <span className="text-foreground font-medium truncate max-w-md group-hover:text-primary transition-colors">
                          {formatDisplayTitle(spec.title, spec.id)}
                        </span>
                      </button>
                    </td>
                    <td className="py-2.5 px-4">
                      <Badge variant="outline" className="text-[10px]">
                        {formatSpecType(spec.type)}
                      </Badge>
                    </td>
                    <td className="py-2.5 px-4 text-muted-foreground text-[11px] truncate max-w-sm">
                      {resolveSpecSection(spec)}
                    </td>
                    <td className="py-2.5 px-4">
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
      )}
    </div>
  );
}
