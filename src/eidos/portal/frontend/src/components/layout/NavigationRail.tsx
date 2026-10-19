import * as React from "react";
import {
  Home,
  Briefcase,
  Network,
  GitBranch,
  Lightbulb,
  Layers,
  Palette,
  ChevronLeft,
  ChevronRight,
  Sun,
  Moon,
  Search,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { EidosLogo } from "@/components/ui/EidosLogo";

export type TabKey =
  | "home"
  | "business"
  | "architecture"
  | "deltas"
  | "explorations"
  | "catalog"
  | "design";

export interface NavigationRailProps {
  activeTab: TabKey;
  onTabChange: (tab: TabKey) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  darkMode: boolean;
  onToggleDarkMode: () => void;
  onOpenCommand: () => void;
  totalSpecs: number;
  deltasCount: number;
  explorationsCount?: number;
}

export function NavigationRail({
  activeTab,
  onTabChange,
  collapsed,
  onToggleCollapse,
  darkMode,
  onToggleDarkMode,
  onOpenCommand,
  totalSpecs,
  deltasCount,
  explorationsCount = 0,
}: NavigationRailProps) {
  // Navigation tabs conforming to Eidos IA
  const tabs = [
    { key: "home", label: "Overview", icon: Home, id: "tab-home" },
    { key: "business", label: "Business & Domains", icon: Briefcase, id: "tab-business" },
    { key: "architecture", label: "Architecture", icon: Network, id: "tab-architecture" },
    {
      key: "deltas",
      label: "SpecDeltas",
      icon: GitBranch,
      id: "tab-deltas",
      badge: deltasCount > 0 ? `${deltasCount}` : undefined,
    },
    {
      key: "explorations",
      label: "Explorations",
      icon: Lightbulb,
      id: "tab-explorations",
      badge: explorationsCount > 0 ? `${explorationsCount}` : undefined,
    },
    {
      key: "catalog",
      label: "Living Catalog",
      icon: Layers,
      id: "tab-catalog",
      badge: `${totalSpecs}`,
    },
    { key: "design", label: "Design Systems", icon: Palette, id: "tab-design" },
  ];

  return (
    <aside
      className={cn(
        "sidebar h-screen sticky top-0 bg-card border-r border-border flex flex-col justify-between transition-all duration-200 z-40 select-none shrink-0",
        collapsed ? "w-16" : "w-64"
      )}
      data-testid="portal-sidebar"
    >
      {/* Sidebar Header & Brand */}
      <div>
        <div
          className={cn(
            "h-14 border-b border-border/70 flex items-center transition-all px-3",
            collapsed ? "justify-center" : "justify-between"
          )}
        >
          {!collapsed ? (
            <>
              <div className="flex items-center gap-2.5 overflow-hidden">
                <div className="h-8 w-8 rounded-xl bg-card border border-border/80 flex items-center justify-center p-1 md-elevation-1 shrink-0 shadow-xs">
                  <EidosLogo size={22} />
                </div>
                <div className="flex flex-col truncate">
                  <span className="font-bold text-sm text-foreground tracking-tight">
                    Eidos Portal
                  </span>
                </div>
              </div>
              <button
                id="sidebarCollapseBtn"
                onClick={toggleSidebarCollapse}
                className="h-7 w-7 rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer flex items-center justify-center"
                title="Collapse sidebar"
                aria-label="Toggle sidebar"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            </>
          ) : (
            <button
              id="sidebarCollapseBtn"
              onClick={toggleSidebarCollapse}
              className="h-8 w-8 rounded-xl flex items-center justify-center hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-all cursor-pointer relative group"
              title="Expand sidebar"
              aria-label="Toggle sidebar"
            >
              <div className="h-8 w-8 rounded-xl bg-card border border-border/80 flex items-center justify-center p-1 md-elevation-1 shrink-0 shadow-xs group-hover:scale-95 transition-transform">
                <EidosLogo size={22} />
              </div>
              <span className="absolute -bottom-0.5 -right-0.5 bg-card border border-border/80 rounded-full p-0.5 shadow-xs">
                <ChevronRight className="h-2.5 w-2.5 text-foreground" />
              </span>
            </button>
          )}
        </div>

        {/* Global Search Shortcut Button */}
        <div className={cn("transition-all", collapsed ? "p-2 flex justify-center" : "p-3")}>
          <button
            onClick={onOpenCommand}
            className={cn(
              "flex items-center gap-2.5 rounded-xl border border-border/80 bg-muted/40 hover:bg-muted/80 text-muted-foreground transition-all cursor-pointer md-state-layer",
              collapsed
                ? "w-10 h-10 justify-center p-0 hover:text-foreground"
                : "w-full text-xs py-2 px-3 text-left"
            )}
            title="Search specifications (CMD+K)"
          >
            <Search className="h-4 w-4 shrink-0 text-muted-foreground group-hover:text-foreground" />
            {!collapsed && (
              <>
                <span className="flex-1 truncate">Search living specs...</span>
                <kbd className="font-mono text-[10px] bg-card px-1.5 py-0.5 rounded border border-border text-muted-foreground">
                  ⌘K
                </kbd>
              </>
            )}
          </button>
        </div>

        {/* Navigation Items (MD3 Navigation Rail / Tabs) */}
        <nav
          className={cn(
            "space-y-1.5 transition-all",
            collapsed ? "px-2 py-1 flex flex-col items-center" : "px-3 py-2"
          )}
        >
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.key;
            return (
              <div key={tab.key} className={cn("relative flex", collapsed ? "justify-center" : "w-full")}>
                <div className={cn("relative", collapsed ? "inline-flex" : "w-full")}>
                  <button
                    id={tab.id}
                    onClick={() => onTabChange(tab.key as TabKey)}
                    className={cn(
                      "cursor-pointer transition-all duration-150",
                      collapsed
                        ? "w-10 h-10 rounded-xl flex items-center justify-center my-0.5"
                        : "w-full flex items-center gap-3 px-3 py-2.5 text-sm font-medium rounded-xl",
                      isActive
                        ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                        : "text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                    )}
                    title={tab.label}
                  >
                    <Icon className="h-4 w-4 shrink-0 transition-transform group-hover:scale-105" />
                    {!collapsed && (
                      <>
                        <span className="flex-1 text-left truncate">{tab.label}</span>
                        {tab.badge && (
                          <span
                            className={cn(
                              "text-xs font-mono px-2 py-0.5 rounded-full font-semibold",
                              isActive
                                ? "bg-white/20 text-white"
                                : "bg-muted text-muted-foreground group-hover:bg-card"
                            )}
                          >
                            {tab.badge}
                          </span>
                        )}
                      </>
                    )}
                  </button>
                  {collapsed && tab.badge && (
                    <span className="pointer-events-none absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-blue-600 text-white font-mono text-[10px] font-bold flex items-center justify-center shadow-md ring-2 ring-card z-20">
                      {tab.badge}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </nav>
      </div>

      {/* Footer & Theme Switcher */}
      <div className={cn("border-t border-border/70 space-y-2", collapsed ? "p-2 flex justify-center" : "p-3")}>
        <button
          id="themeToggleBtn"
          onClick={onToggleDarkMode}
          className={cn(
            "flex items-center rounded-xl transition-all cursor-pointer border border-border/60 hover:bg-muted/60",
            collapsed
              ? "w-10 h-10 justify-center p-0 hover:text-foreground"
              : "w-full justify-between p-2.5 text-xs px-3"
          )}
          title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
          aria-label="Toggle theme"
        >
          <div className="flex items-center gap-2.5 truncate">
            {darkMode ? (
              <Moon className="h-4 w-4 text-blue-400 shrink-0" />
            ) : (
              <Sun className="h-4 w-4 text-amber-500 shrink-0" />
            )}
            {!collapsed && (
              <span className="font-medium text-foreground truncate text-xs">
                {darkMode ? "Dark Theme" : "Light Theme"}
              </span>
            )}
          </div>
          {!collapsed && (
            <div
              className={cn(
                "w-8 h-4 rounded-full transition-colors relative flex items-center px-0.5 shrink-0",
                darkMode ? "bg-primary" : "bg-muted-foreground/30"
              )}
            >
              <div
                className={cn(
                  "w-3 h-3 rounded-full bg-white transition-transform shadow-xs",
                  darkMode ? "translate-x-4" : "translate-x-0"
                )}
              />
            </div>
          )}
        </button>
      </div>
    </aside>
  );
}
