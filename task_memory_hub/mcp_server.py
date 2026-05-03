from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import re
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

from .action_intake import register_ai_action_item
from .branding import APP_NAME, APP_SLUG
from .config import default_db_path, workspace_root
from .orchestrator import run_orchestrator_once
from .runner import run_runner_once
from .registry import (
    current_workspace,
    ensure_principal,
    get_harness_profile,
    heartbeat_agent_runtime,
    list_agent_runtimes,
    list_harness_profiles,
    list_principals,
    register_agent_runtime,
    register_workspace,
)
from .service import (
    ack_task,
    append_progress,
    claim_next_task,
    complete_task,
    create_task,
    ensure_db,
    get_context_pack,
    get_task,
    get_task_tree,
    list_automations,
    list_tasks,
    heartbeat_claim,
    record_approval_decision,
    decide_review_gate,
    release_task,
    request_review_gate,
    request_task_stop,
    snooze_task,
    task_registry_summary,
    update_task,
)
from .sync import fetch_origin_task, pull_from_global


mcp = FastMCP(
    APP_SLUG,
    instructions=(
        "Use this server to create, inspect, acknowledge, snooze, complete, "
        f"and resume local {APP_NAME} tasks. Do not store secrets in task context packs."
    ),
)


def _debug_mcp(message: str) -> None:
    path = os.environ.get("TASK_MEMORY_HUB_MCP_DEBUG_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-._")
    return slug or "automation"


@mcp.tool(name="create_task")
def create_task_tool(
    title: str,
    summary: str = "",
    next_action: str = "",
    priority: str = "normal",
    due_at: str | None = None,
    rank: int | None = None,
    source_workspace: str = workspace_root().name,
    source_agent: str = "cline",
    idempotency_key: str | None = None,
    tags: list[str] | None = None,
    depends_on: list[str] | None = None,
    ai_context_pack: dict[str, Any] | None = None,
    task_kind: str = "action",
    execution_mode: str = "manual",
    schedule_kind: str | None = None,
    source_principal_name: str = "cline",
    target_principal_name: str = "",
    harness: str = "",
    parent_task_id: str = "",
) -> dict[str, Any]:
    """Create or idempotently return a local task with registry-bound principal metadata."""
    ensure_db()
    workspace = register_workspace(registered_by_principal_type="agent", registered_by_display_name=source_principal_name or source_agent)
    source_principal = ensure_principal(
        principal_type="agent",
        display_name=source_principal_name or source_agent,
        trust_level="trusted",
    )
    target_principal = (
        ensure_principal(principal_type="agent", display_name=target_principal_name, trust_level="trusted")
        if target_principal_name
        else None
    )
    harness_profile = get_harness_profile(harness, workspace_id=workspace["workspace_id"]) if harness else None
    return create_task(
        {
            "title": title,
            "summary": summary,
            "next_action": next_action,
            "priority": priority,
            "due_at": due_at,
            "rank": rank,
            "source_workspace": source_workspace or workspace["workspace_slug"],
            "source_workspace_id": workspace["workspace_id"],
            "source_workspace_slug": workspace["workspace_slug"],
            "source_agent": source_agent,
            "source_principal_id": source_principal["principal_id"],
            "target_principal_id": target_principal["principal_id"] if target_principal else "",
            "proposed_by_principal_id": source_principal["principal_id"],
            "assigned_by_principal_id": source_principal["principal_id"] if target_principal else "",
            "harness_id": harness_profile["harness_id"] if harness_profile else "",
            "policy_profile_id": harness_profile.get("policy_profile_id") if harness_profile else "",
            "parent_task_id": parent_task_id,
            "idempotency_key": idempotency_key,
            "tags": tags or [],
            "depends_on": depends_on or [],
            "ai_context_pack": ai_context_pack or {},
            "task_kind": task_kind,
            "execution_mode": execution_mode,
            "schedule_kind": schedule_kind,
        },
        actor=source_agent,
    )


@mcp.tool(name="register_automation")
def register_automation_tool(
    title: str,
    automation_slug: str = "",
    summary: str = "",
    next_action: str = "Run according to the automation execution contract.",
    timezone: str = "Asia/Seoul",
    rrule: str = "",
    start_at: str | None = None,
    catch_up_policy: str = "latest_only",
    execution_mode: str = "agent_assisted",
    controller_status: str = "active",
    harness_id: str = "",
    workflow_refs: list[str] | None = None,
    skill_refs: list[str] | None = None,
    required_capabilities: list[str] | None = None,
    blocked_capabilities: list[str] | None = None,
    approval_required: bool = False,
) -> dict[str, Any]:
    """Register a recurring automation definition as durable controller state."""
    ensure_db()
    workspace = register_workspace(registered_by_principal_type="agent", registered_by_display_name="cline")
    slug = automation_slug or _slugify(title)
    automation_id = f"{workspace['workspace_slug']}:{slug}"
    schedule_kind = "recurring" if rrule else "due_once" if start_at else "none"
    schedule = {
        "timezone": timezone,
        "catch_up_policy": catch_up_policy,
    }
    if rrule:
        schedule["rrule"] = rrule
    if start_at:
        schedule["start_at"] = start_at
    execution_contract = {
        "harness_id": harness_id,
        "execution_mode": execution_mode,
        "approval_required": approval_required,
        "workflow_refs": workflow_refs or [],
        "skill_refs": skill_refs or [],
        "required_capabilities": required_capabilities or [],
        "blocked_capabilities": blocked_capabilities or [],
    }
    return create_task(
        {
            "title": title,
            "summary": summary,
            "next_action": next_action,
            "status": "scheduled",
            "due_at": start_at,
            "task_kind": "automation",
            "execution_mode": execution_mode,
            "schedule_kind": schedule_kind,
            "controller_status": controller_status,
            "automation_id": automation_id,
            "source_agent": "cline",
            "source_workspace": workspace["workspace_slug"],
            "source_workspace_id": workspace["workspace_id"],
            "source_workspace_slug": workspace["workspace_slug"],
            "harness_id": harness_id,
            "idempotency_key": f"automation:{workspace['workspace_slug']}:{slug}",
            "tags": ["automation", "recurring"] if schedule_kind == "recurring" else ["automation"],
            "schedule": schedule,
            "execution_contract": execution_contract,
        },
        actor="cline",
    )


@mcp.tool(name="list_automations")
def list_automations_tool(controller_status: str | None = None, include_runs: bool = False, limit: int = 20) -> list[dict[str, Any]]:
    """List registered automation definitions and optionally workflow runs."""
    ensure_db()
    return list_automations(controller_status=controller_status, include_runs=include_runs, limit=limit)


@mcp.tool(name="list_due_tasks")
def list_due_tasks(limit: int = 20) -> list[dict[str, Any]]:
    """List due or overdue active tasks, ordered by priority, rank, and due time."""
    ensure_db()
    return list_tasks(due=True, limit=limit)


@mcp.tool(name="list_tasks")
def list_tasks_tool(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """List local tasks, optionally filtered by status."""
    ensure_db()
    return list_tasks(status=status, limit=limit)


@mcp.tool(name="list_registered_principals")
def list_registered_principals_tool(active_only: bool = False) -> list[dict[str, Any]]:
    """List registered human, agent, and service principals."""
    ensure_db()
    return list_principals(active_only=active_only)


@mcp.tool(name="list_harness_profiles")
def list_harness_profiles_tool() -> list[dict[str, Any]]:
    """List harness profiles for the current workspace."""
    ensure_db()
    workspace = current_workspace()
    return list_harness_profiles(workspace_id=workspace["workspace_id"])


@mcp.tool(name="register_agent_runtime")
def register_agent_runtime_tool(
    agent_name: str,
    role: str = "worker",
    capabilities: list[str] | None = None,
    default_harness: str = "",
    max_active_tasks: int = 1,
    lease_seconds: int = 600,
) -> dict[str, Any]:
    """Register an active agent runtime for orchestrator assignment."""
    ensure_db()
    workspace = current_workspace()
    harness_id = ""
    if default_harness:
        harness_id = get_harness_profile(default_harness, workspace_id=workspace["workspace_id"])["harness_id"]
    return register_agent_runtime(
        agent_name=agent_name,
        workspace_id=workspace["workspace_id"],
        role=role,
        capabilities=capabilities or [],
        default_harness_id=harness_id,
        max_active_tasks=max_active_tasks,
        lease_seconds=lease_seconds,
    )


@mcp.tool(name="heartbeat_agent_runtime")
def heartbeat_agent_runtime_tool(
    agent_name: str,
    status: str = "active",
    current_task_id: str = "",
    lease_seconds: int = 600,
) -> dict[str, Any]:
    """Update an active agent runtime heartbeat."""
    ensure_db()
    workspace = current_workspace()
    return heartbeat_agent_runtime(
        agent_name=agent_name,
        workspace_id=workspace["workspace_id"],
        status=status,
        current_task_id=current_task_id,
        lease_seconds=lease_seconds,
    )


@mcp.tool(name="list_active_agent_runtimes")
def list_active_agent_runtimes_tool(active_only: bool = True) -> list[dict[str, Any]]:
    """List active agent runtime records for orchestrator assignment."""
    ensure_db()
    workspace = current_workspace()
    return list_agent_runtimes(workspace_id=workspace["workspace_id"], active_only=active_only)


@mcp.tool(name="orchestrator_run_once")
def orchestrator_run_once_tool(
    orchestrator_name: str = "orchestrator",
    include_not_due: bool = True,
    limit: int = 10,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one orchestrator assignment pass through the hub."""
    ensure_db()
    return run_orchestrator_once(
        orchestrator_name=orchestrator_name,
        include_not_due=include_not_due,
        limit=limit,
        dry_run=dry_run,
    )


@mcp.tool(name="runner_run_once")
def runner_run_once_tool(
    agent_name: str = "mcp-runner",
    backend: str = "dry_run",
    backend_command: str = "",
    timeout_seconds: int = 120,
    script_allowlist_path: str = "",
    task_id: str = "",
    capabilities: list[str] | None = None,
    include_not_due: bool = True,
    run_orchestrator: bool = False,
) -> dict[str, Any]:
    """Run one policy-aware harness runner pass."""
    ensure_db()
    return run_runner_once(
        agent_name=agent_name,
        backend=backend,
        backend_command=backend_command,
        timeout_seconds=timeout_seconds,
        script_allowlist_path=script_allowlist_path or None,
        task_id=task_id,
        capabilities=capabilities,
        include_not_due=include_not_due,
        run_orchestrator=run_orchestrator,
    )


@mcp.tool(name="get_hub_status")
def get_hub_status_tool() -> dict[str, Any]:
    """Return workspace registry status and task binding counts."""
    ensure_db()
    workspace = current_workspace()
    return {
        "workspace": workspace,
        "principals": list_principals(),
        "agent_runtimes": list_agent_runtimes(workspace_id=workspace["workspace_id"]),
        "harness_profiles": list_harness_profiles(workspace_id=workspace["workspace_id"]),
        "task_summary": task_registry_summary(),
    }


@mcp.tool(name="get_task_tree")
def get_task_tree_tool(task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Return parent/child task hierarchy, optionally rooted at one task."""
    ensure_db()
    return get_task_tree(task_id, limit=limit)


@mcp.tool(name="get_task")
def get_task_tool(task_id: str) -> dict[str, Any]:
    """Read one task by ID."""
    ensure_db()
    return get_task(task_id)


@mcp.tool(name="update_task")
def update_task_tool(task_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Update task fields such as title, summary, next_action, priority, rank, due_at, status, or tags."""
    ensure_db()
    return update_task(task_id, updates, actor="cline")


@mcp.tool(name="claim_next_task")
def claim_next_task_tool(
    owner: str = "cline",
    lease_seconds: int = 1800,
    workspace: str | None = None,
    include_not_due: bool = False,
) -> dict[str, Any]:
    """Claim the next due or ordered task for an agent lease."""
    ensure_db()
    claimed = claim_next_task(
        owner=owner,
        lease_seconds=lease_seconds,
        workspace=workspace,
        include_not_due=include_not_due,
    )
    return claimed or {"claimed": None}


@mcp.tool(name="release_task")
def release_task_tool(task_id: str, owner: str | None = "cline", next_status: str = "acknowledged") -> dict[str, Any]:
    """Release a claimed task and move it to the requested next status."""
    ensure_db()
    return release_task(task_id, owner=owner, next_status=next_status)


@mcp.tool(name="heartbeat_claim")
def heartbeat_claim_tool(task_id: str, owner: str = "cline", lease_seconds: int = 1800) -> dict[str, Any]:
    """Extend the claim lease for a task already claimed by this owner."""
    ensure_db()
    return heartbeat_claim(task_id, owner=owner, lease_seconds=lease_seconds)


@mcp.tool(name="append_progress")
def append_progress_tool(task_id: str, message: str, owner: str = "cline") -> dict[str, Any]:
    """Append a progress event to a task."""
    ensure_db()
    return append_progress(task_id, message=message, owner=owner)


@mcp.tool(name="record_approval_decision")
def record_approval_decision_tool(
    task_id: str,
    decision: str,
    by: str = "owner",
    reason: str = "",
    approver_principal_id: str = "",
) -> dict[str, Any]:
    """Record a human approval, rejection, or change request for a task."""
    ensure_db()
    if not approver_principal_id:
        approver = ensure_principal(principal_type="human", display_name=by, trust_level="owner")
        approver_principal_id = approver["principal_id"]
    return record_approval_decision(
        task_id,
        decision,
        approver_principal_id=approver_principal_id,
        reason=reason,
        actor=by,
    )


@mcp.tool(name="request_task_stop")
def request_task_stop_tool(task_id: str, by: str = "owner", reason: str = "") -> dict[str, Any]:
    """Request that a running or assigned task stop and wait for human review."""
    ensure_db()
    return request_task_stop(task_id, reason=reason, actor=by)


@mcp.tool(name="request_review_gate")
def request_review_gate_tool(
    task_id: str,
    by: str = "owner",
    reason: str = "",
    gate_type: str = "pre_execution",
    reviewer_principal_id: str = "",
) -> dict[str, Any]:
    """Create or return a human review gate for a task before risky execution."""
    ensure_db()
    return request_review_gate(
        task_id,
        reason=reason,
        actor=by,
        gate_type=gate_type,
        reviewer_principal_id=reviewer_principal_id,
    )


@mcp.tool(name="decide_review_gate")
def decide_review_gate_tool(
    gate_task_id: str,
    decision: str,
    by: str = "owner",
    reason: str = "",
    approver_principal_id: str = "",
) -> dict[str, Any]:
    """Apply a review gate decision to the gate and its subject task."""
    ensure_db()
    if not approver_principal_id:
        approver = ensure_principal(principal_type="human", display_name=by, trust_level="owner")
        approver_principal_id = approver["principal_id"]
    return decide_review_gate(
        gate_task_id,
        decision,
        approver_principal_id=approver_principal_id,
        reason=reason,
        actor=by,
    )


@mcp.tool(name="register_ai_action_item")
def register_ai_action_item_tool(
    title: str,
    summary: str = "",
    next_action: str = "",
    detail_md: str = "",
    priority: str | None = None,
    due_at: str | None = None,
    tags: list[str] | None = None,
    action_key: str | None = None,
    agent_name: str = "cline",
    harness: str = "default",
) -> dict[str, Any]:
    """Register an AI-proposed follow-up task through harness throttling rules."""
    ensure_db()
    result = register_ai_action_item(
        title=title,
        summary=summary,
        next_action=next_action,
        detail_md=detail_md,
        priority=priority,
        due_at=due_at,
        tags=tags or ["ai-action"],
        action_key=action_key,
        agent_name=agent_name,
        harness=harness,
    )
    task = result.get("task") or {}
    event = result.get("event") or {}
    harness_profile = result.get("harness") or {}
    return {
        "accepted": bool(result.get("accepted")),
        "reason": result.get("reason", event.get("reason", "")),
        "task_id": task.get("task_id") or result.get("task_id", ""),
        "title": task.get("title", title),
        "status": task.get("status", ""),
        "priority": task.get("priority", priority or ""),
        "harness_id": harness_profile.get("harness_id") or event.get("harness_id", ""),
        "event_id": event.get("event_id", ""),
        "decision": event.get("decision", ""),
    }


@mcp.tool(name="ack_task")
def ack_task_tool(task_id: str) -> dict[str, Any]:
    """Acknowledge that the reminder was seen."""
    ensure_db()
    return ack_task(task_id, actor="cline")


@mcp.tool(name="snooze_task")
def snooze_task_tool(task_id: str, until: str | None = None, duration: str | None = None) -> dict[str, Any]:
    """Snooze a task until an ISO datetime or for a duration like 2h, 1d, or 1w."""
    ensure_db()
    return snooze_task(task_id, until=until, duration=duration, actor="cline")


@mcp.tool(name="complete_task")
def complete_task_tool(task_id: str) -> dict[str, Any]:
    """Mark a task completed."""
    ensure_db()
    return complete_task(task_id, actor="cline")


@mcp.tool(name="get_task_context_pack")
def get_task_context_pack(task_id: str) -> dict[str, Any]:
    """Return the compact context pack an agent should use to resume a task."""
    ensure_db()
    return get_context_pack(task_id)


@mcp.tool(name="register_current_workspace")
def register_current_workspace_tool(registered_by: str = "cline") -> dict[str, Any]:
    """Register or return the current workspace identity in the local registry."""
    ensure_db()
    ensure_principal(principal_type="agent", display_name=registered_by, trust_level="trusted")
    return register_workspace(
        registered_by_principal_type="agent",
        registered_by_display_name=registered_by,
        authority_basis="agent_suggestion",
        authority_level="agent_suggested",
        approval_status="proposed",
        detect_git=False,
    )


@mcp.tool(name="push_to_global_hub")
def push_to_global_hub_tool(
    global_db_path: str | None = None,
    registered_by: str = "cline",
    profile: str = "normal",
    limit: int = 1000,
) -> dict[str, Any]:
    """Push local workspace tasks to the global hub. This does not pull remote tasks."""
    ensure_db()
    _debug_mcp("push_to_global_hub: start")
    command = [
        sys.executable,
        "-m",
        "task_memory_hub.cli",
        "--db",
        str(default_db_path()),
        "push",
        "--registered-by-type",
        "agent",
        "--registered-by",
        registered_by,
        "--authority-basis",
        "agent_suggestion",
        "--authority-level",
        "agent_suggested",
        "--approval-status",
        "proposed",
        "--profile",
        profile,
        "--limit",
        str(limit),
        "--json",
    ]
    if global_db_path:
        command[6:6] = ["--global-db", global_db_path]
    completed = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=90)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "global push failed")
    result = json.loads(completed.stdout)
    _debug_mcp("push_to_global_hub: pushed")
    return {
        "workspace_id": result["workspace"]["workspace_id"],
        "workspace_slug": result["workspace"]["workspace_slug"],
        "principal_id": result["principal"]["principal_id"],
        "snapshot_profile": result["snapshot_profile"],
        "count": result["count"],
        "created": result["created"],
        "updated": result["updated"],
    }


@mcp.tool(name="fetch_origin_task")
def fetch_origin_task_tool(
    hub_task_id: str,
    global_db_path: str | None = None,
    source_db_path: str | None = None,
) -> dict[str, Any]:
    """Fetch the source workspace task behind a global hub task."""
    return fetch_origin_task(hub_task_id, global_db_path=global_db_path, source_db_path=source_db_path)


@mcp.tool(name="pull_from_global_hub")
def pull_from_global_hub_tool(
    global_db_path: str | None = None,
    registered_by: str = "cline",
    limit: int = 100,
) -> dict[str, Any]:
    """Pull approved global hub tasks targeted to this workspace."""
    ensure_db()
    return pull_from_global(
        global_db_path=global_db_path,
        registered_by_principal_type="agent",
        registered_by_display_name=registered_by,
        limit=limit,
    )


def main() -> None:
    ensure_db()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
