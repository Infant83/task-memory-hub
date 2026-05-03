function toggleTaskRow(toggle) {
  const targetId = toggle.getAttribute("data-row-toggle");
  const detailRow = document.getElementById(targetId);
  if (!detailRow) {
    return;
  }

  const expanded = toggle.getAttribute("aria-expanded") === "true";
  detailRow.hidden = expanded;
  toggle.setAttribute("aria-expanded", String(!expanded));
  toggle.setAttribute("aria-label", expanded ? "작업 요약 펼치기" : "작업 요약 접기");
  toggle.setAttribute("title", expanded ? "작업 요약 펼치기" : "작업 요약 접기");
  toggle.classList.toggle("is-expanded", !expanded);

  if (!expanded) {
    loadTaskDetails(toggle, detailRow);
  }
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function setDetailFields(container, fields) {
  container.replaceChildren();
  for (const [label, value] of fields) {
    const field = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    field.className = "task-row-field";
    term.textContent = label;
    description.textContent = formatValue(value);
    field.append(term, description);
    container.append(field);
  }
}

async function loadTaskDetails(toggle, detailRow) {
  if (detailRow.dataset.loaded === "true" || detailRow.dataset.loading === "true") {
    return;
  }

  const taskId = toggle.getAttribute("data-task-id");
  const container = detailRow.querySelector("[data-detail-content]");
  if (!taskId || !container) {
    return;
  }

  detailRow.dataset.loading = "true";
  try {
    const response = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}`, {
      headers: { "Accept": "application/json" },
      cache: "no-store"
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const task = await response.json();
    const source = task.source_workspace_slug || task.source_workspace || "-";
    const target = task.target_principal_id || task.agent_claim_owner || "-";
    const claim = [task.agent_claim_owner, task.agent_claim_status].filter(Boolean).join(" / ") || "-";
    const origin = task.origin_task_id || task.hub_task_id || "-";
    setDetailFields(container, [
      ["Summary", task.summary || "-"],
      ["Next action", task.next_action || "-"],
      ["Created / Updated", `${formatValue(task.created_at)} / ${formatValue(task.updated_at)}`],
      ["Source", source],
      ["Source agent", task.source_agent || "-"],
      ["Target principal", target],
      ["Task kind", task.task_kind || "-"],
      ["Controller", task.controller_status || "-"],
      ["Routing", task.routing_status || "-"],
      ["Claim", claim],
      ["Origin", origin]
    ]);
    detailRow.dataset.loaded = "true";
  } catch (error) {
    setDetailFields(container, [["Error", `작업 요약을 불러오지 못했습니다: ${error.message}`]]);
  } finally {
    detailRow.dataset.loading = "false";
  }
}

document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-row-toggle]");
  if (toggle) {
    toggleTaskRow(toggle);
    return;
  }

  if (event.target.closest("a, button, input, select, textarea, label")) {
    return;
  }

  const row = event.target.closest(".task-main-row");
  if (!row) {
    return;
  }

  const rowToggle = row.querySelector("[data-row-toggle]");
  if (!rowToggle) {
    return;
  }

  toggleTaskRow(rowToggle);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }

  const row = event.target.closest(".task-main-row");
  if (!row || event.target.closest("a, button, input, select, textarea")) {
    return;
  }

  const rowToggle = row.querySelector("[data-row-toggle]");
  if (!rowToggle) {
    return;
  }

  event.preventDefault();
  toggleTaskRow(rowToggle);
});
