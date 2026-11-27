import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, test } from "vitest";
import { DesignView } from "@/components/views/DesignView";
import { CatalogView } from "@/components/views/CatalogView";
import { BusinessView } from "@/components/views/BusinessView";
import { DeltasView } from "@/components/views/DeltasView";
import { EntityLabelProvider } from "@/lib/presentation";
import type { PortalData } from "@/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let mountedContainer: HTMLDivElement | null = null;
let mountedRoot: Root | null = null;

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

describe("INV-PORTAL-LIVE-DRIFT-SSE: DesignView schema resilience", () => {
  test("INV-PORTAL-LIVE-DRIFT-SSE: renders object/string UIC states, string/object variants, and polymorphic screen slots without throwing", () => {
    const invPfx = "INV";
    const data: PortalData = {
      overview: {
        system_id: "SYS-TEST",
        system_title: "Test System",
        total_specs: 6,
        valid_specs: 6,
        is_clean: true,
        domains_count: 0,
        capabilities_count: 0,
        policies_count: 0,
        personas_count: 0,
        components_count: 0,
        contracts_count: 0,
        deltas_count: 0,
        invariants_count: 1,
        decisions_count: 0,
      },
      specs: [],
      components: [],
      contracts: [],
      domains: [],
      capabilities: [],
      personas: [],
      policies: [],
      invariants: [],
      decisions: [],
      deltas: {
        proposed: [],
        in_review: [],
        approved: [],
        applied: [],
      },
      design_systems: [
        {
          id: "DS-UBA-LAYOUTS",
          title: "UBA Layouts",
          tokens: {
            color: {
              brand: {
                primary: { $value: "#0000ff", $type: "color" },
              },
            },
          },
        },
      ],
      clients: [
        {
          id: "CLT-MTS-WPF",
          title: "MTS WPF Desktop",
          platform: "desktop",
          design_system: "DS-UBA-LAYOUTS",
          screens: ["SCR-IMPORT-DECLARATION-BROWSER"],
        },
      ],
      ui_components: [
        {
          id: "UIC-BROWSER-LAYOUT",
          title: "Browser Layout",
          design_system: "DS-UBA-LAYOUTS",
          category: "data-display",
          variants: [
            { name: "standard-browser", description: "Generic entity list browser." },
          ],
          states: [
            {
              name: "loading",
              description: "Executing async WCF query handler via MessagesHandlerProxy.",
            },
            { name: "populated", description: "Grid populated." },
          ],
          invariants: [
            {
              id: `${invPfx}-UIC-TEST-001`,
              rule: "Must page results.",
              test_binding: "tests/BrowserTests.cs::PagedQuery",
            },
          ],
        },
        {
          id: "UIC-LEGACY-BADGE",
          title: "Legacy Badge",
          design_system: "DS-UBA-LAYOUTS",
          category: "atoms",
          variants: ["compact"],
          states: ["default", "disabled"],
        },
      ],
      screens: [
        {
          id: "SCR-IMPORT-DECLARATION-BROWSER",
          title: "Import Declaration Browser",
          route: "GBF.Layouts/ImportDeclarationBrowserLayout",
          slots: {
            header: "UIC-BROWSER-LAYOUT",
            actions: ["UIC-LEGACY-BADGE", "UIC-EXPORT"],
            modal: { component: "UIC-SCANNING-FORM", mode: "modal" },
          },
          states: [
            { name: "loading", description: "Fetching declarations" },
            "ready",
          ],
        },
      ],
      flows: [],
      diagrams: {
        c4_context: "",
        c4_containers: "",
        c4_components: {},
      },
    };

    const html = renderToStaticMarkup(
      React.createElement(
        EntityLabelProvider,
        { data },
        React.createElement(DesignView, { data })
      )
    );

    expect(html).toContain("Loading");
    expect(html).toContain("Executing async WCF query handler via MessagesHandlerProxy.");
    expect(html).toContain("Populated");
    expect(html).toContain("Default");
    expect(html).toContain("Compact");
    expect(html).toContain("Legacy Badge, Export");
    expect(html).toContain("Component: Scanning Form, Mode: Modal");
    expect(html).not.toContain(":loading");
    expect(html).not.toContain(":populated");
    expect(html).not.toContain(":default");
    expect(html).not.toContain("UIC-LEGACY-BADGE");
    expect(html).not.toContain("UIC-SCANNING-FORM");
    expect(html).toContain('for="designClientSelect"');
    expect(html).toContain('aria-pressed="true"');
  });

  test("INV-PORTAL-DOCS-READER-AND-FLOW-FULLSCREEN: renders Full Screen flow controls, default Catalog Docs Reader, Business rules table, and Timeline Deltas without raw IDs", () => {
    const invPfx = "INV";
    const syntheticRuleId = `${invPfx}-BILLING-LEDGER-BALANCE`;
    const data: PortalData = {
      overview: {
        system_id: "SYS-ACME",
        system_title: "Acme Platform",
        total_specs: 2,
        valid_specs: 2,
        is_clean: true,
        domains_count: 1,
        capabilities_count: 1,
        policies_count: 0,
        personas_count: 0,
        components_count: 0,
        contracts_count: 0,
        deltas_count: 1,
        invariants_count: 1,
        decisions_count: 0,
      },
      specs: [
        {
          id: "SYS-ACME",
          title: "Acme Platform",
          type: "product/system",
          path: "eidos/system.adoc",
          is_valid: true,
          body: "= Acme Platform\n\n== Overview\nPlatform overview prose.",
        },
        {
          id: "DOM-BILLING",
          title: "Billing Domain",
          type: "product/domain",
          path: "eidos/domains/billing/domain.adoc",
          is_valid: true,
          body: "= Billing Domain\n\n== Vision\nManages invoices.",
        },
        {
          id: "CAP-INVOICING",
          title: "Automated Invoicing",
          type: "product/capability",
          path: "eidos/domains/billing/capabilities/invoicing.adoc",
          is_valid: true,
          frontmatter: {
            domain: "DOM-BILLING",
            technical: {
              invariants: [
                {
                  id: syntheticRuleId,
                  rule: "Every ledger entry must balance debits and credits.",
                  test_binding: "tests/test_billing.py::test_balance",
                },
              ],
            },
          },
          body: "= Automated Invoicing\n\n== Intent\nGenerates monthly invoices.",
        },
      ],
      components: [],
      contracts: [],
      domains: [
        {
          id: "DOM-BILLING",
          title: "Billing Domain",
          mission: "Manages invoices cleanly.",
          classification: "core",
        },
      ],
      capabilities: [
        {
          id: "CAP-INVOICING",
          title: "Automated Invoicing",
          domain: "DOM-BILLING",
          summary: "Generates monthly customer invoices.",
          status: "active",
        },
      ],
      personas: [],
      policies: [],
      invariants: [
        {
          id: syntheticRuleId,
          rule: "Every ledger entry must balance debits and credits.",
          category: "reliability",
          severity: "blocking",
          source_title: "Automated Invoicing",
          test_binding: "tests/test_billing.py::test_balance",
        },
      ],
      decisions: [],
      deltas: {
        proposed: [
          {
            id: "DLT-2026-010",
            title: "Add Invoice PDF Export",
            status: "proposed",
            kind: "feature",
            motivation: "Allow customers to download invoices.",
            mutations: [{ operation: "update", target: "CAP-INVOICING" }],
          },
        ],
        in_review: [],
        approved: [],
        applied: [],
      },
      design_systems: [
        { id: "DS-ACME", title: "Acme Design System", tokens: {} },
      ],
      clients: [
        {
          id: "CLT-WEB",
          title: "Customer Web App",
          platform: "web",
          design_system: "DS-ACME",
          screens: [],
        },
      ],
      ui_components: [],
      screens: [],
      flows: [
        {
          id: "FLOW-CHECKOUT",
          title: "Checkout Journey",
          client: "CLT-WEB",
          flow_type: "single_client",
          trigger: "user_action",
          steps: [{ step: 1, title: "Confirm Cart", action: "User reviews items" }],
          mermaid: "flowchart LR\n  Cart --> Pay",
        },
        {
          id: "FLOW-OMNI-SYNC",
          title: "Cross-Client Sync",
          flow_type: "cross_client",
          steps: [{ step: 1, title: "Sync State", action: "Broadcast change" }],
          mermaid: "sequenceDiagram\n  Web->>Mobile: Sync",
        },
      ],
      diagrams: { c4_context: "", c4_containers: "", c4_components: {} },
    };

    const designHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, React.createElement(DesignView, { data }))
    );
    expect(designHtml).toContain('data-flow-fullscreen="FLOW-CHECKOUT"');
    expect(designHtml).toContain('data-flow-fullscreen="FLOW-OMNI-SYNC"');
    expect(designHtml).toContain('id="flowFullscreenDialog"');
    expect(designHtml).toContain("Full Screen");
    expect(designHtml).toContain('aria-label="Expand diagram to full screen"');

    const catalogHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, React.createElement(CatalogView, { data }))
    );
    expect(catalogHtml).toContain('id="catalogDocsReader"');
    expect(catalogHtml).toContain("Docs Reader");
    expect(catalogHtml).toContain("Next");
    expect(catalogHtml).toContain("Billing Domain");

    const capReaderHtml = renderToStaticMarkup(
      React.createElement(
        EntityLabelProvider,
        { data },
        React.createElement(CatalogView, { data, focusSpecId: "CAP-INVOICING" })
      )
    );
    expect(capReaderHtml).toContain('id="spec-verified-rules"');
    expect(capReaderHtml).toContain("Every ledger entry must balance debits and credits.");
    expect(capReaderHtml).toContain("Test Verified");
    expect(capReaderHtml).not.toContain(syntheticRuleId);

    const businessHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, React.createElement(BusinessView, { data }))
    );
    expect(businessHtml).toContain('id="businessRulesTable"');
    expect(businessHtml).toContain("Every ledger entry must balance debits and credits.");
    expect(businessHtml).not.toContain(syntheticRuleId);

    const deltasHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, React.createElement(DeltasView, { data }))
    );
    expect(deltasHtml).toContain("Add Invoice PDF Export");
    expect(deltasHtml).toContain("Allow customers to download invoices.");
  });

  test("INV-PORTAL-DOCS-READER-AND-FLOW-FULLSCREEN: clicking Full Screen on a flow opens #flowFullscreenDialog with toggleable Steps and closes cleanly", async () => {
    const data: PortalData = {
      overview: {
        system_id: "SYS-ACME",
        system_title: "Acme Platform",
        total_specs: 1,
        valid_specs: 1,
        is_clean: true,
        domains_count: 0,
        capabilities_count: 0,
        policies_count: 0,
        personas_count: 0,
        components_count: 0,
        contracts_count: 0,
        deltas_count: 0,
        invariants_count: 0,
        decisions_count: 0,
      },
      specs: [],
      components: [],
      contracts: [],
      domains: [],
      capabilities: [],
      personas: [],
      policies: [],
      invariants: [],
      decisions: [],
      deltas: { proposed: [], in_review: [], approved: [], applied: [] },
      design_systems: [{ id: "DS-ACME", title: "Acme Design System", tokens: {} }],
      clients: [
        {
          id: "CLT-WEB",
          title: "Customer Web App",
          platform: "web",
          design_system: "DS-ACME",
          screens: [],
        },
      ],
      ui_components: [],
      screens: [],
      flows: [
        {
          id: "FLOW-CHECKOUT",
          title: "Checkout Journey",
          client: "CLT-WEB",
          flow_type: "single_client",
          trigger: "user_action",
          steps: [{ step: 1, title: "Confirm Cart", action: "Review order items" }],
          mermaid: "flowchart LR\n  Cart --> Pay",
        },
      ],
      diagrams: { c4_context: "", c4_containers: "", c4_components: {} },
    };

    mountedContainer = document.createElement("div");
    document.body.appendChild(mountedContainer);
    mountedRoot = createRoot(mountedContainer);

    await React.act(async () => {
      mountedRoot!.render(
        React.createElement(EntityLabelProvider, { data }, React.createElement(DesignView, { data }))
      );
    });

    const expandBtn = mountedContainer.querySelector<HTMLButtonElement>(
      'button[data-flow-fullscreen="FLOW-CHECKOUT"]'
    );
    expect(expandBtn).not.toBeNull();

    await React.act(async () => {
      expandBtn!.click();
    });

    const dialog = mountedContainer.querySelector<HTMLDialogElement>("#flowFullscreenDialog");
    expect(dialog).not.toBeNull();
    expect(dialog!.hasAttribute("open")).toBe(true);
    expect(dialog!.textContent).toContain("Checkout Journey");
    expect(dialog!.textContent).toContain("Confirm Cart");

    const closeBtn = dialog!.querySelector<HTMLButtonElement>('button[aria-label="Exit full screen flow"]');
    expect(closeBtn).not.toBeNull();
    await React.act(async () => {
      closeBtn!.click();
    });
    expect(dialog!.hasAttribute("open")).toBe(false);
  });
});
