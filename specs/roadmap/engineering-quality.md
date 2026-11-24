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

**Start with:** `EQ-06`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EQ-06 | Planned | P3 | The two Windows-only tests that no CI job selects run in the Windows job, and a test keeps the next one from being left out. |

## Planned improvements

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
