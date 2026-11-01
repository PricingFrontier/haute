# Frontend Git UI — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/GitPanel.tsx` | The Git side-panel view and user-action boundary: pending-save and milestone rows (each time by `relativeTime`), graph-rail wiring, the row, rail-dot and rail-lane context menus (`BackdropMenu`), the fork dialog (over a `MenuBackdrop`), the dirty-navigation guard (`useDirtyNavigation`), the history's load error with Retry where the empty history would be (`git-panel-load-error`), and row sub-components. Branch-history request identity and hydration are delegated to `useGitHistory`. |
| `frontend/src/panels/git/useGitHistory.ts` | The single state authority for branch-scoped Git history: cache hydration, stale-while-revalidate fetching, request generations, nonce consumption, row selection, expansion, branch replacement, and `loadError`, the failure of its own latest refresh (cleared as a refresh starts, never set by a superseded one). It exposes a render-ready snapshot and event callbacks without render-time ref mutation or effect-driven synchronous state resets. |
| `frontend/src/panels/gitPanelCache.ts` | Module-level session caches (`branchHistory`, `milestoneSaves`, whole-forest `graphCache`) with LRU eviction, feeding `useGitHistory`'s stale-while-revalidate hydration and unchanged-payload short-circuit. |
| `frontend/src/panels/gitgraph/layout.ts` | Pure layout: `computeGitGraphLayout` turns a `GitGraphResponse` + the panel's row list into a `RailModel` (lanes, dots, curves, magnifiers, spawn stubs); `computeRailRuns` consolidates per-row cells into whole-length vertical line segments. No DOM, no fetch. |
| `frontend/src/panels/gitgraph/GraphCell.tsx` | Rendering: `GraphRailCell` (per-row SVG cell), `GraphRailOverlay` (the measured whole-box overlay of consolidated runs), `GraphRailHeader` (top departing-branch chip strip), `Magnifier`. Pure presentation over `frontend/src/panels/gitgraph/layout.ts` types. |
| `frontend/src/stores/useGitStore.ts` | Zustand store: working-branch readiness + retry error, shared branch-list state/action, modal routing (`GitModalMode`), peek/comparison/restore targets, the refresh-triggering nonces (`historyNonce`, `commitNonce`, `branchesExpandNonce`), and `uncaptured`, the files writes reported left off the ledger, kept for a capture. |
| `frontend/src/stores/saveCapture.ts` | `reportSaveCapture`: what a write answered about its version capture, reported the same way for every write (the pipeline's save, the form's save, a Pipeline file written or deleted) — the ledger commit onto the status for the branch indicator through `setLastSaveSha` (left as it is when the answer has no commit field), `notifyHistoryChanged` so an open panel refetches, the answer's `uncaptured` files added, each once, to the git store's `uncaptured`, each warning in a toast of its own, and the identity modal opened on `identity_required` unless it was dismissed this session (`isIdentityPromptDismissed`) or is already open. `captureUncaptured`: nothing while nothing is kept; else the kept files taken off the store, sent to `POST /api/git/capture` (`captureFiles`) and the answer reported, so a file still uncaptured is kept again, while a request that fails keeps them all and toasts "Version capture failed: <reason>"; the identity prompt calls it once an identity is recorded and the project save at its start. |
| `frontend/src/stores/gitBranchLoader.ts` | Lazily loaded branch-list request coordinator. It publishes results into `useGitStore` while delegating the in-flight/queued-refresh bookkeeping to `singleFlight.ts`, keeping branch-only client code out of the initial editor bundle. |
| `frontend/src/stores/singleFlight.ts` | Shared resettable single-flight utility: request joining, the trailing-refresh queue (exactly one follow-up per active request, generation-anchored), and the stalled-request watchdog. Consumed by `useGitStore.ts` (working-branch status) and `gitBranchLoader.ts` (branch list). Test seam: both stores' `reset…ForTests` exports delegate to its `reset()`. |
| `frontend/src/components/BranchIndicator.tsx` | Toolbar entry point: explicit repository/readiness/error labels with Retry (an `interrupted` clone's Retry calls `retryInterruptedGit` and reloads readiness), plus a ready branch-name button that opens the panel on the current branch. Truncated errors use the shared `Tooltip` to expose the full diagnostic on error hover or Retry focus; its function child places `aria-describedby` on Retry. |
| `frontend/src/components/BranchManager.tsx` | Branch list/create/switch/archive/delete/restore, embedded in the Git panel; owns its own confirm dialogs, its dirty-navigation guard (`useDirtyNavigation`) and its row context menu (`BackdropMenu`). Its Deleted section lists `deleted` from the branch listing with Restore (`undeleteBranch(id)`), and a "Restore as" name box when a live branch has the name. A "don't ask again" that `setGitPrefs` fails to save is reported in an error toast. The delete dialog's wording comes from `deleteOutcome`: the branch goes to Deleted and can be restored from there, with its saves not yet in a milestone when it has some, and on the current branch the project's changes since the last save are lost. |
| `frontend/src/hooks/useProjectDirty.ts` | `useProjectDirty`: whether the project holds unsaved edits, the canvas's (`useGraphStore`) or the workbench's form's (`useWorkbenchStore`'s `formDirty`, mirrored by the form store, a lazy chunk); what every navigation guard asks about; `isProjectDirty`, the same read outside a component (a toolbar undo's switch). `discardFormEdits`: after a switch chosen over the form's unsaved edits has succeeded, the form read again (the form store imported then), so the destination's form shows; nothing while the form is as saved. `discardCanvasEdits`: after a switch chosen over the canvas's unsaved edits has succeeded, the canvas's edits dropped (`markSaved`, the sync banner cleared, `setGraphSynchronized(false)` so the answer applies even when the destination's document is the one last accepted) and a resync requested (`requestResync`), so the destination's pipeline shows; nothing while the canvas is as saved. |
| `frontend/src/hooks/useDirtyNavigation.ts` | `useDirtyNavigation(onSave)`: the one dirty-navigation guard of the git panel, the branch manager and the push control. `guard(proceed, question)` runs `proceed` at once while the project is clean (`useProjectDirty`), else opens the question, `question` naming what the action changes (`CHANGING_BRANCHES`, "Save them before changing branches?", unless the caller names another): `confirm` is then the props each caller renders `GitNavigationConfirm` with (null while none is open), whose Cancel drops the action, Discard runs it, and Save first runs it only once `onSave()` has answered true and the question it was asked from is still the open one; a save that answers false or throws keeps the question open, and a Cancel, another question or the surface unmounting while the save runs ends the question, so the save's answer runs nothing. `leaveUnsavedEdits(title, onSave)` is the same guard for an action no component runs, a toolbar undo's or redo's switch: on a dirty project (`isProjectDirty`) it asks the page's question (`useQuestionStore`) under `title`, with Cancel, Save first (offered when there is a save) and Discard (the danger yes), and answers true to go ahead (clean, Discard, or a save that answered true) or false for Cancel; a save that fails asks again. |
| `frontend/src/components/GitNavigationConfirm.tsx` | The question `useDirtyNavigation` opens before a navigation would replace unsaved edits, "You have unsaved changes." and the guard's `question`, placed by each caller (the git panel above its branch manager, the branch manager in its own section, the push control on its row or inside the rejection dialog): Cancel, Discard, and Save first. |
| `frontend/src/components/BackdropMenu.tsx` | The git surfaces' right-click menu: `MenuBackdrop`, a full-window layer under a menu or popover whose click closes it, as does a right-click, which calls `preventDefault`; `BackdropMenu`, that backdrop and, as its next sibling, the menu at the pointer (`testId`, an optional title line naming its branch, its items); `MenuItem`, one item's icon and label (`danger` for Delete). The git panel's row, rail-dot and rail-lane menus and the branch manager's row menu are `BackdropMenu`s; the fork naming step uses `MenuBackdrop` alone. |
| `frontend/src/components/CommitBreadcrumb.tsx` | `CommitBreadcrumb` (version-relative label for a comparison canvas, its time by `relativeTime`) and `ComparisonDelta` (historic↔current commit-count chip). |
| `frontend/src/components/MilestoneCommitModal.tsx` | Commit modal: required message (500-character maximum with visible validation) + version label form, and the 409 fork-warning override flow. |
| `frontend/src/components/RestoreConfirmModal.tsx` | The save/discard/confirm prompt before a restore: reads `useGitStore.restoreTarget` for the target label and `useProjectDirty` (the canvas's or the workbench's form's unsaved edits) to decide single-confirm vs. save-or-discard, and locks buttons plus Escape/backdrop close (`busy`) once clicked while the caller performs the actual restore. |
| `frontend/src/components/BrokenPairs.tsx` | The readiness's `broken_pairs`, one row each (`git-broken-pair`) in `brokenPairText`'s words, with a `type="button"` Repair (`git-broken-pair-repair`) that runs `repairBrokenPair` and hands `onRepaired` the readiness read after it; nothing while every pair is whole. The branch manager renders it above the current branch; the branch chooser renders it below its invalid reasons and confirms itself (`onConfirmed`) on a ready readiness. |
| `frontend/src/utils/brokenPairs.ts` | `brokenPairText(pair)`, a broken pair in the analyst's words; `repairBrokenPair(pair)`, which calls `repairBranchPair(ledger)` (`POST /api/git/repair-pair`), toasts "Repaired <working>" or the refusal, then reads the readiness with a fresh request and bumps the history nonce; `reportBrokenPairs(pairs)`, startup's error toast for each, whose action is its Repair. The startup readiness effect in `frontend/src/App.tsx` reports them when no chooser opens (ready, interrupted, divergent, and the states without a repository). |
| `frontend/src/components/WorkingBranchModal.tsx` | Startup / save-gate branch-selection modal, with an inline git-identity sub-form. Its default choice is the checked-out branch when eligible, else the first eligible branch, else "Create new branch…"; on a `detached` status it is always "Create new branch…", with a line saying that an existing branch opens its latest save. On an `invalid` status it lists every one of the status's `errors` (`working-branch-invalid`). |
| `frontend/src/components/RemotePushControl.tsx` | Remote dropdown, ahead/behind + ledger-divergence display, explicit push (including empty-remote default-bootstrap tooltip/toast and the pending-save integrity confirm), the deploy's-secrets toasts and `CiSecretsMissing` line with its Set from .env button, catch-up, the non-fast-forward `PushRejectedModal`, `AheadBehind`/`LedgerStatus`/`RejectedLeg` sub-components. Catch up (`doCatchUp`) and Spin off a copy (`doBranchAway`), from the standalone button or the rejection dialog, pass its `useDirtyNavigation` guard (the panel's `onSave` for Save first) with a question naming the remote whose files replace the project's; on success they drop the edits chosen over (`discardCanvasEdits`, `discardFormEdits`) and read the readiness, branch list and history again. |
| `frontend/src/components/DivergenceModal.tsx` | Recorded-branch-vs-HEAD divergence recovery modal (go home / stay here / open branch manager). |
| `frontend/src/components/ModalForm.tsx` | The form blocks the working-branch, divergence and save-time identity modals share inside `ModalShell`: the heading with its line of context, the modal text input, the git identity fields (name, email, global-config checkbox) and the cancel/submit row. |
| `frontend/src/utils/vcHistory.ts` | Records switch/archive/restore/delete as undoable entries on `useGraphStore`'s VC history stacks; each entry's undo/redo leg re-syncs git status + the panel's history nonce. A switch's legs (`switchLeg`) ask `leaveUnsavedEdits` first and drop the edits chosen over once the checkout succeeds. |

## Key types and data structures

**`GitModalMode`** (`frontend/src/stores/useGitStore.ts`) — `"select" | "divergence" | "milestone" | "storage" | "upstream" | "identity"`; which gating modal is open (`null` means none). The first three drive `WorkingBranchModal`, `DivergenceModal`, and `MilestoneCommitModal`; `"storage"`, `"upstream"`, and `"identity"` are routed by the hosted-project-storage surface (`StorageBindModal`, `UpstreamSyncModal`, and `IdentityPromptModal`).

**`GitComparison`** — `{ sha: string; label: string }`. Dual-purpose: `comparison` (the
read-only side-by-side target) and `restoreTarget` (the pending restore, gated by
`RestoreConfirmModal` — see Module map) are both typed as this shape but are distinct store
fields with independent lifecycles.

**`GitPendingAction`** — `"save" | "commit" | null`; an action queued behind a
`WorkingBranchModal` confirmation (the save-gate).

**`GitPushResponse`** (`frontend/src/api/types.ts`) — the push result includes required
`default_branch: string` and `bootstrapped_default: boolean` members in addition to the
remote, working/ledger names, and `pushed_refs`. The matching runtime guard requires and
parses both members; `bootstrapped_default` is false by default on the server but is always
present on the wire. `pushed_refs` names the explicit branch refspecs the Push submitted
(for a bootstrap, the default's own push and then the atomic pair push; never implicit
`--follow-tags` tags), and does not necessarily mean each tip changed. `ci_secrets` is a
`GitCiSecrets` for a GitHub remote's repository, else `null`.

**`GitCiSecrets`** (`frontend/src/api/types.ts`) — `{ repository, state, missing, newly_set,
problem }`, the deploy's secrets as a Push or Set from .env found them ([cli](../cli/low-level.md)):
`state` is `"ready" | "missing" | "unknown"`, `missing` and `newly_set` are secret names, and
`problem` is the reason they could not be read or set, else `null`. `gitPush` validates it
inside `GitPushResponse`, and `gitSetCiSecrets(remote)` (`POST /api/git/ci-secrets`) returns
one, both through the generated validators. Neither sets a browser deadline: the server
bounds each of their steps (git's, `gh`'s and Databricks' own timeouts), and a first
publication runs several, so a deadline would report a failure while the push or the
secrets still land.

**`GitRemote`** — `{ name, url, working, ledger }`. `working` and `ledger` are
`GitRemoteLeg | null` and are the only divergence fields; the working leg is not duplicated
into top-level `ahead`/`behind` counters.

**`GitState`** (the store) — see `frontend/src/stores/useGitStore.ts` for the full field
list. Notable invariants:
- `statusError` is transport/server failure text, never a synonym for a successful
  `state: "no-repository"` response. `loadStatus()` retains the last successful status,
  clears `statusError` on success, and exposes retry by simply being callable again.
  Concurrent `loadStatus()` calls share one module-level in-flight promise. Test seam:
  `resetGitStatusRequestForTests()` clears that promise so a never-settling mocked
  request cannot starve later loads in the same test file.
- `branches` has one store owner. Concurrent `loadBranches()` calls share the same in-flight
  promise; a completed mutation/save/commit schedules at most one new endpoint call.
  `loadBranches()` delegates to the lazy `gitBranchLoader.ts` coordinator, while
  `useGitStore` remains the sole publisher and consumer-facing owner of the result.
  The coordinator's settle paths are identity-guarded: a request detached by a newer
  queued refresh (or a reset) neither publishes state nor clobbers a newer request's
  slot. Test seam: `resetGitBranchLoaderForTests()` clears the coordinator's
  in-flight/queued promises.
- `historyNonce` and `commitNonce` both trigger `useGitHistory.refresh()` but only `commitNonce`
  additionally selects the newly-committed milestone, and only when the panel is not
  peeking (`!peeking` at the time the refresh resolves).
- `closeModal` always clears `pendingAction` — a dismissed modal must never leave a queued
  action to fire on a later, unrelated confirmation.

Test seam: `resetGitStoreForTests()` is the family reset — one awaited call restores every
`GitState` data field to its initial value (the data half is spread from one
required-fields object, so a new field cannot silently survive a reset) and clears the two
single-flight seams above.

**`RowDescriptor`** (`frontend/src/panels/gitgraph/layout.ts`) — `{ kind: "pending-save" | "milestone" | "save"
| "placeholder"; sha; expanded?; milestoneSha? }`. Mirrors `GitPanel`'s render order
exactly (`GitPanel.railRowData`): pending saves first, then milestones newest-first, each
followed by its expanded saves or one placeholder row. Rows are keyed `${kind}:${sha}` —
a placeholder shares its milestone's sha, which is why the key includes `kind`.

**`RailModel`** — the layout output: `rows: RailRow[]` (1:1 with the input `RowDescriptor[]`),
`laneCount`, `slotCount`, `overflowCount`, `topChips`, `lanes`, `viewBranch`,
`viewedIsArchived`. `laneCount === 0` is the model's degraded-input sentinel — `GitPanel`
treats such a model as "no rail" (`rail === null`) even though `computeGitGraphLayout`
itself returned a non-null object.

**`RailCell`** union (`frontend/src/panels/gitgraph/layout.ts`) — the nine per-row drawable primitives:
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

**`BranchHistoryEntry`** (`frontend/src/panels/gitPanelCache.ts`) — one branch's last-seen `{milestones,
milestonesJson, pending, pendingJson}`. The `*Json` fields
are `JSON.stringify` serializations kept alongside the parsed data specifically to drive
`useGitHistory`'s unchanged-payload short-circuit without re-serializing on every compare.

**`SpawnChipBranch`** (`frontend/src/panels/GitPanel.tsx`) — `{ name; is_archived; colorIndex? }`, the minimal
graph-derived shape used for in-row spawn chips.

## Control flow

**Panel mount / branch resolution.** `GitPanel` derives
`branchKey = viewBranch ?? workingBranch` (the peek target, else the current
working branch) and renders an immutable keyed branch scope around
`useGitHistory`, the named authority for that branch-scoped snapshot. The
scope key includes whether the branch is being peeked, so a peek becoming the
working branch cannot retain peek-only request semantics. The hook's reducer
initializer reads `gitPanelCache` (`readBranchHistory`, `readGraphCache`)
synchronously, so a previously viewed branch paints with no loading flash; a
miss produces an explicit empty snapshot for that branch. Replacing the scope
unmounts and retires its request generation and creates the replacement from
its own cache seed, rather than synchronously clearing or hydrating several
independent state values in an effect. Every entered scope is then revalidated
(stale while revalidate — see Edge cases).

**`refresh()`** (`useGitHistory`). Stamps a new monotonic request generation
and captures the branch scope. It kicks off the graph fetch
(`getGitGraph(50)`) and the two branch-history fetches (`getMilestones`,
`getPendingSaves`) in parallel. Working-branch metadata comes from the store's shared
`loadBranches()` path and updates fork chips through a store-derived effect. On a cold paint
when the captured snapshot has no rows, it races the graph fetch against a 250ms timer before
committing row state, so the rail doesn't visibly pop in a beat after the list on first
load — this gate is skipped once rows are already on screen (a warm remount or a
subsequent refresh), where the delay would only slow things down. Any response — graph or
row data — is discarded un-applied unless both the captured branch and generation still
own the authority when it resolves. A refresh starts by clearing `loadError`; a row-data
failure that still owns the authority sets it to the toast's text ("Failed to load version
history: <reason>"), which `GitPanel` shows with a Retry (`git-panel-load-retry`, calling
`refresh()`) in place of the empty history while no milestone row is on screen; rows already
shown (a cache seed or an earlier success) stay, and the toast reports their failed
revalidation. On success, every fetched payload is
`serializePayload`'d and written to `gitPanelCache` unconditionally (permissive — a
hydrated mount always revalidates anyway), then each `setState` is skipped individually
when its serialization matches the authority's already-applied value for that branch
— this is what keeps a no-op auto-refresh from re-rendering the row list or re-triggering
the rail's measurement effect.

**Nonce-driven transitions** (`useGitHistory`), each consumed once by the
authority for the current branch:
- `historyNonce` → `refresh()`, no selection change.
- `commitNonce` → `refresh()`, then select `res.milestones[0].sha` if not peeking.
The controller reads current scope at the request boundary; it does not mirror peek state
into a ref during render. Changing branches therefore cannot re-fire a previously handled
nonce or allow an old completion to select a commit in the replacement branch.

No nonce selects a specific commit: selection inside the history is driven by clicking the
history itself.

**Milestone expansion** (`toggleExpand`). A cache hit (`readMilestoneSaves`) expands
synchronously with no fetch or loading placeholder, because a milestone's folded saves are
content-addressed by its immutable merge sha. A miss sets the row to `"loading"`,
fetches, and lands the result only if the row is still `"loading"` when the response
arrives — a collapse before that response lands must not resurrect an abandoned expansion.

**Rail derivation** (`GitPanel`'s `railRowData`, `rail`, and spawn-chip memos). `railRowData` builds the
`RowDescriptor[]` from `pending`/`milestones`/`expanded` in render order. `rail` calls
`computeGitGraphLayout(graph, {viewBranch, rows})`, but only when `graph !== null`,
`milestones.length > 0`, and — critically — `rowsBranch === (viewBranch ?? workingBranch)`:
during a peek's one-round-trip window the row *data* may still describe the previous
branch while `viewBranch` has already changed, and drawing the rail against mismatched
rows would mislabel them, so the rail simply withholds itself until `rowsBranch` catches
up (`refresh()` sets `rowsBranch` only once its own response is applied). `rail === null`
whenever `computeGitGraphLayout` degrades to `laneCount === 0` too. `spawnChipsBySha`
derives in-row branch chips solely from `graph.branches` ancestry. When there is no graph
payload, no topology chips are rendered.

**Overlay geometry** (`GitPanel`'s rail-measurement `useLayoutEffect`). It measures every
`[data-rail-row]` element inside the milestones box after paint (and on `ResizeObserver`
fire), producing `rowGeom: (RailRowGeom | null)[]` aligned to `rail.rows` via an `offset`
(the milestones box only holds the tail of `rail.rows` — pending rows have their own box
and contribute no overlay runs, hence `offset = rail.rows.length - cells.length`). A
stringified geometry key short-circuits `setRowGeom` when nothing actually moved.
`railRuns = computeRailRuns(rail, rowGeom)` is then a pure `useMemo` over that geometry.

**Branch mutation flows.** `GitPanel.submitFork` → `createWorkingBranch(name, {at: sha,
move, switch})`, from the fork draft's mode: `"parallel"` (New branch from here),
`"switch"` (Start a branch from this version, the row button or the menu item) or
`"move"` (Move the later saves to a new branch). The naming step opens at the row
button or the menu's point and is moved back inside the window, 8px from its edges, once
its size is known: the row button sits at the panel's right edge, so a box opened there
would otherwise put Create off screen. While the unsaved-edits question below is open
the naming step is hidden, since its overlay would cover the question, and Cancel brings
it back with the name typed. A refused create (a name git cannot use, a protected or
taken name) is said inside the naming step (`git-panel-fork-error`), which stays open
with the name until it is changed, rather than in a toast, which would sit over Create at
the window's right edge. On `res.switched` it does a full
`window.location.reload()`, since the checkout replaces the working tree and the client
can't safely reconcile in place. A parallel create instead just calls `refresh()`. Before
any switch, switch-mode or move-mode create, the caller's `useDirtyNavigation` guard
reads the project's dirty state, the canvas's or the workbench's form's
(`useProjectDirty`): clean navigation goes ahead (after the branch manager's ordinary
switch confirmation), while dirty navigation opens `GitNavigationConfirm` and requires an
explicit discard or a successful caller-supplied `onSave()` result before mutation. `GitPanel.performSwitch` and
`BranchManager.switchNow` both call `setWorkingBranch(branch, false)`, then
`discardFormEdits` (the workbench's form read again when its unsaved edits were the ones
discarded) and `recordSwitch` (pushing a `useGraphStore` VC undo entry) on success, while
retaining local busy state.
`BranchManager.run()` is the
shared wrapper for its own five mutations (create, switch, archive, delete, restore): on
success, unless `opts.reloadOnDone` is set or `opts.reloadWhen(result)` returns true, it
reloads readiness + the shared branch list and calls
`useGitStore.getState().notifyHistoryChanged()` so any open `GitPanel` refetches; on
failure it sets both a persistent inline banner and a toast. `opts.reloadOnDone` — passed
only by archive/delete, as `b.is_current` — skips that tail and calls
`window.location.reload()` instead, for the *current*-branch case. `doCreate` never sets
`opts.reloadOnDone`; its callback returns the reload outcome when `res.switched`, and
`run()` reloads and returns without starting a duplicate readiness/branch/history refetch
chain while the page is unloading.

**Undo/redo** (`vcHistory.ts`). `recordSwitch`/`recordArchive`/`recordRestore`/`recordDelete`
each push a `{label, undo, redo}` entry onto `useGraphStore`'s VC stacks; every leg is
wrapped by `leg()`, which toasts and resyncs (`loadStatus` + `notifyHistoryChanged`) on
success and toasts + rethrows on failure so the store keeps the entry available for a
retry rather than dropping it. Because the closures only touch stores (never component
state), an entry keeps working after the panel that pushed it unmounts. Delete's inverse
is `undeleteBranch` with the delete's `trash_id` rather than a full re-create, since deletes
are trash-preserving server-side.

A switch's two legs replace the canvas and the workbench's form as the switch did, so each
(`switchLeg`) first awaits `leaveUnsavedEdits` with the `onSave` the recording surface
passed to `recordSwitch` (App's project save, through `GitPanel.performSwitch` and
`BranchManager.switchNow`), titled "Switch back to <from>?" for the undo and "Switch to
<to>?" for the redo. Cancel (or Escape, or another question asked over it) rejects the leg
before `leg()` runs, with no toast and no readiness read, so the store puts the entry back
on the stack it came from. Otherwise the leg runs inside `leg()`: `setWorkingBranch(branch,
false)`, then `discardCanvasEdits` and `discardFormEdits`, both nothing when Save first left
the project clean. The store's `vcBusy` holds further undo and redo while the question is
open.

**Restore flow.** `GitPanel`'s restore affordances (the row button and the row menu's
"Restore this version") call `useGitStore.requestRestore({sha, label})`.
- **Where they show:** on the current branch's own history only (not while peeking), and
  never on the current version. That is `pending[0]`, or with nothing pending
  `milestones[0]` and the first (newest) save expanded under it.
- **The modal:** `App.tsx` surfaces the target as `RestoreConfirmModal`. The modal makes no
  API call: clicking any of its buttons sets a local `busy` flag (blocking
  double-submission) and calls the caller-supplied `onConfirm(saveFirst)`. `saveFirst` is
  `true` only for the dirty-project "Save & restore" button.
- **The handler:** `App.tsx`'s `handleRestoreConfirmed` owns the sequencing. It optionally
  saves first via `saveProject()`, then calls `restoreVersion` (`POST
  /api/git/restore-version`).
- **On success:** it records the label under `JUST_RESTORED_KEY` in `sessionStorage` and
  reloads the page, which toasts "Restored <label> as a new save."
- **On failure:** it toasts "Could not restore this version: <reason>" and closes the modal
  through `closeRestore`.

**Remote push flow.** `RemotePushControl` loads configured remotes and their locally-known
divergence, and auto-selects only a sole remote. That GET never fetches. Project
initialization, server startup, and working-branch confirmation never call `gitPush`;
publication begins only from `onPushClick`. The button tooltip is "Push your
branch and save history. If the remote is empty, Haute also publishes the default branch as
your merge target." Pending saves still interpose the existing confirmation before
`doPush`; otherwise `doPush` calls `gitPush(selected)` directly.

On success, `doPush` branches only on the response metadata. When
`bootstrapped_default=true`, it emits `Published <default_branch> and your branch history to
<remote>`. When false, it retains the ordinary pushed-ref-count toast. It then reads
`ci_secrets`: a `newly_set` name toasts success `Set <n> deploy secret(s) on <repository> from
.env`; a `problem` toasts a warning `Couldn't set the deploy's secrets on <repository>:
<problem>` when the state is `missing`, or when it is `unknown` and the Push bootstrapped, and
an `unknown` state after any other Push shows nothing. The control keeps `{ remote, report }`
for the `CiSecretsMissing` line only when the state is `missing`, and clears it otherwise,
`ci_secrets: null` included, then reloads remote divergence whichever the outcome. The line
renders while that remote is selected: `<n> deploy secret(s) missing on GitHub`, the names
in its tooltip, and **Set from .env**, which calls `gitSetCiSecrets(remote)` with the button
disabled until it settles. Success clears the line and toasts `Set <n> deploy secret(s) on
<repository> from .env`, or `<repository> already has the deploy's secrets` when `newly_set`
is empty; a failure toasts the error `Couldn't set the deploy's secrets: <detail>` and keeps
the line. The frontend neither infers emptiness nor hardcodes
`main`; strict inspection, related-history validation, refspec choice, atomicity, and remote-
host default-branch configuration are backend/out-of-scope concerns. Any refusal follows
the existing push error path and never emits a success toast or retries automatically.
For a structured non-fast-forward 409, only `parseGitPushRejection` is inside the parser
`try`/`catch`. Once parsed, the rejection modal state is committed before a best-effort
remote-list reload; that reload owns and swallows its listing failure while retaining the
last-good remotes (a cold failure starts from the existing empty list), so a badge-refresh
hiccup cannot hide recovery UI or be mislabeled as either a malformed divergence response
or a failed push.

Catch up and Spin off a copy go through the control's `useDirtyNavigation(onSave)` guard,
`onSave` being the project save `GitPanel` passes down. The standalone Catch up button and
the rejection dialog's Catch up call `catchUp(remote)`, and its Spin off a copy calls
`branchAway(remote)`: the standalone button passes the remote selected at the click, the
dialog `rejection.remote`, so a selection moved while the push was pending changes neither.
Each guards `doCatchUp(remote)` or `doBranchAway(remote)` with the question "Save them
before catching up? Catching up replaces the project's files with <remote>'s." or "Save
them before spinning off a copy? Spinning off sets your version aside and replaces the
project's files with <remote>'s.", naming the remote the request is sent to. While the
rejection dialog is open the question renders inside it, in place of its actions; otherwise
it renders on the control's own row. Closing the dialog (Escape, its backdrop) cancels a
question open in it, and selecting another remote cancels one on the row, so neither can run
later. Push and the standalone Catch up are disabled while any of a push, a catch-up or a
spin-off runs (`busy`). On success each drops the edits chosen over (`discardCanvasEdits`, then
`discardFormEdits`), toasts, closes the dialog, reads the readiness with a request issued
after the action (`loadStatus({ refresh: true })`), calls `notifyHistoryChanged` so the
branch list and history refetch, and reloads the remotes; a failure toasts and reads the
readiness as before, and keeps the edits.

## Edge cases and invariants

- **A save must never move the selection; a commit must, but only on the user's own
  branch.** Enforced by keeping `historyNonce`/`commitNonce` as separate nonces and gating
  the commit-effect's selection on `!peeking` evaluated when the refresh resolves
  (`[applyAction, commitNonce, peeking, refresh]`).
- **Expansion state is not cleared by a refresh.** `refresh()` never dispatches an
  expansion action — clearing it on every refresh would collapse a milestone the user just
  opened — and `expanded` returns to `{}` only because replacing the keyed branch scope
  re-runs `initialState()`.
- **Byte-identical revalidation is invisible.** The `useGitHistory` reducer's `rows` action
  compares `milestonesJson` and `pendingJson` against the prior state; an unchanged background
  refresh reuses the existing array identities and returns `state` unchanged, producing zero
  state updates for milestones and pending saves. This preserves array identity through to
  the rail's memos and ensures rail layout and overlay measurement do not re-fire on a
  no-op refresh.
- **Stale-refresh generation guard.** A slower in-flight refresh resolving after a faster,
  newer one must not overwrite the newer result, and must not clear `loading` out from
  under it — `useGitHistory` guards completion, error handling, and the `finally` block with
  an `ownsRequest()` check over `generationRef` and `aliveRef` (see
  `frontend/src/panels/__tests__/GitPanel.staleRefresh.test.tsx`).
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
- **A menu's backdrop takes the next right-click.** Every `BackdropMenu`, and the fork
  naming step's `MenuBackdrop`, closes on a click or a right-click on its backdrop, and the
  right-click calls `preventDefault`, so a second right-click over an open menu never
  shows the browser's menu. The naming step's backdrop ignores both while its create is
  under way. The backdrop is the menu's previous sibling, at `z-40` under the menu's
  `z-50`.
- **Fork-menu availability while peeking.** The context menu always opens (never falls
  through to the browser menu) on any row, but while peeking it offers only "View
  side-by-side". Restore, Start a branch, New branch from here and "Move the later saves" only
  make sense relative to the user's own working branch, and the row buttons for Restore
  and Start a branch are left out too.
- **Create-and-move eligibility is narrower than starting a branch.** `canMove` gates only
  "Move the later saves to a new branch": it is true for the newest milestone and pending saves, and
  false for older milestones and saves already folded inside a milestone. Start a branch
  and New branch from here are available on every row of the user's own history.
- **`BranchManager`'s archive-vs-delete asymmetry.** Archive on the *current* branch with
  uncommitted tracked project changes redirects to a "commit a milestone first" prompt
  instead of proceeding. Before either current-branch archive or confirmed delete reaches
  its API call/reload, its `useDirtyNavigation` guard separately protects unsaved in-memory
  edits, the canvas's or the workbench's form's,
  with Cancel/Discard/Save-first. Delete remains enabled because its own dialog says what
  follows (`deleteOutcome()`): the server pins both of the pair's tips in the trash, so
  the branch, with its saves not yet in a milestone, can be restored from Deleted (or the
  toolbar's Undo, for a branch other than the current one), while the current branch's
  project changes since the last save are discarded with the checkout away and are named
  as lost. That confirmation never bypasses the canvas guard.
- **`RestoreConfirmModal` self-guards independently of its caller.** `App.tsx` only mounts
  it once `restoreTarget` is truthy, but the component also reads `restoreTarget` itself and
  returns `null` when unset. After an action sets `busy`, button, Escape, and backdrop close
  paths are all inert until the parent settles the restore.
- **`RemotePushControl` never defaults to a push target** unless exactly one remote exists
  — with 0 remotes it shows an explicit "no remotes" message, with 2+ it requires a
  deliberate selection, so a push is never accidental.
- **The deploy's-secrets line is response-driven.** It appears only from a Push response
  saying `missing` for a remote; mounting, refreshing and listing remotes never ask for the
  secrets, and only the Set from .env click sends them anywhere.
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

- `frontend/src/api/client.ts` and `ApiError` are owned by
  [frontend-shared](../frontend-shared/low-level.md). The Git request/response wire contract
  is owned by [git-integration](../git-integration/low-level.md), while backend HTTP routing
  and status behaviour are owned by [server-api](../server-api/low-level.md). Handlers narrow on
  `err instanceof ApiError && err.status === 409` to distinguish the two structured
  rejection bodies (`GitMilestoneFork`, `GitPushRejection`) from all other errors.
  Generic Git failures pass through the shared `apiErrorMessage`
  ([frontend shared](../frontend-shared/low-level.md)) with a call-site fallback: a structured
  detail's `message` (both 409 bodies carry a leg-naming one), else a string detail, else an
  ordinary `Error.message`, else the fallback. A structured 409 body that is neither rejection
  is reported by its status, never printed as raw JSON.
- `parseGitMilestoneFork` / `parseGitPushRejection` (`frontend/src/api/client.ts`, async
  because they load the lazy generated git validators) resolve `null` only before their
  status discriminator matches. Once a body declares `would_fork` / `rejected_diverged`, the
  generated `GitMilestoneFork` / `GitPushRejection` validator checks it, a malformed body
  rejects, and each call site converts that failure into a plain error toast instead of
  crashing the modal.
- The generated `GitPushResponse` validator requires `default_branch` and
  `bootstrapped_default` and type-checks both. A malformed success body rejects through the normal request promise and
  reaches `RemotePushControl`'s error toast; the UI must not infer a bootstrap from
  `pushed_refs` or silently substitute `main`.
- `useGitHistory.refresh()`'s catch only toasts, records `loadError` and returns `null` when
  its own generation is still current — a superseded refresh's rejection is swallowed
  entirely, neither toasted nor shown (the newer refresh owns error reporting for that
  logical operation).
- `BranchManager`'s "don't ask again" save (`setGitPrefs`) is fire-and-forget beside the
  switch it accompanies, but its failure toasts "Couldn't save "Don't ask again" on this
  machine: <reason>"; the switch goes ahead, and this session keeps not asking.
- A toolbar undo's or redo's switch cancelled at the unsaved-edits question rejects without
  a toast; only a leg that ran and failed toasts "Undo/redo failed: <reason>".
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
- `RemotePushControl.load()` distinguishes failure from a successful empty response. A
  failure retains the last good remotes and sets a visible load-error state; the rejection
  modal renders outside the empty/error body so refresh failure cannot dismiss an
  in-progress recovery flow.
- `RestoreConfirmModal` itself throws and catches nothing — it is a stateless (aside from
  `busy`) confirm surface. The actual try/catch of a restore (a failed `saveProject()`, a
  failed `restoreVersion` call) lives in `App.tsx`'s `handleRestoreConfirmed`, which toasts
  and calls `closeRestore()` on either failure rather than leaving the modal open in a stuck
  busy state.
- `useGitStore.loadStatus()` catches failures into `statusError` using the shared Git
  formatter while retaining its last successful `status`. A successful retry clears the
  error. A first-load failure and a successful `state: "no-repository"` response therefore
  remain visibly and behaviorally distinct.

## Testing

Tests live alongside the source: `frontend/src/panels/__tests__/`,
`frontend/src/panels/gitgraph/__tests__/`, `frontend/src/components/__tests__/`, and
`frontend/src/stores/__tests__/useGitStore.test.ts`. All are Vitest + React Testing
Library component/unit tests (no e2e for this surface).

- **`frontend/src/stores/__tests__/saveCapture.test.ts`** — the save-capture report: the
  indicator's SHA (and left as it is without the field), the history nonce, each warning's
  toast, the identity prompt opened once per session and not over itself, the uncaptured
  files kept once each and taken by one capture, nothing sent with nothing kept, a file
  still skipped kept again and every file kept with its toast when the request fails, and
  a file a write reports while the capture is under way kept.
- **`frontend/src/panels/__tests__/GitPanel.test.tsx`** — the primary behavioural suite: rendering, milestone
  expand/collapse, pending-save display, both refresh nonces, right-click menus
  (fork/view/restore/start a branch) in every row context (milestone / pending save /
  expanded save), the Start a branch row button opening the naming step and creating a
  switched branch at that row,
  peeking's effect on menu contents, the fork-creation dialog, the graph rail's dots/
  stubs/chips/magnifier, lane and dot context menus, in-app branch switching from the
  lane menu (including the dirty-canvas guard, and the workbench's form's edits dropped
  once a lane switch chosen over them succeeds), the peek in-flight mislabel guard, and the
  restore affordance: shown on older milestones, pending and folded saves, absent on the
  current version and while peeking, the row button and menu item requesting a restore of
  that row; and a failed history read shown with its reason and a Retry in place of the
  empty history, its toast kept, and Retry reading the history again.
- **`frontend/src/panels/__tests__/GitPanel.gaps.test.tsx`** — targeted gap-fill: fork-with-switch page reload, fork
  failure toast, fork-with-move submission, the view affordance opening a comparison,
  peeking's menu contents (a second angle on the same invariant as the main suite).
- **`frontend/src/panels/__tests__/GitPanel.cache.test.tsx`** — the session-cache contract end to end: unchanged-payload
  short-circuit (no re-render), a changed payload applying, warm-remount hydration (both
  with and without a subsequent change), sha-cached milestone-save re-expansion never
  refetching, a late status resolution hydrating the cache the initial seed missed without
  clobbering rows a completed refresh already applied, and peeking an uncached branch
  clearing rows rather than carrying stale ones over.
- **`frontend/src/panels/__tests__/GitPanel.staleRefresh.test.tsx`** — the generation guard specifically: an earlier
  slow refresh resolving after a later fast one must not overwrite the later branch's
  rows or clear the newer refresh's loading state, a superseded refresh's failure must
  show neither the load error nor a toast, and a peek refresh must not replay
  already-active nonce effects.
- **`frontend/src/panels/__tests__/gitPanelCache.test.ts`** — unit tests on the cache module directly: serialization
  round-tripping, per-branch LRU storage/eviction, milestone-save sha caching, the
  single-slot graph cache, and `clearGitPanelCaches`.
- **`frontend/src/panels/gitgraph/__tests__/layout.test.ts`** — the largest and most detailed suite in the
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
- **`frontend/src/panels/gitgraph/__tests__/GraphCell.test.tsx`** — narrowly scoped to `GraphRailOverlay`'s
  bottom-anchored dash-phase convention for dotted runs (both spine and siding kinds) vs.
  the top-anchored convention for solid runs.
- **`frontend/src/stores/__tests__/useGitStore.test.ts`** — store unit tests: status load success/
  failure with retained actionable detail, concurrent readiness-request de-duplication,
  shared branch publication and request de-duplication, modal open/close and pending-
  action interaction (including the close-clears-pending-action regression), peek
  set/clear, each nonce incrementing exactly once per action, comparison and
  restore-target toggling.
- **`frontend/src/components/__tests__/BranchManager.test.tsx`** — listing (current/others/archived),
  peek-without-switch, create (with and without move, including the move confirm step),
  switch confirmation (including persisted "don't ask again") and its in-app (no-reload)
  behaviour plus the VC undo entry, dirty-navigation Cancel/Discard/Save-first behavior
  for switch and Create & Move (including the skipped-confirmation preference, the guard
  on the workbench's form's unsaved edits, and those edits dropped once a switch chosen
  over them succeeds and kept when it fails, and likewise the canvas's edits, a resync
  requested once they are dropped), archive
  (direct and the dirty-current redirect), delete with confirm, restore, the
  "not saved" / "saves to commit" indicator display, the Deleted section (restore, and
  restore under another name when the name is taken), the persistent error banner, the full row
  context-menu surface (open, per-state item visibility, each action's routing, backdrop
  dismiss), a "don't ask again" whose save fails toasting while the switch goes ahead, and
  the delete dialog's wording: the branch goes to Deleted and can be restored from there
  (with its saves not yet in a milestone when it has some), never "Permanently", and on the
  current branch with changes since the last save, those named as lost.
- **`frontend/src/components/__tests__/BranchManager.gaps.test.tsx`** — the `reloadOnDone` matrix:
  which mutations reload the page and which don't, keyed on `switched`/`is_current`, plus
  the dirty-canvas guard on current-branch archive and delete before those reload paths.
- **`frontend/src/components/__tests__/BranchIndicator.test.tsx`** — checking and retryable-error
  states, full error tooltip on hover and Retry focus with an accessible description,
  no-repository/unset/detached/invalid/divergent/ready rendering, an interrupted clone
  naming its files with a Retry that reloads readiness on success and toasts the files
  still held on refusal, the branch-name
  click's panel/store side effects, and that the ready state stays branch-only — carrying no
  save SHA, including while a comparison is open.
- **`frontend/src/components/__tests__/CommitBreadcrumb.test.tsx`** — root/milestone collapse-to-anchor
  rendering, the non-milestone nearest-milestone→commit breadcrumb, and
  `ComparisonDelta`'s singular/plural count text. The breadcrumb's and the history rows'
  times are pinned with every other panel's in
  `frontend/src/__tests__/relativeTimeSurfaces.test.tsx` (one instant reads the same in
  each).
- **`frontend/src/components/__tests__/BackdropMenu.test.tsx`** — the menu primitive: the
  menu at the pointer with its title line and items, the backdrop as its previous sibling
  closing it on a click and on a right-click that is `preventDefault`ed, an item's click,
  a disabled item, and `MenuBackdrop` alone.
- **`frontend/src/hooks/__tests__/useDirtyNavigation.test.tsx`** — the guard: a clean
  project runs the action at once with no question; a dirty one asks, Cancel dropping the
  action, Discard running it, and Save first running it only after `onSave()` answers true
  (false, a throw or no `onSave` keeping the question open).
- **`frontend/src/components/__tests__/RestoreConfirmModal.test.tsx`** — the restore wording
  with the target's label, clean-canvas single-confirm calling `onConfirm(false)`,
  dirty-canvas save/discard button pair calling `onConfirm(true)`/`onConfirm(false)`
  respectively with the warning message shown, the same pair for unsaved edits in the
  workbench's form alone, Cancel calling `onClose` without confirming, busy
  Escape/backdrop dismissal refusal, and rendering nothing when `restoreTarget` is `null`.
- **`frontend/src/__tests__/App.integration.test.tsx`** — the parent-owned restore
  transaction rejects a failed `restoreVersion` request with an error toast and clears
  `restoreTarget`, proving the busy-locked `RestoreConfirmModal` cannot remain stranded
  after its promise settles; a restore that succeeds reloads and the reloaded page toasts
  the restore; a detached project opens the branch chooser at startup on "Create new
  branch…"; and Save captures the files earlier writes left uncaptured before it saves the
  pipeline.
- **`frontend/src/components/__tests__/MilestoneCommitModal.test.tsx`** — submit gating on message
  presence, visible validation above the 500-character maximum, successful commit
  with/without a version label, the 409 fork-warning → commit-anyway override flow, and
  backing out of that warning.
- **`frontend/src/components/__tests__/WorkingBranchModal.test.tsx`** — eligible-branch listing plus
  create option, adopting an existing branch, creating a new one, submit gating, the
  inline identity sub-form, a detached project starting on "Create new branch…" with
  its explanation (and no explanation otherwise), an invalid project listing every
  one of its errors, a broken pair named with its Repair, and a Repair leaving the project
  ready confirming the chooser.
- **`frontend/src/components/__tests__/BrokenPairs.test.tsx`** — each kind of broken pair
  in words, nothing when every pair is whole, a Repair that calls the route, toasts its
  outcome and reads the readiness and history again, and a refused Repair toasting the
  server's reason. `frontend/src/components/__tests__/BranchManager.test.tsx` lists a broken
  pair above the branches, and `frontend/src/__tests__/App.integration.test.tsx` reports one at
  startup in a toast whose Repair repairs it.
- **`frontend/src/components/__tests__/RemotePushControl.test.tsx`** — no-remotes messaging, sole-
  remote auto-selection, multi-remote deliberate-selection requirement, the pending-save
  push confirm (both proceeding and cancelling), ahead/behind display in all states
  (synced / diverged / unknown-vs-never-pushed), ledger-status display (behind / forked /
  silent-when-synced), the 409 rejection modal, non-409 fallback toast, catch-up
  offer/exclusion by leg state, the rejection modal's catch-up vs. spin-off-a-copy
  routing, and the rewrite-specific heading. Push-success coverage pins the exact tooltip,
  the special bootstrap toast using a custom `default_branch`, the ordinary toast when
  `bootstrapped_default=false` (including an idempotent later push), post-push reload, and
  the guarantee that no push occurs merely from mount/startup/branch selection. Deploy's-
  secrets coverage: the toast for secrets a Push set; the warning for a first publication's
  problem, and silence for an `unknown` state after a later Push; the missing-secrets line
  with its names, shown only for its remote; Set from .env sending the selected remote, then
  clearing the line on success and keeping it with an error toast on failure; and a later
  Push reporting none missing clearing it. The dirty-project guard on the standalone Catch
  up, the rejection dialog's Catch up and its Spin off a copy, each with unsaved canvas
  edits: the question naming the remote (inside the dialog when it is open), Cancel sending
  no request, Discard sending it, and Save first saving and then sending it; a clean project
  sending it at once; the form's and the canvas's edits dropped once it succeeds and kept
  when it fails; and closing the dialog dropping its open question. With the selection moved
  to another remote while the push was pending, the dialog's Catch up and Spin off a copy
  each ask about and send the remote that refused the push; selecting another remote drops
  the standalone Catch up's open question; and neither Push nor Catch up starts while the
  other runs.
- **`frontend/src/utils/__tests__/vcHistory.test.ts`** — each `record*` entry's legs call
  the right client function with the right side's branch name, toast and resync on
  success, toast, read the readiness and rethrow on failure. A switch's undo and redo with
  unsaved edits ask the page's question first: Cancel switches nothing, puts the entry back
  on its stack and reports nothing; Discard switches and drops the canvas's and the form's
  edits; Save first saves and then switches, asking again after a save that fails; with no
  save there is no Save first; and a clean project switches without asking.
- **`frontend/src/components/__tests__/RemotePushControl.gaps.test.tsx`** — error-message fidelity for
  catch-up and branch-away failures (`Error` vs. non-`Error` rejection), unparseable-409
  fallback to a plain toast (raw structured JSON is not rendered, while a human-readable
  detail is retained), remote-list failure distinct from an empty configuration, and
  last-good remotes plus parsed-rejection UI surviving a failed best-effort refresh without
  a false push/parser toast. It also covers the full
  behind/ahead/diverged×working/ledger catch-up-eligibility matrix.
- **`frontend/src/api/__tests__/client.test.ts` and
  `frontend/src/types/__tests__/guards.contract.test.ts`** — the push
  client/guard contract requires `default_branch` and `bootstrapped_default`, preserves
  submitted `pushed_refs`, accepts both boolean values, and rejects missing or wrongly typed
  bootstrap metadata rather than inventing a default. `gitSetCiSecrets` posts the remote to
  `/api/git/ci-secrets` and rejects a `GitCiSecrets` whose state is not one of the three, and
  both it and `gitPush` still answer after five minutes, where the default deadline would
  have ended them.
  The guard suite also accepts exactly
  the eight working-branch readiness states, preserves detached `head_sha`, and rejects an
  unknown state.
- **`frontend/src/components/__tests__/DivergenceModal.test.tsx`** — recorded/current branch naming,
  each of the three resolution choices, and stay-here's disabled state when the current
  branch isn't eligible.
- **`frontend/src/components/__tests__/DivergenceModal.gaps.test.tsx`** — error-toast surfacing on a
  rejected `setWorkingBranch` (both `Error` and non-`Error`), the busy/"Working…" disabled
  state, double-submit guarding, and null-status placeholder rendering.
- **`frontend/src/components/__tests__/ModalForm.test.tsx`** — the shared heading, the identity
  fields (change callbacks under the caller's test ids, email type, autofocus, the global-config
  checkbox) and the cancel/submit row (cancel, busy label on a disabled submit).

Known coverage gaps: none flagged in the suites' own comments; the layout test file's
breadth (60+ cases) suggests `computeGitGraphLayout`/`computeRailRuns` are the
highest-risk, most thoroughly defended part of this component, consistent with them being
the only pure, intricate geometry code in the surface.

## Canonical branch source

Under the [canonical-only format policy](../README.md#canonical-only-format-policy),
the Git panel renders topology chips only from the current graph payload.
