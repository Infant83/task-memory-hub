# Script Ref Backend Allowlist

## 목적

`script_ref` backend는 TMH runner가 operator-approved script를 호출할 수 있게 하는 P4 실행 경로다. 핵심은 task 본문이나 task JSON에 들어온 raw command를 실행하지 않고, allowlist에 등록된 named command만 실행하는 것이다.

## Allowlist Format

```json
{
  "commands": {
    "p4-script-smoke": {
      "command": "python scripts\\tmh-script-backend-smoke.py",
      "description": "deterministic script_ref backend smoke"
    }
  }
}
```

`command` 대신 `args`를 사용할 수 있다.

```json
{
  "commands": {
    "p4-script-smoke": {
      "args": ["python", "scripts\\tmh-script-backend-smoke.py"]
    }
  }
}
```

## Task Contract

Task에는 command 자체가 아니라 ref만 둔다.

```json
{
  "required_capabilities": ["tmh-api", "script-ref"],
  "risk_tier": "low",
  "side_effect_class": "none",
  "runner_backend": {
    "command_ref": "p4-script-smoke"
  }
}
```

## Runner Command

```powershell
tmh runner once --backend script_ref --script-allowlist .\.tmh\script-backends.json --capability tmh-api --json
```

Runner는 script process에 다음 환경 변수를 전달한다.

- `TASK_MEMORY_HUB_TASK_ID`
- `TASK_MEMORY_HUB_TASK_JSON`
- `TASK_MEMORY_HUB_CONTEXT_PACK_JSON`
- `TASK_MEMORY_HUB_COMMAND_REF`

## Guardrails

- Task prose, `next_action`, `detail_md`, `execution_contract.command`, `backend_command`에서 command를 가져오지 않는다.
- allowlist 파일이 없거나 ref가 없으면 `policy_decision`에서 block 처리한다.
- 실제 외부 write, irreversible action, secret access는 P5 review gate와 secret-ref policy 이후에만 허용한다.

