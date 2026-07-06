# G12 — Move remote fetches off the request path

**Severity: MEDIUM · Confidence: CONFIRMED · Class: tail-latency stalls (up to 10 s) on panel reads**
**Files: `src/haute/_git.py`**
**Origin: P-8 (perf reviewer), R-8 (routes reviewer). Design of `_fetch_refs` itself is sound (see CLEARED).**

## The defect

`_fetch_refs` (≤10 s timeout, `_git.py:239-276`) runs **inside request handlers** while holding
the process-global `_fetch_exec_lock`:

- `list_remotes` → `fetch_pair` (`_git.py:1963`) — hot: RemotePushControl refetches `/remotes`
  after every save/commit with the panel open;
- `get_status` deploy peek (`_git.py:493-497`) — currently latent (no caller; see G10);
- deliberate ops: `_push_rejection` (`:2010`), `fast_forward_pair` (`:2242`), `branch_away`
  (`:2363`) — these are *authoritative* fetches and should stay synchronous.

Consequences: the first `/remotes` call in each 30 s cooldown window can block up to 10 s on a
slow/auth-walled remote, and **any concurrent request that also needs to fetch queues behind the
global lock** (across worktrees — the lock is deliberately process-wide for the shared object
store). The panel feels frozen; nothing is wrong.

## Fix design

Split fetch into two modes:

1. **Passive freshen (background).** `fetch_pair` / the deploy peek become: claim the cooldown
   slot (`_should_fetch` unchanged), then hand the actual `_fetch_refs` to a single daemon
   worker thread (queue of `(cwd, remote, refs)`, dedup identical pending entries) and return
   immediately. The handler reads the locally-known tracking refs as of *now* — exactly what the
   current code does when throttled, so no honesty change: `_leg_state` already distinguishes
   `untracked`/`unknown` and the UI already renders "?" for can't-tell (`RemotePushControl`
   ahead/behind honesty, cleared by the UX review). The worker holds `_fetch_exec_lock` while
   fetching, unchanged.
2. **Authoritative fetch (synchronous, unchanged).** Push-rejection, fast-forward, branch-away
   keep their in-handler fetch — they are one-shot deliberate actions where a 10 s worst case
   buys correctness. Optionally drop their timeout to the same 10 s (already is) and keep the
   forced (non-throttled) semantics.

Optional polish: surface freshness honestly — `GitRemote` gains `refreshed: bool` (did a fetch
complete for this window) so the UI can render "as of a moment ago" vs "just checked". Keep it
out of scope if it drags UI work; the core fix is the thread.

Threading notes: reuse one `threading.Thread(daemon=True)` + `queue.Queue`; no new locks beyond
the existing two; worker failures follow `_fetch_refs`'s existing degrade-silently contract
(logged, refs stay stale). No asyncio — the engine is sync by design.

## TDD plan

1. `test_list_remotes_returns_promptly_while_fetch_is_slow` — monkeypatch `_fetch_refs` to sleep
   2 s on the worker; assert `list_remotes` returns in well under that (structural: returns before
   a `threading.Event` set by the fake fetch), and the cooldown slot was claimed exactly once.
2. `test_background_fetch_updates_tracking_refs_eventually` — real local "remote" (file:// bare
   repo, advanced); passive path returns stale counts immediately; after the worker drains
   (join/event), a second call reports the new counts.
3. `test_authoritative_ops_still_fetch_synchronously` — monkeypatch recorder: fast-forward /
   branch-away / push-rejection call `_fetch_refs` on the request thread (no queue involvement).
4. Existing fetch tests stay green: throttle keying per `(cwd, remote, kind)`, timeout degrade,
   prompt-proofing env.

## Notes

Bounded, mechanical concurrency work — but concurrency nonetheless: full dev/reviewer pair.
Land after G01 (its lock audit makes reasoning about the new worker trivial).
