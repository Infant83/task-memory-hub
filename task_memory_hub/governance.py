from __future__ import annotations

from typing import Any


RUNNER_EVENT_TYPES = {
    "runner_started",
    "policy_decision",
    "backend_resolved",
    "backend_started",
    "reasoning_summary",
    "artifact_reported",
    "blocked",
    "failed",
    "completed",
}

RISK_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "very_high": 3,
}

SIDE_EFFECTS_REQUIRING_APPROVAL = {
    "external_write",
    "irreversible",
    "sensitive_decision",
}


def normalize_risk_tier(value: str | None) -> str:
    risk = (value or "low").strip().lower().replace("-", "_")
    return risk if risk in RISK_ORDER else "low"


def normalize_side_effect_class(value: str | None) -> str:
    side_effect = (value or "none").strip().lower().replace("-", "_")
    return side_effect or "none"


def task_command_ref(task: dict[str, Any]) -> str:
    contract = task.get("execution_contract") or {}
    backend_contract = contract.get("runner_backend") or {}
    if isinstance(backend_contract, dict):
        value = backend_contract.get("command_ref") or backend_contract.get("script_ref")
        if value:
            return str(value).strip()
    return str(contract.get("command_ref") or contract.get("script_ref") or "").strip()


def evaluate_script_ref_policy(task: dict[str, Any], allowed_refs: set[str]) -> dict[str, Any]:
    command_ref = task_command_ref(task)
    reasons: list[str] = []
    if not command_ref:
        reasons.append("script_ref backend requires execution_contract.runner_backend.command_ref")
    elif command_ref not in allowed_refs:
        reasons.append(f"script command ref is not allowlisted: {command_ref}")
    return {
        "allowed": not reasons,
        "decision": "allow" if not reasons else "block",
        "reasons": reasons,
        "command_ref": command_ref,
        "allowlisted_refs": sorted(allowed_refs),
    }


def evaluate_runner_policy(
    task: dict[str, Any],
    runner_capabilities: list[str] | set[str],
    backend: str = "dry_run",
) -> dict[str, Any]:
    """Return an allow/block decision for a runner before backend execution."""
    contract = task.get("execution_contract") or {}
    capabilities = set(runner_capabilities or [])
    backend_capability = backend.replace("_", "-")
    capabilities.add(backend_capability)

    required = set(contract.get("required_capabilities") or [])
    blocked = set(contract.get("blocked_capabilities") or [])
    missing = sorted(required - capabilities)
    blocked_required = sorted(required & blocked)
    blocked_backend = backend_capability in blocked

    risk_tier = normalize_risk_tier(contract.get("risk_tier"))
    side_effect_class = normalize_side_effect_class(contract.get("side_effect_class"))
    approval_required = bool(
        contract.get("human_signoff_required")
        or contract.get("approval_required")
        or RISK_ORDER[risk_tier] >= RISK_ORDER["medium"]
        or side_effect_class in SIDE_EFFECTS_REQUIRING_APPROVAL
    )
    approved = bool(task.get("approved_by_principal_id"))

    reasons: list[str] = []
    if missing:
        reasons.append(f"missing capabilities: {', '.join(missing)}")
    if blocked_required:
        reasons.append(f"required capability is blocked: {', '.join(blocked_required)}")
    if blocked_backend:
        reasons.append(f"backend capability is blocked: {backend_capability}")
    if approval_required and not approved:
        reasons.append("human approval required")

    return {
        "allowed": not reasons,
        "decision": "allow" if not reasons else "block",
        "reasons": reasons,
        "backend": backend,
        "backend_capability": backend_capability,
        "required_capabilities": sorted(required),
        "runner_capabilities": sorted(capabilities),
        "blocked_capabilities": sorted(blocked),
        "risk_tier": risk_tier,
        "side_effect_class": side_effect_class,
        "approval_required": approval_required,
        "approved": approved,
    }
