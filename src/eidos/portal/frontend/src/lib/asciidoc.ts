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

export interface AsciiDocMermaidBlock {
  index: number;
  code: string;
  title?: string;
}

export interface RenderedAsciiDoc {
  html: string;
  outline: AsciiDocOutlineItem[];
  mermaidBlocks: AsciiDocMermaidBlock[];
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
  // Prefix heading ids with `spec-` so author headings can never clobber portal shell DOM ids.
  idprefix: "spec-",
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
  // Prevent DOM clobbering of window/document globals via passthrough markup while `spec-` preserves heading ids.
  SANITIZE_DOM: true,
};

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

interface TableCellLike {
  getInnerDocument?: () => DocumentLike | null | undefined;
}

interface TableRowsLike {
  head?: TableCellLike[][];
  body?: TableCellLike[][];
  foot?: TableCellLike[][];
}

interface BlockLike {
  getContext?: () => string | undefined;
  getStyle?: () => string | null | undefined;
  getAttribute?: (name: string | number) => unknown;
  getSource?: () => string | undefined;
  getTitle?: () => string | null | undefined;
  addRole?: (role: string) => unknown;
  rows?: TableRowsLike;
}

interface DocumentLike {
  findBy: (filter: (block: BlockLike) => boolean) => BlockLike[];
}

const MERMAID_ROLE_CLASS_PREFIX = "adoc-mermaid-";
let fallbackNonceCounter = 0;

function generateMermaidSlotNonce(): string {
  const cryptoObj = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (cryptoObj?.getRandomValues) {
    const bytes = new Uint8Array(12);
    cryptoObj.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  if (cryptoObj?.randomUUID) {
    return cryptoObj.randomUUID().replace(/-/g, "");
  }
  fallbackNonceCounter += 1;
  return `${Date.now().toString(36)}${fallbackNonceCounter.toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function isMermaidBlock(block: BlockLike): boolean {
  const style = String(block.getStyle?.() ?? "").trim().toLowerCase();
  if (style === "mermaid") return true;
  if (style === "source") {
    const rawLang = block.getAttribute?.("language") ?? block.getAttribute?.("2") ?? block.getAttribute?.(2);
    const lang = String(rawLang ?? "").trim().toLowerCase();
    return lang === "mermaid";
  }
  return false;
}

/**
 * Walk the Asciidoctor AST (`doc.findBy` + recursive `table.rows.{head,body,foot}` inner-document
 * traversal) to extract `[mermaid]` and `[source,mermaid]` blocks verbatim and tag them with a
 * per-parse CSPRNG nonced role before HTML conversion.
 */
function extractMermaidBlocks(
  rootDoc: DocumentLike,
  rolePrefix: string,
  out: AsciiDocMermaidBlock[],
  seen: WeakSet<object> = new WeakSet(),
): void {
  const matches = rootDoc.findBy((block) => {
    const ctx = String(block.getContext?.() ?? "").toLowerCase();
    return ctx === "listing" || ctx === "literal" || ctx === "table";
  });

  for (const block of matches) {
    if (!block || seen.has(block)) continue;
    seen.add(block);

    const ctx = String(block.getContext?.() ?? "").toLowerCase();
    if (ctx === "table") {
      const rowsObj = block.rows;
      if (rowsObj) {
        for (const section of ["head", "body", "foot"] as const) {
          const rows = rowsObj[section];
          if (!Array.isArray(rows)) continue;
          for (const row of rows) {
            if (!Array.isArray(row)) continue;
            for (const cell of row) {
              const innerDoc = cell?.getInnerDocument?.();
              if (innerDoc && !seen.has(innerDoc)) {
                seen.add(innerDoc);
                extractMermaidBlocks(innerDoc, rolePrefix, out, seen);
              }
            }
          }
        }
      }
      continue;
    }

    if (!isMermaidBlock(block)) continue;

    const index = out.length;
    const code = String(block.getSource?.() ?? "");
    const rawTitle = block.getTitle?.();
    const normalizedTitle = rawTitle
      ? textOf(DOMPurify.sanitize(String(rawTitle), SANITIZE_CONFIG))
      : "";

    out.push(normalizedTitle ? { index, code, title: normalizedTitle } : { index, code });
    block.addRole?.(`${rolePrefix}${index}`);
  }
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

function stripAndReplaceMermaidSlots(
  root: HTMLElement,
  rolePrefix: string,
  mermaidBlockCount: number,
): void {
  const verifiedBlocks: Array<{ element: HTMLElement; index: number }> = [];
  const claimedIndices = new Set<number>();

  // Unconditionally inspect and strip spoofed or stray Mermaid slot markers from sanitized HTML,
  // even when the document contains zero real AST-extracted Mermaid blocks.
  root
    .querySelectorAll<HTMLElement>(
      ".adoc-mermaid-slot, [data-mermaid-index], [class*='adoc-mermaid-']",
    )
    .forEach((el) => {
      if (
        mermaidBlockCount > 0 &&
        (el.classList.contains("listingblock") || el.classList.contains("literalblock"))
      ) {
        for (const cls of Array.from(el.classList)) {
          if (!cls.startsWith(rolePrefix)) continue;
          const suffix = cls.slice(rolePrefix.length);
          if (!/^\d+$/.test(suffix)) continue;
          const idx = Number.parseInt(suffix, 10);
          if (idx >= 0 && idx < mermaidBlockCount && !claimedIndices.has(idx)) {
            claimedIndices.add(idx);
            verifiedBlocks.push({ element: el, index: idx });
            break;
          }
        }
      }

      el.classList.remove("adoc-mermaid-slot");
      el.removeAttribute("data-mermaid-index");
      for (const cls of Array.from(el.classList)) {
        if (cls.startsWith(MERMAID_ROLE_CLASS_PREFIX)) {
          el.classList.remove(cls);
        }
      }
      if (el.getAttribute("class") === "") {
        el.removeAttribute("class");
      }
    });

  // Replace only CSPRNG-nonce-matched AST blocks with deterministic empty slot placeholders.
  for (const { element, index } of verifiedBlocks) {
    if (!element.isConnected && element.parentNode === null) continue;
    const slot = document.createElement("div");
    slot.className = "adoc-mermaid-slot";
    slot.setAttribute("data-mermaid-index", String(index));
    if (element.id) {
      slot.id = element.id;
    }
    element.replaceWith(slot);
  }
}

function decorate(root: HTMLElement, mermaidRolePrefix: string, mermaidBlockCount: number): void {
  // Safety net: the reader owns the outline, never an embedded TOC.
  root.querySelector(":scope > #toc")?.remove();

  // Strip untrusted passthrough markers unconditionally and convert only verified AST Mermaid blocks
  // into deterministic placeholders before `.adoc-code-bar` decoration runs.
  stripAndReplaceMermaidSlots(root, mermaidRolePrefix, mermaidBlockCount);

  // Document title + section headings: scroll margin and copyable anchor links.
  root.querySelectorAll<HTMLElement>("h1, h2, h3, h4").forEach((h) => {
    h.classList.add("spec-heading");
    if (!h.id && h.tagName === "H1") {
      const slug = slugify(h.textContent || "");
      h.id = slug ? `spec-${slug}` : "spec-document-title";
    }
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
  const elById = new Map<string, HTMLElement>();
  root.querySelectorAll<HTMLElement>("[id]").forEach((el) => elById.set(el.id, el));

  root.querySelectorAll<HTMLAnchorElement>("a[href]").forEach((a) => {
    if (a.classList.contains("heading-anchor-link")) return;
    const href = a.getAttribute("href") || "";

    if (href.startsWith("#")) {
      const frag = decodeURIComponent(href.slice(1));
      if (elById.has(frag)) return; // in-page anchor
      const prefixedFrag = `spec-${frag}`;
      const prefixedEl = elById.get(prefixedFrag);
      if (prefixedEl) {
        a.setAttribute("href", `#${encodeURIComponent(prefixedFrag)}`);
        const text = a.textContent || "";
        if (text === `[${frag}]`) {
          const cleanHeading = (prefixedEl.textContent || "").replace(/#$/, "").trim();
          a.textContent = cleanHeading || frag;
        }
        return;
      }
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

    if (/^(?:https?:)?\/\//i.test(href)) {
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

/** Convert an AsciiDoc specification body to sanitized, decorated HTML plus its heading outline and embedded Mermaid blocks. */
export async function renderAsciiDoc(content: string): Promise<RenderedAsciiDoc> {
  const doc = await load(content, {
    safe: "secure",
    standalone: false,
    attributes: ASCIIDOC_ATTRIBUTES,
  });

  const outline: AsciiDocOutlineItem[] = [];
  collectOutline(doc.getSections() as unknown as SectionLike[], outline);
  const title = doc.getDocumentTitle?.() ? textOf(String(doc.getDocumentTitle())) : undefined;

  const mermaidNonce = generateMermaidSlotNonce();
  const mermaidRolePrefix = `${MERMAID_ROLE_CLASS_PREFIX}m${mermaidNonce}-`;
  const mermaidBlocks: AsciiDocMermaidBlock[] = [];
  extractMermaidBlocks(doc as unknown as DocumentLike, mermaidRolePrefix, mermaidBlocks);

  const rawHtml = await doc.convert();
  const clean = DOMPurify.sanitize(String(rawHtml), SANITIZE_CONFIG);

  const tpl = document.createElement("template");
  tpl.innerHTML = clean;
  const wrapper = document.createElement("div");
  wrapper.append(tpl.content);
  decorate(wrapper, mermaidRolePrefix, mermaidBlocks.length);

  return { html: wrapper.innerHTML, outline, mermaidBlocks, title };
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
