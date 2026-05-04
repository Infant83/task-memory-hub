# TMH Verification Manual

Updated: 2026-05-04

## 목적

이 문서는 TMH 변경 후 어떤 명령으로 무엇을 검증해야 하는지 한 곳에 모아둔 운영 매뉴얼이다.

기본 원칙은 다음과 같다.

- 변경 범위에 맞는 가장 좁은 smoke test를 먼저 실행한다.
- 실제 운영 DB를 오염시키지 않는 검증은 임시 SQLite DB를 사용한다.
- Hub Station과 global hub 동작을 확인할 때만 `--global` 또는 global DB를 명시한다.
- CLI 옵션, API route, MCP tool, Web UI 버튼이 바뀌면 이 문서와 관련 `--help` 예시도 같이 갱신한다.
- Cline 전역 설정은 사용자가 명시적으로 요청하지 않는 한 수정하지 않는다.

## 빠른 결론

일반 코드 변경 후 최소 검증:

```powershell
python -m compileall task_memory_hub
node --check task_memory_hub\static\app.js
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci-smoke.ps1 -DbPath "$env:TEMP\tmh-ci-smoke.sqlite" -Port 8810
git diff --check
```

Hub Station이 떠 있는지 확인:

```powershell
.\scripts\status-tmh-hub-station.ps1
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/health/ready
```

## 설치 / Hub Station 검증

설치형 흐름을 확인한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-tmh.ps1 -RegisterHubStation -StartNow -DesktopShortcuts
```

시작, 상태 확인, 중지:

```powershell
.\scripts\start-tmh-hub-station.ps1 -Port 8787 -WorkerChannel toast-fallback
.\scripts\status-tmh-hub-station.ps1 -Port 8787
.\scripts\stop-tmh-hub-station.ps1 -Port 8787
```

브라우저에서 확인할 주소:

```text
http://127.0.0.1:8787/
http://127.0.0.1:8787/quick-add
http://127.0.0.1:8787/docs
```

## CLI 기본 검증

임시 DB에서 task 생성, 조회, 상세, 완료 흐름을 확인한다.

```powershell
$db = "$env:TEMP\tmh-cli-smoke.sqlite"
if (Test-Path $db) { Remove-Item $db -Force }

tmh --db $db init
$task = tmh --db $db add "CLI 검증 작업 생성" --next "상세 조회 후 완료 처리" --priority normal --json | ConvertFrom-Json
tmh --db $db list
tmh --db $db show $task.task_id --json
tmh --db $db done $task.task_id --owner codex
tmh --db $db show $task.task_id
```

`tmh` command shim이 없는 환경에서는 같은 명령을 다음 형태로 바꿔 실행한다.

```powershell
python -m task_memory_hub.cli --db $db list
```

## JSON / Markdown 브리지 검증

단일 task JSON 생성:

```powershell
tmh add --json-file .\examples\my-task.json --json
```

배열 또는 동기화 목적의 JSON import:

```powershell
tmh import-json .\examples\tasks.json
```

Markdown export/import round trip:

```powershell
tmh export-md <task_id> .\tmp-task.md
tmh import-md .\tmp-task.md
```

주의: required field는 Markdown 본문이 아니라 YAML frontmatter 또는 JSON field에 있어야 한다.

## REST / Web UI 검증

API 서버 실행:

```powershell
tmh-web --host 127.0.0.1 --port 8787
```

상태, 문서, OpenAPI 확인:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/health/ready
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/docs
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/docs/reference
Invoke-RestMethod http://127.0.0.1:8787/openapi.json
```

쓰기 API 확인:

```powershell
$token = tmh api-token
Invoke-RestMethod http://127.0.0.1:8787/v1/tasks `
  -Method Post `
  -Headers @{"X-Task-Memory-Hub-Token"=$token; "Idempotency-Key"="manual-api-smoke-1"} `
  -ContentType "application/json" `
  -Body (@{
    title="API 검증 작업 생성"
    next_action="Web UI 목록과 상세 화면에서 생성 결과 확인"
    priority="normal"
  } | ConvertTo-Json)
```

Web UI 선택 task 검증:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/tasks/<task_id>
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/static/app.js
```

확인할 화면 요소:

- list 화면의 `Created`, 펼침 행, `Source`, `Agent`, `Origin`.
- detail 화면의 provenance, status, agent/harness, event timeline.
- registry link가 full ID 대신 사람이 읽을 수 있는 이름과 short ID를 보여주는지.

## Registry / Global Hub 검증

현재 workspace와 principal 상태:

```powershell
tmh status
tmh workspace register
tmh principal ensure --type human --name owner
tmh principal ensure --type agent --name codex --trust-level trusted
tmh principal list
tmh harness list
tmh harness show runner-governance
```

registry-bound task 생성:

```powershell
tmh add "레지스트리 바인딩 검증 작업" --by owner --target-agent codex --harness runner-governance --rank 10 --json
tmh list --target-principal codex
tmh tree <task_id>
tmh bind-missing --source owner
```

Global hub push와 reverse lookup:

```powershell
tmh push --profile manifest --json
tmh push --profile normal --json
tmh --global list
tmh --global fetch-origin <hub_task_id> --json
```

검증 기준:

- 반복 push에서 같은 task가 중복 생성되지 않는다.
- hub task에는 thin manifest와 reverse lookup ID가 남는다.
- 상세 context가 필요하면 `fetch-origin`으로 source workspace task를 회수할 수 있다.

## Agent / Orchestrator / Runner 검증

agent runtime 등록:

```powershell
tmh agent register --name codex --capability tmh-cli --capability tmh-api --capability repo-edit --json
tmh agent list --active-only
```

orchestrator assignment:

```powershell
tmh orchestrator run-once --name ralph-orchestrator --limit 5 --json
tmh claim-next --owner codex
tmh progress <task_id> --owner codex --note "검증 진행 중"
tmh done <task_id> --owner codex
```

harness runner dry-run:

```powershell
tmh runner once --task-id <task_id> --backend dry_run --capability tmh-api --capability tmh-cli --json
```

runner event 기준:

- `runner_started`
- `policy_decision`
- `backend_resolved`
- `backend_started`
- `reasoning_summary`
- `artifact_reported`
- terminal event: `completed`, `blocked`, or `failed`

## MCP / Cline 파일럿 검증

repo-local direct MCP smoke:

```powershell
.\scripts\test-cline-mcp-pilot.ps1
```

project-local Cline config 등록까지 확인:

```powershell
.\scripts\test-cline-mcp-pilot.ps1 -RegisterProjectLocalCline
```

수동 등록 기준:

```powershell
cline mcp add --config .\.cline-test task-memory-hub .\scripts\tmh-mcp.cmd
```

검증 기준은 `docs/cline-mcp-onprem-pilot-checklist.md`를 따른다. 사용자 전역 Cline MCP 설정은 명시 요청이 있을 때만 수정한다.

## Deepagents / Script Backend 검증

Deterministic Deepagents backend:

```powershell
python scripts\tmh-deepagents-smoke.py --prompt "hello"
tmh runner once --backend deepagents_cli --backend-command "python scripts\tmh-deepagents-smoke.py" --timeout-seconds 30 --capability tmh-api --capability deepagents-cli --json
```

Allowlisted script backend:

```powershell
tmh runner once --backend script_ref --script-allowlist .\.tmh\script-backends.json --capability tmh-api --json
```

이 명령은 대상 task의 execution contract에 `runner_backend.command_ref`가 있어야 한다. allowlist 형식과 task contract 예시는 `docs/script-ref-backend-allowlist.md`를 따른다.

주의: task prose, `next_action`, `detail_md`, task-provided command string은 실행 대상이 아니다. 실행 가능한 backend는 allowlist의 `script_ref`로만 지정한다.

## Worker / Toast 검증

Due task enqueue:

```powershell
tmh enqueue-due
tmh attempts
```

worker loop:

```powershell
tmh-worker --channel toast-fallback --once
tmh worker status
tmh worker pause
tmh worker resume
```

알림 채널 확인:

```powershell
tmh notify-test --channel toast-fallback --title "TMH 알림 검증" --summary "toast fallback 경로 확인"
```

BurntToast가 설치되어 있으면 Windows toast를 시도하고, 실패하면 fallback 기록을 남기는지 확인한다.

## Public Repo / CI 검증

공개 repo에 push하기 전 기본 검증:

```powershell
git diff --check
rg -n --hidden --glob '!*.egg-info/**' --glob '!.git/**' --glob '!.tmh/**' --glob '!__pycache__/**' --glob '!*.pyc' --glob '!*.sqlite' --glob '!*.db' "ghp_|github_pat_|x-access-token|Bearer [A-Za-z0-9_\.-]{20,}|\bsk-[A-Za-z0-9]{20,}" .
```

GitHub Actions의 현재 기준은 `docs/ci-necessity-review.md`와 `.github/workflows/smoke.yml`이다.

## 결과 기록 기준

검증을 수행한 뒤 final response, PR description, 또는 진행 로그에는 다음을 남긴다.

- 실행한 명령
- 사용한 DB path와 port
- 성공/실패 여부
- 실패 시 오류 메시지의 핵심 줄
- runner/orchestrator/Web UI 검증이면 확인한 event 또는 화면 요소
- Cline/Deepagents live 검증을 하지 못했다면 그 경계와 이유

## 문서 갱신 규칙

다음 변경이 발생하면 이 문서를 같이 수정한다.

- CLI command, option, default DB, `--help` 예시 변경
- REST route, token, OpenAPI, Web UI route 변경
- MCP tool 이름 또는 payload 변경
- runner event 이름, backend 종류, allowlist 규칙 변경
- install/startup/tray/toast 스크립트 변경
- CI smoke 범위 변경
