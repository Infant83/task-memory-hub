# Public Release Plan

Updated: 2026-05-03

## Release Position

The repository is being prepared as a public-facing MVP codebase. The goal is to publish reusable product code, public design documents, deterministic smoke scripts, and a clear roadmap while keeping local runtime data, private audit notes, and personal machine paths out of the tracked public surface.

## Push Scope

Keep tracked:

- `task_memory_hub/`
- `scripts/`
- `installers/`
- `AGENTS.md`
- `README.md`
- `pyproject.toml`
- `requirements.txt`
- `task-memory-hub-설계명세.md`
- public docs under `docs/`

Do not track:

- `.tmh/`
- `.cline-test/`
- `.vscode/`
- SQLite databases
- API token files
- internal handoff logs
- private audit transcripts
- machine-local absolute paths
- real API keys, webhook URLs, passwords, private keys, or auth tokens

## Changes Applied For Public HEAD

- `.vscode/settings.json` removed from git tracking.
- `docs/handoff-progress-log.md` removed from git tracking.
- `docs/ralph/` removed from git tracking.
- `.gitignore` updated so those local-only artifacts stay out of future commits.
- Public progress and release-plan documents added.
- README rewritten as public project documentation instead of an internal session log.
- Agent guide updated to point to public summaries instead of internal audit transcripts.

The removed files remain available locally when present in the working tree, but they are no longer part of the public push scope.

## Current Risk Assessment

### Critical

No live credential value was found in the currently tracked HEAD by the local pattern scan.

### High

The public `main` branch should be published from a clean orphan/squashed snapshot, not from the earlier development history. Removing files from the latest commit does not erase old commits, so this release uses clean history before keeping the repository public.

Residual caveat:

If the repository was public before the clean-history push, old commit contents may have been visible externally. The clean public branch reduces future exposure but cannot retroactively guarantee that nobody saw earlier history.

### Medium

The Web UI is a local control plane and still exposes read routes without authentication on loopback. This is acceptable for the current local-first MVP, but browser-facing routes must keep Host/Origin checks and write-token requirements.

### Medium

Interactive docs are enabled by design for the local loopback API. They should not be exposed on a remote/public network binding.

## Final Public-Ready Checklist

- [x] Runtime DB and local token paths ignored.
- [x] Internal handoff and audit transcript files removed from tracked HEAD.
- [x] Public README created.
- [x] Public progress summary created.
- [x] Public release plan created.
- [x] MIT license added.
- [x] Minimal CI necessity reviewed.
- [x] Minimal Windows smoke CI added.
- [x] Windows PowerShell bootstrap install path documented.
- [x] Run final current-HEAD secret scan.
- [x] Run current-HEAD personal path scan.
- [x] Run compile and API smoke checks.
- [x] Generate clean public history from a clean snapshot.
- [x] Add CI for compile, minimal CLI smoke, and API docs smoke.

## MVP Quality Bar

The public MVP should demonstrate:

- Local-first install and execution in a few commands.
- A Web UI that is readable enough for daily task inspection.
- A task detail screen with provenance, authority, runtime, harness, claim, events, and stop/approval controls.
- CLI/API/MCP parity over the same service layer.
- Safe script execution through allowlisted command references only.
- Clear docs explaining what is implemented, what is intentionally local-only, and what is not safe to enable yet.
