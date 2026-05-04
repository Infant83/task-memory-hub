const configElement = document.getElementById("tmh-task-config");
const config = configElement ? JSON.parse(configElement.textContent) : {};
const result = document.getElementById("result");

function setResult(message, isError = false) {
  if (!result) return;
  result.textContent = message;
  result.classList.toggle("error", isError);
}

async function postJson(url, body = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Task-Memory-Hub-Token": config.writeToken || "",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

async function postTaskAction(action, body = {}) {
  try {
    setResult("working...");
    await postJson(`/v1/tasks/${config.taskId}/${action}`, body);
    location.reload();
  } catch (error) {
    setResult(error.message, true);
  }
}

async function governanceAction(action) {
  const reason = window.prompt("reason", "") || "";
  if (config.isReviewGate) {
    const decisionMap = {
      approve: "approved",
      reject: "rejected",
      "request-changes": "changes_requested",
    };
    await postTaskAction("review-gate-decision", {
      by: "owner",
      reason,
      decision: decisionMap[action] || action,
    });
  } else {
    await postTaskAction(action, { by: "owner", reason });
  }
}

async function claimSelectedTask() {
  const body = {
    owner: config.defaultOwner || "web-ui",
    lease_seconds: 1800,
  };
  if (config.targetPrincipalId) body.target_principal_id = config.targetPrincipalId;
  await postTaskAction("claim", body);
}

async function releaseSelectedTask() {
  const body = { next_status: "acknowledged" };
  if (config.activeClaimOwner) body.owner = config.activeClaimOwner;
  await postTaskAction("release", body);
}

async function activateTargetAgent() {
  try {
    setResult("working...");
    await postJson("/v1/agents/register", {
      name: config.targetAgentName,
      role: "worker",
      status: "active",
      capabilities: config.requiredCapabilities || [],
      lease_seconds: 600,
    });
    location.reload();
  } catch (error) {
    setResult(error.message, true);
  }
}

async function heartbeatAgent() {
  try {
    setResult("working...");
    await postJson("/v1/agents/heartbeat", {
      principal_id: config.targetPrincipalId,
      name: config.targetAgentName,
      status: "active",
      current_task_id: config.taskId,
      lease_seconds: 600,
    });
    location.reload();
  } catch (error) {
    setResult(error.message, true);
  }
}

async function runOrchestrator() {
  try {
    setResult("working...");
    await postJson("/v1/orchestrator/run-once", {
      name: "web-ui-orchestrator",
      include_not_due: true,
      limit: 10,
    });
    location.reload();
  } catch (error) {
    setResult(error.message, true);
  }
}

async function runDryRunner() {
  try {
    setResult("working...");
    const capabilities = new Set([
      ...(config.requiredCapabilities || []),
      "tmh-api",
      "tmh-cli",
      "repo-edit",
      "dry-run",
    ]);
    const data = await postJson("/v1/runner/run-once", {
      name: config.targetAgentName || "web-ui-runner",
      backend: "dry_run",
      task_id: config.taskId,
      include_not_due: true,
      capabilities: Array.from(capabilities),
    });
    setResult(`Runner: ${data.result || data.reason || "ok"}. 새로고침합니다.`);
    setTimeout(() => location.reload(), 700);
  } catch (error) {
    setResult(error.message, true);
  }
}

async function requestDeliveryDryRun() {
  const channel = window.prompt("delivery channel", "email") || "";
  const recipientRef = window.prompt("recipient_ref", "principal:owner") || "";
  const reason = window.prompt("reason", "external delivery dry-run") || "";
  await postTaskAction("delivery-dry-run", {
    by: "owner",
    channel,
    recipient_ref: recipientRef,
    requires_review: true,
    reason,
  });
}

async function appendProgress() {
  const progressBox = document.getElementById("progressMessage");
  const message = progressBox ? progressBox.value.trim() : "";
  if (!message) {
    setResult("progress message is required", true);
    return;
  }
  await postTaskAction("progress", {
    message,
    owner: config.defaultOwner || "web-ui",
  });
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-task-action],[data-governance-action],[data-ui-action]");
  if (!button) return;
  const taskAction = button.dataset.taskAction;
  const governance = button.dataset.governanceAction;
  const uiAction = button.dataset.uiAction;

  if (taskAction === "snooze") {
    postTaskAction("snooze", { duration: button.dataset.duration || "1d" });
  } else if (taskAction) {
    postTaskAction(taskAction);
  } else if (governance) {
    governanceAction(governance);
  } else if (uiAction === "claim") {
    claimSelectedTask();
  } else if (uiAction === "release") {
    releaseSelectedTask();
  } else if (uiAction === "activate-agent") {
    activateTargetAgent();
  } else if (uiAction === "heartbeat-agent") {
    heartbeatAgent();
  } else if (uiAction === "orchestrator-run") {
    runOrchestrator();
  } else if (uiAction === "runner-dry-run") {
    runDryRunner();
  } else if (uiAction === "request-review-gate") {
    const reason = window.prompt("reason", "human review required") || "";
    postTaskAction("review-gate", { by: "owner", reason, gate_type: "manual_review" });
  } else if (uiAction === "delivery-dry-run") {
    requestDeliveryDryRun();
  } else if (uiAction === "append-progress") {
    appendProgress();
  }
});
