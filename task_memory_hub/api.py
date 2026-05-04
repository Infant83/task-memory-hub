from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import argparse
import hmac
import json
import mimetypes

from .branding import APP_NAME
from .config import get_or_create_api_token
from .orchestrator import run_orchestrator_once
from .runner import run_runner_once
from .registry import (
    current_workspace,
    ensure_principal,
    get_harness_profile,
    get_principal,
    get_workspace,
    heartbeat_agent_runtime,
    list_agent_runtimes,
    list_harness_profiles,
    list_principals,
    list_workspaces,
    register_agent_runtime,
)
from .service import (
    ack_task,
    append_progress,
    claim_task,
    claim_next_task,
    complete_task,
    create_task,
    ensure_db,
    get_context_pack,
    get_task,
    get_task_tree,
    heartbeat_claim,
    list_automations,
    list_tasks,
    record_approval_decision,
    decide_review_gate,
    request_delivery_dry_run,
    release_task,
    request_review_gate,
    request_task_stop,
    snooze_task,
    task_registry_summary,
    update_task,
)
from .store import rows_to_dicts, transaction
from .sync import fetch_origin_task
from .timeutil import iso_now


ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
STATIC_DIR = Path(__file__).with_name("static")


def parse_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def parse_form_body(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def apply_default_task_binding(
    payload: dict,
    db_path: Path | str | None,
    principal_type: str,
    display_name: str,
    trust_level: str,
) -> dict:
    workspace = current_workspace(db_path=db_path)
    principal = ensure_principal(
        principal_type=principal_type,
        display_name=display_name,
        trust_level=trust_level,
        db_path=db_path,
    )
    payload.setdefault("source_workspace", workspace["workspace_slug"])
    payload.setdefault("source_workspace_id", workspace["workspace_id"])
    payload.setdefault("source_workspace_slug", workspace["workspace_slug"])
    payload.setdefault("source_principal_id", principal["principal_id"])
    payload.setdefault("proposed_by_principal_id", principal["principal_id"])
    return payload


def make_handler(db_path: Path | str | None = None, write_token: str | None = None):
    class AppHandler(BaseHTTPRequestHandler):
        server_version = f"{APP_NAME.replace(' ', '')}/0.1"

        def log_message(self, format: str, *args) -> None:
            return

        def _host_allowed(self) -> bool:
            host_header = self.headers.get("Host", "")
            host = urlparse(f"//{host_header}").hostname or ""
            origin = self.headers.get("Origin")
            if host not in ALLOWED_HOSTS:
                return False
            if origin:
                parsed = urlparse(origin)
                if parsed.hostname not in ALLOWED_HOSTS:
                    return False
            return True

        def _send_common_headers(self, cache_control: str) -> None:
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

        def _send(self, status: int, payload: object) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._send_common_headers("no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_html(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._send_common_headers("no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, status: int, path: Path) -> None:
            data = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self._send_common_headers("public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        def _send_no_content(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_common_headers("public, max-age=86400")
            self.end_headers()

        def _error(self, status: int, message: str) -> None:
            self._send(status, {"error": message})

        def _write_authorized(self) -> bool:
            if not write_token:
                return True
            header_token = self.headers.get("X-Task-Memory-Hub-Token", "")
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                header_token = auth[7:].strip()
            return hmac.compare_digest(header_token, write_token)

        def _guard(self, write: bool = False) -> bool:
            if not self._host_allowed():
                self._error(HTTPStatus.FORBIDDEN, "Host/Origin rejected")
                return False
            if write and not self._write_authorized():
                self._error(HTTPStatus.UNAUTHORIZED, "Write token required")
                return False
            return True

        def do_GET(self) -> None:
            if not self._guard():
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            try:
                if path in {"/health/live", "/health/ready"}:
                    self._send(HTTPStatus.OK, {"ok": True})
                    return
                if path == "/favicon.ico":
                    self._send_no_content()
                    return
                if path.startswith("/static/"):
                    asset_name = path.removeprefix("/static/")
                    asset_path = get_static_asset_path(asset_name)
                    if asset_path is None:
                        self._error(HTTPStatus.NOT_FOUND, "Static asset not found")
                        return
                    self._send_file(HTTPStatus.OK, asset_path)
                    return
                if path == "/openapi.json":
                    self._send(HTTPStatus.OK, build_openapi_spec())
                    return
                if path.startswith("/docs/swagger-ui/"):
                    asset_name = path.removeprefix("/docs/swagger-ui/")
                    asset_path = get_swagger_ui_asset_path(asset_name)
                    if asset_path is None:
                        self._error(HTTPStatus.NOT_FOUND, "Swagger UI asset not found")
                        return
                    self._send_file(HTTPStatus.OK, asset_path)
                    return
                if path in {"/docs", "/swagger"}:
                    self._send_html(HTTPStatus.OK, render_swagger_docs())
                    return
                if path == "/docs/reference":
                    self._send_html(HTTPStatus.OK, render_api_docs())
                    return
                if path.startswith("/registry/"):
                    parts = path.split("/")
                    if len(parts) == 4:
                        registry_kind = parts[2]
                        identifier = parts[3]
                        if registry_kind == "workspaces":
                            record = get_workspace(identifier, db_path=db_path)
                        elif registry_kind == "principals":
                            record = get_principal(identifier, db_path=db_path)
                        elif registry_kind == "harnesses":
                            record = get_harness_profile(identifier, db_path=db_path)
                        else:
                            self._error(HTTPStatus.NOT_FOUND, "Registry kind not found")
                            return
                        self._send_html(HTTPStatus.OK, render_registry_record(registry_kind, identifier, record))
                        return
                if path == "/":
                    tasks = list_tasks(db_path=db_path, limit=100)
                    self._send_html(HTTPStatus.OK, render_index(tasks))
                    return
                if path == "/quick-add":
                    self._send_html(HTTPStatus.OK, render_quick_add(write_token or ""))
                    return
                if path.startswith("/tasks/"):
                    task_id = path.split("/", 2)[2]
                    task = get_task(task_id, db_path=db_path)
                    detail = build_task_detail_context(task, db_path)
                    self._send_html(HTTPStatus.OK, render_task(task, detail, write_token or ""))
                    return
                if path == "/v1/tasks":
                    status = query.get("status", [None])[0]
                    due = query.get("due", ["false"])[0].lower() in {"1", "true", "yes"}
                    limit = int(query.get("limit", ["50"])[0])
                    task_kind = query.get("kind", [None])[0]
                    controller_status = query.get("controller_status", [None])[0]
                    source_principal_id = query.get("source_principal_id", [None])[0]
                    target_principal_id = query.get("target_principal_id", [None])[0]
                    harness_id = query.get("harness_id", [None])[0]
                    parent_task_id = query.get("parent_task_id", [None])[0]
                    self._send(
                        HTTPStatus.OK,
                        list_tasks(
                            db_path=db_path,
                            status=status,
                            due=due,
                            task_kind=task_kind,
                            controller_status=controller_status,
                            source_principal_id=source_principal_id,
                            target_principal_id=target_principal_id,
                            harness_id=harness_id,
                            parent_task_id=parent_task_id,
                            limit=limit,
                        ),
                    )
                    return
                if path == "/v1/registry/status":
                    workspace = current_workspace(db_path=db_path)
                    self._send(
                        HTTPStatus.OK,
                        {
                            "workspace": workspace,
                            "principals": list_principals(db_path=db_path),
                            "agent_runtimes": list_agent_runtimes(db_path=db_path, workspace_id=workspace["workspace_id"]),
                            "harness_profiles": list_harness_profiles(db_path=db_path, workspace_id=workspace["workspace_id"]),
                            "task_summary": task_registry_summary(db_path=db_path),
                        },
                    )
                    return
                if path == "/v1/workspaces":
                    status = query.get("status", [None])[0]
                    self._send(HTTPStatus.OK, list_workspaces(db_path=db_path, status=status))
                    return
                if path.startswith("/v1/workspaces/"):
                    identifier = path.split("/", 3)[3]
                    self._send(HTTPStatus.OK, get_workspace(identifier, db_path=db_path))
                    return
                if path == "/v1/principals":
                    active_only = query.get("active_only", ["false"])[0].lower() in {"1", "true", "yes"}
                    self._send(HTTPStatus.OK, list_principals(db_path=db_path, active_only=active_only))
                    return
                if path.startswith("/v1/principals/"):
                    identifier = path.split("/", 3)[3]
                    self._send(HTTPStatus.OK, get_principal(identifier, db_path=db_path))
                    return
                if path == "/v1/harnesses":
                    workspace = current_workspace(db_path=db_path)
                    self._send(HTTPStatus.OK, list_harness_profiles(db_path=db_path, workspace_id=workspace["workspace_id"]))
                    return
                if path.startswith("/v1/harnesses/"):
                    workspace = current_workspace(db_path=db_path)
                    identifier = path.split("/", 3)[3]
                    self._send(HTTPStatus.OK, get_harness_profile(identifier, db_path=db_path, workspace_id=workspace["workspace_id"]))
                    return
                if path == "/v1/agents":
                    workspace = current_workspace(db_path=db_path)
                    active_only = query.get("active_only", ["false"])[0].lower() in {"1", "true", "yes"}
                    role = query.get("role", [None])[0]
                    self._send(
                        HTTPStatus.OK,
                        list_agent_runtimes(
                            db_path=db_path,
                            workspace_id=workspace["workspace_id"],
                            active_only=active_only,
                            role=role,
                        ),
                    )
                    return
                if path == "/v1/tasks/tree":
                    limit = int(query.get("limit", ["200"])[0])
                    root_task_id = query.get("task_id", [None])[0]
                    self._send(HTTPStatus.OK, get_task_tree(root_task_id, db_path=db_path, limit=limit))
                    return
                if path == "/v1/automations":
                    limit = int(query.get("limit", ["50"])[0])
                    controller_status = query.get("status", [None])[0]
                    include_runs = query.get("include_runs", ["false"])[0].lower() in {"1", "true", "yes"}
                    self._send(
                        HTTPStatus.OK,
                        list_automations(
                            db_path=db_path,
                            controller_status=controller_status,
                            include_runs=include_runs,
                            limit=limit,
                        ),
                    )
                    return
                if path == "/v1/tasks/due":
                    limit = int(query.get("limit", ["50"])[0])
                    self._send(HTTPStatus.OK, list_tasks(db_path=db_path, due=True, limit=limit))
                    return
                if path.startswith("/v1/tasks/"):
                    parts = path.split("/")
                    task_id = parts[3]
                    if len(parts) == 5 and parts[4] == "context-pack":
                        self._send(HTTPStatus.OK, get_context_pack(task_id, db_path=db_path))
                        return
                    if len(parts) == 5 and parts[4] == "origin":
                        source_db = query.get("source_db", [None])[0]
                        self._send(
                            HTTPStatus.OK,
                            fetch_origin_task(task_id, global_db_path=db_path, source_db_path=source_db),
                        )
                        return
                    if len(parts) == 4:
                        self._send(HTTPStatus.OK, get_task(task_id, db_path=db_path))
                        return
                self._error(HTTPStatus.NOT_FOUND, "Not found")
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
            except Exception as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path == "/quick-add":
                    if not self._guard(write=False):
                        return
                    form = parse_form_body(self)
                    if write_token and not hmac.compare_digest(form.get("token", ""), write_token):
                        self._error(HTTPStatus.UNAUTHORIZED, "Write token required")
                        return
                    payload = apply_default_task_binding(
                        {
                            "title": form.get("title", ""),
                            "summary": form.get("summary", ""),
                            "next_action": form.get("next_action", ""),
                            "detail_md": form.get("detail_md", ""),
                            "priority": form.get("priority", "normal"),
                            "due_at": form.get("due_at") or None,
                            "tags": [item.strip() for item in form.get("tags", "").split(",") if item.strip()],
                            "source_agent": "quick-add",
                        },
                        db_path,
                        principal_type="human",
                        display_name="owner",
                        trust_level="owner",
                    )
                    task = create_task(
                        payload,
                        db_path=db_path,
                        actor="manual",
                    )
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", f"/tasks/{task['task_id']}")
                    self._send_common_headers("no-store")
                    self.end_headers()
                    return

                if not self._guard(write=True):
                    return
                body = parse_json_body(self)
                if path == "/v1/tasks":
                    idempotency = self.headers.get("Idempotency-Key")
                    if idempotency and "idempotency_key" not in body:
                        body["idempotency_key"] = idempotency
                    apply_default_task_binding(
                        body,
                        db_path,
                        principal_type="service",
                        display_name=body.get("source_agent", "api") or "api",
                        trust_level="trusted",
                    )
                    self._send(HTTPStatus.CREATED, create_task(body, db_path=db_path, actor=body.get("source_agent", "api")))
                    return
                if path == "/v1/automations":
                    tags = body.get("tags") or []
                    if isinstance(tags, str):
                        tags = [item.strip() for item in tags.split(",") if item.strip()]
                    body.setdefault("task_kind", "automation")
                    body.setdefault("controller_status", "active")
                    body.setdefault("status", "scheduled")
                    body["tags"] = sorted(set(tags + ["automation"]))
                    apply_default_task_binding(
                        body,
                        db_path,
                        principal_type="service",
                        display_name=body.get("source_agent", "api") or "api",
                        trust_level="trusted",
                    )
                    self._send(HTTPStatus.CREATED, create_task(body, db_path=db_path, actor=body.get("source_agent", "api")))
                    return
                if path == "/v1/tasks/claim-next":
                    self._send(
                        HTTPStatus.OK,
                        claim_next_task(
                            owner=body.get("owner", "api"),
                            lease_seconds=int(body.get("lease_seconds", 1800)),
                            workspace=body.get("workspace"),
                            include_not_due=bool(body.get("include_not_due", False)),
                            db_path=db_path,
                        )
                        or {"claimed": None},
                    )
                    return
                if path == "/v1/agents/register":
                    workspace = current_workspace(db_path=db_path)
                    self._send(
                        HTTPStatus.CREATED,
                        register_agent_runtime(
                            agent_name=body.get("name") or body.get("agent_name", ""),
                            workspace_id=workspace["workspace_id"],
                            role=body.get("role", "worker"),
                            status=body.get("status", "active"),
                            capabilities=body.get("capabilities") or [],
                            default_harness_id=body.get("default_harness_id", ""),
                            max_active_tasks=int(body.get("max_active_tasks", 1)),
                            lease_seconds=int(body.get("lease_seconds", 600)),
                            notes=body.get("notes", ""),
                            db_path=db_path,
                        ),
                    )
                    return
                if path == "/v1/agents/heartbeat":
                    workspace = current_workspace(db_path=db_path)
                    self._send(
                        HTTPStatus.OK,
                        heartbeat_agent_runtime(
                            agent_name=body.get("name") or body.get("agent_name", ""),
                            principal_id=body.get("principal_id", ""),
                            workspace_id=workspace["workspace_id"],
                            status=body.get("status", "active"),
                            current_task_id=body.get("current_task_id", ""),
                            lease_seconds=int(body.get("lease_seconds", 600)),
                            notes=body.get("notes", ""),
                            db_path=db_path,
                        ),
                    )
                    return
                if path == "/v1/orchestrator/run-once":
                    self._send(
                        HTTPStatus.OK,
                        run_orchestrator_once(
                            orchestrator_name=body.get("name", "orchestrator"),
                            db_path=db_path,
                            include_not_due=bool(body.get("include_not_due", True)),
                            limit=int(body.get("limit", 10)),
                            default_harness=body.get("default_harness", ""),
                            dry_run=bool(body.get("dry_run", False)),
                        ),
                    )
                    return
                if path == "/v1/runner/run-once":
                    self._send(
                        HTTPStatus.OK,
                        run_runner_once(
                            agent_name=body.get("name", "web-ui-runner"),
                            backend=body.get("backend", "dry_run"),
                            backend_command=body.get("backend_command", ""),
                            timeout_seconds=int(body.get("timeout_seconds", 120)),
                            script_allowlist_path=body.get("script_allowlist_path") or None,
                            capabilities=body.get("capabilities") or None,
                            harness=body.get("harness", ""),
                            task_id=body.get("task_id", ""),
                            include_not_due=bool(body.get("include_not_due", True)),
                            run_orchestrator=bool(body.get("run_orchestrator", False)),
                            db_path=db_path,
                        ),
                    )
                    return
                if path.startswith("/v1/tasks/"):
                    parts = path.split("/")
                    task_id = parts[3]
                    action = parts[4] if len(parts) >= 5 else ""
                    if action == "ack":
                        self._send(HTTPStatus.OK, ack_task(task_id, db_path=db_path, actor="api"))
                        return
                    if action == "review-gate":
                        self._send(
                            HTTPStatus.CREATED,
                            request_review_gate(
                                task_id,
                                reason=body.get("reason", ""),
                                actor=body.get("by") or body.get("actor") or "web-ui",
                                gate_type=body.get("gate_type", "pre_execution"),
                                reviewer_principal_id=body.get("reviewer_principal_id", ""),
                                db_path=db_path,
                            ),
                        )
                        return
                    if action == "review-gate-decision":
                        approver_principal_id = (
                            body.get("approver_principal_id")
                            or body.get("approved_by_principal_id")
                            or body.get("principal_id")
                            or ""
                        )
                        actor = body.get("by") or body.get("actor") or "web-ui"
                        if not approver_principal_id:
                            approver = ensure_principal(
                                principal_type=body.get("principal_type", "human"),
                                display_name=actor,
                                trust_level=body.get("trust_level", "owner"),
                                db_path=db_path,
                            )
                            approver_principal_id = approver["principal_id"]
                        self._send(
                            HTTPStatus.OK,
                            decide_review_gate(
                                task_id,
                                body.get("decision", "approved"),
                                approver_principal_id=approver_principal_id,
                                reason=body.get("reason", ""),
                                actor=actor,
                                db_path=db_path,
                            ),
                        )
                        return
                    if action == "delivery-dry-run":
                        self._send(
                            HTTPStatus.OK,
                            request_delivery_dry_run(
                                task_id,
                                channel=body.get("channel", ""),
                                recipient_ref=body.get("recipient_ref", ""),
                                include_artifacts=body.get("include_artifacts") or None,
                                requires_review=body.get("requires_review"),
                                reason=body.get("reason", ""),
                                actor=body.get("by") or body.get("actor") or "web-ui",
                                db_path=db_path,
                            ),
                        )
                        return
                    if action in {"approve", "reject", "request-changes"}:
                        approver_principal_id = (
                            body.get("approver_principal_id")
                            or body.get("approved_by_principal_id")
                            or body.get("principal_id")
                            or ""
                        )
                        actor = body.get("by") or body.get("actor") or "web-ui"
                        if not approver_principal_id:
                            approver = ensure_principal(
                                principal_type=body.get("principal_type", "human"),
                                display_name=actor,
                                trust_level=body.get("trust_level", "owner"),
                                db_path=db_path,
                            )
                            approver_principal_id = approver["principal_id"]
                        decision = {
                            "approve": "approved",
                            "reject": "rejected",
                            "request-changes": "changes_requested",
                        }[action]
                        self._send(
                            HTTPStatus.OK,
                            record_approval_decision(
                                task_id,
                                decision,
                                approver_principal_id,
                                reason=body.get("reason", ""),
                                actor=actor,
                                db_path=db_path,
                            ),
                        )
                        return
                    if action == "stop":
                        self._send(
                            HTTPStatus.OK,
                            request_task_stop(
                                task_id,
                                reason=body.get("reason", ""),
                                actor=body.get("by") or body.get("actor") or "web-ui",
                                db_path=db_path,
                            ),
                        )
                        return
                    if action == "complete":
                        self._send(HTTPStatus.OK, complete_task(task_id, db_path=db_path, actor="api"))
                        return
                    if action == "snooze":
                        self._send(
                            HTTPStatus.OK,
                            snooze_task(
                                task_id,
                                until=body.get("until"),
                                duration=body.get("duration"),
                                db_path=db_path,
                                actor="api",
                            ),
                        )
                        return
                    if action == "claim":
                        owner = body.get("owner") or body.get("target_principal_id") or "api"
                        self._send(
                            HTTPStatus.OK,
                            claim_task(
                                task_id,
                                owner=owner,
                                lease_seconds=int(body.get("lease_seconds", 1800)),
                                target_principal_id=body.get("target_principal_id", ""),
                                db_path=db_path,
                            ),
                        )
                        return
                    if action == "release":
                        self._send(
                            HTTPStatus.OK,
                            release_task(
                                task_id,
                                owner=body.get("owner"),
                                next_status=body.get("next_status", "acknowledged"),
                                db_path=db_path,
                            ),
                        )
                        return
                    if action == "heartbeat":
                        self._send(
                            HTTPStatus.OK,
                            heartbeat_claim(
                                task_id,
                                owner=body.get("owner", "api"),
                                lease_seconds=int(body.get("lease_seconds", 1800)),
                                db_path=db_path,
                            ),
                        )
                        return
                    if action == "progress":
                        self._send(
                            HTTPStatus.OK,
                            append_progress(
                                task_id,
                                message=body.get("message", ""),
                                owner=body.get("owner", "api"),
                                db_path=db_path,
                            ),
                        )
                        return
                self._error(HTTPStatus.NOT_FOUND, "Not found")
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
            except Exception as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_PATCH(self) -> None:
            if not self._guard(write=True):
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path.startswith("/v1/tasks/"):
                    task_id = path.split("/")[3]
                    self._send(HTTPStatus.OK, update_task(task_id, parse_json_body(self), db_path=db_path, actor="api"))
                    return
                self._error(HTTPStatus.NOT_FOUND, "Not found")
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
            except Exception as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))

    return AppHandler


def run_server(host: str = "127.0.0.1", port: int = 8787, db_path: Path | str | None = None) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError(f"{APP_NAME} API is loopback-only in this build")
    ensure_db(db_path)
    token = get_or_create_api_token()
    server = ThreadingHTTPServer((host, port), make_handler(db_path=db_path, write_token=token))
    print(f"{APP_NAME} API listening on http://{host}:{port}")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Run {APP_NAME} REST API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    run_server(host=args.host, port=args.port, db_path=args.db)
    return 0


def _esc(value: object) -> str:
    import html

    return html.escape("" if value is None else str(value))


def _json_pre(value: object) -> str:
    return _esc(json.dumps(value or {}, ensure_ascii=False, indent=2))


def _json_script(value: object) -> str:
    data = json.dumps(value or {}, ensure_ascii=False)
    return data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _short_datetime(value: object) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    return text.replace("T", " ").replace("Z", "")[:16]


def _short_id(value: object, chars: int = 7) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    if "_" in text:
        prefix, suffix = text.split("_", 1)
        return f"{prefix}_{suffix[:chars]}"
    return text[: chars + 4]


def _lookup_principal(identifier: str | None, db_path: Path | str | None) -> dict | None:
    if not identifier:
        return None
    try:
        return get_principal(identifier, db_path=db_path)
    except Exception:
        return None


def _lookup_workspace(identifier: str | None, db_path: Path | str | None) -> dict | None:
    if not identifier:
        return None
    try:
        return get_workspace(identifier, db_path=db_path)
    except Exception:
        return None


def _lookup_harness(identifier: str | None, db_path: Path | str | None, workspace_id: str | None = None) -> dict | None:
    if not identifier:
        return None
    try:
        return get_harness_profile(identifier, db_path=db_path, workspace_id=workspace_id)
    except Exception:
        try:
            return get_harness_profile(identifier, db_path=db_path)
        except Exception:
            return None


def _task_events(task_id: str, db_path: Path | str | None, limit: int = 40) -> list[dict]:
    with transaction(ensure_db(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM task_events
            WHERE task_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (task_id, limit),
        ).fetchall()
        return rows_to_dicts(rows)


def _runtime_live(runtime: dict | None) -> bool:
    if not runtime:
        return False
    if runtime.get("status") not in {"active", "idle", "busy"}:
        return False
    lease_until = runtime.get("lease_until")
    return not lease_until or lease_until >= iso_now()


def build_task_detail_context(task: dict, db_path: Path | str | None = None) -> dict:
    current = current_workspace(db_path=db_path)
    source_workspace = _lookup_workspace(task.get("source_workspace_id"), db_path) or _lookup_workspace(
        task.get("source_workspace_slug"), db_path
    )
    target_workspace = _lookup_workspace(task.get("target_workspace_id"), db_path)
    runtime_workspace_id = task.get("target_workspace_id") or current.get("workspace_id")
    runtimes = list_agent_runtimes(db_path=db_path, workspace_id=runtime_workspace_id)
    target_runtime = next(
        (runtime for runtime in runtimes if runtime.get("principal_id") == task.get("target_principal_id")),
        None,
    )
    claim_runtime = next(
        (
            runtime
            for runtime in runtimes
            if runtime.get("principal_id") == task.get("agent_claim_owner")
            or runtime.get("agent_name") == task.get("agent_claim_owner")
        ),
        None,
    )
    target_principal = _lookup_principal(task.get("target_principal_id"), db_path)
    target_agent_name = (
        target_principal.get("display_name")
        if target_principal and target_principal.get("principal_type") == "agent"
        else ""
    )
    return {
        "current_workspace": current,
        "source_workspace": source_workspace,
        "target_workspace": target_workspace,
        "source_principal": _lookup_principal(task.get("source_principal_id"), db_path),
        "target_principal": target_principal,
        "proposed_by": _lookup_principal(task.get("proposed_by_principal_id"), db_path),
        "approved_by": _lookup_principal(task.get("approved_by_principal_id"), db_path),
        "assigned_by": _lookup_principal(task.get("assigned_by_principal_id"), db_path),
        "harness": _lookup_harness(task.get("harness_id"), db_path, runtime_workspace_id),
        "agent_runtime": target_runtime or claim_runtime,
        "agent_runtime_live": _runtime_live(target_runtime or claim_runtime),
        "target_agent_name": target_agent_name,
        "agent_activation_possible": bool(target_agent_name and not _runtime_live(target_runtime)),
        "events": _task_events(task["task_id"], db_path),
        "runtime_workspace_id": runtime_workspace_id,
    }


def _principal_label(principal: dict | None, fallback: str = "") -> str:
    if not principal:
        return fallback
    return f"{principal.get('display_name', '')} ({principal.get('principal_type', '')}) / {principal.get('principal_id', '')}"


def _workspace_label(workspace: dict | None, fallback: str = "") -> str:
    if not workspace:
        return fallback
    return f"{workspace.get('display_name', '')} / {workspace.get('workspace_slug', '')} / {workspace.get('workspace_id', '')}"


def _registry_ref(
    kind: str,
    identifier: str,
    title: str = "",
    subtitle: str = "",
    known: bool = True,
) -> str:
    if not identifier:
        return '<span class="muted">-</span>'
    label = title or ("unregistered" if not known else identifier)
    state = "" if known else " missing"
    href = f"/registry/{kind}/{_esc(identifier)}"
    subtitle_html = f'<span class="registry-ref-sub">{_esc(subtitle)}</span>' if subtitle else ""
    body = (
        f'<span class="registry-ref-label">{_esc(label)}</span>'
        f'<code>{_esc(_short_id(identifier))}</code>'
        f"{subtitle_html}"
    )
    if not known:
        return f'<span class="registry-ref{state}" title="{_esc(identifier)}">{body}</span>'
    return f'<a class="registry-ref{state}" href="{href}" title="{_esc(identifier)}">{body}</a>'


def _principal_ref(principal: dict | None, fallback: str = "") -> str:
    if principal:
        subtitle = principal.get("principal_type") or ""
        return _registry_ref("principals", principal["principal_id"], principal.get("display_name", ""), subtitle)
    return _registry_ref("principals", fallback, known=False) if fallback else '<span class="muted">-</span>'


def _workspace_ref(workspace: dict | None, fallback: str = "") -> str:
    if workspace:
        subtitle = workspace.get("workspace_slug") or ""
        return _registry_ref("workspaces", workspace["workspace_id"], workspace.get("display_name", ""), subtitle)
    return _registry_ref("workspaces", fallback, known=False) if fallback else '<span class="muted">-</span>'


def _harness_ref(harness: dict | None, fallback: str = "") -> str:
    if harness:
        subtitle = harness.get("harness_type") or ""
        return _registry_ref("harnesses", harness["harness_id"], harness.get("profile_name", ""), subtitle)
    return _registry_ref("harnesses", fallback, known=False) if fallback else '<span class="muted">-</span>'


def _task_id_ref(task_id: str) -> str:
    if not task_id:
        return '<span class="muted">-</span>'
    return f'<code title="{_esc(task_id)}">{_esc(_short_id(task_id))}</code>'


def _field_grid(fields: list[tuple[str, object, str]]) -> str:
    rows = []
    for label, value, css_class in fields:
        classes = set(css_class.split())
        if value is None or value == "":
            value_html = '<span class="muted">-</span>'
        elif "html" in classes:
            value_html = str(value)
        else:
            value_html = _esc(value)
        visible_class = " ".join(cls for cls in classes if cls != "html")
        rows.append(
            f"""
            <div class="field">
              <dt>{_esc(label)}</dt>
              <dd class="{_esc(visible_class)}">{value_html}</dd>
            </div>
            """
        )
    return "\n".join(rows)


def _event_rows(events: list[dict]) -> str:
    if not events:
        return '<tr><td colspan="4" class="muted">No events.</td></tr>'
    return "\n".join(
        f"""
        <tr>
          <td>{_esc(event.get('created_at'))}</td>
          <td><span class="pill">{_esc(event.get('event_type'))}</span></td>
          <td>{_esc(event.get('actor'))}</td>
          <td><code>{_esc(json.dumps(event.get('payload') or {}, ensure_ascii=False))}</code></td>
        </tr>
        """
        for event in events
    )


def render_registry_record(kind: str, identifier: str, record: dict) -> str:
    label_keys = {
        "workspaces": ("display_name", "workspace_id"),
        "principals": ("display_name", "principal_id"),
        "harnesses": ("profile_name", "harness_id"),
    }
    title_key, id_key = label_keys.get(kind, ("display_name", "id"))
    title = record.get(title_key) or identifier
    record_id = record.get(id_key) or identifier
    fields = [(key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value, "path") for key, value in record.items()]
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(title)} - Registry - {_esc(APP_NAME)}</title>
  <link rel="stylesheet" href="{_esc(_static_asset_url('app.css'))}">
</head>
<body>
  <main class="shell">
    <nav class="topbar">
      <span><a href="/">Tasks</a> · <a href="/docs">API Docs</a></span>
      <span class="mono">{_esc(_short_id(record_id))}</span>
    </nav>
    <section class="heading">
      <div>
        <h1>{_esc(title)}</h1>
        <div class="subtle">{_esc(kind)} registry record · <code title="{_esc(record_id)}">{_esc(_short_id(record_id))}</code></div>
      </div>
    </section>
    <section class="panel">
      <h2>Registry Fields</h2>
      <dl class="fields">{_field_grid(fields)}</dl>
    </section>
    <section class="panel">
      <h2>Raw JSON</h2>
      <pre>{_json_pre(record)}</pre>
    </section>
  </main>
</body>
</html>"""


def get_static_asset_path(asset_name: str) -> Path | None:
    if not asset_name or "/" in asset_name or "\\" in asset_name:
        return None
    candidate = (STATIC_DIR / asset_name).resolve()
    if candidate.parent != STATIC_DIR.resolve() or not candidate.is_file():
        return None
    return candidate


def _static_asset_url(asset_name: str) -> str:
    path = get_static_asset_path(asset_name)
    if path is None:
        return f"/static/{asset_name}"
    return f"/static/{asset_name}?v={int(path.stat().st_mtime)}"


def get_swagger_ui_root() -> Path | None:
    try:
        import swagger_ui_bundle
    except ImportError:
        return None
    root = Path(swagger_ui_bundle.swagger_ui_path).resolve()
    return root if root.is_dir() else None


def get_swagger_ui_asset_path(asset_name: str) -> Path | None:
    if not asset_name or "/" in asset_name or "\\" in asset_name:
        return None
    root = get_swagger_ui_root()
    if root is None:
        return None
    candidate = (root / asset_name).resolve()
    if candidate.parent != root or not candidate.is_file():
        return None
    return candidate


def build_openapi_spec() -> dict:
    ok_response = {
        "description": "JSON response",
        "content": {"application/json": {"schema": {}}},
    }
    html_response = {
        "description": "HTML response",
        "content": {"text/html": {"schema": {"type": "string"}}},
    }
    error_response = {
        "description": "Error",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }
    task_id_param = {
        "name": "task_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
        "description": "Task identifier.",
    }
    registry_identifier_param = {
        "name": "identifier",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
        "description": "Registry id or human-readable name/slug.",
    }
    source_db_param = {
        "name": "source_db",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": "Optional source workspace SQLite DB path for origin lookup.",
    }

    def query_param(name: str, schema: dict, description: str) -> dict:
        return {
            "name": name,
            "in": "query",
            "required": False,
            "schema": schema,
            "description": description,
        }

    def json_body(schema_ref: str) -> dict:
        return {
            "required": False,
            "content": {
                "application/json": {
                    "schema": {"$ref": schema_ref},
                }
            },
        }

    write_security = [{"TaskMemoryHubToken": []}, {"TaskMemoryHubBearer": []}]

    return {
        "openapi": "3.0.3",
        "info": {
            "title": f"{APP_NAME} Loopback API",
            "version": "0.1.0",
            "description": (
                "Local-first task memory API for CLI, scripts, browser UI, tray, and MCP fallback. "
                "This implementation is a stdlib ThreadingHTTPServer with Swagger UI, not FastAPI."
            ),
        },
        "servers": [{"url": "/", "description": "Current loopback server"}],
        "tags": [
            {"name": "health"},
            {"name": "html"},
            {"name": "tasks"},
            {"name": "automations"},
            {"name": "agent"},
            {"name": "registry"},
            {"name": "orchestrator"},
        ],
        "paths": {
            "/health/live": {
                "get": {
                    "tags": ["health"],
                    "summary": "Liveness probe",
                    "responses": {"200": ok_response},
                }
            },
            "/health/ready": {
                "get": {
                    "tags": ["health"],
                    "summary": "Readiness probe",
                    "responses": {"200": ok_response},
                }
            },
            "/": {
                "get": {
                    "tags": ["html"],
                    "summary": "Minimal task list UI",
                    "responses": {"200": html_response},
                }
            },
            "/quick-add": {
                "get": {
                    "tags": ["html"],
                    "summary": "Quick-add HTML form",
                    "responses": {"200": html_response},
                },
                "post": {
                    "tags": ["html"],
                    "summary": "Create a task from the quick-add form",
                    "responses": {"303": {"description": "Redirect to task detail"}, "401": error_response},
                },
            },
            "/tasks/{task_id}": {
                "get": {
                    "tags": ["html"],
                    "summary": "Task detail UI",
                    "parameters": [task_id_param],
                    "responses": {"200": html_response, "404": error_response},
                }
            },
            "/v1/tasks": {
                "get": {
                    "tags": ["tasks"],
                    "summary": "List tasks",
                    "parameters": [
                        query_param("status", {"type": "string"}, "Filter by task status."),
                        query_param("due", {"type": "boolean"}, "Return only due tasks when true."),
                        query_param("limit", {"type": "integer", "default": 50}, "Maximum tasks to return."),
                        query_param("kind", {"type": "string"}, "Filter by task_kind."),
                        query_param(
                            "controller_status",
                            {"type": "string"},
                            "Filter by automation/controller status.",
                        ),
                        query_param("source_principal_id", {"type": "string"}, "Filter by source principal id."),
                        query_param("target_principal_id", {"type": "string"}, "Filter by target principal id."),
                        query_param("harness_id", {"type": "string"}, "Filter by harness profile id."),
                        query_param("parent_task_id", {"type": "string"}, "Filter by parent task id."),
                    ],
                    "responses": {"200": ok_response, "400": error_response},
                },
                "post": {
                    "tags": ["tasks"],
                    "summary": "Create a task",
                    "security": write_security,
                    "requestBody": json_body("#/components/schemas/TaskInput"),
                    "responses": {"201": ok_response, "401": error_response, "400": error_response},
                },
            },
            "/v1/tasks/due": {
                "get": {
                    "tags": ["tasks"],
                    "summary": "List due tasks",
                    "parameters": [query_param("limit", {"type": "integer", "default": 50}, "Maximum tasks.")],
                    "responses": {"200": ok_response, "400": error_response},
                }
            },
            "/v1/tasks/tree": {
                "get": {
                    "tags": ["tasks"],
                    "summary": "List parent/child task hierarchy",
                    "parameters": [
                        query_param("task_id", {"type": "string"}, "Optional root task id."),
                        query_param("limit", {"type": "integer", "default": 200}, "Maximum nodes."),
                    ],
                    "responses": {"200": ok_response, "400": error_response},
                }
            },
            "/v1/registry/status": {
                "get": {
                    "tags": ["registry"],
                    "summary": "Show workspace registry and task binding status",
                    "responses": {"200": ok_response, "400": error_response},
                }
            },
            "/v1/workspaces": {
                "get": {
                    "tags": ["registry"],
                    "summary": "List registered workspaces",
                    "parameters": [query_param("status", {"type": "string"}, "Filter by registration status.")],
                    "responses": {"200": ok_response, "400": error_response},
                }
            },
            "/v1/workspaces/{identifier}": {
                "get": {
                    "tags": ["registry"],
                    "summary": "Get a workspace registry record",
                    "parameters": [registry_identifier_param],
                    "responses": {"200": ok_response, "404": error_response},
                }
            },
            "/v1/principals": {
                "get": {
                    "tags": ["registry"],
                    "summary": "List registered human, agent, and service principals",
                    "parameters": [
                        query_param("active_only", {"type": "boolean"}, "Return active principals only."),
                    ],
                    "responses": {"200": ok_response, "400": error_response},
                }
            },
            "/v1/principals/{identifier}": {
                "get": {
                    "tags": ["registry"],
                    "summary": "Get a human, agent, or service principal registry record",
                    "parameters": [registry_identifier_param],
                    "responses": {"200": ok_response, "404": error_response},
                }
            },
            "/v1/harnesses": {
                "get": {
                    "tags": ["registry"],
                    "summary": "List harness profiles for the current workspace",
                    "responses": {"200": ok_response, "400": error_response},
                }
            },
            "/v1/harnesses/{identifier}": {
                "get": {
                    "tags": ["registry"],
                    "summary": "Get a harness profile registry record",
                    "parameters": [registry_identifier_param],
                    "responses": {"200": ok_response, "404": error_response},
                }
            },
            "/v1/agents": {
                "get": {
                    "tags": ["registry"],
                    "summary": "List active agent runtimes for the current workspace",
                    "parameters": [
                        query_param("active_only", {"type": "boolean"}, "Return agents with a live lease only."),
                        query_param("role", {"type": "string"}, "Filter by role."),
                    ],
                    "responses": {"200": ok_response, "400": error_response},
                }
            },
            "/v1/agents/register": {
                "post": {
                    "tags": ["registry"],
                    "summary": "Register an active agent runtime",
                    "security": write_security,
                    "requestBody": json_body("#/components/schemas/AgentRuntimeInput"),
                    "responses": {"201": ok_response, "401": error_response, "400": error_response},
                }
            },
            "/v1/agents/heartbeat": {
                "post": {
                    "tags": ["registry"],
                    "summary": "Update an active agent heartbeat",
                    "security": write_security,
                    "requestBody": json_body("#/components/schemas/AgentHeartbeatInput"),
                    "responses": {"200": ok_response, "401": error_response, "400": error_response},
                }
            },
            "/v1/orchestrator/run-once": {
                "post": {
                    "tags": ["orchestrator"],
                    "summary": "Assign unassigned work to active capable agents",
                    "security": write_security,
                    "requestBody": json_body("#/components/schemas/OrchestratorRunInput"),
                    "responses": {"200": ok_response, "401": error_response, "400": error_response},
                }
            },
            "/v1/runner/run-once": {
                "post": {
                    "tags": ["runner"],
                    "summary": "Run one policy-aware harness runner pass",
                    "security": write_security,
                    "requestBody": json_body("#/components/schemas/RunnerRunInput"),
                    "responses": {"200": ok_response, "401": error_response, "400": error_response},
                }
            },
            "/v1/tasks/claim-next": {
                "post": {
                    "tags": ["agent"],
                    "summary": "Claim the next eligible task for an agent",
                    "security": write_security,
                    "requestBody": json_body("#/components/schemas/ClaimNextInput"),
                    "responses": {"200": ok_response, "401": error_response, "400": error_response},
                }
            },
            "/v1/tasks/{task_id}/claim": {
                "post": {
                    "tags": ["agent"],
                    "summary": "Claim the selected task for an agent",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/ClaimTaskInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}": {
                "get": {
                    "tags": ["tasks"],
                    "summary": "Get a task",
                    "parameters": [task_id_param],
                    "responses": {"200": ok_response, "404": error_response},
                },
                "patch": {
                    "tags": ["tasks"],
                    "summary": "Update a task",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/TaskUpdate"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                },
            },
            "/v1/tasks/{task_id}/ack": {
                "post": {
                    "tags": ["tasks"],
                    "summary": "Acknowledge a task",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/approve": {
                "post": {
                    "tags": ["governance"],
                    "summary": "Approve a task for runner execution",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/ApprovalDecisionInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/review-gate": {
                "post": {
                    "tags": ["governance"],
                    "summary": "Create or return a human review-gate task",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/ReviewGateRequestInput"),
                    "responses": {"201": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/review-gate-decision": {
                "post": {
                    "tags": ["governance"],
                    "summary": "Apply a review-gate decision to the gate and subject task",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/ReviewGateDecisionInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/delivery-dry-run": {
                "post": {
                    "tags": ["governance"],
                    "summary": "Record an external delivery request without sending anything",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/DeliveryDryRunInput"),
                    "responses": {"200": ok_response, "400": error_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/reject": {
                "post": {
                    "tags": ["governance"],
                    "summary": "Reject a task until it is revised",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/ApprovalDecisionInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/request-changes": {
                "post": {
                    "tags": ["governance"],
                    "summary": "Request task changes before execution",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/ApprovalDecisionInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/stop": {
                "post": {
                    "tags": ["governance"],
                    "summary": "Request a running or assigned task to stop",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/StopRequestInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/complete": {
                "post": {
                    "tags": ["tasks"],
                    "summary": "Complete a task",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/snooze": {
                "post": {
                    "tags": ["tasks"],
                    "summary": "Snooze a task",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/SnoozeInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/release": {
                "post": {
                    "tags": ["agent"],
                    "summary": "Release an agent claim",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/ReleaseInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/heartbeat": {
                "post": {
                    "tags": ["agent"],
                    "summary": "Extend an agent claim lease",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/HeartbeatInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/progress": {
                "post": {
                    "tags": ["agent"],
                    "summary": "Append agent progress to a task",
                    "security": write_security,
                    "parameters": [task_id_param],
                    "requestBody": json_body("#/components/schemas/ProgressInput"),
                    "responses": {"200": ok_response, "401": error_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/context-pack": {
                "get": {
                    "tags": ["agent"],
                    "summary": "Get the resume context pack for a task",
                    "parameters": [task_id_param],
                    "responses": {"200": ok_response, "404": error_response},
                }
            },
            "/v1/tasks/{task_id}/origin": {
                "get": {
                    "tags": ["agent"],
                    "summary": "Fetch source workspace task detail for a hub task",
                    "parameters": [task_id_param, source_db_param],
                    "responses": {"200": ok_response, "404": error_response},
                }
            },
            "/v1/automations": {
                "get": {
                    "tags": ["automations"],
                    "summary": "List automation definitions",
                    "parameters": [
                        query_param("status", {"type": "string"}, "Filter by controller status."),
                        query_param("include_runs", {"type": "boolean"}, "Include workflow_run children."),
                        query_param("limit", {"type": "integer", "default": 50}, "Maximum definitions."),
                    ],
                    "responses": {"200": ok_response, "400": error_response},
                },
                "post": {
                    "tags": ["automations"],
                    "summary": "Register an automation definition",
                    "security": write_security,
                    "requestBody": json_body("#/components/schemas/AutomationInput"),
                    "responses": {"201": ok_response, "401": error_response, "400": error_response},
                },
            },
        },
        "components": {
            "securitySchemes": {
                "TaskMemoryHubToken": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Task-Memory-Hub-Token",
                },
                "TaskMemoryHubBearer": {
                    "type": "http",
                    "scheme": "bearer",
                },
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                    "required": ["error"],
                },
                "TaskInput": {
                    "type": "object",
                    "required": ["title"],
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "next_action": {"type": "string"},
                        "detail_md": {"type": "string"},
                        "priority": {"type": "string", "default": "normal"},
                        "status": {"type": "string", "default": "scheduled"},
                        "due_at": {"type": "string", "format": "date-time"},
                        "rank": {"type": "integer"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "task_kind": {"type": "string", "default": "action"},
                        "execution_mode": {"type": "string", "default": "manual"},
                        "schedule_kind": {"type": "string", "default": "none"},
                        "source_agent": {"type": "string", "default": "api"},
                        "source_workspace_id": {"type": "string"},
                        "target_workspace_id": {"type": "string"},
                        "source_principal_id": {"type": "string"},
                        "target_principal_id": {"type": "string"},
                        "proposed_by_principal_id": {"type": "string"},
                        "approved_by_principal_id": {"type": "string"},
                        "assigned_by_principal_id": {"type": "string"},
                        "harness_id": {"type": "string"},
                        "parent_task_id": {"type": "string"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "idempotency_key": {"type": "string"},
                    },
                },
                "TaskUpdate": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "next_action": {"type": "string"},
                        "detail_md": {"type": "string"},
                        "priority": {"type": "string"},
                        "status": {"type": "string"},
                        "due_at": {"type": "string", "format": "date-time"},
                        "rank": {"type": "integer"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "controller_status": {"type": "string"},
                    },
                },
                "SnoozeInput": {
                    "type": "object",
                    "properties": {
                        "until": {"type": "string", "format": "date-time"},
                        "duration": {"type": "string", "example": "1d"},
                    },
                },
                "ClaimNextInput": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "default": "api"},
                        "lease_seconds": {"type": "integer", "default": 1800},
                        "workspace": {"type": "string"},
                        "include_not_due": {"type": "boolean", "default": False},
                    },
                },
                "ClaimTaskInput": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "default": "api"},
                        "target_principal_id": {"type": "string"},
                        "lease_seconds": {"type": "integer", "default": 1800},
                    },
                },
                "ReleaseInput": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "next_status": {"type": "string", "default": "acknowledged"},
                    },
                },
                "HeartbeatInput": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "default": "api"},
                        "lease_seconds": {"type": "integer", "default": 1800},
                    },
                },
                "ProgressInput": {
                    "type": "object",
                    "required": ["message"],
                    "properties": {
                        "message": {"type": "string"},
                        "owner": {"type": "string", "default": "api"},
                    },
                },
                "ApprovalDecisionInput": {
                    "type": "object",
                    "properties": {
                        "by": {"type": "string", "default": "owner"},
                        "principal_type": {"type": "string", "default": "human"},
                        "approver_principal_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
                "ReviewGateRequestInput": {
                    "type": "object",
                    "properties": {
                        "by": {"type": "string", "default": "owner"},
                        "reason": {"type": "string"},
                        "gate_type": {"type": "string", "default": "pre_execution"},
                        "reviewer_principal_id": {"type": "string"},
                    },
                },
                "ReviewGateDecisionInput": {
                    "type": "object",
                    "required": ["decision"],
                    "properties": {
                        "decision": {
                            "type": "string",
                            "enum": ["approved", "rejected", "changes_requested", "approve", "reject", "request-changes"],
                        },
                        "by": {"type": "string", "default": "owner"},
                        "principal_type": {"type": "string", "default": "human"},
                        "approver_principal_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
                "DeliveryDryRunInput": {
                    "type": "object",
                    "properties": {
                        "by": {"type": "string", "default": "owner"},
                        "channel": {"type": "string", "example": "email"},
                        "recipient_ref": {"type": "string", "example": "principal:owner"},
                        "include_artifacts": {"type": "array", "items": {"type": "string"}},
                        "requires_review": {"type": "boolean", "default": True},
                        "reason": {"type": "string"},
                    },
                },
                "StopRequestInput": {
                    "type": "object",
                    "properties": {
                        "by": {"type": "string", "default": "owner"},
                        "reason": {"type": "string"},
                    },
                },
                "AutomationInput": {
                    "allOf": [{"$ref": "#/components/schemas/TaskInput"}],
                    "properties": {
                        "task_kind": {"type": "string", "default": "automation"},
                        "controller_status": {"type": "string", "default": "active"},
                        "schedule_json": {"type": "object"},
                        "execution_contract_json": {"type": "object"},
                        "artifact_contract_json": {"type": "object"},
                    },
                },
                "AgentRuntimeInput": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string", "default": "worker"},
                        "status": {"type": "string", "default": "active"},
                        "capabilities": {"type": "array", "items": {"type": "string"}},
                        "default_harness_id": {"type": "string"},
                        "max_active_tasks": {"type": "integer", "default": 1},
                        "lease_seconds": {"type": "integer", "default": 600},
                        "notes": {"type": "string"},
                    },
                },
                "AgentHeartbeatInput": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "principal_id": {"type": "string"},
                        "status": {"type": "string", "default": "active"},
                        "current_task_id": {"type": "string"},
                        "lease_seconds": {"type": "integer", "default": 600},
                        "notes": {"type": "string"},
                    },
                },
                "OrchestratorRunInput": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "default": "orchestrator"},
                        "include_not_due": {"type": "boolean", "default": True},
                        "limit": {"type": "integer", "default": 10},
                        "default_harness": {"type": "string"},
                        "dry_run": {"type": "boolean", "default": False},
                    },
                },
                "RunnerRunInput": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "default": "web-ui-runner"},
                        "backend": {"type": "string", "enum": ["dry_run", "deepagents_cli", "script_ref"], "default": "dry_run"},
                        "backend_command": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "default": 120},
                        "script_allowlist_path": {"type": "string"},
                        "capabilities": {"type": "array", "items": {"type": "string"}},
                        "harness": {"type": "string"},
                        "task_id": {"type": "string"},
                        "include_not_due": {"type": "boolean", "default": True},
                        "run_orchestrator": {"type": "boolean", "default": False},
                    },
                },
            },
        },
    }


def render_api_docs() -> str:
    endpoints = [
        ("GET", "/health/live", "프로세스 liveness 확인", "없음"),
        ("GET", "/health/ready", "DB 초기화 이후 readiness 확인", "없음"),
        ("GET", "/", "최소 작업 목록 UI", "없음"),
        ("GET", "/docs", "Swagger UI", "없음"),
        ("GET", "/docs/reference", "표 형식 API reference", "없음"),
        ("GET", "/quick-add", "브라우저 quick-add form", "없음"),
        ("GET", "/openapi.json", "OpenAPI 3.0.3 JSON", "없음"),
        ("GET", "/v1/tasks", "작업 목록 조회. status, due, kind, controller_status, limit 지원", "없음"),
        ("POST", "/v1/tasks", "작업 생성. Idempotency-Key header 지원", "write token"),
        ("GET", "/v1/tasks/due", "due 작업 조회", "없음"),
        ("GET", "/v1/tasks/tree", "parent/child 작업 계층 조회", "없음"),
        ("GET", "/v1/tasks/{task_id}", "작업 상세 조회", "없음"),
        ("PATCH", "/v1/tasks/{task_id}", "작업 수정", "write token"),
        ("POST", "/v1/tasks/{task_id}/ack", "작업 확인 처리", "write token"),
        ("POST", "/v1/tasks/{task_id}/approve", "사람 승인 기록 및 runner 실행 허용", "write token"),
        ("POST", "/v1/tasks/{task_id}/review-gate", "선택 작업에 대한 review gate task 생성", "write token"),
        ("POST", "/v1/tasks/{task_id}/review-gate-decision", "review gate 결정으로 원 작업 승인/차단 반영", "write token"),
        ("POST", "/v1/tasks/{task_id}/delivery-dry-run", "외부 전달 요청을 실제 발송 없이 이벤트로 검증", "write token"),
        ("POST", "/v1/tasks/{task_id}/reject", "사람 거절 기록 및 controller 차단", "write token"),
        ("POST", "/v1/tasks/{task_id}/request-changes", "수정요청 기록 및 controller 차단", "write token"),
        ("POST", "/v1/tasks/{task_id}/stop", "진행 중/배정 작업 중지 요청", "write token"),
        ("POST", "/v1/tasks/{task_id}/snooze", "작업 snooze 처리", "write token"),
        ("POST", "/v1/tasks/{task_id}/complete", "작업 완료 처리", "write token"),
        ("POST", "/v1/tasks/claim-next", "agent가 다음 실행 가능 작업 claim", "write token"),
        ("POST", "/v1/tasks/{task_id}/claim", "선택한 작업을 특정 agent가 claim/start", "write token"),
        ("POST", "/v1/tasks/{task_id}/release", "agent claim release", "write token"),
        ("POST", "/v1/tasks/{task_id}/heartbeat", "agent claim lease 연장", "write token"),
        ("POST", "/v1/tasks/{task_id}/progress", "agent 진행 로그 추가", "write token"),
        ("GET", "/v1/tasks/{task_id}/context-pack", "agent resume context pack 조회", "없음"),
        ("GET", "/v1/tasks/{task_id}/origin", "hub task에서 원본 workspace task 역추적", "없음"),
        ("GET", "/v1/registry/status", "현재 workspace registry와 task binding 상태 조회", "없음"),
        ("GET", "/v1/workspaces", "등록된 workspace 조회", "없음"),
        ("GET", "/v1/workspaces/{identifier}", "workspace registry record 조회", "없음"),
        ("GET", "/v1/principals", "등록된 human/agent/service principal 조회", "없음"),
        ("GET", "/v1/principals/{identifier}", "principal registry record 조회", "없음"),
        ("GET", "/v1/harnesses", "현재 workspace harness profile 조회", "없음"),
        ("GET", "/v1/harnesses/{identifier}", "harness registry record 조회", "없음"),
        ("GET", "/v1/agents", "현재 workspace active agent runtime 조회", "없음"),
        ("POST", "/v1/agents/register", "agent runtime 등록", "write token"),
        ("POST", "/v1/agents/heartbeat", "agent heartbeat 갱신", "write token"),
        ("POST", "/v1/orchestrator/run-once", "unassigned task를 active agent에게 배정", "write token"),
        ("POST", "/v1/runner/run-once", "정책 확인 후 배정/선택 task를 runner로 실행", "write token"),
        ("GET", "/v1/automations", "automation definition 조회", "없음"),
        ("POST", "/v1/automations", "automation definition 등록", "write token"),
    ]
    rows = "\n".join(
        f"""
        <tr>
          <td><code>{_esc(method)}</code></td>
          <td><code>{_esc(path)}</code></td>
          <td>{_esc(description)}</td>
          <td>{_esc(auth)}</td>
        </tr>
        """
        for method, path, description, auth in endpoints
    )
    create_example = """$token = tmh api-token
Invoke-RestMethod http://127.0.0.1:8787/v1/tasks
Invoke-RestMethod http://127.0.0.1:8787/v1/tasks `
  -Method Post `
  -Headers @{"X-Task-Memory-Hub-Token"=$token; "Idempotency-Key"="docs-smoke-1"} `
  -ContentType "application/json" `
  -Body (@{
    title="API docs 스모크 테스트"
    next_action="http://127.0.0.1:8787/docs 확인"
    priority="normal"
  } | ConvertTo-Json)"""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>API Docs - {_esc(APP_NAME)}</title>
  <link rel="stylesheet" href="{_esc(_static_asset_url('app.css'))}">
</head>
<body>
  <main class="shell">
  <header class="doc-header">
    <h1>{_esc(APP_NAME)} API Docs</h1>
    <nav><a href="/">Tasks</a> · <a href="/docs">Swagger UI</a> · <a href="/quick-add">Quick Add</a> · <a href="/openapi.json">OpenAPI JSON</a></nav>
  </header>
  <div class="note">
    현재 서버는 FastAPI가 아니라 Python 표준 라이브러리 <code>ThreadingHTTPServer</code> 기반 loopback 서버다.
    Swagger UI는 <code>/docs</code>, 표 형식 reference는 <code>/docs/reference</code>, machine-readable spec은 <code>/openapi.json</code>에서 제공한다.
  </div>
  <h2>Auth</h2>
  <p>조회 요청은 토큰 없이 가능하다. 쓰기 요청은 <code>tmh api-token</code>으로 확인한 값을
  <code>X-Task-Memory-Hub-Token</code> header 또는 <code>Authorization: Bearer</code> header에 넣는다.</p>
  <h2>Endpoints</h2>
  <div class="doc-table-wrap"><table>
    <thead><tr><th>Method</th><th>Path</th><th>Purpose</th><th>Auth</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
  <h2>PowerShell Smoke</h2>
  <pre>{_esc(create_example)}</pre>
  </main>
</body>
</html>"""


def render_swagger_docs() -> str:
    if get_swagger_ui_root() is None:
        return render_api_docs()
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Swagger UI - {_esc(APP_NAME)}</title>
  <link rel="stylesheet" href="/docs/swagger-ui/swagger-ui.css">
  <style>
    body {{ margin: 0; background: #f8fafc; }}
    .topbar {{ display: none; }}
    .tmh-docbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 16px;
      background: #111827;
      color: #f9fafb;
      font-family: Segoe UI, system-ui, sans-serif;
      font-size: 14px;
    }}
    .tmh-docbar a {{ color: #99f6e4; text-decoration: none; }}
    .tmh-docbar span {{ font-weight: 600; }}
  </style>
</head>
<body>
  <div class="tmh-docbar">
    <span>{_esc(APP_NAME)} Swagger UI</span>
    <nav>
      <a href="/">Tasks</a> ·
      <a href="/quick-add">Quick Add</a> ·
      <a href="/docs/reference">Reference</a> ·
      <a href="/openapi.json">OpenAPI JSON</a>
    </nav>
  </div>
  <div id="swagger-ui"></div>
  <script src="/docs/swagger-ui/swagger-ui-bundle.js"></script>
  <script src="/docs/swagger-ui/swagger-ui-standalone-preset.js"></script>
  <script>
    window.onload = function() {{
      window.ui = SwaggerUIBundle({{
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIStandalonePreset
        ],
        plugins: [
          SwaggerUIBundle.plugins.DownloadUrl
        ],
        layout: "StandaloneLayout",
        persistAuthorization: true
      }});
    }};
  </script>
</body>
</html>"""


def render_index(tasks: list[dict]) -> str:
    row_parts = []
    for index, task in enumerate(tasks):
        task_id = task["task_id"]
        detail_id = f"task-detail-{index}"
        due_value = task.get("snooze_until") or task.get("due_at") or ""
        row_parts.append(
            f"""
        <tr class="task-main-row">
          <td class="task-expand-cell">
            <button class="task-toggle" type="button" data-row-toggle="{_esc(detail_id)}" data-task-id="{_esc(task_id)}" aria-expanded="false" aria-controls="{_esc(detail_id)}" aria-label="작업 요약 펼치기" title="작업 요약 펼치기">&gt;</button>
          </td>
          <td class="task-title-cell"><a href="/tasks/{_esc(task_id)}">{_esc(task['title'])}</a></td>
          <td>{_esc(task['status'])}</td>
          <td>{_esc(task['priority'])}</td>
          <td>{_esc(task.get('rank') or '')}</td>
          <td class="mono">{_esc(_short_datetime(due_value))}</td>
          <td class="mono">{_esc(_short_datetime(task.get('created_at')))}</td>
          <td>{_esc(task.get('next_action') or '')}</td>
        </tr>
        <tr id="{_esc(detail_id)}" class="task-detail-row" hidden>
          <td></td>
          <td colspan="7">
            <dl class="task-row-detail" data-detail-content>
              <div class="task-row-field"><dt>Loading</dt><dd>작업 요약을 불러오는 중...</dd></div>
            </dl>
            <a class="task-detail-link" href="/tasks/{_esc(task_id)}">상세 화면 열기</a>
          </td>
        </tr>
        """
        )
    rows = "\n".join(row_parts)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(APP_NAME)}</title>
  <link rel="stylesheet" href="{_esc(_static_asset_url('app.css'))}">
</head>
<body>
  <main class="shell">
  <header class="doc-header">
    <h1>{_esc(APP_NAME)}</h1>
    <nav><a class="button primary" href="/quick-add">Quick Add</a> <a class="button" href="/docs">API Docs</a></nav>
  </header>
  <section class="summary-grid">
    <div class="metric"><dt>Tasks</dt><dd>{len(tasks)}</dd></div>
    <div class="metric"><dt>Surface</dt><dd>CLI / API / MCP / Web</dd></div>
    <div class="metric"><dt>API</dt><dd><code>/v1/tasks</code></dd></div>
  </section>
  <div class="task-table-wrap"><table class="task-table">
    <thead><tr><th aria-label="Expand"></th><th>Title</th><th>Status</th><th>Priority</th><th>Rank</th><th>Due</th><th>Created</th><th>Next Action</th></tr></thead>
    <tbody>{rows or '<tr><td colspan="8">No tasks.</td></tr>'}</tbody>
  </table></div>
  </main>
  <script src="{_esc(_static_asset_url('app.js'))}"></script>
</body>
</html>"""


def render_quick_add(write_token: str = "") -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Quick Add - {_esc(APP_NAME)}</title>
  <link rel="stylesheet" href="{_esc(_static_asset_url('app.css'))}">
</head>
<body>
  <main class="shell narrow">
  <nav class="topbar"><a href="/">Back to tasks</a><span>Quick Add</span></nav>
  <header class="doc-header">
    <h1>Quick Add</h1>
    <p class="subtle">사람이 바로 등록하는 작업은 owner principal과 현재 workspace에 기본 바인딩된다.</p>
  </header>
  <form class="form-grid panel" method="post" action="/quick-add">
    <input type="hidden" name="token" value="{_esc(write_token)}">
    <label>Title
      <input name="title" required autofocus>
    </label>
    <label>Summary
      <textarea name="summary"></textarea>
    </label>
    <label>Next Action
      <textarea name="next_action"></textarea>
    </label>
    <div class="row">
      <label>Priority
        <select name="priority">
          <option value="normal">normal</option>
          <option value="high">high</option>
          <option value="urgent">urgent</option>
          <option value="low">low</option>
        </select>
      </label>
      <label>Due
        <input name="due_at" placeholder="2026-05-02 08:00">
      </label>
    </div>
    <label>Tags
      <input name="tags" placeholder="todo, everywhere">
    </label>
    <label>Detail
      <textarea name="detail_md"></textarea>
    </label>
    <button class="primary" type="submit">Add</button>
  </form>
  </main>
</body>
</html>"""


def render_task(task: dict, detail: dict, write_token: str = "") -> str:
    source_workspace = detail.get("source_workspace")
    target_workspace = detail.get("target_workspace")
    source_principal = detail.get("source_principal")
    target_principal = detail.get("target_principal")
    assigned_by = detail.get("assigned_by")
    proposed_by = detail.get("proposed_by")
    approved_by = detail.get("approved_by")
    harness = detail.get("harness")
    runtime = detail.get("agent_runtime")
    due_value = task.get("snooze_until") or task.get("due_at") or ""
    repo_value = ""
    if source_workspace:
        repo_remote = source_workspace.get("repo_remote") or ""
        repo_branch = source_workspace.get("repo_branch") or ""
        repo_value = " @ ".join(item for item in [repo_remote, repo_branch] if item)
    runtime_label = ""
    if runtime:
        runtime_label = (
            f"{runtime.get('agent_name', '')} / {runtime.get('role', '')} / "
            f"{runtime.get('status', '')} / lease {runtime.get('lease_until', '')}"
        )
    claim_owner = task.get("agent_claim_owner") or ""
    target_principal_id = task.get("target_principal_id") or ""
    target_agent_name = detail.get("target_agent_name") or ""
    required_capabilities = task.get("execution_contract", {}).get("required_capabilities") or []
    can_claim = task.get("status") not in {"completed", "cancelled", "archived"} and bool(
        target_principal_id or claim_owner
    )
    claim_button = (
        '<button class="primary" data-ui-action="claim">Agent Claim</button>'
        if can_claim
        else ""
    )
    release_button = (
        '<button data-ui-action="release">진행 중지</button>' if task.get("status") == "in_progress" else ""
    )
    activate_button = (
        '<button data-ui-action="activate-agent">Agent 활성화</button>'
        if detail.get("agent_activation_possible")
        else ""
    )
    heartbeat_button = (
        '<button data-ui-action="heartbeat-agent">Agent Heartbeat</button>' if target_principal_id or runtime else ""
    )
    runner_button = '<button class="primary" data-ui-action="runner-dry-run">Runner Dry-run</button>'
    status_fields = [
        ("Status", task.get("status"), "mono"),
        ("Priority / Rank", f"{task.get('priority', '')} / {task.get('rank') or '-'}", "mono"),
        ("Due", due_value, "mono"),
        ("Snooze", task.get("snooze_until"), "mono"),
        ("Completed", task.get("completed_at"), "mono"),
        ("Task kind", task.get("task_kind"), "mono"),
        ("Execution mode", task.get("execution_mode"), "mono"),
        ("Schedule kind", task.get("schedule_kind"), "mono"),
        ("Controller", task.get("controller_status"), "mono"),
        ("Routing", task.get("routing_status"), "mono"),
    ]
    origin_fields = [
        (
            "Source workspace",
            _workspace_ref(source_workspace, task.get("source_workspace_id") or task.get("source_workspace") or ""),
            "html",
        ),
        ("Source folder", source_workspace.get("canonical_path") if source_workspace else "", "path"),
        ("Source repo", repo_value, "path"),
        ("Submitted by", _principal_ref(source_principal, task.get("source_principal_id") or ""), "html"),
        ("Source agent", task.get("source_agent"), "mono"),
        ("Proposed by", _principal_ref(proposed_by, task.get("proposed_by_principal_id") or ""), "html"),
        ("Approved by", _principal_ref(approved_by, task.get("approved_by_principal_id") or ""), "html"),
        ("Origin task", _task_id_ref(task.get("origin_task_id") or ""), "html"),
        ("Hub task", _task_id_ref(task.get("hub_task_id") or ""), "html"),
        ("Created / Updated", f"{task.get('created_at', '')} / {task.get('updated_at', '')}", "mono"),
    ]
    agent_fields = [
        (
            "Target workspace",
            _workspace_ref(target_workspace, task.get("target_workspace_id") or detail.get("runtime_workspace_id") or ""),
            "html",
        ),
        ("Target principal", _principal_ref(target_principal, target_principal_id), "html"),
        ("Assigned by", _principal_ref(assigned_by, task.get("assigned_by_principal_id") or ""), "html"),
        ("Harness", _harness_ref(harness, task.get("harness_id") or ""), "html"),
        ("Runtime", runtime_label, "path"),
        ("Runtime live", "yes" if detail.get("agent_runtime_live") else "no", "mono"),
        ("Claim owner", claim_owner, "mono"),
        ("Claim status", task.get("agent_claim_status"), "mono"),
        ("Claim until", task.get("agent_claim_until"), "mono"),
        ("Required capabilities", ", ".join(required_capabilities), "path"),
    ]
    hierarchy_fields = [
        ("Parent task", task.get("parent_task_id"), "mono"),
        ("Depends on", ", ".join(task.get("depends_on") or []), "path"),
        ("Automation", task.get("automation_id"), "mono"),
        ("Last human update", task.get("last_human_update_at"), "mono"),
        ("Last agent update", task.get("last_agent_update_at"), "mono"),
        ("Source file", task.get("source_file_path"), "path"),
        ("File conflict", task.get("source_sync_status"), "mono"),
    ]
    contract_payload = {
        "execution_contract": task.get("execution_contract") or {},
        "schedule": task.get("schedule") or {},
        "artifact_contract": task.get("artifact_contract") or {},
        "ai_context_pack": task.get("ai_context_pack") or {},
    }
    detail_body = (
        f"<pre>{_esc(task.get('detail_md'))}</pre>"
        if task.get("detail_md")
        else '<p class="muted">세부 설명 없음.</p>'
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(task['title'])} - {_esc(APP_NAME)}</title>
  <link rel="stylesheet" href="{_esc(_static_asset_url('app.css'))}">
</head>
<body>
  <main class="shell">
    <nav class="topbar">
      <span><a href="/">Tasks</a> · <a href="/quick-add">Quick Add</a> · <a href="/docs">API Docs</a></span>
      <span>{_task_id_ref(task['task_id'])}</span>
    </nav>
    <section class="heading">
      <div>
        <h1>{_esc(task['title'])}</h1>
        <div class="subtle">{_esc(task.get('next_action') or '')}</div>
      </div>
      <a href="/tasks/{_esc(task['task_id'])}">Refresh</a>
    </section>
    <section class="actions">
      <button data-task-action="ack">Ack</button>
      <button data-governance-action="approve">승인</button>
      <button data-governance-action="request-changes">변경요청</button>
      <button class="danger" data-governance-action="reject">거절</button>
      <button class="danger" data-governance-action="stop">중지요청</button>
      <button data-task-action="snooze" data-duration="1d">Snooze 1d</button>
      <button class="primary" data-task-action="complete">Done</button>
      {claim_button}
      {release_button}
      {activate_button}
      {heartbeat_button}
      {runner_button}
      <button data-ui-action="request-review-gate">Review Gate</button>
      <button data-ui-action="delivery-dry-run">Delivery Dry-run</button>
      <button data-ui-action="orchestrator-run">Orchestrator Run</button>
      <span id="result" class="result" aria-live="polite"></span>
    </section>
    <dl class="status-strip">
      <div class="metric"><dt>Status</dt><dd>{_esc(task.get('status'))}</dd></div>
      <div class="metric"><dt>Due</dt><dd>{_esc(due_value or '-')}</dd></div>
      <div class="metric"><dt>Source</dt><dd>{_esc((source_workspace or {}).get('workspace_slug') or task.get('source_workspace') or '-')}</dd></div>
      <div class="metric"><dt>Target Agent</dt><dd>{_esc((target_principal or {}).get('display_name') or claim_owner or '-')}</dd></div>
      <div class="metric"><dt>Runtime</dt><dd>{_esc('live' if detail.get('agent_runtime_live') else 'inactive')}</dd></div>
    </dl>
    <div class="layout">
      <div class="stack">
        <section class="panel">
          <h2>작업 내용</h2>
          <div class="fields">
            {_field_grid([('Summary', task.get('summary'), ''), ('Next action', task.get('next_action'), '')])}
          </div>
          {detail_body}
        </section>
        <section class="panel">
          <h2>제출 / 출처</h2>
          <dl class="fields">{_field_grid(origin_fields)}</dl>
        </section>
        <section class="panel">
          <h2>이벤트 타임라인</h2>
          <table>
            <thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Payload</th></tr></thead>
            <tbody>{_event_rows(detail.get('events') or [])}</tbody>
          </table>
        </section>
      </div>
      <aside class="stack">
        <section class="panel">
          <h2>상태</h2>
          <dl class="fields">{_field_grid(status_fields)}</dl>
        </section>
        <section class="panel">
          <h2>Agent / Harness</h2>
          <dl class="fields">{_field_grid(agent_fields)}</dl>
        </section>
        <section class="panel">
          <h2>계층 / 파일</h2>
          <dl class="fields">{_field_grid(hierarchy_fields)}</dl>
        </section>
        <section class="panel">
          <h2>진행 로그 추가</h2>
          <textarea id="progressMessage" placeholder="현재 진행 상황"></textarea>
          <div class="actions"><button data-ui-action="append-progress">Progress</button></div>
        </section>
        <section class="panel">
          <h2>Contract / Context</h2>
          <pre>{_json_pre(contract_payload)}</pre>
        </section>
      </aside>
    </div>
  </main>
  <script id="tmh-task-config" type="application/json">{_json_script({
      "taskId": task["task_id"],
      "writeToken": write_token,
      "targetPrincipalId": target_principal_id,
      "targetAgentName": target_agent_name,
      "activeClaimOwner": claim_owner,
      "requiredCapabilities": required_capabilities,
      "defaultOwner": claim_owner or target_principal_id or "web-ui",
      "isReviewGate": task.get("task_kind") == "review_gate",
  })}</script>
  <script src="{_esc(_static_asset_url('task-detail.js'))}"></script>
</body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
