/**
 * Data contracts for the Eidos Living Web Portal (CTR-EIDOS-PORTAL).
 */

export interface SpecOverview {
  total_specs: number;
  valid_specs: number;
  is_clean: boolean;
  components_count: number;
  deltas_count: number;
  design_systems_count: number;
  ui_components_count: number;
  screens_count: number;
  clients_count: number;
  flows_count: number;
  explorations_count: number;
  system_id: string;
  system_title: string;
  repo_status?: string;
  active_branch?: string;
}

export interface SpecComponent {
  id: string;
  name: string;
  container_type: string;
  runtime: string;
  package_path: string;
  internal_components: Array<{
    id: string;
    name: string;
    responsibility: string;
    source_path?: string;
  }>;
  dependencies: {
    internal_containers?: string[];
    external_services?: string[];
  };
  contracts: {
    provides?: string[];
    consumes?: string[];
  };
  description?: string;
}

export interface SpecContract {
  id: string;
  title: string;
  protocol: string;
  owning_component: string;
  specification_file: string;
  description?: string;
}

export interface SpecDomain {
  id: string;
  name: string;
  mission: string;
  capabilities?: string[];
}

export interface SpecCapability {
  id: string;
  name: string;
  domain: string;
  summary: string;
  invariants?: Array<{
    id: string;
    rule: string;
    test_binding?: string;
  }>;
}

export interface SpecPersona {
  id: string;
  name: string;
  role: string;
  archetype: string;
  goals?: string[];
}

export interface SpecPolicy {
  id: string;
  title: string;
  rule: string;
  category: string;
  test_binding?: string;
}

export interface SpecInvariant {
  id: string;
  title?: string;
  rule: string;
  category: string;
  source_type: "policy" | "capability";
  test_binding?: string;
}

export interface SpecDelta {
  id: string;
  title: string;
  kind: string;
  status: "proposed" | "in_review" | "approved" | "applied";
  motivation?: string;
  mutations?: Array<{
    action: string;
    target_component?: string;
    target_capability?: string;
    target_domain?: string;
    target_screen?: string;
    details?: string;
  }>;
}

export interface SpecUIComponent {
  id: string;
  name: string;
  design_system: string;
  component_type: string;
  tokens_used?: string[];
  states?: string[];
  description?: string;
}

export interface SpecClient {
  id: string;
  name: string;
  client_type: string;
  target_audience: string;
  supported_screens?: string[];
  flows?: string[];
  design_system?: string;
  description?: string;
}

export interface SpecFlow {
  id: string;
  name: string;
  client: string;
  trigger: string;
  steps?: Array<{
    step: number;
    action: string;
    target_screen?: string;
  }>;
  description?: string;
}

export interface SpecScreen {
  id: string;
  name: string;
  client: string;
  route: string;
  layout: string;
  components?: string[];
  description?: string;
}

export interface SpecExploration {
  id: string;
  title: string;
  status: "proposed" | "in_progress" | "adopted" | "discarded";
  hypothesis: string;
  questions_to_answer?: string[];
  exit_criteria?: {
    max_duration?: string;
    benchmark?: string;
  };
  decision?: {
    outcome: string;
    resulting_delta_id?: string;
    learnings?: string;
  };
}

export interface SpecItem {
  id: string;
  title: string;
  type: string;
  schema: string;
  path: string;
  filename: string;
  frontmatter: Record<string, any>;
  body: string;
  is_valid: boolean;
  errors?: string[];
}

export interface PortalData {
  overview: SpecOverview;
  components: SpecComponent[];
  contracts: SpecContract[];
  domains: SpecDomain[];
  capabilities: SpecCapability[];
  personas: SpecPersona[];
  policies: SpecPolicy[];
  invariants: SpecInvariant[];
  deltas: {
    proposed: SpecDelta[];
    in_review: SpecDelta[];
    approved: SpecDelta[];
    applied: SpecDelta[];
  };
  design_systems: Array<{
    id: string;
    name: string;
    version: string;
    tokens_file: string;
  }>;
  ui_components: SpecUIComponent[];
  clients: SpecClient[];
  flows: SpecFlow[];
  screens: SpecScreen[];
  explorations: SpecExploration[];
  diagrams: {
    c4_context: string;
    c4_containers: string;
    c4_components: Record<string, string>;
  };
  specs: SpecItem[];
}

declare global {
  interface Window {
    __EIDOS_DATA__?: PortalData;
  }
}
