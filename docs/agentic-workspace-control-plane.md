# Agentic Workspace Control Plane

작성일: 2026-05-01

## 1. 왜 이 문서가 필요한가

초기 Task Memory Hub는 workspace 안의 TODO를 시간 기반으로 재호출하고, Cline/agent가 다시 이어서 수행할 수 있게 하는 로컬 알람/작업 메모리 앱으로 출발했다. 이후 사용 중 확인된 중요한 구조 변화는 다음과 같다.

- workspace마다 `.tmh/tmh.sqlite`가 생기는 구조는 git의 `.git`처럼 workspace-local state에 가깝다.
- Windows tray와 개인 알림은 하나의 workspace보다 global hub가 더 자연스럽다.
- global hub에서 특정 workspace로 작업을 배정하려면 `source_workspace`만으로는 부족하다.
- 어떤 workspace가 존재하는지, 누가 등록했는지, 어떤 agent harness가 있는지, 어떤 정보를 다룰 수 있는지에 대한 registry가 필요하다.

따라서 Task Memory Hub에는 두 층이 필요하다.

1. **Data Plane**
   - task, event, reminder, outbox, attempt
   - 현재 구현된 CLI/API/MCP/worker가 주로 다루는 영역

2. **Control Plane**
   - workspace registry
   - principal/person/agent identity
   - authority/provenance
   - harness/agent roster
   - network endpoint and sync topology
   - policy and capability boundary

이 문서는 Control Plane의 초기 설계다.

## 2. Drift 점검

### 원래 방향

초기 설계의 핵심은 다음이었다.

- Cline Memory Bank 위에 별도 실행 계층을 둔다.
- STDIO MCP를 Cline 통합 기본 경로로 둔다.
- Web/API/worker/CLI가 하나의 task service layer를 공유한다.
- DB가 source of truth다.
- due task를 outbox에 넣고 알림을 dispatch한다.

### 지금 논의의 확장

새 논의는 다음으로 확장됐다.

- workspace-local DB와 global hub DB의 이중 구조
- workspace source/target routing
- 누가 workspace를 등록했는지에 대한 authority
- 사람/agent identity와 role
- harness 내 agent 목록, 역할, capability, data access policy
- network endpoint와 sync boundary

### Drift 판단

이 확장은 **기능 drift가 아니라 control-plane 확장**으로 보는 것이 맞다.

이유:

- agent가 task를 안전하게 이어받으려면 "어느 workspace에서 실행해야 하는가"가 필요하다.
- global hub가 workspace task를 aggregate하려면 "등록된 workspace인지"를 알아야 한다.
- agentic workspace에서 "누가 이 작업을 제안/승인/배정했는가"는 중복 실행과 권한 문제를 줄인다.
- 향후 on-prem Cline, Codex, Deepagents, local script가 공존하려면 harness registry가 필요하다.

단, 주의할 점:

- Control Plane이 너무 빨리 복잡해지면 알람앱 MVP가 늦어진다.
- 따라서 지금은 schema와 context field 설계를 먼저 고정하고, 구현은 registry 최소 기능부터 시작한다.

## 3. 계층 구조

```text
Global Hub
├─ Principal Registry
│  ├─ human principals
│  ├─ agent principals
│  └─ service principals
├─ Workspace Registry
│  ├─ workspace identity
│  ├─ path/repo/project metadata
│  ├─ owner/maintainer/registrar
│  └─ sync policy
├─ Harness Registry
│  ├─ agent roster
│  ├─ role/capability profile
│  ├─ data access scope
│  └─ execution endpoints
├─ Task Routing
│  ├─ source workspace
│  ├─ target workspace
│  ├─ source principal
│  ├─ proposed by / approved by
│  └─ sync status
└─ Network Registry
   ├─ local API endpoint
   ├─ MCP command endpoint
   ├─ sync endpoint
   └─ trust boundary
```

Workspace-local DB:

```text
Workspace .tmh DB
├─ task data plane
├─ local events
├─ local outbox
├─ local harness snapshot
└─ sync pointers to global hub
```

## 4. 핵심 ID 모델

### Workspace ID

`workspace_id`는 사람이 읽는 이름이 아니라 stable ID여야 한다.

현 구현 생성:

```text
ws_<hash(canonical_path)>
```

이 결정은 2026-05-01 Round 1 구현 중 확정했다. `repo_remote`를 ID에 포함하면 MCP처럼 git 감지를 끈 경로와 CLI처럼 git remote를 감지한 경로가 같은 workspace를 서로 다른 ID로 등록할 수 있다. 따라서 path를 identity로 두고, `repo_remote`와 `repo_branch`는 metadata로 유지한다.

필드:

| 필드 | 설명 |
|---|---|
| `workspace_id` | stable workspace identity |
| `workspace_slug` | 사람이 읽는 짧은 이름. 예: `task-memory-hub` |
| `display_name` | UI 표시명 |
| `canonical_path` | 로컬 절대 경로 |
| `repo_remote` | git remote URL 또는 logical URI |
| `repo_branch` | 기본 브랜치 또는 현재 브랜치 |
| `workspace_type` | `project`, `personal`, `research`, `ops`, `archive` |
| `registration_status` | `proposed`, `active`, `disabled`, `archived` |
| `created_at` / `updated_at` | registry timestamp |

### Principal ID

principal은 사람, agent, service를 모두 포함한다.

```text
principal_id = pr_<uuid/hash>
```

필드:

| 필드 | 설명 |
|---|---|
| `principal_id` | stable identity |
| `principal_type` | `human`, `agent`, `service` |
| `display_name` | 예: `김현중`, `codex`, `cline-onprem` |
| `contact_ref` | 이메일, local account, service name 등 |
| `auth_method` | `local_user`, `mcp_stdio`, `api_token`, `manual`, `unknown` |
| `trust_level` | `owner`, `trusted`, `limited`, `untrusted` |
| `active` | 비활성 principal 차단 |

### Harness ID

harness는 특정 workspace에서 agent들이 어떤 도구와 정책으로 실행되는지 나타내는 runtime 구성이다.

```text
harness_id = har_<workspace_id>_<profile>
```

필드:

| 필드 | 설명 |
|---|---|
| `harness_id` | stable harness identity |
| `workspace_id` | 연결 workspace |
| `harness_name` | 예: `default-local`, `cline-onprem`, `codex-cli` |
| `harness_type` | `cline`, `codex`, `deepagents`, `script`, `hybrid` |
| `runtime_surface` | `mcp`, `cli`, `api`, `scheduler` |
| `status` | `active`, `paused`, `disabled` |
| `policy_profile_id` | capability/data access policy |
| `network_profile_id` | endpoint/trust boundary |

## 5. Authority 모델

workspace/source/target 등록에는 최소한 아래 authority chain이 필요하다.

| 필드 | 설명 |
|---|---|
| `registered_by_principal_id` | 실제 등록을 수행한 주체 |
| `proposed_by_principal_id` | 등록을 제안한 주체 |
| `approved_by_principal_id` | 승인한 주체 |
| `authority_basis` | `owner_request`, `agent_suggestion`, `imported_config`, `manual`, `policy` |
| `authority_level` | `owner`, `maintainer`, `operator`, `agent_suggested`, `unverified` |
| `approval_status` | `proposed`, `approved`, `rejected`, `revoked` |
| `approval_note` | 승인 근거/메모 |
| `registered_at` / `approved_at` | 시간 |

원칙:

- agent가 workspace를 자동 등록할 수는 있지만 기본 상태는 `proposed`다.
- 사람이 승인해야 `active`가 된다.
- global hub가 task를 workspace로 route하려면 target workspace가 `active`여야 한다.
- `unverified` workspace로는 자동 pull/claim을 하지 않는다.

## 6. Source / Target / Routing Field

현재 `source_workspace`는 문자열이다. 앞으로는 ID 기반으로 확장해야 한다.

Task routing 필드:

| 필드 | 설명 |
|---|---|
| `source_workspace_id` | task가 처음 만들어진 workspace |
| `source_workspace_slug` | 사람이 읽는 source 이름 |
| `target_workspace_id` | task가 실행되어야 할 workspace |
| `target_workspace_slug` | 사람이 읽는 target 이름 |
| `source_principal_id` | task를 만든 주체 |
| `target_principal_id` | 처리 책임자. 없으면 workspace harness가 claim |
| `proposed_by_principal_id` | task 제안자 |
| `approved_by_principal_id` | task 승인자 |
| `assigned_by_principal_id` | workspace/agent에 배정한 주체 |
| `routing_reason` | 왜 target이 이 workspace인지 |
| `routing_confidence` | `manual`, `rule`, `agent_high`, `agent_low` |
| `routing_status` | `local_only`, `pending_push`, `pushed`, `pending_pull`, `pulled`, `synced`, `conflict` |
| `origin_task_id` | 원본 DB의 task ID |
| `hub_task_id` | global hub의 task ID |

중요한 해석:

- `source`는 생성 출처다.
- `target`은 실행 장소다.
- `source_principal`은 누가 만들었는가다.
- `target_principal`은 누가 처리해야 하는가다.
- `approved_by`는 왜 이 route가 신뢰 가능한가다.

## 7. Harness / Agent Roster

agentic workspace에는 harness registry가 있어야 한다.

Agent roster 필드:

| 필드 | 설명 |
|---|---|
| `agent_id` | stable agent identity |
| `principal_id` | principal registry와 연결 |
| `harness_id` | 속한 harness |
| `agent_name` | 예: `cline`, `codex`, `deepagents-doc-agent` |
| `role` | `planner`, `coder`, `reviewer`, `dispatcher`, `doc_writer`, `ops` |
| `claim_scope` | 어떤 task를 claim할 수 있는지 |
| `write_scope` | 수정 가능한 path/API 범위 |
| `read_scope` | 읽을 수 있는 데이터 범위 |
| `secret_scope` | 접근 가능한 secret reference 범위 |
| `tool_scope` | 사용 가능한 MCP/API/CLI tool 목록 |
| `max_autonomy` | `suggest_only`, `claim_and_report`, `edit_local`, `dispatch`, `external_write` |
| `requires_human_approval` | external write/secret/large change 여부 |
| `status` | `active`, `paused`, `disabled` |

예:

```json
{
  "agent_name": "cline-onprem",
  "role": "coder",
  "claim_scope": ["target_workspace_id = current", "priority in high,urgent"],
  "write_scope": ["workspace_files", "tmh_tasks"],
  "tool_scope": ["tmh-mcp", "local-shell"],
  "max_autonomy": "edit_local",
  "requires_human_approval": ["external_api_write", "secret_access"]
}
```

## 8. Policy / Capability / Data Classification

task와 workspace에는 데이터 민감도와 capability policy가 있어야 한다.

Policy fields:

| 필드 | 설명 |
|---|---|
| `classification` | `public`, `internal`, `restricted`, `secret` |
| `redaction_level` | context pack redaction 수준 |
| `allowed_principal_types` | `human`, `agent`, `service` |
| `allowed_agent_roles` | claim 가능한 역할 |
| `allowed_tools` | tool allowlist |
| `blocked_tools` | tool denylist |
| `external_write_allowed` | Teams/OpenProject/email 등 외부 write 허용 |
| `requires_approval_for` | approval gate 목록 |
| `retention_policy` | event/context 보관 |

이 policy는 harness가 task를 claim하기 전 검사해야 한다.

## 9. Network / Endpoint 모델

network registry는 "어디로 어떻게 연결할 수 있는가"를 기록한다.

Endpoint fields:

| 필드 | 설명 |
|---|---|
| `network_profile_id` | network profile identity |
| `workspace_id` | 연결 workspace |
| `api_base_url` | 예: `http://127.0.0.1:8787` |
| `mcp_transport` | `stdio`, `http`, `sse` |
| `mcp_command` | STDIO command |
| `sync_endpoint` | global hub 또는 remote API |
| `bind_scope` | `loopback`, `tailscale`, `lan`, `remote` |
| `auth_profile_id` | token/credential reference |
| `last_seen_at` | heartbeat |
| `network_status` | `online`, `offline`, `unknown` |

원칙:

- 기본은 loopback-only다.
- global hub sync도 초기에는 local file/API sync로 충분하다.
- Tailscale/remote sync는 별도 authority와 token 정책이 있어야 한다.
- network endpoint는 task 본문에 직접 저장하지 않고 registry reference로 연결한다.

## 10. 권장 Context Pack 확장

기존 `ai_context_pack`는 실행 재개에 초점이 있다. 이제 routing/harness context와 분리해야 한다.

권장 구조:

```json
{
  "version": "0.2",
  "objective": "...",
  "current_state": "...",
  "next_action": "...",
  "acceptance_criteria": [],
  "routing": {
    "source_workspace_id": "ws_...",
    "target_workspace_id": "ws_...",
    "source_principal_id": "pr_...",
    "proposed_by_principal_id": "pr_...",
    "approved_by_principal_id": "pr_...",
    "routing_status": "synced"
  },
  "harness": {
    "harness_id": "har_...",
    "allowed_agent_roles": ["coder", "reviewer"],
    "required_capabilities": ["local-shell", "tmh-mcp"],
    "blocked_capabilities": ["external-email-send"]
  },
  "policy": {
    "classification": "internal",
    "redaction_level": 1,
    "external_write_allowed": false
  },
  "provenance": {
    "origin_task_id": "tmh_...",
    "hub_task_id": "tmh_...",
    "created_by": "pr_...",
    "last_human_update_by": "pr_..."
  }
}
```

주의:

- context pack은 task 실행용 압축 정보다.
- registry의 canonical data를 전부 복사하지 말고 ID/reference 중심으로 둔다.
- secret value는 절대 넣지 않는다.

## 11. Sync 의미 재정의

`push`:

```text
workspace-local task -> global hub
```

목적:

- 전체 알림함에 보이게 함
- tray/global due view에서 확인
- workspace status를 hub에 투영

Push snapshot profile:

| Profile | 의미 |
|---|---|
| `manifest` | routing/provenance/fetch ref 중심. 상세 문맥은 source workspace에서 fetch |
| `normal` | manifest + summary + next_action + preview. 기본값 |
| `full` | normal + detail/context. 같은 신뢰 경계에서만 사용 |

원칙:

- global hub는 full context 저장소가 아니라 역추적 가능한 manifest/control plane이다.
- source workspace DB가 canonical source of truth다.
- hub task의 `source_workspace_id + origin_task_id`로 원본 task를 fetch할 수 있어야 한다.
- `detail_md`와 큰 `ai_context_pack`은 기본 push에서 확산시키지 않는다.

`pull`:

```text
global hub task where target_workspace_id = current workspace -> workspace-local DB
```

목적:

- global hub나 다른 workspace/agent가 현재 workspace에 배정한 task를 가져옴
- target workspace가 active/approved registry entry일 때만 자동 pull 가능

`sync`:

```text
push + pull + conflict check
```

초기 구현은 `push only`가 안전하다. `pull`은 workspace registry와 authority check가 들어간 뒤 구현한다.

## 12. 구현 영향

새로 필요한 테이블:

- `principals`
- `workspaces`
- `workspace_authority`
- `harnesses`
- `harness_agents`
- `policy_profiles`
- `network_profiles`
- `sync_links`
- `sync_events`

기존 `tasks`에 추가/정리할 필드:

- `source_workspace_id`
- `target_workspace_id`
- `source_principal_id`
- `target_principal_id`
- `proposed_by_principal_id`
- `approved_by_principal_id`
- `assigned_by_principal_id`
- `routing_status`
- `origin_task_id`
- `hub_task_id`
- `harness_id`
- `policy_profile_id`

하지만 당장 모든 필드를 구현하지 않는다.

권장 단계:

1. workspace registry 최소 구현
2. principal registry 최소 구현
3. `tmh workspace register` 추가
4. task에 `source_workspace_id`, `target_workspace_id`, `source_principal_id` 추가
5. global hub DB scope 추가
6. push only sync 구현
7. authority-approved pull 구현
8. harness registry와 agent roster 구현

## 13. 현재 구현 대비 중요 변경점

Round 1 이전 구현:

- `source_workspace`는 문자열이다.
- `source_agent`는 문자열이다.
- claim owner도 문자열이다.
- global hub scope가 없다.
- workspace registry가 없다.

Round 1 이후 구현:

- 문자열 field는 유지하되 backward compatibility alias로 본다.
- 새 ID field가 task schema에 추가됐다.
- workspace/principal registry가 추가됐다.
- global hub scope와 push-only sync가 추가됐다.
- workspace 등록 없이는 global push/pull을 하지 않는 방향으로 유지한다.
- agent claim owner는 장기적으로 `principal_id` 또는 `agent_id`로 연결한다.
- `target_workspace` 없는 task는 local-only 또는 personal/global task로 본다.

## 14. 결론

이번 확장은 Task Memory Hub를 단순 알람앱에서 agentic workspace control plane으로 확장한다. 이는 원래 목표와 충돌하지 않는다. 오히려 여러 workspace, 여러 agent, global tray hub, on-prem Cline을 안전하게 연결하려면 필요한 구조다.

단기 구현은 다음 원칙을 따른다.

- registry 먼저, sync 나중
- push 먼저, pull 나중
- authority 없는 자동 배정 금지
- ID field 추가, 기존 문자열 field는 호환 유지
- harness/policy/network는 reference 중심으로 설계

## 15. 2026-05-01 Round 1 구현 상태

구현 완료:

- `principals`
- `workspaces`
- `sync_links`
- `sync_events`
- task routing fields
- CLI `workspace register/list/show`
- CLI `principal ensure/list`
- CLI `--global`
- CLI `push`
- MCP `register_current_workspace`
- MCP `push_to_global_hub`
- CLI/API/MCP `fetch-origin`
- `push --profile manifest|normal|full`

검증 완료:

- workspace 등록 idempotency
- principal 등록 idempotency
- local task 2개를 global hub로 push
- 두 번째 push에서 duplicate 없이 update 처리
- 다른 폴더 local DB와 global hub view 분리
- MCP direct client로 registry/push tool 확인
- profile별 push 두께 확인
- hub task에서 origin task fetch 확인

보류:

- `pull`
- automatic target assignment
- harness/policy/network enforcement
- PostgreSQL adapter
