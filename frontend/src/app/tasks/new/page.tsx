"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { AppShell } from "@/components/app-shell";
import { Button, Card, CardHeader, ErrorNote, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { AGENT_ORDER, AGENTS } from "@/lib/format";
import type { AgentName, ModelSelection, ModelsResponse, Repository, TaskConfig } from "@/lib/types";

const DEMO_REQUEST =
  "Implement a secure authentication module with JWT access tokens, refresh token rotation, Google OAuth login, " +
  "role-based access control (admin, member) protecting the existing project and item endpoints, PostgreSQL-compatible " +
  "SQLAlchemy models, and comprehensive automated tests. Google OAuth must be mockable in tests.";

type Provider = ModelSelection["provider"];

export default function NewTaskPage() {
  const router = useRouter();
  const [repos, setRepos] = useState<Repository[]>([]);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [repositoryId, setRepositoryId] = useState("");
  const [title, setTitle] = useState("Secure authentication module");
  const [description, setDescription] = useState(DEMO_REQUEST);
  const [defaultModel, setDefaultModel] = useState<ModelSelection | null>(null);
  const [overrides, setOverrides] = useState<Partial<Record<AgentName, ModelSelection>>>({});
  const [limits, setLimits] = useState<TaskConfig["limits"]>({
    max_dev_iterations: 4,
    max_arch_revisions: 2,
    max_tool_calls: 40,
    max_cost_usd: 5,
    max_wall_clock_minutes: 60,
  });
  const [approvals, setApprovals] = useState<TaskConfig["approvals"]>({ clarifications: true, plan: true, pull_request: "require" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.repositories().then((r) => {
      setRepos(r);
      if (r[0]) setRepositoryId(r[0].id);
    }).catch(setError);
    api.models().then((m) => {
      setModels(m);
      setDefaultModel(m.defaults.developer);
    }).catch(setError);
  }, []);

  const modelOptions = useMemo(() => {
    const opts: { provider: Provider; model: string }[] = [];
    for (const p of models?.providers ?? []) for (const m of p.models) opts.push({ provider: p.provider as Provider, model: m });
    if (defaultModel && !opts.some((o) => o.provider === defaultModel.provider && o.model === defaultModel.model)) opts.unshift(defaultModel);
    return opts;
  }, [models, defaultModel]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const modelConfig: TaskConfig["models"] = { ...overrides };
      if (defaultModel) modelConfig.default = defaultModel;
      const task = await api.createTask({ repository_id: repositoryId, title, description, config: { models: modelConfig, limits, approvals } });
      router.push(`/tasks/${task.id}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  const encode = (m: { provider: string; model: string }) => `${m.provider}::${m.model}`;
  const decode = (v: string): ModelSelection => {
    const [provider, ...rest] = v.split("::");
    return { provider: provider as Provider, model: rest.join("::") };
  };

  return (
    <AppShell title="New engineering task">
      <form onSubmit={submit} className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader title="What should the team build?" icon="spark" subtitle="The Product Manager agent turns this into requirements and acceptance criteria." />
            <div className="space-y-4 p-5">
              <Field label="Repository">
                {repos.length === 0 ? (
                  <p className="text-sm text-muted">
                    No repositories yet. <Link href="/repositories" className="text-accent hover:underline">Connect one</Link> first.
                  </p>
                ) : (
                  <Select value={repositoryId} onChange={(e) => setRepositoryId(e.target.value)} required>
                    {repos.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name} ({r.source}, {r.default_branch})
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Title">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} required minLength={3} maxLength={300} />
              </Field>
              <Field label="Task description" hint="Be specific about behaviour, constraints and what 'done' means.">
                <Textarea value={description} onChange={(e) => setDescription(e.target.value)} required minLength={10} rows={8} />
              </Field>
            </div>
          </Card>

          <Card>
            <CardHeader title="Model configuration" icon="cpu" subtitle="One default model for the team, with optional per-agent overrides." />
            <div className="space-y-4 p-5">
              <Field label="Default model" hint={models ? `Sandbox backend on this server: ${models.sandbox_backend}` : undefined}>
                <Select value={defaultModel ? encode(defaultModel) : ""} onChange={(e) => setDefaultModel(decode(e.target.value))}>
                  {modelOptions.map((o) => (
                    <option key={encode(o)} value={encode(o)}>
                      {o.provider} / {o.model}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                {AGENT_ORDER.map((agent) => (
                  <Field key={agent} label={AGENTS[agent].label}>
                    <Select
                      value={overrides[agent] ? encode(overrides[agent]!) : ""}
                      onChange={(e) =>
                        setOverrides((prev) => {
                          const next = { ...prev };
                          if (e.target.value) next[agent] = decode(e.target.value);
                          else delete next[agent];
                          return next;
                        })
                      }
                    >
                      <option value="">Use default</option>
                      {modelOptions.map((o) => (
                        <option key={encode(o)} value={encode(o)}>
                          {o.provider} / {o.model}
                        </option>
                      ))}
                    </Select>
                  </Field>
                ))}
              </div>
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Approval preferences" icon="shield" subtitle="Where the workflow pauses for a human." />
            <div className="space-y-3 p-5 text-sm">
              {(
                [
                  ["clarifications", "Ask me blocking clarification questions"],
                  ["plan", "Require my approval of the technical plan"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 accent-[rgb(var(--accent))]"
                    checked={approvals[key]}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setApprovals((prev) => (key === "plan" ? { ...prev, plan: checked } : { ...prev, clarifications: checked }));
                    }}
                  />
                  {label}
                </label>
              ))}
              <Field label="Pull request">
                <Select value={approvals.pull_request} onChange={(e) => setApprovals({ ...approvals, pull_request: e.target.value as "require" | "skip" })}>
                  <option value="require">Prepare a draft PR after my approval</option>
                  <option value="skip">Do not open a PR (report only)</option>
                </Select>
              </Field>
              <p className="rounded-md bg-raised p-3 text-xs text-muted">
                AgentForge never merges, never pushes to protected branches and never deploys. Those always stay with you.
              </p>
            </div>
          </Card>
          <Card>
            <CardHeader title="Execution limits" icon="clock" subtitle="Hard caps that stop runaway loops." />
            <div className="grid grid-cols-2 gap-3 p-5">
              {(
                [
                  ["max_dev_iterations", "Dev iterations", 1, 10, 1],
                  ["max_arch_revisions", "Redesigns", 0, 5, 1],
                  ["max_tool_calls", "Tool calls / turn", 5, 200, 1],
                  ["max_cost_usd", "Budget (USD)", 0, 500, 0.5],
                  ["max_wall_clock_minutes", "Wall clock (min)", 5, 480, 5],
                ] as const
              ).map(([key, label, min, max, step]) => (
                <Field key={key} label={label}>
                  <Input
                    type="number"
                    min={min}
                    max={max}
                    step={step}
                    value={limits[key]}
                    onChange={(e) => {
                      const value = Number(e.target.value);
                      setLimits((prev) => ({ ...prev, [key]: value }) as TaskConfig["limits"]);
                    }}
                  />
                </Field>
              ))}
            </div>
          </Card>
          <ErrorNote error={error} />
          <Button type="submit" className="w-full" icon="play" loading={busy} disabled={!repositoryId}>
            Start autonomous run
          </Button>
        </div>
      </form>
    </AppShell>
  );
}
