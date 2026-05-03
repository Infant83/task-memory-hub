# Harness Profile and AI Action Intake

작성일: 2026-05-01
작업공간: 이 repository root

## 1. 목적

이 문서는 Task Memory Hub에서 workspace/profile/harness/AI action item을 어떤 순서로 등록하고 운영할지 정리한다.

목표는 단순히 agent가 TODO를 많이 만드는 것이 아니다. agent가 작업 중 발견한 후속 작업을 자연스럽게 남기되, 중복 task storm, 과도한 알림, authority 없는 cross-workspace push/pull을 막는 것이다.

## 2. 프로필 등록 절차

프로필 등록은 아래 순서가 기본이다.

1. Workspace 등록
   - `workspace_id`는 canonical path 기준 stable ID다.
   - git remote/branch는 identity가 아니라 metadata다.
   - 등록자, 제안자, 승인자, authority basis를 함께 남긴다.

2. Principal 등록
   - human, agent, service를 모두 principal로 본다.
   - `principal_id`, `principal_type`, `display_name`, `trust_level`, `active`를 기록한다.

3. Auth profile 등록
   - 실제 secret은 저장하지 않는다.
   - `secret_ref`만 저장한다. 예: `env:TASK_MEMORY_HUB_API_TOKEN`
   - scope는 `fetch-origin`, `pull`처럼 최소 권한 단위로 남긴다.

4. Network profile 등록
   - workspace의 API/MCP 접근 방식을 기록한다.
   - 예: loopback API URL, STDIO MCP command, auth profile ref.

5. Policy profile 등록
   - 분류, redaction level, 외부 쓰기 허용 여부, 승인 필요 조건을 기록한다.

6. Harness profile 등록
   - 특정 workspace에서 agent action intake를 어떻게 제한할지 정의한다.
   - default agent, policy, network, action rate limit, open action limit, default push profile을 묶는다.

## 3. AI Action Item 등록 절차

agent가 작업 중 follow-up을 발견하면 아래 순서로 들어온다.

1. `tmh ai-action add ...` 또는 MCP/API equivalent로 액션 제안
2. 현재 workspace와 agent principal 확인
3. harness profile 조회 또는 기본 harness 생성
4. `action_key` 기준 중복 여부 확인
5. 최근 1시간 등록 수 확인
6. 마지막 accepted action 이후 최소 간격 확인
7. 해당 harness/agent의 open action 수 확인
8. 통과하면 task 생성
9. 수락/중복/제한/거절 결과를 `action_intake_events`에 기록

기본 CLI 예:

```powershell
python -m task_memory_hub.cli harness register --name cautious --agent-name codex --max-actions-per-hour 2 --min-action-interval-seconds 0 --max-open-actions 2
python -m task_memory_hub.cli ai-action add "내일까지 TMH toast 기능 구현 확인" --next "dispatch-once 결과 확인" --harness cautious --agent-name codex --action-key toast-check
```

## 4. 하네싱 규칙

하네스는 AI가 너무 자주 작업을 등록하지 않게 하는 안전장치다.

현재 구현된 규칙:

- `action_key` 중복 차단
- `max_actions_per_hour` 시간당 accepted action 제한
- `min_action_interval_seconds` accepted action 간 최소 간격
- `max_open_actions` 열려 있는 AI action 수 제한
- disabled harness 차단
- accepted, duplicate, throttled, rejected event 기록

권장 기본값:

| profile | max/hour | interval | open | 용도 |
|---|---:|---:|---:|---|
| `default` | 6 | 300초 | 20 | 일반 agent 작업 |
| `cautious` | 2 | 300초 | 5 | 새 agent 또는 외부 제안 |
| `trusted-local` | 12 | 60초 | 50 | 같은 workspace의 신뢰된 자동화 |

## 5. Source Of Truth 원칙

- workspace-local SQLite DB가 현재 source of truth다.
- Markdown/JSON은 사람이 수정하기 쉬운 bridge 형식이다.
- global hub는 full context 저장소가 아니라 thin manifest/control plane이다.
- cross-workspace 작업은 `source_workspace_id`, `target_workspace_id`, `source_principal_id`, `approved_by_principal_id`, `hub_task_id`, `origin_task_id`로 역추적한다.
- 자세한 context가 필요하면 hub snapshot을 키우는 대신 origin workspace로 fetch/query한다.

## 6. 이번 구현 순서 7가지

1. 프로필/AI action/harness 규칙 정리 및 현 코드 상태 확인
2. profile registry와 AI action CLI 구현 및 검증
3. tray quick-add와 worker pause/resume 구현 및 검증
4. Markdown/JSON import conflict-aware sync 구현 및 검증
5. authority-approved global pull 구현 및 검증
6. auth/network profile 기반 구현 및 검증
7. SQLite backup/restore와 PostgreSQL 전환 준비 구현 및 Ralph 감사 로그 작성

## 7. Drift 점검

이번 확장은 단순 알람앱에서 벗어난 것이 아니라, 원래 목표였던 agentic live memory를 안전하게 운영하기 위한 control plane 확장이다.

다만 drift 위험은 있다.

- 너무 많은 profile/policy/network 필드를 초기에 강제하면 사용성이 떨어진다.
- global hub가 full context 저장소가 되면 source-of-truth 원칙이 흐려진다.
- AI action intake가 무제한이면 task storm이 된다.
- PostgreSQL adapter를 조기 구현하면 앱 기능 완성보다 DB 이중화 복잡도가 커진다.

현재 결론:

- SQLite 기본 운영은 유지한다.
- global hub는 thin manifest와 역추적 ID 중심으로 유지한다.
- AI action은 harness rate limit 뒤에 둔다.
- PostgreSQL은 database URL, backup/restore, schema portable field부터 준비하고 실제 backend는 slow-track로 둔다.
