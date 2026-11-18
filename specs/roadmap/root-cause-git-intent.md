# Root cause: interrupted git operations

## Scope

A git operation of several steps that stops part-way and leaves the clone
in between with nothing recorded.

This file is one pull request. It holds one package, `VCS-11`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `VCS-04` | [git-integration](git-integration.md) | A restore cut short is found at the next start | Resolves |
| `VCS-01` | [git-integration](git-integration.md) | A catch-up that could not remove a file puts the branch back | In part |

`VCS-01` asks for the clone to be as it was before the failed call
returns. The record made here gives its put-back the commits to return to;
comparing commits and not branch names, and counting a held file that is
not tracked, stay with that package.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| VCS-11 | Planned | P2 | A git operation of several steps that stops at any point is found at the next start, with nothing left unrecorded. |

## Planned improvements

### VCS-11 — A git operation of several steps records its intent first
**Why:** Read on 16 November 2026. An operation that moves a branch and
then writes files can stop between the two. Haute marks the clone
interrupted at two places in the code, and both run only after a failure
has been caught. So a stop that nothing catches leaves no mark: a server
process that dies after the ledger moved (`VCS-04`). A failure that is
caught is put back from what the code noted along the way, a branch's name
and flags set after each step returned, so git moving the branch and
exiting 0 with a file still held open is put back to the wrong place
(`VCS-01`). The clone is then somewhere no operation meant it to be, and
nothing says so. Earlier roadmaps dealt with checkouts that stop part-way
and gave the operations one helper to put things back; both act after the
fact. This cause rests on those two findings.

**Plan:** Before its first step, each operation that moves a ref and then
writes files, or moves more than one ref (catch up, restore, switching
branch), writes what it is about to do: the operation, the commit each ref
it touches is at, the commit each will be at, and the files. It removes the
record after its last step. The record also names the operation's commit
point, the step after which it goes forward and not back. A failure caught
before that point is put back from the record, to the commits it holds,
before the call returns, and the result is checked against them. A failure
after it keeps what was committed: a restore that has published its commit
to the ledger stays recorded, as the git specification requires, and Retry
finishes its files. The record stays whenever the operation could neither
be put back nor finished. At start, and before any git action, a record that is
still there is the interruption: readiness names it, and Retry finishes it
or puts it back. Add a test that fails when a function moves a ref outside
an operation that holds a record, apart from a save's one commit to the
ledger.

**Decided with Ralph on 18 November 2026:** the work covers every action
that moves a ref, not only the three named above, and the git runners
refuse a ref move outside a recorded operation's step. What the analyst
sees changes: an interrupted clone's toolbar reads "Unfinished" with Retry,
its tooltip names the operation and its files ("Restoring a version stopped
part-way: rating/main.py and 2 more."), and the server refuses a save's
capture while it is interrupted, as the editor already does. `VCS-01`'s own
part is done here too.

**Acceptance:** A test stops the process after each step of each such
operation in turn. At the next start readiness names the operation and its
files, and Retry completes it or returns every ref to the commit the record
holds. A second test makes each step fail and be caught: before the
operation's commit point it requires the refs and the files as they were,
before the call returns; after it, the commit kept, the interruption
recorded and Retry finishing the files. At no point are
the refs anywhere but where the record says the step reached; a save that
moves the ledger ahead of the working branch is no such case. The
acceptance of `VCS-04` is met.

**Dependencies:** None. Deliver before `VCS-01` and `VCS-04`.

**Evidence:** `src/haute/_git_state.py`; `src/haute/_git_transactions.py`;
`src/haute/_git_core.py`; `src/haute/_git_remote.py`.
