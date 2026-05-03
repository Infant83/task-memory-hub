# Implementation Sanity Check

작성일: 2026-05-01

## 현재 판단

현재 Task Memory Hub는 CLI/API/MCP/tray/worker가 같은 SQLite DB를 바라보는 핵심 scaffold는 검증됐다. 다음 구현은 기능을 넓히기보다, 이미 설계한 목표가 drift 없이 운영 가능한 형태가 되도록 안정성 레이어를 채워야 한다.

## 유지할 것

- SQLite 기본 운영: 현 단계에서는 유지한다.
- MCP/CLI/API 공통 서비스 계층: 유지한다.
- Windows tray는 thin launcher/controller: 유지한다.
- Markdown/JSON은 import/export 먼저: 유지한다.
- PostgreSQL 전환 가능성: schema와 service boundary에서 계속 고려한다.

## 지금 하지 않을 것

- PostgreSQL 설치/adapter 구현: 별도 slow-track로 준비만 한다.
- Teams/OpenProject adapter: core outbox dispatch가 안정된 뒤 진행한다.
- watched Markdown/JSON sync: conflict event 정책 구현 전까지 보류한다.
- 복잡한 SPA UI: 지금은 최소 HTML + REST action으로 충분하다.
- 다중 사용자 인증/권한 모델: local single-user app 범위를 유지한다.

## 지금 추가해야 할 것

1. Worker outbox dispatcher
   - due task를 job으로 enqueue하는 것에서 끝나면 알림앱이 아니다.
   - `notification_attempts`에 성공/실패/skip을 기록해야 한다.

2. Local notification adapter
   - 처음에는 외부 dependency 없는 `local` adapter를 둔다.
   - 실제 Windows toast는 optional channel로 붙인다.
   - 실패해도 task state를 망가뜨리지 말고 attempt에 기록한다.

3. API write token/CSRF
   - loopback-only라도 browser drive-by write를 줄여야 한다.
   - 다음 구현 라운드에서 write token을 넣는다.

4. Minimal web actions
   - Today/Inbox/detail에서 ack/snooze/done 버튼을 붙이면 사람이 바로 쓸 수 있다.

5. Tray quick actions
   - due count, open today, quick add, pause/resume 정도만 우선한다.

## 삭제하거나 뒤로 미룰 후보

- PostgreSQL 즉시 설치: 현재 blocker가 아니므로 미룬다.
- Teams/OpenProject 선구현: outbox retry와 secret storage 전에는 위험하다.
- watched sync 선구현: conflict 처리 전에는 데이터 손상 위험이 있다.
- 과한 UI framework 도입: 현재 앱 목표에 비해 빌드/운영 복잡도가 크다.

## 다음 구현 결정

다음 코딩 범위는 `worker dispatch + local notification attempt 기록`이었다. 2026-05-01에 1차 구현과 smoke 검증을 완료했다.

구현 원칙:

- `notification_jobs`는 durable queue다.
- `notification_attempts`는 성공/실패 증거다.
- dispatch 성공 후 task를 `notified`로 전이해 반복 알림 폭주를 막는다.
- 실패는 retry/backoff로 남기고 task 자체는 깨지 않는다.
- adapter는 interface로 두어 Windows toast, SMTP, RSS, Teams, OpenProject로 확장 가능하게 한다.

다음 범위:

- tray quick actions
- optional Windows toast channel 개선
- Markdown/JSON conflict-aware import
- RSS/SMTP adapter
