"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";

import { Icon, type IconName } from "@/components/icons";
import { Badge, Card, CardHeader, EmptyState } from "@/components/ui";
import { agentLabel, AGENT_ORDER, AGENTS, clockTime, formatDuration } from "@/lib/format";
import type { AgentEvent, AgentName } from "@/lib/types";

const PIPELINE: { node: string; label: string }[] = [
  { node: "analyze_repo", label: "Analyse" },
  { node: "product_manager", label: "Requirements" },
  { node: "architect", label: "Design" },
  { node: "setup_env", label: "Sandbox" },
  { node: "developer", label: "Implement" },
  { node: "qa", label: "Test" },
  { node: "security", label: "Security" },
  { node: "reviewer", label: "Review" },
  { node: "deployment", label: "Deploy prep" },
  { node: "final_report", label: "Report" },
  { node: "pr_review", label: "PR gate" },
];

const EVENT_ICONS: Record<string, IconName> = {
  node_started: "play",
  node_completed: "check",
  message: "message",
  tool_call: "code",
  sandbox_exec: "terminal",
  llm_call: "cpu",
  route: "branch",
  test_results: "test",
  scan_results: "shield",
  error: "alert",
  retry: "refresh",
  approval_requested: "shield",
  approval_decided: "check",
  awaiting_human: "clock",
  pull_request: "pr",
  report: "report",
  environment: "layers",
  limit_reached: "alert",
};

export function WorkflowProgress({ events, currentNode }: { events: AgentEvent[]; currentNode: string | null }) {
  const completed = new Set(events.filter((e) => e.event_type === "node_completed").map((e) => e.payload.node as string));
  const runs = (node: string) => events.filter((e) => e.event_type === "node_started" && e.payload.node === node).length;
  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-1 scrollbar-thin">
      {PIPELINE.map((step, i) => {
        const active = currentNode === step.node || (currentNode === "plan_review" && step.node === "architect") || (currentNode === "clarify" && step.node === "product_manager");
        const done = completed.has(step.node);
        const count = runs(step.node);
        return (
          <div key={step.node} className="flex items-center gap-1">
            <div
              className={clsx(
                "flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs ring-1 ring-inset",
                active ? "bg-accent/15 text-accent ring-accent/40" : done ? "bg-emerald-500/10 text-emerald-600 ring-emerald-500/30 dark:text-emerald-400" : "text-muted ring-line",
              )}
            >
              {active ? <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-current" /> : done ? <Icon name="check" className="h-3 w-3" /> : null}
              {step.label}
              {count > 1 && <span className="rounded bg-black/10 px-1 text-[10px] dark:bg-white/10">×{count}</span>}
            </div>
            {i < PIPELINE.length - 1 && <div className="h-px w-3 bg-line" />}
          </div>
        );
      })}
    </div>
  );
}

export function AgentBoard({ events }: { events: AgentEvent[] }) {
  const stats = useMemo(() => {
    const out: Record<string, { state: "idle" | "working" | "done" | "error"; last: string; llm: number; tools: number; runs: number }> = {};
    for (const a of AGENT_ORDER) out[a] = { state: "idle", last: "Waiting for work", llm: 0, tools: 0, runs: 0 };
    for (const e of events) {
      if (!e.agent || !out[e.agent]) continue;
      const s = out[e.agent];
      if (e.event_type === "node_started") {
        s.state = "working";
        s.runs += 1;
      } else if (e.event_type === "node_completed") s.state = "done";
      else if (e.event_type === "error") s.state = "error";
      else if (e.event_type === "llm_call") s.llm += 1;
      else if (e.event_type === "tool_call") s.tools += 1;
      if (["message", "tool_call", "test_results", "scan_results", "analysis", "node_completed"].includes(e.event_type)) s.last = e.summary;
    }
    return out;
  }, [events]);

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {AGENT_ORDER.map((agent) => {
        const meta = AGENTS[agent];
        const s = stats[agent];
        return (
          <Card key={agent} className={clsx("p-3.5 transition", s.state === "working" && `ring-2 ${meta.ring}`)}>
            <div className="flex items-center gap-2.5">
              <div className={clsx("flex h-8 w-8 items-center justify-center rounded-md bg-raised text-[11px] font-bold", meta.color)}>{meta.short}</div>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{meta.label}</div>
                <div className="text-[11px] text-muted">
                  {s.runs > 0 ? `${s.runs} run${s.runs > 1 ? "s" : ""} · ${s.llm} LLM · ${s.tools} tools` : "idle"}
                </div>
              </div>
              <Badge tone={s.state === "working" ? "indigo" : s.state === "done" ? "green" : s.state === "error" ? "red" : "slate"}>
                {s.state}
              </Badge>
            </div>
            <p className="mt-2 line-clamp-2 min-h-[2rem] text-xs text-muted">{s.last}</p>
          </Card>
        );
      })}
    </div>
  );
}

type Filter = "all" | "messages" | "tools" | "sandbox" | "llm" | "errors";
const FILTERS: Record<Filter, (e: AgentEvent) => boolean> = {
  all: (e) => e.event_type !== "llm_call",
  messages: (e) => e.event_type === "message" || e.event_type === "route",
  tools: (e) => e.event_type === "tool_call",
  sandbox: (e) => e.event_type === "sandbox_exec" || e.event_type === "test_results" || e.event_type === "environment",
  llm: (e) => e.event_type === "llm_call",
  errors: (e) => ["error", "retry", "limit_reached", "warning", "task_failed"].includes(e.event_type),
};

export function Timeline({ events }: { events: AgentEvent[] }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<number | null>(null);
  const visible = events.filter(FILTERS[filter]).slice().reverse();
  return (
    <Card>
      <CardHeader
        title="Live execution timeline"
        icon="clock"
        subtitle={`${events.length} audit events`}
        action={
          <div className="flex gap-1">
            {(Object.keys(FILTERS) as Filter[]).map((f) => (
              <button key={f} onClick={() => setFilter(f)}
                className={clsx("rounded px-2 py-1 text-[11px] capitalize", filter === f ? "bg-raised font-medium text-ink" : "text-muted hover:text-ink")}>
                {f}
              </button>
            ))}
          </div>
        }
      />
      {visible.length === 0 ? (
        <EmptyState icon="clock" title="No events yet" />
      ) : (
        <ol className="max-h-[560px] divide-y overflow-y-auto scrollbar-thin">
          {visible.map((e) => {
            const meta = e.agent ? AGENTS[e.agent as AgentName] : null;
            const isError = FILTERS.errors(e);
            return (
              <li key={e.seq} className="px-5 py-2.5">
                <button className="flex w-full items-start gap-3 text-left" onClick={() => setOpen(open === e.seq ? null : e.seq)}>
                  <Icon name={EVENT_ICONS[e.event_type] ?? "spark"} className={clsx("mt-0.5 h-4 w-4 shrink-0", isError ? "text-rose-500" : meta?.color ?? "text-muted")} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
                      <span className={clsx("font-medium", meta?.color)}>{agentLabel(e.agent)}</span>
                      <span>{e.event_type.replace(/_/g, " ")}</span>
                      {e.iteration > 0 && <span>iter {e.iteration}</span>}
                      {e.payload.duration_ms != null && <span>{formatDuration(e.payload.duration_ms)}</span>}
                      <span className="ml-auto tabular-nums">{clockTime(e.created_at)}</span>
                    </div>
                    <p className={clsx("mt-0.5 text-[13px]", isError && "text-rose-600 dark:text-rose-400")}>{e.summary}</p>
                  </div>
                </button>
                {open === e.seq && (
                  <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-raised p-3 font-mono text-[11px] leading-relaxed scrollbar-thin">
                    {JSON.stringify(e.payload, null, 2)}
                  </pre>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

export function AgentMessages({ events }: { events: AgentEvent[] }) {
  const messages = events.filter((e) => e.event_type === "message");
  return (
    <Card>
      <CardHeader title="Agent-to-agent communication" icon="message" subtitle="Handoffs, feedback, findings and disputes" />
      {messages.length === 0 ? (
        <EmptyState icon="message" title="No messages yet" />
      ) : (
        <ul className="max-h-[560px] space-y-3 overflow-y-auto p-4 scrollbar-thin">
          {messages.slice().reverse().map((e) => {
            const m = e.payload as { from: AgentName; to: AgentName; kind: string; content: string; id: string };
            const kindTone = m.kind === "finding" || m.kind === "change_request" ? "red" : m.kind === "feedback" ? "amber" : m.kind === "dispute" ? "indigo" : "slate";
            return (
              <li key={e.seq} className="rounded-lg border bg-raised/30 p-3">
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className={clsx("font-medium", AGENTS[m.from]?.color)}>{agentLabel(m.from)}</span>
                  <Icon name="chevron" className="h-3 w-3 text-muted" />
                  <span className={clsx("font-medium", AGENTS[m.to]?.color)}>{agentLabel(m.to)}</span>
                  <Badge tone={kindTone}>{m.kind.replace("_", " ")}</Badge>
                  <span className="ml-auto text-[11px] text-muted">{clockTime(e.created_at)}</span>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-[13px] leading-relaxed">{m.content}</p>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
