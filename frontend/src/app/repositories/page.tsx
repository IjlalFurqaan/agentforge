"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { AppShell } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorNote, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Repository } from "@/lib/types";

type GitHubRepo = { full_name: string; private: boolean; default_branch: string; description: string | null; language: string | null };

export default function RepositoriesPage() {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [github, setGithub] = useState<{ configured: boolean; connected: boolean; login: string | null } | null>(null);
  const [ghRepos, setGhRepos] = useState<GitHubRepo[] | null>(null);
  const [source, setSource] = useState<Repository["source"]>("github");
  const [locator, setLocator] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => api.repositories().then(setRepos).catch(setError);
  useEffect(() => {
    void load();
    api.githubStatus().then((s) => {
      setGithub(s);
      if (s.connected) api.githubRepos().then(setGhRepos).catch(setError);
      else setSource("git_url");
    }).catch(setError);
  }, []);

  async function connectGithub() {
    try {
      const { authorize_url } = await api.githubStart();
      window.location.assign(authorize_url);
    } catch (err) {
      setError(err);
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy("add");
    setError(null);
    try {
      const repo = await api.connectRepository({ source, locator });
      setLocator("");
      await load();
      setBusy(repo.id);
      await api.analyzeRepository(repo.id);
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  async function analyze(id: string) {
    setBusy(id);
    try {
      await api.analyzeRepository(id);
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell title="Repositories">
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6">
          <Card>
            <CardHeader title="GitHub" icon="github" subtitle="OAuth connection used for cloning and pull requests." />
            <div className="space-y-3 p-5 text-sm">
              {github?.connected ? (
                <div className="flex items-center gap-2">
                  <Badge tone="green">connected</Badge> as <span className="font-medium">@{github.login}</span>
                </div>
              ) : github?.configured ? (
                <Button icon="github" onClick={connectGithub}>Connect GitHub</Button>
              ) : (
                <p className="text-xs text-muted">
                  GitHub OAuth isn&apos;t configured on this server. Set <code>GITHUB_CLIENT_ID</code> and <code>GITHUB_CLIENT_SECRET</code>.
                  Public repositories can still be added by https URL.
                </p>
              )}
            </div>
          </Card>
          <Card>
            <CardHeader title="Connect a repository" icon="plus" />
            <form onSubmit={add} className="space-y-4 p-5">
              <Field label="Source">
                <Select value={source} onChange={(e) => setSource(e.target.value as Repository["source"])}>
                  <option value="github" disabled={!github?.connected}>GitHub (OAuth)</option>
                  <option value="git_url">Public git URL (https)</option>
                  <option value="local">Local directory (development only)</option>
                </Select>
              </Field>
              {source === "github" && ghRepos ? (
                <Field label="Repository">
                  <Select value={locator} onChange={(e) => setLocator(e.target.value)} required>
                    <option value="">Select a repository…</option>
                    {ghRepos.map((r) => (
                      <option key={r.full_name} value={r.full_name}>
                        {r.full_name}{r.private ? " (private)" : ""}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : (
                <Field label={source === "local" ? "Absolute path (under LOCAL_REPOS_ROOT)" : "https URL"}>
                  <Input
                    value={locator}
                    onChange={(e) => setLocator(e.target.value)}
                    placeholder={source === "local" ? "/path/to/examples/demo-service" : "https://github.com/org/repo.git"}
                    required
                  />
                </Field>
              )}
              <ErrorNote error={error} />
              <Button type="submit" className="w-full" loading={busy === "add"} icon="repo">
                Connect and analyse
              </Button>
            </form>
          </Card>
        </div>

        <Card className="xl:col-span-2">
          <CardHeader title="Connected repositories" icon="repo" subtitle="Static analysis runs on a fresh clone in an isolated scratch directory." />
          {repos.length === 0 ? (
            <EmptyState icon="repo" title="No repositories connected" />
          ) : (
            <ul className="divide-y">
              {repos.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-4 px-5 py-4">
                  <div className="min-w-0 flex-1">
                    <Link href={`/repositories/${r.id}`} className="flex items-center gap-2 font-medium hover:text-accent">
                      <Icon name="repo" className="h-4 w-4 text-muted" />
                      {r.name}
                    </Link>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                      <Badge>{r.source}</Badge>
                      <span className="inline-flex items-center gap-1"><Icon name="branch" className="h-3 w-3" />{r.default_branch}</span>
                      {r.summary && (
                        <>
                          <span>{r.summary.primary_language}</span>
                          <span>{r.summary.file_count} files</span>
                          <span>{r.summary.test_files} test files</span>
                          {r.summary.frameworks.slice(0, 4).map((f) => <Badge key={f} tone="indigo">{f}</Badge>)}
                        </>
                      )}
                    </div>
                  </div>
                  <span className="text-xs text-muted">{r.analyzed_at ? `analysed ${timeAgo(r.analyzed_at)}` : "not analysed"}</span>
                  <Button size="sm" variant="secondary" icon="refresh" loading={busy === r.id} onClick={() => analyze(r.id)}>
                    Analyse
                  </Button>
                  <Link href={`/repositories/${r.id}`}>
                    <Button size="sm" variant="ghost" icon="network">Architecture</Button>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
