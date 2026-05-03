param(
    [string]$DbPath = ".tmh\cline-mcp-pilot.sqlite",
    [string]$ConfigDir = ".cline-test",
    [switch]$RegisterProjectLocalCline
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Launcher = Join-Path $Root "scripts\tmh-mcp.cmd"
$ResolvedDb = Join-Path $Root $DbPath
$DbDir = Split-Path -Parent $ResolvedDb
if (-not (Test-Path -LiteralPath $DbDir)) {
    New-Item -ItemType Directory -Path $DbDir | Out-Null
}

if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "MCP launcher not found: $Launcher"
}

Push-Location $Root
try {
    python -m task_memory_hub.cli --db $ResolvedDb init | Out-Null

    if ($RegisterProjectLocalCline) {
        $Cline = Get-Command cline -ErrorAction SilentlyContinue
        if ($null -eq $Cline) {
            Write-Host "cline_project_config=skipped_missing_cline"
        } else {
            if (-not (Test-Path -LiteralPath $ConfigDir)) {
                New-Item -ItemType Directory -Path $ConfigDir | Out-Null
            }
            & cline mcp add --config $ConfigDir task-memory-hub $Launcher
            Write-Host "cline_project_config=registered"
        }
    }

    $env:TMH_PILOT_DB = $ResolvedDb
    $Python = @'
import asyncio
import json
import os
import sys
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def as_dict(result):
    if result.structuredContent:
        return result.structuredContent
    if result.content and getattr(result.content[0], "text", ""):
        return json.loads(result.content[0].text)
    raise RuntimeError("MCP tool returned no structured content")


async def main():
    env = os.environ.copy()
    env["TASK_MEMORY_HUB_DB"] = os.environ["TMH_PILOT_DB"]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_memory_hub.mcp_server"],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            required = [
                "create_task",
                "list_tasks",
                "record_approval_decision",
                "request_task_stop",
                "request_review_gate",
                "decide_review_gate",
                "runner_run_once",
            ]
            missing = [name for name in required if name not in names]
            if missing:
                raise RuntimeError("missing MCP tools: " + ", ".join(missing))

            created = await session.call_tool(
                "create_task",
                {
                    "title": "P3 Cline MCP 직접 연동 검증 " + uuid.uuid4().hex[:8],
                    "summary": "project-local Cline MCP pilot smoke",
                    "source_principal_name": "cline-pilot",
                    "target_principal_name": "cline-pilot-runner",
                    "idempotency_key": "p3-cline-mcp-pilot-" + uuid.uuid4().hex[:12],
                    "task_kind": "delegated_task",
                },
            )
            task = as_dict(created)
            if not task or not task.get("task_id"):
                raise RuntimeError("create_task did not return task_id")

            approval = await session.call_tool(
                "record_approval_decision",
                {
                    "task_id": task["task_id"],
                    "decision": "approved",
                    "by": "owner",
                    "reason": "P3 MCP pilot smoke",
                },
            )
            approval_payload = as_dict(approval)
            runner = await session.call_tool(
                "runner_run_once",
                {
                    "agent_name": "cline-pilot-runner",
                    "backend": "dry_run",
                    "task_id": task["task_id"],
                    "capabilities": ["tmh-api", "tmh-cli", "repo-edit"],
                    "include_not_due": True,
                },
            )
            runner_payload = as_dict(runner)

            print(
                json.dumps(
                    {
                        "tool_count": len(names),
                        "required_missing": missing,
                        "task_id": task["task_id"],
                        "approval_controller": approval_payload.get("controller_status"),
                        "runner_result": runner_payload.get("result"),
                        "runner_status": runner_payload.get("task", {}).get("status"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )


asyncio.run(main())
'@
    $Python | python -
}
finally {
    Pop-Location
}
