---
title: worktree-ui-improvement — branch technical summary
description: What the UI-improvement branch contributes (by commit), why it stalled, what a merge must preserve, and what is queued behind it
timestamp: 2026-07-20 12:00:00
tags:
  - branch
  - ui-improvement
  - frontend
type: branch-technical-info
status: active
maintained_by_session: 6cf7ad59-4217-43f9-8d30-4e9ec9c8e34c
---

# `worktree-ui-improvement` — Branch Technical Summary

## Identity

| Field | Value |
|---|---|
| Branch | `worktree-ui-improvement` |
| Worktree | `working/.claude/worktrees/ui-improvement` |
| HEAD | `2c1a3bdf` — "Fix _ExprEvaluator trace divergences from Polars semantics" |
| Active window | 2026-04-21 → 2026-06-24 (dormant since) |
| Position vs `main` | ~447 ahead / 63 behind (as of `main` post-PR-#108, 2026-07-20) |
| Driving session | "UI IMPROVEMENTS" (`local_bba5c3fe…`, not archived, idle since 2026-07-09) |
| Landing model | single accumulated PR (per the workstream's standing plan) |

## Purpose

The single long-running UI-improvement feature branch: canvas interaction, panel/editor
affordances, the wrapper/submodel programme (#8/#9), and trace visualisation. Over half its diff is
in `frontend/src/`. **None of its feature payload exists on `main`** — it is not superseded.

## Contributions

### Corpus/test instrumentation (April)
- `7c23a80c`, `401672e7` — `data-testid` attributes across ~30 corpus UI elements.
- `916a253a` — SettingsModal with testid for corpus coverage.

### Canvas & interaction
- `3eb7f226` (2026-06-18) — canvas pan / right-click collision fix (the BUGS.md "Scrolling
  difficulties" entry; a variant later reached `main` independently — see Reconcile #4).
- `0b7f1036` (2026-06-24) — space + drag to pan (trackpad-friendly).
- `bee377dd` — grey out "Group into wrapper" when grouping isn't possible.

### Run button & panels
- `c584ad23`, `5148dc3a`, `4cd0e310` (2026-06-17) — Run button: Shift+Enter runs the selection,
  data-sink export indicator, slimmed width.
- `eae57f90` (2026-06-18) — Escape-to-cancel on the bands + git-confirm dialogs (a11y).
- `d0e1267c` (2026-06-18) — `NODE_GROUP_COLORS` tokenised to CSS variables.

### Input bindings
- `b13cbf11` + `bfced49c` (2026-06-18) — user-editable input binding aliases (backend + frontend
  selector).

### Wrapper / submodel programme (#8/#9)
- `b122a686`, `431f7ef3`, `112b64c3` (2026-06-19) — per-frame I/O side-pane; one boundary
  interface component per frame; frames derived from edges.
- `3d4938e8`, `7017d5a2` (2026-06-19) — Peek v2: scrollable window into the wrapper canvas +
  wrapper I/O boundary in the peek window.
- `e1c284b4`, `dbc674d6`, `9456b165`, `fadec316` — Peek v3: real node cards (read-only mini
  canvas), navigable/resizable screen-space panel, whole-submodel open with canvas-style nav,
  tested refit-on-resize hook.
- `a4c0eb00` — Wrapper terminology + "Open" in the right-click menu.
- `a9e260b6` — create-wrapper hardening: client-side nesting guard, "submodel" → "Wrapper" copy.

### Trace visualisation (#3)
- `8eff12ff` — hover-trace works on selected nodes (hit-test through the selection overlay).
- `c99f1a49` — trace flows THROUGH wrappers; wrapper glows on the path.
- `9bb29ba4` — peek: light the internal data-path inside an open Wrapper Peek.
- `163028ad` — hover-trace lights the full data path; brighten lit nodes; dim off-path edges.
- `03d136dc` — review follow-ups: hover edge-cases, peek-lighting perf, test gaps.

### Backend sleeper (the tip)
- `2c1a3bdf` (2026-06-24) — `_ExprEvaluator` operator-fidelity fixes in
  `src/haute/_expression_parser.py` (the Kleene / arithmetic divergence cluster) + a 368-line
  Polars-parity test (`tests/test_expression_parser_polars_parity.py`). This is the **AR-1 cluster
  the code-review audit deferred as accept-risk on `main`**. `main`'s W3 work (`7c442749`) edited a
  different region of the same file (value-laundering removal, `_ExprConverter`); the two
  **auto-merge cleanly** (verified via `git merge-tree`). Landing this branch closes a deferred risk.

## Why it stalled

The **#9 editing layer** (wrapper rename/select/create) is blocked on the variadic-out foundation;
that blocker was never cleared, and the branch has been dormant since 2026-06-24. Queued behind it:
#7 per-node colour.

## Merge-reconcile checklist

`main` moved under the branch during the 2026-07 fix wave (PRs #72–#108). Dry-run conflict surface
(`git merge-tree --write-tree`) is ~30 files, concentrated in `App.tsx`, `Toolbar.tsx`,
`NodePanel.tsx`, `DataPreview.tsx`, `usePipelineAPI.ts`, four backend files, and a set of trivial
docs conflicts (`main` deleted/moved docs the branch edited). Four **semantic** reconciles, not
textual merges:

1. **Editor undo contract** — 13 branch commits touch `panels/editors`/`NodePanel`, which `main`
   rewrote with `CommittedTextField` (PR #89: commit-on-blur, one undo per field edit). Branch
   editor edits predate that contract and must adopt it, not overwrite it.
2. **Column-stash source keys** — `main`'s PR #95 stamps `node.data._columnsSource` at the
   `usePipelineAPI` applyPreview chokepoint and strips mismatched stashes on source switch.
   `usePipelineAPI.ts` is in conflict; the source-tag mechanism must survive the merge (key-contract
   pins: `frontend/src/hooks/__tests__/columnStashSourceIdentity.test.ts`).
3. **Edge-join semantics** — the standing nick-dev edge-join lineage-divergence ruling: semantic
   reconcile, not blind merge, of edge-join behaviour at merge time.
4. **Pan-gesture overlap** — `3eb7f226` (branch) vs the pan/context-menu fixes that reached `main`
   independently (PR #38 era): keep one gesture controller, don't stack both.

## Queued behind this branch (and NOT fixed inside it)

Three open `notes-haute/common/BUGS.md` items are paced on this branch **only because they share
files with it** — verified none of the fixes exist in the branch:

> NOTE: **Stale-trace CRITICAL** (trace panel narrates the old pipeline after an edit/watcher
> sync): branch `useTracing.ts` has no `structuralVersion` keying and branch `useWebSocketSync.ts`
> has no trace invalidation. The branch *adds* trace UI (hover/wrapper lighting) on top of the
> unfixed staleness, widening the exposure — fix at/after merge.

- **Frontend under-keying residuals** (notes-haute DYLE §Fingerprint / cache-key completeness): the
  concern is merge-survival of PR #95, not a branch feature.
- **a11y HIGHs** (DataPreview keyboard cells, `App.tsx` lazy-loading, Toolbar status dot): the
  branch rewrites these files (testids, Run-button split, column resize) but adds none of the a11y
  semantics.

## Decision pending (Nick)

Land whole (unblock or descope #9 first) **or split**: land the finished
wrapper/trace/pan/evaluator work now and leave #9 on a stub branch — releasing the three
queued-behind fix items without waiting on the variadic-out design.
