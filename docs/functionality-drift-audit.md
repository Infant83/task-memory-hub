# 기능 검증과 드리프트 감사

작성일: 2026-05-04

## 목적

이 문서는 현재 Task Memory Hub 구현이 원래 기능명세와 얼마나 일치하는지, 논의 과정에서 생긴 확장이 의미 있는 개선인지, 아니면 되돌려야 할 drift인지 판단하기 위한 공개용 감사 기록이다.

## 감사 기준

- DB가 runtime source of truth로 남아 있는가.
- CLI, API, MCP, Web UI가 같은 service layer와 같은 task/event 데이터를 다루는가.
- 알람, 작업, agent 실행, 외부 전달이 hidden state가 아니라 durable event로 남는가.
- 사람이 provenance, authority, runtime, claim, approval, stop 상태를 볼 수 있는가.
- Cline, Deepagents, Codex, script는 TMH를 대체하지 않고 client/backend로 동작하는가.
- 외부 write, webhook, email, OpenProject 같은 side effect가 review gate와 secret reference 정책 없이 실행되지 않는가.
- SQLite 현 단계 운영이 PostgreSQL 전환 가능성을 막지 않는가.
- 공개 매뉴얼과 공개 repo 범위에 local-only handoff log, private audit transcript, secret value, machine path가 섞이지 않는가.

## 기능 검증 결과

2026-05-04 현재 아래 검증을 통과했다.

| 범위 | 명령 | 결과 |
| --- | --- | --- |
| Python 컴파일 | `python -m compileall task_memory_hub scripts` | 통과 |
| Web UI JS 문법 | `node --check task_memory_hub\static\app.js`, `node --check task_memory_hub\static\task-detail.js` | 통과 |
| 통합 smoke | `scripts\ci-smoke.ps1 -DbPath %TEMP%\tmh-drift-ci-smoke.sqlite -Port 8824` | 통과 |
| CLI create/list/done/events | 임시 DB에서 `tmh add`, `tmh list --json`, `tmh done`, `tmh events --json` | 통과 |
| P5 delivery dry-run | 승인 전 `delivery dry-run`, review gate 승인, 승인 후 재시도 | 통과 |
| MCP pilot | `scripts\test-cline-mcp-pilot.ps1 -DbPath %TEMP%\tmh-drift-mcp.sqlite` | 통과 |

확인된 대표 결과:

- CLI 완료 검증 task는 `status=completed`로 조회되었고 `created`, `updated`, `completed` 이벤트가 남았다.
- P5 delivery dry-run은 승인 전 `review_required`, 승인 후 `dry_run_recorded`로 동작했다.
- P5 이벤트에는 `delivery_requested`, `review_gate_requested`, `delivery_review_required`, `approval_decision`, `review_gate_decision`, `delivery_dry_run`, `artifact_reported`가 남았다.
- MCP pilot은 tool 34개를 노출했고 runner dry-run 결과가 `completed`로 기록되었다.

## 원 기능명세와 현재 구현 비교

| 원래 목표 | 현재 상태 | 판단 |
| --- | --- | --- |
| Windows 11 local-first alarm/task app | CLI/API/MCP/Web UI, worker/outbox, tray/station installer script, toast fallback이 있다. tray UX와 standalone installer는 더 다듬어야 한다. | 방향 유지. 부족분은 발전 |
| DB source of truth | task, event, registry, runtime, review gate, delivery dry-run이 DB에 남는다. | 핵심 일치 |
| Markdown/JSON 기반 todo everywhere | import/export와 JSON file add가 있다. bidirectional watch sync는 아직 제한적이다. | 핵심 일치, watch sync는 후속 |
| Cline MCP | STDIO MCP 서버와 pilot script가 있다. 실제 on-prem Cline runtime 검증은 사용자 환경에서 남아 있다. | gap이지만 drift 아님 |
| API fallback | loopback REST API, Swagger UI, OpenAPI JSON, static API reference가 있다. | 일치 |
| hidden tray alarm | tray/station 시작, 설치 script, toast fallback은 있으나 daily UX hardening은 부족하다. | gap, 후속 개선 |
| agent 작업 분배 | registry, runtime heartbeat, orchestrator, claim/progress/done, harness runner dry-run이 있다. | 원 목표를 agentic workspace로 확장한 의미 있는 개선 |
| 외부 알림/전달 | 실제 Teams/OpenProject/email/webhook write 대신 P5 review-gate와 delivery dry-run이 먼저 구현되었다. | 안전한 선행 단계. 되돌리지 않음 |
| PostgreSQL 운영 기본 | 최초 설계명세는 PostgreSQL default를 권고했다. 이후 명시적 의사결정으로 현 단계는 SQLite default, PostgreSQL slow-track이 되었다. | 의도적 운영전략 변경. 되돌리지 않음 |

## Drift 판단

### 되돌릴 필요가 없는 변화

Agentic workspace, registry, harness, orchestrator, review gate, delivery dry-run은 원래의 단순 알람 앱 범위를 넓혔다. 하지만 이 확장은 “작업을 누가 요청했고, 누가 수행했고, 어떤 권한으로 완료했는지”를 추적하려는 핵심 목적을 강화한다. 따라서 product drift가 아니라 governance/control-plane 성숙으로 보는 것이 맞다.

### 의도적으로 유지할 변경

SQLite를 현재 기본값으로 유지하는 것은 최초 설계명세의 PostgreSQL default 권고와 다르다. 그러나 설치 난이도, Windows 로컬 검증 속도, 공개 MVP 접근성을 고려하면 현재 단계에서는 유리하다. 단, schema와 service boundary는 PostgreSQL 전환 가능성을 계속 보존해야 한다.

### 수정한 drift

통합 매뉴얼 생성기가 로컬에 남아 있는 `docs/handoff-progress-log.md`를 `docs/manual.html`에 포함하고 있었다. 해당 파일은 `.gitignore`와 public release plan에서 local-only로 분류된 handoff 로그이므로 공개 매뉴얼에 포함되면 안 된다. 생성기에서 local-only 문서를 제외하도록 수정하고, 공개 가능한 판단 결과는 이 문서에 별도로 남긴다.

## 결정

현재 TMH는 원래 목표에서 되돌릴 정도로 벗어나지 않았다. 오히려 단순 알람 앱에서 “사람 중심의 task memory control plane”으로 발전한 것은 의미 있는 개선이다.

다만 다음 원칙은 유지해야 한다.

- 외부 write를 실제로 수행하는 adapter는 P5 review gate와 secret reference 정책 이후에만 붙인다.
- Cline, Deepagents, Codex는 source of truth가 아니라 TMH의 client/backend로 둔다.
- public manual은 공개 문서와 선택 구현 스냅샷만 포함하고 local-only handoff/audit transcript는 제외한다.
- PostgreSQL 전환 준비는 계속하되, 현재 기능 구현을 PostgreSQL 설치에 묶지 않는다.

## 다음 개발 판단

1. OpenProject는 `OPENPROJECT_BASE_URL`, `OPENPROJECT_API_KEY` 기반 read-only capability probe를 먼저 구현한다.
2. Outlook 이메일은 Windows Outlook COM 기반 draft 생성부터 구현하고 자동 발송은 보류한다.
3. Webhook은 raw URL 저장 없이 `network_profile` 또는 `webhook_profile` reference 설계를 먼저 확정한다.
4. tray/station은 시작, 중지, 상태 확인, Web UI 열기 shortcut을 end-user 설치 흐름으로 더 단순화한다.
5. Web UI는 registry/provenance/harness 정보가 많아진 만큼 ID 축약 표시와 drill-down 조회를 병행한다.
6. PostgreSQL은 slow-track smoke로 export/import와 schema portability를 점검한다.
