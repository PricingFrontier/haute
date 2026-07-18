# G03 — Pair-lifecycle edges: switch-away corruption, memoized fallback default, X2 rollback, ff partial window

**Severity: HIGH (E-8) / MEDIUM (E-10, E-9) / LOW (E-7) · Class: half-deleted state + sticky misbehaviour**
**Files: `src/haute/_git.py`**
**Origin: engine reviewer (E-8 reproduced, E-9 mechanics reproduced, E-10, E-7). E-8/E-10 hypothesised independently during the primary read.**

## E-8 [HIGH, CONFIRMED — reproduced] Deleting/archiving the active pair corrupts state when the default branch resolves to the pair itself

`_get_default_branch_cached`'s last fallback returns the **current branch**
(`_git.py:316-320`), and `_switch_away_if_active` checks that "default" out before
deleting/renaming the pair (`_git.py:2515`, `:2518`).

Reproduced: repo whose only long-lived branch is not `main`/`master` and has no remote-HEAD symref
(the "adopt an existing repo" shape, e.g. default `trunk`). HEAD on `feature-x-save` (normal
posture). Delete `feature-x`:

- `checkout -f feature-x-save` — no-op (the "default" *is* the ledger),
- `branch -D feature-x` — **succeeds**,
- `branch -D feature-x-save` — **fails** (`cannot delete branch … used by worktree`) → raw
  `GitError` → sanitized 400.

End state: working ref gone, ledger orphaned and checked out, association already cleared. The
archive sub-case leaves HEAD on `archive/feature-x-save` (renames of a checked-out branch are
allowed) — the user is standing on an archived ledger.

**Fix.** In `_switch_away_if_active`, compute `target = _get_default_branch(cwd)`; when
`target in (working, ledger)` or `target == "HEAD"`, there is no distinct deploy branch to land on
— **detach at the current commit** instead (`checkout --detach` / `checkout --detach -f` for the
discard path) so both refs are free to rename/delete. Keep the branch checkout for the normal case.

**Tests.** `git init -b trunk` + commit, no remote; `set_working_branch("feat", create=True)`; one
ledger save; (a) `delete_working_pair("feat", confirm=True)` succeeds, neither ref remains, HEAD
valid (detached is fine); (b) `archive_working_pair("feat")` succeeds and HEAD is **not** left on
an `archive/*` ref.

## E-10 [MEDIUM, CONFIRMED] `lru_cache` memoizes the mutable current-branch fallback for the process lifetime

`@lru_cache(maxsize=32)` (`_git.py:290`) caches whatever the first call resolved — including the
fallback `_get_current_branch(cwd)` (`:320`), which can be a **ledger**, a working branch, or the
literal `"HEAD"` right after a move. That frozen value then feeds eligible-branch filtering
(`_git.py:911`), the switch-away target (making E-8 **sticky**: once cached as the ledger, every
delete/archive misroutes), and the `main_ahead` baseline. Remotes added/renamed mid-session are
also invisible until restart (cache cleared only in the unborn-rename path, `:1028`).

**Fix.** Only cache authoritative resolutions (remote HEAD, local `main`/`master`); return the
current-branch fallback **uncached**. Simplest shape: move the fallback out of the cached inner
function. Given the cache saves ≤3 spawns and G10 removes most callers' cost anyway, deleting the
cache entirely is also acceptable — pick one, don't keep a wrong cache.

**Tests.** With HEAD detached (fallback fires), call `_get_default_branch`; create `main`; assert
the next call returns `main`. And: after adding a remote with a HEAD symref, assert it wins.

## E-9 [MEDIUM, CONFIRMED mechanics] `branch_away` X2 rollback strands the set-aside ledger

In the X2 branch (remote has `W` but no `W-save`), `resolve_ledger(working)` creates + checks out
the new ledger (`_git.py:2400`) but **`created_l` is never set** (only the `remote_l is not None`
branch sets it, `:2396`). If `write_working_branch` (`:2401`) then raises `OSError`, the rollback
(`_git.py:2315-2323`) skips `branch -D W-save` (created_l False) and its
`branch -m aside_ledger → W-save` **collides** with the freshly-created ledger (exit 128, swallowed
by `_run_git_ok`). End state: `aside_ledger` stranded under the dated name, canonical `W-save`
pointing at the **remote** tip and checked out — the user's pre-branch-away ledger tip survives
only under the stranded name.

**Fix.** Set `created_l = True` immediately after the `resolve_ledger` call in the else branch
(the ledger name is guaranteed free there, so `resolve_ledger` always creates it). Rollback then
deletes it first, freeing the name for the rename-back.

**Tests.** Simulate X2 + monkeypatch `write_working_branch` to raise; assert post-rollback:
`W-save == old_l`, HEAD on `W-save`, no `*-local-*` refs remain. Fold into the T-4 parametrised
rollback-legs test (see G16) covering the other untested legs
(`branch <ledger> <remote_l>` failure; `checkout <ledger>` failure with all four flags set).

## E-7 [LOW, CONFIRMED] `fast_forward_pair` partial-advance window

Ledger advances first (`merge --ff-only`, `_git.py:2261`), then the working ref via CAS
`update-ref` (`:2269`). A CAS failure after the ledger advanced has no rollback. Assessment
(engine reviewer): self-healing — re-running fast-forward completes the working leg, and
`check_invariants` is not tripped (post-ledger-ff the working tip equals the new merge-base, tree
check passes). With G01's mutation lock, the only writer that could move the working ref
concurrently is gone. **Action:** document the window in the docstring + add the T-5 CAS test
(assert a stale `<old>` makes `update-ref` refuse rather than clobber). No rollback machinery.

## Package order

Fix E-8 and E-10 together (E-10 makes E-8 sticky); E-9 is an isolated two-line flag fix; E-7 is
docs+test. Silent-wrongness class → full dev/reviewer pair per project protocol.
