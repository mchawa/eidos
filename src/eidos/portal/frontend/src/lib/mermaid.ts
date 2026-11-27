import type { MermaidConfig } from "mermaid";

/**
 * Mermaid is bundled from npm (version pinned in package.json / pnpm-lock.yaml) and loaded lazily.
 * The single-file Vite build (`inlineDynamicImports`) folds this dynamic import into the one
 * inline <script>, so no extra chunk or remote request is ever emitted (INV-PORTAL-AIR-GAPPED-BUNDLE).
 */
export type MermaidApi = (typeof import("mermaid"))["default"];

let mermaidPromise: Promise<MermaidApi> | null = null;
let renderQueue: Promise<void> = Promise.resolve();
let lastInitializedDark: boolean | null = null;
let svgIdSequence = 0;

export function loadMermaid(): Promise<MermaidApi> {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((mod) => mod.default);
  }
  return mermaidPromise;
}

function enqueueMermaidRender<T>(task: () => Promise<T>): Promise<T> {
  const run = renderQueue.then(task, task);
  renderQueue = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

function sanitizeSvgIdPrefix(id: string): string {
  const cleaned = (id || "diagram")
    .replace(/[^a-zA-Z0-9_-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return cleaned || "diagram";
}

function cleanupOrphanMermaidErrorNodes(uniqueId: string): void {
  if (typeof document === "undefined") return;
  document.getElementById(`d${uniqueId}`)?.remove();
  const straySvg = document.getElementById(uniqueId);
  if (straySvg && straySvg.parentElement === document.body) {
    straySvg.remove();
  }
}

export function sanitizeMermaidCode(rawCode: string): string {
  const withoutDirectives = (rawCode || "").replace(/%%\{[\s\S]*?\}%%/g, "");
  const unsafeClickRe =
    /(^|;)\s*click\s+[^;\n]*(?:\bcall\b|(?:javascript|data|vbscript)\s*:)[^;\n]*/gi;
  const cleanedLines: string[] = [];
  for (const line of withoutDirectives.split("\n")) {
    const hadUnsafeClick = unsafeClickRe.test(line);
    unsafeClickRe.lastIndex = 0;
    if (!hadUnsafeClick) {
      cleanedLines.push(line);
      continue;
    }
    const stripped = line.replace(unsafeClickRe, "").trimEnd();
    if (stripped.trim()) {
      cleanedLines.push(stripped);
    }
  }
  return cleanedLines.join("\n").trim();
}

export function buildMermaidConfig(dark: boolean): MermaidConfig {
  return {
    startOnLoad: false,
    // Mermaid 12 defaults to the ELK layout engine, which the air-gapped bundle does not ship (size budget,
    // see vite.config.ts). dagre is what the C4 click handlers and the portal's diagrams are designed around.
    layout: "dagre",
    theme: dark ? "dark" : "default",
    securityLevel: "strict",
    fontFamily: "Geist, Inter, system-ui, sans-serif",
    themeVariables: dark
      ? {
          darkMode: true,
          background: "transparent",
          mainBkg: "#22272f",
          nodeBorder: "#4b89f0",
          lineColor: "#7cb3ff",
          textColor: "#e6edf3",
        }
      : {
          darkMode: false,
          background: "transparent",
          mainBkg: "#ffffff",
          nodeBorder: "#1d5fd1",
          lineColor: "#1d5fd1",
          textColor: "#1f2328",
        },
  };
}

/** Load Mermaid and (re)initialize it for the requested color scheme. */
export function getConfiguredMermaid(dark: boolean): Promise<MermaidApi> {
  return enqueueMermaidRender(async () => {
    const mermaid = await loadMermaid();
    mermaid.initialize(buildMermaidConfig(dark));
    lastInitializedDark = dark;
    return mermaid;
  });
}

/**
 * Serialize Mermaid v12 SVG rendering through a single module-level queue shared by
 * ArchitectureView C4 diagrams, standalone MermaidDiagram views, and embedded AsciiDoc diagrams.
 *
 * Enforces `sanitizeMermaidCode()`, `securityLevel: "strict"`, empty-input rejection,
 * theme-change-aware initialization, collision-safe monotonic SVG IDs, and orphan `#d<id>`
 * temporary DOM node cleanup on failure.
 */
export function renderMermaidSvg(
  id: string,
  rawCode: string,
  dark: boolean,
): Promise<{ svg: string }> {
  const safeCode = sanitizeMermaidCode(rawCode);
  if (!safeCode) {
    return Promise.reject(new Error("Empty Mermaid diagram source."));
  }

  return enqueueMermaidRender(async () => {
    const mermaid = await loadMermaid();
    if (lastInitializedDark !== dark) {
      mermaid.initialize(buildMermaidConfig(dark));
      lastInitializedDark = dark;
    }
    svgIdSequence += 1;
    const uniqueId = `eidos-mermaid-${sanitizeSvgIdPrefix(id)}-${svgIdSequence}`;
    try {
      const result = await mermaid.render(uniqueId, safeCode);
      if (typeof document !== "undefined") {
        document.getElementById(`d${uniqueId}`)?.remove();
      }
      return { svg: result.svg };
    } catch (err) {
      cleanupOrphanMermaidErrorNodes(uniqueId);
      throw err;
    }
  });
}
