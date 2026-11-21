import * as React from "react";
import type { PortalData, SpecInvariant } from "@/types";

const ACRONYMS: Record<string, string> = {
  ui: "UI",
  ux: "UX",
  api: "API",
  cli: "CLI",
  http: "HTTP",
  https: "HTTPS",
  json: "JSON",
  sql: "SQL",
  ai: "AI",
  kpi: "KPI",
  html: "HTML",
  css: "CSS",
  svg: "SVG",
  url: "URL",
  sdk: "SDK",
  db: "DB",
  id: "ID",
  ast: "AST",
  spa: "SPA",
  yaml: "YAML",
  toc: "TOC",
  xss: "XSS",
  cors: "CORS",
  wal: "WAL",
  rrf: "RRF",
  fts: "FTS",
  onnx: "ONNX",
  ios: "iOS",
  macos: "macOS",
  tui: "TUI",
  wpf: "WPF",
  wcf: "WCF",
  wcag: "WCAG",
  aria: "ARIA",
  bg: "Background",
};

const ENTITY_PREFIX_RE =
  /^(?:SYS|DOM|CAP|POL|PER|INV|DEC|CNT|CMP|CTR|DS|CLT|SCR|UIC|FLOW|DLT|EXP)-/i;

const RAW_ENTITY_ID_RE =
  /^(?:SYS|DOM|CAP|POL|PER|INV|DEC|CNT|CMP|CTR|DS|CLT|SCR|UIC|FLOW|DLT|EXP)-[A-Z0-9_-]+$/i;

export function looksLikeRawEntityId(value?: string | null): boolean {
  const trimmed = String(value || "").trim();
  return Boolean(trimmed) && RAW_ENTITY_ID_RE.test(trimmed);
}

/** Convert snake_case, kebab-case, dotted tokens, CamelCase, or prefixed slugs into clean Title Case. */
export function humanizeToken(raw?: string | null): string {
  const input = String(raw || "").trim();
  if (!input) return "";

  const withoutPrefix = input.replace(ENTITY_PREFIX_RE, "");
  const spaced = withoutPrefix
    .replace(/\.adoc$/i, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/[_./-]+/g, " ")
    .trim();

  if (!spaced) return "";

  return spaced
    .split(/\s+/)
    .map((word) => {
      const low = word.toLowerCase();
      if (ACRONYMS[low]) return ACRONYMS[low];
      if (/^[0-9]+$/.test(word)) return word;
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(" ");
}

/** Normalize a display title (humanizing single-word CamelCase identifiers or raw slugs). */
export function formatDisplayTitle(title?: string | null, fallbackId?: string | null): string {
  const raw = String(title || "").trim();
  if (!raw || looksLikeRawEntityId(raw) || raw.endsWith(".adoc")) {
    return humanizeToken(fallbackId || raw) || "Untitled";
  }
  if (!/\s/.test(raw) && (/[a-z][A-Z]/.test(raw) || /^[a-z0-9_-]+$/.test(raw))) {
    return humanizeToken(raw);
  }
  return raw;
}

const TAB_LABELS: Record<string, string> = {
  home: "Overview",
  business: "Business & Domains",
  architecture: "Architecture",
  deltas: "Changes",
  explorations: "Explorations",
  catalog: "Catalog",
  design: "Design Systems",
};

export function formatTabLabel(tab?: string | null): string {
  const key = String(tab || "").trim().toLowerCase();
  return TAB_LABELS[key] || humanizeToken(tab) || "Overview";
}

const SPEC_TYPE_LABELS: Record<string, string> = {
  "product/system": "System Overview",
  "product/domain": "Domain",
  "product/capability": "Capability",
  "product/policy": "Policy",
  "product/persona": "Persona",
  "product/exploration": "Exploration",
  "tech/architecture": "Architecture",
  "tech/container": "Container",
  "tech/component": "Component",
  "tech/contract": "Interface Contract",
  "design/system": "Design System",
  "design/client": "Client App",
  "design/screen": "Screen",
  "design/ui-component": "UI Component",
  "design/flow": "User Flow",
  "delta/mutation": "Change Proposal",
};

export function formatSpecType(specType?: string | null): string {
  const key = String(specType || "").trim().toLowerCase();
  return SPEC_TYPE_LABELS[key] || humanizeToken(key.split("/").pop() || specType) || "Document";
}

const CONTAINER_TYPE_LABELS: Record<string, string> = {
  web_app: "Web App",
  cli_app: "CLI App",
  frontend_spa: "Web App",
  mobile_app: "Mobile App",
  desktop_app: "Desktop App",
  library: "Library",
  service: "Service",
  microservice: "Microservice",
  database: "Database",
  storage: "Storage",
  plugin: "Plugin",
  extension: "Extension",
  worker: "Background Worker",
  background_worker: "Background Worker",
  gateway: "API Gateway",
  api_gateway: "API Gateway",
  engine: "Engine",
  package: "Package",
};

export function formatContainerType(raw?: string | null): string {
  const key = String(raw || "").trim().toLowerCase();
  return CONTAINER_TYPE_LABELS[key] || humanizeToken(raw) || "Container";
}

const PROTOCOL_LABELS: Record<string, string> = {
  rest_http: "REST HTTP",
  cli_args: "CLI",
  python_protocol: "Python API",
  openapi: "OpenAPI",
  graphql: "GraphQL",
  protobuf: "Protobuf",
  asyncapi: "AsyncAPI",
  json_schema: "JSON Schema",
  json_rpc_stdio: "JSON-RPC Stdio",
};

export function formatProtocol(
  raw?: string | null,
  customProtocol?: string | null
): string {
  const key = String(raw || "").trim().toLowerCase();
  if (key === "other") {
    const custom = String(customProtocol || "").trim();
    if (custom) return custom;
  }
  return PROTOCOL_LABELS[key] || humanizeToken(raw) || "API";
}

const STATUS_LABELS: Record<string, string> = {
  proposed: "Proposed",
  in_review: "In Review",
  approved: "Approved",
  applied: "Applied",
  rejected: "Rejected",
  archived: "Archived",
  in_progress: "In Progress",
  adopted: "Adopted",
  discarded: "Discarded",
  pivoted: "Pivoted",
  active: "Active",
  draft: "Draft",
  planned: "Planned",
  deprecated: "Deprecated",
  retired: "Retired",
};

export function formatStatus(raw?: string | null): string {
  const key = String(raw || "").trim().toLowerCase();
  return STATUS_LABELS[key] || humanizeToken(raw) || "Active";
}

const DELTA_KIND_LABELS: Record<string, string> = {
  feature: "Feature",
  enabler: "Improvement",
  bug: "Bug Fix",
};

export function formatDeltaKind(raw?: string | null): string {
  const key = String(raw || "").trim().toLowerCase();
  return DELTA_KIND_LABELS[key] || humanizeToken(raw) || "Change";
}

const FLOW_TYPE_LABELS: Record<string, string> = {
  client_internal: "Single-Client Flow",
  cross_client: "Cross-Client Flow",
  omnichannel: "Cross-Client Flow",
};

export function formatFlowType(raw?: string | null): string {
  const key = String(raw || "").trim().toLowerCase();
  return FLOW_TYPE_LABELS[key] || humanizeToken(raw) || "User Flow";
}

const SEVERITY_LABELS: Record<string, string> = {
  blocking: "Mandatory",
  warning: "Warning",
  advisory: "Advisory",
};

export function formatSeverity(raw?: string | null): string {
  const key = String(raw || "").trim().toLowerCase();
  return SEVERITY_LABELS[key] || humanizeToken(raw) || "";
}

const COMMAND_CATEGORY_LABELS: Record<string, string> = {
  components: "Architecture",
  contracts: "Contract",
  decisions: "Decision",
  design: "Design",
  invariants: "Rule",
  deltas: "Change",
  specs: "Document",
  policies: "Policy",
  domains: "Domain",
  capabilities: "Capability",
  personas: "Persona",
  explorations: "Exploration",
};

export function formatCommandCategory(category?: string | null): string {
  const key = String(category || "").trim().toLowerCase();
  return COMMAND_CATEGORY_LABELS[key] || humanizeToken(category) || "Document";
}

export function formatMutationOperation(op?: string | null): string {
  const key = String(op || "").trim().toLowerCase();
  switch (key) {
    case "add":
      return "Add";
    case "modify":
      return "Update";
    case "remove":
      return "Remove";
    case "deprecate":
      return "Deprecate";
    default:
      return humanizeToken(op) || "Update";
  }
}

export function humanizeInvariantTitle(
  inv?: Pick<SpecInvariant, "id" | "title" | "category"> | null
): string {
  if (!inv) return "System Rule";
  const rawTitle = String(inv.title || "").trim();
  if (rawTitle && !looksLikeRawEntityId(rawTitle)) {
    return rawTitle;
  }
  const fromId = humanizeToken(inv.id);
  if (fromId) return fromId;
  return humanizeToken(inv.category) || "System Rule";
}

export function humanizeTokenPath(path?: string | null): string {
  const raw = String(path || "").trim();
  if (!raw) return "";
  return raw
    .split(".")
    .filter(Boolean)
    .map((seg) => humanizeToken(seg))
    .join(" · ");
}

function normalizePathKey(path?: string | null): string {
  return String(path || "")
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .trim();
}

function registerLabel(
  map: Map<string, string>,
  id?: string | null,
  title?: string | null,
  path?: string | null
): void {
  const label = formatDisplayTitle(title, id);
  if (!label) return;
  const cleanId = String(id || "").trim();
  if (cleanId) {
    map.set(cleanId, label);
    map.set(cleanId.toUpperCase(), label);
  }
  const cleanPath = normalizePathKey(path);
  if (cleanPath) {
    map.set(cleanPath, label);
    const base = cleanPath.split("/").pop() || "";
    if (base) {
      map.set(base, label);
      const stem = base.replace(/\.adoc$/i, "");
      if (stem) map.set(stem, label);
    }
  }
}

/** Build a lookup index mapping canonical IDs, repo-relative paths, and stems to human titles. */
export function buildEntityLabelIndex(data?: PortalData | null): Map<string, string> {
  const map = new Map<string, string>();
  if (!data) return map;

  if (data.overview?.system_id) {
    registerLabel(map, data.overview.system_id, data.overview.system_title);
  }
  for (const s of data.specs || []) {
    registerLabel(map, s.id, s.title, s.path);
  }
  for (const c of data.components || []) {
    registerLabel(map, c.id, c.title);
    for (const ic of c.internal_components || []) {
      registerLabel(map, ic.id, ic.title, ic.spec_path);
    }
  }
  for (const ct of data.contracts || []) {
    registerLabel(map, ct.id, ct.title);
  }
  for (const d of data.domains || []) {
    registerLabel(map, d.id, d.title, d.path);
  }
  for (const cap of data.capabilities || []) {
    registerLabel(map, cap.id, cap.title, cap.path);
  }
  for (const per of data.personas || []) {
    registerLabel(map, per.id, per.title, per.path);
  }
  for (const pol of data.policies || []) {
    registerLabel(map, pol.id, pol.title, pol.path);
  }
  for (const inv of data.invariants || []) {
    registerLabel(map, inv.id, humanizeInvariantTitle(inv));
  }
  for (const dec of data.decisions || []) {
    registerLabel(map, dec.id, dec.title);
  }
  for (const ds of data.design_systems || []) {
    registerLabel(map, ds.id, ds.title || ds.brand_name);
  }
  for (const clt of data.clients || []) {
    registerLabel(map, clt.id, clt.title);
  }
  for (const scr of data.screens || []) {
    registerLabel(map, scr.id, scr.title, scr.path);
  }
  for (const uic of data.ui_components || []) {
    registerLabel(map, uic.id, uic.title);
  }
  for (const fl of data.flows || []) {
    registerLabel(map, fl.id, fl.title);
  }
  for (const exp of data.explorations || []) {
    registerLabel(map, exp.id, exp.title, exp.path);
  }
  for (const list of Object.values(data.deltas || {})) {
    if (!Array.isArray(list)) continue;
    for (const d of list) {
      registerLabel(map, d.id, d.title, d.path);
    }
  }
  return map;
}

/** Resolve any entity ID, repo-relative path, or stem to its human-readable title. */
export function resolveEntityTitle(
  ref?: string | null,
  index?: Map<string, string>,
  fallback?: string
): string {
  const raw = String(ref || "").trim();
  if (!raw || raw.toUpperCase() === "N/A") return fallback || "";

  const stripped = raw.split("(")[0].trim();
  const normPath = normalizePathKey(stripped);
  const base = normPath.split("/").pop() || "";
  const stem = base.replace(/\.adoc$/i, "");

  if (index) {
    const direct =
      index.get(raw) ||
      index.get(stripped) ||
      index.get(stripped.toUpperCase()) ||
      index.get(normPath) ||
      index.get(base) ||
      index.get(stem);
    if (direct) return direct;
  }

  if (looksLikeRawEntityId(stripped) || stripped.endsWith(".adoc") || stripped.includes("/")) {
    return humanizeToken(stem || stripped) || fallback || "";
  }
  return formatDisplayTitle(stripped, stripped) || fallback || "";
}

const EntityLabelContext = React.createContext<Map<string, string>>(new Map());

export function EntityLabelProvider({
  data,
  children,
}: {
  data?: PortalData | null;
  children: React.ReactNode;
}) {
  const index = React.useMemo(() => buildEntityLabelIndex(data), [data]);
  return React.createElement(EntityLabelContext.Provider, { value: index }, children);
}

export function useEntityLabels(): Map<string, string> {
  return React.useContext(EntityLabelContext);
}
