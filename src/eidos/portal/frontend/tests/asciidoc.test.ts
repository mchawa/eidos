(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import * as React from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { LoggerManager, MemoryLogger } from "@asciidoctor/core";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { MermaidDiagram } from "@/components/ui/MermaidDiagram";
import { renderAsciiDoc, extractAsciiDocOutline } from "@/lib/asciidoc";
import {
  buildMermaidConfig,
  getConfiguredMermaid,
  loadMermaid,
  renderMermaidSvg,
  sanitizeMermaidCode,
} from "@/lib/mermaid";
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
        "xref:containers/core.adoc[Core container], <<links>>, link://cdn.example.com/doc[ProtoRel] and https://example.com[Example].",
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
    // In-page anchor uses `spec-` prefixed heading id and stays an anchor; external & protocol-relative links open safely.
    expect(tpl.content.querySelector('a[href="#spec-links"]')?.hasAttribute("data-spec-ref")).toBe(false);
    const ext = tpl.content.querySelector('a[href="https://example.com"]');
    expect(ext?.getAttribute("target")).toBe("_blank");
    expect(ext?.getAttribute("rel")).toBe("noopener noreferrer");
    const protoRel = tpl.content.querySelector('a[href="//cdn.example.com/doc"]');
    expect(protoRel?.getAttribute("target")).toBe("_blank");
    expect(protoRel?.getAttribute("rel")).toBe("noopener noreferrer");
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
    expect(resolveSpecTarget(specs, "/work/repo/eidos/architecture/containers/core.adoc")?.id).toBe("CNT-DEMO-CORE");
    expect(resolveSpecTarget(specs, "containers/core.html")?.id).toBe("CNT-DEMO-CORE");
    expect(resolveSpecTarget(specs, "../system.adoc#vision")?.id).toBe("SYS-DEMO-001");
    expect(resolveSpecTarget(specs, "nope.adoc")).toBeUndefined();
    expect(looksLikeSpecId("CAP-EXP-001")).toBe(true);
    expect(looksLikeSpecId("section-one")).toBe(true); // shape only; the renderer first checks in-page ids
    expect(isSpecDocumentPath("https://x.io/a.adoc")).toBe(false);
  });

  test("secure mode: include:: is not resolved, raw HTML is sanitized, and DOM clobbering is blocked", async () => {
    const { html } = await renderAsciiDoc(
      [
        "== c4DiagramContainer",
        "",
        "include::/etc/passwd[]",
        "",
        "++++",
        '<script>window.__pwned = true</script><img src=x onerror="window.__pwned = true"><iframe src="https://evil.example"></iframe><style>@import url(https://evil.example/x.css)</style><a id="location" name="cookie" href="https://evil.example">clobber</a>',
        "++++",
        "",
        "link:javascript:alert(1)[click]",
        "",
      ].join("\n"),
    );
    expect(html).not.toContain("root:x:");
    expect(html).not.toMatch(/<script|<iframe|<style|onerror/i);
    expect(html).not.toMatch(/id="location"|name="cookie"/i);
    expect(html).not.toContain('id="c4diagramcontainer"');
    expect(html).toContain('id="spec-c4diagramcontainer"');
    expect(html).not.toMatch(/href="javascript:/i);
  });

  test("INV-PORTAL-XSS-AND-SERVER-ISOLATION: Mermaid config enforces strict securityLevel and strips unsafe directives", () => {
    expect(buildMermaidConfig(true).securityLevel).toBe("strict");
    expect(buildMermaidConfig(false).securityLevel).toBe("strict");
    const sanitized = sanitizeMermaidCode(
      [
        '%%{init: {"securityLevel": "loose"}}%%',
        "flowchart TD",
        '  A["Safe"] --> B["Node"]; click A "javascript:alert(1)"',
        "  click A call alert()",
        '  click B "javascript:alert(1)"',
      ].join("\n"),
    );
    expect(sanitized).not.toContain("securityLevel");
    expect(sanitized).not.toContain("call alert");
    expect(sanitized).not.toContain("javascript:");
    expect(sanitized).toContain('A["Safe"] --> B["Node"]');
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

  test("INV-PORTAL-READING-ERGONOMICS & INV-ASCIIDOC-ZERO-WARNING-HYGIENE: every eidos/**/*.adoc body renders without error, warning, or raw-source fallback", async () => {
    const failures: string[] = [];
    const prevLogger = LoggerManager.getLogger();
    const memoryLogger = MemoryLogger.create();
    LoggerManager.setLogger(memoryLogger);
    try {
      for (const file of files) {
        const rel = path.relative(REPO_ROOT, file);
        memoryLogger.clear();
        try {
          const body = bodyOf(readFileSync(file, "utf8"));
          const { html, outline } = await renderAsciiDoc(body);
          if (!html.trim()) failures.push(`${rel}: empty output`);
          for (const msg of memoryLogger.getMessages()) {
            const sev = msg.getSeverity();
            if (sev === "WARN" || sev === "ERROR" || sev === "FATAL") {
              failures.push(`${rel}: Asciidoctor ${sev}: ${msg.getText()}`);
            }
          }
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
    } finally {
      LoggerManager.setLogger(prevLogger);
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
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
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

describe("INV-PORTAL-DOCUMENT-MERMAID-RENDERING: AST Mermaid extraction, serialized queue & portal mounting", () => {
  let mountedContainer: HTMLDivElement | null = null;
  let mountedRoot: ReturnType<typeof createRoot> | null = null;

  afterEach(async () => {
    vi.restoreAllMocks();
    if (mountedRoot) {
      await React.act(async () => {
        mountedRoot!.unmount();
      });
      mountedRoot = null;
    }
    if (mountedContainer) {
      mountedContainer.remove();
      mountedContainer = null;
    }
    document.documentElement.classList.remove("dark");
  });

  test("extracts [mermaid] and [source,mermaid] across fences, case variants, captions, nesting, tables, and verbatim chars while ignoring comments/examples", async () => {
    const body = [
      "= Embedded Diagram Showcase",
      "",
      ".Flow *with* `code` & Entities",
      "[mermaid]",
      "----",
      'flowchart LR',
      '  A["Line 1<br/>Line 2"] --> B["<<interface>>"]',
      "----",
      "",
      "[mermaid]",
      "....",
      "graph TD",
      "  LitA --> LitB",
      "....",
      "",
      ".Sequence *Spec*",
      "[source,mermaid]",
      "----",
      "sequenceDiagram",
      "  Alice->>Bob: Hi & Bye <1>",
      "----",
      "",
      "[source,mermaid]",
      "....",
      "graph LR",
      "  SrcLit1 --> SrcLit2",
      "....",
      "",
      "[MERMAID]",
      "----",
      "graph TD",
      "  Upper1 --> Upper2",
      "----",
      "",
      "[SOURCE,MERMAID]",
      "----",
      "graph TD",
      "  UpperSrc1 --> UpperSrc2",
      "----",
      "",
      "Term::",
      "+",
      "[mermaid]",
      "----",
      "graph TD",
      "  Dlist1 --> Dlist2",
      "----",
      "",
      "* List item",
      "+",
      "[mermaid]",
      "----",
      "graph TD",
      "  List1 --> List2",
      "----",
      "",
      "[NOTE]",
      "====",
      "[mermaid]",
      "----",
      "graph TD",
      "  Note1 --> Note2",
      "----",
      "====",
      "",
      "====",
      "[mermaid]",
      "----",
      "graph TD",
      "  Example1 --> Example2",
      "----",
      "====",
      "",
      '[cols="1a", options="header,footer"]',
      "|===",
      "| Header Column",
      "",
      "|",
      "[mermaid]",
      "----",
      "graph TD",
      "  TableBody1 --> TableBody2",
      "----",
      "",
      "[cols=\"1a\"]",
      "!===",
      "!",
      "[mermaid]",
      "----",
      "graph TD",
      "  NestedTable1 --> NestedTable2",
      "----",
      "!===",
      "",
      "|",
      "[mermaid]",
      "----",
      "graph TD",
      "  TableFoot1 --> TableFoot2",
      "----",
      "|===",
      "",
      "////",
      "[mermaid]",
      "----",
      "graph TD",
      "  CommentedOut --> Ignored",
      "----",
      "////",
      "",
      "[source,asciidoc]",
      "----",
      "[mermaid]",
      "====",
      "graph TD",
      "  ExampleDoc --> Ignored",
      "====",
      "----",
      "",
      "[source,python]",
      "----",
      "print('regular code block')",
      "----",
      "",
    ].join("\n");

    const first = await renderAsciiDoc(body);
    const second = await renderAsciiDoc(body);

    // Deterministic output HTML across parses despite per-parse CSPRNG nonces
    expect(first.html).toBe(second.html);
    expect(first.mermaidBlocks).toHaveLength(13);

    expect(first.mermaidBlocks[0]).toEqual({
      index: 0,
      title: "Flow with code & Entities",
      code: 'flowchart LR\n  A["Line 1<br/>Line 2"] --> B["<<interface>>"]',
    });
    expect(first.mermaidBlocks[1].code).toContain("LitA --> LitB");
    expect(first.mermaidBlocks[2]).toEqual({
      index: 2,
      title: "Sequence Spec",
      code: "sequenceDiagram\n  Alice->>Bob: Hi & Bye <1>",
    });
    expect(first.mermaidBlocks[3].code).toContain("SrcLit1 --> SrcLit2");
    expect(first.mermaidBlocks[4].code).toContain("Upper1 --> Upper2");
    expect(first.mermaidBlocks[5].code).toContain("UpperSrc1 --> UpperSrc2");
    expect(first.mermaidBlocks[6].code).toContain("Dlist1 --> Dlist2");
    expect(first.mermaidBlocks[7].code).toContain("List1 --> List2");
    expect(first.mermaidBlocks[8].code).toContain("Note1 --> Note2");
    expect(first.mermaidBlocks[9].code).toContain("Example1 --> Example2");
    expect(first.mermaidBlocks[10].code).toContain("TableBody1 --> TableBody2");
    expect(first.mermaidBlocks[11].code).toContain("NestedTable1 --> NestedTable2");
    expect(first.mermaidBlocks[12].code).toContain("TableFoot1 --> TableFoot2");

    // Comments and [source,asciidoc] examples must never be extracted
    const joinedCodes = first.mermaidBlocks.map((b) => b.code).join("\n");
    expect(joinedCodes).not.toContain("CommentedOut");
    expect(joinedCodes).not.toContain("ExampleDoc");

    // Slots are placed cleanly and non-Mermaid source blocks retain copy bars
    const tpl = document.createElement("template");
    tpl.innerHTML = first.html;
    const slots = [...tpl.content.querySelectorAll(".adoc-mermaid-slot[data-mermaid-index]")];
    expect(slots.map((el) => el.getAttribute("data-mermaid-index"))).toEqual(
      Array.from({ length: 13 }, (_, i) => String(i))
    );
    expect(tpl.content.querySelectorAll(".adoc-copy-btn")).toHaveLength(2);
    expect(first.html).not.toMatch(/adoc-mermaid-m[a-f0-9]+-/);
  });

  test("unconditionally strips raw-HTML passthrough slot spoofing with and without real Mermaid blocks", async () => {
    const spoofWithoutMermaid = [
      "= Spoof Only",
      "",
      "++++",
      '<div class="adoc-mermaid-slot adoc-mermaid-mdeadbeef-0" data-mermaid-index="0">spoofed</div>',
      '<div class="listingblock adoc-mermaid-m0000-0" data-mermaid-index="0"><pre>fake</pre></div>',
      '<svg><script>alert(1)</script></svg><style>body{display:none}</style>',
      "++++",
      "",
    ].join("\n");

    const resNoMermaid = await renderAsciiDoc(spoofWithoutMermaid);
    expect(resNoMermaid.mermaidBlocks).toEqual([]);
    expect(resNoMermaid.html).not.toContain("adoc-mermaid-slot");
    expect(resNoMermaid.html).not.toContain("data-mermaid-index");
    expect(resNoMermaid.html).not.toContain("adoc-mermaid-m");
    expect(resNoMermaid.html).not.toMatch(/<svg|<style|<script/i);

    const spoofWithRealMermaid = [
      "= Spoof Plus Real",
      "",
      "++++",
      '<div class="adoc-mermaid-slot adoc-mermaid-mfake-0" data-mermaid-index="0">spoofed</div>',
      "++++",
      "",
      "[mermaid]",
      "----",
      "flowchart LR",
      "  RealA --> RealB",
      "----",
      "",
    ].join("\n");

    const resWithReal = await renderAsciiDoc(spoofWithRealMermaid);
    expect(resWithReal.mermaidBlocks).toHaveLength(1);
    const tpl = document.createElement("template");
    tpl.innerHTML = resWithReal.html;
    const slots = tpl.content.querySelectorAll(".adoc-mermaid-slot");
    expect(slots).toHaveLength(1);
    expect(slots[0].getAttribute("data-mermaid-index")).toBe("0");
    expect(slots[0].textContent).toBe("");
    expect(resWithReal.html).not.toContain("adoc-mermaid-mfake");
  });

  test("extracts embedded Mermaid blocks from real living specs with zero Asciidoctor warnings and valid mermaid.parse()", async () => {
    const specPaths = [
      "eidos/architecture/system-architecture.adoc",
      "eidos/design/flows/cli-serve-and-browse.adoc",
      "eidos/design/clients/web-portal/flows/spec-exploration.adoc",
    ];
    const mermaid = await loadMermaid();
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });

    const prevLogger = LoggerManager.getLogger();
    const memoryLogger = MemoryLogger.create();
    LoggerManager.setLogger(memoryLogger);

    try {
      for (const rel of specPaths) {
        memoryLogger.clear();
        const body = bodyOf(readFileSync(path.join(REPO_ROOT, rel), "utf8"));
        const rendered = await renderAsciiDoc(body);
        expect(rendered.mermaidBlocks.length, `${rel} should expose embedded Mermaid blocks`).toBeGreaterThanOrEqual(1);
        expect(rendered.html).toContain('class="adoc-mermaid-slot"');

        for (const msg of memoryLogger.getMessages()) {
          expect(["WARN", "ERROR", "FATAL"]).not.toContain(msg.getSeverity());
        }

        for (const block of rendered.mermaidBlocks) {
          const parsed = await mermaid.parse(sanitizeMermaidCode(block.code));
          expect(parsed, `${rel} block #${block.index} must pass mermaid.parse()`).toBeTruthy();
        }
      }
    } finally {
      LoggerManager.setLogger(prevLogger);
    }
  });

  test("renderMermaidSvg serializes concurrent renders, tracks theme switches, rejects empty input, and removes orphan #d<id> error nodes", async () => {
    await expect(renderMermaidSvg("empty", "   ", true)).rejects.toThrow(/Empty Mermaid/);
    await expect(
      renderMermaidSvg("directive-only", '%%{init: {"securityLevel": "loose"}}%%', false)
    ).rejects.toThrow(/Empty Mermaid/);

    const mermaid = await loadMermaid();
    const initSpy = vi.spyOn(mermaid, "initialize").mockImplementation(() => undefined);
    let inFlight = 0;
    let maxConcurrent = 0;
    const observedIds: string[] = [];

    vi.spyOn(mermaid, "render").mockImplementation(async (uid: string, src: string) => {
      inFlight += 1;
      maxConcurrent = Math.max(maxConcurrent, inFlight);
      observedIds.push(uid);
      await new Promise((r) => setTimeout(r, 10));
      if (src.includes("FAIL_NODE")) {
        const orphan = document.createElement("div");
        orphan.id = `d${uid}`;
        document.body.appendChild(orphan);
        inFlight -= 1;
        throw new Error("boom");
      }
      inFlight -= 1;
      return { svg: `<svg id="${uid}" viewBox="0 0 300 150"></svg>`, diagramType: "flowchart" };
    });

    // Synchronize theme baseline via getConfiguredMermaid(false)
    await getConfiguredMermaid(false);
    initSpy.mockClear();

    const [r1, r2, r3] = await Promise.allSettled([
      renderMermaidSvg("doc 0", "flowchart LR\n  A --> B", false),
      renderMermaidSvg("doc 1", "flowchart LR\n  FAIL_NODE", false),
      renderMermaidSvg("doc 2", "flowchart LR\n  C --> D", true),
    ]);

    expect(maxConcurrent).toBe(1);
    expect(r1.status).toBe("fulfilled");
    expect(r2.status).toBe("rejected");
    expect(r3.status).toBe("fulfilled");
    // Only re-initialized once when theme flipped from false -> true
    expect(initSpy).toHaveBeenCalledTimes(1);
    expect(initSpy).toHaveBeenCalledWith(expect.objectContaining({ securityLevel: "strict", theme: "dark" }));
    // Unique collision-safe monotonic IDs and zero leaked #d<id> nodes
    expect(new Set(observedIds).size).toBe(3);
    for (const uid of observedIds) {
      expect(uid).toMatch(/^eidos-mermaid-doc-\d+-\d+$/);
      expect(document.getElementById(`d${uid}`)).toBeNull();
    }
  });

  test("AsciiDocRenderer mounts sibling DocumentMermaidBlock portals with modifier-wheel zoom, View Source/Copy, dark-mode reactivity, spec switching, and error-to-valid recovery", async () => {
    const mermaid = await loadMermaid();
    const renderedThemes: string[] = [];
    vi.spyOn(mermaid, "initialize").mockImplementation((cfg) => {
      renderedThemes.push(String(cfg?.theme || ""));
    });
    vi.spyOn(mermaid, "render").mockImplementation(async (uid: string, src: string) => {
      if (src.includes("MALFORMED")) {
        throw new Error("Syntax error: unexpected token CMP-LEAK-999");
      }
      return {
        svg: `<svg id="${uid}" viewBox="0, 0, 400, 200"><text>${src.includes("SecondSpec") ? "SecondSpec" : "FirstSpec"}</text></svg>`,
        diagramType: "flowchart",
      };
    });

    const clipboardWrite = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: clipboardWrite },
      configurable: true,
    });

    const onNavigateSpec = vi.fn();
    mountedContainer = document.createElement("div");
    document.body.appendChild(mountedContainer);
    mountedRoot = createRoot(mountedContainer);

    const validDoc = [
      "= Spec One",
      "",
      "See xref:CNT-EIDOS-PORTAL[Portal].",
      "",
      ".First *Diagram*",
      "[mermaid]",
      "----",
      "flowchart LR",
      "  FirstSpec --> End",
      "----",
      "",
      "[source,python]",
      "----",
      "x = 42",
      "----",
      "",
    ].join("\n");

    const flush = async (ms = 40) => {
      await React.act(async () => {
        await new Promise((r) => setTimeout(r, ms));
      });
    };

    await React.act(async () => {
      mountedRoot!.render(
        React.createElement(AsciiDocRenderer, {
          content: validDoc,
          specId: "SPEC-A",
          onNavigateSpec,
        })
      );
    });
    await flush();

    const slot = mountedContainer.querySelector(".adoc-mermaid-slot[data-mermaid-index='0']");
    expect(slot).not.toBeNull();
    const figure = slot!.querySelector<HTMLElement>('[data-document-mermaid="true"]');
    expect(figure).not.toBeNull();
    expect(figure!.getAttribute("role")).toBe("figure");
    expect(figure!.getAttribute("aria-label")).toBe("Diagram: First Diagram");
    expect(figure!.textContent).toContain("First Diagram");
    const renderedSvg = figure!.querySelector<SVGSVGElement>("svg[id^='eidos-mermaid-']");
    expect(renderedSvg).not.toBeNull();
    // jsdom clientWidth === 0 must fall back to positive 100% zoom, never negative scale
    expect(figure!.textContent).toContain("100%");
    expect(figure!.innerHTML).not.toContain("scale(-");

    // Modifier-wheel zoom: plain wheel does not preventDefault; Ctrl+wheel and Meta+wheel zoom and prevent default
    const canvas = renderedSvg!.closest<HTMLElement>("[style*='min-height']")!;
    expect(canvas).not.toBeNull();
    const plainWheel = new WheelEvent("wheel", { deltaY: -100, bubbles: true, cancelable: true });
    await React.act(async () => {
      canvas.dispatchEvent(plainWheel);
    });
    expect(plainWheel.defaultPrevented).toBe(false);
    expect(figure!.textContent).toContain("100%");

    const ctrlWheel = new WheelEvent("wheel", {
      deltaY: -100,
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    await React.act(async () => {
      canvas.dispatchEvent(ctrlWheel);
    });
    expect(ctrlWheel.defaultPrevented).toBe(true);
    expect(figure!.textContent).toContain("112%");

    const metaWheel = new WheelEvent("wheel", {
      deltaY: 100,
      metaKey: true,
      bubbles: true,
      cancelable: true,
    });
    await React.act(async () => {
      canvas.dispatchEvent(metaWheel);
    });
    expect(metaWheel.defaultPrevented).toBe(true);
    expect(figure!.textContent).toContain("100%");

    // Copy button writes verbatim code, updates label, has no .adoc-copy-btn collision, and sibling portal clicks do not bubble into AsciiDocRenderer.handleClick
    const copyBtn = Array.from(figure!.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Copy")
    )!;
    expect(copyBtn.classList.contains("adoc-copy-btn")).toBe(false);
    await React.act(async () => {
      copyBtn.click();
    });
    expect(clipboardWrite).toHaveBeenCalledWith("flowchart LR\n  FirstSpec --> End");
    expect(copyBtn.textContent).toContain("Copied");
    expect(onNavigateSpec).not.toHaveBeenCalled();

    // Behavioral sibling-portal isolation proof: even though `figure` is a DOM descendant of `.adoc-body`,
    // clicking an anchor inside the sibling React portal must NOT trigger `.adoc-body`'s React `handleClick`,
    // whereas clicking a real `data-spec-ref` link inside `.adoc-body` DOES invoke `onNavigateSpec`.
    const portalProbeLink = document.createElement("a");
    portalProbeLink.setAttribute("data-spec-ref", "CNT-SHOULD-NOT-FIRE");
    portalProbeLink.setAttribute("href", "#spec/CNT-SHOULD-NOT-FIRE");
    portalProbeLink.textContent = "Probe";
    figure!.appendChild(portalProbeLink);
    await React.act(async () => {
      portalProbeLink.click();
    });
    portalProbeLink.remove();
    expect(onNavigateSpec).not.toHaveBeenCalled();

    const realSpecLink = mountedContainer.querySelector<HTMLAnchorElement>('a[data-spec-ref="CNT-EIDOS-PORTAL"]')!;
    expect(realSpecLink).not.toBeNull();
    await React.act(async () => {
      realSpecLink.click();
    });
    expect(onNavigateSpec).toHaveBeenCalledWith("CNT-EIDOS-PORTAL");

    // Neighboring non-Mermaid code block copy bar remains intact and untouched
    const codeCopyBtn = mountedContainer.querySelector(".adoc-copy-btn");
    expect(codeCopyBtn?.textContent).toBe("Copy");

    // View Source / View Diagram toggle (no contradictory aria-pressed on action-swapping button)
    const toggleBtn = Array.from(figure!.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("View Source")
    )!;
    expect(toggleBtn.hasAttribute("aria-pressed")).toBe(false);
    await React.act(async () => {
      toggleBtn.click();
    });
    expect(toggleBtn.textContent).toContain("View Diagram");
    expect(toggleBtn.hasAttribute("aria-pressed")).toBe(false);
    expect(figure!.querySelector("pre code")?.textContent).toContain("FirstSpec --> End");
    await React.act(async () => {
      toggleBtn.click();
    });
    expect(toggleBtn.textContent).toContain("View Source");

    // Reactive dark mode toggle via document.documentElement class mutation
    const themesBefore = renderedThemes.length;
    await React.act(async () => {
      document.documentElement.classList.add("dark");
    });
    await flush();
    expect(renderedThemes.length).toBeGreaterThan(themesBefore);
    expect(renderedThemes[renderedThemes.length - 1]).toBe("dark");

    // Switch to another spec with IDENTICAL placeholder HTML (<div class="adoc-mermaid-slot" data-mermaid-index="0"></div>)
    const secondDoc = [
      "[mermaid]",
      "----",
      "flowchart LR",
      "  SecondSpec --> Done",
      "----",
      "",
    ].join("\n");
    const firstSingleDoc = [
      "[mermaid]",
      "----",
      "flowchart LR",
      "  FirstSpec --> Done",
      "----",
      "",
    ].join("\n");

    await React.act(async () => {
      mountedRoot!.render(React.createElement(AsciiDocRenderer, { content: firstSingleDoc }));
    });
    await flush();
    expect(mountedContainer.querySelector(".adoc-mermaid-slot svg[id^='eidos-mermaid-']")?.textContent).toContain("FirstSpec");

    await React.act(async () => {
      mountedRoot!.render(React.createElement(AsciiDocRenderer, { content: secondDoc }));
    });
    await flush();
    expect(mountedContainer.querySelector(".adoc-mermaid-slot svg[id^='eidos-mermaid-']")?.textContent).toContain("SecondSpec");

    // Error-to-valid recovery without losing diagramRef + single human-readable role="alert"
    const brokenDoc = [
      "[mermaid]",
      "----",
      "MALFORMED DIAGRAM",
      "----",
      "",
    ].join("\n");
    await React.act(async () => {
      mountedRoot!.render(React.createElement(AsciiDocRenderer, { content: brokenDoc }));
    });
    await flush();
    const alerts = mountedContainer.querySelectorAll('[role="alert"]');
    expect(alerts).toHaveLength(1);
    expect(alerts[0].textContent).toContain("Unable to render diagram.");
    expect(alerts[0].textContent).not.toContain("CMP-LEAK-999");
    expect(mountedContainer.querySelector(".adoc-mermaid-slot pre code")?.textContent).toContain(
      "MALFORMED DIAGRAM"
    );

    // Recover from error back to valid diagram
    await React.act(async () => {
      mountedRoot!.render(React.createElement(AsciiDocRenderer, { content: secondDoc }));
    });
    await flush();
    expect(mountedContainer.querySelectorAll('[role="alert"]')).toHaveLength(0);
    expect(mountedContainer.querySelector(".adoc-mermaid-slot svg[id^='eidos-mermaid-']")?.textContent).toContain("SecondSpec");

    // Empty Mermaid block renders single role="alert" fallback and consistent empty View Source copy
    const emptyMermaidDoc = ["[mermaid]", "----", "   ", "----", ""].join("\n");
    await React.act(async () => {
      mountedRoot!.render(React.createElement(AsciiDocRenderer, { content: emptyMermaidDoc }));
    });
    await flush();
    expect(mountedContainer.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(mountedContainer.querySelector('[role="alert"]')?.textContent).toContain(
      "Unable to render diagram."
    );
    const emptyToggleBtn = Array.from(mountedContainer.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("View Source")
    )!;
    await React.act(async () => {
      emptyToggleBtn.click();
    });
    expect(mountedContainer.textContent).toContain("No diagram source provided.");

    // Empty content -> valid content transition (mimics chunkLoading unmount/remount) cleanly restores portal
    await React.act(async () => {
      mountedRoot!.render(React.createElement(AsciiDocRenderer, { content: "" }));
    });
    await flush();
    expect(mountedContainer.querySelectorAll(".adoc-mermaid-slot")).toHaveLength(0);
    await React.act(async () => {
      mountedRoot!.render(React.createElement(AsciiDocRenderer, { content: secondDoc }));
    });
    await flush();
    expect(mountedContainer.querySelector(".adoc-mermaid-slot svg[id^='eidos-mermaid-']")?.textContent).toContain("SecondSpec");
  });

  test("MermaidDiagram bounds height, clamps non-zero autoFit, honors ResizeObserver/resetTo100/Expand, and preserves wheelZoom='always' for DesignView", async () => {
    const mermaid = await loadMermaid();
    vi.spyOn(mermaid, "initialize").mockImplementation(() => undefined);
    vi.spyOn(mermaid, "render").mockImplementation(async (uid: string, src: string) => {
      if (src.includes("BROKEN_STANDALONE")) {
        throw new Error("Standalone parse failure");
      }
      // Tall diagram matching real ARCH-EIDOS-001 C4Context dimensions (516 x 863)
      return {
        svg: `<svg id="${uid}" viewBox="0 -70 516 863"><rect width="516" height="863" /></svg>`,
        diagramType: "c4",
      };
    });

    const resizeCallbacks: Array<() => void> = [];
    const OriginalResizeObserver = globalThis.ResizeObserver;
    class MockResizeObserver {
      private cb: () => void;
      constructor(cb: ResizeObserverCallback) {
        this.cb = () => cb([], this as unknown as ResizeObserver);
      }
      observe() {
        resizeCallbacks.push(this.cb);
      }
      unobserve() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;

    const flush = async (ms = 40) => {
      await React.act(async () => {
        await new Promise((r) => setTimeout(r, ms));
      });
    };

    try {
      mountedContainer = document.createElement("div");
      document.body.appendChild(mountedContainer);
      mountedRoot = createRoot(mountedContainer);

      await React.act(async () => {
        mountedRoot!.render(
          React.createElement(MermaidDiagram, {
            id: "tall-standalone",
            code: "C4Context\n  System(a, 'A')",
            minHeight: "320px",
            expandedHeight: "560px",
          })
        );
      });
      await flush();

      const canvas = mountedContainer.firstElementChild as HTMLDivElement;
      expect(canvas.style.height).toBe("320px");
      expect(canvas.style.minHeight).toBe("320px");

      let mockWidth = 700;
      let mockHeight = 320;
      Object.defineProperty(canvas, "clientWidth", { get: () => mockWidth, configurable: true });
      Object.defineProperty(canvas, "clientHeight", { get: () => mockHeight, configurable: true });

      // Trigger ResizeObserver before manual interaction -> clamps (320-48)/863 = 0.315 up to 0.35 (35%)
      await React.act(async () => {
        resizeCallbacks.forEach((cb) => cb());
      });
      await flush();
      expect(canvas.textContent).toContain("35%");

      // Expand viewport height -> binds height: 560px and re-fits to (560-48)/863 = 0.59 (59%)
      mockHeight = 560;
      const expandBtn = canvas.querySelector<HTMLButtonElement>('button[aria-label="Expand viewport height"]')!;
      expect(expandBtn).not.toBeNull();
      await React.act(async () => {
        expandBtn.click();
      });
      await React.act(async () => {
        resizeCallbacks.forEach((cb) => cb());
      });
      await flush(250);
      expect(canvas.style.height).toBe("560px");
      expect(canvas.textContent).toContain("59%");

      // Reset to 100% marks manual user intent -> ResizeObserver must NOT snap zoom back down
      const resetBtn = canvas.querySelector<HTMLButtonElement>('button[aria-label="Reset zoom to 100%"]')!;
      await React.act(async () => {
        resetBtn.click();
      });
      expect(canvas.textContent).toContain("100%");
      await React.act(async () => {
        resizeCallbacks.forEach((cb) => cb());
      });
      await flush();
      expect(canvas.textContent).toContain("100%");

      // Fit to screen re-enables ResizeObserver auto-fit
      const fitBtn = canvas.querySelector<HTMLButtonElement>('button[aria-label="Fit entire diagram to screen"]')!;
      await React.act(async () => {
        fitBtn.click();
      });
      expect(canvas.textContent).toContain("59%");

      // Standalone default wheelZoom="always" (DesignView) prevents plain wheel events and zooms
      const plainWheel = new WheelEvent("wheel", { deltaY: -100, bubbles: true, cancelable: true });
      await React.act(async () => {
        canvas.dispatchEvent(plainWheel);
      });
      expect(plainWheel.defaultPrevented).toBe(true);
      expect(canvas.textContent).toContain("71%");

      // Standalone error state hides zoom toolbar & pan hint cleanly
      await React.act(async () => {
        mountedRoot!.render(
          React.createElement(MermaidDiagram, {
            id: "tall-standalone",
            code: "BROKEN_STANDALONE",
          })
        );
      });
      await flush();
      expect(canvas.querySelector('[role="alert"]')?.textContent).toContain("Unable to render diagram.");
      expect(canvas.querySelector('button[aria-label="Zoom in"]')).toBeNull();
      expect(canvas.textContent).not.toContain("Drag to pan");
    } finally {
      globalThis.ResizeObserver = OriginalResizeObserver;
    }
  });
});
