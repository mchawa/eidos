import * as React from "react";
import {
  FileText,
  Copy,
  Check,
  Code2,
  ChevronDown,
  ChevronUp,
  ShieldCheck,
  Layers,
  GitBranch,
  List,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { AsciiDocRenderer, useAsciiDocOutline } from "@/components/ui/AsciiDocRenderer";
import { requestSpecNavigation } from "@/lib/specNav";
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
}

export interface SpecReaderProps {
  spec: SpecReaderItem;
  onClose?: () => void;
  className?: string;
  isModal?: boolean;
}

export function SpecReader({
  spec,
  onClose,
  className,
  isModal = false,
}: SpecReaderProps) {
  const [showRawFrontmatter, setShowRawFrontmatter] = React.useState(false);
  const [copiedId, setCopiedId] = React.useState(false);
  const [activeHeadingId, setActiveHeadingId] = React.useState<string>("");

  const stageRef = React.useRef<HTMLDivElement>(null);
  const outline = useAsciiDocOutline(spec.body);

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

  const handleCopyId = () => {
    navigator.clipboard.writeText(spec.id);
    setCopiedId(true);
    setTimeout(() => setCopiedId(false), 2000);
  };

  const handleTocClick = (e: React.MouseEvent<HTMLAnchorElement>, id: string) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveHeadingId(id);
    }
  };

  // Extract key metadata from frontmatter
  const fm = spec.frontmatter || {};
  const domainBinding = fm.domain;
  const containerBinding = fm.container;
  const invariants = fm.blast_radius?.invariants_to_enforce || fm.invariants;
  const vcs = fm.vcs;

  return (
    <div ref={stageRef} className={cn("space-y-4 spec-reader-stage w-full", className)}>
      {/* Human-Centric Specification Metadata Header Card */}
      <div className="p-4 sm:p-5 rounded-2xl bg-card border border-border/80 md-elevation-1 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2 flex-1 min-w-[280px]">
            {/* Breadcrumb Path & Badges */}
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="primary">{spec.type}</Badge>
              <Badge variant={spec.is_valid !== false ? "success" : "danger"}>
                {spec.is_valid !== false ? "● VALID" : "INVALID"}
              </Badge>
              {spec.status && (
                <Badge variant="outline" className="capitalize">
                  {spec.status}
                </Badge>
              )}
              {spec.path && (
                <span className="font-mono text-xs text-muted-foreground px-2 py-0.5 rounded bg-muted/60 truncate max-w-sm">
                  {spec.path}
                </span>
              )}
            </div>

            {/* Document Title & Copyable ID */}
            <div className="flex flex-wrap items-center gap-3 pt-1">
              <h1 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight">
                {spec.title || spec.id}
              </h1>
              <button
                type="button"
                onClick={handleCopyId}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono font-semibold bg-muted hover:bg-muted/80 text-foreground transition-colors cursor-pointer border border-border/60"
                title="Copy Specification ID"
              >
                {copiedId ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-emerald-500" />
                    <span className="text-emerald-500 font-bold">{spec.id}</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                    <span>{spec.id}</span>
                  </>
                )}
              </button>
            </div>
          </div>

        </div>

        {/* High-Level Relationship Badges (Clickable Cross-Entity Navigation) */}
        {(domainBinding || containerBinding || invariants || vcs) && (
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/60 text-xs">
            {domainBinding && (
              <button
                type="button"
                onClick={() => requestSpecNavigation(String(domainBinding))}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-purple-500/10 hover:bg-purple-500/20 text-purple-400 border border-purple-500/20 font-medium transition-colors cursor-pointer"
                title={`Navigate to Domain ${domainBinding}`}
              >
                <Layers className="h-3.5 w-3.5" />
                <span>Domain: {domainBinding}</span>
              </button>
            )}
            {containerBinding && (
              <button
                type="button"
                onClick={() => requestSpecNavigation(String(containerBinding))}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/20 font-medium transition-colors cursor-pointer"
                title={`Navigate to Container ${containerBinding}`}
              >
                <FileText className="h-3.5 w-3.5" />
                <span>Container: {containerBinding}</span>
              </button>
            )}
            {Array.isArray(invariants) && invariants.length > 0 && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
                <ShieldCheck className="h-3.5 w-3.5" />
                <span>{invariants.length} Invariants Enforced</span>
              </span>
            )}
            {vcs?.branch && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-zinc-800 text-zinc-300 border border-zinc-700 font-mono text-[11px]">
                <GitBranch className="h-3.5 w-3.5 text-primary" />
                <span>{vcs.branch}</span>
              </span>
            )}
          </div>
        )}

        {/* Collapsible Raw Frontmatter Accordion (Clean, Non-Blocking) */}
        {spec.frontmatter && (
          <div className="pt-2 border-t border-border/60">
            <button
              type="button"
              onClick={() => setShowRawFrontmatter((prev) => !prev)}
              className="text-xs font-medium text-muted-foreground hover:text-foreground flex items-center gap-1.5 cursor-pointer py-1"
            >
              <Code2 className="h-3.5 w-3.5 text-primary" />
              <span>
                {showRawFrontmatter ? "Hide Raw Frontmatter" : "Inspect Raw YAML / JSON Frontmatter"}
              </span>
              {showRawFrontmatter ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>

            {showRawFrontmatter && (
              <div className="mt-2 animate-in fade-in duration-200">
                <pre className="p-4 rounded-xl bg-zinc-900 dark:bg-zinc-950 border border-border/80 font-mono text-xs text-zinc-100 overflow-x-auto max-h-60 overflow-y-auto leading-relaxed shadow-inner">
                  {JSON.stringify(spec.frontmatter, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Main Reading Stage & Dynamic Outline TOC */}
      <div className="flex items-start gap-5">
        {/* Specification Prose Content */}
        <div className="flex-1 min-w-0">
          {spec.body ? (
            <AsciiDocRenderer
              content={spec.body}
              title={spec.title}
            />
          ) : (
            <div className="p-8 rounded-2xl bg-card border border-border text-center text-muted-foreground">
              No specification body declared for {spec.id}.
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
