import * as React from "react";
import { AlertCircle, Check, Code2, Copy, Eye } from "lucide-react";
import { MermaidDiagram } from "@/components/ui/MermaidDiagram";
import { sanitizeMermaidCode } from "@/lib/mermaid";
import { useDarkMode } from "@/lib/theme";
import { cn } from "@/lib/utils";

export interface DocumentMermaidBlockProps {
  code: string;
  title?: string;
  index: number;
  specId?: string;
  darkMode?: boolean;
}

/**
 * Interactive embedded AsciiDoc Mermaid diagram card mounted post-DOMPurify inside
 * `.adoc-mermaid-slot` placeholders via React portals (DEC-PORTAL-DOCUMENT-MERMAID-RENDERING).
 */
export function DocumentMermaidBlock({
  code,
  title,
  index,
  specId,
  darkMode,
}: DocumentMermaidBlockProps) {
  const isDark = useDarkMode(darkMode);
  const [viewMode, setViewMode] = React.useState<"diagram" | "source">("diagram");
  const [renderError, setRenderError] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState<boolean>(false);
  const copyTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const resetKey = `${code}\u0000${isDark}`;
  const [prevResetKey, setPrevResetKey] = React.useState<string>(resetKey);
  if (resetKey !== prevResetKey) {
    setPrevResetKey(resetKey);
    if (renderError !== null) {
      setRenderError(null);
    }
  }

  React.useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  const displayCaption = title?.trim() || "Diagram";
  const accessibleLabel = title?.trim()
    ? `Diagram: ${title.trim()}`
    : "Embedded diagram";
  const safeSource = React.useMemo(() => sanitizeMermaidCode(code), [code]);
  const diagramId = `${specId ? `${specId}-` : ""}doc-mermaid-${index}`;

  const handleCopy = () => {
    try {
      const maybePromise = navigator.clipboard?.writeText(code);
      if (maybePromise && typeof maybePromise.catch === "function") {
        maybePromise.catch(() => undefined);
      }
    } catch {
      // Ignore clipboard permission errors in restricted contexts
    }
    setCopied(true);
    if (copyTimerRef.current !== null) {
      clearTimeout(copyTimerRef.current);
    }
    copyTimerRef.current = setTimeout(() => {
      setCopied(false);
      copyTimerRef.current = null;
    }, 2000);
  };

  const showErrorFallback = viewMode === "diagram" && Boolean(renderError);
  const hideDiagramCanvas = viewMode !== "diagram" || showErrorFallback;

  return (
    <figure
      role="figure"
      aria-label={accessibleLabel}
      data-document-mermaid="true"
      className="my-0 rounded-xl border border-border/80 bg-card/80 overflow-hidden shadow-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2 border-b border-border/70 bg-muted/40">
        <figcaption className="text-xs font-semibold text-foreground truncate">
          {displayCaption}
        </figcaption>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            onClick={() =>
              setViewMode((prev) => (prev === "diagram" ? "source" : "diagram"))
            }
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-border/70 bg-background/80 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
          >
            {viewMode === "diagram" ? (
              <>
                <Code2 className="h-3.5 w-3.5" aria-hidden="true" />
                <span>View Source</span>
              </>
            ) : (
              <>
                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                <span>View Diagram</span>
              </>
            )}
          </button>
          <button
            type="button"
            aria-label={copied ? "Copied diagram source" : "Copy diagram source"}
            onClick={handleCopy}
            className={cn(
              "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-border/70 bg-background/80 transition-colors cursor-pointer",
              copied
                ? "text-emerald-600 dark:text-emerald-400 border-emerald-500/40"
                : "text-muted-foreground hover:text-foreground hover:bg-muted"
            )}
          >
            {copied ? (
              <>
                <Check className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Copied</span>
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Copy</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Keep MermaidDiagram mounted (hidden when in source or error mode) for seamless error-to-valid recovery */}
      <div className={hideDiagramCanvas ? "hidden" : undefined} hidden={hideDiagramCanvas}>
        <MermaidDiagram
          id={diagramId}
          code={code}
          title={displayCaption}
          darkMode={isDark}
          wheelZoom="modifier"
          emptyBehavior="error"
          hideErrorFallback
          onRenderError={setRenderError}
          minHeight="320px"
          expandedHeight="560px"
          className="rounded-none border-0 bg-transparent"
        />
      </div>

      {showErrorFallback && (
        <div className="divide-y divide-border/70">
          <div
            role="alert"
            className="flex items-center gap-2 px-4 py-3 bg-destructive/10 text-destructive text-xs font-medium"
          >
            <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>Unable to render diagram.</span>
          </div>
          {safeSource ? (
            <pre className="adoc-mermaid-source-pre">
              <code>{safeSource}</code>
            </pre>
          ) : (
            <p className="px-4 py-3 text-xs text-muted-foreground italic m-0">
              No diagram source provided.
            </p>
          )}
        </div>
      )}

      {viewMode === "source" &&
        (safeSource ? (
          <pre className="adoc-mermaid-source-pre">
            <code>{safeSource}</code>
          </pre>
        ) : (
          <p className="px-4 py-3 text-xs text-muted-foreground italic m-0">
            No diagram source provided.
          </p>
        ))}
    </figure>
  );
}
