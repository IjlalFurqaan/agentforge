"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { Button, Card, CardHeader, EmptyState, ErrorNote, Stat, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { agentLabel, AGENTS, formatCost, formatDuration, formatTokens, timeAgo } from "@/lib/format";
import type { AgentName, MetricsSummary, Task } from "@/lib/types";

export default function DashboardPage() {
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const load = () =>
      Promise.all([api.metrics(), api.tasks()])
        .then(([m, t]) => {
          setMetrics(m);
          setTasks(t);
        })
        .catch(setError);
    void load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const active = tasks.filter((t) => ["queued", "running", "awaiting_approval"].includes(t.status));
  const finished = tasks.filter((t) => ["completed", "failed", "cancelled"].includes(t.status)).slice(0, 8);
  const maxDaily = Math.max(1, ...(metrics?.daily.map((d) => d.created) ?? [1]));
  const agentRows = metrics?.agents ?? [];
  const maxCalls = Math.max(1, ...agentRows.map((x) => x.llm_calls));

  return (
    <AppShell
      title="Dashboard"
      actions={
        <Link href="/tasks/new">
          <Button icon="plus" size="sm">New task</Button>
        </Link>
      }
    >
      <ErrorNote error={error} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Active tasks" icon="play" value={metrics?.tasks.active ?? "–"} hint={`${metrics?.tasks.total ?? 0} total`} />
        <Stat
          label="Success rate"
          icon="check"
          value={metrics?.tasks.success_rate != null ? `${Math.round(metrics.tasks.success_rate * 100)}%` : "–"}
          hint={`${metrics?.tasks.by_status.completed ?? 0} completed · ${metrics?.tasks.by_status.failed ?? 0} failed`}
        />
        <Stat label="Model spend" icon="dollar" value={metrics ? formatCost(metrics.usage.cost_usd) : "–"} hint="Across all agents" />
        <Stat
          label="Tokens"
          icon="cpu"
          value={metrics ? formatTokens(metrics.usage.input_tokens + metrics.usage.output_tokens) : "–"}
          hint={metrics ? `${formatTokens(metrics.usage.input_tokens)} in · ${formatTokens(metrics.usage.output_tokens)} out` : undefined}
        />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Active engineering tasks" icon="tasks" subtitle="Live status of running workflows" />
          {active.length === 0 ? (
            <EmptyState icon="spark" title="No active tasks">
              Start a task and the agent team will pick it up immediately.
            </EmptyState>
          ) : (
            <ul className="divide-y">
              {active.map((t) => (
                <li key={t.id}>
                  <Link href={`/tasks/${t.id}`} className="flex items-center gap-4 px-5 py-3 hover:bg-raised/60">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{t.title}</div>
                      <div className="mt-0.5 text-xs text-muted">
                        {t.repository_name} · {t.current_node ? `now: ${t.current_node.replace("_", " ")}` : "waiting for a worker"}
                      </div>
                    </div>
                    {t.pending_approval && (
                      <span className="text-xs font-medium text-amber-600 dark:text-amber-400">Needs your approval</span>
                    )}
                    <StatusBadge status={t.status} />
                    <Icon name="chevron" className="h-4 w-4 text-muted" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader title="Agent activity" icon="network" subtitle="LLM calls and spend per agent" />
          <div className="space-y-3 px-5 py-4">
            {agentRows.length === 0 && <p className="text-xs text-muted">No agent activity yet.</p>}
            {agentRows.map((a) => {
              const meta = AGENTS[a.agent as AgentName];
              const max = maxCalls;
              return (
                <div key={a.agent}>
                  <div className="flex items-center justify-between text-xs">
                    <span className="flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${meta?.dot ?? "bg-slate-400"}`} />
                      {agentLabel(a.agent)}
                    </span>
                    <span className="tabular-nums text-muted">
                      {a.llm_calls} calls · {formatCost(a.cost_usd)}
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 rounded-full bg-raised">
                    <div className={`h-1.5 rounded-full ${meta?.dot ?? "bg-slate-400"}`} style={{ width: `${(a.llm_calls / max) * 100}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Model usage" icon="cpu" subtitle="Tokens, cost and latency per model" />
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b">
                  <th className="px-5 py-2 font-medium">Model</th>
                  <th className="px-3 py-2 text-right font-medium">Calls</th>
                  <th className="px-3 py-2 text-right font-medium">Input</th>
                  <th className="px-3 py-2 text-right font-medium">Output</th>
                  <th className="px-3 py-2 text-right font-medium">Cost</th>
                  <th className="px-3 py-2 text-right font-medium">p95 latency</th>
                  <th className="px-5 py-2 text-right font-medium">Errors</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {(metrics?.models ?? []).map((m) => (
                  <tr key={m.model}>
                    <td className="px-5 py-2.5 font-mono text-xs">{m.model}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{m.calls}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatTokens(m.input_tokens)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatTokens(m.output_tokens)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatCost(m.cost_usd)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{formatDuration(m.p95_latency_ms)}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{m.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(metrics?.models ?? []).length === 0 && <p className="px-5 py-6 text-xs text-muted">No model calls recorded yet.</p>}
          </div>
        </Card>

        <Card>
          <CardHeader title="Throughput" icon="dashboard" subtitle="Tasks created per day (14 days)" />
          <div className="flex h-36 items-end gap-1.5 px-5 py-4">
            {(metrics?.daily ?? []).map((d) => (
              <div key={d.day} className="flex flex-1 flex-col items-center gap-1" title={`${d.day}: ${d.created} created, ${d.completed} completed, ${d.failed} failed`}>
                <div className="w-full rounded-t bg-accent/70" style={{ height: `${(d.created / maxDaily) * 100}%`, minHeight: 2 }} />
              </div>
            ))}
            {(metrics?.daily ?? []).length === 0 && <p className="text-xs text-muted">No tasks in the last 14 days.</p>}
          </div>
          <div className="grid grid-cols-3 border-t text-center text-xs">
            <div className="p-3">
              <div className="font-semibold tabular-nums">{metrics?.sandbox.executions ?? 0}</div>
              <div className="text-muted">sandbox runs</div>
            </div>
            <div className="border-x p-3">
              <div className="font-semibold tabular-nums">{metrics?.sandbox.timeouts ?? 0}</div>
              <div className="text-muted">timeouts</div>
            </div>
            <div className="p-3">
              <div className="font-semibold tabular-nums">{formatDuration(metrics?.sandbox.avg_duration_ms ?? 0)}</div>
              <div className="text-muted">avg run</div>
            </div>
          </div>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title="Recently finished" icon="report" />
          {finished.length === 0 ? (
            <EmptyState icon="report" title="Nothing finished yet" />
          ) : (
            <ul className="divide-y">
              {finished.map((t) => (
                <li key={t.id}>
                  <Link href={`/tasks/${t.id}`} className="flex items-center gap-3 px-5 py-3 hover:bg-raised/60">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm">{t.title}</div>
                      <div className="text-xs text-muted">
                        {t.repository_name} · {t.finished_at ? timeAgo(t.finished_at) : ""} · {formatCost(t.cost_usd)}
                      </div>
                    </div>
                    {t.pr_url && <Icon name="pr" className="h-4 w-4 text-emerald-500" />}
                    <StatusBadge status={t.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Agent communication" icon="message" subtitle="Latest decisions across all tasks" />
          <ul className="max-h-80 divide-y overflow-y-auto scrollbar-thin">
            {(metrics?.recent_activity ?? []).map((e) => (
              <li key={`${e.task_id}-${e.seq}`} className="px-5 py-2.5">
                <Link href={`/tasks/${e.task_id}`} className="block">
                  <div className="flex items-center gap-2 text-[11px] text-muted">
                    <span className={AGENTS[e.agent as AgentName]?.color ?? ""}>{agentLabel(e.agent)}</span>
                    <span>·</span>
                    <span>{e.event_type.replace(/_/g, " ")}</span>
                    <span className="ml-auto">{timeAgo(e.created_at)}</span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs">{e.summary}</p>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </AppShell>
  );
}
