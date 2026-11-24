# Engineering quality roadmap

## Scope

The gates and tests that hold the codebase, as [the engineering quality
specification](../engineering-quality/high-level.md) describes them. The
packages below are maintenance work from the 9 November 2026 codebase review:
none is a wrong result today. Each was found by running a lint rule the gate
does not run over `HEAD` on that day, or by reading a test; `EQ-04` by
setting what the review found from running the commands beside the tests
around each finding. `EQ-05` and `EQ-06` come from the plan's phase 5, a
reader from a second model family (Codex) over the scripts CI depends on;
the main session ran the gate of `EQ-05` itself and the two tests of
`EQ-06` on 13 November.

## Priorities

**Start with:** `EQ-05`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EQ-05 | Planned | P3 | The changed-coverage gate counts a change made on any line of a statement, not only on its first. |
| EQ-06 | Planned | P3 | The two Windows-only tests that no CI job selects run in the Windows job, and a test keeps the next one from being left out. |

## Planned improvements

### EQ-05 — The changed-coverage gate counts a change on any line of a statement
**Why:** Reproduced on 13 November 2026 by running the gate itself on a
small git copy of the checkout. In `src/haute/_rating.py` the statement
that turns a duration key into seconds spans five lines. On its third line
"hours times 3,600" was changed to "hours times 3,000", and Coverage.py,
with the repository's own settings, measured a run in which the statement
never ran. The gate printed "Changed coverage gate passed: no changed
safety-critical targets." and exited 0. With the same statement changed on
its first line instead, the gate failed and named line 902.

Coverage.py records a statement on its first line. The gate takes the
lines a pull request changed and keeps those the coverage file names, so a
change that touches only the later lines of a statement counts as no
change. In the 35 files the gate holds to full coverage of changed code,
4,170 of the 9,783 lines of simple statements are such later lines: an
argument of a call, a term of a sum, an entry of a mapping.

**Plan:** Before the changed lines are matched with the coverage file, move
each one to the first line of the statement that holds it, read from the
file's syntax tree, and treat the branches that leave that statement the
same way.

**Acceptance:** In the run above the gate fails and names the statement's
first line. A test holds a change on a later line of a statement that is
not covered, and one of a statement that is.

**Dependencies:** None.

**Evidence:** `scripts/check_changed_coverage.py::evaluate_changed_coverage`;
`.github/workflows/ci.yml`; `pyproject.toml`.

### EQ-06 — The Windows job runs the tests only Windows can run
**Why:** Read and run on 13 November 2026. CI's Windows job runs a list of
test modules by name; the jobs that run the whole suite are on Linux. Two
tests that are skipped on every system but Windows sit in modules the list
leaves out, so they run in no CI job: one holds a file open and expects a
branch switch to be refused, and one expects a path on a network share to
be refused as outside the project. The register of skipped tests describes
the first as where "the git tree fence's real case lives". Both pass on a
Windows machine today. The other Windows-only tests are in listed modules.

**Plan:** Add the two modules to the Windows job's list. Have a test
compare the modules that hold a test skipped off Windows with that list,
so the next one cannot be left out.

**Acceptance:** The Windows job's log shows both tests run. A test skipped
off Windows, in a module the job does not run, fails the comparison.

**Dependencies:** None.

**Evidence:** `tests/test_git_working_tree.py::TestTreeFence`;
`tests/test_path_traversal_advanced.py`; `.github/workflows/ci.yml`;
`tests/test_test_debt.py`.
