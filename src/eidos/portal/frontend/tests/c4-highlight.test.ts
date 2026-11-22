// @vitest-environment jsdom
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import * as React from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  applyC4BlastRadiusEdges,
  applyC4ConnectionHighlight,
  computeCenterPanForNode,
  computeFullPageZoom,
  matchSvgEdgeEndpoints,
  parseMermaidEdges,
  resolveSvgNodeId,
  restoreC4SvgStyles,
} from "@/lib/c4Highlight";
import { ArchitectureView } from "@/components/views/ArchitectureView";
import { EntityLabelProvider, buildEntityLabelIndex } from "@/lib/presentation";
import type { PortalData } from "@/types";

const SAMPLE_L2_MERMAID = `graph TD
  subgraph tier_presentation["Presentation Layer"]
    CNT_ACME_CLI["Acme CLI"]
    CNT_ACME_PORTAL["Living Web Portal"]
  end
  subgraph tier_core["Core Layer"]
    CNT_ACME_CORE["Acme Core Engine"]
  end
  PER_ARCHITECT["Software Architect"] -->|"runs commands"| CNT_ACME_CLI
  CNT_ACME_CLI -->|"invokes"| CNT_ACME_CORE
  CNT_ACME_PORTAL -.->|"reads specs"| CNT_ACME_CORE
  CNT_ACME_CORE ==>|"writes"| EXT_SQLITE["SQLite Index"]
`;

const SAMPLE_L3_MERMAID = `graph TD
  mod_CMP_PORTAL_ASSETS["Portal Assets"]
  mod_CMP_PORTAL_SERVER["Portal Server"]
  peer_CNT_ACME_CORE["Acme Core Engine"]
  ctr_CTR_ACME_PORTAL["Portal HTTP API"]
  mod_CMP_PORTAL_SERVER -->|"loads bundle"| mod_CMP_PORTAL_ASSETS
  mod_CMP_PORTAL_SERVER -->|"queries"| peer_CNT_ACME_CORE
  mod_CMP_PORTAL_SERVER -->|"provides"| ctr_CTR_ACME_PORTAL
`;

const MOCK_SVG = `<svg viewBox="0 0 800 400">
  <g class="edgePaths">
    <path data-edge="true" data-id="L_PER_ARCHITECT_CNT_ACME_CLI_0" class="flowchart-link" d="M0,0 L10,10" />
    <path data-edge="true" data-id="L_CNT_ACME_CLI_CNT_ACME_CORE_1" class="flowchart-link" d="M0,0 L10,10" />
    <path data-edge="true" data-id="L_CNT_ACME_PORTAL_CNT_ACME_CORE_2" class="flowchart-link" d="M0,0 L10,10" />
    <path data-edge="true" data-id="L_CNT_ACME_CORE_EXT_SQLITE_3" class="flowchart-link" d="M0,0 L10,10" />
  </g>
  <g class="edgeLabels">
    <g class="edgeLabel"><text>runs commands</text></g>
    <g class="edgeLabel"><text>invokes</text></g>
    <g class="edgeLabel"><text>reads specs</text></g>
    <g class="edgeLabel"><text>writes</text></g>
  </g>
  <g class="nodes">
    <g class="node" id="flowchart-PER_ARCHITECT-0" data-id="PER_ARCHITECT"><rect /></g>
    <g class="node" id="flowchart-CNT_ACME_CLI-1" data-id="CNT_ACME_CLI"><rect /></g>
    <g class="node" id="flowchart-CNT_ACME_PORTAL-2" data-id="CNT_ACME_PORTAL"><rect /></g>
    <g class="node" id="flowchart-CNT_ACME_CORE-3" data-id="CNT_ACME_CORE"><rect /></g>
    <g class="node" id="flowchart-EXT_SQLITE-4" data-id="EXT_SQLITE"><rect /></g>
  </g>
</svg>`;

vi.mock("@/lib/mermaid", () => ({
  getConfiguredMermaid: async () => ({
    render: async () => ({ svg: MOCK_SVG }),
  }),
  sanitizeMermaidCode: (src: string) => src,
}));

function createSvgElement(markup: string): SVGElement {
  const wrapper = document.createElement("div");
  wrapper.innerHTML = markup;
  return wrapper.querySelector("svg")!;
}

let mountedContainer: HTMLDivElement | null = null;
let mountedRoot: ReturnType<typeof createRoot> | null = null;

afterEach(async () => {
  if (mountedRoot) {
    await React.act(async () => {
      mountedRoot!.unmount();
    });
    mountedRoot = null;
  }
  if (mountedContainer) {
    mountedContainer.remove();
    mountedContainer = null;
  }
  document.body.style.overflow = "";
});

describe("INV-PORTAL-C4-FULLPAGE-AND-CONNECTION-HIGHLIGHT: pure SVG highlight engine & zoom", () => {
  test("parses directed edges and handles underscore-containing node IDs without split('_') bugs", () => {
    const parsed = parseMermaidEdges(SAMPLE_L2_MERMAID);
    expect(parsed.edges).toEqual([
      { source: "PER_ARCHITECT", target: "CNT_ACME_CLI", label: "runs commands" },
      { source: "CNT_ACME_CLI", target: "CNT_ACME_CORE", label: "invokes" },
      { source: "CNT_ACME_PORTAL", target: "CNT_ACME_CORE", label: "reads specs" },
      { source: "CNT_ACME_CORE", target: "EXT_SQLITE", label: "writes" },
    ]);
    expect(parsed.nodeIds.has("CNT_ACME_CLI")).toBe(true);
    expect(parsed.nodeIds.has("PER_ARCHITECT")).toBe(true);
    expect(parsed.nodeIds.has("EXT_SQLITE")).toBe(true);

    const parsedL3 = parseMermaidEdges(SAMPLE_L3_MERMAID);
    expect(parsedL3.edges).toHaveLength(3);
    expect(parsedL3.nodeIds.has("mod_CMP_PORTAL_ASSETS")).toBe(true);
    expect(parsedL3.nodeIds.has("peer_CNT_ACME_CORE")).toBe(true);
    expect(parsedL3.nodeIds.has("ctr_CTR_ACME_PORTAL")).toBe(true);

    const svg = createSvgElement(MOCK_SVG);
    const cliNode = svg.querySelector("#flowchart-CNT_ACME_CLI-1")!;
    expect(resolveSvgNodeId(cliNode, parsed.nodeIds)).toBe("CNT_ACME_CLI");

    // Verify matching when data-id is absent and only flowchart-<id>-<n> exists
    cliNode.removeAttribute("data-id");
    expect(resolveSvgNodeId(cliNode, parsed.nodeIds)).toBe("CNT_ACME_CLI");

    const edgePaths = Array.from(svg.querySelectorAll('path[data-edge="true"]'));
    expect(matchSvgEdgeEndpoints(edgePaths[1], parsed.nodeIds, parsed.edges, 1)).toEqual({
      source: "CNT_ACME_CLI",
      target: "CNT_ACME_CORE",
    });
    expect(matchSvgEdgeEndpoints(edgePaths[3], parsed.nodeIds, parsed.edges, 3)).toEqual({
      source: "CNT_ACME_CORE",
      target: "EXT_SQLITE",
    });
  });

  test("applies all / incoming / outgoing connection highlight states and restores cleanly on clear", () => {
    const parsed = parseMermaidEdges(SAMPLE_L2_MERMAID);
    const svg = createSvgElement(MOCK_SVG);

    // Focus CNT_ACME_CORE with direction='all' (2 incoming: CLI & PORTAL; 1 outgoing: EXT_SQLITE; PER_ARCHITECT dimmed)
    const allRes = applyC4ConnectionHighlight(svg, {
      selectedNodeId: "CNT_ACME_CORE",
      direction: "all",
      parsedEdges: parsed.edges,
      knownNodeIds: parsed.nodeIds,
    });
    expect(allRes.incomingCount).toBe(2);
    expect(allRes.outgoingCount).toBe(1);
    expect(svg.querySelector("#flowchart-CNT_ACME_CORE-3")!.getAttribute("data-c4-highlight")).toBe("selected");
    expect(svg.querySelector("#flowchart-CNT_ACME_CLI-1")!.getAttribute("data-c4-highlight")).toBe("incoming");
    expect(svg.querySelector("#flowchart-CNT_ACME_PORTAL-2")!.getAttribute("data-c4-highlight")).toBe("incoming");
    expect(svg.querySelector("#flowchart-EXT_SQLITE-4")!.getAttribute("data-c4-highlight")).toBe("outgoing");
    expect(svg.querySelector("#flowchart-PER_ARCHITECT-0")!.getAttribute("data-c4-highlight")).toBe("dimmed");

    const edges = Array.from(svg.querySelectorAll('path[data-edge="true"]'));
    const labels = Array.from(svg.querySelectorAll(".edgeLabel"));
    expect(edges[0].getAttribute("data-c4-edge-state")).toBe("dimmed");
    expect(edges[1].getAttribute("data-c4-edge-state")).toBe("incoming");
    expect(edges[2].getAttribute("data-c4-edge-state")).toBe("incoming");
    expect(edges[3].getAttribute("data-c4-edge-state")).toBe("outgoing");
    expect(labels[0].classList.contains("c4-edge-label-dimmed")).toBe(true);
    expect(labels[1].classList.contains("c4-edge-label-connected")).toBe(true);
    expect(labels[3].classList.contains("c4-edge-label-connected")).toBe(true);

    // Direction='outgoing' dims incoming neighbors and incoming edges
    applyC4ConnectionHighlight(svg, {
      selectedNodeId: "CNT_ACME_CORE",
      direction: "outgoing",
      parsedEdges: parsed.edges,
      knownNodeIds: parsed.nodeIds,
    });
    expect(svg.querySelector("#flowchart-CNT_ACME_CLI-1")!.getAttribute("data-c4-highlight")).toBe("dimmed");
    expect(svg.querySelector("#flowchart-EXT_SQLITE-4")!.getAttribute("data-c4-highlight")).toBe("outgoing");
    expect(edges[1].getAttribute("data-c4-edge-state")).toBe("dimmed");
    expect(edges[3].getAttribute("data-c4-edge-state")).toBe("outgoing");

    // Direction='incoming' dims outgoing neighbor and outgoing edge
    applyC4ConnectionHighlight(svg, {
      selectedNodeId: "CNT_ACME_CORE",
      direction: "incoming",
      parsedEdges: parsed.edges,
      knownNodeIds: parsed.nodeIds,
    });
    expect(svg.querySelector("#flowchart-CNT_ACME_CLI-1")!.getAttribute("data-c4-highlight")).toBe("incoming");
    expect(svg.querySelector("#flowchart-EXT_SQLITE-4")!.getAttribute("data-c4-highlight")).toBe("dimmed");
    expect(edges[1].getAttribute("data-c4-edge-state")).toBe("incoming");
    expect(edges[3].getAttribute("data-c4-edge-state")).toBe("dimmed");

    // Clearing restores all highlight attributes/classes cleanly
    const cleared = applyC4ConnectionHighlight(svg, {
      selectedNodeId: null,
      direction: "all",
      parsedEdges: parsed.edges,
      knownNodeIds: parsed.nodeIds,
    });
    expect(cleared.selectedNodeId).toBeNull();
    expect(svg.querySelectorAll("[data-c4-highlight], [data-c4-edge-state]")).toHaveLength(0);

    // Blast radius edge helper highlights edges between impacted hops and restores cleanly
    applyC4BlastRadiusEdges(svg, {
      impactedNodeIds: new Set(["CNT_ACME_CLI", "CNT_ACME_CORE"]),
      parsedEdges: parsed.edges,
      knownNodeIds: parsed.nodeIds,
    });
    expect(edges[1].getAttribute("data-c4-edge-state")).toBe("outgoing");
    expect(edges[0].getAttribute("data-c4-edge-state")).toBe("dimmed");
    restoreC4SvgStyles(svg);
    expect(svg.querySelectorAll("[data-c4-edge-state]")).toHaveLength(0);
    // Null SVG safety
    expect(applyC4ConnectionHighlight(null, {
      selectedNodeId: "CNT_ACME_CORE",
      direction: "all",
      parsedEdges: parsed.edges,
      knownNodeIds: parsed.nodeIds,
    }).incomingCount).toBe(0);
  });

  test("computeFullPageZoom guarantees readable >= 1.0x up to 1.5x zoom with invalid-size fallback", () => {
    // Huge diagram: optimalScale < 1.0 still opens at 1.0x (never cramped 0.35x)
    expect(computeFullPageZoom({ width: 3200, height: 1800 }, { width: 1280, height: 800 })).toBe(1.0);
    // Compact diagram: zooms in comfortably up to 1.5x cap
    expect(computeFullPageZoom({ width: 400, height: 250 }, { width: 1440, height: 900 })).toBe(1.5);
    // Medium diagram: intermediate scale between 1.0 and 1.5
    const mid = computeFullPageZoom({ width: 1000, height: 600 }, { width: 1400, height: 900 });
    expect(mid).toBeGreaterThanOrEqual(1.0);
    expect(mid).toBeLessThanOrEqual(1.5);
    // Invalid/zero dimensions fall back cleanly
    expect(computeFullPageZoom({ width: 0, height: 0 }, { width: 1200, height: 800 })).toBe(1.15);
    // Centering helper returns deterministic offset
    expect(computeCenterPanForNode({ width: 800, height: 400 }, { x: 600, y: 100, width: 100, height: 100 }, 1.2)).toEqual({
      x: -300,
      y: 60,
    });
  });

  test("ArchitectureView promotes #c4DiagramContainer in-place into z-[45] Full Page dialog and highlights connections", async () => {
    const data: PortalData = {
      overview: {
        system_id: "SYS-ACME",
        system_title: "Acme Platform",
        total_specs: 3,
        valid_specs: 3,
        is_clean: true,
        domains_count: 1,
        capabilities_count: 1,
        policies_count: 0,
        personas_count: 1,
        components_count: 3,
        contracts_count: 0,
        deltas_count: 0,
        invariants_count: 0,
        decisions_count: 0,
      },
      specs: [],
      domains: [],
      capabilities: [],
      policies: [],
      personas: [
        {
          id: "PER-ARCHITECT",
          title: "Software Architect",
          status: "active",
        },
      ],
      components: [
        {
          id: "CNT-ACME-CLI",
          title: "Acme CLI",
          status: "active",
          container_type: "cli_app",
          runtime: "Python",
          tier: "tier_presentation",
          tier_name: "Presentation",
          source_path: "src/acme/cli",
          internal_components: [],
          dependencies: { internal_containers: ["CNT-ACME-CORE"], external_services: [] },
          contracts: { provides: [], consumes: [] },
        },
        {
          id: "CNT-ACME-PORTAL",
          title: "Living Web Portal",
          status: "active",
          container_type: "web_app",
          runtime: "React",
          tier: "tier_presentation",
          tier_name: "Presentation",
          source_path: "src/acme/portal",
          internal_components: [],
          dependencies: { internal_containers: ["CNT-ACME-CORE"], external_services: [] },
          contracts: { provides: [], consumes: [] },
        },
        {
          id: "CNT-ACME-CORE",
          title: "Acme Core Engine",
          status: "active",
          container_type: "library",
          runtime: "Python",
          tier: "tier_core",
          tier_name: "Core",
          source_path: "src/acme/core",
          internal_components: [],
          dependencies: { internal_containers: [], external_services: [] },
          contracts: { provides: [], consumes: [] },
        },
      ],
      contracts: [],
      decisions: [],
      deltas: [],
      explorations: [],
      design: { systems: [], clients: [], flows: [], ui_components: [], screens: [] },
      diagrams: {
        c4_context: SAMPLE_L2_MERMAID,
        c4_containers: SAMPLE_L2_MERMAID,
        c4_components: {},
      },
      edges: [
        { source: "CNT-ACME-CLI", target: "CNT-ACME-CORE", rel_type: "depends_on" },
        { source: "CNT-ACME-PORTAL", target: "CNT-ACME-CORE", rel_type: "depends_on" },
      ],
    };

    const labelIndex = buildEntityLabelIndex(data);
    mountedContainer = document.createElement("div");
    document.body.appendChild(mountedContainer);
    mountedRoot = createRoot(mountedContainer);

    await React.act(async () => {
      mountedRoot!.render(
        React.createElement(
          EntityLabelProvider,
          { value: labelIndex },
          React.createElement(ArchitectureView, { data })
        )
      );
    });
    await React.act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    // Switch to Level 2 Containers
    const btnL2 = document.getElementById("btnC4Level2")!;
    await React.act(async () => {
      btnL2.click();
    });
    await React.act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    // Enter Full Page mode
    const btnFullPage = document.getElementById("btnC4FullPage")!;
    expect(btnFullPage).not.toBeNull();
    await React.act(async () => {
      btnFullPage.click();
    });
    const stage = document.getElementById("c4DiagramStage")!;
    expect(stage.getAttribute("role")).toBe("dialog");
    expect(stage.getAttribute("aria-modal")).toBe("true");
    expect(stage.className).toContain("z-[45]");
    expect(document.body.style.overflow).toBe("hidden");

    // Highlight CNT_ACME_CORE via #c4HighlightSelect
    const select = document.getElementById("c4HighlightSelect") as HTMLSelectElement;
    expect(select).not.toBeNull();
    await React.act(async () => {
      const nativeSetter = Object.getOwnPropertyDescriptor(
        HTMLSelectElement.prototype,
        "value"
      )?.set;
      nativeSetter?.call(select, "CNT_ACME_CORE");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const pill = document.querySelector('[data-c4-connection-pill="true"]');
    expect(pill).not.toBeNull();
    expect(pill!.textContent).toContain("Acme Core Engine");
    expect(pill!.textContent).toContain("2 incoming");
    expect(pill!.textContent).toContain("1 outgoing");

    // Escape closes full-page stage cleanly
    await React.act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(stage.getAttribute("role")).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });
});
