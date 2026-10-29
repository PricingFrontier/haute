# Git Integration — High-Level Specification

## Purpose

Haute is used by pricing analysts who are not git-fluent. This component gives them a
version-control workflow — "save my work", "commit a milestone", "go back to an earlier
version", "push to share" — without exposing branches, merges, rebases, or conflict
resolution as raw git concepts. Every git CLI interaction in the product goes through this
one layer, so guardrails (no writing to protected branches, no force-push, no silent
merges), user-friendly error translation, and safety nets (trash tombstones, transactional
rollback paths) are enforced in exactly one place rather than scattered across call sites.
All mutations and clone-state transactions for one repository are serialized by one
logical reentrant engine critical section. A pipeline save, the assistant's Apply and undo,
a node-scoped save and every repair apply enter that same section for their whole
transaction, from the revision check through the artifact writes, rollback and stale cleanup
to the capture on the save ledger, so a milestone, restore or switch sees all of a save's
files or none of them. Pure Git-object/history reads remain concurrent,
but reads of Haute's clone-state files acquire that same repository lock so they cannot
observe a state transaction halfway through. Lock identity lookup is bounded and cached so
readiness polling does not repeatedly walk the filesystem. A stable project-path key remains
part of the critical section after `git init`, while linked worktrees additionally share
their common-Git-directory key, so the initialization transition cannot split one project's
mutations across two locks.

The stable `haute._git` import is an orchestration facade. One lower-level command core owns
every Git subprocess and repository-lock entry, while repository setup, branch-pair
transactions, immutable read models, history/archive reads, remote synchronization, and
local archive lifecycle each have a single cohesive module. Domain modules never call back
through the facade, so the split does not create hidden state or circular imports.

The component owns the full lifecycle of a "working branch": creating one, saving
incremental progress to it, promoting saves to a named milestone, forking a parallel
version line from any earlier version, restoring an earlier version as a new save,
archiving/deleting/restoring branches,
and the deliberate (never automatic) push/fetch surface that shares work with a remote.

## Scope

In scope:
- The working-branch / save-ledger branch-pair engine (create, save, commit milestone,
  restore a version, fork, archive, delete, undelete, restore).
- Guardrails: protected-branch enforcement, ref-name injection prevention, unborn-repo
  seeding, git-op-in-progress detection, and the shared `.gitignore` deny-list asserted
  before a root commit can stage project files.
- Read paths for the panel: repository/working-branch readiness, branch listing, milestone
  history, ledger save expansion, whole-forest graph topology, and commit "breadcrumb"
  context. There is no separate generic status endpoint without a UI contract.
- Remote interaction: listing locally-known remotes, deliberate hardened fetch, deliberate
  publication of the resolved local default branch on its own (only when bootstrapping an
  advertised-empty remote) followed by the atomic working+ledger pair, fast-forward catch-up, and
  "branch away" fork resolution — never an automatic push, fetch-and-merge, or force-push.
- Per-clone local state: which working branch this clone serves, local UI preferences,
  the fork-point map, trash tombstones, and last-pushed SHAs (all untracked, all living
  under `.haute/`).
- Error taxonomy and its translation to HTTP status codes.

Out of scope (owned by neighbouring components):
- The HTTP framework wiring, request/response schemas as Pydantic models, and the
  `_INTERNAL_ERROR_DETAIL` sanitization constant — see
  [server-api](../server-api/high-level.md).
- The panel UI that renders branch state, the graph rail, and the milestone/save history —
  see [frontend-git-ui](../frontend-git-ui/high-level.md).
- What files get committed as part of a pipeline save (config schema, pathspec meaning
  beyond the seed allow-list) — see [pipeline-config](../pipeline-config/high-level.md).
- Configuring a hosting provider's repository-level default-branch setting (for example,
  changing which branch GitHub selects for new pull requests). Haute publishes git refs;
  provider control-plane configuration remains outside this component.

## Behaviour

**The branch-pair model.** Every working branch `<W>` the user creates is paired with a
save ledger `<W>-save`. Ordinary "saves" are commits on the ledger — one commit per save,
scoped to exactly the files that changed: the pipeline's files, the workbench's form folder's files, or a
file added, replaced or deleted in the pipeline folder's `files/` through the Pipeline files
panel ([server-api](../server-api/high-level.md)). A save whose capture is skipped for want
of a git identity or of a chosen working branch, or fails, still lands on disk, and its
answer names the files it left off the ledger with a warning: no later save's pathspec takes
them, so the editor keeps them and one capture route takes them once an identity is set or a
branch chosen, and at each Save. A project with no repository owes nothing.
HEAD lives on the ledger during normal use, so
the user is always looking at their latest saved state. "Commit" (a milestone) first
sweeps into one final ledger save every modified file already tracked by Git and every new
file in the pipeline folder, which is versioned as a whole
([pipeline-config](../pipeline-config/high-level.md)). A new file elsewhere is never included
implicitly, and neither is one that `.gitignore` ignores nor one the Files panels hide (a
dot-name such as `.env.local`, `__pycache__/`, bytecode), so Commit never records a file the
analyst could not see in Haute. A file in the pipeline folder
larger than 100 MiB refuses the Commit, naming the file, before anything is recorded. Commit
then folds every pending ledger commit into
a single, always-real merge commit on the working branch, with the user's own message and an
optional version-label tag. The branch advance and optional annotated tag ref are one atomic
ref transaction, so a rejected or failed label cannot leave an unlabelled milestone behind.
The working
branch's first-parent chain therefore reads as a clean sequence of deliberate milestones,
while every individual save remains reachable through each merge's second parent.

**Guardrails.** The default-branch and a fixed set of protected names (`main`, `master`,
`develop`, `production`, or an operator-configured override) can never be written to
directly — any attempt raises a guardrail error, distinguished from an ordinary domain
error so the HTTP layer can return 403. Save ledgers cannot be chosen as a working branch.
Any user-supplied ref name (branch name, SHA, tag label) is validated against a
denylist of characters that could be interpreted as a CLI flag or contain control
characters, closing an argument-injection path from user input into the `git` CLI. A
branch name the analyst types is refused before any ref is made when git could not use
it, naming the rule it breaks (a space, `..`, a part starting with `.` or ending in
`.lock`, `//`, a trailing `/`, `@{`); git's own `check-ref-format --branch` decides.
While a merge, rebase, cherry-pick or revert started outside Haute is in progress, a save's
capture, a milestone, a restore, Catch up and Spin off each refuse with that reason rather
than git's own; the save itself still lands on disk, its capture warning saying why.

**Deliberate remote interaction.** No remote mutation or publication happens during `haute
init`, `haute serve`, or working-branch creation. Those stages may scaffold files, establish
the local default/working/ledger history, and perform existing prompt-proof remote status
reads, but only the user's explicit Push action may publish refs. This keeps offline startup
usable, avoids credential prompts during project setup, and makes the remote mutation
boundary visible to the user. The same boundary holds for the deploy's GitHub secrets: the
Push that first publishes a project to a GitHub repository sets the ones it lacks, a later
Push only reports them, and the push panel's Set from .env is the other explicit action that
sets them ([cli](../cli/high-level.md)).

Every deliberate remote command runs its transport noninteractively and against a deadline:
no stdin, no terminal (POSIX), no OpenSSH askpass or Git terminal prompt, and at the deadline
the whole process tree it started is ended, the transport's children included, on Windows
too. A transport the analyst configured (`GIT_SSH_COMMAND`, `core.sshCommand` or `GIT_SSH`,
in Git's order) runs exactly as configured, so an ssh remote that works in the shell works
in Haute; Haute's batch-mode OpenSSH command applies only when none is configured, and it
never appends OpenSSH options to another command, which OpenSSH would ignore (the first value
of an option wins) and a wrapper or Plink may not understand.

Before that push, Haute strictly inspects the selected remote. Only a successful
advertisement containing zero object refs means "empty"; an unborn symbolic `HEAD` alone is
still empty, while any branch or tag makes the remote non-empty. Timeout, authentication or
network failure, or an unreadable advertisement is an inspection failure and refuses the
push rather than being treated as an empty remote. A remote that cannot be reached answers
as Catch up and Spin off do: "Could not refresh '<remote>'. Check your connection or
credentials and try again." An empty remote first receives the resolved default branch on its own, then
the working branch and the save ledger when it exists in one atomic push, so the merge
target is created as the work is shared. The first push carries the default alone because
GitHub never triggers workflows in a repository whose first push created several branches. A
create-only lease on the default ref makes that bootstrap a compare-and-create: a default
branch that appears after inspection is never advanced or replaced. If the pair push then
fails, the remote holds only the default and Haute says so in its own words; the next Push
publishes the pair.

For a non-empty remote, the expected default branch must already exist. Haute fetches that
ref and validates that it shares history with the local working line before publishing the
working+ledger pair. It never implicitly advances or replaces an existing remote default
branch. A non-empty remote missing the expected default, an unrelated default history, or
an inspection/fetch failure refuses before publication; the atomic push guarantees that a
later ref rejection cannot leave only part of the submitted set behind. The push subprocess
itself is prompt-proof and time-bounded, so a wedged transport cannot retain the repository
mutation lock indefinitely; a timeout, launch failure or transport error answers "Could not
push to '<remote>'. Check your connection or credentials and try again.", and a remote that
refuses the refs (a branch rule, missing write access) says it refused the push. A
working/ledger non-fast-forward rejection still surfaces as a structured "fork" the UI can
explain (which leg diverged, by how much) rather than a dead-end error. No path force-pushes.

A remote fast-forward catch-up only ever advances refs when every leg is a clean fast-
forward; anything else is refused so the user spins off a copy instead of triggering a
silent merge. Routine listing and readiness requests never fetch. They report divergence
from locally-known remote-tracking refs; only deliberate Push, Catch up, or Spin off a copy
operations perform prompt-proof, time-bounded network refreshes. Remote URLs returned to the browser strip URL userinfo
(`user:password@`) while leaving scp-style `git@host:path` and local paths unchanged.

**History as read-only.** Viewing a historical commit's pipeline (`GET /show/{sha}`) never
touches HEAD or the working tree. The request names the pipeline file being compared, and
the view parses that file as it stood at the commit, never another pipeline in the same
project. The view extracts only pipeline artifacts required for
parsing, not unrelated datasets or build outputs. Extraction rejects unsafe member types and
paths, more than 10,000 members, or more than 64 MiB of regular-file content; malformed,
oversized, or unparseable historical pipelines fail explicitly instead of returning a
successful empty graph. Haute never checks out a historical commit detached: going back is
a Restore on the current branch, and trying an idea from an earlier version is a new branch
started there. A create-and-move operation preserves pending save topology; when external
merge commits make replay unsafe, it refuses and directs the user to create a parallel line.

**Starting a branch from a version.** A new working branch can start at any milestone or
save of the current branch: a milestone, a pending save, or a save folded inside a
milestone. A save is first crystallised into an anchoring milestone. For a pending save it
sits on the latest milestone; for a folded save it sits on the milestone before the one
that folds it. So the new branch's milestones read in the order the work was done. The new
branch can be left as a parallel line or opened at once, a switch that checks out its
ledger and leaves the current branch's refs as they were. A branch created from the branch
chooser on a project left on a save, by a checkout outside Haute, starts at the same
anchoring milestone, so its Milestones list never shows the old ledger's saves as
milestones.

**Restoring a version.** Going back to an earlier version and carrying on is a Restore.
Restoring a milestone or save of the current working branch writes that version's
files back as one new save on its ledger: the analyst stays on the branch, no other ref
moves, and the saves since stay in the history. A Restore is undone by restoring the save
before it. Its target is any commit in the current branch's history: a milestone, a pending
save, or a save folded inside a milestone. A commit of another branch, or one outside both
lines, is refused. A Restore runs in this order:

1. It records everything on disk first, with the milestone's sweep: modified tracked files
   and new files in the pipeline folder become one save, "Updated project files". So nothing
   the Restore then replaces is lost, and a file too large for version history refuses the
   Restore before anything is recorded.
2. It writes the restore save into history, built from the target's tree with plumbing as a
   milestone is: one parent, the ledger tip, and a message the engine writes from the
   target's short SHA and subject. The ledger then advances under a compare-and-swap.
3. Only then does it write the files: changed files take the target's content, files deleted
   since come back, and files added since are removed. Untracked and ignored files and
   `.haute/` are left alone, and the caches are cleared afterwards.

Restoring the current version with nothing on disk to record answers that it is already this
version. A Restore needs a git identity, and refuses while a git operation is in progress. It
also refuses when an untracked or ignored file sits where the target has a file, naming the
file, rather than overwriting it. If writing the files fails, history already holds the
restore; the analyst sees the failure.

**Recoverability.** Deleting a working pair does not destroy it. Each deletion is its own
trash entry, keyed by the branch name and the moment of deletion, so deleting a name, making
it again and deleting it again keeps both.
- **Before the refs go:** both tips are pinned under the entry in a non-head ref namespace,
  and a tombstone is recorded.
- **The refs themselves:** the working and ledger refs are removed in one ref transaction,
  so no crash leaves half a pair. Archiving and un-archiving move the pair in one
  transaction too.
- **Restoring:** the branch manager lists the 20 most recent deletions and restores any of
  them exactly, under another name when its own is taken.
- **The cap:** when it rolls over, the oldest entry's pins go with its tombstone, since
  nothing could reach them any more.

Multi-step ref
mutations (branch selection, archive/delete/restore, fork with move or switch, branch-away,
fast-forward, and branch-pair creation after any unborn-repository seed has succeeded) keep
an operation snapshot and compensate on failure. An incomplete compensation is an explicit
transaction failure and is never reported as success. Archiving or deleting the active pair
detaches at the working branch's tip, its last milestone, rather than checking out another
branch: the analyst stays on the work they had committed. Its later saves stay with the
archived ledger or the trash entry, and the branch chooser opens at the next startup. Files
changed since the last save refuse an archive, and a delete unless it is confirmed: only a
confirmed delete discards them with the branch. The seed phase itself is outside that rollback
boundary: it may
rename an unborn branch to `main`, append protective `.gitignore` entries, clear/rebuild the
index, and create a permanent root commit before pair creation begins. A failure during that
phase can leave those safe preparatory changes behind, although it does not deliberately
remove working-tree files. Because the root commit is permanent, it never takes a Python
environment below the project's top level (a folder holding a venv's `pyvenv.cfg` or a conda
environment's `conda-meta`, whatever the folder is called), and a first version of more than
1,000 files is refused, naming the folder most of them are in, before anything is committed.

**Files another program holds open.** On Windows git cannot replace a file another program
holds open, such as a CSV open in Excel, and it does not always say so. A checkout still
exits 0: HEAD moves to the new branch while that file keeps the old version, which the next
Commit's sweep would record as a change. A fast-forward exits 1 with the other files already
written, and a restore stops part-way.

So every git command that rewrites the working tree runs through one fence. It compares the
tracked changes before and after, and a new one means the tree is out of step with HEAD. The
fence then puts HEAD back where it was (forcing it when a file is held too tightly to be
read, as Excel holds an open CSV, and nothing was changed before the command), writes the
out-of-step files from it, and refuses
with the files named: "<files> are open in another program, so Haute could not replace them.
Close them and try again."

When a file still cannot be written, the clone is recorded as interrupted. This happens when
the put-back fails, or when a restore fails after its save was recorded. Readiness then
answers `interrupted` with the files. Commit and Restore refuse until the analyst closes the
files and chooses Retry, which writes HEAD's version of those files.

**Crash-safe clone state.** Every `.haute/*.json` write is staged to a sibling temporary
file and atomically replaced while holding the same per-repository mutation lock used by the
engine. Read-modify-write helpers keep that lock for the whole transaction, so concurrent
preference, fork, trash, and pushed-tip updates cannot lose one another and readers see a
complete old or new document, never torn JSON.

## Design rationale

- **Branch-pair model over a single branch.** A single working branch that receives every
  save directly would make "milestone" indistinguishable from "save" in the history, and
  would force every save to be a meaningful commit message. Splitting saves onto a ledger
  lets saves be cheap and automatic (an auto-generated message) while milestones stay
  deliberate and named, without losing either granularity.
- **Real merge commits via plumbing, not a working-tree merge.** Milestones are built with
  `commit-tree` plus a ref transaction rather than `git merge`, so there is no checkout, no
  index, and by construction no possibility of a merge conflict landing in front of the
  user — the tree is exactly the ledger's tree, unconditionally. When a version label is
  supplied, its annotated tag object is prepared first and the working ref plus tag ref are
  committed together.
- **Restore as a save, history before files; no detached move.** A detached checkout
  unlinks the branch, and the next Save's chooser offered the branch's latest save, which
  undid the restore. A ledger
  rewind would drop saves and collide with a published ledger. A save keeps every commit
  reachable and leaves the analyst on the branch. Recording the restore before writing the
  files means a file the operating system refuses to replace cannot leave a part-written
  tree that the next milestone's sweep would record as if it were the restore.
- **Never force, never auto-merge remotely.** The product's trust model is that a user's
  local work is never silently discarded or rewritten. Push refuses on divergence rather
  than forcing; fast-forward refuses on anything but a clean fast-forward; a remote rewrite
  is detected and reported distinctly from ordinary divergence (`_is_rewrite`) using a
  locally-recorded last-pushed SHA, because gc can prune the reflog that would otherwise
  answer "was this rewritten".
- **Bootstrap at explicit Push, not project setup.** The local root/default commit is an
  input to branch creation, but publishing it is a remote side effect. Folding an empty-
  remote bootstrap into the already-deliberate Push gives a new project a merge
  target without making `init`, `serve`, or branch selection depend on a remote, network,
  or credentials. Existing remote defaults are validation inputs, never implicit push
  targets, so this convenience cannot rewrite or advance an established shared branch.
- **Create-only lease is not a force update.** Bootstrap attaches an empty expected value to
  the default ref. It can never authorize replacing or advancing an existing default. If a
  concurrent writer creates the exact commit Haute intended, Git may treat that default as
  already up to date and safely publish the pair; a different commit loses the lease and
  nothing is published. All branch sources are the validated commit snapshots, the working
  and ledger destinations remain ordinary non-force updates, and the pair is atomic.
- **The default goes first, alone.** A repository's first push decides whether GitHub ever
  runs its workflows: one whose first push created several refs never triggers `push` or
  `pull_request` workflows, atomic or not, while one initialised with a single branch behaves
  normally (observed 27 October 2026). Publishing the default by itself before the pair keeps
  every new repository in the working state on every host. The price is one partial state,
  the default without the pair, which is an ordinary established remote: the next Push
  completes it, and the failure is reported in hand-written words instead of being hidden.
  Rolling the default back instead would need a remote deletion, which the never-force rule
  forbids.
- **Content-addressed caching.** Git's own objects are immutable once written, so any pure
  function of a full 40-hex commit SHA (merge-base, ancestry, first-parent spine, a
  commit's tree, its parents) is cached forever with no invalidation story — only ref-name
  lookups (which can move) stay uncached. This turns the panel's read-heavy endpoints
  (graph, milestones, branch manager) from O(branches × history) subprocess calls into
  O(1) after the first request.
- **Guardrails as a distinct error subtype, not just a message string.** `GitGuardrailError`
  is a subtype of `GitDomainError` specifically so the HTTP layer can map it to 403 instead
  of 400 without string-matching the message.
- **One repository mutation lock, not scattered operation locks.** Git's index, HEAD, refs,
  working tree, and Haute's clone-state files form one logical engine state. A reentrant
  per-repository lock lets compound operations call smaller mutators without deadlock while
  preventing two request threads from interleaving successful-looking partial transactions.
  The registry holds locks weakly, and the path-to-repository lookup cache is bounded, so a
  long-running multi-project server does not retain one permanent lock per path it has ever
  observed. Pipeline writers join this lock rather than relying on the project's save lock
  alone: the save lock orders writers among themselves, and only the repository lock orders
  them against Git, so a milestone can no longer capture a save's Python without the
  configuration it references.
- **Untracked per-clone state.** The working-branch association, preferences, fork map,
  trash, and last-pushed SHAs all live in `.haute/*.json`, deliberately outside git's own
  history — HEAD itself cannot answer "which working branch does this clone serve" once
  HEAD lives on the ledger, so a side-channel is unavoidable, and keeping it untracked
  means it never conflicts or gets committed by accident.

## Interactions

- **[server-api](../server-api/high-level.md)** hosts this component's routes
  (`routes/git.py`) under `/api/git/*`, converts its typed exceptions to HTTP responses,
  and defines the Pydantic request/response schemas this component returns directly (no
  intermediate DTO layer).
- **[frontend-git-ui](../frontend-git-ui/high-level.md)** is the sole consumer of every
  route this component exposes: the startup working-branch modal, the save/commit
  controls, the branch manager, the history/graph rail, and the push/remote surfaces.
- **[pipeline-config](../pipeline-config/high-level.md)** defines what a "save" actually
  commits (the pipeline's config files) and is the reason `commit_save` is pathspec-scoped
  rather than committing the whole index.
- **[workbench](../workbench/high-level.md)** saves its form folder's files through the same
  capture as the pipeline save (`capture_save_in_ledger` in the save route module), one
  more pathspec-scoped save on the ledger whose message names the tables and sheets it
  changed, and reports it in the editor through the same save-capture report; a
  milestone's sweep takes the form's tracked files and, the folder being in the pipeline
  folder, its new ones.
- `haute._gitignore_guard.ensure_gitignore_guards` is shared with the CLI initializer and
  asserted again by `set_working_branch`'s unborn-repo seed path, as defence-in-depth
  against a foreign `git init` repo that lacks haute's guard entries.

## Failure model

Every git-facing failure surfaces through a `GitError` subclass (`GitError` itself is a
subclass of the project-wide `HauteError`):

- **`GitError`** — raw subprocess stderr. Treated as unsafe to show a user (it can embed
  absolute paths, remote URLs, SSL errors, or credentials); the HTTP layer collapses it to
  a sanitized constant while the full text is retained in a structured log line.
- **`GitDomainError`** — a hand-authored, user-facing message (e.g. "Not a git repository",
  "Branch already exists", "No changes to save"). Never wraps raw stderr. Surfaces
  verbatim to the client as HTTP 400.
- **`GitGuardrailError`** — a `GitDomainError` subtype specifically for guardrail blocks
  (protected branch, already-archived, save ledger used as a working branch). Surfaces
  verbatim as HTTP 403.
- **`GitTransactionError`** — a `GitDomainError` subtype used when a mutation failed and
  one or more compensating steps also failed. Its safe message tells the user that repository
  repair may be required; the original and rollback failures remain server-side.
- **`GitHistoryReadError`** — a `GitDomainError` subtype for a history git cannot read, a
  malformed archive, or a historical tree that cannot produce a pipeline graph. It is a
  failed read, never an empty successful list or graph.
- **`GitRemoteUnavailableError`** — a `GitDomainError` subtype for a deliberate remote read
  or push that could not reach the remote: a timeout, an authentication or network failure,
  or a transport that would not start. Its message names the remote and the remedy; git's
  stderr stays in the log. Hosted publishing retries it like any transport failure.
- **`GitFilesInUseError`** — a `GitDomainError` subtype carrying the files a tree-rewriting
  command could not write because another program holds them open. HTTP 400 with its
  message, which names at most five of them.

Two operations carry structured, machine-readable failure payloads instead of a plain
message, because the UI needs to *act* on the failure, not just display it:

- **`GitPushRejectedError`** (carries a `GitPushRejection`) — a non-fast-forward push,
  reported as HTTP 409 with per-leg divergence counts and whether the remote history was
  rewritten, so the UI can draw the actual fork.
- **`GitMilestoneForkError`** (carries a `GitMilestoneFork`) — a milestone that would fork
  the remote working branch, reported as HTTP 409 with the divergence state, so the UI can
  offer "commit anyway" as a deliberate override (`allow_fork`).

Anything outside these families (an unexpected exception in a route handler) is caught by a
route-level `except Exception` and turned into a generic HTTP 500 with the same sanitized
detail constant, logged with a stack trace.

This codebase prefers loud failure over silent fallbacks. Consistent with that: an
unreadable object never populates a cache (every cached helper raises rather than memoizing
a failure); a milestone list or a set of saves git cannot read is a `GitHistoryReadError`,
never an empty, successful branch; a save whose status check fails is an error, never "nothing
changed"; a `_wipe_volatile_artefacts` failure is logged but not fatal, because volatile
caches are reconstructable by definition; and no routine read starts a network operation.
Deliberate remote operations abort on a required refresh/inspection failure and never
degrade to an assumed-empty, assumed-related, or successfully-updated remote.

**Process-wide content-cache scope.** The content-addressed caches
(`_merge_base_cached`, `_is_ancestor_cached`, `_first_parent_spine_cached`,
`_commit_parents_cached`, `_graph_log_cached`, `_tree_of_cached`) are
`functools.lru_cache` module-level globals rather than per-request or
per-repository state. Including `str(cwd)` in each key disambiguates repositories and
worktrees served by one process; `_clear_content_caches()` supports test isolation and
repository-reset hygiene. Mutable ref-name lookups, including default-branch discovery, remain
deliberately uncached.
