from __future__ import annotations

from pathlib import Path
import json
import sqlite3

from .branding import APP_NAME
from .service import ensure_db
from .timeutil import iso_now


def _timestamp_slug() -> str:
    return iso_now().replace(":", "").replace("-", "").replace("T", "-").rstrip("Z")


def _resolve_backup_path(target: Path | str, source: Path) -> Path:
    path = Path(target)
    if path.exists() and path.is_dir():
        return path / f"{source.stem}-{_timestamp_slug()}.sqlite"
    if not path.suffix:
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{source.stem}-{_timestamp_slug()}.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def backup_database(db_path: Path | str | None = None, target: Path | str = ".tmh/backups") -> dict:
    source = ensure_db(db_path)
    backup_path = _resolve_backup_path(target, source)
    with sqlite3.connect(source) as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
    manifest = {
        "app": APP_NAME,
        "kind": "sqlite-backup",
        "source": str(source),
        "backup": str(backup_path),
        "created_at": iso_now(),
    }
    manifest_path = backup_path.with_name(f"{backup_path.name}.manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, "manifest": str(manifest_path)}


def restore_database(source: Path | str, db_path: Path | str | None = None, yes: bool = False) -> dict:
    if not yes:
        raise ValueError("restore requires --yes")
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"Backup not found: {source_path}")
    destination = ensure_db(db_path)
    with sqlite3.connect(source_path) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    return {
        "app": APP_NAME,
        "kind": "sqlite-restore",
        "source": str(source_path),
        "destination": str(destination),
        "restored_at": iso_now(),
    }
