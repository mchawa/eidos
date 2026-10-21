import * as React from "react";
import { getConfiguredMermaid } from "@/lib/mermaid";
import {
  ZoomIn,
  ZoomOut,
  RotateCcw,
  ArrowUp,
  Box,
  Layers,
  FileCode,
  ShieldCheck,
  ChevronRight,
  Maximize2,
  Expand,
  Shrink,
  Move,
  Sparkles,
  BookOpen,
  Crosshair,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { requestSpecNavigation } from "@/lib/specNav";
import { cn } from "@/lib/utils";
import type { PortalData, SpecComponent, SpecContract } from "@/types";

export interface ArchitectureViewProps {
  data: PortalData;
  darkMode?: boolean;
  initialComponentId?: string;
  initialComponentNonce?: number;
  onClearSelectedComponent?: () => void;
}

const getTierStyle = (tierId?: string) => {
  switch (tierId) {
    case "tier_presentation":
      return { bg: "bg-sky-500/15", text: "text-sky-400", border: "border-sky-500/30", dot: "bg-sky-400" };
    case "tier_service":
      return { bg: "bg-amber-500/15", text: "text-amber-400", border: "border-amber-500/30", dot: "bg-amber-400" };
    case "tier_extensibility":
      return { bg: "bg-purple-500/15", text: "text-purple-400", border: "border-purple-500/30", dot: "bg-purple-400" };
    case "tier_core":
      return { bg: "bg-indigo-500/15", text: "text-indigo-400", border: "border-indigo-500/30", dot: "bg-indigo-400" };
    case "tier_persistence":
      return { bg: "bg-teal-500/15", text: "text-teal-400", border: "border-teal-500/30", dot: "bg-teal-400" };
    default:
      return { bg: "bg-slate-500/15", text: "text-slate-400", border: "border-slate-500/30", dot: "bg-slate-400" };
  }
};

export function ArchitectureView({
  data,
  darkMode = true,
  initialComponentId,
  initialComponentNonce,
  onClearSelectedComponent,
}: ArchitectureViewProps) {
  const [level, setLevelState] = React.useState<1 | 2 | 3>(1);
  const [selectedContainerId, setSelectedContainerId] = React.useState<string>(
    data.components[0]?.id || "CNT-CORE"
  );
  const [zoom, setZoom] = React.useState<number>(1);
  const [pan, setPan] = React.useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isExpanded, setIsExpanded] = React.useState<boolean>(false);
  const [inspectorOpen, setInspectorOpen] = React.useState<boolean>(false);
  const [inspectedComponent, setInspectedComponent] = React.useState<SpecComponent | null>(null);
  const [focusedSubComponentId, setFocusedSubComponentId] = React.useState<string | null>(null);
  const [inspectedContract, setInspectedContract] = React.useState<SpecContract | null>(null);
  const [blastTargetId, setBlastTargetId] = React.useState<string>("");

  React.useEffect(() => {
    if (!initialComponentId) return;
    const ctrMatch = (data.contracts || []).find((ct) => ct.id === initialComponentId);
    if (ctrMatch) {
      setInspectedContract(ctrMatch);
      return;
    }
    const cntMatch = (data.components || []).find((c) => c.id === initialComponentId);
    if (cntMatch) {
      setFocusedSubComponentId(null);
      setInspectedComponent(cntMatch);
      setInspectorOpen(true);
      return;
    }
    // Check if initialComponentId is a Level 3 internal component (CMP-*)
    for (const c of data.components || []) {
      const sub = (c.internal_components || []).find((ic) => ic.id === initialComponentId);
      if (sub) {
        setSelectedContainerId(c.id);
        setLevelState(3);
        setFocusedSubComponentId(sub.id);
        setInspectedComponent(c);
        setInspectorOpen(true);
        return;
      }
    }
  }, [initialComponentId, initialComponentNonce, data.components, data.contracts]);

  // Client-side k-hop Blast Radius Simulator over data.edges
  const blastRadiusReport = React.useMemo(() => {
    if (!blastTargetId) return null;
    const edges = data.edges || [];
    const upstream1 = new Set<string>();
    const downstream1 = new Set<string>();
    const upstream2 = new Set<string>();
    const downstream2 = new Set<string>();

    edges.forEach((e) => {
      if (e.target === blastTargetId && e.source !== blastTargetId) {
        upstream1.add(e.source);
      }
      if (e.source === blastTargetId && e.target !== blastTargetId) {
        downstream1.add(e.target);
      }
    });

    edges.forEach((e) => {
      if (upstream1.has(e.target) && e.source !== blastTargetId && !upstream1.has(e.source)) {
        upstream2.add(e.source);
      }
      if (downstream1.has(e.source) && e.target !== blastTargetId && !downstream1.has(e.target)) {
        downstream2.add(e.target);
      }
    });

    const allImpacted = new Set<string>([
      blastTargetId,
      ...upstream1,
      ...downstream1,
      ...upstream2,
      ...downstream2,
    ]);

    return {
      targetId: blastTargetId,
      upstream1: Array.from(upstream1),
      downstream1: Array.from(downstream1),
      upstream2: Array.from(upstream2),
      downstream2: Array.from(downstream2),
      allImpacted,
    };
  }, [blastTargetId, data.edges]);

  // Toggle SVG node opacity/outline when Blast Radius Simulator is active (zero Mermaid re-render)
  React.useEffect(() => {
    if (!diagramRef.current) return;
    const nodes = diagramRef.current.querySelectorAll(".node");
    nodes.forEach((n) => {
      const el = n as HTMLElement;
      if (!blastRadiusReport) {
        el.style.opacity = "1";
        el.style.filter = "";
        return;
      }
      const txt = el.textContent || "";
      const nid = el.id || "";
      let isHit = false;
      blastRadiusReport.allImpacted.forEach((impId) => {
        if (
          txt.includes(impId) ||
          nid.includes(impId.replace(/-/g, "_")) ||
          nid.includes(impId)
        ) {
          isHit = true;
        }
      });
      el.style.opacity = isHit ? "1" : "0.28";
      el.style.filter =
        txt.includes(blastRadiusReport.targetId) ||
        nid.includes(blastRadiusReport.targetId.replace(/-/g, "_"))
          ? "drop-shadow(0 0 8px rgba(59,130,246,0.85))"
          : "";
    });
  }, [blastRadiusReport, level, selectedContainerId]);

  // Dynamic active tiers from components
  const activeTiers = React.useMemo(() => {
    const tierMap = new Map<string, { id: string; name: string }>();
    data.components.forEach((c) => {
      if (c.tier) {
        if (!tierMap.has(c.tier)) {
          tierMap.set(c.tier, { id: c.tier, name: c.tier_name || c.tier });
        }
      }
    });
    return Array.from(tierMap.values());
  }, [data.components]);

  const containerRef = React.useRef<HTMLDivElement>(null);
  const diagramRef = React.useRef<HTMLDivElement>(null);
  const isDraggingRef = React.useRef(false);
  const dragStartRef = React.useRef({ x: 0, y: 0, panX: 0, panY: 0 });
  const hasMovedRef = React.useRef(false);

  // Auto-fit diagram to compute optimal, crisp zoom level
  const autoFitDiagram = React.useCallback(() => {
    if (!diagramRef.current || !containerRef.current) return;
    const svgEl = diagramRef.current.querySelector("svg");
    if (!svgEl) return;

    let svgWidth = 0;
    let svgHeight = 0;

    if (svgEl.viewBox && svgEl.viewBox.baseVal && svgEl.viewBox.baseVal.width > 0) {
      svgWidth = svgEl.viewBox.baseVal.width;
      svgHeight = svgEl.viewBox.baseVal.height;
    } else {
      const bbox = svgEl.getBBox();
      svgWidth = bbox.width;
      svgHeight = bbox.height;
    }

    if (svgWidth === 0 || svgHeight === 0) {
      setZoom(1.1);
      setPan({ x: 0, y: 0 });
      return;
    }

    // Set SVG and diagramRef to natural dimensions so 1 unit = 1 physical px
    svgEl.removeAttribute("width");
    svgEl.removeAttribute("height");
    svgEl.style.width = `${svgWidth}px`;
    svgEl.style.height = `${svgHeight}px`;
    svgEl.style.maxWidth = "none";
    svgEl.style.maxHeight = "none";

    diagramRef.current.style.width = `${svgWidth}px`;
    diagramRef.current.style.height = `${svgHeight}px`;

    const containerWidth = containerRef.current.clientWidth - 80;
    const containerHeight = containerRef.current.clientHeight - 80;

    const scaleX = containerWidth / svgWidth;
    const scaleY = containerHeight / svgHeight;
    const optimalScale = Math.min(scaleX, scaleY);

    // Natural fit within viewport:
    // If diagram is smaller than viewport (optimalScale >= 1.0), cap at 1.0 so it never blows up.
    // If diagram is larger than viewport (optimalScale < 1.0, e.g. Level 2 Containers or Level 3 Components),
    // scale to optimalScale so all containers, links, and text fit 100% within the viewport!
    const clampedZoom = Number(Math.min(1.0, optimalScale).toFixed(2));
    setZoom(clampedZoom);
    setPan({ x: 0, y: 0 });
  }, []);

  // Drag & Pan Event Handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    isDraggingRef.current = true;
    hasMovedRef.current = false;
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      panX: pan.x,
      panY: pan.y,
    };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDraggingRef.current) return;
    const dx = e.clientX - dragStartRef.current.x;
    const dy = e.clientY - dragStartRef.current.y;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) {
      hasMovedRef.current = true;
    }
    setPan({
      x: dragStartRef.current.panX + dx,
      y: dragStartRef.current.panY + dy,
    });
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
  };

  // Attach native non-passive wheel listener so scrolling inside the C4 diagram zooms without scrolling the page
  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onNativeWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const delta = e.deltaY < 0 ? 0.1 : -0.1;
      setZoom((prev) => Math.min(3.0, Math.max(0.35, Number((prev + delta).toFixed(2)))));
    };
    el.addEventListener("wheel", onNativeWheel, { passive: false });
    return () => el.removeEventListener("wheel", onNativeWheel);
  }, []);

  // Compute active diagram definition
  const currentDiagramSource = React.useMemo(() => {
    if (level === 1) {
      return data.diagrams.c4_context || "graph TD\nUser[User]-->System[System]";
    } else if (level === 2) {
      return data.diagrams.c4_containers || "graph TD\nCore[CNT-CORE]";
    } else {
      const compMap = data.diagrams.c4_components || {};
      return (
        compMap[selectedContainerId] ||
        `graph TD\n${selectedContainerId.replace(/-/g, "_")}[${selectedContainerId}]`
      );
    }
  }, [level, selectedContainerId, data.diagrams]);

  const setC4Level = (lvl: 1 | 2 | 3) => {
    setLevelState(lvl);
    setZoom(1);
  };

  const drillDownToContainer = (cid: string) => {
    setSelectedContainerId(cid);
    setLevelState(3);
    setZoom(1);
  };

  const openC4Inspector = (comp: SpecComponent, subCompId?: string | null) => {
    setFocusedSubComponentId(subCompId || null);
    setInspectedComponent(comp);
    setInspectorOpen(true);
  };

  const closeC4Inspector = () => {
    setInspectorOpen(false);
  };

  const attachC4ClickHandlers = () => {
    if (!diagramRef.current) return;
    const nodes = diagramRef.current.querySelectorAll(".node");
    nodes.forEach((node) => {
      node.classList.add("cursor-pointer");
      const text = node.textContent || "";
      const nodeId = node.id || "";

      const sysTitle = data.overview?.system_title || "";
      const sysId = data.overview?.system_id || "SYS";
      // Add tooltips to guide user on click actions
      if (level === 1) {
        if (
          text.includes("SYS") ||
          (sysTitle && text.includes(sysTitle)) ||
          (sysId && text.includes(sysId)) ||
          nodeId.includes("SYS")
        ) {
          node.setAttribute("title", "Click to drill down into Level 2: Containers");
        }
      } else if (level === 2) {
        const matched = data.components.find(
          (c) =>
            text.includes(c.id) ||
            text.includes(c.title) ||
            nodeId.includes(c.id.replace(/-/g, "_")) ||
            nodeId.includes(c.id)
        );
        if (matched) {
          node.setAttribute(
            "title",
            `Click to drill down into ${matched.id} (${matched.title}) Level 3 Components`
          );
        } else {
          node.setAttribute("title", "Click container to drill down into Level 3: Components");
        }
      } else if (level === 3) {
        if (text.includes("Peer") || nodeId.includes("peer_")) {
          node.setAttribute("title", "Click to navigate to peer container");
        } else {
          node.setAttribute("title", "Click to inspect module details");
        }
      }

      node.addEventListener("click", (e) => {
        e.stopPropagation();

        // Level 1: System Context navigation -> drill down to Level 2 Containers
        if (level === 1) {
          if (
            text.includes("SYS") ||
            (sysTitle && text.includes(sysTitle)) ||
            (sysId && text.includes(sysId)) ||
            nodeId.includes("SYS")
          ) {
            setC4Level(2);
            return;
          }
        }

        // Level 2: Container navigation -> drill down to Level 3 Components
        if (level === 2) {
          const matched = data.components.find(
            (c) =>
              text.includes(c.id) ||
              text.includes(c.title) ||
              nodeId.includes(c.id.replace(/-/g, "_")) ||
              nodeId.includes(c.id)
          );
          if (matched) {
            drillDownToContainer(matched.id);
            return;
          }
        }

        // Level 3: Component navigation -> drill to peer container or inspect
        if (level === 3) {
          const peerMatch = data.components.find(
            (c) =>
              (text.includes(c.id) && (text.includes("Peer") || nodeId.includes("peer_"))) ||
              nodeId.includes(`peer_${c.id.replace(/-/g, "_")}`)
          );
          if (peerMatch) {
            drillDownToContainer(peerMatch.id);
            return;
          }

          // Inspect container or specific Level 3 component / contract
          const ctrClicked = (data.contracts || []).find(
            (ct) => text.includes(ct.id) || nodeId.includes(ct.id.replace(/-/g, "_"))
          );
          if (ctrClicked) {
            setInspectedContract(ctrClicked);
            return;
          }
          const currentComp = data.components.find((c) => c.id === selectedContainerId);
          if (currentComp) {
            const matchedSub = (currentComp.internal_components || []).find(
              (sub, idx) =>
                (sub.id && text.includes(sub.id)) ||
                (sub.title && text.includes(sub.title)) ||
                nodeId.includes(`mod_${selectedContainerId.replace(/-/g, "_")}_${idx}`)
            );
            openC4Inspector(currentComp, matchedSub?.id || null);
          }
        }
      });
    });
  };

  // Render diagram when source changes
  React.useEffect(() => {
    let isCancelled = false;
    const renderDiagram = async () => {
      if (!diagramRef.current) return;
      try {
        const mermaid = await getConfiguredMermaid(darkMode);
        const uniqueId = `mermaid-${Date.now()}`;
        const { svg } = await mermaid.render(uniqueId, currentDiagramSource);
        if (!isCancelled && diagramRef.current) {
          diagramRef.current.innerHTML = svg;
          const svgEl = diagramRef.current.querySelector("svg");
          if (svgEl) {
            svgEl.style.maxWidth = "none";
          }
          attachC4ClickHandlers();
          // Automatically calculate and apply optimal readable zoom level
          requestAnimationFrame(() => {
            autoFitDiagram();
          });
        }
      } catch (err) {
        if (!isCancelled && diagramRef.current) {
          diagramRef.current.innerHTML = `<pre class="text-xs text-red-400 p-4 font-mono">${String(
            err
          )}</pre>`;
        }
      }
    };

    renderDiagram();
    return () => {
      isCancelled = true;
    };
  }, [currentDiagramSource, darkMode, level, selectedContainerId, autoFitDiagram]);

  return (
    <div className="space-y-4 animate-in fade-in duration-200">
      {/* Top Toolbar / Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 rounded-2xl bg-card border border-border md-elevation-1">
        {/* C4 Level Switcher */}
        <div id="c4LevelSwitcher" className="flex items-center gap-1.5 bg-muted/60 p-1 rounded-full border border-border/60">
          <button
            id="btnC4Level1"
            onClick={() => setC4Level(1)}
            className={`px-3 py-1 text-xs font-medium rounded-full transition-all cursor-pointer ${
              level === 1
                ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Level 1: Context
          </button>
          <button
            id="btnC4Level2"
            onClick={() => setC4Level(2)}
            className={`px-3 py-1 text-xs font-medium rounded-full transition-all cursor-pointer ${
              level === 2
                ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Level 2: Containers
          </button>
          <button
            id="btnC4Level3"
            onClick={() => setC4Level(3)}
            className={`px-3 py-1 text-xs font-medium rounded-full transition-all cursor-pointer ${
              level === 3
                ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Level 3: Components
          </button>
        </div>

        {/* Level 2 Breadcrumbs */}
        {level === 2 && (
          <div id="c4Breadcrumbs" className="flex items-center gap-2 text-xs">
            <button
              onClick={() => setC4Level(1)}
              className="p-1 rounded-lg bg-muted/60 text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 cursor-pointer transition-colors"
              title="Return to System Context"
            >
              <ArrowUp className="h-3 w-3" />
              <span>Context (L1)</span>
            </button>
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="font-semibold text-primary font-mono text-[11px]">Containers (L2)</span>
          </div>
        )}

        {/* Level 3 Breadcrumbs and selector */}
        {level === 3 && (
          <div id="c4Breadcrumbs" className="flex items-center gap-2 text-xs min-w-0">
            <button
              onClick={() => setC4Level(1)}
              className="p-1 rounded-lg bg-muted/60 text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 cursor-pointer transition-colors shrink-0"
              title="Return to System Context"
            >
              <span>Context</span>
            </button>
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            <button
              id="btnC4LevelUp"
              onClick={() => setC4Level(2)}
              className="p-1 rounded-lg bg-muted/60 text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 cursor-pointer transition-colors shrink-0"
              title="Return to Containers"
            >
              <ArrowUp className="h-3 w-3" />
              <span>Containers</span>
            </button>
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            <select
              id="c4ContainerSelect"
              value={selectedContainerId}
              onChange={(e) => drillDownToContainer(e.target.value)}
              className="bg-card border border-border text-xs rounded-lg px-2.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary font-mono max-w-[200px] sm:max-w-[280px] md:max-w-[340px] truncate"
            >
              {data.components.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} ({c.title})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Advanced Zoom & Pan Controls */}
        <div className="flex items-center gap-1 bg-muted/60 p-1 rounded-full border border-border/60">
          <button
            onClick={() => setZoom((z) => Math.max(0.35, +(z - 0.15).toFixed(2)))}
            className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Zoom Out (-)"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <span className="text-[11px] font-mono px-2 text-muted-foreground select-none font-semibold min-w-[42px] text-center">
            {Math.round(zoom * 100)}%
          </span>
          <button
            onClick={() => setZoom((z) => Math.min(3.0, +(z + 0.15).toFixed(2)))}
            className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Zoom In (+)"
          >
            <ZoomIn className="h-4 w-4" />
          </button>
          <button
            onClick={autoFitDiagram}
            className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-primary transition-colors cursor-pointer"
            title="Fit Diagram to Screen (Auto-zoom)"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => {
              setZoom(1);
              setPan({ x: 0, y: 0 });
            }}
            className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Reset to 100% (1:1)"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => {
              setIsExpanded((prev) => !prev);
              setTimeout(autoFitDiagram, 150);
            }}
            className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title={isExpanded ? "Collapse Viewport Height" : "Expand Viewport Height"}
          >
            {isExpanded ? <Shrink className="h-3.5 w-3.5" /> : <Expand className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>

      {/* C4 View Level Indicator & Tier Legend */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-1">
        {level === 2 && activeTiers.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider font-mono">
              Tiers:
            </span>
            {activeTiers.map((tier) => {
              const style = getTierStyle(tier.id);
              return (
                <span
                  key={tier.id}
                  className={cn(
                    "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium border",
                    style.bg,
                    style.text,
                    style.border
                  )}
                >
                  <span className={cn("w-1.5 h-1.5 rounded-full", style.dot)} />
                  {tier.name}
                </span>
              );
            })}
          </div>
        )}
        <div className="ml-auto">
          <span className="text-[11px] text-muted-foreground font-mono hidden sm:inline">
            {level === 1
              ? "C4 Level 1: System Context"
              : level === 2
              ? "C4 Level 2: Container Topology & Hierarchy"
              : `C4 Level 3: Whitebox (${selectedContainerId})`}
          </span>
        </div>
      </div>

      {/* Main Diagram Viewport Card with Drag-to-Pan */}
      <Card
        id="c4DiagramContainer"
        ref={containerRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        className={cn(
          "p-4 sm:p-6 flex items-center justify-center overflow-hidden overscroll-contain bg-card/60 relative select-none transition-all duration-200",
          isExpanded ? "h-[800px]" : "h-[580px]"
        )}
      >
        {/* Floating Pan & Zoom Hint */}
        <div className="absolute bottom-3 left-3 z-20 pointer-events-none flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-background/80 backdrop-blur-md border border-border/80 text-[10px] text-muted-foreground font-mono">
          <Move className="h-3 w-3 text-primary" />
          <span>Drag to pan • Scroll to zoom</span>
        </div>

        {/* Floating Fit to View Quick Action */}
        <div className="absolute bottom-3 right-3 z-20 flex items-center gap-1">
          <button
            onClick={autoFitDiagram}
            className="px-2.5 py-1 rounded-lg bg-background/80 hover:bg-background backdrop-blur-md border border-border/80 text-[11px] text-foreground font-medium flex items-center gap-1 cursor-pointer transition-colors shadow-xs"
            title="Auto-fit Diagram"
          >
            <Maximize2 className="h-3 w-3 text-primary" />
            <span>Fit to View</span>
          </button>
        </div>

        <div
          id="c4ActiveDiagram"
          ref={diagramRef}
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "center center",
          }}
          className="transition-transform duration-75 ease-out flex items-center justify-center shrink-0"
        />
      </Card>

      {/* Client-Side Blast Radius Simulator Panel */}
      <Card className="p-4 rounded-2xl bg-card border border-border/80 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Crosshair className="h-4 w-4 text-rose-400" />
            <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
              Interactive Blast Radius Simulator (1–2 Hop Graph BFS)
            </h4>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={blastTargetId}
              onChange={(e) => setBlastTargetId(e.target.value)}
              className="bg-muted/50 border border-border text-xs rounded-xl px-3 py-1.5 text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="">Select Container or Component to Simulate Impact...</option>
              {data.components.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} ({c.title})
                </option>
              ))}
              {data.components.flatMap((c) =>
                (c.internal_components || []).map((ic) => (
                  <option key={`${c.id}-${ic.id}`} value={ic.id}>
                    ↳ {ic.id} ({ic.title})
                  </option>
                ))
              )}
            </select>
            {blastTargetId && (
              <button
                type="button"
                onClick={() => setBlastTargetId("")}
                className="px-2.5 py-1 rounded-xl text-xs font-medium bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground cursor-pointer"
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {blastRadiusReport && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2 border-t border-border/60 text-xs">
            <div className="p-3 rounded-xl bg-muted/20 border border-border/60 space-y-1.5">
              <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
                1-Hop &amp; 2-Hop Upstream Dependents ({blastRadiusReport.upstream1.length + blastRadiusReport.upstream2.length})
              </span>
              <div className="flex flex-wrap gap-1">
                {[...blastRadiusReport.upstream1, ...blastRadiusReport.upstream2].map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => requestSpecNavigation(id)}
                    className="px-2 py-0.5 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 font-mono text-[10px] cursor-pointer"
                  >
                    {id}
                  </button>
                ))}
                {blastRadiusReport.upstream1.length + blastRadiusReport.upstream2.length === 0 && (
                  <span className="text-muted-foreground italic text-[11px]">None</span>
                )}
              </div>
            </div>
            <div className="p-3 rounded-xl bg-muted/20 border border-border/60 space-y-1.5">
              <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
                1-Hop &amp; 2-Hop Downstream Dependencies ({blastRadiusReport.downstream1.length + blastRadiusReport.downstream2.length})
              </span>
              <div className="flex flex-wrap gap-1">
                {[...blastRadiusReport.downstream1, ...blastRadiusReport.downstream2].map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => requestSpecNavigation(id)}
                    className="px-2 py-0.5 rounded bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/20 font-mono text-[10px] cursor-pointer"
                  >
                    {id}
                  </button>
                ))}
                {blastRadiusReport.downstream1.length + blastRadiusReport.downstream2.length === 0 && (
                  <span className="text-muted-foreground italic text-[11px]">None</span>
                )}
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Container Cards List & Prominent Section Divider */}
      <div className="pt-12 mt-10 border-t-2 border-border/80 space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2">
          <div className="space-y-1">
            <h3 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-3">
              <Box className="h-6 w-6 text-primary shrink-0" />
              <span>Declared Containers & Deployable Units</span>
              <Badge variant="outline" className="text-xs font-mono px-2 py-0.5">
                {data.components.length} Containers
              </Badge>
            </h3>
            <p className="text-sm text-muted-foreground">
              C4 Level 2 architectural boundaries, runtime environments, and deployable service packages
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {data.components.map((comp) => {
            const getDepIds = (list?: Array<string | { container?: string; id?: string }>) =>
              (list || [])
                .map((item) => (typeof item === "string" ? item : item?.container || item?.id || ""))
                .filter(Boolean);
            const inboundDeps = data.components.filter((c) =>
              getDepIds(c.dependencies?.internal_containers as Array<string | { container?: string; id?: string }>).includes(comp.id)
            );
            const outboundDeps = getDepIds(
              comp.dependencies?.internal_containers as Array<string | { container?: string; id?: string }>
            );
            return (
              <Card
                key={comp.id}
                className="hover:border-primary/50 transition-all p-5 space-y-3.5 group flex flex-col justify-between border border-border/80 bg-card rounded-2xl"
              >
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between gap-1 flex-wrap">
                    <span className="font-mono text-xs font-bold text-primary group-hover:underline">
                      {comp.id}
                    </span>
                    <div className="flex items-center gap-1.5">
                      {comp.tier && (() => {
                        const style = getTierStyle(comp.tier);
                        return (
                          <span
                            className={cn(
                              "text-[10px] font-mono px-2 py-0.5 rounded-full font-semibold border",
                              style.bg,
                              style.text,
                              style.border
                            )}
                          >
                            {comp.tier_name || comp.container_type}
                          </span>
                        );
                      })()}
                      <Badge variant="outline">{comp.container_type}</Badge>
                    </div>
                  </div>
                  <h4 className="text-sm font-semibold text-foreground">{comp.title}</h4>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
                    <span className="truncate">{comp.source_path}</span>
                    {comp.domain && comp.domain !== "N/A" && (
                      <span className="px-1.5 py-0.5 rounded bg-muted text-[10px] text-muted-foreground shrink-0 font-semibold">
                        {comp.domain}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-2">
                    {comp.responsibility || comp.summary || "Core system container"}
                  </p>
                  {/* Inter-Container Dependency Metric */}
                  <div className="flex items-center gap-2 pt-1 text-[10px] text-muted-foreground font-mono">
                    <span className="bg-muted/50 px-2 py-0.5 rounded border border-border/40">
                      {outboundDeps.length} dependencies
                    </span>
                    <span>•</span>
                    <span className="bg-muted/50 px-2 py-0.5 rounded border border-border/40">
                      {inboundDeps.length} dependents
                    </span>
                  </div>
                </div>
                <div className="pt-3.5 flex items-center justify-between gap-2 border-t-2 border-border/80 text-[11px]">
                  <button
                    onClick={() => drillDownToContainer(comp.id)}
                    className="flex-1 py-1.5 px-2.5 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary font-semibold flex items-center justify-center gap-1 transition-colors cursor-pointer"
                    title={`Navigate to ${comp.id} Level 3 Components`}
                  >
                    <span>Explore Level 3</span>
                    <ChevronRight className="h-3 w-3" />
                  </button>
                  <button
                    onClick={() => openC4Inspector(comp)}
                    className="py-1.5 px-2.5 rounded-lg bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground font-medium transition-colors cursor-pointer"
                    title="Inspect Details"
                  >
                    Details
                  </button>
                </div>
              </Card>
            );
          })}
        </div>
      </div>

      {/* Boundary Interface Contracts (CTR-*) Section */}
      {data.contracts && data.contracts.length > 0 && (
        <div id="architectureContractsSection" className="space-y-4 pt-6 border-t border-border/60">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground uppercase tracking-wider flex items-center gap-2">
              <FileCode className="h-4 w-4 text-emerald-400" />
              Boundary Interface Contracts (`CTR-*`)
            </h3>
            <span className="text-xs font-mono text-muted-foreground">
              {data.contracts.length} contracts
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.contracts.map((ctr) => (
              <Card
                key={ctr.id}
                onClick={() => setInspectedContract(ctr)}
                className="p-4 space-y-2 border-border/80 bg-card hover:border-emerald-500/50 cursor-pointer transition-all flex flex-col justify-between group"
              >
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs font-bold text-emerald-400 group-hover:underline">
                      {ctr.id}
                    </span>
                    <Badge variant="outline" className="text-[10px] font-mono">
                      {ctr.protocol}
                    </Badge>
                  </div>
                  <h4 className="text-xs font-bold text-foreground">{ctr.title}</h4>
                  <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
                    <span>Provider: {ctr.container}</span>
                  </div>
                </div>
                {ctr.specification_file && ctr.specification_file !== "N/A" && (
                  <p className="text-[10px] font-mono text-muted-foreground truncate pt-1 border-t border-border/50">
                    {ctr.specification_file}
                  </p>
                )}
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* Architectural Decisions (DEC-*) Section */}
      {data.decisions && data.decisions.length > 0 && (
        <div id="architectureDecisionsSection" className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground uppercase tracking-wider flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-amber-500" />
              Architectural Decisions (`DEC-*`)
            </h3>
            <span className="text-xs font-mono text-muted-foreground">
              {data.decisions.length} decisions
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.decisions.map((dec, idx) => (
              <Card
                key={`${dec.id}-${idx}`}
                className="p-4 space-y-2 border-border/80 bg-card flex flex-col justify-between"
              >
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs font-bold text-amber-500">{dec.id}</span>
                    {dec.source_id && (
                      <Badge variant="outline" className="text-[10px] font-mono">
                        {dec.source_id}
                      </Badge>
                    )}
                  </div>
                  <h4 className="text-xs font-bold text-foreground">{dec.title}</h4>
                  <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">
                    {dec.decision}
                  </p>
                </div>
                {dec.rationale && (
                  <p className="text-[11px] text-muted-foreground/80 italic pt-1 border-t border-border/50 line-clamp-2">
                    Rationale: {dec.rationale}
                  </p>
                )}
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* C4 Inspector Sheet Drawer (id="c4InspectorDrawer") */}
      <Sheet
        open={inspectorOpen}
        onOpenChange={(open) => {
          setInspectorOpen(open);
          if (!open) onClearSelectedComponent?.();
        }}
        title={inspectedComponent?.title || "Component Inspector"}
        subtitle={inspectedComponent?.id}
      >
        <div id="c4InspectorDrawer" className="space-y-5">
          {inspectedComponent && (
            <>
              <div className="flex flex-wrap gap-2">
                <Badge variant="primary">{inspectedComponent.container_type}</Badge>
                <Badge variant="default">{inspectedComponent.runtime}</Badge>
                <span className="font-mono text-xs text-muted-foreground px-2 py-0.5 rounded bg-muted">
                  {inspectedComponent.source_path}
                </span>
              </div>

              {/* Architectural Responsibility / Summary */}
              {(inspectedComponent.responsibility || inspectedComponent.summary) && (
                <div className="p-3.5 rounded-2xl bg-muted/20 border border-border/60 text-xs space-y-1">
                  <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
                    Architectural Responsibility
                  </span>
                  <p className="text-muted-foreground leading-relaxed">
                    {inspectedComponent.responsibility || inspectedComponent.summary}
                  </p>
                </div>
              )}

              {/* Internal Components Whitebox */}
              <div className="space-y-2">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Layers className="h-4 w-4 text-primary" />
                  Internal Subsystems & Components ({inspectedComponent.internal_components?.length || 0})
                </h4>
                <div className="space-y-2">
                  {(inspectedComponent.internal_components || []).map((sub, i) => (
                    <div
                      key={sub.id || i}
                      className={cn(
                        "p-3 rounded-xl border bg-muted/20 space-y-2 text-xs transition-all",
                        focusedSubComponentId && sub.id === focusedSubComponentId
                          ? "border-primary ring-2 ring-primary/30 bg-primary/5"
                          : "border-border/70"
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono font-bold text-primary">{sub.id}</span>
                          {sub.is_promoted && (
                            <Badge variant="outline" className="text-[9px] font-mono">
                              Promoted L3
                            </Badge>
                          )}
                        </div>
                        <span className="font-semibold text-foreground">{sub.title}</span>
                      </div>
                      <p className="text-muted-foreground">{sub.responsibility}</p>
                      {sub.source_path && (
                        <p className="font-mono text-[10px] text-muted-foreground/80">
                          {Array.isArray(sub.source_path)
                            ? sub.source_path.join(", ")
                            : sub.source_path}
                        </p>
                      )}
                      {sub.invariants && sub.invariants.length > 0 && (
                        <div className="pt-1.5 border-t border-border/50 space-y-1">
                          <span className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-1">
                            <ShieldCheck className="h-3 w-3" />
                            Component Invariants ({sub.invariants.length})
                          </span>
                          {sub.invariants.map((inv, idx) => (
                            <div
                              key={inv.id || idx}
                              className="p-2 rounded-lg bg-emerald-500/5 border border-emerald-500/20 text-[11px] space-y-0.5"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="font-mono font-bold text-emerald-400">
                                  {inv.id}
                                </span>
                                {inv.test_binding && (
                                  <span className="font-mono text-[10px] text-muted-foreground truncate">
                                    {inv.test_binding}
                                  </span>
                                )}
                              </div>
                              <p className="text-muted-foreground">{inv.rule}</p>
                            </div>
                          ))}
                        </div>
                      )}
                      {sub.decisions && sub.decisions.length > 0 && (
                        <div className="pt-1.5 border-t border-border/50 space-y-1">
                          <span className="text-[10px] font-bold text-amber-500 uppercase tracking-wider flex items-center gap-1">
                            <Sparkles className="h-3 w-3" />
                            Component Decisions ({sub.decisions.length})
                          </span>
                          {sub.decisions.map((dec, idx) => (
                            <div
                              key={dec.id || idx}
                              className="p-2 rounded-lg bg-amber-500/5 border border-amber-500/20 text-[11px] space-y-0.5"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="font-mono font-bold text-amber-500">
                                  {dec.id}
                                </span>
                                <span className="font-semibold text-foreground">{dec.title}</span>
                              </div>
                              <p className="text-muted-foreground">{dec.decision}</p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Container-Level Invariants */}
              {inspectedComponent.invariants && inspectedComponent.invariants.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <ShieldCheck className="h-4 w-4 text-emerald-400" />
                    Container Invariants ({inspectedComponent.invariants.length})
                  </h4>
                  <div className="space-y-2">
                    {inspectedComponent.invariants.map((inv, idx) => (
                      <div
                        key={inv.id || idx}
                        className="p-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 text-xs space-y-1"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono font-bold text-emerald-400">{inv.id}</span>
                          {inv.test_binding && (
                            <span className="font-mono text-[10px] text-muted-foreground truncate">
                              {inv.test_binding}
                            </span>
                          )}
                        </div>
                        <p className="text-muted-foreground leading-relaxed">{inv.rule}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Inter-Container Dependency Connections */}
              <div className="space-y-3">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <FileCode className="h-4 w-4 text-indigo-400" />
                  Inter-Container Dependencies &amp; Contracts
                </h4>
                {/* Outgoing Dependencies */}
                <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1.5 text-xs">
                  <div className="flex items-center justify-between">
                    <p className="font-semibold text-foreground">Upstream Dependencies (Consumes)</p>
                    <span className="text-[10px] font-mono text-muted-foreground">
                      {inspectedComponent.dependencies?.internal_containers?.length || 0} internal
                    </span>
                  </div>
                  {inspectedComponent.dependencies?.internal_containers &&
                  inspectedComponent.dependencies.internal_containers.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {(inspectedComponent.dependencies.internal_containers as Array<
                        string | { container?: string; id?: string; relationship?: string }
                      >).map((rawDep, idx) => {
                        const depId =
                          typeof rawDep === "string"
                            ? rawDep
                            : rawDep?.container || rawDep?.id || "";
                        const targetComp = data.components.find((c) => c.id === depId);
                        return (
                          <button
                            key={`${depId}-${idx}`}
                            onClick={() => {
                              if (targetComp) openC4Inspector(targetComp);
                            }}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-mono text-[11px] bg-primary/10 text-primary hover:bg-primary/20 transition-colors cursor-pointer"
                            title={targetComp?.title || depId}
                          >
                            <span>{depId}</span>
                            <ChevronRight className="h-3 w-3" />
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-[11px] italic">
                      None (Foundation core container)
                    </p>
                  )}
                </div>

                {/* Incoming Dependents */}
                <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1.5 text-xs">
                  {(() => {
                    const dependents = data.components.filter((c) =>
                      (
                        (c.dependencies?.internal_containers || []) as Array<
                          string | { container?: string; id?: string }
                        >
                      ).some((item) =>
                        (typeof item === "string" ? item : item?.container || item?.id || "") ===
                        inspectedComponent.id
                      )
                    );
                    return (
                      <>
                        <div className="flex items-center justify-between">
                          <p className="font-semibold text-foreground">Downstream Dependents</p>
                          <span className="text-[10px] font-mono text-muted-foreground">
                            {dependents.length} consumers
                          </span>
                        </div>
                        {dependents.length > 0 ? (
                          <div className="flex flex-wrap gap-1.5 pt-1">
                            {dependents.map((dep) => (
                              <button
                                key={dep.id}
                                onClick={() => openC4Inspector(dep)}
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-mono text-[11px] bg-muted hover:bg-muted/80 text-foreground transition-colors cursor-pointer"
                                title={dep.title}
                              >
                                <span>{dep.id}</span>
                                <ChevronRight className="h-3 w-3" />
                              </button>
                            ))}
                          </div>
                        ) : (
                          <p className="text-muted-foreground text-[11px] italic">
                            No internal containers depend on this unit
                          </p>
                        )}
                      </>
                    );
                  })()}
                </div>

                {/* External Services */}
                {inspectedComponent.dependencies?.external_services &&
                  inspectedComponent.dependencies.external_services.length > 0 && (
                    <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1.5 text-xs">
                      <p className="font-semibold text-foreground">External Platform Services</p>
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {(inspectedComponent.dependencies.external_services as Array<
                          string | { name?: string; service?: string; relationship?: string }
                        >).map((ext, idx) => {
                          const extLabel =
                            typeof ext === "string"
                              ? ext
                              : ext?.relationship
                                ? `${ext?.name || ext?.service || ""} (${ext.relationship})`
                                : ext?.name || ext?.service || "";
                          return (
                            <span
                              key={idx}
                              className="px-2 py-0.5 rounded-md font-mono text-[10px] bg-muted text-muted-foreground border border-border/40"
                            >
                              {extLabel}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  )}

                {/* Boundary Contracts (Clickable) */}
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                    <p className="font-semibold text-foreground">Provides Contracts</p>
                    <div className="flex flex-wrap gap-1">
                      {(inspectedComponent.contracts?.provides || []).length > 0 ? (
                        (inspectedComponent.contracts?.provides || []).map((cid, idx) => {
                          const ctrId = String(cid).split("(")[0].trim();
                          const ctrObj = (data.contracts || []).find((ct) => ct.id === ctrId);
                          return (
                            <button
                              key={idx}
                              type="button"
                              onClick={() => {
                                if (ctrObj) {
                                  closeC4Inspector();
                                  setInspectedContract(ctrObj);
                                } else {
                                  requestSpecNavigation(ctrId);
                                }
                              }}
                              className="font-mono text-[11px] px-2 py-0.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 cursor-pointer"
                            >
                              {ctrId}
                            </button>
                          );
                        })
                      ) : (
                        <span className="text-muted-foreground font-mono text-[11px]">None</span>
                      )}
                    </div>
                  </div>
                  <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                    <p className="font-semibold text-foreground">Consumes Contracts</p>
                    <div className="flex flex-wrap gap-1">
                      {(inspectedComponent.contracts?.consumes || []).length > 0 ? (
                        (inspectedComponent.contracts?.consumes || []).map((cid, idx) => {
                          const ctrId = String(cid).split("(")[0].trim();
                          const ctrObj = (data.contracts || []).find((ct) => ct.id === ctrId);
                          return (
                            <button
                              key={idx}
                              type="button"
                              onClick={() => {
                                if (ctrObj) {
                                  closeC4Inspector();
                                  setInspectedContract(ctrObj);
                                } else {
                                  requestSpecNavigation(ctrId);
                                }
                              }}
                              className="font-mono text-[11px] px-2 py-0.5 rounded bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/20 cursor-pointer"
                            >
                              {ctrId}
                            </button>
                          );
                        })
                      ) : (
                        <span className="text-muted-foreground font-mono text-[11px]">None</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* Embedded Architectural Decisions (DEC-*) */}
              {inspectedComponent.decisions && inspectedComponent.decisions.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <Sparkles className="h-4 w-4 text-amber-500" />
                    Embedded Architectural Decisions ({inspectedComponent.decisions.length})
                  </h4>
                  <div className="space-y-2">
                    {inspectedComponent.decisions.map((dec, i) => (
                      <div key={i} className="p-3 rounded-xl border border-border/70 bg-card text-xs space-y-1.5">
                        <div className="flex items-center justify-between">
                          <span className="font-mono font-bold text-amber-500">{dec.id}</span>
                          <span className="font-semibold text-foreground">{dec.title}</span>
                        </div>
                        <p className="text-muted-foreground text-xs leading-relaxed">{dec.decision}</p>
                        {dec.rationale && (
                          <p className="text-[11px] text-muted-foreground/80 italic">
                            Rationale: {dec.rationale}
                          </p>
                        )}
                        {dec.rejected_alternatives && dec.rejected_alternatives.length > 0 && (
                          <div className="pt-1 text-[10px] text-muted-foreground">
                            <span className="font-semibold text-rose-400">Rejected Alternatives: </span>
                            {dec.rejected_alternatives.map((ra) => ra.option).join(", ")}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Living Specification Document Body rendered via AsciiDocRenderer */}
              {inspectedComponent.description && (
                <div className="space-y-2 pt-2 border-t border-border/60">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                    Living Container Specification (`CNT-*`)
                  </h4>
                  <div className="max-h-[360px] overflow-y-auto rounded-xl p-3 bg-card border border-border/60">
                    <AsciiDocRenderer content={inspectedComponent.description} />
                  </div>
                </div>
              )}

              <div className="pt-2">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    closeC4Inspector();
                    drillDownToContainer(inspectedComponent.id);
                  }}
                  className="w-full"
                >
                  View Whitebox Architecture Diagram
                </Button>
              </div>
            </>
          )}
        </div>
      </Sheet>

      {/* Boundary Contract Inspector Sheet (CTR-*) */}
      <Sheet
        open={Boolean(inspectedContract)}
        onOpenChange={(open) => {
          if (!open) {
            setInspectedContract(null);
            onClearSelectedComponent?.();
          }
        }}
        title={inspectedContract?.title || "Interface Contract"}
        subtitle={inspectedContract?.id}
        maxWidth="4xl"
      >
        {inspectedContract && (
          <div className="space-y-4 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="primary">{inspectedContract.protocol}</Badge>
              {inspectedContract.container && inspectedContract.container !== "N/A" && (
                <button
                  type="button"
                  onClick={() => {
                    const cnt = data.components.find((c) => c.id === inspectedContract.container);
                    setInspectedContract(null);
                    if (cnt) openC4Inspector(cnt);
                  }}
                  className="font-mono text-xs px-2.5 py-0.5 rounded-md bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 cursor-pointer"
                >
                  Provider: {inspectedContract.container}
                </button>
              )}
              {inspectedContract.specification_file &&
                inspectedContract.specification_file !== "N/A" && (
                  <span className="font-mono text-muted-foreground px-2 py-0.5 rounded bg-muted">
                    {inspectedContract.specification_file}
                  </span>
                )}
            </div>
            {inspectedContract.description && (
              <div className="p-4 rounded-2xl bg-card border border-border/80">
                <AsciiDocRenderer content={inspectedContract.description} />
              </div>
            )}
          </div>
        )}
      </Sheet>
    </div>
  );
}
