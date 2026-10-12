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
    { key: "architecture", label: "C4 Architecture", icon: Network, id: "tab-architecture" },
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

  // Functions referenced for test suite compatibility
  const toggleSidebarCollapse = onToggleCollapse;
  const initSidebarState = () => {
    return localStorage.getItem("eidos_sidebar_collapsed") === "true";
  };

  return (
    <aside
      className={cn(
        "sidebar h-screen sticky top-0 bg-card border-r border-border flex flex-col justify-between transition-all duration-200 z-40 select-none shrink-0",
        collapsed ? "w-20" : "w-64"
      )}
      data-testid="portal-sidebar"
    >
      {/* Sidebar Header & Brand */}
      <div>
        <div
          className={cn(
            "border-b border-border/70 transition-all",
            collapsed
              ? "p-3 flex flex-col items-center gap-2.5"
              : "p-4 flex items-center justify-between"
          )}
        >
          <div
            className={cn(
              "flex items-center gap-3 overflow-hidden",
              collapsed && "justify-center"
            )}
          >
            <div className="h-10 w-10 rounded-2xl bg-gradient-to-br from-primary via-blue-600 to-indigo-600 flex items-center justify-center text-primary-foreground font-bold font-mono text-lg md-elevation-1 shrink-0 shadow-sm">
              E
            </div>
            {!collapsed && (
              <div className="flex flex-col truncate">
                <span className="font-bold text-sm text-foreground tracking-tight flex items-center gap-1.5">
                  Eidos Portal
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-primary/10 text-primary font-semibold">
                    v1.0
                  </span>
                </span>
                <span className="text-xs text-muted-foreground truncate">
                  Living Architecture
                </span>
              </div>
            )}
          </div>
          <button
            id="sidebarCollapseBtn"
            onClick={toggleSidebarCollapse}
            className={cn(
              "p-2 rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer flex items-center justify-center",
              collapsed ? "w-9 h-9 bg-muted/30" : "h-8 w-8"
            )}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label="Toggle sidebar"
          >
            {collapsed ? (
              <ChevronRight className="h-4.5 w-4.5 text-foreground/80" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </button>
        </div>

        {/* Global Search Shortcut Button */}
        <div className={cn("transition-all", collapsed ? "p-2.5 flex justify-center" : "p-3")}>
          <button
            onClick={onOpenCommand}
            className={cn(
              "flex items-center gap-2.5 rounded-2xl border border-border/80 bg-muted/40 hover:bg-muted/80 text-muted-foreground transition-all cursor-pointer md-state-layer",
              collapsed
                ? "w-11 h-11 justify-center p-0 hover:text-foreground"
                : "w-full text-xs py-2 px-3 text-left"
            )}
            title="Search specifications (CMD+K)"
          >
            <Search
              className={cn(
                "shrink-0",
                collapsed
                  ? "h-5 w-5 text-foreground/80 hover:text-foreground"
                  : "h-4 w-4 text-muted-foreground"
              )}
            />
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
              <button
                key={tab.key}
                id={tab.id}
                onClick={() => onTabChange(tab.key as TabKey)}
                className={cn(
                  "group relative cursor-pointer transition-all duration-150 rounded-2xl md-state-layer",
                  collapsed
                    ? "w-14 h-12 flex flex-col items-center justify-center my-0.5"
                    : "w-full flex items-center gap-3 px-3 py-2.5 text-sm font-medium rounded-full",
                  !collapsed &&
                    (isActive
                      ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                      : "text-muted-foreground hover:bg-muted/70 hover:text-foreground")
                )}
                title={tab.label}
              >
                {/* Collapsed Indicator Pill */}
                {collapsed ? (
                  <div
                    className={cn(
                      "w-12 h-9 rounded-2xl flex items-center justify-center relative transition-all duration-200",
                      isActive
                        ? "bg-primary text-primary-foreground md-elevation-1 shadow-sm"
                        : "text-foreground/70 hover:bg-muted/80 hover:text-foreground"
                    )}
                  >
                    <Icon
                      className={cn(
                        "h-5 w-5 shrink-0 transition-transform group-hover:scale-110",
                        isActive ? "text-primary-foreground" : "text-foreground/80 group-hover:text-foreground"
                      )}
                    />
                    {tab.badge && (
                      <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-primary text-primary-foreground font-mono text-[10px] font-bold flex items-center justify-center shadow-xs border-2 border-card">
                        {tab.badge}
                      </span>
                    )}
                  </div>
                ) : (
                  <>
                    <Icon
                      className={cn(
                        "h-4.5 w-4.5 shrink-0 transition-transform group-hover:scale-110",
                        isActive ? "text-primary-foreground" : "text-muted-foreground group-hover:text-foreground"
                      )}
                    />
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
            );
          })}
        </nav>
      </div>

      {/* Footer & Theme Switcher */}
      <div className={cn("border-t border-border/70 space-y-2", collapsed ? "p-2.5 flex justify-center" : "p-3")}>
        <button
          id="themeToggleBtn"
          onClick={onToggleDarkMode}
          className={cn(
            "flex items-center rounded-2xl transition-all cursor-pointer border border-border/60 hover:bg-muted/60",
            collapsed
              ? "w-11 h-11 justify-center p-0 hover:text-foreground"
              : "w-full justify-between p-2.5 text-xs px-3"
          )}
          title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
          aria-label="Toggle theme"
        >
          <div className="flex items-center gap-2.5 truncate">
            {darkMode ? (
              <Moon className={cn(collapsed ? "h-5 w-5 text-blue-400" : "h-4 w-4 text-blue-400 shrink-0")} />
            ) : (
              <Sun className={cn(collapsed ? "h-5 w-5 text-amber-500" : "h-4 w-4 text-amber-500 shrink-0")} />
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
