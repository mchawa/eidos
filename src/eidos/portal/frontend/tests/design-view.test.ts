import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { DesignView } from "@/components/views/DesignView";
import { EntityLabelProvider } from "@/lib/presentation";
import type { PortalData } from "@/types";

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
});
