# Git integration roadmap

## Scope

The working-branch model on top of git: saves and milestones, move, archive,
delete, catch-up, spin-off and push, the unborn-repo seed, the history views and
the remote handling, and the git panel that drives them. Current behaviour is
specified in [the git-integration specification](../git-integration/high-level.md)
and [the git UI](../frontend-git-ui/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md);
the Databricks IO defects the same review found are `BUG-44` and `BUG-45` in
[bugs](bugs.md). `GIT-R10` to `GIT-R15` come from a review on 27 October 2026 of going
back to an earlier version (one Codex and two Fable reviews, every finding checked at
its source), after an analyst restoring a version on the test project took six Move
and return cycles and made and deleted two branches between 13:14 and 13:36.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| GIT-R02 | Planned | P2 | Every git failure the analyst can act on is answered with its reason: an invalid typed branch name, a push or fetch that times out or fails authentication, an unreadable history, a save during an in-progress git operation, and an SSH command of the user's own. |
| GIT-R03 | Planned | P2 | Deleting the active pair without confirmation keeps a dirty tree, and the first commit never seeds a virtual environment. |
| GIT-R04 | Planned | P3 | Commit context and historical extraction pass their SHAs and paths through stdin, so a long branch or a large commit does not fail on Windows' command-line limit. |
| GIT-R05 | Planned | P3 | The git modules carry no dead symbols, one free-name finder and one compensation helper. |
| GIT-R06 | Decision | P3 | Automatic ledger saves either skip the project's git hooks or the specification says they run them. |
| GIT-R07 | Planned | P2 | Every tree-changing action in the git UI passes the dirty-project guard and names what changes, a failed history read shows an error rather than an empty branch, a failed preference save is reported, and the dialogs and specification say what the server does. |
| GIT-R08 | Planned | P3 | The git panel has one context menu, one dirty-navigation wrapper and one relative-time rule. |
| GIT-R10 | Planned | P2 | Leaving an earlier version never silently returns to a branch's latest save: after a Move the branch chooser starts on a new branch and says what choosing an existing one does, a project left on an earlier version asks on startup, a branch made there keeps milestones as milestones, and files changed there are reported as not recorded. |
| GIT-R11 | Planned | P2 | "Start a branch from this version" makes a branch at any milestone or save, folded saves included, and opens it in one step, leaving the current branch as it was; with Restore it replaces Move on the history rows. |
| GIT-R12 | Reverify | P2 | After a branch switch, a restore, Catch up or Spin off a copy, the server reloads the pipeline and the form and the canvas shows the files on disk; a save from the previous document is refused. |
| GIT-R13 | Planned | P2 | A checkout that stops part-way on Windows (a file open in Excel, an antivirus hold) is rolled back and names the file, and a project is never reported ready over a half-changed tree. |
| GIT-R14 | Planned | P2 | Deleted branches stay restorable from the branch manager whatever their name, and deleting or archiving the current branch leaves the analyst on its last milestone rather than the default branch's seed project. |
| GIT-R15 | Planned | P3 | The git UI's words are the analyst's: no "detached", "repo", "ledger", "fold" or "hexadecimal". |

## Planned improvements

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
refuse with the hand-written reason. `commit_save` reads a failed `git status` as
"nothing changed" and returns no save, so a failing status check skips the capture
without a word.

**Plan:** Validate typed names with `git check-ref-format --branch` and raise
`GitDomainError` naming the rule; raise `GitDomainError` with the refresh
message for inspection, fetch and push timeouts, launch failures and non-zero
exits, keeping stderr in the log. Preserve the configured `core.sshCommand`, `GIT_SSH`
or `GIT_SSH_COMMAND` and Git's precedence; set Haute's default OpenSSH command only
when none is configured. Do not append OpenSSH flags to arbitrary commands: the first
OpenSSH option wins, and a wrapper or Plink can use different syntax. Prevent prompts
at the process boundary with closed stdin (`DEVNULL`), no controlling terminal where
supported, `GIT_TERMINAL_PROMPT=0` and disabled OpenSSH askpass. Apply an outer deadline
to inspection, fetch and push, and clean up the launched transport process tree on
timeout, including on Windows. A custom transport must obey the noninteractive
contract; an outer deadline bounds a transport that hangs rather than proving it
honours OpenSSH environment settings. Let the history error propagate; call
`_assert_no_git_op_in_progress` at the top of
`commit_save` and `commit_milestone`, and let a failed status check in `commit_save`
raise rather than return no save.

**Acceptance:** `my branch` is refused naming the rule; a push against an
unreachable remote answers the refresh message. Behavioural subprocess tests show a
configured command reaches Git unchanged, including conflicting OpenSSH options and
a non-OpenSSH wrapper; the wrapper receives no injected OpenSSH flags. A prompting or
hanging fake transport is terminated with its children within the deadline and reports
the refresh message. A corrupt object store
answers an error, not an empty list; a save during a merge answers the
in-progress message; a save whose status check fails answers an error.

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

**Dependencies:** None.

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

### GIT-R10 — Leaving an earlier version without a silent return
**Why:** Four gaps meet an analyst who has Moved to an earlier version.
- **The chooser:** after a Move the branch chooser starts on an existing branch,
  and choosing it opens that branch's latest save without saying so.
- **No prompt:** startup opens no prompt for a detached project. The only cue
  after a Move is a one-time toast from `sessionStorage`, and after a restart or in
  a new tab it is the red "Detached at …" button.
- **No milestones:** creating a branch from the chooser while detached runs
  `git checkout -b` at HEAD without crystallising a milestone. When HEAD is a save,
  the new branch's first-parent chain is the old ledger's saves. The test
  project's `branch-testing`, made from the save `ed71c35`, lists 122 first-parent
  commits and no merges, so its Milestones list shows 122 saves as milestones.
- **Unrecorded files:** with no working branch recorded, `capture_save_in_ledger`
  returns nothing uncaptured. A file the Files panel writes or deletes there is on
  disk only, and nothing says so.

**Plan:**
- **The chooser on a detached project** (after a Move until `GIT-R11` removes it,
  or after a checkout outside Haute): it starts on "Create new branch…" and says
  "Choosing an existing branch opens its latest save; the version open now is kept
  only by a new branch."
- **On startup:** open the chooser when readiness is `detached`, and show a lasting
  banner rather than the toast.
- **A branch made from a save:** `set_working_branch(create=True)` from a save off
  every first-parent spine crystallises a milestone first, as
  `create_working_branch` does.
- **Unrecorded files:** a capture with no working branch names its paths as
  uncaptured with a warning, as a skipped capture does.
- **The menu wording:** rename "New branch & move work here" to say that it carries
  the later saves to the new branch and winds this one back to the chosen point.

**Acceptance:**
- **The chooser:** Move, then Save, starts the chooser on a new branch.
- **Restart:** restarting the server while detached opens the chooser.
- **Milestones:** a branch created from a detached save lists its milestones, not
  the saves before it.
- **Files:** a file uploaded while detached is reported as not recorded.

**Dependencies:** None.

**Evidence:** `frontend/src/components/WorkingBranchModal.tsx`;
`frontend/src/App.tsx`;
`frontend/src/components/BranchIndicator.tsx`;
`src/haute/_git_setup.py::set_working_branch`;
`src/haute/_git_transactions.py::_crystallize_milestone`;
`src/haute/routes/_save_pipeline.py::capture_save_in_ledger`;
`src/haute/routes/project_files.py`;
`frontend/src/panels/GitPanel.tsx`.

### GIT-R11 — Start a branch from a version in one step
**Why:** Trying an idea from an earlier version takes "New branch from here" and
then a Switch. "New branch & move work here" moves the later saves rather than
opening the chosen version. A save folded into a milestone is offered the fork
menu, but `create_working_branch` refuses anything other than a first-parent
milestone or a pending save. Move then remains beside Restore on every row, and
it leaves the analyst on no branch (`GIT-R10`).

**Plan:**
- **The engine:** add a mode of `create_working_branch` that makes the pair at the
  chosen point and checks out its ledger, leaving the current pair's refs
  untouched. It crystallises a milestone at a save, folded saves included, as
  `_crystallize_milestone` does for a pending save, and the switch's dirty guard
  applies.
- **In the panel:** "Start a branch from this version" on every row and in the
  menu, then one naming step.
- **Move goes:** Restore, Start a branch and View cover Move's uses, and on 27
  October 2026 the owner chose to remove it. That takes out:
  - the row button and menu item, the move half of `MoveConfirmModal` (renamed
    for the restore it keeps), the store's `requestMove`/`moveTarget` and the
    move branch of `App.tsx`'s `handleVersionConfirmed`;
  - the after-move toast, `move_to_commit` and `POST /api/git/move`;
  - Move in both git specifications ("go back to an earlier version" becomes
    Restore).

  A project can still be left detached by a checkout outside Haute, so `GIT-R10`'s
  chooser and startup prompt stay.

**Acceptance:** From a folded save, Start a branch names the branch and opens it
at that version in one step, and the previous branch's milestones and saves are
unchanged. Neither the rows, the menu nor the API offers Move.

**Dependencies:** None.

**Evidence:** `src/haute/_git_transactions.py::create_working_branch`;
`src/haute/_git_transactions.py::_on_first_parent`;
`src/haute/_git_transactions.py::_crystallize_milestone`;
`src/haute/routes/git.py::git_move`;
`frontend/src/panels/GitPanel.tsx`.

### GIT-R12 — The canvas follows a tree change
**Why:** An in-app branch switch does not reload the page. The comment beside
`switchNow` says the checkout reaches the canvas through the websocket sync, but
that path is closed:
- the route holds `pause_watcher`;
- the watcher drops every event while paused and for a one-second settle window
  after;
- the client sends a resync only when its websocket opens;
- no git route broadcasts.

The same holds for the undo and redo of a switch. A pipeline save from the
previous document cannot land on the new branch: `_require_base_revision` compares
the client's base revision with the file on disk and raises
`StaleDocumentRevisionError`. Not yet run: whether the canvas keeps showing the
previous branch's pipeline until a page reload, so the analyst's next Save meets a
stale-document refusal. No frontend test asserts what the canvas shows after a
switch.

**Plan:** Reproduce first in a scratch project with two branches holding
different pipelines: switch in the branch manager, read the canvas, Save. Then,
after every tree-changing git route (switch, restore, Catch up, Spin off a copy,
the undo legs), the server reloads the pipeline index, the document and the form,
then broadcasts so open clients load the new document. A route-to-client test
covers it, and the stale-document refusal stays.

**Acceptance:** After a switch the canvas shows the new branch's pipeline without
a page reload, and a save from the old document is still refused.

**Dependencies:** None.

**Evidence:** `frontend/src/components/BranchManager.tsx`;
`frontend/src/hooks/useWebSocketSync.ts`;
`frontend/src/utils/vcHistory.ts`;
`src/haute/routes/_helpers.py::pause_watcher`;
`src/haute/routes/_helpers.py::watcher_is_paused`;
`src/haute/routes/git.py::git_set_working_branch`;
`src/haute/routes/_save_pipeline.py::StaleDocumentRevisionError`;
`src/haute/server.py`.

### GIT-R13 — Checkouts that stop part-way on Windows
**Why:** `move_to_commit` runs the volatile wipe, `checkout --detach` and the
clone-state clear with no rollback.
- **If the checkout stops part-way:** for example, Windows refuses to replace a
  file open in Excel, or an antivirus hold stops it. HEAD stays on the ledger, the
  clone state stays set and the tree is part old, part new.
- **What the analyst sees:** readiness still answers `ready`, and the analyst reads
  "Operation failed. Check the server logs for details."
- **What gets recorded:** the next Commit's sweep records the mixed tree.
- **The other checkouts:** `set_working_branch`'s checkouts roll back with another
  checkout, which can fail on the same lock.
- **The in-progress check:** `_assert_no_git_op_in_progress` guards only Move and
  Restore.

What git leaves when Windows refuses a file part-way has not been probed, and no
test injects a failed checkout.

**Plan:** Probe first: hold a project file open with an exclusive handle on
Windows, Move, and record what git leaves. Then one checkout helper for every
tree-changing checkout:
- it checks the changing paths (`git diff --name-only HEAD <target>`) are writable
  first;
- it translates git's "would be overwritten", "unable to unlink" and "Permission
  denied" into a `GitDomainError` naming the file and asking for it to be closed;
- on failure it writes HEAD's tree back;
- when that also fails, it records `.haute/interrupted.json`, so readiness answers
  an `interrupted` state naming the file with Retry, and Commit refuses until it
  is resolved;
- the in-progress check runs before every checkout.

**Acceptance:** A Move with a locked file answers "Close <file> and try again" and
leaves the tree and branch as they were. A failed rollback shows the interrupted
state with Retry. Tests inject a failed checkout.

**Dependencies:** `GIT-R02` translates the errors; `GIT-R05` supplies the one
compensation helper; `GIT-R07` corrects the count of readiness states that the new
state adds to.

**Evidence:** `src/haute/_git_archive.py::move_to_commit`;
`src/haute/_git_setup.py::set_working_branch`;
`src/haute/_git_read_models.py::working_branch_status`;
`src/haute/_git_transactions.py::commit_milestone`;
`src/haute/_git_core.py::_assert_no_git_op_in_progress`;
`tests/test_git_improvements.py`.

### GIT-R14 — Deleted branches stay restorable
**Why:**
- **Deleting or archiving the current pair:** `_switch_away_if_active` checks out
  the default branch whenever one exists. On the test project `main` is the
  "Initial commit" seed with no `rating/config`, so the canvas looks wiped. The
  specification's "detaches at a preserved commit" describes only the fallback.
- **Undelete:** it is reachable only from the toolbar's undo stack, which a page
  reload clears, and a delete of the current branch reloads the page and records
  nothing.
- **Trash keyed by bare name:** the pin and tombstone are keyed by the branch name
  alone (`_trash_ref`, `record_trash`).
  - Deleting a recreated name again overwrites the first deletion's pin and
    tombstone.
  - The delete's compensation removes the tombstone and both pins whether or not
    it wrote them.
  - Undelete refuses while the name exists. The test project's `branch-testing`
    tombstone is stuck behind the live branch of that name.
  - When the 20-tombstone cap rolls over, the tombstone goes but the pin stays,
    and undelete reads only the tombstone.
- **A crash between two ref steps:** for example, between delete's two
  `branch -D` or archive's two renames. It would leave an orphan ledger or a
  half-archived pair that create, undelete and restore refuse, and nothing checks
  pair health at startup. This one is inferred.

**Plan:**
- Key tombstones and pins by name and deletion time.
- Show a Deleted list in the branch manager with Restore, and Restore under
  another name when the name is taken.
- Undelete from the pin when its tombstone has rolled over, or drop the pin with
  it.
- The delete's compensation removes only what it wrote.
- Deleting or archiving the current pair detaches at the working branch's last
  milestone and opens the chooser.
- A startup check names orphan ledgers and half-archived pairs, with Repair.

**Acceptance:** Delete, recreate and delete `x` again: both are restorable from
the list, under another name while `x` exists. Deleting the current branch leaves
its last milestone open with the chooser. An orphan `x-save` is reported with
Repair.

**Dependencies:** `GIT-R07` owns the delete dialog's wording; `GIT-R03` the dirty
tree at delete; `GIT-R10` the startup prompt.

**Evidence:** `src/haute/_git_archive.py::_switch_away_if_active`;
`src/haute/_git_archive.py::_trash_ref`;
`src/haute/_git_archive.py::delete_working_pair`;
`src/haute/_git_archive.py::undelete_working_pair`;
`src/haute/_git_state.py::record_trash`;
`src/haute/_git_state.py::read_trash`;
`frontend/src/components/BranchManager.tsx`;
`frontend/src/utils/vcHistory.ts`.

### GIT-R15 — The git UI in the analyst's words
**Why:** The git UI shows its machinery:
- the toolbar reads "Detached at <sha>";
- the commit-code tooltip explains a "longer hexadecimal string";
- the pending box reads "Out-of-version saves (n) - to fold into next milestone";
- the divergence dialog says "the repo is" and "a save ledger";
- the push control's warning reads "saves on the ledger … folded into a milestone"
  and "pushed on the ledger branch";
- the branch manager's badges read "uncommitted" and "unsaved".

**Plan:**
- **The toolbar after a Move:** "Viewing an earlier version", with its actions
  from `GIT-R10`.
- **The tooltip:** no hexadecimal.
- **The pending box:** "Saves since the last milestone".
- **The divergence dialog:** plain words.
- **The badges:** "changes not saved" and "saves not in a milestone".
- **Explanations:** go in `docs/`, not on screen.

**Acceptance:** None of the listed words appears in the git UI's visible text,
and the tests asserting the old strings assert the new ones.

**Dependencies:** `GIT-R07` owns the delete dialog's wording; `GIT-R10` the
toolbar's actions after a Move.

**Evidence:** `frontend/src/components/BranchIndicator.tsx`;
`frontend/src/panels/GitPanel.tsx`;
`frontend/src/components/DivergenceModal.tsx`;
`frontend/src/components/RemotePushControl.tsx`;
`frontend/src/components/BranchManager.tsx`.
