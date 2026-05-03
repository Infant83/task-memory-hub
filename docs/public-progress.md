# Public Progress Summary

Updated: 2026-05-03

## Goal

Task Memory Hub is a local-first task, alarm, and agent work-control hub. The intended source of truth is the task database, not in-memory timers, chat history, or ad hoc Markdown notes. Markdown and JSON remain supported as import/export and human-editable bridge formats.

## Implemented

### Core Runtime

- SQLite-backed task store and schema migration.
- Durable task events.
- Task reminders, notification jobs, and notification attempts.
- Local write token for REST writes.
- Loopback Host/Origin guard for browser-facing routes.

### User Surfaces

- CLI command surface through `tmh`.
- Loopback Web UI and REST API through `tmh-web`.
- Swagger UI, static API reference, and OpenAPI JSON.
- STDIO MCP server through `tmh-mcp`.
- Markdown/JSON task import/export.
- Windows tray/station launcher scripts, installer wrappers, install bootstrap, and Start Menu/Desktop shortcuts.

### Registry And Authority

- Workspace registry.
- Principal registry.
- Harness profile registry.
- Auth and network profile references.
- Registry-aware task add path.
- Global hub push profiles.
- Approved global pull path.
- Origin fetch path from hub task back to source workspace task.

### Agent Workflow

- Agent runtime registration and heartbeat.
- Orchestrator run-once assignment.
- Agent claim, release, heartbeat, progress, and done flows.
- Task detail page showing provenance, routing, runtime, harness, events, and control actions.

### Runner Governance

- Dry-run harness runner.
- Human approval, rejection, change-request, and stop-request events.
- P5 review-gate flow using durable `review_gate` child tasks.
- Deterministic Deepagents backend pilot path.
- Cline MCP on-prem pilot checklist and direct STDIO smoke script.
- Allowlisted `script_ref` backend. Raw task prose and task-provided command strings are not executable.

### Automation Model

- One-off tasks, automations, workflow runs, and review gates are modelled as distinct task kinds.
- CLI, REST, and MCP automation registration paths exist.
- Execution contracts separate required capabilities, policy hints, schedule, artifact contract, and context packet fields.

## Verified Smoke Coverage

- Python compile checks.
- CLI help and task creation paths.
- REST health, task CRUD, docs, and OpenAPI routes.
- Direct MCP tool-list and task operation checks.
- Registry/global hub push and origin-fetch behavior.
- Orchestrator assignment and agent claim/progress/done path.
- Runner dry-run event trail.
- Deterministic Deepagents CLI smoke path.
- Cline MCP pilot script without depending on user-global Cline settings.
- Script allowlist smoke: allowlisted command completes, unknown command ref blocks.

## Remaining Gaps

- Public `main` should remain on the clean-history snapshot. If the repository was public before the rewrite, earlier exposure cannot be retroactively ruled out.
- Web UI needs continued polish for daily operations, especially dense task lists, filters, and operator status panels.
- External writes such as Teams, OpenProject, email, and webhooks still need backend-specific pilots, but the P5 review-gate control point now exists.
- Live Cline and live Deepagents runs need on-prem authentication/runtime validation.
- PostgreSQL remains a slow-track target, not the current default backend.
- Backup/restore, diagnostics, standalone installer packaging, and tray UX need more end-user hardening.

## Drift Guard

The project must remain a source-of-truth task hub and human-visible control plane. Cline, Deepagents, Codex, and scripts are clients or execution backends. They must not become hidden authorities that bypass DB events, approval state, provenance, or stop semantics.
