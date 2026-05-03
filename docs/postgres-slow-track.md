# PostgreSQL Slow Track

작성일: 2026-05-01
작업공간: 이 repository root

## 1. 현재 방침

현 단계의 Task Memory Hub 기본 운영 DB는 SQLite다.

- 기본 런타임 DB: `.tmh/tmh.sqlite`
- 현재 우선순위: CLI, REST API, STDIO MCP, worker/outbox, file bridge, web/tray UX 완성
- PostgreSQL 위치: 향후 전환 후보이자 장기 운영 backend 후보
- 현재 결론: PostgreSQL 설치와 adapter 구현은 앱 구현의 blocker가 아니다.

SQLite를 유지하는 이유:

- Windows 11 단일 사용자 로컬 앱을 먼저 안정화하는 것이 우선이다.
- 설치형 DB 서비스, 계정, 권한, 백업 체계를 지금 도입하면 구현 속도와 테스트 표면이 불필요하게 커진다.
- 현재 CLI/API/MCP/worker/tray smoke는 SQLite 기반으로 이미 검증된 흐름을 갖고 있다.
- DB source of truth 원칙은 SQLite에서도 유지할 수 있다.

PostgreSQL은 제거하지 않는다. 다중 agent, 다중 worker, 장기 outbox 이력, 상시 외부 integration이 필요해지는 시점에 repository/adapter로 붙인다.

## 2. Windows 11 전환 준비 시 확인할 것

### 설치 방법 후보

설치 전에는 실제 설치를 하지 말고 후보만 비교한다.

| 후보 | 확인 항목 | 비고 |
|---|---|---|
| 공식 Windows installer | 버전, 설치 경로, 서비스 이름, data directory, initdb 인증 방식 | 가장 일반적인 로컬 설치 경로 |
| winget | package id, 설치 버전 고정 가능성, silent 옵션 | 재현성은 좋지만 installer 옵션 확인 필요 |
| ZIP archive + 수동 service 등록 | `initdb`, `pg_ctl`, Windows service 등록 절차 | 제어력은 높지만 운영 부담이 큼 |
| Docker/Podman | volume, port, restart policy, localhost bind | 앱의 local-first Windows 서비스 모델과 맞는지 별도 판단 필요 |
| WSL PostgreSQL | Windows tray/API와의 네트워크/시작 순서 | 지금 제품 방향의 기본값으로 두지 않는다 |

권장 검토 순서:

1. 공식 Windows installer 또는 winget으로 로컬 서비스 설치 가능성을 확인한다.
2. Docker/WSL은 개발 실험용으로만 검토하고, 제품 기본 운영 경로로 확정하지 않는다.
3. 설치 전 백업/복구 명령과 서비스 시작/중지 절차를 문서화한다.

### service, user, db, schema

설치 전 결정해야 할 최소 단위:

- Windows service 이름: 예: `postgresql-x64-17`처럼 실제 설치 버전에 맞춘다.
- PostgreSQL superuser: installer 기본 계정과 별도 운영 계정을 구분한다.
- 앱 전용 DB user: 예: `tmh_app`
- 앱 전용 DB: 예: `task_memory_hub`
- schema: 기본은 `public`을 피하고 `tmh` 같은 앱 전용 schema 후보를 검토한다.
- 권한: 앱 계정은 해당 DB/schema의 DDL migration 권한과 런타임 DML 권한을 분리할 수 있는지 검토한다.

초기 운영 원칙:

- 앱은 superuser로 접속하지 않는다.
- PostgreSQL 서비스 계정과 앱 DB 계정을 혼동하지 않는다.
- 로컬 전용이라도 `trust` 인증을 기본으로 두지 않는다.
- migration 실행 계정과 일반 앱 실행 계정 분리를 장기 목표로 둔다.

### env var

코드는 hardcoded connection string을 쓰지 않는다.

후보 환경 변수:

```powershell
$env:TASK_MEMORY_HUB_DATABASE_URL = "sqlite:///<repo-root>/.tmh/tmh.sqlite"
$env:TASK_MEMORY_HUB_DATABASE_URL = "postgresql://tmh_app@127.0.0.1:5432/task_memory_hub?sslmode=disable"
$env:TMH_DATABASE_URL = "sqlite:///<repo-root>/.tmh/tmh.sqlite"
$env:TMH_DATABASE_URL = "postgresql://tmh_app@127.0.0.1:5432/task_memory_hub?sslmode=disable"
```

주의:

- 비밀번호를 repository 파일에 저장하지 않는다.
- `.env` 파일을 쓴다면 gitignore와 secret handling 정책을 먼저 정한다.
- 원격 PostgreSQL을 쓰는 경우 `sslmode=verify-full` 또는 `verify-ca`를 검토한다.
- 로컬 loopback PostgreSQL은 `127.0.0.1` bind와 host-based auth를 확인한다.
- 현재 build의 `tmh db-info`는 URL parsing과 password redaction만 제공한다. runtime store는 SQLite다.

### backup/import

SQLite에서 PostgreSQL로 넘어가기 전에는 seed와 rollback 경로가 필요하다.

준비 항목:

- SQLite export: 현재 task/event/reminder/outbox 데이터를 JSONL 또는 CSV로 안정적으로 추출
- SQLite snapshot: 현재 `tmh backup` / `tmh restore --yes`는 SQLite backup API 기반 snapshot/restore를 제공한다.
- PostgreSQL import: `COPY` 또는 batch insert 기반 seed 절차
- PostgreSQL backup: `pg_dump -Fc` 기반 custom format 백업
- PostgreSQL restore: `pg_restore` 드릴
- cutover plan: SQLite를 read-only로 고정한 뒤 PostgreSQL writable 전환
- rollback plan: cutover 실패 시 SQLite read-only snapshot에서 재개

전환 전 최소 드릴:

```powershell
# 예시만 기록한다. 실제 DB가 생긴 뒤 별도 검증한다.
pg_dump -Fc -d task_memory_hub -f .tmh/backups/task_memory_hub.dump
pg_restore --list .tmh/backups/task_memory_hub.dump
```

### 보안

확인할 항목:

- `pg_hba.conf`가 로컬 접속만 허용하는지
- password authentication이 `scram-sha-256` 기반인지
- DB password가 repo, 로그, shell history, test fixture에 남지 않는지
- 앱 로그가 `DATABASE_URL` 전체를 출력하지 않는지
- remote DB를 허용할 경우 TLS 검증 정책이 있는지
- backup 파일이 민감정보를 포함할 수 있음을 문서화했는지
- MCP/API가 DB credential을 도구 응답이나 오류 메시지로 노출하지 않는지

## 3. 실제 설치 전 체크리스트

PostgreSQL 설치를 시작하기 전에 아래 조건이 충족되어야 한다.

- [ ] SQLite 기반 CLI create/list/update/done smoke가 통과한다.
- [ ] REST API health/task CRUD smoke가 통과한다.
- [ ] MCP `tools/list`, `create_task`, `list_due_tasks` direct smoke가 통과한다.
- [ ] worker due scan과 outbox enqueue가 통과한다.
- [ ] Markdown/JSON export/import round trip이 통과한다.
- [ ] SQLite backup/export 명령이 문서화되어 있다.
- [ ] `TMH_DATABASE_URL` 설정 위치와 secret 처리 정책이 정해져 있다.
- [ ] repository abstraction 설계가 먼저 잡혀 있다.
- [ ] PostgreSQL migration 파일과 SQLite migration 파일의 분리 전략이 정해져 있다.
- [ ] local-only bind, Host/Origin/API token 등 browser-facing API 보안 작업이 별도 blocker 없이 진행 중이다.
- [ ] 설치 후에도 SQLite fallback을 유지할지, read-only migration source로만 둘지 결정되어 있다.
- [ ] 설치 작업자가 Windows service, DB user, DB name, schema name, backup path를 명시했다.

## 4. Postgres Adapter 전에 필요한 코드 경계

PostgreSQL을 붙이기 전에 먼저 코드 경계를 정리한다.

### repository abstraction

현재 SQLite store 구현을 그대로 확장하기보다, service layer가 DB 구현체에 직접 묶이지 않게 한다.

필요한 경계:

- `TaskRepository` 또는 동등한 protocol/interface
- task CRUD
- due task query
- state transition update
- idempotent create/update
- agent claim/lease/heartbeat/release
- outbox enqueue/claim/attempt 기록
- import/export seed read/write

서비스 계층은 `sqlite3` row, SQLite exception, SQLite placeholder 문법에 의존하지 않아야 한다.

### database URL

DB 선택은 파일 경로 분기보다 URL 기반으로 한다.

후보:

- `sqlite:///.../.tmh/tmh.sqlite`
- `postgresql://user@127.0.0.1:5432/task_memory_hub`

필요 작업:

- `TMH_DATABASE_URL`을 config entrypoint로 추가
- 기존 `TMH_HOME`/`.tmh/tmh.sqlite` 기본값은 유지
- 로그에는 password redaction 적용
- CLI/API/worker/MCP가 같은 config resolver를 사용

### migration 분리

SQLite와 PostgreSQL은 같은 논리 스키마를 공유하되 migration SQL은 분리한다.

권장 구조 후보:

```text
task_memory_hub/
  migrations/
    sqlite/
      0001_init.sql
      0002_agent_claims.sql
    postgres/
      0001_init.sql
      0002_agent_claims.sql
```

원칙:

- migration version은 논리적으로 맞춘다.
- DB별 SQL 차이는 migration layer에 가둔다.
- runtime service code에 DDL이 퍼지지 않게 한다.
- rollback보다 forward-only + backup/restore를 우선한다.

### claim query와 `SKIP LOCKED`

agent/worker claim은 PostgreSQL에서 `FOR UPDATE SKIP LOCKED`를 사용해야 한다.

목표:

- 여러 worker/agent가 동시에 due task를 claim해도 같은 task를 중복 pickup하지 않는다.
- expired claim은 재claim 가능해야 한다.
- dependency, status, priority, rank/order_index 정렬이 유지되어야 한다.

PostgreSQL claim query 후보:

```sql
WITH next_task AS (
  SELECT task_id
  FROM tasks
  WHERE status IN ('open', 'snoozed')
    AND (agent_claim_until IS NULL OR agent_claim_until < now())
    AND (due_at IS NULL OR due_at <= now())
  ORDER BY
    COALESCE(rank, 1000000) ASC,
    priority DESC,
    created_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE tasks
SET agent_claim_owner = $1,
    agent_claim_until = $2,
    agent_claim_status = 'claimed',
    last_agent_update_at = now()
WHERE task_id IN (SELECT task_id FROM next_task)
RETURNING *;
```

실제 SQL은 현재 schema, priority 정렬 규칙, dependency table 도입 여부에 맞춰 조정한다.

SQLite fallback에서는 단일 worker/agent claim을 기본 가정으로 두고, PostgreSQL 수준의 multi-worker 보장을 약속하지 않는다.

## 5. 지금 설치하지 않는 이유

현재 PostgreSQL 설치를 시작하지 않는 이유:

- 앱의 P0 가치는 DB 종류보다 CLI/API/MCP/file bridge/worker가 같은 task source를 안정적으로 공유하는 데 있다.
- SQLite로 이미 단일 사용자 로컬 운영과 smoke test를 진행할 수 있다.
- PostgreSQL 설치는 Windows service, 계정, 인증, 백업, 복구, migration까지 한 번에 운영 범위를 늘린다.
- adapter 경계가 정리되기 전에 설치하면 code path가 SQLite와 PostgreSQL 사이에서 어중간하게 갈라질 수 있다.
- 현재 다음 구현 우선순위는 worker dispatch, notification attempt, API write 보안, web/tray UX, file sync conflict다.

## 6. 설치를 시작해도 되는 trigger

아래 중 하나가 실제로 발생하면 PostgreSQL slow-track을 active track으로 올린다.

- Cline/on-prem 또는 Deepagents 등 여러 agent가 같은 task DB를 동시에 claim해야 한다.
- worker, notification dispatcher, external adapter가 여러 프로세스로 상시 동작한다.
- outbox/attempt/event 이력이 장기 보관되어 SQLite 파일 운영 부담이 커진다.
- Teams, OpenProject, SMTP/RSS adapter가 상시 연결되고 retry/observability 요구가 커진다.
- 사용자가 명시적으로 Windows 11 로컬 PostgreSQL 설치와 DB 계정 생성을 승인한다.
- repository abstraction, database URL, migration 분리, PostgreSQL claim query 설계가 구현 준비 상태가 된다.

설치를 시작할 때도 순서는 다음과 같다.

1. SQLite export/backup을 먼저 만든다.
2. PostgreSQL 설치 dry-run checklist를 통과한다.
3. 앱 DB user/db/schema를 만든다.
4. PostgreSQL migration을 빈 DB에 적용한다.
5. SQLite seed import를 테스트 DB에 반복 검증한다.
6. CLI/API/MCP/worker smoke를 PostgreSQL URL로 돌린다.
7. cutover 시점에 SQLite를 read-only snapshot으로 보존한다.

## 7. 관련 초안 스크립트

설치 준비 점검용 초안은 `scripts/setup-postgres.ps1`에 둔다.

이 스크립트는 기본적으로 dry-run이며, 실제 설치, service 생성, DB 계정 생성, 비밀번호 저장을 수행하지 않는다. 실제 설치 단계로 바꾸려면 별도 사용자 승인과 구현 변경이 필요하다.
