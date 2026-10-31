# CLI — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/__main__.py` | Package-module entry point; imports and invokes the canonical `haute.cli:cli` group for `python -m haute`. |
| `src/haute/cli/__init__.py` | Builds the Click command group (`cli`), registers all thirteen commands and command groups, exposes `--version`; the group's callback sets the process's output streams through `utf8_output` before any command prints. Its first import is the Polars startup module below, before anything imports Polars. |
| `src/haute/cli/_polars_startup.py` | Applies Haute's Polars settings (spilling off, its folder in the working directory's `.haute_cache/polars-spill`, unknown extension columns as storage; [execution engine](../execution-engine/low-level.md)) and reaps ended processes' spill folders; `SPILL_ROOT` and `POLARS_SETTINGS`, which `haute serve` logs. |
| `src/haute/cli/_helpers.py` | Cross-command utilities: `resolve_model_name`, `_open_browser`, `_node_env`, `_npm`, `_find_frontend_dir`, the `TransportInfo`/`resolve_transport` transport-dispatch helper, and the shared `ENDPOINT_SUFFIX_HELP` string. |
| `src/haute/cli/_output.py` | `utf8_output`: the process's own stdout and stderr, when not UTF-8, written in UTF-8, or, when PYTHONIOENCODING chose the encoding or the stream is a terminal, kept with a mark it cannot hold replaced; a stream a caller put in the process's place left alone. |
| `src/haute/cli/_init_cmd.py` | `haute init` — project scaffolding: `InitConfig`, `handle_init`, TOML-aware `pyproject.toml` dependency injection, CI-provider file generation (`haute._scaffold.ci_files`, written by `write_generated_files`, which `haute ci update` shares) and pruning, the `--workbench` table and form ([workbench](../workbench/low-level.md)), and next steps that name the CI secrets the first deploy needs (`ci_secret_names`), with `haute ci secrets` on GitHub. |
| `src/haute/cli/_run.py` | `haute run` — `RunConfig`, `handle_run`, parses + executes a pipeline and prints per-node results; a pipeline with a Workbench node is given its tables from the form folder of the project the file is in (`haute._workbench_nodes.with_form_tables`), a form that cannot be read reported as "Error reading the workbench's form: ...". |
| `src/haute/cli/_lint.py` | `haute lint` — `LintConfig`, `handle_lint`, structural validation without execution. |
| `src/haute/cli/_train.py` | `haute train` — `TrainConfig`, `handle_train`, loads a training script as a module, runs its `job`, prints a live progress bar. |
| `src/haute/cli/_serve.py` | `haute serve` — `ServeConfig`, `handle_serve`, host-safety checks, working-directory/editable-checkout frontend selection, dev/prod mode dispatch, Vite and uvicorn launch/cleanup. |
| `src/haute/cli/_deploy.py` | `haute deploy` — `DeployCliConfig`, `handle_deploy`, CI-gate check, resolve → validate-and-score-once → render quote results → deploy pipeline. |
| `src/haute/cli/_ci.py` | `haute ci` — the `ci` group. `secrets`: `CiSecretsConfig` and `handle_ci_secrets`, which sets every one of the deploy target's `ci_secret_names` on GitHub from `.env`, else the environment, through `haute._ci_secrets` (each value refused unset or still `.env.example`'s placeholder, each Databricks pair checked unless `--no-verify`), printing where each came from; `--check` compares the repository's list; another provider is given the names and its guide. Each of that module's failures is a Click error with its message. `update`: `handle_ci_update`, which rewrites `ci_files` for `[ci] provider` and `[deploy] target`, with the project's workbench and Python, and `.env.example`, through `write_generated_files`, pruning another provider's generated files. `registry-host`: `handle_ci_registry_host`, which prints the host `docker login` takes for `[deploy.container] registry`, for the login step of a container target's generated deploy jobs. |
| `src/haute/_ci_secrets.py` | The deploy's CI secrets on GitHub, for `haute ci secrets` and the editor's Push. `CiSecretsError` (a step that could not be taken; its message says why and never holds a value) and its `DatabricksRefusedError`. The GitHub CLI's subprocess chokepoint: `resolve_gh` (`gh` on the search path) and `run_gh` (prompts disabled, `GH_TIMEOUT_SECONDS`, UTF-8 pinned, a failure raised with `gh`'s own message, known values redacted), under `github_secret_names` (`gh secret list`) and `set_github_secret` (`gh secret set NAME`, the value on standard input), each on the repository `--repo` names, else the one `gh` resolves from the project's remotes. `secret_values` (`.env`, else the environment, and where each came from), `usable_secret_values` (unset and placeholder values refused, `env_example_placeholders`) and `verify_databricks` (each pair through `haute.deploy._databricks_serving.workspace_user`). `github_repository`, the `OWNER/REPO` a `github.com` remote URL names. The Push's step: `RemoteSecrets` and `remote_secrets` (the deploy's secrets on one remote's repository, or none where they do not apply), `push_with_ci_secrets` (`POST /api/git/push`) and `set_missing_ci_secrets` (`POST /api/git/ci-secrets`), whose outcome is a `GitCiSecrets`. |
| `src/haute/cli/_app.py` | `haute app` — the `app` group and its `build` and `serve` commands: `AppBuildConfig` and `handle_app_build` (the working directory's release through `build_release` into the directory given, `RELEASE_DIR` by default, and with `--image` its image through `build_image`), `AppServeConfig` (with `reader`, `--reader`) and `handle_app_serve` (the release built into `RELEASE_DIR`, the quote store at `QUOTES_FILE` brought to the release's form in dev's mode through `_local_store`, `prepare_store`, each table whose quotes' rows went and each column whose values went named (`dropped`, `cleared`), then `create_app` with the git identity from `get_identity` as its local principal, refused without one, and that store, dev's `StoreLocation`, a reader with `--reader`, served by `uvicorn` on `SERVE_HOST` alone); any fault Haute or SQLAlchemy names (a release, a project or a store that is not as the command needs it) said as a Click error through `_said`, never a traceback, and a note when `[workbench.app] host` is unset. |
| `src/haute/cli/_smoke.py` | `haute smoke` — `SmokeConfig`, `handle_smoke`, waits for a Databricks endpoint through the serving pair's client (`haute.deploy._databricks_serving`), then sends each test quote to a live endpoint (Databricks or HTTP) as a request of its own through `_smoke_files`, which reports each file from `price_test_quotes_live`. |
| `src/haute/cli/_status.py` | `haute status` — `StatusConfig`, `handle_status`, MLflow Model Registry lookup. |
| `src/haute/cli/_impact.py` | `haute impact` — `ImpactConfig`, `handle_impact`, staging-vs-production comparison report on `_impact_records` (the `[safety]` portfolio sample, else the test quotes); `_impact_databricks` (the serving pair's client)/`_impact_http` transport backends. |
| `src/haute/cli/_gpu_setup.py` | `haute gpu-setup` — `GpuSetupConfig`, `handle_gpu_setup`, swaps `xgboost-cpu` for XGBoost's CUDA build (or back) in the running interpreter and verifies it in a fresh process. |

## Key types and data structures

Every command uses a plain mutable `@dataclass` as a configuration value bag consumed by a
`handle_*` function.

- `InitConfig(target, ci, force=False, workbench=False)` — `_init_cmd.py`.
- `CiSecretsConfig(check_only, verify)` — `_ci.py`.
- `RunConfig(pipeline_file: Path)` — `_run.py`.
- `LintConfig(pipeline_file: Path)` — `_lint.py`.
- `TrainConfig(training_script: Path)` — `_train.py`.
- `ServeConfig(port, no_browser, host="localhost")` — `_serve.py`. The `host` default is on the
  dataclass itself (not just the Click option) so every non-CLI caller inherits the loopback-safe
  default too. The browser-facing default deliberately uses the hostname rather than a numeric
  loopback address because the local HttpOnly session-cookie flow must work in ordinary browsers
  with a bare `haute serve`.
- `DeployCliConfig(pipeline_file, model_name, endpoint_suffix, dry_run)` — `_deploy.py`. Distinct
  from `haute.deploy._config.DeployConfig`, which is the fully-resolved deploy configuration built
  from this plus `haute.toml`.
- `SmokeConfig(endpoint_suffix: str | None)` — `_smoke.py`.
- `StatusConfig(model_name, version_only)` — `_status.py`.
- `ImpactConfig(endpoint_suffix, sample, batch_size)` — `src/haute/cli/_impact.py`.
- `GpuSetupConfig(check_only, to_cpu, assume_yes)` — `_gpu_setup.py`.

Other notable types:
- `TransportInfo` (`src/haute/cli/_helpers.py`) — `__slots__`-based result of `resolve_transport(config)`. Fields:
  `kind` (`"databricks" | "http" | "unsupported"`), `staging_url`, `prod_url`. `smoke` and `impact`
  both switch on `.kind` to pick a backend.
- `_Closable` (`_serve.py`) — a `typing.Protocol` describing the socket-like object the TCP
  readiness probe needs (`close()` only), used to type `_wait_for_tcp_ready`'s injectable `connect`
  callable for testability.
- `RemoteSecrets(project_dir, target, repository, names)` (`src/haute/_ci_secrets.py`) — a frozen
  dataclass: the deploy's secret names on one remote's GitHub repository (`OWNER/REPO`).
- `GitCiSecrets` (`src/haute/schemas.py`) — what a Push or Set from .env found:
  `repository`; `state`, one of `ready` (the repository has every one of the deploy's
  secrets), `missing` (it lacks those `missing` names) or `unknown` (its secrets could not be
  read); `newly_set`, the names this action set; and `problem`, why the secrets could not be
  read or set, else `null`.

### Command and option contract

Every command below can be invoked by replacing the leading `haute` token with
`python -m haute`. The module entry point performs no parsing of its own: Click
receives the original argument vector and retains the same options, validation,
handler, and exit-code behaviour. Click's usage prefix identifies which
invocation form the user selected.

Every subcommand also exposes Click's eager `--help` option, which prints usage and exits 0
without validating required positional arguments.

`haute run` reports expanded executable nodes and previews the last executable
result. Metadata-only submodel occurrence entries carry schema warnings, not
rows, and are excluded from both selections. The CLI does not print schema warnings.

| Command | Arguments and options | Exit and failure contract |
|---|---|---|
| `haute` | `--version`; `--help`. | Both print and exit 0. Unknown commands/options are Click usage errors (exit 2). |
| `haute init` | `--target` choice: `databricks` (default), `container`, `azure-container-apps`, `aws-ecs`, `gcp-run`; `--ci` choice: `github` (default), `gitlab`, `azure-devops`, `none`; `-f`/`--force`; `--workbench`. The `--target` help labels `azure-container-apps`, `aws-ecs` and `gcp-run` build and push only; the planned `sagemaker` and `azure-ml` targets are not offered. | Success 0. Existing `haute.toml` without `--force` exits 1. Invalid choices exit 2 before the handler. File/TOML/scaffold write errors are not converted into a fallback. |
| `haute run [PIPELINE_FILE]` | Optional path; absent input uses `resolve_pipeline_file` project/discovery rules. | Missing/ambiguous file, parse failure, empty graph, executor failure, or any node result with non-`ok` status exits 1; success exits 0 after the optional final preview. |
| `haute lint [PIPELINE_FILE]` | Optional path resolved exactly as `run`. | Missing/ambiguous file, parse failure, empty graph, or any collected structural issue exits 1; a clean graph exits 0. |
| `haute train TRAINING_SCRIPT` | Required positional path. | Omission is a Click exit-2 usage error. Missing/unsafe/unloadable script, missing `job`, script exception, or `job.run` failure exits 1; successful training exits 0. |
| `haute serve` | `--host TEXT` (CLI → `[server].host` → `localhost`); `--port INTEGER` (effective default `8000`, not shown by current help); `--no-browser`. Port range is not Click-validated: negative/out-of-range integers reach socket setup and may raise rawly. | Port conflict or missing production static build exits 1; malformed `haute.toml` propagates `ConfigError`. A browser-launch failure prints the manual URL but leaves the running server path intact. |
| `haute deploy [PIPELINE_FILE]` | Optional path; `--model-name TEXT`; `--dry-run`; `--endpoint-suffix TEXT`. | Non-dry-run outside recognised CI exits 1. A non-dry-run deploy whose target's credentials are not all set (`haute.deploy.check_credentials`) exits 1 before parsing, naming each. Resolution, validation, either validation's quote pass or the separately printed quote pass, missing backend dependency, unimplemented target, and backend failure all exit 1. Dry-run success exits 0 before backend dispatch. |
| `haute ci secrets` | `--check` (report, set nothing); `--no-verify` (skip the Databricks check). | GitHub only, through `gh`: another provider exits 1 naming the secrets to add and its guide; no `gh` on `PATH` exits 1 saying where to get it. A secret unset in `.env` and the environment, or still `.env.example`'s placeholder, exits 1 before anything is set, as does a Databricks pair the workspace refuses (its token redacted from the message). A `gh` failure, or a `gh` call still running after `GH_TIMEOUT_SECONDS`, exits 1 with its message. `--check` exits 1 naming each missing secret, 0 when all are set. Values are never printed. |
| `haute ci update` | None. | Exits 1 without `haute.toml`, for an unreadable Python declaration or `[workbench]` table, an unknown `[ci] provider`, a `[ci.staging] endpoint_suffix` that cannot name a staging endpoint (empty, not a string, or holding anything but ASCII letters, digits, `-` and `_`), or a `[ci.production] environment` that cannot name an environment, writing nothing. Otherwise rewrites the generated CI files, whose staging deploy steps pass that suffix, as a Databricks project's smoke and impact steps do (a container-based target's smoke and impact steps pass none and call the `[ci.staging]` and `[ci.production]` `endpoint_url` addresses), and whose production job runs under that environment when one is named, and `.env.example`, reports each updated or unchanged, and exits 0. |
| `haute smoke` | `--endpoint-suffix TEXT`. | Missing config/quotes/endpoint, a missing serving pair (named before any request), an endpoint update Databricks reports failed or cancelled (with its state message and build and server log tails), an endpoint that does not exist (at once), missing Databricks SDK, an installed SDK too old to expose the required `NotFound` error type, unsupported target, readiness timeout, health-request failure, any scoring request failure, an unreadable test-quote file, or an expected output the answer does not match exits 1. Missing and outdated SDKs produce distinct install/upgrade guidance. A quote without an `expected` output passes on any answer. |
| `haute status [MODEL_NAME]` | Optional model name; `--version-only`. | Missing resolvable name or MLflow dependency exits 1. Normal mode prints “not found” and exits 0; `--version-only` prints only a version on success and raises `ClickException` (exit 1, stderr only) when no version exists. |
| `haute impact` | `--sample INTEGER` (default `10000`; every value `<=0` currently means all, although help documents `0`); `--batch-size INTEGER` (default `500`, minimum `1`); `--endpoint-suffix TEXT`. | Invalid batch size exits 2. Missing config/suffix, a named `impact_dataset` that does not exist, no test quotes to compare without one, a missing serving pair or missing Databricks SDK exits 1. Endpoint/scoring/arithmetic/write failures propagate. Unsupported transport returns successfully without a report only after TOML, suffix, and dataset/parquet loading have succeeded; otherwise success writes `impact_report.md` and exits 0. |
| `haute gpu-setup` | `--check` (report only); `--cpu` (switch back to `xgboost-cpu`); `--yes`/`-y` (skip confirmation). | `--check` exits 0. Exactly one of `xgboost-cpu`/`xgboost` must be installed, else exit 1. The target build already installed exits 0 without changes. Any switch on macOS (which installs the standard `xgboost` package and has no CUDA build), or a CUDA request without an `nvidia-smi` GPU, exits 1 before installing. A declined confirmation, a failed uninstall/install step (the message names the command that restores the previous build), an XGBoost that no longer imports, or a fresh-process CUDA check that disagrees with the request exits 1. |

## Control flow

**`init`**: `handle_init` checks for an existing `haute.toml` (abort unless `--force`), resolves the
project name from `pyproject.toml` (creating/patching it via `_ensure_haute_dependency`, which does
a structural TOML edit rather than string templating so existing content survives, and adds a
`[dependency-groups]` dev group of ruff, mypy and pytest and a `[tool.mypy]` table only when the
file has none: `mypy_path = "rating"` and `explicit_package_bases = true`, so the generated CI's
`uv run mypy .` resolves what `rating/main.py` imports from its own folder (`utility`, and
`modules` for its submodels) as Python does when it runs the file, and an override ignoring
missing imports for `haute`, `catboost`, `xgboost`, `lightgbm` and `sklearn`), creates the
`rating/` package tree (including `rating/utility/__init__.py` and
`rating/utility/features.py`), an empty `rating/config/`, and the empty `rating/files/data/`, `rating/files/models/` and `rating/files/params/` folders where versioned data, model and parameter files go ([pipeline-config](../pipeline-config/high-level.md)), writes `haute.toml` via `haute._scaffold.haute_toml` (with the `[workbench]` table when
`--workbench` is given, and then the blank form folder's files in `rating/workbench/` via
`haute._scaffold.starter_form`; `--workbench` in a project that has a `haute.toml` and no
`--force` takes its own path, adding the `[workbench]` table to that `haute.toml` and the
blank form folder to its pipeline folder, nothing else, and refusing with exit 1, naming
what is there, when either already is),
writes `.env.example`, writes starter tests, writes `.python-version` with the project's Python when it has none
(`haute._project_python.project_python_version`), writes CI workflow files for the chosen
provider on that Python
(pruning a *different* provider's stale files first on `--force`), installs a pre-commit hook into
`.githooks/` and — if inside a git repo — `.git/hooks/`, and appends `.gitignore` guard entries via
`haute._gitignore_guard.ensure_gitignore_guards`. A pre-existing root `main.py` is deleted and
reported: `uv init` recreates that placeholder whenever it runs and the project's real entry
point is `rating/main.py`, so the root file is treated as a tooling artifact, not user content. The generated pipeline declares only
`haute.Pipeline(name)` and therefore parses with zero nodes; its starter test checks that parse and
name contract without requiring execution data. Init creates neither `prompts/` nor node sidecars.

**`run`**: resolves the pipeline file via `haute._project.resolve_pipeline_file`, calls
`parse_pipeline_file(..., flatten=True)` then `execute_graph`, prints one line per node (row/column count or error),
exits 1 if any node failed, then previews the last node's output as a `polars.DataFrame`.

**`lint`**: resolves the pipeline file and parses it through the strict canonical entry point. Any
syntax, configuration, topology, or other parse failure is reported and exits 1; editor recovery
fragments never reach lint. For a valid graph it checks edges referencing missing node ids and, for
multi-node graphs, orphan nodes with no edges at all. Those post-parse findings are collected before
reporting so one run surfaces every independent structural issue.

**`train`**: validates the script exists, loads it as a module (trusted project code, run
without the server's accident guard) via `importlib.util`, looks up a module-level `job` attribute, and calls
`job.run(progress=_progress)` without an `isinstance(TrainingJob)` check. The documented/generated
shape is a `TrainingJob`, but at runtime any object implementing that call and returning the fields
the formatter reads is accepted. Execution and rendering have separate exception boundaries:
after `job.run()` returns, malformed result fields produce a “training succeeded, reporting
failed” error and never a “Training failed” message.
`_progress` renders a `\r`-carriage-return progress bar and explicitly flushes stdout after every
write (documented as load-bearing — `click.echo(nl=False)` alone can leave the line buffered).

**`serve`**: `handle_serve` runs, in order: `_require_loopback_host` (rejects every non-loopback
value before any startup side effect), `_configure_trusted_hosts` (clears any stale
`TRUSTED_HOSTS_ENV` remote-bind policy), `_detect_dev_frontend_dir` to choose dev vs. prod mode,
then `_abort_if_port_in_use` (pre-flight socket bind/close probe — `SO_EXCLUSIVEADDRUSE` on Windows
to avoid a false-negative from `SO_REUSEADDR`; dev mode also runs `_abort_if_vite_port_in_use` to
pre-flight the fixed Vite listener at `127.0.0.1:5173`). Frontend detection first considers the
nearest `frontend/package.json` in the working directory's ancestor chain. If that candidate has
no installed `node_modules`, or no such candidate exists, it considers the `frontend/` beside the
imported package when that package is an editable source checkout. A viable working-directory
candidate wins; a wheel install or source checkout without installed dependencies selects prod
mode rather than serving source through an unrelated generated bundle.
  - **Dev mode** (`_run_dev_mode`): spawns `npm run dev` as a subprocess (`_start_vite_subprocess`,
    with `SIGINT`/`SIGTERM` handlers wired to terminate the child). It creates the backend's
    process-local session token but removes `VITE_HAUTE_SESSION_TOKEN` from the child environment,
    so the frontend cannot read the credential. The server-only `HAUTE_BACKEND_URL` carries the
    selected loopback host/port into Vite's same-origin `/api` and `/ws` proxy (including bracketed
    IPv6) without becoming client code. It schedules a background thread
    that polls both the backend TCP port and Vite's fixed TCP port and opens the browser only
    once both are accepting connections
    (`_open_browser_after_servers_ready` → `_wait_for_servers_then_open_browser` → `_wait_for_tcp_ready`),
    then runs `uvicorn.run(...,
    reload=True, reload_dirs=[haute package dir])`. The Vite subprocess is
    terminated in a `finally` block on every uvicorn exit path.
  - **Prod mode** (`_run_prod_mode`): checks `static_build_ready(STATIC_DIR)`, fails loudly with a
    build-hint message (`_missing_static_message`, which distinguishes a source checkout — "run npm
    build" — from an installed wheel — "reinstall haute") if not ready, schedules a delayed browser
    open, and runs plain `uvicorn.run(...)` with no autoreload.
  - Host resolution precedence (in the Click wrapper, before `ServeConfig` is built): `--host` flag
    → `[server] host` in `haute.toml` (`_load_toml_server_host`) → `"localhost"`. Therefore plain
    `haute serve` binds and opens `http://localhost:8000` without requiring project configuration.

**`deploy`**: `handle_deploy` loads `DeployConfig` from `haute.toml` if present, else builds one from
CLI args via `resolve_pipeline_file` + `DeployConfig.from_cli_args`. Blocks non-dry-run deploys
outside CI (`_detect_ci_env`). A non-dry-run deploy then loads the project's `.env` and runs
`haute.deploy.check_credentials` before anything else: a Databricks deploy needs its MLflow
destination and the serving pair, and every one missing is named at once, in seconds rather than
after resolution and validation. A TOML-configured pipeline path is also normalised through
`resolve_pipeline_file`; all command paths therefore share one missing/ambiguous-path contract.
Applies CLI overrides (`pipeline_file`, `model_name`,
`endpoint_suffix`) on top of the loaded config via `.override(**overrides)`. Then: `resolve_config`
(parse/prune/collect artifacts/infer schemas) → `validate_deploy` (which scores configured
quotes once as part of its aggregate gate and returns the successful per-file results) → render
those returned timing/status results → (return early if `--dry-run`) → `deploy_resolved`.
The CLI never calls `score_test_quotes` directly. Each stage prints a `✓`/`✗`
progress line. Resolution keeps its existing user-facing failure boundary;
validation renders `DeployError`; target dispatch renders `DeployError`,
`ImportError`, and `NotImplementedError`. An unexpected target-backend
exception is deliberately not caught, preserving its original type and
traceback.

**`smoke`**: requires `haute.toml`; loads `DeployConfig`, applies an optional endpoint-suffix
override, requires a non-empty `tests/quotes/*.json` set, then dispatches on
`resolve_transport(deploy_config).kind`:
  - `"databricks"` (`_smoke_databricks`): reaches the endpoint with the serving pair's client
    (`serving_workspace_client`, as deploy does: BUG-22's default-credential client failed in CI) and
    waits with `wait_for_endpoint` until it is ready and not updating, then queries the endpoint once
    per test quote (`dataframe_records=[input]`; `None` predictions are a failure). A deploy returns
    once its endpoint serves, so the wait is short. An update Databricks reports failed or cancelled
    stops at once with its reason and log tails; an endpoint that does not exist stops at once; every
    other lookup failure stops immediately with its cause.
  - `"http"` (`_smoke_http`): hits `<url>/health` once, then POSTs each test quote alone via
    `score_http_endpoint_batched(url, [input], batch_size=1)`. An explicit suffix is rejected
    because an HTTP deployment must supply its complete `[ci.staging].endpoint_url`.
  Both hand `_smoke_files` a `price` callable for one quote. `price_test_quotes_live`
  (`deploy/_validators.py`) reads the file's cases, prices each, and checks a case's
  `expected` output against its own answer with `_response_errors`, the per-case check
  `score_test_quotes` uses, so a mismatch reads the same as the deploy check's. Every file
  and every quote runs even after a failure, and the run reports a combined pass/fail at
  the end.

**`status`**: resolves the model name with CLI > `HAUTE_MODEL_NAME` > TOML precedence
(`resolve_model_name`), loads catalog/schema from
`haute.toml` if present else `DatabricksConfig()` defaults, calls
`haute.deploy._mlflow.get_deploy_status`. `--version-only` mode prints only the version number (for
scripting) and raises `click.ClickException` — rather than printing a misleading `0` — when no
version is registered.

**`ci secrets`**: `handle_ci_secrets` reads the project's `haute.toml`; the deploy target's
`ci_secret_names` are the secrets its generated deploy, registry login, smoke, impact and
tag steps read. On
GitHub it needs `gh`: `--check` lists the repository's secrets (`gh secret list --json name`) and
marks each name set or missing. Otherwise each value is read from `.env`, else the environment,
refused unset or equal to `.env.example`'s placeholder, and for Databricks each pair is checked
against the workspace (`workspace_user`, `current_user.me()` with that host and token) unless
`--no-verify`; only then is each passed to `gh secret set NAME` on its standard input, a secret
the repository has included, so the command is how a changed value reaches CI. It names no
`--repo`: `gh` resolves the repository from the project's remotes. `haute._ci_secrets` is
`gh`'s subprocess chokepoint: `resolve_gh` finds it with `shutil.which`, and `run_gh` runs it in
the project with `GH_PROMPT_DISABLED=1`, a `GH_TIMEOUT_SECONDS` (60) timeout and UTF-8 pinned, a
non-zero exit or timeout raised as `CiSecretsError` with `gh`'s own message, every value
being set redacted from it.

**The deploy's secrets on Push** (`push_with_ci_secrets`, which `POST /api/git/push` calls).
`remote_secrets(remote, project_dir)` reads only local state. The remote comes first: its
fetch URL (`git remote get-url`) must be on `github.com` (`https://`, `ssh://` or scp-like
`git@github.com:OWNER/REPO`, with any userinfo, port, trailing `/` or `.git` dropped), and
every URL a push to it lands on (`remote_push_urls`) must name the same repository, compared
without regard to case as GitHub's names are, since the secrets belong where `main` arrives.
`github_repository` reads exactly two path segments, an owner of letters, digits and `-` and
a repository of letters, digits, `.`, `_` and `-`; another host, a local path or another
shape is not GitHub. Then the project's `haute.toml` must set `[ci] provider = "github"` and
its target have secret names. Where they do not apply, the response carries no `ci_secrets`
and `gh` is never run. Settings that cannot be used (an unreadable or malformed `haute.toml`
or `.env`, or a value `DeployConfig.from_toml` or the target lookup refuses, an unknown
target or an unpinned container image, say) are the response's `unknown` with that
`problem`, and the push goes ahead. Where
they apply, the remote is inspected first (`remote_is_empty`, the strict advertisement read
the Push itself makes, whose failure fails the Push as the Push's own would). A remote with no
object refs is a first publication: before `push_working_pair` creates the default branch,
the repository's secrets are listed and the ones it lacks are set from `.env`, so the
workflow runs the default's creation starts already have them. Every one of the deploy's
secrets must be readable from `.env` or the environment and not a placeholder, and each
Databricks pair checked, before any is set; that check takes seconds, so the repository is
listed again after it, and a secret it gained meanwhile is left as it is. The editor's
secrets steps run one at a time (a process-wide lock). Nothing Haute lists is overwritten;
GitHub's API creates or updates, so a write by someone else between that last listing and
Haute's own cannot be excluded. The outcome is the response's `ci_secrets`: `ready` with
`newly_set` naming what was set (empty when the repository already had every one); `missing`
with the names still absent and `problem` saying why (a value unset or a placeholder, `.env`
unreadable, a pair Databricks refused, a `gh secret set` failure, after which the ones
already set stay set and are left out of `missing`); or `unknown` with `problem` saying why
the list could not be read (no `gh`, not logged in, no access to the repository's secrets, a
timeout). None of these stops the push. After any other Push the repository's secrets are
listed once the push has landed and reported `ready`, `missing` (with no `problem`) or
`unknown`; nothing is set. A push that fails raises as before, and secrets a first
publication set before it stay set.

`POST /api/git/ci-secrets {remote}` (`set_missing_ci_secrets`) sets the secrets the
repository lacks the same way, never overwriting one it lists, and answers `ready` with
`newly_set`. A remote the step does not apply to (not configured, not on `github.com`,
pushing somewhere other than it fetches from, a project whose provider is not GitHub or whose
target has no secrets), settings that cannot be read, and every `CiSecretsError` are a 400
naming why; a secret set before a later one failed stays set.

**`ci registry-host`**: `handle_ci_registry_host` reads `[deploy.container] registry` from
the project's `haute.toml` and prints the host a `docker login` before the push takes: the
registry's first `/` segment when Docker reads it as a host (it holds a `.` or a `:`, or is
`localhost`), `docker.io` when the registry is a Docker Hub namespace such as `myteam`, and
an empty line when the registry is empty. The host is the last line it prints: a log line
may come first (on Windows, the CPU QoS line), so a container-based target's generated
deploy jobs read the last line in their login step
([pipeline-config](../pipeline-config/low-level.md)).

**`ci update`**: `handle_ci_update` regenerates the CI files `haute init` would write for the
project's `[ci] provider` and `[deploy] target`, with its workbench, Python,
`[ci.staging] endpoint_suffix` and `[ci.production] environment`, plus `.env.example`, so a
project takes up a newer haute's workflows, a changed staging suffix or an environment that
gates production; another provider's generated files are pruned, and nothing else is touched.
Both values are checked first, each failure a CLI error naming its key before any file is
written: the suffix by `staging_suffix_problem` (an empty one would deploy staging over
production, and one holding anything but ASCII letters, digits, `-` and `_` cannot name a
Databricks endpoint) and the environment by `production_environment_problem`.

**`gpu-setup`**: `installed_xgboost` reads the installed distribution (exactly one of `xgboost-cpu` and `xgboost`, which own the same files); `haute._host_memory.nvidia_gpu_name` asks `nvidia-smi`; this module is the subprocess chokepoint for the installer and the fresh-interpreter check. `--check` prints both plus `xgboost_gpu_status()`. Otherwise the target is `xgboost` (or `xgboost-cpu` with `--cpu`) pinned to the installed version; `installer_commands` uninstalls the current distribution then installs the target into `sys.executable`, through `uv pip --python` when `uv` is on the PATH, else `python -m pip`. `fresh_cuda_build` then imports XGBoost in a new interpreter and checks `build_info()['USE_CUDA']` matches the request. The running server keeps its imported build until restarted, and `uv sync` restores the locked `xgboost-cpu`.

**`impact`**: requires `haute.toml`; resolves the staging suffix (CLI
flag wins, else `deploy_config.ci.staging_endpoint_suffix`, else loud error on Databricks transport;
on other transports an empty suffix yields `staging_name == prod_name`); `_impact_records` reads and
optionally samples (`df.sample(n=..., seed=42)`) the parquet `[safety].impact_dataset` names, or,
with none named, compares the test quotes' requests (`test_quote_requests`, as smoke sends them)
one quote per request as smoke sends them (an endpoint a Workbench Input answers takes exactly
one), with segments from their top-level text fields, and says so (BUG-23: the scaffold's default
sample sat in the gitignored `data/`); dispatches to
`_impact_databricks`/`_impact_http` based on `resolve_transport(...).kind`; Databricks probes
the production endpoint first, while HTTP treats a missing production URL as first deploy and
otherwise discovers absence only when scoring yields HTTP 404. Both score staging and, when
present, production, then build an `ImpactReport` — a first-deploy
variant with empty comparison data when there is no production endpoint yet, otherwise a full
`build_report` diff; prints the terminal report, always writes `impact_report.md`, and additionally
appends to `$GITHUB_STEP_SUMMARY` when that env var is set.

## Edge cases and invariants

- **Windows `chmod` no-op.** `_init_cmd.handle_init` skips `Path.chmod(0o755)` on the pre-commit hook
  entirely on `win32` (NTFS ignores POSIX bits and git-on-Windows ignores the executable bit too),
  documenting the manual `git update-index --chmod=+x` workaround in a comment instead of calling a
  chmod that would silently do nothing.
- **TOML-aware, not string-templated, dependency injection.** `_init_cmd._rewrite_project_dependencies`
  and its helpers (`_scan_table_headers`, `_find_project_table_bounds`, `_find_matching_bracket`,
  `_toml_basic_string`) hand-roll a quote/comment-aware TOML table scanner so injecting `"haute"`
  into an existing `pyproject.toml`'s `[project].dependencies` array cannot be fooled by a `[foo]`
  literal inside a triple-quoted string, and correctly escapes PEP 508 environment markers (which
  contain embedded double quotes) when re-serialising dependency entries.
- **Dotted-key `[project]` tables.** `_rewrite_project_dependencies` distinguishes a file with a
  literal `[project]` header from one that only has `project.dependencies = [...]` at the root
  (which `tomllib` parses into the same nested dict but has no textual `[project]` header to locate)
  — the dotted-key case is rewritten in place rather than appending a second `[project]` table, which
  TOML would reject as a duplicate declaration.
- **CI marker falsy-value handling.** `_deploy._detect_ci_env` treats `"0"`, `"false"`/`"False"`/
  `"FALSE"`, and `"no"`/`"No"`/`"NO"` as *not* CI, even though the env var is present — a provider
  that sets its marker to a falsy string should not be treated as "running in CI".
- **Loopback detection covers the whole `127.0.0.0/8` range**, not just `127.0.0.1` — `_serve._is_loopback_host`
  parses via `ipaddress` and checks `.is_loopback`, so `127.0.0.42` counts as safe.
- **Wildcard and network-visible binds never reach probing.** `_require_loopback_host` runs before
  `_abort_if_port_in_use`, frontend detection, Vite, or uvicorn. The lower-level
  `_backend_probe_host` helper remains general-purpose, but `handle_serve` never calls it with a
  non-loopback host.
- **`_wait_for_tcp_ready` only swallows `ConnectionRefusedError`/`TimeoutError`.** Any other socket
  exception (e.g. an unresolvable hostname) propagates rather than being retried into a misleading
  timeout.
- **`impact`'s sampling only triggers when it would shrink the dataset** (`config.sample > 0 and
  total_rows > config.sample`) — a larger value, zero, or any negative value scores every row;
  the negative-value behaviour is an unvalidated current gap rather than a documented option.
- **The editor never overwrites a deploy secret it can see.** `push_with_ci_secrets` and
  `set_missing_ci_secrets` set only names the repository's list lacks, listed again after the
  Databricks check and one editor step at a time; only `haute ci secrets` replaces a value the
  repository has. GitHub's API creates or updates, so a write by someone else after the last
  listing is the one case Haute cannot rule out.
- **Secrets go where the push lands.** A remote whose push URLs name another repository than
  its fetch URL, or several, gets none: Haute will not guess which repository runs the deploy.
- **The secrets never block the push.** Settings or `.env` that cannot be read are reported
  in `ci_secrets`, and the push goes ahead.
- **A first publication sets the secrets before the default exists.** They are set ahead of
  `push_working_pair`, so a push that then fails leaves them set, and the next Push's listing
  finds them. A remote emptied between the inspection and the push is bootstrapped by the push
  without them; the listing after it reports them `missing`.
- **`status --version-only`** distinguishes "no version" from a genuine version `0` by raising
  instead of printing — printing `0` unconditionally would be indistinguishable from a real version
  number to a scripted caller checking stdout.
- **`subprocess` imported but unused at runtime in `src/haute/cli/_helpers.py`.** The module-level `import
  subprocess  # noqa: F401` exists solely so tests can patch `subprocess.call`/`subprocess.Popen` and
  assert they are never invoked from this module (`_open_browser` uses `webbrowser` exclusively) —
  documented in a comment referencing "codebase-review #79".

## Error handling

- Explicitly handled operational failures use `SystemExit(1)` (normally after
  `click.echo(..., err=True)`); Click argument parsing uses exit 2. This is not universal:
  normal `status` treats a missing registry model as an informational exit 0, and `impact`
  warns then returns 0 when the configured target has no transport implementation.
  `status --version-only` deliberately converts its not-found/no-version case to
  `click.ClickException` (exit 1) so scripts cannot mistake absence for version `0`.
  Several configuration, SDK, I/O, scoring, and programming errors are outside these
  formatting catches and propagate with their original exception/traceback (still exit 1).
- `_serve._load_toml_server_host` distinguishes `OSError` (logged as a warning, treated as "no
  override so fall back to default") from `tomllib.TOMLDecodeError` (raised as `haute.errors.ConfigError`
  — a malformed `haute.toml` must not silently resolve to the loopback default, since that could mask
  a typo like a botched `0.0.0.0`).
- `_impact._is_databricks_not_found` / `_is_http_not_found` are the only points where an exception
  is reclassified as "expected" (first-deploy) rather than propagated; both inspect the exception
  shape narrowly (MRO class-name match; `RuntimeError` message containing `"HTTP 404"`) rather than
  catching broadly.
- `_deploy.handle_deploy`'s target-dispatch step distinguishes `ImportError` (missing optional
  extra — tells the user which `uv add haute[...]` to run), `NotImplementedError` (target not yet
  supported), and `DeployError` (expected target/configuration or operational failure). It has no
  catch-all fallback: implementation exceptions propagate with their original traceback.
- `CiSecretsError` is the deploy-secrets steps' one failure: `haute ci secrets` makes it a Click
  error, `POST /api/git/ci-secrets` a 400 with its message, and `push_with_ci_secrets` the
  `problem` of the response's `ci_secrets`, never a failed push. Anything else, a
  `GitError` from the remote inspection included, propagates.
- Browser auto-open is the one explicit UX fallback: `_helpers._open_browser` catches a
  launcher exception or false return, prints the URL for manual opening, and does not stop
  the server. This does not substitute data or hide a server failure.

## Testing

- `tests/test_starter_pipeline_e2e.py` — hermetic `haute init` scaffold test proving the blank
  generated pipeline parses with the configured name and zero nodes, and that its generated test
  suite passes without source data.

Tests live under `tests/` as a flat set of `test_cli_*.py` files (plus `test_cli.py` and
`test_cli_no_shadow.py`), using `click.testing.CliRunner` for end-to-end command invocation and
direct calls into `handle_*` functions for unit-level coverage. `unittest.mock.patch`/`MagicMock`
stub external systems (MLflow, the Databricks SDK, `uvicorn.run`, `subprocess`, `webbrowser`);
no test hits a real deploy backend or remote endpoint. Port-conflict tests bind real loopback
sockets; other SDK/HTTP/uvicorn/subprocess/browser calls are mocked. Filesystem state goes through
pytest's `tmp_path`, and tests that need a specific cwd use `monkeypatch.chdir`.

Key files and what they cover:
- `test_submodel_instances.py` — real `haute run` reports and final preview with
  occurrence warning entries in either result order; only executable nodes appear.
- `test_cli.py` — end-to-end `CliRunner` coverage per command (`--version`, `init`, `run`, `lint`,
  `smoke`, `serve`), plus real-subprocess parity coverage for `python -m haute`, and a command
  piped under a locale that is not UTF-8 writing its marks in UTF-8, an encoding
  `PYTHONIOENCODING` chose kept with a mark it lacks shown as `?`, and a stream a caller put
  in the process's place left alone; uses a shared `project_dir` fixture that builds a real
  minimal pipeline.
- `test_cli_architecture.py` — structural/contract tests: `resolve_model_name` precedence,
  `model_name` optionality across `deploy`/`status`, single shared `resolve_pipeline_file`, that no
  CLI module reimplements ad-hoc pipeline resolution (AST/source scan), that every command has a
  `handle_*` function, and that Click command bodies stay "thin" (AST-inspected line/statement
  budget) versus the `handle_*` function doing the real work.
- `test_cli_helpers.py` — `_open_browser` (including the `webbrowser.open` failure path) and
  `_find_frontend_dir` walking-up-parents behaviour.
- `test_cli_impact.py` / `test_cli_impact_gaps.py` — `impact` end-to-end and edge cases: unsupported
  target, Databricks SDK not installed, "not found" vs. genuine-error classification for the
  first-deploy check, and, with no portfolio sample, the test quotes compared (their requests,
  segmented by their text fields) or, with none, a refusal.
- `test_cli_status.py` — `status` output formatting and that catalog/schema from `haute.toml` are
  threaded through to `get_deploy_status`.
- `test_cli_deploy.py` — deploy CI gating, dry-run, resolution/validation/quote failures,
  exact resolved-object dispatch, dependency/unsupported-target errors, and suffix overrides.
- `test_cli_smoke.py` — Databricks polling loop (including the "not ready" retry path), the client
  built from the serving pair alone (BUG-22) and refused before any request without it, a failed
  update or a missing endpoint failing at once, HTTP smoke
  path, the unsupported-target error, each test quote sent as a request of its own on both
  transports, an expected output checked within tolerance into a Workbench Output's tables
  (and refused for two rows or a non-object answer), a failed request reported by its row
  while the rest are priced, and an unreadable file.
- `test_cli_ci.py` — `ci secrets`: every secret set from `.env` on `gh`'s standard input and never
  printed, `.env` winning over the environment that fills its gaps, an unset or placeholder value,
  a pair Databricks refuses (its token redacted) and a `gh` failure each setting nothing or
  failing, `--no-verify`, `--check` either way, no `gh`, and another provider given the names;
  `ci update` rewriting edited and missing generated files and following a change of provider;
  `init` naming the secrets the first deploy needs.
- `test_ci_secrets.py` — the deploy's secrets on Push, through `POST /api/git/push` and
  `POST /api/git/ci-secrets` against a local bare remote that `remote_secrets` is told is a
  GitHub repository, with `gh` stubbed at its subprocess boundary: a first publication sets
  every missing secret before the default branch's receive session (one log written by the
  `gh` stub and the remote's pre-receive hook) and overwrites none; an unset or placeholder
  value, a refused pair and a failed `gh secret set` each report `missing` with the problem
  while the push still lands; no `gh` and a failed listing report `unknown`; a later Push lists
  and sets nothing; a secret someone sets during the Databricks check is left as it is, on
  Push and on Set from .env; a malformed `haute.toml`, an undecodable `.env`, an unknown
  target and an unpinned container image each report `unknown` and the push still lands; Set
  from .env sets the missing ones, for a remote whose URLs name its repository in another
  case too, and is a 400 for a remote off GitHub, one pushing to another repository, settings
  it cannot use or any failure; a remote off GitHub, one pushing elsewhere and a GitLab
  project run no `gh`.
  `github_repository` reads each URL form and refuses other hosts and shapes; `run_gh`
  redacts a value from `gh`'s message and turns a timeout into `CiSecretsError`.
- `test_cli_ux.py` — cross-cutting UX contracts: shared `ENDPOINT_SUFFIX_HELP` string reused across
  commands, `init --force`, `train` progress-bar flushing, `status --version-only` exit codes,
  Windows pre-commit chmod skip.
- `test_cli_cleanup.py` — `_node_env` failing loudly without Node, single-call browser opening,
  `_find_frontend_dir` raising when absent, that `smoke`'s error style matches project convention,
  single-source-of-truth staging suffix resolution for `impact`.
- `test_cli_fail_loudly.py` — TOML-aware `pyproject.toml` parsing edge cases, structural (not
  substring) `haute` dependency detection, the exact wording of the missing-static-build install
  instruction, CI-provider detection, `serve` port-conflict detection, `impact`'s prod-exists
  failing loudly on non-404 errors.
- `test_cli_init.py` — full scaffold structure, project naming, every `--target`/`--ci` combination,
  the printed summary, `_ensure_haute_dependency` behaviour and its TOML-safety edge cases
  (including a real `mypy .` run under the scaffolded `[tool.mypy]` resolving `rating/main.py`'s
  imports of its own `utility` and `modules`), and
  `--force` re-init (including stale CI-file pruning), and `--workbench`.
- `test_cli_no_shadow.py` — regression guard that `haute.cli` imports as a package and is not
  shadowed by a same-named module.
- `test_cli_serve.py` / `test_cli_train.py` — additional `serve`/`train` scenarios beyond what
  `test_cli.py` covers.
- `test_cli_lint.py` — lint edge cases beyond the happy path already covered in `test_cli.py`.
- `test_docs_accuracy.py` — extracts every `haute <command>` shown in the
  deployment guides/README and compares it with the command names emitted by
  root `haute --help`; a phantom-command fixture must fail the same helper.

Known gaps: no test boots a real Vite subprocess or uvicorn server. Readiness/open ordering and
`finally` cleanup after a mocked uvicorn interruption are covered, but no test invokes the
registered SIGINT/SIGTERM callbacks themselves. No test snapshots the root plus all ten generated
Click help surfaces, so defaults/types can drift; notably `serve` help currently omits its effective
port-8000 default.
