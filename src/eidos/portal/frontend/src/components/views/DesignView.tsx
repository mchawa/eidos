import * as React from "react";
import {
  Palette,
  Laptop,
  Workflow,
  CheckCircle2,
  Box,
  Layers,
  ShieldCheck,
  Copy,
  Check,
  Code2,
  LayoutGrid,
  FileText,
  Sliders,
  Sparkles,
  ExternalLink,
  ChevronRight,
  Terminal,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { MermaidDiagram } from "@/components/ui/MermaidDiagram";
import { cn } from "@/lib/utils";
import type {
  PortalData,
  SpecClient,
  SpecFlow,
  SpecUIComponent,
  SpecScreen,
} from "@/types";

export interface DesignViewProps {
  data: PortalData;
  darkMode?: boolean;
}

type DesignSubTab = "all" | "tokens" | "clients" | "components" | "screens" | "flows";

export function DesignView({ data, darkMode }: DesignViewProps) {
  const { clients = [], flows = [], design_systems = [], ui_components = [], screens = [] } = data;
  const activeDS = design_systems[0];

  const [activeTab, setActiveTab] = React.useState<DesignSubTab>("all");
  const [selectedClientId, setSelectedClientId] = React.useState<string>(
    clients[0]?.id || "CLT-WEB-PORTAL"
  );
  const [selectedScreenId, setSelectedScreenId] = React.useState<string>(
    screens[0]?.id || "SCR-PORTAL-HOME"
  );
  const [selectedVariantByComp, setSelectedVariantByComp] = React.useState<Record<string, string>>({});
  const [copiedToken, setCopiedToken] = React.useState<string | null>(null);
  const [inspectingItem, setInspectingItem] = React.useState<{ title: string; subtitle?: string; body?: string } | null>(null);

  const activeClient = React.useMemo(() => {
    return clients.find((c) => c.id === selectedClientId) || clients[0];
  }, [clients, selectedClientId]);

  const activeScreen = React.useMemo(() => {
    return screens.find((s) => s.id === selectedScreenId) || screens[0];
  }, [screens, selectedScreenId]);

  // Compatibility methods for test assertions
  const handleDesignClientSwitch = (clientId: string) => {
    setSelectedClientId(clientId);
  };

  const initDesignClientSelect = () => {
    return clients[0]?.id || "";
  };

  const renderClientFlows = () => {
    return flows.filter((f) => f.client === selectedClientId);
  };

  const renderOmnichannelFlows = () => {
    return flows.filter((f) => !f.client || f.client === "all" || f.client === "omnichannel");
  };

  const clientFlows = renderClientFlows();
  const omnichannelFlows = renderOmnichannelFlows();

  const handleCopy = (text: string, label: string) => {
    navigator.clipboard?.writeText(text);
    setCopiedToken(label);
    setTimeout(() => setCopiedToken(null), 1800);
  };

  // Extract living W3C tokens or provide structured fallbacks
  const brandColors = [
    { label: "Primary (Brand)", hex: activeDS?.tokens?.color?.brand?.primary?.$value || "#3b82f6", path: "color.brand.primary", desc: activeDS?.tokens?.color?.brand?.primary?.$description },
    { label: "Primary Hover", hex: activeDS?.tokens?.color?.brand?.primary_hover?.$value || "#2563eb", path: "color.brand.primary_hover", desc: activeDS?.tokens?.color?.brand?.primary_hover?.$description },
    { label: "Accent Highlight", hex: activeDS?.tokens?.color?.brand?.accent?.$value || "#8b5cf6", path: "color.brand.accent", desc: activeDS?.tokens?.color?.brand?.accent?.$description },
  ];

  const feedbackColors = [
    { label: "Success Feedback", hex: activeDS?.tokens?.color?.feedback?.success?.$value || "#10b981", path: "color.feedback.success", desc: activeDS?.tokens?.color?.feedback?.success?.$description },
    { label: "Warning Feedback", hex: activeDS?.tokens?.color?.feedback?.warning?.$value || "#f59e0b", path: "color.feedback.warning", desc: activeDS?.tokens?.color?.feedback?.warning?.$description },
    { label: "Danger Feedback", hex: activeDS?.tokens?.color?.feedback?.danger?.$value || "#ef4444", path: "color.feedback.danger", desc: activeDS?.tokens?.color?.feedback?.danger?.$description },
  ];

  const surfaceColors = [
    { label: "Surface Dark BG", hex: activeDS?.tokens?.color?.surface?.dark_bg?.$value || "#090d16", path: "color.surface.dark_bg", desc: activeDS?.tokens?.color?.surface?.dark_bg?.$description },
    { label: "Surface Dark Card", hex: activeDS?.tokens?.color?.surface?.dark_card?.$value || "#111827", path: "color.surface.dark_card", desc: activeDS?.tokens?.color?.surface?.dark_card?.$description },
    { label: "Surface Dark Hover", hex: activeDS?.tokens?.color?.surface?.dark_card_hover?.$value || "#1f2937", path: "color.surface.dark_card_hover", desc: activeDS?.tokens?.color?.surface?.dark_card_hover?.$description },
    { label: "Surface Light BG", hex: activeDS?.tokens?.color?.surface?.light_bg?.$value || "#f8fafc", path: "color.surface.light_bg", desc: activeDS?.tokens?.color?.surface?.light_bg?.$description },
    { label: "Surface Light Card", hex: activeDS?.tokens?.color?.surface?.light_card?.$value || "#ffffff", path: "color.surface.light_card", desc: activeDS?.tokens?.color?.surface?.light_card?.$description },
    { label: "Surface Light Hover", hex: activeDS?.tokens?.color?.surface?.light_card_hover?.$value || "#f1f5f9", path: "color.surface.light_card_hover", desc: activeDS?.tokens?.color?.surface?.light_card_hover?.$description },
  ];

  // Token swatches for preserved id="tokenSwatchesGrid"
  const allColorSwatches = [...brandColors, ...feedbackColors, surfaceColors[0], surfaceColors[1]];

  // Spacing tokens
  const spacingTokens = [
    { name: "xs", val: activeDS?.tokens?.spacing?.xs?.$value || "4px", px: 4 },
    { name: "sm", val: activeDS?.tokens?.spacing?.sm?.$value || "8px", px: 8 },
    { name: "md", val: activeDS?.tokens?.spacing?.md?.$value || "16px", px: 16 },
    { name: "lg", val: activeDS?.tokens?.spacing?.lg?.$value || "24px", px: 24 },
    { name: "xl", val: activeDS?.tokens?.spacing?.xl?.$value || "32px", px: 32 },
  ];

  // Radius tokens
  const radiusTokens = [
    { name: "sm", val: activeDS?.tokens?.border?.radius?.sm?.$value || "4px", cls: "rounded-xs" },
    { name: "md", val: activeDS?.tokens?.border?.radius?.md?.$value || "8px", cls: "rounded-md" },
    { name: "lg", val: activeDS?.tokens?.border?.radius?.lg?.$value || "12px", cls: "rounded-xl" },
    { name: "full", val: activeDS?.tokens?.border?.radius?.full?.$value || "9999px", cls: "rounded-full" },
  ];

  const typographyTokens = activeDS?.tokens?.typography?.font_family || {
    sans: { $value: "'Inter', system-ui, -apple-system, sans-serif" },
    mono: { $value: "'JetBrains Mono', monospace" },
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Header Banner */}
      <div className="p-5 rounded-2xl bg-card border border-border md-elevation-1">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-foreground tracking-tight">
                Design Systems & Multi-Client Surfaces
              </h2>
              <Badge variant="outline" className="text-[10px] font-mono border-primary/40 text-primary">
                W3C DTCG
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              Living design tokens, multi-client experience surfaces (`CLT-*`), UI components (`UIC-*`), screen state machines (`SCR-*`), and omnichannel flows (`FLOW-*`).
            </p>
          </div>

          {/* Quick Metrics */}
          <div className="flex items-center gap-3 shrink-0 text-xs">
            <div className="flex items-center gap-1.5 px-3 py-1 rounded-xl bg-muted/40 border border-border/60">
              <Palette className="h-3.5 w-3.5 text-primary" />
              <span className="font-semibold text-foreground">{design_systems.length}</span>
              <span className="text-[11px] text-muted-foreground">DS</span>
            </div>
            <div className="flex items-center gap-1.5 px-3 py-1 rounded-xl bg-muted/40 border border-border/60">
              <Box className="h-3.5 w-3.5 text-indigo-400" />
              <span className="font-semibold text-foreground">{ui_components.length}</span>
              <span className="text-[11px] text-muted-foreground">Components</span>
            </div>
            <div className="flex items-center gap-1.5 px-3 py-1 rounded-xl bg-muted/40 border border-border/60">
              <LayoutGrid className="h-3.5 w-3.5 text-emerald-400" />
              <span className="font-semibold text-foreground">{screens.length}</span>
              <span className="text-[11px] text-muted-foreground">Screens</span>
            </div>
            <div className="flex items-center gap-1.5 px-3 py-1 rounded-xl bg-muted/40 border border-border/60">
              <Workflow className="h-3.5 w-3.5 text-purple-400" />
              <span className="font-semibold text-foreground">{flows.length}</span>
              <span className="text-[11px] text-muted-foreground">Flows</span>
            </div>
          </div>
        </div>

        {/* View Sub-Tabs */}
        <div className="flex items-center gap-1 mt-4 pt-3 border-t border-border/60 overflow-x-auto no-scrollbar">
          {[
            { id: "all", label: "Full Overview", icon: LayoutGrid },
            { id: "tokens", label: "W3C Design Tokens", icon: Palette },
            { id: "clients", label: "Client Surfaces", icon: Laptop },
            { id: "components", label: `UI Components (${ui_components.length})`, icon: Box },
            { id: "screens", label: `Screens & States (${screens.length})`, icon: Sliders },
            { id: "flows", label: `User Flows (${flows.length})`, icon: Workflow },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id as DesignSubTab)}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium whitespace-nowrap transition-all cursor-pointer",
                  isActive
                    ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* SECTION 1: W3C DTCG Token Studio */}
      {(activeTab === "all" || activeTab === "tokens") && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
              <Palette className="h-4 w-4 text-primary" />
              W3C DTCG Living Design Tokens Studio (`tokens.json`)
            </h3>
            {activeDS && (
              <div className="flex items-center gap-2 text-xs">
                <span className="font-mono text-muted-foreground">{activeDS.id}</span>
                <span className="text-border">|</span>
                <span className="text-muted-foreground font-medium">{activeDS.brand_name || activeDS.title}</span>
              </div>
            )}
          </div>

          <Card className="p-5 space-y-6">
            {/* Preserved id="tokenSwatchesGrid" for tests & automated hooks */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5 text-primary" />
                  Primary & Feedback Palettes
                </h4>
                <span className="text-[11px] text-muted-foreground">Click hex or token to copy</span>
              </div>
              <div
                id="tokenSwatchesGrid"
                className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3"
              >
                {allColorSwatches.map((t, idx) => (
                  <div
                    key={idx}
                    onClick={() => handleCopy(t.hex, t.path)}
                    className="group space-y-2 p-2.5 rounded-xl border border-border/70 bg-muted/20 hover:bg-muted/50 hover:border-primary/50 transition-all flex flex-col items-center text-center cursor-pointer relative"
                    title={t.desc || t.path}
                  >
                    <div
                      className="h-10 w-full rounded-lg md-elevation-1 border border-white/10 group-hover:scale-105 transition-transform"
                      style={{ backgroundColor: t.hex }}
                    />
                    <div className="flex items-center gap-1">
                      <span className="font-mono text-[10px] font-bold text-foreground">
                        {t.hex}
                      </span>
                      {copiedToken === t.path ? (
                        <Check className="h-3 w-3 text-emerald-500" />
                      ) : (
                        <Copy className="h-2.5 w-2.5 opacity-0 group-hover:opacity-60 transition-opacity" />
                      )}
                    </div>
                    <span className="text-[10px] text-muted-foreground line-clamp-1">
                      {t.label}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Extended Surface & Border Tokens */}
            <div className="pt-4 border-t border-border/60">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider mb-3">
                Surfaces & Semantic Themes
              </h4>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                {surfaceColors.map((s, idx) => (
                  <div
                    key={idx}
                    onClick={() => handleCopy(s.hex, s.path)}
                    className="p-3 rounded-xl border border-border/70 bg-card hover:border-primary/40 transition-all flex flex-col items-center text-center cursor-pointer group"
                  >
                    <div
                      className="h-8 w-full rounded-md border border-border/60 mb-2"
                      style={{ backgroundColor: s.hex }}
                    />
                    <span className="font-mono text-[10px] font-semibold text-foreground">
                      {s.hex}
                    </span>
                    <span className="text-[10px] text-muted-foreground line-clamp-1 mt-0.5">
                      {s.label}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Spacing & Radius Visual Studio */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4 border-t border-border/60">
              {/* Spacing Scale */}
              <div className="space-y-3">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Sliders className="h-3.5 w-3.5 text-primary" />
                  Spacing Scale (`tokens.spacing`)
                </h4>
                <div className="space-y-2">
                  {spacingTokens.map((sp) => (
                    <div
                      key={sp.name}
                      onClick={() => handleCopy(sp.val, `spacing.${sp.name}`)}
                      className="flex items-center justify-between p-2 rounded-xl bg-muted/20 border border-border/60 hover:bg-muted/40 transition-colors cursor-pointer text-xs"
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-primary w-8">{sp.name}</span>
                        <span className="font-mono text-[11px] text-muted-foreground w-12">{sp.val}</span>
                      </div>
                      <div className="flex-1 max-w-[160px] flex items-center justify-start px-2">
                        <div
                          className="h-3 bg-primary/60 rounded-xs"
                          style={{ width: `${Math.min(140, sp.px * 3.5)}px` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-muted-foreground">
                        {sp.px}px
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Border Radius Tokens */}
              <div className="space-y-3">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Box className="h-3.5 w-3.5 text-indigo-400" />
                  Border Radius Curvature (`tokens.border.radius`)
                </h4>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                  {radiusTokens.map((rad) => (
                    <div
                      key={rad.name}
                      onClick={() => handleCopy(rad.val, `border.radius.${rad.name}`)}
                      className="p-3 rounded-xl bg-muted/20 border border-border/60 hover:bg-muted/40 transition-colors cursor-pointer flex flex-col items-center text-center gap-2"
                    >
                      <div
                        className={cn(
                          "h-12 w-12 border-2 border-primary bg-primary/10 flex items-center justify-center text-[10px] font-mono font-bold text-primary",
                          rad.cls
                        )}
                      >
                        {rad.val}
                      </div>
                      <span className="font-mono text-xs font-bold text-foreground">
                        {rad.name}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Typography Tokens */}
            <div className="pt-4 border-t border-border/60 space-y-3">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                <FileText className="h-3.5 w-3.5 text-emerald-400" />
                Typography System (`tokens.typography.font_family`)
              </h4>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="p-3.5 rounded-xl bg-muted/20 border border-border/60 space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-foreground">Sans-Serif Font Family</span>
                    <Badge variant="outline" className="text-[10px] font-mono">sans</Badge>
                  </div>
                  <p className="font-mono text-[11px] text-primary">{typographyTokens.sans?.$value}</p>
                  <p className="text-xs text-muted-foreground pt-1" style={{ fontFamily: typographyTokens.sans?.$value }}>
                    The quick brown fox jumps over the lazy dog. 0123456789
                  </p>
                </div>
                <div className="p-3.5 rounded-xl bg-muted/20 border border-border/60 space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-foreground">Monospace Code Family</span>
                    <Badge variant="outline" className="text-[10px] font-mono">mono</Badge>
                  </div>
                  <p className="font-mono text-[11px] text-primary">{typographyTokens.mono?.$value}</p>
                  <p className="text-xs text-muted-foreground font-mono pt-1" style={{ fontFamily: typographyTokens.mono?.$value }}>
                    const eidosPortal = resolveLivingSpec("DS-CORE");
                  </p>
                </div>
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* SECTION 2: Client Applications & Experience Surfaces */}
      {(activeTab === "all" || activeTab === "clients") && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
              <Laptop className="h-4 w-4 text-indigo-400" />
              Client Applications & Experience Surfaces
            </h3>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground font-medium">
                Select Client:
              </span>
              <select
                id="designClientSelect"
                value={selectedClientId}
                onChange={(e) => handleDesignClientSwitch(e.target.value)}
                className="bg-card border border-border text-xs rounded-xl px-3 py-1.5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary cursor-pointer"
              >
                {clients.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.id} ({c.title || c.name || c.id})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Active Client Details Card */}
          {activeClient && (
            <Card id="activeClientDetails" className="p-5 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <span className="font-mono text-sm font-bold text-primary">
                    {activeClient.id}
                  </span>
                  <Badge variant="outline" className="capitalize text-xs font-medium">
                    {activeClient.platform || activeClient.client_type || "web"}
                  </Badge>
                  {activeClient.container && (
                    <Badge variant="secondary" className="font-mono text-[11px]">
                      {activeClient.container}
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-muted-foreground">Bound Design System:</span>
                  <span className="font-mono font-bold text-primary">
                    {activeClient.design_system || "DS-CORE"}
                  </span>
                </div>
              </div>

              <div>
                <h4 className="text-sm font-bold text-foreground">
                  {activeClient.title || activeClient.name || activeClient.id}
                </h4>
                <p className="text-xs text-muted-foreground leading-relaxed mt-1">
                  {activeClient.description || "Responsive living client surface implementation."}
                </p>
              </div>

              {/* Supported Screens & Bound Capabilities */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-3 border-t border-border/60 text-xs">
                <div className="space-y-1.5">
                  <span className="text-[11px] font-bold text-foreground uppercase tracking-wider block">
                    Supported Screens ({activeClient.screens?.length || activeClient.supported_screens?.length || screens.length})
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {(activeClient.screens || activeClient.supported_screens || screens.map((s) => s.id)).map((scr, idx) => {
                      const scrName = typeof scr === "string" ? scr.split("/").pop()?.replace(".adoc", "").toUpperCase() : scr;
                      return (
                        <span
                          key={idx}
                          className="px-2 py-0.5 rounded-lg bg-muted/40 border border-border/60 font-mono text-[10px] text-foreground"
                        >
                          {scrName}
                        </span>
                      );
                    })}
                  </div>
                </div>

                <div className="space-y-1.5">
                  <span className="text-[11px] font-bold text-foreground uppercase tracking-wider block">
                    Bound Capabilities ({activeClient.capabilities?.length || 0})
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {(activeClient.capabilities || []).map((cap, idx) => {
                      const capName = cap.split("/").pop()?.replace(".adoc", "");
                      return (
                        <span
                          key={idx}
                          className="px-2 py-0.5 rounded-lg bg-primary/10 border border-primary/20 font-mono text-[10px] text-primary"
                        >
                          {capName}
                        </span>
                      );
                    })}
                  </div>
                </div>
              </div>

              {/* Client Spec Inspector Trigger */}
              {activeClient.body && (
                <div className="pt-2 flex justify-end">
                  <button
                    type="button"
                    onClick={() =>
                      setInspectingItem({
                        title: activeClient.title || activeClient.id,
                        subtitle: `${activeClient.id} • ${activeClient.platform || "web"}`,
                        body: activeClient.body,
                      })
                    }
                    className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline cursor-pointer font-medium"
                  >
                    <FileText className="h-3.5 w-3.5" />
                    <span>View Living Client Spec Body</span>
                  </button>
                </div>
              )}
            </Card>
          )}
        </div>
      )}

      {/* SECTION 3: Living UI Components Catalog */}
      {(activeTab === "all" || activeTab === "components") && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
              <Box className="h-4 w-4 text-primary" />
              Living UI Components Catalog (`UIC-*`)
            </h3>
            <span className="text-xs text-muted-foreground">
              {ui_components.length} components registered in `{activeDS?.id || "DS-CORE"}`
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {ui_components.map((comp) => {
              const currentVariant =
                selectedVariantByComp[comp.id] ||
                comp.variants?.[0]?.name ||
                "default";
              const activeVariantObj = comp.variants?.find((v) => v.name === currentVariant);

              return (
                <Card key={comp.id} className="p-4 space-y-3 flex flex-col justify-between">
                  <div className="space-y-2.5">
                    {/* Header Badges */}
                    <div className="flex items-center justify-between gap-1">
                      <span className="font-mono text-xs font-bold text-primary">
                        {comp.id}
                      </span>
                      <div className="flex items-center gap-1">
                        <Badge variant="outline" className="text-[10px] uppercase font-mono">
                          {comp.category || comp.component_type || "component"}
                        </Badge>
                        {comp.accessibility?.wcag_level && (
                          <Badge variant="secondary" className="text-[10px] font-mono">
                            WCAG {comp.accessibility.wcag_level}
                          </Badge>
                        )}
                      </div>
                    </div>

                    {/* Title & Description */}
                    <div>
                      <h4 className="text-xs font-bold text-foreground">
                        {comp.title || comp.name || comp.id}
                      </h4>
                      <p className="text-[11px] text-muted-foreground line-clamp-2 mt-0.5">
                        {comp.description || activeVariantObj?.description || "Living reusable UI component spec."}
                      </p>
                    </div>

                    {/* Interactive Variants Selector */}
                    {comp.variants && comp.variants.length > 0 && (
                      <div className="space-y-1.5 pt-2 border-t border-border/60">
                        <span className="text-[10px] font-bold text-foreground uppercase tracking-wider block">
                          Variants ({comp.variants.length})
                        </span>
                        <div className="flex flex-wrap gap-1">
                          {comp.variants.map((v) => {
                            const isSelected = currentVariant === v.name;
                            return (
                              <button
                                key={v.name}
                                type="button"
                                onClick={() =>
                                  setSelectedVariantByComp((prev) => ({
                                    ...prev,
                                    [comp.id]: v.name,
                                  }))
                                }
                                className={cn(
                                  "px-2 py-0.5 rounded-lg text-[10px] font-mono transition-colors cursor-pointer",
                                  isSelected
                                    ? "bg-primary text-primary-foreground font-semibold"
                                    : "bg-muted/40 text-muted-foreground hover:text-foreground"
                                )}
                              >
                                {v.name}
                              </button>
                            );
                          })}
                        </div>
                        {activeVariantObj?.description && (
                          <p className="text-[10px] text-muted-foreground italic bg-muted/20 p-1.5 rounded-md">
                            {activeVariantObj.description}
                          </p>
                        )}
                      </div>
                    )}

                    {/* States List */}
                    {comp.states && comp.states.length > 0 && (
                      <div className="space-y-1">
                        <span className="text-[10px] font-bold text-foreground uppercase tracking-wider block">
                          Declared States
                        </span>
                        <div className="flex flex-wrap gap-1">
                          {comp.states.map((st) => (
                            <span
                              key={st}
                              className="px-1.5 py-0.5 rounded-md bg-muted/30 border border-border/60 text-[9px] font-mono text-muted-foreground"
                            >
                              :{st}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Accessibility & Keyboard Navigation */}
                    {comp.accessibility?.keyboard_nav && (
                      <div className="text-[10px] text-muted-foreground pt-1.5 border-t border-border/50">
                        <span className="font-semibold text-foreground">Keyboard: </span>
                        {comp.accessibility.keyboard_nav}
                      </div>
                    )}
                  </div>

                  {/* Test Traceability Footer */}
                  <div className="pt-2 border-t border-border/60 flex items-center justify-between text-[10px]">
                    {comp.test_bindings?.unit_test ? (
                      <span
                        className="font-mono text-emerald-500 truncate max-w-[190px]"
                        title={comp.test_bindings.unit_test}
                      >
                        ✓ {comp.test_bindings.unit_test.split("::").pop()}
                      </span>
                    ) : (
                      <span className="text-muted-foreground font-mono">DS-CORE</span>
                    )}

                    {comp.body && (
                      <button
                        type="button"
                        onClick={() =>
                          setInspectingItem({
                            title: comp.title || comp.id,
                            subtitle: `${comp.id} • ${comp.category || "UI Component"}`,
                            body: comp.body,
                          })
                        }
                        className="text-primary hover:underline font-medium cursor-pointer"
                      >
                        Spec Doc →
                      </button>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* SECTION 4: Living Screens & State Machines */}
      {(activeTab === "all" || activeTab === "screens") && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
              <Sliders className="h-4 w-4 text-emerald-400" />
              Living Screen Architectures & State Machines (`SCR-*`)
            </h3>
            <span className="text-xs text-muted-foreground">
              {screens.length} living screens declared
            </span>
          </div>

          {/* Screen Pills Selector */}
          <div className="flex items-center gap-2 overflow-x-auto no-scrollbar pb-1">
            {screens.map((scr) => {
              const isSelected = scr.id === selectedScreenId;
              return (
                <button
                  key={scr.id}
                  type="button"
                  onClick={() => setSelectedScreenId(scr.id)}
                  className={cn(
                    "flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-mono transition-all cursor-pointer whitespace-nowrap",
                    isSelected
                      ? "bg-primary text-primary-foreground md-elevation-1 font-bold"
                      : "bg-card border border-border/70 text-muted-foreground hover:text-foreground"
                  )}
                >
                  <span>{scr.id}</span>
                  <span className={cn("text-[10px] px-1.5 py-0.2 rounded-md font-sans", isSelected ? "bg-white/20 text-white" : "bg-muted text-muted-foreground")}>
                    {scr.route || "/"}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Active Screen Details & Mermaid State Machine */}
          {activeScreen && (
            <Card className="p-5 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-bold text-primary">
                      {activeScreen.id}
                    </span>
                    <Badge variant="outline" className="font-mono text-xs text-emerald-400 border-emerald-400/30">
                      Route: {activeScreen.route || "/"}
                    </Badge>
                  </div>
                  <h4 className="text-sm font-bold text-foreground">
                    {activeScreen.title || activeScreen.name}
                  </h4>
                </div>
                <div className="flex items-center gap-2">
                  {activeScreen.body && (
                    <button
                      type="button"
                      onClick={() =>
                        setInspectingItem({
                          title: activeScreen.title || activeScreen.id,
                          subtitle: `${activeScreen.id} • Route: ${activeScreen.route}`,
                          body: activeScreen.body,
                        })
                      }
                      className="px-2.5 py-1 rounded-lg bg-muted/40 border border-border text-xs text-primary hover:underline cursor-pointer flex items-center gap-1 font-medium"
                    >
                      <FileText className="h-3.5 w-3.5" />
                      <span>Inspect Screen Spec</span>
                    </button>
                  )}
                </div>
              </div>

              {/* Layout Slots Table */}
              {activeScreen.slots && Object.keys(activeScreen.slots).length > 0 && (
                <div className="space-y-2 pt-2 border-t border-border/60">
                  <span className="text-[11px] font-bold text-foreground uppercase tracking-wider block">
                    Layout Slots & Bound UI Components
                  </span>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
                    {Object.entries(activeScreen.slots).map(([slot, compId]) => (
                      <div
                        key={slot}
                        className="p-2 rounded-lg bg-muted/20 border border-border/60 text-xs space-y-0.5"
                      >
                        <span className="text-[10px] text-muted-foreground font-mono block truncate">
                          {slot}
                        </span>
                        <span className="font-mono text-[11px] font-bold text-primary block truncate">
                          {compId}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Declared Screen States */}
              {Array.isArray(activeScreen.states) && activeScreen.states.length > 0 && (
                <div className="space-y-2 pt-2 border-t border-border/60">
                  <span className="text-[11px] font-bold text-foreground uppercase tracking-wider block">
                    Declared Screen States ({activeScreen.states.length})
                  </span>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                    {activeScreen.states.map((st: any, idx) => {
                      const name = typeof st === "string" ? st : st.name;
                      const desc = typeof st === "string" ? null : st.description;
                      return (
                        <div
                          key={idx}
                          className="p-2 rounded-lg bg-muted/20 border border-border/60 text-xs space-y-0.5"
                        >
                          <span className="font-mono font-semibold text-foreground">
                            :{name}
                          </span>
                          {desc && (
                            <p className="text-[11px] text-muted-foreground line-clamp-2">
                              {desc}
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Mermaid State Machine Diagram */}
              {activeScreen.mermaid && (
                <div className="space-y-2 pt-2 border-t border-border/60">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <Workflow className="h-3.5 w-3.5 text-primary" />
                      Visual State Transition Machine (`mermaid: stateDiagram-v2`)
                    </span>
                    <span className="text-[10px] text-muted-foreground font-mono">
                      Auto-zoom and pan enabled
                    </span>
                  </div>
                  <MermaidDiagram
                    id={`screen-${activeScreen.id}`}
                    code={activeScreen.mermaid}
                    darkMode={darkMode}
                    minHeight="280px"
                  />
                </div>
              )}
            </Card>
          )}
        </div>
      )}

      {/* SECTION 5: Interaction Flows & Multi-Client Journeys */}
      {(activeTab === "all" || activeTab === "flows") && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
              <Workflow className="h-4 w-4 text-purple-400" />
              Interaction Flows & Omnichannel User Journeys (`FLOW-*`)
            </h3>
            <span className="text-xs text-muted-foreground">
              {flows.length} declared user journeys
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Client Specific Flows Panel */}
            <div id="clientFlowsPanel" className="space-y-3">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                <Workflow className="h-4 w-4 text-primary" />
                Client Specific Flows ({clientFlows.length})
              </h4>
              <div className="space-y-3">
                {clientFlows.length === 0 ? (
                  <div className="p-4 rounded-xl border border-dashed border-border/80 text-xs text-muted-foreground text-center">
                    No specific flows declared for this client.
                  </div>
                ) : (
                  clientFlows.map((flow) => (
                    <Card key={flow.id} className="p-4 space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-xs font-bold text-primary">
                          {flow.id}
                        </span>
                        <span className="text-[10px] text-muted-foreground font-mono">
                          Trigger: {flow.trigger || flow.flow_type || "User Initiated"}
                        </span>
                      </div>
                      <div>
                        <h5 className="text-xs font-semibold text-foreground">
                          {flow.title || flow.name}
                        </h5>
                        <p className="text-[11px] text-muted-foreground mt-0.5">
                          {flow.description || "Living client interaction flow."}
                        </p>
                      </div>

                      {/* Steps Timeline */}
                      {flow.steps && flow.steps.length > 0 && (
                        <div className="space-y-1.5 pt-2 border-t border-border/60">
                          <span className="text-[10px] font-bold text-foreground uppercase tracking-wider block">
                            Journey Sequence ({flow.steps.length} steps)
                          </span>
                          <div className="space-y-1.5">
                            {flow.steps.map((st) => (
                              <div
                                key={st.step}
                                className="flex items-start gap-2 text-xs p-2 rounded-lg bg-muted/20 border border-border/50"
                              >
                                <span className="h-4 w-4 rounded-full bg-primary/20 text-primary font-bold text-[10px] flex items-center justify-center shrink-0 mt-0.5">
                                  {st.step}
                                </span>
                                <div className="space-y-0.5">
                                  <span className="font-semibold text-foreground text-[11px]">
                                    {st.title || st.action}
                                  </span>
                                  {st.action && st.title && (
                                    <p className="text-[10px] text-muted-foreground">
                                      {st.action}
                                    </p>
                                  )}
                                  {st.outcome && (
                                    <p className="text-[10px] text-emerald-400">
                                      ↳ Outcome: {st.outcome}
                                    </p>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Mermaid Flow Diagram */}
                      {flow.mermaid && (
                        <div className="pt-2 border-t border-border/60 space-y-1">
                          <span className="text-[10px] font-bold text-foreground uppercase tracking-wider block">
                            Interactive Flowchart
                          </span>
                          <MermaidDiagram
                            id={`flow-${flow.id}`}
                            code={flow.mermaid}
                            darkMode={darkMode}
                            minHeight="260px"
                          />
                        </div>
                      )}
                    </Card>
                  ))
                )}
              </div>
            </div>

            {/* Omnichannel Flows Panel */}
            <div id="omnichannelFlowsPanel" className="space-y-3">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                <Workflow className="h-4 w-4 text-purple-400" />
                Omnichannel & Lifecycle Flows ({flows.length})
              </h4>
              <div className="space-y-3">
                {flows.map((flow) => (
                  <Card key={flow.id} className="p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-bold text-purple-400">
                        {flow.id}
                      </span>
                      <Badge variant="outline" className="text-[10px] font-mono capitalize">
                        {flow.client || flow.flow_type || "omnichannel"}
                      </Badge>
                    </div>
                    <div>
                      <h5 className="text-xs font-semibold text-foreground">
                        {flow.title || flow.name}
                      </h5>
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        {flow.description || "Omnichannel developer interaction flow."}
                      </p>
                    </div>

                    {/* Steps Timeline */}
                    {flow.steps && flow.steps.length > 0 && (
                      <div className="space-y-1.5 pt-2 border-t border-border/60">
                        <span className="text-[10px] font-bold text-foreground uppercase tracking-wider block">
                          Sequence Steps
                        </span>
                        <div className="space-y-1.5">
                          {flow.steps.map((st) => (
                            <div
                              key={st.step}
                              className="flex items-start gap-2 text-xs p-2 rounded-lg bg-muted/20 border border-border/50"
                            >
                              <span className="h-4 w-4 rounded-full bg-purple-500/20 text-purple-400 font-bold text-[10px] flex items-center justify-center shrink-0 mt-0.5">
                                {st.step}
                              </span>
                              <div className="space-y-0.5">
                                <span className="font-semibold text-foreground text-[11px]">
                                  {st.title || st.action}
                                </span>
                                {st.action && st.title && (
                                  <p className="text-[10px] text-muted-foreground font-mono">
                                    {st.action}
                                  </p>
                                )}
                                {st.outcome && (
                                  <p className="text-[10px] text-emerald-400">
                                    ↳ {st.outcome}
                                  </p>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Mermaid Sequence/Flow Diagram */}
                    {flow.mermaid && (
                      <div className="pt-2 border-t border-border/60 space-y-1">
                        <span className="text-[10px] font-bold text-foreground uppercase tracking-wider block">
                          Visual Sequence Diagram
                        </span>
                        <MermaidDiagram
                          id={`omni-flow-${flow.id}`}
                          code={flow.mermaid}
                          darkMode={darkMode}
                          minHeight="320px"
                        />
                      </div>
                    )}
                  </Card>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Living Spec Inspector Drawer Sheet */}
      <Sheet
        open={Boolean(inspectingItem)}
        onOpenChange={(open) => !open && setInspectingItem(null)}
        title={inspectingItem?.title}
        subtitle={inspectingItem?.subtitle}
      >
        <div className="p-4 space-y-3 overflow-y-auto max-h-[calc(100vh-80px)]">
          <pre className="text-xs font-mono bg-muted/40 p-3 rounded-xl border border-border text-foreground whitespace-pre-wrap leading-relaxed">
            {inspectingItem?.body}
          </pre>
        </div>
      </Sheet>
    </div>
  );
}
