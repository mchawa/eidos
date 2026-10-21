# Eidos Portal

The **Eidos Portal** is an interactive, visual web application generated directly from your living specifications.

Structured around a responsive left sidebar and URL hash deep-linking (`#/<view>/<entity-id>`), the portal synthesizes all 16 specification schemas into seven interactive views:

1. **Executive Dashboard (Home)**: System archetype (`SYS-*`), high-level KPI metric cards, interactive **Validation & Governance Diagnostics Drawer**, progressive live-reload status indicator, architectural pillars, and recent SpecDeltas stream.
2. **Business Architecture**: Bounded Domains (`DOM-*`), Capabilities (`CAP-*`) with JTBD statements and clickable realizing `CNT-*`/`CMP-*`/`CTR-*` badges, Actor Personas (`PER-*`), System Policies (`POL-*`), and test-bound Governance Invariants (`INV-*`).
3. **Design & Experience**: Multi-Design System Switcher (`DS-*` with `extends` lineage), Client Switcher (`CLT-*`), interactive **Recursive W3C DTCG Token Studio** (`tokens.json`), UI Component Manifests (`UIC-*`), Screen State Machines (`SCR-*`), and Omnichannel User Flows (`FLOW-*`).
4. **Technical Architecture (Hierarchical C4 & Contract Explorer)**:
   * **C4 Level 1 (System Context)**: Actors, system boundary, and external platform integrations.
   * **C4 Level 2 (Container Topology)**: Containers (`CNT-*`) organized by semantic architectural tiers with rendered inter-container dependency edges and clickable boundary protocol contracts (`CTR-*`).
   * **C4 Level 3 (Component Whitebox)**: Click any container to drill down into its inline and promoted components (`CMP-*`), with breadcrumbs, direct component card focus, and a slide-over inspector showing source paths, AST-derived component dependencies, component invariants (`INV-*`), and embedded architectural decisions (`DEC-*` with rejected alternatives).
   * **Interactive Blast Radius Simulator & Boundary Contracts (`CTR-*`) Explorer**: Simulate 1–2 hop upstream/downstream impact directly on the C4 canvas using exported knowledge graph edges (`INV-PORTAL-STATIC-SCALABILITY`) and inspect protocol contracts in a dedicated explorer.
5. **SpecDeltas Pipeline (Kanban & Chronological Timeline)**: Interactive Kanban board and Chronological Timeline tracking semantic mutations across `proposed`, `in_review`, `approved`, `applied` (paginated with archived column support), and `rejected` states, with clickable target and blast-radius entity badges.
6. **Technical Explorations**: Discovery spikes (`EXP-*`) tracking hypotheses, exit criteria, empirical findings, archived status filtering, and resulting SpecDeltas.
7. **Specs Master Catalog**: Filterable repository inventory with pre-indexed instant full-text Spotlight search (`CMD+K` and `/` shortcut across all 16 spec types, `DEC-*` decisions, and section headings), AsciiDoc reader with dynamic Table of Contents (`INV-PORTAL-READING-ERGONOMICS`), and clickable relationship badges.

---

## Local Interactive Development

To inspect your architecture locally with progressive live auto-reload (`GET /api/version` mtime polling and `GET /api/drift` uncommitted drift summaries):

```bash
eidos portal serve --path eidos --port 8080
```

Open `http://localhost:8080` in your web browser. Any changes made to your `.adoc` files in `eidos/` are automatically detected and refreshed in the portal.

---

## Air-Gapped Static Site Export (`INV-PORTAL-AIR-GAPPED-BUNDLE` & `INV-PORTAL-STATIC-SCALABILITY`)

You can export the entire portal as a static site that can be hosted on GitHub Pages, GitLab Pages, AWS S3, or opened in air-gapped environments:

```bash
eidos portal build --path eidos --out public
```

This creates a self-contained bundle with **zero external CDN dependencies**, **zero Node.js runtime requirement (`INV-PORTAL-ZERO-NODE-DEP`)**, and **deduplicated specification payloads (`~40–50%` smaller JSON re-hydrated client-side)**:

```text
public/
├── index.html       # Pre-compiled single-file React 19 / Tailwind v4 / MD3 SPA
│                    # (inline Mermaid.js v11, @asciidoctor/core, Geist woff2 fonts & window.__EIDOS_DATA__)
├── favicon.svg      # Vector portal icon
└── data.json        # Deduplicated AST, C4 topology, directed graph edges & validation diagnostics
```

---

## Automated Deployment with GitHub Actions

You can automatically build and publish the Living Portal on every push to `main`:

```yaml
name: Deploy Living Portal

on:
  push:
    branches: [main]

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.0
      - run: uv sync --all-extras --dev
      - run: uv run eidos portal build --path eidos --out public
      - uses: actions/upload-pages-artifact@v3
        with:
          path: public
      - id: deployment
        uses: actions/deploy-pages@v4
```

