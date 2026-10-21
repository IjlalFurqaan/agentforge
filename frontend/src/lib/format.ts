import type { AgentName, TaskStatus } from "./types";

export const AGENTS: Record<AgentName, { label: string; short: string; color: string; ring: string; dot: string }> = {
  product_manager: { label: "Product Manager", short: "PM", color: "text-violet-500", ring: "ring-violet-500/40", dot: "bg-violet-500" },
  architect: { label: "Architect", short: "AR", color: "text-sky-500", ring: "ring-sky-500/40", dot: "bg-sky-500" },
  developer: { label: "Developer", short: "DV", color: "text-emerald-500", ring: "ring-emerald-500/40", dot: "bg-emerald-500" },
  qa: { label: "QA Engineer", short: "QA", color: "text-amber-500", ring: "ring-amber-500/40", dot: "bg-amber-500" },
  security: { label: "Security", short: "SE", color: "text-rose-500", ring: "ring-rose-500/40", dot: "bg-rose-500" },
  reviewer: { label: "Code Reviewer", short: "CR", color: "text-cyan-500", ring: "ring-cyan-500/40", dot: "bg-cyan-500" },
  deployment: { label: "Deployment", short: "DE", color: "text-orange-500", ring: "ring-orange-500/40", dot: "bg-orange-500" },
};
export const AGENT_ORDER = Object.keys(AGENTS) as AgentName[];

export function agentLabel(agent: string | null | undefined): string {
  if (!agent) return "Platform";
  return AGENTS[agent as AgentName]?.label ?? agent;
}

export const STATUS_STYLES: Record<TaskStatus, string> = {
  queued: "bg-slate-500/15 text-slate-500 ring-slate-500/30",
  running: "bg-indigo-500/15 text-indigo-500 ring-indigo-500/30",
  awaiting_approval: "bg-amber-500/15 text-amber-600 ring-amber-500/30 dark:text-amber-400",
  completed: "bg-emerald-500/15 text-emerald-600 ring-emerald-500/30 dark:text-emerald-400",
  failed: "bg-rose-500/15 text-rose-600 ring-rose-500/30 dark:text-rose-400",
  cancelled: "bg-slate-500/15 text-slate-500 ring-slate-500/30",
};

export function statusLabel(status: TaskStatus): string {
  return status === "awaiting_approval" ? "awaiting approval" : status;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "$0.00";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function timeAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
