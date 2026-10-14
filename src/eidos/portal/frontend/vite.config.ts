import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import path from "node:path";

/**
 * @asciidoctor/core's browser build needs two patches for the air-gapped single-file bundle:
 *
 * 1. It derives Node-only paths via `new URL('.', import.meta.url)`. Vite treats those as asset references
 *    and (with our huge assetsInlineLimit) inlines the whole 1.3 MB library as a base64 data URL, twice.
 *    Pin `import.meta.url` to a constant so nothing is inlined.
 * 2. It embeds the default stylesheets for *standalone* HTML pages (which reference Google Fonts). The portal
 *    converts with `standalone: false` and styles output itself (theme.css), so the dead strings are blanked.
 *    The patch fails the build if the pinned library layout changes, so a version bump cannot silently regress.
 */
function patchAsciidoctorBrowserBuild(): Plugin {
  return {
    name: "eidos:patch-asciidoctor-browser-build",
    enforce: "pre",
    transform(code, id) {
      if (!/@asciidoctor\/core\/build\/browser\/index\.js$/.test(id.split("?")[0])) return null;
      let patched = code.replaceAll("import.meta.url", JSON.stringify("file:///"));
      const stylesheetDecl = /^const stylesheet(\$1)? = ".*";$/gm;
      const blanked = [...patched.matchAll(stylesheetDecl)].length;
      if (blanked !== 2) {
        this.error(`Expected 2 embedded default stylesheets in @asciidoctor/core, found ${blanked}; update vite.config.ts.`);
      }
      patched = patched.replace(stylesheetDecl, (_m, suffix) => `const stylesheet${suffix ?? ""} = "";`);
      return { code: patched, map: null };
    },
  };
}

/**
 * `builder.py` injects `window.__EIDOS_DATA__` before the first `</head>` of PORTAL_HTML_TEMPLATE. Inline JS string
 * literals (Asciidoctor's HTML templates) contain `</head>` too, which would swallow that injection, so every
 * `</head>` before the real document end is escaped as `<\/head>` (identical inside JS strings/templates).
 */
function escapeInlineHeadClose(): Plugin {
  return {
    name: "eidos:escape-inline-head-close",
    enforce: "post",
    generateBundle(_options, bundle) {
      for (const file of Object.values(bundle)) {
        if (file.type !== "asset" || !file.fileName.endsWith(".html")) continue;
        const html = String(file.source);
        const realEnd = html.lastIndexOf("</head>");
        if (realEnd < 0) continue;
        file.source = html.slice(0, realEnd).replaceAll("</head>", "<\\/head>") + html.slice(realEnd);
      }
    },
  };
}

const UNBUNDLED_STUB = path.resolve(__dirname, "./src/stubs/unbundled-optional-dependency.ts");
const UNBUNDLED_MERMAID_OPTIONALS = [
  /^elkjs\/lib\/elk\.bundled\.js$/, // `layout: elk` engine (~1.4 MB); dagre is the portal default and what generator.py targets
];

export default defineConfig({
  plugins: [
    patchAsciidoctorBrowserBuild(),
    react(),
    tailwindcss(),
    viteSingleFile({
      useRecommendedBuildConfig: true,
      removeOptionalTags: false,
    }),
    escapeInlineHeadClose(),
  ],
  resolve: {
    alias: [
      { find: "@", replacement: path.resolve(__dirname, "./src") },
      // Optional Mermaid dependencies that would blow the 5 MiB single-file budget (INV-PORTAL-AIR-GAPPED-BUNDLE
      // keeps everything inline, so unused heavyweight engines are stubbed rather than shipped).
      ...UNBUNDLED_MERMAID_OPTIONALS.map((find) => ({ find, replacement: UNBUNDLED_STUB })),
    ],
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${process.env.VITE_API_PORT || "8090"}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    target: "esnext",
    assetsInlineLimit: 100000000,
    chunkSizeWarningLimit: 10000,
    cssCodeSplit: false,
    brotliSize: false,
    outDir: "dist",
    emptyOutDir: true,
  },
});
