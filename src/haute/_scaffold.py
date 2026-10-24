"""Scaffold templates for ``haute init``.

Each function returns a string for a specific file, parameterised by
project name, deploy target, and CI provider.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath

from haute._project_python import DEFAULT_PYTHON, container_base_image
from haute._test_quotes import DEFAULT_TEST_QUOTES_DIR
from haute._workbench_config import DEFAULT_FORM_PATH
from haute._workbench_form import blank_form, render_form

# ── Per-target configuration ─────────────────────────────────────────
#
# Central registry of every deploy target ``haute init`` offers.  Each entry carries:
#   label        – human-readable name for .env.example header
#   env_body     – literal body appended after the .env.example header
#   secrets      – ordered list of CI secret / env-var names
#   toml_section – callable(name) -> TOML string for [deploy.*] block

_TargetConfig = dict[str, str | list[str] | Callable[[str], str]]

TARGETS: dict[str, _TargetConfig] = {
    "databricks": {
        "label": "Databricks",
        "env_body": """
# Data access credentials — SQL warehouse reads and writes, workspace browsing
DATABRICKS_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_TOKEN=your_databricks_token_here

# MLflow credentials — experiment tracking, the model registry, loading logged
# models, and deploy's model registration. MLflow never uses the data access
# pair above; copy its values here if one token's scopes cover both.
# (Alternatively set MLFLOW_TRACKING_URI=databricks://<profile> to use a profile.)
DATABRICKS_MLFLOW_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_MLFLOW_TOKEN=your_databricks_mlflow_token_here

# Production serving endpoint credentials
DATABRICKS_RATING_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_RATING_TOKEN=your_databricks_token_here
""",
        "secrets": [
            "DATABRICKS_MLFLOW_HOST",
            "DATABRICKS_MLFLOW_TOKEN",
            "DATABRICKS_RATING_HOST",
            "DATABRICKS_RATING_TOKEN",
        ],
        "toml_section": lambda name: (
            f"""\
[deploy.databricks]
experiment_name = "/Shared/haute/{name}"
catalog = "main"
schema = "pricing"
serving_workload_size = "Small"
serving_scale_to_zero = true
"""
        ),
    },
    "container": {
        "label": "Container registry",
        "env_body": """
DOCKER_USERNAME=
DOCKER_PASSWORD=
""",
        "secrets": [
            "DOCKER_USERNAME",
            "DOCKER_PASSWORD",
        ],
        "toml_section": lambda name: (
            """\
[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"
"""
        ),
    },
    "azure-container-apps": {
        "label": "Azure Container Apps",
        "env_body": """
DOCKER_USERNAME=
DOCKER_PASSWORD=
AZURE_SUBSCRIPTION_ID=
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
""",
        "secrets": [
            "DOCKER_USERNAME",
            "DOCKER_PASSWORD",
            "AZURE_SUBSCRIPTION_ID",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
        ],
        "toml_section": lambda name: (
            f"""\
[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"

[deploy.azure-container-apps]
resource_group = ""
container_app_name = "{name}"
environment_name = ""
"""
        ),
    },
    "aws-ecs": {
        "label": "AWS ECS",
        "env_body": """
DOCKER_USERNAME=
DOCKER_PASSWORD=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=eu-west-1
""",
        "secrets": [
            "DOCKER_USERNAME",
            "DOCKER_PASSWORD",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_DEFAULT_REGION",
        ],
        "toml_section": lambda name: (
            f"""\
[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"

[deploy.aws-ecs]
region = "eu-west-1"
cluster = ""
service = "{name}"
"""
        ),
    },
    "gcp-run": {
        "label": "GCP Cloud Run",
        "env_body": """
DOCKER_USERNAME=
DOCKER_PASSWORD=
GCP_PROJECT_ID=
GCP_SERVICE_ACCOUNT_KEY=
""",
        "secrets": [
            "DOCKER_USERNAME",
            "DOCKER_PASSWORD",
            "GCP_PROJECT_ID",
            "GCP_SERVICE_ACCOUNT_KEY",
        ],
        "toml_section": lambda name: (
            f"""\
[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"

[deploy.gcp-run]
project = ""
region = "europe-west1"
service = "{name}"
"""
        ),
    },
}


# Container platforms whose service update is not implemented: ``haute deploy``
# builds and pushes the image and finishes without updating the service. The same
# set as ``haute.deploy._CONTAINER_PLATFORM_TARGETS`` (a test keeps them equal).
BUILD_AND_PUSH_ONLY_TARGETS = ("azure-container-apps", "aws-ecs", "gcp-run")


# Targets whose deploy builds a container image. ``haute status`` reads the
# MLflow registry, so these have no registered version to tag a release with.
_CONTAINER_BASED_TARGETS = ("container", *BUILD_AND_PUSH_ONLY_TARGETS)


# What a release is built from: a push to main that changes none of these deploys
# nothing. The dependency files change what the deployed image or model installs.
#: The pipeline file a scaffolded project's ``haute.toml`` names.
PIPELINE_FILE = "rating/main.py"
#: The folders a scaffolded project's release is read from: the pipeline folder, which
#: holds its code, config and versioned files (``files/data``, ``files/models``), and the
#: test quotes. The root's ``data/`` is ignored and its ``models/`` is for a pipeline at
#: the root, which a scaffold never makes, so neither changes a release.
_RELEASE_INPUT_DIRS = (PurePosixPath(PIPELINE_FILE).parent.as_posix(), DEFAULT_TEST_QUOTES_DIR)
_RELEASE_INPUT_FILES = ("haute.toml", "pyproject.toml", "uv.lock")


def _release_input_dirs(workbench: bool) -> tuple[str, ...]:
    """The release's input directories; a workbench project's form directory with them."""
    if not workbench:
        return _RELEASE_INPUT_DIRS
    return (*_RELEASE_INPUT_DIRS, PurePosixPath(DEFAULT_FORM_PATH).parent.as_posix())


#: A workbench project's report of what a pull request does to its test quotes: posted on
#: the pull request (editing the last one posted) and added to the run's summary.
_GITHUB_TEST_QUOTES_JOB = """
  test-quotes:
    name: Test Quotes
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Report what the change does to the test quotes
        run: >-
          uv run haute test-quotes report --base "origin/${{ github.base_ref }}"
          --output test-quotes-report.md
      - name: Add the report to the run's summary
        run: cat test-quotes-report.md >> "$GITHUB_STEP_SUMMARY"
      # A fork's pull request runs with a token that cannot comment.
      - name: Post the report on the pull request
        if: github.event.pull_request.head.repo.full_name == github.repository
        env:
          GH_TOKEN: ${{ github.token }}
          PR: ${{ github.event.pull_request.number }}
        run: gh pr comment "$PR" --body-file test-quotes-report.md --edit-last --create-if-none
"""

#: The same on a GitLab merge request, kept with the job as an artifact.
_GITLAB_TEST_QUOTES_JOB = """
# ── Test quotes (a merge request's report) ────────────────────
test-quotes-report:
  stage: validate
  timeout: 10 minutes
  # The whole history, so the report finds where the branch left its target.
  variables:
    GIT_DEPTH: "0"
  script:
    - git fetch origin "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    - >-
      uv run haute test-quotes report --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
      --output test-quotes-report.md
    - cat test-quotes-report.md
  artifacts:
    paths:
      - test-quotes-report.md
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
"""

#: The same on an Azure DevOps pull request, published as an artifact.
_AZURE_TEST_QUOTES_JOB = """
      - job: test_quotes
        displayName: Test Quotes Report
        timeoutInMinutes: 10
        condition: eq(variables['Build.Reason'], 'PullRequest')
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
            fetchDepth: 0
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: |
              base="origin/${SYSTEM_PULLREQUEST_TARGETBRANCH#refs/heads/}"
              uv run haute test-quotes report --base "$base" --output test-quotes-report.md
              cat test-quotes-report.md
            displayName: Report what the change does to the test quotes
          - publish: test-quotes-report.md
            artifact: test-quotes-report
"""


#: The Python a generated CI job runs when the project names none (``haute init`` passes
#: the project's own, ``haute._project_python``). The templates below are written with
#: it, and ``_pin_ci_python`` swaps in the project's.
DEFAULT_CI_PYTHON = DEFAULT_PYTHON

# The base image a container target's template is written with; ``haute_toml`` swaps in
# one on the project's Python.
_TEMPLATE_BASE_IMAGE = 'base_image = "python:3.11.9-slim"'

# Every form a template pins the job's Python in: setup-uv, Azure's UsePythonVersion and
# GitLab's job image.
_CI_PYTHON_PINS = ('python-version: "{}"', 'versionSpec: "{}"', "image: python:{}\n")


def _pin_ci_python(text: str, python_version: str) -> str:
    """*text* with every job's Python pin moved from the default to *python_version*."""
    for pin in _CI_PYTHON_PINS:
        text = text.replace(pin.format(DEFAULT_CI_PYTHON), pin.format(python_version))
    return text


def _github_trigger_paths(workbench: bool) -> str:
    """The deploy workflow's ``on.push.paths`` entries, one per release input."""
    dirs = [f'      - "{name}/**"' for name in _release_input_dirs(workbench)]
    files = [f'      - "{name}"' for name in _RELEASE_INPUT_FILES]
    return "\n".join([*dirs, *files])


def _azure_devops_trigger_paths(workbench: bool) -> str:
    """The pipeline's ``trigger.paths.include`` entries, one per release input."""
    dirs = [f"      - {name}/" for name in _release_input_dirs(workbench)]
    files = [f"      - {name}" for name in _RELEASE_INPUT_FILES]
    return "\n".join([*dirs, *files])


def _release_tag_script(target: str, indent: str) -> str:
    """Shell lines, indented, that tag the production release in git.

    Databricks tags the registered MLflow model version, which ``haute status`` reads
    from the registry with the MLflow credentials the step is given; a read that fails,
    or prints anything but a version number, fails the step rather than tagging a
    made-up version. A container-based target tags the deployed commit, the identity
    its image tag carries; a redeploy of a commit that is already tagged leaves the tag
    as it is.
    """
    if target in _CONTAINER_BASED_TARGETS:
        lines = [
            'TAG="deploy/$(git rev-parse --short HEAD)"',
            'if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null; then',
            '  echo "Release tag $TAG already exists."',
            "else",
            '  git tag "$TAG"',
            '  git push origin "$TAG"',
            "fi",
        ]
    else:
        lines = [
            'VERSION="$(uv run haute status --version-only)"',
            'case "$VERSION" in',
            "  ''|*[!0-9]*)",
            '    echo "haute status printed no model version: $VERSION" >&2',
            "    exit 1",
            "    ;;",
            "esac",
            'git tag "deploy/v$VERSION"',
            'git push origin "deploy/v$VERSION"',
        ]
    return "\n".join(indent + line for line in lines)


def _deploy_timeout_minutes(target: str) -> int:
    """Minutes a generated deploy job may run.

    A Databricks deploy waits until its endpoint serves the new version
    (``haute.deploy._databricks_serving.SERVING_READY_TIMEOUT_SECONDS``, 45 minutes), and
    a first deploy's build can take most of that; a container deploy builds and pushes
    an image.
    """
    return 60 if target == "databricks" else 15


def _get_target(target: str) -> _TargetConfig:
    """Look up *target* in :data:`TARGETS`, raising on unknown names."""
    try:
        return TARGETS[target]
    except KeyError:
        msg = f"Unknown target: {target}"
        raise ValueError(msg) from None


def _build_only_notice(target: str) -> str:
    """The comment that labels a build-and-push-only target in its generated files.

    Empty for a target that deploys end to end.
    """
    if target not in BUILD_AND_PUSH_ONLY_TARGETS:
        return ""
    return (
        f"# Build and push only: for {_get_target(target)['label']}, `haute deploy` builds the\n"
        "# scoring image, pushes it to the registry [deploy.container] names (required),\n"
        "# and finishes without updating the service, which is not implemented yet.\n"
        "# Point the service at the image yourself; the deploy output names the image tag.\n"
        "# The generated CI stops after the push: a smoke test or impact analysis would\n"
        "# test the service before it runs the new image, so run `haute smoke` and\n"
        "# `haute impact` once it does.\n"
    )


# ── haute.toml ────────────────────────────────────────────────────────


def haute_toml(
    name: str,
    target: str,
    ci: str,
    workbench: bool = False,
    python_version: str = DEFAULT_PYTHON,
) -> str:
    """Generate ``haute.toml`` with only the relevant target section.

    A container target's base image runs *python_version*, the project's Python.

    With *workbench* the ``[workbench]`` table switches the project's workbench on
    (specs/workbench); without it the project has no workbench.
    """
    min_approvers = 2

    sections = [
        f"""\
[project]
name = "{name}"
pipeline = "{PIPELINE_FILE}"

[deploy]
target = "{target}"
model_name = "{name}"
endpoint_name = "{name}"
""",
        _target_section(name, target).replace(
            _TEMPLATE_BASE_IMAGE, f'base_image = "{container_base_image(python_version)}"'
        ),
        f"""\
[test_quotes]
dir = "tests/quotes"

[safety]
# `haute impact` compares staging with production on these quotes. Without a
# sample it compares the test quotes; commit a Parquet sample of your book (not
# under data/, which is gitignored) and name it here to compare that instead:
# impact_dataset = "tests/impact/portfolio_sample.parquet"

[safety.approval]
min_approvers = {min_approvers}

[ci]
provider = "{ci}"

[ci.staging]
endpoint_suffix = "-staging"
""",
    ]
    if workbench:
        sections.append(
            f"""\
[workbench]
enabled = true
form = "{DEFAULT_FORM_PATH}"
"""
        )
    return "\n".join(sections)


def _target_section(name: str, target: str) -> str:
    cfg = _get_target(target)
    fn = cfg["toml_section"]
    assert callable(fn)
    return _build_only_notice(target) + fn(name)


# ── .env.example ──────────────────────────────────────────────────────

_ENV_EXAMPLE_HEADER = """\
# Haute - {label} credentials
# Copy this file to .env and fill in your values.
# .env is gitignored and will never be committed.
#
#   cp .env.example .env
"""


def env_example(target: str) -> str:
    """Generate ``.env.example`` with only the credentials for the chosen target."""
    cfg = _get_target(target)
    label = cfg["label"]
    assert isinstance(label, str)
    env_body = cfg["env_body"]
    assert isinstance(env_body, str)
    notice = _build_only_notice(target)
    return _ENV_EXAMPLE_HEADER.format(label=label) + (f"#\n{notice}" if notice else "") + env_body


def env_example_values(target: str) -> dict[str, str]:
    """The placeholder value ``.env.example`` gives each variable of *target*.

    ``haute ci secrets`` refuses a value still equal to its placeholder, so an unedited
    copy of the template is never uploaded as a CI secret.
    """
    env_body = _get_target(target)["env_body"]
    assert isinstance(env_body, str)
    values: dict[str, str] = {}
    for line in env_body.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


# ── CI secrets helpers ────────────────────────────────────────────────


def ci_secret_names(target: str) -> list[str]:
    """The CI secrets *target*'s generated deploy, smoke, impact and tag steps read."""
    secrets = _get_target(target)["secrets"]
    assert isinstance(secrets, list)
    return list(secrets)


def _format_secrets(target: str, indent: str, fmt: str) -> str:
    """Build an indented env-var block for any CI provider.

    *fmt* is a format string with ``{key}`` placeholder, e.g.
    ``"{key}: ${{{{ secrets.{key} }}}}"`` for GitHub Actions.
    """
    cfg = _get_target(target)
    secrets = cfg["secrets"]
    assert isinstance(secrets, list)
    lines = [f"{indent}{fmt.format(key=s)}" for s in secrets]
    return "\n".join(lines)


def _github_secrets_env(target: str) -> str:
    """Return the env: block for GitHub Actions secrets, indented for YAML."""
    return _format_secrets(
        target,
        indent="          ",
        fmt="{key}: ${{{{ secrets.{key} }}}}",
    )


def _gitlab_secrets_env(target: str) -> str:
    """Return the variables: block for GitLab CI, indented for YAML."""
    return _format_secrets(
        target,
        indent="    ",
        fmt="{key}: ${key}",
    )


def _azure_devops_secrets_env(target: str, indent: str = "              ") -> str:
    """Return the env: block for Azure DevOps pipeline secrets, indented for YAML.

    The block nests under ``env:`` at more than one depth. Job-level steps place
    ``env:`` at 12 spaces (keys must sit at 14), while the ``DeployProduction``
    deployment strategy nests ``env:`` at 18 spaces (keys must sit at 20). The
    caller supplies the matching *indent* so the emitted keys are always MORE
    indented than their parent ``env:`` — sharing one indent across both depths
    produces YAML whose production secrets are under-indented and unparseable.
    """
    return _format_secrets(
        target,
        indent=indent,
        fmt="{key}: $({key})",
    )


# ── GitHub Actions: ci.yml ────────────────────────────────────────────


def github_ci_yml(*, workbench: bool = False, python_version: str = DEFAULT_CI_PYTHON) -> str:
    """:func:`_github_ci_yml` with every job's Python pinned to *python_version*."""
    return _pin_ci_python(_github_ci_yml(workbench=workbench), python_version)


def _github_ci_yml(*, workbench: bool = False) -> str:
    """PR checks workflow for GitHub Actions; a workbench project's also reports what the
    change does to its test quotes, on the pull request."""
    jobs = """\
name: CI

on:
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  lint:
    name: Lint & Format
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .

  typecheck:
    name: Type Check
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - run: uv run mypy .

  test:
    name: Test
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - run: uv run pytest -v

  pipeline-validate:
    name: Pipeline Validation
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Lint pipeline
        run: uv run haute lint
      - name: Dry-run deploy (score test quotes)
        run: uv run haute deploy --dry-run
"""
    return jobs + (_GITHUB_TEST_QUOTES_JOB if workbench else "")


def github_deploy_yml(
    target: str, *, workbench: bool = False, python_version: str = DEFAULT_CI_PYTHON
) -> str:
    """:func:`_github_deploy_yml` with every job's Python pinned to *python_version*."""
    return _pin_ci_python(_github_deploy_yml(target, workbench=workbench), python_version)


def _github_deploy_yml(target: str, *, workbench: bool = False) -> str:
    """Merge-to-main deploy workflow for GitHub Actions.

    Runs automatically: validate → staging → smoke test → impact analysis.
    Production is a separate manual workflow (``deploy-production.yml``)
    so it works on GitHub Free without environment protection rules.

    The impact-analysis job outputs the deployed git SHA so the
    production workflow can verify it is deploying exactly what was tested.
    A build-and-push-only target stops after staging's image push: there is
    no updated service to smoke-test or compare.
    """
    secrets_env = _github_secrets_env(target)
    verification_jobs = (
        ""
        if target in BUILD_AND_PUSH_ONLY_TARGETS
        else f"""
  smoke-test:
    name: Smoke Test Staging
    needs: deploy-staging
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Score test quotes against staging endpoint
        env:
{secrets_env}
        run: uv run haute smoke --endpoint-suffix "-staging"

  impact-analysis:
    name: Impact Analysis
    needs: smoke-test
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Compare staging vs production predictions
        env:
{secrets_env}
        run: uv run haute impact --endpoint-suffix "-staging"
      - name: Upload impact report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: impact-report
          path: impact_report.md
      - name: Record deployed SHA
        run: >-
          echo "Staged commit: $GITHUB_SHA" >> "$GITHUB_STEP_SUMMARY"
"""
    )

    return (
        _build_only_notice(target)
        + f"""\
name: Deploy

on:
  push:
    branches: [main]
    paths:
{_github_trigger_paths(workbench)}
  workflow_dispatch:

concurrency:
  group: deploy
  cancel-in-progress: false

jobs:
  validate:
    name: Validate
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Lint
        run: uv run ruff check .
      - name: Type check
        run: uv run mypy .
      - name: Test
        run: uv run pytest -v
      - name: Pipeline lint
        run: uv run haute lint
      - name: Dry-run deploy
        run: uv run haute deploy --dry-run

  deploy-staging:
    name: Deploy → Staging
    needs: validate
    runs-on: ubuntu-latest
    timeout-minutes: {_deploy_timeout_minutes(target)}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Deploy to staging
        env:
{secrets_env}
        run: uv run haute deploy --endpoint-suffix "-staging"
"""
        + verification_jobs
    )


def github_deploy_prod_yml(target: str, *, python_version: str = DEFAULT_CI_PYTHON) -> str:
    """:func:`_github_deploy_prod_yml` with every job's Python pinned to *python_version*."""
    return _pin_ci_python(_github_deploy_prod_yml(target), python_version)


def _github_deploy_prod_yml(target: str) -> str:
    """Manual production deploy workflow for GitHub Actions.

    Triggered via the GitHub Actions UI (workflow_dispatch) after
    reviewing the impact report from the deploy workflow.
    Works on GitHub Free - no environment protection rules needed.

    Accepts a ``sha`` input so the deployer can pin the exact commit
    that was staged and impact-analysed.  The workflow verifies that the
    checked-out HEAD matches, preventing accidental deployment of
    untested code.
    """
    secrets_env = _github_secrets_env(target)
    release_tag = _release_tag_script(target, " " * 10)
    # A Databricks release tag reads the registered version from MLflow.
    tag_env = "" if target in _CONTAINER_BASED_TARGETS else f"        env:\n{secrets_env}\n"

    return (
        _build_only_notice(target)
        + f"""\
name: Deploy → Production

on:
  workflow_dispatch:
    inputs:
      sha:
        description: >
          Git SHA that was staged & impact-analysed.
          Leave blank to deploy current HEAD of main (less safe).
        required: false
        type: string

concurrency:
  group: deploy
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  deploy-production:
    name: Deploy → Production
    runs-on: ubuntu-latest
    timeout-minutes: {_deploy_timeout_minutes(target)}
    steps:
      - uses: actions/checkout@v4
      - name: Verify commit matches staged SHA
        if: inputs.sha != ''
        run: |
          if [ "$GITHUB_SHA" != "${{{{ inputs.sha }}}}" ]; then
            echo "::error::HEAD ($GITHUB_SHA) does not match" \\
              "staged SHA (${{{{ inputs.sha }}}}). Merge may have occurred since staging."
            exit 1
          fi
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --frozen
      - name: Deploy to production
        env:
{secrets_env}
        run: uv run haute deploy
      - name: Tag release
{tag_env}        run: |
          set -euo pipefail
{release_tag}
"""
    )


# ── GitLab CI ────────────────────────────────────────────────────────


def gitlab_ci_yml(
    target: str, *, workbench: bool = False, python_version: str = DEFAULT_CI_PYTHON
) -> str:
    """:func:`_gitlab_ci_yml` with every job's Python pinned to *python_version*."""
    return _pin_ci_python(_gitlab_ci_yml(target, workbench=workbench), python_version)


def _gitlab_ci_yml(target: str, *, workbench: bool = False) -> str:
    """Combined CI + deploy pipeline for GitLab CI/CD.

    Uses GitLab stages to enforce ordering.  The ``deploy-production``
    job uses ``when: manual`` so a reviewer must click to approve.

    Credentials are only available in deploy/smoke/impact/production
    jobs (protected-branch jobs), not in the MR validation job.
    """
    secrets_env = _gitlab_secrets_env(target)
    release_tag = _release_tag_script(target, " " * 6)
    verify = target not in BUILD_AND_PUSH_ONLY_TARGETS
    verification_stages = "  - smoke-test\n  - impact-analysis\n" if verify else ""
    verification_jobs = (
        f"""\
# ── Smoke test ────────────────────────────────────────────────
smoke-test:
  stage: smoke-test
  timeout: 10 minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
    - uv run haute smoke --endpoint-suffix "-staging"
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

# ── Impact analysis ──────────────────────────────────────────
impact-analysis:
  stage: impact-analysis
  timeout: 10 minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
    - uv run haute impact --endpoint-suffix "-staging"
  artifacts:
    paths:
      - impact_report.md
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

"""
        if verify
        else ""
    )

    return (
        _build_only_notice(target)
        + f"""\
stages:
  - validate
  - deploy-staging
{verification_stages}  - deploy-production

default:
  image: python:3.11
  cache:
    key: uv-$CI_COMMIT_REF_SLUG
    paths:
      - .cache/uv
  before_script:
    - pip install "uv>=0.5,<1" && uv sync --frozen

# ── Validate ──────────────────────────────────────────────────
lint:
  stage: validate
  timeout: 10 minutes
  script:
    - uv run ruff check .
    - uv run mypy .
    - uv run pytest -v
    - uv run haute lint
    - uv run haute deploy --dry-run
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
{_GITLAB_TEST_QUOTES_JOB if workbench else ""}
# ── Staging ───────────────────────────────────────────────────
deploy-staging:
  stage: deploy-staging
  timeout: {_deploy_timeout_minutes(target)} minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
    - uv run haute deploy --endpoint-suffix "-staging"
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

{verification_jobs}# ── Production (manual approval) ─────────────────────────────
deploy-production:
  stage: deploy-production
  timeout: {_deploy_timeout_minutes(target)} minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
    - uv run haute deploy
    - |
{release_tag}
  when: manual
  allow_failure: false
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
"""
    )


# ── Azure DevOps ─────────────────────────────────────────────────────


def azure_devops_yml(
    target: str, *, workbench: bool = False, python_version: str = DEFAULT_CI_PYTHON
) -> str:
    """:func:`_azure_devops_yml` with every job's Python pinned to *python_version*."""
    return _pin_ci_python(_azure_devops_yml(target, workbench=workbench), python_version)


def _azure_devops_yml(target: str, *, workbench: bool = False) -> str:
    """Combined CI + deploy pipeline for Azure DevOps.

    Uses stages to enforce ordering.  The ``DeployProduction`` stage
    uses an Environment with approval checks so a reviewer must approve
    before production deployment proceeds.

    Credentials are read from an Azure DevOps variable group named
    ``haute-credentials`` - the env var names are identical to other
    CI providers.
    """
    secrets_env = _azure_devops_secrets_env(target)
    # The DeployProduction deployment strategy nests ``env:`` at 18 spaces, so
    # its secret keys must sit at 20 — deeper than the 14-space job-level block.
    secrets_env_production = _azure_devops_secrets_env(target, indent=" " * 20)
    release_tag = _release_tag_script(target, " " * 20)
    # A Databricks release tag reads the registered version from MLflow.
    tag_env = (
        ""
        if target in _CONTAINER_BASED_TARGETS
        else f"                  env:\n{secrets_env_production}\n"
    )
    verify = target not in BUILD_AND_PUSH_ONLY_TARGETS
    production_depends_on = "ImpactAnalysis" if verify else "DeployStaging"
    verification_stages = (
        f"""\
  # ── Smoke test staging ───────────────────────────────────────
  - stage: SmokeTest
    displayName: Smoke Test Staging
    dependsOn: DeployStaging
    variables:
      - group: haute-credentials
    jobs:
      - job: smoke_test
        displayName: Score test quotes against staging
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run haute smoke --endpoint-suffix "-staging"
            displayName: Smoke test
            env:
{secrets_env}

  # ── Impact analysis ──────────────────────────────────────────
  - stage: ImpactAnalysis
    displayName: Impact Analysis
    dependsOn: SmokeTest
    variables:
      - group: haute-credentials
    jobs:
      - job: impact
        displayName: Compare staging vs production
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run haute impact --endpoint-suffix "-staging"
            displayName: Impact analysis
            env:
{secrets_env}
          - publish: impact_report.md
            artifact: impact-report
            condition: succeededOrFailed()

"""
        if verify
        else ""
    )

    return (
        _build_only_notice(target)
        + f"""\
trigger:
  branches:
    include: [main]
  paths:
    include:
{_azure_devops_trigger_paths(workbench)}

pr:
  branches:
    include: [main]

variables:
  CI: "true"

stages:
  # ── Validate (runs on PR and push to main) ───────────────────
  - stage: Validate
    jobs:
      - job: lint
        displayName: Lint & Format
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run ruff check .
            displayName: Ruff check
          - script: uv run ruff format --check .
            displayName: Ruff format check

      - job: typecheck
        displayName: Type Check
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run mypy .
            displayName: Mypy

      - job: test
        displayName: Test
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run pytest -v
            displayName: Pytest

      - job: pipeline_validate
        displayName: Pipeline Validation
        timeoutInMinutes: 10
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run haute lint
            displayName: Lint pipeline
          - script: uv run haute deploy --dry-run
            displayName: Dry-run deploy
{_AZURE_TEST_QUOTES_JOB if workbench else ""}
  # ── Deploy to staging (only on push to main) ─────────────────
  - stage: DeployStaging
    displayName: Deploy → Staging
    dependsOn: Validate
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))
    variables:
      - group: haute-credentials
    jobs:
      - job: deploy_staging
        displayName: Deploy to staging
        timeoutInMinutes: {_deploy_timeout_minutes(target)}
        pool:
          vmImage: ubuntu-latest
        steps:
          - checkout: self
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.11"
          - script: pip install "uv>=0.5,<1" && uv sync --frozen
            displayName: Install dependencies
          - script: uv run haute deploy --endpoint-suffix "-staging"
            displayName: Deploy staging
            env:
{secrets_env}

{verification_stages}  # ── Deploy to production (manual approval) ───────────────────
  - stage: DeployProduction
    displayName: Deploy → Production
    dependsOn: {production_depends_on}
    variables:
      - group: haute-credentials
    jobs:
      - deployment: deploy_production
        displayName: Deploy to production
        timeoutInMinutes: {_deploy_timeout_minutes(target)}
        pool:
          vmImage: ubuntu-latest
        environment: production
        strategy:
          runOnce:
            deploy:
              steps:
                # The release tag is pushed with the pipeline's token.
                - checkout: self
                  persistCredentials: true
                - task: UsePythonVersion@0
                  inputs:
                    versionSpec: "3.11"
                - script: pip install "uv>=0.5,<1" && uv sync --frozen
                  displayName: Install dependencies
                - script: uv run haute deploy
                  displayName: Deploy production
                  env:
{secrets_env_production}
                - script: |
                    set -euo pipefail
{release_tag}
                  displayName: Tag release
{tag_env}"""
    )


# ── Every CI file of a provider ───────────────────────────────────────


def ci_files(
    target: str, ci: str, *, workbench: bool = False, python_version: str = DEFAULT_CI_PYTHON
) -> dict[str, str]:
    """The CI files ``haute init`` and ``haute ci update`` write for *ci*: path -> content.

    Paths are relative to the project root, in the order they are reported; ``"none"``
    has no files. Every job runs *python_version*, the project's Python.
    """
    if ci == "github":
        return {
            ".github/workflows/ci.yml": github_ci_yml(
                workbench=workbench, python_version=python_version
            ),
            ".github/workflows/deploy-staging.yml": github_deploy_yml(
                target, workbench=workbench, python_version=python_version
            ),
            ".github/workflows/deploy-production.yml": github_deploy_prod_yml(
                target, python_version=python_version
            ),
        }
    if ci == "gitlab":
        return {
            ".gitlab-ci.yml": gitlab_ci_yml(
                target, workbench=workbench, python_version=python_version
            )
        }
    if ci == "azure-devops":
        return {
            "azure-pipelines.yml": azure_devops_yml(
                target, workbench=workbench, python_version=python_version
            )
        }
    if ci == "none":
        return {}
    raise ValueError(f"Unknown CI provider: {ci}")


# ── Pre-commit hook ───────────────────────────────────────────────────


def pre_commit_hook() -> str:
    """Generate a Git pre-commit hook that auto-formats with ruff."""
    return """\
#!/bin/sh
# Haute pre-commit hook - auto-format staged Python files with ruff.
# Installed by `haute init`. To reinstall: cp .githooks/pre-commit .git/hooks/

FILES=$(git diff --cached --name-only --diff-filter=ACM -- '*.py')
[ -z "$FILES" ] && exit 0

uv run ruff format $FILES
uv run ruff check --fix $FILES 2>/dev/null || true
git add $FILES
"""


# ── Starter files ─────────────────────────────────────────────────────


def starter_pipeline(name: str) -> str:
    """Generate a valid, discoverable pipeline with an empty canvas."""
    return f'''\
"""Pipeline: {name}

Blank pipeline scaffolded by ``haute init``. Add nodes in the Haute editor.
"""

import haute

pipeline = haute.Pipeline("{name}")
'''


def starter_utility_init() -> str:
    """Generate ``utility/__init__.py``."""
    return '"""Project-level utilities \u2014 reusable functions for pipeline nodes."""\n'


def starter_utility_features() -> str:
    """Generate ``utility/features.py`` with common Polars utilities."""
    return '''\
"""Feature engineering utilities for pipeline nodes.

Add project-specific utility functions, constants, and column mappings here.
These are imported into main.py so your pipeline nodes stay clean and readable.
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl


# ── Date helpers ──────────────────────────────────────────────────────


def to_date(col_name: str, fmt: str = "%Y-%m-%d") -> pl.Expr:
    """Parse a string column to a date.

    Example::

        to_date("proposer.date_of_birth")
    """
    return pl.col(col_name).str.to_date(fmt)


def years_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Whole years between two date expressions (floor).

    Example::

        years_between(to_date("date_of_birth"), to_date("cover_start_date")).alias("age")
    """
    return ((later - earlier).dt.total_days() / 365.25).floor().cast(pl.Int64)


def months_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Calendar months between two date expressions.

    Uses year/month subtraction so Jan 1 to Jul 1 = 6, not 5.

    Example::

        months_between(to_date("start_date"), to_date("end_date")).alias("tenure_months")
    """
    return (later.dt.year() - earlier.dt.year()) * 12 + (later.dt.month() - earlier.dt.month())


def days_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Days between two date expressions.

    Example::

        days_between(to_date("order_date"), to_date("ship_date")).alias("fulfillment_days")
    """
    return (later - earlier).dt.total_days()


# ── String helpers ────────────────────────────────────────────────────


def postcode_area(col_name: str) -> pl.Expr:
    """Extract the outward code (first part) from a UK postcode.

    ``"SW1A 2AA"`` → ``"SW1A"``, ``"B1 1BB"`` → ``"B1"``

    Example::

        postcode_area("postcode").alias("postcode_area")
    """
    return pl.col(col_name).str.split(" ").list.first()


# ── Column cleaning ──────────────────────────────────────────────────


def clean_columns(df: pl.LazyFrame) -> pl.LazyFrame:
    """Replace every ``.`` with ``_`` in column names.

    Example::

        df = clean_columns(quotes)
    """
    rename = {c: c.replace(".", "_") for c in df.collect_schema().names() if "." in c}
    return df.rename(rename) if rename else df


# ── Column matching ──────────────────────────────────────────────────


def cols_matching(all_cols: list[str], pattern_fn: Callable[[str], bool]) -> list[str]:
    """Return columns from *all_cols* where pattern_fn(col) is True.

    Example::

        age_cols = cols_matching(df.collect_schema().names(),
                                 lambda c: c.endswith("_age"))
    """
    return [c for c in all_cols if pattern_fn(c)]
'''


def starter_test(name: str) -> str:
    """Generate a starter test that validates the pipeline file."""
    return f'''\
"""Starter tests for {name}.

Extend these tests with execution assertions as your pipeline grows.
"""

from __future__ import annotations

from pathlib import Path

from haute.parser import parse_pipeline_file


PIPELINE_FILE = Path(__file__).resolve().parent.parent / "rating" / "main.py"

def test_pipeline_parses() -> None:
    """The pipeline remains valid Haute source."""
    graph = parse_pipeline_file(PIPELINE_FILE)
    assert graph.pipeline_name == "{name}"
'''


def starter_test_quote() -> str:
    """Generate the starter ``tests/quotes/example.json``, a row in the test-quote shape."""
    return """\
[
  {
    "_description": "Example quote - replace the input and add the outputs you expect",
    "input": {
      "id": 1,
      "field_a": "value",
      "field_b": 42
    }
  }
]
"""


def starter_form(name: str) -> str:
    """Generate the workbench's blank ``forms/form.json`` for ``haute init --workbench``."""
    return render_form(blank_form(name))
