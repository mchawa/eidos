import * as React from "react";
import { Briefcase, UserCheck, ShieldCheck, CheckCircle2, ArrowRight } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { PortalData } from "@/types";

export interface BusinessViewProps {
  data: PortalData;
}

export function BusinessView({ data }: BusinessViewProps) {
  const { domains, capabilities, personas, policies } = data;

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Top Description */}
      <div className="p-4 rounded-2xl bg-card border border-border md-elevation-1">
        <h2 className="text-base font-bold text-foreground tracking-tight">
          Bounded Product Domains & Capabilities
        </h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          DDD Strategic Design: Domains (`DOM-*`), product capabilities (`CAP-*`), and user personas (`PER-*`).
        </p>
      </div>

      {/* Domains & Capabilities Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {domains.map((dom) => {
          const domCaps = capabilities.filter(
            (c) => c.domain === dom.id || dom.capabilities?.includes(c.id)
          );
          return (
            <Card key={dom.id} className="p-5 space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-bold text-primary">
                  {dom.id}
                </span>
                <Badge variant="outline">Domain</Badge>
              </div>
              <h3 className="text-sm font-bold text-foreground">{dom.name}</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">
                {dom.mission}
              </p>

              {/* Bound Capabilities */}
              <div className="space-y-2 pt-2 border-t border-border/60">
                <h4 className="text-[11px] font-bold text-foreground uppercase tracking-wider">
                  Bound Capabilities ({domCaps.length})
                </h4>
                <div className="space-y-2">
                  {domCaps.map((cap) => (
                    <div
                      key={cap.id}
                      className="p-2.5 rounded-xl border border-border/70 bg-muted/20 text-xs space-y-1"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-semibold text-primary">
                          {cap.id}
                        </span>
                        <span className="font-medium text-foreground">{cap.name}</span>
                      </div>
                      <p className="text-[11px] text-muted-foreground line-clamp-2">
                        {cap.summary}
                      </p>
                      {cap.invariants && cap.invariants.length > 0 && (
                        <div className="flex items-center gap-1 text-[10px] text-emerald-500 font-mono pt-1">
                          <CheckCircle2 className="h-3 w-3" />
                          <span>{cap.invariants.length} capability invariant(s)</span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      {/* Personas Row */}
      <div className="space-y-3 pt-2">
        <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
          <UserCheck className="h-4 w-4 text-purple-400" />
          Target Actor Personas ({personas.length})
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {personas.map((per) => (
            <Card key={per.id} className="p-4 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-bold text-purple-400">
                  {per.id}
                </span>
                <Badge variant="outline">{per.archetype}</Badge>
              </div>
              <h4 className="text-xs font-semibold text-foreground">{per.name}</h4>
              <p className="text-[11px] text-muted-foreground">{per.role}</p>
              {per.goals && per.goals.length > 0 && (
                <ul className="text-[11px] text-muted-foreground list-disc pl-4 space-y-0.5 pt-1">
                  {per.goals.slice(0, 2).map((g, idx) => (
                    <li key={idx} className="line-clamp-1">
                      {g}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
