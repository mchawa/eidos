// @vitest-environment jsdom
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import * as React from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { ArchitectureView } from "@/components/views/ArchitectureView";
import { EntityLabelProvider, buildEntityLabelIndex } from "@/lib/presentation";
import { SPEC_NAV_EVENT, requestSpecNavigation, type SpecNavDetail } from "@/lib/specNav";
import type { PortalData } from "@/types";

vi.mock("@/lib/mermaid", () => ({
  getConfiguredMermaid: async () => ({
    render: async () => ({ svg: '<svg viewBox="0 0 200 100"><g class="node"></g></svg>' }),
  }),
  sanitizeMermaidCode: (src: string) => src,
}));

const RAW_ENTITY_ID_RE =
  /\b(?:SYS|DOM|CAP|POL|PER|INV|DEC|CNT|CMP|CTR|DS|CLT|SCR|UIC|FLOW|DLT|EXP)-[A-Z0-9_-]+\b/i;

function buildPortalFixture(): PortalData {
  return {
    overview: {
      system_id: "SYS-ACME-001",
      system_title: "Acme Commerce Platform",
      total_specs: 4,
      valid_specs: 4,
      is_clean: true,
      domains_count: 1,
      capabilities_count: 1,
      policies_count: 0,
      personas_count: 0,
      components_count: 1,
      contracts_count: 1,
      deltas_count: 0,
      invariants_count: 0,
      decisions_count: 0,
    },
    specs: [
      {
        id: "CNT-CHECKOUT-API",
        title: "Checkout Service",
        type: "tech/container",
        path: "eidos/architecture/containers/checkout.adoc",
        status: "active",
        is_valid: true,
        frontmatter: { id: "CNT-CHECKOUT-API" },
        body: "== Container Boundary\nHandles resilient checkout orchestration across regions.",
      },
      {
        id: "CMP-PAYMENT-ROUTER",
        title: "Payment Router",
        type: "tech/component",
        path: "eidos/architecture/components/payment-router.adoc",
        status: "active",
        is_valid: true,
        frontmatter: { id: "CMP-PAYMENT-ROUTER", container: "CNT-CHECKOUT-API" },
        body: "== Routing Rules\nRoutes payment intents to active acquiring gateways with failover.",
      },
    ],
    domains: [],
    capabilities: [],
    policies: [],
    personas: [],
    components: [
      {
        id: "CNT-CHECKOUT-API",
        title: "Checkout Service",
        status: "active",
        container_type: "service",
        runtime: "Python 3.12",
        tier: "tier_service",
        tier_name: "Service Layer",
        source_path: "src/checkout/",
        responsibility: "Orchestrates shopping cart finalization and payment capture.",
        description: "== Container Boundary\nHandles resilient checkout orchestration across regions.",
        internal_components: [
          {
            id: "CMP-PAYMENT-ROUTER",
            title: "Payment Router",
            responsibility: "Routes payment intents to active acquiring gateways.",
            description:
              "== Routing Rules\nRoutes payment intents to active acquiring gateways with failover.",
            source_path: "src/checkout/router.py",
          },
          {
            id: "CMP-LEDGER-SYNC",
            title: "Ledger Synchronizer",
            responsibility: "Writes immutable accounting journal entries.",
            description: "== Accounting Journal\nAppends double-entry ledger records atomically.",
            source_path: "src/checkout/ledger.py",
          },
        ],
        dependencies: { internal_containers: [], external_services: [] },
        contracts: { provides: ["CTR-CHECKOUT-HTTP"], consumes: [] },
        invariants: [],
        decisions: [],
      },
    ],
    contracts: [
      {
        id: "CTR-CHECKOUT-HTTP",
        title: "Checkout HTTP Interface",
        status: "active",
        protocol: "rest_http",
        container: "CNT-CHECKOUT-API",
        description: "== Endpoint Contract\nPOST /v1/checkouts creates an idempotent session.",
      },
    ],
    decisions: [],
    deltas: [],
    explorations: [],
    design: { systems: [], clients: [], flows: [], ui_components: [], screens: [] },
    diagrams: {
      c4_context: "graph TD\nSYS[Acme Commerce Platform]",
      c4_containers: "graph TD\nCNT_CHECKOUT_API[Checkout Service]",
      c4_components: {
        "CNT-CHECKOUT-API": "graph TD\nmod_CMP_PAYMENT_ROUTER[Payment Router]",
      },
    },
    edges: [],
  };
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
});

describe("INV-PORTAL-INSPECTOR-FULL-SPEC-READING: uncramped inspector prose & reader escalation", () => {
  test("AsciiDocRenderer honors measure='prose' (default) vs measure='full'", () => {
    const proseHtml = renderToStaticMarkup(
      React.createElement(AsciiDocRenderer, { content: "== Heading\nProse body." })
    );
    expect(proseHtml).toContain("max-w-4xl");
    expect(proseHtml).toContain("xl:max-w-5xl");

    const fullHtml = renderToStaticMarkup(
      React.createElement(AsciiDocRenderer, {
        measure: "full",
        content: "== Heading\nFull width prose body.",
      })
    );
    expect(fullHtml).not.toContain("max-w-4xl");
    expect(fullHtml).toContain('class="transition-all duration-200 w-full"');
  });

  test("requestSpecNavigation dispatches optional mode='reader' while preserving default callers", () => {
    const events: SpecNavDetail[] = [];
    const listener = (ev: Event) => events.push((ev as CustomEvent<SpecNavDetail>).detail);
    window.addEventListener(SPEC_NAV_EVENT, listener);
    try {
      requestSpecNavigation("CNT-CHECKOUT-API");
      requestSpecNavigation("CMP-PAYMENT-ROUTER", { mode: "reader" });
    } finally {
      window.removeEventListener(SPEC_NAV_EVENT, listener);
    }
    expect(events).toEqual([
      { target: "CNT-CHECKOUT-API" },
      { target: "CMP-PAYMENT-ROUTER", mode: "reader" },
    ]);
  });

  test("#c4InspectorDrawer renders uncramped container & focused component Full Specification and dispatches mode='reader'", async () => {
    const data = buildPortalFixture();
    const labelIndex = buildEntityLabelIndex(data);
    mountedContainer = document.createElement("div");
    document.body.appendChild(mountedContainer);
    mountedRoot = createRoot(mountedContainer);

    const navEvents: SpecNavDetail[] = [];
    const onNav = (ev: Event) => navEvents.push((ev as CustomEvent<SpecNavDetail>).detail);
    window.addEventListener(SPEC_NAV_EVENT, onNav);

    try {
      await React.act(async () => {
        mountedRoot!.render(
          React.createElement(
            EntityLabelProvider,
            { value: labelIndex },
            React.createElement(ArchitectureView, {
              data,
              initialComponentId: "CMP-PAYMENT-ROUTER",
              initialComponentNonce: 1,
            })
          )
        );
      });
      await React.act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 40));
      });

      const drawer = document.getElementById("c4InspectorDrawer");
      expect(drawer).not.toBeNull();
      // Must never contain the cramped nested scroll trap
      expect(drawer!.innerHTML).not.toContain("max-h-[360px]");

      // Prominent focused component Full Specification section renders near the top
      const focusedSection = drawer!.querySelector('[data-focused-component-section="true"]');
      expect(focusedSection).not.toBeNull();
      expect(focusedSection!.textContent).toContain("Payment Router");
      expect(focusedSection!.textContent).toContain("Full Specification");
      expect(focusedSection!.textContent).toContain("Open in Full Reader");
      // Uses measure="full" (no max-w-4xl cap inside inspector)
      expect(focusedSection!.innerHTML).not.toContain("max-w-4xl");
      // Zero raw entity ID slugs in visible drawer text (INV-PORTAL-HUMAN-READABLE-UI)
      expect(RAW_ENTITY_ID_RE.test(drawer!.textContent || "")).toBe(false);

      // Clicking "Open in Full Reader" on the focused CMP-* dispatches mode: "reader"
      const focusedReaderBtn = Array.from(focusedSection!.querySelectorAll("button")).find((b) =>
        b.textContent?.includes("Open in Full Reader")
      );
      expect(focusedReaderBtn).toBeDefined();
      await React.act(async () => {
        focusedReaderBtn!.click();
      });

      expect(navEvents).toContainEqual({ target: "CMP-PAYMENT-ROUTER", mode: "reader" });
    } finally {
      window.removeEventListener(SPEC_NAV_EVENT, onNav);
    }
  });
});
