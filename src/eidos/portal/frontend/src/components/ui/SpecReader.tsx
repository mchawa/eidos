import * as React from "react";
import {
  FileText,
  ShieldCheck,
  Layers,
  List,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { AsciiDocRenderer, useAsciiDocOutline } from "@/components/ui/AsciiDocRenderer";
import { requestSpecNavigation } from "@/lib/specNav";
import { useResolvedSpecBody } from "@/lib/specChunks";
import {
  formatDisplayTitle,
  formatHumanProse,
  formatSeverity,
  formatSpecType,
  formatStatus,
  humanizeInvariantTitle,
  humanizeToken,
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

export interface SpecReaderNavLink {
  id: string;
  title: string;
  type?: string;
}

export interface SpecReaderProps {
  spec: SpecReaderItem;
  onClose?: () => void;
  className?: string;
  isModal?: boolean;
  breadcrumb?: string[];
  prevSpec?: SpecReaderNavLink | null;
  nextSpec?: SpecReaderNavLink | null;
  onNavigateDoc?: (id: string) => void;
  hideHeaderTitle?: boolean;
}

export function SpecReader({
  spec,
  className,
  isModal = false,
  breadcrumb,
  prevSpec,
  nextSpec,
  onNavigateDoc,
  hideHeaderTitle = false,
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

  // Extract key metadata from frontmatter
  const fm = effectiveFrontmatter || {};
  const domainBinding = fm.domain;
  const containerBinding = fm.container;
  const rawInvariants: unknown[] = Array.isArray(fm.technical?.invariants)
    ? fm.technical.invariants
    : Array.isArray(fm.invariants)
    ? fm.invariants
    : Array.isArray(fm.blast_radius?.invariants_to_enforce)
    ? fm.blast_radius.invariants_to_enforce
    : [];
  const normalizedRules = React.useMemo(() => {
    return rawInvariants
      .map((entry, idx) => {
        if (typeof entry === "string") {
          return {
            key: entry || `rule-${idx}`,
            id: entry,
            title: resolveEntityTitle(entry, labelIndex, humanizeToken(entry)),
            rule: "",
            category: undefined as string | undefined,
            severity: undefined as string | undefined,
            verified: false,
            isRefOnly: true,
          };
        }
        if (entry && typeof entry === "object") {
          const obj = entry as Record<string, any>;
          return {
            key: String(obj.id || `rule-${idx}`),
            id: String(obj.id || ""),
            title: humanizeInvariantTitle({
              id: String(obj.id || ""),
              title: obj.title ? String(obj.title) : undefined,
              category: obj.category ? String(obj.category) : "",
            }),
            rule: formatHumanProse(String(obj.rule || ""), labelIndex),
            category: obj.category ? String(obj.category) : undefined,
            severity: obj.severity ? String(obj.severity) : undefined,
            verified: Boolean(obj.test_binding && obj.test_binding !== "N/A"),
            isRefOnly: false,
          };
        }
        return null;
      })
      .filter((r): r is NonNullable<typeof r> => r !== null);
  }, [rawInvariants, labelIndex]);

  const rulesSectionId = React.useMemo(
    () =>
      outline.some((item) => item.id === "spec-verified-rules")
        ? "spec-frontmatter-verified-rules"
        : "spec-verified-rules",
    [outline]
  );

  const effectiveOutline = React.useMemo(() => {
    if (normalizedRules.length === 0) return outline;
    return [
      ...outline,
      { id: rulesSectionId, text: `Verified Rules (${normalizedRules.length})`, level: 2 },
    ];
  }, [outline, normalizedRules.length, rulesSectionId]);

  // Track active heading on scroll inside modal/scrollable container or window
  React.useEffect(() => {
    if (effectiveOutline.length === 0) return;

    const findScrollParent = (node: HTMLElement | null): HTMLElement | Window => {
      let cur = node?.parentElement || null;
      while (cur && cur !== document.body) {
        const style = window.getComputedStyle(cur);
        if (/(auto|scroll)/.test(style.overflowY) && cur.scrollHeight > cur.clientHeight + 1) {
          return cur;
        }
        cur = cur.parentElement;
      }
      const mainScroll = document.getElementById("main-scroll");
      return mainScroll || window;
    };

    const scrollTarget = findScrollParent(stageRef.current);

    const handleScroll = () => {
      const headings = effectiveOutline
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
  }, [effectiveOutline]);

  const handleTocClick = (e: React.MouseEvent<HTMLAnchorElement>, id: string) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveHeadingId(id);
    }
  };

  const domainTitle = domainBinding ? resolveEntityTitle(String(domainBinding), labelIndex) : "";
  const containerTitle = containerBinding ? resolveEntityTitle(String(containerBinding), labelIndex) : "";
  const displayTitle = formatDisplayTitle(spec.title, spec.id);
  const suppressTitle = hideHeaderTitle || isModal;

  const handleEntityNavigate = React.useCallback(
    (ref: string) => {
      if (onNavigateDoc) {
        onNavigateDoc(ref);
      } else {
        requestSpecNavigation(ref);
      }
    },
    [onNavigateDoc]
  );

  return (
    <div ref={stageRef} className={cn("spec-reader-stage w-full", className)}>
      {/* Main Reading Stage & Quiet Borderless Outline TOC */}
      <div className="flex items-start gap-6">
        {/* Document Reading Column */}
        <article className="flex-1 min-w-0 max-w-5xl mx-auto">
          {/* Editorial Document Header (Borderless, Single Title, Compact Inline Meta) */}
          <header className="pb-5 mb-6 border-b border-border/70 space-y-3">
            {breadcrumb && breadcrumb.length > 0 && (
              <nav aria-label="Document location" className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                {breadcrumb.map((crumb, idx) => (
                  <React.Fragment key={`${crumb}-${idx}`}>
                    {idx > 0 && <ChevronRight aria-hidden="true" className="h-3 w-3 opacity-60" />}
                    <span className={idx === breadcrumb.length - 1 ? "text-foreground/80 font-medium" : undefined}>
                      {crumb}
                    </span>
                  </React.Fragment>
                ))}
              </nav>
            )}

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

            {!suppressTitle && (
              <h1 className="text-2xl sm:text-3xl font-extrabold text-foreground tracking-tight leading-tight">
                {displayTitle}
              </h1>
            )}

            {/* High-Level Relationship Links (Clickable Cross-Entity Navigation) */}
            {(domainTitle || containerTitle || normalizedRules.length > 0) && (
              <div className="flex flex-wrap items-center gap-2 pt-1 text-xs">
                {domainBinding && domainTitle && (
                  <button
                    type="button"
                    onClick={() => handleEntityNavigate(String(domainBinding))}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-purple-500/10 hover:bg-purple-500/20 text-purple-500 dark:text-purple-400 border border-purple-500/20 font-medium transition-colors cursor-pointer"
                    title={`Open ${domainTitle}`}
                  >
                    <Layers className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Domain: {domainTitle}</span>
                  </button>
                )}
                {containerBinding && containerTitle && (
                  <button
                    type="button"
                    onClick={() => handleEntityNavigate(String(containerBinding))}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-blue-500/10 hover:bg-blue-500/20 text-blue-500 dark:text-blue-400 border border-blue-500/20 font-medium transition-colors cursor-pointer"
                    title={`Open ${containerTitle}`}
                  >
                    <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Container: {containerTitle}</span>
                  </button>
                )}
                {normalizedRules.length > 0 && (
                  <a
                    href={`#${rulesSectionId}`}
                    onClick={(e) => handleTocClick(e, rulesSectionId)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-500 dark:text-emerald-400 border border-emerald-500/20 font-medium transition-colors cursor-pointer"
                    title="Jump to Verified Rules"
                  >
                    <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>
                      {normalizedRules.length} {normalizedRules.length === 1 ? "Rule" : "Rules"} Verified
                    </span>
                  </a>
                )}
              </div>
            )}
          </header>

          {/* Document Prose Content (Borderless Canvas, Deduplicated Leading H1) */}
          {loadingChunk ? (
            <div className="py-12 text-center text-muted-foreground italic text-sm">
              Loading document…
            </div>
          ) : resolvedBody ? (
            <AsciiDocRenderer
              specId={spec.id}
              bodyChunk={spec.body_chunk}
              content={resolvedBody}
              title={displayTitle}
              variant="document"
              hideLeadingTitle={true}
              onNavigateSpec={onNavigateDoc}
            />
          ) : (
            <div className="py-12 text-center text-muted-foreground text-sm">
              No content available for this document.
            </div>
          )}

          {/* Frontmatter Verified Rules (Directly Readable in Docs Reader) */}
          {normalizedRules.length > 0 && (
            <section
              id={rulesSectionId}
              aria-labelledby="spec-verified-rules-heading"
              className="mt-8 pt-6 border-t border-border/70 space-y-4 spec-heading"
            >
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-emerald-500 shrink-0" aria-hidden="true" />
                <h2
                  id="spec-verified-rules-heading"
                  className="text-lg sm:text-xl font-bold text-foreground tracking-tight"
                >
                  Verified Rules ({normalizedRules.length})
                </h2>
              </div>

              <div className="divide-y divide-border/60 border-y border-border/60">
                {normalizedRules.map((item) => (
                  <div key={item.key} className="py-3.5 space-y-1.5 max-w-[90ch]">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        {item.isRefOnly && item.id ? (
                          <button
                            type="button"
                            onClick={() => handleEntityNavigate(item.id)}
                            className="text-sm font-semibold text-primary hover:underline text-left cursor-pointer"
                          >
                            {item.title}
                          </button>
                        ) : (
                          <span className="text-sm font-semibold text-foreground">
                            {item.title}
                          </span>
                        )}
                        {item.category && (
                          <Badge variant="outline" className="text-[10px] py-0">
                            {humanizeToken(item.category)}
                          </Badge>
                        )}
                        {item.severity && (
                          <Badge variant="outline" className="text-[10px] py-0">
                            {formatSeverity(item.severity)}
                          </Badge>
                        )}
                      </div>
                      {item.verified && (
                        <span className="text-[11px] font-medium text-emerald-600 dark:text-emerald-400 shrink-0">
                          Test Verified
                        </span>
                      )}
                    </div>
                    {item.rule && (
                      <p className="text-sm text-muted-foreground leading-relaxed">
                        {item.rule}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Previous / Next Document Navigation */}
          {(prevSpec || nextSpec) && onNavigateDoc && (
            <nav
              aria-label="Adjacent specifications"
              className="mt-10 pt-6 border-t border-border/70 grid grid-cols-1 sm:grid-cols-2 gap-3"
            >
              {prevSpec ? (
                <button
                  type="button"
                  onClick={() => onNavigateDoc(prevSpec.id)}
                  className="group flex flex-col items-start gap-1 p-3.5 rounded-xl border border-border/60 hover:border-primary/50 hover:bg-muted/30 transition-colors text-left cursor-pointer"
                >
                  <span className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
                    <ChevronLeft className="h-3.5 w-3.5 group-hover:-translate-x-0.5 transition-transform" aria-hidden="true" />
                    Previous
                  </span>
                  <span className="text-sm font-semibold text-foreground group-hover:text-primary transition-colors line-clamp-1">
                    {prevSpec.title}
                  </span>
                </button>
              ) : (
                <div />
              )}
              {nextSpec ? (
                <button
                  type="button"
                  onClick={() => onNavigateDoc(nextSpec.id)}
                  className="group flex flex-col items-end gap-1 p-3.5 rounded-xl border border-border/60 hover:border-primary/50 hover:bg-muted/30 transition-colors text-right cursor-pointer"
                >
                  <span className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
                    Next
                    <ChevronRight className="h-3.5 w-3.5 group-hover:translate-x-0.5 transition-transform" aria-hidden="true" />
                  </span>
                  <span className="text-sm font-semibold text-foreground group-hover:text-primary transition-colors line-clamp-1">
                    {nextSpec.title}
                  </span>
                </button>
              ) : (
                <div />
              )}
            </nav>
          )}
        </article>

        {/* Quiet Borderless Sticky On-This-Page Table of Contents (TOC) */}
        {effectiveOutline.length >= 2 && (
          <aside
            aria-label="On this page"
            className="hidden xl:block w-52 shrink-0 sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto text-xs space-y-2.5"
          >
            <div className="flex items-center gap-1.5 font-semibold uppercase tracking-wider text-[11px] text-muted-foreground pb-1 pl-3">
              <List className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
              <span>On This Page</span>
            </div>
            <nav className="border-l border-border/60 space-y-0.5">
              {effectiveOutline.map((item) => (
                <a
                  key={item.id}
                  href={`#${item.id}`}
                  onClick={(e) => handleTocClick(e, item.id)}
                  className={cn(
                    "block py-1 pl-3 -ml-px border-l-2 transition-colors leading-snug truncate",
                    item.level === 3 ? "pl-5 text-[11px]" : "text-xs",
                    activeHeadingId === item.id
                      ? "border-primary text-primary font-semibold"
                      : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
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
