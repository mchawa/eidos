"""HTML, CSS, and client-side JavaScript templates for the Eidos Living Web Portal."""

PORTAL_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Eidos Living Web Portal</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    :root {
      --bg: #090d16;
      --bg-card: #111827;
      --bg-card-hover: #1f2937;
      --border: #1f2937;
      --border-accent: #374151;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --accent: #8b5cf6;
      --code-bg: #030712;
    }
    html.light {
      --bg: #f8fafc;
      --bg-card: #ffffff;
      --bg-card-hover: #f1f5f9;
      --border: #e2e8f0;
      --border-accent: #cbd5e1;
      --text: #0f172a;
      --text-muted: #64748b;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --success: #059669;
      --warning: #d97706;
      --danger: #dc2626;
      --accent: #7c3aed;
      --code-bg: #f1f5f9;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: var(--bg-card);
      border-bottom: 1px solid var(--border);
      padding: 0.75rem 1.5rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-weight: 700;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
    }
    .brand-badge {
      font-size: 0.7rem;
      background: rgba(59, 130, 246, 0.15);
      color: var(--primary);
      padding: 0.15rem 0.5rem;
      border-radius: 9999px;
      font-weight: 600;
      border: 1px solid rgba(59, 130, 246, 0.3);
    }
    .nav-tabs {
      display: flex;
      gap: 0.25rem;
      background: var(--bg);
      padding: 0.25rem;
      border-radius: 0.5rem;
      border: 1px solid var(--border);
    }
    .nav-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 0.4rem 0.85rem;
      border-radius: 0.35rem;
      cursor: pointer;
      font-size: 0.85rem;
      font-weight: 500;
      transition: all 0.15s ease;
    }
    .nav-btn:hover { color: var(--text); background: var(--bg-card-hover); }
    .nav-btn.active {
      color: #fff;
      background: var(--primary);
      font-weight: 600;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .search-box {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.375rem;
      padding: 0.35rem 0.75rem;
      color: var(--text);
      font-size: 0.85rem;
      width: 200px;
    }
    .search-box:focus { outline: none; border-color: var(--primary); }
    .btn-icon {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.375rem;
      color: var(--text);
      padding: 0.35rem 0.65rem;
      cursor: pointer;
      font-size: 0.85rem;
    }
    main {
      flex: 1;
      padding: 1.5rem;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
    }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .hero-stat-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .stat-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1rem 1.25rem;
    }
    .stat-val { font-size: 1.75rem; font-weight: 700; color: var(--text); }
    .stat-label { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; letter-spacing: 0.05em; }
    .diagram-panel {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1.25rem;
      margin-bottom: 1.5rem;
      overflow-x: auto;
    }
    .diagram-panel h3 { font-size: 1rem; margin-bottom: 0.75rem; font-weight: 600; }
    .kanban-board {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 1rem;
    }
    .kanban-col {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      min-height: 500px;
    }
    .kanban-col-header {
      font-size: 0.85rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.5rem;
    }
    .delta-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 0.375rem;
      padding: 0.85rem;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .delta-card:hover {
      border-color: var(--primary);
      transform: translateY(-1px);
    }
    .delta-card-id { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; font-weight: 600; color: var(--primary); }
    .delta-card-title { font-size: 0.85rem; font-weight: 600; margin: 0.25rem 0; }
    .badge {
      display: inline-block;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 0.1rem 0.4rem;
      border-radius: 0.25rem;
      margin-top: 0.25rem;
    }
    .badge-feature { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
    .badge-refactor { background: rgba(139, 92, 246, 0.15); color: #c084fc; }
    .badge-bugfix { background: rgba(239, 68, 68, 0.15); color: #f87171; }
    .table-container {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      overflow-x: auto;
    }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.85rem; }
    th {
      background: var(--bg-card-hover);
      padding: 0.75rem 1rem;
      font-weight: 600;
      color: var(--text-muted);
      border-bottom: 1px solid var(--border);
    }
    td { padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); }
    tr:hover td { background: var(--bg-card-hover); }
    .code-pill {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      background: var(--code-bg);
      padding: 0.15rem 0.4rem;
      border-radius: 0.25rem;
      border: 1px solid var(--border);
    }
    /* Modal drawer */
    .modal-backdrop {
      position: fixed;
      top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.6);
      backdrop-filter: blur(2px);
      z-index: 100;
      display: none;
      align-items: center;
      justify-content: center;
    }
    .modal-backdrop.open { display: flex; }
    .modal-box {
      background: var(--bg-card);
      border: 1px solid var(--border-accent);
      border-radius: 0.5rem;
      width: 90%;
      max-width: 800px;
      max-height: 85vh;
      overflow-y: auto;
      padding: 1.5rem;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
    }
    .modal-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem; }
    .color-swatch-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 0.75rem; }
    .swatch {
      border: 1px solid var(--border);
      border-radius: 0.375rem;
      overflow: hidden;
      font-size: 0.75rem;
    }
    .swatch-color { height: 60px; width: 100%; }
    .swatch-info { padding: 0.4rem; background: var(--bg); }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span>⚡ Eidos</span>
      <span class="brand-badge" id="headerSystemId">SYS-EIDOS-001</span>
      <span class="badge" id="headerPassBadge" style="background: rgba(16,185,129,0.15); color: #34d399;">PASS</span>
    </div>
    <div class="nav-tabs">
      <button class="nav-btn active" onclick="switchTab('architecture')">Architecture (C4)</button>
      <button class="nav-btn" onclick="switchTab('product')">Domains & Caps</button>
      <button class="nav-btn" onclick="switchTab('deltas')">SpecDeltas (Kanban)</button>
      <button class="nav-btn" onclick="switchTab('invariants')">Invariants Matrix</button>
      <button class="nav-btn" onclick="switchTab('design')">Design Systems</button>
      <button class="nav-btn" onclick="switchTab('catalog')">Specs Catalog</button>
    </div>
    <div class="header-actions">
      <input type="text" class="search-box" placeholder="Search specifications..." oninput="handleSearch(this.value)">
      <button class="btn-icon" onclick="toggleTheme()" title="Toggle Dark/Light Mode">🌓</button>
    </div>
  </header>

  <main>
    <!-- OVERVIEW STATS -->
    <div class="hero-stat-grid">
      <div class="stat-card">
        <div class="stat-label">Total Specs</div>
        <div class="stat-val" id="statTotalSpecs">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">C4 Containers</div>
        <div class="stat-val" id="statContainers">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Domains</div>
        <div class="stat-val" id="statDomains">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Capabilities</div>
        <div class="stat-val" id="statCapabilities">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">SpecDeltas</div>
        <div class="stat-val" id="statDeltas">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Invariants</div>
        <div class="stat-val" id="statInvariants">0</div>
      </div>
    </div>

    <!-- TAB 1: ARCHITECTURE (C4 MODEL) -->
    <section id="tab-architecture" class="tab-content active">
      <div class="diagram-panel">
        <h3>C4 Level 1: System Context Topology</h3>
        <div class="mermaid" id="c4ContextDiagram">graph TD; Loading...</div>
      </div>

      <div class="diagram-panel">
        <h3>C4 Level 2: Container Topology</h3>
        <div class="mermaid" id="c4ContainersDiagram">graph TD; Loading...</div>
      </div>

      <div class="diagram-panel">
        <h3>C4 Level 2/3: Containers & Internal Modules</h3>
        <div class="table-container">
          <table id="componentsTable">
            <thead>
              <tr>
                <th>Container ID</th>
                <th>Name</th>
                <th>Type</th>
                <th>Runtime</th>
                <th>Package Path</th>
                <th>Internal C4 Modules</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>

      <div class="diagram-panel">
        <h3>Interface Contracts (CTR-*)</h3>
        <div class="table-container">
          <table id="contractsTable">
            <thead>
              <tr>
                <th>Contract ID</th>
                <th>Title</th>
                <th>Protocol</th>
                <th>Owning Component</th>
                <th>Specification File</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- TAB 2: PRODUCT DOMAINS & CAPABILITIES -->
    <section id="tab-product" class="tab-content">
      <div class="diagram-panel">
        <h3>Product Domains (DOM-*)</h3>
        <div class="table-container">
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

      <div class="diagram-panel">
        <h3>Product Capabilities (CAP-*)</h3>
        <div class="table-container">
          <table id="capabilitiesTable">
            <thead>
              <tr>
                <th>Capability ID</th>
                <th>Title</th>
                <th>Domain</th>
                <th>Status</th>
                <th>Invariants</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- TAB 3: SPECDELTAS KANBAN -->
    <section id="tab-deltas" class="tab-content">
      <div class="kanban-board">
        <div class="kanban-col" id="col-proposed">
          <div class="kanban-col-header"><span>Proposed</span><span class="count" id="count-proposed">0</span></div>
          <div class="cards" id="cards-proposed"></div>
        </div>
        <div class="kanban-col" id="col-in_review">
          <div class="kanban-col-header"><span>In Review</span><span class="count" id="count-in_review">0</span></div>
          <div class="cards" id="cards-in_review"></div>
        </div>
        <div class="kanban-col" id="col-approved">
          <div class="kanban-col-header"><span>Approved</span><span class="count" id="count-approved">0</span></div>
          <div class="cards" id="cards-approved"></div>
        </div>
        <div class="kanban-col" id="col-applied">
          <div class="kanban-col-header"><span>Applied</span><span class="count" id="count-applied">0</span></div>
          <div class="cards" id="cards-applied"></div>
        </div>
      </div>
    </section>

    <!-- TAB 4: INVARIANTS MATRIX -->
    <section id="tab-invariants" class="tab-content">
      <div class="diagram-panel">
        <h3>Bidirectional Invariant-Test Traceability Matrix</h3>
        <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 1rem;">
          Every policy (<span class="code-pill">POL-*</span>) and capability invariant (<span class="code-pill">INV-*</span>) is bound to deterministic automated tests.
        </p>
        <div class="table-container">
          <table id="invariantsTable">
            <thead>
              <tr>
                <th>Invariant ID</th>
                <th>Category</th>
                <th>Source Definition</th>
                <th>Rule Statement</th>
                <th>Automated Test Binding</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- TAB 5: DESIGN SYSTEMS (W3C TOKENS & SCREENS) -->
    <section id="tab-design" class="tab-content">
      <div class="diagram-panel">
        <h3>W3C Design Tokens Studio (DTCG)</h3>
        <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 1rem;">
          Color palettes and design token semantics managed under <span class="code-pill">eidos/design/systems/</span>.
        </p>
        <div class="color-swatch-grid" id="designTokenSwatches">
          <!-- Fallback core swatch preview -->
          <div class="swatch"><div class="swatch-color" style="background: #3b82f6;"></div><div class="swatch-info"><b>Primary</b><br>#3b82f6</div></div>
          <div class="swatch"><div class="swatch-color" style="background: #10b981;"></div><div class="swatch-info"><b>Success</b><br>#10b981</div></div>
          <div class="swatch"><div class="swatch-color" style="background: #f59e0b;"></div><div class="swatch-info"><b>Warning</b><br>#f59e0b</div></div>
          <div class="swatch"><div class="swatch-color" style="background: #ef4444;"></div><div class="swatch-info"><b>Danger</b><br>#ef4444</div></div>
          <div class="swatch"><div class="swatch-color" style="background: #8b5cf6;"></div><div class="swatch-info"><b>Accent</b><br>#8b5cf6</div></div>
        </div>
      </div>

      <div class="diagram-panel">
        <h3>Screen State Machines</h3>
        <div id="screensList">
          <p style="font-size: 0.85rem; color: var(--text-muted);">No screen state machines scaffolded yet. Use <span class="code-pill">eidos spec create screen SCR-ID</span> to add interactive screen states.</p>
        </div>
      </div>
    </section>

    <!-- TAB 6: SPECIFICATIONS CATALOG -->
    <section id="tab-catalog" class="tab-content">
      <div class="diagram-panel">
        <h3>Living Specifications Catalog</h3>
        <div class="table-container">
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
    </section>
  </main>

  <!-- MODAL DRAWER FOR DELTA / SPEC DETAILS -->
  <div class="modal-backdrop" id="detailModal" onclick="closeModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()">
      <div class="modal-header">
        <div>
          <h2 id="modalTitle" style="font-size: 1.25rem;">SpecDelta Details</h2>
          <span class="code-pill" id="modalDocId">DLT-ID</span>
        </div>
        <button class="btn-icon" onclick="closeModal()">✕</button>
      </div>
      <div id="modalContent"></div>
    </div>
  </div>

  <script>
    // Injected payload data or loaded via API
    let PORTAL_DATA = window.__EIDOS_DATA__ || null;

    mermaid.initialize({
      startOnLoad: false,
      theme: document.documentElement.classList.contains('dark') ? 'dark' : 'default',
      securityLevel: 'loose'
    });

    async function initPortal() {
      if (!PORTAL_DATA) {
        try {
          const res = await fetch('/api/data');
          PORTAL_DATA = await res.json();
        } catch (e) {
          console.error("Failed to load /api/data:", e);
          return;
        }
      }
      renderPortal();
    }

    function renderPortal() {
      if (!PORTAL_DATA) return;
      const ov = PORTAL_DATA.overview;
      document.getElementById('headerSystemId').innerText = ov.system_id || "SYS-EIDOS-001";
      document.getElementById('statTotalSpecs').innerText = ov.total_specs;
      document.getElementById('statContainers').innerText = ov.components_count;
      document.getElementById('statDomains').innerText = ov.domains_count;
      document.getElementById('statCapabilities').innerText = ov.capabilities_count;
      document.getElementById('statDeltas').innerText = ov.deltas_count;
      document.getElementById('statInvariants').innerText = ov.invariants_count;

      // Render C4 diagrams
      renderMermaidDiagram('c4ContextDiagram', PORTAL_DATA.diagrams.c4_context);
      renderMermaidDiagram('c4ContainersDiagram', PORTAL_DATA.diagrams.c4_containers);

      // Render Components table
      const compBody = document.querySelector('#componentsTable tbody');
      compBody.innerHTML = '';
      PORTAL_DATA.components.forEach(c => {
        const tr = document.createElement('tr');
        const modules = (c.internal_components || []).map(m => m.name).join(', ') || 'N/A';
        tr.innerHTML = `
          <td><span class="code-pill">${c.id}</span></td>
          <td><b>${c.name}</b></td>
          <td><span class="badge badge-feature">${c.container_type}</span></td>
          <td>${c.runtime}</td>
          <td><span class="code-pill">${c.package_path}</span></td>
          <td>${modules}</td>
        `;
        compBody.appendChild(tr);
      });

      // Render Contracts table
      const ctrBody = document.querySelector('#contractsTable tbody');
      ctrBody.innerHTML = '';
      PORTAL_DATA.contracts.forEach(ct => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="code-pill">${ct.id}</span></td>
          <td><b>${ct.title}</b></td>
          <td><span class="badge badge-refactor">${ct.protocol}</span></td>
          <td><span class="code-pill">${ct.owning_component}</span></td>
          <td><span class="code-pill">${ct.specification_file}</span></td>
        `;
        ctrBody.appendChild(tr);
      });

      // Render Domains table
      const domBody = document.querySelector('#domainsTable tbody');
      domBody.innerHTML = '';
      PORTAL_DATA.domains.forEach(d => {
        const tr = document.createElement('tr');
        const resps = (d.boundaries?.responsibilities || []).map(r => `• ${r}`).join('<br>');
        tr.innerHTML = `
          <td><span class="code-pill">${d.id}</span></td>
          <td><b>${d.name}</b></td>
          <td><span class="badge badge-feature">${d.classification || 'core'}</span></td>
          <td style="font-size: 0.8rem;">${resps}</td>
        `;
        domBody.appendChild(tr);
      });

      // Render Capabilities table
      const capBody = document.querySelector('#capabilitiesTable tbody');
      capBody.innerHTML = '';
      PORTAL_DATA.capabilities.forEach(cap => {
        const tr = document.createElement('tr');
        const invs = (cap.technical?.invariants || []).map(i => i.id).join(', ') || 'None';
        tr.innerHTML = `
          <td><span class="code-pill">${cap.id}</span></td>
          <td><b>${cap.title}</b></td>
          <td><span class="code-pill">${cap.domain || 'N/A'}</span></td>
          <td><span class="badge badge-feature">${cap.status}</span></td>
          <td><span class="code-pill">${invs}</span></td>
        `;
        capBody.appendChild(tr);
      });

      // Render Deltas Kanban
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
          const kindBadge = d.kind === 'bugfix' ? 'badge-bugfix' : (d.kind === 'refactor' ? 'badge-refactor' : 'badge-feature');
          card.innerHTML = `
            <div class="delta-card-id">${d.id}</div>
            <div class="delta-card-title">${d.title}</div>
            <span class="badge ${kindBadge}">${d.kind}</span>
            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.4rem;">${d.mutations?.length || 0} mutations</div>
          `;
          container.appendChild(card);
        });
      });

      // Render Invariants table
      const invBody = document.querySelector('#invariantsTable tbody');
      invBody.innerHTML = '';
      PORTAL_DATA.invariants.forEach(inv => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="code-pill">${inv.id}</span></td>
          <td><span class="badge badge-feature">${inv.category}</span></td>
          <td><span class="code-pill">${inv.source_type}</span></td>
          <td>${inv.rule}</td>
          <td><span class="code-pill">${inv.test_binding}</span></td>
        `;
        invBody.appendChild(tr);
      });

      // Render Catalog table
      renderCatalog(PORTAL_DATA.specs);
    }

    function renderCatalog(specs) {
      const catBody = document.querySelector('#specsCatalogTable tbody');
      catBody.innerHTML = '';
      specs.forEach(s => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="code-pill">${s.id}</span></td>
          <td><span class="badge badge-feature">${s.type}</span></td>
          <td><b>${s.title}</b></td>
          <td><span class="badge ${s.is_valid ? 'badge-feature' : 'badge-bugfix'}">${s.is_valid ? 'VALID' : 'INVALID'}</span></td>
          <td><span class="code-pill">${s.path}</span></td>
        `;
        catBody.appendChild(tr);
      });
    }

    function renderMermaidDiagram(elementId, code) {
      const el = document.getElementById(elementId);
      if (!el || !code) return;
      el.removeAttribute('data-processed');
      el.innerHTML = code;
      try {
        mermaid.run({ nodes: [el] });
      } catch (err) {
        console.warn("Mermaid render error:", err);
      }
    }

    function switchTab(tabId) {
      document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
      const activeBtn = Array.from(document.querySelectorAll('.nav-btn')).find(b => b.getAttribute('onclick')?.includes(tabId));
      if (activeBtn) activeBtn.classList.add('active');
      const target = document.getElementById(`tab-${tabId}`);
      if (target) target.classList.add('active');
    }

    function toggleTheme() {
      const html = document.documentElement;
      html.classList.toggle('dark');
      html.classList.toggle('light');
      mermaid.initialize({
        theme: html.classList.contains('dark') ? 'dark' : 'default',
        securityLevel: 'loose'
      });
      renderPortal();
    }

    function handleSearch(query) {
      if (!PORTAL_DATA) return;
      const q = query.toLowerCase().trim();
      if (!q) {
        renderCatalog(PORTAL_DATA.specs);
        return;
      }
      const filtered = PORTAL_DATA.specs.filter(s =>
        s.id.toLowerCase().includes(q) ||
        s.title.toLowerCase().includes(q) ||
        s.type.toLowerCase().includes(q) ||
        s.path.toLowerCase().includes(q)
      );
      renderCatalog(filtered);
    }

    function showDeltaDetails(delta) {
      document.getElementById('modalTitle').innerText = delta.title;
      document.getElementById('modalDocId').innerText = delta.id;
      const content = document.getElementById('modalContent');
      const mutList = (delta.mutations || []).map(m => `
        <li style="margin-bottom: 0.5rem;">
          <b>${m.action}</b> on <span class="code-pill">${m.target_component || m.target_capability || m.target_domain || 'target'}</span>
          <div style="font-size: 0.8rem; color: var(--text-muted);">${m.details || ''}</div>
        </li>
      `).join('');

      content.innerHTML = `
        <div style="margin-bottom: 1rem;">
          <span class="badge badge-feature">${delta.status}</span>
          <span class="badge badge-refactor">${delta.kind}</span>
        </div>
        <div style="margin-bottom: 1rem;">
          <h4 style="font-size: 0.9rem; margin-bottom: 0.25rem;">Motivation / Problem Statement</h4>
          <p style="font-size: 0.85rem; color: var(--text-muted);">${delta.motivation || 'N/A'}</p>
        </div>
        <div style="margin-bottom: 1rem;">
          <h4 style="font-size: 0.9rem; margin-bottom: 0.25rem;">Declared Mutations (${delta.mutations?.length || 0})</h4>
          <ul style="padding-left: 1.25rem; font-size: 0.85rem;">${mutList}</ul>
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
