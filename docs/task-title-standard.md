# TMH 작업 제목 작성 표준

작성일: 2026-05-03

## 1. 목적

TMH의 작업 제목은 사람이 Web UI, tray, CLI, MCP, global hub에서 가장 먼저 보는 운영 신호다. 따라서 새 작업 제목은 기본적으로 한국어로 작성한다. 영어 기술 용어는 필요할 때만 괄호나 원문 약어로 보존한다.

## 2. 기본 규칙

- 새 작업 제목은 한국어를 기본으로 쓴다.
- 제목만 보고 무엇을 해야 하는지 알 수 있어야 한다.
- 제목은 가능하면 `대상 + 행동 + 산출/검증` 구조로 쓴다.
- 단계 표기는 필요한 경우 제목 맨 앞에 `P0-1`, `P1`, `긴급` 같은 짧은 접두어로 둔다.
- Cline, MCP, Deepagents, TMH, API, JSON, SQLite처럼 고유명사나 표준 기술 약어는 그대로 쓸 수 있다.
- 구현 세부 설명, 배경, 긴 조건은 `summary`, `next_action`, `detail_md`, `execution_contract`에 둔다.
- 영어 문장형 제목은 쓰지 않는다. 단, 외부 시스템에서 온 제목을 원문 보존해야 할 때는 `source_title` 또는 detail에 원문을 남기고, TMH 제목은 한국어로 요약한다.

## 3. 권장 형식

| 종류 | 형식 | 예시 |
|---|---|---|
| 구현 작업 | `[단계] 대상 기능 구현` | `P0-2 드라이런 하니스 러너 once/watch 구현` |
| 검증 작업 | `[단계] 대상 시나리오 검증` | `P0-4 거버넌스 차단 시나리오 테스트` |
| 문서 작업 | `[단계] 문서/규약 정리` | `P0-1 거버넌스 용어와 이벤트 규약 확정` |
| 파일럿 | `[단계] 대상 파일럿` | `P2 Deepagents 상주 백엔드 파일럿` |
| 알림 | `시점/대상 행동` | `내일 아침 등산 준비` |

## 4. 나쁜 예와 수정 예

| 피할 제목 | 수정 제목 |
|---|---|
| `governance vocabulary and event convention` | `거버넌스 용어와 이벤트 규약 확정` |
| `dry-run harness runner once/watch` | `드라이런 하니스 러너 once/watch 구현` |
| `runner events and Web UI visibility` | `러너 이벤트와 Web UI 가시성 보강` |
| `approval and stop workflow` | `승인 및 중지 워크플로 구현` |
| `Cline MCP on-prem pilot checklist` | `Cline MCP 온프렘 파일럿 체크리스트` |

## 5. Agent 작성 규칙

Agent가 TMH에 작업을 남길 때는 아래 순서를 따른다.

1. 제목을 한국어로 먼저 작성한다.
2. 작업 단계가 중요하면 `P0-1`, `P1` 같은 접두어를 붙인다.
3. 영어 기술 용어는 고유명사나 검색 키워드가 필요한 경우에만 유지한다.
4. 제목에 모든 맥락을 넣지 않는다.
5. 원문 영어 제목이나 외부 ticket 제목이 필요하면 detail에 보존한다.
6. `tmh add`, MCP `create_task`, API `POST /v1/tasks`, Markdown/JSON import 모두 같은 제목 규칙을 따른다.

## 6. 향후 구현 후보

지금은 표준을 문서와 agent 운영 규칙으로 적용한다. 이후 필요하면 다음 기능을 추가한다.

- `tmh lint-title <title>` 또는 `tmh add --strict-title`
- Web UI quick-add 제목 warning
- MCP create task 시 한국어 제목 권고 warning
- global hub push 전 제목 표준 audit
