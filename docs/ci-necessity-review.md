# CI Necessity Review

Updated: 2026-05-03

## Decision

Add a minimal GitHub Actions CI workflow.

## Why CI Is Needed

The repository is intended to stay public and agent-editable. The risk is not high-scale production failure yet; the real risk is accidental breakage of the shared surfaces that make TMH useful:

- CLI command parsing,
- package importability,
- SQLite schema initialization,
- task creation,
- dry-run runner policy path,
- loopback API docs and OpenAPI generation.

These are cheap to test and easy to break during incremental agent work.

## Why CI Should Stay Small

TMH is still a local-first Windows MVP. CI should not pretend to validate:

- real Cline authentication,
- live Deepagents runtime,
- Windows tray behavior,
- external email/Teams/OpenProject delivery,
- PostgreSQL production behavior.

Those need targeted local or pilot tests, not generic CI.

## Initial CI Scope

Run on `windows-latest`:

1. Install Python.
2. Install the package editable without tray extras.
3. Run `python -m compileall task_memory_hub`.
4. Run `tmh --help`.
5. Create a task in a temporary SQLite DB.
6. Run a dry-run runner pass.
7. Start `tmh-web` against the temp DB.
8. Check `/health/ready`, `/docs`, and `/openapi.json`.

## Stop Condition

This CI is sufficient until the project adds:

- committed test suite,
- stable Postgres adapter,
- live backend adapter,
- packaged Windows installer release,
- external delivery adapters.

When those land, add focused jobs instead of expanding this smoke test into a broad integration harness.
