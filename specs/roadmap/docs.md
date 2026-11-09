# Documentation roadmap

## Scope

The user documentation under `docs/` that the site publishes: the getting-started pages,
the deployment guide and its per-target and per-CI-provider pages, and the README's install
and deploy sections. These packages come from a check of those pages against `main` at
`7bafedad` on 25 October 2026: two reviewers read each page against the code it describes,
and every finding below was verified again at its source lines. `DOC-05` comes from the same
day's live check, which added a submodel to a project scaffolded before the current `haute
init`. The building-models and Workbench pages were not part of that check.

A defect in the behaviour a page describes belongs to the component that owns the behaviour,
and a package here names it as a dependency rather than copying it. The one such dependency,
an expired token named as one, is delivered: deploy's connectivity check names the token
on a 401 (`src/haute/deploy/_mlflow.py::_check_databricks_connectivity`).

The site describes `main`, decided on 25 October 2026: the next release, due soon, ships
`main`, so every page here is written for what `main` does.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| DOC-02 | Planned | P2 | The CI pages describe the workflows Haute generates: who can start production, each target's secrets, job limits and images, and what `haute ci update` rewrites. |
| DOC-03 | Planned | P2 | A new project reaches a green first pull request and a staging deploy by following the guides from an empty folder. |
| DOC-04 | Planned | P2 | The site publishes `main` again, with the other packages landed, before the next release. |
| DOC-05 | Planned | P2 | A project made by haute 0.6.0 upgrades to the next release by following one page. |

## Planned improvements

The other packages land on `main` first, and `DOC-04` then publishes the site. Where
a page restates a fact the code owns (a secret name, a job's time limit, an image, an
endpoint name), a documentation parity test derives it from the code, as
`tests/test_docs_accuracy.py` already does for the scaffold tree, so the page cannot drift
from the generator again.

### DOC-02 — The CI pages describe the generated workflows
**Why:** The three CI pages (`docs/deployment/ci/github-actions.md`, `gitlab.md` and
`azure-devops.md`) disagree with the files `ci_files` generates:
- **Approval.** The GitHub page tells readers to configure GitHub environments or branch
  protections if production needs approval (:35, :105, :275), and the GitLab page to
  configure protected environments (:94). An environment's rules gate only a job that names
  it, and branch protection gates no manual run. As generated, the production jobs name none,
  so production is started by anyone who may run the workflow (GitHub) or play the manual
  job (GitLab), which works on every plan, a free GitHub plan included. A project gates it by
  naming an environment in `[ci.production] environment` and running `haute ci update`,
  which no page mentions. Azure DevOps' production deployment always runs under an
  environment, `production` unless one is named, gated by the checks configured on it, as
  its page says.
- **Time limits.** The GitLab and Azure DevOps pages give the deploy jobs 15 minutes
  (gitlab.md:218, :221; azure-devops.md:252, :255); a Databricks deploy job gets 60, because
  the deploy waits for its endpoint (`_deploy_timeout_minutes`).
- **`haute ci update`.** The GitHub page says it "leaves everything else alone" (:317). It
  also deletes another provider's generated files and overwrites hand edits to its own,
  and a git install's frontend build. The GitLab and Azure DevOps pages never mention the command,
  though it serves every provider, and none says it now takes up a changed
  `[ci.staging] endpoint_suffix` or `[ci.production] environment`.
- **`.env.example`.** All three pages and `docs/deployment/index.md:173` say it "lists
  exactly which secrets are needed". For Databricks it also holds the local data-access
  pair, `DATABRICKS_HOST` and `DATABRICKS_TOKEN`, which no CI step reads; `haute init` prints
  the CI secrets, and `haute ci secrets --check` checks them.
- **Secrets by target.** No page lists GCP Cloud Run's secrets (`GCP_PROJECT_ID`,
  `GCP_SERVICE_ACCOUNT_KEY`, the key the deploy jobs log in to Artifact Registry with),
  though each names GCP as a target.
- **Generated details.** The Azure DevOps page tells readers to give the production
  checkout `persistCredentials: true` (:194), which the generated pipeline already sets, as
  the page's own :245 says. The GitLab page says the jobs run in `python:3.11` (:150, :196);
  the image follows the project's Python and is 3.11 only for a project that declares none.

**Plan:** Each page says who can start production as generated, and how to require a
reviewer. Name the environment in `[ci.production] environment`, run `haute ci update`,
and configure its reviewers in the provider. Required reviewers exist for a private GitHub
repository only on GitHub Enterprise, and GitLab's protected environments need Premium, so
a team on a free plan leaves the setting unset. The time limits, image and secret tables state what the
generator writes, with GCP's table added. The `haute ci update` section moves to the
deployment overview, which all three pages link: what it rewrites, what it deletes, and that
hand edits to generated files are overwritten. Each page points to the CI secrets
`haute init` prints. The Azure checkout clause goes.

**Acceptance:** Parity tests derive each CI page's secret tables from `ci_secret_names`, its
deploy-job time limits from `_deploy_timeout_minutes`, and the GitLab image from the
`python_version` pin. No page asks for a setting the generated files already have, or one
that cannot gate the job it names.

**Dependencies:** None.

**Evidence:** `src/haute/_scaffold.py::ci_files`, `::github_deploy_prod_yml`,
`::gitlab_ci_yml`, `::azure_devops_yml`, `::_deploy_timeout_minutes`, `::_pin_ci_python`,
`::TARGETS` (each target's `secrets`) and `::env_example`;
`src/haute/cli/_ci.py::handle_ci_update` and `::handle_ci_secrets`;
`src/haute/cli/_init_cmd.py::_prune_stale_ci_files` and `::write_generated_files`.

### DOC-03 — A new project's path from an empty folder
**Why:** The guides assume a project that already has a repository and a pipeline:
- `docs/deployment/before-you-start.md` covers cloning an existing repository (:65-104)
  and nothing that connects a new `uv init` project to a GitHub repository. `haute ci secrets`
  runs `gh secret set` in the project without `--repo`, so it needs that remote, and a first
  push of a new branch needs `git push -u origin <branch>` rather than the page's `git push`
  (:180).
- The scaffolded pipeline is an empty canvas and its test quote a placeholder, so a new
  project's first pull request fails its dry-run deploy with "No output node found. Mark a
  node with @pipeline.output()." The Databricks prerequisites and checklist
  (`databricks.md`:36-45, :436) never say to build the pipeline and its test quotes before
  pushing.
- The pages disagree about `.env`. `getting-started/installing-haute.md`:22 says to fill it
  in; `databricks.md`:127 says no credentials are needed on a laptop, though :108 reads
  `.env` for `haute ci secrets`; `before-you-start.md`:131-133 says it is only for calling the
  endpoint, while the editor loads it for the data-access and MLflow pairs.
- `before-you-start.md` says the platform must provide the staging endpoint and that impact
  needs existing staging and production endpoints (:253, :276-277, :301). On Databricks the
  staging deploy creates `<name>-staging`, and impact treats a missing production endpoint
  as a first deploy.
- `docs/deployment/index.md`:211 says `haute impact` sends quotes in batches, which it does
  only with `[safety] impact_dataset`; the test quotes, the default, go one per request. The
  README (:118) says impact compares "on a sample"; without `impact_dataset` it compares the
  test quotes.

**Plan:** A "new repository" path beside the clone path: create the GitHub repository,
add it as `origin`, first push with `-u`, then `haute ci secrets`. The checklist builds the
pipeline and replaces the placeholder test quote before the first push. One account of
`.env`: which pairs it holds, which commands and the editor read it, and that CI reads
secrets instead. The staging and impact statements say what a Databricks project gets by
itself, and the impact wording says when it batches.

**Acceptance:** Following the guides alone from an empty folder on a scratch GitHub
repository reaches a green first pull request and a staging deploy, with each step's
command as written.

**Dependencies:** None.

**Evidence:** `src/haute/_scaffold.py::starter_pipeline` and `::starter_test_quote`;
`src/haute/deploy/_pruner.py` ("No output node found");
`src/haute/cli/_ci.py::_set_every_secret` (no `--repo`); `src/haute/server.py` (`_load_env` at start);
`src/haute/cli/_impact.py::handle_impact` and `::_impact_records` (one quote per request
without a sample, and a missing production endpoint taken as a first deploy).

### DOC-04 — The site publishes `main` again
**Why:** The site describes `main`: `.github/workflows/docs.yml` deploys it on every push to
`main` that changes `docs/` or `mkdocs.yml`, and the next release, due soon, ships `main`.
PyPI's latest release, 0.6.0 of 5 October 2026, predates the Databricks deploy fixes (403
commits before `7bafedad`). The site has not published since 2 October: that day's run
built, but its deploy job has stayed queued for a runner, holding the `pages` concurrency
group, so every later docs run waits and is cancelled by the next. The published pages are
therefore older than every fix the other packages describe.

**Plan:** With the other packages landed, cancel the queued 2 October run (37021482376) so the
`pages` group is free, and publish `main`, by the next docs push or a dispatch of `docs.yml`,
before the release ships. No workflow change: the site keeps tracking `main`.

**Acceptance:** The published site matches `main` at the release commit, and a later docs
change on `main` publishes without waiting on an older run.

**Dependencies:** DOC-02, DOC-03 and DOC-05, which the site publishes.

**Evidence:** `.github/workflows/docs.yml` (its triggers and `concurrency: pages`);
`.github/workflows/release.yml`; the Deploy docs runs since 2 October 2026.

### DOC-05 — Upgrade notes for a project made by 0.6.0
**Why:** A project `haute init` made with 0.6.0, the release PyPI serves, keeps files that
the next release writes differently, and nothing tells its team what to change on upgrading:
- Its `haute.toml` names `impact_dataset = "data/portfolio_sample.parquet"`, under the
  gitignored `data/`, so CI's impact step stops with "Impact dataset not found". BUG-23 fixed
  that for new projects only: today's scaffold leaves the line commented, and impact
  compares the test quotes.
- Its `[tool.mypy]` block lacks `mypy_path = "rating"` and `explicit_package_bases = true`.
  The pipeline now imports a submodel as `from modules import <name>`, so once the team
  creates one, the generated type-check job fails: mypy cannot find `modules`. A project
  scaffolded before those keys showed this on 25 October 2026, when a submodel was added to
  it.
- Its production workflow tags the release with
  `$(uv run haute status --version-only 2>/dev/null || echo "unknown")`, so a status read
  without credentials tags `deploy/vunknown` (BUG-25), and its other generated steps predate
  the Databricks deploy fixes, until `haute ci update` rewrites them.
- A submodel it registered by file path (`pipeline.submodel("modules/x.py", "x")`) is now
  refused as removed, and never migrated: the pipeline opens degraded, its occurrence
  unavailable and saving disabled, until the file is corrected by hand.

**Plan:** An "Upgrading" page in the deployment guide, linked from the release notes: run
`haute ci update`; remove the 0.6.0 `impact_dataset` line, or commit a sample outside `data/`
and name it; add the two mypy keys; and in each pipeline file that registers a submodel by
path, add `from modules import x` and write `pipeline.submodel(x.submodel, "x")` in place of
the path, keeping the occurrence's name, then reload it (the correction
`docs/building-models/recovering-a-pipeline.md` already gives). Each step says how to tell
whether it applies.

**Acceptance:** A project made by `haute init` 0.6.0, with a path-registered submodel and
the scaffolded `impact_dataset`, upgraded by following the page alone, passes its generated
lint, type-check, test and validation jobs, and its staging deploy's smoke and impact steps.

**Dependencies:** None; DOC-04 publishes it.

**Evidence:** `git show v0.6.0:src/haute/_scaffold.py` (the `impact_dataset` line and the
tag step) and `git show v0.6.0:src/haute/cli/_init_cmd.py` (`_MYPY_BLOCK` without the two
keys); `src/haute/cli/_init_cmd.py::_MYPY_BLOCK`; `src/haute/_parser_submodels.py` (the
refusal of a path-registered submodel) and
`tests/test_pipeline_repair_actions.py::test_a_registration_by_file_path_is_unavailable_and_names_the_import_form`
(no migration); `tests/test_cli_impact.py::TestImpactWithoutAPortfolioSample`.
