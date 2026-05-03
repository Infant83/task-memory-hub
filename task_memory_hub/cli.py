from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import re
import sys
import textwrap

from .api import run_server
from .action_intake import register_ai_action_item
from .backup import backup_database, restore_database
from .branding import APP_NAME, APP_SHORT_NAME, CLI_PROG, ENV_PREFIX
from .config import (
    database_backend,
    database_url,
    default_api_token_path,
    default_global_db_path,
    get_or_create_api_token,
    redact_database_url,
    workspace_root,
)
from .file_bridge import export_json, export_markdown, import_json, import_markdown
from .notification_adapters import DEFAULT_NOTIFICATION_CHANNEL, dispatch_notification, notification_capabilities
from .orchestrator import run_orchestrator_once
from .runner import run_runner_once, run_runner_watch
from .registry import (
    current_workspace,
    ensure_auth_profile,
    ensure_principal,
    ensure_network_profile,
    ensure_policy_profile,
    get_auth_profile,
    get_principal,
    get_network_profile,
    get_workspace,
    get_harness_profile,
    heartbeat_agent_runtime,
    list_agent_runtimes,
    list_auth_profiles,
    list_harness_profiles,
    list_network_profiles,
    list_principals,
    list_workspaces,
    register_agent_runtime,
    register_harness_profile,
    register_workspace,
)
from .service import (
    ack_task,
    append_progress,
    backfill_missing_task_bindings,
    claim_next_task,
    complete_task,
    create_task,
    enqueue_due_notifications,
    ensure_db,
    get_context_pack,
    get_task,
    get_task_tree,
    heartbeat_claim,
    list_automations,
    list_notification_attempts,
    list_tasks,
    record_approval_decision,
    decide_review_gate,
    request_task_stop,
    request_review_gate,
    snooze_task,
    task_registry_summary,
    update_task,
    release_task,
)
from .sync import VALID_PUSH_PROFILES, fetch_origin_task, pull_from_global, push_to_global
from .timeutil import iso_now
from .worker import pause_worker, resume_worker, run_once as run_worker_once, worker_status


HELP_FORMATTER = argparse.RawDescriptionHelpFormatter


def examples(text: str) -> str:
    body = textwrap.dedent(text).strip()
    return "Examples:\n" + "\n".join(f"  {line}" if line else "" for line in body.splitlines())


def set_examples(parser: argparse.ArgumentParser, text: str) -> None:
    parser.formatter_class = HELP_FORMATTER
    parser.epilog = examples(text)


def load_task_json_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("JSON file must contain one object. Use import-json for arrays or bulk sync.")
    return raw


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-._")
    return slug or "automation"


def load_json_argument(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    candidate = Path(value)
    try:
        raw = candidate.read_text(encoding="utf-8") if candidate.exists() else value
    except OSError:
        raw = value
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("JSON argument must be an object or a path to a JSON object")
    return parsed


def _append_unique(values: list[str], *items: str) -> list[str]:
    seen = set(values)
    for item in items:
        if item and item not in seen:
            values.append(item)
            seen.add(item)
    return values


def _resolve_or_ensure_principal(
    reference: str | None,
    db_path: str | None,
    principal_type: str = "human",
    trust_level: str = "member",
    ensure_missing: bool = False,
) -> dict[str, Any] | None:
    if not reference:
        return None
    if ensure_missing:
        return ensure_principal(
            principal_type=principal_type,
            display_name=reference,
            trust_level=trust_level,
            db_path=db_path,
        )
    return get_principal(reference, db_path=db_path, principal_type=principal_type or None)


def _resolve_target_principal(args: argparse.Namespace, db_path: str | None) -> dict[str, Any] | None:
    if args.target_agent and args.target_human:
        raise ValueError("Use only one of --target-agent or --target-human")
    if args.target_agent:
        return _resolve_or_ensure_principal(args.target_agent, db_path, "agent", "trusted", ensure_missing=True)
    if args.target_human:
        return _resolve_or_ensure_principal(args.target_human, db_path, "human", "member", ensure_missing=True)
    if args.target_principal:
        return get_principal(args.target_principal, db_path=db_path)
    if args.target_principal_id:
        return get_principal(args.target_principal_id, db_path=db_path)
    return None


def _resolve_workspace_reference(reference: str | None, db_path: str | None) -> dict[str, Any] | None:
    return get_workspace(reference, db_path=db_path) if reference else None


def build_add_payload(args: argparse.Namespace, db_path: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.json_file:
        payload = load_task_json_file(Path(args.json_file))
        if payload.get("task_id"):
            raise ValueError("add --json-file creates a new task. Use import-json when preserving task_id.")
    if args.title is not None:
        payload["title"] = args.title
    if not payload.get("title"):
        raise ValueError("title is required unless --json-file provides title")

    workspace = current_workspace(db_path=db_path)
    source_reference = args.by or args.source_principal or args.source_principal_id
    if source_reference:
        source_principal = (
            get_principal(source_reference, db_path=db_path)
            if args.source_principal_id
            else _resolve_or_ensure_principal(
                source_reference,
                db_path,
                args.by_type,
                "owner" if args.by_type == "human" else "trusted",
                ensure_missing=True,
            )
        )
    elif payload.get("source_principal_id"):
        source_principal = get_principal(payload["source_principal_id"], db_path=db_path)
    else:
        source_principal = ensure_principal(principal_type="human", display_name="owner", trust_level="owner", db_path=db_path)

    target_principal = _resolve_target_principal(args, db_path)
    if not target_principal and payload.get("target_principal_id"):
        target_principal = get_principal(payload["target_principal_id"], db_path=db_path)

    if args.harness and args.harness_id:
        raise ValueError("Use only one of --harness or --harness-id")
    harness_profile = None
    harness_reference = args.harness or args.harness_id or payload.get("harness_id")
    if harness_reference:
        harness_profile = get_harness_profile(harness_reference, db_path=db_path, workspace_id=workspace["workspace_id"])
        if not target_principal and harness_profile.get("default_agent_principal_id"):
            target_principal = get_principal(harness_profile["default_agent_principal_id"], db_path=db_path)

    target_workspace = _resolve_workspace_reference(args.target_workspace, db_path)
    if not target_workspace and args.target_workspace_id:
        target_workspace = get_workspace(args.target_workspace_id, db_path=db_path)

    parent_task_id = args.parent_task_id or args.parent
    if parent_task_id:
        get_task(parent_task_id, db_path=db_path)
    for dependency_task_id in args.depends_on or []:
        get_task(dependency_task_id, db_path=db_path)

    approved_by = None
    if args.approved_by:
        approved_by = get_principal(args.approved_by, db_path=db_path)
    elif args.approve:
        approved_by = source_principal

    overrides = {
        "summary": args.summary,
        "next_action": args.next_action,
        "detail_md": args.detail_md,
        "priority": args.priority,
        "rank": args.rank,
        "due_at": args.due,
        "source_workspace": args.workspace or workspace["workspace_slug"],
        "source_workspace_id": workspace["workspace_id"],
        "source_workspace_slug": workspace["workspace_slug"],
        "target_workspace_id": target_workspace["workspace_id"] if target_workspace else args.target_workspace_id,
        "target_workspace_slug": target_workspace["workspace_slug"] if target_workspace else None,
        "source_principal_id": source_principal["principal_id"] if source_principal else None,
        "target_principal_id": target_principal["principal_id"] if target_principal else None,
        "proposed_by_principal_id": source_principal["principal_id"] if source_principal else None,
        "approved_by_principal_id": approved_by["principal_id"] if approved_by else None,
        "assigned_by_principal_id": source_principal["principal_id"] if source_principal and target_principal else None,
        "harness_id": harness_profile["harness_id"] if harness_profile else args.harness_id,
        "policy_profile_id": harness_profile.get("policy_profile_id") if harness_profile else None,
        "task_kind": args.task_kind,
        "execution_mode": args.execution_mode,
        "schedule_kind": args.schedule_kind,
        "controller_status": args.controller_status,
        "automation_id": args.automation_id,
        "parent_task_id": parent_task_id,
        "source_agent": args.source_agent or (source_principal["display_name"] if source_principal else None),
        "idempotency_key": args.idempotency_key,
    }
    for key, value in overrides.items():
        if value is not None:
            payload[key] = value
    if args.tag:
        payload["tags"] = args.tag
    if args.depends_on:
        payload["depends_on"] = args.depends_on
    if args.execution_contract_json:
        payload["execution_contract"] = load_json_argument(args.execution_contract_json)
    if args.schedule_json:
        payload["schedule"] = load_json_argument(args.schedule_json)
    if args.artifact_contract_json:
        payload["artifact_contract"] = load_json_argument(args.artifact_contract_json)
    payload.setdefault("priority", "normal")
    payload.setdefault("source_workspace", workspace["workspace_slug"] or workspace_root().name)
    payload.setdefault("source_agent", source_principal["display_name"] if source_principal else "manual")
    payload["_actor"] = "manual" if (source_principal or {}).get("principal_type") == "human" else payload["source_agent"]
    return payload


def build_automation_payload(args: argparse.Namespace, db_path: str | None) -> dict[str, Any]:
    workspace = current_workspace(db_path=db_path)
    automation_slug = args.slug or _slugify(args.title)
    automation_id = args.automation_id or f"{workspace['workspace_slug']}:{automation_slug}"
    idempotency_key = args.idempotency_key or f"automation:{workspace['workspace_slug']}:{automation_slug}"
    schedule = load_json_argument(args.schedule_json)
    if args.timezone:
        schedule["timezone"] = args.timezone
    if args.start_at:
        schedule["start_at"] = args.start_at
    if args.rrule:
        schedule["rrule"] = args.rrule
    if args.catch_up_policy:
        schedule["catch_up_policy"] = args.catch_up_policy
    schedule_kind = args.schedule_kind or ("recurring" if args.rrule else "due_once" if args.due or args.start_at else "none")

    execution_contract = load_json_argument(args.execution_contract_json)
    execution_contract.update(
        {
            key: value
            for key, value in {
                "target_workspace_id": args.target_workspace_id,
                "target_principal_id": args.target_principal_id,
                "harness_id": args.harness_id,
                "execution_mode": args.execution_mode,
                "approval_required": args.approval_required,
                "dry_run_default": args.dry_run_default,
            }.items()
            if value not in (None, "", [])
        }
    )
    if args.required_capability:
        execution_contract["required_capabilities"] = args.required_capability
    if args.blocked_capability:
        execution_contract["blocked_capabilities"] = args.blocked_capability
    if args.skill:
        execution_contract["skill_refs"] = args.skill
    if args.rule:
        execution_contract["rule_refs"] = args.rule
    if args.workflow:
        execution_contract["workflow_refs"] = args.workflow
    if args.hook:
        execution_contract["hook_refs"] = args.hook
    if args.script:
        execution_contract["script_refs"] = [{"command_ref": value} for value in args.script]

    artifact_contract = load_json_argument(args.artifact_contract_json)
    if args.output_root:
        artifact_contract["output_root"] = args.output_root
    if args.artifact:
        artifact_contract["artifacts"] = [{"artifact_type": value, "required": True} for value in args.artifact]
    if args.deliver:
        artifact_contract["delivery"] = [
            {
                "channel": value.split(":", 1)[0],
                "recipient_ref": value.split(":", 1)[1] if ":" in value else value,
                "requires_review": args.requires_review,
            }
            for value in args.deliver
        ]

    tags = list(args.tag or [])
    _append_unique(tags, "automation")
    if schedule_kind == "recurring":
        _append_unique(tags, "recurring")

    return {
        "title": args.title,
        "summary": args.summary,
        "next_action": args.next_action or "Run according to the automation execution contract.",
        "detail_md": args.detail_md,
        "priority": args.priority,
        "status": args.status or "scheduled",
        "due_at": args.due or args.start_at,
        "task_kind": "automation",
        "execution_mode": args.execution_mode,
        "schedule_kind": schedule_kind,
        "controller_status": args.controller_status,
        "automation_id": automation_id,
        "source_workspace": workspace["workspace_slug"],
        "source_workspace_id": workspace["workspace_id"],
        "source_workspace_slug": workspace["workspace_slug"],
        "target_workspace_id": args.target_workspace_id,
        "target_principal_id": args.target_principal_id,
        "harness_id": args.harness_id,
        "idempotency_key": idempotency_key,
        "tags": tags,
        "schedule": schedule,
        "execution_contract": execution_contract,
        "artifact_contract": artifact_contract,
    }


def write_automation_mirror(task: dict[str, Any], workspace: dict[str, Any]) -> Path:
    registry_dir = Path(workspace["canonical_path"]) / ".automation" / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / "automation_tasks.jsonl"
    record = {
        "recorded_at": iso_now(),
        "task_id": task["task_id"],
        "automation_id": task.get("automation_id"),
        "title": task.get("title"),
        "controller_status": task.get("controller_status"),
        "schedule_kind": task.get("schedule_kind"),
        "source_workspace_id": task.get("source_workspace_id"),
        "idempotency_key": task.get("idempotency_key"),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def build_synthetic_notification_job(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "job_id": "synthetic",
        "task_id": "synthetic",
        "channel": args.channel,
        "task": {
            "task_id": "synthetic",
            "title": args.title,
            "summary": args.summary,
            "next_action": args.next_action,
        },
        "payload": {
            "title": args.title,
            "summary": args.summary,
            "next_action": args.next_action,
        },
    }


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def print_tasks(tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        print("No tasks.")
        return
    for task in tasks:
        rank = "" if task.get("rank") is None else f"#{task['rank']} "
        due = task.get("snooze_until") or task.get("due_at") or "-"
        print(f"{task['task_id']}  {rank}[{task['priority']}/{task['status']}] {task['title']}  due={due}")
        if task.get("next_action"):
            print(f"  next: {task['next_action']}")


def print_workspaces(workspaces: list[dict[str, Any]]) -> None:
    if not workspaces:
        print("No workspaces.")
        return
    for workspace in workspaces:
        print(
            f"{workspace['workspace_id']}  "
            f"[{workspace['registration_status']}/{workspace['approval_status']}] "
            f"{workspace['workspace_slug']}  path={workspace['canonical_path']}"
        )


def print_principals(principals: list[dict[str, Any]]) -> None:
    if not principals:
        print("No principals.")
        return
    for principal in principals:
        active = "active" if principal.get("active") else "inactive"
        print(
            f"{principal['principal_id']}  "
            f"[{principal['principal_type']}/{principal['trust_level']}/{active}] "
            f"{principal['display_name']}"
        )


def print_harness_profiles(profiles: list[dict[str, Any]]) -> None:
    if not profiles:
        print("No harness profiles.")
        return
    for profile in profiles:
        status = "enabled" if profile.get("enabled") else "disabled"
        print(
            f"{profile['harness_id']}  [{status}] {profile['profile_name']} "
            f"actions/hour={profile['max_actions_per_hour']} "
            f"interval={profile['min_action_interval_seconds']}s "
            f"open={profile['max_open_actions']}"
        )


def print_agent_runtimes(agents: list[dict[str, Any]]) -> None:
    if not agents:
        print("No active agent runtimes.")
        return
    for agent in agents:
        capabilities = ", ".join(agent.get("capabilities") or []) or "-"
        lease = agent.get("lease_until") or "-"
        print(
            f"{agent['agent_name']}  {agent['principal_id']}  "
            f"[{agent['role']}/{agent['status']}] "
            f"capacity={agent['max_active_tasks']} lease={lease}"
        )
        print(f"  capabilities: {capabilities}")
        if agent.get("default_harness_id"):
            print(f"  harness: {agent['default_harness_id']}")


def print_automations(tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        print("No automations.")
        return
    for task in tasks:
        schedule = task.get("schedule") or {}
        next_run = task.get("snooze_until") or task.get("due_at") or schedule.get("start_at") or "-"
        print(
            f"{task['task_id']}  "
            f"[{task.get('controller_status') or 'unknown'}/{task.get('schedule_kind') or 'none'}] "
            f"{task.get('automation_id') or '-'}  {task['title']}  next={next_run}"
        )
        workflows = (task.get("execution_contract") or {}).get("workflow_refs") or []
        if workflows:
            print(f"  workflow: {', '.join(workflows)}")


def print_hub_status(status: dict[str, Any]) -> None:
    workspace = status["workspace"]
    summary = status["task_summary"]
    print(f"Workspace: {workspace['workspace_slug']}  {workspace['workspace_id']}")
    print(f"Path:      {workspace['canonical_path']}")
    print(f"Principals: {len(status['principals'])} registered")
    agents = [principal for principal in status["principals"] if principal["principal_type"] == "agent"]
    if agents:
        print("Agents:")
        for agent in agents:
            active = "active" if agent.get("active") else "inactive"
            print(f"  {agent['display_name']}  {agent['principal_id']}  [{agent['trust_level']}/{active}]")
    else:
        print("Agents: none")
    if status.get("agent_runtimes"):
        print("Active runtimes:")
        for runtime in status["agent_runtimes"]:
            print(
                f"  {runtime['agent_name']}  "
                f"[{runtime['role']}/{runtime['status']}] "
                f"capacity={runtime['max_active_tasks']} lease={runtime.get('lease_until') or '-'}"
            )
    print(f"Harness profiles: {len(status['harness_profiles'])}")
    for profile in status["harness_profiles"]:
        enabled = "enabled" if profile.get("enabled") else "disabled"
        print(f"  {profile['profile_name']}  {profile['harness_id']}  [{enabled}]")
    print(
        "Tasks: "
        f"{summary['total_tasks']} total, "
        f"{summary['tasks_with_source_principal']} bound to source principal, "
        f"{summary['tasks_missing_source_principal']} missing source principal, "
        f"{summary['targeted_tasks']} targeted, "
        f"{summary['parented_tasks']} parented"
    )
    if summary["by_status"]:
        print("By status: " + ", ".join(f"{key}={value}" for key, value in sorted(summary["by_status"].items())))
    if summary["by_kind"]:
        print("By kind:   " + ", ".join(f"{key}={value}" for key, value in sorted(summary["by_kind"].items())))
    if summary["recent_unbound_tasks"]:
        print("Recent tasks missing source principal:")
        for task in summary["recent_unbound_tasks"]:
            print(f"  {task['task_id']}  [{task['status']}] {task['title']}")


def print_task_tree(tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        print("No tasks.")
        return
    for task in tasks:
        indent = "  " * int(task.get("tree_depth") or 0)
        due = task.get("snooze_until") or task.get("due_at") or "-"
        print(f"{indent}- {task['task_id']}  [{task['priority']}/{task['status']}] {task['title']}  due={due}")
        if task.get("target_principal_id"):
            print(f"{indent}  target_principal={task['target_principal_id']}")
        if task.get("depends_on"):
            print(f"{indent}  depends_on={', '.join(task['depends_on'])}")


def add_common_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        help=f"SQLite database path. Defaults to .tmh/tmh.sqlite or {ENV_PREFIX}_DB.",
    )
    parser.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help=f"Use the global hub database. Defaults to {default_global_db_path()} or {ENV_PREFIX}_GLOBAL_DB.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CLI_PROG,
        description=f"{APP_NAME} CLI",
        formatter_class=HELP_FORMATTER,
        epilog=examples(
            f"""
{CLI_PROG} add "내일 아침 등산" --next "아침 7시에 출발 준비" --due "2026-05-03T07:00:00+09:00"
{CLI_PROG} add --json-file .\\mydue.json
{CLI_PROG} worker --help
{CLI_PROG} notify-test --channel {DEFAULT_NOTIFICATION_CHANNEL}
{CLI_PROG} push --profile normal
            """
        ),
    )
    add_common_db(parser)
    subcommands = parser.add_subparsers(dest="command", required=True)

    init_parser = subcommands.add_parser("init", help="Initialize the local database")
    db_info_parser = subcommands.add_parser("db-info", help="Show database backend configuration")
    api_token_parser = subcommands.add_parser("api-token", help="Print the local REST write token for scripts")

    workspace = subcommands.add_parser("workspace", help="Register and inspect workspace identities")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_register = workspace_sub.add_parser("register", help="Register the current or given workspace")
    workspace_register.add_argument("--path")
    workspace_register.add_argument("--slug")
    workspace_register.add_argument("--display-name")
    workspace_register.add_argument("--repo-remote")
    workspace_register.add_argument("--repo-branch")
    workspace_register.add_argument("--type", default="project")
    workspace_register.add_argument("--registered-by-type", default="human", choices=["human", "agent", "service"])
    workspace_register.add_argument("--registered-by", default="owner")
    workspace_register.add_argument("--authority-basis", default="owner_request")
    workspace_register.add_argument("--authority-level", default="owner")
    workspace_register.add_argument("--approval-status", default="approved", choices=["approved", "proposed", "rejected"])
    workspace_register.add_argument("--approval-note", default="")
    workspace_register.add_argument("--json", action="store_true")

    workspace_list = workspace_sub.add_parser("list", help="List registered workspaces")
    workspace_list.add_argument("--status")
    workspace_list.add_argument("--json", action="store_true")

    workspace_show = workspace_sub.add_parser("show", help="Show one workspace by ID or slug")
    workspace_show.add_argument("identifier", nargs="?")

    principal = subcommands.add_parser("principal", help="Register and inspect human or agent principals")
    principal_sub = principal.add_subparsers(dest="principal_command", required=True)
    principal_ensure = principal_sub.add_parser("ensure", help="Create or update a principal identity")
    principal_ensure.add_argument("--type", default="human")
    principal_ensure.add_argument("--name", default="owner")
    principal_ensure.add_argument("--contact-ref", default="")
    principal_ensure.add_argument("--auth-method", default="manual")
    principal_ensure.add_argument("--trust-level", default="owner")
    principal_ensure.add_argument("--inactive", action="store_true")
    principal_ensure.add_argument("--json", action="store_true")

    principal_list = principal_sub.add_parser("list", help="List registered principals")
    principal_list.add_argument("--active-only", action="store_true")
    principal_list.add_argument("--json", action="store_true")

    agent = subcommands.add_parser("agent", help="Register and heartbeat active agent runtimes")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_register = agent_sub.add_parser("register", help="Register an active agent runtime")
    agent_register.add_argument("--name", required=True)
    agent_register.add_argument("--role", default="worker", choices=["worker", "orchestrator", "reviewer", "observer"])
    agent_register.add_argument("--status", default="active", choices=["active", "idle", "busy", "paused", "offline"])
    agent_register.add_argument("--capability", action="append", default=[])
    agent_register.add_argument("--harness", default="")
    agent_register.add_argument("--max-active-tasks", type=int, default=1)
    agent_register.add_argument("--lease-seconds", type=int, default=600)
    agent_register.add_argument("--notes", default="")
    agent_register.add_argument("--json", action="store_true")

    agent_heartbeat = agent_sub.add_parser("heartbeat", help="Update agent runtime heartbeat")
    agent_heartbeat.add_argument("--name", required=True)
    agent_heartbeat.add_argument("--status", default="active", choices=["active", "idle", "busy", "paused", "offline"])
    agent_heartbeat.add_argument("--current-task-id", default="")
    agent_heartbeat.add_argument("--lease-seconds", type=int, default=600)
    agent_heartbeat.add_argument("--notes", default="")
    agent_heartbeat.add_argument("--json", action="store_true")

    agent_list = agent_sub.add_parser("list", help="List active agent runtimes")
    agent_list.add_argument("--active-only", action="store_true")
    agent_list.add_argument("--role")
    agent_list.add_argument("--json", action="store_true")

    auth = subcommands.add_parser("auth", help="Register and inspect non-secret auth profile references")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_ensure = auth_sub.add_parser("ensure", help="Create or update an auth profile reference")
    auth_ensure.add_argument("--name", default="default")
    auth_ensure.add_argument("--type", default="bearer")
    auth_ensure.add_argument("--secret-ref", default="")
    auth_ensure.add_argument("--owner-principal-id", default="")
    auth_ensure.add_argument("--scope", action="append", default=[])
    auth_ensure.add_argument("--disabled", action="store_true")
    auth_ensure.add_argument("--json", action="store_true")

    auth_list = auth_sub.add_parser("list", help="List auth profile references")
    auth_list.add_argument("--json", action="store_true")

    auth_show = auth_sub.add_parser("show", help="Show one auth profile")
    auth_show.add_argument("identifier")

    network = subcommands.add_parser("network", help="Register and inspect workspace network profiles")
    network_sub = network.add_subparsers(dest="network_command", required=True)
    network_ensure = network_sub.add_parser("ensure", help="Create or update a network profile")
    network_ensure.add_argument("--name", default="loopback")
    network_ensure.add_argument("--bind-scope", default="loopback")
    network_ensure.add_argument("--api-base-url", default="")
    network_ensure.add_argument("--mcp-transport", default="stdio")
    network_ensure.add_argument("--mcp-command", default="")
    network_ensure.add_argument("--auth-profile-ref", default="")
    network_ensure.add_argument("--disabled", action="store_true")
    network_ensure.add_argument("--json", action="store_true")

    network_list = network_sub.add_parser("list", help="List network profiles")
    network_list.add_argument("--json", action="store_true")

    network_show = network_sub.add_parser("show", help="Show one network profile")
    network_show.add_argument("identifier")

    harness = subcommands.add_parser("harness", help="Register and inspect AI action intake harness profiles")
    harness_sub = harness.add_subparsers(dest="harness_command", required=True)
    harness_register = harness_sub.add_parser("register", help="Create or update a harness profile")
    harness_register.add_argument("--name", default="default")
    harness_register.add_argument("--type", default="agent")
    harness_register.add_argument("--agent-name", default="")
    harness_register.add_argument("--max-actions-per-hour", type=int, default=6)
    harness_register.add_argument("--min-action-interval-seconds", type=int, default=300)
    harness_register.add_argument("--max-open-actions", type=int, default=20)
    harness_register.add_argument("--default-priority", default="normal", choices=["low", "normal", "high", "urgent"])
    harness_register.add_argument("--default-push-profile", default="normal", choices=sorted(VALID_PUSH_PROFILES))
    harness_register.add_argument("--require-human-approval", action="store_true")
    harness_register.add_argument("--disabled", action="store_true")
    harness_register.add_argument("--classification", default="internal")
    harness_register.add_argument("--redaction-level", type=int, default=0)
    harness_register.add_argument("--api-base-url", default="")
    harness_register.add_argument("--bind-scope", default="loopback")
    harness_register.add_argument("--json", action="store_true")

    harness_list = harness_sub.add_parser("list", help="List harness profiles")
    harness_list.add_argument("--json", action="store_true")

    harness_show = harness_sub.add_parser("show", help="Show one harness profile")
    harness_show.add_argument("identifier")

    ai_action = subcommands.add_parser("ai-action", help="Register AI-proposed action items through harness rules")
    ai_action_sub = ai_action.add_subparsers(dest="ai_action_command", required=True)
    ai_action_add = ai_action_sub.add_parser("add", help="Create an AI action item if harness rules allow it")
    ai_action_add.add_argument("title")
    ai_action_add.add_argument("--summary", default="")
    ai_action_add.add_argument("--next", dest="next_action", default="")
    ai_action_add.add_argument("--detail-md", default="")
    ai_action_add.add_argument("--priority")
    ai_action_add.add_argument("--due")
    ai_action_add.add_argument("--tag", action="append", default=[])
    ai_action_add.add_argument("--action-key")
    ai_action_add.add_argument("--agent-name", default="agent")
    ai_action_add.add_argument("--harness", default="default")
    ai_action_add.add_argument("--json", action="store_true")

    automation = subcommands.add_parser(
        "automation",
        help="Register and inspect recurring automation definitions",
        formatter_class=HELP_FORMATTER,
        epilog=examples(
            f"""
{CLI_PROG} automation add "Daily AI Pulse" --rrule "FREQ=DAILY;BYHOUR=7;BYMINUTE=30" --workflow daily-ai-pulse --requires-review
{CLI_PROG} automation list
{CLI_PROG} --global automation list --status active --json
            """
        ),
    )
    automation_sub = automation.add_subparsers(dest="automation_command", required=True)
    automation_add = automation_sub.add_parser("add", help="Create an automation definition task")
    automation_add.add_argument("title")
    automation_add.add_argument("--slug")
    automation_add.add_argument("--summary", default="")
    automation_add.add_argument("--next", dest="next_action", default="")
    automation_add.add_argument("--detail-md", default="")
    automation_add.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    automation_add.add_argument("--status", default="scheduled")
    automation_add.add_argument("--controller-status", default="active")
    automation_add.add_argument("--execution-mode", default="agent_assisted")
    automation_add.add_argument("--schedule-kind")
    automation_add.add_argument("--due")
    automation_add.add_argument("--timezone", default="Asia/Seoul")
    automation_add.add_argument("--start-at")
    automation_add.add_argument("--rrule")
    automation_add.add_argument("--catch-up-policy", default="latest_only", choices=["latest_only", "run_all_missed", "skip_missed"])
    automation_add.add_argument("--automation-id")
    automation_add.add_argument("--target-workspace-id")
    automation_add.add_argument("--target-principal-id")
    automation_add.add_argument("--harness-id")
    automation_add.add_argument("--required-capability", action="append", default=[])
    automation_add.add_argument("--blocked-capability", action="append", default=[])
    automation_add.add_argument("--skill", action="append", default=[])
    automation_add.add_argument("--rule", action="append", default=[])
    automation_add.add_argument("--workflow", action="append", default=[])
    automation_add.add_argument("--hook", action="append", default=[])
    automation_add.add_argument("--script", action="append", default=[])
    automation_add.add_argument("--output-root")
    automation_add.add_argument("--artifact", action="append", default=[])
    automation_add.add_argument("--deliver", action="append", default=[])
    automation_add.add_argument("--requires-review", action="store_true")
    automation_add.add_argument("--approval-required", action="store_true")
    automation_add.add_argument("--dry-run-default", action="store_true")
    automation_add.add_argument("--tag", action="append", default=[])
    automation_add.add_argument("--idempotency-key")
    automation_add.add_argument("--execution-contract-json")
    automation_add.add_argument("--schedule-json")
    automation_add.add_argument("--artifact-contract-json")
    automation_add.add_argument("--mirror", action="store_true", help="Append a workspace .automation registry mirror record")
    automation_add.add_argument("--json", action="store_true")

    automation_list = automation_sub.add_parser("list", help="List automation definition tasks")
    automation_list.add_argument("--status", dest="controller_status")
    automation_list.add_argument("--include-runs", action="store_true")
    automation_list.add_argument("--limit", type=int, default=50)
    automation_list.add_argument("--json", action="store_true")

    automation_show = automation_sub.add_parser("show", help="Show one automation task by task_id")
    automation_show.add_argument("task_id")

    add = subcommands.add_parser(
        "add",
        help="Create a task",
        formatter_class=HELP_FORMATTER,
        epilog=examples(
            f"""
{CLI_PROG} add "내일 아침 등산" --next "아침 7시에 출발 준비" --due "2026-05-03T07:00:00+09:00"
{CLI_PROG} add "TMH hub status 점검" --by owner --target-agent codex --harness cautious --rank 10
{CLI_PROG} add "하위 작업" --parent tmh_parent_id --depends-on tmh_blocker_id --target-agent cline
{CLI_PROG} add --json-file .\\mydue.json
{CLI_PROG} add --json-file .\\mydue.json --priority high --json

JSON file shape:
  {{
    "title": "내일 아침 등산",
    "next_action": "아침 7시에 출발 준비",
    "due_at": "2026-05-03T07:00:00+09:00",
    "priority": "normal",
    "tags": ["personal", "alarm"]
  }}
            """
        ),
    )
    add.add_argument("title", nargs="?")
    add.add_argument("--json-file", help="Read one JSON object as the new task payload. Use import-json for arrays/sync.")
    add.add_argument("--summary")
    add.add_argument("--next", dest="next_action")
    add.add_argument("--detail-md")
    add.add_argument("--priority", choices=["low", "normal", "high", "urgent"])
    add.add_argument("--rank", type=int)
    add.add_argument("--due")
    add.add_argument("--kind", dest="task_kind", help="Task kind: reminder, action, delegated-task, automation, workflow-run, review-gate")
    add.add_argument("--execution-mode")
    add.add_argument("--schedule-kind")
    add.add_argument("--controller-status")
    add.add_argument("--automation-id")
    add.add_argument("--parent-task-id")
    add.add_argument("--parent", help="Alias for --parent-task-id")
    add.add_argument("--tag", action="append", default=[])
    add.add_argument("--depends-on", action="append", default=[])
    add.add_argument("--workspace")
    add.add_argument("--by", help="Register/use the principal creating this task by display name")
    add.add_argument("--by-type", default="human", choices=["human", "agent", "service"])
    add.add_argument("--source-principal", help="Existing or new source principal display name")
    add.add_argument("--source-principal-id", help="Existing source principal_id")
    add.add_argument("--target-workspace", help="Target workspace id or slug")
    add.add_argument("--target-workspace-id")
    add.add_argument("--target-principal", help="Existing target principal id or display name")
    add.add_argument("--target-agent", help="Register/use an agent principal as target")
    add.add_argument("--target-human", help="Register/use a human principal as target")
    add.add_argument("--target-principal-id")
    add.add_argument("--harness", help="Harness profile name or id")
    add.add_argument("--harness-id")
    add.add_argument("--approve", action="store_true", help="Mark the source principal as approver")
    add.add_argument("--approved-by", help="Existing principal id or display name used as approver")
    add.add_argument("--source-agent")
    add.add_argument("--idempotency-key")
    add.add_argument("--execution-contract-json")
    add.add_argument("--schedule-json")
    add.add_argument("--artifact-contract-json")
    add.add_argument("--json", action="store_true")

    list_parser = subcommands.add_parser("list", help="List tasks")
    list_parser.add_argument("--status")
    list_parser.add_argument("--kind", dest="task_kind")
    list_parser.add_argument("--controller-status")
    list_parser.add_argument("--source-principal")
    list_parser.add_argument("--target-principal")
    list_parser.add_argument("--harness")
    list_parser.add_argument("--parent")
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--json", action="store_true")

    due = subcommands.add_parser("due", help="List due tasks")
    due.add_argument("--limit", type=int, default=50)
    due.add_argument("--json", action="store_true")

    status_parser = subcommands.add_parser("status", help="Show workspace registry and task binding status")
    status_parser.add_argument("--json", action="store_true")

    bind_missing = subcommands.add_parser("bind-missing", help="Backfill source principal/workspace bindings for old unbound tasks")
    bind_missing.add_argument("--source", default="owner", help="Source principal display name or id")
    bind_missing.add_argument("--source-type", default="human", choices=["human", "agent", "service"])
    bind_missing.add_argument("--limit", type=int, default=100)
    bind_missing.add_argument("--yes", action="store_true", help="Apply changes. Without this, only dry-run.")
    bind_missing.add_argument("--json", action="store_true")

    tree_parser = subcommands.add_parser("tree", help="Show parent/child task hierarchy")
    tree_parser.add_argument("task_id", nargs="?")
    tree_parser.add_argument("--limit", type=int, default=200)
    tree_parser.add_argument("--json", action="store_true")

    show = subcommands.add_parser("show", help="Show one task")
    show.add_argument("task_id")

    context = subcommands.add_parser("context", help="Show an agent resume context pack")
    context.add_argument("task_id")

    update_parser = subcommands.add_parser("update", help="Update task fields")
    update_parser.add_argument("task_id")
    update_parser.add_argument("--title")
    update_parser.add_argument("--summary")
    update_parser.add_argument("--next", dest="next_action")
    update_parser.add_argument("--priority", choices=["low", "normal", "high", "urgent"])
    update_parser.add_argument("--rank", type=int)
    update_parser.add_argument("--due")
    update_parser.add_argument("--status")
    update_parser.add_argument("--tag", action="append")
    update_parser.add_argument("--depends-on", action="append")

    ack = subcommands.add_parser("ack", help="Acknowledge a task")
    ack.add_argument("task_id")

    approve = subcommands.add_parser("approve", help="Approve a task for runner execution")
    approve.add_argument("task_id")
    approve.add_argument("--by", default="owner")
    approve.add_argument("--reason", default="")

    reject = subcommands.add_parser("reject", help="Reject a task until it is revised")
    reject.add_argument("task_id")
    reject.add_argument("--by", default="owner")
    reject.add_argument("--reason", default="")

    request_changes = subcommands.add_parser("request-changes", help="Request human/agent revisions before execution")
    request_changes.add_argument("task_id")
    request_changes.add_argument("--by", default="owner")
    request_changes.add_argument("--reason", default="")

    stop = subcommands.add_parser("stop", help="Request a running or assigned task to pause")
    stop.add_argument("task_id")
    stop.add_argument("--by", default="owner")
    stop.add_argument("--reason", default="")

    done = subcommands.add_parser("done", help="Complete a task")
    done.add_argument("task_id")
    done.add_argument("--owner", default="manual")

    snooze = subcommands.add_parser("snooze", help="Snooze a task")
    snooze.add_argument("task_id")
    group = snooze.add_mutually_exclusive_group(required=True)
    group.add_argument("--until")
    group.add_argument("--for", dest="duration")

    claim = subcommands.add_parser("claim-next", help="Claim the next due/ordered task for an agent")
    claim.add_argument("--owner", required=True)
    claim.add_argument("--lease-seconds", type=int, default=1800)
    claim.add_argument("--workspace")
    claim.add_argument("--include-not-due", action="store_true")
    claim.add_argument("--include-unassigned", action="store_true")

    release = subcommands.add_parser("release", help="Release a claimed task")
    release.add_argument("task_id")
    release.add_argument("--owner")
    release.add_argument("--next-status", default="acknowledged")

    heartbeat = subcommands.add_parser("heartbeat", help="Extend a task claim lease")
    heartbeat.add_argument("task_id")
    heartbeat.add_argument("--owner", required=True)
    heartbeat.add_argument("--lease-seconds", type=int, default=1800)

    progress = subcommands.add_parser("progress", help="Append an agent progress event")
    progress.add_argument("task_id")
    progress.add_argument("message")
    progress.add_argument("--owner", default="agent")

    review_gate = subcommands.add_parser("review-gate", help="Request or decide a human review gate")
    review_gate_sub = review_gate.add_subparsers(dest="review_gate_command", required=True)
    review_gate_request = review_gate_sub.add_parser("request", help="Create an idempotent review gate for a task")
    review_gate_request.add_argument("task_id")
    review_gate_request.add_argument("--reason", default="")
    review_gate_request.add_argument("--by", default="owner")
    review_gate_request.add_argument("--gate-type", default="pre_execution")
    review_gate_request.add_argument("--reviewer", default="", help="Reviewer principal name or id")
    review_gate_request.add_argument("--json", action="store_true")
    review_gate_decide = review_gate_sub.add_parser("decide", help="Apply a review gate decision to its subject task")
    review_gate_decide.add_argument("gate_task_id")
    review_gate_decide.add_argument("--decision", required=True, choices=["approved", "rejected", "changes_requested", "approve", "reject", "request-changes"])
    review_gate_decide.add_argument("--by", default="owner")
    review_gate_decide.add_argument("--reason", default="")
    review_gate_decide.add_argument("--json", action="store_true")

    runner = subcommands.add_parser("runner", help="Run a policy-aware harness runner")
    runner_sub = runner.add_subparsers(dest="runner_command", required=True)
    runner_once = runner_sub.add_parser("once", help="Run one harness runner pass")
    runner_once.add_argument("--name", default="tmh-runner")
    runner_once.add_argument("--backend", default="dry_run", choices=["dry_run", "deepagents_cli", "script_ref"])
    runner_once.add_argument("--backend-command", default="")
    runner_once.add_argument("--timeout-seconds", type=int, default=120)
    runner_once.add_argument("--script-allowlist", default="", help="JSON allowlist for script_ref backend")
    runner_once.add_argument("--capability", action="append", default=[])
    runner_once.add_argument("--harness", default="")
    runner_once.add_argument("--task-id", default="")
    runner_once.add_argument("--due-only", action="store_true")
    runner_once.add_argument("--run-orchestrator", action="store_true")
    runner_once.add_argument("--json", action="store_true")
    runner_watch = runner_sub.add_parser("watch", help="Run repeated harness runner passes")
    runner_watch.add_argument("--name", default="tmh-runner")
    runner_watch.add_argument("--backend", default="dry_run", choices=["dry_run", "deepagents_cli", "script_ref"])
    runner_watch.add_argument("--backend-command", default="")
    runner_watch.add_argument("--timeout-seconds", type=int, default=120)
    runner_watch.add_argument("--script-allowlist", default="", help="JSON allowlist for script_ref backend")
    runner_watch.add_argument("--capability", action="append", default=[])
    runner_watch.add_argument("--harness", default="")
    runner_watch.add_argument("--due-only", action="store_true")
    runner_watch.add_argument("--run-orchestrator", action="store_true")
    runner_watch.add_argument("--interval-seconds", type=int, default=15)
    runner_watch.add_argument("--iterations", type=int, default=0)
    runner_watch.add_argument("--json", action="store_true")

    import_md = subcommands.add_parser("import-md", help="Import a Markdown task")
    import_md.add_argument("path")

    import_json_parser = subcommands.add_parser(
        "import-json",
        help="Import JSON task object or array",
        formatter_class=HELP_FORMATTER,
        epilog=examples(
            f"""
{CLI_PROG} import-json .\\tasks.json
{CLI_PROG} import-json .\\exported-tasks.json

Use this for file bridge, bulk import, restore-like flows, or preserving task_id.
Use "{CLI_PROG} add --json-file .\\mydue.json" for one new alarm/task.
            """
        ),
    )
    import_json_parser.add_argument("path")

    export_json_parser = subcommands.add_parser("export-json", help="Export tasks to JSON")
    export_json_parser.add_argument("path")
    export_json_parser.add_argument("--status")

    export_md = subcommands.add_parser("export-md", help="Export one task to Markdown")
    export_md.add_argument("task_id")
    export_md.add_argument("path")

    backup = subcommands.add_parser("backup", help="Create a SQLite snapshot backup")
    backup.add_argument("path", nargs="?", default=".tmh/backups")

    restore = subcommands.add_parser("restore", help="Restore SQLite DB from a snapshot backup")
    restore.add_argument("path")
    restore.add_argument("--yes", action="store_true", help="Required because restore overwrites the selected DB")

    enqueue = subcommands.add_parser("enqueue-due", help="Create notification jobs for due tasks")
    enqueue.add_argument("--channel", default=DEFAULT_NOTIFICATION_CHANNEL)

    dispatch = subcommands.add_parser(
        "dispatch-once",
        help="Enqueue due work and dispatch ready notification jobs",
        formatter_class=HELP_FORMATTER,
        epilog=examples(
            f"""
{CLI_PROG} dispatch-once --channel {DEFAULT_NOTIFICATION_CHANNEL}
{CLI_PROG} dispatch-once --channel local
{CLI_PROG} attempts
            """
        ),
    )
    dispatch.add_argument("--channel", default=DEFAULT_NOTIFICATION_CHANNEL)
    dispatch.add_argument("--worker-id", default="tmh-cli")
    dispatch.add_argument("--dispatch-limit", type=int, default=10)

    notify_test = subcommands.add_parser(
        "notify-test",
        help="Send a synthetic notification through a channel",
        formatter_class=HELP_FORMATTER,
        epilog=examples(
            f"""
{CLI_PROG} notify-test
{CLI_PROG} notify-test --channel {DEFAULT_NOTIFICATION_CHANNEL} --title "{APP_SHORT_NAME} 알림 테스트"
{CLI_PROG} notify-test --capabilities
            """
        ),
    )
    notify_test.add_argument("--channel", default=DEFAULT_NOTIFICATION_CHANNEL)
    notify_test.add_argument("--title", default=f"{APP_NAME} notification test")
    notify_test.add_argument("--summary", default="Synthetic notification smoke test")
    notify_test.add_argument("--next", dest="next_action", default="알림이 보이거나 local fallback 기록이 남는지 확인")
    notify_test.add_argument("--capabilities", action="store_true")

    worker = subcommands.add_parser(
        "worker",
        help="Control notification worker pause state",
        formatter_class=HELP_FORMATTER,
        epilog=examples(
            f"""
{CLI_PROG} worker status
{CLI_PROG} worker pause --reason manual
{CLI_PROG} worker resume
tmh-worker --channel {DEFAULT_NOTIFICATION_CHANNEL} --interval 60
            """
        ),
    )
    worker_sub = worker.add_subparsers(dest="worker_command", required=True)
    worker_pause = worker_sub.add_parser("pause", help="Pause worker dispatch for this DB")
    worker_pause.add_argument("--reason", default="manual")
    worker_resume = worker_sub.add_parser("resume", help="Resume worker dispatch for this DB")
    worker_status_parser = worker_sub.add_parser("status", help="Show worker pause state")

    attempts = subcommands.add_parser("attempts", help="List notification attempts")
    attempts.add_argument("--job-id")
    attempts.add_argument("--limit", type=int, default=50)

    push = subcommands.add_parser("push", help="Push local workspace tasks to the global hub")
    push.add_argument("--global-db", help=f"Global hub DB path. Defaults to {ENV_PREFIX}_GLOBAL_DB or user home.")
    push.add_argument("--registered-by-type", default="human", choices=["human", "agent", "service"])
    push.add_argument("--registered-by", default="owner")
    push.add_argument("--authority-basis", default="owner_request")
    push.add_argument("--authority-level", default="owner")
    push.add_argument("--approval-status", default="approved", choices=["approved", "proposed", "rejected"])
    push.add_argument("--profile", default="normal", choices=sorted(VALID_PUSH_PROFILES))
    push.add_argument("--limit", type=int, default=1000)
    push.add_argument("--include-archived", action="store_true")
    push.add_argument("--json", action="store_true")

    pull = subcommands.add_parser("pull", help="Pull approved tasks targeted to this workspace from the global hub")
    pull.add_argument("--global-db", help=f"Global hub DB path. Defaults to {ENV_PREFIX}_GLOBAL_DB or user home.")
    pull.add_argument("--registered-by-type", default="human", choices=["human", "agent", "service"])
    pull.add_argument("--registered-by", default="owner")
    pull.add_argument("--limit", type=int, default=100)
    pull.add_argument("--json", action="store_true")

    orchestrator = subcommands.add_parser("orchestrator", help="Assign unassigned work to active agents through the hub")
    orchestrator_sub = orchestrator.add_subparsers(dest="orchestrator_command", required=True)
    orchestrator_run_once = orchestrator_sub.add_parser("run-once", help="Run one assignment pass")
    orchestrator_run_once.add_argument("--name", default="orchestrator")
    orchestrator_run_once.add_argument("--limit", type=int, default=10)
    orchestrator_run_once.add_argument("--due-only", action="store_true")
    orchestrator_run_once.add_argument("--default-harness", default="")
    orchestrator_run_once.add_argument("--dry-run", action="store_true")
    orchestrator_run_once.add_argument("--json", action="store_true")

    fetch_origin = subcommands.add_parser("fetch-origin", help="Fetch the source workspace task behind a global hub task")
    fetch_origin.add_argument("hub_task_id")
    fetch_origin.add_argument("--global-db", help=f"Global hub DB path. Defaults to {ENV_PREFIX}_GLOBAL_DB or user home.")
    fetch_origin.add_argument("--source-db", help="Override source workspace DB path when registry path is not reachable")
    fetch_origin.add_argument("--json", action="store_true")

    serve = subcommands.add_parser("serve", help="Run loopback REST API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)

    set_examples(init_parser, f"{CLI_PROG} init")
    set_examples(db_info_parser, f"{CLI_PROG} db-info")
    set_examples(api_token_parser, f"{CLI_PROG} api-token")
    set_examples(workspace, f"{CLI_PROG} workspace register --registered-by owner\n{CLI_PROG} workspace list")
    set_examples(workspace_register, f"{CLI_PROG} workspace register --registered-by owner --authority-basis owner_request")
    set_examples(workspace_list, f"{CLI_PROG} workspace list --json")
    set_examples(workspace_show, f"{CLI_PROG} workspace show\n{CLI_PROG} workspace show task-memory-hub")
    set_examples(principal, f"{CLI_PROG} principal ensure --type human --name owner\n{CLI_PROG} principal list")
    set_examples(principal_ensure, f"{CLI_PROG} principal ensure --type agent --name cline --trust-level trusted")
    set_examples(principal_list, f"{CLI_PROG} principal list --active-only")
    set_examples(agent, f"{CLI_PROG} agent register --name codex --capability local-shell\n{CLI_PROG} agent list --active-only")
    set_examples(agent_register, f"{CLI_PROG} agent register --name codex --capability tmh-mcp --capability local-shell --harness cautious --max-active-tasks 2")
    set_examples(agent_heartbeat, f"{CLI_PROG} agent heartbeat --name codex --status active")
    set_examples(agent_list, f"{CLI_PROG} agent list\n{CLI_PROG} agent list --active-only --json")
    set_examples(auth, f"{CLI_PROG} auth ensure --name local-api --secret-ref env:TASK_MEMORY_HUB_API_TOKEN")
    set_examples(auth_ensure, f"{CLI_PROG} auth ensure --name local-api --secret-ref env:TASK_MEMORY_HUB_API_TOKEN --scope pull")
    set_examples(auth_list, f"{CLI_PROG} auth list")
    set_examples(auth_show, f"{CLI_PROG} auth show local-api")
    set_examples(network, f"{CLI_PROG} network ensure --name local-loopback --api-base-url http://127.0.0.1:8787")
    set_examples(network_ensure, f"{CLI_PROG} network ensure --name local-loopback --mcp-command tmh-mcp")
    set_examples(network_list, f"{CLI_PROG} network list")
    set_examples(network_show, f"{CLI_PROG} network show local-loopback")
    set_examples(harness, f"{CLI_PROG} harness register --name cautious --agent-name codex\n{CLI_PROG} harness list")
    set_examples(harness_register, f"{CLI_PROG} harness register --name cautious --max-actions-per-hour 2 --max-open-actions 5")
    set_examples(harness_list, f"{CLI_PROG} harness list")
    set_examples(harness_show, f"{CLI_PROG} harness show cautious")
    set_examples(ai_action, f"{CLI_PROG} ai-action add \"toast 기능 확인\" --harness cautious --agent-name codex")
    set_examples(ai_action_add, f"{CLI_PROG} ai-action add \"toast 기능 확인\" --action-key toast-check --harness cautious")
    set_examples(automation, f"{CLI_PROG} automation add \"Daily AI Pulse\" --rrule \"FREQ=DAILY;BYHOUR=7;BYMINUTE=30\" --workflow daily-ai-pulse\n{CLI_PROG} automation list")
    set_examples(automation_add, f"{CLI_PROG} automation add \"Daily AI Pulse\" --rrule \"FREQ=DAILY;BYHOUR=7;BYMINUTE=30\" --workflow daily-ai-pulse --requires-review")
    set_examples(automation_list, f"{CLI_PROG} automation list\n{CLI_PROG} --global automation list --status active --json")
    set_examples(automation_show, f"{CLI_PROG} automation show tmh_example")
    set_examples(add, f"{CLI_PROG} add \"작업\" --by owner --target-agent codex --harness cautious\n{CLI_PROG} add \"하위 작업\" --parent tmh_parent_id --target-agent cline")
    set_examples(list_parser, f"{CLI_PROG} list\n{CLI_PROG} list --target-principal codex --json\n{CLI_PROG} list --parent tmh_parent_id")
    set_examples(due, f"{CLI_PROG} due\n{CLI_PROG} due --limit 10 --json")
    set_examples(status_parser, f"{CLI_PROG} status\n{CLI_PROG} status --json")
    set_examples(bind_missing, f"{CLI_PROG} bind-missing --source owner\n{CLI_PROG} bind-missing --source owner --yes")
    set_examples(tree_parser, f"{CLI_PROG} tree\n{CLI_PROG} tree tmh_parent_id")
    set_examples(show, f"{CLI_PROG} show tmh_example")
    set_examples(context, f"{CLI_PROG} context tmh_example")
    set_examples(update_parser, f"{CLI_PROG} update tmh_example --status acknowledged\n{CLI_PROG} update tmh_example --priority high --rank 10")
    set_examples(ack, f"{CLI_PROG} ack tmh_example")
    set_examples(approve, f"{CLI_PROG} approve tmh_example --by owner --reason \"사람 검토 완료\"")
    set_examples(reject, f"{CLI_PROG} reject tmh_example --by owner --reason \"출처 확인 필요\"")
    set_examples(request_changes, f"{CLI_PROG} request-changes tmh_example --by owner --reason \"산출물 기준 보완\"")
    set_examples(stop, f"{CLI_PROG} stop tmh_example --by owner --reason \"사람 판단 대기\"")
    set_examples(done, f"{CLI_PROG} done tmh_example --owner codex")
    set_examples(snooze, f"{CLI_PROG} snooze tmh_example --for 1d\n{CLI_PROG} snooze tmh_example --until 2026-05-03T09:00:00+09:00")
    set_examples(claim, f"{CLI_PROG} claim-next --owner cline --include-not-due")
    set_examples(release, f"{CLI_PROG} release tmh_example --owner cline --next-status acknowledged")
    set_examples(heartbeat, f"{CLI_PROG} heartbeat tmh_example --owner cline")
    set_examples(progress, f"{CLI_PROG} progress tmh_example \"구현 시작\" --owner cline")
    set_examples(review_gate, f"{CLI_PROG} review-gate request tmh_example --reason \"외부 write 전 검토\"\n{CLI_PROG} review-gate decide tmh_gate --decision approved --by owner")
    set_examples(review_gate_request, f"{CLI_PROG} review-gate request tmh_example --reason \"external_write 승인 필요\" --by owner")
    set_examples(review_gate_decide, f"{CLI_PROG} review-gate decide tmh_gate --decision approved --by owner --reason \"검토 완료\"")
    set_examples(runner, f"{CLI_PROG} runner once --name tmh-runner --backend dry_run --json\n{CLI_PROG} runner watch --iterations 3")
    set_examples(runner_once, f"{CLI_PROG} runner once --task-id tmh_example --backend dry_run --capability tmh-api --json\n{CLI_PROG} runner once --backend deepagents_cli --backend-command \"python scripts\\tmh-deepagents-smoke.py\" --timeout-seconds 30\n{CLI_PROG} runner once --backend script_ref --script-allowlist .\\script-backends.json --capability tmh-api --json")
    set_examples(runner_watch, f"{CLI_PROG} runner watch --name tmh-runner --interval-seconds 30")
    set_examples(import_md, f"{CLI_PROG} import-md .\\task.md")
    set_examples(import_json_parser, f"{CLI_PROG} import-json .\\tasks.json\n{CLI_PROG} import-json .\\exported-tasks.json")
    set_examples(export_json_parser, f"{CLI_PROG} export-json .\\tasks.json\n{CLI_PROG} export-json .\\scheduled.json --status scheduled")
    set_examples(export_md, f"{CLI_PROG} export-md tmh_example .\\task.md")
    set_examples(backup, f"{CLI_PROG} backup .tmh\\backups")
    set_examples(restore, f"{CLI_PROG} restore .tmh\\backups\\tmh.sqlite --yes")
    set_examples(enqueue, f"{CLI_PROG} enqueue-due --channel {DEFAULT_NOTIFICATION_CHANNEL}")
    set_examples(worker_pause, f"{CLI_PROG} worker pause --reason manual")
    set_examples(worker_resume, f"{CLI_PROG} worker resume")
    set_examples(worker_status_parser, f"{CLI_PROG} worker status")
    set_examples(attempts, f"{CLI_PROG} attempts\n{CLI_PROG} attempts --job-id job_example")
    set_examples(push, f"{CLI_PROG} push --profile normal\n{CLI_PROG} push --profile manifest")
    set_examples(pull, f"{CLI_PROG} pull --global-db %USERPROFILE%\\.task-memory-hub\\tmh.sqlite")
    set_examples(orchestrator, f"{CLI_PROG} orchestrator run-once --name ralph-orchestrator\n{CLI_PROG} orchestrator run-once --dry-run --json")
    set_examples(orchestrator_run_once, f"{CLI_PROG} orchestrator run-once --name ralph-orchestrator --limit 5")
    set_examples(fetch_origin, f"{CLI_PROG} fetch-origin tmh_hub_example --json")
    set_examples(serve, f"{CLI_PROG} serve --port 8787")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = args.db or (str(default_global_db_path()) if getattr(args, "global_scope", False) else None)

    try:
        if args.command == "init":
            path = ensure_db(db_path)
            print(f"Initialized {path}")
            return 0

        if args.command == "db-info":
            url = database_url(db_path=db_path, global_scope=getattr(args, "global_scope", False))
            print_json(
                {
                    "active_store": "sqlite",
                    "configured_backend": database_backend(url),
                    "database_url": redact_database_url(url),
                    "note": "PostgreSQL URL parsing is prepared; runtime store is SQLite in this build.",
                }
            )
            return 0

        if args.command == "api-token":
            print(get_or_create_api_token())
            print(f"# token file: {default_api_token_path()}", file=sys.stderr)
            return 0

        if args.command == "workspace":
            if args.workspace_command == "register":
                workspace = register_workspace(
                    canonical_path=args.path,
                    workspace_slug=args.slug,
                    display_name=args.display_name,
                    repo_remote=args.repo_remote,
                    repo_branch=args.repo_branch,
                    workspace_type=args.type,
                    registered_by_principal_type=args.registered_by_type,
                    registered_by_display_name=args.registered_by,
                    authority_basis=args.authority_basis,
                    authority_level=args.authority_level,
                    approval_status=args.approval_status,
                    approval_note=args.approval_note,
                    db_path=db_path,
                )
                print_json(workspace) if args.json else print_workspaces([workspace])
                return 0
            if args.workspace_command == "list":
                workspaces = list_workspaces(db_path=db_path, status=args.status)
                print_json(workspaces) if args.json else print_workspaces(workspaces)
                return 0
            if args.workspace_command == "show":
                workspace = get_workspace(args.identifier, db_path=db_path) if args.identifier else current_workspace(db_path=db_path)
                print_json(workspace)
                return 0

        if args.command == "principal":
            if args.principal_command == "ensure":
                principal = ensure_principal(
                    principal_type=args.type,
                    display_name=args.name,
                    contact_ref=args.contact_ref,
                    auth_method=args.auth_method,
                    trust_level=args.trust_level,
                    active=not args.inactive,
                    db_path=db_path,
                )
                print_json(principal) if args.json else print_principals([principal])
                return 0
            if args.principal_command == "list":
                principals = list_principals(db_path=db_path, active_only=args.active_only)
                print_json(principals) if args.json else print_principals(principals)
                return 0

        if args.command == "agent":
            workspace = current_workspace(db_path=db_path)
            if args.agent_command == "register":
                harness_id = ""
                if args.harness:
                    harness_id = get_harness_profile(
                        args.harness,
                        db_path=db_path,
                        workspace_id=workspace["workspace_id"],
                    )["harness_id"]
                runtime = register_agent_runtime(
                    agent_name=args.name,
                    workspace_id=workspace["workspace_id"],
                    role=args.role,
                    status=args.status,
                    capabilities=args.capability,
                    default_harness_id=harness_id,
                    max_active_tasks=args.max_active_tasks,
                    lease_seconds=args.lease_seconds,
                    notes=args.notes,
                    db_path=db_path,
                )
                print_json(runtime) if args.json else print_agent_runtimes([runtime])
                return 0
            if args.agent_command == "heartbeat":
                runtime = heartbeat_agent_runtime(
                    agent_name=args.name,
                    workspace_id=workspace["workspace_id"],
                    status=args.status,
                    current_task_id=args.current_task_id,
                    lease_seconds=args.lease_seconds,
                    notes=args.notes,
                    db_path=db_path,
                )
                print_json(runtime) if args.json else print_agent_runtimes([runtime])
                return 0
            if args.agent_command == "list":
                agents = list_agent_runtimes(
                    db_path=db_path,
                    workspace_id=workspace["workspace_id"],
                    active_only=args.active_only,
                    role=args.role,
                )
                print_json(agents) if args.json else print_agent_runtimes(agents)
                return 0

        if args.command == "auth":
            workspace = current_workspace(db_path=db_path)
            if args.auth_command == "ensure":
                profile = ensure_auth_profile(
                    profile_name=args.name,
                    workspace_id=workspace["workspace_id"],
                    auth_type=args.type,
                    secret_ref=args.secret_ref,
                    owner_principal_id=args.owner_principal_id,
                    allowed_scopes=args.scope,
                    enabled=not args.disabled,
                    db_path=db_path,
                )
                print_json(profile) if args.json else print_json(profile)
                return 0
            if args.auth_command == "list":
                profiles = list_auth_profiles(db_path=db_path, workspace_id=workspace["workspace_id"])
                print_json(profiles)
                return 0
            if args.auth_command == "show":
                print_json(get_auth_profile(args.identifier, db_path=db_path, workspace_id=workspace["workspace_id"]))
                return 0

        if args.command == "network":
            workspace = current_workspace(db_path=db_path)
            if args.network_command == "ensure":
                profile = ensure_network_profile(
                    workspace_id=workspace["workspace_id"],
                    profile_name=args.name,
                    bind_scope=args.bind_scope,
                    api_base_url=args.api_base_url,
                    mcp_transport=args.mcp_transport,
                    mcp_command=args.mcp_command,
                    auth_profile_ref=args.auth_profile_ref,
                    enabled=not args.disabled,
                    db_path=db_path,
                )
                print_json(profile) if args.json else print_json(profile)
                return 0
            if args.network_command == "list":
                profiles = list_network_profiles(db_path=db_path, workspace_id=workspace["workspace_id"])
                print_json(profiles)
                return 0
            if args.network_command == "show":
                print_json(get_network_profile(args.identifier, db_path=db_path, workspace_id=workspace["workspace_id"]))
                return 0

        if args.command == "harness":
            workspace = current_workspace(db_path=db_path)
            if args.harness_command == "register":
                principal_id = ""
                if args.agent_name:
                    principal = ensure_principal(
                        principal_type="agent",
                        display_name=args.agent_name,
                        trust_level="trusted",
                        db_path=db_path,
                    )
                    principal_id = principal["principal_id"]
                policy = ensure_policy_profile(
                    profile_name=f"{args.name}-policy",
                    classification=args.classification,
                    redaction_level=args.redaction_level,
                    db_path=db_path,
                )
                network = ensure_network_profile(
                    workspace_id=workspace["workspace_id"],
                    profile_name=f"{args.name}-network",
                    bind_scope=args.bind_scope,
                    api_base_url=args.api_base_url,
                    db_path=db_path,
                )
                profile = register_harness_profile(
                    profile_name=args.name,
                    workspace_id=workspace["workspace_id"],
                    harness_type=args.type,
                    default_agent_principal_id=principal_id,
                    policy_profile_id=policy["policy_profile_id"],
                    network_profile_id=network["network_profile_id"],
                    max_actions_per_hour=args.max_actions_per_hour,
                    min_action_interval_seconds=args.min_action_interval_seconds,
                    max_open_actions=args.max_open_actions,
                    default_priority=args.default_priority,
                    default_push_profile=args.default_push_profile,
                    require_human_approval=args.require_human_approval,
                    enabled=not args.disabled,
                    db_path=db_path,
                )
                print_json({"harness": profile, "policy": policy, "network": network}) if args.json else print_harness_profiles([profile])
                return 0
            if args.harness_command == "list":
                profiles = list_harness_profiles(db_path=db_path, workspace_id=workspace["workspace_id"])
                print_json(profiles) if args.json else print_harness_profiles(profiles)
                return 0
            if args.harness_command == "show":
                print_json(get_harness_profile(args.identifier, db_path=db_path, workspace_id=workspace["workspace_id"]))
                return 0

        if args.command == "ai-action":
            if args.ai_action_command == "add":
                result = register_ai_action_item(
                    title=args.title,
                    summary=args.summary,
                    next_action=args.next_action,
                    detail_md=args.detail_md,
                    priority=args.priority,
                    due_at=args.due,
                    tags=args.tag,
                    action_key=args.action_key,
                    agent_name=args.agent_name,
                    harness=args.harness,
                    db_path=db_path,
                )
                if args.json:
                    print_json(result)
                elif result.get("accepted"):
                    print_tasks([result["task"]])
                else:
                    print(f"Rejected: {result.get('reason')}")
                return 0

        if args.command == "automation":
            if args.automation_command == "add":
                payload = build_automation_payload(args, db_path)
                task = create_task(payload, db_path=db_path, actor="manual")
                mirror_path = None
                if args.mirror:
                    mirror_path = write_automation_mirror(task, current_workspace(db_path=db_path))
                if args.json:
                    result = {"task": task}
                    if mirror_path:
                        result["mirror_path"] = str(mirror_path)
                    print_json(result)
                else:
                    print_automations([task])
                    if mirror_path:
                        print(f"mirror={mirror_path}")
                return 0
            if args.automation_command == "list":
                automations = list_automations(
                    db_path=db_path,
                    controller_status=args.controller_status,
                    include_runs=args.include_runs,
                    limit=args.limit,
                )
                print_json(automations) if args.json else print_automations(automations)
                return 0
            if args.automation_command == "show":
                print_json(get_task(args.task_id, db_path=db_path))
                return 0

        if args.command == "add":
            payload = build_add_payload(args, db_path)
            actor = payload.pop("_actor", payload.get("source_agent", "manual"))
            task = create_task(payload, db_path=db_path, actor=actor)
            print_json(task) if args.json else print_tasks([task])
            return 0

        if args.command == "list":
            source_principal = get_principal(args.source_principal, db_path=db_path) if args.source_principal else None
            target_principal = get_principal(args.target_principal, db_path=db_path) if args.target_principal else None
            harness = (
                get_harness_profile(args.harness, db_path=db_path, workspace_id=current_workspace(db_path=db_path)["workspace_id"])
                if args.harness
                else None
            )
            tasks = list_tasks(
                db_path=db_path,
                status=args.status,
                task_kind=args.task_kind,
                controller_status=args.controller_status,
                source_principal_id=source_principal["principal_id"] if source_principal else None,
                target_principal_id=target_principal["principal_id"] if target_principal else None,
                harness_id=harness["harness_id"] if harness else None,
                parent_task_id=args.parent,
                limit=args.limit,
            )
            print_json(tasks) if args.json else print_tasks(tasks)
            return 0

        if args.command == "due":
            tasks = list_tasks(db_path=db_path, due=True, limit=args.limit)
            print_json(tasks) if args.json else print_tasks(tasks)
            return 0

        if args.command == "status":
            workspace = current_workspace(db_path=db_path)
            status = {
                "workspace": workspace,
                "principals": list_principals(db_path=db_path),
                "agent_runtimes": list_agent_runtimes(db_path=db_path, workspace_id=workspace["workspace_id"]),
                "harness_profiles": list_harness_profiles(db_path=db_path, workspace_id=workspace["workspace_id"]),
                "task_summary": task_registry_summary(db_path=db_path),
            }
            print_json(status) if args.json else print_hub_status(status)
            return 0

        if args.command == "bind-missing":
            workspace = current_workspace(db_path=db_path)
            try:
                source = get_principal(args.source, db_path=db_path, principal_type=args.source_type)
            except KeyError:
                source = ensure_principal(
                    principal_type=args.source_type,
                    display_name=args.source,
                    trust_level="owner" if args.source_type == "human" else "trusted",
                    db_path=db_path,
                )
            result = backfill_missing_task_bindings(
                source_principal_id=source["principal_id"],
                source_workspace_id=workspace["workspace_id"],
                source_workspace_slug=workspace["workspace_slug"],
                source_workspace=workspace["workspace_slug"],
                db_path=db_path,
                limit=args.limit,
                dry_run=not args.yes,
                actor="manual",
            )
            if args.json:
                print_json({"source": source, **result})
            else:
                mode = "Would bind" if result["dry_run"] else "Bound"
                print(f"{mode} {result['count']} task(s) to {source['display_name']} ({source['principal_id']})")
                if result["dry_run"] and result["count"]:
                    print(f"Run `{CLI_PROG} bind-missing --source {source['display_name']} --yes` to apply.")
                for task_id in result["task_ids"]:
                    print(f"  {task_id}")
            return 0

        if args.command == "tree":
            tasks = get_task_tree(args.task_id, db_path=db_path, limit=args.limit)
            print_json(tasks) if args.json else print_task_tree(tasks)
            return 0

        if args.command == "show":
            print_json(get_task(args.task_id, db_path=db_path))
            return 0

        if args.command == "context":
            print_json(get_context_pack(args.task_id, db_path=db_path))
            return 0

        if args.command == "update":
            updates = {
                key: value
                for key, value in {
                    "title": args.title,
                    "summary": args.summary,
                    "next_action": args.next_action,
                    "priority": args.priority,
                    "rank": args.rank,
                    "due_at": args.due,
                    "status": args.status,
                    "tags": args.tag,
                    "depends_on": args.depends_on,
                }.items()
                if value is not None
            }
            print_json(update_task(args.task_id, updates, db_path=db_path))
            return 0

        if args.command == "ack":
            print_json(ack_task(args.task_id, db_path=db_path))
            return 0

        if args.command in {"approve", "reject", "request-changes"}:
            approver = _resolve_or_ensure_principal(args.by, db_path, "human", "owner", ensure_missing=True)
            decision = {
                "approve": "approved",
                "reject": "rejected",
                "request-changes": "changes_requested",
            }[args.command]
            print_json(
                record_approval_decision(
                    args.task_id,
                    decision,
                    approver["principal_id"],
                    reason=args.reason,
                    actor=args.by,
                    db_path=db_path,
                )
            )
            return 0

        if args.command == "stop":
            print_json(request_task_stop(args.task_id, reason=args.reason, actor=args.by, db_path=db_path))
            return 0

        if args.command == "done":
            print_json(complete_task(args.task_id, db_path=db_path, actor=args.owner))
            return 0

        if args.command == "snooze":
            print_json(snooze_task(args.task_id, until=args.until, duration=args.duration, db_path=db_path))
            return 0

        if args.command == "claim-next":
            try:
                owner_principal = get_principal(args.owner, db_path=db_path, principal_type="agent")
                owner = owner_principal["principal_id"]
                target_principal_id = owner_principal["principal_id"]
            except Exception:
                owner = args.owner
                target_principal_id = None
            claimed = claim_next_task(
                owner=owner,
                lease_seconds=args.lease_seconds,
                workspace=args.workspace,
                include_not_due=args.include_not_due,
                target_principal_id=target_principal_id,
                include_unassigned=args.include_unassigned,
                db_path=db_path,
            )
            print_json(claimed or {"claimed": None})
            return 0

        if args.command == "release":
            print_json(release_task(args.task_id, owner=args.owner, next_status=args.next_status, db_path=db_path))
            return 0

        if args.command == "heartbeat":
            print_json(
                heartbeat_claim(
                    args.task_id,
                    owner=args.owner,
                    lease_seconds=args.lease_seconds,
                    db_path=db_path,
                )
            )
            return 0

        if args.command == "progress":
            print_json(append_progress(args.task_id, args.message, owner=args.owner, db_path=db_path))
            return 0

        if args.command == "review-gate":
            if args.review_gate_command == "request":
                reviewer_principal_id = ""
                if args.reviewer:
                    reviewer = _resolve_or_ensure_principal(args.reviewer, db_path, "human", "owner", ensure_missing=True)
                    reviewer_principal_id = reviewer["principal_id"] if reviewer else ""
                result = request_review_gate(
                    args.task_id,
                    reason=args.reason,
                    actor=args.by,
                    gate_type=args.gate_type,
                    reviewer_principal_id=reviewer_principal_id,
                    db_path=db_path,
                )
                if args.json:
                    print_json(result)
                else:
                    print_tasks([result["review_gate"]])
                return 0
            if args.review_gate_command == "decide":
                approver = _resolve_or_ensure_principal(args.by, db_path, "human", "owner", ensure_missing=True)
                result = decide_review_gate(
                    args.gate_task_id,
                    args.decision,
                    approver_principal_id=approver["principal_id"],
                    reason=args.reason,
                    actor=args.by,
                    db_path=db_path,
                )
                print_json(result) if args.json else print_tasks([result["subject_task"], result["review_gate"]])
                return 0

        if args.command == "runner":
            runner_kwargs = {
                "agent_name": args.name,
                "backend": args.backend,
                "backend_command": args.backend_command,
                "timeout_seconds": args.timeout_seconds,
                "script_allowlist_path": args.script_allowlist or None,
                "capabilities": args.capability or None,
                "harness": args.harness,
                "include_not_due": not args.due_only,
                "run_orchestrator": args.run_orchestrator,
                "db_path": db_path,
            }
            if args.runner_command == "once":
                result = run_runner_once(task_id=args.task_id, **runner_kwargs)
            else:
                result = run_runner_watch(
                    interval_seconds=args.interval_seconds,
                    iterations=args.iterations,
                    **runner_kwargs,
                )
            if args.json:
                print_json(result)
            else:
                items = result if isinstance(result, list) else [result]
                for item in items:
                    task = item.get("task") or {}
                    print(f"{item.get('result') or item.get('reason')} {task.get('task_id', '')} {task.get('title', '')}".strip())
            return 0

        if args.command == "import-md":
            print_json(import_markdown(Path(args.path), db_path=db_path))
            return 0

        if args.command == "import-json":
            print_json(import_json(Path(args.path), db_path=db_path))
            return 0

        if args.command == "export-json":
            tasks = list_tasks(db_path=db_path, status=args.status, limit=10000)
            export_json(tasks, Path(args.path))
            print(f"Exported {len(tasks)} tasks to {args.path}")
            return 0

        if args.command == "export-md":
            task = get_task(args.task_id, db_path=db_path)
            exported_hash = export_markdown(task, Path(args.path))
            update_task(
                args.task_id,
                {
                    "source_file_path": str(Path(args.path)),
                    "last_exported_hash": exported_hash,
                    "last_exported_at": iso_now(),
                    "conflict_status": "",
                },
                db_path=db_path,
                actor="export",
            )
            print(f"Exported {args.task_id} to {args.path}")
            return 0

        if args.command == "backup":
            print_json(backup_database(db_path=db_path, target=args.path))
            return 0

        if args.command == "restore":
            print_json(restore_database(args.path, db_path=db_path, yes=args.yes))
            return 0

        if args.command == "enqueue-due":
            jobs = enqueue_due_notifications(db_path=db_path, channel=args.channel)
            print_json(jobs)
            return 0

        if args.command == "dispatch-once":
            print_json(
                run_worker_once(
                    db_path=db_path,
                    channel=args.channel,
                    worker_id=args.worker_id,
                    dispatch_limit=args.dispatch_limit,
                )
            )
            return 0

        if args.command == "notify-test":
            if args.capabilities:
                print_json(notification_capabilities())
                return 0
            response = dispatch_notification(build_synthetic_notification_job(args))
            print_json(response)
            return 0

        if args.command == "worker":
            if args.worker_command == "pause":
                print_json(pause_worker(db_path=db_path, reason=args.reason))
                return 0
            if args.worker_command == "resume":
                print_json(resume_worker(db_path=db_path))
                return 0
            if args.worker_command == "status":
                print_json(worker_status(db_path=db_path))
                return 0

        if args.command == "attempts":
            print_json(list_notification_attempts(db_path=db_path, job_id=args.job_id, limit=args.limit))
            return 0

        if args.command == "push":
            result = push_to_global(
                local_db_path=args.db,
                global_db_path=args.global_db,
                registered_by_principal_type=args.registered_by_type,
                registered_by_display_name=args.registered_by,
                authority_basis=args.authority_basis,
                authority_level=args.authority_level,
                approval_status=args.approval_status,
                snapshot_profile=args.profile,
                limit=args.limit,
                include_archived=args.include_archived,
            )
            if args.json:
                print_json(result)
            else:
                print(
                    f"Pushed {result['count']} task(s): "
                    f"{result['created']} created, {result['updated']} updated"
                )
                print(f"Local:  {result['local_db']}")
                print(f"Global: {result['global_db']}")
                print(f"Profile: {result['snapshot_profile']}")
            return 0

        if args.command == "pull":
            result = pull_from_global(
                local_db_path=args.db,
                global_db_path=args.global_db,
                registered_by_principal_type=args.registered_by_type,
                registered_by_display_name=args.registered_by,
                limit=args.limit,
            )
            if args.json:
                print_json(result)
            else:
                print(f"Pulled {result['count']} approved task(s), skipped {len(result['skipped'])}")
                print(f"Local:  {result['local_db']}")
                print(f"Global: {result['global_db']}")
            return 0

        if args.command == "orchestrator":
            if args.orchestrator_command == "run-once":
                result = run_orchestrator_once(
                    orchestrator_name=args.name,
                    db_path=db_path,
                    include_not_due=not args.due_only,
                    limit=args.limit,
                    default_harness=args.default_harness,
                    dry_run=args.dry_run,
                )
                if args.json:
                    print_json(result)
                else:
                    mode = "Would assign" if result["dry_run"] else "Assigned"
                    print(
                        f"{mode} {result['assigned_count']} task(s) "
                        f"from {result['candidate_count']} candidate(s); "
                        f"active_agents={result['active_agent_count']}"
                    )
                    for assignment in result["assignments"]:
                        print(
                            f"  {assignment['task_id']} -> {assignment['agent_name']} "
                            f"({assignment['target_principal_id']})"
                        )
                    for skipped in result["skipped"]:
                        print(f"  skipped {skipped['task_id']}: {skipped['reason']}")
                return 0

        if args.command == "fetch-origin":
            result = fetch_origin_task(
                args.hub_task_id,
                global_db_path=args.global_db or db_path,
                source_db_path=args.source_db,
            )
            if args.json:
                print_json(result)
            else:
                origin = result["origin_task"]
                workspace = result["source_workspace"]
                print(f"Hub task:    {result['hub_task_id']}")
                print(f"Origin task: {result['origin_task_id']}")
                print(f"Workspace:   {workspace['workspace_slug']} ({result['source_workspace_id']})")
                print(f"Source DB:   {result['source_db']}")
                print_tasks([origin])
            return 0

        if args.command == "serve":
            ensure_db(db_path)
            run_server(host=args.host, port=args.port, db_path=db_path)
            return 0

    except Exception as exc:
        print(f"{CLI_PROG}: error: {exc}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
