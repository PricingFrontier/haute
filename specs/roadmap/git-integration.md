# Git integration roadmap

## Scope

The working-branch model on top of git: saves and milestones, restore, archive,
delete, catch-up, spin-off and push, the unborn-repo seed, the history views and
the remote handling, and the git panel that drives them. Current behaviour is
specified in [the git-integration specification](../git-integration/high-level.md)
and [the git UI](../frontend-git-ui/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).
`GIT-R16` comes from a review on 27 October 2026 of going
back to an earlier version (one Codex and two Fable reviews, every finding checked at
its source), after an analyst restoring a version on the test project took six Move
and return cycles and made and deleted two branches between 13:14 and 13:36.
`GIT-R18` comes from the [28 October functional review](codebase-review-2026-10-28.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| GIT-R05 | Planned | P3 | The git modules carry no dead symbols, one free-name finder and one compensation helper. |
| GIT-R06 | Decision | P3 | Automatic ledger saves either skip the project's git hooks or the specification says they run them. |
| GIT-R07 | Planned | P2 | Every tree-changing action in the git UI passes the dirty-project guard and names what changes, a failed history read shows an error rather than an empty branch, a failed preference save is reported, and the dialogs say what the server does. |
| GIT-R08 | Planned | P3 | The git panel has one context menu, one dirty-navigation wrapper and one relative-time rule. |
| GIT-R16 | Planned | P3 | A working branch left without its ledger, or half archived, is reported at startup with a Repair that leaves a whole pair. |
| GIT-R18 | Planned | P2 | A rejected push's Catch up and Spin off actions stay bound to that rejection's remote. |

## Planned improvements

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
`src/haute/_git_core.py::commit_parent`;
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
**Why:** Catch up and Spin off a copy update the working tree from one click
with no confirmation and without the dirty-project guard every other tree-changing action has, so unsaved
in-memory canvas and form edits meet the change unannounced (the server refuses
a dirty tracked tree, so what is exposed is the in-memory state); the toolbar's
undo and redo of a branch switch check out the other branch with no guard and
without discarding the form's edits. After a history fetch fails the panel shows
"No milestones yet" once the toast fades, so a transport failure reads as an
empty branch. "Don't ask again on this machine" swallows a failed preference
save, so the prompt returns next time with no explanation. The delete dialog
says "Permanently delete" for a trash-preserving delete the toolbar can undo; an
`invalid` readiness state shows only its first error.

**Plan:** Route Catch up, Spin off and the undo legs through the dirty-navigation
guard with a confirm naming what changes, then discard the form's edits on
success; keep a load error in the history hook and render it with Retry; report
the preference failure; word the delete as recoverable; list every error.

**Acceptance:** Catch up with unsaved canvas edits asks first; a history fetch
failure shows an error with Retry, not the empty state; a failed preference save
toasts.

**Dependencies:** None.

**Evidence:** `frontend/src/components/RemotePushControl.tsx`;
`frontend/src/utils/vcHistory.ts`; `frontend/src/panels/git/useGitHistory.ts`;
`frontend/src/components/BranchManager.tsx`;
`frontend/src/components/WorkingBranchModal.tsx`.

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

### GIT-R16 — Broken pairs reported with Repair
**Why:** Delete and archive used to move a pair's two refs one step at a time, so a
crash between the steps could leave an orphan ledger (`x-save` with no `x`) or a
half-archived pair (`archive/x` beside `x-save`). Both now move in one ref
transaction, but a pair broken before that stays broken. Create, undelete and
restore refuse such a name, and nothing checks pair health at startup. This is
inferred: no broken pair has been seen.

**Plan:**
- A startup check lists orphan ledgers and half-archived pairs.
- Repair finishes a half-done archive, and makes the missing working branch of an
  orphan ledger at the ledger's last milestone.

**Acceptance:** An orphan `x-save` and an `archive/x` beside `x-save` are both
reported at startup, and Repair leaves each a whole pair.

**Dependencies:** None.

**Evidence:** `src/haute/_git_archive.py::archive_working_pair`;
`src/haute/_git_transactions.py::check_invariants`;
`src/haute/_git_read_models.py::working_branch_status`.

### GIT-R18 — Recovery acts on the remote named by its dialog

**Why:** While Push to origin is pending, the remote select remains editable. Switching it to
backup before origin returns 409 produces a dialog naming origin, but its recovery callbacks
submit backup. Actual component tests reproduce both behind and diverged cases. The wrong
endpoint submission is measured; the resulting Git tree mutation is supported by the route
semantics, not executed by this UI probe.

**Plan:** Pass the rejection's authenticated remote into modal recovery actions; standalone
recovery uses its own captured target. Invalidate stale dialogs when their context is no longer
actionable and keep busy/selection rules consistent. Do not take a destructive target from a
mutable dropdown after confirmation.

**Acceptance:** Delay origin's 409, select backup, then invoke each modal action: only origin
is submitted or the stale dialog is explicitly refused. The dialog and action always name the
same remote. Existing dirty-document and branch protections still apply.

**Dependencies:** `GIT-R07` owns dirty-document guards; it does not authenticate this remote identity.

**Evidence:** `frontend/src/components/RemotePushControl.tsx::RemotePushControl.doCatchUp`;
`frontend/src/components/RemotePushControl.tsx::RemotePushControl.doBranchAway`;
`frontend/src/components/RemotePushControl.tsx::RemotePushControl` (remote selector);
`frontend/src/components/RemotePushControl.tsx::RemotePushControl` (`RejectPushDialog` callback props).
