# TMH 뉴비 Hands-on 튜토리얼 커리큘럼

작성일: 2026-05-05

## 목적

이 커리큘럼은 Task Memory Hub를 처음 접하는 사용자가 “로컬 TODO 앱”으로 시작해 “agentic workflow control plane”의 기본 개념까지 손으로 확인하도록 설계한다. 강의 목표는 많은 기능을 설명하는 것이 아니라, 작업이 어디에 저장되고, 누가 요청하고, 누가 승인하고, 어떤 agent/runtime이 처리하는지 눈으로 추적하게 만드는 것이다.

## 대상

- Windows 11에서 PowerShell을 사용할 수 있는 사용자
- Python package 설치와 로컬 웹 UI 실행이 낯설 수 있는 초보 사용자
- Cline, Codex, Deepagents 같은 agent 도구를 “TMH의 client/backend”로 이해하고 싶은 사용자

## 운영 원칙

- 모든 실습은 로컬 SQLite DB를 기본으로 한다.
- 실제 email, Teams, OpenProject, webhook 발송은 하지 않는다.
- 외부 side effect는 review gate와 delivery dry-run까지만 실습한다.
- Cline VS Code 확장 실습은 선택 모듈로 둔다. provider auth와 MCP approval UX가 준비된 환경에서만 진행한다.
- PostgreSQL은 개념과 dry-run까지만 다루고 설치형 실습은 고급 과정으로 분리한다.

## 전체 구성

| 모듈 | 제목 | 목표 | 예상 시간 |
| --- | --- | --- | --- |
| 0 | TMH가 해결하는 문제 | source of truth, todo everywhere, human-visible control 개념 이해 | 20분 |
| 1 | 설치와 Hub Station 시작 | CLI, Web UI, tray/station, shortcut 확인 | 30분 |
| 2 | 첫 작업 등록과 확인 | Web UI/CLI로 task 생성, 조회, 완료 | 30분 |
| 3 | JSON/Markdown task 교환 | `tmh add --json-file`, import/export 이해 | 30분 |
| 4 | Registry 기초 | workspace, principal, harness가 왜 필요한지 확인 | 40분 |
| 5 | Agentic Control 화면 | runtime, queue, review gate, claim 상태 읽기 | 40분 |
| 6 | 승인과 Review Gate | 사람이 승인/거절/변경요청을 남기는 흐름 실습 | 40분 |
| 7 | Runner Dry-run | 실제 외부 실행 없이 runner event trail 확인 | 45분 |
| 8 | Global Hub와 Origin 추적 | local workspace task를 global hub에 push하고 origin fetch | 45분 |
| 9 | Cline MCP 파일럿 | VS Code Cline이 TMH MCP를 client로 쓰는 흐름 | 선택 60분 |
| 10 | 운영 점검과 문제 해결 | tray, startup, port, API docs, logs, backup 개념 | 45분 |

## 모듈 0. TMH가 해결하는 문제

핵심 메시지:

- TMH는 chat history나 Markdown 파일이 아니라 DB를 runtime source of truth로 둔다.
- 사람과 agent가 같은 task/event log를 본다.
- agent는 hidden authority가 아니라 등록된 principal/runtime/backend다.
- 외부 write는 review gate와 정책을 통과해야 한다.

실습:

```powershell
tmh status
tmh --global status
```

완료 기준:

- local DB와 global hub DB의 차이를 설명할 수 있다.
- `source workspace`, `target principal`, `harness`, `runtime`이라는 단어를 구분할 수 있다.

## 모듈 1. 설치와 Hub Station 시작

실습:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-tmh.ps1 -RegisterHubStation -StartNow -DesktopShortcuts
powershell -ExecutionPolicy Bypass -File .\scripts\status-tmh-hub-station.ps1
```

확인:

- `tmh.exe`, `tmh-web.exe`, `tmh-worker.exe`, `tmh-mcp.exe`, `tmh-tray.exe`가 PATH에 있는지 확인한다.
- `http://127.0.0.1:8787/` 접속을 확인한다.
- Start Menu의 `Task Memory Hub` shortcut을 확인한다.

완료 기준:

- Web UI, Quick Add, API Docs, Control 화면을 열 수 있다.
- tray/station은 task engine이 아니라 launcher/controller라는 점을 이해한다.

## 모듈 2. 첫 작업 등록과 확인

CLI 실습:

```powershell
tmh add "튜토리얼 첫 작업 등록" --next "Web UI에서 작업 상세를 확인한다" --priority normal
tmh list
```

Web UI 실습:

- `/quick-add`에서 작업을 추가한다.
- task list에서 펼침 버튼을 누른다.
- task 상세 화면에서 `Done`을 누른다.

완료 기준:

- task가 `created`, `updated`, `completed` 이벤트를 남긴다는 점을 확인한다.

## 모듈 3. JSON/Markdown task 교환

JSON 파일 예시:

```json
{
  "title": "JSON 파일로 작업 추가",
  "next_action": "import 결과를 Web UI에서 확인한다",
  "priority": "high",
  "due_at": "2026-05-06T09:00:00+09:00"
}
```

실습:

```powershell
tmh add --json-file .\examples\my-task.json
tmh export-json --output .\tmp\tasks.json
tmh export-md --output .\tmp\tasks.md
```

완료 기준:

- Markdown/JSON은 bridge이고 runtime source of truth는 DB라는 점을 설명할 수 있다.

## 모듈 4. Registry 기초

실습:

```powershell
tmh workspace show
tmh principal list
tmh harness list
tmh harness show runner-governance
```

개념:

- workspace는 작업이 시작된 폴더/프로젝트의 정체성이다.
- principal은 사람, agent, service 같은 행위 주체다.
- harness는 agent가 어떤 정책과 제한으로 행동할 수 있는지 나타낸다.

완료 기준:

- task에 붙은 `source`, `target`, `submitted by`, `approved by`, `harness`가 재현성 근거라는 점을 이해한다.

## 모듈 5. Agentic Control 화면

실습:

- `/control`을 연다.
- `Live runtimes`, `Review gates`, `Approval-ready`, `Claimed/running`, `Missing harness refs`를 확인한다.
- task 상세 화면에서 `Runtime 등록`, `Runtime Heartbeat`, `작업 Claim`을 눌러 변화를 본다.

중요한 해석:

- `Runtime 등록`은 외부 Cline/Codex/Deepagents process를 실행한다는 뜻이 아니다.
- runtime은 DB에 등록된 liveness/lease 신호다.
- 실제 실행은 runner 또는 MCP client가 별도로 수행한다.

완료 기준:

- “agent principal이 있다”와 “runtime이 live다”와 “task가 claim되었다”를 구분할 수 있다.

## 모듈 6. 승인과 Review Gate

실습:

```powershell
tmh add "검토 승인 실습" --target-agent codex --harness runner-governance --priority high
```

Web UI에서:

- 상세 화면을 연다.
- `Review Gate`를 누른다.
- 생성된 review gate task를 열고 `승인`, `거절`, `변경요청` 중 하나를 누른다.

완료 기준:

- 승인/거절은 숨은 상태가 아니라 durable event로 남아야 한다는 점을 확인한다.

## 모듈 7. Runner Dry-run

실습:

```powershell
tmh runner once --backend dry_run --capability tmh-api --capability tmh-cli --capability repo-edit --json
```

확인할 이벤트:

- `runner_started`
- `policy_decision`
- `backend_resolved`
- `backend_started`
- `reasoning_summary`
- `artifact_reported`
- `completed` 또는 `blocked`

완료 기준:

- dry-run은 정책과 event trail을 검증하지만 실제 외부 side effect를 만들지 않는다는 점을 설명할 수 있다.

## 모듈 8. Global Hub와 Origin 추적

실습:

```powershell
tmh push --profile normal
tmh --global list --target-principal codex
tmh fetch-origin <hub_task_id>
```

완료 기준:

- global hub task가 full context dump가 아니라 thin manifest일 수 있음을 이해한다.
- `source_workspace_id`, `origin_task_id`, `hub_task_id`로 원본을 역추적할 수 있다.

## 모듈 9. Cline MCP 파일럿

사전 조건:

- VS Code Cline 확장 provider auth 완료
- 사용자가 Cline MCP 설정 수정을 명시 승인

검증 목표:

- Cline extension에서 `task-memory-hub` MCP server가 보인다.
- Cline이 TMH task를 생성하거나 조회한다.
- Cline이 진행 상황을 `progress`로 보고한다.
- 완료 시 TMH에 `done` 또는 runner event가 남는다.

기본 명령:

```powershell
.\scripts\test-cline-mcp-pilot.ps1
```

주의:

- direct MCP smoke는 Cline extension end-to-end 검증이 아니다.
- Cline IDE process stop은 stable control API가 없으면 TMH가 hard kill로 간주하지 않는다.

## 모듈 10. 운영 점검과 문제 해결

실습:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\status-tmh-hub-station.ps1
Invoke-RestMethod http://127.0.0.1:8787/health/ready
tmh backup --output .\.tmh\backups\tutorial.sqlite
```

점검 항목:

- Web UI가 안 뜨면 process, port, API health를 먼저 확인한다.
- tray icon이 없어도 API/worker가 살아 있을 수 있다.
- PostgreSQL은 현 단계 필수가 아니며, 전환 전에는 dry-run checklist를 먼저 통과해야 한다.

완료 기준:

- 사용자가 “켜기, 상태 확인, 중지, 백업, 문제 진단”의 기본 루틴을 수행할 수 있다.

## 튜토리얼 산출물

튜토리얼 종료 시 학습자는 다음을 가지고 있어야 한다.

- 로컬 TMH DB에 생성된 실습 task 세트
- Web UI에서 확인 가능한 review gate와 runner dry-run event trail
- `/control` 화면에서 agent/runtime/queue 상태를 읽은 기록
- JSON task 파일과 export 결과
- Hub push와 origin fetch 결과

## 강사용 체크리스트

- 실습 전 `tmh status`, `tmh --global status`, `/health/ready`를 확인한다.
- Cline extension 실습은 별도 환경 확인 후 진행한다.
- 외부 발송 실습은 하지 않는다.
- 초보자에게는 `Runtime 등록`과 “실제 agent process 실행”을 반드시 구분해서 설명한다.
- PostgreSQL은 소개만 하고 설치형 실습은 고급 과정으로 넘긴다.
