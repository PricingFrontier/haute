# CLI — High-Level Specification

## Purpose

The `haute` CLI is the single command-line entry point for the whole project lifecycle: scaffolding
a new pricing project, iterating on a pipeline locally, training models, validating and deploying a
pipeline as a scoring API, and checking on deployed models after release. It exists so that a data
scientist or actuary can drive the entire "author pipeline → validate → deploy → monitor" loop
without leaving the terminal, and so that CI/CD workflows (GitHub Actions, GitLab CI, Azure DevOps)
have a stable, scriptable surface to call into.

Every command follows the same shape: a thin `@click.command` entry point parses arguments into a
typed dataclass, then hands off to a `handle_*(config)` orchestration function that does the actual
filesystem, process, network, or execution work and can be called directly from tests or other
Python code without going through Click.

## Scope

In scope: the nine `haute` subcommands (`init`, `run`, `lint`, `train`, `serve`, `deploy`, `smoke`,
`status`, `impact`), their argument parsing, user-facing output/error formatting, and the small
amount of orchestration logic that glues each command to the rest of the codebase.

Out of scope, owned elsewhere:
- Pipeline parsing and graph execution — [execution-engine](../execution-engine/high-level.md),
  [pipeline-config](../pipeline-config/high-level.md).
- Deploy bundling, pruning, validation, scoring, and MLflow registration internals —
  [deploy](../deploy/high-level.md).
- The FastAPI backend that `haute serve` launches — [server-api](../server-api/high-level.md).
- Model training internals (`TrainingJob`, algorithms, metrics) — [modelling](../modelling/high-level.md).
- Sandboxed execution of user training scripts — [sandbox-security](../sandbox-security/high-level.md).

## Behaviour

- `haute init [--target ...] [--ci ...] [--force]` scaffolds a new project in the current directory:
  `haute.toml`, a blank `rating/` pipeline package, `.env.example`, test-quote fixtures, CI/CD
  workflow files for the chosen provider, a git pre-commit hook, and `.gitignore` guard entries.
  Refuses to run if `haute.toml` already exists unless `--force` is given. A root `main.py` is
  always deleted: `uv init` recreates that placeholder every time it runs, and a Haute project's
  real entry point is `rating/main.py`, so the root file is a tooling artifact — never user
  content — and removing it keeps the entry point unambiguous. No `prompts/` directory or
  starter nodes/sidecars are generated.
- `haute run [pipeline_file]` executes a pipeline end-to-end through the same
  `parse_pipeline_file` → `execute_graph` path the GUI uses, printing a per-node row/column summary
  and a preview of the final node's output.
- `haute lint [pipeline_file]` strictly parses a pipeline and exits non-zero on syntax,
  configuration, topology, or other parse failures. For a valid canonical graph it reports
  structural problems (edges pointing at missing nodes and orphan nodes) without executing
  anything.
- `haute train <training_script>` runs a training script that must define a module-level `job`
  object with a callable `run(progress=...)` method (normally a `TrainingJob`), streaming a
  live progress bar and printing the returned result's model path, feature counts, and metrics.
- `haute serve [--host] [--port] [--no-browser]` starts the Haute UI: dev mode (Vite + FastAPI with
  autoreload) when a `frontend/` checkout with `node_modules` is discoverable, otherwise production
  mode serving a pre-built static bundle. Binds to `localhost` by default so the browser session
  works with a bare `haute serve`.
- `haute deploy [pipeline_file] [--model-name] [--dry-run] [--endpoint-suffix]` validates a pipeline,
  scores its test quotes, and deploys it to the configured target. Non-dry-run deploys are blocked
  outside a recognised CI environment.
- `haute smoke [--endpoint-suffix]` sends every test-quote JSON file in `tests/quotes/` to a live
  serving endpoint and checks that each request completes. The current backends do not
  consistently require a non-empty prediction list, and HTTP health accepts any decodable 2xx JSON.
  An endpoint suffix is a Databricks naming override; HTTP targets reject it and direct operators
  to configure the complete staging endpoint URL instead.
- `haute status [model_name] [--version-only]` looks up a model's latest version/stage in the MLflow
  Model Registry.
- `haute impact [--sample] [--batch-size] [--endpoint-suffix]` scores a configured safety dataset
  through both the staging and production endpoints and reports the pricing differences, writing a
  markdown report and, on GitHub Actions, appending to the job step summary.

Invariants that hold across every command:
- Where `resolve_model_name` is used (`status`), precedence is **CLI value >
  `HAUTE_MODEL_NAME` > `[deploy].model_name` in `haute.toml` > loud error**. `deploy` loads the TOML config then
  applies its `--model-name` override; without TOML it derives the default from the resolved
  pipeline stem when the option is absent.
- `run`, `lint`, and `deploy` delegate CLI/default pipeline discovery to
  `haute._project.resolve_pipeline_file`; a TOML-configured deploy pipeline is passed through
  that same resolver before `DeployConfig` and `resolve_config` consume it.
- Command handlers use exit code 1 for their handled operational failures; Click uses exit
  code 2 for argument/choice/range parsing errors. Two informational outcomes intentionally
  succeed: ordinary `status` reports a missing registered model and exits 0, and `impact`
  warns and exits 0 for a target whose transport is not implemented. `status --version-only`
  makes the same missing-model case an exit-1 `ClickException` for scripts.

## Design rationale

- **`handle_*(config)` split.** Separating Click parsing from the actual work lets tests call
  `handle_deploy`, `handle_serve`, etc. directly with a constructed dataclass, without invoking a
  subprocess or `CliRunner`, and lets non-CLI callers (future alternative frontends, programmatic
  scripts) reuse the same logic. `tests/test_cli_architecture.py` (`TestHandleFunctionsExist`,
  `TestClickBodiesAreThin`) enforces both the existence of the `handle_*` functions and that the
  Click bodies stay thin via AST inspection.
- **CI-gated deploys.** `handle_deploy` refuses non-dry-run deploys unless a recognised CI provider
  env var (`GITHUB_ACTIONS`, `GITLAB_CI`, `CIRCLECI`, `TF_BUILD`, `BUILDKITE`, generic `CI`) is set
  truthily. This is a deliberate guardrail: production model changes must go through reviewed CI/CD,
  not an engineer's laptop.
- **`serve` is loopback-only.** `haute serve` binds `localhost` by default and accepts only
  `localhost`, IPv4 loopback addresses, or IPv6 `::1` from the CLI or `haute.toml`. Wildcard,
  LAN/public IP, and custom-hostname values fail before port probing or process launch; this
  command is a local editor surface, not an application-hosting surface.
- **The development UI has one deterministic address.** Vite binds
  `127.0.0.1:5173` with strict-port mode. `serve` refuses to start if either the API port or
  Vite port is occupied, waits for both listeners before opening the browser, and never follows
  Vite's automatic port rollover to a different, unreported origin.
- **Command-scoped failure rules.** Missing Node/npm and a missing production frontend build
  fail rather than guessing; malformed TOML fails. `smoke`/`impact` require `haute.toml`, while
  deploy and explicit-model status have non-TOML paths. `serve` deliberately treats a missing
  file as no override and an unreadable file as a warning plus the loopback default; it still
  rejects malformed TOML. No hardcoded Windows Node/npm path is guessed.
- **`impact`/`smoke` share a transport abstraction.** Both commands need to dispatch to either
  Databricks Model Serving or a plain HTTP endpoint depending on `DeployConfig.target`. That
  dispatch logic lives once in `haute.cli._helpers.resolve_transport` / `TransportInfo` rather than
  being duplicated.
- **`impact`'s first-deploy handling is narrowly scoped.** Only an explicit "endpoint not found"
  signal (Databricks `NotFound`/`ResourceDoesNotExist`, or HTTP 404) is treated as "no production
  deployment yet" and produces a first-deploy-shaped report; every other exception (timeouts, 5xx,
  connection errors) propagates as a real failure. A broader catch would risk silently misreporting
  transient outages as "nothing to compare against yet".

## Interactions

- Depends on [pipeline-config](../pipeline-config/high-level.md) and
  [execution-engine](../execution-engine/high-level.md) for `run`/`lint` (`parse_pipeline_file`,
  `execute_graph`).
- Depends on [deploy](../deploy/high-level.md) for `deploy`/`smoke`/`impact`/`status`
  (`DeployConfig`, `resolve_config`, `validate_deploy`, `score_test_quotes`, `deploy_resolved`,
  `score_endpoint_batched`, `score_http_endpoint_batched`, `get_deploy_status`).
- Depends on [server-api](../server-api/high-level.md) for `serve` (`haute.server:app`,
  `STATIC_DIR`, `static_build_ready`).
- Depends on [modelling](../modelling/high-level.md) for `train` (`TrainingJob.run`).
- Depends on [sandbox-security](../sandbox-security/high-level.md) for `train`'s pre-execution
  safety check (`validate_user_code`) and for `serve`'s local session token
  (`ensure_local_session_token_env`, `TRUSTED_HOSTS_ENV`).
- `haute init` depends on `haute._scaffold` (template generation for `haute.toml`, CI workflows,
  starter pipeline) and `haute._gitignore_guard` — both outside this component's scope but owned by
  the same project-bootstrap concern as [pipeline-config](../pipeline-config/high-level.md).
- CI/CD workflows generated by `init` call back into this component (`haute deploy`, `haute smoke`,
  `haute impact`) as their own execution surface — the CLI is both the tool CI drives and the tool
  that generates the CI config that drives it.

## Failure model

Expected operational failures explicitly caught by a handler normally raise `SystemExit(1)`
after explanatory stderr; Click's own invocation errors exit 2, and `status --version-only`
uses `ClickException` (exit 1). Some configuration, SDK, filesystem, scoring, and programming
errors deliberately propagate instead, producing exit 1 with the original exception/traceback.
A missing `haute.toml` where the command requires one (`smoke`/`impact`), an unresolvable
required model name, a missing frontend build, a missing training `job` variable, a failed
validation, or a deploy exception is terminal. Ordinary
`status`-not-found and unsupported-target `impact` are the two deliberate informational success
paths described above, not deploy or scoring successes.

Exceptions are handled with narrow, explicit catches rather than blanket `except Exception` used as
a fallback mechanism: transport-classification helpers (`_is_databricks_not_found`,
`_is_http_not_found` in `src/haute/cli/_impact.py`) exist specifically so that only the exact "not found" shape is
swallowed and reclassified — everything else re-raises. Where `except Exception` does appear (e.g.
around `resolve_config` or script execution in `train`), it is used to convert an
internal exception into a formatted CLI error message and `SystemExit(1)`, not to continue past the
failure.

The Databricks smoke readiness loop retries only the SDK's explicit not-found response while an
endpoint is being created. Authentication, permission, transport, and other SDK failures stop
immediately with the original exception chained. Training execution and result rendering are
separate failure boundaries: once `job.run()` has returned successfully, a formatting problem is
reported as a result-reporting failure and never as “Training failed”.

`handle_deploy` renders `DeployError` as an expected target/configuration failure and keeps
the explicit missing-dependency and not-implemented messages. It does not catch the
backend with `except Exception`: an unexpected implementation exception retains its
original type and traceback, while target adapters are responsible for raising
`DeployError` for expected operational failures such as missing credentials, unavailable
Docker, or rejected build/push operations.
