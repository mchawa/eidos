import * as React from "react";
import { ZoomIn, ZoomOut, RotateCcw, Maximize2, Move } from "lucide-react";
import { cn } from "@/lib/utils";

declare const mermaid: any;

export interface MermaidDiagramProps {
  code: string;
  id?: string;
  className?: string;
  darkMode?: boolean;
  minHeight?: string;
  showControls?: boolean;
}

export function MermaidDiagram({
  code,
  id,
  className,
  darkMode,
  minHeight = "240px",
  showControls = true,
}: MermaidDiagramProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const diagramRef = React.useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = React.useState<number>(1);
  const [pan, setPan] = React.useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = React.useState<boolean>(false);
  const [dragStart, setDragStart] = React.useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [error, setError] = React.useState<string | null>(null);

  const isDark = darkMode ?? (typeof document !== "undefined" && document.documentElement.classList.contains("dark"));

  const autoFit = React.useCallback(() => {
    if (!containerRef.current || !diagramRef.current) return;
    const svgEl = diagramRef.current.querySelector("svg");
    if (!svgEl) return;

    const contWidth = containerRef.current.clientWidth - 32;
    const contHeight = containerRef.current.clientHeight - 32;

    let svgW = 0;
    let svgH = 0;
    const viewBox = svgEl.getAttribute("viewBox");
    if (viewBox) {
      const parts = viewBox.trim().split(/\s+/).map(Number);
      if (parts.length >= 4 && parts[2] > 0 && parts[3] > 0) {
        svgW = parts[2];
        svgH = parts[3];
      }
    }
    if (!svgW) {
      const bcr = svgEl.getBoundingClientRect();
      svgW = bcr.width / (zoom || 1);
      svgH = bcr.height / (zoom || 1);
    }

    if (svgW > 0 && svgH > 0 && contWidth > 0 && contHeight > 0) {
      const scaleX = contWidth / svgW;
      const scaleY = contHeight / svgH;
      const optimal = Math.max(0.6, Math.min(1.25, Math.min(scaleX, scaleY)));
      setZoom(Number(optimal.toFixed(2)));
      setPan({ x: 0, y: 0 });
    } else {
      setZoom(1);
      setPan({ x: 0, y: 0 });
    }
  }, [zoom]);

  React.useEffect(() => {
    let isCancelled = false;
    if (typeof mermaid === "undefined" || !code.trim()) {
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
          requestAnimationFrame(() => {
            autoFit();
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
  }, [code, isDark, id, autoFit]);

  // Pan and drag handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    setIsDragging(true);
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    setPan({
      x: e.clientX - dragStart.x,
      y: e.clientY - dragStart.y,
    });
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  return (
    <div
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      style={{ minHeight }}
      className={cn(
        "relative rounded-xl border border-border/70 bg-card/60 overflow-hidden flex items-center justify-center p-4 select-none",
        isDragging ? "cursor-grabbing" : "cursor-grab",
        className
      )}
    >
      {/* Zoom / Pan Controls Toolbar */}
      {showControls && (
        <div className="absolute top-2.5 right-2.5 z-20 flex items-center gap-1 bg-background/80 backdrop-blur-md border border-border/80 rounded-xl p-1 shadow-xs">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setZoom((prev) => Math.max(0.4, Number((prev - 0.15).toFixed(2))));
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Zoom Out"
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </button>
          <span className="text-[10px] font-mono text-muted-foreground px-1 select-none">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setZoom((prev) => Math.min(2.5, Number((prev + 0.15).toFixed(2))));
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Zoom In"
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              autoFit();
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-primary transition-colors cursor-pointer"
            title="Fit to Screen"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setZoom(1);
              setPan({ x: 0, y: 0 });
            }}
            className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Reset (1:1)"
          >
            <RotateCcw className="h-3 w-3" />
          </button>
        </div>
      )}

      {/* Floating Pan Hint */}
      <div className="absolute bottom-2 left-2 z-10 pointer-events-none flex items-center gap-1 px-2 py-0.5 rounded-md bg-background/70 backdrop-blur-xs border border-border/60 text-[9px] text-muted-foreground font-mono">
        <Move className="h-2.5 w-2.5 text-primary" />
        <span>Drag to pan</span>
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
          className="transition-transform duration-75 flex items-center justify-center max-w-full"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "center center",
          }}
        />
      )}
    </div>
  );
}
