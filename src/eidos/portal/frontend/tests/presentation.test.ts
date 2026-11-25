import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { NavigationRail } from "@/components/layout/NavigationRail";
import { CommandDialog, type CommandItem } from "@/components/ui/command";
import { SpecReader } from "@/components/ui/SpecReader";
import { HomeView } from "@/components/views/HomeView";
import { BusinessView } from "@/components/views/BusinessView";
import { ArchitectureView } from "@/components/views/ArchitectureView";
import { DesignView } from "@/components/views/DesignView";
import { DeltasView } from "@/components/views/DeltasView";
import { ExplorationsView } from "@/components/views/ExplorationsView";
import { MemoryView } from "@/components/views/MemoryView";
import { CatalogView } from "@/components/views/CatalogView";
import { EntityMemoriesStrip } from "@/components/ui/EntityMemoriesStrip";
import {
  EntityLabelProvider,
  buildEntityLabelIndex,
  formatCommandCategory,
  formatContainerType,
  formatDeltaKind,
  formatDisplayTitle,
  formatFlowType,
  formatMemoryCategory,
  formatMemoryFreshness,
  formatMemoryFreshnessReason,
  formatMutationOperation,
  formatProtocol,
  formatSeverity,
  formatSpecType,
  formatStatus,
  formatTabLabel,
  humanizeInvariantTitle,
  humanizeToken,
  humanizeTokenPath,
  looksLikeRawEntityId,
  resolveEntityTitle,
} from "@/lib/presentation";
import type { PortalData } from "@/types";

function extractVisibleText(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function extractUserFacingAttributes(html: string): string[] {
  const attrs: string[] = [];
  const re = /\b(?:title|placeholder|aria-label)="([^"]*)"/gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(html)) !== null) {
    attrs.push(
      match[1]
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#x27;/g, "'")
    );
  }
  return attrs;
}

const RAW_ENTITY_ID_RE =
  /\b(?:SYS|ARCH|DOM|CAP|POL|PER|INV|DEC|CNT|CMP|CTR|DS|CLT|SCR|UIC|FLOW|DLT|EXP|MEM)-[A-Z0-9_-]+\b/i;

const RAW_SCHEMA_TYPE_RE =
  /\b(?:product|tech|design|delta|agent)\/(?:system|domain|capability|policy|persona|exploration|architecture|container|component|contract|ui-component|client|screen|flow|mutation|memory)\b/i;

const BANNED_JARGON = [
  "DDD Strategic Layer",
  "Whitebox",
  "W3C DTCG",
  "SSE Live Stream",
  "POL-002 Traceability",
  "1–2 Hop Graph BFS",
  "1-2 Hop Graph BFS",
  "Zero ADR Policy",
  "Planned Mode",
  "SCR-PORTAL-BUSINESS",
  "JTBD",
  "Schema Conformance",
  "Inspect Raw YAML",
];

function buildFixture(): PortalData {
  return {
    overview: {
      system_id: "SYS-ACME-001",
      system_title: "Acme Commerce Platform",
      total_specs: 14,
      valid_specs: 13,
      is_clean: false,
      flows_count: 1,
      explorations_count: 1,
      memories_count: 3,
      active_memories_count: 2,
      domains_count: 1,
      capabilities_count: 1,
      policies_count: 1,
      personas_count: 1,
      components_count: 2,
      contracts_count: 1,
      deltas_count: 1,
      invariants_count: 1,
      decisions_count: 1,
    },
    specs: [
      {
        id: "SYS-ACME-001",
        title: "Acme Commerce Platform",
        type: "product/system",
        path: "specs/system.adoc",
        status: "active",
        is_valid: true,
        frontmatter: { id: "SYS-ACME-001", domain: "DOM-EXPERIENCE-WEB" },
        body: "System overview body.",
      },
      {
        id: "DOM-EXPERIENCE-WEB",
        title: "Experience & Storefront",
        type: "product/domain",
        path: "specs/domains/experience/domain.adoc",
        status: "active",
        is_valid: true,
        frontmatter: { id: "DOM-EXPERIENCE-WEB" },
        body: "Domain body.",
      },
      {
        id: "CAP-WEB-STOREFRONT",
        title: "Interactive Web Storefront",
        type: "product/capability",
        path: "specs/domains/experience/capabilities/web-storefront.adoc",
        status: "active",
        is_valid: true,
        frontmatter: {
          id: "CAP-WEB-STOREFRONT",
          domain: "DOM-EXPERIENCE-WEB",
          container: "CNT-ACME-WEB",
        },
        body: "Capability body.",
      },
      {
        id: "CNT-ACME-WEB",
        title: "Acme Web Application",
        type: "tech/container",
        path: "specs/architecture/containers/web.adoc",
        status: "active",
        is_valid: false,
        frontmatter: { id: "CNT-ACME-WEB", domain: "DOM-EXPERIENCE-WEB" },
        body: "Container body.",
      },
      {
        id: "UIC-APP-SHELL",
        title: "Application Shell",
        type: "design/ui-component",
        path: "specs/design/systems/acme/components/app-shell.adoc",
        status: "active",
        is_valid: true,
        frontmatter: { id: "UIC-APP-SHELL" },
        body: "UI component body.",
      },
    ],
    components: [
      {
        id: "CNT-ACME-WEB",
        title: "Acme Web Application",
        domain: "DOM-EXPERIENCE-WEB",
        container_type: "web_app",
        status: "active",
        tech_stack: ["React", "TypeScript"],
        source_paths: ["src/acme/web/generator.py"],
        dependencies: ["CNT-ACME-API"],
        internal_components: [
          {
            id: "CMP-WEB-ASSETS",
            title: "Web Frontend Assets",
            responsibility: "Renders interactive storefront screens.",
            file_path: "src/acme/web/frontend/src/App.tsx",
            spec_path: "specs/architecture/components/web-assets.adoc",
            contracts_provided: ["CTR-ACME-HTTP"],
            contracts_consumed: [],
            invariants: [
              {
                id: "INV-WEB-HUMAN-READABLE-UI",
                title: "Web Human Readable UI",
                rule: "Always render human-readable titles and labels.",
                test_binding: "tests/test_web.py::test_web_human_readable_ui",
              },
            ],
          },
        ],
      },
      {
        id: "CNT-ACME-API",
        title: "Core Commerce Engine",
        domain: "DOM-EXPERIENCE-WEB",
        container_type: "library",
        status: "active",
        tech_stack: ["Python"],
        source_paths: ["src/acme/core.py"],
        dependencies: [],
        internal_components: [],
      },
    ],
    contracts: [
      {
        id: "CTR-ACME-HTTP",
        title: "Storefront HTTP Interface",
        container: "CNT-ACME-WEB",
        protocol: "http_json",
        status: "active",
        version: "1.0.0",
        summary: "Serves storefront payloads.",
        consumers: ["CNT-ACME-API"],
        body: "Contract documentation.",
      },
    ],
    domains: [
      {
        id: "DOM-EXPERIENCE-WEB",
        title: "Experience & Storefront",
        classification: "core",
        owner: "Platform Team",
        path: "specs/domains/experience/domain.adoc",
        capabilities: ["CAP-WEB-STOREFRONT"],
        components: ["CNT-ACME-WEB"],
        policies: ["POL-DEMO-GIT-SOURCE"],
        body: "Experience domain description.",
      },
    ],
    capabilities: [
      {
        id: "CAP-WEB-STOREFRONT",
        title: "Interactive Web Storefront",
        domain: "DOM-EXPERIENCE-WEB",
        status: "active",
        version: "1.3.0",
        path: "specs/domains/experience/capabilities/web-storefront.adoc",
        personas: ["PER-SOFTWARE-ENGINEER"],
        policies: ["POL-DEMO-GIT-SOURCE"],
        components: ["CNT-ACME-WEB"],
        contracts: ["CTR-ACME-HTTP"],
        invariants: [
          {
            id: "INV-WEB-HUMAN-READABLE-UI",
            title: "Web Human Readable UI",
            rule: "Always render human-readable titles and labels.",
            test_binding: "tests/test_web.py::test_web_human_readable_ui",
          },
        ],
        body: "Capability details.",
      },
    ],
    personas: [
      {
        id: "PER-SOFTWARE-ENGINEER",
        title: "Software Engineer",
        archetype: "human_architect",
        status: "active",
        path: "specs/personas/software-engineer.adoc",
        capabilities: ["CAP-WEB-STOREFRONT"],
        body: "Engineer persona details.",
      },
    ],
    policies: [
      {
        id: "POL-DEMO-GIT-SOURCE",
        title: "Git as Single Source of Truth",
        category: "governance",
        severity: "blocking",
        status: "active",
        path: "specs/policies/git-source.adoc",
        invariants: [
          {
            id: "INV-GIT-SOURCE",
            title: "Git Source",
            rule: "Specs live in Git.",
            test_binding: "tests/test_core.py::test_git_source",
          },
        ],
        body: "Policy details.",
      },
    ],
    invariants: [
      {
        id: "INV-WEB-HUMAN-READABLE-UI",
        title: "Web Human Readable UI",
        source_id: "CAP-WEB-STOREFRONT",
        source_title: "Interactive Web Storefront",
        source_type: "capability",
        category: "usability",
        rule: "Always render human-readable titles and labels.",
        test_binding: "tests/test_web.py::test_web_human_readable_ui",
        path: "specs/domains/experience/capabilities/web-storefront.adoc",
      },
    ],
    decisions: [
      {
        id: "DEC-WEB-HUMAN-FIRST-PRESENTATION",
        title: "Human-First Presentation Layer",
        status: "accepted",
        source_id: "CNT-ACME-WEB",
        source_type: "container",
        decision: "Render human titles while keeping canonical IDs internal.",
        rationale: "Improves readability for every stakeholder.",
      },
    ],
    deltas: {
      proposed: [
        {
          id: "DLT-2026-140-CLEAN-UI",
          title: "Remove Slugs and Technical Jargon Across the App",
          status: "in_review",
          kind: "enabler",
          motivation: "Keep every screen approachable.",
          branch: "feat/clean-ui",
          path: "specs/deltas/dlt-2026-140-clean-ui.adoc",
          mutations: [
            {
              operation: "modify",
              target: "CNT-ACME-WEB",
              details: "Use human titles everywhere.",
              expect: { frontmatter: { status: "active" } },
            },
          ],
          affected_specs: ["CNT-ACME-WEB", "CAP-WEB-STOREFRONT"],
          body: "Change proposal body.",
        },
      ],
      in_review: [],
      approved: [],
      applied: [],
    },
    explorations: [
      {
        id: "EXP-2026-006",
        title: "Readable Navigation Research",
        status: "in_progress",
        outcome: "adopted",
        question: "How can every view avoid raw identifiers?",
        path: "specs/explorations/exp-2026-006.adoc",
        resulting_deltas: ["DLT-2026-140-CLEAN-UI"],
        body: "Exploration findings.",
      },
    ],
    design_systems: [
      {
        id: "DS-ACME",
        title: "Acme Design System",
        brand_name: "Acme",
        tokens: {
          color: {
            brand: {
              primary: { $value: "#2563eb", $type: "color", $description: "Primary action blue" },
            },
          },
          spacing: {
            card_padding: { $value: "16px", $type: "dimension" },
          },
          border: {
            radius: {
              card_lg: { $value: "12px", $type: "dimension" },
            },
          },
          typography: {
            font_family: {
              sans_body: { $value: "Inter, sans-serif", $type: "fontFamily" },
            },
          },
        },
      },
    ],
    clients: [
      {
        id: "CLT-ACME-WEB",
        title: "Acme Web Application",
        platform: "web_app",
        container: "CNT-ACME-WEB",
        design_system: "DS-ACME",
        screens: ["SCR-ACME-HOME"],
        capabilities: ["CAP-WEB-STOREFRONT"],
      },
    ],
    ui_components: [
      {
        id: "UIC-APP-SHELL",
        title: "Application Shell",
        design_system: "DS-ACME",
        category: "layout_chrome",
        variants: [{ name: "focused-prose", description: "Readable editorial width." }],
        states: [{ name: "mobile_drawer_open", description: "Navigation drawer expanded." }],
        invariants: [
          {
            id: "INV-SHELL-A11Y",
            rule: "Keyboard navigation works.",
            test_binding: "tests/test_web.py::test_shell_a11y",
          },
        ],
      },
    ],
    screens: [
      {
        id: "SCR-ACME-HOME",
        title: "Overview Screen",
        route: "/",
        path: "specs/design/clients/acme-web/screens/acme-home.adoc",
        slots: {
          hero_archetype_banner: "UIC-APP-SHELL",
        },
        states: [{ name: "loading", description: "Loading documentation." }],
        capabilities: ["CAP-WEB-STOREFRONT"],
      },
    ],
    flows: [
      {
        id: "FLOW-WEB-EXPLORATION",
        title: "Storefront Exploration Flow",
        flow_type: "client_internal",
        clients: ["CLT-ACME-WEB"],
        capabilities: ["CAP-WEB-STOREFRONT"],
        mermaid: "sequenceDiagram\n  participant User",
      },
    ],
    drift: {
      is_clean: false,
      changed_files: ["src/acme/web/frontend/src/App.tsx"],
      impacted_components: ["CNT-ACME-WEB"],
      impacted_invariants: ["INV-WEB-HUMAN-READABLE-UI"],
    },
    validation_errors: [
      {
        file: "specs/architecture/containers/web.adoc",
        message: "Missing summary section",
      },
    ],
    edges: [
      { source: "CNT-ACME-WEB", target: "CNT-ACME-API", type: "depends_on" },
    ],
    memories: [
      {
        id: "MEM-REAL-DB-TESTS-A1B2",
        title: "Use real temporary storage in integration tests",
        category: "feedback",
        status: "active",
        summary: "Always construct a real temporary database instead of mocking storage queries.",
        targets: {
          entities: ["CNT-ACME-WEB", "DOM-EXPERIENCE-WEB", "CAP-WEB-STOREFRONT"],
          paths: ["src/acme/web/*.ts"],
        },
        verified_at: "2026-11-24",
        verified_commit: "abc1234",
        expires_at: null,
        promoted_to: null,
        freshness: "fresh",
        freshness_reason: "0 commit(s) on target path(s) since abc1234",
        is_archived: false,
        path: "specs/memory/mem-real-db-tests-a1b2.adoc",
        relative_path: "specs/memory/mem-real-db-tests-a1b2.adoc",
        body: "== Guidance\nUse temporary databases.",
      },
      {
        id: "MEM-RELEASE-FREEZE-C3D4",
        title: "Checkout release window freeze",
        category: "project",
        status: "active",
        summary: "Avoid breaking payment checkout contracts during the Q4 freeze.",
        targets: {
          entities: ["CNT-ACME-API"],
          paths: ["specs/architecture/containers/web.adoc"],
        },
        verified_at: "2026-11-01",
        verified_commit: "def5678",
        expires_at: "2026-11-15",
        promoted_to: null,
        freshness: "stale",
        freshness_reason:
          "missing target entity ID(s): CMP-REMOVED-MODULE and specs/architecture/containers/web.adoc and tests/test_checkout.py",
        is_archived: false,
        path: "specs/memory/mem-release-freeze-c3d4.adoc",
        relative_path: "specs/memory/mem-release-freeze-c3d4.adoc",
        body: "== Guidance\nCoordinate freezes before merging.",
      },
      {
        id: "MEM-PROMOTED-NOTE-E5F6",
        title: "Strict storage security guideline",
        category: "feedback",
        status: "promoted",
        summary: "Promoted into formal governance policy.",
        targets: { entities: [], paths: [] },
        verified_at: "2026-10-15",
        verified_commit: null,
        expires_at: null,
        promoted_to: "POL-ZERO-TRUST",
        freshness: "archived",
        freshness_reason: "",
        is_archived: true,
        path: "specs/memory/archive/mem-promoted-note-e5f6.adoc",
        relative_path: "specs/memory/archive/mem-promoted-note-e5f6.adoc",
        body: "== Guidance\nEnforced formally.",
      },
    ],
    memory_audit: {
      total: 3,
      active: 2,
      draft: 0,
      promoted: 1,
      archived: 1,
      fresh: 1,
      stale: 1,
      expired: 0,
      unverifiable: 0,
      stale_memories: [
        {
          id: "MEM-RELEASE-FREEZE-C3D4",
          title: "Checkout release window freeze",
          reason: "missing target entity ID(s): CMP-REMOVED-MODULE",
        },
      ],
      expired_memories: [],
      unverifiable_memories: [],
      duplicate_pairs: [
        {
          memory_a: "MEM-REAL-DB-TESTS-A1B2",
          memory_b: "MEM-RELEASE-FREEZE-C3D4",
          score: 0.62,
          method: "bm25",
        },
      ],
      spec_overlaps: [
        {
          memory_id: "MEM-REAL-DB-TESTS-A1B2",
          spec_id: "POL-ZERO-TRUST",
          spec_type: "product/policy",
          spec_title: "Zero Trust Access Policy",
          score: 0.64,
          method: "bm25",
        },
      ],
    },
    diagrams: {
      c4_context: 'graph TD\n  SYS["<b>Acme Commerce Platform</b><br/><i>Software System</i>"]',
      c4_containers: 'graph TD\n  CNT_ACME_WEB["<b>Acme Web Application</b><br/><i>Web App</i>"]',
      c4_components: {
        "CNT-ACME-WEB": 'graph TD\n  mod_CMP_WEB_ASSETS["<b>Web Frontend Assets</b>"]',
      },
    },
  };
}

describe("INV-PORTAL-HUMAN-READABLE-UI: presentation formatters", () => {
  test("humanizes tokens, schema types, enums, statuses, tabs, and entity references", () => {
    expect(looksLikeRawEntityId("CNT-ACME-WEB")).toBe(true);
    expect(looksLikeRawEntityId("MEM-REAL-DB-TESTS-A1B2")).toBe(true);
    expect(looksLikeRawEntityId("Acme Web Application")).toBe(false);

    expect(formatTabLabel("memory")).toBe("Team Memory");
    expect(formatSpecType("agent/memory")).toBe("Team Memory");
    expect(formatMemoryCategory("project")).toBe("Project Context");
    expect(formatMemoryFreshness("stale")).toBe("Needs Verification");
    expect(formatCommandCategory("memories")).toBe("Team Memory");

    expect(humanizeToken("mobile_drawer_open")).toBe("Mobile Drawer Open");
    expect(humanizeToken("http_json")).toBe("HTTP JSON");
    expect(humanizeToken("UIC-APP-SHELL")).toBe("App Shell");
    expect(humanizeTokenPath("color.brand.primary_bg")).toBe("Color · Brand · Primary Background");

    expect(formatDisplayTitle("PortalShell")).toBe("Portal Shell");
    expect(formatDisplayTitle("in_review")).toBe("In Review");
    expect(formatDisplayTitle("CNT-ACME-WEB", "CNT-ACME-WEB")).toBe("Acme Web");

    expect(formatTabLabel("deltas")).toBe("Changes");
    expect(formatTabLabel("catalog")).toBe("Catalog");
    expect(formatSpecType("tech/container")).toBe("Container");
    expect(formatSpecType("design/ui-component")).toBe("UI Component");
    expect(formatContainerType("web_app")).toBe("Web App");
    expect(formatProtocol("rest_http")).toBe("REST HTTP");
    expect(formatProtocol("code_interface")).toBe("Code Interface");
    expect(formatProtocol("wsdl")).toBe("WSDL");
    expect(formatProtocol("avro")).toBe("Avro");
    expect(formatProtocol("sql_ddl")).toBe("SQL DDL");
    expect(formatProtocol("trpc")).toBe("tRPC");
    expect(formatProtocol("websocket")).toBe("WebSocket");
    expect(formatProtocol("mcp")).toBe("MCP");
    expect(formatProtocol("other", "NATS JetStream")).toBe("NATS JetStream");
    expect(formatProtocol("http_json")).toBe("HTTP JSON");
    expect(formatStatus("in_review")).toBe("In Review");
    expect(formatDeltaKind("enabler")).toBe("Improvement");
    expect(formatFlowType("client_internal")).toBe("Single-Client Flow");
    expect(formatSeverity("blocking")).toBe("Mandatory");
    expect(formatCommandCategory("deltas")).toBe("Change");
    expect(formatMutationOperation("modify")).toBe("Update");

    const invPfx = "INV";
    expect(
      humanizeInvariantTitle({
        id: `${invPfx}-WEB-HUMAN-READABLE-UI`,
        title: `${invPfx}-WEB-HUMAN-READABLE-UI`,
      })
    ).toBe("Web Human Readable UI");

    const data = buildFixture();
    const index = buildEntityLabelIndex(data);
    expect(resolveEntityTitle("CNT-ACME-WEB", index)).toBe("Acme Web Application");
    expect(resolveEntityTitle("specs/domains/experience/capabilities/web-storefront.adoc", index)).toBe(
      "Interactive Web Storefront"
    );
    expect(resolveEntityTitle("SCR-UNINDEXED-CHECKOUT", index)).toBe("Unindexed Checkout");
    expect(resolveEntityTitle("MEM-REAL-DB-TESTS-A1B2", index)).toBe(
      "Use real temporary storage in integration tests"
    );
    const cleanedReason = formatMemoryFreshnessReason(
      "verified_commit abc1234 unreachable; missing target entity ID(s): ARCH-MISSING, CNT-ACME-WEB and eidos/memory/*.adoc and tests\\test_web.py; TTL expired",
      index
    );
    expect(cleanedReason).not.toMatch(RAW_ENTITY_ID_RE);
    expect(cleanedReason).not.toContain(".adoc");
    expect(cleanedReason).not.toContain("tests/test_");
    expect(cleanedReason).not.toContain("verified_commit");
    expect(cleanedReason).not.toContain("TTL");
    expect(cleanedReason).toContain("Acme Web Application");
    expect(cleanedReason).toContain("last verified revision");
  });
});

describe("INV-PORTAL-HUMAN-READABLE-UI: slug-free and jargon-free SSR across all portal views", () => {
  test("renders zero raw entity IDs, schema type slugs, backticks, raw paths, CSS colons, or banned jargon", () => {
    const data = buildFixture();
    const commandItems: CommandItem[] = [
      {
        id: "CNT-ACME-WEB",
        title: "Acme Web Application",
        subtitle: "Web App • Experience & Storefront",
        searchText: "CNT-ACME-WEB web_app",
        category: "components",
        raw: data.components[0],
      },
      {
        id: "DLT-2026-140-CLEAN-UI",
        title: "Remove Slugs and Technical Jargon Across the App",
        subtitle: "In Review • Improvement",
        searchText: "DLT-2026-140-CLEAN-UI",
        category: "deltas",
        raw: data.deltas.proposed[0],
      },
    ];

    const surfaces: Array<{ name: string; element: React.ReactElement }> = [
      {
        name: "NavigationRail",
        element: React.createElement(NavigationRail, {
          activeTab: "home",
          onTabChange: () => {},
          collapsed: false,
          onToggleCollapse: () => {},
          darkMode: false,
          onToggleDarkMode: () => {},
          onOpenCommand: () => {},
          totalSpecs: data.overview.total_specs,
          deltasCount: data.overview.deltas_count,
          explorationsCount: data.explorations?.length,
          memoriesCount: data.memories?.length,
          memoryAttentionCount: 1,
        }),
      },
      {
        name: "CommandDialog",
        element: React.createElement(CommandDialog, {
          open: true,
          onOpenChange: () => {},
          items: commandItems,
          onSelect: () => {},
        }),
      },
      {
        name: "SpecReader",
        element: React.createElement(SpecReader, {
          spec: data.specs[2],
        }),
      },
      {
        name: "HomeView",
        element: React.createElement(HomeView, {
          data,
          onNavigate: () => {},
          onOpenSpec: () => {},
        }),
      },
      {
        name: "BusinessView",
        element: React.createElement(BusinessView, { data }),
      },
      {
        name: "ArchitectureView",
        element: React.createElement(ArchitectureView, { data }),
      },
      {
        name: "DesignView",
        element: React.createElement(DesignView, { data }),
      },
      {
        name: "DeltasView",
        element: React.createElement(DeltasView, { data }),
      },
      {
        name: "ExplorationsView",
        element: React.createElement(ExplorationsView, { data }),
      },
      {
        name: "MemoryView (populated)",
        element: React.createElement(MemoryView, { data }),
      },
      {
        name: "MemoryView (open detail sheet)",
        element: React.createElement(MemoryView, {
          data,
          initialSelectedId: "MEM-RELEASE-FREEZE-C3D4",
        }),
      },
      {
        name: "MemoryView (empty)",
        element: React.createElement(MemoryView, {
          data: { ...data, memories: [], memory_audit: { ...data.memory_audit!, total: 0, active: 0, fresh: 0, stale: 0, duplicate_pairs: [], spec_overlaps: [] } },
        }),
      },
      {
        name: "EntityMemoriesStrip",
        element: React.createElement(EntityMemoriesStrip, {
          entityId: "CNT-ACME-WEB",
          memories: data.memories,
        }),
      },
      {
        name: "CatalogView",
        element: React.createElement(CatalogView, { data }),
      },
    ];

    for (const { name, element } of surfaces) {
      const html = renderToStaticMarkup(
        React.createElement(EntityLabelProvider, { data }, element)
      );
      const visibleText = extractVisibleText(html);
      const userAttrs = extractUserFacingAttributes(html).join(" | ");
      const combined = `${visibleText} || ${userAttrs}`;

      expect(visibleText, `${name} leaked raw entity ID in text: ${visibleText}`).not.toMatch(
        RAW_ENTITY_ID_RE
      );
      expect(userAttrs, `${name} leaked raw entity ID in tooltip/placeholder: ${userAttrs}`).not.toMatch(
        RAW_ENTITY_ID_RE
      );
      expect(combined, `${name} leaked raw schema type slug`).not.toMatch(RAW_SCHEMA_TYPE_RE);
      expect(visibleText, `${name} leaked literal backtick`).not.toContain("`");
      expect(combined, `${name} leaked raw .adoc path`).not.toContain(".adoc");
      expect(combined, `${name} leaked raw pytest test_binding`).not.toContain("tests/test_");
      expect(visibleText, `${name} leaked CSS state colon`).not.toMatch(
        /:(?:loading|mobile_drawer_open|populated)\b/i
      );

      for (const jargon of BANNED_JARGON) {
        expect(combined, `${name} leaked banned jargon "${jargon}"`).not.toContain(jargon);
      }
    }

    // Spot-check key human-readable labels appear on rendered surfaces
    const navHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, surfaces[0].element)
    );
    expect(navHtml).toContain("Changes");
    expect(navHtml).toContain("Catalog");
    expect(navHtml).toContain("Search documentation");

    const archHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, surfaces[5].element)
    );
    expect(archHtml).toContain("System Context");
    expect(archHtml).toContain("Acme Web Application");
    expect(archHtml).toContain("Web App");
    expect(archHtml).toContain("HTTP JSON");

    const catalogHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, surfaces[surfaces.length - 1].element)
    );
    expect(catalogHtml).toContain("Category");
    expect(catalogHtml).toContain("Needs Attention");
    expect(catalogHtml).toContain("UI Component");
    expect(catalogHtml).not.toContain("uppercase font-mono");

    const memoryHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, surfaces[9].element)
    );
    expect(memoryHtml).toContain("Team Operational Memory");
    expect(memoryHtml).toContain("Use real temporary storage in integration tests");
    expect(memoryHtml).toContain("Needs Verification");
    expect(memoryHtml).toContain("Acme Web Application");

    const emptyMemoryHtml = renderToStaticMarkup(
      React.createElement(EntityLabelProvider, { data }, surfaces[11].element)
    );
    expect(emptyMemoryHtml).toContain("No Team Memories Recorded Yet");
    expect(emptyMemoryHtml).toContain("eidos memory add");
  });
});
