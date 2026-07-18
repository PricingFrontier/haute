# Git Integration — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_git.py` | All git CLI interaction. Subprocess wrappers, guardrails, the working/ledger branch-pair engine, content-addressed caches, remote fetch/push/fast-forward, branch manager operations (archive/delete/undelete/restore), read paths (status, graph, milestones, ledger expansion, commit context). ~3,280 lines, no HTTP or filesystem-state concerns beyond git itself. |
| `src/haute/_git_state.py` | Per-clone, untracked JSON state under `<project_root>/.haute/`: working-branch association (`state.json`), UI preferences (`prefs.json`), fork-point back-links (`forks.json`), last-pushed SHAs (`pushed.json`), delete tombstones (`trash.json`). Pure read/write helpers with fail-soft parsing — no git subprocess calls. |
| `src/haute/routes/git.py` | FastAPI router at `/api/git`. One `def` (sync) handler per endpoint, each a thin `try/except` around a single `_git` call; converts `_git`'s typed exceptions to HTTP responses via `_handle_git_error`. |

## Key types and data structures

All public types are Pydantic models defined in `src/haute/schemas.py` (not owned by this
component but returned directly by `_git` functions — no dataclass-to-dict-to-model
conversion in the routes).

**Exceptions** (`_git.py`), forming a strict hierarchy:
- `GitError(HauteError)` — base; wraps raw subprocess stderr.
  - `GitDomainError(GitError)` — hand-written, safe-to-surface message.
    - `GitGuardrailError(GitDomainError)` — guardrail block → HTTP 403.
    - `GitPushRejectedError(GitDomainError)` — carries a `GitPushRejection`; constructed
      only by `_push_rejection()`.
    - `GitMilestoneForkError(GitDomainError)` — carries a `GitMilestoneFork`; constructed
      only by `commit_milestone()`.

**Branch-pair naming** (`_git.py`): `LEDGER_SUFFIX = "-save"`. `ledger_name(working)` /
`working_name(ledger)` are pure string transforms and inverses of each other.
`branch_category(branch)` classifies any branch name into `"protected" | "ledger" |
"working"` by checking membership in `_protected_branches()` first, then the ledger-suffix
pattern; anything else is a working-branch candidate. `is_eligible_working_branch` and
`_assert_eligible_working` build on this classification.

**Content-addressed cache keys**: every `@lru_cache`-decorated inner function
(`_merge_base_cached`, `_is_ancestor_cached`, `_first_parent_spine_cached`,
`_commit_parents_cached`, `_graph_log_cached`, `_tree_of_cached`, `_get_default_branch_cached`)
takes `cwd_key: str` as its final argument — `str(cwd) if cwd else ""` — because a bare
`Path` is unhashable and because two repos/worktrees sharing one process must never share
cache entries. `_FULL_SHA_RE` / `_is_full_sha()` gates which callers may take the cached
path: only a full 40-hex SHA is guaranteed immutable; a ref name (branch, `HEAD`, a tag)
falls through to an uncached live subprocess on every call.

**Per-clone state files** (`_git_state.py`), all siblings under `<project_root>/.haute/`:
`state.json` (`{"workingBranch": str}`), `prefs.json` (flat dict, currently one key:
`skipSwitchConfirm`), `forks.json` (`{branch_name: fork_point_sha}`), `pushed.json`
(`{"<remote>/<ref>": sha}`), `trash.json` (`{deleted_branch_name: {branch_tip, ledger_tip,
forked_from, was_archived, deleted_at}}`, capped at `_TRASH_MAX_ENTRIES = 20`, insertion
order = recency).

**`GitWorkingBranchResponse.state`** (computed by `working_branch_status`) is one of four
literal values: `"unset"` (no working branch recorded), `"invalid"` (recorded branch
missing / ineligible / invariants violated), `"divergent"` (HEAD is on neither the recorded
branch nor its ledger — moved outside haute), `"ready"`.

## Control flow

**Save.** `commit_save(paths, working, cwd, message)` → `resolve_ledger(working)` (find-or-
lazily-spawn the ledger at the working branch's current tip, checkout if not already
current) → `git status --porcelain -- <paths>` to check anything in *paths* actually
changed (idempotent no-op returns `None`) → `git add -- <paths>` → `git commit -m <msg> --
<paths>` (pathspec-scoped, so it commits only those paths' working-tree state regardless of
what else the user may have pre-staged) → returns the new SHA.

**Milestone.** `commit_milestone(message, project_root, version_label, cwd, allow_fork)` →
reads the recorded working branch from `_git_state.read_working_branch` → unless
`allow_fork`, calls `divergence_state(working)` (local-refs-only, no fetch) and raises
`GitMilestoneForkError` if the leg is `"behind"` or `"diverged"` → `merge_to_working`:
validates the message (non-empty, no C0 control characters — a stray record-separator would
corrupt the ledger-save parser's delimiter), runs `check_invariants` (see below) and raises
`GitDomainError` on any violation, computes `merge_base(working_tip, ledger_tip)` and
raises if it already equals the ledger tip ("no new saves"), then builds the merge purely
via `commit-tree <ledger's tree> -p <working_tip> -p <ledger_tip> -m <message>` followed by
a CAS `update-ref` — no checkout, no index, so the operation cannot conflict. An optional
version-label tag is created afterward as `refs/tags/version/<label>`, rejecting a
duplicate label.

**`check_invariants(working, cwd)`** — the healthy-state check run at every milestone and
exposed via `working_branch_status`. Pre-spawn (ledger doesn't exist yet) returns no
violations. Otherwise: `merge_base(working_tip, ledger_tip)` must exist (else "share no
history"); that base must be an ancestor of the ledger tip (else "merge-base is not on the
ledger"); and the working tip's TREE must equal the merge-base's tree (else "the working
branch was advanced outside haute"). This is deliberately NOT naive ancestry — it is false
from the first milestone onward by design, because the working tip's tree after a milestone
equals the last-merged ledger commit's tree, not an ancestor relationship.

**Fork (`create_working_branch`).** Two structurally different paths share one function:
- No recorded working branch yet → delegates to `set_working_branch(..., create=True)`
  (adopt-create off real HEAD; no fork bookkeeping).
- A working branch exists → resolves the fork point (`at=None` → current working tip;
  `at=<sha>` → that commit), classifies it as either on the current branch's first-parent
  (milestone) spine or a pending ledger save ahead of the tip (rejecting anything else), and
  for a pending save calls `_crystallize_milestone` to synthesize an anchoring merge commit
  (parents = `[working_tip, save]`) so the new branch opens at a clean milestone. Then:
  - `move=False` (default): two fresh branches (`name`, `ledger_name(name)`) at the base;
    current HEAD and branch untouched. If the ledger branch creation fails after the
    working branch succeeded, the working branch is deleted so no lone ref leaks.
  - `move=True`: only legal at the latest milestone or a pending save (not an older
    milestone). Refuses if the spawning ledger's later commits are already published on
    ANY remote and the rewind would orphan them (checked per-remote via `_is_ancestor`),
    steering the user to a parallel fork instead. Otherwise: computes the new ledger tip
    (reuse the pending chain, or `_replay_onto` the crystallized base via `commit-tree`
    per-commit, preserving author/committer identity and dates through `GIT_AUTHOR_*` /
    `GIT_COMMITTER_*` env vars), creates both new branches, checks out the new ledger
    (its tree matches old HEAD's, so uncommitted edits carry across untouched), rewinds the
    spawning branch's ledger to the fork point, and records the new working branch. Any
    exception in this multi-step sequence triggers `_rollback_fork` (best-effort, never
    raises) before re-raising.

**Move (`move_to_commit`).** Refuses if a git op is in progress (`_assert_no_git_op_in_progress`
checks `MERGE_HEAD`/`CHERRY_PICK_HEAD`/`REVERT_HEAD`/`rebase-merge`/`rebase-apply` under
the git dir) or if tracked files are dirty. Wipes `_VOLATILE_ARTEFACTS` (best-effort),
`git checkout --detach <sha>`, then `_git_state.clear_working_branch` — HEAD moves but no
ref is created or updated, so the prior branch stays fully reachable.

**Unborn-repo seeding (inside `set_working_branch`, `create=True`).** Only triggers when
`HEAD` has no commits. If the current unborn branch is not itself protected, it is renamed
to `main` first (so the seed root commit lands on the default branch, never on the branch
about to be created) — refusing with a domain error if a born `main` already exists
alongside an unborn HEAD (only reachable via `checkout --orphan` outside haute).
`ensure_gitignore_guards` is asserted against the repo toplevel (defence-in-depth for a
foreign `git init` repo). The index is cleared (`git rm -r -f -q --cached --ignore-unmatch
-- .`) so pre-staged content can't bypass the seed gate, then `git ls-files --others
--exclude-standard -- <_SEED_PATHSPECS>` lists what's both permitted (the pathspec allow-
list) and not `.gitignore`d (the deny gate); matched files are added with `:(literal)`
prefixes so a filename containing glob characters can't re-expand the pathspec. An empty
result still commits (`--allow-empty`) to establish the root. A `git commit` failure whose
stderr mentions missing author identity is translated to a `GitDomainError` steering the
user to set identity first; anything else re-raises as a plain `GitError`. The whole
create path (branch checkout, ledger spawn, state write) is wrapped so any exception
restores the pre-creation HEAD (symbolic ref if it existed, else detached-SHA) and deletes
the half-created branch and ledger.

**Graph topology (`graph_topology`).** Reads every working pair's tip via one
`for-each-ref` call (`_list_branches_with_tips`), then for each branch reads its full
first-parent spine (`_first_parent_spine`, SHA-keyed cached). Branches are processed in a
deterministic order — the CURRENT working branch first (so a crystallized fork can never
have its spawning spine's tail mis-claimed as the current branch's own), then deepest-spine-
first, then name — greedily claiming spine commits into a shared `claimed: dict[sha,
branch_name]` map; the first already-claimed commit encountered walking a spine (newest to
oldest) becomes that branch's `fork_point_sha`/`fork_of`. `_fork_source_and_credit` then
distinguishes a genuine crystallized-fork spawn (whose oldest own commit is a synthetic
merge back into the parent's history) from an ordinary milestone-level fork, and — for a
genuine spawn — binary-searches the parent's spine (containment along a first-parent spine
is monotone) for the oldest parent milestone whose fold already contains the spawn source,
i.e. the row that should visually "take credit" for it.

**Push (`push_working_pair`).** Pre-checks `version/*` tag collisions against the remote
(`_tag_collisions` via `git ls-remote`, best-effort — a genuine collision is still caught
by git's own tag-push rejection if this pre-check can't reach the remote) before attempting
anything. Pushes with `git push --atomic --follow-tags <remote> <working>:<working>
[<ledger>:<ledger>]` — atomic so a partially-fast-forwardable pair never lands half-pushed,
never `--force`. A non-fast-forward failure (`non-fast-forward` / `fetch first` /
`[rejected]` in stderr) is turned into `_push_rejection`, which force-fetches past the
throttle (a rejection is authoritative, not a poll), recomputes both legs, and checks
`_is_rewrite` per leg using the last-pushed-SHA record to distinguish "the remote moved
past this clone" from "the remote history was actually rewritten" (the latter survives a
pruned reflog because it's read from the clone's own untracked record, not git's). On
success, the just-pushed tips are recorded via `_git_state.record_pushed_shas`.

**Fast-forward (`fast_forward_pair`).** Requires HEAD to currently be on the ledger (refuses
mid-move/detached states) and a clean tracked tree. Force-fetches, then requires EVERY leg
to be `"behind"` or already synced — any `"ahead"`/`"diverged"` leg refuses outright (the
user must reconcile via `branch_away` instead; this function never merges). The ledger
(checked out) advances via `git merge --ff-only`; the working ref (not checked out)
advances via a CAS `update-ref`.

**Branch away (`branch_away`).** The reconciliation path when a remote fork is detected.
Renames the current pair to a unique dated aside name (`_unique_aside_name`), creates fresh
`working`/`ledger` branches at the remote's tips, checks out the new ledger, and records the
merge-base of the old and new working tips as the aside branch's fork point (for the branch
manager's back-link). If the remote has no ledger (`X2`), the local ledger is respawned at
the adopted tip rather than adopted. The whole sequence is wrapped with
`_rollback_branch_away` (best-effort; drops any freshly created canonical refs, renames the
aside pair back, restores HEAD) on any `GitError`/`OSError`.

**Delete / trash (`delete_working_pair` / `undelete_working_pair`).** Delete refuses on
unmerged ledger saves unless `confirm=True`. Before removing the branch refs, both tips are
pinned under `refs/haute/trash/<name>` (a plain, non-`refs/heads` ref that shields the
objects from gc) and a tombstone is written to `trash.json` — deliberately in that order,
so a failure between the two leaves at worst a harmless extra pin, never an unrecoverable
delete. `undelete_working_pair` is the exact inverse: recreates both refs at their recorded
tips (verifying each still resolves — a tombstone can outlive its objects if the trash refs
were hand-deleted and gc ran), restores the forks.json back-link, and consumes the trash
refs + tombstone. The restored pair is NOT auto-adopted as the working branch.

## Edge cases and invariants

- **Unborn HEAD vs. unborn non-default branch collision** — handled explicitly in
  `set_working_branch`'s seed path (see Control flow); any other unborn/protected
  combination raises rather than guessing.
- **Ledger absent (pre-spawn)** — `check_invariants` short-circuits to "healthy" (nothing
  to check yet); `_has_unmerged_saves` and `_ledger_or_branch_sha` both treat a missing
  ledger as "fall back to the branch tip" rather than erroring.
- **No canonical remote** — `_canonical_remote` returns `origin` if present, the sole
  remote if exactly one exists, else `None` for genuine ambiguity (several non-origin
  remotes) or full offline. `None` propagates as "can't tell" through `divergence_state`
  (the fork-gate then degrades OPEN, never blocking a local-only user) and through
  `get_status`'s `main_ahead` computation (skipped entirely, never falsely reporting
  in-sync).
- **Concurrent worktrees sharing one process** — the fetch cooldown (`_fetch_cooldowns`) is
  keyed per `(cwd, remote, kind)`, not global, specifically so one worktree's poll cannot
  starve another's; the actual `git fetch` subprocess is still serialized process-wide via
  `_fetch_exec_lock` because worktrees share one object store and git itself races on
  concurrent fetches into it.
- **A slow / credential-walled remote** — every fetch (`_fetch_refs`, `_ls_remote_version_tags`)
  disables terminal/SSH prompts (`GIT_TERMINAL_PROMPT=0`, SSH `BatchMode=yes`) and is
  wrapped in `subprocess.run(..., timeout=_FETCH_TIMEOUT_SECONDS)`; timeout or any `OSError`
  degrades to `False` / `{}` rather than propagating, so a request thread can never hang on
  network I/O.
- **Rename detection in ledger-save history** — `_parse_ledger_saves` runs `git log -M
  --name-status` so a renamed config file shows as one rename entry, not a delete+add pair.
- **Unicode / space-containing paths in log output** — `core.quotepath=false` is passed
  explicitly so git doesn't octal-escape and quote non-ASCII filenames in the parsed
  history output.
- **A commit message containing the internal record separator** — `merge_to_working`
  rejects any C0 control character except tab/newline/CR, because `_parse_ledger_saves`
  delimits per-commit blocks with `\x1e` and a message containing it would corrupt parsing.
- **Full-SHA vs. ref-name cache eligibility** — every cache-fronted helper
  (`_merge_base`, `_is_ancestor`, `_tree_of`, `_first_parent_spine`, `_commit_parents`)
  branches on `_is_full_sha()` first; only a resolved 40-hex SHA takes the memoized path,
  a ref name always takes a live, uncached subprocess.
- **Filename containing glob characters at seed time** — `_SEED_PATHSPECS`-matched files
  are staged with a `:(literal)` prefix specifically so a filename that happens to look
  like a glob can't re-expand into a broader pathspec than the literal file at `add` time.
- **A version-label tag reused for a different release** — `_tag_collisions` compares the
  underlying COMMIT each tag (local vs. remote peeled) resolves to, not the tag object
  SHA, so an idempotent re-push of an already-published label is not a false collision.

## Error handling

`_run_git` is the single subprocess chokepoint for anything expected to succeed; on a
non-zero exit it logs `git_command_failed` (full stderr) and raises plain `GitError`
(sanitize-by-default). `_run_git_ok` (returns `(bool, str)`) and `_run_git_rc` (returns
`(int, str)`) never raise — used wherever a non-zero exit is an expected, meaningful
outcome (e.g. `merge-base --is-ancestor` exiting 1 for "not an ancestor" vs. >1 for a
genuinely unreadable object, which `_is_ancestor_cached` still raises on, to avoid
memoizing a bad answer).

`routes/git.py`'s `_handle_git_error(e: GitError) -> NoReturn` is the sole error-to-HTTP
mapping point, dispatched by `isinstance` in most-specific-first order:
`GitGuardrailError` → 403, `GitDomainError` → 400 (verbatim message), plain `GitError` → 400
with the sanitized `_INTERNAL_ERROR_DETAIL` constant (full detail logged server-side only).
`GitPushRejectedError` and `GitMilestoneForkError` are caught BEFORE the generic `GitError`
handler in the two routes that can raise them (`git_push`, `git_commit`) and mapped to 409
with their structured payload's `model_dump()`. Every route additionally has a catch-all
`except Exception` that logs with `exc_info=True` and returns a plain 500 with the sanitized
detail — this is the backstop for anything that isn't a `GitError` at all (e.g. a bug in
this layer itself).

## Testing

Tests live under `tests/`, all synchronous and driving real git repositories in temp
directories (no git mocking) via the shared helpers in `tests/_git_helpers.py`
(`git_run`, `init_repo`).

- **`tests/test_git_engine.py`** — the primary unit suite for `_git.py`, ~3,050 lines
  organized into 32 test classes covering: slugification, branch-category naming, ledger
  resolve/spawn, `commit_save` (including idempotent-no-op saves), milestone merge and its
  invariant checks, git identity get/set, working-branch status across all four states,
  `set_working_branch` including the unborn-repo seed path and its gitignore-guard
  interaction (`TestSeedGitignoreGuards`, `TestSetWorkingBranchUnbornNonDefault`), move-to-
  commit, milestone history and commit-context breadcrumbs, rename-preserving ledger
  expansion, the branch manager (archive/delete/undelete/restore), fetch throttling,
  canonical-remote resolution, fork/create-working-branch (both move and non-move), archive-
  commit read-only extraction, remotes/push/fast-forward/branch-away, the milestone fork
  gate, protected-branch env-var configuration, and subprocess text-encoding.
- **`tests/test_git_rollback_coverage.py`** — crash-safety-net tests specifically for the
  partial-failure rollback paths: `_rollback_fork`, `_rollback_branch_away`, branch-away
  guard conditions, and that push/fast-forward correctly raise rather than swallow.
- **`tests/test_git_content_caches.py`** — the SHA-keyed cache layer specifically: milestone
  version-label batching, cache freshness across branch moves, subprocess call-count
  assertions (proving the caches actually eliminate the N+1 they claim to), tree-SHA
  caching, fork-credit computation, and the `_list_branches_with_tips` enumeration.
- **`tests/test_git_graph.py`** — `graph_topology` and its route: rich multi-branch
  topology, fork-attachment claiming order, the "merge parents mean saves" semantics,
  entry metadata, window truncation, and that the whole path is read-only (no HEAD/ref
  mutation observed across calls).
- **`tests/test_git_state_coverage.py`** — malformed-input fallback behaviour for every
  `_git_state.py` reader: corrupt JSON, wrong top-level type, missing file — each must
  degrade to the documented empty/default value rather than raising.
- **`tests/test_git_routes.py`** — the HTTP layer: every route's happy path, that all
  handlers are genuinely sync `def` (not `async def`, to avoid event-loop blocking),
  general-exception-to-500 handling, `_handle_git_error`'s logging and status-code mapping
  for all three error families, and that ref-moving routes correctly wrap their `_git` call
  in `pause_watcher()`.
- **`tests/test_git_routes_pydantic.py`** — a narrower contract-pinning suite (item #74)
  asserting `_git` functions return real Pydantic model instances (not dicts), that route
  bodies don't re-wrap what `_git` already returns, and that the wire shape of responses is
  unchanged.
- **Known gaps**: none flagged explicitly in the test files; the suite does not appear to
  exercise true network-level remote failures (a genuinely unreachable host mid-fetch)
  beyond the timeout path, relying instead on `subprocess.run(timeout=...)` semantics being
  correct by construction.
