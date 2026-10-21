// Typed API client. The access token lives in memory only; the refresh token is an httpOnly cookie
// scoped to /api/auth, so a page reload silently re-authenticates through /auth/refresh.

import type {
  AgentEvent,
  Approval,
  Artifact,
  Execution,
  LLMCall,
  MetricsSummary,
  ModelsResponse,
  RepoAnalysis,
  Repository,
  Task,
  TaskConfig,
  TokenResponse,
  User,
  WorkflowTopology,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<(user: User | null) => void>();

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

export function onAuthChange(fn: (user: User | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setSession(token: TokenResponse | null) {
  accessToken = token?.access_token ?? null;
  listeners.forEach((fn) => fn(token?.user ?? null));
}

export function getAccessToken(): string | null {
  return accessToken;
}

export async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(`${API_URL}/auth/refresh`, { method: "POST", credentials: "include" });
        if (!res.ok) {
          setSession(null);
          return false;
        }
        setSession((await res.json()) as TokenResponse);
        return true;
      } catch {
        setSession(null);
        return false;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refreshSession()) return request<T>(path, init, false);
  }
  if (!res.ok) {
    let message = res.statusText;
    let details: unknown;
    try {
      const body = await res.json();
      message = body?.error?.message ?? (Array.isArray(body?.detail) ? body.detail[0]?.msg : body?.detail) ?? message;
      details = body?.error?.details ?? body?.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message, details);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  async login(email: string, password: string): Promise<User> {
    const token = await request<TokenResponse>("/auth/login", json({ email, password }));
    setSession(token);
    return token.user;
  },
  register: (email: string, password: string, full_name: string) =>
    request<User>("/auth/register", json({ email, password, full_name })),
  async logout(): Promise<void> {
    try {
      await request<void>("/auth/logout", { method: "POST" });
    } finally {
      setSession(null);
    }
  },
  me: () => request<User>("/auth/me"),

  repositories: () => request<Repository[]>("/repositories"),
  repository: (id: string) => request<Repository>(`/repositories/${id}`),
  connectRepository: (body: { source: Repository["source"]; locator: string; default_branch?: string }) =>
    request<Repository>("/repositories", json(body)),
  analyzeRepository: (id: string) => request<Repository>(`/repositories/${id}/analyze`, { method: "POST" }),
  analysis: (id: string) => request<RepoAnalysis>(`/repositories/${id}/analysis`),

  githubStatus: () =>
    request<{ configured: boolean; connected: boolean; login: string | null; scopes: string | null }>("/github/status"),
  githubStart: () => request<{ authorize_url: string }>("/github/oauth/start", { method: "POST" }),
  githubRepos: () =>
    request<{ full_name: string; private: boolean; default_branch: string; description: string | null; language: string | null }[]>(
      "/github/repos",
    ),

  tasks: (status?: string) => request<Task[]>(`/tasks${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  task: (id: string) => request<Task>(`/tasks/${id}`),
  createTask: (body: { repository_id: string; title: string; description: string; config: TaskConfig }) =>
    request<Task>("/tasks", json(body)),
  cancelTask: (id: string) => request<Task>(`/tasks/${id}/cancel`, { method: "POST" }),
  resumeTask: (id: string) => request<Task>(`/tasks/${id}/resume`, { method: "POST" }),
  events: (id: string, after = 0) => request<AgentEvent[]>(`/tasks/${id}/events?after=${after}`),
  artifacts: (id: string, kind?: string) =>
    request<Artifact[]>(`/tasks/${id}/artifacts${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`),
  latestArtifact: <T = Record<string, any>>(id: string, kind: string) =>
    request<Artifact<T>>(`/tasks/${id}/artifacts/${kind}/latest`),
  executions: (id: string) => request<Execution[]>(`/tasks/${id}/executions`),
  llmCalls: (id: string) => request<LLMCall[]>(`/tasks/${id}/llm-calls`),
  approvals: (id: string) => request<Approval[]>(`/tasks/${id}/approvals`),
  decide: (
    taskId: string,
    approvalId: string,
    body: { approved: boolean; comment?: string; answers?: { question: string; answer: string }[] },
  ) => request<Approval>(`/tasks/${taskId}/approvals/${approvalId}`, json(body)),
  workflow: () => request<WorkflowTopology>("/tasks/workflow"),

  metrics: () => request<MetricsSummary>("/metrics/summary"),
  models: () => request<ModelsResponse>("/models"),
};
