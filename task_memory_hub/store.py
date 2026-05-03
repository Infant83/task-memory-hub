from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Any, Iterable


CURRENT_SCHEMA_VERSION = 8


TASK_EXTRA_COLUMNS = {
    "depends_on_json": "TEXT NOT NULL DEFAULT '[]'",
    "agent_claim_owner": "TEXT NOT NULL DEFAULT ''",
    "agent_claim_until": "TEXT",
    "agent_claim_status": "TEXT NOT NULL DEFAULT ''",
    "claimed_at": "TEXT",
    "context_pack_version": "TEXT NOT NULL DEFAULT '0.1'",
    "last_imported_hash": "TEXT NOT NULL DEFAULT ''",
    "last_exported_hash": "TEXT NOT NULL DEFAULT ''",
    "last_imported_at": "TEXT",
    "last_exported_at": "TEXT",
    "conflict_status": "TEXT NOT NULL DEFAULT ''",
}


TASK_ROUTING_COLUMNS = {
    "source_workspace_id": "TEXT NOT NULL DEFAULT ''",
    "source_workspace_slug": "TEXT NOT NULL DEFAULT ''",
    "target_workspace_id": "TEXT NOT NULL DEFAULT ''",
    "target_workspace_slug": "TEXT NOT NULL DEFAULT ''",
    "source_principal_id": "TEXT NOT NULL DEFAULT ''",
    "target_principal_id": "TEXT NOT NULL DEFAULT ''",
    "proposed_by_principal_id": "TEXT NOT NULL DEFAULT ''",
    "approved_by_principal_id": "TEXT NOT NULL DEFAULT ''",
    "assigned_by_principal_id": "TEXT NOT NULL DEFAULT ''",
    "routing_status": "TEXT NOT NULL DEFAULT 'local_only'",
    "origin_task_id": "TEXT NOT NULL DEFAULT ''",
    "hub_task_id": "TEXT NOT NULL DEFAULT ''",
    "harness_id": "TEXT NOT NULL DEFAULT ''",
    "policy_profile_id": "TEXT NOT NULL DEFAULT ''",
}

TASK_EXECUTION_COLUMNS = {
    "task_kind": "TEXT NOT NULL DEFAULT 'action'",
    "execution_mode": "TEXT NOT NULL DEFAULT 'manual'",
    "schedule_kind": "TEXT NOT NULL DEFAULT 'none'",
    "controller_status": "TEXT NOT NULL DEFAULT ''",
    "automation_id": "TEXT NOT NULL DEFAULT ''",
    "parent_task_id": "TEXT NOT NULL DEFAULT ''",
    "execution_contract_json": "TEXT NOT NULL DEFAULT '{}'",
    "schedule_json": "TEXT NOT NULL DEFAULT '{}'",
    "artifact_contract_json": "TEXT NOT NULL DEFAULT '{}'",
}


NOTIFICATION_JOB_EXTRA_COLUMNS = {
    "locked_by": "TEXT NOT NULL DEFAULT ''",
    "locked_until": "TEXT",
    "next_attempt_at": "TEXT",
    "last_error": "TEXT NOT NULL DEFAULT ''",
}


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    fingerprint_sha256 TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    detail_md TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'inbox',
    priority TEXT NOT NULL DEFAULT 'normal',
    rank INTEGER,
    due_at TEXT,
    snooze_until TEXT,
    ack_at TEXT,
    completed_at TEXT,
    source_agent TEXT NOT NULL DEFAULT 'manual',
    source_workspace TEXT NOT NULL DEFAULT '',
    source_workspace_id TEXT NOT NULL DEFAULT '',
    source_workspace_slug TEXT NOT NULL DEFAULT '',
    target_workspace_id TEXT NOT NULL DEFAULT '',
    target_workspace_slug TEXT NOT NULL DEFAULT '',
    source_principal_id TEXT NOT NULL DEFAULT '',
    target_principal_id TEXT NOT NULL DEFAULT '',
    proposed_by_principal_id TEXT NOT NULL DEFAULT '',
    approved_by_principal_id TEXT NOT NULL DEFAULT '',
    assigned_by_principal_id TEXT NOT NULL DEFAULT '',
    routing_status TEXT NOT NULL DEFAULT 'local_only',
    origin_task_id TEXT NOT NULL DEFAULT '',
    hub_task_id TEXT NOT NULL DEFAULT '',
    harness_id TEXT NOT NULL DEFAULT '',
    policy_profile_id TEXT NOT NULL DEFAULT '',
    task_kind TEXT NOT NULL DEFAULT 'action',
    execution_mode TEXT NOT NULL DEFAULT 'manual',
    schedule_kind TEXT NOT NULL DEFAULT 'none',
    controller_status TEXT NOT NULL DEFAULT '',
    automation_id TEXT NOT NULL DEFAULT '',
    parent_task_id TEXT NOT NULL DEFAULT '',
    execution_contract_json TEXT NOT NULL DEFAULT '{}',
    schedule_json TEXT NOT NULL DEFAULT '{}',
    artifact_contract_json TEXT NOT NULL DEFAULT '{}',
    source_repo TEXT NOT NULL DEFAULT '',
    source_branch TEXT NOT NULL DEFAULT '',
    source_session_id TEXT NOT NULL DEFAULT '',
    source_file_path TEXT NOT NULL DEFAULT '',
    source_content_hash TEXT NOT NULL DEFAULT '',
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    ai_context_pack TEXT NOT NULL DEFAULT '{}',
    ai_context_preview TEXT NOT NULL DEFAULT '',
    redaction_level INTEGER NOT NULL DEFAULT 0,
    agent_claim_owner TEXT NOT NULL DEFAULT '',
    agent_claim_until TEXT,
    agent_claim_status TEXT NOT NULL DEFAULT '',
    claimed_at TEXT,
    context_pack_version TEXT NOT NULL DEFAULT '0.1',
    last_imported_hash TEXT NOT NULL DEFAULT '',
    last_exported_hash TEXT NOT NULL DEFAULT '',
    last_imported_at TEXT,
    last_exported_at TEXT,
    conflict_status TEXT NOT NULL DEFAULT '',
    last_human_update_at TEXT,
    last_agent_update_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_snooze ON tasks(snooze_until);
CREATE INDEX IF NOT EXISTS idx_tasks_rank ON tasks(rank);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(source_workspace);
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at);

CREATE TABLE IF NOT EXISTS principals (
    principal_id TEXT PRIMARY KEY,
    principal_type TEXT NOT NULL DEFAULT 'human',
    display_name TEXT NOT NULL,
    contact_ref TEXT NOT NULL DEFAULT '',
    auth_method TEXT NOT NULL DEFAULT 'manual',
    trust_level TEXT NOT NULL DEFAULT 'member',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(principal_type, display_name)
);

CREATE INDEX IF NOT EXISTS idx_principals_type_name
ON principals(principal_type, display_name);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    workspace_slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    repo_remote TEXT NOT NULL DEFAULT '',
    repo_branch TEXT NOT NULL DEFAULT '',
    workspace_type TEXT NOT NULL DEFAULT 'project',
    registration_status TEXT NOT NULL DEFAULT 'active',
    registered_by_principal_id TEXT NOT NULL DEFAULT '',
    proposed_by_principal_id TEXT NOT NULL DEFAULT '',
    approved_by_principal_id TEXT NOT NULL DEFAULT '',
    authority_basis TEXT NOT NULL DEFAULT 'manual',
    authority_level TEXT NOT NULL DEFAULT 'owner',
    approval_status TEXT NOT NULL DEFAULT 'approved',
    approval_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspaces_path ON workspaces(canonical_path);
CREATE INDEX IF NOT EXISTS idx_workspaces_registered_by ON workspaces(registered_by_principal_id);

CREATE TABLE IF NOT EXISTS auth_profiles (
    auth_profile_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    profile_name TEXT NOT NULL,
    auth_type TEXT NOT NULL DEFAULT 'bearer',
    secret_ref TEXT NOT NULL DEFAULT '',
    owner_principal_id TEXT NOT NULL DEFAULT '',
    allowed_scopes_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, profile_name)
);

CREATE INDEX IF NOT EXISTS idx_auth_profiles_workspace ON auth_profiles(workspace_id);

CREATE TABLE IF NOT EXISTS policy_profiles (
    policy_profile_id TEXT PRIMARY KEY,
    profile_name TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'internal',
    redaction_level INTEGER NOT NULL DEFAULT 0,
    external_write_allowed INTEGER NOT NULL DEFAULT 0,
    requires_approval_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(profile_name)
);

CREATE TABLE IF NOT EXISTS network_profiles (
    network_profile_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    profile_name TEXT NOT NULL,
    bind_scope TEXT NOT NULL DEFAULT 'loopback',
    api_base_url TEXT NOT NULL DEFAULT '',
    mcp_transport TEXT NOT NULL DEFAULT 'stdio',
    mcp_command TEXT NOT NULL DEFAULT '',
    auth_profile_ref TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, profile_name)
);

CREATE INDEX IF NOT EXISTS idx_network_profiles_workspace ON network_profiles(workspace_id);

CREATE TABLE IF NOT EXISTS harness_profiles (
    harness_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    profile_name TEXT NOT NULL,
    harness_type TEXT NOT NULL DEFAULT 'agent',
    default_agent_principal_id TEXT NOT NULL DEFAULT '',
    policy_profile_id TEXT NOT NULL DEFAULT '',
    network_profile_id TEXT NOT NULL DEFAULT '',
    max_actions_per_hour INTEGER NOT NULL DEFAULT 6,
    min_action_interval_seconds INTEGER NOT NULL DEFAULT 300,
    max_open_actions INTEGER NOT NULL DEFAULT 20,
    default_priority TEXT NOT NULL DEFAULT 'normal',
    default_push_profile TEXT NOT NULL DEFAULT 'normal',
    require_human_approval INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, profile_name)
);

CREATE INDEX IF NOT EXISTS idx_harness_profiles_workspace ON harness_profiles(workspace_id);
CREATE INDEX IF NOT EXISTS idx_harness_profiles_agent ON harness_profiles(default_agent_principal_id);

CREATE TABLE IF NOT EXISTS agent_runtime_status (
    workspace_id TEXT NOT NULL DEFAULT '',
    principal_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'worker',
    status TEXT NOT NULL DEFAULT 'active',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    default_harness_id TEXT NOT NULL DEFAULT '',
    max_active_tasks INTEGER NOT NULL DEFAULT 1,
    current_task_id TEXT NOT NULL DEFAULT '',
    last_heartbeat_at TEXT NOT NULL,
    lease_until TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, principal_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_runtime_status_workspace
ON agent_runtime_status(workspace_id, status, role);

CREATE INDEX IF NOT EXISTS idx_agent_runtime_status_principal
ON agent_runtime_status(principal_id);

CREATE TABLE IF NOT EXISTS action_intake_events (
    event_id TEXT PRIMARY KEY,
    harness_id TEXT NOT NULL DEFAULT '',
    principal_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    action_key TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_intake_harness_created
ON action_intake_events(harness_id, created_at);

CREATE TABLE IF NOT EXISTS sync_links (
    link_id TEXT PRIMARY KEY,
    local_task_id TEXT NOT NULL,
    hub_task_id TEXT NOT NULL,
    source_workspace_id TEXT NOT NULL,
    target_workspace_id TEXT NOT NULL DEFAULT '',
    routing_status TEXT NOT NULL DEFAULT 'pushed',
    last_push_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(local_task_id, source_workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_sync_links_hub_task ON sync_links(hub_task_id);
CREATE INDEX IF NOT EXISTS idx_sync_links_source ON sync_links(source_workspace_id);
CREATE INDEX IF NOT EXISTS idx_sync_links_target ON sync_links(target_workspace_id);

CREATE TABLE IF NOT EXISTS sync_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    local_task_id TEXT NOT NULL DEFAULT '',
    hub_task_id TEXT NOT NULL DEFAULT '',
    source_workspace_id TEXT NOT NULL DEFAULT '',
    target_workspace_id TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sync_events_created ON sync_events(created_at);

CREATE TABLE IF NOT EXISTS task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, created_at);

CREATE TABLE IF NOT EXISTS notification_jobs (
    job_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'local',
    status TEXT NOT NULL DEFAULT 'pending',
    run_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_by TEXT NOT NULL DEFAULT '',
    locked_until TEXT,
    next_attempt_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_jobs_ready
ON notification_jobs(status, run_at);

CREATE TABLE IF NOT EXISTS notification_attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES notification_jobs(job_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    response_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_attempts_job
ON notification_attempts(job_id, created_at);
"""


JSON_FIELDS = {
    "tags_json",
    "ai_context_pack",
    "payload_json",
    "depends_on_json",
    "response_json",
    "allowed_scopes_json",
    "execution_contract_json",
    "schedule_json",
    "artifact_contract_json",
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _table_columns(conn, table)
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _record_migration(conn: sqlite3.Connection, version: int, name: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
        VALUES (?, ?, ?)
        """,
        (version, name, _now_iso()),
    )


def init_db(db_path: Path | str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_columns(conn, "tasks", TASK_EXTRA_COLUMNS)
        _ensure_columns(conn, "tasks", TASK_ROUTING_COLUMNS)
        _ensure_columns(conn, "tasks", TASK_EXECUTION_COLUMNS)
        _ensure_columns(conn, "notification_jobs", NOTIFICATION_JOB_EXTRA_COLUMNS)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_claim ON tasks(agent_claim_owner, agent_claim_until)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_source_workspace_id ON tasks(source_workspace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_target_workspace_id ON tasks(target_workspace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_origin_task_id ON tasks(origin_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_hub_task_id ON tasks(hub_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_routing_status ON tasks(routing_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_task_kind ON tasks(task_kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_automation_id ON tasks(automation_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_controller_status ON tasks(controller_status)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_runtime_status (
                workspace_id TEXT NOT NULL DEFAULT '',
                principal_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'worker',
                status TEXT NOT NULL DEFAULT 'active',
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                default_harness_id TEXT NOT NULL DEFAULT '',
                max_active_tasks INTEGER NOT NULL DEFAULT 1,
                current_task_id TEXT NOT NULL DEFAULT '',
                last_heartbeat_at TEXT NOT NULL,
                lease_until TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id, principal_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runtime_status_workspace
            ON agent_runtime_status(workspace_id, status, role)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runtime_status_principal
            ON agent_runtime_status(principal_id)
            """
        )
        _record_migration(conn, 1, "base_schema")
        _record_migration(conn, 2, "claim_and_outbox_extensions")
        _record_migration(conn, 3, "workspace_control_plane")
        _record_migration(conn, 4, "harness_profiles_and_action_intake")
        _record_migration(conn, 5, "file_sync_timestamps")
        _record_migration(conn, 6, "auth_profiles")
        _record_migration(conn, 7, "task_execution_contract")
        _record_migration(conn, 8, "agent_runtime_status")


@contextmanager
def transaction(db_path: Path | str, immediate: bool = False) -> Iterable[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    if "tags_json" in data:
        data["tags"] = json.loads(data.pop("tags_json") or "[]")
    if "depends_on_json" in data:
        data["depends_on"] = json.loads(data.pop("depends_on_json") or "[]")
    if "ai_context_pack" in data:
        data["ai_context_pack"] = json.loads(data["ai_context_pack"] or "{}")
    if "execution_contract_json" in data:
        data["execution_contract"] = json.loads(data.pop("execution_contract_json") or "{}")
    if "schedule_json" in data:
        data["schedule"] = json.loads(data.pop("schedule_json") or "{}")
    if "artifact_contract_json" in data:
        data["artifact_contract"] = json.loads(data.pop("artifact_contract_json") or "{}")
    if "payload_json" in data:
        data["payload"] = json.loads(data.pop("payload_json") or "{}")
    if "allowed_scopes_json" in data:
        data["allowed_scopes"] = json.loads(data.pop("allowed_scopes_json") or "[]")
    if "capabilities_json" in data:
        data["capabilities"] = json.loads(data.pop("capabilities_json") or "[]")
    return data


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows if row is not None]
