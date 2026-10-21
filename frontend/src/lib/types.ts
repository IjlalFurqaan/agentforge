// API contracts mirrored from backend/app/schemas.

export type Role = "viewer" | "engineer" | "admin";
export type TaskStatus = "queued" | "running" | "awaiting_approval" | "completed" | "failed" | "cancelled";
export type ApprovalKind = "clarification" | "plan" | "pull_request";
export type AgentName =
  | "product_manager"
  | "architect"
  | "developer"
  | "qa"
  | "security"
  | "reviewer"
  | "deployment";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  github_login: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface RepositorySummary {
  primary_language: string | null;
  file_count: number;
  total_lines: number;
  frameworks: string[];
  test_files: number;
}

export interface Repository {
  id: string;
  name: string;
  source: "github" | "git_url" | "local";
  locator: string;
  default_branch: string;
  analyzed_at: string | null;
  created_at: string;
  summary: RepositorySummary | null;
}

export interface GraphNode {
  id: string;
  files?: number;
  lines?: number;
  primary_language?: string | null;
  component?: string;
  language?: string | null;
  fan_in?: number;
  fan_out?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight?: number;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  cycles: string[][];
}

export interface RepoAnalysis {
  repository_id: string;
  commit: string | null;
  analyzed_at: string | null;
  root_name: string;
  file_count: number;
  total_lines: number;
  languages: { language: string; files: number; lines: number; share: number; is_code: boolean }[];
  primary_language: string | null;
  dependencies: { name: string; spec: string; ecosystem: string; dev: boolean; source: string }[];
  frameworks: { name: string; category: string }[];
  testing: { frameworks: string[]; test_files: number; test_command: string[] | null };
  infrastructure: Record<string, string[]>;
  entry_points: string[];
  routes: { method: string; path: string; handler?: string; file: string; lineno: number }[];
  data_models: { name: string; kind: string; file: string; lineno: number }[];
  module_graph: Graph;
  component_graph: Graph;
  hotspots: GraphNode[];
  tree: { path: string; depth: number; files: number }[];
  observations: { severity: string; message: string }[];
}

export interface Approval {
  id: string;
  kind: ApprovalKind;
  status: "pending" | "approved" | "rejected";
  request_payload: Record<string, any>;
  response_payload: Record<string, any> | null;
  decided_at: string | null;
  created_at: string;
}

export interface ModelSelection {
  provider: "anthropic" | "openai" | "ollama";
  model: string;
  effort?: "low" | "medium" | "high" | "xhigh" | "max" | null;
  context_window?: number | null;
}

export interface TaskConfig {
  models: Partial<Record<AgentName | "default", ModelSelection>>;
  limits: {
    max_dev_iterations: number;
    max_arch_revisions: number;
    max_tool_calls: number;
    max_cost_usd: number;
    max_wall_clock_minutes: number;
  };
  approvals: { clarifications: boolean; plan: boolean; pull_request: "require" | "skip" };
}

export interface Task {
  id: string;
  repository_id: string;
  repository_name: string | null;
  title: string;
  description: string;
  config: TaskConfig;
  status: TaskStatus;
  current_node: string | null;
  branch_name: string | null;
  base_commit: string | null;
  pr_url: string | null;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  pending_approval: Approval | null;
}

export interface AgentEvent {
  seq: number;
  agent: AgentName | null;
  event_type: string;
  summary: string;
  payload: Record<string, any>;
  iteration: number;
  created_at: string;
}

export interface Artifact<T = Record<string, any>> {
  id: number;
  kind: string;
  iteration: number;
  agent: string | null;
  content: T;
  created_at: string;
}

export interface Execution {
  id: number;
  agent: string | null;
  purpose: string;
  backend: string;
  command: string[];
  workdir: string;
  network: boolean;
  exit_code: number | null;
  timed_out: boolean;
  duration_ms: number;
  stdout: string;
  stderr: string;
  created_at: string;
}

export interface LLMCall {
  id: number;
  agent: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  latency_ms: number;
  status: string;
  stop_reason: string | null;
  error: string | null;
  created_at: string;
}

export interface MetricsSummary {
  tasks: {
    by_status: Partial<Record<TaskStatus, number>>;
    total: number;
    active: number;
    success_rate: number | null;
    finished: number;
  };
  usage: { cost_usd: number; input_tokens: number; output_tokens: number };
  models: {
    model: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    avg_latency_ms: number;
    p95_latency_ms: number;
    errors: number;
  }[];
  agents: { agent: string; llm_calls: number; tokens: number; cost_usd: number }[];
  sandbox: { executions: number; non_zero_exits: number; timeouts: number; avg_duration_ms: number };
  daily: { day: string; created: number; completed: number; failed: number }[];
  recent_activity: {
    task_id: string;
    seq: number;
    agent: string | null;
    event_type: string;
    summary: string;
    created_at: string;
  }[];
}

export interface ModelsResponse {
  defaults: Record<AgentName, ModelSelection>;
  providers: { provider: string; configured: boolean; models: string[]; base_url?: string }[];
  sandbox_backend: string;
}

export interface WorkflowTopology {
  nodes: { id: string; label: string; agent: AgentName | null }[];
  edges: { source: string; target: string; label: string | null }[];
}
