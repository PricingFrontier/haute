# CLEARED — adversarially checked and found CORRECT. Do not "fix" anything on this list.

Five reviewers + the primary read probed each of these; several were reproduced in scratch repos
before being cleared. If an implementer believes one is wrong, bring evidence — don't patch it
speculatively.

## Engine model & plumbing

- **The working/ledger pair model itself.** Saves on the ledger, plumbing milestone merges
  (`commit-tree` + CAS `update-ref`), HEAD-on-ledger posture, first-parent milestone spine —
  internally consistent, correctly invariant-checked at `merge_to_working` (`_git.py:824`), and
  the healthy-state invariant (`check_invariants`, `:757-789`) is the right, cheap formulation.
- **CAS `update-ref` usage** — both the milestone advance (`_git.py:839`) and the fast-forward
  working leg (`:2269`) pass the expected old value; concurrent movement is refused, not clobbered.
- **`\x1e` record framing is injection-safe.** The milestone gate rejects all C0 except `\t\n\r`
  (`_git.py:821` — `\x1e` IS rejected); ledger save messages are always auto-generated
  (`_save_pipeline.py:213` passes no message); tag labels pass `_validate_ref_name` whose
  `_BAD_REF_CHARS` covers `\x00-\x1f`. The tab-ordering issue is a separate, real finding (G04) —
  the separator choice itself is sound.
- **`_replay_onto` metadata fidelity** — 6-field `%x1f` split is exact (identity/date fields
  cannot contain `\x1f`), `%B` fetched separately so multi-line messages survive; author +
  committer + dates preserved. (The *invariant gap* around external merges is G04/E-12; the
  replay mechanics are correct for haute-authored linear ledgers.)
- **Windows case-insensitivity does not corrupt refs.** `git branch feature` fails cleanly when
  `Feature` exists; `_rev_parse` resolves case-variants so create/adopt paths see "already
  exists" pre-flight. Verified with git 2.52 on NTFS.
- **Odd-name edges are blocked or benign.** A branch literally named `-save` is rejected
  (leading dash, `_git.py:358`); `x-save` categorises as a ledger with a clear hand-written
  refusal; `working_name("-save")` returns None via the length guard (`:642`).
- **`branch_away` non-X2 rollback** tracks all four flags correctly; the X2 `created_l` gap is
  the finding (G03/E-9), not the mechanism.
- **`_switch_away_if_active` non-discard dirty refusal** (`_git.py:2509-2514`) correctly blocks a
  lossy archive with actionable guidance.
- **`commit_save` pathspec scoping** (`_git.py:738-750`) — commits exactly the given paths,
  bypasses pre-staged index content, returns None on no-op (idempotent saves).
- **Unborn seeding control flow** — rename-to-main handling, born-main collision domain error,
  all-or-nothing rollback incl. orphan-ledger cleanup (`_git.py:1016-1079`) is careful and
  well-tested; the *pathspec breadth* of the seed add is the finding (G02), not the flow.
- **`milestone_saves` range-injection** — resolves via `_rev_parse …^{commit}` before building
  the range (`_git.py:1783`), so range-shaped input can't smuggle a rev expression (unlike the
  LOW `?branch=` peek, G16 item 8).
- **Identity-missing detection survives localisation** — `"user.email"`/`"user.name"` are literal
  config keys inside git's hint and are never translated (verified against a forced non-C
  locale); only the push classifier needs the locale pin (G09).
- **The push surface** — `--atomic --follow-tags` pairing (`_git.py:2165`), peeled-commit tag
  collision comparison (`_ls_remote_version_tags` prefers `^{}` lines, `:2088-2095`; idempotent
  re-push of a published label is NOT a collision), data-bearing `_push_rejection` with
  blocking-leg naming and X3 rewrite detection — internally consistent and well-tested.
- **Fetch hardening** — `GIT_TERMINAL_PROMPT=0` + SSH BatchMode + 10 s timeout + degrade-to-known
  refs is the right design; per-`(cwd, remote, kind)` throttle keying prevents cross-worktree
  starvation. G12 moves *where* the passive fetch runs; the mechanism stays.

## HTTP / security surface

- **Auth mounting** — `LocalSessionMiddleware` (`server.py:413`) gates all `/api/*` (incl. the
  git router) with the per-process token + local-Origin check; WS `/ws/sync` has its own
  token/origin gate; `hmac.compare_digest`; middleware order TrustedHost→Session→RequestId is
  correct.
- **No user-supplied remote ever reaches fetch/push** — push/ff/branch-away all guard
  `remote not in _remote_names(...)`; passive fetches use only `_canonical_remote`/configured
  names. No SSRF-ish egress.
- **`tar` extraction** uses `filter="data"` — traversal-safe (the 3.11.x availability floor is
  G13's one-liner; keep the filter).
- **Secret hygiene** — `_redact_remote_url` strips userinfo before URLs cross the API; raw remote
  URLs are never logged; `_run_git` logs argv (names + refspecs), not URLs; error sanitisation
  default (`_INTERNAL_ERROR_DETAIL`) is the right posture (G09 adds a classified allowlist on
  top, it does not weaken this).
- **409 payload shapes** — `GitMilestoneFork` / `GitPushRejection` are JSON-safe, emitted via
  `detail=model_dump()`, parsed into `ApiError.body`, and pinned by
  `guards.contract.test.ts` — consistent end to end.
- **`pause_watcher` coverage** — wrapped for every worktree-mutating route; correctly omitted for
  `commit` (plumbing-only), `push`, `identity`, `prefs`, and `restore` (renames only). The 60 s
  watchdog edge is G16 item 7; the coverage map is right.
- **No `chdir`/`--dir` drift** — the server consistently treats `Path.cwd()` as project root.

## Performance posture (things that are already right)

- **No polling anywhere** — all git refreshes are event-driven (nonces); `BranchIndicator` is a
  pure store consumer. The cost problems are per-event fan-out (G05/G06), not cadence.
- **`list_branches`** already batches via one `for-each-ref … %(ahead-behind:…)` with an old-git
  fallback — it is the pattern G06 copies.
- **`milestone_saves` / `pending_ledger_saves`** — single `git log -M --name-status` each;
  appropriately batched; rename-aware; `core.quotepath=false` handled.
- **Deliberate one-off ops** (move/commit/ff/branch-away/push/archive/delete/restore) at ~5-18
  spawns are fine for user-initiated actions — left alone deliberately.
- **GitPanel preserved expansion is NOT a staleness bug** — a plain save lands in *pending*
  (refetched every refresh); only a commit folds pending→milestone and that path refetches the
  list. Skipping per-milestone save refetches avoids N needless calls at zero correctness cost.

## Frontend architecture

- **The nonce selection system** (`GitPanel.tsx:127-197`) — live `peekingRef` dodges stale
  closures; per-effect processed-nonce refs make each bump fire exactly once; save-refreshes
  don't move selection, commits do. Sound.
- **`toggleExpand` landing guard** (`:215,219-224`) — only lands/removes while still `"loading"`;
  correct against collapse/refresh races.
- **RemotePushControl monotonic `reqId`** — a slow remotes fetch can't clobber a newer one.
- **Structured 409 rendering** — `PushRejectedModal` names the blocking leg and offers exactly
  the safe resolutions (catch up ff / spin off a copy), never a merge, never a dead-end;
  `MilestoneCommitModal`'s fork-confirm mirrors it. This is the panel at its best.
- **Ahead/behind honesty** — "?" (couldn't read) vs "—" (never pushed) vs counts; the ledger leg
  surfaces only when it matters. F2 honesty done right.
- **`MoveConfirmModal`** — reads in-memory `dirty`, forces Save & move / Discard & move, awaits
  the save before checkout. It is the donor pattern for G08.
- **`ComparisonView`** — freezes the live graph on entry, aborts per-sha fetches, explicit
  read-only affordances.
- **App save-gate race** (`App.tsx:317`) — awaits the in-flight status load before deciding, so
  a startup save can't slip past the gate. Deliberate, correct.
- **Modal focus-trapping + keyboard access** on the row-embedded `role="button"` spans
  (tabIndex + Enter/Space + stopPropagation) — consistently applied.

## Test architecture

- **Real git everywhere it matters** — engine tests drive real repos (`tests/_git_helpers.py`);
  fault injection is surgical (`_fail_run_git_on` fails exactly one matching call, everything
  else hits real git); route tests run TestClient against the real engine; frontend mocks only
  the network boundary with the real stores. 296 backend git tests green in this session.
- **Exemplary suites worth imitating**: the baton/ledger multi-generation walk, push
  atomicity/rewrite-detection, the unborn-seeding matrix, create/move publish guards,
  state-reader hardening, the AST test pinning `encoding="utf-8"` on every subprocess call, the
  parametrised watcher-pause route test, and `RemotePushControl.gaps` (full catch-up matrix with
  negatives).

## Deliberate design decisions — do not "fix" as if they were bugs

- **The fork gate reads local refs only (no fetch)** — U4 rules a milestone must never block on
  the network; freshness comes from the passive `/remotes` fetch. Degrade-open is the spec.
- **Saves proceed ungated when git status is unreadable** — the editor must never hold user work
  hostage to VC availability. G11 adds *visibility*, not gating.
- **`move_to_commit` clears the working association** — the S13 re-prompt on next save is the
  designed flow, not amnesia (G16 item 6 only improves the copy).
- **Volatile-artefact wiping on tree swaps** (S12) — outputs/caches are contractually
  reconstructable; G02 aligns the ignore list with it rather than narrowing the wipe.
