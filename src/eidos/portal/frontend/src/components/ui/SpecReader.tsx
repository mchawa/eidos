import * as React from "react";
import {
  FileText,
  ShieldCheck,
  Layers,
  List,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { AsciiDocRenderer, useAsciiDocOutline } from "@/components/ui/AsciiDocRenderer";
import { requestSpecNavigation } from "@/lib/specNav";
import { useResolvedSpecBody } from "@/lib/specChunks";
import {
  formatDisplayTitle,
  formatSpecType,
  formatStatus,
  resolveEntityTitle,
  useEntityLabels,
} from "@/lib/presentation";
import { cn } from "@/lib/utils";

export interface SpecReaderItem {
  id: string;
  title: string;
  type: string;
  path?: string;
  status?: string;
  is_valid?: boolean;
  frontmatter?: Record<string, any>;
  body?: string;
  body_chunk?: string;
}

export interface SpecReaderProps {
  spec: SpecReaderItem;
  onClose?: () => void;
  className?: string;
  isModal?: boolean;
}

export function SpecReader({
  spec,
  className,
}: SpecReaderProps) {
  const [activeHeadingId, setActiveHeadingId] = React.useState<string>("");
  const labelIndex = useEntityLabels();

  const stageRef = React.useRef<HTMLDivElement>(null);
  const { body: resolvedBody, frontmatter: loadedFrontmatter, loading: loadingChunk } =
    useResolvedSpecBody(spec.id, spec.body, spec.body_chunk);
  const effectiveFrontmatter = React.useMemo(
    () => (loadedFrontmatter ? { ...(spec.frontmatter || {}), ...loadedFrontmatter } : spec.frontmatter),
    [spec.frontmatter, loadedFrontmatter]
  );
  const outline = useAsciiDocOutline(resolvedBody);

  // Track active heading on scroll inside modal/scrollable container or window
  React.useEffect(() => {
    if (outline.length === 0) return;

    const findScrollParent = (node: HTMLElement | null): HTMLElement | Window => {
      let cur = node?.parentElement || null;
      while (cur && cur !== document.body) {
        const style = window.getComputedStyle(cur);
        if (/(auto|scroll)/.test(style.overflowY)) {
          return cur;
        }
        cur = cur.parentElement;
      }
      const mainScroll = document.getElementById("main-scroll");
      return mainScroll || window;
    };

    const scrollTarget = findScrollParent(stageRef.current);

    const handleScroll = () => {
      const headings = outline
        .map((item) => document.getElementById(item.id))
        .filter(Boolean) as HTMLElement[];

      if (headings.length === 0) return;

      const containerTop =
        scrollTarget instanceof Window
          ? 0
          : scrollTarget.getBoundingClientRect().top;

      for (let i = headings.length - 1; i >= 0; i--) {
        const el = headings[i];
        const relativeTop = el.getBoundingClientRect().top - containerTop;
        if (relativeTop <= 140) {
          setActiveHeadingId(el.id);
          return;
        }
      }
      setActiveHeadingId(headings[0].id);
    };

    scrollTarget.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => scrollTarget.removeEventListener("scroll", handleScroll);
  }, [outline]);

  const handleTocClick = (e: React.MouseEvent<HTMLAnchorElement>, id: string) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveHeadingId(id);
    }
  };

  // Extract key metadata from frontmatter
  const fm = effectiveFrontmatter || {};
  const domainBinding = fm.domain;
  const containerBinding = fm.container;
  const invariants = fm.blast_radius?.invariants_to_enforce || fm.invariants || fm.technical?.invariants;
  const domainTitle = domainBinding ? resolveEntityTitle(String(domainBinding), labelIndex) : "";
  const containerTitle = containerBinding ? resolveEntityTitle(String(containerBinding), labelIndex) : "";
  const displayTitle = formatDisplayTitle(spec.title, spec.id);

  return (
    <div ref={stageRef} className={cn("space-y-4 spec-reader-stage w-full", className)}>
      {/* Human-Centric Document Metadata Header Card */}
      <div className="p-4 sm:p-5 rounded-2xl bg-card border border-border/80 md-elevation-1 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2 flex-1 min-w-[280px]">
            {/* Category & Status Badges */}
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="primary">{formatSpecType(spec.type)}</Badge>
              <Badge variant={spec.is_valid !== false ? "success" : "danger"}>
                {spec.is_valid !== false ? "Valid" : "Needs Attention"}
              </Badge>
              {spec.status && (
                <Badge variant="outline">
                  {formatStatus(spec.status)}
                </Badge>
              )}
            </div>

            {/* Document Title */}
            <div className="flex flex-wrap items-center gap-3 pt-1">
              <h1 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight">
                {displayTitle}
              </h1>
            </div>
          </div>
        </div>

        {/* High-Level Relationship Badges (Clickable Cross-Entity Navigation) */}
        {(domainTitle || containerTitle || (Array.isArray(invariants) && invariants.length > 0)) && (
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/60 text-xs">
            {domainBinding && domainTitle && (
              <button
                type="button"
                onClick={() => requestSpecNavigation(String(domainBinding))}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-purple-500/10 hover:bg-purple-500/20 text-purple-400 border border-purple-500/20 font-medium transition-colors cursor-pointer"
                title={`Open ${domainTitle}`}
              >
                <Layers className="h-3.5 w-3.5" />
                <span>Domain: {domainTitle}</span>
              </button>
            )}
            {containerBinding && containerTitle && (
              <button
                type="button"
                onClick={() => requestSpecNavigation(String(containerBinding))}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/20 font-medium transition-colors cursor-pointer"
                title={`Open ${containerTitle}`}
              >
                <FileText className="h-3.5 w-3.5" />
                <span>Container: {containerTitle}</span>
              </button>
            )}
            {Array.isArray(invariants) && invariants.length > 0 && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
                <ShieldCheck className="h-3.5 w-3.5" />
                <span>
                  {invariants.length} {invariants.length === 1 ? "Rule" : "Rules"} Verified
                </span>
              </span>
            )}
          </div>
        )}
      </div>

      {/* Main Reading Stage & Dynamic Outline TOC */}
      <div className="flex items-start gap-5">
        {/* Document Prose Content */}
        <div className="flex-1 min-w-0">
          {loadingChunk ? (
            <div className="p-8 rounded-2xl bg-card border border-border text-center text-muted-foreground italic">
              Loading document…
            </div>
          ) : resolvedBody ? (
            <AsciiDocRenderer
              specId={spec.id}
              bodyChunk={spec.body_chunk}
              content={resolvedBody}
              title={displayTitle}
            />
          ) : (
            <div className="p-8 rounded-2xl bg-card border border-border text-center text-muted-foreground">
              No content available for this document.
            </div>
          )}
        </div>

        {/* Sticky On-This-Page Table of Contents (TOC) Outline */}
        {outline.length >= 2 && (
          <aside className="hidden xl:block w-72 shrink-0 sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto p-4 rounded-2xl bg-card/60 backdrop-blur-sm border border-border/80 text-xs space-y-3">
            <div className="flex items-center gap-1.5 font-bold uppercase tracking-wider text-[11px] text-muted-foreground pb-2 border-b border-border/60">
              <List className="h-3.5 w-3.5 text-primary" />
              <span>On This Page</span>
            </div>
            <nav className="space-y-1">
              {outline.map((item) => (
                <a
                  key={item.id}
                  href={`#${item.id}`}
                  onClick={(e) => handleTocClick(e, item.id)}
                  className={cn(
                    "block py-1.5 px-2.5 rounded-lg transition-colors leading-snug truncate",
                    item.level === 3 ? "pl-5 text-[11px]" : "font-medium text-xs",
                    activeHeadingId === item.id
                      ? "bg-primary/10 text-primary font-semibold border-l-2 border-primary"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  )}
                  title={item.text}
                >
                  {item.text}
                </a>
              ))}
            </nav>
          </aside>
        )}
      </div>
    </div>
  );
}
