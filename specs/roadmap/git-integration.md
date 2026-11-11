# Git integration roadmap

## Scope

Version control inside Haute, as [the git integration
specification](../git-integration/high-level.md) describes it: the working
branch and its save ledger, catching up from a remote, and what a failed git
command leaves behind. The packages below come from the review plan's runs
of 10 November 2026. Each was reproduced on Windows in a scratch
repository: `VCS-01` with a real open file and a local remote, `VCS-02` and
`VCS-03` with nothing injected into Haute, `VCS-04` by killing the server
process, and `VCS-05` in a browser with two pages open on one project.
`VCS-06` and `VCS-07` come from the main session's runs of 11 November
behind the plan's sweep of damaged requests: folders and files renamed on
disk, and branch names typed through the routes, with nothing injected.
`VCS-08` comes from the main session's runs of the same day: a tracked
file edited by hand, then each operation that replaces working files.
`VCS-09` comes from the plan's browser sweep of what happens around an
open editor, also on 11 November, run again by the main session with two
tabs and with a terminal.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| VCS-01 | Planned | P2 | A catch-up that cannot remove an open file leaves the branches and the files as they were, and removes the file on the retry. |
| VCS-02 | Planned | P2 | Commit and Restore succeed when a deletion or a rename is already staged, instead of failing every time with "Check the server logs". |
| VCS-03 | Planned | P2 | A leftover git lock, a folder git does not trust, a damaged git config and a commit that cannot be signed are each reported with their cause, not as "Not a git repository", a missing identity or "see server log". |
| VCS-04 | Planned | P3 | A restore the server died in reads as interrupted at the next start, and Retry finishes it. |
| VCS-05 | Planned | P3 | A branch delete is refused when the branch is no longer as its confirmation described it. |
| VCS-06 | Planned | P2 | A save, an upload and a replace reach version history after their folder or file is renamed to another letter case, and a capture that records nothing for a changed file says so. |
| VCS-07 | Planned | P2 | A branch name that cannot exist beside another branch is refused naming the clash, when a branch is created, restored or brought back from Deleted. |
| VCS-08 | Planned | P2 | A switch, a catch-up, an archive and a new branch that meet a file edited by hand name it and offer to record it or put it back, instead of "Operation failed" or advice that does not apply. |
| VCS-09 | Planned | P2 | An open tab learns when the project changes branch, and a save made from a tab that shows another branch is refused naming both, instead of landing on the other branch, moving the checkout back or leaving its files uncommitted with "see server log". |

## Planned improvements

### VCS-01 — A catch-up that could not remove a file puts the branch back
**Why:** Reproduced. A colleague deletes a file and pushes. The analyst
catches up while a program still holds that file open. Git cannot remove an
open file on Windows; it moves the branch all the same and exits 0. Haute
answers "utility/old_rates.py is open in another program, so Haute could not
replace it. Close it and try again." and leaves the clone in between: the
save ledger is at the colleague's commit, the working branch is not, the
file is still on disk and now untracked, and nothing records an interrupted
operation. When the analyst closes the file and tries again, the working
branch catches up and the call succeeds, and the deleted file stays on disk
for good. A utility module left that way is still imported by this analyst's
pipelines and by nobody else's. The function that runs the merge documents
the opposite: "HEAD is then put back where it was and the out-of-step files
written from it; any still out of step record the clone interrupted". It
decides whether HEAD moved by comparing the branch's name before and after,
and a fast-forward moves the branch's tip without changing its name, so the
put-back never runs. The file left behind is untracked, so the check for
files still out of step, which reads tracked changes, finds none. The
caller's own compensation does not run either: it marks the ledger as
advanced only after the merge returns. A held file that the colleague
changed, not deleted, is handled, because git exits 1 there and leaves the
branch. The 28 October audit recorded this lead without running it.

**Plan:** Compare commits, not names: record the commit HEAD points at
before the command, and when a file was held put the branch back to that
commit, whatever git's exit code. Count a file git reported as not removed
as out of step whether it is tracked or not, so that it is either removed
once it is free or recorded as interrupted. Let the caller's compensation
read the refs, not a flag set after the call.

**Acceptance:** With a file the remote deleted held open, a catch-up is
refused naming the file, and the ledger, the working branch and every file
are as they were before the call. After the file is closed, a second
catch-up succeeds and the file is gone. A test does both on Windows with a
real open handle, and a held file the remote changed still passes.

**Dependencies:** None.

**Evidence:** `src/haute/_git_core.py::_rewrite_tree`;
`src/haute/_git_remote.py::fast_forward_pair_from_tracking`;
`src/haute/_git_state.py::record_interrupted`.

### VCS-02 — Commit and Restore work with a deletion or rename already staged
**Why:** Reproduced on 10 November 2026, with nothing injected. An analyst
removes a tracked file with `git rm`, renames one with `git mv`, or stages a
deletion in an editor's git panel; `git add .` after deleting a file does
the same. From then on Commit answers "Operation failed. Check the server
logs for details." every time, Restore answers the same, and the capture
route answers "Changes saved; version capture failed (git error — see
server log)." Pipeline saves keep working, because each commits only its own
files, and readiness reads `ready`, so nothing in the editor points at the
staged change. The file can be anywhere in the repository: a staged deletion
of `README.txt` did the same. The log holds git's words: `fatal: pathspec
':(literal)rating/files/data/old.csv' did not match any files`.

Before a milestone or a restore, the sweep lists every tracked path that
differs from the last save, deleted paths included, and passes the list to
the save capture, which runs `git add` on each path. Git refuses to add a
path that is in neither the folder nor its index. A staged deletion is such
a path, and so is the old name of a staged rename. A file deleted in the
file manager is recorded as it should be, because its deletion is not staged
yet and the same `git add` stages it. The comment beside the command says
"the explicit-path add also stages deletions of tracked paths", which holds
only until something else has staged them.

Haute reaches the state without help. Deleting a Pipeline file stages the
deletion and then commits it. With the process killed between the two, the
restart found the deletion staged and the file still in version history,
readiness `ready`, no pending change shown, and Commit failing as above. A
restore cut short leaves a staged deletion for every file the restored
version has and the later one had removed, with the same result (`VCS-04`).

**Plan:** In the save capture, stage each path by what it is. A path that is
in neither the folder nor the index needs no `git add`: its removal is
already staged, and the path-scoped commit that follows records it (checked
with git in a scratch repository, for a deletion and for both names of a
rename). Keep the rule in that one function, so the milestone's sweep, the
restore's sweep, the capture route and a file delete share it.

**Acceptance:** After `git rm` of a tracked file, after `git mv`, and after
a staged deletion outside the pipeline folder, Commit succeeds and the
milestone holds the deletion or the rename; Restore succeeds; `POST
/api/git/capture` takes a deleted path. A Pipeline file delete whose commit
did not happen is recorded by the next Commit.

**Dependencies:** None.

**Evidence:** `src/haute/_git_transactions.py::commit_save`;
`src/haute/_git_transactions.py::_residual_tracked_changes`;
`src/haute/_git_transactions.py::_unrecorded_project_files`;
`src/haute/routes/project_files.py::_delete`.

### VCS-03 — A git command that git refuses is reported with git's reason
**Why:** Reproduced on 10 November 2026 with four everyday causes, none
injected into Haute.

- A `.git/index.lock` left by a git command that crashed, or held by one
  still running. Readiness reads `ready`. Every save answers "Changes saved;
  version capture failed (git error — see server log)." and Commit answers
  "Operation failed. Check the server logs for details." Git's own message,
  which only the log holds, says another git process seems to be running
  and that the file can be removed by hand if none is.
- A folder git does not trust because another account owns it, as on a
  network share (switched on here with git's own
  `GIT_TEST_ASSUME_DIFFERENT_OWNER`). Git says "detected dubious ownership
  in repository" and gives the command that adds an exception. Haute's
  readiness answers `no-repository`, so the toolbar reads "Git not
  initialised" with the hint "Run git init in this project to enable version
  control."; Commit answers "Not a git repository. Run 'git init' first.";
  and every save answers "Changes saved, but version capture needs a git
  identity. Set your name and email to keep version history." and opens the
  identity prompt, although an identity is set.
- A damaged line in `.git/config`. Git says "bad config line 29 in file
  .git/config". Haute answers exactly as for the untrusted folder.
- A git set to sign every commit (`commit.gpgsign`, usual where signed
  commits are required) whose signing program fails, as a gpg with no key
  or one that cannot ask for its passphrase does (run with a program that
  exits at once). Git says "error: gpg failed to sign the data". Every
  save answers the "see server log" warning, and Commit and Restore answer
  "Operation failed. Check the server logs for details.", so version
  history stops and nothing mentions signing. Each failed save leaves its
  files staged, which for a deleted file is the state `VCS-02` describes.

In the second and third cases the repository exists and holds the project's
history, and `git init` repairs neither. The readiness check asks git
whether the folder is inside a work tree and reads any failure as "no"; the
identity check reads a failed `git config` as "not set". The helper both use
returns only success or failure and drops git's exit code and message. A
second helper beside it exists "for callers that must distinguish a
SEMANTIC non-zero exit from a real failure" and is used elsewhere. Keeping
git's raw message out of answers is deliberate, since it can hold paths and
remote addresses, so the remedy is to recognise these causes, not to print
the message. The specification already separates `git-unavailable` from
`no-repository` "so the UI does not offer init".

**Plan:** Tell a refusal from a "no". When `rev-parse` or `config` ends with
git's fatal exit code, or a commit fails, match the message against a short
list (a lock file present, an untrusted folder, a config file git cannot
read, a commit that could not be signed) and carry the reason. Readiness
then answers a state other than `no-repository`, with the reason in its
errors. The capture warning and Commit's refusal use sentences written for
each cause, with what to do: wait, or delete `.git/index.lock`
when no git command is running; the `safe.directory` command with the
folder; the config file and the line; that the commit could not be signed.
A failure outside the list keeps the generic text. Whether an automatic save
should be signed at all is worth deciding with this: saves already skip the
project's hooks, which run at the milestone.

**Acceptance:** With a `.git/index.lock` present, a save's warning and
Commit's refusal name the lock and what to do. With
`GIT_TEST_ASSUME_DIFFERENT_OWNER=1`, and with a damaged `.git/config`,
readiness is not `no-repository`, no answer mentions `git init`, the
identity prompt does not open, and the message names the cause. With
`commit.gpgsign` on and a signing program that fails, a save's warning and
Commit's refusal say the commit could not be signed.

**Dependencies:** None.

**Evidence:** `src/haute/_git_core.py::_is_git_repo`;
`src/haute/_git_core.py::_run_git_ok`;
`src/haute/_git_core.py::_assert_git_repo`;
`src/haute/_git_setup.py::get_identity`;
`src/haute/_git_read_models.py::working_branch_status`;
`src/haute/routes/_save_pipeline.py::capture_save_in_ledger`;
`src/haute/_git_transactions.py::commit_save`;
`frontend/src/components/BranchIndicator.tsx`.

### VCS-04 — A restore cut short is found at the next start
**Why:** Reproduced on 10 November 2026. A Restore records its save on the
ledger first and writes the files after. When a file cannot be written, the
clone is marked interrupted, readiness names the files, and Retry writes
them: the specification describes this for a restore that fails after its
save was recorded. The mark is written only by the code that catches the
failure, so a server process that dies between the two steps leaves no
mark. Killed right after the ledger moved, the restart found the restore as
the ledger's newest save ("Restored 40c845c7: Updated 4 files"), the files
on disk still the later version, readiness `ready`, the pipeline loading
`ready`, and no `.haute/interrupted.json`.
Nothing says the restore did not finish. Commit then failed with "Operation
failed. Check the server logs for details.", because the file the restored
version adds back shows as a staged deletion (`VCS-02`). With that fixed, a
Commit would record the files on disk as "Updated project files" on top of
the restore, undoing it without a word (read from the sweep's rule, not
run). No work is lost, since every version stays in the history. The window
is the write-back of the files, one git command long.

**Plan:** Write the interrupted mark, naming the files the restore will
replace, before the ledger moves, and clear it once the files are written.
A process that dies in between then leaves the state the specification
already handles.

**Acceptance:** With the process killed after the ledger moved, the next
start reads `interrupted` naming the files, Commit and Restore refuse until
Retry, and Retry writes the restored version. A restore that completes
leaves no mark.

**Dependencies:** `VCS-02` for the Commit that follows.

**Evidence:** `src/haute/_git_transactions.py::restore_version`;
`src/haute/_git_transactions.py::retry_interrupted`;
`src/haute/_git_state.py::record_interrupted`.

### VCS-05 — A branch delete names what was confirmed
**Why:** Reproduced on 10 November 2026 in a browser, with two pages open on
one project. Page one opens the branch manager's confirmation for a branch
that is not the current one: "Delete scratch? It goes to Deleted and can be
restored from there." Page two switches the project onto that branch (and,
in the sweep's second run, saves work there). Page one's confirmation keeps
its text, and its Delete removes the branch the project is now on: HEAD is
left detached at the last milestone, page one reads "Viewing an earlier
version", and the work saved from page two is in Deleted. Nothing is lost,
since Deleted keeps the branch and an undelete restores it. But the analyst
confirmed something else: the current branch is normally deleted behind a
further sentence, "You'll be returned to the branch chooser.", and a check
for unsaved edits.

The specification has the server refuse a delete over unmerged saves, or
over the active branch's changes, "unless `confirm=True`". The editor sends
that flag with every delete, on the strength of a dialog that described the
branch as it was when it opened. A file delete in the Files panel names the
revision it was confirmed against, and is refused when the file moved
("changed since you chose to delete it. Look again, then retry."). A branch
delete names nothing.

**Plan:** Send with the delete what the confirmation showed: the branch's
two tips and whether it was the current one. Refuse when either differs, in
the Files panel's words, and let the manager show the branch as it now is.

**Acceptance:** With a branch switched onto, or saved to, after its
confirmation opened, Delete is refused naming the change and the branch is
as it was; an unchanged branch is deleted as now. A browser test drives two
pages on one project.

**Dependencies:** None.

**Evidence:** `frontend/src/components/BranchManager.tsx`;
`src/haute/_git_archive.py::delete_working_pair`;
`src/haute/routes/project_files.py::_delete`.

### VCS-06 — A save is captured after its folder's letter case changes
**Why:** Reproduced on 11 November 2026 with nothing injected. An analyst
renames a folder in Explorer from `rating` to `Rating`. Windows allows it,
and git on Windows, which `git init` sets to ignore letter case, does not
notice: `git status` stays clean and git keeps the old spelling. Haute names
files as the disk spells them, so the editor's document now carries
`Rating/main.py`. From then on every save answers 200 with a null commit, no
warning and an empty list of files left out: the same answer as a save that
changed nothing. It was run four ways with the same result: the pipeline
folder renamed, with `haute.toml` left alone and with it edited to match; a
data folder under Pipeline files renamed, then a new upload and a replace
through the names the Files panel lists; and one data file renamed, then
replaced. The files on disk are right. Version history is not: the save
ledger keeps the content from before the rename, the panel's pending saves
list one save, and `git status` shows the files modified or staged.

Three things follow, each of them run. A Commit records the content, and
the next save is left out again, so the saves between milestones are never
kept. A Restore records what is on disk first, so nothing is lost by it. A
switch to another branch is refused with "Operation failed. Check the server
logs for details.": git will not overwrite changes that Haute believes are
on the ledger.

The capture hands git the request's spelling as a literal path, and git
compares it with its own spelling letter for letter. For a file git already
tracks, the first check, whether the path has changed, sees nothing. A new
file is found on disk and staged under git's spelling of the folder, and the
list of staged names, asked for under the request's spelling, comes back
empty. Either way the capture concludes that nothing changed. The plan's
sweep of damaged requests found the same answer for a request that spells
an existing folder in other capitals (`DATA/b.csv` into `data`).

**Plan:** Have the capture ask git for its own spelling of each path first:
the tracked name for a file git knows, and for a new file the spelling git
has for its nearest tracked folder. Use that spelling in every command.
Then, as a net under any other mismatch: where a write changed a file and
the capture recorded nothing for it, compare the file with the ledger's
copy before answering, and answer as a failed capture does when they
differ, with the save's warning and the file in the list the editor keeps
for the next capture.

**Acceptance:** With the pipeline folder, a data folder and a data file each
renamed to another letter case after a first capture, the next save, upload
and replace each answer a commit whose tree holds the new content, and a
switch to another branch then succeeds. A capture that records nothing for
a file whose content differs from the ledger's answers the save's warning
and names the file.

**Dependencies:** None.

**Evidence:** `src/haute/_git_transactions.py::commit_save`;
`src/haute/_git_transactions.py::_literal`;
`src/haute/routes/_save_pipeline.py::capture_save_in_ledger`;
`src/haute/_git_setup.py::set_working_branch`.

### VCS-07 — A branch name that clashes with another is refused in words
**Why:** Reproduced on 11 November 2026 through the routes, with nothing
injected. Git keeps each branch as a file, so a branch named `motor` and one
named `motor/renewal` cannot both exist: `motor` would have to be a file and
a folder at once. Haute's own advice for a branch name is "Use letters,
numbers, dashes and slashes." With a branch `motor`, creating
`motor/renewal` answers "Operation failed. Check the server logs for
details." So does creating `team` beside `team/home`, and creating `archive`
once any branch has been archived. Git's own words stay in the server log:
"cannot lock ref 'refs/heads/motor/renewal': 'refs/heads/motor' exists;
cannot create 'refs/heads/motor/renewal'". Nothing changes in the
repository, and the analyst is not told why, or that another name would
work.

Bringing a branch back fails the same way, and there it stands between the
analyst and their work. `squad/home` is archived, a branch `squad` is made,
and Restore of the archived branch answers the generic text. `grp/x` is
deleted, a branch `grp` is made, and bringing `grp/x` back from Deleted
answers the generic text, as does bringing it back as `grp/y`; as `grp-x`
it works. The branch manager offers to bring a branch back under another
name only when the refusal says "another name" (read from the source), so
here it offers nothing.

A name Windows keeps for a device (`con`, `aux.v2`) is answered the same
way; git's words there are "cannot lock ref 'refs/heads/con'" and "Invalid
argument". The same name in another letter case is refused well: "Branch
'Motor' already exists." The check that runs before a branch is made
promises more than it does: "a name the in-process rules let through is
still refused, in words, if git would not take it". It asks git whether the
name is well formed, which says nothing about the branches that exist.

**Plan:** Before creating, restoring or bringing back a branch, compare its
name and its ledger's name with the branches that exist. Refuse a name that
has a branch above it or beneath it, naming that branch and saying that the
two cannot both exist, and refuse the device names by name as the file
routes do. Word the two refusals for bringing a branch back so that the
branch manager offers another name.

**Acceptance:** Creating `motor/renewal` beside `motor`, `team` beside
`team/home` and `con` each answer naming the clash or the rule. Restoring an
archived branch and bringing back a deleted one into such a name answer the
same, and the branch manager offers another name. None of them answers
"Operation failed."

**Dependencies:** None. `VCS-03` words git's other refusals.

**Evidence:** `src/haute/_git_core.py::_assert_new_branch_name`;
`src/haute/_git_core.py::_branch_name_problem`;
`src/haute/_git_transactions.py::create_working_branch`;
`src/haute/_git_archive.py::restore_working_pair`;
`src/haute/_git_archive.py::undelete_working_pair`;
`frontend/src/components/BranchManager.tsx`.

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

**Dependencies:** None. `VCS-06` reaches the same failed switch by another
road.

**Evidence:** `src/haute/_git_setup.py::set_working_branch`;
`src/haute/_git_remote.py::fast_forward_pair_from_tracking`;
`src/haute/_git_remote.py::branch_away`;
`src/haute/_git_archive.py::_switch_away_if_active`;
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
