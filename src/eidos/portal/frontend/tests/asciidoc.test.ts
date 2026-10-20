import { beforeAll, describe, expect, test } from "vitest";
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { renderAsciiDoc, extractAsciiDocOutline } from "@/lib/asciidoc";
import { isSpecDocumentPath, looksLikeSpecId, resolveSpecTarget } from "@/lib/specNav";

// Build-time only (Vitest + jsdom). The shipped portal bundle needs no Node.js (INV-PORTAL-ZERO-NODE-DEP).

const REPO_ROOT = path.resolve(import.meta.dirname, "../../../../..");
const EIDOS_DIR = path.join(REPO_ROOT, "eidos");

function walkAdoc(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) walkAdoc(full, out);
    else if (entry.endsWith(".adoc")) out.push(full);
  }
  return out;
}

/** Strip YAML frontmatter the same way the portal generator hands bodies to the reader. */
function bodyOf(source: string): string {
  const match = source.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n/);
  return match ? source.slice(match[0].length) : source;
}

describe("INV-PORTAL-READING-ERGONOMICS: Asciidoctor.js reader", () => {
  test("INV-PORTAL-READING-ERGONOMICS: [quote] block renders as blockquote with attribution", async () => {
    const { html } = await renderAsciiDoc(
      ["= Doc", "", "== Manifesto", "", "[quote, Eidos Manifesto]", "Specs are living *code*.", ""].join("\n"),
    );
    expect(html).toContain('<div class="quoteblock">');
    expect(html).toMatch(/<blockquote>\s*Specs are living <strong>code<\/strong>\.\s*<\/blockquote>/);
    expect(html).toContain("Eidos Manifesto");
    expect(html).not.toContain("[quote");
  });

  test("definition lists render as <dl>/<dt>/<dd>", async () => {
    const { html } = await renderAsciiDoc("CAP-A:: First capability\nCAP-B::\n  Second capability\n");
    expect(html).toContain('<div class="dlist">');
    expect(html).toMatch(/<dt class="hdlist1">CAP-A<\/dt>/);
    expect(html).toMatch(/<dd>\s*<p>First capability<\/p>\s*<\/dd>/);
    expect(html).toMatch(/<dt class="hdlist1">CAP-B<\/dt>/);
  });

  test("code callouts render conums and a callout list", async () => {
    const { html } = await renderAsciiDoc(
      ["[source,python]", "----", "x = 1 <1>", "y = 2 <2>", "----", "<1> first", "<2> second", ""].join("\n"),
    );
    expect(html).toContain('<b class="conum">(1)</b>');
    expect(html).toContain('<b class="conum">(2)</b>');
    expect(html).toContain('<div class="colist arabic">');
    expect(html).toContain("adoc-copy-btn");
    expect(html).toContain("PYTHON".toLowerCase());
  });

  test("multi-line table cells, header row and column specs render", async () => {
    const { html } = await renderAsciiDoc(
      [
        '[cols="1,2", options="header"]',
        "|===",
        "| Name | Responsibility",
        "",
        "| `CMP-A`",
        "| First line",
        "",
        "second paragraph of the same cell",
        "",
        "| `CMP-B` | One-liner",
        "|===",
        "",
      ].join("\n"),
    );
    expect(html).toContain("adoc-table-scroll");
    expect(html).toMatch(/<th class="tableblock[^"]*">Name<\/th>/);
    expect(html).toMatch(/<p class="tableblock">First line<\/p>\s*<p class="tableblock">second paragraph of the same cell<\/p>/);
    expect(html).toContain("<code>CMP-B</code>");
    expect(html.match(/<tr>/g)?.length).toBe(3);
  });

  test("admonitions (paragraph and delimited) render", async () => {
    const { html } = await renderAsciiDoc(
      ["NOTE: inline note", "", "[WARNING]", "====", "Delimited *warning*", "====", ""].join("\n"),
    );
    expect(html).toContain('<div class="admonitionblock note">');
    expect(html).toContain('<div class="admonitionblock warning">');
    expect(html).toContain("Delimited <strong>warning</strong>");
  });

  test("xref:, <<ID>> and relative link:*.adoc[] map to in-portal spec navigation", async () => {
    const { html } = await renderAsciiDoc(
      [
        "== Links",
        "",
        "See xref:CNT-DEMO-CORE[the core], <<CAP-EXP-001>>, link:../personas/product-lead.adoc[Persona], ",
        "xref:containers/core.adoc[Core container], <<links>> and https://example.com[Example].",
        "",
      ].join("\n"),
    );
    const tpl = document.createElement("template");
    tpl.innerHTML = html;
    const refs = [...tpl.content.querySelectorAll("a[data-spec-ref]")].map((a) => a.getAttribute("data-spec-ref"));
    expect(refs).toEqual(["CNT-DEMO-CORE", "CAP-EXP-001", "../personas/product-lead.adoc", "containers/core.html"]);
    // Unresolved <<ID>> is shown without brackets, and spec links never leave the SPA.
    expect(tpl.content.querySelector('a[data-spec-ref="CAP-EXP-001"]')?.textContent).toBe("CAP-EXP-001");
    for (const a of tpl.content.querySelectorAll("a[data-spec-ref]")) {
      expect(a.getAttribute("href")).toMatch(/^#spec\//);
      expect(a.hasAttribute("target")).toBe(false);
    }
    // In-page anchor stays an anchor; external links open safely in a new tab.
    expect(tpl.content.querySelector('a[href="#links"]')?.hasAttribute("data-spec-ref")).toBe(false);
    const ext = tpl.content.querySelector('a[href="https://example.com"]');
    expect(ext?.getAttribute("target")).toBe("_blank");
    expect(ext?.getAttribute("rel")).toContain("noopener");
  });

  test("spec targets resolve against the catalog by id and by relative path", () => {
    const specs = [
      { id: "CNT-DEMO-CORE", path: "eidos/architecture/containers/core.adoc" },
      { id: "PER-PRODUCT-LEAD", path: "eidos/personas/product-lead.adoc" },
      { id: "SYS-DEMO-001", path: "eidos/system.adoc" },
    ];
    expect(resolveSpecTarget(specs, "CNT-DEMO-CORE")?.id).toBe("CNT-DEMO-CORE");
    expect(resolveSpecTarget(specs, "#cnt-demo-core")?.id).toBe("CNT-DEMO-CORE");
    expect(resolveSpecTarget(specs, "../personas/product-lead.adoc")?.id).toBe("PER-PRODUCT-LEAD");
    expect(resolveSpecTarget(specs, "containers/core.html")?.id).toBe("CNT-DEMO-CORE");
    expect(resolveSpecTarget(specs, "../system.adoc#vision")?.id).toBe("SYS-DEMO-001");
    expect(resolveSpecTarget(specs, "nope.adoc")).toBeUndefined();
    expect(looksLikeSpecId("CAP-EXP-001")).toBe(true);
    expect(looksLikeSpecId("section-one")).toBe(true); // shape only; the renderer first checks in-page ids
    expect(isSpecDocumentPath("https://x.io/a.adoc")).toBe(false);
  });

  test("secure mode: include:: is not resolved and raw HTML passthrough is sanitized", async () => {
    const { html } = await renderAsciiDoc(
      [
        "include::/etc/passwd[]",
        "",
        "++++",
        '<script>window.__pwned = true</script><img src=x onerror="window.__pwned = true"><iframe src="https://evil.example"></iframe><style>@import url(https://evil.example/x.css)</style>',
        "++++",
        "",
        "link:javascript:alert(1)[click]",
        "",
      ].join("\n"),
    );
    expect(html).not.toContain("root:x:");
    expect(html).not.toMatch(/<script|<iframe|<style|onerror/i);
    expect(html).not.toMatch(/evil\.example/);
    expect(html).not.toMatch(/href="javascript:/i);
  });

  test("outline ids equal the rendered heading ids (levels 2 and 3)", async () => {
    const body = ["= Title", "", "== Problem Statement", "", "=== Detail A", "", "== Problem Statement", "", "=== Title", ""].join("\n");
    const outline = await extractAsciiDocOutline(body);
    expect(outline.map((o) => [o.level, o.text])).toEqual([
      [2, "Problem Statement"],
      [3, "Detail A"],
      [2, "Problem Statement"],
      [3, "Title"],
    ]);
    const ids = outline.map((o) => o.id);
    expect(new Set(ids).size).toBe(ids.length);
    const { html } = await renderAsciiDoc(body);
    for (const id of ids) expect(html).toContain(`id="${id}"`);
    expect(html).toContain("spec-heading");
    expect(html).toContain("heading-anchor-link");
    expect(html).not.toContain('id="toc"');
  });
});

describe("every living specification converts", () => {
  const files = walkAdoc(EIDOS_DIR);

  test("repository has living specs to convert", () => {
    expect(files.length).toBeGreaterThan(30);
  });

  test("INV-PORTAL-READING-ERGONOMICS: every eidos/**/*.adoc body renders without error or raw-source fallback", async () => {
    const failures: string[] = [];
    for (const file of files) {
      const rel = path.relative(REPO_ROOT, file);
      try {
        const body = bodyOf(readFileSync(file, "utf8"));
        const { html, outline } = await renderAsciiDoc(body);
        if (!html.trim()) failures.push(`${rel}: empty output`);
        // Markers that survive only when the AsciiDoc source was not converted.
        for (const raw of ["|===", "[quote", "[source", "\n----\n", "xref:", "link:"]) {
          // Allow markers that live inside rendered code blocks (specs documenting AsciiDoc itself).
          const tpl = document.createElement("template");
          tpl.innerHTML = html;
          tpl.content.querySelectorAll("pre, code").forEach((n) => n.remove());
          if ((tpl.content.textContent || "").includes(raw.trim())) failures.push(`${rel}: unconverted marker ${JSON.stringify(raw)}`);
        }
        const ids = outline.map((o) => o.id);
        if (new Set(ids).size !== ids.length) failures.push(`${rel}: duplicate heading ids`);
        for (const id of ids) if (!html.includes(`id="${id}"`)) failures.push(`${rel}: outline id ${id} missing in HTML`);
      } catch (err) {
        failures.push(`${rel}: ${(err as Error).message}`);
      }
    }
    expect(failures).toEqual([]);
  });

  test("eidos/system.adoc [quote] block is rendered", async () => {
    const body = bodyOf(readFileSync(path.join(EIDOS_DIR, "system.adoc"), "utf8"));
    const { html } = await renderAsciiDoc(body);
    expect(html).toContain('class="quoteblock"');
    expect(html).toContain("Eidos Manifesto");
  });
});

// --- Mermaid sources emitted by the portal generator parse with the bundled mermaid -------------

const PY_DUMP = `
import orjson, sys
from pathlib import Path
from eidos.portal.generator import get_portal_data
d = get_portal_data(Path(sys.argv[1]))
out = {"c4_context": d["diagrams"]["c4_context"], "c4_containers": d["diagrams"]["c4_containers"]}
for k, v in (d["diagrams"].get("c4_components") or {}).items():
    out["c4_components:" + k] = v
for s in d.get("screens", []):
    if s.get("mermaid"): out["screen:" + s["id"]] = s["mermaid"]
for f in d.get("flows", []):
    if f.get("mermaid"): out["flow:" + f["id"]] = f["mermaid"]
print(orjson.dumps(out).decode("utf-8"))
`;

function loadMermaidSources(): Record<string, string> | null {
  const candidates = [process.env.EIDOS_PYTHON, path.join(REPO_ROOT, ".venv/bin/python"), "python3"].filter(
    (c): c is string => Boolean(c) && (c === "python3" || existsSync(c as string)),
  );
  for (const py of candidates) {
    try {
      const raw = execFileSync(py, ["-c", PY_DUMP, EIDOS_DIR], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
      return JSON.parse(raw.trim().split("\n").pop() as string);
    } catch {
      // try next interpreter
    }
  }
  return null;
}

describe("Mermaid sources from generator.py parse with the bundled mermaid", () => {
  let sources: Record<string, string> | null = null;
  beforeAll(() => {
    sources = loadMermaidSources();
  });

  test("python interpreter with eidos installed is available (set EIDOS_PYTHON otherwise)", () => {
    expect(sources, "no python interpreter could import eidos.portal.generator").not.toBeNull();
  });

  test("INV-PORTAL-INTERACTIVE-C4: every C4 / screen / flow diagram passes mermaid.parse()", async () => {
    expect(sources).not.toBeNull();
    const mermaid = (await import("mermaid")).default;
    mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
    const entries = Object.entries(sources!);
    expect(entries.length).toBeGreaterThanOrEqual(8);
    const failures: string[] = [];
    for (const [name, src] of entries) {
      try {
        const result = await mermaid.parse(src);
        if (!result) failures.push(`${name}: parse returned falsy`);
      } catch (err) {
        failures.push(`${name}: ${(err as Error).message}`);
      }
    }
    expect(failures).toEqual([]);
  });
});
