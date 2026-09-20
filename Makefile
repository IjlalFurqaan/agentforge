# Developer entry points. Run `make help` for the list.
PY := backend/.venv/bin/python

.PHONY: help setup migrate api worker frontend test lint compose sandbox-image

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Create the backend virtualenv and install dependencies
	python3 -m venv backend/.venv
	$(PY) -m pip install -e "backend[dev]"
	cd frontend && npm install

migrate: ## Apply database migrations
	cd backend && .venv/bin/python -m alembic upgrade head

api: migrate ## Run the API (with the embedded worker) on :8000
	cd backend && .venv/bin/python -m uvicorn app.main:create_app --factory --reload --port 8000

worker: ## Run a standalone workflow worker
	cd backend && .venv/bin/python -m app.worker

frontend: ## Run the dashboard on :3000
	cd frontend && npm run dev

test: ## Backend test suite
	cd backend && .venv/bin/python -m pytest -q

lint: ## Ruff + TypeScript checks
	cd backend && .venv/bin/python -m ruff check app tests
	cd frontend && npm run typecheck

sandbox-image: ## Build the Docker execution sandbox image
	docker build -t agentforge-sandbox:latest sandbox

compose: sandbox-image ## Full stack with PostgreSQL, Redis and the Docker sandbox
	mkdir -p .data/workspaces
	docker compose up --build
