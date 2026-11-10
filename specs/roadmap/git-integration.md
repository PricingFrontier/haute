# Git integration roadmap

## Scope

Version control inside Haute, as [the git integration
specification](../git-integration/high-level.md) describes it: the working
branch and its save ledger, catching up from a remote, and what a failed git
command leaves behind. The packages below come from the review plan's runs
of 10 November 2026. Each was reproduced on Windows in a scratch
repository: `VCS-01` with a real open file and a local remote, `VCS-02` and
`VCS-03` with nothing injected into Haute, and `VCS-04` by killing the
server process.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| VCS-01 | Planned | P2 | A catch-up that cannot remove an open file leaves the branches and the files as they were, and removes the file on the retry. |
| VCS-02 | Planned | P2 | Commit and Restore succeed when a deletion or a rename is already staged, instead of failing every time with "Check the server logs". |
| VCS-03 | Planned | P2 | A leftover git lock, a folder git does not trust, a damaged git config and a commit that cannot be signed are each reported with their cause, not as "Not a git repository", a missing identity or "see server log". |
| VCS-04 | Planned | P3 | A restore the server died in reads as interrupted at the next start, and Retry finishes it. |

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
