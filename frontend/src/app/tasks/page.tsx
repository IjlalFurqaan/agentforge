"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Button, Card, EmptyState, ErrorNote, Select, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCost, formatTokens, timeAgo } from "@/lib/format";
import type { Task, TaskStatus } from "@/lib/types";

const FILTERS: (TaskStatus | "")[] = ["", "running", "awaiting_approval", "queued", "completed", "failed", "cancelled"];

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [filter, setFilter] = useState<TaskStatus | "">("");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const load = () => api.tasks(filter || undefined).then(setTasks).catch(setError);
    void load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [filter]);

  return (
    <AppShell
      title="Engineering tasks"
      actions={
        <>
          <Select value={filter} onChange={(e) => setFilter(e.target.value as TaskStatus | "")} className="w-44">
            {FILTERS.map((f) => (
              <option key={f} value={f}>
                {f ? f.replace("_", " ") : "All statuses"}
              </option>
            ))}
          </Select>
          <Link href="/tasks/new">
            <Button icon="plus" size="sm">New task</Button>
          </Link>
        </>
      }
    >
      <ErrorNote error={error} />
      <Card>
        {tasks && tasks.length === 0 ? (
          <EmptyState icon="tasks" title="No tasks yet">
            Connect a repository, then describe the change you want the agent team to make.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b">
                  <th className="px-5 py-2.5 font-medium">Task</th>
                  <th className="px-3 py-2.5 font-medium">Status</th>
                  <th className="px-3 py-2.5 font-medium">Stage</th>
                  <th className="px-3 py-2.5 text-right font-medium">Tokens</th>
                  <th className="px-3 py-2.5 text-right font-medium">Cost</th>
                  <th className="px-5 py-2.5 text-right font-medium">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {(tasks ?? []).map((t) => (
                  <tr key={t.id} className="hover:bg-raised/50">
                    <td className="max-w-md px-5 py-3">
                      <Link href={`/tasks/${t.id}`} className="block">
                        <div className="truncate font-medium">{t.title}</div>
                        <div className="truncate text-xs text-muted">{t.repository_name}</div>
                      </Link>
                    </td>
                    <td className="px-3 py-3">
                      <StatusBadge status={t.status} />
                    </td>
                    <td className="px-3 py-3 text-xs capitalize text-muted">{t.current_node?.replace(/_/g, " ") ?? "–"}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-xs">{formatTokens(t.input_tokens + t.output_tokens)}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-xs">{formatCost(t.cost_usd)}</td>
                    <td className="px-5 py-3 text-right text-xs text-muted">{timeAgo(t.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </AppShell>
  );
}
