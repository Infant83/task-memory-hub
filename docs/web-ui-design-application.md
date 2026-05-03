# Web UI Design Application

Updated: 2026-05-03

## Source Signal

The email search for `DESIGN.md` found one message titled `AI한테 디자인을 시키려면 DESIGN.md부터 만들어보세요...` dated 2026-05-01. The message body only contained a YouTube Shorts link, so there was no reusable source text or template to copy.

## Decision

Use the existing Codex `webapp-builder` skill as the default Web UI build process. Keep repo-local `DESIGN.md` only as a TMH-specific delta for product semantics, screen priorities, and operator-control-plane constraints.

`DESIGN.md` is not a replacement for `webapp-builder`. Its advantage is local specificity:

- TMH is an operator control plane, not a marketing site.
- provenance, authority, review gate, runtime, and stop semantics must stay visible.
- Korean-first task operation labels are preferred.
- route/API doc updates must stay coupled to UI action changes.

It should not duplicate generic responsive-layout, accessibility, or polished-UI rules already covered by `webapp-builder`.

## Application To TMH

TMH is a control-plane app. The Web UI should prioritize:

- operator scan speed,
- provenance and authority,
- approval/review-gate visibility,
- active agent/runtime state,
- stop and release semantics,
- event timeline,
- task contract readability.

It should not drift toward:

- marketing-page structure,
- decorative AI visuals,
- oversized hero sections,
- single-purpose demo pages,
- hidden automation state.

## Implementation Hooks

- `DESIGN.md` is the source for product/UI design rules.
- `webapp-builder` remains the baseline skill for implementation workflow and quality bar.
- `task_memory_hub/static/app.css` carries shared visual tokens and layout classes.
- `task_memory_hub/static/task-detail.js` carries action behavior through `data-*` attributes.
- `task_memory_hub/api.py` remains the stdlib HTML renderer and must keep output escaped.

## Next UI Iteration

1. Add task-list filters for due, blocked, review gate, active, and completed.
2. Add first-screen metrics for due tasks, open review gates, active agents, and blocked work.
3. Add review-gate visual states to task rows and detail pages.
4. Add an operator status panel for worker/outbox/runner readiness.
5. Keep the task detail page as the most complete inspector surface.
