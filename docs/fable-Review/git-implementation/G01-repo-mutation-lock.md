# G01 — Cross-request repo mutation lock

**Severity: HIGH · Confidence: CONFIRMED · Class: silent wrongness (lost saves) + spurious errors**
**Files: `src/haute/_git.py`, `src/haute/routes/git.py`, `src/haute/routes/pipeline.py` (evidence only)**
**Origin: R-1 (routes reviewer), R-10, T-7. Verified independently against `routes/pipeline.py:376-378`.**

## Finding R-1 — no per-repo lock over mutating git operations

The engine has only *fetch* locks (`_git.py:79` `_fetch_time_lock`, `_git.py:84` `_fetch_exec_lock`).
Nothing serialises mutating git operations against each other:

- `POST /api/pipeline/save` is `async def` and holds only the asyncio `save_lock`
  (`routes/pipeline.py:376`) — which serialises saves against submodel create/dissolve, then parks
  the actual work in `run_in_threadpool(svc.save, body)`.
- Every git route is a sync `def` (`routes/git.py:182` etc.), dispatched by FastAPI to the AnyIO
  threadpool with **no** lock. While a save runs in one worker thread, an incoming
  `POST /api/git/move` (or fast-forward, branch-away, create, archive, delete) runs concurrently in
  another, on the same `.git`.

### Failure scenario A — `index.lock` contention (spurious sanitized 400s)

The save's ledger commit performs index/worktree operations: `_git.py:718` checkout (inside
`resolve_ledger`), `:747` `git add`, `:750` `git commit`. A concurrent `/git/move` runs
`_git.py:1204` `git checkout --detach`. Both take `.git/index.lock`; the loser dies with
`fatal: Unable to create '…/index.lock': File exists` → the move surfaces the sanitized
`_INTERNAL_ERROR_DETAIL` 400 (`routes/git.py:117-118`), the save degrades to
`"version capture failed (git error — see server log)"` (`routes/_save_pipeline.py:217-220`).
Non-deterministic, unexplainable to the user.

### Failure scenario B — orphaned save commit reported as success (silent history loss)

If move's `checkout --detach` lands between the save's `resolve_ledger` and its `git commit`
(`_git.py:750`), the save commit is created on **detached HEAD**, not the ledger.
`git rev-parse HEAD` (`_git.py:752`) still returns the new SHA, so `commit_save` returns success
and logs `save_committed` — but `refs/heads/<ledger>` never advanced, and the move has already
`clear_working_branch()`ed. The commit is reachable only via the reflog. The user is told the save
was captured when it was not. (The milestone path is immune — `update-ref <ref> <new> <old>` at
`_git.py:839` is a compare-and-swap — but `commit_save` commits via HEAD with no such guard.)

## Fix design

A per-cwd reentrant mutation lock **in the engine layer**, mirroring the F7 per-cwd fetch keying:

```python
_repo_mutation_locks: dict[str, threading.RLock] = {}
_repo_mutation_locks_guard = threading.Lock()

@contextmanager
def _repo_lock(cwd: Path | None, timeout: float = 30.0) -> Iterator[None]:
    key = str(cwd) if cwd is not None else ""
    with _repo_mutation_locks_guard:
        lock = _repo_mutation_locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise GitDomainError("Another version operation is still finishing — try again in a moment.")
    try:
        yield
    finally:
        lock.release()
```

- Wrap every mutating entrypoint: `commit_save`, `merge_to_working` (via `commit_milestone`),
  `move_to_commit`, `set_working_branch`, `create_working_branch`, `fast_forward_pair`,
  `branch_away`, `archive_working_pair`, `delete_working_pair`, `restore_working_pair`,
  `push_working_pair`, `resolve_ledger`, `set_identity`.
- **Reentrant** because these nest (`commit_save` → `resolve_ledger`;
  `set_working_branch` → `resolve_ledger`).
- It must live in the **engine, not a route dependency** — the save path reaches `commit_save`
  through `SavePipelineService`, so a route-level lock would not close scenario B.
- Reads stay lock-free (status/milestones/saves are all ref reads; git handles concurrent readers).
- Keep the existing `_fetch_exec_lock` as-is (object-store concern, spans worktrees).

While in here, extend watcher coverage for the rare `resolve_ledger` checkout inside a save
(R-10): in normal posture HEAD is already on the ledger so the checkout is a no-op, but when it
does run it is a tree-affecting op outside `pause_watcher`. Cheapest fix: have `commit_save` enter
`pause_watcher()` only when `_get_current_branch(cwd) != ledger`.

## TDD plan (failing tests first)

1. `test_concurrent_save_and_move_cannot_orphan_the_save` — monkeypatch a `threading.Barrier`
   into `_run_git` so thread A (`commit_save`) parks after `resolve_ledger`'s checkout and before
   `git commit`, while thread B runs `move_to_commit`. With the lock: B blocks until A finishes;
   assert the returned save SHA **equals** `git rev-parse refs/heads/<ledger>` and no
   `index.lock` error occurred.
2. `test_concurrent_mutations_serialise_without_index_lock_errors` — two threads racing
   `commit_save` vs `fast_forward_pair` (or `create_working_branch`); assert neither raises the
   sanitized `GitError` and end state is coherent.
3. `test_mutation_lock_times_out_with_domain_error` — hold the lock in one thread; assert the
   second gets the hand-written `GitDomainError`, not a hang.
4. Structural: `test_all_mutating_entrypoints_take_the_repo_lock` — monkeypatch `_repo_lock` with
   a recorder; call each mutating entrypoint on a scratch repo; assert each acquired it.

## Notes for the implementer

- This is the **first package to land** — several later packages (G03, G14) assume serialised
  mutations when reasoning about end states.
- Windows CI note: use generous barrier timeouts; never assert on wall-clock.
