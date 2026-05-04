from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any
import hashlib
import json
import uuid

from .branding import TASK_ID_PREFIX
from .config import default_db_path
from .store import init_db, row_to_dict, rows_to_dicts, transaction
from .timeutil import duration_until_iso, iso_now, parse_datetime, seconds_until_iso


ACTIVE_STATUSES = {"inbox", "scheduled", "notified", "acknowledged", "snoozed", "in_progress"}
VALID_STATUSES = ACTIVE_STATUSES | {"completed", "cancelled", "archived"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
_ENSURED_DB_PATHS: set[str] = set()
_ENSURE_DB_LOCK = Lock()
VALID_TASK_KINDS = {"reminder", "action", "delegated_task", "automation", "workflow_run", "review_gate"}
TASK_KIND_ALIASES = {
    "delegated-task": "delegated_task",
    "workflow-run": "workflow_run",
    "review-gate": "review_gate",
}
VALID_EXECUTION_MODES = {"manual", "agent_assisted", "agent_autonomous", "scripted", "external"}
VALID_SCHEDULE_KINDS = {"none", "due_once", "recurring", "event_triggered"}
VALID_CONTROLLER_STATUSES = {
    "",
    "active",
    "paused",
    "blocked",
    "stale",
    "failed",
    "catching_up",
    "completed",
    "test",
    "unknown",
}
APPROVAL_DECISIONS = {"approved", "rejected", "changes_requested"}
APPROVAL_DECISION_ALIASES = {
    "approve": "approved",
    "reject": "rejected",
    "request_changes": "changes_requested",
    "change_requested": "changes_requested",
}
EXTERNAL_SIDE_EFFECT_CLASSES = {"external_write", "irreversible", "sensitive_decision"}
RAW_DELIVERY_SECRET_FIELDS = {
    "api_key",
    "authorization",
    "bearer",
    "email",
    "password",
    "secret",
    "secret_value",
    "token",
    "url",
    "webhook",
    "webhook_url",
}
CLAIM_BLOCKING_CONTROLLER_STATUSES = {"paused", "blocked", "failed", "completed"}
DEFAULT_EXECUTION_MODE_BY_KIND = {
    "reminder": "manual",
    "action": "manual",
    "delegated_task": "agent_assisted",
    "automation": "agent_assisted",
    "workflow_run": "agent_assisted",
    "review_gate": "manual",
}
CLAIMABLE_STATUSES = {"inbox", "scheduled", "notified", "acknowledged", "snoozed"}
ALLOWED_TRANSITIONS = {
    "inbox": {"scheduled", "in_progress", "completed", "cancelled", "archived"},
    "scheduled": {"notified", "acknowledged", "snoozed", "in_progress", "completed", "cancelled", "archived"},
    "notified": {"acknowledged", "snoozed", "in_progress", "completed", "cancelled", "archived"},
    "acknowledged": {"scheduled", "snoozed", "in_progress", "completed", "cancelled", "archived"},
    "snoozed": {"scheduled", "notified", "acknowledged", "in_progress", "completed", "cancelled", "archived"},
    "in_progress": {"acknowledged", "snoozed", "completed", "cancelled", "archived"},
    "completed": {"inbox", "archived"},
    "cancelled": {"inbox", "archived"},
    "archived": {"inbox"},
}


def make_task_id() -> str:
    return f"{TASK_ID_PREFIX}_{uuid.uuid4().hex}"


def make_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def make_job_id() -> str:
    return f"job_{uuid.uuid4().hex}"


def make_attempt_id() -> str:
    return f"att_{uuid.uuid4().hex}"


def normalized_fingerprint(payload: dict[str, Any]) -> str:
    parts = [
        str(payload.get("title", "")).strip().lower(),
        str(payload.get("next_action", "")).strip().lower(),
        str(payload.get("source_workspace", "")).strip().lower(),
        str(payload.get("source_repo", "")).strip().lower(),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def source_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_transition(current: str, new: str) -> None:
    if current == new:
        return
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise ValueError(f"Invalid status transition: {current} -> {new}")


def db_path_or_default(db_path: Path | str | None = None) -> Path:
    return Path(db_path) if db_path else default_db_path()


def ensure_db(db_path: Path | str | None = None) -> Path:
    path = db_path_or_default(db_path)
    key = str(path.expanduser().resolve(strict=False))
    with _ENSURE_DB_LOCK:
        if key not in _ENSURED_DB_PATHS or not path.exists():
            init_db(path)
            _ENSURED_DB_PATHS.add(key)
    return path


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        value = default
    return str(value).strip()


def _normalize_choice(value: Any, valid_values: set[str], aliases: dict[str, str] | None = None, field: str = "value") -> str:
    normalized = _clean_text(value).lower().replace(" ", "_").replace("-", "_")
    normalized = (aliases or {}).get(normalized, normalized)
    if normalized not in valid_values:
        raise ValueError(f"{field} must be one of {sorted(valid_values)}")
    return normalized


def normalize_task_kind(value: Any = "action") -> str:
    return _normalize_choice(value or "action", VALID_TASK_KINDS, TASK_KIND_ALIASES, "task_kind")


def normalize_execution_mode(value: Any = "manual") -> str:
    return _normalize_choice(value or "manual", VALID_EXECUTION_MODES, field="execution_mode")


def normalize_schedule_kind(value: Any = "none") -> str:
    return _normalize_choice(value or "none", VALID_SCHEDULE_KINDS, field="schedule_kind")


def normalize_controller_status(value: Any = "") -> str:
    return _normalize_choice(value or "", VALID_CONTROLLER_STATUSES, field="controller_status")


def _json_object_from_payload(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key) or {}
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a JSON object")
        return value
    return {}


def _event(conn, task_id: str, event_type: str, actor: str, payload: dict[str, Any] | None = None) -> None:
    conn.execute(
        """
        INSERT INTO task_events(event_id, task_id, event_type, actor, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (make_event_id(), task_id, event_type, actor, _json(payload or {}), iso_now()),
    )


def append_task_event(
    task_id: str,
    event_type: str,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        _event(conn, task_id, event_type, actor, payload or {})
        conn.execute(
            "UPDATE tasks SET updated_at = ?, last_agent_update_at = ? WHERE task_id = ?",
            (timestamp, timestamp, task_id),
        )
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def list_task_events(
    task_id: str,
    db_path: Path | str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    path = ensure_db(db_path)
    with transaction(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM task_events
            WHERE task_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (task_id, int(limit)),
        ).fetchall()
    events = rows_to_dicts(rows)
    for event in events:
        payload = event.pop("payload_json", "{}") or "{}"
        try:
            event["payload"] = json.loads(payload)
        except json.JSONDecodeError:
            event["payload"] = {"raw": payload}
    return events


def _get_by_unique(conn, idempotency_key: str | None, fingerprint: str) -> dict[str, Any] | None:
    if idempotency_key:
        row = conn.execute("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if row:
            return row_to_dict(row)
    row = conn.execute("SELECT * FROM tasks WHERE fingerprint_sha256 = ?", (fingerprint,)).fetchone()
    return row_to_dict(row)


def _get_by_task_id(conn, task_id: str | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return row_to_dict(row)


def create_task(payload: dict[str, Any], db_path: Path | str | None = None, actor: str = "manual") -> dict[str, Any]:
    path = ensure_db(db_path)
    title = str(payload.get("title", "")).strip()
    if not title:
        raise ValueError("title is required")

    priority = str(payload.get("priority", "normal")).strip().lower()
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"priority must be one of {sorted(VALID_PRIORITIES)}")

    status = str(payload.get("status") or ("scheduled" if payload.get("due_at") else "inbox")).strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")

    tags = payload.get("tags") or []
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.split(",") if item.strip()]
    depends_on = payload.get("depends_on") or []
    if isinstance(depends_on, str):
        depends_on = [item.strip() for item in depends_on.split(",") if item.strip()]

    ai_context_pack = payload.get("ai_context_pack") or {}
    if isinstance(ai_context_pack, str):
        ai_context_pack = json.loads(ai_context_pack)

    task_kind = normalize_task_kind(payload.get("task_kind", payload.get("kind", "action")))
    default_execution_mode = DEFAULT_EXECUTION_MODE_BY_KIND[task_kind]
    execution_mode = normalize_execution_mode(payload.get("execution_mode") or default_execution_mode)
    default_schedule_kind = "due_once" if payload.get("due_at") else "none"
    schedule_kind = normalize_schedule_kind(payload.get("schedule_kind") or default_schedule_kind)
    controller_status = normalize_controller_status(
        payload.get("controller_status", "active" if task_kind == "automation" else "")
    )
    execution_contract = _json_object_from_payload(payload, "execution_contract", "execution_contract_json")
    schedule = _json_object_from_payload(payload, "schedule", "schedule_json")
    artifact_contract = _json_object_from_payload(payload, "artifact_contract", "artifact_contract_json")

    due_at = parse_datetime(payload.get("due_at"))
    snooze_until = parse_datetime(payload.get("snooze_until"))
    idempotency_key = payload.get("idempotency_key")
    fingerprint = payload.get("fingerprint_sha256") or normalized_fingerprint(payload)
    timestamp = iso_now()
    task_id = payload.get("task_id") or make_task_id()

    record = {
        "task_id": task_id,
        "idempotency_key": idempotency_key,
        "fingerprint_sha256": fingerprint,
        "title": title,
        "summary": str(payload.get("summary", "")).strip(),
        "next_action": str(payload.get("next_action", payload.get("next", ""))).strip(),
        "detail_md": str(payload.get("detail_md", "")).strip(),
        "status": status,
        "priority": priority,
        "rank": payload.get("rank"),
        "due_at": due_at,
        "snooze_until": snooze_until,
        "ack_at": parse_datetime(payload.get("ack_at")),
        "completed_at": parse_datetime(payload.get("completed_at")),
        "source_agent": str(payload.get("source_agent", actor)).strip() or actor,
        "source_workspace": str(payload.get("source_workspace", "")).strip(),
        "source_workspace_id": _clean_text(payload.get("source_workspace_id")),
        "source_workspace_slug": _clean_text(payload.get("source_workspace_slug"), str(payload.get("source_workspace", ""))),
        "target_workspace_id": _clean_text(payload.get("target_workspace_id")),
        "target_workspace_slug": _clean_text(payload.get("target_workspace_slug")),
        "source_principal_id": _clean_text(payload.get("source_principal_id")),
        "target_principal_id": _clean_text(payload.get("target_principal_id")),
        "proposed_by_principal_id": _clean_text(payload.get("proposed_by_principal_id")),
        "approved_by_principal_id": _clean_text(payload.get("approved_by_principal_id")),
        "assigned_by_principal_id": _clean_text(payload.get("assigned_by_principal_id")),
        "routing_status": _clean_text(payload.get("routing_status"), "local_only") or "local_only",
        "origin_task_id": _clean_text(payload.get("origin_task_id")),
        "hub_task_id": _clean_text(payload.get("hub_task_id")),
        "harness_id": _clean_text(payload.get("harness_id")),
        "policy_profile_id": _clean_text(payload.get("policy_profile_id")),
        "task_kind": task_kind,
        "execution_mode": execution_mode,
        "schedule_kind": schedule_kind,
        "controller_status": controller_status,
        "automation_id": _clean_text(payload.get("automation_id")),
        "parent_task_id": _clean_text(payload.get("parent_task_id")),
        "execution_contract_json": _json(execution_contract),
        "schedule_json": _json(schedule),
        "artifact_contract_json": _json(artifact_contract),
        "source_repo": str(payload.get("source_repo", "")).strip(),
        "source_branch": str(payload.get("source_branch", "")).strip(),
        "source_session_id": str(payload.get("source_session_id", "")).strip(),
        "source_file_path": str(payload.get("source_file_path", "")).strip(),
        "source_content_hash": str(payload.get("source_content_hash", "")).strip(),
        "depends_on_json": _json(depends_on),
        "tags_json": _json(tags),
        "ai_context_pack": _json(ai_context_pack),
        "ai_context_preview": str(payload.get("ai_context_preview", payload.get("summary", ""))).strip()[:280],
        "redaction_level": int(payload.get("redaction_level", 0)),
        "agent_claim_owner": str(payload.get("agent_claim_owner", "")).strip(),
        "agent_claim_until": parse_datetime(payload.get("agent_claim_until")),
        "agent_claim_status": str(payload.get("agent_claim_status", "")).strip(),
        "claimed_at": parse_datetime(payload.get("claimed_at")),
        "context_pack_version": str(payload.get("context_pack_version", "0.1")).strip() or "0.1",
        "last_imported_hash": str(payload.get("last_imported_hash", "")).strip(),
        "last_exported_hash": str(payload.get("last_exported_hash", "")).strip(),
        "last_imported_at": parse_datetime(payload.get("last_imported_at")),
        "last_exported_at": parse_datetime(payload.get("last_exported_at")),
        "conflict_status": str(payload.get("conflict_status", "")).strip(),
        "last_human_update_at": timestamp if actor == "manual" else None,
        "last_agent_update_at": timestamp if actor != "manual" else None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    with transaction(path) as conn:
        existing_by_id = _get_by_task_id(conn, task_id)
        if existing_by_id:
            return existing_by_id

        existing = _get_by_unique(conn, idempotency_key, fingerprint)
        if existing:
            return existing

        columns = list(record.keys())
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO tasks({', '.join(columns)}) VALUES ({placeholders})",
            tuple(record[column] for column in columns),
        )
        _event(conn, task_id, "created", actor, {"title": title, "priority": priority, "status": status})
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(row)


def get_task(task_id: str, db_path: Path | str | None = None) -> dict[str, Any]:
    path = ensure_db(db_path)
    with transaction(path) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        result = row_to_dict(row)
        if not result:
            raise KeyError(f"Task not found: {task_id}")
        return result


def list_tasks(
    db_path: Path | str | None = None,
    status: str | None = None,
    due: bool = False,
    task_kind: str | None = None,
    controller_status: str | None = None,
    source_principal_id: str | None = None,
    target_principal_id: str | None = None,
    harness_id: str | None = None,
    parent_task_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    path = ensure_db(db_path)
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if due:
        where.append("status IN ({})".format(",".join("?" for _ in ACTIVE_STATUSES)))
        params.extend(sorted(ACTIVE_STATUSES))
        where.append("COALESCE(snooze_until, due_at) IS NOT NULL")
        where.append("COALESCE(snooze_until, due_at) <= ?")
        params.append(iso_now())
    if task_kind:
        where.append("task_kind = ?")
        params.append(normalize_task_kind(task_kind))
    if controller_status is not None:
        where.append("controller_status = ?")
        params.append(normalize_controller_status(controller_status))
    if source_principal_id is not None:
        where.append("source_principal_id = ?")
        params.append(source_principal_id)
    if target_principal_id is not None:
        where.append("target_principal_id = ?")
        params.append(target_principal_id)
    if harness_id is not None:
        where.append("harness_id = ?")
        params.append(harness_id)
    if parent_task_id is not None:
        where.append("parent_task_id = ?")
        params.append(parent_task_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT * FROM tasks
        {clause}
        ORDER BY
            CASE priority
                WHEN 'urgent' THEN 0
                WHEN 'high' THEN 1
                WHEN 'normal' THEN 2
                ELSE 3
            END,
            CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
            rank ASC,
            COALESCE(snooze_until, due_at, created_at) ASC
        LIMIT ?
    """
    params.append(limit)
    with transaction(path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return rows_to_dicts(rows)


def list_task_children(
    parent_task_id: str,
    db_path: Path | str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return list_tasks(db_path=db_path, parent_task_id=parent_task_id, limit=limit)


def get_task_tree(
    root_task_id: str | None = None,
    db_path: Path | str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    path = ensure_db(db_path)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def task_sort_key(task: dict[str, Any]) -> tuple[int, int, str]:
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        rank = task.get("rank")
        return (priority_order.get(task.get("priority"), 4), 999999 if rank is None else int(rank), task.get("created_at") or "")

    with transaction(path) as conn:
        if root_task_id:
            roots = rows_to_dicts(conn.execute("SELECT * FROM tasks WHERE task_id = ?", (root_task_id,)).fetchall())
            if not roots:
                raise KeyError(f"Task not found: {root_task_id}")
        else:
            roots = rows_to_dicts(
                conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE parent_task_id = ''
                    ORDER BY
                        CASE priority
                            WHEN 'urgent' THEN 0
                            WHEN 'high' THEN 1
                            WHEN 'normal' THEN 2
                            ELSE 3
                        END,
                        CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
                        rank ASC,
                        created_at ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            )

        def visit(task: dict[str, Any], depth: int) -> None:
            if len(items) >= limit or task["task_id"] in seen:
                return
            seen.add(task["task_id"])
            node = dict(task)
            node["tree_depth"] = depth
            items.append(node)
            children = rows_to_dicts(
                conn.execute("SELECT * FROM tasks WHERE parent_task_id = ?", (task["task_id"],)).fetchall()
            )
            for child in sorted(children, key=task_sort_key):
                visit(child, depth + 1)

        for root in roots:
            visit(root, 0)
    return items


def task_registry_summary(db_path: Path | str | None = None) -> dict[str, Any]:
    path = ensure_db(db_path)
    with transaction(path) as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"]
        unbound_source = conn.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE source_principal_id = ''"
        ).fetchone()["count"]
        targeted = conn.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE target_principal_id != ''"
        ).fetchone()["count"]
        unassigned_targeted = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM tasks
            WHERE target_principal_id != ''
              AND assigned_by_principal_id = ''
            """
        ).fetchone()["count"]
        parented = conn.execute("SELECT COUNT(*) AS count FROM tasks WHERE parent_task_id != ''").fetchone()["count"]
        by_status = {
            row["status"]: row["count"]
            for row in conn.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
        }
        by_kind = {
            row["task_kind"]: row["count"]
            for row in conn.execute("SELECT task_kind, COUNT(*) AS count FROM tasks GROUP BY task_kind").fetchall()
        }
        unbound_rows = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM tasks
                WHERE source_principal_id = ''
                ORDER BY updated_at DESC
                LIMIT 10
                """
            ).fetchall()
        )
    return {
        "total_tasks": total,
        "tasks_with_source_principal": total - unbound_source,
        "tasks_missing_source_principal": unbound_source,
        "targeted_tasks": targeted,
        "targeted_tasks_missing_assignment": unassigned_targeted,
        "parented_tasks": parented,
        "by_status": by_status,
        "by_kind": by_kind,
        "recent_unbound_tasks": unbound_rows,
    }


def backfill_missing_task_bindings(
    source_principal_id: str,
    source_workspace_id: str,
    source_workspace_slug: str,
    source_workspace: str,
    db_path: Path | str | None = None,
    limit: int = 100,
    dry_run: bool = True,
    actor: str = "repair",
) -> dict[str, Any]:
    if not source_principal_id.strip():
        raise ValueError("source_principal_id is required")
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path, immediate=not dry_run) as conn:
        candidates = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM tasks
                WHERE source_principal_id = ''
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )
        if not dry_run:
            for task in candidates:
                conn.execute(
                    """
                    UPDATE tasks
                    SET source_principal_id = ?,
                        proposed_by_principal_id = CASE
                            WHEN proposed_by_principal_id = '' THEN ? ELSE proposed_by_principal_id
                        END,
                        source_workspace_id = CASE
                            WHEN source_workspace_id = '' THEN ? ELSE source_workspace_id
                        END,
                        source_workspace_slug = CASE
                            WHEN source_workspace_slug = '' THEN ? ELSE source_workspace_slug
                        END,
                        source_workspace = CASE
                            WHEN source_workspace = '' THEN ? ELSE source_workspace
                        END,
                        updated_at = ?,
                        last_human_update_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        source_principal_id,
                        source_principal_id,
                        source_workspace_id,
                        source_workspace_slug,
                        source_workspace,
                        timestamp,
                        timestamp,
                        task["task_id"],
                    ),
                )
                _event(
                    conn,
                    task["task_id"],
                    "binding_repaired",
                    actor,
                    {
                        "source_principal_id": source_principal_id,
                        "source_workspace_id": source_workspace_id,
                    },
                )
    return {
        "dry_run": dry_run,
        "count": len(candidates),
        "task_ids": [task["task_id"] for task in candidates],
    }


def list_automations(
    db_path: Path | str | None = None,
    controller_status: str | None = None,
    include_runs: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    path = ensure_db(db_path)
    kinds = ["automation", "workflow_run"] if include_runs else ["automation"]
    placeholders = ",".join("?" for _ in kinds)
    where = [f"task_kind IN ({placeholders})"]
    params: list[Any] = list(kinds)
    if controller_status is not None:
        where.append("controller_status = ?")
        params.append(normalize_controller_status(controller_status))
    sql = f"""
        SELECT * FROM tasks
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE controller_status
                WHEN 'active' THEN 0
                WHEN 'catching_up' THEN 1
                WHEN 'blocked' THEN 2
                WHEN 'failed' THEN 3
                WHEN 'stale' THEN 4
                WHEN 'paused' THEN 5
                ELSE 6
            END,
            COALESCE(snooze_until, due_at, updated_at, created_at) ASC
        LIMIT ?
    """
    params.append(limit)
    with transaction(path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return rows_to_dicts(rows)


def update_task(
    task_id: str,
    updates: dict[str, Any],
    db_path: Path | str | None = None,
    actor: str = "manual",
) -> dict[str, Any]:
    path = ensure_db(db_path)
    allowed = {
        "title",
        "summary",
        "next_action",
        "detail_md",
        "status",
        "priority",
        "rank",
        "due_at",
        "snooze_until",
        "ack_at",
        "completed_at",
        "source_workspace",
        "source_workspace_id",
        "source_workspace_slug",
        "target_workspace_id",
        "target_workspace_slug",
        "source_principal_id",
        "target_principal_id",
        "proposed_by_principal_id",
        "approved_by_principal_id",
        "assigned_by_principal_id",
        "routing_status",
        "origin_task_id",
        "hub_task_id",
        "harness_id",
        "policy_profile_id",
        "task_kind",
        "execution_mode",
        "schedule_kind",
        "controller_status",
        "automation_id",
        "parent_task_id",
        "source_repo",
        "source_branch",
        "source_session_id",
        "source_file_path",
        "source_content_hash",
        "agent_claim_owner",
        "agent_claim_until",
        "agent_claim_status",
        "claimed_at",
        "context_pack_version",
        "last_imported_hash",
        "last_exported_hash",
        "last_imported_at",
        "last_exported_at",
        "conflict_status",
        "ai_context_preview",
        "redaction_level",
    }
    json_updates = {}
    if "tags" in updates:
        json_updates["tags_json"] = _json(updates["tags"])
    if "depends_on" in updates:
        json_updates["depends_on_json"] = _json(updates["depends_on"])
    if "ai_context_pack" in updates:
        json_updates["ai_context_pack"] = _json(updates["ai_context_pack"])
    if "execution_contract" in updates:
        json_updates["execution_contract_json"] = _json(updates["execution_contract"])
    if "schedule" in updates:
        json_updates["schedule_json"] = _json(updates["schedule"])
    if "artifact_contract" in updates:
        json_updates["artifact_contract_json"] = _json(updates["artifact_contract"])

    normalized: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key in {
            "due_at",
            "snooze_until",
            "ack_at",
            "completed_at",
            "agent_claim_until",
            "claimed_at",
            "last_imported_at",
            "last_exported_at",
        }:
            normalized[key] = parse_datetime(value)
        elif key == "priority":
            priority = str(value).lower()
            if priority not in VALID_PRIORITIES:
                raise ValueError(f"priority must be one of {sorted(VALID_PRIORITIES)}")
            normalized[key] = priority
        elif key == "status":
            status = str(value).lower()
            if status not in VALID_STATUSES:
                raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
            normalized[key] = status
        elif key == "task_kind":
            normalized[key] = normalize_task_kind(value)
        elif key == "execution_mode":
            normalized[key] = normalize_execution_mode(value)
        elif key == "schedule_kind":
            normalized[key] = normalize_schedule_kind(value)
        elif key == "controller_status":
            normalized[key] = normalize_controller_status(value)
        else:
            normalized[key] = value

    normalized.update(json_updates)
    normalized["updated_at"] = iso_now()
    normalized["last_human_update_at" if actor == "manual" else "last_agent_update_at"] = normalized["updated_at"]

    assignments = ", ".join(f"{key} = ?" for key in normalized)
    values = list(normalized.values()) + [task_id]
    with transaction(path) as conn:
        existing = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not existing:
            raise KeyError(f"Task not found: {task_id}")
        existing_task = row_to_dict(existing)
        if "status" in normalized:
            validate_transition(existing_task["status"], normalized["status"])
        conn.execute(f"UPDATE tasks SET {assignments} WHERE task_id = ?", values)
        _event(conn, task_id, "updated", actor, {"fields": sorted(normalized.keys())})
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(row)


def assign_task(
    task_id: str,
    target_principal_id: str,
    assigned_by_principal_id: str,
    db_path: Path | str | None = None,
    harness_id: str = "",
    policy_profile_id: str = "",
    routing_status: str = "assigned",
    actor: str = "orchestrator",
) -> dict[str, Any]:
    if not target_principal_id.strip():
        raise ValueError("target_principal_id is required")
    if not assigned_by_principal_id.strip():
        raise ValueError("assigned_by_principal_id is required")
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        task = row_to_dict(row)
        if task["status"] not in ACTIVE_STATUSES:
            raise ValueError(f"Task is not assignable in status {task['status']}")
        conn.execute(
            """
            UPDATE tasks
            SET target_principal_id = ?,
                assigned_by_principal_id = ?,
                harness_id = CASE WHEN ? = '' THEN harness_id ELSE ? END,
                policy_profile_id = CASE WHEN ? = '' THEN policy_profile_id ELSE ? END,
                routing_status = ?,
                updated_at = ?,
                last_agent_update_at = ?
            WHERE task_id = ?
            """,
            (
                target_principal_id,
                assigned_by_principal_id,
                harness_id,
                harness_id,
                policy_profile_id,
                policy_profile_id,
                routing_status,
                timestamp,
                timestamp,
                task_id,
            ),
        )
        _event(
            conn,
            task_id,
            "assigned",
            actor,
            {
                "target_principal_id": target_principal_id,
                "assigned_by_principal_id": assigned_by_principal_id,
                "harness_id": harness_id,
                "routing_status": routing_status,
            },
        )
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def claim_task(
    task_id: str,
    owner: str,
    lease_seconds: int = 1800,
    db_path: Path | str | None = None,
    target_principal_id: str = "",
) -> dict[str, Any]:
    if not owner.strip():
        raise ValueError("owner is required")
    path = ensure_db(db_path)
    timestamp = iso_now()
    claim_until = seconds_until_iso(lease_seconds)
    target_principal_id = target_principal_id.strip()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        task = row_to_dict(row)
        if task.get("controller_status") in CLAIM_BLOCKING_CONTROLLER_STATUSES:
            raise ValueError(f"Task controller status blocks claiming: {task['controller_status']}")
        existing_owner = task.get("agent_claim_owner") or ""
        existing_until = task.get("agent_claim_until")
        active_other_claim = existing_owner and existing_owner != owner and existing_until and existing_until > timestamp
        if active_other_claim:
            raise ValueError(f"Task is claimed by {existing_owner}")
        if task["status"] == "in_progress" and existing_owner and existing_owner != owner:
            raise ValueError(f"Task is already in progress by {existing_owner}")
        if task["status"] != "in_progress":
            if task["status"] not in CLAIMABLE_STATUSES:
                raise ValueError(f"Task is not claimable in status {task['status']}")
            if not _dependencies_satisfied(conn, task.get("depends_on") or []):
                raise ValueError("Task dependencies are not completed")
            validate_transition(task["status"], "in_progress")
        conn.execute(
            """
            UPDATE tasks
            SET status = 'in_progress',
                target_principal_id = CASE
                    WHEN target_principal_id = '' AND ? != '' THEN ?
                    ELSE target_principal_id
                END,
                routing_status = CASE
                    WHEN routing_status = '' AND ? != '' THEN 'assigned'
                    ELSE routing_status
                END,
                agent_claim_owner = ?,
                agent_claim_until = ?,
                agent_claim_status = 'claimed',
                claimed_at = CASE WHEN claimed_at IS NULL THEN ? ELSE claimed_at END,
                last_agent_update_at = ?,
                updated_at = ?
            WHERE task_id = ?
            """,
            (
                target_principal_id,
                target_principal_id,
                target_principal_id,
                owner.strip(),
                claim_until,
                timestamp,
                timestamp,
                timestamp,
                task_id,
            ),
        )
        _event(
            conn,
            task_id,
            "claimed",
            owner.strip(),
            {"claim_until": claim_until, "target_principal_id": target_principal_id},
        )
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def ack_task(task_id: str, db_path: Path | str | None = None, actor: str = "manual") -> dict[str, Any]:
    timestamp = iso_now()
    return update_task(task_id, {"status": "acknowledged", "ack_at": timestamp}, db_path=db_path, actor=actor)


def complete_task(task_id: str, db_path: Path | str | None = None, actor: str = "manual") -> dict[str, Any]:
    timestamp = iso_now()
    updated = update_task(task_id, {"status": "completed", "completed_at": timestamp}, db_path=db_path, actor=actor)
    with transaction(ensure_db(db_path)) as conn:
        _event(conn, task_id, "completed", actor, {"completed_at": timestamp})
    return updated


def request_review_gate(
    task_id: str,
    reason: str = "",
    actor: str = "manual",
    gate_type: str = "pre_execution",
    reviewer_principal_id: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create or return an idempotent review-gate task for a risky subject task."""
    subject = get_task(task_id, db_path=db_path)
    gate_type = _clean_text(gate_type, "pre_execution") or "pre_execution"
    reason = _clean_text(reason)
    reviewer_principal_id = _clean_text(reviewer_principal_id)
    contract = subject.get("execution_contract") or {}
    gate_payload = {
        "title": f"검토 필요: {subject['title']}",
        "summary": reason or "실행 전 사람의 검토와 승인이 필요하다.",
        "next_action": "승인, 거절, 변경요청 중 하나를 기록한다.",
        "detail_md": (
            f"Subject task: `{subject['task_id']}`\n\n"
            f"Reason: {reason or 'approval required'}\n\n"
            "This review gate is the human control point before risky execution or external side effects."
        ),
        "status": "scheduled",
        "priority": "urgent" if subject.get("priority") == "urgent" else "high",
        "task_kind": "review_gate",
        "execution_mode": "manual",
        "schedule_kind": "none",
        "controller_status": "active",
        "parent_task_id": subject["task_id"],
        "source_workspace": subject.get("source_workspace", ""),
        "source_workspace_id": subject.get("source_workspace_id", ""),
        "source_workspace_slug": subject.get("source_workspace_slug", ""),
        "target_workspace_id": subject.get("target_workspace_id", ""),
        "target_workspace_slug": subject.get("target_workspace_slug", ""),
        "source_principal_id": subject.get("source_principal_id", ""),
        "target_principal_id": reviewer_principal_id or subject.get("source_principal_id", ""),
        "proposed_by_principal_id": subject.get("proposed_by_principal_id") or subject.get("source_principal_id", ""),
        "harness_id": subject.get("harness_id", ""),
        "policy_profile_id": subject.get("policy_profile_id", ""),
        "routing_status": subject.get("routing_status", "local_only"),
        "idempotency_key": f"review_gate:{subject['task_id']}:{gate_type}",
        "tags": sorted(set((subject.get("tags") or []) + ["review-gate", gate_type])),
        "execution_contract": {
            "review_gate": {
                "subject_task_id": subject["task_id"],
                "gate_type": gate_type,
                "required_decision": "approved",
                "requested_by": actor,
                "requested_reason": reason,
            },
            "risk_tier": contract.get("risk_tier", "medium"),
            "side_effect_class": contract.get("side_effect_class", "none"),
            "approval_required": True,
            "store_raw_cot": False,
        },
        "artifact_contract": {
            "review_outcome_required": True,
            "subject_task_id": subject["task_id"],
        },
    }
    gate = create_task(gate_payload, db_path=db_path, actor=actor)
    append_task_event(
        subject["task_id"],
        "review_gate_requested",
        actor,
        {
            "review_gate_task_id": gate["task_id"],
            "gate_type": gate_type,
            "reason": reason,
            "reviewer_principal_id": reviewer_principal_id,
        },
        db_path=db_path,
    )
    return {"subject_task": get_task(subject["task_id"], db_path=db_path), "review_gate": gate}


def decide_review_gate(
    gate_task_id: str,
    decision: str,
    approver_principal_id: str,
    reason: str = "",
    actor: str = "manual",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Apply a review-gate decision to the gate and its subject task."""
    gate = get_task(gate_task_id, db_path=db_path)
    if gate.get("task_kind") != "review_gate":
        raise ValueError("gate_task_id must reference a review_gate task")
    gate_contract = gate.get("execution_contract") or {}
    gate_info = gate_contract.get("review_gate") if isinstance(gate_contract.get("review_gate"), dict) else {}
    subject_task_id = _clean_text(gate_info.get("subject_task_id") or gate.get("parent_task_id"))
    if not subject_task_id:
        raise ValueError("review gate does not reference a subject task")

    gate_after_approval = record_approval_decision(
        gate_task_id,
        decision,
        approver_principal_id=approver_principal_id,
        reason=reason,
        actor=actor,
        db_path=db_path,
    )
    subject_after_approval = record_approval_decision(
        subject_task_id,
        decision,
        approver_principal_id=approver_principal_id,
        reason=f"review_gate:{gate_task_id} {reason}".strip(),
        actor=actor,
        db_path=db_path,
    )
    append_task_event(
        subject_task_id,
        "review_gate_decision",
        actor,
        {
            "review_gate_task_id": gate_task_id,
            "decision": APPROVAL_DECISION_ALIASES.get(decision.strip().lower().replace("-", "_"), decision),
            "approver_principal_id": approver_principal_id,
            "reason": reason,
        },
        db_path=db_path,
    )
    completed_gate = complete_task(gate_task_id, db_path=db_path, actor=actor)
    completed_gate = update_task(
        gate_task_id,
        {"controller_status": "completed"},
        db_path=db_path,
        actor=actor,
    )
    return {
        "review_gate": completed_gate,
        "review_gate_approval": gate_after_approval,
        "subject_task": subject_after_approval,
    }


def _normalize_delivery_items(
    task: dict[str, Any],
    channel: str = "",
    recipient_ref: str = "",
    include_artifacts: list[str] | None = None,
    requires_review: bool | None = None,
) -> list[dict[str, Any]]:
    artifact_contract = task.get("artifact_contract") or {}
    raw_items = artifact_contract.get("delivery") if isinstance(artifact_contract, dict) else []
    items = [item for item in (raw_items or []) if isinstance(item, dict)]
    channel = _clean_text(channel)
    recipient_ref = _clean_text(recipient_ref)
    include_artifacts = [str(item).strip() for item in (include_artifacts or []) if str(item).strip()]

    if channel:
        items = [item for item in items if _clean_text(item.get("channel")) == channel]
    if not items and (channel or recipient_ref or include_artifacts):
        items = [
            {
                "channel": channel or "external",
                "recipient_ref": recipient_ref,
                "include_artifacts": include_artifacts,
                "requires_review": True if requires_review is None else requires_review,
            }
        ]

    normalized: list[dict[str, Any]] = []
    for item in items:
        delivery = dict(item)
        if channel:
            delivery["channel"] = channel
        if recipient_ref:
            delivery["recipient_ref"] = recipient_ref
        if include_artifacts:
            delivery["include_artifacts"] = include_artifacts
        delivery["channel"] = _clean_text(delivery.get("channel"), "external") or "external"
        delivery["recipient_ref"] = _clean_text(delivery.get("recipient_ref"))
        delivery["include_artifacts"] = [
            str(value).strip()
            for value in (delivery.get("include_artifacts") or [])
            if str(value).strip()
        ]
        if requires_review is None:
            delivery["requires_review"] = bool(delivery.get("requires_review", True))
        else:
            delivery["requires_review"] = bool(requires_review)
        normalized.append(delivery)
    return normalized


def _delivery_policy_violations(deliveries: list[dict[str, Any]]) -> list[str]:
    violations: list[str] = []
    for index, item in enumerate(deliveries, start=1):
        if not item.get("recipient_ref"):
            violations.append(f"delivery[{index}] requires recipient_ref")
        for key, value in item.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key.endswith("_ref") or normalized_key in {"recipient_ref", "auth_profile_ref"}:
                continue
            if normalized_key in RAW_DELIVERY_SECRET_FIELDS and value:
                violations.append(f"delivery[{index}] must use a reference field instead of raw {key}")
    return violations


def request_delivery_dry_run(
    task_id: str,
    channel: str = "",
    recipient_ref: str = "",
    include_artifacts: list[str] | None = None,
    requires_review: bool | None = None,
    reason: str = "",
    actor: str = "manual",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Record a dry-run external delivery request without sending anything."""
    task = get_task(task_id, db_path=db_path)
    deliveries = _normalize_delivery_items(
        task,
        channel=channel,
        recipient_ref=recipient_ref,
        include_artifacts=include_artifacts,
        requires_review=requires_review,
    )
    if not deliveries:
        raise ValueError("delivery dry-run requires artifact_contract.delivery or --channel/--recipient-ref")

    contract = task.get("execution_contract") or {}
    side_effect_class = _clean_text(contract.get("side_effect_class"), "none").lower().replace("-", "_")
    requires_human_review = any(item.get("requires_review", True) for item in deliveries)
    requires_human_review = requires_human_review or side_effect_class in EXTERNAL_SIDE_EFFECT_CLASSES
    approved = bool(task.get("approved_by_principal_id"))
    policy_violations = _delivery_policy_violations(deliveries)
    timestamp = iso_now()
    payload = {
        "dry_run": True,
        "real_send": False,
        "deliveries": deliveries,
        "requires_review": requires_human_review,
        "approved": approved,
        "reason": reason,
        "side_effect_class": side_effect_class,
        "requested_at": timestamp,
    }
    append_task_event(task_id, "delivery_requested", actor, payload, db_path=db_path)

    if policy_violations:
        blocked_payload = {**payload, "policy_violations": policy_violations}
        append_task_event(task_id, "delivery_policy_blocked", actor, blocked_payload, db_path=db_path)
        blocked_task = block_task(
            task_id,
            reason="; ".join(policy_violations),
            actor=actor,
            db_path=db_path,
            payload=blocked_payload,
        )
        return {
            "allowed": False,
            "result": "policy_blocked",
            "task": blocked_task,
            "deliveries": deliveries,
            "policy_violations": policy_violations,
        }

    if requires_human_review and not approved:
        review_gate_result = request_review_gate(
            task_id,
            reason=reason or "external delivery dry-run requires human review",
            actor=actor,
            gate_type="external_delivery",
            db_path=db_path,
        )
        review_payload = {
            **payload,
            "review_gate_task_id": review_gate_result["review_gate"]["task_id"],
        }
        append_task_event(task_id, "delivery_review_required", actor, review_payload, db_path=db_path)
        return {
            "allowed": False,
            "result": "review_required",
            "task": get_task(task_id, db_path=db_path),
            "review_gate": review_gate_result["review_gate"],
            "deliveries": deliveries,
        }

    append_task_event(task_id, "delivery_dry_run", actor, payload, db_path=db_path)
    append_task_event(
        task_id,
        "artifact_reported",
        actor,
        {
            "artifact_type": "delivery_dry_run",
            "artifact_ref": f"delivery-dry-run:{task_id}:{timestamp}",
            "summary": "External delivery request validated without sending.",
            "deliveries": deliveries,
        },
        db_path=db_path,
    )
    return {
        "allowed": True,
        "result": "dry_run_recorded",
        "task": get_task(task_id, db_path=db_path),
        "deliveries": deliveries,
    }


def reopen_task(task_id: str, db_path: Path | str | None = None, actor: str = "manual") -> dict[str, Any]:
    return update_task(task_id, {"status": "inbox", "completed_at": None}, db_path=db_path, actor=actor)


def snooze_task(
    task_id: str,
    until: str | None = None,
    duration: str | None = None,
    db_path: Path | str | None = None,
    actor: str = "manual",
) -> dict[str, Any]:
    if not until and not duration:
        raise ValueError("until or duration is required")
    snooze_until = duration_until_iso(duration) if duration else parse_datetime(until)
    return update_task(task_id, {"status": "snoozed", "snooze_until": snooze_until}, db_path=db_path, actor=actor)


def get_context_pack(task_id: str, db_path: Path | str | None = None) -> dict[str, Any]:
    task = get_task(task_id, db_path)
    pack = task.get("ai_context_pack") or {}
    if not pack:
        pack = {
            "version": "0.1",
            "objective": task["title"],
            "current_state": task["summary"],
            "next_action": task["next_action"],
            "confidentiality": "internal",
            "redacted": True,
        }
    pack.setdefault("task_kind", task.get("task_kind", "action"))
    pack.setdefault("execution_mode", task.get("execution_mode", "manual"))
    pack.setdefault("schedule_kind", task.get("schedule_kind", "none"))
    pack.setdefault("automation_id", task.get("automation_id", ""))
    pack.setdefault("execution_contract", task.get("execution_contract") or {})
    pack.setdefault("schedule", task.get("schedule") or {})
    pack.setdefault("artifact_contract", task.get("artifact_contract") or {})
    return pack


def _dependencies_satisfied(conn, depends_on: list[str]) -> bool:
    if not depends_on:
        return True
    placeholders = ",".join("?" for _ in depends_on)
    rows = conn.execute(f"SELECT task_id, status FROM tasks WHERE task_id IN ({placeholders})", depends_on).fetchall()
    statuses = {row["task_id"]: row["status"] for row in rows}
    return all(statuses.get(task_id) == "completed" for task_id in depends_on)


def claim_next_task(
    owner: str,
    lease_seconds: int = 1800,
    db_path: Path | str | None = None,
    workspace: str | None = None,
    include_not_due: bool = False,
    target_principal_id: str | None = None,
    include_unassigned: bool = False,
) -> dict[str, Any] | None:
    if not owner.strip():
        raise ValueError("owner is required")
    path = ensure_db(db_path)
    timestamp = iso_now()
    claim_until = seconds_until_iso(lease_seconds)
    where = [
        "status IN ({})".format(",".join("?" for _ in CLAIMABLE_STATUSES)),
        "(agent_claim_owner = '' OR agent_claim_until IS NULL OR agent_claim_until <= ?)",
        "controller_status NOT IN ('paused', 'blocked', 'failed', 'completed')",
    ]
    params: list[Any] = sorted(CLAIMABLE_STATUSES) + [timestamp]
    if workspace:
        where.append("source_workspace = ?")
        params.append(workspace)
    if target_principal_id:
        if include_unassigned:
            where.append("(target_principal_id = ? OR target_principal_id = '')")
        else:
            where.append("target_principal_id = ?")
        params.append(target_principal_id)
    if not include_not_due:
        where.append("COALESCE(snooze_until, due_at) IS NOT NULL")
        where.append("COALESCE(snooze_until, due_at) <= ?")
        params.append(timestamp)

    sql = f"""
        SELECT * FROM tasks
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE priority
                WHEN 'urgent' THEN 0
                WHEN 'high' THEN 1
                WHEN 'normal' THEN 2
                ELSE 3
            END,
            CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
            rank ASC,
            COALESCE(snooze_until, due_at, created_at) ASC
        LIMIT 100
    """
    with transaction(path, immediate=True) as conn:
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            task = row_to_dict(row)
            if not _dependencies_satisfied(conn, task.get("depends_on") or []):
                continue
            validate_transition(task["status"], "in_progress")
            conn.execute(
                """
                UPDATE tasks
                SET status = 'in_progress',
                    agent_claim_owner = ?,
                    agent_claim_until = ?,
                    agent_claim_status = 'claimed',
                    claimed_at = ?,
                    last_agent_update_at = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (owner, claim_until, timestamp, timestamp, timestamp, task["task_id"]),
            )
            _event(conn, task["task_id"], "claimed", owner, {"claim_until": claim_until})
            claimed = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
            return row_to_dict(claimed)
    return None


def release_task(
    task_id: str,
    owner: str | None = None,
    db_path: Path | str | None = None,
    next_status: str = "acknowledged",
) -> dict[str, Any]:
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        task = row_to_dict(row)
        if owner and task.get("agent_claim_owner") and task["agent_claim_owner"] != owner:
            raise ValueError(f"Task is claimed by {task['agent_claim_owner']}")
        validate_transition(task["status"], next_status)
        conn.execute(
            """
            UPDATE tasks
            SET status = ?,
                agent_claim_owner = '',
                agent_claim_until = NULL,
                agent_claim_status = '',
                updated_at = ?,
                last_agent_update_at = ?
            WHERE task_id = ?
            """,
            (next_status, timestamp, timestamp, task_id),
        )
        _event(conn, task_id, "released", owner or "agent", {"next_status": next_status})
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def record_approval_decision(
    task_id: str,
    decision: str,
    approver_principal_id: str,
    reason: str = "",
    actor: str = "manual",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    normalized = decision.strip().lower().replace("-", "_")
    normalized = APPROVAL_DECISION_ALIASES.get(normalized, normalized)
    if normalized not in APPROVAL_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(APPROVAL_DECISIONS)}")
    approver_principal_id = approver_principal_id.strip()
    if not approver_principal_id:
        raise ValueError("approver_principal_id is required")

    path = ensure_db(db_path)
    timestamp = iso_now()
    controller_status = "active" if normalized == "approved" else "blocked"
    approved_by = approver_principal_id if normalized == "approved" else ""
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        conn.execute(
            """
            UPDATE tasks
            SET approved_by_principal_id = ?,
                controller_status = ?,
                updated_at = ?,
                last_human_update_at = ?
            WHERE task_id = ?
            """,
            (approved_by, controller_status, timestamp, timestamp, task_id),
        )
        _event(
            conn,
            task_id,
            "approval_decision",
            actor,
            {
                "decision": normalized,
                "approver_principal_id": approver_principal_id,
                "reason": reason,
            },
        )
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def request_task_stop(
    task_id: str,
    reason: str = "",
    actor: str = "manual",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        conn.execute(
            """
            UPDATE tasks
            SET controller_status = 'paused',
                agent_claim_status = CASE
                    WHEN status = 'in_progress' OR agent_claim_owner != '' THEN 'stop_requested'
                    ELSE agent_claim_status
                END,
                updated_at = ?,
                last_human_update_at = ?
            WHERE task_id = ?
            """,
            (timestamp, timestamp, task_id),
        )
        _event(conn, task_id, "stop_requested", actor, {"reason": reason})
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def observe_task_stop(
    task_id: str,
    reason: str = "",
    actor: str = "runner",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        task = row_to_dict(row)
        next_status = "acknowledged" if task["status"] == "in_progress" else task["status"]
        conn.execute(
            """
            UPDATE tasks
            SET status = ?,
                controller_status = 'paused',
                agent_claim_owner = '',
                agent_claim_until = NULL,
                agent_claim_status = '',
                updated_at = ?,
                last_agent_update_at = ?
            WHERE task_id = ?
            """,
            (next_status, timestamp, timestamp, task_id),
        )
        _event(conn, task_id, "stop_observed", actor, {"reason": reason})
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def block_task(
    task_id: str,
    reason: str,
    actor: str = "runner",
    db_path: Path | str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        task = row_to_dict(row)
        next_status = "acknowledged" if task["status"] == "in_progress" else task["status"]
        conn.execute(
            """
            UPDATE tasks
            SET status = ?,
                controller_status = 'blocked',
                agent_claim_owner = '',
                agent_claim_until = NULL,
                agent_claim_status = '',
                updated_at = ?,
                last_agent_update_at = ?
            WHERE task_id = ?
            """,
            (next_status, timestamp, timestamp, task_id),
        )
        event_payload = {"reason": reason}
        event_payload.update(payload or {})
        _event(conn, task_id, "blocked", actor, event_payload)
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def fail_task(
    task_id: str,
    reason: str,
    actor: str = "runner",
    db_path: Path | str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        task = row_to_dict(row)
        next_status = "acknowledged" if task["status"] == "in_progress" else task["status"]
        conn.execute(
            """
            UPDATE tasks
            SET status = ?,
                controller_status = 'failed',
                agent_claim_owner = '',
                agent_claim_until = NULL,
                agent_claim_status = '',
                updated_at = ?,
                last_agent_update_at = ?
            WHERE task_id = ?
            """,
            (next_status, timestamp, timestamp, task_id),
        )
        event_payload = {"reason": reason}
        event_payload.update(payload or {})
        _event(conn, task_id, "failed", actor, event_payload)
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def heartbeat_claim(
    task_id: str,
    owner: str,
    lease_seconds: int = 1800,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = ensure_db(db_path)
    claim_until = seconds_until_iso(lease_seconds)
    timestamp = iso_now()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        task = row_to_dict(row)
        if task.get("agent_claim_owner") != owner:
            raise ValueError(f"Task is not claimed by {owner}")
        conn.execute(
            """
            UPDATE tasks
            SET agent_claim_until = ?,
                agent_claim_status = 'claimed',
                updated_at = ?,
                last_agent_update_at = ?
            WHERE task_id = ?
            """,
            (claim_until, timestamp, timestamp, task_id),
        )
        _event(conn, task_id, "claim_heartbeat", owner, {"claim_until": claim_until})
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def append_progress(
    task_id: str,
    message: str,
    owner: str = "agent",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not message.strip():
        raise ValueError("message is required")
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        _event(conn, task_id, "progress", owner, {"message": message})
        conn.execute(
            "UPDATE tasks SET updated_at = ?, last_agent_update_at = ? WHERE task_id = ?",
            (timestamp, timestamp, task_id),
        )
        updated = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_dict(updated)


def enqueue_due_notifications(db_path: Path | str | None = None, channel: str = "local") -> list[dict[str, Any]]:
    path = ensure_db(db_path)
    due_tasks = list_tasks(path, due=True, limit=100)
    timestamp = iso_now()
    jobs = []
    with transaction(path) as conn:
        for task in due_tasks:
            if task["status"] == "notified":
                continue
            existing = conn.execute(
                """
                SELECT job_id FROM notification_jobs
                WHERE task_id = ? AND channel = ? AND status IN ('pending', 'sending', 'retry')
                """,
                (task["task_id"], channel),
            ).fetchone()
            if existing:
                continue
            job = {
                "job_id": make_job_id(),
                "task_id": task["task_id"],
                "channel": channel,
                "status": "pending",
                "run_at": timestamp,
                "payload_json": _json({"title": task["title"], "next_action": task["next_action"]}),
                "attempts": 0,
                "locked_by": "",
                "locked_until": None,
                "next_attempt_at": None,
                "last_error": "",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            conn.execute(
                """
                INSERT INTO notification_jobs(
                    job_id, task_id, channel, status, run_at, payload_json, attempts,
                    locked_by, locked_until, next_attempt_at, last_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(job.values()),
            )
            _event(conn, task["task_id"], "notification_enqueued", "worker", {"channel": channel})
            jobs.append(job)
    return jobs


def claim_ready_notification_jobs(
    worker_id: str,
    db_path: Path | str | None = None,
    limit: int = 10,
    lease_seconds: int = 60,
) -> list[dict[str, Any]]:
    if not worker_id.strip():
        raise ValueError("worker_id is required")
    path = ensure_db(db_path)
    timestamp = iso_now()
    locked_until = seconds_until_iso(lease_seconds)
    with transaction(path, immediate=True) as conn:
        rows = conn.execute(
            """
            SELECT * FROM notification_jobs
            WHERE status IN ('pending', 'retry')
              AND run_at <= ?
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
              AND (locked_until IS NULL OR locked_until <= ? OR locked_by = '')
            ORDER BY run_at ASC, created_at ASC
            LIMIT ?
            """,
            (timestamp, timestamp, timestamp, limit),
        ).fetchall()
        jobs = []
        for row in rows:
            conn.execute(
                """
                UPDATE notification_jobs
                SET status = 'sending',
                    locked_by = ?,
                    locked_until = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (worker_id, locked_until, timestamp, row["job_id"]),
            )
            updated = conn.execute("SELECT * FROM notification_jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
            job = row_to_dict(updated)
            task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (job["task_id"],)).fetchone()
            job["task"] = row_to_dict(task)
            jobs.append(job)
        return jobs


def record_notification_attempt(
    job_id: str,
    status: str,
    db_path: Path | str | None = None,
    error: str = "",
    response: dict[str, Any] | None = None,
    retry_delay_seconds: int = 60,
) -> dict[str, Any]:
    if status not in {"sent", "failed", "retry", "skipped"}:
        raise ValueError("status must be sent, failed, retry, or skipped")
    path = ensure_db(db_path)
    timestamp = iso_now()
    with transaction(path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM notification_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(f"Notification job not found: {job_id}")
        job = row_to_dict(row)
        conn.execute(
            """
            INSERT INTO notification_attempts(
                attempt_id, job_id, task_id, channel, status, error, response_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                make_attempt_id(),
                job_id,
                job["task_id"],
                job["channel"],
                status,
                error,
                _json(response or {}),
                timestamp,
            ),
        )
        attempts = int(job.get("attempts") or 0) + 1
        next_attempt_at = seconds_until_iso(retry_delay_seconds) if status == "retry" else None
        final_status = status
        conn.execute(
            """
            UPDATE notification_jobs
            SET status = ?,
                attempts = ?,
                locked_by = '',
                locked_until = NULL,
                next_attempt_at = ?,
                last_error = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (final_status, attempts, next_attempt_at, error, timestamp, job_id),
        )
        if status == "sent":
            task_row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (job["task_id"],)).fetchone()
            if task_row:
                task = row_to_dict(task_row)
                if task["status"] in {"inbox", "scheduled", "snoozed"}:
                    validate_transition(task["status"], "notified")
                    conn.execute(
                        """
                        UPDATE tasks
                        SET status = 'notified',
                            updated_at = ?
                        WHERE task_id = ?
                        """,
                        (timestamp, job["task_id"]),
                    )
                    _event(conn, job["task_id"], "notification_sent", "worker", {"channel": job["channel"]})
        if status in {"failed", "retry", "skipped"}:
            _event(
                conn,
                job["task_id"],
                f"notification_{status}",
                "worker",
                {"channel": job["channel"], "error": error},
            )
        updated = conn.execute("SELECT * FROM notification_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return row_to_dict(updated)


def list_notification_attempts(
    db_path: Path | str | None = None,
    job_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    path = ensure_db(db_path)
    where = ""
    params: list[Any] = []
    if job_id:
        where = "WHERE job_id = ?"
        params.append(job_id)
    params.append(limit)
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM notification_attempts
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)
