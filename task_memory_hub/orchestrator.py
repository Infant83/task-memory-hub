from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import current_workspace, ensure_principal, get_harness_profile, list_agent_runtimes
from .service import ACTIVE_STATUSES, assign_task, ensure_db
from .store import rows_to_dicts, transaction
from .timeutil import iso_now


ASSIGNABLE_KINDS = {"action", "delegated_task", "workflow_run"}


def _required_capabilities(task: dict[str, Any]) -> set[str]:
    contract = task.get("execution_contract") or {}
    values = contract.get("required_capabilities") or contract.get("required_capability") or []
    if isinstance(values, str):
        values = [values]
    return {str(value).strip() for value in values if str(value).strip()}


def _agent_can_handle(agent: dict[str, Any], task: dict[str, Any]) -> bool:
    required = _required_capabilities(task)
    if not required:
        return True
    capabilities = {str(value).strip() for value in agent.get("capabilities") or [] if str(value).strip()}
    return required.issubset(capabilities)


def _task_sort_key(task: dict[str, Any]) -> tuple[int, int, str]:
    priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    rank = task.get("rank")
    return (priority_order.get(task.get("priority"), 4), 999999 if rank is None else int(rank), task.get("created_at") or "")


def _open_task_counts(db_path: Path, workspace_id: str) -> dict[str, int]:
    statuses = sorted(ACTIVE_STATUSES)
    placeholders = ",".join("?" for _ in statuses)
    with transaction(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT target_principal_id, COUNT(*) AS count
            FROM tasks
            WHERE target_principal_id != ''
              AND source_workspace_id = ?
              AND status IN ({placeholders})
            GROUP BY target_principal_id
            """,
            [workspace_id, *statuses],
        ).fetchall()
        return {row["target_principal_id"]: int(row["count"]) for row in rows}


def _candidate_tasks(db_path: Path, include_not_due: bool, limit: int) -> list[dict[str, Any]]:
    statuses = sorted(ACTIVE_STATUSES - {"in_progress"})
    status_placeholders = ",".join("?" for _ in statuses)
    kind_placeholders = ",".join("?" for _ in ASSIGNABLE_KINDS)
    where = [
        f"status IN ({status_placeholders})",
        f"task_kind IN ({kind_placeholders})",
        "source_principal_id != ''",
        "target_principal_id = ''",
    ]
    params: list[Any] = [*statuses, *sorted(ASSIGNABLE_KINDS)]
    if not include_not_due:
        where.append("COALESCE(snooze_until, due_at) IS NOT NULL")
        where.append("COALESCE(snooze_until, due_at) <= ?")
        params.append(iso_now())
    with transaction(db_path) as conn:
        rows = conn.execute(
            f"""
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
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return rows_to_dicts(rows)


def run_orchestrator_once(
    orchestrator_name: str = "orchestrator",
    db_path: Path | str | None = None,
    include_not_due: bool = True,
    limit: int = 10,
    default_harness: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    path = ensure_db(db_path)
    workspace = current_workspace(db_path=path)
    orchestrator = ensure_principal(
        principal_type="agent",
        display_name=orchestrator_name,
        trust_level="trusted",
        db_path=path,
    )
    agents = [
        agent
        for agent in list_agent_runtimes(db_path=path, workspace_id=workspace["workspace_id"], active_only=True)
        if agent.get("role") != "orchestrator" and agent.get("principal_id") != orchestrator["principal_id"]
    ]
    agents.sort(key=lambda item: (item.get("role") or "", item.get("agent_name") or ""))
    open_counts = _open_task_counts(path, workspace["workspace_id"])
    candidates = sorted(_candidate_tasks(path, include_not_due=include_not_due, limit=limit), key=_task_sort_key)
    assignments: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    default_harness_profile = None
    if default_harness:
        default_harness_profile = get_harness_profile(default_harness, db_path=path, workspace_id=workspace["workspace_id"])

    for task in candidates:
        chosen = None
        for agent in agents:
            principal_id = agent["principal_id"]
            capacity = max(1, int(agent.get("max_active_tasks") or 1))
            if open_counts.get(principal_id, 0) >= capacity:
                continue
            if not _agent_can_handle(agent, task):
                continue
            chosen = agent
            break
        if not chosen:
            skipped.append({"task_id": task["task_id"], "reason": "no active capable agent"})
            continue

        harness_id = task.get("harness_id") or chosen.get("default_harness_id") or ""
        policy_profile_id = ""
        if not harness_id and default_harness_profile:
            harness_id = default_harness_profile["harness_id"]
            policy_profile_id = default_harness_profile.get("policy_profile_id", "")
        if dry_run:
            updated = dict(task)
            updated["target_principal_id"] = chosen["principal_id"]
            updated["assigned_by_principal_id"] = orchestrator["principal_id"]
            updated["harness_id"] = harness_id
            updated["routing_status"] = "assigned"
        else:
            updated = assign_task(
                task["task_id"],
                target_principal_id=chosen["principal_id"],
                assigned_by_principal_id=orchestrator["principal_id"],
                harness_id=harness_id,
                policy_profile_id=policy_profile_id,
                db_path=path,
                actor=orchestrator_name,
            )
        open_counts[chosen["principal_id"]] = open_counts.get(chosen["principal_id"], 0) + 1
        assignments.append(
            {
                "task_id": task["task_id"],
                "title": task["title"],
                "agent_name": chosen["agent_name"],
                "target_principal_id": chosen["principal_id"],
                "harness_id": updated.get("harness_id", ""),
                "routing_status": updated.get("routing_status", ""),
            }
        )

    return {
        "dry_run": dry_run,
        "workspace": workspace,
        "orchestrator": orchestrator,
        "candidate_count": len(candidates),
        "active_agent_count": len(agents),
        "assigned_count": len(assignments),
        "assignments": assignments,
        "skipped": skipped,
    }
