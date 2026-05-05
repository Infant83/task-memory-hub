from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import uuid

from .config import APP_DIR_NAME, DEFAULT_DB_NAME, default_db_path, default_global_db_path
from .registry import (
    ensure_principal,
    get_harness_profile,
    get_principal,
    get_workspace,
    register_workspace,
    record_sync_event,
)
from .service import create_task, get_task
from .store import init_db, row_to_dict, rows_to_dicts, transaction
from .timeutil import iso_now


VALID_PUSH_PROFILES = {"manifest", "normal", "full"}


def _db_path_or_default(db_path: Path | str | None, global_scope: bool = False) -> Path:
    if db_path:
        return Path(db_path)
    return default_global_db_path() if global_scope else default_db_path()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hub_fingerprint(source_workspace_id: str, local_task_id: str) -> str:
    raw = f"hub-push\n{source_workspace_id}\n{local_task_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hub_idempotency_key(source_workspace_id: str, local_task_id: str) -> str:
    return f"hub-push:{source_workspace_id}:{local_task_id}"


def _pull_idempotency_key(target_workspace_id: str, hub_task_id: str) -> str:
    return f"hub-pull:{target_workspace_id}:{hub_task_id}"


def _pull_fingerprint(target_workspace_id: str, hub_task_id: str) -> str:
    raw = f"hub-pull\n{target_workspace_id}\n{hub_task_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _task_snapshot_hash(task: dict[str, Any]) -> str:
    payload = {
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "summary": task.get("summary"),
        "next_action": task.get("next_action"),
        "detail_md": task.get("detail_md"),
        "status": task.get("status"),
        "priority": task.get("priority"),
        "rank": task.get("rank"),
        "due_at": task.get("due_at"),
        "snooze_until": task.get("snooze_until"),
        "tags": task.get("tags") or [],
        "depends_on": task.get("depends_on") or [],
        "ai_context_pack": task.get("ai_context_pack") or {},
        "task_kind": task.get("task_kind"),
        "execution_mode": task.get("execution_mode"),
        "schedule_kind": task.get("schedule_kind"),
        "controller_status": task.get("controller_status"),
        "automation_id": task.get("automation_id"),
        "parent_task_id": task.get("parent_task_id"),
        "execution_contract": task.get("execution_contract") or {},
        "schedule": task.get("schedule") or {},
        "artifact_contract": task.get("artifact_contract") or {},
        "updated_at": task.get("updated_at"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fetch_ref(
    task: dict[str, Any],
    workspace: dict[str, Any],
    principal: dict[str, Any],
    snapshot_profile: str,
) -> dict[str, Any]:
    origin_task_id = task["task_id"]
    return {
        "version": "0.1",
        "kind": "task_manifest",
        "snapshot_profile": snapshot_profile,
        "snapshot_at": iso_now(),
        "source": {
            "workspace_id": workspace["workspace_id"],
            "workspace_slug": workspace["workspace_slug"],
            "origin_task_id": origin_task_id,
            "source_principal_id": task.get("source_principal_id") or principal["principal_id"],
            "source_task_created_at": task.get("created_at"),
            "source_task_updated_at": task.get("updated_at"),
            "source_task_hash": _task_snapshot_hash(task),
            "task_api_path": f"/v1/tasks/{origin_task_id}",
            "context_pack_api_path": f"/v1/tasks/{origin_task_id}/context-pack",
        },
        "policy": {
            "redaction_level": int(task.get("redaction_level", 0) or 0),
            "detail_fetch_required": snapshot_profile != "full",
        },
    }


def _manifest_context_pack(
    task: dict[str, Any],
    workspace: dict[str, Any],
    principal: dict[str, Any],
    snapshot_profile: str,
) -> dict[str, Any]:
    return {
        "version": "0.1",
        "objective": task["title"],
        "current_state": task.get("summary", "") if snapshot_profile == "normal" else "",
        "next_action": task.get("next_action", "") if snapshot_profile == "normal" else "",
        "fetch_ref": _fetch_ref(task, workspace, principal, snapshot_profile),
    }


def _profiled_content(
    task: dict[str, Any],
    workspace: dict[str, Any],
    principal: dict[str, Any],
    snapshot_profile: str,
) -> dict[str, Any]:
    if snapshot_profile not in VALID_PUSH_PROFILES:
        raise ValueError(f"snapshot_profile must be one of {sorted(VALID_PUSH_PROFILES)}")

    fetch_ref = _fetch_ref(task, workspace, principal, snapshot_profile)
    if snapshot_profile == "manifest":
        return {
            "summary": "",
            "next_action": "",
            "detail_md": "",
            "ai_context_pack": _manifest_context_pack(task, workspace, principal, snapshot_profile),
            "ai_context_preview": (task.get("ai_context_preview") or task.get("summary") or task.get("next_action") or "")[:280],
        }
    if snapshot_profile == "normal":
        return {
            "summary": task.get("summary", ""),
            "next_action": task.get("next_action", ""),
            "detail_md": "",
            "ai_context_pack": _manifest_context_pack(task, workspace, principal, snapshot_profile),
            "ai_context_preview": (task.get("ai_context_preview") or task.get("summary") or task.get("next_action") or "")[:280],
        }

    ai_context_pack = dict(task.get("ai_context_pack") or {})
    ai_context_pack.setdefault("fetch_ref", fetch_ref)
    return {
        "summary": task.get("summary", ""),
        "next_action": task.get("next_action", ""),
        "detail_md": task.get("detail_md", ""),
        "ai_context_pack": ai_context_pack,
        "ai_context_preview": (task.get("ai_context_preview") or task.get("summary") or "")[:280],
    }


def _fetch_local_tasks(db_path: Path, limit: int, include_archived: bool = False) -> list[dict[str, Any]]:
    init_db(db_path)
    where = "" if include_archived else "WHERE status != 'archived'"
    with transaction(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM tasks
            {where}
            ORDER BY updated_at ASC, created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)


def _copyable_task_payload(
    task: dict[str, Any],
    workspace: dict[str, Any],
    principal: dict[str, Any],
    snapshot_profile: str = "normal",
) -> dict[str, Any]:
    source_workspace_slug = workspace["workspace_slug"]
    content = _profiled_content(task, workspace, principal, snapshot_profile)
    return {
        "title": task["title"],
        "summary": content["summary"],
        "next_action": content["next_action"],
        "detail_md": content["detail_md"],
        "status": task.get("status", "inbox"),
        "priority": task.get("priority", "normal"),
        "rank": task.get("rank"),
        "due_at": task.get("due_at"),
        "snooze_until": task.get("snooze_until"),
        "ack_at": task.get("ack_at"),
        "completed_at": task.get("completed_at"),
        "source_agent": task.get("source_agent", "sync"),
        "source_workspace": task.get("source_workspace") or source_workspace_slug,
        "source_workspace_id": workspace["workspace_id"],
        "source_workspace_slug": source_workspace_slug,
        "target_workspace_id": task.get("target_workspace_id", ""),
        "target_workspace_slug": task.get("target_workspace_slug", ""),
        "source_principal_id": task.get("source_principal_id") or principal["principal_id"],
        "target_principal_id": task.get("target_principal_id", ""),
        "proposed_by_principal_id": task.get("proposed_by_principal_id") or principal["principal_id"],
        "approved_by_principal_id": task.get("approved_by_principal_id") or principal["principal_id"],
        "assigned_by_principal_id": task.get("assigned_by_principal_id", ""),
        "routing_status": "pushed",
        "origin_task_id": task["task_id"],
        "hub_task_id": task.get("hub_task_id", ""),
        "harness_id": task.get("harness_id", ""),
        "policy_profile_id": task.get("policy_profile_id", ""),
        "task_kind": task.get("task_kind", "action"),
        "execution_mode": task.get("execution_mode", "manual"),
        "schedule_kind": task.get("schedule_kind", "none"),
        "controller_status": task.get("controller_status", ""),
        "automation_id": task.get("automation_id", ""),
        "parent_task_id": task.get("parent_task_id", ""),
        "execution_contract": task.get("execution_contract") or {},
        "schedule": task.get("schedule") or {},
        "artifact_contract": task.get("artifact_contract") or {},
        "source_repo": task.get("source_repo") or workspace.get("repo_remote", ""),
        "source_branch": task.get("source_branch") or workspace.get("repo_branch", ""),
        "source_session_id": task.get("source_session_id", ""),
        "source_file_path": task.get("source_file_path", ""),
        "source_content_hash": task.get("source_content_hash", ""),
        "depends_on": task.get("depends_on") or [],
        "tags": task.get("tags") or [],
        "ai_context_pack": content["ai_context_pack"],
        "ai_context_preview": content["ai_context_preview"],
        "redaction_level": task.get("redaction_level", 0),
        "context_pack_version": task.get("context_pack_version", "0.1"),
        "last_imported_hash": task.get("last_imported_hash", ""),
        "last_exported_hash": task.get("last_exported_hash", ""),
        "last_imported_at": task.get("last_imported_at"),
        "last_exported_at": task.get("last_exported_at"),
        "conflict_status": task.get("conflict_status", ""),
        "idempotency_key": _hub_idempotency_key(workspace["workspace_id"], task["task_id"]),
        "fingerprint_sha256": _hub_fingerprint(workspace["workspace_id"], task["task_id"]),
    }


def _find_hub_task(global_db_path: Path, idempotency_key: str) -> dict[str, Any] | None:
    init_db(global_db_path)
    with transaction(global_db_path) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return row_to_dict(row)


def _find_local_pulled_task(local_db_path: Path, target_workspace_id: str, hub_task_id: str) -> dict[str, Any] | None:
    init_db(local_db_path)
    with transaction(local_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE idempotency_key = ?",
            (_pull_idempotency_key(target_workspace_id, hub_task_id),),
        ).fetchone()
        return row_to_dict(row)


def _mirror_hub_task(global_db_path: Path, hub_task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    timestamp = iso_now()
    fields = {
        "title": payload["title"],
        "summary": payload.get("summary", ""),
        "next_action": payload.get("next_action", ""),
        "detail_md": payload.get("detail_md", ""),
        "status": payload.get("status", "inbox"),
        "priority": payload.get("priority", "normal"),
        "rank": payload.get("rank"),
        "due_at": payload.get("due_at"),
        "snooze_until": payload.get("snooze_until"),
        "ack_at": payload.get("ack_at"),
        "completed_at": payload.get("completed_at"),
        "source_agent": payload.get("source_agent", "sync"),
        "source_workspace": payload.get("source_workspace", ""),
        "source_workspace_id": payload.get("source_workspace_id", ""),
        "source_workspace_slug": payload.get("source_workspace_slug", ""),
        "target_workspace_id": payload.get("target_workspace_id", ""),
        "target_workspace_slug": payload.get("target_workspace_slug", ""),
        "source_principal_id": payload.get("source_principal_id", ""),
        "target_principal_id": payload.get("target_principal_id", ""),
        "proposed_by_principal_id": payload.get("proposed_by_principal_id", ""),
        "approved_by_principal_id": payload.get("approved_by_principal_id", ""),
        "assigned_by_principal_id": payload.get("assigned_by_principal_id", ""),
        "routing_status": payload.get("routing_status", "pushed"),
        "origin_task_id": payload.get("origin_task_id", ""),
        "hub_task_id": hub_task_id,
        "harness_id": payload.get("harness_id", ""),
        "policy_profile_id": payload.get("policy_profile_id", ""),
        "task_kind": payload.get("task_kind", "action"),
        "execution_mode": payload.get("execution_mode", "manual"),
        "schedule_kind": payload.get("schedule_kind", "none"),
        "controller_status": payload.get("controller_status", ""),
        "automation_id": payload.get("automation_id", ""),
        "parent_task_id": payload.get("parent_task_id", ""),
        "execution_contract_json": _json(payload.get("execution_contract") or {}),
        "schedule_json": _json(payload.get("schedule") or {}),
        "artifact_contract_json": _json(payload.get("artifact_contract") or {}),
        "source_repo": payload.get("source_repo", ""),
        "source_branch": payload.get("source_branch", ""),
        "source_session_id": payload.get("source_session_id", ""),
        "source_file_path": payload.get("source_file_path", ""),
        "source_content_hash": payload.get("source_content_hash", ""),
        "depends_on_json": _json(payload.get("depends_on") or []),
        "tags_json": _json(payload.get("tags") or []),
        "ai_context_pack": _json(payload.get("ai_context_pack") or {}),
        "ai_context_preview": payload.get("ai_context_preview", ""),
        "redaction_level": int(payload.get("redaction_level", 0)),
        "context_pack_version": payload.get("context_pack_version", "0.1"),
        "last_imported_hash": payload.get("last_imported_hash", ""),
        "last_exported_hash": payload.get("last_exported_hash", ""),
        "last_imported_at": payload.get("last_imported_at"),
        "last_exported_at": payload.get("last_exported_at"),
        "conflict_status": payload.get("conflict_status", ""),
        "updated_at": timestamp,
        "last_agent_update_at": timestamp,
    }
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [hub_task_id]
    with transaction(global_db_path) as conn:
        conn.execute(f"UPDATE tasks SET {assignments} WHERE task_id = ?", values)
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (hub_task_id,)).fetchone()
        return row_to_dict(row)


def _mark_hub_pulled(global_db_path: Path, hub_task_id: str, target_workspace_id: str) -> dict[str, Any]:
    timestamp = iso_now()
    with transaction(global_db_path) as conn:
        conn.execute(
            """
            UPDATE tasks
            SET routing_status = 'pulled',
                target_workspace_id = ?,
                updated_at = ?,
                last_agent_update_at = ?
            WHERE task_id = ?
            """,
            (target_workspace_id, timestamp, timestamp, hub_task_id),
        )
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (hub_task_id,)).fetchone()
        return row_to_dict(row)


def _mark_local_pushed(
    local_db_path: Path,
    local_task_id: str,
    hub_task_id: str,
    workspace: dict[str, Any],
    principal: dict[str, Any],
) -> dict[str, Any]:
    timestamp = iso_now()
    fields = {
        "source_workspace_id": workspace["workspace_id"],
        "source_workspace_slug": workspace["workspace_slug"],
        "source_principal_id": principal["principal_id"],
        "proposed_by_principal_id": principal["principal_id"],
        "approved_by_principal_id": principal["principal_id"],
        "routing_status": "pushed",
        "hub_task_id": hub_task_id,
        "updated_at": timestamp,
        "last_agent_update_at": timestamp,
    }
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [local_task_id]
    with transaction(local_db_path) as conn:
        conn.execute(f"UPDATE tasks SET {assignments} WHERE task_id = ?", values)
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (local_task_id,)).fetchone()
        return row_to_dict(row)


def _upsert_sync_link(
    db_path: Path,
    local_task_id: str,
    hub_task_id: str,
    source_workspace_id: str,
    target_workspace_id: str = "",
) -> dict[str, Any]:
    timestamp = iso_now()
    link_id = f"link_{uuid.uuid4().hex}"
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sync_links(
                link_id, local_task_id, hub_task_id, source_workspace_id, target_workspace_id,
                routing_status, last_push_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'pushed', ?, ?, ?)
            ON CONFLICT(local_task_id, source_workspace_id) DO UPDATE SET
                hub_task_id = excluded.hub_task_id,
                target_workspace_id = excluded.target_workspace_id,
                routing_status = 'pushed',
                last_push_at = excluded.last_push_at,
                updated_at = excluded.updated_at
            """,
            (
                link_id,
                local_task_id,
                hub_task_id,
                source_workspace_id,
                target_workspace_id,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM sync_links
            WHERE local_task_id = ? AND source_workspace_id = ?
            """,
            (local_task_id, source_workspace_id),
        ).fetchone()
        return row_to_dict(row)


def _mirror_workspace_harness_registry(local_path: Path, global_path: Path, workspace_id: str) -> dict[str, int]:
    with transaction(local_path) as conn:
        harness_rows = rows_to_dicts(
            conn.execute("SELECT * FROM harness_profiles WHERE workspace_id = ?", (workspace_id,)).fetchall()
        )
        policy_ids = sorted({row.get("policy_profile_id", "") for row in harness_rows if row.get("policy_profile_id")})
        network_ids = sorted({row.get("network_profile_id", "") for row in harness_rows if row.get("network_profile_id")})
        policy_rows = (
            rows_to_dicts(
                conn.execute(
                    f"SELECT * FROM policy_profiles WHERE policy_profile_id IN ({','.join('?' for _ in policy_ids)})",
                    policy_ids,
                ).fetchall()
            )
            if policy_ids
            else []
        )
        network_rows = (
            rows_to_dicts(
                conn.execute(
                    f"SELECT * FROM network_profiles WHERE network_profile_id IN ({','.join('?' for _ in network_ids)})",
                    network_ids,
                ).fetchall()
            )
            if network_ids
            else []
        )

    with transaction(global_path) as conn:
        for row in policy_rows:
            conn.execute(
                """
                INSERT INTO policy_profiles(
                    policy_profile_id, profile_name, classification, redaction_level,
                    external_write_allowed, requires_approval_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_profile_id) DO UPDATE SET
                    classification = excluded.classification,
                    redaction_level = excluded.redaction_level,
                    external_write_allowed = excluded.external_write_allowed,
                    requires_approval_json = excluded.requires_approval_json,
                    updated_at = excluded.updated_at
                """,
                (
                    row["policy_profile_id"],
                    row["profile_name"],
                    row["classification"],
                    row["redaction_level"],
                    row["external_write_allowed"],
                    row["requires_approval_json"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        for row in network_rows:
            conn.execute(
                """
                INSERT INTO network_profiles(
                    network_profile_id, workspace_id, profile_name, bind_scope, api_base_url,
                    mcp_transport, mcp_command, auth_profile_ref, enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(network_profile_id) DO UPDATE SET
                    bind_scope = excluded.bind_scope,
                    api_base_url = excluded.api_base_url,
                    mcp_transport = excluded.mcp_transport,
                    mcp_command = excluded.mcp_command,
                    auth_profile_ref = excluded.auth_profile_ref,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    row["network_profile_id"],
                    row["workspace_id"],
                    row["profile_name"],
                    row["bind_scope"],
                    row["api_base_url"],
                    row["mcp_transport"],
                    row["mcp_command"],
                    row["auth_profile_ref"],
                    row["enabled"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        for row in harness_rows:
            conn.execute(
                """
                INSERT INTO harness_profiles(
                    harness_id, workspace_id, profile_name, harness_type,
                    default_agent_principal_id, policy_profile_id, network_profile_id,
                    max_actions_per_hour, min_action_interval_seconds, max_open_actions,
                    default_priority, default_push_profile, require_human_approval,
                    enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(harness_id) DO UPDATE SET
                    harness_type = excluded.harness_type,
                    default_agent_principal_id = excluded.default_agent_principal_id,
                    policy_profile_id = excluded.policy_profile_id,
                    network_profile_id = excluded.network_profile_id,
                    max_actions_per_hour = excluded.max_actions_per_hour,
                    min_action_interval_seconds = excluded.min_action_interval_seconds,
                    max_open_actions = excluded.max_open_actions,
                    default_priority = excluded.default_priority,
                    default_push_profile = excluded.default_push_profile,
                    require_human_approval = excluded.require_human_approval,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    row["harness_id"],
                    row["workspace_id"],
                    row["profile_name"],
                    row["harness_type"],
                    row["default_agent_principal_id"],
                    row["policy_profile_id"],
                    row["network_profile_id"],
                    row["max_actions_per_hour"],
                    row["min_action_interval_seconds"],
                    row["max_open_actions"],
                    row["default_priority"],
                    row["default_push_profile"],
                    row["require_human_approval"],
                    row["enabled"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
    return {
        "harness_profiles": len(harness_rows),
        "policy_profiles": len(policy_rows),
        "network_profiles": len(network_rows),
    }


def push_to_global(
    local_db_path: Path | str | None = None,
    global_db_path: Path | str | None = None,
    registered_by_principal_type: str = "human",
    registered_by_display_name: str = "owner",
    authority_basis: str = "owner_request",
    authority_level: str = "owner",
    approval_status: str = "approved",
    detect_git: bool = True,
    snapshot_profile: str = "normal",
    limit: int = 1000,
    include_archived: bool = False,
) -> dict[str, Any]:
    if snapshot_profile not in VALID_PUSH_PROFILES:
        raise ValueError(f"snapshot_profile must be one of {sorted(VALID_PUSH_PROFILES)}")
    local_path = _db_path_or_default(local_db_path, global_scope=False)
    global_path = _db_path_or_default(global_db_path, global_scope=True)
    init_db(local_path)
    init_db(global_path)

    local_workspace = register_workspace(
        registered_by_principal_type=registered_by_principal_type,
        registered_by_display_name=registered_by_display_name,
        authority_basis=authority_basis,
        authority_level=authority_level,
        approval_status=approval_status,
        detect_git=detect_git,
        db_path=local_path,
    )
    global_workspace = register_workspace(
        canonical_path=local_workspace["canonical_path"],
        workspace_slug=local_workspace["workspace_slug"],
        display_name=local_workspace["display_name"],
        repo_remote=local_workspace.get("repo_remote", ""),
        repo_branch=local_workspace.get("repo_branch", ""),
        workspace_type=local_workspace.get("workspace_type", "project"),
        registered_by_principal_type=registered_by_principal_type,
        registered_by_display_name=registered_by_display_name,
        authority_basis=local_workspace.get("authority_basis", authority_basis),
        authority_level=local_workspace.get("authority_level", authority_level),
        approval_status=local_workspace.get("approval_status", approval_status),
        approval_note=local_workspace.get("approval_note", ""),
        db_path=global_path,
    )
    local_principal = ensure_principal(
        principal_type=registered_by_principal_type,
        display_name=registered_by_display_name,
        db_path=local_path,
    )
    global_principal = ensure_principal(
        principal_type=registered_by_principal_type,
        display_name=registered_by_display_name,
        db_path=global_path,
    )
    mirrored_registry = _mirror_workspace_harness_registry(
        local_path,
        global_path,
        local_workspace["workspace_id"],
    )

    tasks = _fetch_local_tasks(local_path, limit=limit, include_archived=include_archived)
    pushed_items: list[dict[str, Any]] = []
    created = 0
    updated = 0

    for task in tasks:
        payload = _copyable_task_payload(task, local_workspace, local_principal, snapshot_profile=snapshot_profile)
        existing_hub_task = _find_hub_task(global_path, payload["idempotency_key"])
        if existing_hub_task:
            hub_task = _mirror_hub_task(global_path, existing_hub_task["task_id"], payload)
            updated += 1
        else:
            hub_task = create_task(payload, db_path=global_path, actor="sync")
            hub_task = _mirror_hub_task(global_path, hub_task["task_id"], payload)
            created += 1

        _mark_local_pushed(local_path, task["task_id"], hub_task["task_id"], local_workspace, local_principal)
        _upsert_sync_link(
            local_path,
            local_task_id=task["task_id"],
            hub_task_id=hub_task["task_id"],
            source_workspace_id=local_workspace["workspace_id"],
            target_workspace_id=payload.get("target_workspace_id", ""),
        )
        _upsert_sync_link(
            global_path,
            local_task_id=task["task_id"],
            hub_task_id=hub_task["task_id"],
            source_workspace_id=local_workspace["workspace_id"],
            target_workspace_id=payload.get("target_workspace_id", ""),
        )
        event_payload = {
            "workspace_slug": local_workspace["workspace_slug"],
            "idempotency_key": payload["idempotency_key"],
            "snapshot_profile": snapshot_profile,
        }
        record_sync_event(
            "push",
            event_payload,
            local_task_id=task["task_id"],
            hub_task_id=hub_task["task_id"],
            source_workspace_id=local_workspace["workspace_id"],
            target_workspace_id=payload.get("target_workspace_id", ""),
            db_path=local_path,
        )
        record_sync_event(
            "push_received",
            event_payload,
            local_task_id=task["task_id"],
            hub_task_id=hub_task["task_id"],
            source_workspace_id=local_workspace["workspace_id"],
            target_workspace_id=payload.get("target_workspace_id", ""),
            db_path=global_path,
        )
        pushed_items.append(
            {
                "local_task_id": task["task_id"],
                "hub_task_id": hub_task["task_id"],
                "title": task["title"],
                "status": hub_task["status"],
                "routing_status": "pushed",
            }
        )

    return {
        "local_db": str(local_path),
        "global_db": str(global_path),
        "workspace": local_workspace,
        "global_workspace": global_workspace,
        "principal": local_principal,
        "global_principal": global_principal,
        "mirrored_registry": mirrored_registry,
        "snapshot_profile": snapshot_profile,
        "count": len(pushed_items),
        "created": created,
        "updated": updated,
        "items": pushed_items,
    }


def _fetch_pull_candidates(global_db_path: Path, target_workspace: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    init_db(global_db_path)
    with transaction(global_db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE target_workspace_id = ?
              AND approved_by_principal_id != ''
              AND routing_status IN ('pushed', 'pending_pull', 'assigned')
            ORDER BY
                CASE priority
                    WHEN 'urgent' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'normal' THEN 2
                    ELSE 3
                END,
                CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
                rank ASC,
                updated_at ASC
            LIMIT ?
            """,
            (target_workspace["workspace_id"], limit),
        ).fetchall()
        return rows_to_dicts(rows)


def _pull_payload(hub_task: dict[str, Any], target_workspace: dict[str, Any]) -> dict[str, Any]:
    hub_task_id = hub_task["task_id"]
    target_workspace_id = target_workspace["workspace_id"]
    ai_context_pack = dict(hub_task.get("ai_context_pack") or {})
    ai_context_pack.setdefault("hub_task_id", hub_task_id)
    ai_context_pack.setdefault("source_workspace_id", hub_task.get("source_workspace_id", ""))
    ai_context_pack.setdefault("origin_task_id", hub_task.get("origin_task_id", ""))
    return {
        "title": hub_task["title"],
        "summary": hub_task.get("summary", ""),
        "next_action": hub_task.get("next_action", ""),
        "detail_md": hub_task.get("detail_md", ""),
        "status": "inbox",
        "priority": hub_task.get("priority", "normal"),
        "rank": hub_task.get("rank"),
        "due_at": hub_task.get("due_at"),
        "snooze_until": hub_task.get("snooze_until"),
        "source_agent": "sync-pull",
        "source_workspace": hub_task.get("source_workspace", ""),
        "source_workspace_id": hub_task.get("source_workspace_id", ""),
        "source_workspace_slug": hub_task.get("source_workspace_slug", ""),
        "target_workspace_id": target_workspace_id,
        "target_workspace_slug": target_workspace["workspace_slug"],
        "source_principal_id": hub_task.get("source_principal_id", ""),
        "target_principal_id": hub_task.get("target_principal_id", ""),
        "proposed_by_principal_id": hub_task.get("proposed_by_principal_id", ""),
        "approved_by_principal_id": hub_task.get("approved_by_principal_id", ""),
        "assigned_by_principal_id": hub_task.get("assigned_by_principal_id", ""),
        "routing_status": "pulled",
        "origin_task_id": hub_task.get("origin_task_id", ""),
        "hub_task_id": hub_task_id,
        "harness_id": hub_task.get("harness_id", ""),
        "policy_profile_id": hub_task.get("policy_profile_id", ""),
        "task_kind": hub_task.get("task_kind", "action"),
        "execution_mode": hub_task.get("execution_mode", "manual"),
        "schedule_kind": hub_task.get("schedule_kind", "none"),
        "controller_status": hub_task.get("controller_status", ""),
        "automation_id": hub_task.get("automation_id", ""),
        "parent_task_id": hub_task.get("parent_task_id", ""),
        "execution_contract": hub_task.get("execution_contract") or {},
        "schedule": hub_task.get("schedule") or {},
        "artifact_contract": hub_task.get("artifact_contract") or {},
        "source_repo": hub_task.get("source_repo", ""),
        "source_branch": hub_task.get("source_branch", ""),
        "source_session_id": hub_task.get("source_session_id", ""),
        "source_file_path": hub_task.get("source_file_path", ""),
        "source_content_hash": hub_task.get("source_content_hash", ""),
        "depends_on": hub_task.get("depends_on") or [],
        "tags": sorted(set((hub_task.get("tags") or []) + ["pulled"])),
        "ai_context_pack": ai_context_pack,
        "ai_context_preview": hub_task.get("ai_context_preview", ""),
        "redaction_level": hub_task.get("redaction_level", 0),
        "context_pack_version": hub_task.get("context_pack_version", "0.1"),
        "last_imported_hash": hub_task.get("last_imported_hash", ""),
        "last_exported_hash": hub_task.get("last_exported_hash", ""),
        "last_imported_at": hub_task.get("last_imported_at"),
        "last_exported_at": hub_task.get("last_exported_at"),
        "conflict_status": hub_task.get("conflict_status", ""),
        "idempotency_key": _pull_idempotency_key(target_workspace_id, hub_task_id),
        "fingerprint_sha256": _pull_fingerprint(target_workspace_id, hub_task_id),
    }


def pull_from_global(
    local_db_path: Path | str | None = None,
    global_db_path: Path | str | None = None,
    registered_by_principal_type: str = "human",
    registered_by_display_name: str = "owner",
    limit: int = 100,
) -> dict[str, Any]:
    local_path = _db_path_or_default(local_db_path, global_scope=False)
    global_path = _db_path_or_default(global_db_path, global_scope=True)
    init_db(local_path)
    init_db(global_path)

    target_workspace = register_workspace(
        registered_by_principal_type=registered_by_principal_type,
        registered_by_display_name=registered_by_display_name,
        db_path=local_path,
    )
    register_workspace(
        canonical_path=target_workspace["canonical_path"],
        workspace_slug=target_workspace["workspace_slug"],
        display_name=target_workspace["display_name"],
        repo_remote=target_workspace.get("repo_remote", ""),
        repo_branch=target_workspace.get("repo_branch", ""),
        workspace_type=target_workspace.get("workspace_type", "project"),
        registered_by_principal_type=registered_by_principal_type,
        registered_by_display_name=registered_by_display_name,
        authority_basis=target_workspace.get("authority_basis", "owner_request"),
        authority_level=target_workspace.get("authority_level", "owner"),
        approval_status=target_workspace.get("approval_status", "approved"),
        approval_note=target_workspace.get("approval_note", ""),
        db_path=global_path,
    )

    candidates = _fetch_pull_candidates(global_path, target_workspace, limit=limit)
    pulled_items: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    created = 0
    existing = 0

    for hub_task in candidates:
        already = _find_local_pulled_task(local_path, target_workspace["workspace_id"], hub_task["task_id"])
        if already:
            existing += 1
            skipped_items.append(
                {
                    "hub_task_id": hub_task["task_id"],
                    "local_task_id": already["task_id"],
                    "reason": "already pulled",
                }
            )
            continue
        payload = _pull_payload(hub_task, target_workspace)
        local_task = create_task(payload, db_path=local_path, actor="sync-pull")
        _mark_hub_pulled(global_path, hub_task["task_id"], target_workspace["workspace_id"])
        _upsert_sync_link(
            local_path,
            local_task_id=local_task["task_id"],
            hub_task_id=hub_task["task_id"],
            source_workspace_id=hub_task.get("source_workspace_id", ""),
            target_workspace_id=target_workspace["workspace_id"],
        )
        record_sync_event(
            "pull",
            {"workspace_slug": target_workspace["workspace_slug"]},
            local_task_id=local_task["task_id"],
            hub_task_id=hub_task["task_id"],
            source_workspace_id=hub_task.get("source_workspace_id", ""),
            target_workspace_id=target_workspace["workspace_id"],
            db_path=local_path,
        )
        record_sync_event(
            "pull_delivered",
            {"workspace_slug": target_workspace["workspace_slug"]},
            local_task_id=local_task["task_id"],
            hub_task_id=hub_task["task_id"],
            source_workspace_id=hub_task.get("source_workspace_id", ""),
            target_workspace_id=target_workspace["workspace_id"],
            db_path=global_path,
        )
        created += 1
        pulled_items.append(
            {
                "hub_task_id": hub_task["task_id"],
                "local_task_id": local_task["task_id"],
                "title": local_task["title"],
                "routing_status": "pulled",
            }
        )

    return {
        "local_db": str(local_path),
        "global_db": str(global_path),
        "workspace": target_workspace,
        "count": len(pulled_items),
        "created": created,
        "existing": existing,
        "items": pulled_items,
        "skipped": skipped_items,
    }


def source_db_path_for_workspace(workspace: dict[str, Any]) -> Path:
    canonical_path = workspace.get("canonical_path")
    if not canonical_path:
        raise ValueError("workspace has no canonical_path")
    return Path(canonical_path) / APP_DIR_NAME / DEFAULT_DB_NAME


def fetch_origin_task(
    hub_task_id: str,
    global_db_path: Path | str | None = None,
    source_db_path: Path | str | None = None,
) -> dict[str, Any]:
    global_path = _db_path_or_default(global_db_path, global_scope=True)
    init_db(global_path)
    hub_task = get_task(hub_task_id, db_path=global_path)
    origin_task_id = hub_task.get("origin_task_id")
    source_workspace_id = hub_task.get("source_workspace_id")
    if not origin_task_id:
        raise ValueError(f"Hub task has no origin_task_id: {hub_task_id}")
    if not source_workspace_id:
        raise ValueError(f"Hub task has no source_workspace_id: {hub_task_id}")

    source_workspace = get_workspace(source_workspace_id, db_path=global_path)
    source_path = Path(source_db_path) if source_db_path else source_db_path_for_workspace(source_workspace)
    if not source_path.exists():
        raise FileNotFoundError(f"Source DB not found: {source_path}")
    origin_task = get_task(origin_task_id, db_path=source_path)
    registry_records: dict[str, Any] = {}
    for key in (
        "source_principal_id",
        "target_principal_id",
        "proposed_by_principal_id",
        "approved_by_principal_id",
        "assigned_by_principal_id",
    ):
        identifier = origin_task.get(key)
        if identifier:
            try:
                registry_records[key] = get_principal(identifier, db_path=source_path)
            except Exception:
                registry_records[key] = {"missing": identifier}
    if origin_task.get("harness_id"):
        try:
            registry_records["harness"] = get_harness_profile(origin_task["harness_id"], db_path=source_path)
        except Exception:
            registry_records["harness"] = {"missing": origin_task["harness_id"]}
    return {
        "hub_task_id": hub_task_id,
        "origin_task_id": origin_task_id,
        "source_workspace_id": source_workspace_id,
        "source_workspace": source_workspace,
        "source_db": str(source_path),
        "hub_task": hub_task,
        "origin_task": origin_task,
        "origin_registry": registry_records,
    }
