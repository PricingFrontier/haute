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
| DEP-R05 | Planned | P1 | Flat Quote Input uses checked manifest types; container framing errors return 422, and MLflow's public signature boundary is explicit. |
| DEP-R06 | Planned | P1 | The Databricks model installs a target-compatible lock for the validated scoring runtime and every bundled engine. |
| DEP-R09 | Planned | P2 | The container image installs a recorded dependency closure resolved for its target Python and platform. |
| DEP-R10 | Planned | P2 | `haute.toml` values are typed at load, a malformed `[test_quotes] dir` is refused, and an unknown override key raises. |
| DEP-R11 | Planned | P2 | Impact analysis fails on a row-count mismatch instead of pairing the wrong quotes, and a mistyped production URL is not a first deploy. |
| DEP-R12A | Planned | P2 | Each build owns a fresh directory under the project and ships only its own artefacts. |
| DEP-R12B | Planned | P3 | The output-schema cache lives under the project root with one entry per fingerprint. |
| DEP-R13 | Planned | P3 | Resolution does not rewrite the graph it ships, and the deploy module's duplicated predicates, loops and defaults are written once. |
| DEP-R14 | Planned | P3 | Validation parses each test-quote file once and reports every row with missing columns. |
| DEP-R15 | Planned | P3 | The served error envelopes carry a correlation id rather than the exception text. |
| DEP-R16 | Decision | P3 | A quote whose production value is zero and whose staging value is not is reported, not a failure of the whole impact analysis. |

## Planned improvements

`DEP-R05` and `DEP-R06` protect served results and come first. `DEP-R06` uses the
target-resolution mechanism in `DEP-R09`; deliver that shared mechanism with it.
`DEP-R12A` can proceed independently, coordinating edits to shared build code.

### DEP-R05 — Served requests typed by the manifest schema
**Why:** A flat Quote Input's served request is framed by Polars inference from the
JSON values (`pl.DataFrame(rows)` in the generated `app.py` and `pl.DataFrame(rows,
schema=None)` in the batch worker), not by the
`input_schema` the manifest records: `30` and `30.0` give Int64 and Float64, a `null`
gives a Null column, so a cast to String (`"30"` against `"30.0"`), a Banding level or
a categorical model feature can differ from the dry run and the test quotes, which read
the sample file's types. The specification says only that such a request is read "as
one frame". A request Polars cannot frame at all (a list mixing an integer and a string)
is answered 500 `deploy_internal_error` after the execution context was admitted, and
that context is never released. The public MLflow path first enforces the logged
`ModelSignature`, before `HauteModel.predict` converts the frame: a double signature
refuses an int64 input in the validation probe. Identical unchecked inference on that
public path is not established, and a cast inside `predict` cannot repair an earlier
signature refusal. Polars `cast(strict=True)` also truncates `30.9` to Int64 `30`;
strict casting alone does not establish lossless validation.

**Plan:** Extract and extend the checked value rules in `_workbench_input._value`
for reuse by flat Quote Input framing against `input_schema`. Refuse numeric strings,
booleans in numeric columns, float-to-integer coercion and integer overflow; accept an
integer in a float column only when the conversion is finite and preserves its exact
value (test both representable and non-representable integers above 2^53). Preserve
typed nulls at this input boundary; fitted model contracts can impose stricter null
rules. Do not infer non-nullability from the sample file. Retain the shared date,
string and boolean checks and specify the supported dtype mapping before implementation.

Use this validator in the single-quote path, the admitted batch worker and the pyfunc
request adapter. Preserve MLflow's public signature enforcement and document its
stricter accepted-input subset; do not relax the signature to promise target parity.
Keep batch framing inside its bounded worker. A size-capped single quote may be framed
before admission; any failure after admission must release the context. Catch
`UnicodeDecodeError` in `read_limited_json_body` as the `invalid_json` envelope. Define
these rules and each target's error envelope in the deploy specification.

**Acceptance:** Exercise `30`, `30.0`, `30.9`, `"30"`, `true`, null, integer range
boundaries, integers on both sides of the exact-float boundary and mixed lists against
each supported dtype through `/quote`, the batch worker and
`mlflow.pyfunc.load_model(...).predict`. Container `30` for Float64 matches the dry run;
`30.9` for Int64 is refused without truncation. Container refusals are 422 naming the
column and leave no admitted context; MLflow signature refusals occur at the public
boundary and are asserted separately. Accepted equivalent inputs score equally. Typed
nulls keep the declared dtype; a body that is not UTF-8 is answered 422.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::_generate_app_source` (the generated
`/quote` route);
`src/haute/deploy/_batch_scoring.py::score_batch_scoring_request` and
`::prepare_batch_scoring` (the worker's frame and the schema only the dry run passes);
`src/haute/deploy/_model_code.py::_request`;
`src/haute/deploy/_request_limits.py::read_limited_json_body`;
`src/haute/_workbench_input.py::read_quote` (the typed reader the tables path uses).

### DEP-R06 — The Databricks environment pins the scoring runtime
**Why:** The pyfunc model's environment pins `haute`, `mlflow` and `polars` and nothing
else, so the model engines (xgboost, lightgbm, catboost, interpret-core, t-boost,
rustystats) and pandas and numpy resolve at Model Serving build time within haute's
dependency ranges, and the served model can load under a different engine release than
the one that trained it. The container target pins each bundled artefact's engine for
exactly this reason: a different engine version can fail to load the model or change
its predictions. An importable model alone does not establish equivalent premiums.

**Plan:** Build `_pip_requirements` with the shared target resolver in `DEP-R09`, using
Model Serving's Linux platform and the conda environment's Python version. Preserve
this target's existing full Haute dependency profile; sharing a resolver does not mean
sharing the container's minimal root set. Include the
pyfunc imports (`mlflow`, `pandas`, `numpy`), Haute's required runtime and each bundled
engine, constrained to the validated deploying versions where available. pandas is
already a direct Haute dependency, but the container list pins it only for pickle
artefacts; copying that list alone is insufficient. Resolve platform-specific
dependencies for the target and fail clearly if the pinned runtime is incompatible.

**Acceptance:** The logged model's `conda_env` names every resolved runtime distribution,
including pandas and every bundled engine, at its locked version. Install that
environment on the target Python/platform, run `pip check`, load the logged model and
score a representative request. Assert the installed versions against the lock;
a test of the generated package list alone is insufficient.

**Dependencies:** `DEP-R09` supplies the shared target resolver; ship its resolver with
this package rather than deferring the P1 environment fix.

**Evidence:** `src/haute/deploy/_mlflow.py::_pip_requirements` and `::_conda_env`;
`src/haute/deploy/_model_code.py`; `pyproject.toml`;
`src/haute/deploy/_container.py::runtime_versions`, `::_pinned_dockerfile_deps` and
`::_pinned_extra_dockerfile_deps`.

### DEP-R09 — The container's dependency closure is pinned
**Why:** The Dockerfile pins the scoring-runtime distributions and the artefacts' engines
to the deploying environment's versions, but their transitive dependencies (starlette,
anyio, h11, typing-extensions, ...) resolve at image build time, so two builds of one
deploy can differ, against the specification's reproducibility rationale.

**Plan:** Use one resolver for container, pyfunc and hosted-app dependency locks, with
explicit root requirements for each target. Preserve the
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

Compare normalised installed distributions with the lock, accounting explicitly for
the bundled Haute wheel and base-image tooling. An engine-free pricing pipeline passes
in the minimal container without installing editor/training stacks; removing a required
transitive dependency or installing an incompatible version fails the runtime check.
A Windows-originated deployment resolves Linux-only requirements for the target; an
unavailable compatible artifact or changed hash fails the build. Verify real scoring
in the target installation, not just identical install command text.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::_generate_dockerfile` and
`::_pinned_dockerfile_deps`.

Resolver reference: [uv platform-specific resolution](https://docs.astral.sh/uv/concepts/resolution/).

### DEP-R10 — Typed configuration, refused rather than defaulted
**Why:** `haute.toml` is checked for unknown keys but never for value types: `port =
"x"`, `serving_scale_to_zero = "yes"` or `[test_quotes] dir = 5` all load, and a
non-string or empty `dir` silently falls back to `tests/quotes` (and to no gate when
that folder is empty), which the specification says must fail rather than disable the
gate. A boolean environment override treats any value other than `true`/`1`/`yes` as
`False`, so a typo disables scale-to-zero. `DeployConfig.override` ignores a key the
class does not have, so a misspelt override is dropped without error, and
`resolve_config` assigns the canonical pipeline path into the caller's config.

**Plan:** Load the TOML tables through typed models (pydantic or dataclass validators)
that refuse a wrong type by key; refuse a `[test_quotes] dir` that is not a non-empty
string; accept an explicit true/false vocabulary for boolean overrides and raise
otherwise; raise `TypeError` in `override` for an unknown key; resolve the pipeline path
into a local and store it on `ResolvedDeploy`.

**Acceptance:** Each wrong-typed value above fails `from_toml` naming the key; `dir = ""`
fails; `HAUTE_SERVING_SCALE_TO_ZERO=ture` fails; the caller's `DeployConfig` is unchanged
after `resolve_config`.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_config.py::DeployConfig`, `::_test_quotes_dir`,
`::_apply_env_overrides`, `::_validate_toml_keys` and `::resolve_config`.

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

### DEP-R12A — Each build owns its directory and artefacts
**Why:** The container build writes `cwd/.haute_build`, removes it wholesale on failure
(whatever a user kept there) and leaves it behind on success with a copy of every
artefact; a reused directory keeps earlier builds' `artifacts/*`, which the Dockerfile's
`COPY artifacts/ artifacts/` ships although the manifest never names them. The MLflow
target roots its build directory at the pipeline's directory instead. Stale unnamed
artefacts are copied into the image even though the served loader reads manifest-named
files; this is bundle isolation and disclosure risk, not evidence that those unnamed
files change prices. Concurrent builds sharing the directory can overwrite each other's
named files.

**Plan:** Allocate a unique owned directory per build under the resolved project root,
stage into an empty artefact folder, and remove only that directory after push or
failure (keep it only on request). Apply the same ownership rule to both targets. Do
not delete a pre-existing `.haute_build` or another build's files.

**Acceptance:** Consecutive and concurrent builds from one checkout produce bundles
containing only their own manifest's artefacts. Failure of one build does not change
the other's bundle or a user's pre-existing directory. Running from another cwd keeps
generated build files under the resolved project. P2 addresses this isolation gap;
reassess as P1 if shared-checkout concurrent deployment is an advertised workflow.

**Dependencies:** None; coordinate edits to shared build code with `DEP-R05` to
`DEP-R11` without delaying isolation behind those packages.

**Evidence:** `src/haute/deploy/_container.py::build_and_push_image` and
`::write_bundle`; `src/haute/deploy/_mlflow.py::_deploy_to_mlflow`;
`src/haute/deploy/_scorer.py`.

### DEP-R12B — A project-scoped output-schema cache
**Why:** `.haute_cache/output_schema.json` is relative to the process cwd and stores
one entry overwritten by whichever pipeline deployed last, causing avoidable cache
misses and placing generated files outside the resolved project.

**Plan:** Root the cache at the resolved project and keep one entry per validated
fingerprint, preserving the existing freshness checks.

**Acceptance:** Alternating between two unchanged pipelines reuses both entries;
changing a pipeline invalidates its entry. A dry run from another cwd writes the
schema cache only under the resolved project.

**Dependencies:** None; coordinate the project-root resolution with `DEP-R12A`.

**Evidence:** `src/haute/deploy/_schema.py::infer_output_schema`.

### DEP-R13 — One copy of each deploy mechanism
**Why:** Artefact collection rewrites a Model Score's `artifact_path` in the node's
config dict in place, so a read-only resolution step changes the canonical config it
ships (the node object is shared with the full graph). The `output_fields` predicate is
written in resolution and again in validation; `_request_rows._topological_order`
hand-rolls Kahn's algorithm beside `_topo.topo_sort_ids`; the deploy intercepts repeat
the builders' config defaults (`fileType`, `version_column`, `task`, `output_column`)
and bodies for External File and Optimiser Apply; a bundled CatBoost model is loaded up
to three times per resolution; a snapshot lease failure of any kind is reported as
"invalid Data Input configuration"; a Live Switch without a `live` entry raises
`StopIteration`; a loop over request-input edges computes names only for the error it
raises; `_next_version` always returns 1 and `_detect_extra_deps` has no caller;
`resolve_config` assigns the canonical pipeline path into the caller's `DeployConfig`,
which `DeployConfig.override` deep-copies precisely to avoid elsewhere.

**Plan:** Build the remapped graph with `GraphNode.with_config`; keep one
`output_fields` predicate; use `topo_sort_ids`; expose each node default once and have
the intercepts call the builders' bodies; load the model once; narrow the lease
`except`; raise `ValueError` for the switch; name the edge check; report the image tag
as the version and delete the stub and the unused wrapper.

**Acceptance:** `resolve_config` leaves the parsed graph's node configs equal to the
parsed file's; a default changed in `_builders.py` reaches deployed scoring; the deploy
module has no second topological sort and no `_next_version`.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_bundler.py::collect_artifacts` and
`::_collect_static_data_input`; `src/haute/deploy/_config.py::resolve_config`;
`src/haute/deploy/_validators.py::validate_deploy`;
`src/haute/deploy/_request_rows.py::_topological_order`; `src/haute/_topo.py::topo_sort_ids`;
`src/haute/deploy/_scorer.py::_score_graph_lazy` and
`::_attach_bundled_model_contract_inputs`; `src/haute/deploy/_pruner.py::_live_only_edges`;
`src/haute/deploy/_container.py::_next_version` and `::_detect_extra_deps`.

### DEP-R14 — Validation reads each test-quote file once
**Why:** `validate_deploy` parses every test-quote file for the column pre-check and
`score_test_quotes` parses it again; the pre-check stops at the first row with missing
columns, so later rows' problems are not in the one aggregated report the specification
promises.

**Plan:** Parse once and pass the cases to `score_test_quotes`; report every row with
missing columns.

**Acceptance:** A file with two rows missing columns names both rows in one
`DeployError`; each file is read once per validation.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_validators.py::validate_deploy`,
`::score_test_quotes` and `::load_test_quote_file`.

### DEP-R15 — Error envelopes without exception text
**Why:** The generated container's 500 envelopes return `str(exc)` to the API client,
which can carry file paths and internal identifiers; the exception is already logged.

**Plan:** Return a fixed message with the request's correlation id and keep the detail in
the log.

**Acceptance:** No 500 envelope body contains a file-system path; the log line for the
request carries the id the envelope names.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::_generate_app_source`.

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
