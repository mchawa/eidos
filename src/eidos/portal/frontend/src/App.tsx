import * as React from "react";
import { NavigationRail, type TabKey } from "@/components/layout/NavigationRail";
import { HomeView } from "@/components/views/HomeView";
import { ArchitectureView } from "@/components/views/ArchitectureView";
import { DeltasView } from "@/components/views/DeltasView";
import { CatalogView } from "@/components/views/CatalogView";
import { BusinessView } from "@/components/views/BusinessView";
import { DesignView } from "@/components/views/DesignView";
import { CommandDialog, type CommandItem } from "@/components/ui/command";
import type { PortalData } from "@/types";

export function App() {
  const [data, setData] = React.useState<PortalData | null>(() => {
    return window.__EIDOS_DATA__ || null;
  });

  const [activeTab, setActiveTab] = React.useState<TabKey>("home");
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState<boolean>(() => {
    return localStorage.getItem("eidos_sidebar_collapsed") === "true";
  });
  const [darkMode, setDarkMode] = React.useState<boolean>(() => {
    const saved = localStorage.getItem("eidos_theme");
    return saved ? saved === "dark" : true;
  });
  const [commandOpen, setCommandOpen] = React.useState<boolean>(false);

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

  // Build searchable items for CMD+K spotlight search
  const commandItems: CommandItem[] = React.useMemo(() => {
    if (!data) return [];
    const items: CommandItem[] = [];

    // Specs
    data.specs.forEach((s) => {
      items.push({
        id: s.id,
        title: s.title,
        subtitle: s.path,
        category: "specs",
        raw: s,
      });
    });

    // C4 Components
    data.components.forEach((c) => {
      items.push({
        id: c.id,
        title: c.name,
        subtitle: c.package_path,
        category: "components",
        raw: c,
      });
    });

    // Invariants
    (data.invariants || []).forEach((inv) => {
      items.push({
        id: inv.id,
        title: inv.title || inv.category,
        subtitle: inv.rule,
        category: "invariants",
        raw: inv,
      });
    });

    // Deltas
    const deltasList = [
      ...(data.deltas?.proposed || []),
      ...(data.deltas?.in_review || []),
      ...(data.deltas?.approved || []),
      ...(data.deltas?.applied || []),
    ];
    deltasList.forEach((d) => {
      items.push({
        id: d.id,
        title: d.title,
        subtitle: d.status,
        category: "deltas",
        raw: d,
      });
    });

    return items;
  }, [data]);

  const handleCommandSelect = (item: CommandItem) => {
    if (item.category === "components") {
      setActiveTab("architecture");
    } else if (item.category === "deltas") {
      setActiveTab("deltas");
    } else {
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

          <div className="flex items-center gap-3">
            <span className="text-[11px] font-mono text-emerald-500 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-0.5 rounded-full flex items-center gap-1.5 font-semibold">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              Live Sync Active
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
            <ArchitectureView data={data} darkMode={darkMode} />
          )}
          {activeTab === "deltas" && <DeltasView data={data} />}
          {activeTab === "catalog" && <CatalogView data={data} />}
          {activeTab === "business" && <BusinessView data={data} />}
          {activeTab === "design" && <DesignView data={data} />}
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
