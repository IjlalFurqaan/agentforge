"use client";

import { Icon } from "@/components/icons";
import { Badge, Card, CardHeader, EmptyState, Mono } from "@/components/ui";
import { agentLabel, formatCost, formatDuration, formatTokens } from "@/lib/format";
import type { Artifact, Execution, LLMCall, Task } from "@/lib/types";

type Any = Record<string, any>;

export function FinalReportView({ task, report, pr, executions, calls }: { task: Task; report?: Artifact; pr?: Artifact; executions: Execution[]; calls: LLMCall[] }) {
  if (!report) {
    return (
      <Card>
        <EmptyState icon="report" title="The final report is generated after code review and deployment checks" />
      </Card>
    );
  }
  const r: Any = report.content;
  const prInfo: Any | undefined = pr?.content;
  const tests: Any = r.tests ?? {};
  return (
    <div className="space-y-6">
      <Card>
        <div className="flex flex-wrap items-start gap-4 p-5">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <Badge tone={r.status === "ready_for_review" ? "green" : "amber"}>{String(r.status).replace(/_/g, " ")}</Badge>
              <span className="text-xs text-muted">sandbox: {r.sandbox_backend}</span>
            </div>
            <h2 className="mt-2 text-lg font-semibold">{r.title}</h2>
            <p className="mt-1 text-sm text-muted">{r.summary}</p>
          </div>
          <div className="text-right text-xs">
            {task.pr_url || prInfo?.url ? (
              <a href={task.pr_url ?? prInfo?.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 font-medium text-accent hover:underline">
                <Icon name="pr" className="h-4 w-4" /> Open pull request
              </a>
            ) : prInfo ? (
              <span className="text-muted">PR: {prInfo.status.replace("_", " ")}. {prInfo.reason}</span>
            ) : (
              <span className="text-muted">PR awaiting human approval</span>
            )}
            <div className="mt-1 font-mono text-muted">{r.branch} @ {(r.commit ?? "").slice(0, 10)}</div>
          </div>
        </div>
        <div className="grid grid-cols-2 border-t text-center text-xs md:grid-cols-5">
          {[
            ["Developer iterations", r.iterations?.developer],
            ["Redesigns", r.iterations?.architecture_revisions],
            ["Tests passed", tests.executed ? `${tests.passed}/${tests.total}` : "not run"],
            ["Blocking security issues", (r.security?.blocking ?? []).length],
            ["Cost", formatCost(task.cost_usd)],
          ].map(([label, value], i) => (
            <div key={String(label)} className={`p-3 ${i ? "border-l" : ""}`}>
              <div className="text-base font-semibold tabular-nums">{String(value ?? "–")}</div>
              <div className="text-muted">{label}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title="Requirements completed" icon="tasks" subtitle="QA verdicts per acceptance criterion" />
          <ul className="divide-y text-sm">
            {(r.requirements?.acceptance ?? []).map((a: Any) => (
              <li key={a.id} className="flex gap-3 px-5 py-2.5">
                <Badge tone={a.status === "verified" ? "green" : a.status === "failed" ? "red" : "slate"}>{a.status.replace("_", " ")}</Badge>
                <div>
                  <span className="font-mono text-xs text-muted">{a.id}</span> {a.description}
                  {a.evidence && <div className="text-xs text-muted">Evidence: {a.evidence}</div>}
                </div>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Remaining limitations" icon="alert" subtitle="Recorded automatically. Nothing is hidden." />
          {(r.limitations ?? []).length === 0 ? (
            <EmptyState icon="check" title="No limitations recorded" />
          ) : (
            <ul className="list-disc space-y-1.5 py-4 pl-10 pr-5 text-sm">
              {(r.limitations as string[]).map((l) => <li key={l}>{l}</li>)}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Files modified" icon="code" />
          <ul className="divide-y font-mono text-xs">
            {(r.files_modified ?? []).map((f: Any) => (
              <li key={f.path} className="flex items-center gap-3 px-5 py-2">
                <Badge tone={f.status === "A" ? "green" : f.status === "D" ? "red" : "sky"}>{f.status}</Badge>
                <span className="truncate">{f.path}</span>
                <span className="ml-auto tabular-nums"><span className="text-emerald-500">+{f.additions}</span> <span className="text-rose-500">−{f.deletions}</span></span>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Implementation decisions" icon="spark" />
          <ul className="list-disc space-y-1.5 py-4 pl-10 pr-5 text-sm">
            {(r.implementation?.decisions ?? []).map((d: string) => <li key={d}>{d}</li>)}
          </ul>
          {r.deployment?.deployment_instructions && (
            <div className="border-t px-5 py-4 text-sm">
              <div className="mb-1 text-xs font-semibold text-muted">Deployment instructions (prepared, not executed)</div>
              <ol className="list-decimal space-y-1 pl-5">
                {(r.deployment.deployment_instructions as string[]).map((s) => <li key={s}>{s}</li>)}
              </ol>
              {(r.deployment.environment_variables ?? []).length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {(r.deployment.environment_variables as Any[]).map((v) => (
                    <Badge key={v.name} tone={v.secret ? "amber" : "slate"}>{v.name}{v.required ? "*" : ""}</Badge>
                  ))}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      <Card>
        <CardHeader title="Workflow decisions" icon="branch" subtitle="Every routing decision the orchestrator made" />
        <ol className="divide-y text-sm">
          {(r.route_log ?? []).map((step: Any, i: number) => (
            <li key={i} className="flex items-center gap-3 px-5 py-2">
              <span className="w-6 text-right font-mono text-xs text-muted">{i + 1}</span>
              <Mono>{step.from}</Mono>
              <Icon name="chevron" className="h-3 w-3 text-muted" />
              <Mono>{step.to === "__end__" ? "END" : step.to}</Mono>
              <span className="text-xs text-muted">{step.reason}</span>
            </li>
          ))}
        </ol>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title="Sandbox executions" icon="terminal" subtitle={`${executions.length} commands, all run in isolation`} />
          <ul className="max-h-96 divide-y overflow-y-auto text-xs scrollbar-thin">
            {executions.map((x) => (
              <li key={x.id} className="px-5 py-2">
                <div className="flex items-center gap-2">
                  <Badge tone={x.timed_out || x.exit_code ? "red" : "green"}>{x.timed_out ? "timeout" : `exit ${x.exit_code}`}</Badge>
                  <span className="truncate font-mono">{x.command.join(" ")}</span>
                </div>
                <div className="mt-0.5 text-muted">
                  {agentLabel(x.agent)} · {x.purpose} · {x.backend} · network {x.network ? "on" : "off"} · {formatDuration(x.duration_ms)}
                </div>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Model calls" icon="cpu" subtitle={`${calls.length} calls · ${formatTokens(task.input_tokens)} in / ${formatTokens(task.output_tokens)} out`} />
          <ul className="max-h-96 divide-y overflow-y-auto text-xs scrollbar-thin">
            {calls.map((c) => (
              <li key={c.id} className="flex items-center gap-3 px-5 py-2">
                <span className="w-28 truncate">{agentLabel(c.agent)}</span>
                <span className="flex-1 truncate font-mono text-muted">{c.provider}/{c.model}</span>
                <span className="tabular-nums">{formatTokens(c.input_tokens)}/{formatTokens(c.output_tokens)}</span>
                <span className="w-16 text-right tabular-nums">{formatDuration(c.latency_ms)}</span>
                <Badge tone={c.status === "ok" ? "green" : "red"}>{c.status}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
