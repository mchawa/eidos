/**
 * Pure client-side Mermaid v12 SVG connection highlighting and full-page zoom helpers
 * (DEC-PORTAL-C4-FULLPAGE-AND-CONNECTION-FOCUS, INV-PORTAL-C4-FULLPAGE-AND-CONNECTION-HIGHLIGHT).
 *
 * Operates strictly on post-render SVG DOM under Mermaid `securityLevel: "strict"`, matching
 * nodes and edges against known Mermaid diagram node IDs so underscore-containing Eidos IDs
 * (`CNT_EIDOS_CLI`, `mod_CMP_PORTAL_ASSETS`, `peer_CNT_EIDOS_CORE`, `PER_*`, `EXT_*`) are
 * never mis-split.
 */

export type HighlightDirection = "all" | "incoming" | "outgoing";

export interface ParsedMermaidEdge {
  source: string;
  target: string;
  label?: string;
}

export interface ParsedMermaidDiagram {
  edges: ParsedMermaidEdge[];
  nodeIds: Set<string>;
}

export interface C4ConnectionHighlightOptions {
  selectedNodeId: string | null;
  direction: HighlightDirection;
  parsedEdges: ParsedMermaidEdge[];
  knownNodeIds: Set<string>;
}

export interface C4ConnectionHighlightResult {
  selectedNodeId: string | null;
  incomingCount: number;
  outgoingCount: number;
  incomingNodeIds: string[];
  outgoingNodeIds: string[];
  connectedNodeIds: string[];
}

export interface Size2D {
  width: number;
  height: number;
}

const MERMAID_KEYWORD_RE =
  /^(?:graph|flowchart|subgraph|end|direction|classDef|class|style|linkStyle|click|accTitle|accDescr)\b/i;

const NODE_ID_PATTERN = "[A-Za-z_][A-Za-z0-9_-]*";

// Matches directed Mermaid flowchart edges:
//   A --> B, A -->|label| B, A -- "label" --> B, A ==> B, A ==>|label| B, A -.-> B, A -. "label" .-> B, A -.->|label| B
const DIRECTED_EDGE_RE = new RegExp(
  `^(${NODE_ID_PATTERN})(?:\\s*(?:\\[[^\\]]*\\]|\\([^)]*\\)|\\{[^}]*\\}))?\\s*` +
    `(?:` +
    `--\\s*(?:"([^"]*)"|([^>-]+?))\\s*-->|` +
    `==\\s*(?:"([^"]*)"|([^>=]+?))\\s*==>|` +
    `-\\.\\s*(?:"([^"]*)"|([^>.]+?))\\s*\\.->|` +
    `(?:-->|==>|-.->)(?:\\s*\\|([^|]*)\\|)?` +
    `)\\s*` +
    `(${NODE_ID_PATTERN})(?:\\s*(?:\\[[^\\]]*\\]|\\([^)]*\\)|\\{[^}]*\\}))?`
);

const STANDALONE_NODE_RE = new RegExp(`^(${NODE_ID_PATTERN})\\s*(?:\\[|\\(|\\{)`);

/**
 * Parse directed Mermaid flowchart edges (`-->`, `==>`, `-.->`) and declared node IDs from source.
 */
export function parseMermaidEdges(source: string): ParsedMermaidDiagram {
  const edges: ParsedMermaidEdge[] = [];
  const nodeIds = new Set<string>();
  if (!source) return { edges, nodeIds };

  const lines = source.split(/\r?\n/);
  for (const rawLine of lines) {
    const trimmed = rawLine.trim();
    if (!trimmed || trimmed.startsWith("%%")) continue;
    if (MERMAID_KEYWORD_RE.test(trimmed)) continue;

    // Support semicolon-separated statements on a single line
    const statements = trimmed.split(";").map((s) => s.trim()).filter(Boolean);
    for (const stmt of statements) {
      if (MERMAID_KEYWORD_RE.test(stmt)) continue;

      const edgeMatch = DIRECTED_EDGE_RE.exec(stmt);
      if (edgeMatch) {
        const src = edgeMatch[1];
        const dst = edgeMatch[9];
        const rawLabel =
          edgeMatch[2] ??
          edgeMatch[3] ??
          edgeMatch[4] ??
          edgeMatch[5] ??
          edgeMatch[6] ??
          edgeMatch[7] ??
          edgeMatch[8];
        const label = rawLabel ? rawLabel.trim().replace(/^"(.*)"$/, "$1") : undefined;
        nodeIds.add(src);
        nodeIds.add(dst);
        edges.push({ source: src, target: dst, ...(label ? { label } : {}) });
        continue;
      }

      const nodeMatch = STANDALONE_NODE_RE.exec(stmt);
      if (nodeMatch) {
        nodeIds.add(nodeMatch[1]);
      }
    }
  }

  return { edges, nodeIds };
}

/**
 * Resolve the canonical Mermaid diagram node ID from a rendered SVG `g.node` element without
 * breaking on underscores inside Eidos node IDs (e.g. `flowchart-mod_CMP_PORTAL_ASSETS-14`).
 */
export function resolveSvgNodeId(nodeEl: Element, knownNodeIds: Set<string>): string | null {
  const dataId = (nodeEl.getAttribute("data-id") || "").trim();
  if (dataId && (knownNodeIds.size === 0 || knownNodeIds.has(dataId))) {
    return dataId;
  }

  const rawId = (nodeEl.getAttribute("id") || "").trim();
  if (rawId) {
    // Strip Mermaid v11/v12 prefix `flowchart-` and trailing instance suffix `-<digits>`
    const stripped = rawId.replace(/^flowchart-/, "").replace(/-\d+$/, "");
    if (stripped && (knownNodeIds.size === 0 || knownNodeIds.has(stripped))) {
      return stripped;
    }
    if (knownNodeIds.has(rawId)) {
      return rawId;
    }
    // Longest known node ID boundary match fallback
    const candidates = Array.from(knownNodeIds).sort((a, b) => b.length - a.length);
    for (const candidate of candidates) {
      if (
        rawId === candidate ||
        rawId.startsWith(`${candidate}-`) ||
        rawId.includes(`-${candidate}-`) ||
        rawId.endsWith(`-${candidate}`)
      ) {
        return candidate;
      }
    }
    if (stripped) return stripped;
  }

  if (dataId) return dataId;
  return null;
}

/**
 * Resolve `(source, target)` Mermaid node IDs for an SVG edge path without naive `split("_")`.
 * Handles Mermaid v12 `data-id="L_<src>_<dst>_<idx>"` / `id="L_<src>_<dst>_<idx>"`,
 * `LS-<src>`/`LE-<dst>` class pairs, and deterministic parsed-edge-order fallback.
 */
export function matchSvgEdgeEndpoints(
  edgeEl: Element,
  knownNodeIds: Set<string>,
  parsedEdges: ParsedMermaidEdge[],
  edgeIndex: number
): { source: string; target: string } | null {
  // 1. Mermaid LS-<src> / LE-<dst> classes (present in several Mermaid flowchart versions)
  const classList = Array.from(edgeEl.classList || []);
  const lsClass = classList.find((c) => c.startsWith("LS-"));
  const leClass = classList.find((c) => c.startsWith("LE-"));
  if (lsClass && leClass) {
    const src = lsClass.slice(3);
    const dst = leClass.slice(3);
    if (
      src &&
      dst &&
      (knownNodeIds.size === 0 || (knownNodeIds.has(src) && knownNodeIds.has(dst)))
    ) {
      return { source: src, target: dst };
    }
  }

  // 2. Mermaid v12 `data-id` / `id`: `L_<src>_<dst>_<n>` or `L-<src>-<dst>-<n>`
  const rawId = (edgeEl.getAttribute("data-id") || edgeEl.getAttribute("id") || "").trim();
  const bodyMatch = /^L[_-](.+)[_-]\d+$/.exec(rawId);
  if (bodyMatch) {
    const body = bodyMatch[1];
    const sep = rawId.startsWith("L_") ? "_" : "-";
    const sortedKnown = Array.from(knownNodeIds).sort((a, b) => b.length - a.length);
    const matches: Array<{ source: string; target: string }> = [];

    for (const src of sortedKnown) {
      const prefix = `${src}${sep}`;
      if (body.startsWith(prefix)) {
        const dst = body.slice(prefix.length);
        if (knownNodeIds.has(dst)) {
          matches.push({ source: src, target: dst });
        }
      }
    }

    if (matches.length === 1) {
      return matches[0];
    }
    if (matches.length > 1) {
      const declared = matches.find((m) =>
        parsedEdges.some((pe) => pe.source === m.source && pe.target === m.target)
      );
      return declared ?? matches[0];
    }
  }

  // 3. Deterministic parsed-edge-order fallback
  if (edgeIndex >= 0 && edgeIndex < parsedEdges.length) {
    const pe = parsedEdges[edgeIndex];
    return { source: pe.source, target: pe.target };
  }

  return null;
}

const C4_HIGHLIGHT_CLASSES = [
  "c4-node-selected",
  "c4-node-connected",
  "c4-node-incoming",
  "c4-node-outgoing",
  "c4-node-dimmed",
  "c4-edge-incoming",
  "c4-edge-outgoing",
  "c4-edge-dimmed",
  "c4-edge-label-connected",
  "c4-edge-label-dimmed",
  "blast-radius-target",
  "blast-radius-1-hop",
  "blast-radius-2-hop",
  "blast-radius-dimmed",
];

function queryEdgePaths(svgRoot: Element): Element[] {
  const seen = new Set<Element>();
  const ordered: Element[] = [];
  svgRoot
    .querySelectorAll('path[data-edge="true"], .edgePaths path.flowchart-link, path.flowchart-link')
    .forEach((el) => {
      if (!seen.has(el)) {
        seen.add(el);
        ordered.push(el);
      }
    });
  return ordered;
}

function queryEdgeLabels(svgRoot: Element): Element[] {
  return Array.from(svgRoot.querySelectorAll(".edgeLabels .edgeLabel, g.edgeLabel"));
}

/**
 * Restore all prior C4 connection-highlight and Blast Radius classes, attributes, and inline
 * shape styles cleanly so switching modes never corrupts `data-orig-*` snapshots.
 */
export function restoreC4SvgStyles(svgRoot: Element | null): void {
  if (!svgRoot) return;

  const styledNodes = svgRoot.querySelectorAll(
    '.node, [data-c4-entity], [data-c4-highlight], [data-blast-hop], path[data-edge="true"], .edgePaths path.flowchart-link, path.flowchart-link, .edgeLabels .edgeLabel, g.edgeLabel, [data-c4-edge-state]'
  );

  styledNodes.forEach((node) => {
    const el = node as HTMLElement | SVGElement;
    el.classList.remove(...C4_HIGHLIGHT_CLASSES);
    el.removeAttribute("data-c4-highlight");
    el.removeAttribute("data-c4-edge-state");
    el.removeAttribute("data-blast-hop");
    el.style.opacity = "";
    el.style.filter = "";

    const shape = el.querySelector<SVGElement>("rect, polygon, path, circle");
    if (shape) {
      const origStroke = shape.getAttribute("data-orig-stroke");
      const origStrokeW = shape.getAttribute("data-orig-stroke-width");
      const origDash = shape.getAttribute("data-orig-dasharray");
      if (origStroke !== null) {
        shape.style.stroke = origStroke;
        shape.removeAttribute("data-orig-stroke");
      }
      if (origStrokeW !== null) {
        shape.style.strokeWidth = origStrokeW;
        shape.removeAttribute("data-orig-stroke-width");
      }
      if (origDash !== null) {
        shape.style.strokeDasharray = origDash;
        shape.removeAttribute("data-orig-dasharray");
      }
    }
  });
}

/**
 * Apply interactive component-and-connection highlighting across all nodes, edge paths, and
 * `.edgeLabel` elements in a rendered Mermaid v12 SVG.
 */
export function applyC4ConnectionHighlight(
  svgRoot: Element | null,
  options: C4ConnectionHighlightOptions
): C4ConnectionHighlightResult {
  restoreC4SvgStyles(svgRoot);

  const empty: C4ConnectionHighlightResult = {
    selectedNodeId: null,
    incomingCount: 0,
    outgoingCount: 0,
    incomingNodeIds: [],
    outgoingNodeIds: [],
    connectedNodeIds: [],
  };

  const { selectedNodeId, direction, parsedEdges, knownNodeIds } = options;
  if (!svgRoot || !selectedNodeId) {
    return empty;
  }

  // Augment known node IDs with any rendered SVG node IDs so matching is exhaustive
  const effectiveNodeIds = new Set<string>(knownNodeIds);
  const nodeEls = Array.from(svgRoot.querySelectorAll("g.node, .node"));
  const resolvedNodeMap = new Map<Element, string>();
  nodeEls.forEach((nodeEl) => {
    const nid = resolveSvgNodeId(nodeEl, effectiveNodeIds);
    if (nid) {
      effectiveNodeIds.add(nid);
      resolvedNodeMap.set(nodeEl, nid);
    }
  });

  // Collect edge relationships from both parsed source edges and rendered SVG edges
  const incomingNodes = new Set<string>();
  const outgoingNodes = new Set<string>();

  parsedEdges.forEach((edge) => {
    if (edge.target === selectedNodeId && edge.source !== selectedNodeId) {
      incomingNodes.add(edge.source);
    }
    if (edge.source === selectedNodeId && edge.target !== selectedNodeId) {
      outgoingNodes.add(edge.target);
    }
  });

  const edgePaths = queryEdgePaths(svgRoot);
  const edgeEndpoints = edgePaths.map((edgeEl, idx) =>
    matchSvgEdgeEndpoints(edgeEl, effectiveNodeIds, parsedEdges, idx)
  );

  edgeEndpoints.forEach((ep) => {
    if (!ep) return;
    if (ep.target === selectedNodeId && ep.source !== selectedNodeId) {
      incomingNodes.add(ep.source);
    }
    if (ep.source === selectedNodeId && ep.target !== selectedNodeId) {
      outgoingNodes.add(ep.target);
    }
  });

  const includeIncoming = direction === "all" || direction === "incoming";
  const includeOutgoing = direction === "all" || direction === "outgoing";

  const activeIncomingNodes = includeIncoming ? incomingNodes : new Set<string>();
  const activeOutgoingNodes = includeOutgoing ? outgoingNodes : new Set<string>();

  // 1. Style nodes
  nodeEls.forEach((nodeEl) => {
    const nid = resolvedNodeMap.get(nodeEl) ?? resolveSvgNodeId(nodeEl, effectiveNodeIds);
    if (nid === selectedNodeId) {
      nodeEl.setAttribute("data-c4-highlight", "selected");
      nodeEl.classList.add("c4-node-selected");
      return;
    }

    const isIn = Boolean(nid && activeIncomingNodes.has(nid));
    const isOut = Boolean(nid && activeOutgoingNodes.has(nid));

    if (isIn && isOut) {
      nodeEl.setAttribute("data-c4-highlight", "connected");
      nodeEl.classList.add("c4-node-connected");
    } else if (isIn) {
      nodeEl.setAttribute("data-c4-highlight", "incoming");
      nodeEl.classList.add("c4-node-connected", "c4-node-incoming");
    } else if (isOut) {
      nodeEl.setAttribute("data-c4-highlight", "outgoing");
      nodeEl.classList.add("c4-node-connected", "c4-node-outgoing");
    } else {
      nodeEl.setAttribute("data-c4-highlight", "dimmed");
      nodeEl.classList.add("c4-node-dimmed");
    }
  });

  // 2. Style edge paths & record active state by index for edge labels
  const edgeStates: Array<"incoming" | "outgoing" | "dimmed"> = [];
  edgePaths.forEach((edgeEl, idx) => {
    const ep = edgeEndpoints[idx];
    let state: "incoming" | "outgoing" | "dimmed" = "dimmed";
    if (ep) {
      if (includeOutgoing && ep.source === selectedNodeId) {
        state = "outgoing";
      } else if (includeIncoming && ep.target === selectedNodeId) {
        state = "incoming";
      }
    }
    edgeStates.push(state);
    edgeEl.setAttribute("data-c4-edge-state", state);
    if (state === "incoming") {
      edgeEl.classList.add("c4-edge-incoming");
    } else if (state === "outgoing") {
      edgeEl.classList.add("c4-edge-outgoing");
    } else {
      edgeEl.classList.add("c4-edge-dimmed");
    }
  });

  // 3. Style edge labels matching edge order (or matching data-id when present)
  const edgeLabels = queryEdgeLabels(svgRoot);
  const edgeStateByDataId = new Map<string, "incoming" | "outgoing" | "dimmed">();
  edgePaths.forEach((edgeEl, idx) => {
    const did = edgeEl.getAttribute("data-id") || edgeEl.getAttribute("id");
    if (did) edgeStateByDataId.set(did, edgeStates[idx]);
  });

  edgeLabels.forEach((labelEl, idx) => {
    const labelDataId = labelEl.getAttribute("data-id") || "";
    const state =
      (labelDataId && edgeStateByDataId.get(labelDataId)) ||
      edgeStates[idx] ||
      "dimmed";
    labelEl.setAttribute("data-c4-edge-state", state);
    if (state === "dimmed") {
      labelEl.classList.add("c4-edge-label-dimmed");
    } else {
      labelEl.classList.add("c4-edge-label-connected");
    }
  });

  const connectedSet = new Set<string>([...activeIncomingNodes, ...activeOutgoingNodes]);

  return {
    selectedNodeId,
    incomingCount: incomingNodes.size,
    outgoingCount: outgoingNodes.size,
    incomingNodeIds: Array.from(incomingNodes),
    outgoingNodeIds: Array.from(outgoingNodes),
    connectedNodeIds: Array.from(connectedSet),
  };
}

/**
 * Shared edge-highlighting helper for Blast Radius simulation: highlights edges connecting two
 * impacted nodes and dims unrelated edges and edge labels.
 */
export function applyC4BlastRadiusEdges(
  svgRoot: Element | null,
  options: {
    impactedNodeIds: Set<string>;
    parsedEdges: ParsedMermaidEdge[];
    knownNodeIds: Set<string>;
  }
): void {
  if (!svgRoot) return;
  const { impactedNodeIds, parsedEdges, knownNodeIds } = options;
  const edgePaths = queryEdgePaths(svgRoot);
  const edgeStates: Array<"outgoing" | "dimmed"> = [];

  edgePaths.forEach((edgeEl, idx) => {
    const ep = matchSvgEdgeEndpoints(edgeEl, knownNodeIds, parsedEdges, idx);
    const active = Boolean(ep && impactedNodeIds.has(ep.source) && impactedNodeIds.has(ep.target));
    const state: "outgoing" | "dimmed" = active ? "outgoing" : "dimmed";
    edgeStates.push(state);
    edgeEl.setAttribute("data-c4-edge-state", state);
    if (active) {
      edgeEl.classList.add("c4-edge-outgoing");
    } else {
      edgeEl.classList.add("c4-edge-dimmed");
    }
  });

  const edgeLabels = queryEdgeLabels(svgRoot);
  edgeLabels.forEach((labelEl, idx) => {
    const state = edgeStates[idx] || "dimmed";
    labelEl.setAttribute("data-c4-edge-state", state);
    if (state === "dimmed") {
      labelEl.classList.add("c4-edge-label-dimmed");
    } else {
      labelEl.classList.add("c4-edge-label-connected");
    }
  });
}

/**
 * Compute the readable zoomed-in entry scale when entering Full-Page C4 Diagram mode:
 * always `>= 1.0x` (never shrinking into unreadable 40% text) and capped at `1.5x`.
 */
export function computeFullPageZoom(svgSize: Size2D, viewportSize: Size2D): number {
  if (
    !Number.isFinite(svgSize.width) ||
    !Number.isFinite(svgSize.height) ||
    svgSize.width <= 0 ||
    svgSize.height <= 0 ||
    !Number.isFinite(viewportSize.width) ||
    !Number.isFinite(viewportSize.height) ||
    viewportSize.width <= 0 ||
    viewportSize.height <= 0
  ) {
    return 1.15;
  }

  const usableWidth = Math.max(200, viewportSize.width - 96);
  const usableHeight = Math.max(200, viewportSize.height - 140);
  const optimalScale = Math.min(usableWidth / svgSize.width, usableHeight / svgSize.height);
  return Number(Math.min(1.5, Math.max(optimalScale, 1.0)).toFixed(2));
}

/**
 * Compute pan offset `{ x, y }` to center a highlighted SVG node box inside the diagram stage.
 */
export function computeCenterPanForNode(
  svgSize: Size2D,
  nodeBox: { x: number; y: number; width: number; height: number },
  zoom: number
): { x: number; y: number } {
  if (
    !Number.isFinite(svgSize.width) ||
    !Number.isFinite(svgSize.height) ||
    svgSize.width <= 0 ||
    svgSize.height <= 0 ||
    !Number.isFinite(zoom) ||
    zoom <= 0
  ) {
    return { x: 0, y: 0 };
  }
  const svgCenterX = svgSize.width / 2;
  const svgCenterY = svgSize.height / 2;
  const nodeCenterX = nodeBox.x + nodeBox.width / 2;
  const nodeCenterY = nodeBox.y + nodeBox.height / 2;
  return {
    x: Math.round((svgCenterX - nodeCenterX) * zoom),
    y: Math.round((svgCenterY - nodeCenterY) * zoom),
  };
}
