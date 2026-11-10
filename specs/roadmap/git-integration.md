# Git integration roadmap

## Scope

Version control inside Haute, as [the git integration
specification](../git-integration/high-level.md) describes it: the working
branch and its save ledger, catching up from a remote, and what a failed git
command leaves behind. The package below comes from the review plan's runs
of 10 November 2026 and was reproduced on Windows with a real open file, in
a scratch repository with a local remote.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| VCS-01 | Planned | P2 | A catch-up that cannot remove an open file leaves the branches and the files as they were, and removes the file on the retry. |

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
