from __future__ import annotations

import os
from pathlib import Path
import secrets
from urllib.parse import urlparse, urlunparse

from .branding import ENV_PREFIX


APP_DIR_NAME = ".tmh"
GLOBAL_APP_DIR_NAME = ".task-memory-hub"
DEFAULT_DB_NAME = "tmh.sqlite"
DEFAULT_API_TOKEN_NAME = "api-token"


def workspace_root() -> Path:
    return Path.cwd()


def default_app_dir() -> Path:
    raw = os.environ.get(f"{ENV_PREFIX}_HOME") or os.environ.get("TMH_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return workspace_root() / APP_DIR_NAME


def default_db_path() -> Path:
    raw = os.environ.get(f"{ENV_PREFIX}_DB") or os.environ.get("TMH_DB")
    if raw:
        return Path(raw).expanduser().resolve()
    return default_app_dir() / DEFAULT_DB_NAME


def default_global_app_dir() -> Path:
    raw = os.environ.get(f"{ENV_PREFIX}_GLOBAL_HOME") or os.environ.get("TMH_GLOBAL_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / GLOBAL_APP_DIR_NAME


def default_global_db_path() -> Path:
    raw = os.environ.get(f"{ENV_PREFIX}_GLOBAL_DB") or os.environ.get("TMH_GLOBAL_DB")
    if raw:
        return Path(raw).expanduser().resolve()
    return default_global_app_dir() / DEFAULT_DB_NAME


def database_url(db_path: Path | str | None = None, global_scope: bool = False) -> str:
    raw = os.environ.get(f"{ENV_PREFIX}_DATABASE_URL") or os.environ.get("TMH_DATABASE_URL")
    if raw:
        return raw.strip()
    path = Path(db_path) if db_path else (default_global_db_path() if global_scope else default_db_path())
    return f"sqlite:///{path.resolve().as_posix()}"


def database_backend(url: str | None = None) -> str:
    parsed = urlparse(url or database_url())
    scheme = parsed.scheme.lower()
    if scheme in {"sqlite", "sqlite3"}:
        return "sqlite"
    if scheme in {"postgres", "postgresql"}:
        return "postgresql"
    return scheme or "sqlite"


def redact_database_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.password:
        return url
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    username = parsed.username or ""
    netloc = f"{username}:***@{hostname}{port}" if username else f"***@{hostname}{port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def default_api_token_path() -> Path:
    raw = os.environ.get(f"{ENV_PREFIX}_API_TOKEN_FILE")
    if raw:
        return Path(raw).expanduser().resolve()
    return default_app_dir() / DEFAULT_API_TOKEN_NAME


def get_or_create_api_token() -> str:
    env_token = os.environ.get(f"{ENV_PREFIX}_API_TOKEN")
    if env_token:
        return env_token.strip()
    path = default_api_token_path()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    return token
