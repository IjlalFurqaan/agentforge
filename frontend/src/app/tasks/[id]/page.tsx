"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { ApprovalPanel } from "@/components/task/approval-panel";
import { FinalReportView } from "@/components/task/report";
import { CodeReviewView } from "@/components/task/review";
import { AgentBoard, AgentMessages, Timeline, WorkflowProgress } from "@/components/task/workspace";
import { Button, Card, ErrorNote, Spinner, StatusBadge, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCost, formatTokens } from "@/lib/format";
import { streamTaskEvents } from "@/lib/sse";
import type { AgentEvent, Artifact, Execution, LLMCall, Task } from "@/lib/types";

type View = "workspace" | "review" | "report";
const ARTIFACT_KINDS = ["diff", "test_report", "security_report", "review", "final_report", "pull_request"] as const;
type Kind = (typeof ARTIFACT_KINDS)[number];

export default function TaskPage() {
  const { id } = useParams<{ id: string }>();
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [artifacts, setArtifacts] = useState<Partial<Record<Kind, Artifact>>>({});
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [calls, setCalls] = useState<LLMCall[]>([]);
  const [view, setView] = useState<View>("workspace");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadTask = useCallback(() => api.task(id).then(setTask).catch(setError), [id]);
  const loadArtifacts = useCallback(async () => {
    const all = await api.artifacts(id);
    const latest: Partial<Record<Kind, Artifact>> = {};
    for (const a of all) if ((ARTIFACT_KINDS as readonly string[]).includes(a.kind)) latest[a.kind as Kind] = a;
    setArtifacts(latest);
    const [x, c] = await Promise.all([api.executions(id), api.llmCalls(id)]);
    setExecutions(x);
    setCalls(c);
  }, [id]);

  // Debounced refresh of task + artifacts when meaningful events arrive.
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      void loadTask();
      void loadArtifacts().catch(setError);
    }, 600);
  }, [loadTask, loadArtifacts]);

  useEffect(() => {
    void loadTask();
    void loadArtifacts().catch(setError);
    setEvents([]);
    const stop = streamTaskEvents(id, {
      onEvent: (e) => {
        setEvents((prev) => (prev.length && prev[prev.length - 1].seq >= e.seq ? prev : [...prev, e]));
        if (["node_completed", "approval_requested", "approval_decided", "task_completed", "task_failed", "task_cancelled", "pull_request"].includes(e.event_type)) {
          scheduleRefresh();
        }
      },
      onSnapshot: (s) =>
        setTask((t) => (t ? { ...t, status: s.status, current_node: s.current_node, cost_usd: s.cost_usd, input_tokens: s.input_tokens, output_tokens: s.output_tokens } : t)),
      onEnd: scheduleRefresh,
    });
    return () => {
      stop();
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [id, loadTask, loadArtifacts, scheduleRefresh]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      await loadTask();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (!task) {
    return (
      <AppShell title="Task">
        <ErrorNote error={error} />
        {!error && <Spinner />}
      </AppShell>
    );
  }

  const active = ["queued", "running", "awaiting_approval"].includes(task.status);
  return (
    <AppShell
      title={
        <span className="flex items-center gap-3">
          <span className="truncate">{task.title}</span>
          <StatusBadge status={task.status} />
        </span>
      }
      actions={
        <>
          <span className="hidden text-xs text-muted lg:inline">
            {formatTokens(task.input_tokens + task.output_tokens)} tokens · {formatCost(task.cost_usd)}
          </span>
          {task.status === "failed" && (
            <Button size="sm" icon="refresh" loading={busy} onClick={() => act(() => api.resumeTask(id))}>Resume from checkpoint</Button>
          )}
          {active && (
            <Button size="sm" variant="secondary" icon="stop" loading={busy} onClick={() => act(() => api.cancelTask(id))}>Cancel</Button>
          )}
        </>
      }
    >
      <div className="space-y-5">
        <ErrorNote error={error} />
        <Card className="p-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted">
            <span className="inline-flex items-center gap-1.5"><Icon name="repo" className="h-3.5 w-3.5" />{task.repository_name}</span>
            {task.branch_name && <span className="inline-flex items-center gap-1.5 font-mono"><Icon name="branch" className="h-3.5 w-3.5" />{task.branch_name}</span>}
            {task.pr_url && (
              <a className="inline-flex items-center gap-1.5 text-accent hover:underline" href={task.pr_url} target="_blank" rel="noreferrer">
                <Icon name="pr" className="h-3.5 w-3.5" /> Pull request
              </a>
            )}
          </div>
          <p className="mt-2 line-clamp-2 text-sm">{task.description}</p>
          <div className="mt-3">
            <WorkflowProgress events={events} currentNode={task.current_node} />
          </div>
          {task.error && <p className="mt-3 text-sm text-rose-500">{task.error}</p>}
        </Card>

        {task.pending_approval && task.status === "awaiting_approval" && (
          <ApprovalPanel taskId={id} approval={task.pending_approval} onDecided={() => void loadTask()} />
        )}

        <Tabs<View>
          value={view}
          onChange={setView}
          tabs={[
            { id: "workspace", label: "Agent workspace", icon: "network" },
            { id: "review", label: "Code review", icon: "code", count: artifacts.diff?.content.files?.length },
            { id: "report", label: "Final report", icon: "report" },
          ]}
        />

        {view === "workspace" && (
          <div className="space-y-5">
            <AgentBoard events={events} />
            <div className="grid gap-5 xl:grid-cols-5">
              <div className="xl:col-span-3"><Timeline events={events} /></div>
              <div className="xl:col-span-2"><AgentMessages events={events} /></div>
            </div>
          </div>
        )}
        {view === "review" && (
          <CodeReviewView diff={artifacts.diff} tests={artifacts.test_report} security={artifacts.security_report} review={artifacts.review} />
        )}
        {view === "report" && (
          <FinalReportView task={task} report={artifacts.final_report} pr={artifacts.pull_request} executions={executions} calls={calls} />
        )}
      </div>
    </AppShell>
  );
}
