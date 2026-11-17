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

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EQ-01 | Planned | P3 | The frontend's lint gate checks that a promise is awaited or deliberately dropped. |
| EQ-02 | Planned | P3 | No test decides whether to run by a file the repository does not hold. |
| EQ-03 | Planned | P3 | The dead branches and unused suppressions the review's lint sweep found are gone. |
| EQ-04 | Planned | P2 | A test runs the three commands whole on every shipped example, and on wrong inputs, and holds what each prints. |
| EQ-05 | Planned | P3 | The changed-coverage gate counts a change made on any line of a statement, not only on its first. |
| EQ-06 | Planned | P3 | The two Windows-only tests that no CI job selects run in the Windows job, and a test keeps the next one from being left out. |

## Planned improvements

### EQ-01 — The frontend lint gate has promise rules
**Why:** The frontend's ESLint configuration uses the untyped
`typescript-eslint` rule set, so nothing checks a promise that is neither
awaited nor handled. A one-off run of the type-aware rules over the 648
non-test source files found 10 unawaited promises and 7 promise-returning
functions passed where a void callback is expected. The loaders among them
that were read (the utility panel's and the git panel's) catch and report
their own failures. `handleCreateInstance`, called without `await` from a
keyboard shortcut, has no `catch` around its awaited transform, so a
rejection there would vanish. None of the 17 was shown to fail today; the
gate is what is missing, since the next one written without a `catch` fails
silently.

**Plan:** Turn on `no-floating-promises` and `no-misused-promises` with the
type-aware parser for `src`, mark the 17 existing sites with `void` or an
awaited call, and keep the rule set otherwise as it is. Measure the lint
job's time before and after and record it in the pull request.

**Acceptance:** `npm run lint` fails on a new unawaited promise in `src` and
passes on `HEAD` with the 17 sites marked. The lint job's time is recorded.

**Dependencies:** None.

**Evidence:** `frontend/eslint.config.js`; `frontend/tsconfig.app.json`;
`frontend/src/panels/UtilityPanel.tsx::loadFile` and
`frontend/src/panels/GitPanel.tsx::loadStatus` are two of the fire-and-forget
loaders; `frontend/src/hooks/useNodeHandlers.ts::handleCreateInstance` is the
handler with no `catch`.

### EQ-02 — A test never skips on a file the repository does not hold
**Why:** One explainability test reads `outputs/conversion.rsglm` relative
to the working directory and skips when it is absent. The repository holds
no such file, so the test never runs in CI, and on a developer's machine it
runs against whatever model a local training left in `outputs`. Its name
says it checks the contract of a real RustyStats model's contributions,
which nothing then checks.

**Plan:** Build the small model the test needs in a fixture, as the
neighbouring tests do, or delete the test if those already cover the
contract.

**Acceptance:** The test runs in CI on every platform and reads nothing
outside its temporary folder, or it is gone and the write-up of the pull
request names the test that covers the same contract.

**Dependencies:** None.

**Evidence:** `tests/test_model_explainability.py::test_real_rustystats_conversion_model_predict_contributions_contract`.

### EQ-03 — Dead branches and unused suppressions are removed
**Why:** A sweep with lint rules the gate does not select found a small
amount of code that does nothing. A conditional in the node config recovery
has two identical branches. One background task's handle is not kept, so an
exception in it would surface only when the task is collected. One optimiser
assignment exists only to validate a handle and is never read. Eight blocks
of commented-out code remain, and 80 `noqa` comments name rules the gate
does not run, so they suppress nothing and say nothing about what is
checked.

**Plan:** Collapse the identical branches, keep the task's handle and log
its failure, drop the unused name, delete the commented-out blocks, and
remove the `noqa` comments for rules that are not selected. Add `RUF100` to
the gate so an unused suppression fails.

**Acceptance:** `ruff check` with `RUF100` and `ERA001` added reports nothing
under `src/haute`, and the gate runs `RUF100`.

**Dependencies:** None.

**Evidence:** `src/haute/_node_config_recovery.py:744`;
`src/haute/routes/_supersession.py:236-241`;
`src/haute/routes/_optimiser_solver.py:1478`; `pyproject.toml`.

### EQ-04 — The commands are run whole on the shipped examples
**Why:** Four of the review's findings (`MLR-04`, `DPL-03`, `IOL-01` and
`IOL-02`) came from running `haute lint`, `haute run` and
`haute deploy --dry-run` on the sixteen shipped example projects, as they
are and then with wrong inputs. No test does either. Every test that takes
`haute deploy --dry-run` past the command's own argument checks replaces the
resolution with a mock, a `MagicMock` standing for the resolved deploy, so
the check a scaffolded project's CI runs before a deploy is never run whole
by Haute's own suite. The workflow coverage ledger gives the deploy workflow
the entry points `cli` and `scoring`, and both of its scenarios enter
through `scoring`. `haute run` is run whole on hand-built fixtures and on
the three-node reference example. The example projects are parsed and
regenerated by one test; those another test executes are run in process,
after the test has built their input snapshots itself with absolute paths;
one is built and served by the container smoke test. None goes through
`haute run` or the dry run. The `model_lifecycle` example declares a
`model_scoring` check on its `quote_response` node, and the test that
carries it trains the model and predicts with CatBoost directly: the Model
Scoring node, whose placeholder run id is what `MLR-04` reports, is never
executed. Of the review's 48 runs, 45 passed, one was refused in one
readable line and two ended in that traceback.

**Plan:** One test, parametrised over the shipped examples, runs the three
commands through the CLI runner with nothing patched and holds, for each,
the exit code and that no traceback is printed; an example expected to fail
states its one error line. Beside it, a short table of wrong inputs on one
example (an unknown deploy target, a `haute.toml` naming a missing pipeline
file, a request file with a wrong value, a truncated one, one with a
byte-order mark and an empty one) holds the line the analyst is given for
each. A declared `model_scoring` check is met by executing the example's own
target node, with the trained run's id in place of the placeholder. Record
the scenario in the workflow coverage ledger under the `cli` entry point.

**Acceptance:** Run at `HEAD`, the test reports the `model_lifecycle`
traceback and the unknown target the dry run accepts. It passes once
`MLR-04` and `DPL-03` land, and the wrong-input table holds the lines
`IOL-01` and `IOL-02` settle. The backend job's time before and after is
recorded in the pull request.

**Dependencies:** None to write the test. `MLR-04`, `DPL-03`, `IOL-01` and
`IOL-02` decide the lines it holds.

**Evidence:** `tests/test_cli_deploy.py::_mock_resolved` and
`tests/test_cli_architecture.py` hold the patched dry runs;
`tests/test_cli.py::TestRun` and
`tests/test_reference_pipeline.py::test_default_pipeline_runs` run
`haute run` whole;
`tests/test_assistant_example_portfolio.py::_prepare_inputs` builds the
snapshots, and
`tests/test_assistant_example_portfolio.py::test_model_training_and_resulting_model_scoring`
predicts with CatBoost;
`src/haute/assistant/assets/examples/model_lifecycle/assertions.json`
declares the check and
`src/haute/assistant/assets/examples/model_lifecycle/config/model_score.json`
holds the placeholder; `src/haute/_scaffold.py::_DRY_RUN_CHECK` is the
scaffolded check; `tests/workflow_coverage.toml`;
`tests/test_container_smoke_script.py`.

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
