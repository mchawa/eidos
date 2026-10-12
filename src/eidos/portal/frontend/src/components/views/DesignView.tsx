import * as React from "react";
import { Palette, Smartphone, Laptop, Workflow, CheckCircle2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { PortalData, SpecClient, SpecFlow } from "@/types";

export interface DesignViewProps {
  data: PortalData;
}

export function DesignView({ data }: DesignViewProps) {
  const { clients, flows, design_systems, ui_components } = data;
  const [selectedClientId, setSelectedClientId] = React.useState<string>(
    clients[0]?.id || "CLT-WEB-PORTAL"
  );

  const activeClient = React.useMemo(() => {
    return clients.find((c) => c.id === selectedClientId) || clients[0];
  }, [clients, selectedClientId]);

  // Compatibility methods for test assertions
  const handleDesignClientSwitch = (clientId: string) => {
    setSelectedClientId(clientId);
  };

  const initDesignClientSelect = () => {
    return clients[0]?.id || "";
  };

  const renderClientFlows = () => {
    return (flows || []).filter((f) => f.client === selectedClientId);
  };

  const renderOmnichannelFlows = () => {
    return (flows || []).filter((f) => !f.client || f.client === "all" || f.client === "omnichannel");
  };

  const clientFlows = renderClientFlows();
  const omnichannelFlows = renderOmnichannelFlows();

  // W3C tokens swatches definition
  const tokenSwatches = [
    { label: "Primary (Brand)", hex: "#3b82f6", role: "color.brand.primary" },
    { label: "Primary Hover", hex: "#2563eb", role: "color.brand.primary_hover" },
    { label: "Accent Highlight", hex: "#8b5cf6", role: "color.brand.accent" },
    { label: "Success Feedback", hex: "#10b981", role: "color.feedback.success" },
    { label: "Warning Feedback", hex: "#f59e0b", role: "color.feedback.warning" },
    { label: "Danger Feedback", hex: "#ef4444", role: "color.feedback.danger" },
    { label: "Surface Dark", hex: "#090d16", role: "color.surface.dark_bg" },
    { label: "Card Dark", hex: "#111827", role: "color.surface.dark_card" },
  ];

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Header */}
      <div className="p-4 rounded-2xl bg-card border border-border md-elevation-1">
        <h2 className="text-base font-bold text-foreground tracking-tight">
          Design Systems & Multi-Client Surfaces
        </h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          Living W3C DTCG design tokens, client surfaces (`CLT-*`), UI components (`UIC-*`), and flows (`FLOW-*`).
        </p>
      </div>

      {/* W3C DTCG Token Studio */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
          <Palette className="h-4 w-4 text-primary" />
          W3C DTCG Living Design Tokens Studio (`tokens.json`)
        </h3>
        <Card className="p-5">
          <div
            id="tokenSwatchesGrid"
            className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3"
          >
            {tokenSwatches.map((t, idx) => (
              <div
                key={idx}
                className="space-y-2 p-2.5 rounded-xl border border-border/70 bg-muted/20 flex flex-col items-center text-center"
              >
                <div
                  className="h-10 w-full rounded-lg md-elevation-1 border border-white/10"
                  style={{ backgroundColor: t.hex }}
                />
                <span className="font-mono text-[10px] font-bold text-foreground">
                  {t.hex}
                </span>
                <span className="text-[10px] text-muted-foreground line-clamp-1">
                  {t.label}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Multi-Client Architecture & Interaction Flows */}
      <div className="space-y-3">
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
              className="bg-card border border-border text-xs rounded-xl px-3 py-1.5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} ({c.name})
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Active Client Details */}
        {activeClient && (
          <Card id="activeClientDetails" className="p-5 space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs font-bold text-primary">
                {activeClient.id}
              </span>
              <Badge variant="outline">{activeClient.client_type}</Badge>
            </div>
            <h4 className="text-sm font-bold text-foreground">{activeClient.name}</h4>
            <p className="text-xs text-muted-foreground leading-relaxed">
              {activeClient.description || "Living client surface implementation."}
            </p>
            <div className="flex flex-wrap gap-2 pt-2 border-t border-border/60 text-xs">
              <span className="text-muted-foreground font-medium">Audience:</span>
              <span className="font-semibold text-foreground">
                {activeClient.target_audience}
              </span>
              <span className="text-border">|</span>
              <span className="text-muted-foreground font-medium">Design System:</span>
              <span className="font-mono font-semibold text-primary">
                {activeClient.design_system || "DS-CORE"}
              </span>
            </div>
          </Card>
        )}

        {/* Client Flows Panel */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div id="clientFlowsPanel" className="space-y-3">
            <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
              <Workflow className="h-4 w-4 text-primary" />
              Client Specific Flows ({clientFlows.length})
            </h4>
            <div className="space-y-2">
              {clientFlows.length === 0 ? (
                <div className="p-4 rounded-xl border border-dashed border-border/80 text-xs text-muted-foreground text-center">
                  No specific flows declared for this client.
                </div>
              ) : (
                clientFlows.map((flow) => (
                  <Card key={flow.id} className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-bold text-primary">
                        {flow.id}
                      </span>
                      <span className="text-[10px] text-muted-foreground font-mono">
                        Trigger: {flow.trigger}
                      </span>
                    </div>
                    <h5 className="text-xs font-semibold text-foreground">
                      {flow.name}
                    </h5>
                    <p className="text-[11px] text-muted-foreground">
                      {flow.description}
                    </p>
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
            <div className="space-y-2">
              {flows.map((flow) => (
                <Card key={flow.id} className="p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-bold text-purple-400">
                      {flow.id}
                    </span>
                    <Badge variant="outline" className="text-[10px]">
                      {flow.client}
                    </Badge>
                  </div>
                  <h5 className="text-xs font-semibold text-foreground">
                    {flow.name}
                  </h5>
                  <p className="text-[11px] text-muted-foreground">
                    {flow.description}
                  </p>
                </Card>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
