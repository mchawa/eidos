import * as React from "react";
import { cn } from "@/lib/utils";
import {
  renderAsciiDocCached,
  slugify,
  extractAsciiDocOutline,
  type AsciiDocOutlineItem,
  type RenderedAsciiDoc,
} from "@/lib/asciidoc";
import { requestSpecNavigation } from "@/lib/specNav";

export { slugify, extractAsciiDocOutline };
export type { AsciiDocOutlineItem };

export interface AsciiDocRendererProps {
  content: string;
  className?: string;
  title?: string;
  fontSize?: "compact" | "comfortable" | "large";
  measure?: "prose" | "full";
  hideControls?: boolean;
  /**
   * Invoked when the reader clicks an `xref:` / `<<SPEC-ID>>` / `link:*.adoc[]` reference to another
   * living specification. Defaults to broadcasting on the portal's spec-navigation event bus.
   */
  onNavigateSpec?: (target: string) => void;
}

type RenderState =
  | { status: "loading" }
  | { status: "ready"; result: RenderedAsciiDoc }
  | { status: "error"; message: string };

/** Hook form of {@link extractAsciiDocOutline} for components that need the TOC outline. */
export function useAsciiDocOutline(content: string | undefined): AsciiDocOutlineItem[] {
  const [outline, setOutline] = React.useState<AsciiDocOutlineItem[]>([]);
  React.useEffect(() => {
    let cancelled = false;
    if (!content) {
      setOutline([]);
      return;
    }
    extractAsciiDocOutline(content)
      .then((items) => {
        if (!cancelled) setOutline(items);
      })
      .catch(() => {
        if (!cancelled) setOutline([]);
      });
    return () => {
      cancelled = true;
    };
  }, [content]);
  return outline;
}

function copyCodeFrom(button: HTMLElement) {
  const block = button.closest(".listingblock, .literalblock");
  const pre = block?.querySelector("pre");
  if (!pre) return;
  const clone = pre.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(".conum").forEach((el) => el.remove());
  const text = (clone.textContent || "")
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n");
  navigator.clipboard?.writeText(text);
  const previous = button.textContent;
  button.textContent = "Copied";
  button.classList.add("is-copied");
  setTimeout(() => {
    button.textContent = previous;
    button.classList.remove("is-copied");
  }, 2000);
}

export function AsciiDocRenderer({ content, className, onNavigateSpec }: AsciiDocRendererProps) {
  const [state, setState] = React.useState<RenderState>({ status: "loading" });

  React.useEffect(() => {
    let cancelled = false;
    if (!content) {
      setState({ status: "ready", result: { html: "", outline: [] } });
      return;
    }
    setState((prev) => (prev.status === "ready" ? prev : { status: "loading" }));
    renderAsciiDocCached(content)
      .then((result) => {
        if (!cancelled) setState({ status: "ready", result });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          console.error("AsciiDoc render error:", err);
          setState({ status: "error", message: String((err as Error)?.message || err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [content]);

  const handleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;

    const copyBtn = target.closest<HTMLElement>(".adoc-copy-btn");
    if (copyBtn) {
      event.preventDefault();
      copyCodeFrom(copyBtn);
      return;
    }

    const anchor = target.closest<HTMLAnchorElement>("a[href]");
    if (!anchor) return;

    const specRef = anchor.getAttribute("data-spec-ref");
    if (specRef) {
      event.preventDefault();
      (onNavigateSpec ?? requestSpecNavigation)(specRef);
      return;
    }

    const href = anchor.getAttribute("href") || "";
    if (href.startsWith("#") && href.length > 1) {
      const el = document.getElementById(decodeURIComponent(href.slice(1)));
      if (el) {
        event.preventDefault();
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  };

  // Fixed optimal reading typography & expansive container sizing (INV-PORTAL-READING-ERGONOMICS)
  const measureClass = "w-full max-w-4xl xl:max-w-5xl mx-auto";

  const isEmpty = state.status === "ready" && !state.result.html;

  return (
    <div className={cn("space-y-3.5", className)}>
      <div className="p-5 sm:p-7 rounded-2xl bg-card border border-border/80 md-elevation-1 space-y-4 text-foreground/90 overflow-x-hidden">
        <div className={cn("transition-all duration-200", measureClass)}>
          {state.status === "loading" && (
            <p className="text-muted-foreground italic text-center py-8">Rendering specification…</p>
          )}
          {state.status === "error" && (
            <div role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-400">
              <strong className="font-semibold">AsciiDoc rendering failed.</strong>
              <pre className="mt-2 whitespace-pre-wrap font-mono text-xs">{state.message}</pre>
            </div>
          )}
          {isEmpty && (
            <p className="text-muted-foreground italic text-center py-8">No specification body declared.</p>
          )}
          {state.status === "ready" && !isEmpty && (
            <div
              className="adoc-body prose-reading"
              onClick={handleClick}
              dangerouslySetInnerHTML={{ __html: state.result.html }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
