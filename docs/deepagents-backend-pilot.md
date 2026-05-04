# Deepagents Backend Pilot

## 목적

TMH의 harness runner가 Deepagents 같은 상주/headless agent backend를 호출할 수 있는지 확인한다. P2의 목표는 live model execution이 아니라 runner/backend contract, event trail, timeout, 실패 처리, operator visibility를 먼저 고정하는 것이다.

## 현재 원칙

- TMH의 source of truth는 DB와 task event log다.
- Deepagents는 core runtime이 아니라 runner backend 중 하나다.
- Runner는 task prose를 shell command로 실행하지 않는다. Backend command는 operator가 명시한 명령만 사용한다.
- Backend stdout은 artifact/event summary로 저장할 수 있지만 raw chain-of-thought는 저장하지 않는다.
- P2 smoke는 deterministic stub를 사용한다. On-prem Deepagents live run은 사용자 환경에서 별도 인증/네트워크 조건을 확인한 뒤 활성화한다.

Live API smoke는 선택 사항이다. `OPENAI_API_KEY`가 설정되어 있고 `deepagents`, `langchain-openai`, `langgraph`가 설치되어 있으면 다음 스크립트로 최소 호출을 확인한다.

```powershell
python scripts\tmh-deepagents-live-smoke.py --prompt "TMH를 한 문장으로 설명해줘."
```

`OPENAI_BASE_URL`이 있으면 해당 endpoint를 사용한다. 없으면 기본 OpenAI endpoint를 사용한다. 이 스크립트는 API key 값을 출력하지 않는다.

## Backend Contract

Runner가 `deepagents_cli` backend를 사용할 때:

1. task/context pack에서 prompt를 구성한다.
2. `--backend-command`를 `shlex.split(..., posix=False)`로 분해한다.
3. command에 `--prompt`가 없으면 runner가 prompt를 뒤에 추가한다.
4. process timeout은 `--timeout-seconds`로 제한한다.
5. return code 0이면 `reasoning_summary`와 `artifact_reported` event를 남긴다.
6. return code가 0이 아니면 `failed` event를 남기고 runtime heartbeat를 idle로 되돌린다.

## Smoke Stub

P2 검증용 deterministic backend:

```powershell
python scripts\tmh-deepagents-smoke.py --prompt "hello"
python scripts\tmh-deepagents-smoke.py --exit-code 7 --prompt "failure check"
```

Runner 호출 예:

```powershell
tmh runner once --backend deepagents_cli --backend-command "python scripts\tmh-deepagents-smoke.py" --timeout-seconds 30 --capability tmh-api --capability deepagents-cli --json
```

## Live Deepagents 연결 시 확인할 것

- 외부 Deepagents checkout의 CLI entrypoint와 실제 one-shot 실행 옵션.
- Live model auth/network availability.
- 작업별 prompt/context size limit.
- 중간 heartbeat/progress를 TMH에 보고할 방법.
- cooperative stop polling 지점.
- 산출물 경로를 `artifact_reported` event에 남기는 형식.

## P2 Drift Guard

이 단계에서는 TMH가 Deepagents 내부 구현을 흡수하지 않는다. TMH는 orchestration, policy, task event log, audit visibility를 담당하고, Deepagents는 실행 backend로만 연결한다.
