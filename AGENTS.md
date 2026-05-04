# Task Memory Hub Agent Guide

## Scope

This file applies to this repository root.

The project goal is to build a Windows 11 local-first alarm and task-memory hub that lets humans and AI agents create, update, acknowledge, snooze, complete, and resume work from the same durable task source.

## Source Of Truth

- Primary product/design reference: `task-memory-hub-설계명세.md`.
- Product README and implementation plan: `README.md`.
- Integrated HTML manual: `docs/manual.html`.
- Web UI design contract: `DESIGN.md`.
- Web UI screen guide: `docs/web-ui-screen-guide.md`.
- Windows install standard: `docs/windows-install-standard.md`.
- Public progress summary: `docs/public-progress.md`.
- Public release plan and safety checklist: `docs/public-release-plan.md`.
- Verification command manual: `docs/verification-manual.md`.
- Implementation scope sanity check: `docs/implementation-sanity-check.md`.
- Agentic workspace control-plane design: `docs/agentic-workspace-control-plane.md`.
- Task kind, automation, workflow/run contract standard: `docs/task-execution-contract-standard.md`.
- Harness runner, resident agent, governance, and Ralph audit model: `docs/harness-runner-governance-development-spec.md`.
- Task title writing standard: `docs/task-title-standard.md`.
- PostgreSQL slow-track notes: `docs/postgres-slow-track.md`.
- The runtime source of truth should be the task database, not in-memory timers or only Markdown files.
- Markdown and JSON files are supported as import/export and human-editable bridge formats, but they must have idempotent sync rules before bidirectional editing is enabled.
- Product naming is intentionally centralized in `task_memory_hub/branding.py`. Avoid scattering display names, short names, or task ID prefixes through implementation code.

## Product Direction

Build one Python codebase with four operational surfaces:

- `tmh-web`: loopback-only Web UI and REST API on `127.0.0.1`.
- `tmh-worker`: DB-backed scheduler, durable outbox, retry, and notification routing.
- `tmh-mcp`: STDIO MCP server for Cline and other MCP-capable agents.
- `tmh`: CLI for terminal-based "todo everywhere" operations.

Windows tray support is part of the product, but the tray process should be a thin launcher/controller for the web API and worker, not a second task engine.
The Hub Station tray path is `tmh-tray --station` or `scripts/start-tmh-hub-station.ps1`; it should use the global hub DB and keep the actual task engine in the DB/API/worker layers.
Autostart installation is handled by `scripts/install-tmh-hub-station.ps1`, with double-click wrappers under `installers/`. If Task Scheduler registration is denied, the installer falls back to the current user's Startup folder.

## Current Architecture Decisions

- Cline integration should prefer STDIO MCP.
- REST API is still required as a fallback and for scripts, browser UI, and local automation.
- Current-stage operating default is SQLite for convenience and local durability. Keep the schema and service boundary portable so PostgreSQL can become the production backend later, but do not block app features on PostgreSQL migration now.
- Workspace/global hub sync must be authority-aware. Do not add automatic pull or cross-workspace routing before workspace/principal registry and approval status are represented.
- Workspace/principal registry, push sync, and authority-approved pull are implemented. Pull must remain gated by `target_workspace_id` and non-empty `approved_by_principal_id`.
- Auth/network/harness profiles are implemented as registry records. Auth profiles store secret references only, never secret values.
- New tasks should be registry-bound by default: source workspace, source principal, target principal when known, assigned/proposed/approved principal fields, and harness/policy references should be resolved from registry records rather than left as free-floating strings.
- New task titles must be written in Korean by default. Preserve English only for proper nouns and stable technical terms such as Cline, MCP, API, Deepagents, JSON, SQLite, or exact external ticket titles. Follow `docs/task-title-standard.md`.
- `tmh add` should prefer human/agent-friendly registry references such as `--by owner`, `--target-agent codex`, and `--harness cautious`; raw IDs remain available for scripts and sync internals.
- Existing old tasks missing `source_principal_id` should be identified by `tmh status` and repaired explicitly with `tmh bind-missing --source <principal> --yes`, not silently guessed during unrelated operations.
- AI-created action items must go through harness rules. Preserve duplicate, rate-limit, interval, and max-open-action checks before adding more automation.
- Active agents are represented by `agent_runtime_status` records, not by the mere existence of an agent principal. Orchestrator assignment must use active heartbeat/lease, role, capacity, and capability data.
- The orchestrator must assign through the hub by updating task routing fields and writing an `assigned` event. Worker agents should claim assigned tasks and report progress/completion back through task events.
- Harness runner execution must emit `runner_started`, `policy_decision`, `backend_resolved`, `backend_started`, `reasoning_summary`, `artifact_reported`, and terminal `completed|blocked|failed` events. P0 runner execution must use `dry_run` unless a later P2/P3 backend explicitly opts in.
- Human control must be explicit in durable task events. Approval, rejection, change request, stop request, and runner stop observation should be represented as `approval_decision`, `stop_requested`, and `stop_observed` events plus `controller_status`, not as hidden process state.
- Capability matching should use task `execution_contract.required_capabilities` against agent runtime `capabilities`; no-capability tasks may be assigned to any active worker with capacity.
- The Web UI task detail route `/tasks/{task_id}` is a control-plane inspector. Keep source workspace/principal, folder path, agent runtime, harness, claim state, event timeline, and selected-task control actions visible there when changing task/registry/agent fields.
- `Agent 활성화` in the Web UI means registering or heartbeating an agent runtime in the hub. Do not imply that this launches an external Cline/Codex process until a harness runner exists.
- Distinguish one-off tasks from recurring automation. Use `automation` as a definition/template and `workflow_run` as the executable instance created from it.
- Distinguish orchestrator, harness runner, and execution backend. The orchestrator assigns; the harness runner supervises execution; Cline, Deepagents, Codex, scripts, and external systems are backends or clients.
- Deepagents integration is a runner backend path. Keep P2 validation on deterministic/scripted contract smokes unless the user explicitly provides the live on-prem auth/runtime context.
- Cline on-prem validation should follow `docs/cline-mcp-onprem-pilot-checklist.md` and `scripts/test-cline-mcp-pilot.ps1`; do not modify global Cline MCP settings without explicit instruction.
- Script execution must use `script_ref` allowlists. Do not execute raw task prose, `next_action`, `detail_md`, or task-provided command strings as shell commands.
- Preserve human visibility and human override as a top-level requirement. A human must be able to see provenance, authority, runtime, claim state, backend, progress, artifacts, and stop semantics before trusting automation.
- Before implementing live runner backends, preserve the AI governance alignment vocabulary from `docs/harness-runner-governance-development-spec.md`: memory classification, audit trace packet events, risk tier, side-effect class, reasoning summary policy, and expertise asset references.
- A Web UI stop/release action must describe backend-specific semantics. Cline IDE stop cannot be treated as a hard process kill unless a stable control API exists.
- External delivery, email send, hook execution, and arbitrary script execution require explicit policy/capability references; do not store secrets or raw webhook URLs in tasks.
- Codex global automation workflows may treat TMH global DB as the primary durable controller and workspace `.automation\registry` as a mirror. Keep repo behavior aligned with that precedence without depending on a machine-specific path.
- `workspace_id` is based on canonical path. Treat git remote/branch as metadata, not identity, so MCP and CLI paths do not register the same workspace twice.
- MCP `push_to_global_hub` uses the CLI/script fallback internally to keep STDIO transport responsive; preserve `stdin=DEVNULL` if this path is changed.
- Global hub tasks should be thin manifests by default. Use `push --profile normal` for routine sync, `manifest` for minimal cross-workspace routing, and `full` only inside a trusted boundary.
- Preserve `source_workspace_id`, `origin_task_id`, `hub_task_id`, and fetch refs; they are the reverse lookup path back to the source-of-truth workspace task.
- The scheduler must read due work from durable storage and write notification jobs to an outbox.
- MCP tool calls must be idempotent. Use idempotency keys and stable fingerprints to avoid duplicate task storms.
- Local HTTP must bind only to loopback and enforce Host/Origin checks before any browser-facing API is treated as safe.
- REST API documentation is served by the current stdlib loopback server at `/docs` as Swagger UI, `/docs/reference` as a static reference, and `/openapi.json` as the machine-readable spec. When adding or changing routes, update the docs/OpenAPI surface in the same change.

## Added Requirement From 2026-05-01 Discussion

The user wants "todo in everywhere":

- tasks editable through CLI commands,
- tasks creatable and readable by Cline through MCP,
- API/script fallback when MCP is unavailable,
- Markdown and JSON task files usable by humans,
- order-based and priority-based work queues for agents,
- immediate visibility when a human updates a task.

Treat this as a P0 product requirement, not a later nice-to-have.

## Implementation Priorities

1. Define task state machine, portable schema, and idempotent create/update rules.
2. Implement CLI and REST API first so the system can be tested without Cline.
3. Implement STDIO MCP using the same service layer.
4. Add file bridge for Markdown/JSON task exchange with explicit import/export and then watched sync.
5. Add worker/outbox and at least one low-risk notification channel.
6. Add Windows tray launcher/controller.
7. Add Teams, OpenProject, and richer Windows toast integrations.
8. Add packaging, startup registration, backup/restore, and operational diagnostics.

## Data Model Additions To Preserve

The design spec already defines the core `tasks`, `task_events`, `task_reminders`, and `notification_jobs` shape. Preserve these additions when implementing the "live memory" use case:

- `rank` or `order_index` for explicit ordered work queues.
- `agent_claim_owner`, `agent_claim_until`, and `agent_claim_status` for safe agent pickup.
- `last_human_update_at` and `last_agent_update_at` for handoff visibility.
- `source_file_path`, `source_file_mtime`, and `source_content_hash` for Markdown/JSON bridge sync.
- `depends_on` or a dependency table for ordered multi-step work.
- `context_pack_version` for Cline/deepagents resume compatibility.

## File Bridge Rules

- Prefer Markdown with YAML frontmatter for human editing.
- Prefer JSON/JSONL for scripts and bulk exchange.
- Never parse freeform Markdown as the only source for required fields; required fields must live in frontmatter or JSON.
- Every import must preserve `task_id` when present and use a deterministic fingerprint when not present.
- Bidirectional sync must record conflict status instead of silently overwriting human edits. Current import conflict marker is `file_changed_after_local_update`.

## Cline Integration Rules

- Do not modify the user's global Cline MCP settings without explicit instruction.
- For smoke tests, prefer a project-local Cline config directory such as `.cline-test`.
- The expected Cline MCP registration shape is:

```powershell
cline mcp add --config .\.cline-test task-memory-hub .\scripts\tmh-mcp.cmd
```

- If Cline provider auth is unavailable in the test config, verify the MCP server directly and verify task operations through CLI/API.
- If a Cline test is run with the user's real config, keep the prompt low-risk and do not request destructive file operations.

## Deepagents Fallback

An external Deepagents scaffold can be used as a fallback harness for agent-driven smoke tests. Treat it as a caller of TMH CLI/API, not as part of the core runtime.

## Testing Expectations

Before considering a change ready, run the narrowest useful tests available:

- CLI create/list/update smoke test.
- REST health and task CRUD smoke test.
- REST `/docs`, `/docs/reference`, Swagger UI static assets, and `/openapi.json` smoke test when API routes or docs change.
- MCP tools/list and one `create_task`/`list_due_tasks` smoke test.
- Registry/global hub smoke: `workspace register`, `principal ensure`, `push`, duplicate push check, and another-folder local/global separation.
- Registry-bound add smoke: `tmh status`, `tmh add --by owner --target-agent <agent> --harness <profile>`, `tmh list --target-principal <agent>`, `tmh tree <task_id>`, and `tmh bind-missing` dry-run.
- Orchestrator smoke: `tmh agent register`, `tmh orchestrator run-once`, `tmh claim-next --owner <agent>`, `tmh progress`, and `tmh done --owner <agent>`.
- Runner smoke: `tmh runner once --backend dry_run --task-id <task_id> --json`, then verify task events include `policy_decision`, `backend_resolved`, `backend_started`, `reasoning_summary`, and `artifact_reported`.
- Web UI selected-task smoke: open `/tasks/{task_id}`, verify provenance/agent/event sections, run `POST /v1/tasks/{task_id}/claim`, progress, release, then verify the page reflects those events.
- Harness runner changes must preserve the governance model in `docs/harness-runner-governance-development-spec.md`.
- AI governance alignment must preserve memory classification, audit trace packet events, risk tier, side-effect class, reasoning summary policy, and expertise asset references before live execution.
- Thin manifest smoke: `push --profile manifest|normal|full`, then `fetch-origin` to confirm origin detail can be recovered without copying it into the hub.
- Markdown/JSON import/export round trip once the bridge exists.
- Worker due-task scan and outbox enqueue test once scheduling exists.
- Windows tray launch smoke test once tray support exists.

Record commands and outcomes in the final response when tests are run.

## Editing Policy

- Keep this folder self-contained.
- Do not touch unrelated dirty files in parent workspaces.
- Prefer small, verifiable increments over speculative broad scaffolding.
- Prefer explicit, local readability cleanup over broad abstractions. Do not split CLI/service/registry modules or add repository layers unless tests and migration needs justify it.
- Keep user-facing documentation in Korean unless a tool requires English.
- Do not store API keys, webhook URLs, or secret values in repository files.
- When adding or changing a CLI command or option, update the relevant `--help` examples in the same change.
