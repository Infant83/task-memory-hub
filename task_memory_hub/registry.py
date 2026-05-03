from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import re
import subprocess
import uuid

from .config import default_db_path, workspace_root
from .store import init_db, row_to_dict, rows_to_dicts, transaction
from .timeutil import iso_now, seconds_until_iso


def _db_path_or_default(db_path: Path | str | None = None) -> Path:
    return Path(db_path) if db_path else default_db_path()


def ensure_registry_db(db_path: Path | str | None = None) -> Path:
    path = _db_path_or_default(db_path)
    init_db(path)
    return path


def _slugify(value: str, fallback: str = "workspace") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-._")
    return slug or fallback


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def make_principal_id(principal_type: str, display_name: str) -> str:
    return _stable_id("pr", principal_type.strip().lower(), display_name.strip().lower())


def make_workspace_id(canonical_path: str, repo_remote: str = "") -> str:
    return _stable_id("ws", canonical_path.strip().lower())


def make_sync_event_id() -> str:
    return f"sync_{uuid.uuid4().hex}"


def make_policy_profile_id(profile_name: str) -> str:
    return _stable_id("pol", profile_name.strip().lower())


def make_auth_profile_id(workspace_id: str, profile_name: str) -> str:
    return _stable_id("auth", workspace_id.strip().lower(), profile_name.strip().lower())


def make_network_profile_id(workspace_id: str, profile_name: str) -> str:
    return _stable_id("net", workspace_id.strip().lower(), profile_name.strip().lower())


def make_harness_id(workspace_id: str, profile_name: str) -> str:
    return _stable_id("har", workspace_id.strip().lower(), profile_name.strip().lower())


def make_action_event_id() -> str:
    return f"act_{uuid.uuid4().hex}"


def _git_output(path: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=path,
            capture_output=True,
            check=False,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def detect_git_context(path: Path | None = None) -> dict[str, str]:
    base = (path or workspace_root()).resolve()
    return {
        "repo_remote": _git_output(base, ["config", "--get", "remote.origin.url"]),
        "repo_branch": _git_output(base, ["branch", "--show-current"]),
    }


def ensure_principal(
    principal_type: str = "human",
    display_name: str = "owner",
    contact_ref: str = "",
    auth_method: str = "manual",
    trust_level: str = "owner",
    active: bool = True,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not display_name.strip():
        raise ValueError("display_name is required")
    path = ensure_registry_db(db_path)
    principal_type = principal_type.strip().lower() or "human"
    display_name = display_name.strip()
    principal_id = make_principal_id(principal_type, display_name)
    timestamp = iso_now()
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO principals(
                principal_id, principal_type, display_name, contact_ref, auth_method,
                trust_level, active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(principal_id) DO UPDATE SET
                contact_ref = excluded.contact_ref,
                auth_method = excluded.auth_method,
                trust_level = excluded.trust_level,
                active = excluded.active,
                updated_at = excluded.updated_at
            """,
            (
                principal_id,
                principal_type,
                display_name,
                contact_ref.strip(),
                auth_method.strip() or "manual",
                trust_level.strip() or "member",
                1 if active else 0,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM principals WHERE principal_id = ?", (principal_id,)).fetchone()
        return row_to_dict(row)


def list_principals(db_path: Path | str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    path = ensure_registry_db(db_path)
    where = "WHERE active = 1" if active_only else ""
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM principals
            {where}
            ORDER BY principal_type ASC, display_name ASC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def get_principal(
    identifier: str,
    db_path: Path | str | None = None,
    principal_type: str | None = None,
) -> dict[str, Any]:
    if not identifier.strip():
        raise ValueError("principal identifier is required")
    path = ensure_registry_db(db_path)
    params: list[Any] = [identifier.strip(), identifier.strip()]
    type_clause = ""
    if principal_type:
        type_clause = "AND principal_type = ?"
        params.append(principal_type.strip().lower())
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM principals
            WHERE (principal_id = ? OR display_name = ?)
              {type_clause}
            ORDER BY principal_type ASC, display_name ASC
            """,
            params,
        ).fetchall()
        principals = rows_to_dicts(rows)
        if not principals:
            raise KeyError(f"Principal not found: {identifier}")
        if len(principals) > 1:
            raise ValueError(f"Principal reference is ambiguous, add --by-type or use principal_id: {identifier}")
        return principals[0]


def register_workspace(
    canonical_path: str | Path | None = None,
    workspace_slug: str | None = None,
    display_name: str | None = None,
    repo_remote: str | None = None,
    repo_branch: str | None = None,
    workspace_type: str = "project",
    registered_by_principal_type: str = "human",
    registered_by_display_name: str = "owner",
    registered_by_principal_id: str | None = None,
    proposed_by_principal_id: str | None = None,
    approved_by_principal_id: str | None = None,
    authority_basis: str = "owner_request",
    authority_level: str = "owner",
    approval_status: str = "approved",
    approval_note: str = "",
    detect_git: bool = True,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = ensure_registry_db(db_path)
    root = Path(canonical_path).expanduser().resolve() if canonical_path else workspace_root().resolve()
    git_context = detect_git_context(root) if detect_git else {"repo_remote": "", "repo_branch": ""}
    repo_remote = (repo_remote if repo_remote is not None else git_context["repo_remote"]).strip()
    repo_branch = (repo_branch if repo_branch is not None else git_context["repo_branch"]).strip()
    workspace_id = make_workspace_id(str(root), repo_remote)
    workspace_slug = _slugify(workspace_slug or root.name or workspace_id, fallback=workspace_id)
    display_name = (display_name or root.name or workspace_slug).strip()
    approval_status = approval_status.strip().lower() or "approved"
    registration_status = "active" if approval_status == "approved" else "proposed"
    timestamp = iso_now()

    if not registered_by_principal_id:
        principal = ensure_principal(
            principal_type=registered_by_principal_type,
            display_name=registered_by_display_name,
            db_path=path,
        )
        registered_by_principal_id = principal["principal_id"]
    proposed_by_principal_id = proposed_by_principal_id or registered_by_principal_id
    approved_by_principal_id = approved_by_principal_id or (registered_by_principal_id if approval_status == "approved" else "")

    with transaction(path) as conn:
        existing = conn.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
        if existing and existing["approval_status"] == "approved" and approval_status == "proposed":
            approval_status = "approved"
            registration_status = "active"
            approved_by_principal_id = existing["approved_by_principal_id"] or approved_by_principal_id
        conn.execute(
            """
            INSERT INTO workspaces(
                workspace_id, workspace_slug, display_name, canonical_path, repo_remote, repo_branch,
                workspace_type, registration_status, registered_by_principal_id,
                proposed_by_principal_id, approved_by_principal_id, authority_basis,
                authority_level, approval_status, approval_note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                workspace_slug = excluded.workspace_slug,
                display_name = excluded.display_name,
                canonical_path = excluded.canonical_path,
                repo_remote = excluded.repo_remote,
                repo_branch = excluded.repo_branch,
                workspace_type = excluded.workspace_type,
                registration_status = excluded.registration_status,
                registered_by_principal_id = excluded.registered_by_principal_id,
                proposed_by_principal_id = excluded.proposed_by_principal_id,
                approved_by_principal_id = excluded.approved_by_principal_id,
                authority_basis = excluded.authority_basis,
                authority_level = excluded.authority_level,
                approval_status = excluded.approval_status,
                approval_note = excluded.approval_note,
                updated_at = excluded.updated_at
            """,
            (
                workspace_id,
                workspace_slug,
                display_name,
                str(root),
                repo_remote,
                repo_branch,
                workspace_type.strip() or "project",
                registration_status,
                registered_by_principal_id,
                proposed_by_principal_id,
                approved_by_principal_id,
                authority_basis.strip() or "manual",
                authority_level.strip() or "member",
                approval_status,
                approval_note.strip(),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
        return row_to_dict(row)


def get_workspace(identifier: str, db_path: Path | str | None = None) -> dict[str, Any]:
    path = ensure_registry_db(db_path)
    with transaction(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM workspaces
            WHERE workspace_id = ? OR workspace_slug = ?
            """,
            (identifier, identifier),
        ).fetchone()
        result = row_to_dict(row)
        if not result:
            raise KeyError(f"Workspace not found: {identifier}")
        return result


def current_workspace(db_path: Path | str | None = None) -> dict[str, Any]:
    root = workspace_root().resolve()
    git_context = detect_git_context(root)
    workspace_id = make_workspace_id(str(root), git_context["repo_remote"])
    try:
        return get_workspace(workspace_id, db_path=db_path)
    except KeyError:
        return register_workspace(db_path=db_path)


def list_workspaces(db_path: Path | str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    path = ensure_registry_db(db_path)
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE registration_status = ?"
        params.append(status)
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM workspaces
            {where}
            ORDER BY workspace_slug ASC
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


def ensure_auth_profile(
    profile_name: str = "default",
    workspace_id: str = "",
    auth_type: str = "bearer",
    secret_ref: str = "",
    owner_principal_id: str = "",
    allowed_scopes: list[str] | None = None,
    enabled: bool = True,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not profile_name.strip():
        raise ValueError("profile_name is required")
    path = ensure_registry_db(db_path)
    profile_id = make_auth_profile_id(workspace_id, profile_name)
    timestamp = iso_now()
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO auth_profiles(
                auth_profile_id, workspace_id, profile_name, auth_type, secret_ref,
                owner_principal_id, allowed_scopes_json, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(auth_profile_id) DO UPDATE SET
                auth_type = excluded.auth_type,
                secret_ref = excluded.secret_ref,
                owner_principal_id = excluded.owner_principal_id,
                allowed_scopes_json = excluded.allowed_scopes_json,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                workspace_id.strip(),
                profile_name.strip(),
                auth_type.strip() or "bearer",
                secret_ref.strip(),
                owner_principal_id.strip(),
                json.dumps(allowed_scopes or [], ensure_ascii=False, sort_keys=True),
                1 if enabled else 0,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM auth_profiles WHERE auth_profile_id = ?", (profile_id,)).fetchone()
        return row_to_dict(row)


def list_auth_profiles(db_path: Path | str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
    path = ensure_registry_db(db_path)
    params: list[Any] = []
    where = ""
    if workspace_id:
        where = "WHERE workspace_id = ?"
        params.append(workspace_id)
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM auth_profiles
            {where}
            ORDER BY workspace_id ASC, profile_name ASC
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


def get_auth_profile(identifier: str, db_path: Path | str | None = None, workspace_id: str | None = None) -> dict[str, Any]:
    path = ensure_registry_db(db_path)
    with transaction(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM auth_profiles
            WHERE auth_profile_id = ?
               OR (profile_name = ? AND (? = '' OR workspace_id = ?))
            """,
            (identifier, identifier, workspace_id or "", workspace_id or ""),
        ).fetchone()
        result = row_to_dict(row)
        if not result:
            raise KeyError(f"Auth profile not found: {identifier}")
        return result


def ensure_policy_profile(
    profile_name: str = "default-internal",
    classification: str = "internal",
    redaction_level: int = 0,
    external_write_allowed: bool = False,
    requires_approval: list[str] | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not profile_name.strip():
        raise ValueError("profile_name is required")
    path = ensure_registry_db(db_path)
    profile_id = make_policy_profile_id(profile_name)
    timestamp = iso_now()
    with transaction(path) as conn:
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
                profile_id,
                profile_name.strip(),
                classification.strip() or "internal",
                int(redaction_level),
                1 if external_write_allowed else 0,
                json.dumps(requires_approval or [], ensure_ascii=False, sort_keys=True),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM policy_profiles WHERE policy_profile_id = ?", (profile_id,)).fetchone()
        return row_to_dict(row)


def ensure_network_profile(
    workspace_id: str = "",
    profile_name: str = "default-loopback",
    bind_scope: str = "loopback",
    api_base_url: str = "",
    mcp_transport: str = "stdio",
    mcp_command: str = "",
    auth_profile_ref: str = "",
    enabled: bool = True,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not profile_name.strip():
        raise ValueError("profile_name is required")
    path = ensure_registry_db(db_path)
    profile_id = make_network_profile_id(workspace_id, profile_name)
    timestamp = iso_now()
    with transaction(path) as conn:
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
                profile_id,
                workspace_id,
                profile_name.strip(),
                bind_scope.strip() or "loopback",
                api_base_url.strip(),
                mcp_transport.strip() or "stdio",
                mcp_command.strip(),
                auth_profile_ref.strip(),
                1 if enabled else 0,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM network_profiles WHERE network_profile_id = ?", (profile_id,)).fetchone()
        return row_to_dict(row)


def list_network_profiles(db_path: Path | str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
    path = ensure_registry_db(db_path)
    params: list[Any] = []
    where = ""
    if workspace_id:
        where = "WHERE workspace_id = ?"
        params.append(workspace_id)
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM network_profiles
            {where}
            ORDER BY workspace_id ASC, profile_name ASC
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


def get_network_profile(identifier: str, db_path: Path | str | None = None, workspace_id: str | None = None) -> dict[str, Any]:
    path = ensure_registry_db(db_path)
    with transaction(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM network_profiles
            WHERE network_profile_id = ?
               OR (profile_name = ? AND (? = '' OR workspace_id = ?))
            """,
            (identifier, identifier, workspace_id or "", workspace_id or ""),
        ).fetchone()
        result = row_to_dict(row)
        if not result:
            raise KeyError(f"Network profile not found: {identifier}")
        return result


def register_harness_profile(
    profile_name: str = "default",
    workspace_id: str | None = None,
    harness_type: str = "agent",
    default_agent_principal_id: str = "",
    policy_profile_id: str = "",
    network_profile_id: str = "",
    max_actions_per_hour: int = 6,
    min_action_interval_seconds: int = 300,
    max_open_actions: int = 20,
    default_priority: str = "normal",
    default_push_profile: str = "normal",
    require_human_approval: bool = False,
    enabled: bool = True,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not profile_name.strip():
        raise ValueError("profile_name is required")
    path = ensure_registry_db(db_path)
    if workspace_id is None:
        workspace_id = current_workspace(db_path=path)["workspace_id"]
    if not policy_profile_id:
        policy_profile_id = ensure_policy_profile(db_path=path)["policy_profile_id"]
    if not network_profile_id:
        network_profile_id = ensure_network_profile(workspace_id=workspace_id, db_path=path)["network_profile_id"]
    harness_id = make_harness_id(workspace_id, profile_name)
    timestamp = iso_now()
    with transaction(path) as conn:
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
                harness_id,
                workspace_id,
                profile_name.strip(),
                harness_type.strip() or "agent",
                default_agent_principal_id.strip(),
                policy_profile_id,
                network_profile_id,
                int(max_actions_per_hour),
                int(min_action_interval_seconds),
                int(max_open_actions),
                default_priority.strip() or "normal",
                default_push_profile.strip() or "normal",
                1 if require_human_approval else 0,
                1 if enabled else 0,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM harness_profiles WHERE harness_id = ?", (harness_id,)).fetchone()
        return row_to_dict(row)


def list_harness_profiles(db_path: Path | str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
    path = ensure_registry_db(db_path)
    where = ""
    params: list[Any] = []
    if workspace_id:
        where = "WHERE workspace_id = ?"
        params.append(workspace_id)
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM harness_profiles
            {where}
            ORDER BY workspace_id ASC, profile_name ASC
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


def get_harness_profile(identifier: str, db_path: Path | str | None = None, workspace_id: str | None = None) -> dict[str, Any]:
    path = ensure_registry_db(db_path)
    with transaction(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM harness_profiles
            WHERE harness_id = ?
               OR (profile_name = ? AND (? = '' OR workspace_id = ?))
            """,
            (identifier, identifier, workspace_id or "", workspace_id or ""),
        ).fetchone()
        result = row_to_dict(row)
        if not result:
            raise KeyError(f"Harness profile not found: {identifier}")
        return result


def register_agent_runtime(
    agent_name: str,
    workspace_id: str | None = None,
    role: str = "worker",
    status: str = "active",
    capabilities: list[str] | None = None,
    default_harness_id: str = "",
    max_active_tasks: int = 1,
    lease_seconds: int = 600,
    notes: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not agent_name.strip():
        raise ValueError("agent_name is required")
    path = ensure_registry_db(db_path)
    workspace = current_workspace(db_path=path) if workspace_id is None else {"workspace_id": workspace_id}
    principal = ensure_principal(
        principal_type="agent",
        display_name=agent_name,
        trust_level="trusted",
        db_path=path,
    )
    timestamp = iso_now()
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO agent_runtime_status(
                workspace_id, principal_id, agent_name, role, status, capabilities_json,
                default_harness_id, max_active_tasks, current_task_id, last_heartbeat_at,
                lease_until, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, principal_id) DO UPDATE SET
                agent_name = excluded.agent_name,
                role = excluded.role,
                status = excluded.status,
                capabilities_json = excluded.capabilities_json,
                default_harness_id = excluded.default_harness_id,
                max_active_tasks = excluded.max_active_tasks,
                last_heartbeat_at = excluded.last_heartbeat_at,
                lease_until = excluded.lease_until,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                workspace["workspace_id"],
                principal["principal_id"],
                agent_name.strip(),
                role.strip() or "worker",
                status.strip() or "active",
                json.dumps(sorted(set(capabilities or [])), ensure_ascii=False),
                default_harness_id.strip(),
                max(1, int(max_active_tasks)),
                timestamp,
                seconds_until_iso(lease_seconds),
                notes.strip(),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM agent_runtime_status WHERE workspace_id = ? AND principal_id = ?",
            (workspace["workspace_id"], principal["principal_id"]),
        ).fetchone()
        result = row_to_dict(row)
        result["principal"] = principal
        return result


def heartbeat_agent_runtime(
    agent_name: str = "",
    principal_id: str = "",
    workspace_id: str | None = None,
    status: str = "active",
    current_task_id: str = "",
    lease_seconds: int = 600,
    notes: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if not agent_name.strip() and not principal_id.strip():
        raise ValueError("agent_name or principal_id is required")
    path = ensure_registry_db(db_path)
    workspace = current_workspace(db_path=path) if workspace_id is None else {"workspace_id": workspace_id}
    principal = get_principal(principal_id, db_path=path) if principal_id else get_principal(agent_name, db_path=path, principal_type="agent")
    timestamp = iso_now()
    with transaction(path) as conn:
        existing = conn.execute(
            "SELECT * FROM agent_runtime_status WHERE workspace_id = ? AND principal_id = ?",
            (workspace["workspace_id"], principal["principal_id"]),
        ).fetchone()
        if not existing:
            created = register_agent_runtime(
                agent_name=principal["display_name"],
                workspace_id=workspace["workspace_id"],
                status=status,
                lease_seconds=lease_seconds,
                notes=notes,
                db_path=path,
            )
            if current_task_id:
                return heartbeat_agent_runtime(
                    principal_id=principal["principal_id"],
                    workspace_id=workspace["workspace_id"],
                    status=status,
                    current_task_id=current_task_id,
                    lease_seconds=lease_seconds,
                    notes=notes,
                    db_path=path,
                )
            return created
        conn.execute(
            """
            UPDATE agent_runtime_status
            SET status = ?,
                current_task_id = ?,
                last_heartbeat_at = ?,
                lease_until = ?,
                notes = CASE WHEN ? = '' THEN notes ELSE ? END,
                updated_at = ?
            WHERE workspace_id = ? AND principal_id = ?
            """,
            (
                status.strip() or "active",
                current_task_id.strip(),
                timestamp,
                seconds_until_iso(lease_seconds),
                notes.strip(),
                notes.strip(),
                timestamp,
                workspace["workspace_id"],
                principal["principal_id"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM agent_runtime_status WHERE workspace_id = ? AND principal_id = ?",
            (workspace["workspace_id"], principal["principal_id"]),
        ).fetchone()
        result = row_to_dict(row)
        result["principal"] = principal
        return result


def list_agent_runtimes(
    db_path: Path | str | None = None,
    workspace_id: str | None = None,
    active_only: bool = False,
    role: str | None = None,
) -> list[dict[str, Any]]:
    path = ensure_registry_db(db_path)
    params: list[Any] = []
    where: list[str] = []
    if workspace_id:
        where.append("workspace_id = ?")
        params.append(workspace_id)
    if active_only:
        where.append("status IN ('active', 'idle', 'busy')")
        where.append("(lease_until IS NULL OR lease_until >= ?)")
        params.append(iso_now())
    if role:
        where.append("role = ?")
        params.append(role)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM agent_runtime_status
            {clause}
            ORDER BY role ASC, agent_name ASC
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


def get_agent_runtime(
    identifier: str,
    db_path: Path | str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    path = ensure_registry_db(db_path)
    params = [identifier, identifier]
    workspace_clause = ""
    if workspace_id:
        workspace_clause = "AND workspace_id = ?"
        params.append(workspace_id)
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM agent_runtime_status
            WHERE (principal_id = ? OR agent_name = ?)
              {workspace_clause}
            ORDER BY workspace_id ASC
            """,
            params,
        ).fetchall()
        results = rows_to_dicts(rows)
        if not results:
            raise KeyError(f"Agent runtime not found: {identifier}")
        if len(results) > 1:
            raise ValueError(f"Agent runtime reference is ambiguous: {identifier}")
        return results[0]


def record_action_intake_event(
    decision: str,
    harness_id: str = "",
    principal_id: str = "",
    workspace_id: str = "",
    task_id: str = "",
    action_key: str = "",
    reason: str = "",
    payload: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = ensure_registry_db(db_path)
    event = {
        "event_id": make_action_event_id(),
        "harness_id": harness_id,
        "principal_id": principal_id,
        "workspace_id": workspace_id,
        "task_id": task_id,
        "action_key": action_key,
        "decision": decision,
        "reason": reason,
        "payload_json": json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        "created_at": iso_now(),
    }
    with transaction(path) as conn:
        columns = list(event)
        conn.execute(
            f"INSERT INTO action_intake_events({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(event[column] for column in columns),
        )
        row = conn.execute("SELECT * FROM action_intake_events WHERE event_id = ?", (event["event_id"],)).fetchone()
        return row_to_dict(row)


def record_sync_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    local_task_id: str = "",
    hub_task_id: str = "",
    source_workspace_id: str = "",
    target_workspace_id: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = ensure_registry_db(db_path)
    event = {
        "event_id": make_sync_event_id(),
        "event_type": event_type,
        "local_task_id": local_task_id,
        "hub_task_id": hub_task_id,
        "source_workspace_id": source_workspace_id,
        "target_workspace_id": target_workspace_id,
        "payload_json": json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        "created_at": iso_now(),
    }
    with transaction(path) as conn:
        columns = list(event)
        conn.execute(
            f"INSERT INTO sync_events({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(event[column] for column in columns),
        )
        row = conn.execute("SELECT * FROM sync_events WHERE event_id = ?", (event["event_id"],)).fetchone()
        return row_to_dict(row)
