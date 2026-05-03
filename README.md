# Task Memory Hub

Task Memory Hub is a Windows-local, local-first task and alarm hub for humans and AI agents. It keeps tasks, due reminders, agent claims, approval decisions, runner events, and automation definitions in one durable database so CLI, Web UI, REST API, MCP clients, tray notifications, and file bridges can work from the same source of truth.

The current implementation is intentionally local-first. SQLite is the default runtime store, while the schema and service boundaries are kept portable for a future PostgreSQL backend.

## What Works Today

- `tmh` CLI for task create/list/update/ack/snooze/done/import/export.
- `tmh add --json-file <file>` for script-friendly task creation.
- Loopback REST API and Web UI through `tmh-web`.
- Swagger UI at `/docs`, static reference at `/docs/reference`, and OpenAPI JSON at `/openapi.json`.
- Local write token for POST/PATCH requests.
- STDIO MCP server through `tmh-mcp`.
- Markdown/JSON import/export bridge with conflict markers.
- SQLite worker scan, outbox enqueue, and notification attempt tracking.
- Windows tray/station launcher skeleton, installer scripts, and install bootstrap.
- Workspace/principal/harness/auth/network registry records.
- Authority-aware global hub push and approved pull.
- Agent runtime heartbeat, orchestrator assignment, claim/release/progress/done flow.
- Harness runner with `dry_run`, deterministic Deepagents CLI pilot, Cline MCP pilot checklist, and allowlisted `script_ref` backend.
- Human approval/rejection/change-request/stop-request events.
- Automation definition registration through CLI, REST, and MCP.

## Public Repository Scope

This repository should contain the reusable product code, public design docs, public progress summaries, and deterministic smoke scripts.

It should not track local runtime state or operator-private artifacts:

- `.tmh/`
- `.cline-test/`
- `.vscode/`
- local API tokens
- SQLite databases
- machine-specific handoff logs
- internal audit transcripts
- real webhook URLs, API keys, passwords, or secret values

See `docs/public-release-plan.md` for the current public-release checklist and clean-history caveat.

## Install

Recommended local install with tray, toast attempt, clickable shortcuts, startup registration, and immediate start:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-tmh.ps1 -RegisterHubStation -StartNow -DesktopShortcuts
```

Double-click wrapper:

```text
installers\TaskMemoryHub-Install.cmd
```

The installer creates a Start Menu folder named `Task Memory Hub` with shortcuts for:

- starting TMH Hub Station,
- opening the Web UI,
- opening Quick Add,
- opening API Docs,
- checking Hub Station status,
- stopping Hub Station.

Manual package install:

```powershell
python -m pip install -e ".[tray]"
```

If tray dependencies are not needed:

```powershell
python -m pip install -e .
```

## Quick Start

Create a task:

```powershell
tmh add "내일 아침 등산" --next "아침 7시에 출발 준비" --due "2026-05-04T07:00:00+09:00"
```

Create from JSON:

```powershell
tmh add --json-file .\examples\my-task.json
```

Run the Web UI/API:

```powershell
tmh-web --host 127.0.0.1 --port 8787
```

Open:

- `http://127.0.0.1:8787/`
- `http://127.0.0.1:8787/quick-add`
- `http://127.0.0.1:8787/docs`
- `http://127.0.0.1:8787/openapi.json`

Show the local write token:

```powershell
tmh api-token
```

Use the REST API:

```powershell
$token = tmh api-token
Invoke-RestMethod http://127.0.0.1:8787/v1/tasks
Invoke-RestMethod http://127.0.0.1:8787/v1/tasks `
  -Method Post `
  -Headers @{"X-Task-Memory-Hub-Token"=$token; "Idempotency-Key"="readme-api-smoke-1"} `
  -ContentType "application/json" `
  -Body (@{
    title="API 스모크 테스트"
    next_action="Web UI에서 생성 결과 확인"
    priority="normal"
  } | ConvertTo-Json)
```

Run a dry-run harness runner:

```powershell
tmh runner once --task-id <task_id> --backend dry_run --capability tmh-api --capability tmh-cli --json
```

## MCP

`tmh-mcp` exposes the same service layer over STDIO for Cline and other MCP-capable clients.

Project-local Cline smoke registration:

```powershell
cline mcp add --config .\.cline-test task-memory-hub .\scripts\tmh-mcp.cmd
```

Direct MCP smoke is covered by:

```powershell
.\scripts\test-cline-mcp-pilot.ps1
```

Do not modify a user's global Cline MCP settings unless explicitly requested.

## Architecture

TMH separates assignment, supervision, and execution:

| Layer | Responsibility |
|---|---|
| Task DB | Durable source of truth |
| CLI/API/MCP/File Bridge | Shared read/write surfaces |
| Worker | Due scan, outbox, retry, notification dispatch |
| Orchestrator | Assign tasks to active capable agents |
| Harness Runner | Claim tasks, enforce policy, supervise backend execution |
| Backend | Cline, Deepagents, Codex, allowlisted scripts, or future external systems |
| Web UI | Human-visible control plane for provenance, approval, stop, progress, and artifacts |

Script execution uses `script_ref` allowlists. Task prose, `next_action`, `detail_md`, and task-provided command strings must not be executed as shell commands.

## Documentation

- `task-memory-hub-설계명세.md`: original product/design specification.
- `DESIGN.md`: Web UI and operator-control-plane design guide.
- `AGENTS.md`: repository operating rules for coding agents.
- `docs/public-progress.md`: public implementation summary.
- `docs/public-release-plan.md`: public repository scope and cleanup plan.
- `docs/ci-necessity-review.md`: why the initial CI scope is intentionally small.
- `docs/web-ui-design-application.md`: how the DESIGN.md approach maps to TMH.
- `docs/web-ui-screen-guide.md`: Web UI list/detail screen meaning and drift guard.
- `docs/windows-install-standard.md`: Windows install, toast, startup, and future installer standard.
- `docs/agentic-workspace-control-plane.md`: workspace/principal/authority model.
- `docs/task-execution-contract-standard.md`: task kind, automation, workflow run, and execution contract model.
- `docs/harness-runner-governance-development-spec.md`: orchestrator/runner/backend governance design.
- `docs/review-gate-flow.md`: P5 human review-gate workflow.
- `docs/task-title-standard.md`: Korean-first task title standard.
- `docs/postgres-slow-track.md`: PostgreSQL migration preparation notes.

## Current Roadmap

P0 through P4 are implemented as local-first pilots:

- P0 dry-run harness runner.
- P1 approval and stop controls.
- P2 deterministic Deepagents backend pilot.
- P3 Cline MCP pilot checklist.
- P4 allowlisted script backend.

Next:

1. Web UI polish: make the control-plane inspector readable enough for daily use, not just a smoke-test page.
2. Installer hardening: move from PowerShell bootstrap to signed zip, then standalone executable packaging when lifecycle and upgrade behavior are stable.
3. External delivery pilots: Teams, OpenProject, email, and webhook after review gate, secret-ref, and capability policy are stable.
4. Live backend pilots: Cline IDE and on-prem Deepagents only after stop semantics, artifact reporting, and approval rules are stable.
5. PostgreSQL adapter: migrate after the local service boundary and concurrency model prove stable under multi-agent use.

## Security Notes

- The API binds to loopback by default and rejects non-loopback Host/Origin values.
- Write routes require a local token from `tmh api-token`.
- Auth profiles store secret references only, never secret values.
- Tasks should store thin manifests by default. Use IDs and origin lookup instead of copying excessive private context into hub records.
- External delivery, email send, webhook execution, and arbitrary script execution require explicit policy/capability references.

## License

MIT. See `LICENSE`.
