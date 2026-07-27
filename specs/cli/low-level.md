# CLI — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/cli/__init__.py` | Builds the Click command group (`cli`), registers all nine subcommands, exposes `--version`. |
| `src/haute/cli/_helpers.py` | Cross-command utilities: `resolve_model_name`, `_open_browser`, `_node_env`, `_npm`, `_find_frontend_dir`, the `TransportInfo`/`resolve_transport` transport-dispatch helper, and the shared `ENDPOINT_SUFFIX_HELP` string. |
| `src/haute/cli/_init_cmd.py` | `haute init` — project scaffolding: `InitConfig`, `handle_init`, TOML-aware `pyproject.toml` dependency injection, CI-provider file generation/pruning. |
| `src/haute/cli/_run.py` | `haute run` — `RunConfig`, `handle_run`, parses + executes a pipeline and prints per-node results. |
| `src/haute/cli/_lint.py` | `haute lint` — `LintConfig`, `handle_lint`, structural validation without execution. |
| `src/haute/cli/_train.py` | `haute train` — `TrainConfig`, `handle_train`, loads a training script as a module, runs its `job`, prints a live progress bar. |
| `src/haute/cli/_serve.py` | `haute serve` — `ServeConfig`, `handle_serve`, host-safety checks, dev/prod mode dispatch, uvicorn launch. |
| `src/haute/cli/_deploy.py` | `haute deploy` — `DeployCliConfig`, `handle_deploy`, CI-gate check, resolve → validate → score test quotes → deploy pipeline. |
| `src/haute/cli/_smoke.py` | `haute smoke` — `SmokeConfig`, `handle_smoke`, sends test quotes to a live endpoint (Databricks or HTTP). |
| `src/haute/cli/_status.py` | `haute status` — `StatusConfig`, `handle_status`, MLflow Model Registry lookup. |
| `src/haute/cli/_impact.py` | `haute impact` — `ImpactConfig`, `handle_impact`, staging-vs-production comparison report; `_impact_databricks`/`_impact_http` transport backends. |

## Key types and data structures

Every command uses a plain mutable `@dataclass` as a configuration value bag consumed by a
`handle_*` function.

- `InitConfig(target, ci, force=False)` — `_init_cmd.py`.
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

Other notable types:
- `TransportInfo` (`src/haute/cli/_helpers.py`) — `__slots__`-based result of `resolve_transport(config)`. Fields:
  `kind` (`"databricks" | "http" | "unsupported"`), `staging_url`, `prod_url`. `smoke` and `impact`
  both switch on `.kind` to pick a backend.
- `_Closable` (`_serve.py`) — a `typing.Protocol` describing the socket-like object the TCP
  readiness probe needs (`close()` only), used to type `_wait_for_tcp_ready`'s injectable `connect`
  callable for testability.

### Command and option contract

Every subcommand also exposes Click's eager `--help` option, which prints usage and exits 0
without validating required positional arguments.

| Command | Arguments and options | Exit and failure contract |
|---|---|---|
| `haute` | `--version`; `--help`. | Both print and exit 0. Unknown commands/options are Click usage errors (exit 2). |
| `haute init` | `--target` choice: `databricks` (default), `container`, `azure-container-apps`, `aws-ecs`, `gcp-run`, `sagemaker`, `azure-ml`; `--ci` choice: `github` (default), `gitlab`, `azure-devops`, `none`; `-f`/`--force`. `sagemaker` and `azure-ml` are accepted scaffold choices even though deploy deliberately rejects those planned targets as unimplemented. | Success 0. Existing `haute.toml` without `--force` exits 1. Invalid choices exit 2 before the handler. File/TOML/scaffold write errors are not converted into a fallback. |
| `haute run [PIPELINE_FILE]` | Optional path; absent input uses `resolve_pipeline_file` project/discovery rules. | Missing/ambiguous file, parse failure, empty graph, executor failure, or any node result with non-`ok` status exits 1; success exits 0 after the optional final preview. |
| `haute lint [PIPELINE_FILE]` | Optional path resolved exactly as `run`. | Missing/ambiguous file, parse failure, empty graph, or any collected structural issue exits 1; a clean graph exits 0. |
| `haute train TRAINING_SCRIPT` | Required positional path. | Omission is a Click exit-2 usage error. Missing/unsafe/unloadable script, missing `job`, script exception, or `job.run` failure exits 1; successful training exits 0. |
| `haute serve` | `--host TEXT` (CLI → `[server].host` → `localhost`); `--port INTEGER` (effective default `8000`, not shown by current help); `--no-browser`. Port range is not Click-validated: negative/out-of-range integers reach socket setup and may raise rawly. | Port conflict or missing production static build exits 1; malformed `haute.toml` propagates `ConfigError`. A browser-launch failure prints the manual URL but leaves the running server path intact. |
| `haute deploy [PIPELINE_FILE]` | Optional path; `--model-name TEXT`; `--dry-run`; `--endpoint-suffix TEXT`. | Non-dry-run outside recognised CI exits 1. Resolution, validation, either validation's quote pass or the separately printed quote pass, missing backend dependency, unimplemented target, and backend failure all exit 1. Dry-run success exits 0 before backend dispatch. |
| `haute smoke` | `--endpoint-suffix TEXT`. | Missing config/quotes/endpoint, missing Databricks SDK, an installed SDK too old to expose the required `NotFound` error type, unsupported target, readiness timeout, health-request failure, or any scoring request failure exits 1. Missing and outdated SDKs produce distinct install/upgrade guidance. A successful request can currently pass with an empty prediction payload. |
| `haute status [MODEL_NAME]` | Optional model name; `--version-only`. | Missing resolvable name or MLflow dependency exits 1. Normal mode prints “not found” and exits 0; `--version-only` prints only a version on success and raises `ClickException` (exit 1, stderr only) when no version exists. |
| `haute impact` | `--sample INTEGER` (default `10000`; every value `<=0` currently means all, although help documents `0`); `--batch-size INTEGER` (default `500`, minimum `1`); `--endpoint-suffix TEXT`. | Invalid batch size exits 2. Missing config/suffix/dataset or missing Databricks SDK exits 1. Endpoint/scoring/arithmetic/write failures propagate. Unsupported transport returns successfully without a report only after TOML, suffix, and dataset/parquet loading have succeeded; otherwise success writes `impact_report.md` and exits 0. |

## Control flow

**`init`**: `handle_init` checks for an existing `haute.toml` (abort unless `--force`), resolves the
project name from `pyproject.toml` (creating/patching it via `_ensure_haute_dependency`, which does
a structural TOML edit rather than string templating so existing content survives), creates the
`rating/` package tree and config placeholders, writes `haute.toml` via `haute._scaffold.haute_toml`,
writes `.env.example`, writes starter tests, writes CI workflow files for the chosen provider
(pruning a *different* provider's stale files first on `--force`), installs a pre-commit hook into
`.githooks/` and — if inside a git repo — `.git/hooks/`, and appends `.gitignore` guard entries via
`haute._gitignore_guard.ensure_gitignore_guards`. A pre-existing root `main.py` is preserved
verbatim and reported; `--force` applies to Haute-owned scaffold files, not that root entry point.

**`run`**: resolves the pipeline file via `haute._project.resolve_pipeline_file`, calls
`parse_pipeline_file` then `execute_graph`, prints one line per node (row/column count or error),
exits 1 if any node failed, then previews the last node's output as a `polars.DataFrame`.

**`lint`**: resolves the pipeline file, parses it, and runs three structural checks in order: edges
referencing missing node ids, nodes carrying a `parseError` in their config, and (for multi-node
graphs) orphan nodes with no edges at all. All findings are collected before reporting, so a single
run surfaces every issue rather than stopping at the first.

**`train`**: validates the script exists, runs it through
`haute._sandbox.validate_user_code(..., allow_imports=True)` before execution, loads it as a module
via `importlib.util`, looks up a module-level `job` attribute, and calls
`job.run(progress=_progress)` without an `isinstance(TrainingJob)` check. The documented/generated
shape is a `TrainingJob`, but at runtime any object implementing that call and returning the fields
the formatter reads is accepted. Execution and rendering have separate exception boundaries:
after `job.run()` returns, malformed result fields produce a “training succeeded, reporting
failed” error and never a “Training failed” message.
`_progress` renders a `\r`-carriage-return progress bar and explicitly flushes stdout after every
write (documented as load-bearing — `click.echo(nl=False)` alone can leave the line buffered).

**`serve`**: `handle_serve` runs, in order: `_require_loopback_host` (rejects every non-loopback
value before any startup side effect), `_configure_trusted_hosts` (clears any stale
`TRUSTED_HOSTS_ENV` remote-bind policy), `_abort_if_port_in_use`
(pre-flight socket bind/close probe — `SO_EXCLUSIVEADDRUSE` on Windows to avoid a false-negative from
`SO_REUSEADDR`), then `_detect_dev_frontend_dir` to choose dev vs. prod mode. Dev mode also
pre-flights the fixed Vite listener at `127.0.0.1:5173`.
  - **Dev mode** (`_run_dev_mode`): spawns `npm run dev` as a subprocess (`_start_vite_subprocess`,
    with `SIGINT`/`SIGTERM` handlers wired to terminate the child). It creates the backend's
    process-local session token but removes `VITE_HAUTE_SESSION_TOKEN` from the child environment,
    so the frontend cannot read the credential. The server-only `HAUTE_BACKEND_URL` carries the
    selected loopback host/port into Vite's same-origin `/api` and `/ws` proxy (including bracketed
    IPv6) without becoming client code. It schedules a background thread
    that polls both the backend TCP port and Vite's fixed TCP port and opens the browser only
    once both are accepting connections
    (`_open_browser_after_backend_ready` → `_wait_for_tcp_ready`), then runs `uvicorn.run(...,
    reload=True, reload_dirs=[haute package dir])`. The Vite subprocess is terminated in a `finally`
    block on every uvicorn exit path.
  - **Prod mode** (`_run_prod_mode`): checks `static_build_ready(STATIC_DIR)`, fails loudly with a
    build-hint message (`_missing_static_message`, which distinguishes a source checkout — "run npm
    build" — from an installed wheel — "reinstall haute") if not ready, schedules a delayed browser
    open, and runs plain `uvicorn.run(...)` with no autoreload.
  - Host resolution precedence (in the Click wrapper, before `ServeConfig` is built): `--host` flag
    → `[server] host` in `haute.toml` (`_load_toml_server_host`) → `"localhost"`. Therefore plain
    `haute serve` binds and opens `http://localhost:8000` without requiring project configuration.

**`deploy`**: `handle_deploy` loads `DeployConfig` from `haute.toml` if present, else builds one from
CLI args via `resolve_pipeline_file` + `DeployConfig.from_cli_args`. Blocks non-dry-run deploys
outside CI (`_detect_ci_env`). A TOML-configured pipeline path is also normalised through
`resolve_pipeline_file`; all command paths therefore share one missing/ambiguous-path contract.
Applies CLI overrides (`pipeline_file`, `model_name`,
`endpoint_suffix`) on top of the loaded config via `.override(**overrides)`. Then: `resolve_config`
(parse/prune/collect artifacts/infer schemas) → `validate_deploy` (which already scores configured
quotes as part of its aggregate gate) → `score_test_quotes` again for per-file timing/status output
→ (return early if `--dry-run`) → `deploy_resolved`. Each stage prints a `✓`/`✗` progress line; any stage
failure is caught, formatted, and turns into `SystemExit(1)`.

**`smoke`**: requires `haute.toml`; loads `DeployConfig`, applies an optional endpoint-suffix
override, requires a non-empty `tests/quotes/*.json` set, then dispatches on
`resolve_transport(deploy_config).kind`:
  - `"databricks"` (`_smoke_databricks`): polls `ws.serving_endpoints.get(name)` every 30s up to 30
    minutes waiting for `state.ready == "READY"` and `config_update in ("", "NOT_UPDATING")`, then
    queries the endpoint once per test-quote file. Only the SDK's explicit not-found exception is
    retryable; every other endpoint lookup failure stops immediately with its cause.
  - `"http"` (`_smoke_http`): hits `<url>/health` once, then POSTs each test-quote file via
    `score_http_endpoint_batched`. An explicit suffix is rejected because an HTTP deployment must
    supply its complete `[ci.staging].endpoint_url`.
  Both backends run every file even after an individual failure and report a combined pass/fail at
  the end.

**`status`**: resolves the model name with CLI > `HAUTE_MODEL_NAME` > TOML precedence
(`resolve_model_name`), loads catalog/schema from
`haute.toml` if present else `DatabricksConfig()` defaults, calls
`haute.deploy._mlflow.get_deploy_status`. `--version-only` mode prints only the version number (for
scripting) and raises `click.ClickException` — rather than printing a misleading `0` — when no
version is registered.

**`impact`**: requires `haute.toml` and `[safety].impact_dataset`; resolves the staging suffix (CLI
flag wins, else `deploy_config.ci.staging_endpoint_suffix`, else loud error); reads and optionally
samples (`df.sample(n=..., seed=42)`) the impact dataset parquet; dispatches to
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
  supported), and a final `except Exception` fallback that formats and exits 1 for anything else.
- `_train.handle_train` treats `UnsafeCodeError` from `validate_user_code` as a distinct, clearly
  labelled failure ("failed safety validation") from a plain execution/import error.
- Browser auto-open is the one explicit UX fallback: `_helpers._open_browser` catches a
  launcher exception or false return, prints the URL for manual opening, and does not stop
  the server. This does not substitute data or hide a server failure.

## Testing

- `tests/test_starter_pipeline_e2e.py` — hermetic `haute init` scaffold parse-and-execute test proving a fresh starter pipeline produces output through parser and executor.

Tests live under `tests/` as a flat set of `test_cli_*.py` files (plus `test_cli.py` and
`test_cli_no_shadow.py`), using `click.testing.CliRunner` for end-to-end command invocation and
direct calls into `handle_*` functions for unit-level coverage. `unittest.mock.patch`/`MagicMock`
stub external systems (MLflow, the Databricks SDK, `uvicorn.run`, `subprocess`, `webbrowser`);
no test hits a real deploy backend or remote endpoint. Port-conflict tests bind real loopback
sockets; other SDK/HTTP/uvicorn/subprocess/browser calls are mocked. Filesystem state goes through
pytest's `tmp_path`, and tests that need a specific cwd use `monkeypatch.chdir`.

Key files and what they cover:
- `test_cli.py` — end-to-end `CliRunner` coverage per command (`--version`, `init`, `run`, `lint`,
  `smoke`, `serve`), using a shared `project_dir` fixture that builds a real minimal pipeline.
- `test_cli_architecture.py` — structural/contract tests: `resolve_model_name` precedence,
  `model_name` optionality across `deploy`/`status`, single shared `resolve_pipeline_file`, that no
  CLI module reimplements ad-hoc pipeline resolution (AST/source scan), that every command has a
  `handle_*` function, and that Click command bodies stay "thin" (AST-inspected line/statement
  budget) versus the `handle_*` function doing the real work.
- `test_cli_helpers.py` — `_open_browser` (including the `webbrowser.open` failure path) and
  `_find_frontend_dir` walking-up-parents behaviour.
- `test_cli_impact.py` / `test_cli_impact_gaps.py` — `impact` end-to-end and edge cases: unsupported
  target, Databricks SDK not installed, "not found" vs. genuine-error classification for the
  first-deploy check.
- `test_cli_status.py` — `status` output formatting and that catalog/schema from `haute.toml` are
  threaded through to `get_deploy_status`.
- `test_cli_deploy.py` — deploy CI gating, dry-run, resolution/validation/quote failures,
  exact resolved-object dispatch, dependency/unsupported-target errors, and suffix overrides.
- `test_cli_smoke.py` — Databricks polling loop (including the "not ready" retry path), HTTP smoke
  path, and the unsupported-target error.
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
  the printed summary, `_ensure_haute_dependency` behaviour and its TOML-safety edge cases, and
  `--force` re-init (including stale CI-file pruning).
- `test_cli_no_shadow.py` — regression guard that `haute.cli` imports as a package and is not
  shadowed by a same-named module.
- `test_cli_serve.py` / `test_cli_train.py` — additional `serve`/`train` scenarios beyond what
  `test_cli.py` covers.
- `test_cli_lint.py` — lint edge cases beyond the happy path already covered in `test_cli.py`.

Known gaps: no test boots a real Vite subprocess or uvicorn server. Readiness/open ordering and
`finally` cleanup after a mocked uvicorn interruption are covered, but no test invokes the
registered SIGINT/SIGTERM callbacks themselves. No test snapshots the root plus all nine generated
Click help surfaces, so defaults/types can drift; notably `serve` help currently omits its effective
port-8000 default.
