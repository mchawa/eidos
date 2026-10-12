"""HTML, CSS, and client-side JavaScript templates for the Eidos Living Web Portal."""

PORTAL_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Eidos Living Web Portal</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    :root {
      --bg: #090d16;
      --bg-surface: #0f172a;
      --bg-card: #111827;
      --bg-card-hover: #1e293b;
      --border: #1e293b;
      --border-accent: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --primary-subtle: rgba(59, 130, 246, 0.12);
      --success: #10b981;
      --success-subtle: rgba(16, 185, 129, 0.12);
      --warning: #f59e0b;
      --warning-subtle: rgba(245, 158, 11, 0.12);
      --danger: #ef4444;
      --danger-subtle: rgba(239, 68, 68, 0.12);
      --accent: #8b5cf6;
      --accent-subtle: rgba(139, 92, 246, 0.12);
      --code-bg: #030712;
      --sidebar-width: 270px;
      --header-height: 60px;
    }
    html.light {
      --bg: #f8fafc;
      --bg-surface: #ffffff;
      --bg-card: #ffffff;
      --bg-card-hover: #f1f5f9;
      --border: #e2e8f0;
      --border-accent: #cbd5e1;
      --text: #0f172a;
      --text-muted: #64748b;
      --text-dim: #94a3b8;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --primary-subtle: rgba(37, 99, 235, 0.08);
      --success: #059669;
      --success-subtle: rgba(5, 150, 105, 0.08);
      --warning: #d97706;
      --warning-subtle: rgba(217, 119, 6, 0.08);
      --danger: #dc2626;
      --danger-subtle: rgba(220, 38, 38, 0.08);
      --accent: #7c3aed;
      --accent-subtle: rgba(124, 58, 237, 0.08);
      --code-bg: #f1f5f9;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      min-height: 100vh;
      overflow-x: hidden;
    }

    /* APP LAYOUT */
    .portal-shell {
      display: flex;
      min-height: 100vh;
      width: 100vw;
    }

    /* SIDEBAR */
    .sidebar {
      width: var(--sidebar-width);
      background: var(--bg-surface);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow-y: auto;
      overflow-x: hidden;
      z-index: 40;
      transition: width 0.2s cubic-bezier(0.4, 0, 0.2, 1), transform 0.2s ease;
    }
    .sidebar-header {
      padding: 1.25rem 1.25rem 1rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      transition: padding 0.2s ease;
    }
    .brand-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      font-size: 1.2rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: var(--text);
      min-width: 0;
    }
    .brand-icon {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 6px;
      background: linear-gradient(135deg, var(--primary), var(--accent));
      color: #fff;
      font-size: 0.95rem;
      flex-shrink: 0;
    }
    .brand-title {
      white-space: nowrap;
      overflow: hidden;
    }
    .brand-actions {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      flex-shrink: 0;
    }
    .sidebar-collapse-btn {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text-muted);
      width: 26px;
      height: 26px;
      border-radius: 0.35rem;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: all 0.15s ease;
      padding: 0;
      flex-shrink: 0;
    }
    .sidebar-collapse-btn:hover {
      color: var(--text);
      border-color: var(--border-accent);
      background: var(--bg-card-hover);
    }
    .collapse-icon {
      transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .brand-meta {
      display: flex;
      gap: 0.35rem;
      align-items: center;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 0.15rem 0.5rem;
      border-radius: 9999px;
      letter-spacing: 0.02em;
      border: 1px solid transparent;
    }
    .badge-primary { background: var(--primary-subtle); color: var(--primary); border-color: rgba(59, 130, 246, 0.25); }
    .badge-success { background: var(--success-subtle); color: var(--success); border-color: rgba(16, 185, 129, 0.25); }
    .badge-warning { background: var(--warning-subtle); color: var(--warning); border-color: rgba(245, 158, 11, 0.25); }
    .badge-danger { background: var(--danger-subtle); color: var(--danger); border-color: rgba(239, 68, 68, 0.25); }
    .badge-accent { background: var(--accent-subtle); color: var(--accent); border-color: rgba(139, 92, 246, 0.25); }
    .badge-neutral { background: var(--code-bg); color: var(--text-muted); border-color: var(--border); }

    .sidebar-nav {
      flex: 1;
      padding: 1rem 0.75rem;
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }
    .nav-group-title {
      font-size: 0.68rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-dim);
      padding: 0.75rem 0.75rem 0.25rem;
    }
    .nav-link {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      padding: 0.55rem 0.75rem;
      border-radius: 0.45rem;
      color: var(--text-muted);
      text-decoration: none;
      font-size: 0.85rem;
      font-weight: 500;
      cursor: pointer;
      border: none;
      background: transparent;
      width: 100%;
      text-align: left;
      transition: all 0.15s ease;
    }
    .nav-link:hover {
      color: var(--text);
      background: var(--bg-card-hover);
    }
    .nav-link.active {
      color: #fff;
      background: var(--primary);
      font-weight: 600;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    }
    .nav-icon {
      font-size: 1rem;
      width: 20px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .nav-label { flex: 1; }
    .nav-counter {
      font-size: 0.7rem;
      padding: 0.1rem 0.45rem;
      border-radius: 9999px;
      background: var(--code-bg);
      color: var(--text-muted);
      font-weight: 600;
    }
    .nav-link.active .nav-counter {
      background: rgba(255, 255, 255, 0.25);
      color: #fff;
    }

    .sidebar-footer {
      padding: 1rem 1.25rem;
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .btn-theme {
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      padding: 0.45rem 0.75rem;
      border-radius: 0.4rem;
      border: 1px solid var(--border);
      background: var(--bg-card);
      color: var(--text);
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.15s ease;
    }
    .btn-theme:hover { background: var(--bg-card-hover); }
    .footer-status {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      font-size: 0.72rem;
      color: var(--text-dim);
      font-weight: 500;
    }
    .status-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 6px var(--success);
    }

    /* MAIN STAGE */
    .portal-main {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-width: 0;
      background: var(--bg);
    }
    .top-header {
      height: var(--header-height);
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border);
      padding: 0 1.75rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 30;
    }
    .header-left {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .mobile-menu-btn {
      display: none;
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.35rem 0.55rem;
      border-radius: 0.375rem;
      cursor: pointer;
      font-size: 1rem;
    }
    .breadcrumb {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.85rem;
      color: var(--text-muted);
    }
    .breadcrumb-root { font-weight: 600; color: var(--text-muted); }
    .breadcrumb-sep { color: var(--text-dim); }
    .breadcrumb-active { font-weight: 600; color: var(--text); }

    .header-right {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .search-container {
      position: relative;
      display: flex;
      align-items: center;
    }
    .search-icon {
      position: absolute;
      left: 0.65rem;
      font-size: 0.8rem;
      color: var(--text-dim);
      pointer-events: none;
    }
    .search-input {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.4rem;
      padding: 0.35rem 0.75rem 0.35rem 2rem;
      color: var(--text);
      font-size: 0.82rem;
      width: 240px;
      transition: all 0.15s ease;
    }
    .search-input:focus {
      outline: none;
      border-color: var(--primary);
      width: 300px;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
    }
    .btn-header {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.4rem;
      color: var(--text-muted);
      padding: 0.35rem 0.65rem;
      font-size: 0.8rem;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      transition: all 0.15s ease;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
    }
    .btn-header:hover { color: var(--text); border-color: var(--border-accent); }

    /* CONTENT VIEWPORT */
    .viewport {
      padding: 1.75rem;
      max-width: 1440px;
      width: 100%;
      margin: 0 auto;
    }
    .tab-content { display: none; }
    .tab-content.active { display: block; animation: fadeIn 0.15s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

    /* HERO ARCHETYPE CARD */
    .hero-card {
      background: linear-gradient(135deg, rgba(59, 130, 246, 0.08), rgba(139, 92, 246, 0.05)), var(--bg-card);
      border: 1px solid var(--border-accent);
      border-radius: 0.75rem;
      padding: 1.5rem 1.75rem;
      margin-bottom: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .hero-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      flex-wrap: wrap;
      gap: 1rem;
    }
    .hero-title {
      font-size: 1.45rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .hero-desc {
      font-size: 0.92rem;
      color: var(--text-muted);
      max-width: 900px;
      line-height: 1.6;
    }
    .hero-badges {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-top: 0.25rem;
    }

    /* KPI METRIC CARDS */
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1rem;
      margin-bottom: 1.75rem;
    }
    .metric-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      padding: 1rem 1.25rem;
      cursor: pointer;
      transition: all 0.15s ease;
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }
    .metric-card:hover {
      border-color: var(--primary);
      transform: translateY(-2px);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    }
    .metric-val {
      font-size: 1.9rem;
      font-weight: 800;
      color: var(--text);
      letter-spacing: -0.03em;
    }
    .metric-label {
      font-size: 0.72rem;
      color: var(--text-dim);
      text-transform: uppercase;
      font-weight: 700;
      letter-spacing: 0.06em;
    }
    .metric-sub {
      font-size: 0.75rem;
      color: var(--text-muted);
    }

    /* QUICK NAVIGATION HUB */
    .quick-nav-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 1rem;
      margin-bottom: 1.75rem;
    }
    .quick-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      padding: 1.25rem;
      cursor: pointer;
      transition: all 0.15s ease;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .quick-card:hover {
      border-color: var(--primary);
      background: var(--bg-card-hover);
      transform: translateY(-2px);
    }
    .quick-card-header {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      font-weight: 700;
      font-size: 0.95rem;
      color: var(--text);
    }
    .quick-card-desc {
      font-size: 0.82rem;
      color: var(--text-muted);
      line-height: 1.5;
    }
    .quick-card-action {
      font-size: 0.78rem;
      font-weight: 600;
      color: var(--primary);
      display: flex;
      align-items: center;
      gap: 0.3rem;
      margin-top: auto;
    }

    /* SECTION PANELS */
    .panel {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1.5rem;
    }
    .panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .panel-title {
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .panel-desc {
      font-size: 0.83rem;
      color: var(--text-muted);
      margin-top: 0.2rem;
    }

    /* TABLES */
    .table-responsive {
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 0.5rem;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 0.84rem;
    }
    th {
      background: var(--bg-surface);
      padding: 0.75rem 1rem;
      font-weight: 600;
      color: var(--text-muted);
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }
    td {
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--border);
      color: var(--text);
    }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--bg-card-hover); }
    .code-pill {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      background: var(--code-bg);
      color: var(--text);
      padding: 0.15rem 0.4rem;
      border-radius: 0.25rem;
      border: 1px solid var(--border);
      display: inline-block;
    }

    /* KANBAN BOARD */
    .kanban-board {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 1rem;
    }
    .kanban-col {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      min-height: 520px;
    }
    .kanban-col-header {
      font-size: 0.8rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.6rem;
    }
    .cards-list {
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
      flex: 1;
    }
    .delta-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.45rem;
      padding: 0.85rem;
      cursor: pointer;
      transition: all 0.15s ease;
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }
    .delta-card:hover {
      border-color: var(--primary);
      transform: translateY(-1px);
      box-shadow: 0 4px 10px rgba(0, 0, 0, 0.2);
    }
    .delta-card-id {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.73rem;
      font-weight: 600;
      color: var(--primary);
    }
    .delta-card-title {
      font-size: 0.84rem;
      font-weight: 600;
      color: var(--text);
      line-height: 1.35;
    }
    .delta-card-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 0.2rem;
    }

    /* W3C TOKENS STUDIO */
    .token-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
      gap: 0.85rem;
      margin-bottom: 1.5rem;
    }
    .swatch {
      border: 1px solid var(--border);
      border-radius: 0.45rem;
      overflow: hidden;
      background: var(--bg);
      font-size: 0.75rem;
    }
    .swatch-preview { height: 55px; width: 100%; }
    .swatch-info { padding: 0.5rem; display: flex; flex-direction: column; gap: 0.2rem; }
    .swatch-title { font-weight: 600; color: var(--text); font-size: 0.75rem; }
    .swatch-val { font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; color: var(--text-dim); }

    .token-scale-row {
      display: flex;
      align-items: center;
      gap: 1rem;
      padding: 0.5rem 0;
      border-bottom: 1px solid var(--border);
    }
    .token-scale-row:last-child { border-bottom: none; }
    .scale-label { width: 80px; font-weight: 600; font-size: 0.8rem; color: var(--text); }
    .scale-val { width: 70px; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--text-dim); }
    .scale-bar-container { flex: 1; background: var(--code-bg); height: 16px; border-radius: 4px; overflow: hidden; display: flex; align-items: center; padding: 2px; }
    .scale-bar { height: 100%; background: var(--primary); border-radius: 3px; }

    /* MERMAID PANELS */
    .mermaid-box {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      overflow-x: auto;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      position: relative;
    }

    /* LARGE ARCHITECTURE TOPOLOGY (C4 LEVEL 1 & 2) */
    .mermaid-architecture {
      padding: 2.25rem 1.5rem;
      min-height: 440px;
    }
    .mermaid-architecture svg {
      width: 100% !important;
      max-width: 1100px !important;
      min-width: 680px;
      height: auto !important;
      min-height: 340px;
      display: block;
      margin: 0 auto;
      transform-origin: top center;
      transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .mermaid-architecture .node .label {
      font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
      font-size: 15px !important;
      font-weight: 600 !important;
      line-height: 1.45 !important;
    }
    .mermaid-architecture .node rect,
    .mermaid-architecture .node circle,
    .mermaid-architecture .node polygon {
      stroke-width: 2.5px !important;
    }
    .mermaid-architecture .edgeLabel {
      font-family: 'Inter', system-ui, sans-serif !important;
      font-size: 13px !important;
      font-weight: 500 !important;
      background: var(--bg-card) !important;
      color: var(--text-muted) !important;
      padding: 3px 8px !important;
      border-radius: 4px !important;
      border: 1px solid var(--border) !important;
    }

    /* COMPACT SCREEN STATE MACHINE DIAGRAMS */
    .mermaid-compact {
      padding: 1rem;
      min-height: 120px;
      background: var(--bg);
    }
    .mermaid-compact svg {
      width: 100% !important;
      max-width: 380px !important;
      min-width: 0 !important;
      height: auto !important;
      max-height: 180px !important;
      display: block;
      margin: 0 auto;
    }
    .mermaid-compact .node .label {
      font-family: 'Inter', system-ui, sans-serif !important;
      font-size: 13px !important;
      font-weight: 600 !important;
    }
    .mermaid-compact .edgeLabel {
      font-family: 'Inter', system-ui, sans-serif !important;
      font-size: 11px !important;
      padding: 2px 6px !important;
      background: var(--bg-card) !important;
      border-radius: 3px !important;
    }
    .diagram-controls {
      display: flex;
      gap: 0.4rem;
      align-items: center;
    }
    .btn-ctrl {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text-muted);
      border-radius: 0.35rem;
      padding: 0.25rem 0.55rem;
      font-size: 0.75rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    /* LIVING SPECS MASTER CATALOG & TREE VIEW */
    .catalog-toolbar-actions {
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .view-mode-pills {
      display: inline-flex;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.45rem;
      padding: 2px;
      gap: 2px;
    }
    .view-pill {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 0.35rem 0.75rem;
      font-size: 0.78rem;
      font-weight: 600;
      border-radius: 0.35rem;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      transition: all 0.15s ease;
    }
    .view-pill:hover { color: var(--text); }
    .view-pill.active {
      background: var(--primary);
      color: #fff;
      box-shadow: 0 1px 3px rgba(0,0,0,0.2);
    }

    .catalog-filter-bar {
      display: flex;
      gap: 0.75rem;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      margin-bottom: 1rem;
      padding: 0.75rem 1rem;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
    }
    .catalog-search-box {
      position: relative;
      flex: 1;
      min-width: 240px;
      max-width: 440px;
      display: flex;
      align-items: center;
    }
    .catalog-search-input {
      width: 100%;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.4rem;
      padding: 0.4rem 2rem 0.4rem 2rem;
      color: var(--text);
      font-size: 0.82rem;
      transition: all 0.15s ease;
    }
    .catalog-search-input:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
    }
    .clear-search-btn {
      position: absolute;
      right: 0.6rem;
      background: transparent;
      border: none;
      color: var(--text-dim);
      cursor: pointer;
      font-size: 0.8rem;
      display: none;
      padding: 0.2rem;
    }
    .clear-search-btn:hover { color: var(--text); }

    .catalog-filter-controls {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .catalog-select {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.38rem 0.65rem;
      border-radius: 0.375rem;
      font-size: 0.78rem;
      font-weight: 500;
      cursor: pointer;
    }
    .catalog-select:focus { outline: none; border-color: var(--primary); }

    .tree-group-toggle {
      display: inline-flex;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.375rem;
      padding: 2px;
      gap: 2px;
    }
    .btn-group-toggle {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 0.25rem 0.55rem;
      font-size: 0.75rem;
      font-weight: 600;
      border-radius: 0.25rem;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .btn-group-toggle:hover { color: var(--text); }
    .btn-group-toggle.active {
      background: var(--bg-card);
      color: var(--primary);
      box-shadow: 0 1px 2px rgba(0,0,0,0.15);
    }

    /* MASTER-DETAIL LAYOUT */
    .catalog-layout {
      display: flex;
      gap: 1.25rem;
      align-items: flex-start;
      min-height: 600px;
    }
    .catalog-tree-pane {
      width: 380px;
      min-width: 320px;
      flex-shrink: 0;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      display: flex;
      flex-direction: column;
      max-height: calc(100vh - 170px);
      position: sticky;
      top: calc(var(--header-height) + 1.25rem);
      overflow: hidden;
    }
    .tree-pane-header {
      padding: 0.75rem 1rem;
      background: var(--bg-card);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .tree-pane-count {
      font-size: 0.72rem;
      background: var(--code-bg);
      color: var(--text);
      padding: 0.1rem 0.5rem;
      border-radius: 9999px;
      border: 1px solid var(--border);
    }
    .tree-scroll-area {
      padding: 0.5rem;
      overflow-y: auto;
      flex: 1;
      font-size: 0.82rem;
    }

    /* TREE NODES */
    .tree-folder {
      margin-bottom: 0.15rem;
    }
    .tree-folder-row {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      padding: 0.35rem 0.5rem;
      border-radius: 0.375rem;
      cursor: pointer;
      color: var(--text);
      font-weight: 600;
      transition: background 0.12s ease;
      user-select: none;
    }
    .tree-folder-row:hover { background: var(--bg-card-hover); }
    .tree-chevron {
      font-size: 0.65rem;
      color: var(--text-dim);
      width: 14px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: transform 0.15s ease;
    }
    .tree-folder-icon { font-size: 0.95rem; }
    .tree-folder-name { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .tree-folder-badge {
      font-size: 0.68rem;
      background: var(--code-bg);
      color: var(--text-dim);
      padding: 0.08rem 0.4rem;
      border-radius: 9999px;
      font-weight: 600;
    }
    .tree-children {
      padding-left: 1.15rem;
      border-left: 1px dashed var(--border);
      margin-left: 0.65rem;
      margin-top: 0.15rem;
    }

    .tree-leaf {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.38rem 0.55rem;
      border-radius: 0.375rem;
      cursor: pointer;
      color: var(--text-muted);
      transition: all 0.12s ease;
      border: 1px solid transparent;
      user-select: none;
      margin-bottom: 0.1rem;
    }
    .tree-leaf:hover {
      color: var(--text);
      background: var(--bg-card-hover);
    }
    .tree-leaf.selected {
      background: var(--primary-subtle);
      color: var(--primary);
      font-weight: 600;
      border-color: rgba(59, 130, 246, 0.3);
    }
    .tree-leaf-icon { font-size: 0.88rem; flex-shrink: 0; }
    .tree-leaf-id {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.74rem;
      flex-shrink: 0;
    }
    .tree-leaf-title {
      flex: 1;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 0.78rem;
    }
    .tree-leaf-valid {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .tree-leaf-valid.valid { background: var(--success); }
    .tree-leaf-valid.invalid { background: var(--danger); }

    /* DETAIL INSPECTOR PANE */
    .catalog-detail-pane {
      flex: 1;
      min-width: 0;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 0.6rem;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
    }
    .inspector-header {
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.25rem;
    }
    .inspector-top-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
    }
    .inspector-ids {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .inspector-id {
      font-size: 0.85rem;
      font-weight: 700;
      color: var(--primary);
      padding: 0.2rem 0.55rem;
    }
    .inspector-title {
      font-size: 1.35rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: var(--text);
      line-height: 1.3;
    }
    .inspector-path-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.78rem;
      color: var(--text-dim);
      font-family: 'JetBrains Mono', monospace;
    }
    .inspector-meta-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 0.75rem;
    }
    .meta-chip {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.45rem;
      padding: 0.6rem 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
    }
    .meta-chip-label {
      font-size: 0.68rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-dim);
    }
    .meta-chip-val {
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .inspector-tabs {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .inspector-tab-nav {
      display: flex;
      gap: 0.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.5rem;
    }
    .inspector-tab-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 0.82rem;
      font-weight: 600;
      padding: 0.35rem 0.75rem;
      border-radius: 0.35rem;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .inspector-tab-btn:hover { color: var(--text); background: var(--bg-card-hover); }
    .inspector-tab-btn.active {
      color: var(--primary);
      background: var(--primary-subtle);
    }
    .inspector-tab-pane { display: none; }
    .inspector-tab-pane.active { display: block; animation: fadeIn 0.15s ease; }
    .inspector-code-box {
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 0.45rem;
      padding: 1rem;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.76rem;
      line-height: 1.5;
      overflow-x: auto;
      max-height: 480px;
      color: var(--text);
    }
    .inspector-body-content {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.45rem;
      padding: 1.25rem;
      font-size: 0.85rem;
      line-height: 1.6;
      max-height: 520px;
      overflow-y: auto;
      white-space: pre-wrap;
      font-family: 'Inter', system-ui, sans-serif;
    }
    .relation-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.25rem 0.6rem;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.35rem;
      font-size: 0.75rem;
      cursor: pointer;
      transition: all 0.15s ease;
      font-family: 'JetBrains Mono', monospace;
    }
    .relation-pill:hover {
      border-color: var(--primary);
      background: var(--primary-subtle);
      color: var(--primary);
      transform: translateY(-1px);
    }

    /* TOAST */
    .toast-popup {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: var(--bg-surface);
      border: 1px solid var(--primary);
      color: var(--text);
      padding: 0.6rem 1.1rem;
      border-radius: 0.45rem;
      box-shadow: 0 10px 25px rgba(0,0,0,0.3);
      font-size: 0.82rem;
      font-weight: 600;
      z-index: 110;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      transform: translateY(20px);
      opacity: 0;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      pointer-events: none;
    }
    .toast-popup.show {
      transform: translateY(0);
      opacity: 1;
    }

    /* MODAL DRAWER */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.65);
      backdrop-filter: blur(4px);
      z-index: 100;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
    }
    .modal-backdrop.open { display: flex; animation: modalFadeIn 0.15s ease; }
    @keyframes modalFadeIn { from { opacity: 0; } to { opacity: 1; } }
    .modal-box {
      background: var(--bg-surface);
      border: 1px solid var(--border-accent);
      border-radius: 0.65rem;
      width: 100%;
      max-width: 820px;
      max-height: 88vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
      overflow: hidden;
    }
    .modal-header {
      padding: 1.25rem 1.5rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
    }
    .modal-body {
      padding: 1.5rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
      font-size: 0.88rem;
    }
    .modal-close {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-muted);
      border-radius: 0.35rem;
      width: 30px;
      height: 30px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 1rem;
      transition: all 0.15s ease;
    }
    .modal-close:hover { color: var(--text); border-color: var(--border-accent); }

    /* RESPONSIVE BREAKPOINTS & SIDEBAR COLLAPSE */
    @media (min-width: 901px) {
      .sidebar.collapsed {
        width: 68px;
      }
      .sidebar.collapsed .sidebar-header {
        padding: 1rem 0.5rem;
        align-items: center;
      }
      .sidebar.collapsed .brand-row {
        flex-direction: column;
        gap: 0.6rem;
        justify-content: center;
        align-items: center;
      }
      .sidebar.collapsed .brand-title,
      .sidebar.collapsed .brand-badge,
      .sidebar.collapsed .brand-meta,
      .sidebar.collapsed .nav-label,
      .sidebar.collapsed .nav-counter,
      .sidebar.collapsed .nav-group-title,
      .sidebar.collapsed #themeLabel,
      .sidebar.collapsed .footer-status span:last-child {
        display: none !important;
      }
      .sidebar.collapsed .collapse-icon {
        transform: rotate(180deg);
      }
      .sidebar.collapsed .sidebar-nav {
        padding: 0.75rem 0.4rem;
        align-items: center;
      }
      .sidebar.collapsed .nav-link {
        justify-content: center;
        padding: 0.65rem 0;
        width: 44px;
        height: 40px;
        margin: 0 auto;
        border-radius: 0.5rem;
      }
      .sidebar.collapsed .nav-icon {
        margin: 0;
      }
      .sidebar.collapsed .sidebar-footer {
        padding: 0.75rem 0.5rem;
        align-items: center;
      }
      .sidebar.collapsed .btn-theme {
        width: 40px;
        height: 36px;
        padding: 0;
        justify-content: center;
      }
      .sidebar.collapsed .footer-status {
        justify-content: center;
        padding: 0.25rem 0;
      }
    }
    @media (max-width: 1024px) {
      .catalog-layout {
        flex-direction: column;
      }
      .catalog-tree-pane {
        width: 100%;
        max-height: 380px;
        position: static;
      }
    }
    @media (max-width: 900px) {
      .sidebar {
        position: fixed;
        left: 0;
        top: 0;
        bottom: 0;
        transform: translateX(-100%);
      }
      .sidebar.open { transform: translateX(0); box-shadow: 0 0 30px rgba(0,0,0,0.5); }
      .mobile-menu-btn { display: inline-flex; }
      .sidebar-collapse-btn { display: none; }
      .search-input { width: 160px; }
      .search-input:focus { width: 200px; }
    }
  </style>
</head>
<body>
  <div class="portal-shell">
    <!-- COLLAPSIBLE RESPONSIVE SIDEBAR -->
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-header">
        <div class="brand-row">
          <div class="brand">
            <span class="brand-icon">⚡</span>
            <span class="brand-title">Eidos</span>
          </div>
          <div class="brand-actions">
            <span class="badge badge-success brand-badge" id="sidebarPassBadge">VALID</span>
            <button class="sidebar-collapse-btn" id="sidebarCollapseBtn" onclick="toggleSidebarCollapse()" title="Collapse sidebar (Ctrl+B)" aria-label="Collapse sidebar">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="collapse-icon">
                <polyline points="15 18 9 12 15 6"></polyline>
              </svg>
            </button>
          </div>
        </div>
        <div class="brand-meta">
          <span class="badge badge-primary" id="sidebarSystemId">SYS-EIDOS-001</span>
          <span class="badge badge-neutral" id="sidebarVersion">v0.3.0</span>
        </div>
      </div>

      <nav class="sidebar-nav">
        <div class="nav-group-title">Overview</div>
        <button class="nav-link active" onclick="switchTab('home')" title="Home (Overview)">
          <span class="nav-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 9.5L12 3l9 6.5V20a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9.5z"/>
              <polyline points="9 22 9 12 15 12 15 22"/>
            </svg>
          </span>
          <span class="nav-label">Home</span>
        </button>

        <div class="nav-group-title">Information Flow</div>
        <button class="nav-link" onclick="switchTab('business')" title="1. Business & Governance">
          <span class="nav-icon">💼</span>
          <span class="nav-label">1. Business & Governance</span>
          <span class="nav-counter" id="navCountBusiness">0</span>
        </button>
        <button class="nav-link" onclick="switchTab('design')" title="2. Design & Experience">
          <span class="nav-icon">🎨</span>
          <span class="nav-label">2. Design & Experience</span>
          <span class="nav-counter" id="navCountDesign">0</span>
        </button>
        <button class="nav-link" onclick="switchTab('architecture')" title="3. Technical Architecture">
          <span class="nav-icon">🏛️</span>
          <span class="nav-label">3. Technical Architecture</span>
          <span class="nav-counter" id="navCountArchitecture">0</span>
        </button>

        <div class="nav-group-title">Mutation Lifecycle</div>
        <button class="nav-link" onclick="switchTab('deltas')" title="SpecDeltas (Kanban)">
          <span class="nav-icon">🔄</span>
          <span class="nav-label">SpecDeltas (Kanban)</span>
          <span class="nav-counter" id="navCountDeltas">0</span>
        </button>

        <div class="nav-group-title">Repository Registry</div>
        <button class="nav-link" onclick="switchTab('catalog')" title="Living Specs Catalog">
          <span class="nav-icon">📑</span>
          <span class="nav-label">Living Specs Catalog</span>
          <span class="nav-counter" id="navCountCatalog">0</span>
        </button>
      </nav>

      <div class="sidebar-footer">
        <button class="btn-theme" onclick="toggleTheme()" title="Switch dark/light theme">
          <span id="themeLabel">Dark Theme</span>
          <span id="themeIcon">🌙</span>
        </button>
        <div class="footer-status" title="Git-Native • Zero Drift">
          <span class="status-dot"></span>
          <span>Git-Native • Zero Drift</span>
        </div>
      </div>
    </aside>

    <!-- MAIN VIEWPORT STAGE -->
    <main class="portal-main">
      <header class="top-header">
        <div class="header-left">
          <button class="mobile-menu-btn" onclick="toggleMobileSidebar()" title="Toggle sidebar">☰</button>
          <div class="breadcrumb">
            <span class="breadcrumb-root">Eidos</span>
            <span class="breadcrumb-sep">/</span>
            <span class="breadcrumb-active" id="breadcrumbActive">Home</span>
          </div>
        </div>

        <div class="header-right">
          <div class="search-container">
            <span class="search-icon">🔍</span>
            <input type="text" class="search-input" id="globalSearch" placeholder="Search living specs... (/)" oninput="handleSearch(this.value)">
          </div>
          <a href="/api/data" target="_blank" class="btn-header" title="Inspect raw JSON endpoint">API JSON</a>
          <button class="btn-header" onclick="toggleTheme()" title="Toggle light/dark mode">🌓</button>
        </div>
      </header>

      <div class="viewport">
        <!-- 1. HOME VIEW (SCR-PORTAL-HOME) -->
        <section id="tab-home" class="tab-content active">
          <!-- Archetype Hero -->
          <div class="hero-card">
            <div class="hero-top">
              <div>
                <h1 class="hero-title" id="heroSystemTitle">Eidos: Living Architecture & Spec Engine</h1>
                <p class="hero-desc" id="heroSystemDesc">
                  Git is the single source of authoritative truth. Invariants bridge human Jobs-to-be-Done with deterministic automated tests. Living specifications continuously co-evolve with the codebase with zero documentation drift.
                </p>
              </div>
              <div class="badge badge-success" style="font-size: 0.85rem; padding: 0.4rem 0.9rem;">
                ● 100% Valid Baseline
              </div>
            </div>
            <div class="hero-badges">
              <span class="badge badge-primary">Direct & Planned Workflows</span>
              <span class="badge badge-accent">No ADRs (Living Graph)</span>
              <span class="badge badge-success">Invariant-Test Traceability</span>
              <span class="badge badge-neutral">Continuous Docs Sync</span>
            </div>
          </div>

          <!-- Hero KPI Metric Grid -->
          <div class="metric-grid">
            <div class="metric-card" onclick="switchTab('catalog')">
              <div class="metric-label">Total Living Specs</div>
              <div class="metric-val" id="statTotalSpecs">0</div>
              <div class="metric-sub">100% schema compliant</div>
            </div>
            <div class="metric-card" onclick="switchTab('business')">
              <div class="metric-label">Domains & Caps</div>
              <div class="metric-val" id="statDomainsCaps">0 / 0</div>
              <div class="metric-sub">Bounded business contexts</div>
            </div>
            <div class="metric-card" onclick="switchTab('business')">
              <div class="metric-label">Governance Invariants</div>
              <div class="metric-val" id="statInvariants">0</div>
              <div class="metric-sub">Automated test bindings</div>
            </div>
            <div class="metric-card" onclick="switchTab('design')">
              <div class="metric-label">Design & Experience</div>
              <div class="metric-val" id="statDesign">0 / 0</div>
              <div class="metric-sub">UI components & screens</div>
            </div>
            <div class="metric-card" onclick="switchTab('architecture')">
              <div class="metric-label">C4 Containers</div>
              <div class="metric-val" id="statContainers">0</div>
              <div class="metric-sub">Monorepo packages & contracts</div>
            </div>
            <div class="metric-card" onclick="switchTab('deltas')">
              <div class="metric-label">SpecDeltas</div>
              <div class="metric-val" id="statDeltas">0</div>
              <div class="metric-sub">Atomic mutation pipeline</div>
            </div>
          </div>

          <!-- Quick Navigation Hub -->
          <div class="quick-nav-grid">
            <div class="quick-card" onclick="switchTab('business')">
              <div class="quick-card-header">
                <span>💼</span>
                <span>1. Business Architecture</span>
              </div>
              <div class="quick-card-desc">
                Explore actor personas, bounded domains, capability JTBDs, governance policies, and hypothesis spikes.
              </div>
              <div class="quick-card-action">View Business Specs →</div>
            </div>

            <div class="quick-card" onclick="switchTab('design')">
              <div class="quick-card-header">
                <span>🎨</span>
                <span>2. Design & Experience</span>
              </div>
              <div class="quick-card-desc">
                Inspect W3C DTCG tokens studio, UI component contracts with accessibility invariants, and screen state machines.
              </div>
              <div class="quick-card-action">View Design Systems →</div>
            </div>

            <div class="quick-card" onclick="switchTab('architecture')">
              <div class="quick-card-header">
                <span>🏛️</span>
                <span>3. Technical Architecture</span>
              </div>
              <div class="quick-card-desc">
                Inspect interactive C4 Level 1 context diagrams, C4 Level 2 container topologies, and formal interface contracts.
              </div>
              <div class="quick-card-action">View C4 Diagrams →</div>
            </div>

            <div class="quick-card" onclick="switchTab('deltas')">
              <div class="quick-card-header">
                <span>🔄</span>
                <span>SpecDeltas Pipeline</span>
              </div>
              <div class="quick-card-desc">
                Monitor the Kanban board of proposed, in-review, approved, and applied architectural mutations.
              </div>
              <div class="quick-card-action">View Kanban Board →</div>
            </div>
          </div>

          <!-- Recent SpecDeltas Feed -->
          <div class="panel">
            <div class="panel-header">
              <div>
                <h3 class="panel-title"><span>🔄</span> Recent Architectural Mutations</h3>
                <p class="panel-desc">Atomic SpecDeltas co-evolving the system's living specifications</p>
              </div>
              <button class="btn-header" onclick="switchTab('deltas')">View Kanban Board →</button>
            </div>
            <div class="table-responsive">
              <table id="homeRecentDeltasTable">
                <thead>
                  <tr>
                    <th>Delta ID</th>
                    <th>Kind</th>
                    <th>Title</th>
                    <th>Status</th>
                    <th>Mutations</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
        </section>

        <!-- 2. BUSINESS ARCHITECTURE VIEW (SCR-PORTAL-BUSINESS) -->
        <section id="tab-business" class="tab-content">
          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>👥</span> Actor Personas (PER-*)</h2>
                <p class="panel-desc">Primary human and autonomous AI agent actors interacting with the system</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="personasTable">
                <thead>
                  <tr>
                    <th>Persona ID</th>
                    <th>Name</th>
                    <th>Role / Archetype</th>
                    <th>Responsibilities & Interaction Modes</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>💼</span> Product Domains (DOM-*)</h2>
                <p class="panel-desc">Bounded strategic contexts organizing business responsibilities</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="domainsTable">
                <thead>
                  <tr>
                    <th>Domain ID</th>
                    <th>Name</th>
                    <th>Classification</th>
                    <th>Core Responsibilities</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>🎯</span> Product Capabilities & JTBD (CAP-*)</h2>
                <p class="panel-desc">Customer Jobs-to-be-Done and functional capability anchors</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="capabilitiesTable">
                <thead>
                  <tr>
                    <th>Capability ID</th>
                    <th>Domain</th>
                    <th>Title</th>
                    <th>Job-To-Be-Done (JTBD)</th>
                    <th>Invariants</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>⚖️</span> System Policies & Invariant Traceability Matrix (POL-* / INV-*)</h2>
                <p class="panel-desc">Non-negotiable architectural rules bound directly to deterministic automated tests</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="invariantsTable">
                <thead>
                  <tr>
                    <th>Invariant ID</th>
                    <th>Category</th>
                    <th>Source Definition</th>
                    <th>Rule Condition</th>
                    <th>Automated Test Binding</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>🔬</span> Product Explorations & Spikes (EXP-*)</h2>
                <p class="panel-desc">Hypothesis-driven architectural research and sandboxed proof-of-concepts</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="explorationsTable">
                <thead>
                  <tr>
                    <th>Exploration ID</th>
                    <th>Title</th>
                    <th>Status</th>
                    <th>Hypothesis Statement</th>
                    <th>Decision / Outcome</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
        </section>

        <!-- 3. DESIGN & EXPERIENCE VIEW (SCR-PORTAL-DESIGN) -->
        <section id="tab-design" class="tab-content">
          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>🎨</span> Multi-Design Systems Catalog (DS-*)</h2>
                <p class="panel-desc">Design systems supporting multi-tenant brand token inheritance and overrides</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="designSystemsTable">
                <thead>
                  <tr>
                    <th>Design System ID</th>
                    <th>Title / Brand</th>
                    <th>Client ID</th>
                    <th>Inheritance</th>
                    <th>Token Manifest</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>💎</span> W3C DTCG Design Tokens Studio</h2>
                <p class="panel-desc">W3C Design Tokens Community Group standard tokens rendered from <span class="code-pill">tokens.json</span></p>
              </div>
            </div>
            <h4 style="font-size: 0.88rem; margin-bottom: 0.75rem; color: var(--text);">Color Palettes & Surfaces</h4>
            <div class="token-grid" id="tokenSwatchesGrid"></div>

            <h4 style="font-size: 0.88rem; margin: 1.25rem 0 0.75rem; color: var(--text);">Dimension & Spacing Scales</h4>
            <div id="tokenSpacingScales"></div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>🧩</span> UI Component Contracts (UIC-*)</h2>
                <p class="panel-desc">Reusable interface components with accessibility invariants and keyboard contracts</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="uiComponentsTable">
                <thead>
                  <tr>
                    <th>Component ID</th>
                    <th>Title</th>
                    <th>Design System</th>
                    <th>Category</th>
                    <th>Variants</th>
                    <th>WCAG / ARIA Role</th>
                    <th>Keyboard Interaction</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>📱</span> Product Screen State Machines (SCR-*)</h2>
                <p class="panel-desc">Verified layout slots and finite state machines across product views</p>
              </div>
            </div>
            <div id="screenStateMachinesContainer" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 1.25rem;"></div>
          </div>
        </section>

        <!-- 4. TECHNICAL ARCHITECTURE VIEW (SCR-PORTAL-ARCHITECTURE) -->
        <section id="tab-architecture" class="tab-content">
          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>🌐</span> C4 Level 1: System Context Topology</h2>
                <p class="panel-desc">High-level ecosystem context showing human personas, autonomous agents, and external integrations</p>
              </div>
              <div class="diagram-controls">
                <button class="btn-ctrl" onclick="zoomDiagram('c4ContextDiagram', 1.25)" title="Zoom in">🔍 +</button>
                <button class="btn-ctrl" onclick="zoomDiagram('c4ContextDiagram', 0.8)" title="Zoom out">🔍 -</button>
                <button class="btn-ctrl" onclick="resetZoomDiagram('c4ContextDiagram')" title="Reset zoom">Reset</button>
              </div>
            </div>
            <div class="mermaid-box mermaid-architecture">
              <div class="mermaid" id="c4ContextDiagram">graph TD; Loading System Context...</div>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>📦</span> C4 Level 2: Container Topology</h2>
                <p class="panel-desc">Software containers, libraries, daemon services, and web applications</p>
              </div>
              <div class="diagram-controls">
                <button class="btn-ctrl" onclick="zoomDiagram('c4ContainersDiagram', 1.25)" title="Zoom in">🔍 +</button>
                <button class="btn-ctrl" onclick="zoomDiagram('c4ContainersDiagram', 0.8)" title="Zoom out">🔍 -</button>
                <button class="btn-ctrl" onclick="resetZoomDiagram('c4ContainersDiagram')" title="Reset zoom">Reset</button>
              </div>
            </div>
            <div class="mermaid-box mermaid-architecture">
              <div class="mermaid" id="c4ContainersDiagram">graph TD; Loading Container Topology...</div>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>⚙️</span> Containers & Internal Modules (CMP-*)</h2>
                <p class="panel-desc">Arc42 Section 5.2 / C4 Level 3 component manifests in the monorepo</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="componentsTable">
                <thead>
                  <tr>
                    <th>Container ID</th>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Runtime</th>
                    <th>Package Path</th>
                    <th>Internal Modules</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>🔌</span> Interface & Protocol Contracts (CTR-*)</h2>
                <p class="panel-desc">Syntactic and semantic protocols governing CLI, JSON-RPC MCP, and HTTP APIs</p>
              </div>
            </div>
            <div class="table-responsive">
              <table id="contractsTable">
                <thead>
                  <tr>
                    <th>Contract ID</th>
                    <th>Title</th>
                    <th>Protocol</th>
                    <th>Owning Component</th>
                    <th>Specification Reference</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
        </section>

        <!-- 5. SPECDELTAS KANBAN VIEW (SCR-PORTAL-DELTAS) -->
        <section id="tab-deltas" class="tab-content">
          <div class="panel-header" style="margin-bottom: 1.25rem;">
            <div>
              <h2 class="panel-title"><span>🔄</span> SpecDeltas Mutation Pipeline (DLT-*)</h2>
              <p class="panel-desc">Git-native mutation lifecycle tracking architectural changes from proposal to reconciliation</p>
            </div>
          </div>
          <div class="kanban-board">
            <div class="kanban-col" id="col-proposed">
              <div class="kanban-col-header">
                <span>Proposed</span>
                <span class="badge badge-neutral" id="count-proposed">0</span>
              </div>
              <div class="cards-list" id="cards-proposed"></div>
            </div>
            <div class="kanban-col" id="col-in_review">
              <div class="kanban-col-header">
                <span>In Review</span>
                <span class="badge badge-warning" id="count-in_review">0</span>
              </div>
              <div class="cards-list" id="cards-in_review"></div>
            </div>
            <div class="kanban-col" id="col-approved">
              <div class="kanban-col-header">
                <span>Approved</span>
                <span class="badge badge-accent" id="count-approved">0</span>
              </div>
              <div class="cards-list" id="cards-approved"></div>
            </div>
            <div class="kanban-col" id="col-applied">
              <div class="kanban-col-header">
                <span>Applied</span>
                <span class="badge badge-success" id="count-applied">0</span>
              </div>
              <div class="cards-list" id="cards-applied"></div>
            </div>
          </div>
        </section>

        <!-- 6. LIVING SPECS MASTER CATALOG (SCR-PORTAL-CATALOG) -->
        <section id="tab-catalog" class="tab-content">
          <div class="panel">
            <div class="panel-header">
              <div>
                <h2 class="panel-title"><span>📑</span> Living Specifications Master Catalog</h2>
                <p class="panel-desc">Interactive tree navigation and inspector across all validated living specifications</p>
              </div>
              <div class="catalog-toolbar-actions">
                <div class="view-mode-pills">
                  <button class="view-pill active" id="btnViewTree" onclick="setCatalogViewMode('tree')" title="Repository Hierarchy Tree">
                    <span>📂</span> Tree View
                  </button>
                  <button class="view-pill" id="btnViewTable" onclick="setCatalogViewMode('table')" title="Tabular Matrix">
                    <span>📋</span> Table View
                  </button>
                </div>
              </div>
            </div>

            <!-- Global Search & Tree Filter Controls -->
            <div class="catalog-filter-bar">
              <div class="catalog-search-box">
                <span class="search-icon">🔍</span>
                <input type="text" class="catalog-search-input" id="catalogSearchInput" placeholder="Filter specs by ID, title, path, or schema..." oninput="handleCatalogFilter(this.value)">
                <button class="clear-search-btn" id="catalogClearSearch" onclick="clearCatalogFilter()" title="Clear filter">✕</button>
              </div>
              <div class="catalog-filter-controls">
                <select id="catalogTypeFilter" onchange="handleCatalogFilter(document.getElementById('catalogSearchInput').value)" class="catalog-select">
                  <option value="all">All Document Types</option>
                  <option value="product">Business Specs (DOM, CAP, PER, POL, EXP)</option>
                  <option value="design">Design Specs (DS, UIC, SCR)</option>
                  <option value="tech">Tech Architecture (ARCH, CMP, CTR, SYS)</option>
                  <option value="delta">SpecDeltas (DLT)</option>
                </select>
                <div class="tree-group-toggle" id="treeGroupToggle">
                  <button class="btn-group-toggle active" id="btnGroupPath" onclick="setTreeGrouping('path')" title="Organize by Git Directory Tree">Path</button>
                  <button class="btn-group-toggle" id="btnGroupCategory" onclick="setTreeGrouping('category')" title="Organize by Domain & Type Taxonomy">Category</button>
                </div>
                <div class="tree-quick-controls">
                  <button class="btn-ctrl" onclick="expandAllCatalogTree()" title="Expand all tree folders">Expand All</button>
                  <button class="btn-ctrl" onclick="collapseAllCatalogTree()" title="Collapse all tree folders">Collapse All</button>
                </div>
                <span class="badge badge-primary" id="catalogMatchCount">44 Specs</span>
              </div>
            </div>

            <!-- MASTER-DETAIL TREE LAYOUT -->
            <div class="catalog-layout" id="catalogTreeLayout">
              <!-- Left Tree Pane -->
              <div class="catalog-tree-pane">
                <div class="tree-pane-header">
                  <span class="tree-pane-title" id="treePaneTitle">Repository Hierarchy (eidos/)</span>
                  <span class="tree-pane-count" id="treePaneCount">44</span>
                </div>
                <div class="tree-scroll-area" id="catalogTreeContainer">
                  <!-- Dynamic Tree Nodes Rendered Here -->
                </div>
              </div>

              <!-- Right Detail Inspector Pane -->
              <div class="catalog-detail-pane" id="catalogDetailPane">
                <!-- Selected Spec Inspection Rendered Here -->
              </div>
            </div>

            <!-- FLAT TABLE VIEW (Alternate Mode) -->
            <div class="catalog-table-view" id="catalogTableView" style="display: none;">
              <div class="table-responsive">
                <table id="specsCatalogTable">
                  <thead>
                    <tr>
                      <th>Document ID</th>
                      <th>Type</th>
                      <th>Title</th>
                      <th>Status</th>
                      <th>File Path</th>
                    </tr>
                  </thead>
                  <tbody></tbody>
                </table>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  </div>

  <!-- DETAIL MODAL DRAWER -->
  <div class="modal-backdrop" id="detailModal" onclick="closeModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()">
      <div class="modal-header">
        <div>
          <h2 id="modalTitle" style="font-size: 1.15rem; font-weight: 700; color: var(--text);">Specification Details</h2>
          <span class="code-pill" id="modalDocId" style="margin-top: 0.25rem;">ID</span>
        </div>
        <button class="modal-close" onclick="closeModal()" title="Close (Escape)">✕</button>
      </div>
      <div class="modal-body" id="modalContent"></div>
    </div>
  </div>

  <script>
    let PORTAL_DATA = window.__EIDOS_DATA__ || null;
    let CURRENT_TAB = 'home';

    // Immediate check to restore sidebar collapsed state before rendering
    try {
      if (localStorage.getItem('eidos_sidebar_collapsed') === 'true') {
        document.getElementById('sidebar')?.classList.add('collapsed');
      }
    } catch(e) {}

    mermaid.initialize({
      startOnLoad: false,
      theme: document.documentElement.classList.contains('dark') ? 'dark' : 'default',
      securityLevel: 'loose',
      fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
      fontSize: 16,
      flowchart: {
        useMaxWidth: false,
        htmlLabels: true,
        curve: 'basis',
        nodeSpacing: 55,
        rankSpacing: 65,
        padding: 22
      }
    });

    async function initPortal() {
      initSidebarState();
      if (!PORTAL_DATA) {
        try {
          const res = await fetch('/api/data');
          PORTAL_DATA = await res.json();
        } catch (e) {
          console.error("Failed to load /api/data:", e);
          return;
        }
      }
      renderAllViews();
      setupKeyboardShortcuts();
    }

    function renderAllViews() {
      if (!PORTAL_DATA) return;
      const ov = PORTAL_DATA.overview;

      // Sidebar badges
      document.getElementById('sidebarSystemId').innerText = ov.system_id || "SYS-EIDOS-001";
      document.getElementById('navCountBusiness').innerText = (ov.domains_count || 0) + (ov.capabilities_count || 0);
      document.getElementById('navCountDesign').innerText = (ov.design_systems_count || 0) + (ov.ui_components_count || 0) + (ov.screens_count || 0);
      document.getElementById('navCountArchitecture').innerText = (ov.components_count || 0) + (ov.contracts_count || 0);
      document.getElementById('navCountDeltas').innerText = ov.deltas_count || 0;
      document.getElementById('navCountCatalog').innerText = ov.total_specs || 0;

      // 1. Home View
      document.getElementById('heroSystemTitle').innerText = ov.system_title || "Eidos Living Architecture";
      document.getElementById('statTotalSpecs').innerText = ov.total_specs;
      document.getElementById('statDomainsCaps').innerText = `${ov.domains_count} / ${ov.capabilities_count}`;
      document.getElementById('statInvariants').innerText = ov.invariants_count;
      document.getElementById('statDesign').innerText = `${ov.ui_components_count || 0} / ${ov.screens_count || 0}`;
      document.getElementById('statContainers').innerText = ov.components_count;
      document.getElementById('statDeltas').innerText = ov.deltas_count;

      renderRecentDeltasTable();

      // 2. Business View
      renderPersonasTable();
      renderDomainsTable();
      renderCapabilitiesTable();
      renderInvariantsTable();
      renderExplorationsTable();

      // 3. Design View
      renderDesignSystemsTable();
      renderTokenStudio();
      renderUiComponentsTable();
      renderScreenStateMachines();

      // 4. Architecture View
      renderMermaidDiagram('c4ContextDiagram', PORTAL_DATA.diagrams.c4_context);
      renderMermaidDiagram('c4ContainersDiagram', PORTAL_DATA.diagrams.c4_containers);
      renderComponentsTable();
      renderContractsTable();

      // 5. SpecDeltas Kanban
      renderKanbanBoard();

      // 6. Specs Catalog
      renderCatalog(PORTAL_DATA.specs);
    }

    function renderRecentDeltasTable() {
      const tbody = document.querySelector('#homeRecentDeltasTable tbody');
      tbody.innerHTML = '';
      const allDeltas = [
        ...(PORTAL_DATA.deltas.applied || []),
        ...(PORTAL_DATA.deltas.approved || []),
        ...(PORTAL_DATA.deltas.in_review || []),
        ...(PORTAL_DATA.deltas.proposed || [])
      ].slice(0, 5);

      allDeltas.forEach(d => {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.onclick = () => showDeltaDetails(d);
        const kindBadge = d.kind === 'bugfix' ? 'badge-danger' : (d.kind === 'refactor' ? 'badge-accent' : 'badge-primary');
        const stBadge = d.status === 'applied' ? 'badge-success' : (d.status === 'approved' ? 'badge-accent' : 'badge-warning');
        tr.innerHTML = `
          <td><span class="code-pill">${d.id}</span></td>
          <td><span class="badge ${kindBadge}">${d.kind}</span></td>
          <td><b>${d.title}</b></td>
          <td><span class="badge ${stBadge}">${d.status}</span></td>
          <td><span class="code-pill">${d.mutations?.length || 0} mutations</span></td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderPersonasTable() {
      const tbody = document.querySelector('#personasTable tbody');
      tbody.innerHTML = '';
      (PORTAL_DATA.personas || []).forEach(p => {
        const tr = document.createElement('tr');
        const traits = (p.responsibilities || []).map(r => `• ${r}`).join('<br>') || p.role || 'N/A';
        tr.innerHTML = `
          <td><span class="code-pill">${p.id}</span></td>
          <td><b>${p.name}</b></td>
          <td><span class="badge badge-accent">${p.archetype || 'actor'}</span></td>
          <td style="font-size: 0.8rem;">${traits}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderDomainsTable() {
      const tbody = document.querySelector('#domainsTable tbody');
      tbody.innerHTML = '';
      (PORTAL_DATA.domains || []).forEach(d => {
        const tr = document.createElement('tr');
        const resps = (d.boundaries?.responsibilities || []).map(r => `• ${r}`).join('<br>') || 'N/A';
        tr.innerHTML = `
          <td><span class="code-pill">${d.id}</span></td>
          <td><b>${d.name}</b></td>
          <td><span class="badge badge-primary">${d.classification || 'core'}</span></td>
          <td style="font-size: 0.8rem;">${resps}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderCapabilitiesTable() {
      const tbody = document.querySelector('#capabilitiesTable tbody');
      tbody.innerHTML = '';
      (PORTAL_DATA.capabilities || []).forEach(cap => {
        const tr = document.createElement('tr');
        const invCount = cap.technical?.invariants?.length || 0;
        const jtbd = cap.business?.jtbd || 'N/A';
        tr.innerHTML = `
          <td><span class="code-pill">${cap.id}</span></td>
          <td><span class="code-pill">${cap.domain || 'N/A'}</span></td>
          <td><b>${cap.title}</b></td>
          <td style="font-size: 0.82rem; color: var(--text-muted);">${jtbd}</td>
          <td><span class="badge badge-success">${invCount} invariants</span></td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderInvariantsTable() {
      const tbody = document.querySelector('#invariantsTable tbody');
      tbody.innerHTML = '';
      (PORTAL_DATA.invariants || []).forEach(inv => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="code-pill" style="font-weight: 600;">${inv.id}</span></td>
          <td><span class="badge badge-accent">${inv.category}</span></td>
          <td><span class="code-pill">${inv.source_type}</span></td>
          <td style="font-size: 0.82rem;">${inv.rule}</td>
          <td><span class="code-pill" style="font-size: 0.72rem;">${inv.test_binding}</span></td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderExplorationsTable() {
      const tbody = document.querySelector('#explorationsTable tbody');
      tbody.innerHTML = '';
      (PORTAL_DATA.explorations || []).forEach(exp => {
        const tr = document.createElement('tr');
        const stBadge = exp.status === 'adopted' ? 'badge-success' : (exp.status === 'in_progress' ? 'badge-warning' : 'badge-neutral');
        const outcome = exp.decision?.outcome ? `${exp.decision.outcome} (${exp.decision.resulting_delta_id || ''})` : 'Under evaluation';
        tr.innerHTML = `
          <td><span class="code-pill">${exp.id}</span></td>
          <td><b>${exp.title}</b></td>
          <td><span class="badge ${stBadge}">${exp.status}</span></td>
          <td style="font-size: 0.82rem; color: var(--text-muted);">${exp.hypothesis}</td>
          <td><span class="badge badge-primary">${outcome}</span></td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderDesignSystemsTable() {
      const tbody = document.querySelector('#designSystemsTable tbody');
      tbody.innerHTML = '';
      (PORTAL_DATA.design_systems || []).forEach(ds => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="code-pill">${ds.id}</span></td>
          <td><b>${ds.title || ds.brand_name}</b></td>
          <td><span class="badge badge-neutral">${ds.client}</span></td>
          <td><span class="code-pill">${ds.extends || 'None (Base)'}</span></td>
          <td><span class="code-pill">tokens.json</span></td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderTokenStudio() {
      const grid = document.getElementById('tokenSwatchesGrid');
      grid.innerHTML = '';

      // Extract colors from DS-CORE tokens
      const coreTokens = PORTAL_DATA.design_systems?.[0]?.tokens || {};
      const colors = coreTokens.color || {
        brand: { primary: { $value: "#3b82f6" }, accent: { $value: "#8b5cf6" } },
        feedback: { success: { $value: "#10b981" }, warning: { $value: "#f59e0b" }, danger: { $value: "#ef4444" } }
      };

      function flattenColors(obj, prefix = '') {
        let entries = [];
        for (const [key, val] of Object.entries(obj)) {
          if (val && typeof val === 'object') {
            if (val.$value && typeof val.$value === 'string' && val.$value.startsWith('#')) {
              entries.push({ name: `${prefix}${key}`, value: val.$value });
            } else {
              entries = entries.concat(flattenColors(val, `${prefix}${key}.`));
            }
          }
        }
        return entries;
      }

      const flatColors = flattenColors(colors);
      flatColors.forEach(c => {
        const swatch = document.createElement('div');
        swatch.className = 'swatch';
        swatch.innerHTML = `
          <div class="swatch-preview" style="background: ${c.value};"></div>
          <div class="swatch-info">
            <span class="swatch-title">${c.name}</span>
            <span class="swatch-val">${c.value}</span>
          </div>
        `;
        grid.appendChild(swatch);
      });

      // Spacing scales
      const scaleContainer = document.getElementById('tokenSpacingScales');
      scaleContainer.innerHTML = '';
      const spacings = coreTokens.spacing || {
        xs: { $value: "4px" },
        sm: { $value: "8px" },
        md: { $value: "16px" },
        lg: { $value: "24px" },
        xl: { $value: "32px" }
      };

      Object.entries(spacings).forEach(([k, v]) => {
        const rawVal = v.$value || "16px";
        const px = parseInt(rawVal, 10) || 16;
        const row = document.createElement('div');
        row.className = 'token-scale-row';
        row.innerHTML = `
          <div class="scale-label">${k}</div>
          <div class="scale-val">${rawVal}</div>
          <div class="scale-bar-container">
            <div class="scale-bar" style="width: ${Math.min(px * 6, 260)}px;"></div>
          </div>
        `;
        scaleContainer.appendChild(row);
      });
    }

    function renderUiComponentsTable() {
      const tbody = document.querySelector('#uiComponentsTable tbody');
      tbody.innerHTML = '';
      (PORTAL_DATA.ui_components || []).forEach(u => {
        const tr = document.createElement('tr');
        const varList = (u.variants || []).map(v => v.name).join(', ') || 'standard';
        const a11y = u.accessibility || {};
        tr.innerHTML = `
          <td><span class="code-pill">${u.id}</span></td>
          <td><b>${u.title}</b></td>
          <td><span class="code-pill">${u.design_system}</span></td>
          <td><span class="badge badge-accent">${u.category}</span></td>
          <td><span class="badge badge-neutral">${varList}</span></td>
          <td><span class="badge badge-success">${a11y.wcag_level || 'AA'}</span> <span class="code-pill">${a11y.aria_role || 'role'}</span></td>
          <td style="font-size: 0.78rem; color: var(--text-muted);">${a11y.keyboard_nav || 'Tab navigable'}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderScreenStateMachines() {
      const container = document.getElementById('screenStateMachinesContainer');
      container.innerHTML = '';
      (PORTAL_DATA.screens || []).forEach(s => {
        const card = document.createElement('div');
        card.className = 'panel';
        card.style.background = 'var(--bg-surface)';
        card.innerHTML = `
          <div class="panel-header" style="margin-bottom: 0.75rem;">
            <div>
              <h3 class="panel-title" style="font-size: 0.92rem;"><span>📱</span> ${s.title}</h3>
              <p class="panel-desc" style="font-size: 0.76rem;"><span class="code-pill">${s.id}</span> • Route: <span class="code-pill">${s.route}</span></p>
            </div>
            <span class="badge badge-primary" style="font-size: 0.7rem;">${s.system_ref}</span>
          </div>
          <div class="mermaid-box mermaid-compact">
            <div class="mermaid" id="screen_mermaid_${s.id.replace(/-/g, '_')}">${s.mermaid}</div>
          </div>
        `;
        container.appendChild(card);
        renderMermaidDiagram(`screen_mermaid_${s.id.replace(/-/g, '_')}`, s.mermaid);
      });
    }

    function renderComponentsTable() {
      const compBody = document.querySelector('#componentsTable tbody');
      compBody.innerHTML = '';
      (PORTAL_DATA.components || []).forEach(c => {
        const tr = document.createElement('tr');
        const modules = (c.internal_components || []).map(m => m.name).join(', ') || 'N/A';
        tr.innerHTML = `
          <td><span class="code-pill">${c.id}</span></td>
          <td><b>${c.name}</b></td>
          <td><span class="badge badge-primary">${c.container_type}</span></td>
          <td>${c.runtime}</td>
          <td><span class="code-pill">${c.package_path}</span></td>
          <td>${modules}</td>
        `;
        compBody.appendChild(tr);
      });
    }

    function renderContractsTable() {
      const ctrBody = document.querySelector('#contractsTable tbody');
      ctrBody.innerHTML = '';
      (PORTAL_DATA.contracts || []).forEach(ct => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="code-pill">${ct.id}</span></td>
          <td><b>${ct.title}</b></td>
          <td><span class="badge badge-accent">${ct.protocol}</span></td>
          <td><span class="code-pill">${ct.owning_component}</span></td>
          <td><span class="code-pill">${ct.specification_file}</span></td>
        `;
        ctrBody.appendChild(tr);
      });
    }

    function renderKanbanBoard() {
      const deltas = PORTAL_DATA.deltas;
      ['proposed', 'in_review', 'approved', 'applied'].forEach(st => {
        const list = deltas[st] || [];
        document.getElementById(`count-${st}`).innerText = list.length;
        const container = document.getElementById(`cards-${st}`);
        container.innerHTML = '';
        list.forEach(d => {
          const card = document.createElement('div');
          card.className = 'delta-card';
          card.onclick = () => showDeltaDetails(d);
          const kindBadge = d.kind === 'bugfix' ? 'badge-danger' : (d.kind === 'refactor' ? 'badge-accent' : 'badge-primary');
          card.innerHTML = `
            <div class="delta-card-id">${d.id}</div>
            <div class="delta-card-title">${d.title}</div>
            <div class="delta-card-meta">
              <span class="badge ${kindBadge}">${d.kind}</span>
              <span style="font-size: 0.72rem; color: var(--text-dim);">${d.mutations?.length || 0} muts</span>
            </div>
          `;
          container.appendChild(card);
        });
      });
    }

    let CATALOG_VIEW_MODE = 'tree';
    let CATALOG_TREE_GROUPING = 'path';
    let SELECTED_SPEC_ID = null;
    let ACTIVE_INSPECTOR_TAB = 'yaml';

    function setCatalogViewMode(mode) {
      CATALOG_VIEW_MODE = mode;
      document.getElementById('btnViewTree')?.classList.toggle('active', mode === 'tree');
      document.getElementById('btnViewTable')?.classList.toggle('active', mode === 'table');

      const treeLayout = document.getElementById('catalogTreeLayout');
      const tableView = document.getElementById('catalogTableView');
      const groupToggle = document.getElementById('treeGroupToggle');

      if (mode === 'tree') {
        if (treeLayout) treeLayout.style.display = 'flex';
        if (tableView) tableView.style.display = 'none';
        if (groupToggle) groupToggle.style.display = 'inline-flex';
      } else {
        if (treeLayout) treeLayout.style.display = 'none';
        if (tableView) tableView.style.display = 'block';
        if (groupToggle) groupToggle.style.display = 'none';
      }
      filterAndRenderCatalog();
    }

    function setTreeGrouping(grouping) {
      CATALOG_TREE_GROUPING = grouping;
      document.getElementById('btnGroupPath')?.classList.toggle('active', grouping === 'path');
      document.getElementById('btnGroupCategory')?.classList.toggle('active', grouping === 'category');

      const titleEl = document.getElementById('treePaneTitle');
      if (titleEl) {
        titleEl.innerText = grouping === 'path' ? 'Repository Hierarchy (eidos/)' : 'Categorical Taxonomy';
      }
      filterAndRenderCatalog();
    }

    function handleCatalogFilter(query) {
      const clearBtn = document.getElementById('catalogClearSearch');
      if (clearBtn) {
        clearBtn.style.display = query ? 'inline-block' : 'none';
      }
      filterAndRenderCatalog();
    }

    function clearCatalogFilter() {
      const searchInput = document.getElementById('catalogSearchInput');
      if (searchInput) searchInput.value = '';
      const globalSearch = document.getElementById('globalSearch');
      if (globalSearch) globalSearch.value = '';
      const clearBtn = document.getElementById('catalogClearSearch');
      if (clearBtn) clearBtn.style.display = 'none';
      const typeFilter = document.getElementById('catalogTypeFilter');
      if (typeFilter) typeFilter.value = 'all';
      filterAndRenderCatalog();
    }

    function filterAndRenderCatalog() {
      if (!PORTAL_DATA) return;
      const query = (document.getElementById('catalogSearchInput')?.value || document.getElementById('globalSearch')?.value || '').toLowerCase().trim();
      const typeFilter = document.getElementById('catalogTypeFilter')?.value || 'all';

      let filtered = PORTAL_DATA.specs || [];
      if (typeFilter !== 'all') {
        filtered = filtered.filter(s => s.type.startsWith(typeFilter));
      }
      if (query) {
        filtered = filtered.filter(s =>
          s.id.toLowerCase().includes(query) ||
          s.title.toLowerCase().includes(query) ||
          s.type.toLowerCase().includes(query) ||
          s.path.toLowerCase().includes(query) ||
          (s.frontmatter && JSON.stringify(s.frontmatter).toLowerCase().includes(query))
        );
      }

      const countBadge = document.getElementById('catalogMatchCount');
      if (countBadge) countBadge.innerText = `${filtered.length} Specs`;
      const paneCount = document.getElementById('treePaneCount');
      if (paneCount) paneCount.innerText = filtered.length;

      renderCatalogTree(filtered, !!query);
      renderCatalogTable(filtered);

      if (filtered.length > 0) {
        if (!SELECTED_SPEC_ID || !filtered.some(s => s.id === SELECTED_SPEC_ID)) {
          selectCatalogSpec(filtered[0].id, false);
        } else {
          const spec = filtered.find(s => s.id === SELECTED_SPEC_ID);
          if (spec) renderSpecDetailInspector(spec);
        }
      } else {
        renderEmptyInspector(query);
      }
    }

    function filterCatalog() {
      filterAndRenderCatalog();
    }

    function renderCatalog(specs) {
      if (!SELECTED_SPEC_ID && specs && specs.length > 0) {
        SELECTED_SPEC_ID = specs[0].id;
      }
      filterAndRenderCatalog();
    }

    function buildPathTree(specs) {
      const root = { name: "eidos", isDir: true, path: "eidos", children: {}, specs: [] };

      specs.forEach(s => {
        const rawParts = s.path.split('/').filter(Boolean);
        const parts = (rawParts[0] === 'eidos') ? rawParts.slice(1) : rawParts;

        let current = root;
        let currentPath = "eidos";
        for (let i = 0; i < parts.length - 1; i++) {
          const dirName = parts[i];
          currentPath += "/" + dirName;
          if (!current.children[dirName]) {
            current.children[dirName] = {
              name: dirName,
              isDir: true,
              path: currentPath,
              children: {},
              specs: []
            };
          }
          current = current.children[dirName];
        }
        current.specs.push(s);
      });

      return root;
    }

    function getFolderTotalSpecs(folderNode) {
      let count = folderNode.specs.length;
      for (const child of Object.values(folderNode.children)) {
        count += getFolderTotalSpecs(child);
      }
      return count;
    }

    function renderCatalogTree(specs, isFiltering = false) {
      const container = document.getElementById('catalogTreeContainer');
      if (!container) return;

      if (specs.length === 0) {
        container.innerHTML = `
          <div style="padding: 2.5rem 1rem; text-align: center; color: var(--text-dim);">
            <div style="font-size: 1.8rem; margin-bottom: 0.5rem;">🔍</div>
            <div style="font-weight: 700; font-size: 0.88rem; color: var(--text);">No matching specifications</div>
            <div style="font-size: 0.78rem; margin-top: 0.25rem;">Try adjusting search keywords or type filters</div>
            <button class="btn-ctrl" onclick="clearCatalogFilter()" style="margin-top: 0.8rem; font-size: 0.75rem;">Clear Filters</button>
          </div>
        `;
        return;
      }

      if (CATALOG_TREE_GROUPING === 'path') {
        const rootTree = buildPathTree(specs);
        container.innerHTML = renderFolderNodeHtml(rootTree, true, isFiltering);
      } else {
        container.innerHTML = renderCategoryTreeHtml(specs, isFiltering);
      }

      if (SELECTED_SPEC_ID) {
        const safeId = SELECTED_SPEC_ID.replace(/[^a-zA-Z0-9_]/g, '_');
        const target = document.getElementById(`treeLeaf_${safeId}`);
        if (target) target.classList.add('selected');
      }
    }

    function renderFolderNodeHtml(folderNode, isRoot = false, forceOpen = false) {
      const folderTotal = getFolderTotalSpecs(folderNode);
      if (folderTotal === 0) return '';

      const folderIcons = {
        architecture: '🏛️',
        components: '⚙️',
        contracts: '🔌',
        deltas: '🔄',
        design: '🎨',
        screens: '📱',
        systems: '📐',
        domains: '💼',
        capabilities: '⚡',
        explorations: '🧪',
        personas: '👥',
        policies: '⚖️'
      };
      const icon = folderIcons[folderNode.name] || '📁';
      const openIcon = folderIcons[folderNode.name] || '📂';

      const childFolders = Object.values(folderNode.children).sort((a, b) => a.name.localeCompare(b.name));
      const childSpecs = folderNode.specs.sort((a, b) => a.id.localeCompare(b.id));

      const childrenHtml = `
        ${childFolders.map(cf => renderFolderNodeHtml(cf, false, forceOpen)).join('')}
        ${childSpecs.map(s => renderLeafNodeHtml(s)).join('')}
      `;

      if (isRoot) {
        return childrenHtml;
      }

      const isOpen = forceOpen || true;

      return `
        <div class="tree-folder ${isOpen ? 'open' : ''}" data-path="${folderNode.path}">
          <div class="tree-folder-row" onclick="toggleFolderNode(this)">
            <span class="tree-chevron">${isOpen ? '▼' : '▶'}</span>
            <span class="tree-folder-icon">${isOpen ? openIcon : icon}</span>
            <span class="tree-folder-name">${folderNode.name}</span>
            <span class="tree-folder-badge">${folderTotal}</span>
          </div>
          <div class="tree-children" style="display: ${isOpen ? 'block' : 'none'};">
            ${childrenHtml}
          </div>
        </div>
      `;
    }

    function renderCategoryTreeHtml(specs, forceOpen = false) {
      const taxonomy = [
        {
          id: "business",
          name: "Business & Governance",
          icon: "💼",
          subgroups: [
            { name: "Domains (DOM)", type: "product/domain", icon: "🏢" },
            { name: "Capabilities (CAP)", type: "product/capability", icon: "⚡" },
            { name: "Actor Personas (PER)", type: "product/persona", icon: "👤" },
            { name: "Policies (POL)", type: "product/policy", icon: "⚖️" },
            { name: "Explorations (EXP)", type: "product/exploration", icon: "🧪" }
          ]
        },
        {
          id: "design",
          name: "Design & Experience",
          icon: "🎨",
          subgroups: [
            { name: "Design Systems (DS)", type: "design/system", icon: "📐" },
            { name: "UI Components (UIC)", type: "design/ui-component", icon: "🧩" },
            { name: "Screens (SCR)", type: "design/screen", icon: "📱" }
          ]
        },
        {
          id: "tech",
          name: "Technical Architecture",
          icon: "🏛️",
          subgroups: [
            { name: "System Archetype (SYS)", type: "product/system", icon: "⚡" },
            { name: "Architecture (ARCH)", type: "tech/architecture", icon: "🏛️" },
            { name: "Containers & Modules (CMP)", type: "tech/component", icon: "⚙️" },
            { name: "Contracts (CTR)", type: "tech/contract", icon: "🔌" }
          ]
        },
        {
          id: "delta",
          name: "Mutation Lifecycle",
          icon: "🔄",
          subgroups: [
            { name: "SpecDeltas (DLT)", type: "delta/mutation", icon: "🔄" }
          ]
        }
      ];

      return taxonomy.map(group => {
        let groupSpecsCount = 0;
        const subHtml = group.subgroups.map(sub => {
          const matching = specs.filter(s => s.type === sub.type).sort((a,b) => a.id.localeCompare(b.id));
          groupSpecsCount += matching.length;
          if (matching.length === 0) return '';
          return `
            <div class="tree-folder open">
              <div class="tree-folder-row" onclick="toggleFolderNode(this)">
                <span class="tree-chevron">▼</span>
                <span class="tree-folder-icon">${sub.icon}</span>
                <span class="tree-folder-name">${sub.name}</span>
                <span class="tree-folder-badge">${matching.length}</span>
              </div>
              <div class="tree-children" style="display: block;">
                ${matching.map(s => renderLeafNodeHtml(s)).join('')}
              </div>
            </div>
          `;
        }).join('');

        if (groupSpecsCount === 0) return '';

        return `
          <div class="tree-folder open" style="margin-bottom: 0.5rem;">
            <div class="tree-folder-row" onclick="toggleFolderNode(this)" style="background: var(--bg-card); font-weight: 700;">
              <span class="tree-chevron">▼</span>
              <span class="tree-folder-icon">${group.icon}</span>
              <span class="tree-folder-name">${group.name}</span>
              <span class="tree-folder-badge" style="background: var(--primary-subtle); color: var(--primary);">${groupSpecsCount}</span>
            </div>
            <div class="tree-children" style="display: block; border-left-color: var(--primary);">
              ${subHtml}
            </div>
          </div>
        `;
      }).join('');
    }

    function renderLeafNodeHtml(spec) {
      const typeIcons = {
        'product/system': '⚡',
        'product/domain': '🏢',
        'product/capability': '⚡',
        'product/persona': '👤',
        'product/policy': '⚖️',
        'product/exploration': '🧪',
        'design/system': '📐',
        'design/ui-component': '🧩',
        'design/screen': '📱',
        'tech/architecture': '🏛️',
        'tech/component': '⚙️',
        'tech/contract': '🔌',
        'delta/mutation': '🔄'
      };
      const icon = typeIcons[spec.type] || '📄';
      const isSelected = spec.id === SELECTED_SPEC_ID;
      const safeId = spec.id.replace(/[^a-zA-Z0-9_]/g, '_');

      return `
        <div class="tree-leaf ${isSelected ? 'selected' : ''}" id="treeLeaf_${safeId}" data-id="${spec.id}" onclick="selectCatalogSpec('${spec.id}')" title="${escapeHtml(spec.title)} (${spec.path})">
          <span class="tree-leaf-icon">${icon}</span>
          <span class="tree-leaf-id">${spec.id}</span>
          <span class="tree-leaf-title">${escapeHtml(spec.title)}</span>
          <span class="tree-leaf-valid ${spec.is_valid ? 'valid' : 'invalid'}" title="${spec.is_valid ? 'Valid Schema' : 'Schema Errors'}"></span>
        </div>
      `;
    }

    function toggleFolderNode(rowEl) {
      const folder = rowEl.closest('.tree-folder');
      if (!folder) return;
      const isOpen = folder.classList.toggle('open');
      const chevron = folder.querySelector('.tree-chevron');
      const icon = folder.querySelector('.tree-folder-icon');
      const children = folder.querySelector('.tree-children');
      if (chevron) chevron.innerText = isOpen ? '▼' : '▶';
      if (icon) {
        if (icon.innerText === '📁') icon.innerText = '📂';
        else if (icon.innerText === '📂') icon.innerText = '📁';
      }
      if (children) children.style.display = isOpen ? 'block' : 'none';
    }

    function expandAllCatalogTree() {
      document.querySelectorAll('#catalogTreeContainer .tree-folder').forEach(folder => {
        folder.classList.add('open');
        const chevron = folder.querySelector('.tree-chevron');
        const icon = folder.querySelector('.tree-folder-icon');
        const children = folder.querySelector('.tree-children');
        if (chevron) chevron.innerText = '▼';
        if (icon && icon.innerText === '📁') icon.innerText = '📂';
        if (children) children.style.display = 'block';
      });
    }

    function collapseAllCatalogTree() {
      document.querySelectorAll('#catalogTreeContainer .tree-folder').forEach(folder => {
        folder.classList.remove('open');
        const chevron = folder.querySelector('.tree-chevron');
        const icon = folder.querySelector('.tree-folder-icon');
        const children = folder.querySelector('.tree-children');
        if (chevron) chevron.innerText = '▶';
        if (icon && icon.innerText === '📂') icon.innerText = '📁';
        if (children) children.style.display = 'none';
      });
    }

    function selectCatalogSpec(specId, scrollIntoView = true) {
      if (!PORTAL_DATA) return;
      const spec = PORTAL_DATA.specs.find(s => s.id === specId);
      if (!spec) return;

      SELECTED_SPEC_ID = specId;

      document.querySelectorAll('.tree-leaf').forEach(el => el.classList.remove('selected'));
      const safeId = specId.replace(/[^a-zA-Z0-9_]/g, '_');
      const targetLeaf = document.getElementById(`treeLeaf_${safeId}`);
      if (targetLeaf) {
        targetLeaf.classList.add('selected');
        let parent = targetLeaf.closest('.tree-folder');
        while (parent) {
          parent.classList.add('open');
          const chevron = parent.querySelector('.tree-chevron');
          const icon = parent.querySelector('.tree-folder-icon');
          const children = parent.querySelector('.tree-children');
          if (chevron) chevron.innerText = '▼';
          if (icon && icon.innerText === '📁') icon.innerText = '📂';
          if (children) children.style.display = 'block';
          parent = parent.parentElement?.closest('.tree-folder');
        }
        if (scrollIntoView) {
          targetLeaf.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
      }

      renderSpecDetailInspector(spec);
    }

    function renderSpecDetailInspector(spec) {
      const pane = document.getElementById('catalogDetailPane');
      if (!pane) return;

      const fm = spec.frontmatter || {};
      const yamlStr = jsToYamlString(fm);
      const safeTitle = escapeHtml(spec.title);
      const categoryKind = fm.category || fm.intent?.kind || fm.classification || spec.type;
      const ownerDomain = fm.design_system || fm.domain || fm.owning_component || 'Core Engine';
      const testRef = fm.test_bindings?.unit_test || (fm.technical?.invariants ? `${fm.technical.invariants.length} Invariants` : '100% Traceable');

      const relationsHtml = extractSpecRelationsHtml(spec);

      pane.innerHTML = `
        <div class="inspector-header">
          <div class="inspector-top-row">
            <div class="inspector-ids">
              <span class="code-pill inspector-id">${spec.id}</span>
              <span class="badge badge-primary">${spec.type}</span>
              <span class="badge ${spec.is_valid ? 'badge-success' : 'badge-danger'}">
                ● ${spec.is_valid ? 'VALID SCHEMA' : 'INVALID'}
              </span>
              ${fm.status ? `<span class="badge badge-accent">${fm.status}</span>` : ''}
            </div>
            <div class="inspector-actions">
              <button class="btn-ctrl" onclick="copyToClipboard('${spec.path}', 'Path copied!')" title="Copy relative path">📋 Copy Path</button>
              <button class="btn-ctrl" onclick="copyToClipboard(jsToYamlString(PORTAL_DATA.specs.find(s=>s.id==='${spec.id}')?.frontmatter || {}), 'YAML copied!')" title="Copy YAML Frontmatter">📄 Copy YAML</button>
              <button class="btn-ctrl" onclick="showSpecDetails(PORTAL_DATA.specs.find(s=>s.id==='${spec.id}'))" title="Open full modal">🔍 Full Modal</button>
            </div>
          </div>
          <h2 class="inspector-title">${safeTitle}</h2>
          <div class="inspector-path-row">
            <span class="inspector-path-icon">📄</span>
            <span class="inspector-path-text">${spec.path}</span>
          </div>
        </div>

        <div class="inspector-meta-grid">
          <div class="meta-chip">
            <span class="meta-chip-label">Schema Reference</span>
            <span class="meta-chip-val" title="${spec.schema || 'schemas/v1/'}">${spec.schema || 'schemas/v1/'}</span>
          </div>
          <div class="meta-chip">
            <span class="meta-chip-label">Category / Classification</span>
            <span class="meta-chip-val">${categoryKind}</span>
          </div>
          <div class="meta-chip">
            <span class="meta-chip-label">Domain / Component Context</span>
            <span class="meta-chip-val">${ownerDomain}</span>
          </div>
          <div class="meta-chip">
            <span class="meta-chip-label">Test & Invariant Bindings</span>
            <span class="meta-chip-val">${testRef}</span>
          </div>
        </div>

        <div class="inspector-tabs">
          <div class="inspector-tab-nav">
            <button class="inspector-tab-btn ${ACTIVE_INSPECTOR_TAB === 'yaml' ? 'active' : ''}" onclick="switchInspectorTab('yaml')">YAML Frontmatter</button>
            <button class="inspector-tab-btn ${ACTIVE_INSPECTOR_TAB === 'body' ? 'active' : ''}" onclick="switchInspectorTab('body')">Document Source</button>
            <button class="inspector-tab-btn ${ACTIVE_INSPECTOR_TAB === 'relations' ? 'active' : ''}" onclick="switchInspectorTab('relations')">Traceability & Cross-Refs</button>
          </div>

          <div class="inspector-tab-pane ${ACTIVE_INSPECTOR_TAB === 'yaml' ? 'active' : ''}" id="inspectorTabYaml">
            <pre class="inspector-code-box"><code>${escapeHtml(yamlStr)}</code></pre>
          </div>

          <div class="inspector-tab-pane ${ACTIVE_INSPECTOR_TAB === 'body' ? 'active' : ''}" id="inspectorTabBody">
            <div class="inspector-body-content">${formatAsciiDocPreview(spec.body)}</div>
          </div>

          <div class="inspector-tab-pane ${ACTIVE_INSPECTOR_TAB === 'relations' ? 'active' : ''}" id="inspectorTabRelations">
            ${relationsHtml}
          </div>
        </div>
      `;
    }

    function switchInspectorTab(tabId) {
      ACTIVE_INSPECTOR_TAB = tabId;
      document.querySelectorAll('.inspector-tab-btn').forEach(btn => btn.classList.remove('active'));
      document.querySelectorAll('.inspector-tab-pane').forEach(p => p.classList.remove('active'));

      const activeBtn = Array.from(document.querySelectorAll('.inspector-tab-btn')).find(b => b.getAttribute('onclick')?.includes(tabId));
      if (activeBtn) activeBtn.classList.add('active');

      const targetPane = document.getElementById(`inspectorTab${tabId.charAt(0).toUpperCase() + tabId.slice(1)}`);
      if (targetPane) targetPane.classList.add('active');
    }

    function extractSpecRelationsHtml(spec) {
      if (!PORTAL_DATA) return '<p style="color: var(--text-dim);">No relations data.</p>';
      const allSpecs = PORTAL_DATA.specs;
      const strContent = JSON.stringify(spec.frontmatter || {}) + ' ' + (spec.body || '');

      const referenced = [];
      allSpecs.forEach(other => {
        if (other.id !== spec.id) {
          if (strContent.includes(other.id) || (other.filename && strContent.includes(other.filename))) {
            referenced.push(other);
          }
        }
      });

      if (referenced.length === 0) {
        return `
          <div style="padding: 1.5rem; text-align: center; color: var(--text-dim); background: var(--bg-card); border-radius: 0.45rem;">
            <div>🔗 No direct upstream or downstream document links declared in frontmatter.</div>
            <div style="font-size: 0.78rem; margin-top: 0.35rem;">Living specifications bridge domains through contracts, capabilities, and system policy invariants.</div>
          </div>
        `;
      }

      return `
        <div style="display: flex; flex-direction: column; gap: 0.75rem;">
          <p style="font-size: 0.82rem; color: var(--text-muted);">
            Detected <b>${referenced.length}</b> cross-specification references in this document. Click any pill to jump to that specification in the tree:
          </p>
          <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
            ${referenced.map(r => `
              <div class="relation-pill" onclick="selectCatalogSpec('${r.id}')" title="${escapeHtml(r.title)} (${r.type})">
                <span class="code-pill" style="font-size: 0.7rem; color: var(--primary);">${r.id}</span>
                <span style="font-weight: 500;">${escapeHtml(r.title)}</span>
                <span class="badge badge-neutral" style="font-size: 0.65rem;">${r.type.split('/')[1] || r.type}</span>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    function formatAsciiDocPreview(body) {
      if (!body || !body.trim()) {
        return '<p style="color: var(--text-dim); font-style: italic;">No additional AsciiDoc body text in specification.</p>';
      }
      return escapeHtml(body);
    }

    function renderEmptyInspector(query) {
      const pane = document.getElementById('catalogDetailPane');
      if (!pane) return;
      pane.innerHTML = `
        <div style="padding: 3rem 1.5rem; text-align: center; color: var(--text-dim); display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%;">
          <div style="font-size: 2.5rem; margin-bottom: 0.75rem;">📑</div>
          <h3 style="font-size: 1.1rem; font-weight: 700; color: var(--text); margin-bottom: 0.35rem;">No Specifications Found</h3>
          <p style="font-size: 0.85rem; max-width: 360px; line-height: 1.5;">No living specifications match the query "${escapeHtml(query)}".</p>
          <button class="btn-ctrl" onclick="clearCatalogFilter()" style="margin-top: 1rem; padding: 0.4rem 0.9rem;">Reset All Filters</button>
        </div>
      `;
    }

    function renderCatalogTable(specs) {
      const catBody = document.querySelector('#specsCatalogTable tbody');
      if (!catBody) return;
      catBody.innerHTML = '';
      specs.forEach(s => {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.onclick = () => selectCatalogSpec(s.id);
        tr.innerHTML = `
          <td><span class="code-pill">${s.id}</span></td>
          <td><span class="badge badge-primary">${s.type}</span></td>
          <td><b>${escapeHtml(s.title)}</b></td>
          <td><span class="badge ${s.is_valid ? 'badge-success' : 'badge-danger'}">${s.is_valid ? 'VALID' : 'INVALID'}</span></td>
          <td><span class="code-pill" style="font-size: 0.75rem;">${s.path}</span></td>
        `;
        catBody.appendChild(tr);
      });
    }

    function jsToYamlString(obj, indent = 0) {
      if (obj === null || obj === undefined) return '';
      const pad = '  '.repeat(indent);
      if (typeof obj !== 'object') {
        return String(obj);
      }
      if (Array.isArray(obj)) {
        if (obj.length === 0) return '[]';
        return obj.map(item => {
          if (typeof item === 'object' && item !== null) {
            const inner = jsToYamlString(item, indent + 1).trimStart();
            return `${pad}- ${inner}`;
          }
          return `${pad}- ${jsToYamlString(item)}`;
        }).join('\n');
      }
      const lines = [];
      for (const [key, val] of Object.entries(obj)) {
        if (val === null || val === undefined) {
          lines.push(`${pad}${key}: null`);
        } else if (typeof val === 'object') {
          if (Array.isArray(val)) {
            if (val.length === 0) {
              lines.push(`${pad}${key}: []`);
            } else {
              lines.push(`${pad}${key}:\n${jsToYamlString(val, indent + 1)}`);
            }
          } else {
            lines.push(`${pad}${key}:\n${jsToYamlString(val, indent + 1)}`);
          }
        } else if (typeof val === 'string') {
          if (val.includes('\n') || val.includes(':') || val.includes('"')) {
            lines.push(`${pad}${key}: "${val.replace(/"/g, '\\"')}"`);
          } else {
            lines.push(`${pad}${key}: ${val}`);
          }
        } else {
          lines.push(`${pad}${key}: ${val}`);
        }
      }
      return lines.join('\n');
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    function showToast(message) {
      let toast = document.getElementById('portalToast');
      if (!toast) {
        toast = document.createElement('div');
        toast.id = 'portalToast';
        toast.className = 'toast-popup';
        document.body.appendChild(toast);
      }
      toast.innerHTML = `<span>✓</span> <span>${message}</span>`;
      toast.classList.add('show');
      setTimeout(() => {
        toast.classList.remove('show');
      }, 2000);
    }

    function copyToClipboard(text, successMsg = 'Copied!') {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(() => {
          showToast(successMsg);
        }).catch(() => {
          fallbackCopyText(text, successMsg);
        });
      } else {
        fallbackCopyText(text, successMsg);
      }
    }

    function fallbackCopyText(text, successMsg) {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        showToast(successMsg);
      } catch (e) {}
      document.body.removeChild(ta);
    }

    const diagramScales = {};

    function zoomDiagram(id, factor) {
      diagramScales[id] = (diagramScales[id] || 1) * factor;
      diagramScales[id] = Math.max(0.6, Math.min(2.5, diagramScales[id]));
      applyDiagramZoom(id);
    }

    function resetZoomDiagram(id) {
      diagramScales[id] = 1;
      applyDiagramZoom(id);
    }

    function applyDiagramZoom(id) {
      const el = document.getElementById(id);
      if (!el) return;
      const svg = el.querySelector('svg');
      if (svg) {
        svg.style.transform = `scale(${diagramScales[id] || 1})`;
      }
    }

    async function renderMermaidDiagram(elementId, code) {
      const el = document.getElementById(elementId);
      if (!el || !code) return;
      try {
        const uid = 'svg_' + elementId.replace(/[^a-zA-Z0-9_]/g, '_') + '_' + Math.random().toString(36).substring(2, 9);
        const { svg } = await mermaid.render(uid, code);
        // Remove restrictive inline max-width so the SVG can expand up to container width
        const cleanSvg = svg.replace(/max-width:\s*[^;"]+;?/gi, 'max-width: 100%;');
        el.innerHTML = cleanSvg;
        applyDiagramZoom(elementId);
      } catch (err) {
        console.warn("Mermaid render error on", elementId, err);
        try {
          el.removeAttribute('data-processed');
          el.textContent = code;
          await mermaid.run({ nodes: [el] });
          applyDiagramZoom(elementId);
        } catch (err2) {
          console.error("Mermaid fallback error on", elementId, err2);
        }
      }
    }

    function switchTab(tabId) {
      CURRENT_TAB = tabId;
      document.querySelectorAll('.nav-link').forEach(btn => btn.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));

      const activeBtn = Array.from(document.querySelectorAll('.nav-link')).find(b => b.getAttribute('onclick')?.includes(tabId));
      if (activeBtn) activeBtn.classList.add('active');

      const target = document.getElementById(`tab-${tabId}`);
      if (target) target.classList.add('active');

      // Update breadcrumb
      const titles = {
        home: 'Home',
        business: '1. Business & Governance',
        design: '2. Design & Experience',
        architecture: '3. Technical Architecture',
        deltas: 'SpecDeltas (Kanban)',
        catalog: 'Living Specs Catalog'
      };
      document.getElementById('breadcrumbActive').innerText = titles[tabId] || tabId;

      // Close mobile drawer if open
      document.getElementById('sidebar').classList.remove('open');
      window.scrollTo({ top: 0, behavior: 'smooth' });

      // Re-render diagrams when switching to diagram-bearing views
      if (tabId === 'architecture') {
        setTimeout(() => {
          renderMermaidDiagram('c4ContextDiagram', PORTAL_DATA.diagrams.c4_context);
          renderMermaidDiagram('c4ContainersDiagram', PORTAL_DATA.diagrams.c4_containers);
        }, 30);
      } else if (tabId === 'design') {
        setTimeout(() => {
          (PORTAL_DATA.screens || []).forEach(s => {
            renderMermaidDiagram(`screen_mermaid_${s.id.replace(/-/g, '_')}`, s.mermaid);
          });
        }, 30);
      }
    }

    function toggleTheme() {
      const html = document.documentElement;
      const isDark = html.classList.toggle('dark');
      if (isDark) {
        html.classList.remove('light');
        document.getElementById('themeLabel').innerText = 'Dark Theme';
        document.getElementById('themeIcon').innerText = '🌙';
      } else {
        html.classList.add('light');
        document.getElementById('themeLabel').innerText = 'Light Theme';
        document.getElementById('themeIcon').innerText = '☀️';
      }
      mermaid.initialize({
        theme: isDark ? 'dark' : 'default',
        securityLevel: 'loose',
        fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
        fontSize: 16,
        flowchart: {
          useMaxWidth: false,
          htmlLabels: true,
          curve: 'basis',
          nodeSpacing: 55,
          rankSpacing: 65,
          padding: 22
        }
      });
      renderMermaidDiagram('c4ContextDiagram', PORTAL_DATA.diagrams.c4_context);
      renderMermaidDiagram('c4ContainersDiagram', PORTAL_DATA.diagrams.c4_containers);
      (PORTAL_DATA.screens || []).forEach(s => {
        renderMermaidDiagram(`screen_mermaid_${s.id.replace(/-/g, '_')}`, s.mermaid);
      });
    }

    function initSidebarState() {
      try {
        const isCollapsed = localStorage.getItem('eidos_sidebar_collapsed') === 'true';
        const sidebar = document.getElementById('sidebar');
        const btn = document.getElementById('sidebarCollapseBtn');
        if (isCollapsed) {
          sidebar.classList.add('collapsed');
          if (btn) {
            btn.setAttribute('title', 'Expand sidebar (Ctrl+B)');
            btn.setAttribute('aria-label', 'Expand sidebar');
          }
        } else {
          sidebar.classList.remove('collapsed');
          if (btn) {
            btn.setAttribute('title', 'Collapse sidebar (Ctrl+B)');
            btn.setAttribute('aria-label', 'Collapse sidebar');
          }
        }
      } catch (e) {
        console.warn("localStorage unavailable for sidebar state", e);
      }
    }

    function toggleSidebarCollapse() {
      const sidebar = document.getElementById('sidebar');
      const btn = document.getElementById('sidebarCollapseBtn');
      sidebar.classList.toggle('collapsed');
      const isCollapsed = sidebar.classList.contains('collapsed');
      try {
        localStorage.setItem('eidos_sidebar_collapsed', isCollapsed ? 'true' : 'false');
      } catch (e) {}
      if (btn) {
        btn.setAttribute('title', isCollapsed ? 'Expand sidebar (Ctrl+B)' : 'Collapse sidebar (Ctrl+B)');
        btn.setAttribute('aria-label', isCollapsed ? 'Expand sidebar' : 'Collapse sidebar');
      }
    }

    function toggleMobileSidebar() {
      document.getElementById('sidebar').classList.toggle('open');
    }

    function handleSearch(query) {
      if (!PORTAL_DATA) return;
      if (CURRENT_TAB !== 'catalog') {
        switchTab('catalog');
      }
      document.getElementById('globalSearch').value = query;
      const catSearch = document.getElementById('catalogSearchInput');
      if (catSearch) catSearch.value = query;
      filterCatalog();
    }

    function setupKeyboardShortcuts() {
      window.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
          e.preventDefault();
          toggleSidebarCollapse();
          return;
        }
        if (e.key === '/' && document.activeElement !== document.getElementById('globalSearch')) {
          e.preventDefault();
          document.getElementById('globalSearch').focus();
        }
        if (e.key === 'Escape') {
          closeModal();
        }
      });
    }

    function showDeltaDetails(delta) {
      document.getElementById('modalTitle').innerText = delta.title;
      document.getElementById('modalDocId').innerText = delta.id;
      const content = document.getElementById('modalContent');
      const mutList = (delta.mutations || []).map(m => `
        <li style="margin-bottom: 0.5rem;">
          <b>${m.action}</b> on <span class="code-pill">${m.target_component || m.target_capability || m.target_domain || m.target_screen || 'target'}</span>
          <div style="font-size: 0.8rem; color: var(--text-muted);">${m.details || ''}</div>
        </li>
      `).join('');

      content.innerHTML = `
        <div style="display: flex; gap: 0.5rem;">
          <span class="badge badge-success">${delta.status}</span>
          <span class="badge badge-primary">${delta.kind}</span>
        </div>
        <div>
          <h4 style="font-size: 0.88rem; font-weight: 700; margin-bottom: 0.25rem;">Motivation & Intent</h4>
          <p style="font-size: 0.84rem; color: var(--text-muted); line-height: 1.5;">${delta.motivation || 'Standard architectural evolution.'}</p>
        </div>
        <div>
          <h4 style="font-size: 0.88rem; font-weight: 700; margin-bottom: 0.35rem;">Declared Mutations (${delta.mutations?.length || 0})</h4>
          <ul style="padding-left: 1.25rem; font-size: 0.84rem;">${mutList}</ul>
        </div>
      `;
      document.getElementById('detailModal').classList.add('open');
    }

    function showSpecDetails(spec) {
      document.getElementById('modalTitle').innerText = spec.title;
      document.getElementById('modalDocId').innerText = spec.id;
      const content = document.getElementById('modalContent');
      const fmJson = JSON.stringify(spec.frontmatter, null, 2);
      content.innerHTML = `
        <div style="display: flex; gap: 0.5rem;">
          <span class="badge badge-primary">${spec.type}</span>
          <span class="badge ${spec.is_valid ? 'badge-success' : 'badge-danger'}">${spec.is_valid ? 'VALID' : 'INVALID'}</span>
          <span class="code-pill">${spec.path}</span>
        </div>
        <div>
          <h4 style="font-size: 0.88rem; font-weight: 700; margin-bottom: 0.35rem;">YAML Frontmatter</h4>
          <pre style="background: var(--code-bg); border: 1px solid var(--border); border-radius: 0.4rem; padding: 0.75rem; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; overflow-x: auto; color: var(--text);">${fmJson}</pre>
        </div>
      `;
      document.getElementById('detailModal').classList.add('open');
    }

    function closeModal(e) {
      document.getElementById('detailModal').classList.remove('open');
    }

    window.addEventListener('DOMContentLoaded', initPortal);
  </script>
</body>
</html>
"""
