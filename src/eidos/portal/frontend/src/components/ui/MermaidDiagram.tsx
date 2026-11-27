import * as React from "react";
import { ZoomIn, ZoomOut, RotateCcw, Maximize2, Maximize, Move, Expand, Shrink, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { renderMermaidSvg, sanitizeMermaidCode } from "@/lib/mermaid";
import { useDarkMode } from "@/lib/theme";

export interface MermaidDiagramProps {
  code: string;
  id?: string;
  title?: string;
  className?: string;
  darkMode?: boolean;
  minHeight?: string;
  expandedHeight?: string;
  showControls?: boolean;
  allowFullscreen?: boolean;
  wheelZoom?: "always" | "modifier";
  emptyBehavior?: "fallback" | "error";
  hideErrorFallback?: boolean;
  onRenderError?: (message: string | null) => void;
}

const FALLBACK_DIAGRAM_SOURCE = "flowchart LR\n  Start --> End";

export function MermaidDiagram({
  code,
  id,
  title,
  className,
  darkMode,
  minHeight = "300px",
  expandedHeight = "540px",
  showControls = true,
  allowFullscreen = true,
  wheelZoom = "always",
  emptyBehavior = "fallback",
  hideErrorFallback = false,
  onRenderError,
}: MermaidDiagramProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const diagramRef = React.useRef<HTMLDivElement>(null);
  const fullscreenDialogRef = React.useRef<HTMLDialogElement>(null);
  const fullscreenPressedOutsideRef = React.useRef<boolean>(false);
  const fullscreenHeadingId = React.useId();
  const isDraggingRef = React.useRef<boolean>(false);
  const userInteractedRef = React.useRef<boolean>(false);
  const expandTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragStartRef = React.useRef<{ x: number; y: number; panX: number; panY: number }>({
    x: 0,
    y: 0,
    panX: 0,
    panY: 0,
  });

  const [zoom, setZoom] = React.useState<number>(1.0);
  const [pan, setPan] = React.useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isExpanded, setIsExpanded] = React.useState<boolean>(false);
  const [isFullscreen, setIsFullscreen] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const dialog = fullscreenDialogRef.current;
    if (!dialog) return;
    if (isFullscreen) {
      if (typeof dialog.showModal === "function") {
        if (!dialog.open) dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    } else if (dialog.open) {
      if (typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
    }
  }, [isFullscreen]);

  const isDark = useDarkMode(darkMode);
  const onRenderErrorRef = React.useRef(onRenderError);
  onRenderErrorRef.current = onRenderError;

  // Helper to extract native SVG dimensions
  const getSvgDimensions = React.useCallback(() => {
    if (!diagramRef.current) return { width: 0, height: 0 };
    const svgEl = diagramRef.current.querySelector("svg");
    if (!svgEl) return { width: 0, height: 0 };

    let width = 0;
    let height = 0;

    const viewBox = svgEl.getAttribute("viewBox");
    if (viewBox) {
      const parts = viewBox
        .trim()
        .split(/[\s,]+/)
        .map(Number);
      if (
        parts.length >= 4 &&
        Number.isFinite(parts[2]) &&
        Number.isFinite(parts[3]) &&
        parts[2] > 0 &&
        parts[3] > 0
      ) {
        width = parts[2];
        height = parts[3];
      }
    }
    if (!width || !height) {
      try {
        const bbox = svgEl.getBBox();
        if (Number.isFinite(bbox.width) && Number.isFinite(bbox.height) && bbox.width > 0 && bbox.height > 0) {
          width = bbox.width;
          height = bbox.height;
        }
      } catch {
        // Ignore getBBox failures on hidden/jsdom SVG elements
      }
      if (!width || !height) {
        width = svgEl.clientWidth || 800;
        height = svgEl.clientHeight || 300;
      }
    }
    return { width, height };
  }, []);

  // Auto-fit function that guarantees readable positive zoom even in narrow/opening containers
  const autoFit = React.useCallback(() => {
    if (!containerRef.current || !diagramRef.current) return;
    const svgEl = diagramRef.current.querySelector("svg");
    if (!svgEl) return;

    const { width: svgWidth, height: svgHeight } = getSvgDimensions();
    if (svgWidth <= 0 || svgHeight <= 0) {
      setZoom(1.0);
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
    if (containerWidth <= 0 || containerHeight <= 0) {
      setZoom(1.0);
      setPan({ x: 0, y: 0 });
      return;
    }

    const scaleX = containerWidth / svgWidth;
    const scaleY = containerHeight / svgHeight;
    const naturalFit = Math.min(scaleX, scaleY);

    const optimalZoom = Number(Math.max(0.35, Math.min(1.0, naturalFit)).toFixed(2));

    setZoom(optimalZoom);
    setPan({ x: 0, y: 0 });
  }, [getSvgDimensions]);

  const autoFitRef = React.useRef(autoFit);
  autoFitRef.current = autoFit;

  // Fit to screen explicitly (scales entire diagram to fit container regardless of size)
  const fitToScreen = () => {
    userInteractedRef.current = false;
    if (!containerRef.current || !diagramRef.current) return;
    const { width: svgWidth, height: svgHeight } = getSvgDimensions();
    if (svgWidth <= 0 || svgHeight <= 0) {
      setZoom(1.0);
      setPan({ x: 0, y: 0 });
      return;
    }

    const containerWidth = containerRef.current.clientWidth - 48;
    const containerHeight = containerRef.current.clientHeight - 48;
    if (containerWidth <= 0 || containerHeight <= 0) {
      setZoom(1.0);
      setPan({ x: 0, y: 0 });
      return;
    }

    const scaleX = containerWidth / svgWidth;
    const scaleY = containerHeight / svgHeight;
    const fitScale = Number(Math.min(scaleX, scaleY).toFixed(2));

    setZoom(Math.max(0.35, Math.min(2.5, fitScale)));
    setPan({ x: 0, y: 0 });
  };

  // Reset to 100% (1:1) — explicit user zoom choice, so ResizeObserver must not override it
  const resetTo100 = () => {
    userInteractedRef.current = true;
    setZoom(1.0);
    setPan({ x: 0, y: 0 });
  };

  React.useEffect(() => {
    return () => {
      if (expandTimerRef.current !== null) {
        clearTimeout(expandTimerRef.current);
      }
    };
  }, []);

  // Render diagram through the unified serialized Mermaid queue when code, theme, or mode changes
  React.useEffect(() => {
    let isCancelled = false;
    userInteractedRef.current = false;

    const safeCode = sanitizeMermaidCode(code);
    if (!safeCode) {
      if (emptyBehavior === "error") {
        diagramRef.current?.replaceChildren();
        const message = "Unable to render diagram.";
        setError(message);
        onRenderErrorRef.current?.(message);
        return;
      }
      if (!code || !code.trim()) {
        diagramRef.current?.replaceChildren();
        setError(null);
        onRenderErrorRef.current?.(null);
        return;
      }
    }

    const sourceToRender = safeCode || FALLBACK_DIAGRAM_SOURCE;

    const renderCode = async () => {
      try {
        setError(null);
        onRenderErrorRef.current?.(null);
        const { svg } = await renderMermaidSvg(id || "dia", sourceToRender, isDark);
        if (!isCancelled && diagramRef.current) {
          diagramRef.current.innerHTML = svg;
          const svgEl = diagramRef.current.querySelector("svg");
          if (svgEl) {
            svgEl.style.maxWidth = "none";
          }
          requestAnimationFrame(() => {
            if (!isCancelled) {
              autoFitRef.current();
            }
          });
        }
      } catch (err: unknown) {
        if (!isCancelled) {
          diagramRef.current?.replaceChildren();
          const message = String((err as Error)?.message || err || "Unable to render diagram.");
          console.warn("Mermaid render error:", err);
          setError(message);
          onRenderErrorRef.current?.(message);
        }
      }
    };

    renderCode();

    return () => {
      isCancelled = true;
    };
  }, [code, isDark, id, emptyBehavior]);

  // Re-fit smoothly on sidebar collapse or 200ms Sheet / expand transitions unless user zoomed/panned
  React.useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let rafId = 0;
    const observer = new ResizeObserver(() => {
      if (userInteractedRef.current) return;
      if (el.clientWidth <= 0 || el.clientHeight <= 0) return;
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        if (!userInteractedRef.current && el.clientWidth > 0 && el.clientHeight > 0) {
          autoFitRef.current();
        }
      });
    });
    observer.observe(el);
    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      observer.disconnect();
    };
  }, []);

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
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
      userInteractedRef.current = true;
    }
    setPan({
      x: dragStartRef.current.panX + dx,
      y: dragStartRef.current.panY + dy,
    });
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
  };

  // Attach native non-passive wheel listener; in "modifier" mode require Ctrl/Cmd so document scroll is never trapped
  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onNativeWheel = (e: WheelEvent) => {
      if (wheelZoom === "modifier" && !(e.ctrlKey || e.metaKey)) {
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      userInteractedRef.current = true;
      const delta = e.deltaY < 0 ? 0.12 : -0.12;
      setZoom((prev) => Math.min(3.5, Math.max(0.35, Number((prev + delta).toFixed(2)))));
    };
    el.addEventListener("wheel", onNativeWheel, { passive: false });
    return () => el.removeEventListener("wheel", onNativeWheel);
  }, [wheelZoom]);

  const wheelHintText =
    wheelZoom === "modifier"
      ? "Drag to pan • Ctrl/⌘ + scroll to zoom"
      : "Drag to pan • Scroll to zoom";

  const viewportHeight = isExpanded ? expandedHeight : minHeight;

  return (
    <>
    <div
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      style={{
        height: error ? undefined : viewportHeight,
        minHeight: viewportHeight,
      }}
      className={cn(
        "relative rounded-xl border border-border/70 bg-card/60 overflow-hidden flex items-center justify-center p-4 select-none transition-all duration-200",
        !error && "cursor-grab active:cursor-grabbing",
        wheelZoom === "always" && "overscroll-contain",
        className
      )}
    >
      {/* Zoom / Pan Controls Toolbar */}
      {showControls && !error && (
        <div
          onMouseDown={(e) => e.stopPropagation()}
          className="absolute top-2.5 right-2.5 z-20 flex items-center gap-1 bg-background/90 backdrop-blur-md border border-border/80 rounded-xl p-1 shadow-xs"
        >
          <button
            type="button"
            aria-label="Zoom out"
            onClick={(e) => {
              e.stopPropagation();
              userInteractedRef.current = true;
              setZoom((prev) => Math.max(0.35, Number((prev - 0.15).toFixed(2))));
            }}
            className="min-h-6 min-w-6 inline-flex items-center justify-center p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Zoom Out (-15%)"
          >
            <ZoomOut className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <span className="text-[10px] font-mono text-foreground font-semibold px-1 select-none min-w-[36px] text-center">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            aria-label="Zoom in"
            onClick={(e) => {
              e.stopPropagation();
              userInteractedRef.current = true;
              setZoom((prev) => Math.min(3.5, Number((prev + 0.15).toFixed(2))));
            }}
            className="min-h-6 min-w-6 inline-flex items-center justify-center p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Zoom In (+15%)"
          >
            <ZoomIn className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Fit entire diagram to screen"
            onClick={(e) => {
              e.stopPropagation();
              fitToScreen();
            }}
            className="min-h-6 min-w-6 inline-flex items-center justify-center p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-primary transition-colors cursor-pointer"
            title="Fit Entire Diagram to Screen"
          >
            <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Reset zoom to 100%"
            onClick={(e) => {
              e.stopPropagation();
              resetTo100();
            }}
            className="min-h-6 min-w-6 inline-flex items-center justify-center p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Reset to 100% (1:1 Natural Size)"
          >
            <RotateCcw className="h-3 w-3" aria-hidden="true" />
          </button>
          {minHeight !== expandedHeight && (
            <button
              type="button"
              aria-label={isExpanded ? "Collapse viewport height" : "Expand viewport height"}
              onClick={(e) => {
                e.stopPropagation();
                userInteractedRef.current = false;
                setIsExpanded((prev) => !prev);
                if (expandTimerRef.current !== null) {
                  clearTimeout(expandTimerRef.current);
                }
                expandTimerRef.current = setTimeout(() => {
                  expandTimerRef.current = null;
                  autoFitRef.current();
                }, 220);
              }}
              className="min-h-6 min-w-6 inline-flex items-center justify-center p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
              title={isExpanded ? "Collapse Viewport Height" : "Expand Viewport Height"}
            >
              {isExpanded ? (
                <Shrink className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Expand className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          )}
          {allowFullscreen && (
            <button
              type="button"
              aria-label="Expand diagram to full screen"
              onClick={(e) => {
                e.stopPropagation();
                setIsFullscreen(true);
              }}
              className="min-h-6 min-w-6 inline-flex items-center justify-center p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-primary transition-colors cursor-pointer"
              title="Open Full Screen"
            >
              <Maximize className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
        </div>
      )}

      {/* Floating Pan & Wheel Zoom Hint */}
      {!error && (
        <div className="absolute bottom-2 left-2 z-10 pointer-events-none flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-background/80 backdrop-blur-xs border border-border/60 text-[9px] text-muted-foreground font-mono">
          <Move className="h-2.5 w-2.5 text-primary" aria-hidden="true" />
          <span>{wheelHintText}</span>
        </div>
      )}

      {/* Inline error notice (suppressed when parent card renders its own error fallback) */}
      {error && !hideErrorFallback && (
        <div role="alert" className="w-full p-4 text-center">
          <p className="text-xs text-destructive font-semibold">Unable to render diagram.</p>
        </div>
      )}

      {/* Always-mounted Diagram Canvas to prevent error-to-valid unmount races */}
      <div
        ref={diagramRef}
        className={cn(
          "transition-transform duration-75 ease-out flex items-center justify-center shrink-0",
          error && "hidden"
        )}
        style={{
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          transformOrigin: "center center",
        }}
      />
    </div>

    {/* Native Top-Layer Full-Screen Diagram Modal (<dialog>) as sibling to avoid event bubbling */}
    {allowFullscreen && (
      <dialog
        ref={fullscreenDialogRef}
        aria-labelledby={fullscreenHeadingId}
        {...({ closedby: "any" } as Record<string, string>)}
        onClose={() => setIsFullscreen(false)}
        onPointerDown={(e) => {
          const dlg = fullscreenDialogRef.current;
          if (!dlg || (typeof HTMLDialogElement !== "undefined" && "closedBy" in HTMLDialogElement.prototype)) return;
          const r = dlg.getBoundingClientRect();
          fullscreenPressedOutsideRef.current =
            e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
        }}
        onPointerUp={(e) => {
          const dlg = fullscreenDialogRef.current;
          if (
            !dlg ||
            (typeof HTMLDialogElement !== "undefined" && "closedBy" in HTMLDialogElement.prototype) ||
            !fullscreenPressedOutsideRef.current
          ) {
            return;
          }
          const r = dlg.getBoundingClientRect();
          const outside =
            e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
          fullscreenPressedOutsideRef.current = false;
          if (outside) setIsFullscreen(false);
        }}
        className="open:flex flex-col w-[96vw] h-[94vh] max-w-none max-h-[94vh] m-auto rounded-2xl bg-card text-foreground border border-border shadow-2xl p-0 overflow-hidden"
      >
        {isFullscreen && (
          <>
            <div className="flex items-center justify-between gap-4 px-5 py-3 border-b border-border/80 bg-muted/30 shrink-0">
              <h2 id={fullscreenHeadingId} className="text-sm sm:text-base font-bold text-foreground truncate">
                {title || "Diagram"}
              </h2>
              <button
                type="button"
                onClick={() => setIsFullscreen(false)}
                aria-label="Exit full screen"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-muted hover:bg-muted/80 text-xs font-medium text-foreground cursor-pointer transition-colors shrink-0"
              >
                <X className="h-4 w-4" aria-hidden="true" />
                <span>Close</span>
              </button>
            </div>
            <div className="flex-1 min-h-0 p-3 flex flex-col">
              <MermaidDiagram
                id={`${id || "dia"}-fullscreen`}
                code={code}
                darkMode={isDark}
                minHeight="100%"
                expandedHeight="100%"
                wheelZoom="always"
                allowFullscreen={false}
                className="flex-1 h-full border-0 bg-transparent rounded-none"
              />
            </div>
          </>
        )}
      </dialog>
    )}
    </>
  );
}
