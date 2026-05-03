from __future__ import annotations

from pathlib import Path
from typing import Any
import base64
import json
import os
import subprocess

from .branding import APP_NAME
from .config import default_app_dir
from .timeutil import iso_now


class NotificationDispatchError(RuntimeError):
    pass


DEFAULT_NOTIFICATION_CHANNEL = "toast-fallback"
TOAST_FALLBACK_CHANNELS = {"toast-fallback", "toast-local", "toast_or_local"}


def _local_log_path() -> Path:
    path = default_app_dir() / "local-notifications.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _task_detail_url(task_id: str | None) -> str:
    base = os.environ.get("TASK_MEMORY_HUB_API_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
    return f"{base}/tasks/{task_id}" if task_id else base


def _powershell_available(command_name: str) -> bool:
    script = f"if (Get-Command {command_name} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}"
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
    )
    return completed.returncode == 0


def toast_available() -> bool:
    return _powershell_available("New-BurntToastNotification")


def notification_capabilities() -> dict[str, Any]:
    return {
        "channels": ["local", "toast", DEFAULT_NOTIFICATION_CHANNEL],
        "aliases": sorted(TOAST_FALLBACK_CHANNELS - {DEFAULT_NOTIFICATION_CHANNEL}),
        "recommended_channel": DEFAULT_NOTIFICATION_CHANNEL,
        "toast_available": toast_available(),
        "toast_dependency": "PowerShell BurntToast module",
        "install_hint": "Install-Module BurntToast -Scope CurrentUser",
        "local_log_path": str(_local_log_path()),
    }


def dispatch_local(job: dict[str, Any]) -> dict[str, Any]:
    task = job.get("task") or {}
    task_id = job.get("task_id") or task.get("task_id")
    record = {
        "created_at": iso_now(),
        "job_id": job.get("job_id"),
        "task_id": task_id,
        "channel": job.get("channel"),
        "title": task.get("title") or (job.get("payload") or {}).get("title"),
        "summary": task.get("summary") or (job.get("payload") or {}).get("summary"),
        "next_action": task.get("next_action") or (job.get("payload") or {}).get("next_action"),
        "due_at": task.get("due_at"),
        "snooze_until": task.get("snooze_until"),
        "task_url": _task_detail_url(task_id),
    }
    with _local_log_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"adapter": "local", "path": str(_local_log_path()), "record": record}


def dispatch_toast(job: dict[str, Any]) -> dict[str, Any]:
    task = job.get("task") or {}
    task_id = job.get("task_id") or task.get("task_id")
    title = task.get("title") or (job.get("payload") or {}).get("title") or "Task reminder"
    next_action = task.get("next_action") or (job.get("payload") or {}).get("next_action") or ""
    due = task.get("snooze_until") or task.get("due_at") or ""
    line2 = next_action or task.get("summary") or f"Open {APP_NAME} for details."
    line3 = f"Due: {due}" if due else f"Task: {task_id}" if task_id else ""
    toast_payload = {"title": title, "line2": line2, "line3": line3}
    encoded_payload = base64.b64encode(json.dumps(toast_payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop'; "
        "if (-not (Get-Command New-BurntToastNotification -ErrorAction SilentlyContinue)) { "
        "throw 'BurntToast PowerShell module is not installed'; "
        "} "
        f"$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_payload}')) | ConvertFrom-Json; "
        "$lines = @([string]$payload.title, [string]$payload.line2); "
        "if ($payload.line3) { $lines += [string]$payload.line3; } "
        "New-BurntToastNotification -Text $lines"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=15,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Toast command failed").strip()
        raise NotificationDispatchError(message)
    return {"adapter": "toast", "stdout": completed.stdout.strip(), "task_url": _task_detail_url(task_id)}


def dispatch_notification(job: dict[str, Any]) -> dict[str, Any]:
    channel = job.get("channel") or DEFAULT_NOTIFICATION_CHANNEL
    if channel == "local":
        return dispatch_local(job)
    if channel == "toast":
        return dispatch_toast(job)
    if channel in TOAST_FALLBACK_CHANNELS:
        try:
            return dispatch_toast(job)
        except NotificationDispatchError as exc:
            local_response = dispatch_local(job)
            return {
                "adapter": "toast-fallback",
                "toast_error": str(exc),
                "fallback": local_response,
            }
    raise NotificationDispatchError(f"Unsupported notification channel: {channel}")
