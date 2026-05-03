from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

from .config import default_db_path
from .registry import (
    current_workspace,
    ensure_principal,
    get_harness_profile,
    record_action_intake_event,
    register_harness_profile,
)
from .service import ACTIVE_STATUSES, create_task
from .store import init_db, transaction
from .timeutil import iso_now, parse_datetime


def _hash_action(title: str, next_action: str, owner: str) -> str:
    raw = json.dumps(
        {"title": title.strip().lower(), "next_action": next_action.strip().lower(), "owner": owner.strip().lower()},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _cutoff_iso(seconds: int) -> str:
    value = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _count_recent_accepts(db_path: Path, harness_id: str, seconds: int = 3600) -> int:
    with transaction(db_path) as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM action_intake_events
                WHERE harness_id = ? AND decision = 'accepted' AND created_at >= ?
                """,
                (harness_id, _cutoff_iso(seconds)),
            ).fetchone()[0]
        )


def _last_accept_at(db_path: Path, harness_id: str) -> str | None:
    with transaction(db_path) as conn:
        row = conn.execute(
            """
            SELECT created_at FROM action_intake_events
            WHERE harness_id = ? AND decision = 'accepted'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (harness_id,),
        ).fetchone()
        return row["created_at"] if row else None


def _accepted_duplicate(db_path: Path, harness_id: str, action_key: str) -> str | None:
    with transaction(db_path) as conn:
        row = conn.execute(
            """
            SELECT task_id FROM action_intake_events
            WHERE harness_id = ? AND action_key = ? AND decision = 'accepted' AND task_id != ''
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (harness_id, action_key),
        ).fetchone()
        return row["task_id"] if row else None


def _open_action_count(db_path: Path, harness_id: str, principal_id: str) -> int:
    statuses = sorted(ACTIVE_STATUSES)
    placeholders = ",".join("?" for _ in statuses)
    with transaction(db_path) as conn:
        return int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM tasks
                WHERE harness_id = ?
                  AND source_principal_id = ?
                  AND status IN ({placeholders})
                """,
                [harness_id, principal_id, *statuses],
            ).fetchone()[0]
        )


def _seconds_since(value: str) -> int:
    parsed = parse_datetime(value)
    if not parsed:
        return 10**9
    normalized = parsed[:-1] + "+00:00" if parsed.endswith("Z") else parsed
    timestamp = datetime.fromisoformat(normalized)
    return int((datetime.now(tz=timezone.utc) - timestamp).total_seconds())


def register_ai_action_item(
    title: str,
    summary: str = "",
    next_action: str = "",
    detail_md: str = "",
    priority: str | None = None,
    due_at: str | None = None,
    tags: list[str] | None = None,
    action_key: str | None = None,
    agent_name: str = "agent",
    harness: str = "default",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not title.strip():
        raise ValueError("title is required")
    registry_db_path = Path(db_path) if db_path else None
    workspace = current_workspace(db_path=registry_db_path)
    principal = ensure_principal(
        principal_type="agent",
        display_name=agent_name,
        trust_level="trusted",
        db_path=registry_db_path,
    )
    try:
        harness_profile = get_harness_profile(harness, workspace_id=workspace["workspace_id"], db_path=registry_db_path)
    except KeyError:
        harness_profile = register_harness_profile(
            profile_name=harness,
            workspace_id=workspace["workspace_id"],
            default_agent_principal_id=principal["principal_id"],
            db_path=registry_db_path,
        )
    if not harness_profile.get("enabled"):
        event = record_action_intake_event(
            "rejected",
            harness_id=harness_profile["harness_id"],
            principal_id=principal["principal_id"],
            workspace_id=workspace["workspace_id"],
            reason="harness disabled",
            payload={"title": title},
            db_path=registry_db_path,
        )
        return {"accepted": False, "reason": "harness disabled", "event": event}

    task_db_path = registry_db_path or default_db_path()
    init_db(task_db_path)
    action_key = action_key or _hash_action(title, next_action, agent_name)
    existing_task_id = _accepted_duplicate(task_db_path, harness_profile["harness_id"], action_key)
    if existing_task_id:
        event = record_action_intake_event(
            "duplicate",
            harness_id=harness_profile["harness_id"],
            principal_id=principal["principal_id"],
            workspace_id=workspace["workspace_id"],
            task_id=existing_task_id,
            action_key=action_key,
            reason="duplicate action_key",
            payload={"title": title},
            db_path=task_db_path,
        )
        return {"accepted": False, "reason": "duplicate action_key", "task_id": existing_task_id, "event": event}

    recent = _count_recent_accepts(task_db_path, harness_profile["harness_id"], seconds=3600)
    if recent >= int(harness_profile["max_actions_per_hour"]):
        event = record_action_intake_event(
            "throttled",
            harness_id=harness_profile["harness_id"],
            principal_id=principal["principal_id"],
            workspace_id=workspace["workspace_id"],
            action_key=action_key,
            reason="max_actions_per_hour exceeded",
            payload={"title": title, "recent": recent},
            db_path=task_db_path,
        )
        return {"accepted": False, "reason": "max_actions_per_hour exceeded", "event": event}

    last = _last_accept_at(task_db_path, harness_profile["harness_id"])
    min_interval = int(harness_profile["min_action_interval_seconds"])
    if last and _seconds_since(last) < min_interval:
        event = record_action_intake_event(
            "throttled",
            harness_id=harness_profile["harness_id"],
            principal_id=principal["principal_id"],
            workspace_id=workspace["workspace_id"],
            action_key=action_key,
            reason="min_action_interval_seconds not elapsed",
            payload={"title": title, "last_accept_at": last},
            db_path=task_db_path,
        )
        return {"accepted": False, "reason": "min_action_interval_seconds not elapsed", "event": event}

    open_count = _open_action_count(task_db_path, harness_profile["harness_id"], principal["principal_id"])
    if open_count >= int(harness_profile["max_open_actions"]):
        event = record_action_intake_event(
            "throttled",
            harness_id=harness_profile["harness_id"],
            principal_id=principal["principal_id"],
            workspace_id=workspace["workspace_id"],
            action_key=action_key,
            reason="max_open_actions exceeded",
            payload={"title": title, "open_count": open_count},
            db_path=task_db_path,
        )
        return {"accepted": False, "reason": "max_open_actions exceeded", "event": event}

    task = create_task(
        {
            "title": title,
            "summary": summary,
            "next_action": next_action,
            "detail_md": detail_md,
            "priority": priority or harness_profile["default_priority"],
            "due_at": due_at,
            "tags": tags or ["ai-action"],
            "source_agent": agent_name,
            "source_workspace": workspace["workspace_slug"],
            "source_workspace_id": workspace["workspace_id"],
            "source_workspace_slug": workspace["workspace_slug"],
            "source_principal_id": principal["principal_id"],
            "proposed_by_principal_id": principal["principal_id"],
            "routing_status": "local_only",
            "harness_id": harness_profile["harness_id"],
            "policy_profile_id": harness_profile.get("policy_profile_id", ""),
            "idempotency_key": f"ai-action:{harness_profile['harness_id']}:{action_key}",
            "ai_context_pack": {
                "version": "0.1",
                "source": "ai_action_intake",
                "harness_id": harness_profile["harness_id"],
                "action_key": action_key,
                "requires_human_approval": bool(harness_profile.get("require_human_approval")),
            },
        },
        db_path=task_db_path,
        actor=agent_name,
    )
    event = record_action_intake_event(
        "accepted",
        harness_id=harness_profile["harness_id"],
        principal_id=principal["principal_id"],
        workspace_id=workspace["workspace_id"],
        task_id=task["task_id"],
        action_key=action_key,
        reason="accepted",
        payload={"title": title},
        db_path=task_db_path,
    )
    return {"accepted": True, "task": task, "event": event, "harness": harness_profile, "principal": principal}
