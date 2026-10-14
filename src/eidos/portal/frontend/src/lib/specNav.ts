/**
 * In-portal specification navigation.
 *
 * AsciiDoc bodies reference other living specifications through `xref:` macros, `<<ID>>` cross
 * references and relative `link:path/to/spec.adoc[]` macros. The portal is a single-page app, so such
 * targets must never trigger a browser navigation: the reader marks them and publishes the raw target
 * on this tiny event bus; the App shell resolves it against the loaded spec catalog.
 */

export const SPEC_NAV_EVENT = "eidos:navigate-spec";

export interface SpecNavDetail {
  /** Raw target as written in the document: a spec ID (`CAP-EXP-001`) or a relative `.adoc`/`.html` path. */
  target: string;
}

export interface SpecLike {
  id: string;
  path: string;
}

const SPEC_ID_RE = /^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+$/;

export function looksLikeSpecId(value: string): boolean {
  return SPEC_ID_RE.test(value);
}

/** True for `foo/bar.adoc`, `../x.adoc#frag` and the `.html` form Asciidoctor emits for `xref:x.adoc[]`. */
export function isSpecDocumentPath(href: string): boolean {
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("#") || href.startsWith("/")) return false;
  const path = href.split("#")[0];
  return /\.(adoc|html)$/i.test(path);
}

export function requestSpecNavigation(target: string): void {
  window.dispatchEvent(new CustomEvent<SpecNavDetail>(SPEC_NAV_EVENT, { detail: { target } }));
}

function pathSegments(path: string): string[] {
  return path
    .split("#")[0]
    .replace(/\.html$/i, ".adoc")
    .split("/")
    .filter((seg) => seg !== "" && seg !== "." && seg !== "..");
}

/** Resolve a raw xref target to a spec of the catalog (by ID first, then by relative path suffix). */
export function resolveSpecTarget<T extends SpecLike>(specs: T[], rawTarget: string): T | undefined {
  const target = rawTarget.trim().replace(/^#/, "");
  if (!target) return undefined;

  const byId = specs.find((s) => s.id === target) ?? specs.find((s) => s.id.toLowerCase() === target.toLowerCase());
  if (byId) return byId;

  if (!isSpecDocumentPath(target)) return undefined;
  const wanted = pathSegments(target);
  if (wanted.length === 0) return undefined;

  const matches = specs.filter((s) => {
    const have = pathSegments(s.path);
    if (have.length < wanted.length) return false;
    return wanted.every((seg, i) => have[have.length - wanted.length + i] === seg);
  });
  if (matches.length === 0) return undefined;
  // Prefer the most specific (shortest) path when a bare filename is ambiguous.
  return matches.sort((a, b) => a.path.length - b.path.length)[0];
}
