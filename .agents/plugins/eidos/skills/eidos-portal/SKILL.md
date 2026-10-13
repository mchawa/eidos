---
name: eidos-portal
description: Serve, build, and publish the interactive Eidos Living Web Portal, static documentation sites, C4 architecture diagrams, and W3C tokens studio.
---

# Eidos Living Web Portal & Documentation (`eidos-portal`)

This skill empowers AI agents to preview, build, and publish the **Eidos Living Web Portal**—an interactive, self-contained documentation site synthesizing C4 architecture topologies, domain catalogs, SpecDelta Kanban boards, W3C design tokens, and invariant traceability matrices.

---

## 1. What the Living Portal Provides

The portal is a zero-dependency, single-page application (SPA) generated directly from the repository's `.adoc` and schema files, organizing the living specification into key views:

1. **Architecture Topologies (C4 L1-2):** Rendered dynamically using Mermaid graphs directly from component manifests and persona declarations.
2. **Product Domains & Capabilities:** Interactive catalog and detail views into bounded contexts and business capabilities.
3. **SpecDelta Kanban Board:** Visual column cards for `proposed`, `in_review`, `approved`, and `applied` mutations.
4. **Invariant Traceability Matrix:** Bidirectional table mapping policies (`POL-*`) and capability invariants (`INV-*`) to automated test files.
5. **Design Systems & Token Studio:** Visual swatches rendering colors, typography, and spacing tokens from `eidos/design/systems/` (W3C DTCG).
6. **Living Specs Catalog & Live Search:** Interactive filtering, full-text search, schema badges, and dark/light theme toggle.
7. **Zero-Dependency Architecture:** Zero Node.js or npm dependencies; pure Python standard library HTTP server and static HTML/CSS/JS export.

---

## 2. Local Live Preview (`eidos portal serve` / `eidos serve`)

Run the local live preview server during development using either the namespaced command or its top-level shortcut:

```bash
# Serve on default port (eidos portal serve or shortcut eidos serve)
eidos portal serve
# Or using shortcut:
eidos serve

# Serve with custom port and host
eidos portal serve --port 3000 --host 0.0.0.0

# Serve a custom specifications directory
eidos portal serve --path packages/service-a/eidos
```

### Endpoints
* `http://localhost:3000/`: Interactive Web Portal.
* `http://localhost:3000/api/data`: Full JSON payload dynamically synthesized from living specifications.
* `http://localhost:3000/health`: Health status and document count.

---

## 3. Static Site Export (`eidos portal build` / `eidos build-site`)

Export the portal as a self-contained, offline-capable static site suitable for **GitHub Pages**, **GitLab Pages**, or static web servers:

```bash
# Build into default 'site/' folder (eidos portal build or shortcut eidos build-site)
eidos portal build
# Or using shortcut:
eidos build-site

# Build into custom destination (e.g. documentation subfolder)
eidos portal build --path eidos --out site/portal
```

### What It Generates
* `site/portal/index.html`: Fully self-contained SPA with inlined JSON data (`window.__EIDOS_DATA__`) enabling zero-fetch offline loading.
* `site/portal/data.json`: Machine-readable payload for external documentation tools or dashboards.

---

## 4. Integration with Documentation Generators

In hybrid documentation architectures (e.g., Zensical, MkDocs, or Antora):

```bash
# 1. Build main prose documentation (Zensical / MkDocs)
zensical build --strict

# 2. Build Living Architecture Portal into documentation subfolder
eidos build-site --path eidos --out site/portal

# 3. Verify output files exist
test -f site/portal/index.html
test -f site/portal/data.json
```

The living architecture portal seamlessly embeds into the documentation site navigation under `/portal/`.

---

## 5. CI/CD GitHub Actions Deployment

To publish the Living Portal automatically on every push to `main` or tag release:

```yaml
# .github/workflows/deploy-portal.yml
name: Deploy Living Portal

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v5
      - run: uvx eidos-ai build-site --out site
      - uses: actions/upload-pages-artifact@v3
        with:
          path: site
      - uses: actions/deploy-pages@v4
```

---

## 6. Verification

Before exporting or publishing:
```bash
# 1. Verify specification syntax
eidos validate eidos

# 2. Test site build
eidos portal build --out /tmp/portal-test
test -f /tmp/portal-test/index.html
```
Ensures zero broken references or rendering errors before public deployment.

