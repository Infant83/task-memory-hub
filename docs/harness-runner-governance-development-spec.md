# Harness Runner 및 Governance 개발 명세

작성일: 2026-05-02

## 1. 요약

Task Memory Hub의 다음 단계는 단순히 agent 실행 버튼을 붙이는 것이 아니다. 목표는 사람이 상황을 보고 판단할 수 있는 control plane 위에 Cline, Deepagents, Codex, script 같은 실행 backend를 안전하게 연결하는 것이다.

핵심 결론:

- TMH는 계속 source of truth다. task, registry, runtime, event, approval, progress는 DB에 남아야 한다.
- Orchestrator는 배정자다. 어떤 task를 어떤 workspace/principal/harness에 줄지 결정한다.
- Harness Runner는 실행 감독자다. 배정된 task를 claim하고, backend를 호출하고, heartbeat/progress/artifact/failure/completion을 기록한다.
- Cline, Deepagents, Codex, script는 실행 backend 또는 client다. backend는 TMH를 대체하지 않고 TMH에 보고한다.
- Web UI는 관제면이다. 사람이 출처, 책임 주체, 진행 상태, due, 권한, 중지 가능성, 산출물, 실패 사유를 볼 수 있어야 한다.
- 최상위 운영권은 사람에게 있다. agent autonomy는 policy/capability/approval/event trail 안에서만 허용한다.

## 2. 현재 구현 기준선

### Core data and interface

- SQLite 기반 portable schema
- CLI CRUD, import/export, JSON file add
- loopback REST API와 Swagger UI
- STDIO MCP server
- local write token
- worker outbox enqueue와 dispatch attempts
- local/toast fallback notification path
- Windows tray Hub Station starter/stopper/install scripts

### Registry and control plane

- workspace registry
- human, agent, service principal registry
- auth/network/harness profile registry
- authority-aware push/pull
- global hub DB scope
- thin manifest push profiles
- hub task ID 기반 origin fetch
- registry-aware `tmh add`
- task tree와 missing binding repair

### Execution contract and automation

- task kind: `reminder`, `action`, `delegated_task`, `automation`, `workflow_run`, `review_gate`
- `execution_mode`, `schedule_kind`
- `execution_contract`, `schedule`, `artifact_contract`
- automation add/list/show
- REST/MCP automation endpoint
- harness rate/duplicate/open-action 규칙을 통과하는 AI action intake

### Runtime and orchestration

- `agent_runtime_status` table
- agent runtime register/heartbeat/list
- orchestrator run-once
- `execution_contract.required_capabilities` 기반 capability matching
- task assignment event
- agent claim/lease/progress/complete
- Web UI selected-task claim/release/progress control

### Web UI

- task list
- quick add
- task detail control-plane inspector
- provenance, folder, principal, due/status, agent runtime, claim state, event timeline
- selected-task controls: ack, snooze, done, claim, release, heartbeat/register, orchestrator run-once, progress

## 3. 현재 gap

다음에 빠져 있는 것은 또 다른 UI 버튼이 아니라 상주 실행 감독자다.

현재 가능한 것:

- Orchestrator가 task를 배정할 수 있다.
- Web UI가 task 상태와 출처를 보여주고 selected task를 제어할 수 있다.
- Agent runtime을 등록하고 heartbeat할 수 있다.
- Task를 claim할 수 있다.
- Progress와 completion event를 기록할 수 있다.

아직 없는 것:

- 자기에게 배정된 일을 계속 확인하는 long-running runner process
- Deepagents, Cline, Codex, allowlisted script backend adapter
- release보다 명확한 stop request semantics
- policy-bound backend execution
- artifact reporting standard
- failure classification, retry/backoff, human-visible blocked state
- Codex가 수동으로 메우지 않아도 Web UI task를 resident Deepagents runner가 가져가 실행하고 보고하는 pilot workflow

## 4. 역할 분리

```text
Human Owner / Operator
  -> Web UI / CLI approval and visibility

TMH Hub DB/API/MCP
  -> source of truth and control plane

Orchestrator
  -> assignment and routing

Harness Runner
  -> execution supervision

Execution Backend
  -> Cline IDE, Deepagents resident runner, Codex test backend, script backend, external system
```

### Orchestrator

책임:

- active task를 읽는다.
- task 요구사항을 workspace/principal/runtime capability와 맞춘다.
- target principal, assigned-by principal, harness, routing status, event를 써서 배정한다.
- approval이 없거나, target이 불명확하거나, dependency가 막혔거나, capability 밖인 작업을 배정하지 않는다.

책임이 아닌 것:

- task를 실행하지 않는다.
- 임의 외부 process를 launch하지 않는다.
- authority를 임의 판단하지 않는다. 기존 policy/approval 기록을 따른다.

### Harness Runner

책임:

- agent runtime을 등록하고 heartbeat한다.
- 자기 principal/workspace에 배정된 task를 polling 또는 subscribe한다.
- 실행 전에 selected task를 claim한다.
- context pack을 만들거나 가져온다.
- harness/profile/execution contract에서 backend를 resolve한다.
- policy 안에서 backend를 실행한다.
- progress event를 기록한다.
- artifact와 reference를 기록한다.
- stop/pause/cancel 요청을 관찰한다.
- task를 release, fail, block, complete 중 하나로 정리하고 감사 event를 남긴다.

책임이 아닌 것:

- source of truth가 되지 않는다.
- 승인되지 않은 cross-workspace task를 조용히 승인하지 않는다.
- raw secret이나 webhook URL을 저장하지 않는다.
- external delivery 또는 destructive action에 대해 review gate를 우회하지 않는다.

### Execution backend

| Backend | 의도된 역할 | 제어 방식 |
|---|---|---|
| `deepagents` | on-prem resident/headless execution | runner가 local CLI/API wrapper를 호출하고 process를 감시 |
| `cline` | IDE-bound workspace agent | Cline이 MCP로 TMH를 읽고 보고. 안정 API가 없으면 runner가 IDE process control을 가정하지 않음 |
| `codex` | 개발/검증 backend | smoke test에는 유용하지만 운영 중심이 아님 |
| `script` | allowlisted deterministic automation | raw shell이 아니라 command ref 기반 |
| `manual` | 사람 직접 수행 | UI/CLI guidance와 review event만 제공 |

## 5. Governance 모델

### Human-in-control 원칙

1. 사람은 누가 만들고, 승인하고, 배정하고, claim하고, 완료했는지 볼 수 있어야 한다.
2. 사람은 외부 side effect가 발생하기 전에 pause, release, cancel, block을 할 수 있어야 한다.
3. 사람은 task가 local-only, pushed, pulled, assigned, in_progress, blocked, failed, completed 중 어디인지 볼 수 있어야 한다.
4. 사람은 어떤 backend가 task를 실행할 예정인지 볼 수 있어야 한다.
5. 사람은 필요한 capability와 차단된 capability를 볼 수 있어야 한다.
6. external delivery나 destructive action은 명확한 policy가 없으면 review gate를 거쳐야 한다.
7. Agent suggestion은 authority가 아니다. harness intake rule과 approval policy를 거쳐야 actionable work가 된다.

### Authority chain

실행 또는 route 가능한 task는 아래 값을 보존해야 한다.

- `source_workspace_id`
- `target_workspace_id`
- `source_principal_id`
- `target_principal_id`
- `proposed_by_principal_id`
- `approved_by_principal_id`
- `assigned_by_principal_id`
- `harness_id`
- `policy_profile_id`
- `origin_task_id`
- `hub_task_id`

값이 없으면 UI는 조용히 추정하지 말고 비어 있음을 보여줘야 한다.

### Autonomy level

| Level | 이름 | 의미 | 기본 동작 |
|---|---|---|---|
| L0 | Manual only | 사람이 직접 수행 | 표시만 하고 runner 실행 없음 |
| L1 | Suggest only | agent가 후속 작업을 제안 | 새 work는 사람 승인 필요 |
| L2 | Assisted | agent가 claim하고 draft/progress 가능 | 완료나 external side effect 전 human review |
| L3 | Local autonomous | 허용된 local non-destructive step 실행 가능 | progress/artifact 기록, stop 가능 |
| L4 | External side effect | email, Teams, OpenProject, webhook, deletion, deploy | owner-approved policy가 없으면 review gate 필요 |

기본 mapping:

- `manual` -> L0
- `agent_assisted` -> L1 또는 L2
- `agent_autonomous` -> 기본 L3
- `scripted` -> script ref가 allowlist에 있을 때만 L3
- `external` -> L4

### Capability gate

Capability는 prose가 아니라 stable ref로 표현한다.

예시:

- `tmh-mcp`
- `tmh-api`
- `local-shell-read`
- `local-shell-write`
- `repo-edit`
- `deepagents-run`
- `cline-mcp`
- `codex-run`
- `external-email-send`
- `openproject-write`
- `teams-send`

Blocked capability는 required capability보다 우선한다. External write capability는 task/harness policy가 명시적으로 허용하지 않는 한 기본 차단한다.

## 6. Harness Runner runtime contract

### Runner identity

Runner는 아래 정보를 가진 registered agent runtime이다.

- `workspace_id`
- `principal_id`
- `agent_name`
- `role`
- `status`
- `capabilities`
- `default_harness_id`
- `max_active_tasks`
- `current_task_id`
- `last_heartbeat_at`
- `lease_until`

권장 role:

- `orchestrator`
- `runner`
- `worker`
- `reviewer`
- `watcher`

### Runner loop

```text
start
  -> register runtime
  -> heartbeat
  -> optionally run orchestrator
  -> list assigned claimable tasks
  -> claim selected task
  -> resolve backend
  -> emit runner_started
  -> execute backend
  -> emit progress/artifact events
  -> observe stop/cancel/pause
  -> complete, release, block, or fail
  -> heartbeat idle
```

### Required runner events

새 table을 만들기 전에는 `task_events`를 우선 사용한다.

| Event | 의미 |
|---|---|
| `runner_started` | runner가 실행 감독을 수락 |
| `backend_resolved` | backend type/ref 선택 |
| `backend_started` | backend execution 시작 |
| `progress` | 사람이 읽을 수 있는 진행 로그 |
| `artifact_reported` | file/path/url/ref 생성 |
| `stop_requested` | 사람 또는 system이 stop 요청 |
| `stop_observed` | runner가 stop을 관찰하고 반응 |
| `released` | 완료 없이 claim 해제 |
| `blocked` | policy/dependency/human review 필요 |
| `failed` | 실행 실패 |
| `completed` | 작업 완료 |

### Stop semantics

Stop은 backend마다 강도가 다르다.

| Backend | Stop behavior |
|---|---|
| runner가 launch한 Deepagents process | runner가 process 종료 후 release/fail 가능 |
| runner가 launch한 script process | policy가 허용하면 runner가 process 종료 가능 |
| Cline IDE | process control을 가정하지 않음. stop requested 표시, claim release, Cline/MCP workflow가 이를 관찰해야 함 |
| Manual | cancelled/blocked/released로 정리 |

UI는 이 차이를 설명해야 한다. backend를 kill할 수 없는데 hard kill처럼 보여주면 안 된다.

## 7. Backend adapter contract

최소 adapter interface:

```text
prepare(task, context, policy) -> execution_plan
start(execution_plan) -> run_handle
poll(run_handle) -> status/progress/artifacts
stop(run_handle) -> stopped/released/failed
finalize(run_handle) -> completion/failure summary
```

MVP backend 우선순위:

1. `dry_run`
   - 외부 side effect 없이 runner loop 증명
   - progress와 completion/failure event 기록
2. `script_ref`
   - allowlisted command ref만 실행
   - task body의 raw shell command 실행 금지
3. `deepagents_cli`
   - local Deepagents scaffold를 process로 호출
   - stdout/stderr summary를 캡처
4. `cline_mcp`
   - 주로 MCP client/user flow
   - IDE process control을 흉내내지 않음
5. `codex_test`
   - 개발 전용 smoke backend

## 8. Web UI 요구사항

Web UI는 조용한 운영 콘솔이어야 한다. full SPA나 marketing page가 아니다.

필수 visibility:

- task source와 target
- folder path와 repo metadata
- submitted/proposed/approved/assigned/claimed/completed principal
- due, priority, rank, dependency
- harness profile과 policy
- runtime status와 lease
- backend type과 current run handle
- latest progress와 artifacts
- stop/pause/cancel 가능 여부와 backend별 의미
- drift 또는 missing authority warning

필수 action:

- approve
- assign/reassign
- orchestrator run
- agent runtime heartbeat/register
- selected task claim
- release/stop
- mark blocked
- complete
- append progress
- open origin/fetch context

보류:

- full SPA rewrite
- audit event 없는 임의 task editing
- direct secret editing
- review gate 없는 external send

## 9. Drift control

### Source-of-truth drift

Risk:

- Hub가 control-plane manifest가 아니라 full context 복제 저장소가 된다.

Guard:

- `manifest`, `normal`, `full` push profile을 policy로 구분한다.
- `origin_task_id`, `source_workspace_id`, fetch refs를 보존한다.
- 자세한 내용은 필요할 때 origin에서 fetch한다.

### Product drift

Risk:

- 알람/task loop가 안정되기 전에 full agent platform으로 커진다.

Guard:

- 모든 기능은 create, due, notify, assign, execute, report, stop, resume 중 하나를 개선해야 한다.
- 이 동사 중 하나를 개선하지 못하면 보류한다.

### Authority drift

Risk:

- agent-suggested task가 human authority 없이 workspace를 넘어 이동한다.

Guard:

- cross-workspace pull은 target workspace와 approved principal로 계속 gate한다.
- external side effect는 policy/capability와 review gate를 요구한다.

### Execution drift

Risk:

- task text에 적혀 있다는 이유로 raw command나 external send가 실행된다.

Guard:

- command ref와 capability ref를 사용한다.
- secret ref만 저장한다.
- 실행 전 backend resolution과 policy decision을 event로 남긴다.

## 10. Ralph audit model

이제 Ralph는 단일 감사자가 아니라 lens 집합으로 쓴다. 실제 process가 아니라 감사 관점이다.

| Lens | 질문 | Evidence |
|---|---|---|
| Ralph-Drift | source-of-truth, local-first, todo-everywhere 목표에서 벗어나지 않았는가 | spec diff, field usage, feature mapping |
| Ralph-Governance | 누가 승인했고, 누가 멈출 수 있고, 어떤 외부 효과가 허용되는가 | registry fields, policy refs, event trail |
| Ralph-Human-Visibility | 사람이 UI/CLI에서 상황과 제어 가능성을 이해할 수 있는가 | Web UI snapshot, CLI output |
| Ralph-Execution | orchestrator/runner/backend lifecycle이 실제 event로 남는가 | task events, runtime heartbeat, artifacts |
| Ralph-Code | 구현이 단순하고, local, testable하며 과한 abstraction이 아닌가 | code inspection, compile/tests |
| Ralph-Regression | 기존 CLI/API/MCP/tray가 유지되는가 | smoke matrix |
| Ralph-Pilot | Codex 수동 개입 없이 실제 시나리오가 끝까지 도는가 | pilot script, run log, result artifacts |

각 Ralph round는 아래를 남긴다.

- target
- quality bar
- scenario
- commands
- observed evidence
- findings by severity
- fixes made
- residual risks
- next required round

## 11. Test strategy

### Code tests

목적:

- service transition, registry lookup, runner loop decision, backend adapter behavior 보호

최소 항목:

- transition validation
- claim/release/heartbeat
- capability matching
- approval gating
- stop request handling
- artifact event creation
- idempotency keys

### Functional tests

목적:

- temp DB에서 CLI/API/MCP behavior 검증

최소 항목:

- create/list/update/done
- selected-task claim/progress/release
- agent runtime register/heartbeat/list
- orchestrator run-once
- import/export
- push/fetch-origin/pull approved

### Smoke tests

목적:

- 설치된 surface가 살아 있는지 확인

최소 항목:

- `tmh --help`
- `tmh add/list`
- `/health/ready`
- `/docs`
- `/openapi.json`
- MCP tools/list
- Hub Station start/stop
- Web UI detail render

### Pilot tests

목적:

- on-prem 운영 모델 증명

Pilot 1:

- Human creates Web UI task.
- Orchestrator assigns to `deepagents-runner`.
- Runner claims task.
- Dry-run backend writes progress and completes.
- Human sees full event trail.

Pilot 2:

- 같은 flow를 Deepagents backend로 수행한다.
- Runner가 output과 artifact reference를 캡처한다.
- 사람은 in-progress run 하나를 stop하고 release/failure state를 확인한다.

Pilot 3:

- Cline IDE가 MCP로 assigned task를 읽는다.
- Cline이 progress/done을 보고한다.
- TMH Web UI가 업데이트를 보여준다.

### Governance tests

목적:

- unsafe autonomy 방지

최소 항목:

- unapproved cross-workspace task cannot auto-pull
- external send task requires review gate
- blocked capability prevents runner execution
- missing source principal is visible
- stop request is visible and evented

## 12. Implementation roadmap

### P0 - 하니스 러너 드라이런

- `tmh-harness-runner` 또는 `tmh runner once/watch` 추가
- runtime register/heartbeat
- 자기 principal에 배정된 task claim
- `dry_run` backend resolve
- runner/backend/progress/completed event 기록
- release/cancel/block state 존중
- Web UI에 current backend/run status 표시

### P1 - 중지와 차단 상태

- explicit stop request event/action 추가
- blocked/failed transition 또는 controller status 처리
- backend별 stop semantics 표시
- Ralph governance test 추가

### P2 - Deepagents 백엔드

- local Deepagents CLI/API adapter 추가
- progress/log summary 캡처
- artifact ref 첨부
- 대표 task 3개로 pilot 수행

### P3 - Cline MCP 파일럿

- Cline은 IDE-bound MCP client로 유지
- Cline workflow/rule guidance 제공
- on-prem config는 별도 검증
- stable external control path가 생기기 전에는 TMH가 Cline launch를 요구하지 않음

### P4 - Script 백엔드 allowlist

- named command ref 추가
- policy check 추가
- task text의 raw arbitrary command 실행 금지

Status: implemented in P4. See `docs/script-ref-backend-allowlist.md`.

### P5 - Review gate와 외부 전달

- review_gate task flow 추가
- review policy와 secret ref가 안정된 뒤 Teams/OpenProject/email 추가

### P6 - PostgreSQL slow-track

- 지금은 SQLite default 유지
- multi-runner contention이 실제로 생기면 repository/adapter split 준비
- export/import와 schema portability check 유지

## 13. Feature deletion or deferral rules

아래 조건이면 삭제하거나 뒤로 미룬다.

- DB source of truth를 우회한다.
- authority/provenance를 사람에게 숨긴다.
- raw secret storage가 필요하다.
- task prose에서 arbitrary shell을 실행한다.
- control 문제를 해결하지 못하는 UI framework를 추가한다.
- global hub를 기본 full context dump로 만든다.
- Codex를 replaceable backend가 아니라 운영 중심으로 만든다.

## 14. Immediate next build slice

P0-P4 완료 이후 다음 build slice는 좁게 잡는다.

1. P5 review_gate flow를 기존 task/event 모델 안에서 구현한다.
2. 외부 전달은 실제 Teams/OpenProject/email 전송이 아니라 dry-run delivery request와 approval event로 먼저 검증한다.
3. secret value는 저장하지 않고 secret reference만 받는다.
4. Ralph governance/human-visibility audit를 작성한다.
5. end-to-end dry-run pilot 1회 수행

이 범위면 Cline 또는 Deepagents live process control에 성급하게 묶이지 않고 architecture를 검증할 수 있다.

## 15. AI Governance Philosophy Alignment Addendum

사용자 제공 AI 거버넌스/하니스 엔지니어링 검토 자료와 비교한 결과, TMH의 현재 방향은 해당 철학과 대체로 일치한다. 특히 DB source of truth, control plane, provenance, authority, human-visible Web UI, orchestrator/runner/backend 분리는 "기억을 하니스로 통제하고 인간이 최종 책임을 행사한다"는 방향과 맞다.

다만 다음 항목은 runner 구현 전에 vocabulary와 event convention으로 먼저 내려야 한다.

### 15.1 Memory classification

보고서의 기억 구분:

- 로컬 기억
- 장기 기억
- 전문성 기억
- 금지 기억

TMH 반영 후보:

- `memory_kind`: `local`, `long_term`, `expertise`, `forbidden`
- `retention_class`: `ephemeral`, `project`, `audit`, `legal_hold`
- `memory_write_allowed`
- `memory_delete_requires_approval`
- `forbidden_memory_reason`

초기에는 `policy_profiles`와 `execution_contract` payload에서 시작하고, 반복 사용이 확인되면 별도 `memory_policies` 또는 `expertise_assets` table로 승격한다.

### 15.2 Audit trace packet

보고서가 요구하는 최소 감사 패킷:

- prompt package
- model/runtime config
- retrieval evidence
- tool events
- memory events
- human approvals
- output package
- security envelope

TMH MVP는 새 table을 바로 늘리지 않고 `task_events` payload convention으로 시작한다.

권장 event type:

- `prompt_package_recorded`
- `backend_resolved`
- `backend_started`
- `tool_event`
- `retrieval_evidence`
- `reasoning_summary`
- `approval_requested`
- `approval_decision`
- `artifact_reported`
- `memory_event`
- `policy_decision`

### 15.3 Reasoning policy

원칙:

- raw chain-of-thought는 기본 저장 금지
- human-readable reasoning summary는 저장 가능
- tool/evidence/approval trace는 runner 실행에서 의무 기록
- 민감한 trace는 redaction 후 저장

### 15.4 Risk tier and side effect class

보고서의 risk tier를 TMH vocabulary로 반영한다.

필드 후보:

- `risk_tier`: `low`, `medium`, `high`, `very_high`
- `side_effect_class`: `none`, `local_write`, `external_write`, `irreversible`, `sensitive_decision`
- `human_signoff_required`
- `policy_decision_ref`

Runner는 `risk_tier >= medium`이거나 `external_write`, `irreversible`, `sensitive_decision`이면 review gate 또는 approval decision 없이 실행하지 않아야 한다.

### 15.5 Expertise memory assets

전문성 기억은 task 본문이 아니라 versioned asset reference로 관리한다.

Asset 후보:

- rule
- skill
- workflow
- hook
- rubric
- playbook
- exception catalog

필드 후보:

- `asset_type`
- `asset_ref`
- `version`
- `content_hash`
- `owner_principal_id`
- `approved_by_principal_id`
- `review_cycle`

### 15.6 Governance framework mapping

Policy profile에는 나중에 조직 표준/법/가이드라인 mapping을 달 수 있어야 한다.

필드 후보:

- `governance_framework_refs`
- `risk_register_ref`
- `control_matrix_ref`
- `impact_assessment_ref`
- `audit_schedule`

이 mapping은 초기 MVP 구현을 막는 선행조건은 아니지만, 외부 발송, 인사/법무/안전 등 고위험 업무를 다루기 전에는 필요하다.
