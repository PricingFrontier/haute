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
| DEP-R05 | Planned | P1 | A flat Quote Input's served request is typed by the manifest's input schema, and a request that cannot be framed is refused with 422. |
| DEP-R06 | Planned | P1 | The Databricks model's environment pins the scoring runtime and every bundled model's engine to the deploying versions. |
| DEP-R07 | Planned | P1 | Every MLflow-sourced Model Score ships the feature contract its training run logged, or the deploy refuses. |
| DEP-R08 | Planned | P2 | The MLflow target reads its registered version from the log call, treats a 401 as a credential problem, and shares one schema default. |
| DEP-R09 | Planned | P2 | The container image's whole dependency closure is pinned to the deploying environment. |
| DEP-R10 | Planned | P2 | `haute.toml` values are typed at load, a malformed `[test_quotes] dir` is refused, and an unknown override key raises. |
| DEP-R11 | Planned | P2 | Impact analysis fails on a row-count mismatch instead of pairing the wrong quotes, and a mistyped production URL is not a first deploy. |
| DEP-R12 | Planned | P3 | Build directories and the output-schema cache live under the project root and leave no stale artefacts behind. |
| DEP-R13 | Planned | P3 | Resolution does not rewrite the graph it ships, and the deploy module's duplicated predicates, loops and defaults are written once. |
| DEP-R14 | Planned | P3 | Validation parses each test-quote file once and reports every row with missing columns. |
| DEP-R15 | Planned | P3 | The served error envelopes carry a correlation id rather than the exception text. |
| DEP-R16 | Decision | P3 | A quote whose production value is zero and whose staging value is not is reported, not a failure of the whole impact analysis. |

## Planned improvements

`DEP-R05`, `DEP-R06` and `DEP-R07` each change what a served pipeline computes or checks,
so they come first; `DEP-R12` moves files other packages touch, so it comes after them.

### DEP-R05 — Served requests typed by the manifest schema
**Why:** A flat Quote Input's served request is framed by Polars inference from the
JSON values (`pl.DataFrame(rows)` in the generated `app.py`, `pl.DataFrame(rows,
schema=None)` in the batch worker, `pl.from_pandas` in the Databricks model), not by the
`input_schema` the manifest records: `30` and `30.0` give Int64 and Float64, a `null`
gives a Null column, so a cast to String (`"30"` against `"30.0"`), a Banding level or
a categorical model feature can differ from the dry run and the test quotes, which read
the sample file's types. The specification says only that such a request is read "as
one frame". A request Polars cannot frame at all (a list mixing an integer and a string)
is answered 500 `deploy_internal_error` after the execution context was admitted, and
that context is never released.

**Plan:** Cast the request frame to the manifest's `input_schema` strictly in the
single-quote path, the batch worker and the pyfunc model, refusing a value that does
not fit with 422 naming the column, as the tables path refuses a misfit; frame the
request before admitting the execution context; catch `UnicodeDecodeError` in
`read_limited_json_body` as the `invalid_json` envelope. State the typing rule in the
deploy specification.

**Acceptance:** A served request with `30` for a Float64 column scores as the dry run
did; a request with `"x"` for an Int64 column, or a value Polars cannot frame, is
answered 422 naming the column and leaves no admitted context; the batch worker and the
Databricks model behave the same; a body that is not UTF-8 is answered 422.

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
exactly this reason, and names the risk: a model loaded under another engine is
silently wrong premiums, not an error.

**Plan:** Build `_pip_requirements` from the same pinned list the container's Dockerfile
uses (`runtime_versions` plus each artefact's engine), so both targets install the
deploying environment's versions.

**Acceptance:** The logged model's `conda_env` names every scoring-runtime distribution
and every bundled artefact's engine with `==` and the deploying version; the test that
pins the three-package requirement list is replaced by one over the full list.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_mlflow.py::_pip_requirements` and `::_conda_env`;
`src/haute/deploy/_container.py::runtime_versions`, `::_pinned_dockerfile_deps` and
`::_pinned_extra_dockerfile_deps`.

### DEP-R07 — Every MLflow-sourced model ships its training contract
**Why:** Training logs a feature contract beside every model artefact, but the bundler
fetches it from the run only for a family that loads under its contract (EBM) or a
CatBoost model whose file does not declare its offset; for every other MLflow-sourced
model it looks for a bare `feature_contract.json` beside the cached download, which
training never writes, and logs that none was found. The deployed scorer then runs that
node without a contract, so a served request is checked against the model's own feature
names only: a dtype or a categorical level that drifted from training is not refused
at the boundary. The specification says bundling carries each model's feature contract
and that the scorer binds each model to it exactly as the preview does.

**Plan:** Fetch the run's contract (`_resolve_run_contract`) for every MLflow-sourced
Model Score and bundle it under the node's contract key; refuse the deploy, naming the
node and the run, when the run logged none. Make the editor's preview bind the same
contract so the two keep agreeing.

**Acceptance:** A deployed pipeline with an MLflow-sourced XGBoost model refuses a
request whose categorical value is outside the training levels with the contract
mismatch error; a run without a contract fails resolution naming it.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_bundler.py::collect_artifacts` and
`::_bundle_feature_contract`; `src/haute/_mlflow_io.py::_resolve_run_contract` and
`::_run_contract_artifact`; `src/haute/deploy/_scorer.py::_score_graph_lazy` (the
contract-less `model_score_fn` branch); `src/haute/modelling/_mlflow_log.py` (the
contract logged with every candidate).

### DEP-R08 — The MLflow target's version, credential and default
**Why:** After `mlflow.pyfunc.log_model` the registered version is found by searching the
registry and taking the maximum, so a concurrent registration of the same Unity Catalog
model name from another job would be served instead; `log_model` returns the version it
registered. The connectivity pre-check treats every HTTP error but 403 as "workspace
reachable", so an expired token (401) passes and fails later inside MLflow with a less
direct message. `get_deploy_status` defaults the schema to `default` while
`DatabricksConfig` defaults it to `pricing`.

**Plan:** Read `ModelInfo.registered_model_version` from the log call; treat 401 like
403; take the schema from the one `DatabricksConfig` default.

**Acceptance:** The deploy result's version is the one `log_model` returned; a 401 from
the pre-check fails with the credential message; the two defaults are one constant.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_mlflow.py::_deploy_to_mlflow`,
`::_check_databricks_connectivity` and `::get_deploy_status`;
`src/haute/deploy/_config.py::DatabricksConfig`.

### DEP-R09 — The container's dependency closure is pinned
**Why:** The Dockerfile pins the scoring-runtime distributions and the artefacts' engines
to the deploying environment's versions, but their transitive dependencies (starlette,
anyio, h11, typing-extensions, ...) resolve at image build time, so two builds of one
deploy can differ, against the specification's reproducibility rationale.

**Plan:** Export the deploying environment's lock for the runtime closure
(`uv export` or an equivalent filtered freeze) into the build directory and install
from it; keep the direct pins as the readable summary.

**Acceptance:** The generated build directory holds a requirements file naming every
installed distribution with `==`; two builds from one environment produce identical pip
install lines.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_container.py::_generate_dockerfile` and
`::_pinned_dockerfile_deps`.

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

**Plan:** Fail the report when either endpoint's row count differs from the records
sent (the request-order proof guarantees one row per quote); raise a typed error
carrying the status code from `score_http_endpoint_batched`, and treat a 404 as a first
deploy only when the production `/health` is unreachable too.

**Acceptance:** A staging response one row short fails `haute impact` naming both
counts; a production URL whose `/quote` answers 404 while `/health` answers fails the
step naming the URL.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_impact.py::build_report` and
`::score_http_endpoint_batched`; `src/haute/cli/_impact.py::_is_http_not_found` and
`::_impact_http`.

### DEP-R12 — Build directories and caches under the project
**Why:** The container build writes `cwd/.haute_build`, removes it wholesale on failure
(whatever a user kept there) and leaves it behind on success with a copy of every
artefact; a reused directory keeps earlier builds' `artifacts/*`, which the Dockerfile's
`COPY artifacts/ artifacts/` ships although the manifest never names them. The MLflow
target roots its build directory at the pipeline's directory instead. The output-schema
cache is `.haute_cache/output_schema.json` relative to the process cwd, one entry
overwritten by whichever pipeline deployed last.

**Plan:** Build in a fresh directory under the resolved project root (removed after the
push, kept only on request), stage artefacts into an empty folder, and root the schema
cache at the project root with one entry per fingerprint.

**Acceptance:** Two consecutive container builds from one project produce an image
containing only the manifest's artefacts; `haute deploy --dry-run` run from another
directory reads and writes no file outside the project.

**Dependencies:** `DEP-R05` to `DEP-R11`, which change the same files.

**Evidence:** `src/haute/deploy/_container.py::build_and_push_image` and
`::write_bundle`; `src/haute/deploy/_mlflow.py::_deploy_to_mlflow`;
`src/haute/deploy/_schema.py::infer_output_schema`.

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
