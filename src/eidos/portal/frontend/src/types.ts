/**
 * Data contracts for the Eidos Living Web Portal.
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
  domains_count?: number;
  capabilities_count?: number;
  policies_count?: number;
  personas_count?: number;
  invariants_count?: number;
  decisions_count?: number;
  system_id: string;
  system_title: string;
  repo_status?: string;
  active_branch?: string;
}

export interface SpecDecision {
  id: string;
  title: string;
  decision: string;
  rationale: string;
  rejected_alternatives?: Array<{
    option: string;
    reason_rejected: string;
  }>;
  graduated_from?: string;
  source_entity?: string;
  source_type?: string;
}

export interface SpecComponent {
  id: string;
  name: string;
  container_type: string;
  runtime: string;
  package_path: string;
  domain_binding?: string;
  tier?: string;
  tier_name?: string;
  responsibility?: string;
  summary?: string;
  internal_components: Array<{
    id: string;
    name: string;
    responsibility: string;
    source_path?: string | string[];
    spec_path?: string;
    file_path?: string;
    is_promoted?: boolean;
    status?: string;
    description?: string;
    invariants?: Array<{
      id: string;
      rule: string;
      test_binding?: string;
    }>;
    decisions?: SpecDecision[];
  }>;
  dependencies: {
    internal_containers?: string[];
    internal_components?: string[];
    external_services?: string[];
  };
  contracts: {
    provides?: string[];
    consumes?: string[];
    requires?: string[];
  };
  quality_attributes?: Record<string, any>;
  decisions?: SpecDecision[];
  invariants?: Array<{
    id: string;
    rule: string;
    test_binding?: string;
  }>;
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
  classification?: string;
  capabilities?: string[];
  boundaries?: {
    responsibilities?: string[];
    out_of_scope?: string[];
  };
  technical_mapping?: {
    containers?: string[];
    components?: string[];
    dependencies?: string[];
  };
  body?: string;
  path?: string;
}

export interface SpecCapability {
  id: string;
  name: string;
  title?: string;
  domain: string;
  summary: string;
  status?: string;
  version?: string;
  business?: {
    personas?: string[];
    jtbd?: string;
    policies?: string[];
  };
  technical?: {
    containers?: string[];
    components?: string[];
    contracts?: {
      interfaces?: string[];
    };
    invariants?: Array<{
      id: string;
      rule: string;
      test_binding?: string;
    }>;
  };
  invariants?: Array<{
    id: string;
    rule: string;
    test_binding?: string;
  }>;
  body?: string;
  path?: string;
}

export interface SpecPersona {
  id: string;
  name: string;
  role: string;
  role_type?: string;
  archetype: string;
  goals?: string[];
  pain_points?: string[];
  interaction_modes?: string[];
  body?: string;
  path?: string;
}

export interface SpecPolicy {
  id: string;
  title: string;
  rule: string;
  category: string;
  test_binding?: string;
  enforcement?: {
    mechanism?: string;
    severity?: string;
  };
  body?: string;
  path?: string;
}

export interface SpecInvariant {
  id: string;
  title?: string;
  rule: string;
  category: string;
  source_type: "policy" | "capability" | "container" | "component" | string;
  test_binding?: string;
}

export interface SpecMutation {
  operation: string;
  target: string;
  parent?: string;
  title?: string;
  details?: string;
  expect?: Record<string, any>;
}

export interface SpecDelta {
  id: string;
  title: string;
  kind: string;
  status: "proposed" | "in_review" | "approved" | "applied" | "rejected" | string;
  motivation?: string;
  mutations?: SpecMutation[];
  affects?: {
    containers?: string[];
    components?: string[];
    capabilities?: string[];
    contracts?: string[];
    domains?: string[];
    policies?: string[];
  };
  blast_radius?: {
    impacted_personas?: string[];
    impacted_components?: string[];
    impacted_containers?: string[];
    impacted_design_systems?: string[];
    impacted_screens?: string[];
    invariants_to_enforce?: string[];
    impact_level?: string;
    domains?: string[];
    components?: string[];
    [key: string]: any;
  };
  vcs?: { branch?: string };
  body?: string;
  path?: string;
}

export interface SpecUIVariant {
  name: string;
  description?: string;
}

export interface SpecUIAccessibility {
  wcag_level?: string;
  aria_role?: string;
  keyboard_nav?: string;
}

export interface SpecUIComponent {
  id: string;
  name?: string;
  title?: string;
  design_system: string;
  category?: string;
  component_type?: string;
  status?: string;
  variants?: SpecUIVariant[];
  states?: string[];
  accessibility?: SpecUIAccessibility;
  test_bindings?: Record<string, string>;
  tokens_used?: string[];
  description?: string;
  body?: string;
}

export interface SpecClient {
  id: string;
  name?: string;
  title?: string;
  platform?: string;
  client_type?: string;
  target_audience?: string;
  design_system?: string;
  container?: string;
  capabilities?: string[];
  supported_screens?: string[];
  screens?: string[];
  flows?: string[];
  description?: string;
  body?: string;
}

export interface SpecFlowStep {
  step: number;
  title?: string;
  action: string;
  outcome?: string;
  screen?: string;
  client?: string;
  target_screen?: string;
}

export interface SpecFlow {
  id: string;
  name?: string;
  title?: string;
  client?: string | null;
  clients?: string[];
  trigger?: string;
  status?: string;
  flow_type?: string;
  steps?: SpecFlowStep[];
  capabilities?: string[];
  mermaid?: string;
  description?: string;
  body?: string;
}

export interface SpecScreenState {
  name: string;
  description?: string;
}

export interface SpecScreen {
  id: string;
  name?: string;
  title?: string;
  client?: string;
  route?: string;
  layout?: string;
  system_ref?: string;
  components?: string[];
  states?: SpecScreenState[] | string[];
  slots?: Record<string, string>;
  capabilities?: string[];
  mermaid?: string;
  description?: string;
  body?: string;
}

export interface SpecDesignSystemTokenItem {
  $value: string;
  $type?: string;
  $description?: string;
}

export interface SpecDesignSystemTokens {
  color?: {
    brand?: Record<string, SpecDesignSystemTokenItem>;
    surface?: Record<string, SpecDesignSystemTokenItem>;
    border?: Record<string, SpecDesignSystemTokenItem>;
    feedback?: Record<string, SpecDesignSystemTokenItem>;
    [key: string]: any;
  };
  spacing?: Record<string, SpecDesignSystemTokenItem>;
  border?: {
    radius?: Record<string, SpecDesignSystemTokenItem>;
    [key: string]: any;
  };
  typography?: {
    font_family?: Record<string, SpecDesignSystemTokenItem>;
    [key: string]: any;
  };
  [key: string]: any;
}

export interface SpecDesignSystem {
  id: string;
  title?: string;
  name?: string;
  client?: string;
  brand_name?: string;
  extends?: string | null;
  version?: string;
  tokens_file?: string;
  tokens?: SpecDesignSystemTokens;
}

export interface SpecExploration {
  id: string;
  title: string;
  status: "proposed" | "in_progress" | "adopted" | "discarded" | string;
  hypothesis: string;
  questions_to_answer?: string[];
  spikes_to_run?: Array<{ name: string; target: string }>;
  exit_criteria?:
    | {
        max_duration?: string;
        benchmark?: string;
      }
    | string[];
  findings?: string;
  recommendation?: string;
  resulting_deltas?: string[];
  decision?: {
    outcome?: string;
    resulting_delta_id?: string;
    resulting_deltas?: string[];
    learnings?: string;
    findings?: string;
    recommendation?: string;
  };
  body?: string;
  path?: string;
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
  decisions?: SpecDecision[];
  deltas: {
    proposed: SpecDelta[];
    in_review: SpecDelta[];
    approved: SpecDelta[];
    applied: SpecDelta[];
    rejected?: SpecDelta[];
    [status: string]: SpecDelta[] | undefined;
  };
  design_systems: SpecDesignSystem[];
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
