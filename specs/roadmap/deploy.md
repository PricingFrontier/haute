# Deploy roadmap

## Scope

The deploy component: resolving and validating a deployment, the served request path
(container and Databricks), the bundle's dependencies and build directories, the
MLflow target and the impact analysis. Current behaviour is specified in
[the deploy specification](../deploy/high-level.md). These packages come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md), which read every file of
`src/haute/deploy/` in full.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DEP-R09 | Planned | P2 | The container image and the Databricks model install a recorded dependency closure resolved for their target Python and platform. |
| DEP-R11 | Planned | P2 | Impact analysis fails on a row-count mismatch instead of pairing the wrong quotes, and a mistyped production URL is not a first deploy. |
| DEP-R16 | Decision | P3 | A quote whose production value is zero and whose staging value is not is reported, not a failure of the whole impact analysis. |

## Planned improvements

`DEP-R09` locks every target's dependency closure with one resolver.

### DEP-R09 — Every target's dependency closure is locked
**Why:** The Dockerfile pins the scoring-runtime distributions and the artefacts' engines
to the deploying environment's versions, but their transitive dependencies (starlette,
anyio, h11, typing-extensions, ...) resolve at image build time, so two builds of one
deploy can differ, against the specification's reproducibility rationale. The Databricks
model's environment pins the same way (haute, mlflow, Polars, pandas, NumPy and each
bundled engine), and the rest of haute's closure resolves at Model Serving's build within
haute's ranges, from a host whose platform may not be the target's.

**Plan:** Use one resolver for container, pyfunc and hosted-app dependency locks, with
explicit root requirements for each target. The hosted app already locks this way:
`databricks_app/scripts/build_bundle.sh` runs `uv pip compile` for its Python and
platform with hashes and keeps its index provenance free of credentials; bring the
container and the pyfunc onto the same resolution, extracting what they share. Preserve the
[minimal scoring container policy](../deploy/high-level.md#scoring-runtime): its roots
are the scoring runtime plus graph/artefact requirements, and Haute is a separate
`--no-deps` install. Do not feed Haute's full dependency metadata into the container's
resolution or add editor, assistant and training stacks just to satisfy metadata.
The pyfunc and hosted-app profiles retain their full Haute dependencies and extras.
Compile the selected runtime requirements with
`uv pip compile runtime.in --python-version <target>
--python-platform <target-triple> --generate-hashes`, constraining validated direct
runtime and model-engine versions and including the requested extras. Record the target
Python, OS/architecture, resolver version, evaluated markers and credential-free index
provenance beside the lock. Resolve once per deployment bundle and reuse the recorded
lock on rebuild; a fresh resolution is a new dependency input.

Install the locked runtime with `--no-deps --require-hashes` and verify the separately
bundled Haute wheel's digest before its `--no-deps` install. Require compatible wheels;
if a dependency needs a source build, build and hash its wheel in a declared target
build environment before finalising the bundle. Do not substitute the deploying host's
freeze: a Windows environment omits Linux-only dependencies such as `uvloop`.

**Acceptance:** Two images from one recorded bundle install the same locked runtime
versions and pass a selected-runtime dependency check. Use installed `Requires-Dist`
metadata with target markers and selected extras to verify every required dependency
and version in that closure. Account for Haute's intentionally omitted non-scoring
requirements with an explicit, reviewed list tied to the runtime policy; never ignore
all missing requirements from Haute, omit a selected engine requirement, or suppress
another package's missing dependency/version conflict. `pip check` remains appropriate
for the full pyfunc/hosted-app profiles, but its non-zero status for these declared
container omissions is not itself a runtime failure.

For the Databricks model, the logged `conda_env` names every resolved runtime
distribution at its locked version, resolved for Model Serving's Linux platform and the
environment's Python; install it on that target, run `pip check`, load the logged model,
score a representative request and assert the installed versions against the lock (a
test of the generated package list alone is insufficient).

Compare normalised installed distributions with the lock, accounting explicitly for
the bundled Haute wheel and base-image tooling. An engine-free pricing pipeline passes
in the minimal container without installing editor/training stacks; removing a required
transitive dependency or installing an incompatible version fails the runtime check.
A Windows-originated deployment resolves Linux-only requirements for the target; an
unavailable compatible artifact or changed hash fails the build. Verify real scoring
in the target installation, not just identical install command text.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::_generate_dockerfile` and
`::_pinned_dockerfile_deps`; `src/haute/deploy/_mlflow.py::_pip_requirements` and
`::_conda_env`.

Resolver reference: [uv platform-specific resolution](https://docs.astral.sh/uv/concepts/resolution/).

### DEP-R11 — Impact analysis compares the right quotes
**Why:** `build_report` pairs staging and production predictions by position and
silently truncates the three row sources to the shortest, so an endpoint that answered
fewer rows than it was sent shifts every later pair and the report compares the wrong
quotes, counting the difference as "failed rows". For the HTTP transport, "no production
yet" is detected by matching the text `HTTP 404` in a `RuntimeError` message, so a
mistyped production URL passes as a first deployment and the impact step goes green with
nothing compared.

**Plan:** Check each response batch before concatenation: require exactly one prediction
per sent quote, permitting a scalar only for a one-quote batch. Fail with the batch and
expected/actual counts, and remove `build_report`'s truncation. Check final totals too.
Raise typed transport errors carrying status and URL. For HTTP, require explicit
`haute impact --first-deploy` intent to skip an absent production baseline; generated CI
must opt in for that release, never persist the bypass as a default. A registry-backed
target may instead use an authoritative not-found result. Authentication failures,
unexpected 404s, timeouts and unreachable endpoints fail closed; `/health` failure is
not evidence of a first deploy. Report an intentional skip explicitly.

**Acceptance:** A missing middle prediction fails before any positional comparison;
one short and one long batch fail even when totals match; a scalar for a multi-quote
batch fails. A 404 or unreachable production endpoint without first-deploy intent fails
naming the URL. Explicit first-deploy intent and authoritative registry absence each
produce a labelled skip; authentication failures never produce that skip.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_impact.py::build_report` and
`::score_http_endpoint_batched`; `src/haute/cli/_impact.py::_is_http_not_found` and
`::_impact_http`.

### DEP-R16 — Zero production baselines in the impact report
**Why:** One quote whose production value is 0 and whose staging value is not fails the
whole impact analysis with `ValueError`, which fails the generated CI job; the
specification chose this over a misleading percentage. A newly priced cover or a
reversed decline is an ordinary release event.

**Plan:** Decide whether to keep the refusal or report such rows as a count beside the
statistics; update the specification either way.

**Acceptance:** The decision is recorded in the deploy specification and the behaviour
matches it.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_impact.py::_percent_change_series` and
`::_total_percent_change`.
