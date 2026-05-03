# Task Memory Hub Design Guide

This file is a TMH-specific design delta. Use the default Codex `webapp-builder` skill as the baseline for frontend workflow, responsive layout, accessibility, and implementation quality. Apply this file only where TMH needs stricter product-specific guidance.

Task Memory Hub is an operator control plane, not a marketing site. The UI should help a human quickly understand what work exists, where it came from, who can act on it, what authority exists, and what can be stopped or approved.

## Product Feel

- Quiet, dense, readable, and operational.
- Korean-first labels for user-facing task operations.
- English is acceptable for stable technical terms such as CLI, API, MCP, Cline, Deepagents, runner, backend, JSON, and SQLite.
- Favor tables, inspector panels, timelines, and compact metrics over hero sections or decorative layouts.
- Avoid decorative blobs, oversized cards, and generic AI-product visuals.

## Primary Screens

### Task List

The first screen should answer:

- What needs attention now?
- Which tasks are due, blocked, waiting for review, or in progress?
- Which agents are active?
- Are notification/outbox/runner paths healthy?

Use:

- compact task table,
- status and review filters,
- due and priority indicators,
- active agent/runtime summary,
- visible quick-add entrypoint.

### Task Detail

The task detail page is the control-plane inspector. It must make these visible:

- title, next action, status, priority, due,
- source workspace and source principal,
- target principal and active runtime,
- harness and policy references,
- claim owner, lease, and claim status,
- approval/review-gate state,
- stop semantics,
- event timeline,
- execution contract and artifact contract.

### Review Gate

Review gates must feel like human decision checkpoints. A review gate screen should emphasize:

- subject task,
- requested reason,
- side-effect class,
- risk tier,
- proposed backend or external delivery,
- approve/reject/request-changes actions,
- event trail.

## Interaction Rules

- Use buttons for actions and links for navigation.
- Avoid hidden state changes. After a write action, reload or visibly update the page.
- Destructive or external-side-effect actions need explicit approval language.
- Stop controls must not imply a hard process kill unless the backend supports it.
- Never execute task prose as commands.
- Keep copy short. The UI is for repeated operational use.

## Layout Rules

- Use a constrained shell with full-width operational sections.
- Use panels for inspectors, forms, timelines, and repeated task cards.
- Do not nest cards inside cards.
- Keep border radius at 8px or less.
- Use stable grid dimensions for status strips, tables, toolbars, and buttons.
- Preserve responsive behavior down to narrow mobile widths.
- Avoid viewport-based font scaling.

## Visual Tokens

- Background: cool neutral.
- Panel: white.
- Text: high-contrast near-black.
- Accent: restrained teal for primary actions and links.
- Danger: red for reject/stop.
- Warning: amber for review/pending states.
- Radius: 7-8px.
- Shadow: subtle, only to separate operational surfaces.

## Accessibility

- Keep one clear `h1` per page.
- Preserve keyboard focus states.
- Use native buttons, links, inputs, selects, and textareas when possible.
- Make every action button text explicit.
- Avoid text overlap and horizontal overflow.
- Keep table content readable with wrapping or horizontal scrolling.

## Agent Editing Rules

When an AI agent changes Web UI code:

1. Inspect `task_memory_hub/api.py`, `task_memory_hub/static/app.css`, and `task_memory_hub/static/task-detail.js` first.
2. Keep HTML rendering simple and escaped.
3. Prefer shared CSS classes over inline styles.
4. Prefer `data-*` action hooks over inline JavaScript handlers.
5. Update `/docs`, `/docs/reference`, and `/openapi.json` when API routes change.
6. Run compile, API smoke, and browser checks when UI behavior changes.
