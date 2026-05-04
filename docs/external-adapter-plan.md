# 외부 연동 어댑터 계획

Updated: 2026-05-04

## 목적

TMH의 외부 연동은 task를 실제 외부 시스템에 쓰기 전에 사람이 볼 수 있는 review gate, delivery dry-run, event trail을 통과해야 한다. 이 문서는 OpenProject, Outlook 이메일, Teams, Webhook 연동의 현재 방침을 고정한다.

## 공통 원칙

- task DB와 task event log가 source of truth다.
- 실제 외부 write는 `delivery dry-run`과 review gate 이후에만 붙인다.
- task 본문, `next_action`, `detail_md`에 적힌 URL이나 명령은 실행하지 않는다.
- secret 값은 저장하지 않는다. `auth_profile_ref`, `secret_ref`, 환경변수 이름 같은 reference만 저장한다.
- 모든 외부 write는 성공/실패와 payload summary를 `task_events`에 남긴다.
- adapter별 실제 전송은 capability와 policy profile을 요구한다.

## OpenProject

현재 사용 가능한 입력:

- `OPENPROJECT_BASE_URL`
- `OPENPROJECT_API_KEY`

권장 범위:

- work package 조회
- work package activity/comment 추가
- 제한적 description 업데이트
- status 변경은 별도 승인 후 허용

구현 방침:

1. `OPENPROJECT_BASE_URL`과 `OPENPROJECT_API_KEY`는 환경변수에서 읽는다.
2. task에는 직접 API key를 저장하지 않고 `auth_profile_ref=openproject-default` 같은 reference만 둔다.
3. write 전에는 work package를 먼저 읽어 `lockVersion`을 확보한다.
4. PATCH가 필요한 작업은 변경 diff summary를 먼저 `delivery dry-run` event에 남긴다.
5. 실제 write adapter는 `openproject-write` capability와 승인된 review gate를 요구한다.
6. 502/timeout은 무한 retry하지 않고 `delivery_failed` event로 남긴다.

초기 pilot 후보:

```powershell
tmh delivery dry-run tmh_example --channel openproject --recipient-ref openproject:work-package-52 --requires-review
```

## Outlook 이메일

현재 방침:

- Windows Outlook Desktop COM, 즉 `win32com.client` 기반으로 pilot한다.
- SMTP password나 mail API secret을 TMH에 저장하지 않는다.
- 기본 동작은 `draft` 생성이다. 실제 send는 별도 승인 이후로 미룬다.

권장 범위:

- Outlook 실행 가능 여부 확인
- 계정/보낼 편지함 접근 가능 여부 확인
- draft 메일 생성
- 첨부파일은 workspace-local artifact path reference만 사용

구현 방침:

1. `pywin32`는 optional dependency로 둔다.
2. `delivery dry-run`에서 제목, 수신자 reference, 첨부 artifact 목록만 검증한다.
3. 첫 pilot은 `CreateItem(0)`으로 draft를 만들고 바로 보내지 않는다.
4. 실제 send는 `external-email-send` capability, review gate 승인, 그리고 별도 `--send` 명시 옵션이 모두 있을 때만 허용한다.

## Teams

현재 상태: 보류.

보류 이유:

- Teams Graph API, webhook, desktop automation 중 어떤 경로를 쓸지 아직 결정하지 않았다.
- 조직 정책과 인증 방식에 따라 구현 난이도와 보안 리스크가 크게 달라진다.
- 먼저 OpenProject와 Outlook draft pilot으로 외부 write 제어 패턴을 검증한다.

## Webhook

Webhook은 기능을 바로 확정하지 않고 다음 세 가지 중 우선순위를 정한다.

### 1. Outbound delivery webhook

TMH task event나 artifact summary를 외부 endpoint로 POST한다.

위험:

- raw webhook URL 저장 위험
- secret header 노출 위험
- retry storm 위험

필수 조건:

- endpoint는 raw URL이 아니라 `network_profile` 또는 `webhook_profile` reference로 등록한다.
- auth는 `auth_profile_ref`만 사용한다.
- idempotency key와 retry limit을 둔다.
- 기본은 dry-run이다.

### 2. Incoming task webhook

외부 시스템이 TMH에 task를 생성하거나 update하도록 받는다.

위험:

- local-first loopback 모델과 충돌
- 외부 노출 시 인증, Host/Origin, replay 방어 필요

권장:

- 현재는 보류한다.
- 필요하면 Tailscale 또는 로컬 reverse proxy의 auth 정책이 먼저 있어야 한다.

### 3. Local hook

외부 HTTP가 아니라 workspace-local script/hook reference를 호출한다.

위험:

- arbitrary script 실행으로 drift 가능

권장:

- 이미 구현된 `script_ref` allowlist backend를 우선 사용한다.
- Webhook이라는 이름으로 shell command를 실행하지 않는다.

## 다음 구현 순서

1. OpenProject read-only smoke: 환경변수 확인, `/api/v3` 접근, current user 또는 work package 조회.
2. OpenProject dry-run write plan: comment/update payload summary를 event에 남긴다.
3. Outlook COM smoke: Outlook profile 접근 가능 여부와 draft 생성 가능 여부를 확인한다.
4. Outlook draft adapter: 실제 send 없이 draft만 생성한다.
5. Webhook profile 설계: outbound/incoming/local hook 중 무엇을 먼저 지원할지 결정한다.
6. Teams는 계속 보류한다.

## 삭제/보류 조건

다음 조건이면 adapter 구현을 보류한다.

- secret 값을 DB나 task payload에 저장해야 한다.
- review gate 없이 외부 write가 가능해야 한다.
- 실패/성공 event를 남길 수 없다.
- raw URL이나 raw command를 task에서 직접 실행해야 한다.
- 사람이 Web UI에서 어떤 외부 시스템에 무엇이 나가는지 볼 수 없다.
