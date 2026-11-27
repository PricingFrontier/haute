# Git Integration — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_git.py` | Stable import facade. Re-exports the route/public Git API and exception taxonomy from the cohesive domain modules below; owns no subprocess, mutable state, cache, or branch transaction. |
| `src/haute/_git_core.py` | Sole Git command and repository-mutation boundary: exception taxonomy, prompt-proof subprocess adapters, fetch serialization, ref validation and branch-pair naming (`_free_pair_name`), guardrails, the classification of every git command it runs as one that moves a local ref or not (`_moves_local_refs`, refusing one it does not know) and the refusal of a ref move outside a recorded operation's step or a save's one commit (`_ref_moves_permitted`, `_save_commit`), immutable object/ref queries, content-addressed primitive caches (including parsed graph-log rows), and the tree fence every working-tree-rewriting command runs through (`_rewrite_tree`, `_tracked_changes`, `GitFilesInUseError`). Domain modules depend on this layer; it depends on none of them but the clone-state leaf, whose operation record its lock decorator reads. |
| `src/haute/_git_operation.py` | Recorded operations: `Operation`, the ordered steps of a git action that moves a ref (ref transactions, checkouts, a fast-forward, file writes, HEAD, the recorded working branch, trash entries), its record in `.haute/operation.json` written before the first step and removed after the last, the commit point, and `recover`, the one routine that finishes or puts back an operation from its record, for a caught failure and for Retry alike. It is the only module whose code runs a ref-moving git command. |
| `src/haute/_git_setup.py` | Repository and clone setup: repository readiness, identity, default/working-branch association, unborn-repository seeding, and Git preferences. |
| `src/haute/_git_transactions.py` | Working/ledger branch-pair transactions: path-scoped saves, invariant checks, the sweep of unrecorded project files, milestone merge/ref transaction, version restore, and branch creation/fork replay, each a recorded operation, and Retry of an interrupted one. |
| `src/haute/_lint_refusal.py` | `explain_lint_refusal`: a milestone refused by the scaffolded pre-commit hook, said node by node from ruff's concise diagnostics (`lint_problems`, `LintProblem`), each line quoted, with the fix-or-`noqa` instruction and `REFUSAL_GUIDE_URL`; nothing for output holding no diagnostic. |
| `src/haute/_git_read_models.py` | Immutable panel read models for repository/working-branch readiness and managed working-branch listings, and the project-file reads behind the Pipeline files panel: `pipeline_file_states` (how each file in a folder stands against version history: `new`, `changed` or `ignored`) and `ignore_rule` (the `.gitignore` rule that ignores a path). It performs no mutation or fetch. |
| `src/haute/_git_history.py` | Immutable history projection and safe historical materialisation: milestones, ledger saves, commit context, forest topology, use of the core's object-addressed log cache, and bounded archive extraction. |
| `src/haute/_git_remote.py` | Deliberate remote synchronization and transport: remote configuration/listing, clone/bundle flows, pair divergence, fetch, push/bootstrap, fast-forward, and branch-away resolution. It uses the core command boundary and never owns clone-state files. |
| `src/haute/_git_archive.py` | Local branch-pair archive lifecycle: archive, delete/tombstone, undelete, un-archive and repair, each a recorded operation. |
| `src/haute/_git_lock.py` | Reentrant per-repository mutation-lock registry shared by the engine and clone-state helpers. Uses a bounded marker-aware identity cache, a stable project-path key across `git init`, a common-Git-directory key for linked worktrees, and weak lock values so idle repositories are evicted. It never invokes Git. |
| `src/haute/_git_state.py` | Per-clone, untracked JSON state under `<project_root>/.haute/`: working-branch association (`state.json`), UI preferences (`prefs.json`), last-pushed SHAs (`pushed.json`), delete tombstones (`trash.json`), and the record of a git operation in progress (`operation.json`, read by `read_operation`, worded by `operation_words`). Fail-soft parsing plus lock-scoped atomic replace; no git subprocess calls. |
| `src/haute/_gitignore_guard.py` | Shared `.gitignore` deny-list owned by [sandbox-security](../sandbox-security/low-level.md) and append-only `ensure_gitignore_guards()` used both by project initialization and unborn-repository seeding; preserves tracked `*.haute.json` sidecars while excluding per-clone/cache/data/venv state. |
| `src/haute/routes/git.py` | FastAPI router at `/api/git`. One `def` (sync) handler per Git endpoint, each a thin call into the Git domain; a git error the route does not map itself is answered by the server API's failure answers. The `/api/git/storage/*` endpoints hosted in this router are owned by [hosted-project-storage](../hosted-project-storage/low-level.md). `POST /api/git/push` publishes through `haute._ci_secrets.push_with_ci_secrets`, and `POST /api/git/ci-secrets` is `set_missing_ci_secrets`; the deploy's-secrets step they add is owned by [cli](../cli/low-level.md). |

## Key types and data structures

All public types are Pydantic models defined in `src/haute/schemas.py` (not owned by this
component but returned directly by `_git` functions — no dataclass-to-dict-to-model
conversion in the routes).

**Exceptions** (`_git_core.py`, re-exported by `_git.py`), forming a strict hierarchy:
- `GitError(HauteError)` — base; wraps raw subprocess stderr.
  - `GitDomainError(GitError)` — hand-written, safe-to-surface message.
    - `GitGuardrailError(GitDomainError)` — guardrail block → HTTP 403.
    - `GitBlockedError(GitDomainError)` — git refused for a cause outside Haute that its
      message names (`_blocked_by`), carrying `cause`: `lock` ("Unable to create
      '<path>.lock': File exists"), `untrusted-folder` ("detected dubious ownership"),
      `damaged-config` ("bad config line <n> in file <file>", or "bad boolean config value
      '<value>' for '<setting>'") or `signing`, read only of a
      command that commits (`commit`, `commit-tree`), since an SSH key's words can be a
      push's ("failed to sign the data" from gpg or gpgsm; for the SSH format, no
      `user.signingkey` set, "Couldn't load public key", "No private key found for public
      key", a key that asks for a passphrase, an `ssh-keygen` too old to sign). Its message says the cause and what to do: "Another git program is
      using this project's repository: git found its lock file <file>. Wait for it to
      finish, then try again. If no git program is running, one that stopped left the file
      behind: delete <file>, then try again."; "Git does not trust this project's folder,
      because another account owns it (as on a network share), so it will not work in it.
      To trust the folder, run: git config --global --add safe.directory <the value git
      proposes>"; "Git can't read its settings: line <n> of <file> is damaged. Correct or
      remove that line, then try again." (for a setting that is neither true nor false: "Git
      can't read its settings: <setting> is set to '<value>', which is neither true nor
      false. Correct it, then try again."); "Git is set to sign every commit, and this one
      could not be signed, so nothing was recorded. Check that the signing key git uses is
      available and signs without asking for a passphrase, then try again." A file inside
      the project is named relative to it.
    - `GitUnrecordedFilesError(GitDomainError)` — an operation that replaces the project's
      files refused over files off version history (`_assert_way_clear`), carrying `files`;
      HTTP 409 `unrecorded_files`.
    - `GitTransactionError(GitDomainError)` — a recorded operation `recover` can neither
      put back nor finish: its refs are where no step of its record leaves them ("<operation>
      stopped part-way, and its branches have moved since. Inspect the repository before
      retrying."), git refuses the transaction that would set them ("<operation> could not
      be put back." or "finished."), or the record cannot be read. The record stays, and
      the operation's own failure is its cause. The milestone's hook pass raises it too
      when the hooks' operation cannot put HEAD back on the ledger.
    - `GitInterruptedError(GitDomainError)` — a git action asked for while
      `.haute/operation.json` is there: "<operation> stopped part-way. Choose Retry first."
      Every `_serialized_mutation` raises it once it holds the lock; Retry, identity and
      preferences (`_serialized_while_interrupted`) do not.
    - `GitHistoryReadError(GitDomainError)` — history git could not read (a milestone list
      or a range of saves), or historical archive/extraction/parsing could not produce a
      trustworthy pipeline graph.
    - `GitRemoteUnavailableError(GitDomainError)` — a deliberate remote read (inspection, the
      expected default's fetch, Catch up's and Spin off's refresh) or push could not reach
      the remote: a timeout, a launch failure, or a non-zero exit that is not a refusal.
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
`_assert_eligible_working` build on this classification. `_assert_eligible_working` first
refuses a name `_branch_name_problem` finds git cannot use. That check names the first rule
broken, in words: empty; `HEAD` or `@`; a leading `-` or `/`; a space or a control
character; `~ ^ : ? * [ \`; `..`, `@{` or `//`; a trailing `/`, `.lock` or `.`; a part
starting with `.` or ending in `.lock`. The refusal is a `GitDomainError`, "'<name>' can't be
used as a branch name: <rule>. Use letters, numbers, dashes and slashes.". A name the analyst
types (create, adopt-create, undelete under its own or another name) also passes
`_assert_new_branch_name` first: the same rules, then `git check-ref-format --branch` as the
authority, a name it refuses that the rules let through answering "git does not accept it as
a branch name". It then refuses, on every platform, a name with a part Windows keeps for a
device (`is_windows_reserved_filename`: `con`, `aux.v2`), "'<name>' can't be used as a branch
name: Windows keeps '<device>' for a device, so the branch could not exist there. Choose
another name.", and a name that cannot exist beside a local branch (`_branch_in_the_way`): git
keeps each branch as a file under `refs/heads/`, so a branch named as a folder of the name or
of its ledger (`motor` for `motor/renewal`), or one inside a folder either would be
(`team/home` for `team`), compared in any letter case as git on Windows and macOS keeps those
files, answers "'<name>' can't be used as a branch name: the branch '<existing>' exists, and
git can't keep both it and '<name>', since one would be a folder of the other. Choose another
name." Both refusals say "another name", which is what opens the branch manager's name box
for an undelete. A branch of exactly the name is each caller's "already exists". Un-archive
refuses a branch in the way of the restored name the same way ("Cannot restore '<name>':
… Delete '<existing>' first: Deleted keeps it, to restore under another name."), and
`_free_pair_name` (archive's `archive/<X>`, Spin off's aside) passes over a dated candidate
with a branch beneath it and refuses, naming it, a branch named as a folder of the stem
(`archive`), which is in the way of every candidate. All of these run before any git command
can fail on the name.

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

**Domain import direction.** `_git_lock` and `_git_state` are leaves, and `_git_core`
imports only them. `_git_operation` imports the core and the clone state. Setup,
transactions, read models, history, remote synchronization, and archive lifecycle
may import those and narrowly import one another in the direction documented by the
control flows below; they never import the `_git` facade. `_git.py` imports domain modules
only to re-export their stable surface. This keeps the graph acyclic and prevents a second
subprocess or clone-state owner from appearing during future work.

**Process boundary.** `_git_core.py` is the sole module that imports or invokes
`subprocess` for Git, in the Git modules and everywhere else: a consumer outside the Git
component, such as the container deploy target's image tag, calls the core too, and a
repository-hygiene test rejects a Git argument list in any other subprocess-importing
module. Ordinary local commands use `_run_git`, `_run_git_ok`, or `_run_git_rc`.
Commands that need an explicit timeout, non-interactive remote environment, replacement
decoding, or binary output use the overloaded `_run_git_process` adapter. The adapter
returns an immutable typed result (`str` output by default, `bytes` when `binary=True`) and
translates only `subprocess.TimeoutExpired` into the core-owned
`_GitProcessTimeoutError`; operating-system and Unicode failures retain their native types
so each domain preserves its established fail/degrade policy. It launches through
`_run_process_group` with closed stdin, and at the timeout kills every process the command
started before draining the output pipes (`_communicate_until`). On POSIX the processes share
a new session, so none has a controlling terminal, and `killpg` kills its process group. On
Windows git starts suspended (`CREATE_SUSPENDED`), joins a job object of its own
(`_job_holding`) and only then runs, so everything it starts belongs to the job, a child whose
parent already exited included, and `TerminateJobObject` ends them all. The job is not killed
when its handle closes, so a credential cache git starts outlives a command that finishes;
a git process the job cannot hold is killed before it runs, an `OSError` like a launch
failure. A transport's children therefore never outlive the deadline or hold its pipes open,
which Windows' pipe reader threads would otherwise wait on with the repository lock held. Text output is decoded as UTF-8 with the caller's error policy and universal newlines.
Domain modules never receive the `subprocess` module or construct a raw process call
themselves.

**Ref moves (`_git_core.py`).** Every git command the core runs is built by `_git_argv`,
which classifies it (`_moves_local_refs`) before anything starts. The refs that count are
HEAD and everything under `refs/` but `refs/remotes/`: local branches, the `version/`
labels under `refs/tags/`, and the trash pins under `refs/haute/`. Remote-tracking refs are
git's copy of another repository, which every fetch replaces, so a fetch or push that writes
only those, or a clone, moves none. `update-ref`, `branch`, `checkout`, `switch`, `merge`,
`reset`, `commit`, `rebase`, `cherry-pick`, `revert`, `pull`, `am`, `stash` and `worktree`
move refs; `symbolic-ref` with a new target or `--delete`, and `tag` other than a listing
(`-l`, `--list`, `--points-at`, `--merged`, `--contains`), move them. A `fetch` moves none
only when it follows no tags (`--no-tags`), applies no configured mapping (`--refmap=`)
and names every destination, each under `refs/remotes/`: by default git follows tags into
`refs/tags/`, and a remote's configured fetch refspecs, which may name any ref, update refs
beside the ones a command names unless `--refmap=` empties them. Its first operand is the
repository, a bundle's path on Windows holding a colon, and only the operands after it are
refspecs. Every fetch the core runs takes that form (`SAFE_FETCH`: `_fetch_refs`,
`fetch_bundle_refs`, the expected default's fetch): branches into
`refs/remotes/<remote>/`, and version labels into `LABELS_NAMESPACE`
(`refs/remotes/haute-labels/<remote>/version/`, `label_refspec`), beside the branches and
outside the clone's own labels. Read-only and index-only commands are
named one by one, and a subcommand the core does not know raises `GitError` naming it, so a
new kind of command is classified before it can run. A command that moves a ref raises
`GitError` ("git <subcommand> would move a ref outside a recorded operation") unless it runs
inside `_ref_moves_permitted()`, which only `_git_operation`'s steps and `recover` enter, or
is the one `commit` inside `_save_commit()`, which only `commit_save` enters. A structural
test holds both uses, and that `"git"` heads an argument list only in `_git_argv`.

**Remote environment (`_remote_env(cwd)`).** Every deliberate remote command, best-effort
or strict, runs with `GIT_TERMINAL_PROMPT=0` and `SSH_ASKPASS_REQUIRE=never`. When the
analyst has configured no ssh transport (`_ssh_command_configured`: `GIT_SSH_COMMAND` or
`GIT_SSH` in the environment, or `git config --get core.sshCommand` at the command's working
directory), it sets `GIT_SSH_COMMAND` to `ssh -oBatchMode=yes -oConnectTimeout=10`. A
configured transport is left as Git would run it, in Git's precedence, with nothing appended.

**Per-clone state files** (`_git_state.py`), all siblings under `<project_root>/.haute/`:
`state.json` (`{"workingBranch": str}`), `prefs.json` (flat dict, currently one key:
`skipSwitchConfirm`), `pushed.json`
(`{"<remote>/<ref>": sha}`), `trash.json` (`{entry_id: {branch, branch_tip, ledger_tip,
working_pin, ledger_pin, was_archived, deleted_at}}`, capped at `_TRASH_MAX_ENTRIES = 20`,
insertion order = recency). An entry id is `<branch>@<UTC YYYYMMDDTHHMMSSZ>`, with `-2`,
`-3` and so on on a clash, and its pins are `refs/haute/trash/<entry_id>/working` and
`/ledger`. An entry written before ids were introduced is keyed by the bare branch name,
with no `branch` field. It is read with that name as its branch and its pins at
`refs/haute/trash/<name>` and `refs/haute/trash/<name>-save`. `operation.json` is the
record of a git operation in progress (`_git_operation.py`): `{operation, at, initial,
steps: [{kind, sets}], commit_point, files, wipes_caches}`. *operation* is one of `restore`, `catch-up`,
`select-branch`, `open-ledger`, `seed`, `create-branch`, `spin-off`, `archive`, `delete`,
`undelete`, `unarchive`, `repair`, `milestone`, `milestone-hooks` and `adopt-clone`, which
`operation_words` says as "Restoring a version", "Catching up", "Choosing a branch",
"Opening the working branch", "Making the first version", "Creating a branch", "Spinning
off a copy", "Archiving a branch", "Deleting a branch", "Restoring a deleted branch",
"Restoring an archived branch", "Repairing a branch", "Committing a milestone", "Running
the project's commit hooks" and "Opening the stored project". *initial* maps every slot the
operation changes, and HEAD and every branch HEAD names in any of its states whether the
operation changes them or not, to its value before the first step. Each entry of *steps*
gives its *kind* (`refs`, `checkout`, `fast-forward`, `files`, `head`, `branch`, `trash`
or `call`) and the slots it *sets*, in order, so the state after step *k* is *initial*
overlaid with the first *k* steps, and the steps whose kind writes files (`checkout`,
`fast-forward`, `files`) say which commit the files hold after each. A slot is `HEAD` (`"ref: refs/heads/<name>"` or a full SHA), a full ref name (a
full SHA, or `null` for no ref), `branch` (the working branch `state.json` records, or
`null`) or `trash:<entry id>` (the tombstone, or `null`); a step that sets none runs
something between two others (the hooks). *commit_point* is the number of steps after which
the operation goes forward, *files* the paths it writes, and *wipes_caches* whether it
clears `_VOLATILE_ARTEFACTS` once its files are in place. A record that cannot be read is
still an interruption, of an operation `read_operation` reports as `unknown`. Every writer holds the repository mutation lock, stages a complete UTF-8
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
`.env`, `.haute/`, `impact_report.md`, `test_quotes_report.md` (the report `haute app build`
writes), `.haute_cache/`, `mlruns/`, `/files/`,
`/.cache/`, `.haute_build/`, `__pycache__/` and `.venv/`. `/files/` and `/.cache/` are
anchored to the project root, so the pipeline folder's `files/` stays versioned; an
unanchored `files/` or `data/` line in an existing project ignores it, and the project
fixes that line by hand.
`ensure_gitignore_guards(project_dir)` creates `.gitignore` with those lines when absent or
appends only missing exact lines under a `# Haute` block. Existing non-UTF-8 bytes are decoded
with replacement for membership testing. It deliberately does not ignore `*.haute.json`,
because pipeline position sidecars belong on the save ledger.

**`GitWorkingBranchResponse.state`** (computed by `working_branch_status`) is one of nine
literal values: `"git-unavailable"` (no Git binary on PATH; distinct from
`"no-repository"` so the UI does not offer init), `"no-repository"` (there is no Git
repository), `"blocked"` (a repository is there, but a probe raised `GitBlockedError`: a
folder git does not trust or settings it cannot read; `errors` holds its one message, and
the UI offers no init), `"interrupted"` (`.haute/operation.json` exists: an operation stopped
part-way, and `errors` holds one line naming it and up to five of its files, "<operation>
stopped part-way: <files>." or "<operation> stopped part-way." when it writes none; read
before every state below), `"unset"` (repository present, attached HEAD, no working branch recorded),
`"detached"` (HEAD has no branch; `head_sha` supplies the accurate commit context),
`"invalid"` (Git metadata or the recorded pair is missing / ineligible /
invariant-violating), `"divergent"` (attached HEAD is on neither the recorded branch nor
its ledger), or `"ready"`. This read is total for missing and invalid repository metadata;
transport/server failures remain non-200 responses.

**`GitWorkingBranchResponse.broken_pairs`** lists, in every state once a repository is
present, each pair a crash split (`_broken_pairs`, read from the same one `for-each-ref`
listing as `eligible_branches`), as `GitBrokenPair {kind, ledger, working}`. A ledger is a
local branch `<W>-save` whose `<W>` does not exist, `<W>` being a name a working branch may
have (protected names and names git cannot use are left out).
- **`half-archived`:** an archived working branch the old two-step archive moved without
  its ledger stands beside it. That is `archive/<W>`, or the dated `archive/<W>-<YYYYMMDD>`
  or `archive/<W>-<YYYYMMDD>-<n>` the archive chose when the name was taken, with no ledger
  of its own and history the ledger continues (their merge base has the archived tip's tree,
  as `check_invariants` requires of a pair). Exactly one such branch: `working` names it.
- **`orphan-ledger`:** no such branch, or more than one. `working` is the missing `<W>`.

**HTTP contracts.** Every handler is synchronous `def`, so FastAPI runs git subprocess work
in its thread pool. Request bodies are the named Pydantic models; omitted fields take the
defaults shown. Query bounds are enforced by FastAPI and invalid input uses its standard 422
validation envelope. The `/api/git/storage/*` endpoints hosted in this router and the post-commit push enqueue on `POST /api/git/commit` are owned by [hosted-project-storage](../hosted-project-storage/low-level.md). Every route that holds `pause_watcher()` does so through `_rewriting_tree(background)`, which lists the pipelines on disk first and publishes `git.tree.changed` on `default_bus`, with that list as `pipelines_before`, as a background task once the route has answered successfully. A listing that fails (the configured pipeline missing or damaged, which a restore or a switch may be repairing) is logged and sent empty, and the action goes ahead. These are working-branch selection, branch creation, restore-version, archive, delete, fast-forward and branch-away. The server then broadcasts what is on disk ([server-api](../server-api/low-level.md)).

| Method and path | Input | Success response |
|---|---|---|
| `GET /api/git/working-branch` | None | `GitWorkingBranchResponse` (nine-state readiness contract above, including `head_sha` when resolvable; augmented via `_with_storage_state` with durable-storage fields `storage`, `storage_remote`, `storage_forked_from`, `sync`, and `storage_bind` owned by [hosted-project-storage](../hosted-project-storage/low-level.md)) |
| `POST /api/git/working-branch` | `GitSetWorkingBranchRequest {branch,create=false,settle=null}`; files off version history are 409 `unrecorded_files`, and *settle* is the choice for them ("Files changed outside Haute" above), as for the archive, branch creation, fast-forward and branch-away requests | `GitSetWorkingBranchResponse {working_branch,state,last_save_sha?}` |
| `POST /api/git/interrupted/retry` | None | `GitWorkingBranchResponse`, the readiness once `retry_interrupted` has finished the stopped operation or put it back and removed its record; files still held are 400 `GitFilesInUseError`, a file changed since it stopped is 400 naming it, and refs no step leaves are 400 `GitTransactionError`, the record kept in each case |
| `POST /api/git/restore-version` | `GitRestoreVersionRequest {sha}` | `GitRestoreVersionResponse {sha,short_sha,restored_from,working_branch}`: `sha` is the new save on the ledger, `restored_from` the full SHA of the version restored |
| `POST /api/git/identity` | `GitSetIdentityRequest {user_name,user_email,set_global=false}` | `GitSetIdentityResponse {user_name,user_email,scope}` |
| `POST /api/git/capture` | `GitCaptureRequest {paths,working_branch=null}`, *working_branch* the branch the tab shows, refused 409 `branch_changed` as the pipeline save is (`refuse_another_branch`): at least one file, relative to the project in POSIX form, as writes reported it `uncaptured` | `SaveCapture {warnings,git_sha,identity_required,uncaptured}`: the paths committed on the ledger as they are on disk, through the save capture, under the repository lock; a path `_artifact_paths.ordinary_path_problem` refuses (absolute, a drive, `..`, `.git`, `.haute`, a link on the way) is 400, nothing captured |
| `POST /api/git/commit` | `GitCommitRequest {message,version_label=null,allow_fork=false}` | `GitCommitResponse {sha,short_sha,working_branch,version_label?}`; would-fork is 409 |
| `GET /api/git/milestones` | Query `limit=20` (1..500), `branch=null` | `GitMilestonesResponse {working_branch?,entries:[GitMilestoneEntry...]}` |
| `GET /api/git/graph` | Query `limit=50` (1..500) | `GitGraphResponse {working_branch?,order:[],branches:[GitGraphBranch...]}` |
| `GET /api/git/milestones/{sha}/saves` | Commit SHA path | `GitLedgerSavesResponse {saves:[GitLedgerSave...]}` |
| `GET /api/git/pending-saves` | Query `branch=null` | `GitLedgerSavesResponse` |
| `POST /api/git/archive` | `GitArchiveRequest {branch,settle=null}` | `GitArchiveResponse {archived_as}` |
| `DELETE /api/git/branches` | `GitDeleteBranchRequest {branch,confirm=false,seen}`: *seen* is `GitBranchSeen {tip,ledger_tip,is_current,is_checked_out}`, the branch as the confirmation showed it | `GitDeleteBranchResponse {status="deleted",branch,trash_id}` |
| `POST /api/git/undelete` | `GitUndeleteRequest {id,as_name=null}`: a trash entry, rebuilt under its branch name or *as_name* | `GitUndeleteResponse {status="restored",branch}`, the name it was rebuilt under |
| `GET /api/git/working-branches` | None | `GitWorkingBranchesResponse {current?,branches:[GitManagedBranch {name,is_current,is_archived,has_unmerged_saves,has_uncommitted_changes,tip,ledger_tip,is_checked_out}...],deleted:[GitDeletedBranch {id,branch,deleted_at,was_archived}...]}`, `deleted` newest first |
| `POST /api/git/restore` | `GitRestoreRequest {branch}` | `GitRestoreResponse {restored_as}` |
| `POST /api/git/repair-pair` | `GitRepairPairRequest {ledger}`: a ledger readiness lists in `broken_pairs` | `GitRepairPairResponse {working,ledger}`, the whole pair's names; a ledger that is no longer part of a broken pair is 400, nothing changed |
| `POST /api/git/working-branches` | `GitCreateWorkingBranchRequest {name,at=null,move=false,switch=false,from_remote=null,settle=null}` | `GitCreateWorkingBranchResponse {working_branch,moved,switched,last_save_sha?}`; `move` and `switch` together are 400. With `from_remote` the route calls `start_from_default` (below) in place of `create_working_branch`, and `at` or `move` beside it is 400 |
| `GET /api/git/prefs` | None | `GitPrefs {skip_switch_confirm=false}` |
| `POST /api/git/prefs` | `GitPrefs {skip_switch_confirm=false}` | The persisted `GitPrefs` |
| `GET /api/git/remotes` | None | `GitRemotesResponse {remotes:[GitRemote {name,url,working,ledger}...],working_branch?}`; `working` and `ledger` are the sole per-leg divergence records and URL userinfo is redacted |
| `GET /api/git/show/{sha}` | Commit SHA path; required query `source_file` (project-relative pipeline file) | Read-only `PipelineGraph` of that file at the commit |
| `GET /api/git/commit-context/{sha}` | Commit SHA path; query `base=null` | `GitCommitContext`, with `delta_from_base` only when `base` is supplied |
| `POST /api/git/push` | `GitPushRequest {remote}` | `GitPushResponse {remote,working_branch,ledger_branch,default_branch,bootstrapped_default=false,pushed_refs=[],ci_secrets=null}`; `default_branch` and `bootstrapped_default` are required response members, `ci_secrets` is the deploy's secrets on a GitHub remote's repository ([cli](../cli/low-level.md)), and working/ledger non-fast-forward is 409 |
| `POST /api/git/ci-secrets` | `GitCiSecretsRequest {remote}` | `GitCiSecrets {repository,state,missing=[],newly_set=[],problem=null}` once the deploy's secrets the repository lacked are set from `.env`; 400 naming why when they do not apply or cannot be set ([cli](../cli/low-level.md)) |
| `POST /api/git/fast-forward` | `GitFastForwardRequest {remote,settle=null}` | `GitFastForwardResponse {remote,working_branch,fast_forwarded,labels_added}`: the branches that moved and the version labels (`2.0`, without `version/`) now held too |
| `POST /api/git/branch-away` | `GitBranchAwayRequest {remote,settle=null}` | `GitBranchAwayResponse {working_branch,set_aside_as}` |

## Control flow

**Save.** `commit_save(paths, working, cwd, message)` → `_assert_no_git_op_in_progress`
(a pathspec commit would otherwise fail with git's "cannot do a partial commit during a
merge") → `resolve_ledger(working)` (find-or-
lazily-spawn the ledger at the working branch's current tip, checkout if not already
current: the `open-ledger` operation, nothing when both are so already; only from the working
branch itself, where the checkout changes no file: with HEAD on another branch or detached,
which the analyst moved it to in a terminal or another tab, it refuses, "The project is
checked out on '<branch>' | an earlier version (a detached HEAD), not on '<working>', so
nothing was recorded on '<working>'. Go back to '<working>', or make '<branch>' the working
branch.", which a save's capture answers as its warning, so a save never checks out a branch
the analyst left; a restore refuses the same way) → each path given
git's spelling (`_git_spelling`): git on Windows and macOS ignores letter case on disk but
compares paths letter for letter, so after `rating` is renamed `Rating` it keeps `rating` and
finds nothing under `Rating/main.py`; one `ls-files --cached` of `:(icase,literal)<top
folder>` pathspecs gives the tracked names, and a path takes the tracked name of the same file,
or a new file the spelling of its nearest tracked folder, when both spellings name one place
on disk (`os.path.samefile`; for a deleted file, its folders), which on a disk that keeps case
apart they never do, so two names differing only in case stay two files → *paths* kept to those on disk or in the index (`git ls-files -z -- <paths>`), which `git add` takes, and those staged against HEAD (`git diff --cached --name-only -z HEAD -- <paths>`), a deletion or a rename's old name already staged (`git rm`, `git mv`, an editor's git panel, or an earlier `git add` before the process stopped), which the path-scoped commit records as staged: one in none of them has nothing to record, as a file a save's record names that a stopped save never created, and is left out → `git status --porcelain -- <paths>` to check anything in *paths* actually
changed (idempotent no-op returns `None`; a status git cannot answer raises `GitError`,
never reads as no change) → `stage_paths` (`git add -- <the paths git add takes>`, the
project's executable files among them, the pre-commit hook, `git add --chmod=+x`:
`haute._git_core.stage_paths` is the one way Haute stages a file, the editor's first-commit
seed included, so the hook is recorded 100755 where the file system has no executable bit) →
`git diff --cached --name-only --no-relative --no-renames -z HEAD -- <paths>` re-check (`--no-relative`: names from the repository's root whatever `diff.relative` says) (returns `None` when no path is listed, reconciling a stale index entry that reported a spurious modification; the listed paths, in full and as git spells them, are what the commit takes and the default message names) → `_commit_staged_paths`: a commit
of exactly those changed paths as the index now holds them, regardless of what else the user
may have pre-staged, made from an index of its own (`GIT_INDEX_FILE` at the git dir's
`haute-save.index`, removed after): `read-tree HEAD`, each changed path's entry copied
from the index with its mode (`ls-files -s -z --full-name -- :(top,literal)<name>` into `update-index -z --index-info`, every path named from the repository's root as `git diff --name-only` lists it, the project perhaps below it; a mode-0 record removes) and one
the index no longer holds removed, then `git commit -m <msg>`. `git commit -- <paths>`
builds the same index but stats a file HEAD lacks again, and with `core.fileMode=false`, as
on Windows, would commit the hook 100644. The commit is the one ref move outside a recorded
operation, inside `_save_commit()` → `_refuse_unrecorded`, a net under any other way a path can miss
git's: each path's file on disk (`hash-object`, with its filters) is compared with the
ledger's copy (`ls-tree HEAD`), and any that differ raise `GitDomainError` ("Version history
did not take the change to <files>; the next save tries again.", at most five named), which
the capture answers as a failed capture, with the warning and the files `uncaptured` → returns
the new SHA, or `None` when nothing changed. The `git commit` passes
`-c core.hooksPath=/dev/null` (`_git_core.NO_HOOKS`): a save runs none of the project's
hooks, `prepare-commit-msg` and `post-commit` included (which `--no-verify` would still
run); they run at a milestone over every save it records. `git commit` signs a save when git
is set to sign every commit; every other commit Haute makes is
`src/haute/_git_core.py::_commit_tree`, which asks `commit-tree` (which reads no such
setting) for the signature with `-S` when `signs_commits` reads `commit.gpgsign` true. Only
a setting left unset or false signs nothing: one git cannot read as a boolean raises
`GitBlockedError` (`damaged-config`), so no unsigned commit is made. A copy `_replay_onto`
makes keeps its original's author and committer through the environment, so for GPG or X.509
with no `user.signingkey`, which git would pick by that committer, `_signature` names the
analyst's own identity (`git var GIT_COMMITTER_IDENT`, read before the copy's) as the key.
`tests/test_git_signing.py` fails any `commit-tree` named outside the git core. Every path reaches Git as a
`:(literal)` pathspec, so a filename holding `*`, `?` or `[` names only itself, as in the
unborn-repo seed. A canvas save passes no
message, so the default names the changed files; an assistant apply passes its change
headline and an assistant undo `Undo: ` and that headline, through
`SavePipelineService.save_graph_transactionally(commit_message=...)`.

An assistant apply and undo save through the same service and answer the same fields: the
applied change's record (`AssistantChangeRecord`) and the undo's response
(`AssistantUndoResponse`) are each a `SaveCapture`.

Every route that writes project files captures them through the save module's
`capture_save_in_ledger(root, rel_paths, warnings, message)`, which returns a
`LedgerCapture` (`git_sha`, `identity_required`, `uncaptured`) and answers it in the shared
`schemas.SaveCapture` fields. No repository: no capture and nothing owed. A repository
with no working branch chosen: no capture, a warning ("Changes saved, but no working branch
is chosen, so version history doesn't have them yet. Choose a working branch to keep
them."), and every path `uncaptured`. A missing identity skips the commit with a warning
and `identity_required`; a `GitDomainError` or
`GitError` is a warning, the repository and identity probes included, so a
`GitBlockedError` they raise is the warning "Changes saved; version capture failed: <its
message>", never the identity prompt or nothing owed. A skipped or failed capture names its paths in `uncaptured`, since
the pathspec-scoped commit of a later save never takes them; `POST /api/git/capture` takes
them later.

`haute ci update` and a `haute init` re-run capture through it too, with
`capture_new_files` (`haute/cli/_init_cmd.py`): of the files they generate (every generated
file, so one an earlier run wrote and nobody committed is taken too) and the other files a
re-run wrote, those Git does not track yet, in one save whose default message names them:
untracked (`git ls-files --others --exclude-standard -z` on their literal paths) or staged as
an addition against `HEAD` and never committed (`git diff --cached --name-only
--diff-filter=A`), as a capture whose commit failed leaves its files, so the next run
still takes them. Their changes to tracked files, and the workflows they prune, are left on
disk for review and the milestone's sweep. Since `commit_save` checks the ledger out, the
capture is made only while the checkout is on the chosen working branch's ledger; on any other
branch, or a detached `HEAD`, it is skipped, saying where the checkout is, so no other
branch's files are replaced. A capture made is reported with the files it took; one skipped or
failed prints why and names its files as not yet in version history, pointing at `git
status`, which lists new files where `git diff` does not. Outside a repository there is
nothing to capture.

Every public mutation entry point acquires the reentrant repository lock before its first
precondition read and holds it through Git changes, clone-state writes, and any put-back.
Nested calls such as `commit_milestone` → `commit_save` → `resolve_ledger` reuse the same
lock. Pure Git history/object readers do not acquire the engine lock, but every clone-state
reader in `_git_state.py` acquires it for the file read so it cannot overlap an atomic state
transaction.

The project's writers hold the same lock across their whole transaction:
`SavePipelineService.save` (the canvas save, a save with persisted submodels, the assistant's
Apply and undo) from its base-revision check through every artifact write, the rollback of a
failed one, stale-config cleanup and the ledger capture, and
`_pipeline_repair._commit_repair_plan` (a node-scoped save and every repair apply) from its
revision and artifact re-checks through staging, verification and rollback. Both run in a
worker thread entered after the project's `save_lock`, the order `project_write` uses; no
holder of the repository lock waits for `save_lock`, so the order cannot deadlock, and the
capture's `commit_save` re-enters the lock its save already holds. A milestone, restore or
switch that arrives during a save therefore waits for it and then sees all of its artifacts,
or, after a failed save's rollback, none of them.

**The tree fence (`_rewrite_tree(*args, cwd)`).** Every command that rewrites the working
tree runs through it, and only as a checkout or fast-forward step of a recorded operation.
The fence:
1. checks for a git operation in progress, then records `_tracked_changes` (`status
   --porcelain -z --untracked-files=no`);
2. runs the command, keeping its stderr;
3. treats as out of step any tracked change that was not there before, and every file stderr
   names as not replaced (`unable to unlink old '<path>'`) or not removed (`unable to unlink
   '<path>'`). The stderr catches a file that was already changed before a forced checkout,
   and one left behind untracked, which the status comparison cannot. With none, a non-zero
   exit is an ordinary `GitError`.

When files are out of step it raises `GitFilesInUseError`, naming the files stderr reports,
else the out-of-step ones. It puts nothing back itself: the operation it is a step of does,
from its record.

**Recorded operations (`_git_operation.py`).** A mutator builds an `Operation(name,
project_root, cwd)` once every refusal has been checked and every commit object it needs has
been made (`commit-tree`), then adds its steps in order. Each step reads the value its slots
have after the steps before it (from the live repository for a slot no earlier step set) and
is dropped when it would change nothing:
- `refs({ref: sha | None})`: one `update-ref --stdin` transaction, `create`, `update` with
  the earlier value as the expected one, or `delete`, all or nothing;
- `checkout(branch)`, `checkout_detached(sha, force=False)`: HEAD to a branch or a commit,
  writing the files, through the fence;
- `fast_forward(sha)`: HEAD's branch to *sha* with `merge --ff-only`, through the fence;
- `write_files()`: `restore --source=HEAD --staged --worktree` of the record's files (the
  restore's write after its ledger moved);
- `head(ref)`: `symbolic-ref HEAD <ref>`, no files (the hooks' pass);
- `working_branch(name | None)`: `state.json`;
- `trash(entry_id, entry | None)`: one tombstone added or removed;
- `call(fn)`: runs *fn* between two steps, setting no slot; a call is never the last step,
  since only the refs show how far an operation got.

`committed()` puts the commit point after the steps added so far; by default it is after the
last. `run()` with no steps does nothing. Otherwise it refuses while another operation is in
progress in the thread, or a record is there (`GitInterruptedError`), puts HEAD and the
branches it names in *initial* (`_pin_head`), so the commits the files come from are the
record's and a branch moved after a stop is found, and works out *files*. The files hold
HEAD's commit before the first step (the empty tree for an unborn HEAD), then HEAD's commit
after each step that writes files, unchanged by one that does not (`_worktree_commits`): a
step that moves HEAD or a ref alone, as the hooks' pass does, names no file. *files* is
every path that differs between the first of those commits and any later one, less every
path not in step when the record is written, a tracked change (the analyst's own edit) or a
path the first commit lacks that is on disk (an untracked or ignored file). It then writes the record, performs
each step inside `_ref_moves_permitted()`, wipes the caches when the operation does, and
removes the record. On an `Exception` it calls `recover` with *failed_step*, the step
that raised. That step is unfinished, whatever its git command did before failing (a
checkout or fast-forward the fence refuses has moved its ref already): an operation whose
failing step comes before its commit point is put back before the call returns, and one
whose failing step is at or past it keeps the record. The original failure is re-raised,
from whatever `recover` raised when that could not finish. A stop that is not an `Exception` (the process killed, or
interrupted from the keyboard) leaves the record as it is.

`recover(project_root, cwd, failed_step=None)` is the one routine that acts on a record, for
the caught failure and for Retry:
1. It reads every slot live and finds the steps whose state equals them all. With none,
   the refs moved after the operation stopped: `GitTransactionError`, the record kept.
2. For Retry (no *failed_step*), the last such step at or past the commit point goes forward
   from there to the last step; before it, back from the first such step to the first
   state. For a caught failure, *failed_step* decides:
   before the commit point back to the first state, at or past it the record is kept and
   nothing more is done.
3. Each of *files* on disk must hold one of the versions the commits the files hold in the
   record's states give it (`hash-object --stdin-paths`, with the path's filters); one that holds neither was
   changed after the operation stopped, and is refused by name ("<files> changed after
   <operation> stopped. Move the changes aside, then choose Retry."), nothing changed. A file
   git cannot read is left to the check in step 6.
4. It walks the slots there one recorded step at a time (`_walk`), undoing the steps in
   reverse order going back. Each step sets one kind of slot, so each transition is one
   `update-ref --stdin` transaction expecting the values of the state it leaves, one HEAD
   update (`symbolic-ref`, or `update-ref --no-deref` for a commit), or one write of the
   recorded working branch or of a tombstone (`_apply_transition`): a recovery that itself
   stops or fails leaves a state of the record, and the next Retry goes on from it. A git or
   file error here raises `GitTransactionError`, the record kept.
5. It writes *files* from HEAD: `restore --source=<HEAD's commit, or the empty tree for an
   unborn HEAD> --staged --worktree --pathspec-from-file=- --pathspec-file-nul` for each one
   the index or HEAD's tree holds, and removes one on disk that neither holds, a leftover of
   the operation; git's own words are logged when the restore fails.
6. It checks the result: every slot at its target, and each of *files* in step, tracked and
   unchanged where HEAD's tree has it, and neither indexed nor on disk where it does not.
   Files that are not raise `GitFilesInUseError`, the record kept.
7. It removes the record, and wipes the caches when it went forward in an operation that
   does.

**Interrupted (`.haute/operation.json`).** While the record exists, readiness answers
`"interrupted"`, and every `_serialized_mutation` refuses with `GitInterruptedError` once it
holds the lock: Commit, Restore, a save's capture, a switch, a catch-up, a push and every
other git action. `retry_interrupted(project_root, cwd)` (`_serialized_while_interrupted`)
calls `recover` with no *failed_step*, so the refs decide which way it goes. With no
record it does nothing. Its route runs under `pause_watcher()` and announces the tree change
like the other paused routes.

**Milestone.** `commit_milestone(message, project_root, version_label, cwd, allow_fork,
pipeline_folder)` → reads the recorded working branch from `_git_state.read_working_branch` →
refuses a git operation in progress (`_assert_no_git_op_in_progress`) →
unless `allow_fork`, calls `divergence_state(working)` (local-refs-only, no fetch) and raises
`GitMilestoneForkError` if the leg is `"behind"` or `"diverged"` → before the merge,
`_residual_tracked_changes` enumerates every modified path already tracked by Git, and
`_pipeline_folder_new_files(pipeline_folder)` adds every new file under the pipeline folder
(`git ls-files --others --exclude-standard -z` on its literal pathspec). That sweep takes
only the names `_project_layout.versioned_name` accepts, the rule the Files panels show and
take: a path with a dot-name part (`.env.local`, `.DS_Store`, an editor's lock file,
`.ipynb_checkpoints/`, `.haute_build/`) or a `__pycache__` part, or a `*.pyc` file, stays
out even where `.gitignore` lacks it, and a file `.gitignore` ignores stays out. So does an
atomic write's staging file (`src/haute/_file_ops.py::is_staging_name`, `<stem>.<8 hex>.tmp`),
which a writer killed before its rename leaves beside its target. The route passes `pipeline_folder` from
`_project_layout.pipeline_folder`; `None` (no pipeline folder below the root) adds nothing.
Any swept path inside the pipeline folder that is a file larger than
`VERSIONED_FILE_LIMIT_BYTES` raises `GitDomainError` naming it, its size and the root's
`files/data/` as the place for it, before anything is committed. A non-empty sweep is recorded in
one final ledger save with the fixed message `"Updated project files"`, without hooks; a new
file outside the pipeline folder is never included → `_run_commit_hooks` runs the project's
`pre-commit` and `commit-msg` hooks, the ones a `git commit` of the milestone would run, when
either is installed where git looks for it (`git rev-parse --git-path hooks/<name>`, which
follows `core.hooksPath`). The milestone's saves are already on the ledger, so for the hooks
HEAD names the working branch (`symbolic-ref`, no checkout; the `milestone-hooks` operation,
whose steps are HEAD to the working branch, the hooks, and HEAD back to the ledger) and a
scratch index
(`GIT_INDEX_FILE`, `read-tree` of the ledger) holds the ledger's tree: the milestone's changes
are what is staged, and the real index and files are untouched. Each hook runs through `git
hook run --ignore-missing` (git 2.36 or later; an older git with a hook installed is refused
with "This project has a <hook> hook, and running it needs git 2.36 or later. Update git,
then commit again."), bounded by `_HOOK_TIMEOUT_SECONDS` (300 s). A hook that exits non-zero
refuses the milestone with "The project's <hook> hook refused the milestone: <the end of what
it printed>", nothing recorded, except a `pre-commit` refusal whose output holds ruff's
concise diagnostics (`<path>:<line>:<column>: <code> <message>`, paths relative to the
repository's top level), which `haute._lint_refusal.explain_lint_refusal` says instead: "the
project's pre-commit hook found <n> problem(s) to fix first:", one line per problem (at most
five, the rest counted) naming the node whose decorated top-level function on `pipeline` or
`submodel` holds the line, or the file and line outside one, with the line quoted (cut to 60
characters) and ruff's message and code, then "Change each line, save, and commit again. To
keep a line as it is, end it with `# noqa: <code>` (its code). More: <docs URL>"
(`REFUSAL_GUIDE_URL`); output holding no such line, another hook's or ruff failing to run,
keeps the generic refusal; `commit-msg` gets the message in a file and may rewrite it,
and a message it empties is refused. A hook that refuses, or anything else that ends the
hooks with an `Exception`, has the operation put HEAD back on the ledger; a server that
stops in them leaves the record, and Retry puts HEAD back. The scratch files are removed in
a `finally`. What `pre-commit` left changed
on disk, swept by the same rules, is recorded as one more save without hooks, "Changes made by
the project's pre-commit hook" → `merge_to_working`
validates the message (non-empty, no C0 control characters — a stray record-separator would
corrupt the ledger-save parser's delimiter), runs `check_invariants` (see below) and raises
`GitDomainError` on any violation, computes `merge_base(working_tip, ledger_tip)` and raises
if it already equals the ledger tip ("no new saves"), then requires
`refs/tags/version/<label>` to satisfy Git's complete full-ref format (invalid labels are a
user-facing `GitDomainError` rather than a raw plumbing failure), rejects any duplicate tag
before mutation, and builds the merge purely via
`commit-tree <ledger's tree> -p <working_tip> -p <ledger_tip> -m <message>`. With a label,
`hash-object --literally -t tag` writes the fully Haute-constructed annotated-tag payload
without invoking platform-dependent tag fsck. The `milestone` operation's one step is then
one `update-ref --stdin` transaction that applies the working-branch CAS and, with a label,
the tag-ref creation atomically — no checkout or index,
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
  `at=<sha>` → that commit) and classifies it.
  - **Milestone:** on the current branch's first-parent spine.
  - **Pending save:** an ancestor of the ledger tip and not of the working tip.
  - **Folded save:** an ancestor of the working tip off the spine.

  Anything else is refused: "You can only branch from a milestone or a save on the current
  branch." For a save, `_crystallize_milestone(anchor, save, name)` synthesises an
  anchoring merge commit (parents `[anchor, save]`, the save's tree), so the new branch
  opens at a clean milestone. For a pending save the anchor is the working tip. For a
  folded save it is the first parent of the oldest spine milestone the save is an ancestor
  of (`_folding_anchor`). Then:
  Each way is one `create-branch` operation, put back whole when it fails.
  - `move=False, switch=False` (default): one ref transaction creating `name` and
    `ledger_name(name)` at the base; current HEAD and branch untouched.
  - `switch=True`: that transaction, then a checkout of the new ledger and the recorded
    working branch set to `name`, the current pair's refs untouched. It first refuses a git
    operation in progress, and files off version history (`_assert_way_clear`, below), since
    the checkout replaces the files.
  - `move=True`: only legal at the latest milestone or a pending save (not an older
    milestone or a folded save), and not with `switch`, which it implies. Refuses if the spawning ledger's later commits are already published on
    ANY remote and the rewind would orphan them (checked per-remote via `_is_ancestor`),
    steering the user to a parallel fork instead. Otherwise: computes the new ledger tip
    (reuse the pending chain, or `_replay_onto` the crystallized base via `commit-tree`
    per-commit, preserving author/committer identity and dates through `GIT_AUTHOR_*` /
    `GIT_COMMITTER_*` env vars). Before replay, every to-be-relocated commit must have
    exactly one parent; an external merge is rejected because linear replay would destroy
    topology. The operation then creates both new branches in one transaction and checks
    out the new ledger (its tree matches old HEAD's, so uncommitted edits carry across
    untouched), rewinds the spawning branch's ledger to the fork point, and records the new
    working branch.

**Open tabs and the branch.** The server's branch watcher (`server._branch_watcher`) watches
the git directory's `HEAD` (`rev-parse --absolute-git-dir`, not recursively), which every
checkout rewrites, a terminal's `git switch -c` included, which changes no file the file
watcher sees, and which that watcher skips with `.git`. `_tell_branch_change` reads the
checkout (`symbolic-ref -q HEAD`, else HEAD's commit) and the recorded working branch, and
publishes `git.branch.changed` (`{checkout, working_branch}`) when they differ from what it
last told; its first reading, at startup, is the baseline. A change while a git route holds
the watcher pause is skipped there and told once the route has answered (`_broadcast_tree_change`
calls it), so a tab never reads an operation half done. A project that has no repository
when the server starts is not watched. Each tab then reads its readiness again
([frontend-git-ui](../frontend-git-ui/low-level.md)). Each of the project Save's writes names
the branch the saving tab shows, and `routes._save_pipeline.refuse_another_branch` refuses
it, under the repository lock and before anything is written, when the project has moved:
the capture of files left uncaptured (`POST /api/git/capture`), the workbench form's save
([workbench](../workbench/low-level.md)) and the pipeline save
([server-api](../server-api/low-level.md), `POST /api/pipeline/save`). The working branch the
clone records is compared without git, so another one recorded refuses even in a repository
git refuses to work in; there the checkout alone is not checked, and the save lands on disk
with its capture's warning. The editor reads the branch once, when Save is pressed (before a Save queued behind another waits its turn), and names
it in all three writes; a capture refused as `branch_changed` ends the Save before the form
or the pipeline is written.

**Files changed outside Haute (`_assert_way_clear`, `GitUnrecordedFilesError`,
`settle_unrecorded`).** Every operation that replaces the project's files runs one check
before its operation is recorded: a switch (`set_working_branch` to an existing branch, the
destination its ledger tip, or its working tip before the ledger spawns), opening a new
branch (`create_working_branch(switch=True)`, the base), Catch up (when the ledger moves, the
remote ledger's tip), Spin off a copy (the adopted ledger's tip) and an archive or an
unconfirmed delete of the current pair (its last milestone). The files it names are every
tracked file that differs from HEAD (`_tracked_changes(with_sources=True)`, a CSV saved in
Excel among them, and both names of a staged rename, whose source's removal is part of it)
and every file on disk, untracked or ignored, where the destination has one that HEAD's tree
lacks (`diff --diff-filter=A HEAD <destination>`, which git's checkout would refuse or
overwrite). An operation leaving the branch the files were changed on (a switch, opening a
new branch, the archive or delete) is checked always; one staying on it (Catch up, Spin off)
only when the checkout changes HEAD's commit. Adopting the branch the project is already on
replaces nothing and is let through, as is Create & Move, which carries uncommitted edits by
design, and a confirmed delete, which discards them. Any file named raises
`GitUnrecordedFilesError` carrying every file, worded "<files> has/have changed on disk
outside Haute since the last save. Before <switching to 'other' | opening '<name>' | catching
up | spinning off a copy | archiving '<X>' | deleting '<X>'>, record it/them as a save on this
branch, or put it/them back as version history has it/them." (five named, the rest counted),
which the failure answers make 409 `unrecorded_files` with `files` and `token` beside the
message. *token* (`_unrecorded_token`) is a digest of what the files were when the analyst
was asked: the branch the checkout is on and, for each file, its blob on disk and at HEAD.
The five routes' requests, and the hosted parent catch-up's (`GitUpstreamPullRequest`), take
`settle: GitSettleUnrecorded {choice: "record" | "put-back", files, token}`, the analyst's
choice for those files: `_rewriting_tree(background, settle)` refuses a path that is not an
ordinary one inside the project (400), then, under the repository lock the operation
re-enters, calls `settle_unrecorded`, which first refuses, touching nothing, files whose token
differs ("<files> has/have changed since you were asked about it/them, so nothing was
recorded or put back. Look again, then retry.": another tab recorded them, switched branch
or edited them while the question was open), then records them as one save on the
working branch with the sweep's message, "Updated project files" (a working branch and an
identity required, each refused in words, and a file over the history limit refused by
name), or puts them back: `restore --source=HEAD --staged --worktree` for those HEAD has, and
for one it lacks the staged addition undone and the file removed. Only the named files are
touched; the operation then checks the way again and names any file changed since. Files the
settlement changed are announced (`git.tree.changed`) although the operation after it fails,
since the watcher was paused and no background task runs. A
recorded save before Catch up is a save the remote lacks, so the catch-up refuses as for any
local save and offers Spin off a copy, which keeps it on the set-aside line; putting the
files back lets it complete.

**The volatile wipe (`_wipe_volatile_artefacts`).** Restore, Catch up and Spin off a copy
wipe `_VOLATILE_ARTEFACTS` once their files are in place, so a refused or failed one deletes
nothing. The wipe is best-effort and covers only reconstructable caches: the root's
`__pycache__`, `.haute_cache`, `.cache` and the tool caches. It never touches `files/`, the
analyst's local data and results, or `output/`, where Apply Optimisation reads published optimiser
results. `_assert_no_git_op_in_progress` checks `MERGE_HEAD`/`CHERRY_PICK_HEAD`/
`REVERT_HEAD`/`rebase-merge`/`rebase-apply` under the git dir.

**Restore a version (`restore_version`).** `restore_version(sha, project_root, cwd,
pipeline_folder)` holds the repository lock throughout. It refuses, with a `GitDomainError`
and no change, in this order:
1. The clone is interrupted, no working branch is recorded, a git operation is in progress,
   or no git identity is set
   ("Restoring a version needs a git identity. Set your name and email first.").
2. `_validate_ref_name` refuses a ref that could reach git as a flag. `resolve_ledger` then
   puts HEAD on the ledger, and `_rev_parse` refuses a ref that is not a commit ("No commit
   found for …").
3. The commit is neither an ancestor of the working tip (milestones and the saves folded in
   them) nor of the ledger tip (pending and earlier saves): "You can only restore a version
   from this branch's history."
4. `_unrecorded_project_files` (the milestone's sweep: tracked changes, new pipeline-folder
   files, the `VERSIONED_FILE_LIMIT_BYTES` refusal) is empty and the target's tree equals the
   ledger tip's tree: "This is already the current version."

Then:
5. A non-empty sweep is committed as one ledger save, "Updated project files".
6. A path the target's tree adds against HEAD's (`diff --name-only --no-renames
   --diff-filter=A -z HEAD <sha>`) that already exists on disk refuses, naming up to five
   paths ("…would overwrite a file that is not in version history: <paths>. Move or rename it
   first."). The sweep's save stays.
7. `commit-tree <sha>^{tree} -p <ledger tip>`, message `Restored <short sha>: <target
   subject>` with a `Restored-from: <sha>` trailer.
8. The `restore` operation: its first step, the commit point, advances the ledger by CAS to
   the restore save; its second writes the record's files from HEAD (`write_files`), which
   writes the target's files, brings back deleted ones and removes ones added since, leaving
   untracked and ignored files and `.haute/` alone. It then wipes `_VOLATILE_ARTEFACTS`.

A failure writing the files, or a server that stops after the ledger moved, leaves the
ledger at the restore save, so it is not undone, and the record stays: readiness reads
`interrupted`, and Retry writes the files. A file another program holds raises
`GitFilesInUseError`. The route runs it inside
`pause_watcher()`, then calls `_project_storage.enqueue_push()` as a save's
capture does. `commit_milestone` takes its sweep from the same
`_unrecorded_project_files`.

**The project's hook in a clone.** `set_working_branch`, once its arguments are valid and
before it changes anything, installs the project's `.githooks/pre-commit` (which `haute init`
scaffolds and the seed versions) as the repository's `pre-commit` hook, executable, when the
file exists, no `core.hooksPath` is configured and git's hook path holds nothing: a clone
carries no `.git/hooks`, so its first working branch brings the hook its saves and milestones
then run. A hook already there is left alone.

**Adopt-create's start (inside `set_working_branch`, `create=True`, a born repository).**
The new branch starts at HEAD, unless HEAD is a save of a working branch's ledger off every
working branch's first-parent spine. `_branch_start` reads every working branch whose ledger
exists from `_list_branches_with_tips`.
- **On a spine:** HEAD is a milestone, and the branch starts there.
- **A pending save of `W`:** an ancestor of `W-save`'s tip and not of `W`'s. The branch
  starts at `_crystallize_milestone(W tip, HEAD, branch)`.
- **A folded save of `W`:** an ancestor of `W`'s tip off its spine. It starts at
  `_crystallize_milestone(_folding_anchor(W tip, HEAD), HEAD, branch)`.

These helpers live in `_git_setup.py` and are shared with `create_working_branch`. The
anchoring commit's tree is HEAD's, so the checkout of the new ledger at it changes no file
and carries any uncommitted edit across. The `create-branch` operation's steps are one ref
transaction creating the branch and its ledger at the start, the checkout of the ledger, and
the recorded working branch set to the new branch.

**Unborn-repo seeding (inside `set_working_branch`, `create=True`).** Only triggers when
`HEAD` has no commits. If the current unborn branch is not itself protected, it is renamed
to `main` first (so the seed root commit lands on the default branch, never on the branch
about to be created) — refusing with a domain error if a born `main` already exists
alongside an unborn HEAD (only reachable via `checkout --orphan` outside haute).
`ensure_gitignore_guards` is asserted against the repo toplevel (defence-in-depth for a
foreign `git init` repo). The index is cleared (`git rm -r -f -q --cached --ignore-unmatch
-- .`) so pre-staged content can't bypass the seed gate, then `git ls-files --others
--exclude-standard -- <_SEED_PATHSPECS>` lists what's both permitted (the pathspec allow-
list) and not `.gitignore`d (the deny gate). `_outside_environments` then drops every file
under a folder below the top level that holds a Python environment's marker
(`_ENVIRONMENT_MARKERS`: `pyvenv.cfg`, `conda-meta/history`, listed with `ls-files --others`
and no ignore rules, since a rule hiding the marker need not hide the environment's files),
whatever the folder is called; a marker at the top level is the project folder itself and
drops nothing. More than `_SEED_FILE_LIMIT` (1,000)
files left is refused with a `GitDomainError` before staging, naming the count and the
top-level folder holding most of them: "The first version of this project would hold <n>
files, <m> of them in '<folder>/'. That is more than a project holds: move that folder out of
the project or add it to .gitignore, then try again." A file left larger than
`VERSIONED_FILE_LIMIT_BYTES` (100 MiB) is refused next by `_refuse_too_large_for_history`
(`_git_core`), the check the sweep before a milestone or restore applies to the pipeline
folder (`_refuse_unversionable`): "<path> (<size>) is too large for version history, which
takes files up to 100 MiB. Move it to the project's files/data/ folder, which stays on this machine,
and try again." A larger file the pathspecs leave out is never a candidate. The matched files are added through
`stage_paths`, with `:(literal)` prefixes so a filename containing glob characters can't re-expand the pathspec, the
pre-commit hook `--chmod=+x` so the root commit holds it executable on Windows too.
The staged index becomes the root commit's tree (`write-tree`), possibly the empty one, and
`commit-tree <tree> -m "Initial commit"` makes the root commit, which runs none of the
project's hooks, as a save does not. A `commit-tree` failure whose stderr mentions missing
author identity is translated to a `GitDomainError` steering the user to set identity first;
anything else re-raises as a plain `GitError`. The `seed` operation then renames the unborn
branch (`head("refs/heads/main")`) when it is not protected, and creates the default branch
at the root commit, its commit point its last step. The *post-seed pair creation* is the
`create-branch` operation above, taken next.

A failure while seeding can leave newly appended `.gitignore` guards and index staging
changes, which leave working-tree file contents in place; a seed that finished is
deliberately permanent, whatever the pair creation after it does.

Adopting an existing pair is the `select-branch` operation: the ledger created at the
working branch's tip when there is none, the checkout of the ledger, and the recorded
working branch, each a step only when it changes something. Adopting a cloned project
(`adopt_cloned_lineage`) is the `adopt-clone` operation: one ref transaction setting the
working branch and, when the remote has one, its ledger to their remote-tracking tips, then
the checkout of the ledger, else of the working branch.

**Milestone metadata and commit context.** Log rows use NUL-delimited fields, so tabs in
external commit subjects cannot shift the SHA/message/timestamp columns. Version labels are
read once per request with `for-each-ref`. The paginated milestone endpoint defaults to its
newest 20 entries, while `commit_context` explicitly reads the complete first-parent
milestone spine so commits older than that display window are still classified and anchored
correctly. It reads the target's ancestor set in batched commands, derives fold points from
the already returned parent columns, and never launches one subprocess per milestone. The
milestones' parents come from one `rev-list --parents --no-walk=unsorted --stdin` with the
SHAs on stdin, so a branch of any length stays within Windows' 32,767-character command line.

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
remote using the noninteractive remote environment (`_remote_env`) and process launcher, with
`_FETCH_TIMEOUT_SECONDS` (60 seconds, the publication's own) as its deadline: a credential
helper may sign in before the remote answers, and Git Credential Manager's first sign-in
after a quiet spell takes longer than ten seconds. Unlike a best-effort poll, this helper must
preserve three outcomes: a successful advertisement with object refs, a successful
advertisement with zero object refs, and failure. An unborn symbolic `HEAD` without an
object SHA does not make the remote non-empty; any advertised branch or tag does. A timeout,
launch `OSError` or non-zero exit (authentication or network failure included) raises
`GitRemoteUnavailableError` with `_refresh_failed`'s message, "Could not refresh '<remote>'.
Check your connection or credentials and try again.", the words Catch up and Spin off use;
stderr goes to the `git_remote_inspection_failed` log line. Output that does not decode or
parse raises a sanitized `GitError`. Either aborts before any push.

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
refuses before mutation. Bootstrap then publishes in two pushes, in order, with nothing else
touching the network between them:

1. The default alone, through `_create_remote_default`: `git push --no-follow-tags
   --force-with-lease=refs/heads/<default>: <remote> <default_sha>:refs/heads/<default>`.
   The push that initialises a remote carries exactly one ref because GitHub was observed
   (27 October 2026) never to trigger `push` or `pull_request` workflows in a repository
   whose first push created more than one ref, atomic or not, while a repository initialised
   with one branch behaves normally; later pushes do not repair such a repository.
   `--no-follow-tags` stops a configured `push.followTags` from adding labels to this push.
   The create-only lease's empty expected value can never authorize updating an existing
   default. If a concurrent writer creates that ref at the exact validated `default_sha`, Git
   reports it up to date and bootstrap continues, because the required merge target is
   already identical. A different-SHA concurrent default fails the lease (`(stale info)`),
   answered "'<remote>' changed while Haute was publishing to it, so nothing was published.
   Push again." Every failure of this push goes through `_push_failed`, never through
   `_push_rejection`, and nothing from this call is published.
2. The working/ledger pair, with exactly the established-remote pair command below. Labels
   reachable from the pair travel with it, including labels on default commits the working
   line contains; a label reachable only from default commits outside the pair's history is
   not published.

When the default lands and the pair push fails, the remote holds only the validated default,
which is an established remote, and `GitBootstrapIncompleteError` (a `GitDomainError`) is
raised. Its hand-written message names the published default and never carries git stderr;
it describes a refusal only when the remote explicitly rejected the pair (`[remote rejected]`
in stderr), and an unconfirmed publication otherwise — a timeout, a launch failure, or any
other non-zero exit, since git also exits non-zero when the connection drops after the remote
applied the refs. A `non-fast-forward`, `fetch first` or `[rejected]` pair failure
is the structured rejection described below even during bootstrap, because a teammate's
same-named branch can only have landed after the default existed. The next Push takes the
established-remote path and completes the publication.
For a non-empty remote, the resolved expected default ref must be advertised; it is fetched
authoritatively with `--no-tags` and checked for a merge base with the local working history
and ledger when present. The existing remote default is validation-only and is omitted from the push
refspecs, so Haute never implicitly advances it. A non-empty remote without the expected
default, an unrelated history, or an inspection/fetch failure raises a domain or sanitized
git error before either pair ref is published. Haute does not configure the remote host's
repository-level default-branch setting.

`start_from_default(name, remote, project_root)` (`_git_remote.py`, a serialized mutation)
starts the working branch *name* where *remote*'s default branch is now and opens it. It
applies the new-branch name checks `create_working_branch` applies
(`_assert_new_branch_name`, `_validate_ref_name`, `_assert_eligible_working`, neither the
name nor its ledger existing), refuses an unknown remote and a git operation in progress,
then fetches the remote's namespace (`_fetch_refs`, under `_fetch_exec_lock`; a failure is
`_refresh_failed`), reads its advertisement (`_inspect_remote`; no object ref is a domain
error) and resolves the default with `_resolve_push_default`, the rule Push uses, so the
branch starts at the commit Push validates against. That commit must share history with
`HEAD` ("The remote default and local working history are unrelated." otherwise).
`_assert_way_clear(..., leaving=True)` then names files changed outside Haute, and one
`Operation("create-branch")` writes `refs/heads/<name>` and its ledger at the commit with
`labels_to_adopt(remote, tip)`, checks the ledger out and records the working branch; an
interruption is put back or finished like any other recorded operation. The local default
branch and the pair left are never written. It answers a `GitCreateWorkingBranchResponse`
with `switched=true`, `moved=false` and the commit as `last_save_sha`.

After preflight, `_tag_collisions` still checks `version/*` labels (best-effort — git's tag
rejection remains the backstop). Then, after the bootstrap's default push when there is one,
the pinned-SHA pair refspecs `<working_sha>:refs/heads/<working>
[<ledger_sha>:refs/heads/<ledger>]` are submitted with `git push --atomic --follow-tags`; the
pair push never names the default and never carries a lease. No path supplies `--force`, a
force refspec, or a lease that expects an existing object. Every push subprocess runs through
`_run_push`, with the prompt-proof remote environment and a hard `_PUSH_TIMEOUT_SECONDS`
ceiling; `TimeoutExpired` and process-launch `OSError` become `GitRemoteUnavailableError`,
"Could not push to '<remote>'. Check your connection or credentials and try again." (for a
bootstrap's pair push, `GitBootstrapIncompleteError`), after which the repository mutation
lock is released. A push's other non-zero exit, outside the structured rejection below, goes
through `_push_failed`: `[remote rejected]` (a hook or branch rule) answers "'<remote>'
refused the push, so nothing was published and your work is unchanged. Its reason, for
example a branch rule or missing write access, is recorded under incident <id>.", the
lease's `(stale info)` the message above, and anything else the could-not-push message
followed by "Git's own message is recorded under incident <id>." (`record_incident`, event
`git_push_unrecognised`, with the stderr; a refusal's event is `git_push_rejected`). On a
host that takes an atomic push the working/ledger pair is atomic, so one pair ref never
lands without the other.

A host that takes none makes git end the atomic push itself, before it sends a ref, with
`the receiving end does not support --atomic push` (`_NOT_ATOMIC`; git's messages are in
English under `_GIT_LOCALE_ENV`). `_push_pair_in_order` then publishes the same pinned
commits through `_run_push`, each push naming exactly the refs it sends
(`--no-follow-tags`, since without atomicity a tag that followed a branch could land, or be
refused, apart from it): the ledger's branch when there is one, the working branch only
after it succeeded, and last, in one push of their own, the version labels on the working
branch's history (`_labels_on`: the annotated `refs/tags/version/*` tags merged into its
commit, what `--follow-tags` carries with an atomic push). It stops at the first push that
fails and answers that push's result; each branch is added to the caller's `landed` list,
and its tip recorded (`record_pushed_shas`), as it lands, so what is published is known
even when a later push raises. A ledger push that fails is handled as a failed atomic
push is, since nothing landed. A labels push that fails leaves the pair published and
recorded, and says so (`_labels_failed`): a refusal, "Your branch and save history are on
'<remote>', but '<remote>' did not take its version labels. Its reason, for example a
label of that name already there, is recorded under incident <id>."; anything else, a
timeout or launch failure included, "Your branch and save history are on '<remote>', but
its version labels could not be pushed. Check your connection or credentials and push
again to finish.", with the incident sentence when git ended it. The next Push sends them.
A working push that fails after the ledger landed is
`_push_rejection` for a non-fast-forward, as ever; for `[remote rejected]`, "'<remote>' took
your save history but refused the branch '<working>', so the branch is not published yet
and your work is unchanged. Its reason, for example a branch rule or missing write access,
is recorded under incident <id>."; and for anything else, a timeout or launch failure
included, "Your save history is on '<remote>', but the branch '<working>' could not be
pushed. Check your connection or credentials and push again to finish.", with the incident
sentence when git ended it. During a bootstrap those two take the default's opening, "'<default>'
is now on '<remote>' as your merge target and your save history is published, but ...",
as a `GitBootstrapIncompleteError`. A bootstrap default-push rejection, including a
different-SHA default winning the create-only race, follows a safe non-409 push-failure path
and is never misreported as working/ledger divergence. A
working/ledger non-fast-forward failure (`non-fast-forward` / `fetch first` / `[rejected]`
in stderr) is turned into `_push_rejection`, which force-fetches (a rejection is
authoritative) in the namespace form, so a pair leg the remote
lacks reads as a missing tracking ref instead of failing the fetch, recomputes both legs,
and checks `_is_rewrite` per leg using the last-pushed-SHA record. The same validated working/ledger SHA snapshots used
as push sources are recorded on success via `_git_state.record_pushed_shas`, so a concurrent
local branch move can neither publish an unvalidated tip nor record a tip that was not
submitted. The one-time default is deliberately omitted because
`pushed.json` is rewrite evidence only for pair refs Haute may publish again, while an
established default is validation-only and never Haute-owned.

`GitPushResponse.default_branch` is the selected-remote-aware default used by the preflight.
`bootstrapped_default` is a required boolean response member whose model default is `false`;
it is `true` only when the call found a zero-ref remote, its default push created the default
(or found it already at `default_sha`), and its pair push succeeded. `pushed_refs` lists the
names from explicit branch refspecs, excluding annotated tags that `--follow-tags` may add; it
is not a claim that every named ref advanced. Bootstrap returns `[default, working]` plus the
ledger when that local ref exists, while an established remote returns `[working]` plus that
optional ledger. `ledger_branch` continues to name the managed ledger even when it has not
spawned and is therefore absent from `pushed_refs`. Repeating a successful push is idempotent
and reports `bootstrapped_default=false` once the remote advertises refs; so does the Push
that completes an interrupted bootstrap, since it finds an established remote.

**Empty remote (`remote_is_empty`).** `True` exactly when the strict advertisement read
(`_inspect_remote`) finds no object ref, the state in which the next Push bootstraps; an
inspection failure propagates. The editor's Push asks it before calling `push_working_pair`
to know a first publication is coming ([cli](../cli/low-level.md)); `push_working_pair`
inspects the remote again itself and owns the decision to bootstrap.

**Push destination (`remote_push_urls`).** Where a push to a remote lands: `git remote
get-url --push --all`, its `pushurl` entries when it has any, else its URL, with
`insteadOf` and `pushInsteadOf` applied; empty for an absent remote. `remote_url` is the
fetch URL. The editor's Push sets the deploy's secrets only when both name one repository.

**Adoptable remote (`remote_holds_other_history`).** Hosted binding asks whether a remote
holds history other than what this clone itself bootstraps there. The answer is `False` for
a zero-object-ref remote, and for exactly the state an interrupted bootstrap leaves: the only
object ref other than `HEAD` is `refs/heads/<default>`, where `<default>` is what the
empty-remote resolution rules above choose for this clone (using the advertised `HEAD`
target), that ref is at exactly the local default's tip, and an advertised `HEAD` object, if
any, agrees. Every other state — another branch, a tag, another tip (a descendant included),
an advertisement holding only an object-valued `HEAD` (hidden refs), or no working branch
recorded — answers `True`. It reads the same strict advertisement parse
as Push inspection (`_read_advertisement`, which `_inspect_remote` wraps), and an inspection
failure propagates: an unreachable remote never reads as adoptable.

**Fast-forward (`fast_forward_pair`).** Requires HEAD to currently be on the ledger (refuses
detached states). Once the legs are known, a catch-up whose ledger moves refuses files off
version history (`_assert_way_clear`); one of the working ref or of labels alone writes no
file and checks none. Fetches and prunes the remote's branches and
version labels (`_fetch_refs`), then aborts if that required refresh times out, cannot launch or exits non-zero
(`GitRemoteUnavailableError`, the refresh message) or shows that either managed remote leg is
missing (`GitDomainError`); transport failure and a deleted working/ledger branch have
distinct user-facing refusals. No cache
wipe, merge, or ref update may run on stale or incomplete tracking refs. It then requires
EVERY leg to be `"behind"` or already synced — any `"ahead"`/`"diverged"` leg refuses
outright (the user must reconcile via `branch_away` instead; this function never merges).
With both legs synced and no label to adopt it answers "Already up to date with
'<remote>'."; labels alone (one published for a milestone the clone holds) are a catch-up
of their one step, which writes no file and keeps the caches.
The `catch-up` operation's steps: the ledger (checked out) advances to its remote tip via
`git merge --ff-only <sha>` through the fence, then the working ref (not checked out) via a
CAS transaction, each only when that leg is behind, then one transaction creating the
version labels the remote has on the working branch's caught-up history that the clone has
no label of that name for (`labels_to_adopt`, read from `LABELS_NAMESPACE` with
`for-each-ref --merged`), so a colleague's labels appear as they did; its commit point is its
last step, and it wipes the caches only when a leg moved. Any
failure puts both refs and the files back from the record, so a file the remote deleted that
another program holds leaves the ledger, the working branch and every file as they were,
and the next catch-up, once the file is closed, removes it. A put-back that cannot finish
keeps the record, never a clean refusal or success.

**Branch away (`branch_away`).** The reconciliation path when a remote fork is detected.
It first fetches and prunes the remote's branches and version labels, aborting with
`GitRemoteUnavailableError` (the refresh message) on any transport/process failure before
choosing an aside name or mutating refs. This
authoritative snapshot lets an absent optional ledger remain a successful fetch while
removing a deleted tracking ref; an absent working branch is then a distinct domain
refusal. The `spin-off` operation detaches HEAD at the ledger's tip (no file changes), then
in one ref transaction creates a unique dated aside pair (`_free_pair_name`:
`<W>-local-<YYYYMMDD>`, then `-2`, `-3`… until it and its ledger are both free) at the
current pair's tips, moves `working`/`ledger` to the remote's tips and creates the version
labels the remote has on the adopted line that the clone lacks, then checks out the
ledger. It records the merge-base of the old and new working tips as the aside branch's fork
point (for the branch manager's back-link). If the remote has no ledger (`X2`), the local
ledger moves to the adopted working tip rather than adopting one. Any failure puts the pair,
the aside names and HEAD back from the record.

**Archive/delete active pair.** HEAD detaches at the working branch's tip, its last
milestone, through the tree fence. A confirmed delete forces it over a dirty tree; an archive
or an unconfirmed delete refuses files off version history first (`_leave_if_active` through
`_assert_way_clear`, "Before archiving '<X>'…" or "deleting"), and the tree is left as it
was. The
ledger's later saves stay with the archived ledger or the trash pins. HEAD never moves to
another branch, so the analyst is not left on a default branch's unrelated files. Archive
moves the pair with one `update-ref --stdin` transaction: it creates `archive/<X>` (or,
when that or its ledger is taken, `_free_pair_name`'s dated `archive/<X>-<YYYYMMDD>`, then
`-2`, `-3`…) and its
ledger at their tips and deletes `<X>` and its ledger with their tips as expected values.
The `archive` operation's steps are that detach when the pair is active, the recorded working
branch cleared when it is this pair, and the transaction. Un-archive (`restore_working_pair`)
is the same transaction the other way, the `unarchive` operation's one step.

**Delete / trash (`delete_working_pair` / `undelete_working_pair`).** Delete takes *seen*,
the branch as its confirmation showed it (`GitBranchSeen`: the branch's and its ledger's
tips as the listing gave them, whether it was the recorded working branch, and whether HEAD
was on it or its ledger, `is_checked_out`, the two things `_leave_if_active` reads to leave
it), and refuses, under the repository lock and before anything else it checks, a branch that
is no longer so (`_change_since_seen`): "'<branch>' changed since you chose to delete it: <the
project is on it now | the project is no longer on it | the project is checked out on it now |
the project is no longer checked out on it | its milestones changed | its saves changed>. Look
again, then retry." A checkout made in a terminal since the confirmation is a change, so a
confirmed delete never forces HEAD off a branch it did not show as checked out, discarding
the changes on disk. The listing's `has_uncommitted_changes` is the tree's for the current
branch and for one checked out. *seen* is required, so no caller deletes a branch it has not described.
Delete refuses on
unmerged ledger saves, and on the active pair's tracked changes, unless `confirm=True`. It
takes a fresh entry id, then the `delete` operation:
1. detaches HEAD at the working tip when the pair is active, forced when confirmed, and
   clears the recorded working branch when it is this pair;
2. in one `update-ref --stdin` transaction pins both tips under the entry, plain refs
   outside `refs/heads` that shield the objects from gc, and removes the working and ledger
   refs with their tips as expected values;
3. writes the tombstone;
4. when the trash is then over its cap, removes the oldest tombstones and, in one
   transaction, their pins.

Its commit point is the transaction of step 2, or the detach when a confirmed delete's
checkout discards tracked changes: those cannot be put back, so a failure after it finishes
the delete on Retry. A failure before the commit point puts the branch, HEAD and the
recorded working branch back.

`undelete_working_pair(id, as_name=None)` rebuilds the pair under *as_name* or the entry's
branch. It refuses an unknown id, a name already taken by either ref ("Restore it under
another name."), and a recorded commit that no longer resolves. The `undelete` operation
creates both refs and deletes the entry's pins in one transaction, its commit point, then
removes the tombstone; a failed transaction leaves the tombstone, so the entry stays
restorable. The restored pair
is NOT auto-adopted as the working branch. `working_branches()` lists the tombstones, newest
first, as `deleted`.

**Repair (`repair_working_pair(ledger, project_root, cwd)`).** Under the repository lock it
reads the pair again (`_broken_pair`) and refuses a ledger that is not part of a broken pair
("'<ledger>' is not part of a broken branch."). It changes refs only, the `repair`
operation's one step, so HEAD, the files and clone state stay as they are.
- A `half-archived` pair is finished as the archive would have moved it: one `update-ref
  --stdin` transaction creates `<archived>-save` at the ledger's tip and deletes the ledger
  with that tip as its expected value.
- An `orphan-ledger` gets its working branch back with `create refs/heads/<W> <milestone>`
  in one transaction. *Milestone* is the ledger's last milestone (`_ledger_last_milestone`),
  the commit its saves were recorded on. Walking the ledger's first-parent chain back from
  its tip, it is the first commit that is a merge (a milestone, or the anchoring milestone of
  a branch started at a save), has no parent (the root), or lies on the first-parent chain
  of a local branch that is not a save ledger (the default branch's commit a branch was
  started at). Those chains come from one `rev-list --first-parent --stdin` of the branches'
  tips; with no such branch there is none. Another ledger's chain is no evidence: after Spin
  off a copy the set-aside ledger shares the saves before the fork.
- What the working branch's lost milestones held stays in the ledger's saves, which read as
  pending until the next Commit folds them into one milestone. A save is never taken for a
  milestone: saves have one parent and lie on no other branch's first-parent chain. That
  includes restore saves, sweep saves and saves replayed by Create & Move.

**Historical extraction (`src/haute/_git_history.py::archive_commit` / `src/haute/routes/_helpers.py::commit_pipeline_graph`).** Bounded extraction is owned by `archive_commit`, while the parse step `commit_pipeline_graph` is owned by [server-api](../server-api/low-level.md). `ls-tree` first
enumerates the selected commit and filters to `haute.toml`, Python modules, Haute sidecars,
and files below `config/` or `prompts/`; `git archive` receives only those literal paths,
which take no stdin, so `_path_batches` splits them into batches whose command lines stay
within `_ARCHIVE_COMMAND_BUDGET` (24,000 characters, under Windows' 32,767) and every batch
is archived before any is extracted. Tar extraction is implemented with
Python-3.11-compatible regular-file/directory handling and validates the complete member list
of every batch before writing. It rejects traversal, unsupported
members, more than `_HISTORY_ARCHIVE_MAX_MEMBERS = 10_000` entries, and cumulative
regular-file size above `_HISTORY_ARCHIVE_MAX_BYTES = 64 * 1024 * 1024`, both counted over
all the batches. Malformed or
over-limit tar data raises `GitHistoryReadError`. Parsing reads only the requested
`source_file` (contained to the extracted tree); a file absent at that commit or one that fails to parse raises the same typed failure rather than returning
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
  read/merge/write transactions cannot lose sibling updates. A pipeline save or repair holds
  that boundary through its artifact writes, so a concurrent milestone never records a
  pipeline whose Python references a configuration file the save had not written yet.
- **A slow / credential-walled remote** — routine readiness, branch, history, graph, and
  remote-list reads never perform network I/O. Every deliberate remote read runs through
  `_remote_env` and `_run_process_group`: no stdin, no terminal, no prompt, and a transport
  of the analyst's own run as configured. Fetch/inspection subprocesses use
  `_FETCH_TIMEOUT_SECONDS`; the publication subprocess uses `_PUSH_TIMEOUT_SECONDS`; both are
  60 seconds, since each is an action the analyst started and is waiting on (Push, Catch up,
  Spin off a copy) and a credential helper may sign in first; at either deadline the whole
  process tree goes.
  Explicit Push inspection/publication, expected-default fetch, Catch up, and Spin off a
  copy are strict: timeout, launch failure, non-zero exit, or unreadable output refuses the
  operation and must never be reclassified as a zero-ref remote or successful refresh.
- **Remote baseline states at Push** — successful zero-object-ref advertisement is the sole
  bootstrap case; non-empty + expected default + related history publishes only the pair;
  non-empty + missing expected default and non-empty + unrelated default both refuse before
  submitting any refspec. “Empty” describes the strict preflight snapshot: a different
  branch or tag may appear afterward and coexist safely. A concurrently-created default at
  a different SHA fails the empty-value lease; one at the exact submitted SHA may be an
  up-to-date no-op and safely coexist with successful pair publication. The initialising push
  carries the default alone; the pair push's atomicity prevents a failure from creating one
  pair ref without the other, and a bootstrap whose pair push fails leaves only the validated
  default, which the next Push treats as an established remote. A host without atomic pushes
  can also be left holding the ledger, never the working branch alone.
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
On a non-zero exit it logs `git_command_failed` (full stderr) and raises `GitBlockedError`
when `_blocked_by` recognises the cause in git's message, else plain `GitError`
(sanitize-by-default); the tree fence does the same for a command that wrote no file out of
step. `_run_git_ok` (returns `(bool, str)`) and `_run_git_rc` (returns
`(int, str)`) do not raise merely for a non-zero git exit — used wherever that exit is an expected, meaningful
outcome (e.g. `merge-base --is-ancestor` exiting 1 for "not an ancestor" vs. >1 for a
genuinely unreadable object, which `_is_ancestor_cached` still raises on, to avoid
memoizing a bad answer) — but raise `GitBlockedError` for a recognised cause, which no exit
code stands for: `rev-parse --is-inside-work-tree` in an untrusted folder, or `config
user.name` beside a damaged line, would otherwise read as no repository or no identity.
Outside the Git component a caller that must go on catches it by name: training
provenance records no commit (`provenance_git_blocked` logged), and `capture_new_files`
prints the cause and that nothing was recorded. None of these three general wrappers catches a subprocess-launch
`OSError`; that propagates to the route's generic 500 handler. Deliberate remote helpers,
including the final push subprocess, explicitly catch timeout/OS failures and convert
required refresh failures to a Git error.

Push-time remote inspection is not a polling helper and never degrades. It logs unsafe raw
stderr server-side and answers a transport failure with `GitRemoteUnavailableError`'s
hand-written message, hand-authored detail for a known domain condition, and the sanitized
`GitError` path for an advertisement it cannot read. Authentication, network,
timeout, launch, or advertisement-parse failure occurs before refspec submission and leaves
HEAD, local branch/tag refs, the index, working tree, and `.haute` state unchanged; it must
not be converted into an empty-remote bootstrap. A later authoritative default fetch may
update the selected remote-tracking ref and object database before validation refuses, but
never mutates those user-owned local surfaces.

`haute._failure_answers` is the sole error-to-HTTP mapping point for git, as for every
other failure, dispatched by `isinstance` in most-specific-first order:
`GitGuardrailError` → 403 (`git_guardrail`), `GitDomainError` → 400 (verbatim message,
`git_refused`), plain `GitError` → 400 `git_failed`, "Git refused this. Its reason is recorded
under incident <id>.", git's own message logged under the incident only (`git_refusal_answer`).
A save whose capture git refuses warns in the same words. A hand-written git message whose cause
only git's stderr names (a push the remote rejected, a bootstrap the remote refused, history git
could not read) names the incident its stderr is logged under (`record_incident`).
`GitPushRejectedError` and `GitMilestoneForkError` are caught BEFORE the generic `GitError`
handler in the two routes that can raise them (`git_push`, `git_commit`) and mapped to 409
with their structured payload's `model_dump()` as `HTTPException.detail`; the wire envelope
is `{"detail": <GitPushRejection|GitMilestoneFork object>}`. Anything that isn't a
`GitError` at all (e.g. a bug in this layer itself) reaches the server's unexpected-exception
handler, which logs its traceback and returns a plain 500 with the sanitized detail.

## Testing

Tests live under `tests/` and are synchronous. Workflow tests drive real git repositories in
temporary directories via the shared helpers in `tests/_git_helpers.py` (`git_run`,
`init_repo`); narrow subprocess seams are mocked only to make malformed advertisements,
process failures, and precise ref-movement races deterministic.

- **`tests/test_service_domain_boundaries.py`** — structural contracts keep the
  Git facade explicit, the domain-module import graph acyclic, subprocess
  execution in `_git_core.py`, and the shared repository lock on every
  serialized mutator.
- **`tests/test_git_ledger.py`**, **`tests/test_git_working_branches.py`**, **`tests/test_git_working_tree.py`**, **`tests/test_git_remotes.py`** and **`tests/test_git_core_commands.py`** — the primary unit suite for the Git facade and domain
  modules, organized into
  focused test classes covering: branch-category naming, a typed name beside a branch git
  cannot keep with it (beneath, above, in another case, through the ledger) and a device
  name refused by name, an un-archive, an archive and an undelete into such a name, ledger
  resolve/spawn, `commit_save` (including idempotent-no-op saves, a
  filename with glob characters committing only itself, a save during a merge refused with
  the reason and a status check git cannot answer raising), a typed branch name refused
  naming each rule and git deciding one the rules let through, the milestone sweep of the pipeline
  folder (a new file there recorded, a new file outside it, an ignored file, `__pycache__/`,
  `.haute_build/`, bytecode and the names the Files panels hide left out, and a file over the
  limit refusing the Commit by name), a milestone and a restore after `git rm`, `git mv` and
  a staged deletion outside the pipeline folder, and a file delete stopped before its commit
  recorded by the next Commit, the milestone's run of the project's hooks (the
  pre-commit hook seeing only the milestone's changes and its rewrite recorded before the
  milestone, a refusing hook refusing it with HEAD back on the ledger, a commit-msg hook
  rewriting the message), the project's tracked hook installed in a clone and an analyst's
  own hook or hooks path left alone,
  milestone merge and its
  invariant checks, git identity get/set, working-branch status across all nine readiness states,
  `set_working_branch` including a branch created from a detached pending save, folded
  save and milestone (an anchoring milestone for a save, none for a milestone), the
  capture of a save made with no working branch chosen (warned and left uncaptured) and
  in a project with no repository (nothing owed), the unborn-repo seed path and its
  gitignore-guard
  interaction (`TestSeedGitignoreGuards`, `TestSetWorkingBranchUnbornNonDefault`; a venv,
  an env and a conda environment left out of the root commit whatever their names, a
  seed over the limit refused naming its folder, and a seed holding a file too large to
  version refused naming it while a large file outside the pathspecs is left out), version
  restore (`TestRestoreVersion`: a pending save, a milestone and a folded save restored as
  one save on the same branch with later saves kept, files changed, deleted and added since
  put back, untracked files, `.haute/` and `files/outputs/` untouched and caches wiped,
  unrecorded changes and new pipeline files recorded first, the already-current,
  other-branch, overwrite, identity and in-progress refusals, and a failure writing the
  files leaving the restore recorded and the caches in place), milestone
  history and commit-context breadcrumbs, rename-preserving ledger
  expansion, the branch manager (archive/delete/undelete/restore; a name deleted, made
  again and deleted again keeping both entries, undelete under another name, the cap
  dropping the oldest entry's pins, a legacy bare-name tombstone restored, the active pair
  detached at its last milestone, an unconfirmed delete of it keeping a dirty tree),
  a delete refused when the branch was switched onto, saved to or committed since its
  confirmation, or checked out in a terminal with a change on disk (the change kept), with
  the branch, HEAD and the recorded working branch unchanged, the
  listing giving the tips a delete names, request-path no-fetch
  guarantees,
  the tree fence (`TestTreeFence`: a switch whose checkout exits 0 with a file left stale
  put back and refused by name, a file held too tightly to read leaving the clone
  interrupted until Retry, a forced checkout over a changed file it could not replace, and
  on Windows a real held file), canonical-remote resolution,
  fork/create-working-branch (parallel, switch and move, from
  a milestone, a pending save and a folded save, the switch refusing tracked changes and
  rolling back a failed checkout), archive-
  commit read-only extraction, remotes/push/fast-forward/branch-away, the milestone fork
  gate, protected-branch env-var configuration, and subprocess text-encoding. Push coverage
  includes the full unborn-repo → first working branch → explicit Push journey; default-first
  publication to a zero-object-ref remote, whose initialising receive session carries the
  resolved default alone (even with `push.followTags` set and a label on the default) before
  the pair and its labels follow atomically; a pair failure after the default landed reported
  as `GitBootstrapIncompleteError` with only the default left on the remote — worded as a
  refusal for an explicit remote rejection and as unconfirmed for a timeout, launch failure or
  lost connection — and the next Push completing the publication; a teammate's same-named
  working branch landing between the two pushes surfacing as the structured rejection with
  the working leg measured although the remote has no ledger;
  `remote_holds_other_history` answering `False` only for an empty remote or this clone's own
  default at its exact tip (a `HEAD`-only advertisement counts as content), and propagating
  an inspection failure; `remote_is_empty` answering `True` only for a zero-object-ref
  remote and propagating an inspection failure; `remote_push_urls` giving a remote's URL, its
  `pushurl` entries once it has any, and nothing for an absent remote;
  required response metadata and explicit-branch-refspec reporting; absent-ledger behavior;
  idempotent re-push; selected-remote and custom-default resolution; preservation of an
  existing related default; both outcomes of an empty-snapshot/concurrent-default race;
  pinned source-SHA refspecs and exact pair-only `pushed.json` snapshots; and refusal without
  partial publication for a tags-only or non-empty
  missing-default remote, unrelated history, malformed advertisement, remote-only tag
  auto-follow during a refused preflight,
  inspection/auth/timeout failure, or any ref rejection; an unreachable remote answered with the
  refresh message, a push transport failure with the could-not-push message, a hook that
  declines the push and a lost create-only lease each answered in words.
- **`tests/test_git_core_commands.py`** also holds each cause `GitBlockedError` names (a
  lock file, `GIT_TEST_ASSUME_DIFFERENT_OWNER`, a damaged `.git/config`, a signing program
  that cannot run) said by a save's capture warning and Commit's refusal, never `git init`
  nor the identity prompt, readiness `blocked` for the two that refuse the repository, and
  a failure with no recognised cause staying git's own.
- **`tests/test_git_working_tree.py::TestFilesChangedOutsideHaute`** — with a tracked file
  edited by hand, a switch, opening a new branch, an archive, Catch up and Spin off a copy
  each refused naming it, with no ref, HEAD or file moved and no "Save or discard"; each
  completing once it is put back, with the file as history has it; each but Catch up
  completing once it is recorded, the save on the branch it was made on (set aside or
  archived with it); a recorded save before Catch up refused with Spin off a copy; a file git
  does not track where the destination has one named, removed by a put-back and then the
  switch completing; recording refused with no working branch or no identity; a staged
  rename named by both its names and recorded or put back whole; and a Put back answered
  after another tab recorded the file, switched and edited it again refused, the other
  branch's edit kept. In
  `tests/test_git_routes.py`, the 409's `error_code` and `files`, the switch asked again with
  `settle` recording the file, a settled path outside the project refused 400, files put back
  announced although the Catch up after them fails, and the hosted parent catch-up settling
  the files before it runs. In
  `tests/test_git_operations.py`, Create & Move carrying the analyst's edit with none of it
  in its record or touched by a put-back, and a switch refusing it before any record.
- **Open tabs and the branch** — in `tests/test_git_ledger.py`, a save with HEAD on another
  branch or detached refused naming where the project is, with the checkout, the ledger and
  the file as they were; in `tests/test_save_git_serialization.py`, a save from a tab after
  another tab switched, a terminal checkout and a terminal `git switch -c`, each refused
  `branch_changed` naming both, writing nothing and leaving the checkout alone, and one from
  a tab on the project's branch recorded, a repeat of a committed save after a switch
  answered from its receipt, and a save while git refuses the repository written with the
  cause as its warning; in `tests/test_workbench_routes.py` and
  `tests/test_project_files_routes.py`, the form's save and the capture of files left
  uncaptured refused the same way, nothing written; in `tests/test_git_routes.py`, a switch through a
  route told once to the open tabs, and a `git switch -c` in a terminal told by the branch
  watcher.
- **`tests/test_git_letter_case.py`** — a save after the pipeline folder is renamed to other
  letter case, an upload into a renamed data folder, a replace of a renamed data file and a
  request naming a folder in other capitals, each recorded under git's spelling with nothing
  left changed, a switch then succeeding, the net warning and keeping a file a capture recorded
  nothing for, and two names that differ only in case kept apart where the disk keeps them so.
  CI's platform smoke runs it on Windows and macOS, where the rename is real; elsewhere a link
  under the new spelling stands for a folder that ignores case.
- **`tests/test_git_remote_transport.py`** — real remote commands over `ssh://` through a
  fake ssh script, on every platform (CI's platform smoke runs it on Windows and macOS): a
  transport configured as `GIT_SSH_COMMAND`, `core.sshCommand` or `GIT_SSH` inspects and
  publishes with nothing added to its arguments, conflicting OpenSSH options reach it as
  written, a hanging transport and its child are ended within the deadline with the refresh
  message for Push and Catch up, a child its transport left behind, still holding the
  output pipe, is ended too, and a transport asking for a password cannot wait for one.
- **`tests/test_git_improvements.py`** — focused roadmap regressions for the shared
  repository lock (including cached lookup, `git init` identity stability, and weak-registry
  eviction), concurrent real saves, lock-scoped atomic clone-state updates,
  seven-state readiness, locale-stable Git execution, NUL-delimited history fields,
  batched commit context, merge-replay refusal, network-free remote listing, targeted
  historical extraction, archive member/byte ceilings, typed archive/parse failures, and
  Windows cleanup retries; commit context over 1,000 milestones and the extraction of a
  commit with 40,000 characters of paths, every command line launched within Windows' limit,
  and the member ceiling counted across batches with nothing written.
- **`tests/test_save_git_serialization.py`** — real saves and repairs against a real
  repository, each paused between two artifact writes while a milestone or restore runs on
  another thread: the Git action cannot take the repository lock until the save is done,
  then records the complete artifact set, which loads as the saved graph; a write failure
  injected at the pause rolls back before the waiting milestone proceeds, which then records
  none of the save. It covers the full save, a node-scoped config change and a reset that
  rewrites a pipeline and its configuration.
- **`tests/test_git_operations.py`** — recorded operations, over one table of every
  operation that moves a ref (`OPERATIONS`: restore, catch-up, choosing a branch, opening the
  ledger, the seed, creating a branch from the chooser and in each of the fork's three ways,
  spin-off, archive, delete unconfirmed and confirmed over a dirty tree, undelete,
  un-archive, repair, a milestone with a label, the hooks' pass and adopting a clone). For
  each, the process is stopped after each step in turn, the record written and before the
  first step included: the record names the operation and its files, every ref, HEAD, the
  recorded working branch and the tombstones are exactly where the record says that step
  leaves them, readiness reads `interrupted` naming the operation and the files, Commit,
  Restore and a save's capture refuse, and Retry finishes the operation from a step at or
  past its commit point and otherwise puts every slot and file back, and removes the record.
  A second test fails each step in turn and catches it: before the commit point the call
  returns with every slot and file as before and no record; after it, the steps done kept,
  the record there, and Retry finishing. A recovery is itself stopped and failed at each of
  its transitions, for a stop just before and at the commit point, and the next Retry
  finishes or puts back. A third fails each step before the commit point
  after its own git command has run (the fence refusing a checkout or fast-forward that
  moved its ref), and requires every slot and file as they began and no record. A child process killed with `os._exit` at the
  restore's, the catch-up's, the spin-off's and the confirmed delete's most dangerous step
  leaves the same record and state. Further: a file holding neither version refused by
  Retry by name and kept, refs moved after the stop refused as `GitTransactionError` with
  the record kept, a branch HEAD names moved after a stop refused although the operation
  never changed it, a record that cannot be read reading `interrupted` with no operation
  named and Retry refusing it, a hook that rewrites a file and refuses leaving HEAD on the
  ledger and no record, a fetch adding no label nor a branch a configured mapping names, Catch
  up and Spin off bringing a colleague's labels as a recorded step, and Catch up bringing a
  label alone when both branches already match, the analyst's own edit and an untracked file never in a record nor
  touched by a put-back, a catch-up over a file the remote deleted that another program
  holds refused with the ledger, the working branch and the files as they were and the
  next catch-up removing it (on Windows with a real open handle), and the runners refusing
  a ref-moving command outside an operation's step, a second commit in a save, and a
  subcommand they do not know, with the structural test that only `_git_operation` enters
  `_ref_moves_permitted`, only `commit_save` enters `_save_commit`, and `"git"` heads an
  argument list only in `_git_argv`.
- **`tests/test_git_lifecycle_improvements.py`** — failure-injection coverage for
  adopting an existing pair, deleting the only active pair, partial fast-forward,
  archive state replacement, archive restore and undelete, each put back from its record,
  and a put-back that cannot finish keeping the record.
- **`tests/test_git_rollback.py`** — branch-away guard conditions, and that
  unreachable-remote push preflight and fast-forward failures correctly raise rather than
  swallow.
- **`tests/test_git_content_caches.py`** — the SHA-keyed cache layer specifically: milestone
  version-label batching, cache freshness across branch moves, subprocess call-count
  assertions (proving the caches actually eliminate the N+1 they claim to), tree-SHA
  caching, fork-credit computation, and the `_list_branches_with_tips` enumeration.
- **`tests/test_git_graph.py`** — `graph_topology` and its route: rich multi-branch
  topology, fork-attachment claiming order, the "merge parents mean saves" semantics,
  entry metadata, window truncation, and that the whole path is read-only (no HEAD/ref
  mutation observed across calls).
- **`tests/test_git_state.py`** — malformed-input fallback behaviour for every
  `_git_state.py` reader: corrupt JSON, wrong top-level type, missing file — each must
  degrade to the documented empty/default value rather than raising; deterministic thread
  coordination additionally pins lossless concurrent pushed-tip updates and atomic
  reader-visible replacement.
- **`tests/test_gitignore_guard.py`** — exact deny-list membership (including the positive
  assertion that `*.haute.json` remains tracked), file creation, idempotent byte preservation,
  append-only missing-entry repair, and non-UTF-8 input handling.
- **`tests/test_git_routes.py`** — the HTTP layer: every route's happy path, that all
  handlers are genuinely sync `def` (not `async def`, to avoid event-loop blocking),
  general-exception-to-500 handling, the failure answers' status-code mapping
  for all three error families, and that ref-moving routes correctly wrap their `_git` call
  in `pause_watcher()` and publish `git.tree.changed` once they have answered (a switch
  broadcasting the destination's document, and the name→path index seeing a pipeline the
  destination adds). The push route pins `default_branch`,
  `bootstrapped_default`, and `pushed_refs` on bootstrap and established-remote responses,
  plus refusal/error mapping without partial publication.
- **`tests/test_git_routes_pydantic.py`** — a narrower contract-pinning suite (item #74)
  asserting `_git` functions return real Pydantic model instances (not dicts), that route
  bodies don't re-wrap what `_git` already returns, and that the wire shape of responses is
  pinned, including the two required default-bootstrap response members and their false-
  by-default semantics.
- **Known gaps**: provider control-plane behaviour is intentionally outside this suite;
  tests assert git refs only and do not claim that GitHub/GitLab/another host changes its
  repository-level default-branch setting after the bootstrap push. GitHub's response to the
  bootstrap's push shape (workflows trigger in a repository initialised by a single-ref push,
  and never in one whose initialising push created several refs) was qualified by hand
  against GitHub on 27 October 2026; the suite pins the git-level shape, one ref in the
  initialising receive session, not the host's behaviour.
