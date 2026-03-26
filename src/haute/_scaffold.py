"""Scaffold templates for ``haute init``.

Each function returns a string for a specific file, parameterised by
project name, deploy target, and CI provider.
"""

from __future__ import annotations

from collections.abc import Callable

# ── Per-target configuration ─────────────────────────────────────────
#
# Central registry of every deploy target.  Each entry carries:
#   label        – human-readable name for .env.example header
#   env_body     – literal body appended after the .env.example header
#   secrets      – ordered list of CI secret / env-var names
#   toml_section – callable(name) -> TOML string for [deploy.*] block

_TargetConfig = dict[str, str | list[str] | Callable[[str], str]]

TARGETS: dict[str, _TargetConfig] = {
    "databricks": {
        "label": "Databricks",
        "env_body": """
# General credentials — data warehouse + MLflow tracking
DATABRICKS_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_TOKEN=your_databricks_token_here

# Production serving endpoint credentials
DATABRICKS_RATING_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_RATING_TOKEN=your_databricks_token_here
""",
        "secrets": [
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
base_image = "python:3.11-slim"
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
base_image = "python:3.11-slim"

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
base_image = "python:3.11-slim"

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
base_image = "python:3.11-slim"

[deploy.gcp-run]
project = ""
region = "europe-west1"
service = "{name}"
"""
        ),
    },
    "sagemaker": {
        "label": "AWS SageMaker",
        "env_body": """
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=eu-west-1
SAGEMAKER_ROLE_ARN=arn:aws:iam::123456789012:role/SageMakerRole
""",
        "secrets": [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_DEFAULT_REGION",
            "SAGEMAKER_ROLE_ARN",
        ],
        "toml_section": lambda name: (
            """\
[deploy.sagemaker]
region = "eu-west-1"
instance_type = "ml.m5.large"
initial_instance_count = 1
"""
        ),
    },
    "azure-ml": {
        "label": "Azure ML",
        "env_body": """
AZURE_SUBSCRIPTION_ID=
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
""",
        "secrets": [
            "AZURE_SUBSCRIPTION_ID",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
        ],
        "toml_section": lambda name: (
            """\
[deploy.azure-ml]
resource_group = ""
workspace_name = ""
instance_type = "Standard_DS3_v2"
instance_count = 1
"""
        ),
    },
}


def _get_target(target: str) -> _TargetConfig:
    """Look up *target* in :data:`TARGETS`, raising on unknown names."""
    try:
        return TARGETS[target]
    except KeyError:
        msg = f"Unknown target: {target}"
        raise ValueError(msg) from None


# ── haute.toml ────────────────────────────────────────────────────────


def haute_toml(name: str, target: str, ci: str) -> str:
    """Generate ``haute.toml`` with only the relevant target section."""
    min_approvers = 2

    sections = [
        f"""\
[project]
name = "{name}"
pipeline = "rating/main.py"

[deploy]
target = "{target}"
model_name = "{name}"
endpoint_name = "{name}"
""",
        _target_section(name, target),
        f"""\
[test_quotes]
dir = "tests/quotes"

[safety]
impact_dataset = "data/portfolio_sample.parquet"

[safety.approval]
min_approvers = {min_approvers}

[ci]
provider = "{ci}"

[ci.staging]
endpoint_suffix = "-staging"
""",
    ]
    return "\n".join(sections)


def _target_section(name: str, target: str) -> str:
    cfg = _get_target(target)
    fn = cfg["toml_section"]
    assert callable(fn)
    return fn(name)


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
    return _ENV_EXAMPLE_HEADER.format(label=label) + env_body


# ── CI secrets helpers ────────────────────────────────────────────────


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


def _azure_devops_secrets_env(target: str) -> str:
    """Return the env: block for Azure DevOps pipeline secrets, indented for YAML."""
    return _format_secrets(
        target,
        indent="              ",
        fmt="{key}: $({key})",
    )


# ── GitHub Actions: ci.yml ────────────────────────────────────────────


def github_ci_yml() -> str:
    """PR checks workflow for GitHub Actions."""
    return """\
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


def github_deploy_yml(target: str) -> str:
    """Merge-to-main deploy workflow for GitHub Actions.

    Runs automatically: validate → staging → smoke test → impact analysis.
    Production is a separate manual workflow (``deploy-production.yml``)
    so it works on GitHub Free without environment protection rules.

    The impact-analysis job outputs the deployed git SHA so the
    production workflow can verify it is deploying exactly what was tested.
    """
    secrets_env = _github_secrets_env(target)

    return f"""\
name: Deploy

on:
  push:
    branches: [main]
    paths:
      - "rating/**"
      - "haute.toml"
      - "data/**"
      - "models/**"
      - "tests/quotes/**"
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
    timeout-minutes: 15
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
        run: echo "Staged commit: $GITHUB_SHA" >> "$GITHUB_STEP_SUMMARY"
"""


def github_deploy_prod_yml(target: str) -> str:
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

    return f"""\
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
    timeout-minutes: 15
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
        run: |
          set -euo pipefail
          VERSION=$(uv run haute status --version-only 2>/dev/null || echo "unknown")
          git tag "deploy/v$VERSION"
          git push origin "deploy/v$VERSION"
"""


# ── GitLab CI ────────────────────────────────────────────────────────


def gitlab_ci_yml(target: str) -> str:
    """Combined CI + deploy pipeline for GitLab CI/CD.

    Uses GitLab stages to enforce ordering.  The ``deploy-production``
    job uses ``when: manual`` so a reviewer must click to approve.

    Credentials are only available in deploy/smoke/impact/production
    jobs (protected-branch jobs), not in the MR validation job.
    """
    secrets_env = _gitlab_secrets_env(target)

    return f"""\
stages:
  - validate
  - deploy-staging
  - smoke-test
  - impact-analysis
  - deploy-production

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

# ── Staging ───────────────────────────────────────────────────
deploy-staging:
  stage: deploy-staging
  timeout: 15 minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
    - uv run haute deploy --endpoint-suffix "-staging"
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

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

# ── Production (manual approval) ─────────────────────────────
deploy-production:
  stage: deploy-production
  timeout: 15 minutes
  resource_group: deploy
  variables:
{secrets_env}
  script:
    - uv run haute deploy
    - |
      VERSION=$(uv run haute status --version-only 2>/dev/null || echo "unknown")
      git tag "deploy/v$VERSION"
      git push origin "deploy/v$VERSION"
  when: manual
  allow_failure: false
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
"""


# ── Azure DevOps ─────────────────────────────────────────────────────


def azure_devops_yml(target: str) -> str:
    """Combined CI + deploy pipeline for Azure DevOps.

    Uses stages to enforce ordering.  The ``DeployProduction`` stage
    uses an Environment with approval checks so a reviewer must approve
    before production deployment proceeds.

    Credentials are read from an Azure DevOps variable group named
    ``haute-credentials`` - the env var names are identical to other
    CI providers.
    """
    secrets_env = _azure_devops_secrets_env(target)

    return f"""\
trigger:
  branches:
    include: [main]
  paths:
    include:
      - rating/
      - haute.toml
      - data/
      - models/
      - tests/quotes/

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
        timeoutInMinutes: 15
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

  # ── Deploy to production (manual approval) ───────────────────
  - stage: DeployProduction
    displayName: Deploy → Production
    dependsOn: ImpactAnalysis
    variables:
      - group: haute-credentials
    jobs:
      - deployment: deploy_production
        displayName: Deploy to production
        timeoutInMinutes: 15
        pool:
          vmImage: ubuntu-latest
        environment: production
        strategy:
          runOnce:
            deploy:
              steps:
                - checkout: self
                - task: UsePythonVersion@0
                  inputs:
                    versionSpec: "3.11"
                - script: pip install "uv>=0.5,<1" && uv sync --frozen
                  displayName: Install dependencies
                - script: uv run haute deploy
                  displayName: Deploy production
                  env:
{secrets_env}
                - script: |
                    set -euo pipefail
                    VERSION=$(uv run haute status --version-only 2>/dev/null || echo "unknown")
                    git tag "deploy/v$VERSION"
                    git push origin "deploy/v$VERSION"
                  displayName: Tag release
"""


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
    """Generate the starter ``main.py`` pipeline."""
    return f'''\
"""Pipeline: {name}"""

import polars as pl
import haute

from utility.features import (
    clean_columns,
    to_date,
    years_between,
    months_between,
    days_between,
    postcode_area,
    cols_matching,
)

pipeline = haute.Pipeline("{name}", description="")
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
    """Generate a starter ``tests/test_pipeline.py`` so pytest passes out of the box."""
    return f'''\
"""Starter tests for {name}."""

from pathlib import Path


def test_pipeline_parses():
    """Pipeline file is valid Python and contains a haute Pipeline."""
    pipeline_path = Path(__file__).resolve().parent.parent / "rating" / "main.py"
    source = pipeline_path.read_text()
    compile(source, str(pipeline_path), "exec")
    assert "haute.Pipeline" in source
'''


def starter_test_quote() -> str:
    """Generate the starter ``tests/quotes/example.json``."""
    return """\
[
  {
    "_description": "Example quote - replace with your own fields",
    "id": 1,
    "field_a": "value",
    "field_b": 42
  }
]
"""
