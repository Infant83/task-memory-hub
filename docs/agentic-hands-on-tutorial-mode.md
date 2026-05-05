# TMH Agentic Hands-on Tutorial Mode

작성일: 2026-05-05

## 목적

이 문서는 TMH를 처음 배우는 사용자를 위해 에이전트가 어떤 방식으로 핸즈온 튜토리얼을 진행해야 하는지 규격화한다. 목표는 단순 기능 시연이 아니라 사용자가 다음을 직접 확인하게 만드는 것이다.

- 작업이 어디에 저장되는지
- 사람, agent, workspace, harness가 어떻게 기록되는지
- Web UI, CLI, API, MCP가 같은 task DB를 어떻게 보는지
- 승인, claim, runner dry-run, event trail이 왜 필요한지
- 이후 다른 세션에서도 같은 단계에서 이어서 학습할 수 있는지

이 문서는 강사용 운영 모드이고, 세부 커리큘럼은 `docs/newbie-hands-on-curriculum.md`를 따른다.

## 활성화 문구

사용자가 다음과 비슷하게 말하면 에이전트는 이 모드로 전환한다.

- `tmh 핸즈온 튜토리얼을 시작하자`
- `다시 핸즈온을 시작하자`
- `TMH 튜토리얼 이어서 하자`
- `핸즈온 N단계부터 하자`
- `agentic hands-on tutorial mode로 진행하자`

명시적인 단계가 없으면 에이전트는 먼저 현재 진행 상태를 확인한다. 진행 상태를 찾을 수 없으면 모듈 0부터 시작한다.

## Source Of Truth 순서

튜토리얼을 진행하는 에이전트는 다음 순서로 자료를 확인한다.

1. `AGENTS.md`: 저장소 전체 운영 규칙과 안전 경계
2. `docs/agentic-hands-on-tutorial-mode.md`: 튜토리얼 진행 방식
3. `docs/newbie-hands-on-curriculum.md`: 단계별 학습 목차
4. `docs/verification-manual.md`: 검증 명령과 smoke test
5. `docs/web-ui-screen-guide.md`: Web UI 화면의 안정적 의미
6. `README.md`: 설치, 실행, 현재 구조

구현이나 명령어가 바뀌어 문서와 실제 CLI가 다를 수 있으면, 실제 `tmh --help`와 하위 명령 `--help` 출력을 우선 확인한다. 차이가 발견되면 문서를 고치는 작업을 별도 task로 남긴다.

## 진행 원칙

튜토리얼 모드는 설명과 실행을 분리하지 않는다. 각 단계는 다음 순서로 진행한다.

1. 목표 설명: 이번 단계에서 무엇을 이해해야 하는지 짧게 설명한다.
2. 안전 경계 확인: 외부 발송, destructive command, global setting 변경이 있는지 확인한다.
3. 실습 명령 제시: 사용자가 따라 칠 수 있는 명령을 먼저 보여준다.
4. 가능하면 에이전트가 실행: 현재 워크스페이스에서 안전한 명령은 직접 실행하고 결과를 요약한다.
5. 관찰 포인트 설명: 출력에서 어떤 필드를 봐야 하는지 설명한다.
6. Web UI 확인: 가능한 단계에서는 `http://127.0.0.1:8787/`, `/control`, `/docs` 중 필요한 화면을 연결한다.
7. 완료 기준 확인: 사용자가 무엇을 말할 수 있으면 단계가 끝난 것인지 정리한다.
8. 진행 상태 기록: `TMH 핸즈온 진행 상태` task에 progress event를 남기거나, 최소한 최종 답변에 다음 시작점을 명시한다.

한 번에 너무 많은 명령을 몰아서 실행하지 않는다. 초보 학습에서는 한 단계의 핵심 관찰이 끝난 뒤 다음 단계로 넘어간다.

## 진행 상태 기록 규칙

튜토리얼 진행 상태는 가능하면 TMH 자체에 남긴다. 단, 공개 저장소 파일에 개인별 진행 로그를 커밋하지 않는다.

권장 progress task:

- 제목: `TMH 핸즈온 진행 상태`
- 우선순위: `normal`
- rank: `900`
- tag: `tutorial`
- next action: 다음에 진행할 모듈

처음 시작할 때 task가 없으면 다음 형태로 하나만 만든다.

```powershell
tmh add "TMH 핸즈온 진행 상태" --summary "TMH 학습 진행 상태를 추적한다." --next "모듈 0부터 시작한다." --priority normal --rank 900 --tag tutorial --by owner
```

각 모듈을 시작하거나 마칠 때는 같은 task에 progress event를 남긴다.

```powershell
tmh progress <task_id> "모듈 0 시작: source of truth와 local/global DB 차이를 확인한다." --owner tutor
tmh progress <task_id> "모듈 0 완료: tmh status와 db-info를 확인했다." --owner tutor
tmh update <task_id> --next "모듈 1 설치와 Hub Station 시작"
```

이미 비슷한 task가 여러 개 있으면 새로 만들지 말고 사용자가 어떤 task를 계속 쓸지 확인한다. 진행 상태가 전혀 확인되지 않으면 마지막으로 확인된 문서상 커리큘럼 기준 모듈 0부터 시작한다.

## 단계별 운영 형식

각 모듈은 다음 템플릿으로 진행한다.

```text
모듈 N. 제목

목표:
- 이번 단계에서 이해할 개념

먼저 볼 것:
- CLI/Web UI/API 중 확인할 화면 또는 명령

실습:
1. 명령 또는 UI 조작
2. 출력 확인
3. 이벤트 또는 DB 상태 확인

관찰 포인트:
- 출력에서 봐야 할 필드
- 흔한 오해

완료 기준:
- 사용자가 설명할 수 있어야 하는 문장

다음 단계:
- 다음 모듈 이름
```

에이전트의 최종 답변에는 항상 다음 시작점을 남긴다.

예:

```text
다음에 "핸즈온 이어서 하자"라고 하면 모듈 2, 첫 작업 등록과 확인부터 시작하면 됩니다.
```

## 학습 레벨

### Level 0. 방향 잡기

대상: 처음 보는 사용자

핵심 질문:

- TMH는 일반 TODO 앱과 무엇이 다른가?
- DB가 source of truth라는 말은 무슨 뜻인가?
- Web UI와 CLI가 같은 작업을 보는가?

모듈: 0, 1, 2

### Level 1. Todo Everywhere

대상: CLI와 Web UI를 같이 쓰려는 사용자

핵심 질문:

- CLI로 만든 작업이 Web UI에 보이는가?
- due, rank, priority는 어떻게 다르게 쓰는가?
- JSON/Markdown bridge는 DB와 어떤 관계인가?

모듈: 2, 3

### Level 2. Agentic Workspace

대상: Cline, Codex, Deepagents 같은 agent를 TMH와 연결하려는 사용자

핵심 질문:

- workspace, principal, harness는 왜 등록 대상인가?
- agent principal과 active runtime은 어떻게 다른가?
- claim과 progress event는 왜 필요한가?

모듈: 4, 5

### Level 3. Governance And Runner

대상: 승인, review gate, runner dry-run, audit trail을 이해하려는 사용자

핵심 질문:

- 사람이 어떤 지점에서 승인하거나 멈출 수 있는가?
- runner dry-run은 실제 실행과 무엇이 다른가?
- event trail을 보면 작업의 완결성과 재현성을 설명할 수 있는가?

모듈: 6, 7

### Level 4. Hub And Pilot Operations

대상: 다른 workspace, Cline MCP, 운영 점검까지 확인하려는 사용자

핵심 질문:

- global hub에는 어느 정도의 context가 올라가는가?
- origin task를 역추적할 수 있는가?
- Cline MCP 파일럿은 direct MCP smoke와 무엇이 다른가?
- Hub Station, tray, startup, backup을 어떻게 점검하는가?

모듈: 8, 9, 10

## 안전 경계

튜토리얼 중에는 다음을 하지 않는다.

- 실제 이메일, Teams, OpenProject, webhook 발송
- 사용자 승인 없는 global Cline MCP 설정 변경
- raw command string, task prose, `next_action`, `detail_md`를 shell command로 실행
- 공개 저장소에 로컬 DB, token, webhook URL, 개인별 진행 로그 커밋
- PostgreSQL 설치를 기본 단계로 강제

튜토리얼 중 허용되는 기본 실행:

- `tmh --help`
- `tmh db-info`
- `tmh status`
- `tmh add`, `tmh list`, `tmh show`, `tmh events`
- `tmh update`, `tmh progress`, `tmh done`
- `tmh runner once --backend dry_run`
- loopback Web UI/API health check

## 재개 절차

사용자가 `다시 핸즈온을 시작하자`라고 말하면 에이전트는 다음 절차를 따른다.

1. `AGENTS.md`와 이 문서를 확인한다.
2. `docs/newbie-hands-on-curriculum.md`에서 모듈 목록을 확인한다.
3. `tmh list --limit 50` 또는 적절한 조회로 `TMH 핸즈온 진행 상태` task가 있는지 확인한다.
4. task가 있으면 `tmh events <task_id>`로 마지막 완료 모듈과 next action을 확인한다.
5. task가 없으면 모듈 0부터 시작하되, 사용자가 특정 단계를 말하면 그 단계부터 시작한다.
6. 시작 전에 현재 환경을 빠르게 확인한다: `tmh db-info`, `tmh status`, Web UI health.
7. 다음 시작점을 최종 답변에 남긴다.

진행 task가 없거나 명령이 실패해도 튜토리얼을 멈추지 않는다. 실패 자체를 운영 점검 학습의 일부로 설명하고, 안전한 최소 명령부터 다시 시작한다.

## 모듈 0 시작 스크립트

모듈 0은 튜토리얼의 기본 출발점이다. 에이전트는 다음 흐름으로 시작한다.

```powershell
tmh --help
tmh db-info
tmh status
tmh list --limit 5
```

설명해야 할 내용:

- `tmh`는 CLI 표면이고, Web UI/API/MCP와 같은 DB를 본다.
- local DB와 global hub DB는 다르다.
- 작업은 title만이 아니라 status, priority, rank, due, source, target, harness, events를 가진다.
- 초보자는 먼저 “작업을 만들고, 보고, 완료하고, event를 확인하는 루프”를 익혀야 한다.

모듈 0 완료 문장:

```text
TMH는 작업 목록 그 자체보다, 작업이 생성되고 승인되고 agent/runtime이 처리하고 완료되는 과정을 DB와 event log에 남기는 로컬 우선 control plane이다.
```

