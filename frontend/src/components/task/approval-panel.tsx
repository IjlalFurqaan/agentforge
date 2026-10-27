"use client";

import { useState } from "react";

import { Icon } from "@/components/icons";
import { Badge, Button, Card, ErrorNote, Field, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import type { Approval } from "@/lib/types";

export function ApprovalPanel({ taskId, approval, onDecided }: { taskId: string; approval: Approval; onDecided: () => void }) {
  const payload = approval.request_payload;
  const [comment, setComment] = useState("");
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function decide(approved: boolean) {
    setBusy(approved ? "approve" : "reject");
    setError(null);
    try {
      const questions = (payload.questions ?? []) as { question: string }[];
      await api.decide(taskId, approval.id, {
        approved,
        comment,
        answers: approval.kind === "clarification" ? questions.map((q, i) => ({ question: q.question, answer: answers[i] ?? "" })) : [],
      });
      onDecided();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="border-amber-500/40 bg-amber-500/[0.04]">
      <div className="flex items-start gap-3 border-b border-amber-500/20 px-5 py-4">
        <Icon name="shield" className="mt-0.5 h-5 w-5 text-amber-500" />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">{payload.title ?? "Human approval required"}</h3>
            <Badge tone="amber">{approval.kind.replace("_", " ")}</Badge>
          </div>
          <p className="mt-0.5 text-xs text-muted">The workflow is paused at a checkpoint and resumes as soon as you decide.</p>
        </div>
      </div>
      <div className="space-y-4 p-5">
        {approval.kind === "clarification" &&
          (payload.questions as { question: string; why_it_matters: string; assumption: string }[]).map((q, i) => (
            <Field key={q.question} label={q.question} hint={`Why it matters: ${q.why_it_matters} · Default assumption: ${q.assumption}`}>
              <Textarea rows={2} value={answers[i] ?? ""} onChange={(e) => setAnswers({ ...answers, [i]: e.target.value })} placeholder={q.assumption} />
            </Field>
          ))}

        {approval.kind === "plan" && (
          <div className="space-y-3 text-sm">
            <p>{payload.design?.overview}</p>
            <div>
              <div className="mb-1 text-xs font-semibold text-muted">Affected files</div>
              <ul className="space-y-1">
                {(payload.design?.affected_files ?? []).map((f: { path: string; action: string; purpose: string }) => (
                  <li key={f.path} className="flex items-center gap-2 text-xs">
                    <Badge tone={f.action === "create" ? "green" : f.action === "delete" ? "red" : "sky"}>{f.action}</Badge>
                    <code className="font-mono">{f.path}</code>
                    <span className="truncate text-muted">{f.purpose}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold text-muted">Risks</div>
              <ul className="list-disc space-y-0.5 pl-5 text-xs">
                {(payload.design?.risks ?? []).map((r: { risk: string; severity: string; mitigation: string }) => (
                  <li key={r.risk}>
                    <span className="font-medium">[{r.severity}]</span> {r.risk}. Mitigation: {r.mitigation}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold text-muted">Acceptance criteria</div>
              <ul className="space-y-0.5 text-xs">
                {(payload.requirements?.acceptance_criteria ?? []).map((c: { id: string; description: string }) => (
                  <li key={c.id}><span className="font-mono text-muted">{c.id}</span> {c.description}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {approval.kind === "pull_request" && (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge tone={payload.status === "ready_for_review" ? "green" : "amber"}>{String(payload.status).replace(/_/g, " ")}</Badge>
              <span className="inline-flex items-center gap-1 font-mono"><Icon name="branch" className="h-3 w-3" />{payload.branch}</span>
              <span className="text-muted">→ {payload.base}</span>
            </div>
            <div className="text-xs text-muted">{(payload.files ?? []).length} files changed. Review the diff in the Code Review tab.</div>
            {(payload.limitations ?? []).length > 0 && (
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-700 dark:text-amber-400">
                {(payload.limitations as string[]).map((l) => <li key={l}>{l}</li>)}
              </ul>
            )}
            {!payload.can_push && (
              <p className="rounded-md bg-raised p-2 text-xs text-muted">
                This repository isn&apos;t connected through GitHub. Approving records the decision; the branch stays in the task workspace.
              </p>
            )}
            <p className="text-xs text-muted">Approving pushes the agent branch and opens a <strong>draft</strong> pull request. It is never merged automatically.</p>
          </div>
        )}

        <Field label={approval.kind === "clarification" ? "Additional context (optional)" : "Comment (sent to the agents if you reject)"}>
          <Textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
        <ErrorNote error={error} />
        <div className="flex gap-2">
          <Button variant="success" icon="check" loading={busy === "approve"} onClick={() => decide(true)}>
            {approval.kind === "clarification" ? "Submit answers" : "Approve"}
          </Button>
          {approval.kind !== "clarification" && (
            <Button variant="secondary" icon="x" loading={busy === "reject"} onClick={() => decide(false)}>
              {approval.kind === "plan" ? "Request a revised plan" : "Reject"}
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}
