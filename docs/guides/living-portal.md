# Eidos Portal

The **Eidos Portal** is an interactive, visual web application generated directly from your living specifications.

Structured around a responsive left sidebar, the portal synthesizes all 16 specification schemas into seven interactive views:

1. **Executive Dashboard (Home)**: System archetype (`SYS-*`), high-level KPI metric cards, architectural pillars, and recent SpecDeltas stream.
2. **Business Architecture**: Actor Personas (`PER-*`), Bounded Domains (`DOM-*`), Capabilities (`CAP-*`) with JTBD statements, System Policies (`POL-*`), and test-bound Governance Invariants (`INV-*`).
3. **Design & Experience**: Multi-Design Systems (`DS-*`), Client Switcher (`CLT-*`), interactive W3C DTCG Token Studio (`tokens.json`), UI Component Manifests (`UIC-*`), Screen State Machines (`SCR-*`), and Omnichannel User Flows (`FLOW-*`).
4. **Technical Architecture (Hierarchical C4 Explorer)**:
   * **C4 Level 1 (System Context)**: Actors, system boundary, and external platform integrations.
   * **C4 Level 2 (Container Topology)**: Containers (`CNT-*`) organized by semantic architectural tiers with rendered inter-container dependency edges and boundary protocol contracts (`CTR-*`).
   * **C4 Level 3 (Component Whitebox)**: Click any container to drill down into its inline and promoted components (`CMP-*`), with breadcrumbs and a slide-over inspector showing source paths, in-process APIs (`interfaces.provides`), component invariants (`INV-*`), and embedded architectural decisions (`DEC-*` with rejected alternatives).
5. **SpecDeltas Pipeline**: Interactive Kanban board tracking atomic mutations across `draft`, `proposed`, `planned`, `in_review`, `approved`, `applied`, and `rejected` states, with full target and blast-radius visualization.
6. **Technical Explorations**: Discovery spikes (`EXP-*`) tracking hypotheses, exit criteria, empirical findings, and resulting SpecDeltas.
7. **Specs Master Catalog**: Filterable repository inventory with instant fuzzy search (`CMD+K`), AsciiDoc reader with dynamic Table of Contents (`INV-PORTAL-READING-ERGONOMICS`), and rich frontmatter inspection.

---

## Local Interactive Development

To inspect your architecture locally with live updates:

```bash
eidos portal serve --path eidos --port 8080
```

Open `http://localhost:8080` in your web browser. Any changes made to your `.adoc` files in `eidos/` are immediately reflected in the portal.

---

## Air-Gapped Static Site Export (`INV-PORTAL-AIR-GAPPED-BUNDLE`)

You can export the entire portal as a static site that can be hosted on GitHub Pages, GitLab Pages, AWS S3, or opened in air-gapped environments:

```bash
eidos portal build --path eidos --out public
```

This creates a self-contained bundle with **zero external CDN dependencies** and **zero Node.js runtime requirement (`INV-PORTAL-ZERO-NODE-DEP`)**:

```text
public/
├── index.html       # Pre-compiled single-file React 19 / Tailwind v4 / MD3 SPA
│                    # (inline Mermaid.js v11, @asciidoctor/core, and Geist woff2 fonts)
├── favicon.svg      # Vector portal icon
└── data.json        # Compiled AST, C4 topology, and graph metadata of all specifications
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

