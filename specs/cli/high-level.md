# CLI — High-Level Specification

## Purpose

The Haute CLI is the single command group for the whole project lifecycle, exposed equivalently as
the installed `haute` console command and as `python -m haute`: scaffolding a new pricing project,
iterating on a pipeline locally, training models, validating and deploying a pipeline as a scoring
API, and checking on deployed models after release. It exists so that a data
scientist or actuary can drive the entire "author pipeline → validate → deploy → monitor" loop
without leaving the terminal, and so that CI/CD workflows (GitHub Actions, GitLab CI, Azure DevOps)
have a stable, scriptable surface to call into.

Every command follows the same shape: a thin `@click.command` entry point parses arguments into a
typed dataclass, then hands off to a `handle_*(config)` orchestration function that does the actual
filesystem, process, network, or execution work and can be called directly from tests or other
Python code without going through Click.

## Scope

In scope: the equivalent `haute ...` and `python -m haute ...` invocation forms; the ten
subcommands (`init`, `run`, `lint`, `train`, `serve`, `deploy`, `smoke`, `status`, `impact`,
`gpu-setup`), the `ci` group's `secrets` and `update`; their
argument parsing, user-facing output/error formatting; and the small amount of orchestration logic
that glues each command to the rest of the codebase.

Out of scope, owned elsewhere:
- Pipeline parsing and graph execution — [execution-engine](../execution-engine/high-level.md),
  [pipeline-config](../pipeline-config/high-level.md).
- Deploy bundling, pruning, validation, scoring, and MLflow registration internals —
  [deploy](../deploy/high-level.md).
- The FastAPI backend that `haute serve` launches — [server-api](../server-api/high-level.md).
- Model training internals (`TrainingJob`, algorithms, metrics) — [modelling](../modelling/high-level.md).
- The trust boundary for user training scripts, which run as trusted project code in the CLI
  process without the server's accident guard — [sandbox-security](../sandbox-security/high-level.md).

## Behaviour

- `haute ...` and `python -m haute ...` invoke the same Click group. The module form is a
  first-class compatibility path for managed environments that permit the selected Python
  interpreter but block generated console-launcher executables. It maintains no separate command
  registry or parser: Click receives the arguments and preserves the command and exit-code
  contracts, while identifying the chosen invocation form in usage text.
- `haute --help` opens with the package's own description, `pyproject.toml`'s `description`,
  and so names no deploy target: Databricks is one of several.
- `haute init [--automated | --workbench] [--ci ...] [--target ...] [--host ...] [--store ...]
  [--sign-in ...] [--force]` scaffolds a new project in the current directory. At a terminal
  it asks what its flags leave out, each question a numbered list to pick from, in turn: how
  the project prices, automated (an API other systems call) or workbench (an app
  underwriters price quotes in); where the repository is (GitHub, GitLab, Azure DevOps or
  none); where it is hosted, an automated project among the deploy targets and a workbench
  project among the underwriter app's hosts, a list of one, Azure Container Apps; and, for a
  workbench project, which database holds the saved quotes (SQL Server or PostgreSQL) and
  how the analyst signs in to it (a password, their Microsoft Entra ID sign-in or a service
  principal). Without a terminal an unanswered question takes its default (automated,
  GitHub, Databricks, a workbench project's one host), and a workbench project's database
  and sign-in, which have none, are refused naming their flags; a flag of the other kind of
  project (`--target` with `--workbench`, or `--host`, `--store` or `--sign-in` without it)
  is refused. Every project gets a blank `rating/` pipeline package, `.env.example`,
  test-quote fixtures, CI/CD workflow files for the chosen provider, a git pre-commit hook,
  and `.gitignore` guard entries. An automated project's `haute.toml` holds its deploy
  target's settings and its workflows deploy the scoring endpoint. A workbench project hosts
  no scoring API: its `haute.toml` holds `[deploy.container]`, the app image's base image,
  registry and port, and no endpoint settings, and the `[workbench]` table with
  `[workbench.app] host` and the quote store's `dev`, `staging` and `prod` on the database
  chosen and `[workbench.app.staging]` and `[workbench.app.prod]`, the Container Apps each
  hosted environment runs as, left for IT to fill in; it also gets a blank form folder,
  `rating/workbench/`, and `connections.toml`, the
  three environments' connections for the editor and `haute app serve`, on that database
  with the analyst's sign-in, whose variables `.env.example` names beside the host's
  credentials; and its workflows validate and publish the underwriter app
  ([workbench](../workbench/high-level.md)). Refuses to run if `haute.toml` already exists
  unless `--force` is given, `--workbench` included: an automated project is never
  converted into a workbench project, but started again. A path of
  the form folder reached through a link or junction refuses the init before anything is
  written. A root `main.py` is
  always deleted: `uv init` recreates that placeholder every time it runs, and a Haute project's
  real entry point is `rating/main.py`, so the root file is a tooling artifact — never user
  content — and removing it keeps the entry point unambiguous. No `prompts/` directory or
  starter nodes/sidecars are generated.
- `haute run [pipeline_file]` executes a pipeline end-to-end through the same
  parse → flatten → `execute_graph` path the GUI uses (each submodel occurrence runs as its
  definition's nodes, and a Workbench node takes its tables from the project's form folder,
  [workbench](../workbench/high-level.md)), printing a per-node row/column summary and a
  preview of the final node's output.
- `haute lint [pipeline_file]` strictly parses a pipeline and exits non-zero on syntax,
  configuration, topology, or other parse failures. For a valid canonical graph it reports
  structural problems (edges pointing at missing nodes and orphan nodes) without executing
  anything.
- `haute train <training_script>` runs a training script that must define a module-level `job`
  object with a callable `run(progress=...)` method (normally a `TrainingJob`), streaming a
  live progress bar and printing the returned result's model path, feature counts, and metrics.
- `haute serve [--host] [--port] [--no-browser]` starts the Haute UI: dev mode (Vite + FastAPI with
  autoreload) when `node_modules` is installed for either the nearest working-directory frontend
  or the frontend beside the imported editable Haute source checkout. The working-directory
  frontend has priority. Wheel installs and source checkouts without frontend dependencies use
  production mode with the pre-built static bundle. It binds to `localhost` by default so the
  browser session works with a bare `haute serve`.
- `haute deploy [pipeline_file] [--model-name] [--dry-run] [--endpoint-suffix]` validates a pipeline,
  scores its test quotes, and deploys it to the configured target. Non-dry-run deploys are blocked
  outside a recognised CI environment, and one whose target's credentials are not all set stops
  before any other work, naming every missing one. A Databricks deploy returns once its endpoint
  serves the new version, or fails with Databricks' reason when it cannot. A workbench project
  hosts no scoring API, so in one `haute deploy` refuses before anything else, a dry run
  included, naming `haute app build`, which validates and builds what it releases.
- `haute ci secrets [--check] [--no-verify]` sets the CI secrets the deploy target needs, or in
  a workbench project its `[workbench.app] host`, on the
  project's GitHub repository from `.env`, after checking Databricks accepts them; `--check`
  reports which are set. On GitLab and Azure DevOps it names the secrets to add by hand. The
  editor's Push does the same for a GitHub repository it first publishes to, before the push,
  and reports any a later Push finds missing so the push panel can offer to set them
  ([frontend git UI](../frontend-git-ui/high-level.md)); the editor sets only secrets the
  repository lacks, and the command is how a changed value reaches CI.
- `haute ci update` rewrites the CI files `haute init` generated, and `.env.example`, with
  the variables `connections.toml` names when the project has one, for the
  installed Haute and the project's `[ci]` table, so a project takes up fixed workflows, a
  changed `[ci.staging] endpoint_suffix`, or a `[ci.production] environment` whose reviewers
  then gate production, without being initialised again. A workbench project's are the
  workbench's set, for its `[workbench.app] host`, which it refuses to run without; an
  automated project's are the scoring endpoint's, for its `[deploy] target`.
- `haute ci registry-host` prints the host of the registry a container target, or a
  workbench project's app image, pushes to, for the login step its generated jobs run
  before `haute deploy` or `haute app build --push`.
- `haute smoke [--endpoint-suffix]` sends each test quote in `tests/quotes/` to a live
  serving endpoint as a request of its own, as a pipeline reading one quote per request takes it, and
  holds each quote with an `expected` output to it as the deploy check does, walked into a
  Workbench Output's tables: one answer row, an object of output columns, each expected
  value within tolerance. A failed request or mismatch is reported by its row in the file
  and the other quotes are still priced. HTTP health accepts any decodable 2xx JSON.
  An endpoint suffix is a Databricks naming override; HTTP targets reject it and direct operators
  to configure the complete staging endpoint URL instead.
- `haute app build [OUTPUT] [--image TAG | --push]` builds the workbench project's release for
  the underwriter app into `OUTPUT` (`.haute/app/release` by default, which git
  ignores): the pipeline resolved,
  validated and bundled as `haute deploy` does it, test quotes included, with the
  sheets, what the release is and its entry point, and, with `[deploy.container]
  base_image` set, the Dockerfile that makes it the image's build context, installing the
  driver of each provider the release's environments declare, pinned as installed where it
  builds (pyodbc and Microsoft's ODBC Driver 18 for `sqlserver`, psycopg for `postgresql`,
  azure-identity for a Microsoft Entra ID sign-in), a driver not installed there refused
  naming it; `--image` builds that image with Docker. `--push` builds it as
  `<registry>/<name>:<short commit>`, the registry `[deploy.container] registry` names, which
  it refuses to run without, as it does a project whose commit git cannot name, and the
  project's name in lower case, as Docker takes a
  repository's, pushes it there and prints the reference by digest that was pushed,
  `<registry>/<name>@sha256:...`, as its last line, so the
  workbench's CI publishes each release's image once and knows exactly what it published.
  A directory that is or holds the
  project or the working
  directory, a file, and a directory holding anything but an earlier release are refused,
  as are a project without a workbench and sheets never saved; an earlier release is
  replaced only once the new one has built beside it, so a build that fails leaves it as
  it was.
- `haute app rollout ENVIRONMENT (--image REFERENCE | --from ENVIRONMENT)` rolls a
  workbench project's hosted app, the Container App `[workbench.app.<ENVIRONMENT>]` names, to
  an image by digest in a maintenance window, checks the release there (it runs, the host's
  sign-in stands in front of it, and its quotes are open), puts the previous release back
  when the new one fails, and records the release that stays on the Container App as Azure
  tags; `--from` rolls to the release another environment's app runs, when it runs the commit
  checked out. It is what
  the workbench's generated jobs run, signed in as their service principal, and exits 1 naming
  what failed ([workbench](../workbench/high-level.md)).
- `haute app serve [--port PORT] [--static-dir DIR] [--reader]` builds the release
  under `.haute/app/release` and serves the underwriter app on `127.0.0.1` alone (port
  8001 by default), every request signed in as the analyst's git identity, able to
  change quotes unless `--reader` signs it in as one who reads them; without an identity
  it refuses to start. It keeps the quotes saved in it in the `dev` environment, at the
  database `connections.toml`'s `[dev]` connection names, signed in as it says, refusing to
  start while `haute.toml` declares no `dev` or the file holds no `[dev]`; the app checks
  that database's tables as a hosted app checks its own, its quotes closed, naming each
  difference, until they fit the release's form. No command changes any environment's
  tables: the people who own its database change them in SQL, starting from the SQL the
  Workbench view's schema pane writes ([workbench](../workbench/high-level.md)).
- `haute status [model_name] [--version-only]` looks up a model's latest version/stage in the MLflow
  Model Registry.
- `haute impact [--sample] [--batch-size] [--endpoint-suffix]` scores a configured safety dataset,
  or the test quotes when none is configured,
  through both the staging and production endpoints and reports the pricing differences, writing a
  markdown report and, on GitHub Actions, appending to the job step summary. `smoke` and `impact`
  reach Databricks endpoints with the serving pair `deploy` uses, the credentials the generated
  workflows give them.

Invariants that hold across every command:
- Where `resolve_model_name` is used (`status`), precedence is **CLI value >
  `HAUTE_MODEL_NAME` > `[deploy].model_name` in `haute.toml` > loud error**. `deploy` loads the TOML config then
  applies its `--model-name` override; without TOML it derives the default from the resolved
  pipeline stem when the option is absent.
- `run`, `lint`, and `deploy` delegate CLI/default pipeline discovery to
  `haute._project.resolve_pipeline_file`; a TOML-configured deploy pipeline is passed through
  that same resolver before `DeployConfig` and `resolve_config` consume it.
- A command piped or redirected to a file writes every mark it prints (`✓`, `✗`), whatever
  the locale: a stream that is not UTF-8 is written in UTF-8, or, when `PYTHONIOENCODING`
  chose its encoding or it is a terminal of another encoding, keeps that encoding and shows
  a mark it cannot hold as `?`. None of it raises.
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
- **The first Push sets the deploy's secrets.** A new project's first staging deploy runs as
  soon as its default branch reaches GitHub, and a deploy without its secrets fails. The Push
  that first publishes the project is the moment the analyst has deliberately connected it to
  that repository, so it sets the secrets the repository lacks from `.env` before the default
  branch exists. Later Pushes only report missing ones: setting them then waits for the push
  panel's explicit Set from .env, since on a shared repository a missing secret may be one a
  teammate or an administrator manages. Neither path overwrites a secret, so one analyst's
  `.env` never replaces the value CI already uses; the command remains the deliberate way to
  change one. The push never fails because the secrets could not be set: publishing the work
  is what the analyst asked for, and the secrets' problem is reported beside it.
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
- Depends on [sandbox-security](../sandbox-security/high-level.md) for `serve`'s local session
  token (`ensure_local_session_token_env`, `TRUSTED_HOSTS_ENV`).
- `haute init` depends on `haute._scaffold` (template generation for `haute.toml`, CI workflows,
  starter pipeline) and `haute._gitignore_guard` — both outside this component's scope but owned by
  the same project-bootstrap concern as [pipeline-config](../pipeline-config/high-level.md).
- The editor's `POST /api/git/push` and `POST /api/git/ci-secrets`
  ([git integration](../git-integration/high-level.md)) reach the deploy's secrets through
  `haute._ci_secrets`, the module `haute ci secrets` uses.
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
