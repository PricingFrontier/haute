# WS-10 — Frontend node editors & config/preview panels

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: unassigned · Status: not started.

**Branch:** `opus5/ws-10-frontend-editors-panels`

## Mission

Everything the user actually edits: the node editors, the modelling/optimiser config panels,
and the preview/Explore panes. These three components share `NodePanel.tsx`, `App.tsx` and
the `panels/editors/` tree, and their bugs share one shape — a config write that silently
loses another write, or a panel state that silently strands the user.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| frontend-node-editors | 0 | 2 | 4 | 6 |
| frontend-modelling-optimiser-ui | 0 | 1 | 9 | 3 |
| frontend-preview-explore | 0 | 0 | 5 | 5 |
| **Total** | **0** | **3** | **18** | **14** |

## Priorities

**P1 — panel crashes and silent config loss:**

- `frontend-node-editors-1` (H): a null-handle apiInput edge makes `edgeInputName` throw
  during `NodePanel` render, collapsing the whole right panel into its ErrorBoundary — no
  node can be edited until reload — and the spec's documented warning-chip path is
  unreachable dead code. Return the unresolved marker instead of throwing.
- `frontend-node-editors-3` (H): renaming an ordinary upstream node migrates nothing, so
  live-switch and instance mappings silently orphan and the pipeline fails at run time with
  `LiveSwitchScenarioError`; re-renaming back silently resurrects a mapping the user never
  re-authored. Route label changes through the same rename pipeline as frame renames.
- `frontend-modelling-optimiser-ui-3` (H): two consecutive single-key `onUpdate` calls both
  read the stale `configRef`, so changing the ratebook Banding source keeps the new node's
  `factor_columns` with the old node's `banding_source`. Use the atomic object form — and
  make the shared test double reproduce NodePanel's stale-ref spread so this class fails in
  CI.
- `frontend-node-editors-2` (M): Data Output write identity includes volatile preview/trace
  state, silently revoking the overwrite grant and erasing write results.
- `frontend-modelling-optimiser-ui-13` (M): renaming or removing a constraint loses its
  frontier range and orphans the entry in saved config.

**P2 — stranded UI states:** `frontend-modelling-optimiser-ui-12` (stale export-tab detail
permanently disables Save and MLflow log), `-2` (contract violation swallowed by the stale
guard leaves a permanent spinner), `-1` (frontier range inputs inherit globals, hiding
constraints the backend rejects), `-10` (raw error stringified, discarding the structured
detail), `-11` (auto-range supersede path unreachable), `frontend-preview-explore-1` (a
running Explore job becomes invisible and uncancellable when the cache identity changes),
`-2` (switching utility files discards a draft the server rejected), `-4` (rejected strategy
payload renders as silence), `-13` (tablist with no keyboard navigation).

**P3 — spec truth and cleanup:** fold the shipped contracts
(`contracts-c-12`, `contracts-c-6`, `contracts-c-11`, `contracts-c-8`,
`frontend-node-editors-4`, `frontend-modelling-optimiser-ui-6`), fix ownership and
consumer-set drift (`frontend-modelling-optimiser-ui-14`, `frontend-node-editors-12`,
`frontend-preview-explore-8`, `frontend-preview-explore-10`), the unrendered metrics and
coverage claims (`frontend-preview-explore-3`, `-5`, `-12`), editor/spec mismatches
(`frontend-node-editors-6`, `-11`, `frontend-modelling-optimiser-ui-5`, `-8`), and the
duplication cluster (`frontend-node-editors-9`, `over-complication-10` — the four hand-copied
Python keyword lists, `frontend-preview-explore-14`, `frontend-node-editors-7` session-global
capability cache).

## Finding inventory

High (3): `frontend-node-editors-1`, `frontend-node-editors-3`,
`frontend-modelling-optimiser-ui-3`.
Medium (18): `contracts-c-6`, `contracts-c-8`, `contracts-c-11`, `contracts-c-12`,
`frontend-node-editors-2`, `frontend-node-editors-4`,
`frontend-modelling-optimiser-ui-1`, `frontend-modelling-optimiser-ui-2`,
`frontend-modelling-optimiser-ui-5`, `frontend-modelling-optimiser-ui-6`,
`frontend-modelling-optimiser-ui-10`, `frontend-modelling-optimiser-ui-12`,
`frontend-modelling-optimiser-ui-13`, `frontend-preview-explore-1`,
`frontend-preview-explore-2`, `frontend-preview-explore-3`, `frontend-preview-explore-4`,
`frontend-preview-explore-5`.
Low (14): `frontend-node-editors-6`, `frontend-node-editors-7`, `frontend-node-editors-9`,
`frontend-node-editors-11`, `frontend-node-editors-12`, `over-complication-10`,
`frontend-modelling-optimiser-ui-8`, `frontend-modelling-optimiser-ui-11`,
`frontend-modelling-optimiser-ui-14`, `frontend-preview-explore-8`,
`frontend-preview-explore-10`, `frontend-preview-explore-12`,
`frontend-preview-explore-13`, `frontend-preview-explore-14`.

## File ownership (exclusive)

- `frontend/src/App.tsx`, `frontend/src/panels/NodePanel.tsx`
- `frontend/src/panels/editors/**` (`ApiInputEditor.tsx`, `DataInputEditor.tsx`,
  `DataOutputEditor.tsx`, `OutputEditor.tsx`, `CodeEditor.tsx`, `_shared.tsx`,
  `_ioFormats.ts`, `RatingStepEditor.tsx`)
- `frontend/src/panels/ModellingConfig.tsx`, `OptimiserConfig.tsx`, `OptimiserPreview.tsx`,
  `modelling/**`, `ExplorePreview.tsx`, `DataPreview.tsx`, `UtilityPanel.tsx`,
  `PreviewPanelTabs.tsx`, `ImportsPanel.tsx`
- `frontend/src/hooks/useConstraintHandlers.ts`, `useDataInputColumns.ts`,
  `useSchemaFetch.ts`; `frontend/src/utils/banding.ts`, `configField.ts`, `buildGraph.ts`
- `docs/specs/frontend-node-editors/**`, `docs/specs/frontend-modelling-optimiser-ui/**`,
  `docs/specs/frontend-preview-explore/**`
- Their colocated `__tests__` directories and `frontend/e2e/` specs for these panels

## Cross-stream touchpoints

- `api/client.ts` / `types/guards.ts` are WS-09's: `frontend-preview-explore-4` needs a
  distinguishable "diagnostic unavailable" return from `parseExecutionStrategyDiagnostic`,
  and `-10`'s job completion lives in `useBackgroundJobs.ts`. Request those changes from
  WS-09.
- `frontend-node-editors-1` depends on WS-09's edge-retention behaviour in
  `useWebSocketSync`/`filterIncomingEdges` — agree the contract before fixing.
- `over-complication-10`: two of the four keyword-list copies are in WS-09's `utils/` —
  de-duplicate jointly, one shared source of truth.
- `over-complication-1` (WS-02): the json-cache Cancel button in `ApiInputEditor.tsx` is this
  stream's file — remove or wire it once WS-02 decides the backend's fate.
- Optimiser backend contracts (WS-08) drive `frontend-modelling-optimiser-ui-1`'s
  `configured_ranges` rejection — keep the two in sync.

## Definition of done

- No panel crash from a null-handle edge; node renames migrate mappings with a duplicate
  preflight; the atomic multi-key config write is used wherever two keys change together,
  and the shared test double reproduces production's stale-ref spread.
- Stranded UI states (spinner, disabled Save, invisible job, silently discarded draft) each
  have a regression test.
- All contract sections in the three components folded and deleted; ownership rows agree with
  `ownership.toml`.
- Baseline entries deleted; findings fixed or deferred with reasons.

## Verification

- `npm --prefix frontend test -- src/panels/__tests__ src/panels/editors/__tests__`
- `npm --prefix frontend test -- src/__tests__/editors`
- `npm --prefix frontend run typecheck` and `npm --prefix frontend run lint`
- Targeted `frontend/e2e` canvas-assurance runs where the finding names them.
