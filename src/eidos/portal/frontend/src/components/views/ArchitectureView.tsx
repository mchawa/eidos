import * as React from "react";
// Use global window.mermaid (either bundled or loaded via standalone script)
declare const mermaid: any;
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
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import type { PortalData, SpecComponent } from "@/types";

export interface ArchitectureViewProps {
  data: PortalData;
  darkMode?: boolean;
}

export function ArchitectureView({ data, darkMode = true }: ArchitectureViewProps) {
  const [level, setLevelState] = React.useState<1 | 2 | 3>(1);
  const [selectedContainerId, setSelectedContainerId] = React.useState<string>(
    data.components[0]?.id || "CNT-EIDOS-CORE"
  );
  const [zoom, setZoom] = React.useState<number>(1);
  const [pan, setPan] = React.useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isExpanded, setIsExpanded] = React.useState<boolean>(false);
  const [inspectorOpen, setInspectorOpen] = React.useState<boolean>(false);
  const [inspectedComponent, setInspectedComponent] = React.useState<SpecComponent | null>(null);

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
    // If diagram is larger than viewport (optimalScale < 1.0, e.g. Level 2 Containers),
    // scale to optimalScale so all containers, links, and text fit 100% within the viewport without clipping!
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

  const handleWheel = (e: React.WheelEvent) => {
    // Zoom with scroll wheel
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.1 : -0.1;
    setZoom((prev) => Math.min(3.0, Math.max(0.35, Number((prev + delta).toFixed(2)))));
  };

  // Initialize mermaid and reconfigure when darkMode changes
  React.useEffect(() => {
    mermaid.initialize({
      startOnLoad: false,
      theme: darkMode ? "dark" : "default",
      securityLevel: "loose",
      fontFamily: "Inter, system-ui, sans-serif",
      themeVariables: darkMode
        ? {
            darkMode: true,
            background: "transparent",
            mainBkg: "#111827",
            nodeBorder: "#3b82f6",
            lineColor: "#60a5fa",
            textColor: "#f8fafc",
          }
        : {
            darkMode: false,
            background: "transparent",
            mainBkg: "#ffffff",
            nodeBorder: "#2563eb",
            lineColor: "#2563eb",
            textColor: "#0f172a",
          },
    });
  }, [darkMode]);

  // Compute active diagram definition
  const currentDiagramSource = React.useMemo(() => {
    if (level === 1) {
      return data.diagrams.c4_context || "graph TD\nUser[User]-->Eidos[Eidos Living System]";
    } else if (level === 2) {
      return data.diagrams.c4_containers || "graph TD\nCore[CNT-EIDOS-CORE]";
    } else {
      const compMap = data.diagrams.c4_components || {};
      return (
        compMap[selectedContainerId] ||
        `graph TD\n${selectedContainerId.replace(/-/g, "_")}[${selectedContainerId}]`
      );
    }
  }, [level, selectedContainerId, data.diagrams]);

  // Compatibility functions referenced by tests
  const setC4Level = (lvl: 1 | 2 | 3) => {
    setLevelState(lvl);
    setZoom(1);
  };

  const drillDownToContainer = (cid: string) => {
    setSelectedContainerId(cid);
    setLevelState(3);
    setZoom(1);
  };

  const openC4Inspector = (comp: SpecComponent) => {
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

      // Add tooltips to guide user on click actions
      if (level === 1) {
        if (text.includes("SYS") || text.includes("Eidos") || nodeId.includes("SYS")) {
          node.setAttribute("title", "Click to drill down into Level 2: Containers");
        }
      } else if (level === 2) {
        node.setAttribute("title", "Click container to drill down into Level 3: Components");
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
            text.includes("Eidos") ||
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
              text.includes(c.name) ||
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

          // Inspect container / module
          const currentComp = data.components.find((c) => c.id === selectedContainerId);
          if (currentComp) {
            openC4Inspector(currentComp);
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
          <div id="c4Breadcrumbs" className="flex items-center gap-2 text-xs">
            <button
              onClick={() => setC4Level(1)}
              className="p-1 rounded-lg bg-muted/60 text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 cursor-pointer transition-colors"
              title="Return to System Context"
            >
              <span>Context</span>
            </button>
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            <button
              id="btnC4LevelUp"
              onClick={() => setC4Level(2)}
              className="p-1 rounded-lg bg-muted/60 text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 cursor-pointer transition-colors"
              title="Return to Containers"
            >
              <ArrowUp className="h-3 w-3" />
              <span>Containers</span>
            </button>
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            <select
              id="c4ContainerSelect"
              value={selectedContainerId}
              onChange={(e) => drillDownToContainer(e.target.value)}
              className="bg-card border border-border text-xs rounded-lg px-2.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary font-mono"
            >
              {data.components.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} ({c.name})
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

      {/* C4 View Level Indicator */}
      <div className="flex items-center justify-end px-1">
        <span className="text-[11px] text-muted-foreground font-mono hidden sm:inline">
          {level === 1 ? "C4 Level 1: System Context" : level === 2 ? "C4 Level 2: Container Topology" : `C4 Level 3: Whitebox (${selectedContainerId})`}
        </span>
      </div>

      {/* Main Diagram Viewport Card with Drag-to-Pan */}
      <Card
        id="c4DiagramContainer"
        ref={containerRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
        className={cn(
          "p-4 sm:p-6 flex items-center justify-center overflow-hidden bg-card/60 relative select-none transition-all duration-200",
          isExpanded ? "min-h-[760px]" : "min-h-[520px]"
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

      {/* Container Cards List */}
      <div className="space-y-3 pt-2">
        <h3 className="text-sm font-bold text-foreground tracking-tight flex items-center gap-2">
          <Box className="h-4 w-4 text-primary" />
          Declared Containers & Deployable Units ({data.components.length})
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {data.components.map((comp) => (
            <Card
              key={comp.id}
              className="hover:border-primary/50 transition-all p-4 space-y-3 group flex flex-col justify-between"
            >
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-bold text-primary group-hover:underline">
                    {comp.id}
                  </span>
                  <Badge variant="outline">{comp.container_type}</Badge>
                </div>
                <h4 className="text-sm font-semibold text-foreground">{comp.name}</h4>
                <p className="text-xs text-muted-foreground font-mono truncate">
                  {comp.package_path}
                </p>
                <p className="text-[11px] text-muted-foreground line-clamp-2">
                  {comp.responsibility || comp.summary || "Core system container"}
                </p>
              </div>
              <div className="pt-2 flex items-center justify-between gap-2 border-t border-border/50 text-[11px]">
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
          ))}
        </div>
      </div>

      {/* C4 Inspector Sheet Drawer (id="c4InspectorDrawer") */}
      <Sheet
        open={inspectorOpen}
        onOpenChange={setInspectorOpen}
        title={inspectedComponent?.name || "Component Inspector"}
        subtitle={inspectedComponent?.id}
        className="w-full max-w-xl"
      >
        <div id="c4InspectorDrawer" className="space-y-5">
          {inspectedComponent && (
            <>
              <div className="flex flex-wrap gap-2">
                <Badge variant="primary">{inspectedComponent.container_type}</Badge>
                <Badge variant="default">{inspectedComponent.runtime}</Badge>
                <span className="font-mono text-xs text-muted-foreground px-2 py-0.5 rounded bg-muted">
                  {inspectedComponent.package_path}
                </span>
              </div>

              {/* Internal Components Whitebox */}
              <div className="space-y-2">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Layers className="h-4 w-4 text-primary" />
                  Internal Subsystems & Components ({inspectedComponent.internal_components?.length || 0})
                </h4>
                <div className="space-y-2">
                  {(inspectedComponent.internal_components || []).map((sub, i) => (
                    <div
                      key={i}
                      className="p-3 rounded-xl border border-border/70 bg-muted/20 space-y-1 text-xs"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-bold text-primary">{sub.id}</span>
                        <span className="font-semibold text-foreground">{sub.name}</span>
                      </div>
                      <p className="text-muted-foreground">{sub.responsibility}</p>
                      {sub.source_path && (
                        <p className="font-mono text-[10px] text-muted-foreground/80">
                          {sub.source_path}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Contracts & Dependencies */}
              <div className="space-y-2">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <FileCode className="h-4 w-4 text-indigo-400" />
                  Boundary Contracts & Dependencies
                </h4>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="p-3 rounded-xl border border-border/60 bg-muted/20">
                    <p className="font-semibold text-foreground mb-1">Provides</p>
                    <p className="text-muted-foreground font-mono text-[11px]">
                      {inspectedComponent.contracts?.provides?.join(", ") || "None"}
                    </p>
                  </div>
                  <div className="p-3 rounded-xl border border-border/60 bg-muted/20">
                    <p className="font-semibold text-foreground mb-1">Consumes</p>
                    <p className="text-muted-foreground font-mono text-[11px]">
                      {inspectedComponent.contracts?.consumes?.join(", ") || "None"}
                    </p>
                  </div>
                </div>
              </div>

              <div className="pt-2">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => drillDownToContainer(inspectedComponent.id)}
                  className="w-full"
                >
                  View Whitebox Architecture Diagram
                </Button>
              </div>
            </>
          )}
        </div>
      </Sheet>
    </div>
  );
}
