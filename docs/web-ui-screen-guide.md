# Web UI Screen Guide

Updated: 2026-05-04

## Purpose

This guide explains the stable meaning of the TMH Web UI screens. Exact layout, labels, and visual density may change as the product matures, but the Web UI must continue to make human control visible:

- what the task is,
- who or which workspace submitted it,
- when it was created and when it is due,
- how important it is and where it sits in the work order,
- which principal or agent is responsible,
- whether an agent runtime is active,
- what actions, approvals, stops, claims, and runner events have happened.

Domain access for a local machine is intentionally left as a later TODO. The current Web UI remains a loopback-first local control plane.

## Control Screen

`/control`은 task 상세 화면보다 상위의 운영 대시보드다. 이 화면은 사람이 지금 TMH가 agentic workflow를 받을 준비가 되었는지 빠르게 확인하기 위한 화면이다.

확인해야 하는 핵심 질문:

- 등록된 workspace가 무엇이고 현재 hub가 어떤 workspace DB를 보고 있는가.
- human, agent, service principal이 등록되어 있는가.
- harness profile이 현재 workspace에서 해석 가능한가.
- active runtime이 있고 lease가 살아 있는가.
- 승인 대기 review gate가 있는가.
- 승인되었지만 아직 claim되지 않은 agentic task가 있는가.
- claim되었거나 실행 중인 task가 있는가.
- harness reference가 task에는 있으나 registry에서 해석되지 않는 missing reference가 있는가.

이 화면은 외부 Cline, Codex, Deepagents process를 직접 실행했다는 뜻을 주지 않는다. `live runtime`은 DB에 등록된 heartbeat/lease 상태이고, 실제 backend 실행은 runner 또는 MCP client가 별도로 수행해야 한다.

## Task List Screen

The root screen is the fast scanning surface for the current hub task queue.

Top summary cards show the current task count, active product surfaces, and the REST API entrypoint. These cards are orientation aids, not the runtime source of truth. The database remains the source of truth.

The task table is the primary queue view:

| Column | Meaning |
| --- | --- |
| `>` | Expands a lightweight row inspector without leaving the list. The row body can also be clicked, while the title still opens the full detail page. |
| `Title` | Human-readable task title. Click it to open the full task detail page. |
| `Status` | Current lifecycle status such as `scheduled`, `completed`, `blocked`, or `failed`. |
| `Priority` | Importance or urgency class. |
| `Rank` | Explicit ordering value inside a queue or work slice. Smaller numbers should be handled earlier when queue policy uses rank. |
| `Due` | Due time or snooze time currently controlling when the task should surface. |
| `Created` | Time the task was first recorded in the local task database. |
| `Next Action` | The next concrete action expected from a human, orchestrator, runner, or assigned agent. |

The expanded row is a quick inspector. It should stay compact and show only fields useful for deciding whether to open the full detail page: summary, next action, created/updated time, source workspace, source agent label, target principal, task kind, controller status, routing status, claim state, and origin/hub reference.

## Priority And Rank

`Priority` and `Rank` are intentionally separate.

`Priority` answers: “How important or urgent is this task?”

Examples: `urgent`, `high`, `normal`, `low`.

`Rank` answers: “In what order should tasks be handled inside the relevant queue?”

Examples: rank `10` before rank `20`; rank `1` before rank `30`.

Practical rule:

- Priority is a coarse severity or urgency class.
- Rank is a deterministic ordering hint.
- A queue policy may sort by priority first and rank second, or may use rank inside a filtered slice such as “all urgent governance tasks.”
- Do not treat `rank=1` as globally more important than every `urgent` task unless the active queue policy says so.

This separation lets a human mark several tasks as urgent while still deciding the exact order agents should attempt them.

## Task Detail Screen

The task detail page is the control-plane inspector for one selected task. It is the place where provenance, authority, runtime state, and human override controls must remain visible.

### Header

The header shows navigation back to the task list, the selected task id, the title, and the short next-action text. The task id is useful for CLI/API/MCP references and for reverse lookup from global hub manifests back to the source workspace task.

### Control Actions

The action buttons are durable task operations. They should write task events or state transitions rather than acting as hidden UI-only state.

Common actions:

| Action | Meaning |
| --- | --- |
| `Ack` | Acknowledge the task or notification. |
| `승인` | Approve runner or governance-controlled execution. |
| `변경요청` | Ask for revision before execution or completion. |
| `거절` | Reject the current execution proposal or submitted result. |
| `중지요청` | Record a human stop request. Backend-specific stop semantics must be visible in events. |
| `Snooze` | Defer the task until a later time. |
| `Done` | Mark the task complete. |
| `작업 Claim` | Assign/claim the task for a principal or runtime through the hub. |
| `Runtime 등록` | Register or refresh an agent runtime record in the hub. This does not launch an external Cline, Codex, or Deepagents process. |
| `Runtime Heartbeat` | Refresh runtime liveness and write a selected-task event when a current task is attached. |
| `Dry-run 실행` | Run the policy-aware harness runner without side effects. |
| `Review Gate` | Create or reuse a human review-gate child task. |
| `Orchestrator 배정` | Attempt one orchestrator pass that assigns eligible work to active capable agents. |

### Status Strip

The compact cards below the actions show the minimum situational state: lifecycle status, due time, source, target agent, and runtime liveness. This area should allow a human to answer whether the task is pending, overdue, assigned, running, or inactive without reading raw JSON.

### Work Content

The work content panel shows summary, next action, and detail text. This is the human-readable task description. It is not executable script content. Runner backends must use explicit execution contracts and allowlisted script references.

### Status Panel

The status panel carries operational metadata:

- lifecycle status,
- priority and rank,
- due and snooze time,
- completed time,
- task kind,
- execution mode,
- schedule kind,
- controller status,
- routing status.

This panel explains how the task should be interpreted by queue views, orchestrator logic, and automation policy.

### Provenance

The submission/source panel explains where the task came from:

- source workspace,
- source folder,
- source repository metadata,
- submitted/proposed/approved principal,
- source agent,
- origin task id,
- hub task id,
- created and updated time.

This section is essential for reverse lookup. A thin global hub manifest can point back to the source workspace instead of copying every detail into the hub.

### Agent And Harness

The agent/harness panel explains how agentic execution is attached:

- target workspace,
- target principal,
- assigned principal,
- harness profile,
- runtime id and liveness,
- claim owner/status/until,
- capabilities.

This separates “an agent principal exists” from “an active runtime is available and holding a lease.”

### Hierarchy, Contract, And Events

Child tasks, review gates, source files, execution contract JSON, and task events form the audit trail. These sections may be visually reorganized later, but the detail page must continue to expose:

- parent/child task relationships,
- origin and file bridge references,
- required capabilities and execution contract,
- policy decisions,
- runner/backend events,
- progress and artifact reports,
- approval, rejection, stop, and completion events.

## Development Drift Guard

When the Web UI changes, check that the following are still visible somewhere in the list or detail flow:

- created time and due/snooze time,
- priority and rank as separate concepts,
- source workspace/principal and target principal,
- task kind and execution mode,
- active runtime and claim state,
- approval/stop/review controls,
- event timeline and runner decisions,
- linkable task id for CLI/API/MCP workflows.

If a field becomes too dense for the list screen, move it to the expanded row or detail page rather than deleting it from the Web UI entirely.

## Registry Visibility

Workspace, principal, agent runtime, and harness records are reproducibility evidence, not decorative metadata.

Stable rules:

- A task should expose the source workspace, source principal, target principal, assigned-by principal, harness profile, and runtime/claim state when those fields exist.
- The UI may show shortened IDs for readability, but the full ID must remain available through title/hover, JSON, API, or CLI output.
- Full IDs remain the database/API identity. Short IDs are display aliases only and must not be used as the only durable reference.
- Registry references should resolve to record views where possible: workspace, principal, and harness records need to be inspectable from the Web UI or API.
- If a hub task references a registry record that is not mirrored into the hub DB, the UI should mark it as unregistered in the current DB rather than pretending it is known.
- `fetch-origin` should be used to recover source-workspace registry context for hub tasks when the hub carries only a thin manifest.

Useful CLI/API paths:

```powershell
tmh workspace show task-memory-hub
tmh principal list
tmh harness show runner-governance
tmh --global fetch-origin tmh_example --json
Invoke-RestMethod http://127.0.0.1:8787/v1/principals/pr_...
Invoke-RestMethod http://127.0.0.1:8787/v1/harnesses/har_...
```

## Performance Guard

The Web UI is an operator surface for alarms and task control. It should feel lightweight even when the hub grows.

Stable performance rules:

- The root list must render a thin queue first.
- Hidden detail content should not be pre-rendered for every task.
- Row expansion may fetch one selected task through `/v1/tasks/{task_id}` on demand.
- Static CSS and JavaScript should use versioned URLs so a running browser does not keep stale behavior after an update.
- The server process may initialize the database once on startup, but normal read requests should not rerun full schema setup.
- If the root page becomes slow again, measure startup time, first HTML response time, static asset time, and browser interaction separately before adding features.
