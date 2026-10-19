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

export function App() {
  const [data, setData] = React.useState<PortalData | null>(() => {
    return window.__EIDOS_DATA__ || null;
  });

  const [activeTab, setActiveTab] = React.useState<TabKey>("home");
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
    if (!data) {
      fetch("/api/data")
        .then((res) => res.json())
        .then((payload: PortalData) => setData(payload))
        .catch((err) => console.warn("Could not load /api/data:", err));
    }
  }, [data]);

  // Resolve xref:/<<ID>>/link:*.adoc[] clicks from the AsciiDoc reader to the spec catalog inspector.
  React.useEffect(() => {
    if (!data) return;
    const onNavigate = (event: Event) => {
      const { target } = (event as CustomEvent<SpecNavDetail>).detail;
      const spec = resolveSpecTarget(data.specs, target);
      if (!spec) {
        console.warn(`Eidos portal: cross-reference '${target}' does not match any loaded specification.`);
        return;
      }
      setFocusSpec({ id: spec.id, nonce: Date.now() });
      setActiveTab("catalog");
    };
    window.addEventListener(SPEC_NAV_EVENT, onNavigate);
    return () => window.removeEventListener(SPEC_NAV_EVENT, onNavigate);
  }, [data]);

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

  // Build searchable items for CMD+K spotlight search (deduplicating specialized items against generic specs)
  const commandItems: CommandItem[] = React.useMemo(() => {
    if (!data) return [];
    const items: CommandItem[] = [];
    const seenIds = new Set<string>();

    // C4 Components
    data.components.forEach((c) => {
      seenIds.add(c.id);
      items.push({
        id: c.id,
        title: c.title,
        subtitle: c.source_path,
        category: "components",
        raw: c,
      });
    });

    // Invariants
    (data.invariants || []).forEach((inv) => {
      seenIds.add(inv.id);
      items.push({
        id: inv.id,
        title: inv.title || inv.category,
        subtitle: inv.rule,
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
        category: "explorations",
        raw: exp,
      });
    });

    // Specs not already indexed under a specialized category
    data.specs.forEach((s) => {
      if (!seenIds.has(s.id)) {
        seenIds.add(s.id);
        items.push({
          id: s.id,
          title: s.title,
          subtitle: s.path,
          category: "specs",
          raw: s,
        });
      }
    });

    return items;
  }, [data]);

  const handleCommandSelect = (item: CommandItem) => {
    const nonce = Date.now();
    if (item.category === "components") {
      setSelectedComponent({ id: item.id, nonce });
      setActiveTab("architecture");
    } else if (item.category === "deltas") {
      setSelectedDelta({ id: item.id, nonce });
      setActiveTab("deltas");
    } else if (item.category === "explorations") {
      setSelectedExploration({ id: item.id, nonce });
      setActiveTab("explorations");
    } else if (
      item.category === "policies" ||
      item.category === "domains" ||
      item.category === "capabilities" ||
      item.category === "personas" ||
      item.category === "invariants"
    ) {
      setSelectedBusiness({ id: item.id, nonce });
      setActiveTab("business");
    } else {
      setFocusSpec({ id: item.id, nonce });
      setActiveTab("catalog");
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
        </header>

        {/* Viewport Content */}
        <div className="flex-1 p-6 max-w-7xl w-full mx-auto">
          {activeTab === "home" && (
            <HomeView
              data={data}
              onNavigateTab={setActiveTab}
              onInspectSpec={() => setActiveTab("catalog")}
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
                setActiveTab("deltas");
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
          {activeTab === "design" && <DesignView data={data} darkMode={darkMode} />}
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
