# Eidos Portal

The **Eidos Portal** is an interactive, visual web application generated directly from your living specifications.

Structured around a responsive left sidebar, the portal organizes your architecture into a logical information flow:

1. **Executive Dashboard (Home)**: System archetype (`SYS-*`), high-level KPI metric cards, and recent SpecDeltas stream.
2. **Business Architecture**: Actor Personas (`PER-*`), Bounded Domains (`DOM-*`), Capabilities (`CAP-*`) with JTBD statements, System Policies (`POL-*`), and Governance Invariants (`INV-*`).
3. **Design & Experience**: Multi-Design Systems (`DS-*`), interactive W3C DTCG Token Studio (`tokens.json`), UI Component Manifests (`UIC-*`), Screen State Machines (`SCR-*`), and Omnichannel User Flows (`FLOW-*`).
4. **Technical Architecture**: C4 Level 1 System Context, C4 Level 2 Container Topologies with dependency connections, Monorepo Component Manifests (`CMP-*`), and Protocol Contracts (`CTR-*`).
5. **SpecDeltas Pipeline**: Interactive Kanban board tracking atomic mutations across `proposed`, `in_review`, `approved`, and `applied` states, with full blast-radius visualization.
6. **Technical Explorations**: Discovery spikes (`EXP-*`) tracking hypotheses, exit criteria, trade-off findings, and resulting SpecDeltas.
7. **Specs Master Catalog**: Filterable repository inventory with instant fuzzy search (`CMD+K`) and rich document inspection.

---

## Local Interactive Development

To inspect your architecture locally with live-reload:

```bash
eidos portal serve --path eidos --port 8080
```

Open `http://localhost:8080` in your web browser. Any changes made to your `.adoc` files in `eidos/` are immediately reflected in the portal.

---

## Static Site Export

You can export the entire portal as a static site that can be hosted on GitHub Pages, GitLab Pages, AWS S3, or any web server:

```bash
eidos portal build --path eidos --out public
```

This creates a self-contained bundle with zero external CDN dependencies (`INV-PORTAL-AIR-GAPPED-BUNDLE`):

```
public/
├── index.html       # Fully bundled single-file SPA (embedded Mermaid, Asciidoctor, fonts)
└── data.json        # Compiled AST and graph metadata of all specifications
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

