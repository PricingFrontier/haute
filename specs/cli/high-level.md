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
  [--sign-in ...]` scaffolds a new project in the current directory, or sets an existing one
  up again (below). At a terminal
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
  is refused. Every new project gets a blank `rating/` pipeline package, `.env.example`,
  test-quote fixtures, CI/CD workflow files for the chosen provider (each GitHub action
  pinned to a commit), a git pre-commit hook that fixes and formats only what is staged and
  stops a commit (a milestone, or a person's own `git commit`; never a save) on a problem
  ruff cannot fix safely, recorded executable in git's index inside a repository on every
  system (Windows has no executable bit for git to read), so a clone on Linux or macOS can
  run it,
  `.gitignore` guard entries, and `.gitattributes` entries that pin LF line endings for the
  `*.py`, `*.json`, `*.toml`, `*.yml` and `*.md` files and the extensionless
  `.githooks/pre-commit` in every checkout, whatever `core.autocrlf` says, and mark `uv.lock`
  generated (CSV files are left out: analysts save parameter files from other tools; a
  team's own `.gitattributes` keeps its lines, the missing entries appended under
  `# Haute`). Every file `haute init` writes is UTF-8 with LF line endings on every
  platform (`haute._scaffold_files.write_lf`); a starter file of `rating/` or `tests/` is written only
  where the folder has none, so a `rating/main.py` already there is kept and the summary
  names what was kept, and no workflow already in the folder is pruned. The hook is installed in `.git/hooks/` too inside a git repository,
  unless a `pre-commit` there is not Haute's, which is left as it is and named. An automated project's `haute.toml` holds its deploy
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
  ([workbench](../workbench/high-level.md)). In a project that already has a `haute.toml`,
  `haute init` sets the project up again. It asks the same questions, each defaulting to
  the project's current answer: the kind of project from `[workbench] enabled`, the
  repository from `[ci] provider`, the deploy target from `[deploy] target`, the app's
  host from `[workbench.app] host`, the database from the quote store's environments'
  `provider` and the sign-in from `connections.toml`'s connections. Enter, or a run
  without a terminal, keeps what the project has; a question the project holds no answer
  to (one it does not offer, or the other kind of project's) has no default there, save
  the app's host, whose one choice is its default. A
  `haute.toml` that does not read as TOML is refused, naming the error, before any
  question. The re-run writes `haute.toml` afresh from the answers, keeping
  `[project] name`; the previous file is left to git, and the summary says to copy back
  from it what was set by hand, the `[mlflow]` settings saved from the app among them. It
  writes the workflows (removing each CI file Haute wrote, which its first line records, that
  the new set does not hold; a team's own file at one of Haute's paths is left),
  `.env.example` and the pre-commit hook again. Its summary and what it records are one
  list: every file the re-run writes or regenerates (`pyproject.toml`, `.python-version`,
  `haute.toml`, the form folder and `connections.toml` when written, `.env.example`, the
  hook, the workflows, `.gitignore` and `.gitattributes`) is named as written, updated or
  unchanged, each workflow it removed as removed, and each of them Git does not track yet
  is recorded in version history as a save, as `haute ci update` does,
  and writes nothing in `rating/` or `tests/`, which are the project's, beyond the
  workbench's form folder when it has none. The kind of project may change: an automated
  project set up as a workbench gets the blank form folder and `connections.toml` where
  it has none, a workbench set up as an automated project keeps both, and either way the
  pipeline keeps its input and output nodes, so the summary names the ones to replace (a
  Quote Input and a Quote Response by a Workbench Input and a Workbench Output, or the
  reverse). The `haute` requirement in `pyproject.toml` gains each extra the answers need
  that it lacks, keeping its version, marker and other extras; none is taken away.
  `connections.toml` is written, the sign-in asked, when there is none or when one of its
  `dev`, `staging` or `prod` connections, those of the environments the new
  `haute.toml` declares, is on another database than the one answered (SQLite
  included), a changed quote store;
  otherwise it is kept and the sign-in is not asked, a `--sign-in` given then answering
  nothing. A path of
  the form folder reached through a link or junction refuses the init before anything is
  written. A root `main.py` that is
  exactly the placeholder `uv init` writes, its project name aside, is deleted: a Haute
  project's entry point is `rating/main.py`, and removing the placeholder keeps it
  unambiguous. Any other root `main.py` is the team's, in a repository Haute is set up in,
  so it is kept and the summary says so. A first `haute init` also removes `uv init`'s other
  leftovers that are still exactly as uv wrote them and that nothing references, keeping a
  working build configuration (`remove_uv_init_leftovers`): an empty README with the
  pyproject's `readme` naming it, the placeholder `description`, and a `--package` or `--lib`
  project's `src/<module>/` with the `[project.scripts]` entry and the `[build-system]` that
  name it, each named in the summary. A re-run removes no file, the root `main.py` included
  (READ-41). `.env.example` says in a comment what each credential is for, and the
  `.gitignore` guard writes its `# Haute` heading once, adding a newer Haute's entries under
  it. No `prompts/` directory or
  starter nodes/sidecars are generated.
- `haute run [pipeline_file] [--node NODE] [--source SOURCE] [--rows N]` runs a pipeline
  to its output node (a Quote Response, a Workbench Output or a node marked as the output),
  or to `--node`, as a batch run does: parsed and flattened as the editor's runs are (each
  submodel occurrence runs as its definition's nodes, and a Workbench node takes its tables
  from the project's form folder, [workbench](../workbench/high-level.md)), then walked once
  as lazy plans under the `LAZY_SINK` profile and the named source (`live` by default), so
  no node is materialised but the target, whose output alone is collected, all of it or its
  first N rows. It prints each node walked with the columns its plan carries and the
  target's output. A pipeline with no output node and no `--node` is refused, naming both
  remedies. `--node` takes the names an analyst sees, through the same record the editor's
  requests resolve their addresses by ([server API](../server-api/low-level.md), "Node
  addresses"): a pipeline node's, a submodel's (run to its first output, as the editor
  previews it), and a node inside a submodel by its own name when one submodel holds it, or
  as `<submodel>/<node>`, the name the run prints for it; so grouping nodes into a submodel
  keeps each of them findable. A name several submodels' nodes have is refused naming each,
  a submodel without outputs naming its nodes, and a name that fits nothing naming the names
  there are.
- `haute lint [pipeline_file]` checks a pipeline as the editor's save does, without
  executing anything, so the generated CI's lint step refuses what the editor would refuse
  to save. It strictly parses the pipeline and exits non-zero on syntax, configuration,
  topology, or other parse failures; then reports, all in one run, the editor document's
  diagnostics with file and line (a statement or comment a save would drop among them),
  what the editor's save-time validation refuses (names, global constants, joins, selectors,
  a node path another checkout would read otherwise, such as an absolute path or one in
  another letter case than its file, and the rest, each with the save's own message), and
  structural problems of the flattened
  graph (edges pointing at missing nodes and orphan nodes). The save's warnings are printed
  without failing. A pipeline with no nodes, the one `haute init` writes, passes, its summary
  saying it has none yet: the editor loads it ready and saves it, so a new project passes its
  CI's lint step.
- `haute train <training_script>` runs a training script that must define a module-level `job`
  object with a callable `run(progress=...)` method (normally a `TrainingJob`), streaming a
  live progress bar and printing the returned result's model path, feature counts, and metrics.
- `haute serve [--host] [--port] [--no-browser]` starts the Haute UI: dev mode (Vite + FastAPI with
  autoreload) when `node_modules` is installed for either the nearest working-directory frontend
  or the frontend beside the imported editable Haute source checkout. The working-directory
  frontend has priority. Wheel installs and source checkouts without frontend dependencies use
  production mode with the pre-built static bundle. It binds to `localhost` by default so the
  browser session works with a bare `haute serve`.
- `haute deploy [pipeline_file] [--model-name] [--dry-run] [--keep-build] [--endpoint-suffix]`
  validates a pipeline, scores its test quotes, and deploys it to the configured target. Each
  deploy stages its build in a directory of its own under the project's `.haute_build`, removed
  once it has shipped or failed; `--keep-build` keeps it and names it. Non-dry-run deploys are blocked
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
- `haute ci update` rewrites the CI files `haute init` generated, and no others (it removes
  only a CI file Haute wrote, as the re-run does), and `.env.example`, with
  the variables `connections.toml` names when the project has one, for the
  installed Haute and the project's `[ci]` table, so a project takes up fixed workflows, a
  changed `[ci.staging] endpoint_suffix`, or a `[ci.production] environment` whose reviewers
  then gate production, without being initialised again. A workbench project's are the
  workbench's set, for its `[workbench.app] host`, which it refuses to run without; an
  automated project's are the scoring endpoint's, for its `[deploy] target`. A generated
  file Git does not track yet, a workflow a newer Haute adds or one an earlier run left
  uncommitted, is recorded in version history as a save on the working branch, so the next
  Commit takes it: Commit takes a new
  file only in the pipeline folder ([git integration](../git-integration/high-level.md)).
  It names each file it wrote, updated, found unchanged or removed, a GitHub project's
  `.python-version` among them, from the one list it records from, and says the CI files
  are up to date only when it changed none.
  Without a chosen working branch or a git identity it names such files as not yet in
  version history, for `git status`, which lists them where `git diff` does not. What it
  changes in tracked files is left for review and the next Commit.
- `haute ci registry-host` prints the host of the registry a container target, or a
  workbench project's app image, pushes to, for the login step its generated jobs run
  before `haute deploy` or `haute app build --push`. Where that push needs a registry, a
  workbench project's app image and a build-and-push-only target's scoring image, an empty
  `[deploy.container] registry` is refused in the push's own words, so the job stops at its
  login step; a `container` target's image without one stays local, and it prints an empty
  line.
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
  validated and bundled as `haute deploy` does it, with the sheets, what the release is
  and its entry point, its test quotes priced and compared (one that fails to price
  failing the build, one whose outputs moved listed while the build passes, each in
  `test_quotes_report.md`, written in the working directory whatever the outcome;
  [workbench](../workbench/high-level.md), Test quotes), and, with `[deploy.container]
  base_image` set, the Dockerfile that makes it the image's build context, installing the
  driver of each provider the release's environments declare, pinned as installed where it
  builds (mssql-python, which carries ODBC Driver 18, for `sqlserver`, psycopg for `postgresql`,
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
- `haute app impact [--sample N]` compares the release a workbench project's staging app
  runs with production's on up to N of the final quotes production keeps (100 by
  default), each priced on both apps through their own routes, on the output fields
  the two releases share, and writes `impact_report.md`: the fields only one release
  has, each quote whose prices moved and how, the quotes that no longer compare or
  that the new release refuses. It changes nothing. It is what the workbench's
  publish job runs after the staging rollout, signed in as its service principal, and
  exits 1, the report written, when staging fails on a quote production prices or an app
  cannot be reached or used ([workbench](../workbench/high-level.md)).
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
- Every command states what it needs of the project, and the command line finds it from
  the shell's folder by the one finder ([pipeline-config](../pipeline-config/high-level.md),
  "Finding the project") before the command runs. No command's need differs from what it
  did before the finder was shared:

  | Need | Commands | Where it runs |
  |---|---|---|
  | none | `init`, `gpu-setup` | the shell's folder; no project is looked for |
  | the pipeline file's project | `run`, `lint` | the project holding the pipeline file, found from the file's folder; the file's folder when no `haute.toml` is above it |
  | a project if there is one | `status`, `deploy`, `train` | the project holding the shell's folder, from any folder inside it; the shell's folder when no `haute.toml` is above it (`status` and `deploy` then need the model named on the command line) |
  | a project | `serve`, `smoke`, `impact`, `ci secrets`, `ci update`, `ci registry-host`, `app build`, `app rollout`, `app impact`, `app serve` | the project holding the shell's folder, from any folder inside it; refused without one |

  A command that needs a project and finds none is refused in one sentence, the same for
  each: no `haute.toml` in the folder or above it, run inside a Haute project or make one
  with `haute init`. A `haute.toml` found outside a git repository is refused by every
  command but `init` and `gpu-setup`, naming the folder and `git init`. Every command that
  finds a `haute.toml` reads it through the one settings parse, so a byte-order mark is read
  through by all of them and a misspelt key or table is refused by all of them in the same
  words. `tests/test_project_finding.py` runs every command on a table of projects and fails
  for a command without a row.
- `haute serve` checks the configured pipeline file before the server starts: one missing,
  or holding no pipeline, ends the command in one line and exit 1, naming the file.
- Where `resolve_model_name` is used (`status`), precedence is **CLI value >
  `HAUTE_MODEL_NAME` > `[deploy].model_name` in `haute.toml` > loud error**. `deploy` loads the TOML config then
  applies its `--model-name` override; without TOML it derives the default from the resolved
  pipeline stem when the option is absent.
- `run`, `lint`, and `deploy` delegate CLI/default pipeline discovery to
  `haute._project.resolve_pipeline_file`; a TOML-configured deploy pipeline is passed through
  that same resolver before `DeployConfig` and `resolve_config` consume it.
- A command piped into a reader that stops reading ends quietly (Failure model, below).
- A command piped or redirected to a file writes every mark it prints (`✓`, `✗`), whatever
  the locale: a stream that is not UTF-8 is written in UTF-8, or, when `PYTHONIOENCODING`
  chose its encoding or it is a terminal of another encoding, keeps that encoding and shows
  a mark it cannot hold as `?`. None of it raises.
- Command handlers use exit code 1 for their handled operational failures; Click uses exit
  code 2 for argument/choice/range parsing errors. One informational outcome intentionally
  succeeds: ordinary `status` reports a missing registered model and exits 0.
  `status --version-only` makes the same missing-model case an exit-1 `ClickException` for
  scripts.
- Every command that reads the project's deploy settings refuses a `[deploy] target` no
  deploy can go to (one Haute does not know, a typo say, or one planned and not yet
  implemented) in the same words, naming the key as it is written (`HAUTE_TARGET` when the
  environment set it) and the targets there are, as `haute deploy --dry-run` does.

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
  Vite port is occupied by a listener (a stopped server's connection that a browser tab still
  holds is not one, so a restart straight after a stop starts), waits for both listeners
  before opening the browser, and never follows
  Vite's automatic port rollover to a different, unreported origin.
- **Command-scoped failure rules.** Missing Node/npm and a missing production frontend build
  fail rather than guessing; malformed TOML fails. What each command needs of the
  project is the table above: `smoke`/`impact` need one, while deploy and explicit-model
  status have non-TOML paths. `serve` reads `[server] host` from the settings parse like
  every other setting: no table is no override, and an unreadable or malformed file is
  refused. No hardcoded Windows Node/npm path is guessed.
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
- **`impact` skips production only on an authoritative record that it has never been
  released.** On Databricks that is the workspace's own answer that the production endpoint does
  not exist (`NotFound`/`ResourceDoesNotExist`); for an HTTP target, whose production is only an
  address, it is origin holding no `deploy/` release tag, the tag the generated production job
  pushes for every release it ships, so the first release needs no flag or manual step. The report
  then says production was not compared and why. Once production has been released it must answer:
  an unset `[ci.production] endpoint_url`, an HTTP error status (a 404 included, so a mistyped
  address is never a first deploy), an authentication failure, a timeout or no answer fails the step
  naming the address, as does release tags that cannot be read. A broader catch would misreport an
  outage as "nothing to compare against yet". Each batch's answer must hold one prediction per quote
  sent (a single value only for a one-quote batch): any other count fails naming the batch and both
  counts, because the comparison pairs rows by position.

## Interactions

- Depends on [pipeline-config](../pipeline-config/high-level.md) and
  [execution-engine](../execution-engine/high-level.md) for `run`/`lint` (`parse_pipeline_file`,
  `execute_lazy_graph`), and on [server-api](../server-api/high-level.md)'s save validation
  (`SavePipelineService.validate_graph`) for `lint`.
- Depends on [deploy](../deploy/high-level.md) for `deploy`/`smoke`/`impact`/`status`
  (`DeployConfig`, `resolve_config`, `validate_deploy`, `score_test_quotes`, `deploy_resolved`,
  `score_endpoint_batched`, `score_http_endpoint_batched`, `get_deploy_status`).
- Depends on [server-api](../server-api/high-level.md) for `serve`
  (`haute.server:create_app_from_environment`,
  `STATIC_DIR`, `static_build_ready`).
- Depends on [modelling](../modelling/high-level.md) for `train` (`TrainingJob.run`).
- Depends on [sandbox-security](../sandbox-security/high-level.md) for `serve`'s local session
  token (`ensure_local_session_token_env`, `TRUSTED_HOSTS_ENV`).
- `haute init` depends on `haute._scaffold` (template generation for `haute.toml`, CI workflows,
  starter pipeline), `haute._scaffold_files` (LF writes and `.gitattributes`) and
  `haute._gitignore_guard` — all outside this component's scope but owned by
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
uses `ClickException` (exit 1). Every other failure ends at the `haute` group in one line and
exit 1, as below.
A missing `haute.toml` where the command requires one (`smoke`/`impact`), an unresolvable
required model name, a missing frontend build, a missing training `job` variable, a failed
validation, or a deploy exception is terminal. Ordinary
`status`-not-found is the one deliberate informational success path described above, not a
deploy or scoring success.

Exceptions are handled with narrow, explicit catches rather than blanket `except Exception` used as
a fallback mechanism: transport-classification helpers (`_is_databricks_not_found`,
`_is_http_not_found` in `src/haute/cli/_impact.py`) exist specifically so that only the exact "not found" shape is
swallowed and reclassified — everything else re-raises. Where a command turns a failure into
one line with its own context (around `resolve_config` in `deploy`, parsing and execution in
`run` and `lint`, the script and `job.run` in `train`), it catches only the expected ones,
`EXPECTED_FAILURES` in `src/haute/cli/_helpers.py`: Haute's own errors (`HauteError`:
`DeployError`, `ConfigError`, `ParseError` and the rest), `ValueError` and `OSError`
(`FileNotFoundError` among them). Helpers raise rather than exit: `resolve_transport` raises a
`ClickException` for a container target without a staging endpoint URL.

Whatever a command raises past its own catches ends at the command line's one edge, the
`haute` group (`HauteGroup.invoke`, through `answer_command_failure`), in one line and exit 1,
never a traceback: a failure Haute names (memory, a file the operating system refused, a node's
own code, a refusal) in the words every edge gives it (`haute._failure_answers`), as `haute run`
refused for its process memory cap names the cap, what it holds and the setting that raises
it; an expected failure in its own words, so a misspelt `haute.toml` key, a value of the wrong
type and a read-only file each end in their sentence; and anything else, a `TypeError` say,
as "Haute hit a failure it does not recognise (incident <id>) …" and where its traceback was
written, `.haute/incidents/<id>.txt` in the project, or `haute-incidents/` in the system's
temporary folder outside one. A record that cannot be written is said with its reason, and
the traceback follows. Click's own usage errors (a port out of range among them) exit 2, and
an abort ends as Click ends it. A reader that stopped reading the command's output (a pipe into
`head` that took its lines, a pager quit early) ends the command quietly, exit 1, whatever was
writing (the command's own line or a log line): nothing is said, neither a parse error nor
the interpreter's notice. The process's own standard output and error, when not a terminal,
raise `OutputClosed` for it, which is no `OSError`, so no command's handler of its own
failures takes it for one; any other failure of a stream, or a broken pipe to a program a
command runs, is answered as a failure.

The Databricks smoke readiness loop retries only the SDK's explicit not-found response while an
endpoint is being created. Authentication, permission, transport, and other SDK failures stop
immediately with the original exception chained. Training execution and result rendering are
separate failure boundaries: once `job.run()` has returned successfully, a formatting problem is
reported as a result-reporting failure, in the `haute` group's line, and never as “Training failed”.

`handle_deploy` renders `DeployError` as an expected target/configuration failure and keeps
the explicit missing-dependency and not-implemented messages. It does not catch the
backend with `except Exception`: an unexpected implementation exception ends at the
`haute` group, an incident whose record keeps its original type and traceback, while target
adapters are responsible for raising
`DeployError` for expected operational failures such as missing credentials, unavailable
Docker, or rejected build/push operations.
