# Engineering quality roadmap

## Scope

The gates and tests that hold the codebase, as [the engineering quality
specification](../engineering-quality/high-level.md) describes them. The
packages below are maintenance work from the 9 November 2026 codebase review:
none is a wrong result today. Each was found by running a lint rule the gate
does not run over `HEAD` on that day, or by reading a test.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EQ-01 | Planned | P3 | The frontend's lint gate checks that a promise is awaited or deliberately dropped. |
| EQ-02 | Planned | P3 | No test decides whether to run by a file the repository does not hold. |
| EQ-03 | Planned | P3 | The dead branches and unused suppressions the review's lint sweep found are gone. |

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
