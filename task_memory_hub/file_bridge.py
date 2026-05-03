from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from .service import create_task, get_task, source_content_hash, update_task
from .timeutil import iso_now


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value == "":
        return ""
    if value.lower() in {"null", "none"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("'\"") for part in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        return value.strip("'\"")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta_raw, body = match.groups()
    meta: dict[str, Any] = {}
    for line in meta_raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = _parse_scalar(value)
    return meta, body.strip()


def parse_markdown_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = "detail"
    sections[current] = []
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections.setdefault(current, [])
            continue
        if line.startswith("# "):
            continue
        sections.setdefault(current, []).append(line)

    def clean(name: str) -> str:
        return "\n".join(sections.get(name, [])).strip()

    return {
        "summary": clean("summary"),
        "next_action": clean("next action"),
        "detail_md": clean("detail") or body.strip(),
    }


def task_to_markdown(task: dict[str, Any]) -> str:
    tags = task.get("tags") or []
    frontmatter = {
        "task_id": task.get("task_id"),
        "task_kind": task.get("task_kind"),
        "execution_mode": task.get("execution_mode"),
        "schedule_kind": task.get("schedule_kind"),
        "controller_status": task.get("controller_status"),
        "automation_id": task.get("automation_id"),
        "parent_task_id": task.get("parent_task_id"),
        "status": task.get("status"),
        "priority": task.get("priority"),
        "rank": task.get("rank"),
        "due_at": task.get("due_at"),
        "snooze_until": task.get("snooze_until"),
        "source_workspace": task.get("source_workspace"),
        "target_workspace_id": task.get("target_workspace_id"),
        "target_principal_id": task.get("target_principal_id"),
        "harness_id": task.get("harness_id"),
        "depends_on": f"[{', '.join(task.get('depends_on') or [])}]" if task.get("depends_on") else "[]",
        "tags": f"[{', '.join(tags)}]" if tags else "[]",
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        if value is None or value == "":
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {task['title']}")
    lines.append("")
    if task.get("summary"):
        lines.append("## Summary")
        lines.append(task["summary"])
        lines.append("")
    if task.get("next_action"):
        lines.append("## Next Action")
        lines.append(task["next_action"])
        lines.append("")
    if task.get("detail_md"):
        lines.append("## Detail")
        lines.append(task["detail_md"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def markdown_to_payload(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    title = meta.get("title")
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
    payload = dict(meta)
    sections = parse_markdown_sections(body)
    payload.setdefault("title", title or path.stem)
    payload.setdefault("summary", sections["summary"])
    payload.setdefault("next_action", sections["next_action"])
    payload.setdefault("detail_md", sections["detail_md"])
    payload["source_file_path"] = str(path)
    payload["source_content_hash"] = source_content_hash(text)
    payload["last_imported_hash"] = payload["source_content_hash"]
    payload["last_imported_at"] = iso_now()
    return payload


def _sync_marker(task: dict[str, Any]) -> str:
    return max(task.get("last_imported_at") or "", task.get("last_exported_at") or "")


def _has_import_conflict(existing: dict[str, Any], incoming_hash: str) -> bool:
    previous_hash = existing.get("source_content_hash") or existing.get("last_imported_hash") or existing.get("last_exported_hash")
    marker = _sync_marker(existing)
    if not previous_hash or not marker:
        return False
    return (existing.get("updated_at") or "") > marker


def _mark_import_conflict(task_id: str, incoming_hash: str, source_file_path: str, db_path: Path | str | None = None) -> dict[str, Any]:
    task = update_task(
        task_id,
        {"conflict_status": "file_changed_after_local_update"},
        db_path=db_path,
        actor="import",
    )
    task["import_conflict"] = True
    task["incoming_source_content_hash"] = incoming_hash
    task["incoming_source_file_path"] = source_file_path
    return task


def import_markdown(path: Path, db_path: Path | str | None = None) -> dict[str, Any]:
    payload = markdown_to_payload(path)
    task_id = payload.get("task_id")
    if task_id:
        try:
            existing = get_task(str(task_id), db_path=db_path)
            if _has_import_conflict(existing, payload["source_content_hash"]):
                return _mark_import_conflict(
                    str(task_id),
                    payload["source_content_hash"],
                    str(path),
                    db_path=db_path,
                )
            return update_task(str(task_id), payload, db_path=db_path, actor="manual")
        except KeyError:
            pass
    return create_task(payload, db_path=db_path, actor="manual")


def import_json(path: Path, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    items = raw if isinstance(raw, list) else [raw]
    results = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("JSON import must contain an object or a list of objects")
        item = dict(item)
        item["source_file_path"] = str(path)
        item["source_content_hash"] = source_content_hash(text)
        item["last_imported_hash"] = item["source_content_hash"]
        item["last_imported_at"] = iso_now()
        task_id = item.get("task_id")
        if task_id:
            try:
                existing = get_task(str(task_id), db_path=db_path)
                if _has_import_conflict(existing, item["source_content_hash"]):
                    results.append(
                        _mark_import_conflict(
                            str(task_id),
                            item["source_content_hash"],
                            str(path),
                            db_path=db_path,
                        )
                    )
                    continue
                results.append(update_task(str(task_id), item, db_path=db_path, actor="manual"))
                continue
            except KeyError:
                pass
        results.append(create_task(item, db_path=db_path, actor="manual"))
    return results


def export_json(tasks: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


def export_markdown(task: dict[str, Any], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = task_to_markdown(task)
    path.write_text(text, encoding="utf-8")
    return source_content_hash(text)
