"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { DependencyGraph } from "@/components/dependency-graph";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorNote, Mono, Spinner, Tabs } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { RepoAnalysis, Repository } from "@/lib/types";

type View = "components" | "modules" | "structure" | "dependencies" | "api";

export default function ArchitecturePage() {
  const { id } = useParams<{ id: string }>();
  const [repo, setRepo] = useState<Repository | null>(null);
  const [analysis, setAnalysis] = useState<RepoAnalysis | null>(null);
  const [view, setView] = useState<View>("components");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setRepo(await api.repository(id));
    try {
      setAnalysis(await api.analysis(id));
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
  };
  useEffect(() => {
    load().catch(setError);
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function reanalyze() {
    setBusy(true);
    try {
      await api.analyzeRepository(id);
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const severityTone = (s: string) => (s === "high" ? "red" : s === "medium" ? "amber" : "slate") as "red" | "amber" | "slate";

  return (
    <AppShell
      title={repo ? `Architecture · ${repo.name}` : "Architecture"}
      actions={<Button size="sm" variant="secondary" icon="refresh" loading={busy} onClick={reanalyze}>Re-analyse</Button>}
    >
      <ErrorNote error={error} />
      {!repo && !error && <Spinner />}
      {repo && !analysis && (
        <Card>
          <EmptyState icon="network" title="Not analysed yet">
            <Button size="sm" className="mt-3" onClick={reanalyze} loading={busy}>Run analysis</Button>
          </EmptyState>
        </Card>
      )}
      {analysis && (
        <div className="space-y-6">
          <div className="grid gap-4 md:grid-cols-4">
            <Card className="p-4">
              <div className="text-xs text-muted">Primary language</div>
              <div className="mt-1 text-lg font-semibold">{analysis.primary_language ?? "–"}</div>
              <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-raised">
                {analysis.languages.filter((l) => l.is_code).map((l, i) => (
                  <div key={l.language} title={`${l.language} ${(l.share * 100).toFixed(1)}%`} style={{ width: `${l.share * 100}%` }}
                    className={["bg-indigo-500", "bg-emerald-500", "bg-amber-500", "bg-rose-500", "bg-sky-500"][i % 5]} />
                ))}
              </div>
            </Card>
            <Card className="p-4">
              <div className="text-xs text-muted">Size</div>
              <div className="mt-1 text-lg font-semibold tabular-nums">{analysis.file_count} files</div>
              <div className="text-xs text-muted">{analysis.total_lines.toLocaleString()} lines</div>
            </Card>
            <Card className="p-4">
              <div className="text-xs text-muted">Tests</div>
              <div className="mt-1 text-lg font-semibold tabular-nums">{analysis.testing.test_files} files</div>
              <div className="truncate text-xs text-muted">{analysis.testing.test_command?.join(" ") ?? "no test command"}</div>
            </Card>
            <Card className="p-4">
              <div className="text-xs text-muted">Frameworks</div>
              <div className="mt-2 flex flex-wrap gap-1">
                {analysis.frameworks.map((f) => <Badge key={f.name} tone="indigo">{f.name}</Badge>)}
              </div>
            </Card>
          </div>

          {analysis.observations.length > 0 && (
            <Card>
              <CardHeader title="Observations" icon="alert" />
              <ul className="divide-y text-sm">
                {analysis.observations.map((o) => (
                  <li key={o.message} className="flex items-center gap-3 px-5 py-2.5">
                    <Badge tone={severityTone(o.severity)}>{o.severity}</Badge>
                    {o.message}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card>
            <div className="px-5 pt-2">
              <Tabs<View>
                value={view}
                onChange={setView}
                tabs={[
                  { id: "components", label: "Service dependencies", icon: "network", count: analysis.component_graph.nodes.length },
                  { id: "modules", label: "Module graph", icon: "layers", count: analysis.module_graph.nodes.length },
                  { id: "structure", label: "Structure", icon: "repo" },
                  { id: "dependencies", label: "Packages", icon: "cpu", count: analysis.dependencies.length },
                  { id: "api", label: "API & data", icon: "code", count: analysis.routes.length },
                ]}
              />
            </div>
            <div className="p-5">
              {view === "components" && (
                <DependencyGraph nodes={analysis.component_graph.nodes} edges={analysis.component_graph.edges} cycles={analysis.component_graph.cycles} />
              )}
              {view === "modules" && (
                <>
                  {analysis.module_graph.cycles.length > 0 && (
                    <p className="mb-3 text-xs text-rose-500">{analysis.module_graph.cycles.length} import cycle(s) highlighted in red.</p>
                  )}
                  <DependencyGraph nodes={analysis.module_graph.nodes.filter((n) => (n.fan_in ?? 0) + (n.fan_out ?? 0) > 0).slice(0, 80)}
                    edges={analysis.module_graph.edges} cycles={analysis.module_graph.cycles} />
                </>
              )}
              {view === "structure" && (
                <ul className="font-mono text-xs">
                  {analysis.tree.map((t) => (
                    <li key={t.path} className="flex justify-between py-0.5" style={{ paddingLeft: t.depth * 16 }}>
                      <span>{t.path.split("/").pop()}/</span>
                      <span className="text-muted">{t.files} files</span>
                    </li>
                  ))}
                  <li className="mt-3 font-sans text-xs text-muted">Entry points: {analysis.entry_points.join(", ") || "none detected"}</li>
                </ul>
              )}
              {view === "dependencies" && (
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted">
                    <tr className="border-b"><th className="py-2">Package</th><th>Version</th><th>Ecosystem</th><th>Scope</th><th>Declared in</th></tr>
                  </thead>
                  <tbody className="divide-y">
                    {analysis.dependencies.map((d) => (
                      <tr key={`${d.ecosystem}:${d.name}`}>
                        <td className="py-2 font-mono text-xs">{d.name}</td>
                        <td className="font-mono text-xs text-muted">{d.spec || "unpinned"}</td>
                        <td className="text-xs">{d.ecosystem}</td>
                        <td>{d.dev ? <Badge>dev</Badge> : <Badge tone="green">runtime</Badge>}</td>
                        <td className="text-xs text-muted">{d.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {view === "api" && (
                <div className="grid gap-6 lg:grid-cols-2">
                  <div>
                    <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">HTTP routes</h4>
                    <ul className="space-y-1.5 text-sm">
                      {analysis.routes.map((r) => (
                        <li key={`${r.method}${r.path}${r.file}`} className="flex items-center gap-2">
                          <Badge tone={r.method === "GET" ? "sky" : r.method === "DELETE" ? "red" : "green"}>{r.method}</Badge>
                          <Mono>{r.path}</Mono>
                          <span className="truncate text-xs text-muted">{r.file}:{r.lineno}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Data models</h4>
                    <ul className="space-y-1.5 text-sm">
                      {analysis.data_models.map((m) => (
                        <li key={`${m.file}:${m.name}`} className="flex items-center gap-2">
                          <Badge tone={m.kind === "orm_model" ? "amber" : "indigo"}>{m.kind === "orm_model" ? "ORM" : "schema"}</Badge>
                          <span className="font-medium">{m.name}</span>
                          <span className="truncate text-xs text-muted">{m.file}:{m.lineno}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </div>
          </Card>
          <p className="text-xs text-muted">
            Analysed at commit <Mono>{analysis.commit?.slice(0, 10)}</Mono>. Python is parsed with the standard-library AST; JavaScript and
            TypeScript imports are extracted lexically and resolved through tsconfig paths.
          </p>
        </div>
      )}
    </AppShell>
  );
}
