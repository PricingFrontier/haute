# CLI — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/cli/__init__.py` | Builds the `click.Group` (`cli`), registers all nine subcommands, exposes `--version`. |
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

Every command follows the same pattern: a frozen-in-spirit (not actually `frozen=True`) `@dataclass`
holding parsed CLI inputs, consumed by a `handle_*` function.

- `InitConfig(target, ci, force=False)` — `_init_cmd.py`.
- `RunConfig(pipeline_file: Path)` — `_run.py`.
- `LintConfig(pipeline_file: Path)` — `_lint.py`.
- `TrainConfig(training_script: Path)` — `_train.py`.
- `ServeConfig(port, no_browser, host="127.0.0.1")` — `_serve.py`. The `host` default is on the
  dataclass itself (not just the Click option) so every non-CLI caller inherits the loopback-safe
  default too.
- `DeployCliConfig(pipeline_file, model_name, endpoint_suffix, dry_run)` — `_deploy.py`. Distinct
  from `haute.deploy._config.DeployConfig`, which is the fully-resolved deploy configuration built
  from this plus `haute.toml`.
- `SmokeConfig(endpoint_suffix: str | None)` — `_smoke.py`.
- `StatusConfig(model_name, version_only)` — `_status.py`.
- `ImpactConfig(endpoint_suffix, sample, batch_size)` — `_impact.py`.

Other notable types:
- `TransportInfo` (`_helpers.py`) — `__slots__`-based result of `resolve_transport(config)`. Fields:
  `kind` (`"databricks" | "http" | "unsupported"`), `staging_url`, `prod_url`. `smoke` and `impact`
  both switch on `.kind` to pick a backend.
- `_Closable` (`_serve.py`) — a `typing.Protocol` describing the socket-like object the TCP
  readiness probe needs (`close()` only), used to type `_wait_for_tcp_ready`'s injectable `connect`
  callable for testability.

## Control flow

**`init`**: `handle_init` checks for an existing `haute.toml` (abort unless `--force`), resolves the
project name from `pyproject.toml` (creating/patching it via `_ensure_haute_dependency`, which does
a structural TOML edit rather than string templating so existing content survives), creates the
`rating/` package tree and config placeholders, writes `haute.toml` via `haute._scaffold.haute_toml`,
writes `.env.example`, writes starter tests, writes CI workflow files for the chosen provider
(pruning a *different* provider's stale files first on `--force`), installs a pre-commit hook into
`.githooks/` and — if inside a git repo — `.git/hooks/`, and appends `.gitignore` guard entries via
`haute._gitignore_guard.ensure_gitignore_guards`.

**`run`**: resolves the pipeline file via `haute._project.resolve_pipeline_file`, calls
`parse_pipeline_file` then `execute_graph`, prints one line per node (row/column count or error),
exits 1 if any node failed, then previews the last node's output as a `polars.DataFrame`.

**`lint`**: resolves the pipeline file, parses it, and runs three structural checks in order: edges
referencing missing node ids, nodes carrying a `parseError` in their config, and (for multi-node
graphs) orphan nodes with no edges at all. All findings are collected before reporting, so a single
run surfaces every issue rather than stopping at the first.

**`train`**: validates the script exists, runs it through
`haute._sandbox.validate_user_code(..., allow_imports=True)` before execution, loads it as a module
via `importlib.util`, looks up a module-level `job` attribute, and calls `job.run(progress=_progress)`.
`_progress` renders a `\r`-carriage-return progress bar and explicitly flushes stdout after every
write (documented as load-bearing — `click.echo(nl=False)` alone can leave the line buffered).

**`serve`**: `handle_serve` runs, in order: `_warn_if_non_loopback` (logs before any other work so
the warning still fires even if a later step aborts), `_configure_trusted_hosts` (sets/clears the
`TRUSTED_HOSTS_ENV` env var consumed by the FastAPI `TrustedHostMiddleware`), `_abort_if_port_in_use`
(pre-flight socket bind/close probe — `SO_EXCLUSIVEADDRUSE` on Windows to avoid a false-negative from
`SO_REUSEADDR`), then `_detect_dev_frontend_dir` to choose dev vs. prod mode.
  - **Dev mode** (`_run_dev_mode`): spawns `npm run dev` as a subprocess (`_start_vite_subprocess`,
    with `SIGINT`/`SIGTERM` handlers wired to terminate the child), schedules a background thread
    that polls the backend TCP port and opens the browser once it's accepting connections
    (`_open_browser_after_backend_ready` → `_wait_for_tcp_ready`), then runs `uvicorn.run(...,
    reload=True, reload_dirs=[haute package dir])`. The Vite subprocess is terminated in a `finally`
    block on every uvicorn exit path.
  - **Prod mode** (`_run_prod_mode`): checks `static_build_ready(STATIC_DIR)`, fails loudly with a
    build-hint message (`_missing_static_message`, which distinguishes a source checkout — "run npm
    build" — from an installed wheel — "reinstall haute") if not ready, schedules a delayed browser
    open, and runs plain `uvicorn.run(...)` with no autoreload.
  - Host resolution precedence (in the Click wrapper, before `ServeConfig` is built): `--host` flag
    → `[server] host` in `haute.toml` (`_load_toml_server_host`) → `"127.0.0.1"`.

**`deploy`**: `handle_deploy` loads `DeployConfig` from `haute.toml` if present, else builds one from
CLI args via `resolve_pipeline_file` + `DeployConfig.from_cli_args`. Blocks non-dry-run deploys
outside CI (`_detect_ci_env`). Applies CLI overrides (`pipeline_file`, `model_name`,
`endpoint_suffix`) on top of the loaded config via `.override(**overrides)`. Then: `resolve_config`
(parse/prune/collect artifacts/infer schemas) → `validate_deploy` → `score_test_quotes` → (return
early if `--dry-run`) → `deploy_resolved`. Each stage prints a `✓`/`✗` progress line; any stage
failure is caught, formatted, and turns into `SystemExit(1)`.

**`smoke`**: requires `haute.toml`; loads `DeployConfig`, applies an optional endpoint-suffix
override, requires a non-empty `tests/quotes/*.json` set, then dispatches on
`resolve_transport(deploy_config).kind`:
  - `"databricks"` (`_smoke_databricks`): polls `ws.serving_endpoints.get(name)` every 30s up to 30
    minutes waiting for `state.ready == "READY"` and `config_update in ("", "NOT_UPDATING")`, then
    queries the endpoint once per test-quote file.
  - `"http"` (`_smoke_http`): hits `<url>/health` once, then POSTs each test-quote file via
    `score_http_endpoint_batched`.
  Both backends run every file even after an individual failure and report a combined pass/fail at
  the end.

**`status`**: resolves the model name (`resolve_model_name`), loads catalog/schema from
`haute.toml` if present else `DatabricksConfig()` defaults, calls
`haute.deploy._mlflow.get_deploy_status`. `--version-only` mode prints only the version number (for
scripting) and raises `click.ClickException` — rather than printing a misleading `0` — when no
version is registered.

**`impact`**: requires `haute.toml` and `[safety].impact_dataset`; resolves the staging suffix (CLI
flag wins, else `deploy_config.ci.staging_endpoint_suffix`, else loud error); reads and optionally
samples (`df.sample(n=..., seed=42)`) the impact dataset parquet; dispatches to
`_impact_databricks`/`_impact_http` based on `resolve_transport(...).kind`; each backend probes
whether the production endpoint exists (only a "not found" signal flips `prod_exists = False`) and
scores staging (always) and production (if it exists); builds an `ImpactReport` — a first-deploy
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
- **Wildcard binds probed via their loopback counterpart.** `_serve._backend_probe_host` maps
  `0.0.0.0` → `127.0.0.1` and `::` → `::1` for the post-launch TCP readiness check, since a wildcard
  bind address isn't itself a connectable target.
- **`_wait_for_tcp_ready` only swallows `ConnectionRefusedError`/`TimeoutError`.** Any other socket
  exception (e.g. an unresolvable hostname) propagates rather than being retried into a misleading
  timeout.
- **`impact`'s sampling only triggers when it would shrink the dataset** (`config.sample > 0 and
  total_rows > config.sample`) — a `--sample` larger than the dataset, or `--sample 0`, scores every
  row.
- **`status --version-only`** distinguishes "no version" from a genuine version `0` by raising
  instead of printing — printing `0` unconditionally would be indistinguishable from a real version
  number to a scripted caller checking stdout.
- **`subprocess` imported but unused at runtime in `_helpers.py`.** The module-level `import
  subprocess  # noqa: F401` exists solely so tests can patch `subprocess.call`/`subprocess.Popen` and
  assert they are never invoked from this module (`_open_browser` uses `webbrowser` exclusively) —
  documented in a comment referencing "codebase-review #79".

## Error handling

- Every `handle_*` function raises `SystemExit(1)` (via `click.echo(..., err=True)` followed by
  `raise SystemExit(1)`) on user-facing failure; `status --version-only` uses
  `click.ClickException` instead so Click's own formatting applies.
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

## Testing

Tests live under `tests/` as a flat set of `test_cli_*.py` files (plus `test_cli.py` and
`test_cli_no_shadow.py`), using `click.testing.CliRunner` for end-to-end command invocation and
direct calls into `handle_*` functions for unit-level coverage. `unittest.mock.patch`/`MagicMock`
stub every external system (MLflow, the Databricks SDK, `uvicorn.run`, `subprocess`, `webbrowser`);
no test hits a real deploy backend, endpoint, or network resource. Filesystem state goes through
pytest's `tmp_path`, and tests that need a specific cwd use `monkeypatch.chdir`.

Key files and what they cover:
- `test_cli.py` — end-to-end `CliRunner` coverage per command (`--version`, `init`, `run`, `lint`,
  `smoke`, `serve`), using a shared `project_dir` fixture that builds a real minimal pipeline.
- `test_cli_architecture.py` — structural/contract tests: `resolve_model_name` precedence,
  `model_name` optionality across `deploy`/`status`, single shared `resolve_pipeline_file`, that no
  CLI module reimplements ad-hoc pipeline resolution (AST/source scan), that every command has a
  `handle_*` function, and that Click command bodies stay "thin" (AST-inspected line/statement
  budget) versus the `handle_*` function doing the real work. Explicitly documents itself as a
  TDD red-phase suite ("all tests are expected to fail before the dev patch lands").
- `test_cli_helpers.py` — `_open_browser` (including the `webbrowser.open` failure path) and
  `_find_frontend_dir` walking-up-parents behaviour.
- `test_cli_impact.py` / `test_cli_impact_gaps.py` — `impact` end-to-end and edge cases: unsupported
  target, Databricks SDK not installed, "not found" vs. genuine-error classification for the
  first-deploy check.
- `test_cli_status.py` — `status` output formatting and that catalog/schema from `haute.toml` are
  threaded through to `get_deploy_status`.
- `test_cli_deploy.py` — `deploy` end-to-end (`TestDeploy`).
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

Known gaps: no test drives `haute serve` far enough to observe a real Vite subprocess or a live
uvicorn server (both are patched out), so the actual dev-mode browser-open race and Vite process
cleanup on signal receipt are exercised at the unit level (`_wait_for_tcp_ready`,
`_start_vite_subprocess` wiring) rather than through a genuine end-to-end server boot.
