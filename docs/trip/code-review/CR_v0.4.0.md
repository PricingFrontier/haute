# Code Review: Pricing Copilot

**Review Date**: 2026-07-19  
**Version**: 0.4.0  
**Files Reviewed**:

- `docs/specs/README.md`
- `docs/specs/copilot/high-level.md`
- `docs/specs/copilot/low-level.md`
- `docs/specs/frontend-copilot-ui/high-level.md`
- `docs/specs/frontend-copilot-ui/low-level.md`
- `docs/specs/frontend-graph-canvas/high-level.md`
- `docs/specs/frontend-node-editors/high-level.md`
- `docs/specs/frontend-shared/high-level.md`
- `docs/specs/frontend-shared/low-level.md`
- `docs/specs/server-api/high-level.md`
- `docs/specs/server-api/low-level.md`
- `docs/trip/README.md`
- `docs/trip/changelog/changelog_table.md`
- `docs/trip/plans/F_0.4.0_pricing-copilot.plan.md`
- `frontend/package-lock.json`
- `frontend/package.json`
- `frontend/scripts/check-bundle-size.mjs`
- `frontend/src/App.tsx`
- `frontend/src/__tests__/App.copilotLazy.test.ts`
- `frontend/src/api/__tests__/copilot.test.ts`
- `frontend/src/api/client.ts`
- `frontend/src/api/copilot.ts`
- `frontend/src/components/Toolbar.tsx`
- `frontend/src/panels/copilot/Composer.tsx`
- `frontend/src/panels/copilot/CopilotPanel.tsx`
- `frontend/src/panels/copilot/TranscriptEntryView.tsx`
- `frontend/src/stores/__tests__/useCopilotStore.test.ts`
- `frontend/src/stores/__tests__/useUIStore.exclusivity.test.ts`
- `frontend/src/stores/useCopilotStore.ts`
- `frontend/src/stores/useUIStore.ts`
- `mkdocs.yml`
- `pyproject.toml`
- `src/haute/copilot/__init__.py`
- `src/haute/copilot/_assets.py`
- `src/haute/copilot/_catalog.py`
- `src/haute/copilot/_config.py`
- `src/haute/copilot/_loop.py`
- `src/haute/copilot/_ops.py`
- `src/haute/copilot/_providers.py`
- `src/haute/copilot/_session.py`
- `src/haute/copilot/_tools.py`
- `src/haute/copilot/assets/authoring_guide.md`
- `src/haute/copilot/assets/examples/branched_features.py`
- `src/haute/copilot/assets/examples/joined_reference.py`
- `src/haute/copilot/assets/examples/linear_pricing.py`
- `src/haute/routes/_save_pipeline.py`
- `src/haute/routes/copilot.py`
- `src/haute/schemas.py`
- `src/haute/server.py`
- `tests/test_api_contracts.py`
- `tests/test_copilot_assets.py`
- `tests/test_copilot_catalog.py`
- `tests/test_copilot_config.py`
- `tests/test_copilot_integration.py`
- `tests/test_copilot_loop.py`
- `tests/test_copilot_ops.py`
- `tests/test_copilot_providers.py`
- `tests/test_copilot_routes.py`
- `tests/test_copilot_tools.py`
- `tests/test_infrastructure_contracts.py`
- `tests/test_save_pipeline_integrity.py`
- `uv.lock`

**Plan**: `docs/trip/plans/F_0.4.0_pricing-copilot.plan.md`

---

## Executive Summary

This change introduces an in-app pricing copilot with pluggable providers, graph-authoring tools, transactional save and broadcast behavior, SSE streaming, session management, and a lazy-loaded frontend panel. All findings were addressed during the review loop; the final per-round provider-stream fix landed after the last formal review and is recorded as pending final verification.

APPROVED with observations

---

## Changes Overview

The backend adds provider adapters, an agent loop, bounded sessions, graph-edit operations, knowledge assets, configuration/readiness handling, and copilot API routes. Mutations flow through the existing transactional save service and publish ordinary `graph.update` events while preserving `.py` as the source of truth.

The frontend adds the copilot transcript, composer, store-owned SSE state machine, readiness and mutation gates, session recovery, panel exclusivity, and lazy loading. Component specifications, API documentation, package metadata, and behavioral tests were updated alongside the implementation.

---

## Findings

### Critical Issues

None.

### Major Issues

1. **Graph updates published from a worker thread** — **Addressed.** Publishing now occurs on the event-loop thread while `save_lock` remains held at `src/haute/copilot/_tools.py:459-468`; the integration regression verifies thread identity at `tests/test_copilot_integration.py:121-158`.

2. **Limit-aborted turns persisted orphaned tool calls** — **Addressed.** Tool-call cap, deadline, and executor-configuration checks now precede recording the call at `src/haute/copilot/_loop.py:321-332`, preserving valid call/result history groups.

3. **Exact duplicate edges produced ambiguous graph state** — **Addressed.** `add_edge` now rejects identical endpoints and handles with a named validation error at `src/haute/copilot/_ops.py:414-427`; regression coverage is at `tests/test_copilot_ops.py:499`.

4. **Truncated or filtered provider output was reported as completion** — **Addressed.** `_map_stop_reason` raises typed `truncated` or `filtered` provider failures at `src/haute/copilot/_providers.py:129-150`, used by both adapters at `src/haute/copilot/_providers.py:365` and `src/haute/copilot/_providers.py:577-580`.

5. **Concurrent sends raced during awaited pre-stream work** — **Addressed.** Atomic reservation is implemented at `src/haute/copilot/_loop.py:233-248`, acquired before provider construction and graph parsing at `src/haute/routes/copilot.py:235-260`, with pre-stream 404/409 mapping and failure release at `src/haute/routes/copilot.py:241-275`.

6. **Disconnect before body iteration leaked the session lock** — **Addressed.** The idempotent reservation owner is defined at `src/haute/copilot/_loop.py:210-230`; `_ReservedStreamingResponse` releases it from the response lifecycle at `src/haute/routes/copilot.py:196-224`.

7. **Mid-stream send failure could release the session while a zombie turn remained active** — **Addressed.** The response closes its body iterator before releasing the reservation at `src/haute/routes/copilot.py:212-224`; `_event_stream` explicitly closes the inner turn at `src/haute/routes/copilot.py:182-193`, while shielded tools drain and preserve their original interrupt at `src/haute/copilot/_loop.py:190-207`.

8. **Abnormal turn exit left the active provider stream to GC cleanup** — **Addressed.** Provider streams are closed through `_aclose_quietly` at `src/haute/copilot/_loop.py:176-187`, with the outer abnormal-exit guard running before history append and reservation release at `src/haute/copilot/_loop.py:410-418`.

9. **Completed tool-use rounds were overwritten without closing their provider streams** — **Fixed post-review, pending final verification.** Each round now closes `active_stream` before the next round opens at `src/haute/copilot/_loop.py:311-316` and `src/haute/copilot/_loop.py:358-372`; the outer abnormal-exit guard remains at `src/haute/copilot/_loop.py:410-414`. Deterministic open/close ordering is covered at `tests/test_copilot_loop.py:770-817`, and the implementer reports 105 affected tests, mypy, and Ruff green.

### Minor Issues

1. **Send-time HTTP 400 discarded the backend’s actionable detail** — **Addressed.** The store renders the detail verbatim, refreshes readiness, and avoids a toast at `frontend/src/stores/useCopilotStore.ts:150-160`; regression coverage is at `frontend/src/stores/__tests__/useCopilotStore.test.ts:381-398`.

2. **The system prompt omitted graph node-count/type context** — **Addressed.** Node summaries are assembled at `src/haute/copilot/_loop.py:67-78`, included in the prompt at `src/haute/copilot/_loop.py:81-89`, and supplied from the parsed graph at `src/haute/routes/copilot.py:260-265`; tests are at `tests/test_copilot_loop.py:703-726`.

### Suggestions

None.

---

## Checklist

- [ ] 1. Functional Requirements — Passed with caveat: the post-review per-round stream-close fix awaits final independent verification.
- [x] 2. Code Quality — Passed.
- [ ] 3. Architectural Compliance — Passed with the same final-verification caveat; reviewed implementation otherwise matches the plan and updated component specifications.
- [x] 4. Haute Design Philosophy — Passed.
- [x] 5. Error Handling — Passed.
- [x] 6. Security — Passed.
- [ ] 7. Performance — Passed with caveat: deterministic cleanup of every provider round is fixed post-review and pending final verification.

---

## Verdict

**APPROVED with observations**

No findings were overridden and no Critical or Major issue remains knowingly open. The per-round provider-stream fix is present with deterministic regression coverage but landed after the final formal review round, so release verification should confirm its reported 105 affected tests and static checks. Two declared environment limitations remain non-blocking: `frontend/bun.lock` requires regeneration on a machine with Bun, as recorded at `docs/trip/plans/F_0.4.0_pricing-copilot.plan.md:169`, and five pre-existing Windows-only failures reproduce on clean `HEAD`—four unlink-while-open cases in `tests/test_train_service_coverage.py:279-540` and the case-resolution probe at `tests/test_path_case_audit.py:39`—while passing in Ubuntu CI.


---

## Post-review addendum (2026-07-19, pre-release)

The review above was conducted against the feature under its working name "copilot".
After the review synthesized, the following user-directed changes landed before release,
each TDD-first with its own verification (a dedicated reviewer agent for the
silent-wrongness classes, batch verification for mechanical work):

1. **Product rename "copilot" → "Assistant"** — full rename (package `haute.assistant`,
   `/api/assistant/*` routes, `[assistant]` config table, `Assistant*` schemas/stores/
   components, spec directories). Verified by the complete backend suite, contract
   snapshots, and the full frontend gate chain.
2. **Live-smoke dialect fixes** (captured against real Databricks serving endpoints):
   OpenAI-adapter content-part lists (`text`/`reasoning`), a strictly-guarded tolerance
   for Databricks' intermittently-omitted `finish_reason` (typed `truncated`/
   `malformed_stream` failures preserved), debug-level chunk-shape wire logging, and
   server-side provider-error diagnostics.
3. **Core parser fix (user-approved scope extension)** — removed the definition-order
   edge-invention fallbacks (`_build_edges`, `Pipeline.to_graph()`); disconnected graphs
   are now representable. ~1,100 affected tests green.
4. **Persistent chat sessions** — write-through to `.haute/assistant/sessions/`, resume
   via `POST /session`, frontend rehydration. A dedicated review of this delta
   (REQUEST_CHANGES → fixed) caught a Critical under-cap file-pruning bug (negative
   slice deleting oldest sessions); fixed with a regression test plus its Minor findings.
5. **Assistant ships in core** — `anthropic`/`openai` moved into core dependencies; the
   `haute[assistant]` extra was removed pre-release.
6. **`list_datasets` navigability** and POSIX-normalised model-facing paths.

## Known rough edges at release (0.4.0 ships as an initial working model)

- The final per-round provider-stream fix (finding 9) was regression-pinned but not
  independently re-verified by the original reviewer (round cap reached).
- `frontend/bun.lock` still needs regeneration on a machine with bun.
- Five pre-existing Windows-only test failures reproduce on clean HEAD (unlink-while-open
  in `test_train_service_coverage.py`, case-resolution probe in `test_path_case_audit.py`);
  they pass in ubuntu CI and are unrelated to this feature.
- Resumed transcripts do not rehydrate "Canvas updated" rows (not stored as messages),
  and a mismatched-pipeline resume briefly occupies an LRU slot — both accepted reviewer
  observations.
- Session persistence uses a fixed per-id tmp filename; concurrent multi-process servers
  on one clone are not a supported scenario.
- The UX (prompting guidance, transcript polish, model-choice ergonomics) is deliberately
  early-preview; the README labels the feature accordingly.
