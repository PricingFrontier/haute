# G16 — Polish batch: small confirmed items, test-only gaps, CI cost

**Severity: LOW (each) · Confidence: CONFIRMED unless marked · Class: hygiene / micro-UX / test debt**
**Origin: U-5, U-9, U-10, R-6, R-7, R-8/R-9, P-4, E-7 note, UX polish list, T-4/T-5/T-6/T-7, tests reviewer's CI note.**

Batch these in a handful of commits with one batch reviewer (calibrated split: mechanical class).

## Frontend micro-UX

1. **U-5 — remotes error renders as "No remotes configured".** `RemotePushControl.tsx:79-84`
   `catch { setRemotes([]) }` + `:177-188` empty-state. Track `loadError` separately; on error
   render "Couldn't read remotes — retry" (button) instead of the misleading empty-state. Test:
   mock reject → assert retry copy, not "No remotes configured".
2. **U-9 — fork menu/dialog overflow at viewport edges.** `GitPanel.tsx:235` raw
   `clientX/clientY`; menu `:517-521`, 260-px dialog `:548-551`, `position:fixed`, no clamp.
   Clamp: `left = Math.max(8, Math.min(clientX, innerWidth - W - 8))`, same for top. Test:
   contextmenu at `innerWidth-10` → rendered left ≤ innerWidth − width.
3. **U-10 — "Don't ask again" silently fails to persist.** `BranchManager.tsx:137-141` flips
   local state then `.catch(() => {})`. Toast on failure (via G07's `apiErrorText`) or only flip
   local state on success. Test: reject → toast fires.
4. **Truncation tooltips.** `GitPanel.tsx:452-454` (milestone message) and SaveRow `:622-624`:
   add `title={message}`.
5. **Redundant status refetch per peek.** `GitPanel.tsx:104-107` re-runs `loadStatus()` whenever
   `refresh` identity changes (every `viewBranch` change). Split the effect: `loadStatus` on
   mount only; `refresh` keeps its own effect.
6. **Post-move indicator moment.** After a successful move the association is (by design) unset →
   indicator flips to red "Set branch" while `App.tsx:395` toasts the explanation. With G11's
   per-state labels, give the move flow its transient copy: *"Viewing a version — save to start a
   new branch from here."* (One-shot flag already exists for the post-move startup, reuse it.)

## Backend hygiene

7. **R-6 — watcher watchdog can force-resume mid-checkout.** `_helpers.py:335`
   `_WATCHER_PAUSE_MAX_SECONDS=60`; `watcher_is_paused` returns False past the deadline even at
   depth>0 (`:377-388`) → a >60 s checkout (huge repo, AV) gets its half-written tree broadcast
   (flicker/reload storm). Fix: on watchdog trip, log-and-drop the next flush instead of
   broadcasting (or raise the cap for tree-swap ops via `pause_watcher(max_seconds=…)`, already
   parameterised). Test: simulate deadline expiry mid-pause → assert the flush after force-resume
   is suppressed once and a warning logged.
8. **R-7 — `?branch=` accepts revision syntax.** `_BAD_REF_CHARS` (`_git.py:88`) misses `@ { } ..`
   → `working_milestones` will `git log --first-parent @{-1}` (read-only info leak of another
   branch's history; single-user → LOW). Reject `@{` and `..` in `_validate_ref_name` (they are
   invalid in branch names per `git check-ref-format` anyway). `milestone_saves` /
   `pending_ledger_saves` already resolve via `_rev_parse` first (cleared).
9. **R-8/R-9 — GET semantics.** `/remotes` (and `/status` if kept) trigger egress + cooldown
   writes on GET — acceptable + throttled + auth-gated, but G12 moves the egress off-path anyway;
   note in the route docstrings. Unknown sha on `/show`, `/commit-context`, `/milestones/{sha}/saves`
   → 400; 404 reads better — change `_handle_git_error` call sites? No: map "Unknown commit"/
   "No commit found" domain errors to 404 in the three read routes only (leave mutation routes
   at 400). Cosmetic; skip if contentious.
10. **P-4 — trim the per-save spawn count (~8 → ~5).** In `commit_save`/`resolve_ledger`
    (`_git.py:699-752`): skip the `_get_current_branch` spawn when the recorded posture already
    says HEAD-on-ledger (verify cheaply via the same `symbolic-ref` only when needed), and drop
    the redundant repo assert (the save transaction asserted already). Keep behaviour identical —
    counting-wrapper test pins the new budget.
11. **E-7 note.** Document the fast-forward partial-advance window in the docstring (see G03).

## Test-only gaps (no product change)

12. **T-4 — branch_away rollback legs.** Parametrise `_fail_run_git_on` over: ledger-create
    failure (`branch <ledger> <remote_l>`), post-create checkout failure, X2 respawn failure
    (this leg lands with G03/E-9's fix). Assert full restoration: tips, HEAD, no `*-local-*`
    strays, association intact.
13. **T-5 — fast-forward CAS.** Advance `refs/heads/<W>` between leg-read and update-ref →
    assert refusal, no clobber.
14. **T-6 — lightweight remote `version/*` tag collision.** Create a lightweight tag in the bare
    remote at a different commit → `push_working_pair` refuses via `_tag_collisions`' non-peeled
    branch (`_git.py:2092-2095`).
15. **T-7 — `_should_fetch` contention.** N barrier-synced threads, one winner per window.

## CI cost (tests reviewer)

16. Backend git suites: 267 tests / **10m09s** (real git, ~2 s/test on Windows — right fidelity,
    heavy wall-clock). Adopt `pytest-xdist` (`-n auto`) for these files — they are `tmp_path`-
    isolated and should parallelise cleanly. Acceptance: same pass set, wall-clock cut ≥3×; no
    test may share a repo dir. (Also consider `-p no:cacheprovider` noise on CI — optional.)
17. Note, not an action: `test_git_routes_pydantic.py`'s `inspect.getsource` string assertions
    are refactor-guards coupled to source text — fine to keep, but when G05/G06 touch those
    functions, update the guards deliberately rather than weakening them.

## Acceptance

Each item lands with its test (or is a test); `ruff` + `mypy` + the focused suites green; no
wall-clock assertions anywhere.
