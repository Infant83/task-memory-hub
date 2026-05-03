# Cline MCP On-Prem Pilot Checklist

## 목적

On-prem Cline에서 TMH를 MCP server로 붙여 task 생성, 승인, runner dry-run까지 이어지는 최소 workflow를 검증한다. 이 문서는 사용자의 실제 on-prem 환경에서 실행할 체크리스트이고, repo의 기본 검증은 전역 Cline 설정을 수정하지 않는 direct STDIO MCP smoke로 수행한다.

## Verified Locally

다음은 repo에서 검증 가능한 범위다.

```powershell
.\scripts\test-cline-mcp-pilot.ps1
```

이 스크립트가 확인하는 것:

- `scripts\tmh-mcp.cmd` 존재 여부
- 지정 DB 초기화
- MCP `tools/list`
- 필수 tool 존재 여부
  - `create_task`
  - `list_tasks`
  - `record_approval_decision`
  - `request_task_stop`
  - `runner_run_once`
- MCP `create_task`
- MCP `record_approval_decision`
- MCP `runner_run_once` with `dry_run`

Project-local Cline config 등록까지 같이 확인하려면:

```powershell
.\scripts\test-cline-mcp-pilot.ps1 -RegisterProjectLocalCline
```

이 옵션은 `.cline-test`에만 등록하며 사용자의 전역 Cline MCP 설정은 수정하지 않는다.

## Expected Cline Registration

사용자 on-prem 환경에서 Cline auth/runtime이 준비되면 다음 형태가 기준이다.

```powershell
cline mcp add --config .\.cline-test task-memory-hub .\scripts\tmh-mcp.cmd
```

전역 config에 등록하려면 사용자가 명시적으로 승인한 뒤 진행한다.

## Pilot Scenario

1. Cline에서 `task-memory-hub` MCP server가 tools/list를 반환하는지 확인한다.
2. Cline이 `create_task`로 한국어 제목 task를 만든다.
3. 사람이 Web UI 또는 CLI에서 `approve`를 기록한다.
4. Cline 또는 runner가 `runner_run_once` dry-run을 실행한다.
5. Web UI task detail에서 다음 event trail을 확인한다.
   - `created`
   - `approval_decision`
   - `runner_started`
   - `policy_decision`
   - `backend_started`
   - `reasoning_summary`
   - `artifact_reported`
   - `completed`

## Unverified Until On-Prem

- 실제 Cline provider auth.
- Cline prompt routing과 tool approval UX.
- On-prem model/network latency.
- 장시간 작업 중 cooperative stop polling.
- Cline이 생성한 산출물을 TMH artifact contract로 되돌리는 workflow.

## Drift Guard

P3는 Cline을 TMH 내부 프로세스로 흡수하지 않는다. Cline은 MCP-capable client/backend이고, TMH는 task DB, policy, approval, event audit, runner coordination을 담당한다.

