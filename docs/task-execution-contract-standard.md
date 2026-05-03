# Task Execution Contract Standard

작성일: 2026-05-02

## 1. 목적

Task Memory Hub는 단순 TODO 목록이 아니라 사람과 agent가 함께 쓰는 작업 실행 메모리다. 따라서 task에는 아래 질문에 답할 수 있는 표준 필드가 필요하다.

- 이 항목은 단순 알림인가, 1회성 작업인가, 반복 자동화인가?
- 누가 실행해야 하는가?
- 어느 workspace에서 실행해야 하는가?
- 어떤 skill/rule/workflow/hook/script를 따라야 하는가?
- 어떤 산출물을 만들어야 하는가?
- 산출물은 어디로 전달되어야 하는가?
- 자동 실행이 가능한가, 사람 승인 후 실행해야 하는가?

이 문서는 그 공통 vocabulary와 최소 contract를 정의한다.

## 2. 핵심 원칙

1. DB가 source of truth다.
2. Markdown/JSON은 사람이 읽고 쓰는 bridge다.
3. global hub는 full context 저장소가 아니라 routing/control-plane이다.
4. 반복 자동화는 `automation definition`과 매번 실행되는 `run task`를 분리한다.
5. 실행 규칙은 자유 텍스트만으로 두지 않고, skill/rule/workflow/hook/script reference로 구조화한다.
6. secret, API key, webhook URL, email password는 task에 저장하지 않는다. `auth_profile_ref` 같은 reference만 저장한다.
7. 처음부터 많은 table을 만들지 않는다. top-level 분류 필드 몇 개와 JSON contract로 시작하고, 반복되는 패턴이 굳으면 table로 승격한다.
8. Codex global automation workflow와 맞춰 TMH global DB를 우선 durable controller로 보고, workspace `.automation/registry`는 mirror로 둔다.

## 3. Task Kind

초기 표준 task kind는 아래로 제한한다.

| kind | 의미 | 예 |
|---|---|---|
| `reminder` | 사람에게 알려주면 끝나는 알림 | 내일 아침 등산 준비 |
| `action` | 사람이 직접 처리할 1회성 작업 | 문서 검토하기 |
| `delegated_task` | 특정 workspace/principal/harness가 처리해야 하는 1회성 작업 | FEATHER 리포트 생성 |
| `automation` | 반복 실행 정의 자체 | 매일 07:30 AI pulse 생성 |
| `workflow_run` | automation에서 생성된 개별 실행 task | 2026-05-03 AI pulse run |
| `review_gate` | 실행 전후 승인/검토 checkpoint | 메일 발송 전 검토 |

원칙:

- `automation`은 실행 그 자체가 아니라 template이다.
- scheduler는 due가 된 `automation`에서 `workflow_run`을 생성한다.
- agent는 일반적으로 `workflow_run`이나 `delegated_task`를 claim한다.
- 사람이 만든 단순 due 알림은 `reminder`가 기본이다.
- 기존 `tmh add` 기본값은 호환성을 위해 당분간 `action`으로 본다.

## 4. Execution Mode

| execution_mode | 의미 |
|---|---|
| `manual` | 사람이 직접 수행 |
| `agent_assisted` | 사람이 시작하거나 검토하고 agent가 보조 |
| `agent_autonomous` | 허용된 harness가 자동 claim/실행 가능 |
| `scripted` | 지정된 script/command가 실행 가능 |
| `external` | OpenProject, Teams, 외부 system이 주 실행면 |

권장 기본값:

- `reminder`: `manual`
- `action`: `manual`
- `delegated_task`: `agent_assisted`
- `automation`: `scripted` 또는 `agent_autonomous`
- `workflow_run`: 원본 automation의 `execution_mode`를 상속
- `review_gate`: `manual`

## 5. Schedule Contract

반복 자동화는 timezone이 중요하다. cron만 저장하면 timezone과 DST 처리가 애매해지므로, 기본 표현은 iCalendar RRULE 형태를 우선한다.

필드 후보:

```json
{
  "schedule_kind": "recurring",
  "timezone": "Asia/Seoul",
  "start_at": "2026-05-03T07:30:00+09:00",
  "rrule": "FREQ=DAILY;BYHOUR=7;BYMINUTE=30",
  "misfire_policy": "run_once",
  "max_catchup_runs": 1
}
```

`schedule_kind` 값:

| schedule_kind | 의미 |
|---|---|
| `none` | 예약 없음 |
| `due_once` | 한 번 due |
| `recurring` | 반복 실행 |
| `event_triggered` | 파일 변경, webhook, 외부 이벤트 기반 |

misfire policy:

| policy | 의미 |
|---|---|
| `skip` | 놓친 실행은 건너뜀 |
| `run_once` | 여러 번 놓쳐도 한 번만 생성 |
| `catch_up_limited` | `max_catchup_runs`까지만 보정 실행 |

## 6. Execution Contract

agent나 script가 작업을 실행하려면 최소한 아래 contract가 필요하다.

```json
{
  "target_workspace_id": "ws_...",
  "target_principal_id": "pr_...",
  "harness_id": "har_...",
  "execution_mode": "agent_autonomous",
  "approval_required": false,
  "dry_run_default": true,
  "required_capabilities": ["local-shell", "tmh-mcp"],
  "blocked_capabilities": ["external-email-send"],
  "skill_refs": ["daily-pulse-review"],
  "rule_refs": ["source-linked-markdown"],
  "workflow_refs": ["daily-ai-pulse"],
  "hook_refs": [],
  "script_refs": [
    {
      "name": "daily_pulse",
      "command_ref": "scripts:daily_pulse",
      "args_schema_ref": "schemas:daily_pulse_args"
    }
  ]
}
```

원칙:

- `script_refs.command_ref`는 실제 shell command 문자열보다 named reference를 우선한다.
- 직접 command를 저장해야 한다면 repo-local allowlist를 통과해야 한다.
- 외부 발송 capability는 기본 차단하고, review gate 또는 policy approval 뒤에 허용한다.
- Cline skill, workflow, rule, hook은 실행 규칙 reference로 다루며 task body에 장문 복사하지 않는다.

## 7. Artifact And Delivery Contract

반복 업무는 산출물과 전달 경로가 핵심이다.

```json
{
  "artifacts": [
    {
      "artifact_type": "markdown_report",
      "path_template": "reports/daily-pulse/{date}/report.md",
      "required": true
    },
    {
      "artifact_type": "html_report",
      "path_template": "reports/daily-pulse/{date}/report.html",
      "required": false
    }
  ],
  "delivery": [
    {
      "channel": "email",
      "recipient_ref": "principal:owner",
      "subject_template": "[Daily Pulse] {date}",
      "include_artifacts": ["markdown_report", "html_report"],
      "requires_review": true
    }
  ]
}
```

원칙:

- recipient email 주소 자체보다 `recipient_ref`를 우선한다.
- 외부 발송은 기본적으로 `requires_review=true`다.
- 산출물 경로는 workspace-local path template로 남기고, 실제 생성 파일은 event 또는 run task에 기록한다.

## 8. Minimal Schema Direction

다음 migration에서 바로 넣을 후보는 많지 않다.

Top-level 후보:

- `task_kind TEXT DEFAULT 'action'`
- `execution_mode TEXT DEFAULT 'manual'`
- `schedule_kind TEXT DEFAULT 'none'`
- `controller_status TEXT DEFAULT ''`
- `automation_id TEXT DEFAULT ''`
- `parent_task_id TEXT DEFAULT ''`
- `execution_contract_json TEXT DEFAULT '{}'`
- `schedule_json TEXT DEFAULT '{}'`
- `artifact_contract_json TEXT DEFAULT '{}'`

현재 구현 상태:

- 위 top-level 후보는 schema version 7로 반영했다.
- `tmh automation add/list/show`를 추가했다.
- `tmh add`는 `--kind`, `--execution-mode`, `--schedule-kind`, `--controller-status`, `--automation-id`, `--parent-task-id`, `--execution-contract-json`, `--schedule-json`, `--artifact-contract-json`을 받을 수 있다.
- REST API는 `/v1/automations` GET/POST를 제공한다.
- MCP는 `register_automation`, `list_automations` tool을 제공한다.

보류:

- 별도 `automations` table
- 별도 `workflow_runs` table
- 별도 `artifacts` table
- cron parser/runner
- email sender adapter

보류 이유:

- 지금은 SQLite local-first 단계다.
- 너무 일찍 table을 늘리면 CLI/API/MCP/bridge 전체 수정면이 커진다.
- 반복 패턴이 실제 사용에서 확인되면 table로 승격하는 편이 안전하다.

## 9. CLI/API 방향

당장 구현할 때의 CLI 형태는 아래가 자연스럽다.

단순 알림:

```powershell
tmh add "내일 아침 등산" --kind reminder --due "2026-05-03T07:00:00+09:00"
```

workspace에 위임되는 1회성 작업:

```powershell
tmh add "리포트 초안 생성" --kind delegated-task --target-workspace ws_... --harness codex-cli --next "source를 수집하고 report.md 생성"
```

반복 자동화 정의:

```powershell
tmh automation add "Daily AI Pulse" --timezone Asia/Seoul --rrule "FREQ=DAILY;BYHOUR=7;BYMINUTE=30" --workflow daily-ai-pulse --deliver email:owner --requires-review
```

초기 구현에서는 `automation add`가 내부적으로 `task_kind=automation` task를 만들고, worker가 due 시점에 `workflow_run` task를 생성하는 방식으로 충분하다.

native 조회:

```powershell
tmh automation list
tmh --global automation list --status active --json
```

workspace mirror가 필요하면:

```powershell
tmh automation add "Daily AI Pulse" --rrule "FREQ=DAILY;BYHOUR=7;BYMINUTE=30" --workflow daily-ai-pulse --mirror
```

## 10. JSON 예시

```json
{
  "title": "Daily AI Pulse",
  "task_kind": "automation",
  "execution_mode": "agent_assisted",
  "schedule_kind": "recurring",
  "priority": "normal",
  "schedule": {
    "timezone": "Asia/Seoul",
    "start_at": "2026-05-03T07:30:00+09:00",
    "rrule": "FREQ=DAILY;BYHOUR=7;BYMINUTE=30",
    "misfire_policy": "run_once"
  },
  "execution_contract": {
    "target_workspace_id": "ws_...",
    "harness_id": "har_...",
    "approval_required": true,
    "required_capabilities": ["tmh-mcp", "local-shell"],
    "blocked_capabilities": ["external-email-send"],
    "workflow_refs": ["daily-ai-pulse"],
    "skill_refs": ["source-linked-markdown"]
  },
  "artifact_contract": {
    "artifacts": [
      {
        "artifact_type": "markdown_report",
        "path_template": "reports/daily-pulse/{date}/report.md",
        "required": true
      }
    ],
    "delivery": [
      {
        "channel": "email",
        "recipient_ref": "principal:owner",
        "include_artifacts": ["markdown_report"],
        "requires_review": true
      }
    ]
  }
}
```

## 11. Drift Check

이 표준화는 원래 목표와 맞다.

- 단순 알람 앱에서 벗어나는 drift가 아니라, 알람/작업/자동화를 같은 source of truth에서 다루기 위한 정규화다.
- 다만 외부 email 발송, script execution, hook execution은 권한과 보안 위험이 크므로 아직 adapter 구현을 서두르지 않는다.
- 현재 단계에서는 vocabulary와 contract를 고정하고, 다음 단계에서 작은 schema migration과 CLI 옵션만 추가한다.
