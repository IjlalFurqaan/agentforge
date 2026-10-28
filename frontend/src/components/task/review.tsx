"use client";

import { DiffViewer, type InlineComment } from "@/components/diff-viewer";
import { Badge, Card, CardHeader, EmptyState, Mono } from "@/components/ui";
import { formatDuration } from "@/lib/format";
import type { Artifact } from "@/lib/types";

type Any = Record<string, any>;

const sevTone = (s: string) =>
  (s === "critical" || s === "high" || s === "blocker" ? "red" : s === "medium" || s === "major" ? "amber" : "slate") as "red" | "amber" | "slate";

export function CodeReviewView({ diff, tests, security, review }: { diff?: Artifact; tests?: Artifact; security?: Artifact; review?: Artifact }) {
  const comments: InlineComment[] = ((review?.content.comments ?? []) as Any[]).map((c) => ({
    file: c.file, line: c.line, severity: c.severity, body: c.body, author: "Code Reviewer",
  }));
  const run: Any = tests?.content.run ?? {};
  const analysis: Any | null = tests?.content.analysis ?? null;
  const sec: Any = security?.content ?? {};
  return (
    <div className="grid gap-6 2xl:grid-cols-3">
      <div className="2xl:col-span-2">
        <Card>
          <CardHeader
            title="Generated changes"
            icon="code"
            subtitle={diff ? `${(diff.content.files ?? []).length} files · iteration ${diff.iteration} · diff from git, not from the model` : undefined}
          />
          <div className="p-4">{diff ? <DiffViewer diff={diff.content.diff ?? ""} comments={comments} /> : <EmptyState icon="code" title="No diff yet" />}</div>
        </Card>
      </div>
      <div className="space-y-6">
        <Card>
          <CardHeader title="Test results" icon="test" subtitle={run.executed ? `Parsed from ${run.source === "junit" ? "JUnit XML" : "exit code"} · sandbox` : undefined} />
          {!tests ? (
            <EmptyState icon="test" title="Tests have not run yet" />
          ) : !run.executed ? (
            <p className="p-5 text-sm text-amber-600">Not executed: {run.reason}</p>
          ) : (
            <div className="space-y-3 p-5 text-sm">
              <div className="grid grid-cols-4 gap-2 text-center">
                {[["passed", "text-emerald-500"], ["failed", "text-rose-500"], ["errors", "text-rose-500"], ["skipped", "text-muted"]].map(([k, c]) => (
                  <div key={k} className="rounded-md bg-raised p-2">
                    <div className={`text-lg font-semibold tabular-nums ${c}`}>{run[k] ?? 0}</div>
                    <div className="text-[11px] text-muted">{k}</div>
                  </div>
                ))}
              </div>
              <div className="text-xs text-muted">
                <Mono>{(run.command ?? []).join(" ")}</Mono> · exit {String(run.exit_code)} · {formatDuration(Math.round((run.duration_s ?? 0) * 1000))}
              </div>
              {(run.failures ?? []).map((f: Any) => (
                <div key={`${f.classname}.${f.name}`} className="rounded-md border border-rose-500/30 bg-rose-500/5 p-2.5 text-xs">
                  <div className="font-mono font-medium">{f.classname}::{f.name}</div>
                  <div className="mt-1 text-muted">{f.message}</div>
                </div>
              ))}
              {analysis && (
                <div>
                  <div className="mb-1 text-xs font-semibold text-muted">Acceptance criteria (QA verdicts)</div>
                  <ul className="space-y-1 text-xs">
                    {(analysis.acceptance ?? []).map((a: Any) => (
                      <li key={a.criterion_id} className="flex gap-2">
                        <Badge tone={a.status === "verified" ? "green" : a.status === "failed" ? "red" : "slate"}>{a.status}</Badge>
                        <span><span className="font-mono">{a.criterion_id}</span> {a.evidence}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Security findings" icon="shield" subtitle={security ? `Scanners: ${(sec.tools_run ?? []).join(", ")}` : undefined} />
          {!security ? (
            <EmptyState icon="shield" title="Security review pending" />
          ) : (
            <div className="space-y-3 p-5 text-sm">
              {Object.entries(sec.tools_skipped ?? {}).map(([tool, why]) => (
                <p key={tool} className="text-xs text-muted">Skipped {tool}: {String(why)}</p>
              ))}
              <div className="text-xs">
                Blocking issues: <span className={(sec.blocking ?? []).length ? "font-semibold text-rose-500" : "font-semibold text-emerald-500"}>{(sec.blocking ?? []).length}</span>
                {" · "}raw scanner findings: {(sec.tool_findings ?? []).length}
              </div>
              {[...((sec.review?.findings ?? []) as Any[]).map((f) => ({ ...f, source: "security agent" })),
                ...((sec.tool_findings ?? []) as Any[]).map((f) => ({ ...f, title: f.rule, description: f.message, source: f.tool }))].map((f: Any, i: number) => (
                <div key={`${f.source}-${f.id ?? i}`} className="rounded-md border p-2.5 text-xs">
                  <div className="flex items-center gap-2">
                    <Badge tone={sevTone(f.severity)}>{f.severity}</Badge>
                    <span className="font-medium">{f.title}</span>
                    <span className="ml-auto text-muted">{f.source}</span>
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-muted">{f.file}:{f.line} {f.cwe}</div>
                  <p className="mt-1">{f.description}</p>
                  {f.remediation && <p className="mt-1 text-muted">Fix: {f.remediation}</p>}
                </div>
              ))}
              {sec.review?.summary && <p className="text-xs text-muted">{sec.review.summary}</p>}
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Review comments" icon="message" subtitle={review ? `Verdict: ${review.content.verdict}` : undefined} />
          {!review ? (
            <EmptyState icon="message" title="Code review pending" />
          ) : (
            <div className="space-y-2 p-5 text-sm">
              <p className="text-xs">{review.content.summary}</p>
              {((review.content.comments ?? []) as Any[]).map((c, i) => (
                <div key={i} className="rounded-md border p-2.5 text-xs">
                  <div className="flex items-center gap-2">
                    <Badge tone={sevTone(c.severity)}>{c.severity}</Badge>
                    <span className="text-muted">{c.category}</span>
                    <span className="ml-auto font-mono text-[11px] text-muted">{c.file}:{c.line}</span>
                  </div>
                  <p className="mt-1">{c.body}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
