import type { MermaidConfig } from "mermaid";

/**
 * Mermaid is bundled from npm (version pinned in package.json / pnpm-lock.yaml) and loaded lazily.
 * The single-file Vite build (`inlineDynamicImports`) folds this dynamic import into the one
 * inline <script>, so no extra chunk or remote request is ever emitted (INV-PORTAL-AIR-GAPPED-BUNDLE).
 */
export type MermaidApi = (typeof import("mermaid"))["default"];

let mermaidPromise: Promise<MermaidApi> | null = null;

export function loadMermaid(): Promise<MermaidApi> {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((mod) => mod.default);
  }
  return mermaidPromise;
}

export function buildMermaidConfig(dark: boolean): MermaidConfig {
  return {
    startOnLoad: false,
    // Mermaid 12 defaults to the ELK layout engine, which the air-gapped bundle does not ship (size budget,
    // see vite.config.ts). dagre is what the C4 click handlers and the portal's diagrams are designed around.
    layout: "dagre",
    theme: dark ? "dark" : "default",
    securityLevel: "loose",
    fontFamily: "Geist, Inter, system-ui, sans-serif",
    themeVariables: dark
      ? {
          darkMode: true,
          background: "transparent",
          mainBkg: "#111827",
          nodeBorder: "#3b82f6",
          lineColor: "#60a5fa",
          textColor: "#f8fafc",
        }
      : {
          darkMode: false,
          background: "transparent",
          mainBkg: "#ffffff",
          nodeBorder: "#2563eb",
          lineColor: "#2563eb",
          textColor: "#0f172a",
        },
  };
}

/** Load Mermaid and (re)initialize it for the requested color scheme. */
export async function getConfiguredMermaid(dark: boolean): Promise<MermaidApi> {
  const mermaid = await loadMermaid();
  mermaid.initialize(buildMermaidConfig(dark));
  return mermaid;
}
