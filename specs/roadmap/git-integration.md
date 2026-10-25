# Git integration roadmap

## Scope

The working-branch model on top of git: saves and milestones, move, archive,
delete, catch-up, spin-off and push, the unborn-repo seed, the history views and
the remote handling, and the git panel that drives them. Current behaviour is
specified in [the git-integration specification](../git-integration/high-level.md)
and [the git UI](../frontend-git-ui/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md);
the Databricks IO defects the same review found are `BUG-44` and `BUG-45` in
[bugs](bugs.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| GIT-R01 | Decision | P1 | Move, Catch up and Spin off a copy stop deleting the project's `outputs/` folder, or the specification and the UI say plainly that they do. |
| GIT-R02 | Planned | P2 | Every git failure the analyst can act on is answered with its reason: an invalid typed branch name, a push or fetch that times out or fails authentication, an unreadable history, a save during an in-progress git operation, and an SSH command of the user's own. |
| GIT-R03 | Planned | P2 | Deleting the active pair without confirmation keeps a dirty tree, and the first commit never seeds a virtual environment. |
| GIT-R04 | Planned | P3 | Commit context and historical extraction pass their SHAs and paths through stdin, so a long branch or a large commit does not fail on Windows' command-line limit. |
| GIT-R05 | Planned | P3 | The git modules carry no dead symbols, one free-name finder and one compensation helper. |
| GIT-R06 | Decision | P3 | Automatic ledger saves either skip the project's git hooks or the specification says they run them. |
| GIT-R07 | Planned | P2 | Every tree-changing action in the git UI passes the dirty-project guard and names what changes, a failed history read shows an error rather than an empty branch, a failed preference save is reported, and the dialogs and specification say what the server does. |
| GIT-R08 | Planned | P3 | The git panel has one context menu, one dirty-navigation wrapper and one relative-time rule. |

## Planned improvements

### GIT-R01 — `outputs/` survives a move
**Why:** `_VOLATILE_ARTEFACTS` names `outputs` beside the caches, and
`_wipe_volatile_artefacts` removes each entry with `shutil.rmtree` before the
detached checkout, so Move, Catch up and Spin off a copy delete the project's
`outputs/` folder wholesale, even when the checkout is then refused. `outputs/`
is the documented local, unversioned folder of analyst results (`LOCAL_FOLDERS`):
the Files panel serves it and a model's data export defaults into it, and git
cannot restore it. The comment beside the list already exempts `output`
(singular) because Apply Optimisation prices from results there; the same hazard
applies. The specification lists `outputs` among the wiped folders, so code and
specification agree and the choice is the owner's.

**Plan:** Decide. The review's recommendation: remove `outputs` from the list
(keep the caches), run the wipe after a successful checkout, and update the
specification; otherwise make the UI name the deletion before the action and
record the choice.

**Acceptance:** A move with a file in `outputs/` leaves it in place (or the move
dialog names its deletion); a refused checkout deletes nothing.

**Dependencies:** None.

**Evidence:** `src/haute/_git_core.py::_VOLATILE_ARTEFACTS`;
`src/haute/_git_core.py::_wipe_volatile_artefacts`;
`src/haute/_git_archive.py::move_to_commit`;
`src/haute/_project_layout.py::LOCAL_FOLDERS`.

### GIT-R02 — Git failures the analyst can act on
**Why:** A typed branch name git refuses but `_validate_ref_name` admits (a
space, `..`, `.lock`, a leading `.`, `//`, a trailing `/`, `@{`) is answered
"Operation failed. Check the server logs for details." (probed with seven such
names), while the stricter `check-ref-format --branch` validation exists and is
used only by push. A push whose remote inspection or default-branch fetch times
out, fails authentication or cannot launch is answered the same sanitized text,
while Catch up and Spin off report the identical failure as "Could not refresh
'origin'. Check your connection or credentials and try again." `_remote_env`
sets `GIT_SSH_COMMAND` unconditionally, so a user's `core.sshCommand`, `GIT_SSH`
or `GIT_SSH_COMMAND` (a specific key, a jump host) is discarded and an SSH remote
that works in the shell fails in Haute with the sanitized message. An unreadable
history (`git log` failing for a resolved tip) is returned as an empty,
successful milestone list. Saves and milestones do not check for an in-progress
merge, rebase or cherry-pick, so every canvas save then fails with the sanitized
"cannot do a partial commit during a merge" while move, catch-up and spin-off
refuse with the hand-written reason.

**Plan:** Validate typed names with `git check-ref-format --branch` and raise
`GitDomainError` naming the rule; raise `GitDomainError` with the refresh
message for inspection, fetch and push timeouts, launch failures and non-zero
exits, keeping stderr in the log; when the user configures an SSH command,
append the batch-mode and timeout options to it instead of replacing it; let the
history error propagate; call `_assert_no_git_op_in_progress` at the top of
`commit_save` and `commit_milestone`.

**Acceptance:** `my branch` is refused naming the rule; a push against an
unreachable remote answers the refresh message; a configured `GIT_SSH_COMMAND`
is honoured (a test asserts the resolved command); a corrupt object store
answers an error, not an empty list; a save during a merge answers the
in-progress message.

**Dependencies:** None.

**Evidence:** `src/haute/_git_core.py::_validate_ref_name`;
`src/haute/_git_core.py::_validate_managed_working_branch`;
`src/haute/_git_core.py::_remote_env`;
`src/haute/_git_core.py::_assert_no_git_op_in_progress`;
`src/haute/_git_remote.py::_inspect_remote`;
`src/haute/_git_remote.py::_fetch_expected_default`;
`src/haute/_git_remote.py::push_working_pair`;
`src/haute/_git_history.py::working_milestones`;
`src/haute/_git_transactions.py::commit_save`.

### GIT-R03 — A dirty tree and a virtual environment
**Why:** `delete_working_pair` passes `discard=True` to the switch-away whenever
the active pair is deleted, including with `confirm=False`; `confirm` guards
only unmerged ledger saves, so the API contract alone does not protect
uncommitted edits (the UI always confirms after a dialog naming the loss). The
unborn-repo seed stages every `*.py` under the root that `.gitignore` does not
exclude, and the guard excludes only `.venv/`, so a project with `venv/`, `env/`
or `node_modules/` commits that tree into the permanent root commit, which the
specification places outside rollback; `haute init` does not run `git init`, so
this seed is the normal first-branch path.

**Plan:** Pass `discard=confirm` and refuse a dirty tree without confirmation
with the archive's unsaved-changes error; exclude directories holding
`pyvenv.cfg` (or add `venv/`, `env/`, `node_modules/` and `site-packages/` to
the guard entries) and refuse an implausibly large seed naming the directory.

**Acceptance:** `delete_working_pair(confirm=False)` on a dirty tree is refused
with the tree intact; a project with `venv/` seeds without it.

**Dependencies:** None.

**Evidence:** `src/haute/_git_archive.py::delete_working_pair`;
`src/haute/_git_setup.py::_SEED_PATHSPECS`;
`src/haute/_gitignore_guard.py::GITIGNORE_GUARD_ENTRIES`.

### GIT-R04 — Long argument lists through stdin
**Why:** `_milestone_fold_points` spreads every milestone SHA of the first-parent
spine into one `git rev-list` argv and `archive_commit` spreads every selected
path, so on Windows `GET /api/git/commit-context/{sha}` fails with a 500 once
the working branch has about 790 milestones and `GET /api/git/show/{sha}` fails
when a commit's paths exceed about 32K characters (probed: `WinError 206` at
32,814 characters).

**Plan:** Feed the SHAs through `git rev-list --stdin` (byte-mode stdin as
`_run_git(input_text=...)` already does) and archive the whole commit, letting
the validated extractor skip non-artifact members, or chunk the path list.

**Acceptance:** A branch with 1,000 milestones answers commit context on
Windows; a commit with 40K characters of paths is shown.

**Dependencies:** None.

**Evidence:** `src/haute/_git_history.py::_milestone_fold_points`;
`src/haute/_git_history.py::archive_commit`; `src/haute/_git_core.py::_run_git`.

### GIT-R05 — Dead symbols, one free-name finder, one compensation helper
**Why:** `PROTECTED_BRANCHES` is only re-exported (`DEFAULT_PROTECTED_BRANCHES`
is the real set), `fetch_pair` and `commit_parent` are called only by tests, and
the `pricing/<user>/` ownership convention (`_BRANCH_PREFIX`, `_is_own_branch`,
`_get_user_slug`, `_slugify`, the `is_yours` sort key) is produced by nothing in
the product, so `is_yours` is false for every branch Haute creates. Two
date-and-counter free-name finders exist (`_unique_archive_name`,
`_unique_aside_name`), and nine hand-rolled compensation blocks repeat
`restored &= ok` with two different exception filters.

**Plan:** Delete the dead symbols and either generate names under the
convention or drop `is_yours` and its helpers; one free-name helper taking the
prefix pattern; one `compensate(steps)` helper with one exception filter raising
`GitTransactionError` on an incomplete rollback.

**Acceptance:** The named symbols are gone or called from production; jscpd
reports no pair among the compensation blocks.

**Dependencies:** `ENGQ-R01` owns `_assert_not_protected`.

**Evidence:** `src/haute/_git_core.py`; `src/haute/_git_remote.py::fetch_pair`;
`src/haute/_git_history.py::commit_parent`;
`src/haute/_git_archive.py::_unique_archive_name`;
`src/haute/_git_remote.py::_unique_aside_name`.

### GIT-R06 — Hooks on automatic saves
**Why:** Automatic ledger saves and the seed commit run the project's git hooks
(no `--no-verify`) while milestones bypass them (`commit-tree`); with the
scaffolded hook every background save runs `uv run ruff format`, `ruff check
--fix` and `git add`, so a save pays a `uv run` and can commit content the hook
rewrote rather than the bytes the editor wrote (normally a no-op, since codegen
lays code out as ruff would).

**Plan:** Decide once: pass `--no-verify` on the automatic save so hooks run
only where a person commits, or state in the specification that hooks run per
save.

**Acceptance:** The specification and `commit_save` agree.

**Dependencies:** None.

**Evidence:** `src/haute/_git_transactions.py::commit_save`;
`src/haute/_git_transactions.py::commit_milestone`;
`src/haute/_scaffold.py::pre_commit_hook`.

### GIT-R07 — The git UI guards and reports every tree change
**Why:** Catch up and Spin off a copy update the working tree (and wipe
`outputs/` while `GIT-R01` stands) from one click with no confirmation and
without the dirty-project guard every other tree-changing action has, so unsaved
in-memory canvas and form edits meet the change unannounced (the server refuses
a dirty tracked tree, so what is exposed is the in-memory state); the toolbar's
undo and redo of a branch switch check out the other branch with no guard and
without discarding the form's edits. After a history fetch fails the panel shows
"No milestones yet" once the toast fades, so a transport failure reads as an
empty branch. "Don't ask again on this machine" swallows a failed preference
save, so the prompt returns next time with no explanation. The delete dialog
says "Permanently delete" for a trash-preserving delete the toolbar can undo; an
`invalid` readiness state shows only its first error; the git UI specification
and the guard test speak of six readiness states where the contract has seven.

**Plan:** Route Catch up, Spin off and the undo legs through the dirty-navigation
guard with a confirm naming what changes, then discard the form's edits on
success; keep a load error in the history hook and render it with Retry; report
the preference failure; word the delete as recoverable; list every error; add
`git-unavailable` to the specification and the test.

**Acceptance:** Catch up with unsaved canvas edits asks first; a history fetch
failure shows an error with Retry, not the empty state; a failed preference save
toasts; the guard test iterates seven states.

**Dependencies:** `GIT-R01` decides the `outputs/` wording.

**Evidence:** `frontend/src/components/RemotePushControl.tsx`;
`frontend/src/utils/vcHistory.ts`; `frontend/src/panels/git/useGitHistory.ts`;
`frontend/src/components/BranchManager.tsx`;
`frontend/src/components/WorkingBranchModal.tsx`;
`specs/frontend-git-ui/low-level.md`.

### GIT-R08 — One menu, one wrapper, one clock
**Why:** The fixed-backdrop context menu is written five times across the git
panel and the branch manager, the dirty-navigation wrapper (guard plus the
confirm dialog's save-then-proceed block) twice verbatim, and relative time five
times across the git panel, the breadcrumb, the assistant and two utilities with
three different rules ("400d ago" never stops; the assistant switches to a date
after seven days; the shared formatter after a day).

**Plan:** A `ContextMenu` primitive, a `useDirtyNavigation(onSave)` hook, and
one `relativeTime` in `utils/` imported by every site.

**Acceptance:** One copy of each; the same timestamp reads the same in every
panel.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/GitPanel.tsx`;
`frontend/src/components/BranchManager.tsx`;
`frontend/src/components/CommitBreadcrumb.tsx`;
`frontend/src/panels/assistant/relativeTime.ts`;
`frontend/src/utils/formatTime.ts`.
