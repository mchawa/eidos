# Living Architecture Portal

The **Living Architecture Portal** is an interactive, visual web application generated directly from your living specifications.

It renders C4 architecture diagrams, container dependencies, capability hierarchies, system policies, and SpecDelta timelines without requiring external diagramming tools or manual sync.

---

## Local Interactive Development

To inspect your architecture locally with live-reload:

```bash
eidos serve --path eidos --port 8080
```

Open `http://localhost:8080` in your web browser. Any changes made to your `.adoc` files in `eidos/` are immediately reflected in the portal.

---

## Static Site Export

You can export the entire portal as a static site that can be hosted on GitHub Pages, GitLab Pages, AWS S3, or any web server:

```bash
eidos build-site --path eidos --out public
```

This creates a self-contained bundle:

```
public/
├── index.html       # Single-Page Application interface
├── data.json        # Compiled AST and graph metadata of all specifications
└── assets/          # Stylesheets and visual diagram renderer
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
      - run: uv run eidos build-site --path eidos --out public
      - uses: actions/upload-pages-artifact@v3
        with:
          path: public
      - id: deployment
        uses: actions/deploy-pages@v4
```
