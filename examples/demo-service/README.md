# Taskboard API

A small FastAPI service for tracking projects and their work items. It is the target repository
for the AgentForge demo ("implement a secure authentication module ...").

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest
```

Endpoints today (no authentication yet):

| Method | Path | Description |
|---|---|---|
| GET | /health | liveness |
| GET/POST | /projects | list / create projects |
| GET/DELETE | /projects/{id} | read / delete a project |
| GET/POST | /projects/{id}/items | list / add work items |
| PATCH | /items/{id} | update an item's status |
