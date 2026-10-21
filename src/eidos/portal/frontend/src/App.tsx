import * as React from "react";
import { NavigationRail, type TabKey } from "@/components/layout/NavigationRail";
import { HomeView } from "@/components/views/HomeView";
import { ArchitectureView } from "@/components/views/ArchitectureView";
import { DeltasView } from "@/components/views/DeltasView";
import { CatalogView } from "@/components/views/CatalogView";
import { BusinessView } from "@/components/views/BusinessView";
import { DesignView } from "@/components/views/DesignView";
import { ExplorationsView } from "@/components/views/ExplorationsView";
import { CommandDialog, type CommandItem } from "@/components/ui/command";
import type { PortalData } from "@/types";
import { SPEC_NAV_EVENT, resolveSpecTarget, type SpecNavDetail } from "@/lib/specNav";

const VALID_TABS = new Set<TabKey>([
  "home",
  "business",
  "design",
  "architecture",
  "deltas",
  "explorations",
  "catalog",
]);

function hydratePortalData(raw: PortalData): PortalData {
  const bodyById = new Map<string, string>();
  (raw.specs || []).forEach((s) => {
    if (s.id && typeof s.body === "string") {
      bodyById.set(s.id, s.body);
    }
  });

  const withDesc = <T extends { id: string; description?: string }>(items: T[] = []): T[] =>
    items.map((item) =>
      item.description === undefined && bodyById.has(item.id)
        ? { ...item, description: bodyById.get(item.id) }
        : item
    );

  const withBody = <T extends { id: string; body?: string }>(items: T[] = []): T[] =>
    items.map((item) =>
      item.body === undefined && bodyById.has(item.id)
        ? { ...item, body: bodyById.get(item.id) }
        : item
    );

  const hydratedComponents = withDesc(raw.components || []).map((c) => ({
    ...c,
    internal_components: (c.internal_components || []).map((ic) =>
      ic.description === undefined && bodyById.has(ic.id)
        ? { ...ic, description: bodyById.get(ic.id) }
        : ic
    ),
  }));

  const hydratedDeltas: PortalData["deltas"] = {
    proposed: [],
    in_review: [],
    approved: [],
    applied: [],
  };
  for (const [k, list] of Object.entries(raw.deltas || {})) {
    if (Array.isArray(list)) {
      hydratedDeltas[k] = withBody(list);
    }
  }

  return {
    ...raw,
    components: hydratedComponents,
    contracts: withDesc(raw.contracts || []),
    domains: withBody(raw.domains || []),
    capabilities: withBody(raw.capabilities || []),
    personas: withBody(raw.personas || []),
    policies: withBody(raw.policies || []),
    explorations: withBody(raw.explorations || []),
    clients: withBody(raw.clients || []),
    flows: withBody(raw.flows || []),
    ui_components: withBody(raw.ui_components || []),
    deltas: hydratedDeltas,
  };
}

function parseRouteHash(hash: string): { tab: TabKey; entityId?: string } | null {
  const cleaned = (hash || "").replace(/^#\/?/, "").trim();
  if (!cleaned) return null;
  const parts = cleaned.split("/").filter(Boolean);
  const first = parts[0]?.toLowerCase() as TabKey;
  if (!VALID_TABS.has(first)) return null;
  const entityId = parts[1] ? decodeURIComponent(parts[1]) : undefined;
  return { tab: first, entityId };
}

export function App() {
  const isStaticBundle = Boolean(window.__EIDOS_DATA__);
  const [data, setData] = React.useState<PortalData | null>(() => {
    return window.__EIDOS_DATA__ ? hydratePortalData(window.__EIDOS_DATA__) : null;
  });

  const [activeTab, setActiveTabState] = React.useState<TabKey>(() => {
    return parseRouteHash(window.location.hash)?.tab || "home";
  });
  // In-portal xref navigation: the reader publishes raw targets, the shell resolves them to a catalog spec.
  const [focusSpec, setFocusSpec] = React.useState<{ id: string; nonce: number } | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState<boolean>(() => {
    return localStorage.getItem("eidos_sidebar_collapsed") === "true";
  });
  const [darkMode, setDarkMode] = React.useState<boolean>(() => {
    const saved = localStorage.getItem("eidos_theme");
    return saved ? saved === "dark" : true;
  });
  const [commandOpen, setCommandOpen] = React.useState<boolean>(false);
  const [selectedDelta, setSelectedDelta] = React.useState<{ id: string; nonce: number } | null>(null);
  const [selectedComponent, setSelectedComponent] = React.useState<{ id: string; nonce: number } | null>(null);
  const [selectedExploration, setSelectedExploration] = React.useState<{ id: string; nonce: number } | null>(null);
  const [selectedBusiness, setSelectedBusiness] = React.useState<{ id: string; nonce: number } | null>(null);
  const [selectedDesign, setSelectedDesign] = React.useState<{ id: string; nonce: number } | null>(null);
  const [liveSyncActive, setLiveSyncActive] = React.useState<boolean>(!isStaticBundle);

  const setActiveTab = React.useCallback((tab: TabKey, entityId?: string) => {
    setActiveTabState(tab);
    const nextHash = entityId ? `#/${tab}/${encodeURIComponent(entityId)}` : `#/${tab}`;
    if (window.location.hash !== nextHash) {
      window.history.replaceState(null, "", nextHash);
    }
  }, []);

  const routeToEntity = React.useCallback(
    (rawId: string, preferredTab?: TabKey) => {
      const id = rawId.trim();
      if (!id) return;
      const nonce = Date.now();
      const upper = id.toUpperCase();

      if (preferredTab === "catalog") {
        setFocusSpec({ id, nonce });
        setActiveTab("catalog", id);
        return;
      }

      if (upper.startsWith("CNT-") || upper.startsWith("CMP-") || upper.startsWith("CTR-")) {
        setSelectedComponent({ id, nonce });
        setActiveTab("architecture", id);
      } else if (upper.startsWith("DLT-")) {
        setSelectedDelta({ id, nonce });
        setActiveTab("deltas", id);
      } else if (upper.startsWith("EXP-")) {
        setSelectedExploration({ id, nonce });
        setActiveTab("explorations", id);
      } else if (
        upper.startsWith("DOM-") ||
        upper.startsWith("CAP-") ||
        upper.startsWith("POL-") ||
        upper.startsWith("PER-") ||
        upper.startsWith("INV-")
      ) {
        setSelectedBusiness({ id, nonce });
        setActiveTab("business", id);
      } else if (
        upper.startsWith("DS-") ||
        upper.startsWith("CLT-") ||
        upper.startsWith("SCR-") ||
        upper.startsWith("UIC-") ||
        upper.startsWith("FLOW-")
      ) {
        setSelectedDesign({ id, nonce });
        setActiveTab("design", id);
      } else {
        setFocusSpec({ id, nonce });
        setActiveTab("catalog", id);
      }
    },
    [setActiveTab]
  );

  // Sync URL hash (#/<tab>/<entity-id>) on initial load and hashchange (DEC-PORTAL-HASH-ROUTING)
  React.useEffect(() => {
    const applyHash = () => {
      const parsed = parseRouteHash(window.location.hash);
      if (!parsed) return;
      setActiveTabState(parsed.tab);
      if (parsed.entityId) {
        const nonce = Date.now();
        if (parsed.tab === "architecture") {
          setSelectedComponent({ id: parsed.entityId, nonce });
        } else if (parsed.tab === "deltas") {
          setSelectedDelta({ id: parsed.entityId, nonce });
        } else if (parsed.tab === "explorations") {
          setSelectedExploration({ id: parsed.entityId, nonce });
        } else if (parsed.tab === "business") {
          setSelectedBusiness({ id: parsed.entityId, nonce });
        } else if (parsed.tab === "design") {
          setSelectedDesign({ id: parsed.entityId, nonce });
        } else if (parsed.tab === "catalog") {
          setFocusSpec({ id: parsed.entityId, nonce });
        }
      }
    };
    applyHash();
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, []);

  // Sync theme to <html> element
  React.useEffect(() => {
    if (darkMode) {
      document.documentElement.classList.add("dark");
      document.documentElement.classList.remove("light");
      localStorage.setItem("eidos_theme", "dark");
    } else {
      document.documentElement.classList.remove("dark");
      document.documentElement.classList.add("light");
      localStorage.setItem("eidos_theme", "light");
    }
  }, [darkMode]);

  // Load data from /api/data if not pre-injected in window.__EIDOS_DATA__
  React.useEffect(() => {
    if (!data && !isStaticBundle) {
      fetch("/api/data")
        .then((res) => res.json())
        .then((payload: PortalData) => setData(hydratePortalData(payload)))
        .catch((err) => console.warn("Could not load /api/data:", err));
    }
  }, [data, isStaticBundle]);

  // Progressive mtime polling strictly when window.__EIDOS_DATA__ is absent (DEC-PORTAL-PROGRESSIVE-MTIME-POLLING)
  React.useEffect(() => {
    if (isStaticBundle) return;
    let lastSig = "";
    let cancelled = false;
    const timer = window.setInterval(() => {
      fetch("/api/version")
        .then((r) => (r.ok ? r.json() : null))
        .then((ver) => {
          if (!ver || cancelled) return;
          const sig = `${ver.count}:${ver.max_mtime_ns}:${ver.total_size}`;
          if (lastSig && lastSig !== sig) {
            fetch("/api/data")
              .then((r) => r.json())
              .then((payload: PortalData) => {
                if (!cancelled) setData(hydratePortalData(payload));
              })
              .catch(() => {});
          }
          lastSig = sig;
          setLiveSyncActive(true);
        })
        .catch(() => setLiveSyncActive(false));
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isStaticBundle]);

  // Resolve xref:/<<ID>>/link:*.adoc[] and cross-entity badge clicks
  React.useEffect(() => {
    if (!data) return;
    const onNavigate = (event: Event) => {
      const { target } = (event as CustomEvent<SpecNavDetail>).detail;
      const spec = resolveSpecTarget(data.specs, target);
      if (spec) {
        routeToEntity(spec.id);
        return;
      }
      // Also support direct entity IDs (INV-*, CMP-*, DEC-*) that may be embedded inside a parent spec
      const trimmed = (target || "").trim();
      if (
        trimmed.startsWith("INV-") ||
        trimmed.startsWith("CMP-") ||
        trimmed.startsWith("CTR-") ||
        trimmed.startsWith("CNT-")
      ) {
        routeToEntity(trimmed);
        return;
      }
      console.warn(`Eidos portal: cross-reference '${target}' does not match any loaded specification.`);
    };
    window.addEventListener(SPEC_NAV_EVENT, onNavigate);
    return () => window.removeEventListener(SPEC_NAV_EVENT, onNavigate);
  }, [data, routeToEntity]);

  const toggleSidebar = () => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("eidos_sidebar_collapsed", String(next));
      return next;
    });
  };

  const toggleTheme = () => {
    setDarkMode((prev) => !prev);
  };

  // Build searchable items for CMD+K spotlight search with pre-indexed lowercased searchText
  const commandItems: CommandItem[] = React.useMemo(() => {
    if (!data) return [];
    const items: CommandItem[] = [];
    const seenIds = new Set<string>();
    const extractHeadings = (body?: string) =>
      (body || "")
        .split("\n")
        .filter((l) => l.startsWith("="))
        .join(" ");

    // C4 Containers & Internal L3 Components
    data.components.forEach((c) => {
      seenIds.add(c.id);
      const headings = extractHeadings(c.description);
      items.push({
        id: c.id,
        title: c.title,
        subtitle: c.source_path,
        searchText: `${c.id} ${c.title} ${c.source_path} ${c.responsibility || ""} ${headings}`.toLowerCase(),
        category: "components",
        raw: c,
      });
      (c.internal_components || []).forEach((ic) => {
        if (ic.id && !seenIds.has(ic.id)) {
          seenIds.add(ic.id);
          items.push({
            id: ic.id,
            title: ic.title || ic.id,
            subtitle: `${c.id} • ${Array.isArray(ic.source_path) ? ic.source_path.join(", ") : ic.source_path || ""}`,
            searchText: `${ic.id} ${ic.title || ""} ${ic.responsibility || ""} ${c.id}`.toLowerCase(),
            category: "components",
            raw: ic,
          });
        }
      });
    });

    // Boundary Contracts (CTR-*)
    (data.contracts || []).forEach((ct) => {
      seenIds.add(ct.id);
      items.push({
        id: ct.id,
        title: ct.title,
        subtitle: `${ct.protocol} • ${ct.container}`,
        searchText: `${ct.id} ${ct.title} ${ct.protocol} ${ct.container} ${extractHeadings(ct.description)}`.toLowerCase(),
        category: "contracts",
        raw: ct,
      });
    });

    // Architectural Decisions (DEC-*)
    (data.decisions || []).forEach((dec) => {
      if (dec.id && !seenIds.has(dec.id)) {
        seenIds.add(dec.id);
        items.push({
          id: dec.id,
          title: dec.title,
          subtitle: `${dec.source_id || "Architecture"} • ${dec.decision?.slice(0, 80) || ""}`,
          searchText: `${dec.id} ${dec.title} ${dec.decision || ""} ${dec.rationale || ""} ${dec.source_id || ""}`.toLowerCase(),
          category: "decisions",
          raw: dec,
        });
      }
    });

    // Invariants
    (data.invariants || []).forEach((inv) => {
      seenIds.add(inv.id);
      items.push({
        id: inv.id,
        title: inv.title || inv.category,
        subtitle: inv.rule,
        searchText: `${inv.id} ${inv.title || ""} ${inv.category || ""} ${inv.rule || ""} ${inv.test_binding || ""}`.toLowerCase(),
        category: "invariants",
        raw: inv,
      });
    });

    // Deltas (including rejected)
    const deltasList = [
      ...(data.deltas?.proposed || []),
      ...(data.deltas?.in_review || []),
      ...(data.deltas?.approved || []),
      ...(data.deltas?.applied || []),
      ...(data.deltas?.rejected || []),
    ];
    deltasList.forEach((d) => {
      seenIds.add(d.id);
      items.push({
        id: d.id,
        title: d.title,
        subtitle: d.status,
        searchText: `${d.id} ${d.title} ${d.status} ${d.motivation || ""} ${extractHeadings(d.body)}`.toLowerCase(),
        category: "deltas",
        raw: d,
      });
    });

    // Policies
    (data.policies || []).forEach((pol) => {
      seenIds.add(pol.id);
      items.push({
        id: pol.id,
        title: pol.title,
        subtitle: pol.invariants?.[0]?.rule || pol.category,
        searchText: `${pol.id} ${pol.title} ${pol.category} ${extractHeadings(pol.body)}`.toLowerCase(),
        category: "policies",
        raw: pol,
      });
    });

    // Domains
    (data.domains || []).forEach((dom) => {
      seenIds.add(dom.id);
      items.push({
        id: dom.id,
        title: dom.title,
        subtitle: dom.mission,
        searchText: `${dom.id} ${dom.title} ${dom.mission || ""} ${extractHeadings(dom.body)}`.toLowerCase(),
        category: "domains",
        raw: dom,
      });
    });

    // Capabilities
    (data.capabilities || []).forEach((cap) => {
      seenIds.add(cap.id);
      items.push({
        id: cap.id,
        title: cap.title || cap.id,
        subtitle: cap.summary,
        searchText: `${cap.id} ${cap.title || ""} ${cap.summary || ""} ${extractHeadings(cap.body)}`.toLowerCase(),
        category: "capabilities",
        raw: cap,
      });
    });

    // Personas
    (data.personas || []).forEach((per) => {
      seenIds.add(per.id);
      items.push({
        id: per.id,
        title: per.title,
        subtitle: per.role,
        searchText: `${per.id} ${per.title} ${per.role || ""} ${extractHeadings(per.body)}`.toLowerCase(),
        category: "personas",
        raw: per,
      });
    });

    // Explorations
    (data.explorations || []).forEach((exp) => {
      seenIds.add(exp.id);
      items.push({
        id: exp.id,
        title: exp.title,
        subtitle: `${exp.status} • ${exp.hypothesis?.slice(0, 80) || ""}`,
        searchText: `${exp.id} ${exp.title} ${exp.status} ${exp.hypothesis || ""} ${extractHeadings(exp.body)}`.toLowerCase(),
        category: "explorations",
        raw: exp,
      });
    });

    // Design entities (DS-*, CLT-*, UIC-*, SCR-*, FLOW-*)
    [
      ...(data.design_systems || []),
      ...(data.clients || []),
      ...(data.ui_components || []),
      ...(data.screens || []),
      ...(data.flows || []),
    ].forEach((de: any) => {
      if (de.id && !seenIds.has(de.id)) {
        seenIds.add(de.id);
        items.push({
          id: de.id,
          title: de.title || de.id,
          subtitle: de.route || de.platform || de.category || de.flow_type || "Design",
          searchText: `${de.id} ${de.title || ""} ${de.route || ""} ${de.category || ""}`.toLowerCase(),
          category: "design",
          raw: de,
        });
      }
    });

    // Specs not already indexed under a specialized category
    data.specs.forEach((s) => {
      if (!seenIds.has(s.id)) {
        seenIds.add(s.id);
        items.push({
          id: s.id,
          title: s.title,
          subtitle: s.path,
          searchText: `${s.id} ${s.title} ${s.path} ${extractHeadings(s.body)}`.toLowerCase(),
          category: "specs",
          raw: s,
        });
      }
    });

    return items;
  }, [data]);

  const handleCommandSelect = (item: CommandItem) => {
    const nonce = Date.now();
    if (item.category === "components" || item.category === "contracts") {
      setSelectedComponent({ id: item.id, nonce });
      setActiveTab("architecture", item.id);
    } else if (item.category === "decisions") {
      const srcId = item.raw?.source_id || item.id;
      routeToEntity(srcId);
    } else if (item.category === "deltas") {
      setSelectedDelta({ id: item.id, nonce });
      setActiveTab("deltas", item.id);
    } else if (item.category === "explorations") {
      setSelectedExploration({ id: item.id, nonce });
      setActiveTab("explorations", item.id);
    } else if (item.category === "design") {
      setSelectedDesign({ id: item.id, nonce });
      setActiveTab("design", item.id);
    } else if (
      item.category === "policies" ||
      item.category === "domains" ||
      item.category === "capabilities" ||
      item.category === "personas" ||
      item.category === "invariants"
    ) {
      setSelectedBusiness({ id: item.id, nonce });
      setActiveTab("business", item.id);
    } else {
      setFocusSpec({ id: item.id, nonce });
      setActiveTab("catalog", item.id);
    }
  };

  if (!data) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-background text-foreground font-mono text-xs">
        <div className="flex items-center gap-3">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span>Synthesizing Living Specifications...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen w-full bg-background text-foreground antialiased portal-shell">
      {/* Sidebar Rail (UIC-PORTAL-SIDEBAR) */}
      <NavigationRail
        activeTab={activeTab}
        onTabChange={setActiveTab}
        collapsed={sidebarCollapsed}
        onToggleCollapse={toggleSidebar}
        darkMode={darkMode}
        onToggleDarkMode={toggleTheme}
        onOpenCommand={() => setCommandOpen(true)}
        totalSpecs={data.overview.total_specs}
        deltasCount={data.overview.deltas_count}
        explorationsCount={data.explorations?.length || data.overview.explorations_count || 0}
      />

      {/* Main Content Area */}
      <main className="flex-1 min-w-0 flex flex-col min-h-screen overflow-y-auto">
        {/* Top Header */}
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-border/80 bg-background/80 backdrop-blur-md px-6 py-3.5">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-semibold text-muted-foreground">Eidos</span>
            <span className="text-border">/</span>
            <span className="font-mono font-bold text-primary uppercase">
              {activeTab}
            </span>
          </div>
          {!isStaticBundle && (
            <div className="flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground">
              <span
                className={`h-2 w-2 rounded-full ${
                  liveSyncActive ? "bg-emerald-500 animate-pulse" : "bg-amber-500"
                }`}
              />
              <span>{liveSyncActive ? "Live Sync" : "Offline"}</span>
            </div>
          )}
        </header>

        {/* Viewport Content */}
        <div className="flex-1 p-6 max-w-7xl w-full mx-auto">
          {activeTab === "home" && (
            <HomeView
              data={data}
              onNavigateTab={(t: TabKey) => setActiveTab(t)}
              onInspectSpec={(spec: any) => {
                if (spec?.id) {
                  setFocusSpec({ id: spec.id, nonce: Date.now() });
                  setActiveTab("catalog", spec.id);
                } else {
                  setActiveTab("catalog");
                }
              }}
            />
          )}
          {activeTab === "architecture" && (
            <ArchitectureView
              data={data}
              darkMode={darkMode}
              initialComponentId={selectedComponent?.id}
              initialComponentNonce={selectedComponent?.nonce}
              onClearSelectedComponent={() => setSelectedComponent(null)}
            />
          )}
          {activeTab === "deltas" && (
            <DeltasView
              data={data}
              initialDeltaId={selectedDelta?.id}
              initialDeltaNonce={selectedDelta?.nonce}
              onClearSelectedDelta={() => setSelectedDelta(null)}
            />
          )}
          {activeTab === "explorations" && (
            <ExplorationsView
              data={data}
              initialSelectedId={selectedExploration?.id}
              initialSelectedNonce={selectedExploration?.nonce}
              onClearSelected={() => setSelectedExploration(null)}
              onNavigateDelta={(deltaId) => {
                setSelectedDelta({ id: deltaId, nonce: Date.now() });
                setActiveTab("deltas", deltaId);
              }}
            />
          )}
          {activeTab === "catalog" && (
            <CatalogView
              data={data}
              focusSpecId={focusSpec?.id}
              focusNonce={focusSpec?.nonce}
              onFocusHandled={() => setFocusSpec(null)}
            />
          )}
          {activeTab === "business" && (
            <BusinessView
              data={data}
              initialSelectedId={selectedBusiness?.id}
              initialSelectedNonce={selectedBusiness?.nonce}
              onClearSelected={() => setSelectedBusiness(null)}
            />
          )}
          {activeTab === "design" && (
            <DesignView
              data={data}
              darkMode={darkMode}
              initialSelectedId={selectedDesign?.id}
              initialSelectedNonce={selectedDesign?.nonce}
              onClearSelected={() => setSelectedDesign(null)}
            />
          )}
        </div>
      </main>

      {/* Spotlight Command Dialog (CMD+K) */}
      <CommandDialog
        open={commandOpen}
        onOpenChange={setCommandOpen}
        items={commandItems}
        onSelect={handleCommandSelect}
      />
    </div>
  );
}
