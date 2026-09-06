# Git Integration — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_git.py` | Stable import facade. Re-exports the route/public Git API and exception taxonomy from the cohesive domain modules below; owns no subprocess, mutable state, cache, or branch transaction. |
| `src/haute/_git_core.py` | Sole Git command and repository-mutation boundary: exception taxonomy, prompt-proof subprocess adapters, fetch serialization/cooldowns, ref validation and branch-pair naming, guardrails, immutable object/ref queries, and content-addressed primitive caches (including parsed graph-log rows). Domain modules depend on this layer; it depends on none of them. |
| `src/haute/_git_setup.py` | Repository and clone setup: repository readiness, identity, default/working-branch association, unborn-repository seeding, and Git preferences. |
| `src/haute/_git_transactions.py` | Working/ledger branch-pair transactions: path-scoped saves, invariant checks, milestone merge/ref transaction, and branch creation/fork replay with compensating rollback. |
| `src/haute/_git_read_models.py` | Immutable panel read models for repository/working-branch readiness and managed working-branch listings. It performs no mutation or fetch. |
| `src/haute/_git_history.py` | Immutable history projection and safe historical materialisation: milestones, ledger saves, commit context, forest topology, use of the core's object-addressed log cache, and bounded archive extraction. |
| `src/haute/_git_remote.py` | Deliberate remote synchronization and transport: remote configuration/listing, clone/bundle flows, pair divergence, fetch, push/bootstrap, fast-forward, and branch-away resolution. It uses the core command boundary and never owns clone-state files. |
| `src/haute/_git_archive.py` | Local branch-pair movement and archive lifecycle: detached historical move, archive, delete/tombstone, undelete, and restore transactions with compensating rollback. |
| `src/haute/_git_lock.py` | Reentrant per-repository mutation-lock registry shared by the engine and clone-state helpers. Uses a bounded marker-aware identity cache, a stable project-path key across `git init`, a common-Git-directory key for linked worktrees, and weak lock values so idle repositories are evicted. It never invokes Git. |
| `src/haute/_git_state.py` | Per-clone, untracked JSON state under `<project_root>/.haute/`: working-branch association (`state.json`), UI preferences (`prefs.json`), last-pushed SHAs (`pushed.json`), delete tombstones (`trash.json`). Fail-soft parsing plus lock-scoped atomic replace; no git subprocess calls. |
| `src/haute/_gitignore_guard.py` | Shared `.gitignore` deny-list owned by [sandbox-security](../sandbox-security/low-level.md) and append-only `ensure_gitignore_guards()` used both by project initialization and unborn-repository seeding; preserves tracked `*.haute.json` sidecars while excluding per-clone/cache/data/venv state. |
| `src/haute/routes/git.py` | FastAPI router at `/api/git`. One `def` (sync) handler per Git endpoint, each a thin `try/except` around a single Git-domain call; converts the domain layer's typed exceptions to HTTP responses via `_handle_git_error`. The `/api/git/storage/*` endpoints hosted in this router are owned by [hosted-project-storage](../hosted-project-storage/low-level.md). |

## Key types and data structures

All public types are Pydantic models defined in `src/haute/schemas.py` (not owned by this
component but returned directly by `_git` functions — no dataclass-to-dict-to-model
conversion in the routes).

**Exceptions** (`_git_core.py`, re-exported by `_git.py`), forming a strict hierarchy:
- `GitError(HauteError)` — base; wraps raw subprocess stderr.
  - `GitDomainError(GitError)` — hand-written, safe-to-surface message.
    - `GitGuardrailError(GitDomainError)` — guardrail block → HTTP 403.
    - `GitTransactionError(GitDomainError)` — the primary mutation and at least one
      compensating rollback step both failed.
    - `GitHistoryReadError(GitDomainError)` — historical archive/extraction/parsing could
      not produce a trustworthy pipeline graph.
    - `GitPushRejectedError(GitDomainError)` — carries a `GitPushRejection`; constructed
      only by `_push_rejection()`.
    - `GitMilestoneForkError(GitDomainError)` — carries a `GitMilestoneFork`; constructed
      only by `commit_milestone()`.

**Branch-pair naming** (`_git_core.py`, re-exported by `_git.py`):
`LEDGER_SUFFIX = "-save"`. `ledger_name(working)` /
`working_name(ledger)` are pure string transforms and inverses of each other.
`branch_category(branch)` classifies any branch name into `"protected" | "ledger" |
"working"` by checking membership in `_protected_branches()` first, then the ledger-suffix
pattern; anything else is a working-branch candidate. `is_eligible_working_branch` and
`_assert_eligible_working` build on this classification.

**Content-addressed cache keys**: every `@lru_cache`-decorated inner function
(`_merge_base_cached`, `_is_ancestor_cached`, `_first_parent_spine_cached`,
`_commit_parents_cached`, `_graph_log_cached`, `_tree_of_cached`)
takes `cwd_key: str` as its final argument — `str(cwd) if cwd else ""` — because a bare
path string is the explicit cache discriminator and two repos/worktrees sharing one
process must never share cache entries. (`Path` itself is hashable; string conversion is
not a hashability workaround.) `_FULL_SHA_RE` / `_is_full_sha()` gates the cached
path: only a full 40-hex SHA is guaranteed immutable; a ref name (branch, `HEAD`, a tag)
falls through to an uncached live subprocess on every call. `_get_default_branch` is also
uncached because the selected remote and its symbolic `HEAD` are mutable ref-name state.

**Domain import direction.** `_git_core`, `_git_lock`, and `_git_state` are leaves.
Setup, transactions, read models, history, remote synchronization, and archive lifecycle
may import those leaves and narrowly import one another in the direction documented by the
control flows below; they never import the `_git` facade. `_git.py` imports domain modules
only to re-export their stable surface. This keeps the graph acyclic and prevents a second
subprocess or clone-state owner from appearing during future work.

**Process boundary.** `_git_core.py` is the sole module that imports or invokes
`subprocess`. Ordinary local commands use `_run_git`, `_run_git_ok`, or `_run_git_rc`.
Commands that need an explicit timeout, non-interactive remote environment, replacement
decoding, or binary output use the overloaded `_run_git_process` adapter. The adapter
returns an immutable typed result (`str` output by default, `bytes` when `binary=True`) and
translates only `subprocess.TimeoutExpired` into the core-owned
`_GitProcessTimeoutError`; operating-system and Unicode failures retain their native types
so each domain preserves its established fail/degrade policy. Domain modules never receive
the `subprocess` module or construct a raw process call themselves.

**Per-clone state files** (`_git_state.py`), all siblings under `<project_root>/.haute/`:
`state.json` (`{"workingBranch": str}`), `prefs.json` (flat dict, currently one key:
`skipSwitchConfirm`), `pushed.json`
(`{"<remote>/<ref>": sha}`), `trash.json` (`{deleted_branch_name: {branch_tip, ledger_tip,
was_archived, deleted_at}}`, capped at `_TRASH_MAX_ENTRIES = 20`, insertion
order = recency). Every writer holds the repository mutation lock, stages a complete UTF-8
document beside its destination, and atomically replaces the target. Every read-modify-write
helper holds that lock across both phases, preserving concurrent entries while readers
observe either the old complete document or the new complete document.

**Mutation-lock identity and lifetime** (`_git_lock.py`): the normalized absolute caller
path is a stable local key. Once `.git` exists, the resolved common Git directory is a
second key acquired after the local key; linked worktrees therefore meet on the common key,
while a mutation that began before `git init` still blocks a post-init mutation on the
unchanged local key. Path resolution and marker-aware identity lookup use separate bounded
256-entry LRU caches; the identity cache key includes the direct `.git` marker fingerprint.
In steady state a clone-state read performs one marker `stat` plus cache lookup, not
`resolve()` plus an ancestor walk and marker/`commondir` reads. The lock registry is a
`WeakValueDictionary`: active and waiting contexts retain a strong reference, while a
repository with no callers leaves no permanent registry entry.

**Gitignore guards** (`_gitignore_guard.py`): `GITIGNORE_GUARD_ENTRIES` is exactly
`.env`, `.haute/`, `impact_report.md`, `.haute_cache/`, `mlruns/`, `data/`, and `.venv/`.
`ensure_gitignore_guards(project_dir)` creates `.gitignore` with those lines when absent or
appends only missing exact lines under a `# Haute` block. Existing non-UTF-8 bytes are decoded
with replacement for membership testing. It deliberately does not ignore `*.haute.json`,
because pipeline position sidecars belong on the save ledger.

**`GitWorkingBranchResponse.state`** (computed by `working_branch_status`) is one of seven
literal values: `"git-unavailable"` (no Git binary on PATH; distinct from
`"no-repository"` so the UI does not offer init), `"no-repository"` (there is no Git
repository), `"unset"` (repository present, attached HEAD, no working branch recorded),
`"detached"` (HEAD has no branch; `head_sha` supplies the accurate commit context),
`"invalid"` (Git metadata or the recorded pair is missing / ineligible /
invariant-violating), `"divergent"` (attached HEAD is on neither the recorded branch nor
its ledger), or `"ready"`. This read is total for missing and invalid repository metadata;
transport/server failures remain non-200 responses.

**HTTP contracts.** Every handler is synchronous `def`, so FastAPI runs git subprocess work
in its thread pool. Request bodies are the named Pydantic models; omitted fields take the
defaults shown. Query bounds are enforced by FastAPI and invalid input uses its standard 422
validation envelope. The `/api/git/storage/*` endpoints hosted in this router and the post-commit push enqueue on `POST /api/git/commit` are owned by [hosted-project-storage](../hosted-project-storage/low-level.md).

| Method and path | Input | Success response |
|---|---|---|
| `GET /api/git/working-branch` | None | `GitWorkingBranchResponse` (seven-state readiness contract above, including `head_sha` when resolvable; augmented via `_with_storage_state` with durable-storage fields `storage`, `storage_remote`, `storage_forked_from`, `sync`, and `storage_bind` owned by [hosted-project-storage](../hosted-project-storage/low-level.md)) |
| `POST /api/git/working-branch` | `GitSetWorkingBranchRequest {branch,create=false}` | `GitSetWorkingBranchResponse {working_branch,state,last_save_sha?}` |
| `POST /api/git/move` | `GitMoveRequest {sha}` | `GitMoveResponse {sha,short_sha,prior_branch,is_detached=true}` |
| `POST /api/git/identity` | `GitSetIdentityRequest {user_name,user_email,set_global=false}` | `GitSetIdentityResponse {user_name,user_email,scope}` |
| `POST /api/git/commit` | `GitCommitRequest {message,version_label=null,allow_fork=false}` | `GitCommitResponse {sha,short_sha,working_branch,version_label?}`; would-fork is 409 |
| `GET /api/git/milestones` | Query `limit=20` (1..500), `branch=null` | `GitMilestonesResponse {working_branch?,entries:[GitMilestoneEntry...]}` |
| `GET /api/git/graph` | Query `limit=50` (1..500) | `GitGraphResponse {working_branch?,order:[],branches:[GitGraphBranch...]}` |
| `GET /api/git/milestones/{sha}/saves` | Commit SHA path | `GitLedgerSavesResponse {saves:[GitLedgerSave...]}` |
| `GET /api/git/pending-saves` | Query `branch=null` | `GitLedgerSavesResponse` |
| `POST /api/git/archive` | `GitArchiveRequest {branch}` | `GitArchiveResponse {archived_as}` |
| `DELETE /api/git/branches` | `GitDeleteBranchRequest {branch,confirm=false}` | `GitDeleteBranchResponse {status="deleted",branch}` |
| `POST /api/git/undelete` | `GitUndeleteRequest {branch}` | `GitUndeleteResponse {status="restored",branch}` |
| `GET /api/git/working-branches` | None | `GitWorkingBranchesResponse {current?,branches:[GitManagedBranch...]}` |
| `POST /api/git/restore` | `GitRestoreRequest {branch}` | `GitRestoreResponse {restored_as}` |
| `POST /api/git/working-branches` | `GitCreateWorkingBranchRequest {name,at=null,move=false}` | `GitCreateWorkingBranchResponse {working_branch,moved,switched,last_save_sha?}` |
| `GET /api/git/prefs` | None | `GitPrefs {skip_switch_confirm=false}` |
| `POST /api/git/prefs` | `GitPrefs {skip_switch_confirm=false}` | The persisted `GitPrefs` |
| `GET /api/git/remotes` | None | `GitRemotesResponse {remotes:[GitRemote {name,url,working,ledger}...],working_branch?}`; `working` and `ledger` are the sole per-leg divergence records and URL userinfo is redacted |
| `GET /api/git/show/{sha}` | Commit SHA path | Read-only `PipelineGraph` |
| `GET /api/git/commit-context/{sha}` | Commit SHA path; query `base=null` | `GitCommitContext`, with `delta_from_base` only when `base` is supplied |
| `POST /api/git/push` | `GitPushRequest {remote}` | `GitPushResponse {remote,working_branch,ledger_branch,default_branch,bootstrapped_default=false,pushed_refs=[]}`; `default_branch` and `bootstrapped_default` are required response members, and working/ledger non-fast-forward is 409 |
| `POST /api/git/fast-forward` | `GitFastForwardRequest {remote}` | `GitFastForwardResponse {remote,working_branch,fast_forwarded=[]}` |
| `POST /api/git/branch-away` | `GitBranchAwayRequest {remote}` | `GitBranchAwayResponse {working_branch,set_aside_as}` |

## Control flow

**Save.** `commit_save(paths, working, cwd, message)` → `resolve_ledger(working)` (find-or-
lazily-spawn the ledger at the working branch's current tip, checkout if not already
current) → `git status --porcelain -- <paths>` to check anything in *paths* actually
changed (idempotent no-op returns `None`) → `git add -- <paths>` → `git diff --cached --quiet HEAD -- <paths>` re-check (returns `None` when clean, reconciling a stale index entry that reported a spurious modification) → `git commit -m <msg> --
<paths>` (pathspec-scoped, so it commits only those paths' working-tree state regardless of
what else the user may have pre-staged) → returns the new SHA.

Every public mutation entry point acquires the reentrant repository lock before its first
precondition read and holds it through Git changes, clone-state writes, and any compensation.
Nested calls such as `commit_milestone` → `commit_save` → `resolve_ledger` reuse the same
lock. Pure Git history/object readers do not acquire the engine lock, but every clone-state
reader in `_git_state.py` acquires it for the file read so it cannot overlap an atomic state
transaction.

**Milestone.** `commit_milestone(message, project_root, version_label, cwd, allow_fork)` →
reads the recorded working branch from `_git_state.read_working_branch` → unless
`allow_fork`, calls `divergence_state(working)` (local-refs-only, no fetch) and raises
`GitMilestoneForkError` if the leg is `"behind"` or `"diverged"` → before the merge,
`_residual_tracked_changes` enumerates every modified path already tracked by Git and, when
non-empty, records all of them in one final ledger save with the fixed message `"Updated
tracked project files"`; untracked paths are deliberately excluded → `merge_to_working`
validates the message (non-empty, no C0 control characters — a stray record-separator would
corrupt the ledger-save parser's delimiter), runs `check_invariants` (see below) and raises
`GitDomainError` on any violation, computes `merge_base(working_tip, ledger_tip)` and raises
if it already equals the ledger tip ("no new saves"), then requires
`refs/tags/version/<label>` to satisfy Git's complete full-ref format (invalid labels are a
user-facing `GitDomainError` rather than a raw plumbing failure), rejects any duplicate tag
before mutation, and builds the merge purely via
`commit-tree <ledger's tree> -p <working_tip> -p <ledger_tip> -m <message>`. Without a
label, a CAS `update-ref` advances the working branch. With a label, `hash-object
--literally -t tag` writes the fully Haute-constructed annotated-tag payload without
invoking platform-dependent tag fsck, and one `update-ref --stdin` transaction
atomically applies both the working-branch CAS and tag-ref creation — no checkout or index,
and a label conflict or ref race leaves both refs unchanged. `_run_git` passes stdin as
bytes so Windows cannot translate the transaction's required LF separators to CRLF.

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
    `GIT_COMMITTER_*` env vars). Before replay, every to-be-relocated commit must have
    exactly one parent; an external merge is rejected because linear replay would destroy
    topology. The operation then creates both new branches and checks out the new ledger
    (its tree matches old HEAD's, so uncommitted edits carry across untouched), rewinds the
    spawning branch's ledger to the fork point, and records the new working branch. Any
    exception in this multi-step sequence triggers `_rollback_fork`; a complete rollback
    re-raises the original error, while any failed compensation raises
    `GitTransactionError`.

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
*post-seed pair-creation* path (branch checkout, ledger spawn, state write) is wrapped so any
exception restores the post-seed HEAD (symbolic ref if it existed, else detached-SHA) and
deletes the half-created branch and ledger.

The seed phase runs before that rollback snapshot. A failure while seeding can therefore
leave an unborn branch renamed to `main`, newly appended `.gitignore` guards, and index
staging changes; a root commit that succeeded is deliberately permanent. These operations
leave working-tree file contents in place. Once seeding has completed, branch/ledger refs,
HEAD, and recorded working-branch state are the state the pair-creation rollback protects.

Adopting an existing pair is transactional too: the previous symbolic/detached HEAD,
whether the target ledger existed, and the previous clone association are captured before
checkout. A failed ledger spawn, checkout, or state-file replacement restores all three.

**Milestone metadata and commit context.** Log rows use NUL-delimited fields, so tabs in
external commit subjects cannot shift the SHA/message/timestamp columns. Version labels are
read once per request with `for-each-ref`. The paginated milestone endpoint defaults to its
newest 20 entries, while `commit_context` explicitly reads the complete first-parent
milestone spine so commits older than that display window are still classified and anchored
correctly. It reads the target's ancestor set in batched commands, derives fold points from
the already returned parent columns, and never launches one subprocess per milestone.

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

**Push (`push_working_pair`).** This is the first and only publication boundary: `haute
init`, `haute serve`, `set_working_branch`, and `create_working_branch` perform no remote
mutation. The recorded working name must first pass the normal working-branch guardrails and
resolve specifically through `refs/heads/<working>`; an optional ledger likewise exists only
when `refs/heads/<ledger>` resolves, so a corrupted state value cannot reinterpret `HEAD`, a
tag, or another revision expression as a branch. Push then performs a strict `git ls-remote
--symref` inspection of the selected
remote using a prompt-proof environment (`GIT_TERMINAL_PROMPT=0`, SSH `BatchMode=yes` with a
connection timeout) plus the process timeout. Unlike a best-effort poll, this helper must
preserve three outcomes: a successful advertisement with object refs, a successful
advertisement with zero object refs, and failure. An unborn symbolic `HEAD` without an
object SHA does not make the remote non-empty; any advertised branch or tag does. Timeout,
`OSError`, non-zero exit (including authentication/network failure), or malformed/unreadable
output raises and aborts before any push.

Advertisement parsing accepts only a `HEAD` symref to a Git-valid `refs/heads/*` target and
non-zero 40- or 64-hex object IDs attached to `HEAD` or Git-valid fully qualified refs; every
object ID in one advertisement must use the same width. A `^{}` pseudo-ref is allowed only
as the peeled form of a valid tag. Conflicting duplicate object lines, or an object `HEAD`
that disagrees with its advertised target branch, are
malformed. This validation is deliberately stricter than merely finding one usable branch:
an untrusted advertisement must be internally coherent before it can authorize publication.

Default resolution is selected-remote-aware and never accepts the current working or ledger
branch as a fallback. An advertised symbolic `HEAD` naming `refs/heads/<name>` is
authoritative for a non-empty remote; if that named ref is absent/dangling, the push refuses.
When a non-empty advertisement contains no `HEAD` symref, a distinct resolving local
`main` (then `master`) is an expectation: the matching advertised remote head is required,
and its absence refuses instead of selecting some other branch. If neither conventional
local branch exists, resolution considers names present both as advertised remote heads and
as distinct resolving base refs from either local branches or remote-tracking refs belonging
to that selected remote (never another remote): `main`, then `master`, then exactly one
remaining unmanaged intersection. No match or several matches refuses rather than guessing
which remote branch is the merge target. Including the selected remote-tracking namespace
keeps a normal clone usable when its remote `HEAD` is dangling and Git therefore created
`origin/main` but no local `main` branch.

For an empty remote, the symbolic target is used only when a distinct local branch of that
name resolves. Otherwise resolution checks a resolving local `main`, then `master`, then
requires exactly one remaining unmanaged local base branch after excluding the current
working/ledger pair, ledger-suffixed branches, archived branches, and branches that have a
managed ledger sibling. Zero or several remaining candidates is ambiguous and refuses.
The resulting `default_branch` is therefore deterministic, can be custom-named, and cannot
come from another canonical remote by accident.

For a zero-object-ref remote, the local default, working, and optional ledger refs are first
resolved to commit-SHA snapshots and must share history; a missing ref or missing merge base
refuses before mutation. The explicit branch refspecs pin those validated sources:
`<default_sha>:refs/heads/<default> <working_sha>:refs/heads/<working>
[<ledger_sha>:refs/heads/<ledger>]`. The push also supplies a create-only lease
`--force-with-lease=refs/heads/<default>:`: its empty expected value can never authorize
updating an existing default. If a concurrent writer creates that ref at the exact validated
`default_sha`, Git may classify the refspec as already up to date and submit no update for
that ref; allowing the atomic pair to proceed is safe because the required merge target is
already identical. A different-SHA concurrent default fails the lease.
For a non-empty remote, the resolved expected default ref must be advertised; it is fetched
authoritatively with `--no-tags` and checked for a merge base with the local working history
and ledger when present. The existing remote default is validation-only and is omitted from the push
refspecs, so Haute never implicitly advances it. A non-empty remote without the expected
default, an unrelated history, or an inspection/fetch failure raises a domain or sanitized
git error before either pair ref is published. Haute does not configure the remote host's
repository-level default-branch setting.

After preflight, `_tag_collisions` still checks `version/*` labels (best-effort — git's tag
rejection remains the backstop), then the pinned-SHA refspecs are submitted with `git push
--atomic --follow-tags` and, only for bootstrap, the create-only default lease described
above. No path supplies `--force`, a force refspec, or a lease that expects an existing
object. The push subprocess uses the prompt-proof remote environment and a hard
`_PUSH_TIMEOUT_SECONDS` ceiling; `TimeoutExpired` and process-launch `OSError` are converted
to sanitized `GitError` failures, after which the repository mutation lock is released. The
default, working, and optional ledger therefore all land or none lands during bootstrap; on
an established remote the working/ledger pair remains atomic. Any bootstrap
rejection, including a different-SHA default winning the create-only race, follows a safe
non-409 push-failure path and is never misreported as working/ledger divergence. On an
established remote, a
working/ledger non-fast-forward failure (`non-fast-forward` / `fetch first` / `[rejected]`
in stderr) is turned into `_push_rejection`, which force-fetches past the throttle (a
rejection is authoritative, not a poll), recomputes both legs, and checks `_is_rewrite` per
leg using the last-pushed-SHA record. The same validated working/ledger SHA snapshots used
as push sources are recorded on success via `_git_state.record_pushed_shas`, so a concurrent
local branch move can neither publish an unvalidated tip nor record a tip that was not
submitted. The one-time default is deliberately omitted because
`pushed.json` is rewrite evidence only for pair refs Haute may publish again, while an
established default is validation-only and never Haute-owned.

`GitPushResponse.default_branch` is the selected-remote-aware default used by the preflight.
`bootstrapped_default` is a required boolean response member whose model default is `false`;
it is `true` only when the successful atomic push submitted the default ref to a zero-ref
remote. `pushed_refs` lists the names from explicit branch refspecs, excluding annotated tags
that `--follow-tags` may add; it is not a claim that every named ref advanced. Bootstrap
returns `[default, working]` plus the ledger when that local ref exists, while an established
remote returns `[working]` plus that optional ledger. `ledger_branch` continues to name the
managed ledger even when it has not spawned and is therefore absent from `pushed_refs`.
Repeating a successful push is idempotent and reports `bootstrapped_default=false` once the
remote advertises refs.

**Fast-forward (`fast_forward_pair`).** Requires HEAD to currently be on the ledger (refuses
mid-move/detached states) and a clean tracked tree. Fetches and prunes the configured remote
namespace, then aborts with `GitDomainError` if that required refresh times out, cannot
launch, exits non-zero, or shows that either managed remote leg is missing; transport
failure and a deleted working/ledger branch have distinct user-facing refusals. No cache
wipe, merge, or ref update may run on stale or incomplete tracking refs. It then requires
EVERY leg to be `"behind"` or already synced — any `"ahead"`/`"diverged"` leg refuses
outright (the user must reconcile via `branch_away` instead; this function never merges).
The ledger (checked out) advances via `git merge --ff-only`; the working ref (not checked
out) advances via a CAS `update-ref`. If the checked-out ledger advances but the working-ref
CAS fails, the ledger and working tree reset to their captured tip. A failed reset is
reported as `GitTransactionError`, never as a clean refusal or success.

**Branch away (`branch_away`).** The reconciliation path when a remote fork is detected.
It first fetches and prunes the configured remote namespace, aborting with `GitDomainError`
on any transport/process failure before choosing an aside name or mutating refs. This
authoritative snapshot lets an absent optional ledger remain a successful fetch while
removing a deleted tracking ref; an absent working branch is then a distinct domain
refusal. It renames the current pair to a unique dated aside name
(`_unique_aside_name`), creates fresh
`working`/`ledger` branches at the remote's tips, checks out the new ledger, and records the
merge-base of the old and new working tips as the aside branch's fork point (for the branch
manager's back-link). If the remote has no ledger (`X2`), the local ledger is respawned at
the adopted tip rather than adopted. The whole sequence is wrapped with
`_rollback_branch_away` (drops any freshly created canonical refs, renames the aside pair
back, and restores HEAD) on any `GitError`/`OSError`; incomplete compensation raises
`GitTransactionError`.

**Archive/delete active-pair fallback.** The fallback is resolved immediately before the
mutation and must not be either leg of the pair. A distinct existing local base branch is
preferred; if none exists, HEAD detaches at the captured pair tip. This covers adopted
repositories whose only apparent default is the active managed pair.

**Delete / trash (`delete_working_pair` / `undelete_working_pair`).** Delete refuses on
unmerged ledger saves unless `confirm=True`. Before removing the branch refs, both tips are
pinned under `refs/haute/trash/<name>` (a plain, non-`refs/heads` ref that shields the
objects from gc) and a tombstone is written to `trash.json` — deliberately in that order,
so a failure between the two leaves at worst a harmless extra pin, never an unrecoverable
delete. `undelete_working_pair` is the exact inverse: recreates both refs at their recorded
tips (verifying each still resolves — a tombstone can outlive its objects if the trash refs
were hand-deleted and gc ran), and consumes the trash
refs + tombstone. The restored pair is NOT auto-adopted as the working branch.

**Historical extraction (`archive_commit` / `commit_pipeline_graph`).** `ls-tree` first
enumerates the selected commit and filters to `haute.toml`, Python modules, Haute sidecars,
and files below `config/` or `prompts/`; `git archive` receives only those literal paths.
Tar extraction is implemented with Python-3.11-compatible regular-file/directory handling
and validates the complete member list before writing. It rejects traversal, unsupported
members, more than `_HISTORY_ARCHIVE_MAX_MEMBERS = 10_000` entries, and cumulative
regular-file size above `_HISTORY_ARCHIVE_MAX_BYTES = 64 * 1024 * 1024`. Malformed or
over-limit tar data raises `GitHistoryReadError`. Parsing tries each discovered pipeline,
but if every candidate fails it raises the same typed failure rather than returning
`PipelineGraph()`. Temporary
directory cleanup retries transient Windows sharing violations and logs an exhausted cleanup
without replacing a successfully parsed response with a cleanup error.

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
  (the fork-gate then degrades OPEN, never blocking a local-only user). Remote listings
  remain explicitly last-known rather than claiming an in-sync result.
- **Concurrent worktrees sharing one process** — linked worktrees resolve through their
  `.git` indirection and `commondir` to the same common-dir mutation key because they share
  refs and object storage. Every call also acquires its stable local-path key first; that
  key does not change when a previously uninitialized project gains `.git`, closing the
  pre-init/post-init identity transition. Distinct repositories use distinct locks, and
  their reads/mutations may proceed concurrently. `_fetch_exec_lock` remains process-wide
  because Git object writes for a shared store must not race.
- **Concurrent git mutations are serialized per repository** — FastAPI may run synchronous
  handlers on different worker threads, but only one can cross a repository's mutation
  boundary. Successful saves cannot be orphaned by an interleaved checkout, and state-file
  read/merge/write transactions cannot lose sibling updates.
- **A slow / credential-walled remote** — routine readiness, branch, history, graph, and
  remote-list reads never perform network I/O. Every deliberate remote read disables terminal/SSH prompts
  (`GIT_TERMINAL_PROMPT=0`, SSH `BatchMode=yes`). Fetch/inspection subprocesses use
  `_FETCH_TIMEOUT_SECONDS`; the publication subprocess uses `_PUSH_TIMEOUT_SECONDS`.
  Explicit Push inspection/publication, expected-default fetch, Catch up, and Spin off a
  copy are strict: timeout, launch failure, non-zero exit, or unreadable output refuses the
  operation and must never be reclassified as a zero-ref remote or successful refresh.
- **Remote baseline states at Push** — successful zero-object-ref advertisement is the sole
  bootstrap case; non-empty + expected default + related history publishes only the pair;
  non-empty + missing expected default and non-empty + unrelated default both refuse before
  submitting any refspec. “Empty” describes the strict preflight snapshot: a different
  branch or tag may appear afterward and coexist safely. A concurrently-created default at
  a different SHA fails the empty-value lease; one at the exact submitted SHA may be an
  up-to-date no-op and safely coexist with successful pair publication. Atomicity prevents
  any failure from partially creating the submitted default/working/optional-ledger set.
- **Rename detection in ledger-save history** — `_parse_ledger_saves` runs `git log -M
  --name-status` so a renamed config file shows as one rename entry, not a delete+add pair.
- **Unicode / space-containing paths in log output** — `core.quotepath=false` is passed
  explicitly so git doesn't octal-escape and quote non-ASCII filenames in the parsed
  history output.
- **Tabbed or externally-authored commit subjects** — milestone, graph, ledger-save, and
  commit-context metadata use NUL-delimited fixed fields with the subject last. Tabs are
  preserved verbatim and cannot shift timestamps or SHAs. Haute-authored milestone messages
  still reject non-whitespace C0 controls.
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

`_run_git` is the single subprocess chokepoint for anything expected to succeed; all text
Git processes receive `LC_ALL=C`, `LANG=C`, and `LANGUAGE=C` in addition to UTF-8 decoding,
so recognition of the small documented set of Git failure phrases is locale-independent.
When `_run_git` supplies byte-mode stdin for an LF-delimited plumbing protocol, it
replacement-decodes stdout/stderr as UTF-8 so malformed diagnostic bytes still reach the
normal sanitized `GitError` path instead of escaping as `UnicodeDecodeError`.
On a non-zero exit it logs `git_command_failed` (full stderr) and raises plain `GitError`
(sanitize-by-default). `_run_git_ok` (returns `(bool, str)`) and `_run_git_rc` (returns
`(int, str)`) do not raise merely for a non-zero git exit — used wherever that exit is an expected, meaningful
outcome (e.g. `merge-base --is-ancestor` exiting 1 for "not an ancestor" vs. >1 for a
genuinely unreadable object, which `_is_ancestor_cached` still raises on, to avoid
memoizing a bad answer). None of these three general wrappers catches a subprocess-launch
`OSError`; that propagates to the route's generic 500 handler. Deliberate remote helpers,
including the final push subprocess, explicitly catch timeout/OS failures and convert
required refresh failures to a Git error.

Push-time remote inspection is not a polling helper and never degrades. It logs unsafe raw
stderr server-side, returns only hand-authored safe detail when a specific domain condition
is known, and otherwise follows the sanitized `GitError` path. Authentication, network,
timeout, launch, or advertisement-parse failure occurs before refspec submission and leaves
HEAD, local branch/tag refs, the index, working tree, and `.haute` state unchanged; it must
not be converted into an empty-remote bootstrap. A later authoritative default fetch may
update the selected remote-tracking ref and object database before validation refuses, but
never mutates those user-owned local surfaces.

`routes/git.py`'s `_handle_git_error(e: GitError) -> NoReturn` is the sole error-to-HTTP
mapping point, dispatched by `isinstance` in most-specific-first order:
`GitGuardrailError` → 403, `GitDomainError` → 400 (verbatim message), plain `GitError` → 400
with the sanitized `_INTERNAL_ERROR_DETAIL` constant (full detail logged server-side only).
`GitPushRejectedError` and `GitMilestoneForkError` are caught BEFORE the generic `GitError`
handler in the two routes that can raise them (`git_push`, `git_commit`) and mapped to 409
with their structured payload's `model_dump()` as `HTTPException.detail`; the wire envelope
is `{"detail": <GitPushRejection|GitMilestoneFork object>}`. Every route additionally has a catch-all
`except Exception` that logs with `exc_info=True` and returns a plain 500 with the sanitized
detail — this is the backstop for anything that isn't a `GitError` at all (e.g. a bug in
this layer itself).

## Testing

Tests live under `tests/` and are synchronous. Workflow tests drive real git repositories in
temporary directories via the shared helpers in `tests/_git_helpers.py` (`git_run`,
`init_repo`); narrow subprocess seams are mocked only to make malformed advertisements,
process failures, and precise ref-movement races deterministic.

- **`tests/test_service_domain_boundaries.py`** — structural contracts keep the
  Git facade explicit, the domain-module import graph acyclic, subprocess
  execution in `_git_core.py`, and the shared repository lock on every
  serialized mutator.
- **`tests/test_git_engine.py`** — the primary unit suite for the Git facade and domain
  modules, organized into
  focused test classes covering: slugification, branch-category naming, ledger
  resolve/spawn, `commit_save` (including idempotent-no-op saves), milestone merge and its
  invariant checks, git identity get/set, working-branch status across all seven readiness states,
  `set_working_branch` including the unborn-repo seed path and its gitignore-guard
  interaction (`TestSeedGitignoreGuards`, `TestSetWorkingBranchUnbornNonDefault`), move-to-
  commit, milestone history and commit-context breadcrumbs, rename-preserving ledger
  expansion, the branch manager (archive/delete/undelete/restore), request-path no-fetch
  guarantees,
  canonical-remote resolution, fork/create-working-branch (both move and non-move), archive-
  commit read-only extraction, remotes/push/fast-forward/branch-away, the milestone fork
  gate, protected-branch env-var configuration, and subprocess text-encoding. Push coverage
  includes the full unborn-repo → first working branch → explicit Push journey; atomic
  publication of resolved default + working + optional ledger to a zero-object-ref remote;
  required response metadata and explicit-branch-refspec reporting; absent-ledger behavior;
  idempotent re-push; selected-remote and custom-default resolution; preservation of an
  existing related default; both outcomes of an empty-snapshot/concurrent-default race;
  pinned source-SHA refspecs and exact pair-only `pushed.json` snapshots; and refusal without
  partial publication for a tags-only or non-empty
  missing-default remote, unrelated history, malformed advertisement, remote-only tag
  auto-follow during a refused preflight,
  inspection/auth/timeout failure, or any ref rejection.
- **`tests/test_git_improvements.py`** — focused roadmap regressions for the shared
  repository lock (including cached lookup, `git init` identity stability, and weak-registry
  eviction), concurrent real saves, lock-scoped atomic clone-state updates,
  seven-state readiness, locale-stable Git execution, NUL-delimited history fields,
  batched commit context, merge-replay refusal, network-free remote listing, targeted
  historical extraction, archive member/byte ceilings, typed archive/parse failures, and
  Windows cleanup retries.
- **`tests/test_git_lifecycle_improvements.py`** — failure-injection coverage for
  adopting an existing pair, deleting the only active pair, partial fast-forward,
  archive state replacement, archive restore, undelete, and explicit incomplete-
  compensation failures.
- **`tests/test_git_rollback_coverage.py`** — crash-safety-net tests specifically for the
  partial-failure rollback paths: `_rollback_fork`, `_rollback_branch_away`, branch-away
  guard conditions, and that unreachable-remote push preflight and fast-forward failures
  correctly raise rather than swallow.
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
  degrade to the documented empty/default value rather than raising; deterministic thread
  coordination additionally pins lossless concurrent pushed-tip updates and atomic
  reader-visible replacement.
- **`tests/test_gitignore_guard.py`** — exact deny-list membership (including the positive
  assertion that `*.haute.json` remains tracked), file creation, idempotent byte preservation,
  append-only missing-entry repair, and non-UTF-8 input handling.
- **`tests/test_git_routes.py`** — the HTTP layer: every route's happy path, that all
  handlers are genuinely sync `def` (not `async def`, to avoid event-loop blocking),
  general-exception-to-500 handling, `_handle_git_error`'s logging and status-code mapping
  for all three error families, and that ref-moving routes correctly wrap their `_git` call
  in `pause_watcher()`. The push route pins `default_branch`,
  `bootstrapped_default`, and `pushed_refs` on bootstrap and established-remote responses,
  plus refusal/error mapping without partial publication.
- **`tests/test_git_routes_pydantic.py`** — a narrower contract-pinning suite (item #74)
  asserting `_git` functions return real Pydantic model instances (not dicts), that route
  bodies don't re-wrap what `_git` already returns, and that the wire shape of responses is
  pinned, including the two required default-bootstrap response members and their false-
  by-default semantics.
- **Known gaps**: provider control-plane behaviour is intentionally outside this suite;
  tests assert git refs only and do not claim that GitHub/GitLab/another host changes its
  repository-level default-branch setting after the bootstrap push.
