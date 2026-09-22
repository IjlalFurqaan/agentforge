"""Environment-based configuration.

Every setting can be supplied through environment variables or a `.env` file in the backend
directory. Secrets have no defaults that would be usable in production: `validate_for_runtime`
refuses to start a production process with missing or development-only values.
"""

from __future__ import annotations

import json
import os
import secrets
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

AgentName = Literal[
    "product_manager", "architect", "developer", "security", "qa", "reviewer", "deployment"
]
AGENT_NAMES: tuple[str, ...] = (
    "product_manager", "architect", "developer", "security", "qa", "reviewer", "deployment",
)


class Settings(BaseSettings):
    # AGENTFORGE_ENV_FILE="" disables dotenv loading (used by the test suite for isolation).
    model_config = SettingsConfigDict(
        env_file=os.environ.get("AGENTFORGE_ENV_FILE", str(BACKEND_DIR / ".env")) or None,
        env_file_encoding="utf-8", extra="ignore",
    )

    # --- runtime -------------------------------------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "AgentForge"
    api_prefix: str = "/api"
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"
    log_json: bool = True

    # --- persistence ---------------------------------------------------------------------------
    database_url: str = f"sqlite+aiosqlite:///{BACKEND_DIR / 'data' / 'agentforge.db'}"
    checkpoint_url: str | None = None  # defaults to a sibling of database_url
    redis_url: str | None = None

    # --- auth ----------------------------------------------------------------------------------
    secret_key: str = ""
    encryption_key: str = ""  # Fernet key for GitHub tokens; derived from secret_key when empty
    jwt_issuer: str = "agentforge"
    jwt_audience: str = "agentforge-api"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    refresh_cookie_name: str = "af_refresh"
    login_rate_limit: int = 10  # attempts
    login_rate_window_seconds: int = 300
    allow_registration: bool = True

    # --- GitHub --------------------------------------------------------------------------------
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_oauth_redirect_uri: str = "http://localhost:8000/api/github/oauth/callback"
    github_api_url: str = "https://api.github.com"
    github_oauth_scopes: str = "repo read:user"

    # --- workspaces & repositories ---------------------------------------------------------------
    workspaces_root: Path = BACKEND_DIR / "data" / "workspaces"
    local_repos_root: Path | None = None  # enables `local` repositories (development only)
    max_repo_files: int = 20_000
    max_file_bytes: int = 1_000_000
    git_author_name: str = "AgentForge Bot"
    git_author_email: str = "agentforge-bot@users.noreply.github.com"

    # --- sandbox -------------------------------------------------------------------------------
    sandbox_backend: Literal["docker", "seatbelt", "disabled"] = "docker"
    sandbox_image: str = "agentforge-sandbox:latest"
    sandbox_docker_runtime: str | None = None  # e.g. "runsc" for gVisor
    sandbox_host_workspaces_root: Path | None = None  # host path when the worker runs in a container
    sandbox_cpus: float = 1.0
    sandbox_memory_mb: int = 1024
    sandbox_pids_limit: int = 256
    sandbox_timeout_seconds: int = 300
    sandbox_output_limit_bytes: int = 200_000
    sandbox_python: str = "python3"  # interpreter used to create per-task venvs (seatbelt backend)

    # --- LLM -----------------------------------------------------------------------------------
    default_llm_provider: str = "anthropic"
    default_llm_model: str = "claude-opus-5-5"
    agent_models: dict[str, dict[str, Any]] = Field(default_factory=dict)
    anthropic_api_key: str | None = None
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_refusal_fallbacks: Literal["default", "off"] = "default"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    llm_request_timeout_seconds: float = 600.0
    llm_max_retries: int = 4
    llm_max_tokens: int = 16_000
    model_pricing_overrides: dict[str, dict[str, float]] = Field(default_factory=dict)
    embeddings_provider: Literal["none", "ollama", "openai"] = "none"
    embeddings_model: str = "nomic-embed-text"

    # --- workflow ------------------------------------------------------------------------------
    worker_mode: Literal["embedded", "external", "off"] = "embedded"
    worker_poll_interval_seconds: float = 1.0
    max_concurrent_workflows: int = 2
    default_max_dev_iterations: int = 4
    default_max_arch_revisions: int = 2
    default_max_cost_usd: float = 5.0
    default_max_wall_clock_minutes: int = 60
    default_max_tool_calls: int = 40
    graph_recursion_limit: int = 80

    # --- observability -------------------------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "agentforge"
    otel_console_exporter: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip().startswith("["):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @field_validator("workspaces_root", "local_repos_root", "sandbox_host_workspaces_root", mode="after")
    @classmethod
    def _anchor_relative_paths(cls, value: Path | None) -> Path | None:
        # Relative paths are resolved against the backend directory, so the project can be moved freely.
        if value is not None and not value.expanduser().is_absolute():
            return (BACKEND_DIR / value).resolve()
        return value.expanduser() if value is not None else None

    @field_validator("agent_models", "model_pricing_overrides", mode="before")
    @classmethod
    def _parse_json(cls, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value

    @model_validator(mode="after")
    def _fill_dev_secrets(self) -> Settings:
        if not self.secret_key and self.environment != "production":
            # Ephemeral key: sessions do not survive a restart. Fine for dev/test only.
            self.secret_key = secrets.token_urlsafe(48)
            if self.environment == "development":
                warnings.warn(
                    "SECRET_KEY is not set; using an ephemeral key (sessions reset on restart).",
                    stacklevel=2,
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def resolved_checkpoint_url(self) -> str:
        if self.checkpoint_url:
            return self.checkpoint_url
        if self.database_url.startswith("sqlite"):
            db_path = self.database_url.split(":///", 1)[1]
            return str(Path(db_path).with_name("checkpoints.db"))
        # postgresql+asyncpg://... -> postgresql://... for psycopg
        return self.database_url.replace("+asyncpg", "").replace("+psycopg", "")

    def validate_for_runtime(self) -> None:
        """Fail fast on configurations that are unsafe outside development."""
        problems: list[str] = []
        if self.is_production:
            if len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be set (>= 32 chars) in production")
            if self.sandbox_backend == "seatbelt":
                problems.append("The seatbelt sandbox is development-only; use docker in production")
            if self.local_repos_root is not None:
                problems.append("LOCAL_REPOS_ROOT must not be set in production")
            if self.database_url.startswith("sqlite"):
                problems.append("Use PostgreSQL in production")
            if any(o.startswith("http://") and "localhost" not in o for o in self.cors_origins):
                problems.append("CORS origins must use https in production")
        if problems:
            raise RuntimeError("Invalid configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
