from __future__ import annotations

import os

os.environ["AGENTFORGE_ENV_FILE"] = ""  # never read a developer's backend/.env during tests
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_REPO = REPO_ROOT / "examples" / "demo-service"


@pytest.fixture(scope="session")
def tmp_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("agentforge")


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    env = {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        "CHECKPOINT_URL": str(tmp_path / "checkpoints.db"),
        "WORKSPACES_ROOT": str(tmp_path / "workspaces"),
        "LOCAL_REPOS_ROOT": str(tmp_path / "repos"),
        "SANDBOX_BACKEND": os.environ.get("AGENTFORGE_SANDBOX") or ("seatbelt" if sys.platform == "darwin" else "disabled"),
        "WORKER_MODE": "off",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-1234567890",
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "DEFAULT_LLM_PROVIDER": "scripted",
        "DEFAULT_LLM_MODEL": "scripted-1",
        "LOGIN_RATE_LIMIT": "5",
        "WORKER_POLL_INTERVAL_SECONDS": "0.1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "repos").mkdir()
    get_settings.cache_clear()
    s = get_settings()
    yield s
    get_settings.cache_clear()


@pytest.fixture
async def db(settings):
    from app.db.models import Base
    from app.db.session import dispose_engine, get_engine, init_engine

    init_engine(settings)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest.fixture
async def client(settings, db) -> AsyncIterator[AsyncClient]:
    from app.core.ratelimit import MemoryRateLimiter
    from app.main import create_app

    app = create_app(settings)
    app.state.rate_limiter = MemoryRateLimiter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.app = app  # type: ignore[attr-defined]
        yield c


async def register_and_login(client: AsyncClient, email: str = "admin@example.com",
                             password: str = "Sup3r-Secret-Pass!") -> str:
    r = await client.post("/api/auth/register", json={"email": email, "password": password, "full_name": "Test"})
    assert r.status_code in (201, 409), r.text
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def demo_repo(settings) -> Path:
    """A fresh copy of the demo service under LOCAL_REPOS_ROOT."""
    target = settings.local_repos_root / "demo-service"
    shutil.copytree(DEMO_REPO, target, ignore=shutil.ignore_patterns("__pycache__", "*.db", ".venv"))
    return target


requires_sandbox = pytest.mark.skipif(
    sys.platform != "darwin" and os.environ.get("AGENTFORGE_SANDBOX") != "docker",
    reason="needs the macOS Seatbelt sandbox or SANDBOX=docker",
)
