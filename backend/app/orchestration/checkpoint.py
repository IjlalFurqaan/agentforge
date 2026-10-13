"""LangGraph checkpointer selection (Postgres in production, SQLite in development)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app.core.config import Settings


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[Any]:
    url = settings.resolved_checkpoint_url
    if url.startswith(("postgres://", "postgresql://")):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # optional extra: agentforge[postgres]

        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        if url != ":memory:":
            Path(url).parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(url) as saver:
            yield saver
