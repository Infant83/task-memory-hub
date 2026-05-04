# Review Gate Flow

Updated: 2026-05-03

## Purpose

Review gates are durable human decision checkpoints before risky execution, external writes, irreversible actions, or sensitive decisions. They use the existing task/event model instead of a separate hidden approval system.

## Model

- The subject task remains the source of truth for the work.
- The review gate is a child task with `task_kind=review_gate`.
- The gate links back to the subject task through `parent_task_id` and `execution_contract.review_gate.subject_task_id`.
- The gate is idempotent per `subject_task_id + gate_type`.
- Decisions are written as durable events on both the gate and the subject task.

## Events

The flow records:

- `review_gate_requested` on the subject task.
- `created` on the review gate task.
- `approval_decision` on the review gate.
- `approval_decision` on the subject task.
- `review_gate_decision` on the subject task.
- `completed` on the review gate.

Runner execution also records:

- `runner_started`
- `policy_decision`
- `blocked` when approval is missing

## CLI

Request a review gate:

```powershell
tmh review-gate request tmh_subject --reason "external_write 승인 필요" --by owner
```

Decide a review gate:

```powershell
tmh review-gate decide tmh_gate --decision approved --by owner --reason "검토 완료"
```

Valid decisions:

- `approved`
- `rejected`
- `changes_requested`

## REST

Request a review gate:

```http
POST /v1/tasks/{task_id}/review-gate
```

Apply a gate decision:

```http
POST /v1/tasks/{gate_task_id}/review-gate-decision
```

Both routes require the local write token.

## MCP

MCP tools:

- `request_review_gate`
- `decide_review_gate`

These tools let Cline or another MCP client request human review without becoming the approval authority.

## Runner Behavior

When runner policy says human approval is required and the subject task has no approved principal, the runner:

1. creates or returns a review gate,
2. writes `policy_decision` with `review_gate_task_id`,
3. blocks the subject task,
4. waits for a human decision.

After approval, the subject task controller status becomes `active`, and the runner can execute it on the next pass.

## Delivery Dry-Run

P5 includes a dry-run delivery control point. It validates an external delivery request and writes durable events without sending email, Teams messages, OpenProject updates, or webhooks.

CLI:

```powershell
tmh delivery dry-run tmh_subject --channel email --recipient-ref principal:owner --requires-review --by owner
```

REST:

```http
POST /v1/tasks/{task_id}/delivery-dry-run
```

MCP tool:

- `request_delivery_dry_run`

Event flow:

- `delivery_requested`
- `delivery_policy_blocked` when raw secret/write fields are present
- `delivery_review_required` when approval is required but missing
- `delivery_dry_run` when the request is approved for dry-run
- `artifact_reported` with `artifact_type=delivery_dry_run`

Guardrails:

- The dry-run path never sends to external systems.
- Delivery targets should use `recipient_ref`, not raw email addresses or webhook URLs.
- Secret values are not accepted in delivery payloads; use references such as `auth_profile_ref`.
- Review-gate approval authorizes the dry-run record, not a real external send adapter.

## Guardrails

- Review gate approval does not store secrets.
- Review gate approval does not execute external writes by itself.
- External write backends still need explicit capabilities, policy references, and safe backend implementations.
- Reject or changes-requested decisions block the subject task until it is revised.
