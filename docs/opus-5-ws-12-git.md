# WS-12 — Git integration (full stack)

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-12 · Status: delivered in PR #141.

**Branch:** `opus5/ws-12-git`

## Mission

The Git engine (`_git.py`, `_git_state.py`) and the Git panel that drives it. Backend and
frontend travel together: every finding here is about a user action that either proceeds on
stale state, deadlocks the panel, or discards work the user could still see on screen.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| git-integration | 0 | 2 | 3 | 3 |
| frontend-git-ui | 0 | 1 | 4 | 4 |
| **Total** | **0** | **3** | **7** | **7** |

## Priorities

**P1 — work loss and stale-state operations (review Wave 1/2):**

- `frontend-git-ui-1` (H): archiving or deleting the current branch reloads the page without
  the dirty-canvas guard, destroying unsaved in-memory edits — and the archive prompt
  actively promises the opposite ("your changes ride into the archive"), which is false for
  edits that never reached disk. Route both paths through `guardNavigation` like `switchNow`.
- `git-integration-1` (H): Catch-up and Spin-off-a-copy discard `_fetch_refs`'s boolean, so a
  failed or timed-out fetch silently proceeds against cached refs — `fast_forward_pair`
  advances to a stale tip while reporting success, and `branch_away` renames the user's whole
  working pair aside and repoints the canonical names at a stale remote tip. Raise a
  `GitDomainError` before any ref mutation; keep degradation only in `_push_rejection`.
- `git-integration-2` (H): `git push` runs with no timeout while holding the per-repository
  mutation lock — a wedged remote deadlocks the entire Git panel permanently, because every
  clone-state read takes the same lock. Add a push timeout and convert
  `TimeoutExpired`/`OSError` into a `GitError` before releasing.

**P2 — bugs:** `git-integration-4` (duplicate version label rejected *after* the milestone
ref was already updated, leaving an unlabellable milestone), `git-integration-5`
(`commit_context` silently anchors on the root commit for anything older than the newest 20
milestones), `frontend-git-ui-3` (a remote-listing failure renders as "No remotes configured"
and that early return also hides the pending push-rejection modal),
`frontend-git-ui-4` (nonce effects re-fire on every peek — spurious selection jump plus 3×
refresh fan-out), `frontend-git-ui-11` (an in-flight move can be dismissed via backdrop or
Escape and re-opened), `frontend-git-ui-7` (undocumented 500-character milestone-message cap
silently disables Commit with no explanation).

**P3 — spec truth:** the undocumented milestone sweep of every modified tracked file
(`git-integration-3` — spec it and test it, or scope it), read-path locking claim
(`git-integration-6`), memoised default-branch lookup vs the stated cache invariant
(`git-integration-7`), rollback exception scope (`git-integration-9`), the unresolved
editorial self-correction left in the spec prose (`frontend-git-ui-10`), the wrong
line-number citations (`frontend-git-ui-9`), and the `canMove`/`run()` drift
(`frontend-git-ui-5`, `frontend-git-ui-6`).

## Finding inventory

High (3): `git-integration-1`, `git-integration-2`, `frontend-git-ui-1`.
Medium (7): `git-integration-3`, `git-integration-4`, `git-integration-5`,
`frontend-git-ui-3`, `frontend-git-ui-4`, `frontend-git-ui-5`, `frontend-git-ui-6`.
Low (7): `git-integration-6`, `git-integration-7`, `git-integration-9`,
`frontend-git-ui-7`, `frontend-git-ui-9`, `frontend-git-ui-10`, `frontend-git-ui-11`.

## File ownership (exclusive)

- `src/haute/_git.py`, `src/haute/_git_state.py`, and the Git routes
- `frontend/src/panels/GitPanel.tsx`, `frontend/src/components/BranchManager.tsx`,
  `RemotePushControl.tsx`, `MoveConfirmModal.tsx`, `MilestoneCommitModal.tsx`,
  `PushRejectedModal.tsx`
- `docs/specs/git-integration/**`, `docs/specs/frontend-git-ui/**`
- Their tests (`tests/test_git*.py`, `frontend/src/components/__tests__` Git suites,
  `frontend/src/panels/__tests__/GitPanel*.test.tsx`)

## Cross-stream touchpoints

- `App.tsx` owns the dirty-canvas guard plumbing and is WS-10's file: `frontend-git-ui-1`
  needs `guardNavigation` reachable from BranchManager's archive/delete paths. If the fix is
  contained in `BranchManager.tsx`, no coordination is needed; if it requires an `App.tsx`
  change, request it from WS-10.
- `ModalShell.tsx` is WS-09's — `frontend-git-ui-11`'s busy-gate fix should live in
  `MoveConfirmModal.tsx` (this stream), not in the shell.
- Git roadmap packages GIT-G01–G15 are deleted by WS-01 (`readme-coherence-4`); this stream
  edits only the "remaining work is tracked in…" sentences in its own specs.
- `assistant/_config.py`'s five working-branch states (WS-13's `frontend-assistant-ui-9`)
  read this stream's branch state — keep the state vocabulary stable.

## Definition of done

- No Git operation proceeds on a fetch that failed; push cannot hold the mutation lock
  indefinitely; current-branch archive/delete cannot discard unsaved canvas edits — each with
  a regression test (inject a failing/timing-out fetch; assert the guard fires).
- Milestone labelling is atomic with respect to the ref update; `commit_context` no longer
  silently anchors on the root commit.
- Git specs describe the real locking model, the tracked-file sweep and the message cap;
  citations corrected.
- Baseline entries for both components deleted; findings fixed or deferred with reasons.

## Verification

- `uv run pytest tests/test_git.py tests/test_git_state.py -q` (plus the milestone/branch
  suites named in the specs)
- `npm --prefix frontend test -- src/panels/__tests__/GitPanel src/components/__tests__`
- `npm --prefix frontend run typecheck`; `uv run pytest tests/test_docs_accuracy.py -q`.
