# Roadmap and implementation status

This file is the honest ledger of what exists, what was verified, and how. "Verified" means it was
executed in the development environment used to build the project: macOS, Python 3.15, no Docker,
no Node.js, and no hosted LLM key.

## Phase 1: vertical slice (done)

| Capability | Status | How it was verified |
|---|---|---|
| Auth: register/login, JWT, refresh rotation + reuse detection, RBAC, rate limiting | Done | `tests/test_auth.py`, plus manual curl against the running API |
| Repository connection (local, https git URL, GitHub) + static analysis | Done | `tests/test_intelligence.py`, plus the live API on `examples/demo-service`. GitHub OAuth flow not exercised (needs an OAuth app). |
| LangGraph workflow with revision loops, gates, checkpoints, resume | Done | `tests/test_workflow.py` (two full runs through the real sandbox) |
| Seven agents with typed structured outputs and a tool loop | Done | Workflow tests with a scripted model. **A live LLM run is still pending**: see below. |
| Seatbelt sandbox: no network, no writes outside the task, `$HOME` unreadable, timeouts | Done | `tests/test_sandbox.py` |
| Docker sandbox backend | Implemented | **Not executed**: Docker isn't installed in the build environment. CI is configured to run the suite with `AGENTFORGE_SANDBOX=docker`. |
| Test execution with JUnit parsing (fails closed on missing or hostile reports) | Done | Unit + workflow tests |
| Security agent: Bandit, secret scanner, Semgrep (local rules) + LLM review | Done | Bandit/secret scanner run in the workflow tests. Semgrep isn't installed locally (it's in the sandbox image). |
| Final report, PR gate, draft PR publication | Done | Report and gate verified in tests. Pushing and PR creation against GitHub not exercised. |
| Audit log, SSE stream, metrics API, OpenTelemetry spans | Done | API tests. The OTLP export path wasn't connected to a collector. |
| Alembic migrations | Done | upgrade / `alembic check` / downgrade on SQLite. The PostgreSQL run is in CI. |
| Next.js dashboard (6 views) | Written | **Not built or type-checked**: Node.js wasn't available here. CI runs `tsc`, `next lint` and `next build`. |
| Docker Compose stack, CI pipeline | Written | Not executed here (no Docker). |

### Live LLM verification

The provider layer supports Anthropic (official SDK), OpenAI-compatible servers and native Ollama.
Only the Ollama provider's request/response mapping was exercised (against a mocked transport). The
Anthropic provider follows the documented Messages API (structured outputs via `output_config.format`,
adaptive thinking defaults, the refusal-fallback beta), but it has **not** been called against the live
API yet. Running the demo with a real model is the next verification step.

## Phase 2: hardening

- Egress-filtering proxy for sandbox install steps (PyPI/npm allowlist), and gVisor (`runsc`) as the default runtime.
- Git-backed workspace retention and cleanup job; per-tenant workspace quotas.
- Redis pub/sub fan-out for SSE (removes the 750 ms polling interval).
- Dependency vulnerability audit (`pip-audit` / `npm audit`) as a security-agent tool, with cached advisories.
- Playwright end-to-end tests for the dashboard.
- Persistent embedding index (pgvector) for large repositories; tree-sitter parsing for more languages.

## Phase 3: scale and product

- Multiple workers with Postgres `SKIP LOCKED` claiming (the claim query is already atomic) and per-tenant concurrency limits.
- MCP tool servers as pluggable agent tools (issue trackers, docs, internal APIs).
- Evaluation harness: replay benchmark tasks across model configurations and track pass rates, cost and iterations per agent.
- GitHub App installation (fine-grained permissions) instead of user OAuth tokens; webhook-triggered tasks from issues.
- Kubernetes manifests / Helm chart; Terraform for AWS (ECS/RDS/ElastiCache) or GCP (Cloud Run/Cloud SQL/Memorystore).
