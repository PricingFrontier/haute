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
| `VCS-01` | [git-integration](git-integration.md) | A catch-up that could not remove a file puts the branch back | Resolves |
| `VCS-04` | [git-integration](git-integration.md) | A restore cut short is found at the next start | Resolves |

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
process that dies after the ledger moved (`VCS-04`), and git itself moving
the branch and exiting 0 with a file still held open (`VCS-01`). The clone
is then in between, the save ledger at one commit and the working branch
at another, and nothing says so. Earlier roadmaps dealt with checkouts
that stop part-way and gave the operations one helper to put things back;
both act after the fact.

**Plan:** Before its first step, each operation of several steps, catch up,
restore, switching branch and a save's pair of commits, writes what it is
about to do: the operation, where the ledger and the branch are, where they
will be, and the files. It removes the record after its last step. At
start, and before any git action, a record that is still there is the
interruption: readiness names it, and Retry finishes it or puts it back.

**Acceptance:** A test stops the process after each step of each such
operation in turn. At the next start readiness names the operation and its
files, Retry completes it or returns the clone to where it was, and at no
point do the ledger and the working branch differ with nothing recorded.
The acceptance of `VCS-01` and `VCS-04` is met.

**Dependencies:** None. Deliver before `VCS-01` and `VCS-04`.

**Evidence:** `src/haute/_git_state.py`; `src/haute/_git_transactions.py`;
`src/haute/_git_core.py`; `src/haute/_git_remote.py`.
