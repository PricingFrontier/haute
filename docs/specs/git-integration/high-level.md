# Git Integration — High-Level Specification

## Purpose

Haute is used by pricing analysts who are not git-fluent. This component gives them a
version-control workflow — "save my work", "commit a milestone", "go back to an earlier
version", "push to share" — without exposing branches, merges, rebases, or conflict
resolution as raw git concepts. Every git CLI interaction in the product goes through this
one layer, so guardrails (no writing to protected branches, no force-push, no silent
merges), user-friendly error translation, and safety nets (trash tombstones, best-effort
rollback paths) are enforced in exactly one place rather than scattered across call sites.

The component owns the full lifecycle of a "working branch": creating one, saving
incremental progress to it, promoting saves to a named milestone, forking a parallel
version line, moving to read a historical version, archiving/deleting/restoring branches,
and the deliberate (never automatic) push/fetch surface that shares work with a remote.

## Scope

In scope:
- The working-branch / save-ledger branch-pair engine (create, save, commit milestone,
  fork, move, archive, delete, undelete, restore).
- Guardrails: protected-branch enforcement, ref-name injection prevention, unborn-repo
  seeding, git-op-in-progress detection, and the shared `.gitignore` deny-list asserted
  before a root commit can stage project files.
- Read paths for the panel: status, branch listing, milestone history, ledger save
  expansion, whole-forest graph topology, commit "breadcrumb" context.
- Remote interaction: listing remotes, throttled/hardened background fetch, deliberate
  atomic push of the working+ledger pair, fast-forward catch-up, and "branch away" fork
  resolution — never an automatic push, fetch-and-merge, or force-push.
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

## Behaviour

**The branch-pair model.** Every working branch `<W>` the user creates is paired with a
save ledger `<W>-save`. Ordinary "saves" are commits on the ledger — one commit per save,
scoped to exactly the files that changed. HEAD lives on the ledger during normal use, so
the user is always looking at their latest saved state. "Save & commit" (a milestone)
folds every pending ledger commit into a single, always-real merge commit on the working
branch, with the user's own message and an optional version-label tag. The working
branch's first-parent chain therefore reads as a clean sequence of deliberate milestones,
while every individual save remains reachable through each merge's second parent.

**Guardrails.** The default-branch and a fixed set of protected names (`main`, `master`,
`develop`, `production`, or an operator-configured override) can never be written to
directly — any attempt raises a guardrail error, distinguished from an ordinary domain
error so the HTTP layer can return 403. Save ledgers cannot be chosen as a working branch.
Any user-supplied ref name (branch name, SHA, tag label) is validated against a
denylist of characters that could be interpreted as a CLI flag or contain control
characters, closing an argument-injection path from user input into the `git` CLI.

**Deliberate remote interaction.** Nothing is ever pushed automatically. A push moves the
working branch AND its ledger together, atomically — both refs land or neither does — and
never force-pushes; a non-fast-forward rejection surfaces as a structured "fork" the UI can
explain (which leg diverged, by how much) rather than a dead-end error. A remote fast-
forward catch-up only ever advances refs when every leg is a clean fast-forward; anything
else is refused so the user spins off a copy instead of triggering a silent merge. Background
polling fetches (for the "main is ahead" badge, and for divergence detection) are throttled,
time-bounded, and prompt-proof, so a slow or credential-walled remote can never hang a
request. Remote URLs returned to the browser strip URL userinfo (`user:password@`) while
leaving scp-style `git@host:path` and local paths unchanged.

**History as read-only.** Viewing a historical commit's pipeline (`GET /show/{sha}`) never
touches HEAD or the working tree. Actually moving the working directory to a historical
commit (`move`) is a distinct, explicit operation: a detached-HEAD checkout that clears the
clone's working-branch association, so the very next save re-triggers the working-branch
chooser rather than silently resuming an old branch.

**Recoverability.** Deleting a working pair does not destroy it: both tips are pinned under
a non-head ref namespace and a tombstone is recorded before the branch refs are removed, so
`undelete` can rebuild one of the 20 most recently tombstoned pairs exactly. Older trash
pins remain reachable but lose their API tombstone when the cap rolls over. Multi-step ref
mutations (fork with move, branch-away, and branch-pair creation after any unborn-repository
seed has succeeded) have best-effort rollback of HEAD and refs so a partial failure does not
normally strand half a pair. The seed phase itself is outside that rollback boundary: it may
rename an unborn branch to `main`, append protective `.gitignore` entries, clear/rebuild the
index, and create a permanent root commit before pair creation begins. A failure during that
phase can leave those safe preparatory changes behind, although it does not deliberately
remove working-tree files.

## Design rationale

- **Branch-pair model over a single branch.** A single working branch that receives every
  save directly would make "milestone" indistinguishable from "save" in the history, and
  would force every save to be a meaningful commit message. Splitting saves onto a ledger
  lets saves be cheap and automatic (an auto-generated message) while milestones stay
  deliberate and named, without losing either granularity.
- **Real merge commits via plumbing, not a working-tree merge.** Milestones are built with
  `commit-tree` + `update-ref` rather than `git merge`, so there is no checkout, no index,
  and by construction no possibility of a merge conflict landing in front of the user —
  the tree is exactly the ledger's tree, unconditionally.
- **Never force, never auto-merge remotely.** The product's trust model is that a user's
  local work is never silently discarded or rewritten. Push refuses on divergence rather
  than forcing; fast-forward refuses on anything but a clean fast-forward; a remote rewrite
  is detected and reported distinctly from ordinary divergence (`_is_rewrite`) using a
  locally-recorded last-pushed SHA, because gc can prune the reflog that would otherwise
  answer "was this rewritten".
- **Content-addressed caching.** Git's own objects are immutable once written, so any pure
  function of a full 40-hex commit SHA (merge-base, ancestry, first-parent spine, a
  commit's tree, its parents) is cached forever with no invalidation story — only ref-name
  lookups (which can move) stay uncached. This turns the panel's read-heavy endpoints
  (graph, milestones, branch manager) from O(branches × history) subprocess calls into
  O(1) after the first request.
- **Guardrails as a distinct error subtype, not just a message string.** `GitGuardrailError`
  is a subtype of `GitDomainError` specifically so the HTTP layer can map it to 403 instead
  of 400 without string-matching the message.
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
- `haute._gitignore_guard.ensure_gitignore_guards` is shared with the CLI initializer and
  asserted again by `set_working_branch`'s unborn-repo seed path, as defence-in-depth
  against a foreign `git init` repo that lacks haute's guard entries.

## Failure model

Every git-facing failure surfaces as one of three exception families, all subclasses of
`GitError` (itself a subclass of the project-wide `HauteError`):

- **`GitError`** — raw subprocess stderr. Treated as unsafe to show a user (it can embed
  absolute paths, remote URLs, SSL errors, or credentials); the HTTP layer collapses it to
  a sanitized constant while the full text is retained in a structured log line.
- **`GitDomainError`** — a hand-authored, user-facing message (e.g. "Not a git repository",
  "Branch already exists", "No changes to save"). Never wraps raw stderr. Surfaces
  verbatim to the client as HTTP 400.
- **`GitGuardrailError`** — a `GitDomainError` subtype specifically for guardrail blocks
  (protected branch, already-archived, save ledger used as a working branch). Surfaces
  verbatim as HTTP 403.

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
a failure); a `_wipe_volatile_artefacts` failure is logged but not fatal, because volatile
caches are reconstructable by definition; a failed background fetch degrades silently to
the last-known remote-tracking refs, because fetches only ever advance `refs/remotes/*` and
can never violate a local invariant by failing.

> NOTE: `_get_default_branch_cached` and the content-addressed caches
> (`_merge_base_cached`, `_is_ancestor_cached`, `_first_parent_spine_cached`,
> `_commit_parents_cached`, `_graph_log_cached`, `_tree_of_cached`) are `functools.lru_cache`
> module-level globals, not per-request or per-repo-instance state. Multiple repos (or
> worktrees) served by one long-lived process are disambiguated only by including
> `str(cwd)` in the cache key; `_clear_content_caches()` exists specifically for test
> isolation and repo-reset hygiene because there is no automatic invalidation otherwise.
