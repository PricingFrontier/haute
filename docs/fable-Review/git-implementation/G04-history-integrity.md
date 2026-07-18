# G04 — History integrity: tab-corrupted milestone rows; move-fork linearizes external merges

**Severity: MEDIUM · Confidence: CONFIRMED (both reproduced) · Class: silent wrongness (wrong history shown / corrupted save chain)**
**Files: `src/haute/_git.py`**
**Origin: engine reviewer (E-16b, E-12).**

## E-16b [MEDIUM, CONFIRMED — reproduced] A tab in a milestone message truncates the message and corrupts the timestamp

The milestone-message gate allows `\t` (`_git.py:821`: `ord(c) < 0x20 and c not in "\t\n\r"`), but
two parsers put the free-text subject **before** the timestamp in tab-separated formats:

- `working_milestones`: `--format=%H\t%h\t%s\t%aI` + `line.split("\t", 3)` (`_git.py:1510`, `:1517`)
- `_commit_meta`: `%H%x09%h%x09%s%x09%aI` + `raw.split("\t", 3)` (`_git.py:1555-1556`)

Reproduced: subject `"Subject with\ttab here"` → parsed message `"Subject with"` (truncated) and
timestamp `"tab here\t2026-…"` (subject text inside a datetime field). The Git panel's milestone
list and the commit-context breadcrumb then render a truncated message and a garbage time
("NaN ago" in `timeAgo`). User-triggerable by pasting a tab into the commit-message box.
`_parse_ledger_saves` already solved this exact problem by putting the message **last**
(`_git.py:1714-1719`) — these two parsers never adopted the ordering. `_commit_meta`'s docstring
("a tab in the subject can't shift the columns") is wrong as written — maxsplit avoids IndexError,
not field pollution.

**Fix.** Reorder both formats to message-last and split identically to the ledger parser:
`--format=%H\t%h\t%aI\t%s` with `sha, short, ts, msg = line.split("\t", 3)`. Fix the
`_commit_meta` docstring to state the real invariant (subject last, `%aI` never contains a tab —
now actually true). Do **not** strip tabs from messages on the way in (that would silently alter
user input).

**Tests.** Milestone with `\t` in the message → `working_milestones()` returns the full message and
a parseable ISO timestamp; `commit_context()` likewise; frontend snapshot of a tabbed message rows
renders time correctly (vitest, optional).

## E-12 [MEDIUM, CONFIRMED — reproduced] `create_working_branch(move=True)` replays external merge commits linearly, with no invariant check

`create_working_branch` never calls `check_invariants` (contrast `merge_to_working`,
`_git.py:824`). The move path replays `rev-list --reverse point..ledger_tip` (`_git.py:1235`,
`:1400`) through `_replay_onto`, which rebuilds each commit as **single-parent**
`commit-tree` (`_git.py:1270`).

Reproduced: a user merges a side branch into the ledger via CLI (real merge commit on `W-save`).
The range then contains that merge; the replay drops its second-parent lineage and produces
intermediate replayed commits whose trees don't correspond to any real save sequence — a
scrambled save history on the new ledger, silently. A state that `commit_milestone` would REFUSE
(invariant violation) can thus still be forked-with-move, laundering the corruption into a fresh
pair.

**Fix.** Two guards, both cheap:
1. Run `check_invariants(current)` at the top of `create_working_branch` (both modes) and raise
   the same domain error `merge_to_working` uses ("…was advanced outside haute. Use the branch
   manager to start a fresh branch from your current state.").
2. Belt-and-braces in the move path: refuse when `git rev-list --merges --count point..ledger_tip`
   is non-zero, with a message naming the external merge ("Your save history contains a merge made
   outside haute — spin off a parallel branch instead.").

Parallel (non-move) forks at a milestone stay unaffected — they replay nothing.

**Tests.** (a) external commit directly on the working branch → `create_working_branch` (either
mode) raises the invariant domain error; (b) external merge on the ledger → move-fork refuses with
the merge-specific message; parallel fork at the last milestone still succeeds; (c) existing
`TestCreateWorkingBranch` matrix stays green.

## Notes

Silent-wrongness class → full dev/reviewer pair (project protocol). Both fixes are small and
independent; land E-16b first (two format strings + docstring), then E-12.
