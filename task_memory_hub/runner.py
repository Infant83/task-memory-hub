from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import shlex
import subprocess
import sys
import time

from .branding import CLI_PROG
from .governance import evaluate_runner_policy, evaluate_script_ref_policy, task_command_ref
from .orchestrator import run_orchestrator_once
from .registry import current_workspace, get_harness_profile, heartbeat_agent_runtime, register_agent_runtime
from .service import (
    append_progress,
    append_task_event,
    block_task,
    claim_next_task,
    claim_task,
    complete_task,
    ensure_db,
    fail_task,
    get_context_pack,
    get_task,
    observe_task_stop,
    request_review_gate,
)


DEFAULT_RUNNER_CAPABILITIES = [
    "tmh-cli",
    "tmh-api",
    "repo-edit",
    "dry-run",
]


def _backend_capability(backend: str) -> str:
    return backend.replace("_", "-")


def _load_script_allowlist(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        import os

        env_path = os.environ.get("TASK_MEMORY_HUB_SCRIPT_ALLOWLIST")
        if env_path:
            candidates.append(Path(env_path))
        candidates.append(Path.cwd() / ".tmh" / "script-backends.json")

    for candidate in candidates:
        if not candidate.exists():
            continue
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        entries = raw.get("commands", raw) if isinstance(raw, dict) else {}
        if not isinstance(entries, dict):
            raise ValueError("script allowlist must be a JSON object or contain a commands object")
        allowlist: dict[str, dict[str, Any]] = {}
        for name, entry in entries.items():
            if isinstance(entry, str):
                entry = {"command": entry}
            if not isinstance(entry, dict):
                raise ValueError(f"script allowlist entry must be object or string: {name}")
            command = entry.get("command") or entry.get("args")
            if not command:
                raise ValueError(f"script allowlist entry requires command or args: {name}")
            allowlist[str(name)] = dict(entry)
        return allowlist
    return {}


def _command_args_from_allowlist_entry(entry: dict[str, Any]) -> list[str]:
    args = entry.get("args")
    if args is not None:
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError("script allowlist args must be a list of strings")
        return args
    command = str(entry.get("command") or "").strip()
    if not command:
        raise ValueError("script allowlist entry requires command")
    return shlex.split(command, posix=False)


def _run_deepagents_cli(command: str, prompt: str, timeout_seconds: int) -> dict[str, Any]:
    if not command.strip():
        raise ValueError("deepagents_cli backend requires backend_command")
    args = shlex.split(command, posix=False)
    if "--prompt" not in args:
        args.extend(["--prompt", prompt])
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    return {
        "returncode": completed.returncode,
        "stdout_preview": stdout[:4000],
        "stderr_preview": stderr[:4000],
        "succeeded": completed.returncode == 0,
    }


def _run_script_ref(
    command_ref: str,
    entry: dict[str, Any],
    task: dict[str, Any],
    context_pack: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    import os

    args = _command_args_from_allowlist_entry(entry)
    env = os.environ.copy()
    env["TASK_MEMORY_HUB_TASK_ID"] = task["task_id"]
    env["TASK_MEMORY_HUB_TASK_JSON"] = json.dumps(task, ensure_ascii=False, sort_keys=True)
    env["TASK_MEMORY_HUB_CONTEXT_PACK_JSON"] = json.dumps(context_pack, ensure_ascii=False, sort_keys=True)
    env["TASK_MEMORY_HUB_COMMAND_REF"] = command_ref
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        env=env,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    return {
        "returncode": completed.returncode,
        "stdout_preview": stdout[:4000],
        "stderr_preview": stderr[:4000],
        "succeeded": completed.returncode == 0,
        "summary": stdout[:1000] or f"script_ref backend completed: {command_ref}",
        "artifacts": [
            {
                "artifact_type": "script_stdout",
                "artifact_ref": f"script-ref:{command_ref}",
                "description": stdout[:4000],
            }
        ],
    }


def _execute_backend(
    backend: str,
    task: dict[str, Any],
    context_pack: dict[str, Any],
    backend_command: str = "",
    timeout_seconds: int = 120,
    script_allowlist: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if backend == "dry_run":
        return {
            "succeeded": True,
            "summary": "드라이런 backend가 외부 부작용 없이 실행 경로를 검증했다.",
            "artifacts": [
                {
                    "artifact_type": "runner_report",
                    "artifact_ref": f"tmh://tasks/{task['task_id']}/events",
                    "description": "runner event trail",
                }
            ],
        }
    if backend == "deepagents_cli":
        prompt = "\n".join(
            [
                f"TMH task_id: {task['task_id']}",
                f"Title: {task['title']}",
                f"Summary: {task.get('summary') or ''}",
                f"Next action: {task.get('next_action') or ''}",
                "Return a concise progress/completion report for TMH.",
            ]
        )
        result = _run_deepagents_cli(backend_command, prompt, timeout_seconds)
        result["summary"] = result.get("stdout_preview") or "deepagents_cli backend completed without stdout."
        result["artifacts"] = [
            {
                "artifact_type": "backend_stdout",
                "artifact_ref": "stdout",
                "description": result.get("stdout_preview", ""),
            }
        ]
        return result
    if backend == "script_ref":
        command_ref = task_command_ref(task)
        allowlist = script_allowlist or {}
        entry = allowlist.get(command_ref)
        if not entry:
            raise ValueError(f"script command ref is not allowlisted: {command_ref}")
        return _run_script_ref(command_ref, entry, task, context_pack, timeout_seconds)
    raise ValueError(f"Unsupported runner backend: {backend}")


def run_runner_once(
    agent_name: str = "tmh-runner",
    backend: str = "dry_run",
    capabilities: list[str] | None = None,
    harness: str = "",
    task_id: str = "",
    include_not_due: bool = True,
    run_orchestrator: bool = False,
    orchestrator_name: str = "tmh-orchestrator",
    lease_seconds: int = 600,
    claim_lease_seconds: int = 1800,
    backend_command: str = "",
    timeout_seconds: int = 120,
    script_allowlist_path: str | Path | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = ensure_db(db_path)
    workspace = current_workspace(db_path=path)
    runner_capabilities = sorted(set((capabilities or DEFAULT_RUNNER_CAPABILITIES) + [_backend_capability(backend)]))
    harness_id = ""
    if harness:
        harness_id = get_harness_profile(harness, db_path=path, workspace_id=workspace["workspace_id"])["harness_id"]
    runtime = register_agent_runtime(
        agent_name=agent_name,
        workspace_id=workspace["workspace_id"],
        role="runner",
        status="active",
        capabilities=runner_capabilities,
        default_harness_id=harness_id,
        max_active_tasks=1,
        lease_seconds=lease_seconds,
        notes=f"backend={backend}",
        db_path=path,
    )
    principal_id = runtime["principal_id"]

    orchestrator_result = None
    if run_orchestrator:
        orchestrator_result = run_orchestrator_once(
            orchestrator_name=orchestrator_name,
            db_path=path,
            include_not_due=include_not_due,
            limit=10,
            default_harness=harness,
        )

    if task_id:
        requested_task = get_task(task_id, db_path=path)
        if requested_task.get("controller_status") == "paused" or requested_task.get("agent_claim_status") == "stop_requested":
            stopped_task = observe_task_stop(
                task_id,
                reason="controller requested stop before runner claim",
                actor=agent_name,
                db_path=path,
            )
            idle_runtime = heartbeat_agent_runtime(
                principal_id=principal_id,
                workspace_id=workspace["workspace_id"],
                status="idle",
                current_task_id="",
                lease_seconds=lease_seconds,
                db_path=path,
            )
            return {
                "processed": True,
                "result": "stopped",
                "task": stopped_task,
                "runtime": idle_runtime,
                "orchestrator": orchestrator_result,
            }
        task = claim_task(
            task_id,
            owner=principal_id,
            lease_seconds=claim_lease_seconds,
            db_path=path,
            target_principal_id=principal_id,
        )
    else:
        task = claim_next_task(
            owner=principal_id,
            lease_seconds=claim_lease_seconds,
            include_not_due=include_not_due,
            target_principal_id=principal_id,
            db_path=path,
        )
    if not task:
        idle_runtime = heartbeat_agent_runtime(
            principal_id=principal_id,
            workspace_id=workspace["workspace_id"],
            status="idle",
            current_task_id="",
            lease_seconds=lease_seconds,
            db_path=path,
        )
        return {
            "processed": False,
            "reason": "no assigned claimable task",
            "runtime": idle_runtime,
            "orchestrator": orchestrator_result,
        }

    heartbeat_agent_runtime(
        principal_id=principal_id,
        workspace_id=workspace["workspace_id"],
        status="busy",
        current_task_id=task["task_id"],
        lease_seconds=lease_seconds,
        db_path=path,
    )
    append_task_event(
        task["task_id"],
        "runner_started",
        agent_name,
        {
            "principal_id": principal_id,
            "backend": backend,
            "capabilities": runner_capabilities,
        },
        db_path=path,
    )
    decision = evaluate_runner_policy(task, runner_capabilities, backend=backend)
    script_allowlist: dict[str, dict[str, Any]] = {}
    if backend == "script_ref":
        script_allowlist = _load_script_allowlist(script_allowlist_path)
        script_decision = evaluate_script_ref_policy(task, set(script_allowlist))
        decision["script_ref_policy"] = script_decision
        if not script_decision["allowed"]:
            decision["allowed"] = False
            decision["decision"] = "block"
            decision["reasons"].extend(script_decision["reasons"])
    review_gate_result = None
    if decision.get("approval_required") and not decision.get("approved"):
        review_gate_result = request_review_gate(
            task["task_id"],
            reason="; ".join(decision.get("reasons") or ["human approval required"]),
            actor=agent_name,
            gate_type="pre_execution",
            db_path=path,
        )
        decision["review_gate_task_id"] = review_gate_result["review_gate"]["task_id"]
    append_task_event(task["task_id"], "policy_decision", agent_name, decision, db_path=path)
    if not decision["allowed"]:
        blocked_task = block_task(
            task["task_id"],
            reason="; ".join(decision["reasons"]),
            actor=agent_name,
            db_path=path,
            payload=decision,
        )
        idle_runtime = heartbeat_agent_runtime(
            principal_id=principal_id,
            workspace_id=workspace["workspace_id"],
            status="idle",
            current_task_id="",
            lease_seconds=lease_seconds,
            db_path=path,
        )
        return {
            "processed": True,
            "result": "blocked",
            "task": blocked_task,
            "policy": decision,
            "review_gate": review_gate_result,
            "runtime": idle_runtime,
            "orchestrator": orchestrator_result,
        }

    append_task_event(
        task["task_id"],
        "backend_resolved",
        agent_name,
        {"backend": backend, "backend_capability": decision["backend_capability"]},
        db_path=path,
    )
    append_task_event(task["task_id"], "backend_started", agent_name, {"backend": backend}, db_path=path)
    context_pack = get_context_pack(task["task_id"], db_path=path)
    append_progress(task["task_id"], f"{backend} backend 실행을 시작했다.", owner=agent_name, db_path=path)
    try:
        backend_result = _execute_backend(
            backend,
            task,
            context_pack,
            backend_command=backend_command,
            timeout_seconds=timeout_seconds,
            script_allowlist=script_allowlist,
        )
    except Exception as exc:
        failed_task = fail_task(task["task_id"], reason=str(exc), actor=agent_name, db_path=path)
        idle_runtime = heartbeat_agent_runtime(
            principal_id=principal_id,
            workspace_id=workspace["workspace_id"],
            status="idle",
            current_task_id="",
            lease_seconds=lease_seconds,
            db_path=path,
        )
        return {
            "processed": True,
            "result": "failed",
            "task": failed_task,
            "error": str(exc),
            "runtime": idle_runtime,
            "orchestrator": orchestrator_result,
        }

    if not backend_result.get("succeeded", True):
        failed_task = fail_task(
            task["task_id"],
            reason=backend_result.get("stderr_preview") or "backend returned non-zero",
            actor=agent_name,
            db_path=path,
            payload=backend_result,
        )
        idle_runtime = heartbeat_agent_runtime(
            principal_id=principal_id,
            workspace_id=workspace["workspace_id"],
            status="idle",
            current_task_id="",
            lease_seconds=lease_seconds,
            db_path=path,
        )
        return {
            "processed": True,
            "result": "failed",
            "task": failed_task,
            "backend_result": backend_result,
            "runtime": idle_runtime,
            "orchestrator": orchestrator_result,
        }

    append_task_event(
        task["task_id"],
        "reasoning_summary",
        agent_name,
        {
            "summary": backend_result.get("summary")
            or "backend execution completed; raw chain-of-thought was not stored.",
            "store_raw_cot": False,
        },
        db_path=path,
    )
    for artifact in backend_result.get("artifacts") or []:
        append_task_event(task["task_id"], "artifact_reported", agent_name, artifact, db_path=path)
    append_progress(task["task_id"], f"{backend} backend 실행을 완료했다.", owner=agent_name, db_path=path)
    completed = complete_task(task["task_id"], db_path=path, actor=agent_name)
    idle_runtime = heartbeat_agent_runtime(
        principal_id=principal_id,
        workspace_id=workspace["workspace_id"],
        status="idle",
        current_task_id="",
        lease_seconds=lease_seconds,
        db_path=path,
    )
    return {
        "processed": True,
        "result": "completed",
        "task": completed,
        "backend_result": backend_result,
        "policy": decision,
        "runtime": idle_runtime,
        "orchestrator": orchestrator_result,
    }


def run_runner_watch(
    interval_seconds: int = 15,
    iterations: int = 0,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    count = 0
    while True:
        result = run_runner_once(**kwargs)
        results.append(result)
        count += 1
        if iterations and count >= iterations:
            return results
        time.sleep(interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the TMH harness runner")
    parser.add_argument("--db")
    parser.add_argument("--name", default="tmh-runner")
    parser.add_argument("--backend", default="dry_run", choices=["dry_run", "deepagents_cli", "script_ref"])
    parser.add_argument("--backend-command", default="")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--script-allowlist", default="")
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--harness", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--due-only", action="store_true")
    parser.add_argument("--run-orchestrator", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=15)
    parser.add_argument("--iterations", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kwargs = {
        "agent_name": args.name,
        "backend": args.backend,
        "backend_command": args.backend_command,
        "timeout_seconds": args.timeout_seconds,
        "script_allowlist_path": args.script_allowlist or None,
        "capabilities": args.capability or None,
        "harness": args.harness,
        "task_id": args.task_id,
        "include_not_due": not args.due_only,
        "run_orchestrator": args.run_orchestrator,
        "db_path": args.db,
    }
    try:
        result = (
            run_runner_watch(
                interval_seconds=args.interval_seconds,
                iterations=args.iterations,
                **kwargs,
            )
            if args.watch
            else run_runner_once(**kwargs)
        )
    except Exception as exc:
        print(f"{CLI_PROG}-runner: error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        items = result if isinstance(result, list) else [result]
        for item in items:
            task = item.get("task") or {}
            print(f"{item.get('result') or item.get('reason')} {task.get('task_id', '')} {task.get('title', '')}".strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
