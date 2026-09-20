# AgentForge

**An autonomous multi-agent software engineering platform.** You connect a repository and describe a
change. A team of seven specialised AI agents, orchestrated as a LangGraph state machine, then plans
the change, implements it, tests it in an isolated sandbox, reviews it for security and quality, and
prepares a draft pull request. The pull request is only opened after a human approves it.

```
Requirements ─▶ Design ─▶ Implement ─▶ Test ─▶ Security ─▶ Review ─▶ Deploy prep ─▶ Report ─▶ PR gate
                  ▲            ▲         │         │          │
                  │            └─────────┴─────────┴──────────┘  failures and findings go back to the developer
                  └──────────────────────────────────────────────┘  "request_redesign" goes back to the architect
```

This is not a chat wrapper. Every outcome the platform reports comes from real execution: test
results are parsed from JUnit XML produced in the sandbox, diffs come from `git`, and security
findings come from scanner output. Models analyse that evidence, but they cannot overwrite it.

## Highlights

| Area | What's implemented |
|---|---|
| **Multi-agent orchestration** | LangGraph `StateGraph` with 14 nodes, conditional routing and revision loops (QA → Developer, Security → Developer, Reviewer → Developer/Architect). Typed agent-to-agent messages; disputes go back to the sender. |
| **Human-in-the-loop** | `interrupt()` gates for clarification questions, plan approval and PR approval. Decisions arrive through the API and resume the graph from its checkpoint. |
| **Reliability** | Durable checkpoints (SQLite / Postgres). Retry policies for transient LLM/infra errors. Resume-after-failure from the last good super-step. Iteration, tool-call, cost, step and wall-clock limits. |
| **Secure execution** | Every command runs in a sandbox. Production: one locked-down Docker container per command (no network, read-only rootfs, `cap_drop=ALL`, non-root, CPU/mem/PID limits). Development on macOS: a kernel-enforced Seatbelt profile. A command allowlist sits in front of both. |
| **Repository intelligence** | Python AST (imports, symbols, FastAPI/Flask routes, ORM/Pydantic models), JS/TS import resolution with tsconfig aliases, dependency manifests (pip, poetry, npm, go, cargo, gems), module and component graphs with cycle detection, BM25 code search with optional embedding re-ranking. |
| **LLM layer** | Provider abstraction (Anthropic SDK, OpenAI-compatible, native Ollama), per-agent model selection, strict JSON-schema structured outputs, token/cost/latency tracking and per-task budgets. |
| **GitHub** | OAuth with signed state and Fernet-encrypted tokens, repository listing, isolated clones with hooks disabled, `agentforge/*` branches, draft PRs with inline review comments. Never merges, and never pushes to protected or default branches. |
| **Auth** | argon2id passwords, 15-minute JWTs, rotating refresh tokens with reuse detection (a replayed token revokes its whole family), RBAC (viewer / engineer / admin), login rate limiting. |
| **Observability** | Append-only audit log (also the live SSE feed), OpenTelemetry spans (`gen_ai.*` attributes), structured JSON logs, metrics API for the dashboard. |
| **Dashboard** | Next.js + TypeScript + Tailwind views: dashboard, new task, live agent workspace, architecture view, code review (diff + findings + tests + comments), final report. |

## Repository layout

```
backend/            FastAPI service, LangGraph workflow, agents, sandbox, repo intelligence (Python 3.12+)
  app/agents/       seven agents, tool loop, prompts, schemas
  app/orchestration/graph, nodes, routing, runner, checkpointing
  app/intelligence/ AST + import analysis, dependency graphs, code search
  app/sandbox/      command policy, Docker and Seatbelt backends
  app/vcs/          hardened git operations and GitHub client
  tests/            69 tests, incl. end-to-end workflow runs through the real sandbox
frontend/           Next.js dashboard
sandbox/            Docker image used for agent command execution
examples/           demo-service: the FastAPI app used in the demo scenario
docs/               architecture, security model, roadmap
```

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and its decisions,
[docs/SECURITY.md](docs/SECURITY.md) for the threat model, and [docs/ROADMAP.md](docs/ROADMAP.md) for
what is and isn't finished.

## Quick start (local development)

Requirements: Python 3.12+, git, Node 22 (for the dashboard), and either macOS (Seatbelt sandbox)
or Docker (Docker sandbox).

```bash
make setup                       # backend venv + frontend deps
cp backend/.env.example backend/.env
```

Edit `backend/.env`:

```ini
SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
SANDBOX_BACKEND=seatbelt         # macOS dev; use docker elsewhere (make sandbox-image first)
LOCAL_REPOS_ROOT=/absolute/path/to/this/repo/examples
# pick a model provider:
DEFAULT_LLM_PROVIDER=anthropic   # or ollama / openai
DEFAULT_LLM_MODEL=claude-opus-5-5
ANTHROPIC_API_KEY=...
```

```bash
make api        # http://localhost:8000/api/docs (runs migrations, embeds the worker)
make frontend   # http://localhost:3000
```

### Full stack with Docker Compose

```bash
cp .env.example .env             # set SECRET_KEY and a model provider
make compose                     # builds the sandbox image, then Postgres, Redis, API, worker, dashboard
```

The worker reaches Docker through `docker-socket-proxy`, which only exposes the container API.

## Demo scenario

1. Sign in, then open **Repositories**. Connect GitHub (or add `examples/demo-service` as a local
   repository) and run the analysis. The **Architecture** view shows routes, models, service
   dependencies and the module graph.
2. Open **New task**. The form is pre-filled with the demo request: *"Implement a secure
   authentication module with JWT, refresh token rotation, Google OAuth, role-based access control
   and comprehensive tests."* Choose models and approval gates, then start the run.
3. The **Agent workspace** streams the run live: requirements, design (approve it at the plan gate),
   implementation, sandboxed test runs, failures sent back to the developer, security scanning,
   review, and deployment preparation.
4. **Code review** shows the git diff with the reviewer's inline comments, the parsed test results
   and the security findings. **Final report** lists the acceptance criteria verdicts, modified files,
   executed tests, findings, limitations and every routing decision.
5. Approve the PR gate. For GitHub repositories, AgentForge pushes `agentforge/<task>` and opens a
   **draft** PR with the report and inline review comments.

## Testing

```bash
make test       # 69 backend tests
make lint       # ruff + tsc
```

The end-to-end tests (`backend/tests/test_workflow.py`) drive the real graph, workspace cloning,
dependency installation, pytest execution and JUnit parsing, Bandit and git through the sandbox. They
use a scripted model double so that the "agent decisions" are deterministic. That double lives only in
`tests/` and is never registered by the application. One test makes the first implementation fail its
tests and asserts that the QA → Developer loop fixes it. Another asserts that a resume after a
provider failure continues from the checkpoint instead of starting over.

## Configuration reference

All settings are environment variables (see `backend/.env.example`). The important ones:

| Variable | Purpose |
|---|---|
| `DEFAULT_LLM_PROVIDER`, `DEFAULT_LLM_MODEL`, `AGENT_MODELS` | Team default and per-agent model selection (`AGENT_MODELS` is JSON). Tasks can override per agent. |
| `ANTHROPIC_EFFORT`, `ANTHROPIC_REFUSAL_FALLBACKS` | Effort level for Claude models, and whether supported models use the server-side refusal fallback (`default`, on by default). |
| `SANDBOX_BACKEND`, `SANDBOX_*` | `docker` (production), `seatbelt` (macOS dev only) or `disabled`. Resource limits and timeouts. |
| `MODEL_PRICING_OVERRIDES` | JSON of `{"provider/model": {"input": $, "output": $}}` per million tokens. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Enables OTLP trace export (e.g. the bundled Jaeger profile). |
| `GITHUB_CLIENT_ID/SECRET` | GitHub OAuth app credentials. |
