# WS-13 — Assistant (full stack)

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-13 · Status: delivered in PR #147.

**Branch:** `opus5/ws-13-assistant`

## Mission

The in-app assistant: the backend turn loop, session store, tool surface and packaged
authoring assets, plus the assistant panel and its streaming store. Self-contained — it
shares almost nothing with the rest of the codebase — which makes it the cleanest parallel
worktree in the split. The headline defect permanently bricks a session.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| assistant | 0 | 1 | 4 | 6 |
| frontend-assistant-ui | 0 | 0 | 4 | 5 |
| **Total** | **0** | **1** | **8** | **11** |

## Priorities

**P1 — session-breaking and lock-leaking:**

- `assistant-1` (H): a client disconnect between `tool_started` and the result append
  persists an assistant message with `tool_calls` and no matching result. Both providers
  reject that conversation, so every later turn fails with `AssistantProviderError` until the
  orphan ages out of the 40-message window. Record the result before emitting the events (or
  drop unmatched `tool_calls` in `_append_round`), with a regression that closes the
  generator at each of the three yields.
- `assistant-7` (L, but same class): a raising `store.append` in `run_turn`'s `finally` leaks
  the session lock permanently — 409 forever.
- `frontend-assistant-ui-2` (M): `turnStatus` is not the lock the spec claims — set after an
  await and cleared unconditionally in `finally`.
- `frontend-assistant-ui-3` (M): a parser throw or transport error never cancels the response
  body, so the backend turn keeps running and mutating the pipeline.
- `assistant-2` (L): tool-result `is_error` is dropped on the history round-trip, so failed
  tools are replayed to the model as successes.

**P2 — correctness and safety:** `assistant-3` (hard-coded 4-extension dataset allowlist
hides 12 supported formats and advertises `.xml`, which the I/O registry cannot read),
`assistant-6` (`get_dataset_schema` has no hidden-file or denylist filter, so `.haute/` state
or a credentials file can be previewed into the provider request), `assistant-11` (orphaned
`.json.tmp` files are never pruned, so persisted-session storage is not actually bounded),
`assistant-5` (all three packaged exemplars teach the anti-pattern the authoring guide
forbids), `frontend-assistant-ui-11` (post-terminal events silently discarded),
`frontend-assistant-ui-4` (send-time 400/404/409 leaves an orphaned user message and an empty
assistant bubble with no turn marker).

**P3 — UI polish and spec truth:** `frontend-assistant-ui-5` (`assistant-markdown` has no CSS
rule anywhere, so markdown renders flat), `frontend-assistant-ui-6` (every streamed token
re-renders the whole transcript and re-parses every entry's markdown — memoise),
`frontend-assistant-ui-8` (send gates implemented twice; the store's copy is unreachable),
`frontend-assistant-ui-9` (three documented working-branch states vs the backend's five),
`frontend-assistant-ui-12` (module-map rows misdescribe the panel's inputs), plus the
assistant-side contract fold and hygiene (`assistant-4`, `contracts-d-5`, `assistant-8` dead
second prompt builder, `assistant-9` hand-duplicated node list with no completeness guard).

## Finding inventory

High (1): `assistant-1`.
Medium (8): `assistant-3`, `assistant-4`, `assistant-5`, `contracts-d-5`,
`frontend-assistant-ui-2`, `frontend-assistant-ui-3`, `frontend-assistant-ui-5`,
`frontend-assistant-ui-6`.
Low (11): `assistant-2`, `assistant-6`, `assistant-7`, `assistant-8`, `assistant-9`,
`assistant-11`, `frontend-assistant-ui-4`, `frontend-assistant-ui-8`,
`frontend-assistant-ui-9`, `frontend-assistant-ui-11`, `frontend-assistant-ui-12`.

## File ownership (exclusive)

- `src/haute/assistant/**` (`_loop.py`, `_session.py`, `_tools.py`, `_providers.py`,
  `_catalog.py`, `_config.py`, `assets/**`)
- `src/haute/routes/assistant.py`
- `frontend/src/stores/useAssistantStore.ts`, `frontend/src/api/assistant.ts`,
  `frontend/src/panels/assistant/**` (`AssistantPanel.tsx`, `Composer.tsx`,
  `TranscriptEntryView.tsx`)
- `docs/specs/assistant/**`, `docs/specs/frontend-assistant-ui/**`
- Their tests (`tests/test_assistant*.py`, `frontend/src/**/__tests__` assistant suites)

## Cross-stream touchpoints

- `frontend/src/index.css` (shared): `frontend-assistant-ui-5` needs one scoped
  `.assistant-markdown` block — additive, low conflict risk, but announce it.
- `assistant-3`'s real extension source is `routes/files.py`'s `_installed_input_extensions()`
  (WS-04) — consume it rather than duplicating the list.
- `assistant-9`'s node-name list should derive from `_types.py` (WS-06) or gain a
  completeness guard; the packaged asset lives here.
- `frontend-assistant-ui-9`'s five working-branch states come from Git state (WS-12) — keep
  the vocabulary aligned.
- `assistant-5`'s exemplars must teach the authoring pattern WS-06's decorator/spec work
  settles — check before rewriting them.

## Definition of done

- No client disconnect can persist an orphaned tool call, and no exception path can leak the
  session lock — both with regression tests.
- The turn lock and abort path actually cancel the backend turn; `is_error` survives the
  history round-trip.
- Dataset listing and schema preview cannot expose hidden/state/credential files; session
  storage is genuinely bounded.
- Assistant contract sections folded and deleted; markdown renders with real typography.
- Baseline entries for both components deleted; findings fixed or deferred with reasons.

## Verification

- `uv run pytest tests/test_assistant_loop.py tests/test_assistant_session.py tests/test_assistant_tools.py -q`
  (use the actual suite names in `docs/specs/assistant/low-level.md` after correcting them)
- `npm --prefix frontend test -- src/panels/assistant src/stores`
- `npm --prefix frontend run typecheck`; `uv run pytest tests/test_docs_accuracy.py -q`.
