# Frontend Git UI — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/GitPanel.tsx` | The Git side panel: pending-save list, milestone list + expansion, graph-rail row wiring, all right-click context menus, the fork-naming dialog, `SaveRow`/`FileRow`/`ForkLinks`/`ViewVersionButton`/`MoveToVersionButton` sub-components. |
| `frontend/src/panels/gitPanelCache.ts` | Module-level session caches (`branchHistory`, `milestoneSaves`, whole-forest `graphCache`) with LRU eviction, feeding `GitPanel`'s stale-while-revalidate hydration and unchanged-payload short-circuit. |
| `frontend/src/panels/gitgraph/layout.ts` | Pure layout: `computeGitGraphLayout` turns a `GitGraphResponse` + the panel's row list into a `RailModel` (lanes, dots, curves, magnifiers, spawn stubs); `computeRailRuns` consolidates per-row cells into whole-length vertical line segments. No DOM, no fetch. |
| `frontend/src/panels/gitgraph/GraphCell.tsx` | Rendering: `GraphRailCell` (per-row SVG cell), `GraphRailOverlay` (the measured whole-box overlay of consolidated runs), `GraphRailHeader` (top departing-branch chip strip), `Magnifier`. Pure presentation over `layout.ts` types. |
| `frontend/src/stores/useGitStore.ts` | Zustand store: working-branch status, modal routing (`GitModalMode`), peek/comparison/move targets, and the refresh-triggering nonces (`historyNonce`, `commitNonce`, `selectLatestSaveNonce`, `selectSaveNonce`, `branchesExpandNonce`). |
| `frontend/src/components/BranchIndicator.tsx` | Toolbar entry point: branch name + last-save SHA buttons that open the panel and set its initial view/selection. |
| `frontend/src/components/BranchManager.tsx` | Branch list/create/switch/archive/delete/restore, embedded in `GitPanel`; owns its own confirm dialogs and row context menu. |
| `frontend/src/components/CommitBreadcrumb.tsx` | `CommitBreadcrumb` (version-relative label for a comparison canvas) and `ComparisonDelta` (historic↔current commit-count chip). |
| `frontend/src/components/MilestoneCommitModal.tsx` | Save & commit modal: message + version label form, the 409 fork-warning override flow. |
| `frontend/src/components/MoveConfirmModal.tsx` | Pre-move save/discard/confirm prompt: reads `useGitStore.moveTarget` for the target label and `useGraphStore.dirty` to decide single-confirm vs. save-or-discard, and locks its buttons (`busy`) once clicked while the caller performs the actual move. |
| `frontend/src/components/WorkingBranchModal.tsx` | Startup / save-gate branch-selection modal, with an inline git-identity sub-form. |
| `frontend/src/components/RemotePushControl.tsx` | Remote dropdown, ahead/behind + ledger-divergence display, explicit push (including empty-remote default-bootstrap tooltip/toast and the pending-save integrity confirm), catch-up, the non-fast-forward `PushRejectedModal`, `AheadBehind`/`LedgerStatus`/`RejectedLeg` sub-components. |
| `frontend/src/components/DivergenceModal.tsx` | Recorded-branch-vs-HEAD divergence recovery modal (go home / stay here / open branch manager). |
| `frontend/src/utils/vcHistory.ts` | Records switch/archive/restore/delete as undoable entries on `useGraphStore`'s VC history stacks; each entry's undo/redo leg re-syncs git status + the panel's history nonce. |

## Key types and data structures

**`GitModalMode`** (`useGitStore.ts`) — `"select" | "divergence" | "milestone"`; which of
the three gating modals (`WorkingBranchModal`/`DivergenceModal`/`MilestoneCommitModal`) is
open. `null` means none.

**`GitComparison`** — `{ sha: string; label: string }`. Dual-purpose: `comparison` (the
read-only side-by-side target) and `moveTarget` (the pending move, gated by
`MoveConfirmModal` — see Module map) are both typed as this shape but are distinct store
fields with independent lifecycles.

**`GitPendingAction`** — `"save" | "commit" | null`; an action queued behind a
`WorkingBranchModal` confirmation (the save-gate).

**`GitPushResponse`** (`frontend/src/api/types.ts`) — the push result includes required
`default_branch: string` and `bootstrapped_default: boolean` members in addition to the
remote, working/ledger names, and `pushed_refs`. The matching runtime guard requires and
parses both members; `bootstrapped_default` is false by default on the server but is always
present on the wire. `pushed_refs` names the explicit branch refspecs submitted by the
atomic push (not implicit `--follow-tags` tags), and does not necessarily mean each tip
changed.

**`GitState`** (the store) — see `frontend/src/stores/useGitStore.ts:32-104` for the full
field list. Notable invariants:
- `historyNonce` and `commitNonce` both trigger `GitPanel.refresh()` but only `commitNonce`
  additionally selects the newly-committed milestone, and only when the panel is not
  peeking (`peekingRef.current` false at the time the refresh resolves).
- `selectSaveTarget` is a side channel read by `GitPanel` via
  `useGitStore.getState().selectSaveTarget` inside an effect rather than a subscribed
  value, because the sha must be captured at the moment `selectSaveNonce` bumps, not
  re-read on every render.
- `closeModal` always clears `pendingAction` — a dismissed modal must never leave a queued
  action to fire on a later, unrelated confirmation.

**`RowDescriptor`** (`gitgraph/layout.ts`) — `{ kind: "pending-save" | "milestone" | "save"
| "placeholder"; sha; expanded?; milestoneSha? }`. Mirrors `GitPanel`'s render order
exactly (`GitPanel.railRowData`): pending saves first, then milestones newest-first, each
followed by its expanded saves or one placeholder row. Rows are keyed `${kind}:${sha}` —
a placeholder shares its milestone's sha, which is why the key includes `kind`.

**`RailModel`** — the layout output: `rows: RailRow[]` (1:1 with the input `RowDescriptor[]`),
`laneCount`, `slotCount`, `overflowCount`, `topChips`, `lanes`, `viewBranch`,
`viewedIsArchived`. `laneCount === 0` is the model's degraded-input sentinel — `GitPanel`
treats such a model as "no rail" (`rail === null`) even though `computeGitGraphLayout`
itself returned a non-null object.

**`RailCell`** union (`gitgraph/layout.ts:220-229`) — the nine per-row drawable primitives:
`dot` (milestone), `hollow-dot` (pending save), `save-dot` (expanded save on the sub-rail),
`pass` (lane continuing with no node), `transition` (spine changing lanes at a fork-point
row), `fold-in`/`fold-out` (the siding's merge into / departure from a milestone dot),
`siding-pass` (siding running straight through a doubly-expanded same-lane milestone),
`spawn-stub` (a departing branch's curve into its slot). Each carries `branch` +
`colorIndex` for right-click targeting and colouring.

**`RailRun`** — a consolidated vertical line (`kind: "spine" | "siding"`) spanning
`y1`..`y2` at a fixed `x`, produced by `computeRailRuns` from measured row geometry
(`RailRowGeom`). Drawn once per run by `GraphRailOverlay` so dash phase is continuous
across every row and 1px box border the run crosses.

**`BranchHistoryEntry`** (`gitPanelCache.ts`) — one branch's last-seen `{milestones,
milestonesJson, pending, pendingJson, forkBranches, forkBranchesJson}`. The `*Json` fields
are `JSON.stringify` serializations kept alongside the parsed data specifically to drive
`GitPanel`'s unchanged-payload short-circuit without re-serializing on every compare.

**`SpawnChipBranch`** (`GitPanel.tsx`) — `{ name; is_archived; colorIndex? }`, the minimal
shape shared by the graph-derived spawn chips and the legacy `forks.json`-derived fallback
chips (`colorIndex` is `undefined` on the fallback path, rendered as a plain accent chip).

## Control flow

**Panel mount / branch resolution.** `GitPanel` derives `branchKey = viewBranch ?? workingBranch`
(the peek target, else the current working branch). On mount it seeds `milestones`/`pending`/
`forkBranches`/`graph` synchronously from `gitPanelCache` (`readBranchHistory`,
`readGraphCache`) so a previously-viewed branch paints with no loading flash, then always
runs `refresh()` (stale-while-revalidate — see Edge cases). A `branchKey` change (peek
start/clear, or the workingBranch resolving after the panel mounted before status loaded)
re-hydrates from cache or clears rows, gated on `applied.current.branch !== branchKey` so
an already-landed refresh for that key is never clobbered.

**`refresh()`** (`GitPanel.tsx:155-238`). Stamps a new `refreshGeneration`. Kicks off the
graph fetch (`getGitGraph(50)`) and the three row-data fetches (`getMilestones`,
`getPendingSaves`, `getWorkingBranches`) in parallel via `Promise.all`. On a cold paint
(`!hasRowsOnScreen.current`) it races the graph fetch against a 250ms timer before
committing row state, so the rail doesn't visibly pop in a beat after the list on first
load — this gate is skipped once rows are already on screen (a warm remount or a
subsequent refresh), where the delay would only slow things down. Any response — graph or
row-data — is discarded un-applied if `generation !== refreshGeneration.current` by the
time it resolves (a newer refresh started first). On success, every fetched payload is
`serializePayload`'d and written to `gitPanelCache` unconditionally (permissive — a
hydrated mount always revalidates anyway), then each `setState` is skipped individually
when its serialization matches what's already applied for that branch (`applied.current`)
— this is what keeps a no-op auto-refresh from re-rendering the row list or re-triggering
the rail's measurement effect.

**Nonce-driven effects** (`GitPanel.tsx:306-384`), each a `useEffect` keyed on one store
nonce:
- `historyNonce` → `refresh()`, no selection change.
- `commitNonce` → `refresh()`, then select `res.milestones[0].sha` if not peeking.
- `selectLatestSaveNonce` → `refresh()`, then select the newest pending save, or (no
  pending) expand the newest milestone and select its newest save. Guarded by
  `processedSelectNonce` so a nonce already handled (e.g. the same click that just opened
  the panel) doesn't re-fire.
- `selectSaveNonce` → `refresh()`, then search pending saves, then milestones directly,
  then each milestone's expanded saves (loading them via `loadMilestoneSaves` as needed)
  until `selectSaveTarget` is found. Also guarded by a processed-nonce ref.

**Milestone expansion** (`toggleExpand`). A cache hit (`readMilestoneSaves`) expands
synchronously with no fetch or loading placeholder, because a milestone's folded saves are
content-addressed by its immutable merge sha. A miss sets the row to `"loading"`,
fetches, and lands the result only if the row is still `"loading"` when the response
arrives — a collapse or an intervening refresh (which clears `expanded` — no, see Edge
cases: it deliberately does not) must not resurrect an abandoned expansion.

**Rail derivation** (`GitPanel.tsx:510-628`, all `useMemo`). `railRowData` builds the
`RowDescriptor[]` from `pending`/`milestones`/`expanded` in render order. `rail` calls
`computeGitGraphLayout(graph, {viewBranch, rows})`, but only when `graph !== null`,
`milestones.length > 0`, and — critically — `rowsBranch === (viewBranch ?? workingBranch)`:
during a peek's one-round-trip window the row *data* may still describe the previous
branch while `viewBranch` has already changed, and drawing the rail against mismatched
rows would mislabel them, so the rail simply withholds itself until `rowsBranch` catches
up (`refresh()` sets `rowsBranch` only once its own response is applied). `rail === null`
whenever `computeGitGraphLayout` degrades to `laneCount === 0` too. `spawnChipsBySha`
derives in-row branch chips from `graph.branches` ancestry rather than `forkBranches`
(`forks.json`) whenever a rail exists, because ancestry sees branches created in another
clone that never wrote a local `forks.json` entry; `chipsAt()` falls back to
`forksAt()` (the `forks.json`-derived list) only when there is no rail at all.

**Overlay geometry** (`GitPanel.tsx:552-593`). A `useLayoutEffect` measures every
`[data-rail-row]` element inside the milestones box after paint (and on `ResizeObserver`
fire), producing `rowGeom: (RailRowGeom | null)[]` aligned to `rail.rows` via an `offset`
(the milestones box only holds the tail of `rail.rows` — pending rows have their own box
and contribute no overlay runs, hence `offset = rail.rows.length - cells.length`). A
stringified geometry key short-circuits `setRowGeom` when nothing actually moved.
`railRuns = computeRailRuns(rail, rowGeom)` is then a pure `useMemo` over that geometry.

**Branch mutation flows.** `GitPanel.submitFork` → `createWorkingBranch(name, {at: sha,
move})`; on `res.switched` it does a full `window.location.reload()` (a move relocates the
working tree, so the client can't safely reconcile in place) — a parallel (non-move)
create instead just calls `refresh()`. `GitPanel.performSwitch` and
`BranchManager.switchNow` are two independent in-app switch implementations (see
high-level.md's failure-model NOTE): both call `setWorkingBranch(branch, false)`, both call
`recordSwitch` (pushing a `useGraphStore` VC undo entry) on success, but they maintain
separate `busy`/error state and neither calls the other. `BranchManager.run()` is the
shared wrapper for its own five mutations (create, switch, archive, delete, restore): on
success, unless `opts.reloadOnDone` is set, it reloads status + the branch list and calls
`useGitStore.getState().notifyHistoryChanged()` so any open `GitPanel` refetches; on
failure it sets both a persistent inline banner and a toast. `opts.reloadOnDone` — passed
only by archive/delete, as `b.is_current` — skips that tail and calls
`window.location.reload()` instead, for the *current*-branch case. `doCreate` never sets
`opts.reloadOnDone` (it always passes `false`); the parallel-create-with-move case instead
reloads via a direct `reloadApp()` call inside `doCreate`'s own callback when
`res.switched`, with `run()`'s status/refresh/notify tail still executing afterward
(harmless once the page is unloading, but not actually gated by `opts.reloadOnDone`).

**Undo/redo** (`vcHistory.ts`). `recordSwitch`/`recordArchive`/`recordRestore`/`recordDelete`
each push a `{label, undo, redo}` entry onto `useGraphStore`'s VC stacks; every leg is
wrapped by `leg()`, which toasts and resyncs (`loadStatus` + `notifyHistoryChanged`) on
success and toasts + rethrows on failure so the store keeps the entry available for a
retry rather than dropping it. Because the closures only touch stores (never component
state), an entry keeps working after the panel that pushed it unmounts. Delete's inverse
is `undeleteBranch` rather than a full re-create, since deletes are trash-preserving
server-side.

**Move-to-version flow.** `GitPanel`'s row/lane move affordances call
`useGitStore.requestMove({sha, label})`, which `App.tsx` surfaces as `MoveConfirmModal`. The
modal itself performs no API call: clicking any of its buttons sets a local `busy` flag
(blocking double-submission) and calls the caller-supplied `onConfirm(saveFirst)` —
`saveFirst` is `true` only for the dirty-canvas "Save & move" button, `false` for a
clean-canvas confirm or an explicit "Discard & move". `App.tsx`'s `handleMoveConfirmed` owns
the actual sequencing: optionally flushing unsaved edits via `handleSave()`, then calling the
`moveToVersion` API and reloading the page on success (a move replaces the whole working tree,
so the client re-initialises from scratch rather than reconciling in place) — or closing the
modal (`closeMove`) and toasting on either a failed save or a failed move.

**Remote push flow.** `RemotePushControl` loads configured remotes and auto-selects only a
sole remote. Project initialization, server startup, and working-branch confirmation never
call `gitPush`; publication begins only from `onPushClick`. The button tooltip is "Push your
branch and save history. If the remote is empty, Haute also publishes the default branch as
your merge target." Pending saves still interpose the existing confirmation before
`doPush`; otherwise `doPush` calls `gitPush(selected)` directly.

On success, `doPush` branches only on the response metadata. When
`bootstrapped_default=true`, it emits `Published <default_branch> and your branch history to
<remote>`. When false, it retains the ordinary pushed-ref-count toast. It then reloads
remote divergence in either case. The frontend neither infers emptiness nor hardcodes
`main`; strict inspection, related-history validation, refspec choice, atomicity, and remote-
host default-branch configuration are backend/out-of-scope concerns. Any refusal follows
the existing push error path and never emits a success toast or retries automatically.

## Edge cases and invariants

- **A save must never move the selection; a commit must, but only on the user's own
  branch.** Enforced by keeping `historyNonce`/`commitNonce` as separate nonces and gating
  the commit-effect's selection on `!peekingRef.current` read at resolution time (via a
  ref, not the reactive `peeking` value, so the effect doesn't need `peeking` in its
  dependency array and re-fire spuriously on every peek change).
- **Expansion state is not cleared by a refresh.** `refresh()` explicitly does not touch
  `expanded` — only the peek-change effect does (`setExpanded({})`) — because clearing it
  on every auto-refresh would collapse a milestone the user just opened.
- **Byte-identical revalidation is invisible.** The `applied` ref's serializations mean an
  unchanged background refresh produces zero `setState` calls for milestones/pending/forks,
  preserving array identity through to the rail's memos — this is what makes the rail
  layout and the two-pass overlay measurement not re-fire on a no-op poll.
- **Stale-refresh generation guard.** A slower in-flight refresh resolving after a faster,
  newer one must not overwrite the newer result, and must not clear `loading` out from
  under it — both branches of the `generation !== refreshGeneration.current` check exist
  for this (see `panels/__tests__/GitPanel.staleRefresh.test.tsx`).
- **Peek's in-flight mislabel window.** Immediately after `setViewBranch` fires, `graph`
  and `milestones` may still reflect the previous branch for one round trip; the rail
  withholds itself (`rowsBranch` check) rather than draw a rail for the wrong branch's rows.
- **Root milestone termination.** `computeGitGraphLayout` marks the root commit's dot
  `terminal: true` and stops emitting spine cells for save/placeholder rows below it
  (`spineEnded`), since there is no earlier commit to draw a line down to.
- **Archived-branch grouping.** Multiple archived departures anchored at the same group
  key collapse into a single muted stub (in the parent branch's colour) carrying a
  `count`, rather than one stub per archived branch — this bounds rail width regardless of
  how many branches were archived from one point.
- **Doubly-expanded same-lane siding.** When a milestone's neighbours on both sides are
  expanded and share its lane, the siding is drawn as one continuous `siding-pass` run
  through that milestone's row instead of a fold-out+fold-in pinch, so the commit dot
  isn't visually obscured by two curves converging on it.
- **Archived branches never burn a palette slot.** `colorIndexOf` recurses to an archived
  branch's `fork_of` parent's colour; the palette itself (`paletteOrder`) is computed only
  over non-archived branches in payload order.
- **Fork-menu availability while peeking.** The context menu always opens (never falls
  through to the browser menu) on any row, but the "new branch from here" / "…& move work
  here" items are omitted while peeking — those actions only make sense relative to the
  user's own working branch.
- **"Move" is only offered on the branch tip.** `GitPanel` passes `canMove: idx === 0` for
  the milestone right-click and `true` unconditionally for save rows (all pending/expanded
  saves shown are, by construction, ahead of the last milestone).
- **`BranchManager`'s archive-vs-delete asymmetry.** Archive on the *current* branch with
  uncommitted changes redirects to a "commit a milestone first" prompt instead of
  proceeding (archiving must not silently drop work); delete is always enabled regardless
  of dirty state, because deleting is an explicit, confirmed data-loss action whose dialog
  already names what will be lost (`deleteLoss()`).
- **`MoveConfirmModal` self-guards independently of its caller.** `App.tsx` only mounts it
  once `moveTarget` is truthy, but the component also reads `moveTarget` itself and returns
  `null` when unset — belt-and-braces consistent with the other git modals never assuming a
  caller's mount gate is the only guard.
- **`RemotePushControl` never defaults to a push target** unless exactly one remote exists
  — with 0 remotes it shows an explicit "no remotes" message, with 2+ it requires a
  deliberate selection, so a push is never accidental.
- **Bootstrap is response-driven and first-Push-only.** The special toast appears only for
  `bootstrapped_default=true`; an idempotent second push receives false and uses the normal
  toast. The displayed branch comes from `default_branch`, so custom defaults work and UI
  copy never assumes `main`.
- **Catch-up eligibility mirrors the engine's fast-forward precondition exactly**:
  `canCatchUp` requires no leg (`working` or `ledger`) to be `ahead`/`diverged`, and at
  least one leg `behind` — computed identically in `RemotePushControl`'s standalone button
  and inside `PushRejectedModal`.
- **LRU caches are size-bounded, not time-bounded.** `BRANCH_HISTORY_CAP = 8`,
  `MILESTONE_SAVES_CAP = 64`; both `gitPanelCache` maps evict the least-recently-*read*
  entry (a `touch()` re-inserts on read, `put()` evicts from the front after inserting).
  The graph cache is a single slot (the graph endpoint is whole-forest, not per-branch).

## Error handling

- API calls throughout this component surface `ApiError` (from `frontend/src/api/client.ts`,
  owned by [server-api](../server-api/high-level.md)); handlers narrow on
  `err instanceof ApiError && err.status === 409` to distinguish the two structured
  rejection bodies (`GitMilestoneFork`, `GitPushRejection`) from all other errors, which
  fall through to `err instanceof Error ? err.message : "unknown error"` before toasting.
- `parseGitMilestoneFork` / `parseGitPushRejection` (`frontend/src/types/guards.ts`) return
  `null` on an unparseable body rather than throwing; both call sites treat a `null` parse
  as "not this structured case" and fall back to the generic toast path, so a server-shape
  drift degrades to a plain error message instead of crashing the modal.
- `parseGitPushResponse` treats `default_branch` and `bootstrapped_default` as required and
  type-checks both. A malformed success body rejects through the normal request promise and
  reaches `RemotePushControl`'s error toast; the UI must not infer a bootstrap from
  `pushed_refs` or silently substitute `main`.
- `GitPanel.refresh()`'s catch only toasts and returns `null` when its own generation is
  still current — a superseded refresh's rejection is swallowed entirely (the newer
  refresh owns error reporting for that logical operation).
- The graph fetch inside `refresh()` uses a `.then(onFulfilled, onRejected)` pair whose
  rejection handler is a no-op (`() => {}`) — by design, per the Failure model, since the
  rail is chrome.
- `toggleExpand`'s catch both toasts and rolls the row back out of `expanded` (deleting the
  key rather than leaving a permanent `"loading"` placeholder), but only if the row is
  still the one that was loading (`prev[sha] !== "loading"` guards against a stale catch
  landing after the user collapsed and re-expanded).
- `BranchManager.reloadApp()` wraps `window.location.reload()` in a try/catch that
  swallows the error silently — documented inline as a jsdom/non-browser no-op, not a
  production error path.
- `MoveConfirmModal` itself throws and catches nothing — it is a stateless (aside from
  `busy`) confirm surface. The move's actual try/catch (a failed `handleSave()`, a failed
  `moveToVersion` call) lives in `App.tsx`'s `handleMoveConfirmed`, which toasts and calls
  `closeMove()` on either failure rather than leaving the modal open in a stuck busy state.
- `useGitStore.loadStatus()` swallows all errors (`catch {}`) without touching `status` —
  the field is simply left at whatever it held before the failed call (`null` on a first
  load; stale data if a later refresh fails) — this is the one place in the store itself
  that intentionally hides a failure, matching the "status is best-effort chrome" rule in
  the high-level Failure model.

## Testing

Tests live alongside the source: `frontend/src/panels/__tests__/`,
`frontend/src/panels/gitgraph/__tests__/`, `frontend/src/components/__tests__/`, and
`frontend/src/stores/__tests__/useGitStore.test.ts`. All are Vitest + React Testing
Library component/unit tests (no e2e for this surface).

- **`GitPanel.test.tsx`** — the primary behavioural suite: rendering, milestone
  expand/collapse, pending-save display, all four selection nonces, right-click menus
  (fork/view/move) in every row context (milestone / pending save / expanded save),
  peeking's effect on menu contents, the fork-creation dialog, the graph rail's dots/
  stubs/chips/magnifier, lane and dot context menus, in-app branch switching from the
  lane menu, and the peek in-flight mislabel guard.
- **`GitPanel.gaps.test.tsx`** — targeted gap-fill: fork-with-switch page reload, fork
  failure toast, fork-with-move submission, the view affordance opening a comparison,
  peeking's menu contents (a second angle on the same invariant as the main suite).
- **`GitPanel.cache.test.tsx`** — the session-cache contract end to end: unchanged-payload
  short-circuit (no re-render), a changed payload applying, warm-remount hydration (both
  with and without a subsequent change), sha-cached milestone-save re-expansion never
  refetching, a late status resolution hydrating the cache the initial seed missed without
  clobbering rows a completed refresh already applied, and peeking an uncached branch
  clearing rows rather than carrying stale ones over.
- **`GitPanel.staleRefresh.test.tsx`** — the generation guard specifically: an earlier
  slow refresh resolving after a later fast one must not overwrite the later branch's
  rows, and must not clear the newer refresh's loading state.
- **`gitPanelCache.test.ts`** — unit tests on the cache module directly: serialization
  round-tripping, per-branch LRU storage/eviction, milestone-save sha caching, the
  single-slot graph cache, and `clearGitPanelCaches`.
- **`gitgraph/__tests__/layout.test.ts`** — the largest and most detailed suite in the
  component, covering `computeGitGraphLayout` and `computeRailRuns` fixture-by-fixture:
  linear-spine lane/dot/stub placement and rail sizing; dotted-rail dash inheritance
  across expanded ranges, transitions, and placeholders; the sub-rail (fold-in/fold-out/
  siding-pass, including the doubly-expanded same-lane continuation and its ownership-
  transition counterpart); ancestor lanes when peeking a fork (lane assignment, top-of-
  rail extension, transition ownership, colour stability across peeks); nearest-ancestor-
  first lane ordering for a fork-of-fork; the spawn-stub anchor-resolution cascade
  (source save → pending save → credit milestone → fork point → overflow) and its
  behaviour as saves expand/collapse; per-anchor-group slot reservation; archived-branch
  grouping and colour borrowing; magnifier placement rules; and degraded-input handling
  (unresolvable branch, unknown view branch, no entries, independent-root-as-overflow).
- **`gitgraph/__tests__/GraphCell.test.tsx`** — narrowly scoped to `GraphRailOverlay`'s
  bottom-anchored dash-phase convention for dotted runs (both spine and siding kinds) vs.
  the top-anchored convention for solid runs.
- **`stores/__tests__/useGitStore.test.ts`** — store unit tests: status load success/
  failure, modal open/close and pending-action interaction (including the
  close-clears-pending-action regression), peek set/clear, each nonce incrementing
  exactly once per action, comparison and move-target toggling.
- **`components/__tests__/BranchManager.test.tsx`** — listing (current/others/archived),
  peek-without-switch, create (with and without move, including the move confirm step),
  switch confirmation (including persisted "don't ask again") and its in-app (no-reload)
  behaviour plus the VC undo entry, archive (direct and the dirty-current redirect),
  delete with confirm, restore, the uncommitted/unsaved indicator display, the persistent
  error banner, and the full row context-menu surface (open, per-state item visibility,
  each action's routing, backdrop dismiss).
- **`components/__tests__/BranchManager.gaps.test.tsx`** — the `reloadOnDone` matrix:
  which mutations reload the page and which don't, keyed on `switched`/`is_current`.
- **`components/__tests__/BranchIndicator.test.tsx`** — pre-load render-nothing, ready-
  state display, both click targets' panel/store side effects, and the comparison-aware
  SHA-click behaviour.
- **`components/__tests__/CommitBreadcrumb.test.tsx`** — root/milestone collapse-to-anchor
  rendering, the non-milestone nearest-milestone→commit breadcrumb, and
  `ComparisonDelta`'s singular/plural count text.
- **`components/__tests__/MoveConfirmModal.test.tsx`** — target-label rendering, clean-canvas
  single-confirm calling `onConfirm(false)`, dirty-canvas save/discard button pair calling
  `onConfirm(true)`/`onConfirm(false)` respectively with the warning message shown, Cancel
  calling `onClose` without confirming, and rendering nothing when `moveTarget` is `null`.
- **`components/__tests__/MilestoneCommitModal.test.tsx`** — submit gating on message
  presence, successful commit with/without a version label, the 409 fork-warning →
  commit-anyway override flow, and backing out of that warning.
- **`components/__tests__/WorkingBranchModal.test.tsx`** — eligible-branch listing plus
  create option, adopting an existing branch, creating a new one, submit gating, and the
  inline identity sub-form.
- **`components/__tests__/RemotePushControl.test.tsx`** — no-remotes messaging, sole-
  remote auto-selection, multi-remote deliberate-selection requirement, the pending-save
  push confirm (both proceeding and cancelling), ahead/behind display in all states
  (synced / diverged / unknown-vs-never-pushed), ledger-status display (behind / forked /
  silent-when-synced), the 409 rejection modal, non-409 fallback toast, catch-up
  offer/exclusion by leg state, the rejection modal's catch-up vs. spin-off-a-copy
  routing, and the rewrite-specific heading. Push-success coverage pins the exact tooltip,
  the special bootstrap toast using a custom `default_branch`, the ordinary toast when
  `bootstrapped_default=false` (including an idempotent later push), post-push reload, and
  the guarantee that no push occurs merely from mount/startup/branch selection.
- **`components/__tests__/RemotePushControl.gaps.test.tsx`** — error-message fidelity for
  catch-up and branch-away failures (`Error` vs. non-`Error` rejection), unparseable-409
  fallback to a plain toast (both a bad body shape and a missing detail), and the full
  behind/ahead/diverged×working/ledger catch-up-eligibility matrix.
- **`api/__tests__/client.test.ts` and `types/__tests__/guards.contract.test.ts`** — the push
  client/guard contract requires `default_branch` and `bootstrapped_default`, preserves
  submitted `pushed_refs`, accepts both boolean values, and rejects missing or wrongly typed
  bootstrap metadata rather than inventing a default.
- **`components/__tests__/DivergenceModal.test.tsx`** — recorded/current branch naming,
  each of the three resolution choices, and stay-here's disabled state when the current
  branch isn't eligible.
- **`components/__tests__/DivergenceModal.gaps.test.tsx`** — error-toast surfacing on a
  rejected `setWorkingBranch` (both `Error` and non-`Error`), the busy/"Working…" disabled
  state, double-submit guarding, and null-status placeholder rendering.

Known coverage gaps: none flagged in the suites' own comments; the layout test file's
breadth (60+ cases) suggests `computeGitGraphLayout`/`computeRailRuns` are the
highest-risk, most thoroughly defended part of this component, consistent with them being
the only pure, intricate geometry code in the surface.
