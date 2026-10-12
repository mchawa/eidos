import * as React from "react";
import { ZoomIn, ZoomOut, RotateCcw, Maximize2, Move, Expand, Shrink } from "lucide-react";
import { cn } from "@/lib/utils";

declare const mermaid: any;

export interface MermaidDiagramProps {
  code: string;
  id?: string;
  className?: string;
  darkMode?: boolean;
  minHeight?: string;
  expandedHeight?: string;
  showControls?: boolean;
}

export function MermaidDiagram({
  code,
  id,
  className,
  darkMode,
  minHeight = "300px",
  expandedHeight = "540px",
  showControls = true,
}: MermaidDiagramProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const diagramRef = React.useRef<HTMLDivElement>(null);
  const isDraggingRef = React.useRef<boolean>(false);
  const dragStartRef = React.useRef<{ x: number; y: number; panX: number; panY: number }>({
    x: 0,
    y: 0,
    panX: 0,
    panY: 0,
  });

  const [zoom, setZoom] = React.useState<number>(1.05);
  const [pan, setPan] = React.useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isExpanded, setIsExpanded] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string | null>(null);

  const isDark = darkMode ?? (typeof document !== "undefined" && document.documentElement.classList.contains("dark"));

  // Helper to extract native SVG dimensions
  const getSvgDimensions = React.useCallback(() => {
    if (!diagramRef.current) return { width: 0, height: 0 };
    const svgEl = diagramRef.current.querySelector("svg");
    if (!svgEl) return { width: 0, height: 0 };

    let width = 0;
    let height = 0;

    const viewBox = svgEl.getAttribute("viewBox");
    if (viewBox) {
      const parts = viewBox.trim().split(/\s+/).map(Number);
      if (parts.length >= 4 && parts[2] > 0 && parts[3] > 0) {
        width = parts[2];
        height = parts[3];
      }
    }
    if (!width || !height) {
      try {
        const bbox = svgEl.getBBox();
        width = bbox.width;
        height = bbox.height;
      } catch {
        width = svgEl.clientWidth || 800;
        height = svgEl.clientHeight || 300;
      }
    }
    return { width, height };
  }, []);

  // Auto-fit function that guarantees text is NEVER tiny or over-zoomed-out
  const autoFit = React.useCallback(() => {
    if (!containerRef.current || !diagramRef.current) return;
    const svgEl = diagramRef.current.querySelector("svg");
    if (!svgEl) return;

    const { width: svgWidth, height: svgHeight } = getSvgDimensions();
    if (svgWidth <= 0 || svgHeight <= 0) {
      setZoom(1.05);
      setPan({ x: 0, y: 0 });
      return;
    }

    // Set SVG and diagramRef to true natural dimensions so 1 unit = 1 physical px
    svgEl.removeAttribute("width");
    svgEl.removeAttribute("height");
    svgEl.style.width = `${svgWidth}px`;
    svgEl.style.height = `${svgHeight}px`;
    svgEl.style.maxWidth = "none";
    svgEl.style.maxHeight = "none";
    svgEl.style.minWidth = `${svgWidth}px`;
    svgEl.style.minHeight = `${svgHeight}px`;

    diagramRef.current.style.width = `${svgWidth}px`;
    diagramRef.current.style.height = `${svgHeight}px`;

    const containerWidth = containerRef.current.clientWidth - 48;
    const containerHeight = containerRef.current.clientHeight - 48;

    const scaleX = containerWidth / svgWidth;
    const scaleY = containerHeight / svgHeight;
    const naturalFit = Math.min(scaleX, scaleY);

    // Natural fit within container:
    // 1. If naturalFit >= 1.0 (diagram is smaller than container), cap at 1.0 so it never blows up.
    // 2. If naturalFit < 1.0 (diagram is wider/taller than container), scale to naturalFit so the
    //    entire diagram (all nodes, steps, and arrows) fits 100% within the card without clipping!
    const optimalZoom = Number(Math.min(1.0, naturalFit).toFixed(2));

    setZoom(optimalZoom);
    setPan({ x: 0, y: 0 });
  }, [getSvgDimensions]);

  const autoFitRef = React.useRef(autoFit);
  autoFitRef.current = autoFit;

  // Fit to screen explicitly (scales entire diagram to fit container regardless of size)
  const fitToScreen = () => {
    if (!containerRef.current || !diagramRef.current) return;
    const { width: svgWidth, height: svgHeight } = getSvgDimensions();
    if (svgWidth <= 0 || svgHeight <= 0) return;

    const containerWidth = containerRef.current.clientWidth - 48;
    const containerHeight = containerRef.current.clientHeight - 48;
    const scaleX = containerWidth / svgWidth;
    const scaleY = containerHeight / svgHeight;
    const fitScale = Number(Math.min(scaleX, scaleY).toFixed(2));

    setZoom(Math.max(0.35, Math.min(2.5, fitScale)));
    setPan({ x: 0, y: 0 });
  };

  // Reset to 100% (1:1)
  const resetTo100 = () => {
    setZoom(1.0);
    setPan({ x: 0, y: 0 });
  };

  // Render diagram ONLY when code, dark mode, or id changes
  React.useEffect(() => {
    let isCancelled = false;
    if (typeof mermaid === "undefined" || !code || !code.trim()) {
      return;
    }

    mermaid.initialize({
      startOnLoad: false,
      theme: isDark ? "dark" : "default",
      securityLevel: "loose",
      fontFamily: "Inter, system-ui, sans-serif",
      themeVariables: isDark
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

    const renderCode = async () => {
      try {
        setError(null);
        const uniqueId = `mermaid-${id || "dia"}-${Math.random().toString(36).substring(2, 9)}`;
        const { svg } = await mermaid.render(uniqueId, code.trim());
        if (!isCancelled && diagramRef.current) {
          diagramRef.current.innerHTML = svg;
          const svgEl = diagramRef.current.querySelector("svg");
          if (svgEl) {
            svgEl.style.maxWidth = "none";
          }
          requestAnimationFrame(() => {
            autoFitRef.current();
          });
        }
      } catch (err: any) {
        if (!isCancelled) {
          console.warn("Mermaid render error:", err);
          setError(String(err?.message || err));
        }
      }
    };

    renderCode();

    return () => {
      isCancelled = true;
    };
  }, [code, isDark, id]);

  // Drag & Pan Event Handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    isDraggingRef.current = true;
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
    setPan({
      x: dragStartRef.current.panX + dx,
      y: dragStartRef.current.panY + dy,
    });
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
  };

  const handleWheel = (e: React.WheelEvent) => {
    // Zoom in/out via mouse wheel
    e.preventDefault();
    e.stopPropagation();
    const delta = e.deltaY < 0 ? 0.12 : -0.12;
    setZoom((prev) => Math.min(3.5, Math.max(0.35, Number((prev + delta).toFixed(2)))));
  };

  return (
    <div
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onWheel={handleWheel}
      style={{ minHeight: isExpanded ? expandedHeight : minHeight }}
      className={cn(
        "relative rounded-xl border border-border/70 bg-card/60 overflow-hidden flex items-center justify-center p-4 select-none cursor-grab active:cursor-grabbing transition-all duration-200",
        className
      )}
    >
      {/* Zoom / Pan Controls Toolbar */}
      {showControls && (
        <div className="absolute top-2.5 right-2.5 z-20 flex items-center gap-1 bg-background/90 backdrop-blur-md border border-border/80 rounded-xl p-1 shadow-xs">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setZoom((prev) => Math.max(0.35, Number((prev - 0.15).toFixed(2))));
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Zoom Out (-15%)"
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </button>
          <span className="text-[10px] font-mono text-foreground font-semibold px-1 select-none min-w-[36px] text-center">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setZoom((prev) => Math.min(3.5, Number((prev + 0.15).toFixed(2))));
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Zoom In (+15%)"
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              fitToScreen();
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-primary transition-colors cursor-pointer"
            title="Fit Entire Diagram to Screen"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              resetTo100();
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Reset to 100% (1:1 Natural Size)"
          >
            <RotateCcw className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setIsExpanded((prev) => !prev);
              setTimeout(() => autoFitRef.current(), 150);
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title={isExpanded ? "Collapse Viewport Height" : "Expand Viewport Height"}
          >
            {isExpanded ? <Shrink className="h-3.5 w-3.5" /> : <Expand className="h-3.5 w-3.5" />}
          </button>
        </div>
      )}

      {/* Floating Pan & Wheel Zoom Hint */}
      <div className="absolute bottom-2 left-2 z-10 pointer-events-none flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-background/80 backdrop-blur-xs border border-border/60 text-[9px] text-muted-foreground font-mono">
        <Move className="h-2.5 w-2.5 text-primary" />
        <span>Drag to pan • Scroll to zoom</span>
      </div>

      {/* Diagram Canvas */}
      {error ? (
        <div className="w-full p-4 overflow-auto">
          <p className="text-xs text-destructive font-semibold mb-1">Mermaid rendering error:</p>
          <pre className="text-[11px] font-mono text-muted-foreground whitespace-pre-wrap">{code}</pre>
        </div>
      ) : (
        <div
          ref={diagramRef}
          className="transition-transform duration-75 ease-out flex items-center justify-center shrink-0"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "center center",
          }}
        />
      )}
    </div>
  );
}
