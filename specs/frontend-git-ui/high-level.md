# Frontend Git UI — High-Level Specification

## Purpose

Pricing analysts using Haute are not git-fluent, so the product never shows them a raw
git CLI. This component is the visual surface for the working-branch version-control model:
a persistent toolbar indicator, a slide-out panel with a branch manager and a save/milestone
history (drawn as a small commit-graph "rail"), and the handful of modals that gate
branch selection, milestone commits, divergence recovery, and remote pushes. Its job is to
make "save my work", "record a version", "go back", "try a parallel idea", and "share with
a remote" feel like ordinary product actions rather than version-control operations.

It is a pure consumer of [git-integration](../git-integration/high-level.md): every button
here calls one of that layer's HTTP endpoints and renders the guardrailed result. This
component owns no git logic of its own — only presentation, client-side layout of the graph
rail, optimistic/cached read paths, and the client-side undo/redo bookkeeping for a handful
of branch operations.

## Scope

In scope:
- The toolbar working-branch indicator (`BranchIndicator`) — branch name + last-save SHA,
  and the entry point into the panel.
- The Git side panel (`GitPanel`): out-of-version (pending) save list, milestone list with
  expandable folded saves, the graph rail (lanes, spawn stubs, magnifiers), right-click
  context menus for forking/viewing/moving/switching, and the fork-naming dialog.
- The branch manager (`BranchManager`), embedded in the panel: list/create/switch/archive/
  delete/restore of working branches, with their confirmation dialogs.
- The five git modals: `WorkingBranchModal` (branch selection at startup / save-gate),
  `MilestoneCommitModal` (save & commit a milestone, including the fork-warning override),
  `DivergenceModal` (recover when the recorded working branch and HEAD disagree),
  `MoveConfirmModal` (the pre-move save/discard/confirm prompt — see Behaviour), and the
  push surface `RemotePushControl` (remote selection, ahead/behind, explicit publication
  including empty-remote default-branch bootstrap, fast-forward catch-up, and the non-fast-
  forward rejection recovery flow).
- `CommitBreadcrumb`, the small version-relative label used on comparison canvases.
- Client-side state for this surface: `useGitStore` (readiness plus retryable load errors,
  shared branch metadata, modal routing, peek/compare/move targets, refresh nonces) and the
  session-lived read caches in `gitPanelCache.ts`.
- Pure layout of the graph rail from the graph-topology payload (`panels/gitgraph/`).

Out of scope (owned by neighbouring components):
- All git mutation and guardrail logic (branch create/save/commit/fork/archive/delete,
  protected-branch enforcement, push/fetch, error taxonomy) — see
  [git-integration](../git-integration/high-level.md).
- `frontend/src/api/client.ts` and `ApiError` are owned by
  [frontend-shared](../frontend-shared/high-level.md). The Git request/response wire
  contract is owned by [git-integration](../git-integration/high-level.md). Backend HTTP
  routing and status behaviour are owned by [server-api](../server-api/high-level.md).
- Shared chrome this component builds on but does not own: `PanelShell`, `ModalShell`,
  `Tooltip`, toast notifications — see
  [frontend-shared](../frontend-shared/high-level.md).
- The canvas-side comparison view that `openComparison`/`requestMove` drive (the dual-
  canvas overlay itself, diffing, and the historic↔current delta rendering beyond
  `CommitBreadcrumb`'s `ComparisonDelta`) — owned by the graph-canvas surface, see
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md).
- The websocket sync that lands a branch switch on the canvas after `setWorkingBranch`
  resolves.
- Any hosting-provider control plane used to select a repository-level default branch;
  this UI reports git-ref publication only and does not claim to reconfigure GitHub,
  GitLab, or another remote host.

## Behaviour

**Toolbar indicator.** While the first readiness request is in flight it shows a quiet
checking state. A transport/server failure produces a visible "Git unavailable" state with
the backend detail and a Retry action; it is not confused with a project that has no
repository. Successful responses render distinct labels and remediation for no repository,
unset working branch, invalid state, attached divergence, and detached HEAD (including its
short SHA). A ready state shows the working branch name plus the last-save short SHA.
Clicking the ready name opens the panel on the current branch; clicking the SHA opens the
panel and selects the latest save in the history — unless a comparison is open, in which
case it points at and selects the compared version instead.

**Panel — save history.** The panel shows, top to bottom: the remote push control, the
branch manager, an optional "peeking another branch" banner, out-of-version (pending)
saves in a highlighted box, then the milestone list. Every milestone can be expanded to
reveal the individual saves folded into it. Milestones and saves each carry: a version
label or "init" tag, message, short SHA with a tooltip, a relative time, a view (read-only
compare) affordance, a move-to-this-version affordance, and — when a branch was spawned
from that exact point — clickable branch chips ("fork links") back to the spawned branch.
Right-clicking any row opens a context menu; on the user's own (non-peeked) branch it also
offers "new branch from here" and, on the branch tip only, "new branch & move work here".

**Graph rail.** When the whole-forest graph topology loads successfully, each row in the
history additionally draws a small rail: a coloured lane for the viewed branch and its
ancestor chain, dots for milestones and saves, a dotted "siding" alongside an expanded
milestone's folded saves, and short curved "spawn stubs" for branches that fork off the
visible history without owning their own lane. A magnifier toggle sits beside any
milestone that folds saves. A header strip above the list shows one clickable chip per
branch departing the visible spine, plus an overflow count for departures whose anchor
falls outside the loaded window. The rail is chrome: if the graph fetch fails, the panel
still renders the row list without it, silently.

**Branch manager.** Lists the current branch in its own box, other live branches, then
archived branches, with a "Create" / "Create & Move" split control. Peeking (viewing
another branch's history) never switches the working branch; switching, archiving,
deleting, and restoring do, each behind role-appropriate confirmation (switch has a
persistable "don't ask again"; delete always confirms and names what it discards;
archiving the current branch with uncommitted tracked project changes redirects to "commit
a milestone first" instead of proceeding). A switch or Create & Move that would replace a
dirty in-memory graph always requires Cancel, Discard, or Save first; the persisted
preference to skip clean switch confirmation never bypasses this dirty-work guard.
Current-branch archive and delete
use the same guard before their mutation/reload path, including when the branch also has
saved work awaiting a milestone. Save first must complete successfully before the Git
mutation starts.

**Modals.** `WorkingBranchModal` gates first use (and any save attempted with no working
branch set): choose among eligible branches or create one, optionally set git identity
inline. `MilestoneCommitModal` records a message (required, at most 500 characters with
visible validation when exceeded) and version label (optional) against the accumulated
ledger saves; a 409 from the server surfaces a "committing now
would fork the remote" warning with an explicit "commit anyway" override rather than a
dead-end error. `DivergenceModal` appears when the recorded working branch and the actual
HEAD disagree (the repo was moved outside Haute) and offers three resolutions: return to
the recorded branch, adopt the current one (only if attached and eligible), or defer to the
branch manager. Detached HEAD is named as detached at its commit, never as a branch called
`HEAD`. `MoveConfirmModal` gates every move-to-version action (`GitPanel`'s row/lane
"move to this version" affordance): on a clean canvas it is a single confirm; when the
canvas has unsaved edits it forces an explicit choice between saving them onto the
current branch first or discarding them, because a move is a real checkout that replaces
the whole working canvas and in-memory-only edits — which never reached disk — would
otherwise be lost silently. Once a move starts, its modal ignores Cancel, Escape, and
backdrop dismissal until the asynchronous operation settles, preventing a second move from
being opened over the first.

**Remote push.** `RemotePushControl` requires a deliberate remote selection (no default
push target unless exactly one remote exists), shows the working branch's and save-ledger's
last-known ahead/behind/diverged state per remote, and warns (overridably) before pushing
out-of-version saves. Merely opening or refreshing the panel never fetches. Nothing remote
is triggered by project initialization, server startup, working-branch creation, or a list
request; Push, Catch up, and Spin off a copy are the deliberate freshness/network boundaries.
An initial remote-listing failure is shown as an error, not as "No remotes configured"; a
failed refresh retains the last good remote list and cannot hide an already-open push
rejection modal.

The Push tooltip says: "Push your branch and save history. If the remote is empty, Haute
also publishes the default branch as your merge target." When that explicit push reports
`bootstrapped_default=true`, success is surfaced with the distinct toast `Published
<default_branch> and your branch history to <remote>`. Otherwise the ordinary pushed-
branch-count toast remains. On a non-fast-forward rejection the control shows an honest
recovery modal: fast-forward catch-up when the fork is behind-only, or "spin off a copy"
(never a local merge) when it has genuinely diverged.

**Invariants.**
- A plain save never changes the panel's selection; a milestone commit does (selects the
  new milestone), but only when viewing the current branch's own history.
- Peeking a branch never mutates working-tree state; only "switch" (branch manager /
  lane context menu) and "move to version" perform a real checkout.
- The rail's row list and the graph payload are fetched independently and may transiently
  disagree; the rail withholds rendering rather than mislabel rows during that window
  (see Failure model).
- Right-clicking any history row always calls `preventDefault` — the browser's native
  context menu never appears over the panel.

## Design rationale

**Rail is derived, not fetched pre-shaped.** The graph endpoint returns raw ancestry
(branches, fork points, tip SHAs) rather than a pre-laid-out rail, and `computeGitGraphLayout`
does the lane/slot/dot assignment client-side (see
[low-level.md](low-level.md#control-flow)). This keeps the server oblivious to panel
presentation and lets the same payload serve any future row ordering without a new
endpoint.

**Session cache, not a persistent one.** `gitPanelCache.ts` exists purely because the panel
is conditionally mounted — every open would otherwise re-run four fetches behind a loading
flash. It is deliberately permissive (module-level, no invalidation beyond LRU eviction and
sha-content-addressing) because `GitPanel` always revalidates after hydrating from it and a
byte-identical revalidate is a no-op; staleness is bounded to one round trip. Nothing here
is durable across a page reload — that would risk showing branch state from before an
external change.

**Two refresh-triggering nonces (`historyNonce` vs `commitNonce`), not one.** A background
save must not disturb what the user is looking at; a milestone commit is a deliberate
action whose result the user wants to see. Both refetch identically but only the latter
drives a selection, so they're kept as separate store fields rather than one refresh
signal with a flag.

**One shared branch-list load.** `useGitStore` owns branch metadata and de-duplicates an
in-flight request. `GitPanel` derives fork chips from that shared result and `BranchManager`
renders it; they do not independently call the same endpoint. A mutation or save/commit
event schedules one branch-list refresh and one history refresh, not a refetch chain from
each mounted consumer.

**One Git error-message policy.** Git UI actions use a shared formatter that prefers a
human-readable string `ApiError.detail`, then ordinary `Error.message`, then a stable
fallback. A serialized structured detail is reserved for the dedicated rejection parsers;
if it does not match one of those contracts, the formatter uses the plain error message
instead of displaying raw JSON. Consequently protected-branch, duplicate-label, ledger,
and dirty-state messages authored by the backend survive every relevant toast and inline
banner.

**Peek is separate from working-branch state.** `peekBranch` lives in `useGitStore` (not
component state) specifically so the toolbar indicator can return the panel to "current"
without the panel being mounted — a component-local peek couldn't survive the panel being
closed and reopened mid-peek.

**Generation-guarded refresh, not request cancellation.** Concurrent refreshes (a peek
followed immediately by a switch, for instance) are common given how many triggers call
`refresh()`. Rather than aborting in-flight fetches, each refresh stamps a monotonic
generation counter and any response that lands after a newer refresh started is discarded
un-applied — simpler than plumbing `AbortController` through every call site, at the cost
of a wasted network round-trip on the superseded request.

**Fork/push rejections get dedicated recovery UI, not generic error toasts.** Both
`MilestoneCommitModal`'s 409 and `RemotePushControl`'s non-fast-forward 409 carry
structured per-leg divergence data from the server; the UI parses it and offers the exact
next action (commit-anyway, catch-up, spin-off-a-copy) rather than leaving the user at a
dead end. A parse failure (unexpected body shape) falls back to a plain toast rather than
crashing the modal.

**Never a local merge.** Every divergence-recovery path (branch-away, spin-off-a-copy,
catch-up) either fast-forwards or forks off a new branch; there is no code path in this UI
that could trigger a three-way merge, matching the underlying engine's guarantee.

**Bootstrap uses the existing explicit Push.** A separate automatic onboarding push would
make `init`, `serve`, or branch selection depend on remote availability and credentials.
Using the already-deliberate Push action keeps the remote boundary understandable while the
response metadata lets the UI explain the one extra ref published only for a genuinely
empty remote. The UI reads `default_branch`; it never hardcodes `main`.

## Interactions

- Depends on [git-integration](../git-integration/high-level.md) for every Git read and
  mutation and for the Git request/response wire contract consumed by the git-prefixed
  functions and `Git*` types.
- Depends on [frontend-shared](../frontend-shared/high-level.md), which owns
  `frontend/src/api/client.ts` and `ApiError`, for transport and 409 error delivery.
- Depends on [server-api](../server-api/high-level.md) for backend HTTP routing and status
  behaviour, not for ownership of the frontend transport.
- Depends on shared chrome — see [frontend-shared](../frontend-shared/high-level.md):
  `PanelShell` (the sliding panel frame), `ModalShell` (modal frame/backdrop/focus),
  `Tooltip`, `ConfigCheckbox`, and the toast store (`useToastStore`).
- Depends on `useGraphStore` (owned by
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md)) for `pushVcEntry` —
  recording branch switch/archive/restore/delete as undoable history entries
  (`frontend/src/utils/vcHistory.ts`) — and for `MoveConfirmModal`'s read of the `dirty`
  flag, which decides whether the pre-move prompt forces a save/discard choice or offers a
  single plain confirm.
- Depends on `useUIStore` for the panel's open/closed chrome state (`gitOpen`).
- `useGitStore.openComparison` is consumed by
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md) to drive the read-only
  comparison overlay; this component only sets the target, it does not render the
  comparison itself. `useGitStore.requestMove`/`moveTarget`, by contrast, drives this
  component's own `MoveConfirmModal` — rendered directly by `App.tsx` alongside the other
  git modals, not by the graph canvas.
- `CommitBreadcrumb` is rendered by the comparison canvas top bar, sourced from a
  git-integration commit-context endpoint.

## Failure model

This codebase prefers loud failure over silent fallbacks; within that, this component
draws a hard line between **history/chrome reads**, which degrade silently because they
are not the user's data, and **mutations**, which always surface an error.

- **Readiness load (`loadStatus`)** distinguishes a successful `no-repository` response
  from transport/server failure. The latter records a visible, retryable error while
  retaining any previous successful status; Retry is always a user action. Pipeline Save
  remains available without a repository, while Git Commit remains unavailable.
- **History reads** (`getMilestones`, `getPendingSaves`, `getWorkingBranches`) on the
  panel's main `refresh()` path show an error toast on failure — this is the user's saved
  work, so a failure is not silent — but a *superseded* refresh's failure is dropped
  without a toast (the newer refresh owns reporting).
- **Graph topology fetch** is explicitly best-effort: it is caught separately from the
  row-data fetch, never toasts on failure, and a failed refetch keeps the last-good graph
  rather than nulling it (nulling would flip the whole list between rail and no-rail
  layouts on a transient blip).
- **Remote listing** (`RemotePushControl`) degrades to an explicit "No remotes configured"
  message after a successful empty response rather than an empty, confusing dropdown. A
  cold-load failure is shown separately; a later refresh failure retains the last-good
  remotes so transient chrome failure cannot erase an active divergence-recovery modal.
- **Milestone-save expansion** (`toggleExpand`) shows an error toast and rolls the row
  back to collapsed on failure, rather than leaving a permanent "loading" placeholder.
- **Mutations** (create/switch/archive/delete/restore branch, commit milestone, move to
  version, push, fast-forward, branch-away, fork) always surface a toast on failure, and several also
  persist the error inline (`BranchManager`'s `actionError` banner) since a modal or panel
  dismiss elsewhere in the app should not silently swallow it.
- **Push inspection/validation failures** use that mutation path: authentication, timeout,
  non-empty-missing-default, or unrelated-history refusal shows the push error toast, does
  not show the bootstrap success toast, and is never retried automatically.
- **Structured 409 bodies** (fork warning, push rejection) are parsed defensively
  (`parseGitMilestoneFork`, `parseGitPushRejection`); an unparseable body falls through to
  the generic error-toast path rather than throwing.
- Nothing in this component retries automatically; every recovery (catch-up, branch-away,
  retry a switch) is a distinct user-initiated action.

**Shared switch policy, separate entry points.** `GitPanel.performSwitch` and
`BranchManager.switchNow` remain separate mutations for the rail lane menu and branch manager,
but both use the same dirty-navigation confirmation component, Save-first callback, and Git
error formatter.
