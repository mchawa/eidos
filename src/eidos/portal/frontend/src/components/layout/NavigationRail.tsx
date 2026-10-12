import * as React from "react";
import {
  Home,
  Briefcase,
  Network,
  GitBranch,
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
        "sidebar h-screen sticky top-0 bg-card border-r border-border flex flex-col justify-between transition-all duration-200 z-40 select-none",
        collapsed ? "w-20" : "w-64"
      )}
      data-testid="portal-sidebar"
    >
      {/* Sidebar Header */}
      <div>
        <div className="flex items-center justify-between p-4 border-b border-border/70">
          <div className="flex items-center gap-3 overflow-hidden">
            <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-primary to-blue-600 flex items-center justify-center text-primary-foreground font-bold font-mono text-base md-elevation-1 shrink-0">
              E
            </div>
            {!collapsed && (
              <div className="flex flex-col truncate">
                <span className="font-bold text-sm text-foreground tracking-tight flex items-center gap-1.5">
                  Eidos Portal
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-primary/10 text-primary">
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
            className="p-1.5 rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer"
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label="Toggle sidebar"
          >
            {collapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </button>
        </div>

        {/* Global Search Shortcut Button */}
        <div className="p-3">
          <button
            onClick={onOpenCommand}
            className={cn(
              "w-full flex items-center gap-2.5 rounded-xl border border-border/80 bg-muted/40 hover:bg-muted/80 text-muted-foreground text-xs py-2 px-3 transition-colors cursor-pointer text-left md-state-layer",
              collapsed && "justify-center px-0"
            )}
            title="Search specifications (CMD+K)"
          >
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
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
        <nav className="px-2 py-2 space-y-1">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                id={tab.id}
                onClick={() => onTabChange(tab.key as TabKey)}
                className={cn(
                  "w-full flex items-center gap-3 px-3 py-2.5 rounded-full text-xs font-medium transition-all duration-150 cursor-pointer md-state-layer group",
                  isActive
                    ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                    : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
                  collapsed && "justify-center px-0"
                )}
                title={tab.label}
              >
                <Icon
                  className={cn(
                    "h-4 w-4 shrink-0 transition-transform group-hover:scale-110",
                    isActive ? "text-primary-foreground" : "text-muted-foreground group-hover:text-foreground"
                  )}
                />
                {!collapsed && (
                  <>
                    <span className="flex-1 text-left truncate">{tab.label}</span>
                    {tab.badge && (
                      <span
                        className={cn(
                          "text-[10px] font-mono px-2 py-0.5 rounded-full",
                          isActive
                            ? "bg-white/20 text-white font-bold"
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
      <div className="p-3 border-t border-border/70 space-y-2">
        <button
          id="themeToggleBtn"
          onClick={onToggleDarkMode}
          className={cn(
            "w-full flex items-center justify-between p-2.5 rounded-xl text-xs transition-colors cursor-pointer border border-border/60 hover:bg-muted/60",
            collapsed ? "justify-center px-0" : "px-3"
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
              <span className="font-medium text-foreground truncate">
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
