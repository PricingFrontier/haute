# Git integration roadmap

## Scope

Version control inside Haute, as [the git integration
specification](../git-integration/high-level.md) describes it: the working
branch and its save ledger, catching up from a remote, and what a failed git
command leaves behind. The packages below come from the review plan's runs
of 11 November 2026. `VCS-08` comes from the main session's runs: a
tracked file edited by hand, then each operation that replaces working files.
`VCS-09` comes from the plan's browser sweep of what happens around an
open editor, also on 11 November, run again by the main session with two
tabs and with a terminal.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| VCS-08 | Planned | P2 | A switch, a catch-up, an archive and a new branch that meet a file edited by hand name it and offer to record it or put it back, instead of "Operation failed" or advice that does not apply. |
| VCS-09 | Planned | P2 | An open tab learns when the project changes branch, and a save made from a tab that shows another branch is refused naming both, instead of landing on the other branch, moving the checkout back or leaving its files uncommitted with "see server log". |

## Planned improvements

### VCS-08 — A file edited by hand does not strand the analyst
**Why:** Reproduced on 11 November 2026 through the routes, with nothing
injected. An analyst opens `rating/files/data/rates.csv` in Excel, changes a
rate and saves it. Git now shows the file as modified, and the operations
that replace working files treat it in four ways.

A switch to a branch where the file is the same succeeds and carries the
edit across. A switch to a branch where the file differs answers "Operation
failed. Check the server logs for details."; git's words, "Your local
changes to the following files would be overwritten by checkout", stay in
the log. Catch up answers "You have unsaved changes. Save or discard them
before catching up.", archiving the current branch answers "You have unsaved
changes on this branch. Save or discard them before archiving it.", and
creating and opening a new branch answers the same about "opening the new
branch". Spinning off a copy holds the same sentence (read from the source).
A Restore records the file first, as the specification says: "It records
everything on disk first, with the milestone's sweep".

A file that git does not track blocks a switch in the same way. A file
copied into the pipeline folder by hand, with the name of a file the other
branch holds, makes the switch answer the same generic text (git: "The
following untracked working tree files would be overwritten by checkout").

The advice does not work for this file. The editor counts as unsaved only
what is on its canvas or in the workbench form, and its Save and its Discard
act on those (read from the source). The editor's Save was run next, with a
real change on the canvas: it answered 200 with a commit, the hand-edited
file stayed modified, and Catch up refused again. A Commit would record the
file, but with a colleague's push waiting it answers the fork warning,
"Saving a milestone now will fork 'origin'". So the analyst can neither
catch up nor commit without forking, and none of the refusals names the
file.

**Plan:** One rule for every operation that replaces working files (a
switch, create and open, catch up, archive, spin off): where tracked files
differ from version history, or a file git does not track sits where the
destination has one, answer naming them and offer the two things an
analyst can want. Record them as one save, with the sweep a Restore already
uses, or put them back as version history has them. Carry on with the
operation after either. Drop "Save" from the advice, since the editor's Save
does not record a file edited by hand.

**Acceptance:** With a tracked data file edited by hand, a switch to a
branch where the file differs, a catch-up, an archive of the current branch
and a create-and-open each answer naming the file and offering to record it
or to put it back. After recording, the operation completes and the edit is
among the branch's saves. After putting back, the file is as version history
has it and the operation completes. A switch that an untracked file is in
the way of answers naming the file. None answers "Operation failed." and
none advises Save.

**Dependencies:** None.

**Evidence:** `src/haute/_git_setup.py::set_working_branch`;
`src/haute/_git_remote.py::fast_forward_pair_from_tracking`;
`src/haute/_git_remote.py::branch_away`;
`src/haute/_git_archive.py::_leave_if_active`;
`src/haute/_git_transactions.py::create_working_branch`;
`src/haute/_git_transactions.py::restore_version`;
`frontend/src/hooks/useProjectDirty.ts`;
`frontend/src/components/RemotePushControl.tsx`.

### VCS-09 — An open editor learns that the project changed branch
**Why:** Reproduced in a browser on 11 November 2026, by the review plan's
sweep of what happens around an open editor and again by the main session.
The page reads the project's branch when it loads and after its own git
actions. Nothing tells an open tab that the branch has changed, and a save
does not ask. Three ordinary cases were run.

- Two tabs on one project. One holds an unsaved rename. In the other the
  analyst creates the branch `other` and switches to it. The first tab's
  toolbar still reads `pricing/haute-e2e/work` 45 seconds later, and after
  the tab is brought to the front, and it shows no notice. Its Save answers
  "Saved → rating/main.py". The rename is recorded on `other`; the branch
  the toolbar names has nothing. A reload shows `other`.
- A checkout in a terminal. With the editor open, the analyst runs
  `git checkout colleague` to look at a colleague's branch, whose pipeline
  differs. The tab takes the file, "Pipeline updated from file", and its
  toolbar keeps the old branch. The analyst renames a node and saves:
  "Saved → rating/main.py", then "Changes saved; version capture failed
  (git error — see server log)." The save's thirteen files are left as
  uncommitted changes on the colleague's branch, and the analyst's own
  branch has nothing. The log has git's reason: the save tried to check
  out the analyst's branch, and git would not overwrite the file the save
  had just written.
- A new branch in a terminal. The analyst runs `git switch -c mine`, which
  changes no file. The tab shows nothing. Its next Save answers "Saved →
  rating/main.py" and checks the project out on the working branch Haute
  recorded: the terminal is no longer on `mine`, and nothing says so.

After the second case a reload does tell the analyst: the toolbar reads
"Branch changed externally" and a dialog says "You've moved off your working
branch". Its first choice, "Go back to pricing/haute-e2e/work", then answers
"Could not switch working branch: Operation failed. Check the server logs
for details." That is the refusal `VCS-08` describes, met here through
files Haute's own save wrote. Its second choice, "Make colleague my working
branch", succeeds and leaves the save's files uncommitted.

One step causes all three. A save's capture resolves the ledger of the
working branch Haute recorded and checks it out whenever the checkout is
anywhere else. That is right the first time a branch is saved to, and wrong
once the analyst has moved the project. The readiness the page loads has a
state for it, `divergent`, and the dialog above. The commands' own captures
follow the other rule: theirs "is made only while the checkout is on the
chosen working branch's ledger; on any other branch, or a detached `HEAD`,
it is skipped, saying where the checkout is, so no other branch's files are
replaced".

**Plan:** Tell open tabs when the branch changes. The live-sync connection
sends the readiness when the checked-out branch or the recorded working
branch changes, whoever changed it, and the page reads it as it does after
its own git actions, so the toolbar and the dialog follow without a reload.
Give a save the rule the commands' captures have. A save names the working
branch its tab shows. When that is not the recorded one, or the checkout is
on neither that branch nor its ledger, the save is refused before anything
is written, naming both branches, and the unsaved work stays in the tab. A
save never checks out a branch the analyst left.

**Acceptance:** In a browser test with two tabs, a switch in one changes
the other's toolbar without a reload, and a Save sent from a tab before it
has learned of the switch is refused naming both branches. After
`git checkout` of another branch in a terminal, the open tab shows the
dialog; a Save made before a choice is refused naming where the project is,
writes nothing and leaves the checkout alone. After `git switch -c` in a
terminal, a Save does not move the checkout.

**Dependencies:** `VCS-08` words the failed switch that the dialog's first
choice meets. `FEG-05` covers a tab with unsaved work whose file changes
under it.

**Evidence:** `src/haute/_git_setup.py::resolve_ledger`;
`src/haute/_git_transactions.py::commit_save`;
`src/haute/routes/_save_pipeline.py::capture_save_in_ledger`;
`src/haute/_git_read_models.py::working_branch_status`;
`src/haute/cli/_init_cmd.py::capture_new_files`;
`frontend/src/components/BranchIndicator.tsx`;
`frontend/src/components/DivergenceModal.tsx`;
`frontend/src/stores/useGitStore.ts`;
`frontend/src/hooks/useWebSocketSync.ts`;
`specs/git-integration/low-level.md`.

