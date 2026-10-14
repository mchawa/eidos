import { load } from "@asciidoctor/core";
import DOMPurify from "dompurify";
import { isSpecDocumentPath, looksLikeSpecId } from "@/lib/specNav";

/**
 * Asciidoctor.js-backed rendering pipeline for living specification bodies.
 *
 *  - Parsing/conversion by `@asciidoctor/core` in `secure` safe mode (no file system, no `include::`,
 *    no remote fetches by the converter).
 *  - HTML output is sanitized (passthrough blocks can still carry raw markup) and then decorated:
 *    heading anchors, code-block copy buttons, external-link hardening and in-portal spec navigation
 *    for `xref:`, `<<ID>>` and relative `link:*.adoc[]` targets.
 *
 * Nothing here falls back to raw source: failures reject so that callers (and the build-time Vitest
 * suite) see them.
 */

export interface AsciiDocOutlineItem {
  id: string;
  text: string;
  level: 2 | 3;
}

export interface RenderedAsciiDoc {
  html: string;
  outline: AsciiDocOutlineItem[];
  title?: string;
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .trim();
}

const ASCIIDOC_ATTRIBUTES = {
  // Heading ids mirror the portal slug convention (`problem-statement`, not `_problem_statement`).
  idprefix: "",
  idseparator: "-",
  // The reader renders its own sticky "On This Page" outline.
  "toc!": "",
  showtitle: "",
  "skip-front-matter": "",
  "source-highlighter!": "",
  "icons!": "",
  nofooter: "",
};

const SANITIZE_CONFIG = {
  FORBID_TAGS: ["style", "link", "meta", "iframe", "frame", "frameset", "object", "embed", "form", "input", "button", "svg", "math", "base"],
  FORBID_ATTR: ["style", "srcset"],
  // Section ids such as `title` or `location` must survive; heading anchors are part of the contract.
  SANITIZE_DOM: false,
} as const;

function textOf(html: string): string {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  return (tpl.content.textContent || "").replace(/\s+/g, " ").trim();
}

interface SectionLike {
  getId(): string | undefined;
  getLevel(): number;
  getTitle(): string | undefined;
  getSections(): SectionLike[];
}

function collectOutline(sections: SectionLike[], out: AsciiDocOutlineItem[]): void {
  for (const section of sections) {
    const level = section.getLevel();
    const id = section.getId();
    if (id && (level === 1 || level === 2)) {
      out.push({ id, text: textOf(section.getTitle() || ""), level: (level + 1) as 2 | 3 });
    }
    if (level < 2) collectOutline(section.getSections(), out);
  }
}

function decorate(root: HTMLElement): void {
  // Safety net: the reader owns the outline, never an embedded TOC.
  root.querySelector(":scope > #toc")?.remove();

  // Document title + section headings: scroll margin and copyable anchor links.
  root.querySelectorAll<HTMLElement>("h1, h2, h3, h4").forEach((h) => {
    h.classList.add("spec-heading");
    if (!h.id && h.tagName === "H1") h.id = slugify(h.textContent || "");
    if (h.id && (h.tagName === "H2" || h.tagName === "H3")) {
      const a = document.createElement("a");
      a.className = "heading-anchor-link";
      a.href = `#${h.id}`;
      a.title = h.tagName === "H2" ? "Copy link to section" : "Copy link to subsection";
      a.setAttribute("aria-label", a.title);
      a.textContent = "#";
      h.appendChild(a);
    }
  });

  // Code / literal blocks: language label + copy button.
  root.querySelectorAll<HTMLElement>(".listingblock, .literalblock").forEach((block) => {
    const pre = block.querySelector("pre");
    if (!pre) return;
    const lang = pre.querySelector("code")?.getAttribute("data-lang") || "";
    const bar = document.createElement("div");
    bar.className = "adoc-code-bar";
    const label = document.createElement("span");
    label.className = "adoc-code-lang";
    label.textContent = lang && lang !== "text" ? lang : "code";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "adoc-copy-btn";
    btn.title = "Copy code to clipboard";
    btn.textContent = "Copy";
    bar.append(label, btn);
    const content = block.querySelector(".content");
    (content ?? block).insertBefore(bar, pre);
  });

  // Tables scroll horizontally inside a bordered wrapper instead of overflowing the reading column.
  root.querySelectorAll<HTMLTableElement>("table.tableblock").forEach((table) => {
    const wrap = document.createElement("div");
    wrap.className = "adoc-table-scroll";
    table.replaceWith(wrap);
    wrap.appendChild(table);
  });

  // Links: in-portal spec navigation, in-page anchors, external hardening.
  const ids = new Set<string>();
  root.querySelectorAll<HTMLElement>("[id]").forEach((el) => ids.add(el.id));

  root.querySelectorAll<HTMLAnchorElement>("a[href]").forEach((a) => {
    if (a.classList.contains("heading-anchor-link")) return;
    const href = a.getAttribute("href") || "";

    if (href.startsWith("#")) {
      const frag = decodeURIComponent(href.slice(1));
      if (ids.has(frag)) return; // in-page anchor
      if (looksLikeSpecId(frag)) {
        markSpecLink(a, frag);
        // Asciidoctor renders unresolved <<ID>> references as "[ID]".
        const text = a.textContent || "";
        if (text === `[${frag}]`) a.textContent = frag;
      }
      return;
    }

    if (isSpecDocumentPath(href)) {
      markSpecLink(a, href);
      return;
    }

    if (/^https?:\/\//i.test(href)) {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.classList.add("adoc-external-link");
    }
  });
}

function markSpecLink(a: HTMLAnchorElement, target: string): void {
  a.setAttribute("data-spec-ref", target);
  a.setAttribute("href", `#spec/${encodeURIComponent(target)}`);
  a.classList.add("adoc-spec-link");
  a.removeAttribute("target");
}

/** Convert an AsciiDoc specification body to sanitized, decorated HTML plus its heading outline. */
export async function renderAsciiDoc(content: string): Promise<RenderedAsciiDoc> {
  const doc = await load(content, {
    safe: "secure",
    standalone: false,
    attributes: ASCIIDOC_ATTRIBUTES,
  });

  const outline: AsciiDocOutlineItem[] = [];
  collectOutline(doc.getSections() as unknown as SectionLike[], outline);
  const title = doc.getDocumentTitle?.() ? textOf(String(doc.getDocumentTitle())) : undefined;

  const rawHtml = await doc.convert();
  const clean = DOMPurify.sanitize(String(rawHtml), SANITIZE_CONFIG);

  const tpl = document.createElement("template");
  tpl.innerHTML = clean;
  const wrapper = document.createElement("div");
  wrapper.append(tpl.content);
  decorate(wrapper);

  return { html: wrapper.innerHTML, outline, title };
}

const CACHE_LIMIT = 64;
const renderCache = new Map<string, Promise<RenderedAsciiDoc>>();

/** Memoized {@link renderAsciiDoc}; the reader and its outline share one parse per body. */
export function renderAsciiDocCached(content: string): Promise<RenderedAsciiDoc> {
  let pending = renderCache.get(content);
  if (!pending) {
    pending = renderAsciiDoc(content);
    pending.catch(() => renderCache.delete(content));
    renderCache.set(content, pending);
    if (renderCache.size > CACHE_LIMIT) {
      const oldest = renderCache.keys().next().value;
      if (oldest !== undefined) renderCache.delete(oldest);
    }
  }
  return pending;
}

/**
 * Hierarchical table of contents (`==` → level 2, `===` → level 3). The ids are exactly the ones
 * emitted into the rendered HTML because both come from the same Asciidoctor parse.
 */
export async function extractAsciiDocOutline(content: string): Promise<AsciiDocOutlineItem[]> {
  if (!content) return [];
  return (await renderAsciiDocCached(content)).outline;
}
