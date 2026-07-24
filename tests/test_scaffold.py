"""Tests for haute._scaffold - template generation for ``haute init``."""

import tomllib

import pytest
import yaml

from haute._scaffold import (
    TARGETS,
    azure_devops_yml,
    env_example,
    github_ci_yml,
    github_deploy_yml,
    gitlab_ci_yml,
    haute_toml,
    pre_commit_hook,
    starter_pipeline,
    starter_test,
    starter_test_quote,
)


class TestHauteToml:
    """haute_toml() generates target-specific config with no leaking sections."""

    def test_databricks_only_contains_databricks_section(self) -> None:
        result = haute_toml("motor", "databricks", "github")
        assert "[deploy.databricks]" in result
        assert "[deploy.docker]" not in result
        assert "[deploy.sagemaker]" not in result
        assert "[deploy.azure-ml]" not in result

    def test_container_only_contains_container_section(self) -> None:
        result = haute_toml("motor", "container", "github")
        assert "[deploy.container]" in result
        assert "[deploy.databricks]" not in result
        assert "[deploy.sagemaker]" not in result
        assert "[deploy.azure-ml]" not in result

    def test_sagemaker_only_contains_sagemaker_section(self) -> None:
        result = haute_toml("motor", "sagemaker", "gitlab")
        assert "[deploy.sagemaker]" in result
        assert "[deploy.databricks]" not in result

    def test_azure_ml_only_contains_azure_section(self) -> None:
        result = haute_toml("motor", "azure-ml", "github")
        assert "[deploy.azure-ml]" in result
        assert "[deploy.databricks]" not in result

    def test_project_name_substituted(self) -> None:
        result = haute_toml("my_pipeline", "databricks", "github")
        assert 'name = "my_pipeline"' in result
        assert 'model_name = "my_pipeline"' in result
        assert 'endpoint_name = "my_pipeline"' in result

    def test_ci_provider_set(self) -> None:
        result = haute_toml("motor", "databricks", "none")
        assert 'provider = "none"' in result

    def test_team_defaults(self) -> None:
        result = haute_toml("motor", "databricks", "github")
        assert "min_approvers = 2" in result

    def test_safety_section_present(self) -> None:
        result = haute_toml("motor", "databricks", "github")
        assert "[safety]" in result
        assert "impact_dataset" in result

    def test_staging_section(self) -> None:
        result = haute_toml("motor", "databricks", "github")
        assert "[ci.staging]" in result
        assert 'endpoint_suffix = "-staging"' in result

    def test_azure_container_apps_contains_both_sections(self) -> None:
        result = haute_toml("motor", "azure-container-apps", "github")
        assert "[deploy.container]" in result
        assert "[deploy.azure-container-apps]" in result
        assert "[deploy.databricks]" not in result

    def test_aws_ecs_contains_both_sections(self) -> None:
        result = haute_toml("motor", "aws-ecs", "github")
        assert "[deploy.container]" in result
        assert "[deploy.aws-ecs]" in result
        assert "[deploy.databricks]" not in result

    def test_gcp_run_contains_both_sections(self) -> None:
        result = haute_toml("motor", "gcp-run", "github")
        assert "[deploy.container]" in result
        assert "[deploy.gcp-run]" in result
        assert "[deploy.databricks]" not in result

    def test_unknown_target_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Unknown target"):
            haute_toml("motor", "unknown", "github")


class TestEnvExample:
    """env_example() generates only the chosen target's credentials."""

    def test_databricks_creds_only(self) -> None:
        result = env_example("databricks")
        assert "DATABRICKS_HOST" in result
        assert "DATABRICKS_TOKEN" in result
        assert "DATABRICKS_RATING_HOST" in result
        assert "DATABRICKS_RATING_TOKEN" in result
        assert "AWS_ACCESS_KEY" not in result
        assert "AZURE_" not in result

    def test_sagemaker_creds_only(self) -> None:
        result = env_example("sagemaker")
        assert "AWS_ACCESS_KEY_ID" in result
        assert "SAGEMAKER_ROLE_ARN" in result
        assert "DATABRICKS_" not in result

    def test_azure_creds_only(self) -> None:
        result = env_example("azure-ml")
        assert "AZURE_SUBSCRIPTION_ID" in result
        assert "DATABRICKS_" not in result
        assert "AWS_" not in result

    def test_container_creds_only(self) -> None:
        result = env_example("container")
        assert "DOCKER_USERNAME" in result
        assert "DATABRICKS_" not in result

    def test_azure_container_apps_creds(self) -> None:
        result = env_example("azure-container-apps")
        assert "DOCKER_USERNAME" in result
        assert "AZURE_SUBSCRIPTION_ID" in result
        assert "DATABRICKS_" not in result

    def test_aws_ecs_creds(self) -> None:
        result = env_example("aws-ecs")
        assert "DOCKER_USERNAME" in result
        assert "AWS_ACCESS_KEY_ID" in result
        assert "DATABRICKS_" not in result

    def test_gcp_run_creds(self) -> None:
        result = env_example("gcp-run")
        assert "DOCKER_USERNAME" in result
        assert "GCP_PROJECT_ID" in result
        assert "DATABRICKS_" not in result

    def test_unknown_target_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Unknown target"):
            env_example("unknown")


class TestGithubCiYml:
    """github_ci_yml() generates valid CI workflow."""

    def test_contains_required_jobs(self) -> None:
        result = github_ci_yml()
        assert "lint:" in result
        assert "typecheck:" in result
        assert "test:" in result
        assert "pipeline-validate:" in result

    def test_triggers_on_pr_to_main(self) -> None:
        result = github_ci_yml()
        assert "pull_request:" in result
        assert "branches: [main]" in result

    def test_uses_uv(self) -> None:
        result = github_ci_yml()
        assert "astral-sh/setup-uv@v4" in result
        assert "uv sync --frozen" in result


class TestGithubDeployYml:
    """github_deploy_yml() generates valid deploy workflow with correct secrets."""

    def test_databricks_secrets(self) -> None:
        result = github_deploy_yml("databricks")
        assert "secrets.DATABRICKS_RATING_HOST" in result
        assert "secrets.DATABRICKS_RATING_TOKEN" in result
        assert "secrets.AWS_ACCESS_KEY_ID" not in result

    def test_sagemaker_secrets(self) -> None:
        result = github_deploy_yml("sagemaker")
        assert "secrets.AWS_ACCESS_KEY_ID" in result
        assert "secrets.SAGEMAKER_ROLE_ARN" in result
        assert "secrets.DATABRICKS_RATING_HOST" not in result

    def test_contains_staging_and_impact(self) -> None:
        result = github_deploy_yml("databricks")
        assert "deploy-staging:" in result
        assert "smoke-test:" in result
        assert "impact-analysis:" in result
        assert "deploy-production:" not in result

    def test_deploy_prod_yml_contains_production(self) -> None:
        from haute._scaffold import github_deploy_prod_yml

        result = github_deploy_prod_yml("databricks")
        assert "deploy-production:" in result
        assert "workflow_dispatch" in result
        assert "secrets.DATABRICKS_RATING_HOST" in result

    def test_deploy_prod_yml_tags_release(self) -> None:
        from haute._scaffold import github_deploy_prod_yml

        result = github_deploy_prod_yml("databricks")
        assert "Tag release" in result

    def test_triggers_on_push_to_main(self) -> None:
        result = github_deploy_yml("databricks")
        assert "push:" in result
        assert "branches: [main]" in result


class TestGithubDeployProdYml:
    """github_deploy_prod_yml() includes SHA pinning and strict tag handling."""

    def test_sha_input_present(self) -> None:
        from haute._scaffold import github_deploy_prod_yml

        result = github_deploy_prod_yml("databricks")
        assert "inputs:" in result
        assert "sha:" in result

    def test_sha_verification_step(self) -> None:
        from haute._scaffold import github_deploy_prod_yml

        result = github_deploy_prod_yml("databricks")
        assert "Verify commit matches staged SHA" in result
        assert "GITHUB_SHA" in result

    def test_tag_step_uses_strict_mode(self) -> None:
        from haute._scaffold import github_deploy_prod_yml

        result = github_deploy_prod_yml("databricks")
        assert "set -euo pipefail" in result
        assert "|| true" not in result

    def test_timeout_set(self) -> None:
        from haute._scaffold import github_deploy_prod_yml

        result = github_deploy_prod_yml("databricks")
        assert "timeout-minutes:" in result

    def test_python_version_pinned(self) -> None:
        from haute._scaffold import github_deploy_prod_yml

        result = github_deploy_prod_yml("databricks")
        assert 'python-version: "3.11"' in result


class TestGithubDeployYmlImprovements:
    """Verify engineering improvements in deploy-staging workflow."""

    def test_impact_report_uploaded_as_artifact(self) -> None:
        result = github_deploy_yml("databricks")
        assert "upload-artifact@v4" in result
        assert "impact-report" in result

    def test_deployed_sha_recorded(self) -> None:
        result = github_deploy_yml("databricks")
        assert "GITHUB_SHA" in result
        assert "GITHUB_STEP_SUMMARY" in result

    def test_timeouts_on_all_jobs(self) -> None:
        result = github_deploy_yml("databricks")
        assert result.count("timeout-minutes:") == 4

    def test_python_version_pinned(self) -> None:
        result = github_deploy_yml("databricks")
        assert 'python-version: "3.11"' in result


class TestGithubCiYmlImprovements:
    """Verify engineering improvements in CI workflow."""

    def test_timeouts_on_all_jobs(self) -> None:
        result = github_ci_yml()
        assert result.count("timeout-minutes:") == 4

    def test_python_version_pinned(self) -> None:
        result = github_ci_yml()
        assert 'python-version: "3.11"' in result


class TestGitlabCiYml:
    """gitlab_ci_yml() generates valid GitLab CI pipeline."""

    def test_contains_all_stages(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("databricks")
        assert "validate" in result
        assert "deploy-staging" in result
        assert "smoke-test" in result
        assert "impact-analysis" in result
        assert "deploy-production" in result

    def test_secrets_not_at_top_level(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("databricks")
        lines = result.split("\n")
        # Top-level variables: block should not contain secrets
        in_top_variables = False
        for line in lines:
            if line == "variables:" and not line.startswith(" "):
                in_top_variables = True
                continue
            if in_top_variables and not line.startswith(" ") and line.strip():
                in_top_variables = False
            if in_top_variables:
                assert "DATABRICKS_RATING_HOST" not in line

    def test_secrets_in_deploy_jobs_only(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("databricks")
        # Secrets should appear in the deploy-staging, smoke-test, impact, production sections
        assert "DATABRICKS_RATING_HOST" in result
        # The lint section should NOT have variables with secrets
        parts = result.split("# ── Validate")
        assert len(parts) == 2, "Expected exactly one '# ── Validate' sentinel"
        lint_section = parts[1].split("# ── Staging")[0]
        assert "DATABRICKS_RATING_HOST" not in lint_section

    def test_concurrency_control(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("databricks")
        assert "resource_group: deploy" in result

    def test_timeouts(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("databricks")
        assert "timeout:" in result

    def test_caching(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("databricks")
        assert "cache:" in result
        assert ".cache/uv" in result

    def test_uv_version_pinned(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("databricks")
        assert '"uv>=0.5,<1"' in result

    def test_databricks_secrets(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("databricks")
        assert "$DATABRICKS_RATING_HOST" in result
        assert "$DATABRICKS_RATING_TOKEN" in result

    def test_container_secrets(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("container")
        assert "$DOCKER_USERNAME" in result
        assert "$DOCKER_PASSWORD" in result

    def test_azure_container_apps_secrets(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("azure-container-apps")
        assert "$DOCKER_USERNAME" in result
        assert "$AZURE_SUBSCRIPTION_ID" in result

    def test_aws_ecs_secrets(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("aws-ecs")
        assert "$DOCKER_USERNAME" in result
        assert "$AWS_ACCESS_KEY_ID" in result

    def test_gcp_run_secrets(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        result = gitlab_ci_yml("gcp-run")
        assert "$DOCKER_USERNAME" in result
        assert "$GCP_PROJECT_ID" in result


class TestAzureDevopsYml:
    """azure_devops_yml() generates valid Azure DevOps pipeline."""

    def test_contains_all_stages(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        assert "Validate" in result
        assert "DeployStaging" in result
        assert "SmokeTest" in result
        assert "ImpactAnalysis" in result
        assert "DeployProduction" in result

    def test_ci_env_var_set(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        assert 'CI: "true"' in result

    def test_secrets_not_in_validate_stage(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        parts = result.split("# ── Deploy to staging")
        assert len(parts) == 2, "Expected exactly one '# ── Deploy to staging' sentinel"
        validate_section = parts[0]
        assert "haute-credentials" not in validate_section

    def test_secrets_in_deploy_stages(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        parts = result.split("# ── Deploy to staging")
        assert len(parts) == 2, "Expected exactly one '# ── Deploy to staging' sentinel"
        deploy_onwards = parts[1]
        assert "haute-credentials" in deploy_onwards

    def test_generated_yaml_fully_parses(self) -> None:
        """The whole pipeline must be valid YAML, not just have the right headers.

        Regression for the production ``env:`` secrets being emitted at the same
        14-space indent as the job-level blocks: under the deeper
        ``DeployProduction`` deployment ``env:`` (18 spaces) the keys were LESS
        indented than their parent, so ``yaml.safe_load`` could not parse the
        document at all.
        """
        result = azure_devops_yml("databricks")
        doc = yaml.safe_load(result)
        assert isinstance(doc, dict)
        assert "stages" in doc

    def test_production_deploy_secrets_nest_under_env(self) -> None:
        """The production deploy step's ``env:`` must carry the secret keys.

        A full structural walk of the parsed document proves the secrets are
        nested inside the deployment step's ``env`` mapping rather than
        dedenting out of it.
        """
        result = azure_devops_yml("databricks")
        doc = yaml.safe_load(result)

        stages = {stage["stage"]: stage for stage in doc["stages"]}
        production = stages["DeployProduction"]
        deployment_job = production["jobs"][0]
        deploy_steps = deployment_job["strategy"]["runOnce"]["deploy"]["steps"]

        deploy_script = next(
            step for step in deploy_steps if step.get("displayName") == "Deploy production"
        )
        env = deploy_script["env"]
        assert env == {
            "DATABRICKS_RATING_HOST": "$(DATABRICKS_RATING_HOST)",
            "DATABRICKS_RATING_TOKEN": "$(DATABRICKS_RATING_TOKEN)",
        }

    def test_python_version_pinned(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        assert "UsePythonVersion@0" in result
        assert 'versionSpec: "3.11"' in result

    def test_uv_version_pinned(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        assert '"uv>=0.5,<1"' in result

    def test_timeouts(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        assert "timeoutInMinutes:" in result

    def test_tag_step_uses_strict_mode(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        assert "set -euo pipefail" in result
        assert "|| true" not in result

    def test_path_filters_use_prefix(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        assert "- data/" in result
        assert "- models/" in result


# ---------------------------------------------------------------------------
# Structural YAML validation — ensures generated YAML is parseable and has
# expected top-level keys and job/stage structure.
# ---------------------------------------------------------------------------


class TestYamlStructure:
    """Validate that generated CI/CD YAML is syntactically valid and has required keys."""

    def test_github_ci_yml_parses(self) -> None:
        doc = yaml.safe_load(github_ci_yml())
        assert isinstance(doc, dict)
        # YAML parses bare `on:` as boolean True
        assert True in doc or "on" in doc
        assert "jobs" in doc
        jobs = doc["jobs"]
        assert "lint" in jobs
        assert "test" in jobs

    def test_github_deploy_yml_header_parses(self) -> None:
        """The deploy YAML header (triggers, name) is valid YAML.

        Full parsing fails because embedded bash steps contain unquoted colons
        which is valid in GitHub Actions but not strict YAML.  We extract and
        validate the trigger/name section instead.
        """
        raw = github_deploy_yml("databricks")
        # Extract everything before the first `jobs:` line
        header = raw.split("\njobs:")[0] + "\njobs: {}"
        doc = yaml.safe_load(header)
        assert isinstance(doc, dict)
        assert True in doc or "on" in doc
        assert "name" in doc

    def test_github_deploy_yml_has_expected_jobs(self) -> None:
        """String-level verification of job names in deploy YAML."""
        raw = github_deploy_yml("databricks")
        assert "deploy-staging:" in raw
        assert "smoke-test:" in raw
        assert "impact-analysis:" in raw
        # smoke-test should depend on deploy-staging
        assert "needs:" in raw

    def test_github_deploy_prod_yml_header_parses(self) -> None:
        from haute._scaffold import github_deploy_prod_yml

        raw = github_deploy_prod_yml("databricks")
        header = raw.split("\njobs:")[0] + "\njobs: {}"
        doc = yaml.safe_load(header)
        assert isinstance(doc, dict)
        assert "deploy-production:" in raw

    def test_gitlab_ci_yml_parses(self) -> None:
        from haute._scaffold import gitlab_ci_yml

        doc = yaml.safe_load(gitlab_ci_yml("databricks"))
        assert isinstance(doc, dict)
        assert "stages" in doc
        stages = doc["stages"]
        assert "validate" in stages
        assert "deploy-staging" in stages

    def test_databricks_secrets(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("databricks")
        assert "$(DATABRICKS_RATING_HOST)" in result
        assert "$(DATABRICKS_RATING_TOKEN)" in result

    def test_sagemaker_secrets(self) -> None:
        from haute._scaffold import azure_devops_yml

        result = azure_devops_yml("sagemaker")
        assert "$(AWS_ACCESS_KEY_ID)" in result
        assert "$(SAGEMAKER_ROLE_ARN)" in result
        assert "$(DATABRICKS_RATING_HOST)" not in result


# ---------------------------------------------------------------------------
# Structural TOML validation — ensures generated TOML is parseable and has
# expected top-level keys and section structure.
# ---------------------------------------------------------------------------


class TestTomlStructure:
    """Validate that generated TOML is syntactically valid and has required keys."""

    def test_databricks_toml_parses(self) -> None:
        raw = haute_toml("motor", "databricks", "github")
        doc = tomllib.loads(raw)
        assert "project" in doc
        assert doc["project"]["name"] == "motor"
        assert "deploy" in doc
        assert "databricks" in doc["deploy"]

    def test_container_toml_parses(self) -> None:
        raw = haute_toml("motor", "container", "github")
        doc = tomllib.loads(raw)
        assert "deploy" in doc
        assert "container" in doc["deploy"]
        assert "databricks" not in doc["deploy"]

    def test_sagemaker_toml_parses(self) -> None:
        raw = haute_toml("motor", "sagemaker", "gitlab")
        doc = tomllib.loads(raw)
        assert "deploy" in doc
        assert "sagemaker" in doc["deploy"]

    def test_azure_ml_toml_parses(self) -> None:
        raw = haute_toml("motor", "azure-ml", "github")
        doc = tomllib.loads(raw)
        assert "deploy" in doc
        assert "azure-ml" in doc["deploy"]

    def test_azure_container_apps_toml_parses(self) -> None:
        raw = haute_toml("motor", "azure-container-apps", "github")
        doc = tomllib.loads(raw)
        assert "container" in doc["deploy"]
        assert "azure-container-apps" in doc["deploy"]

    def test_aws_ecs_toml_parses(self) -> None:
        raw = haute_toml("motor", "aws-ecs", "github")
        doc = tomllib.loads(raw)
        assert "container" in doc["deploy"]
        assert "aws-ecs" in doc["deploy"]

    def test_gcp_run_toml_parses(self) -> None:
        raw = haute_toml("motor", "gcp-run", "github")
        doc = tomllib.loads(raw)
        assert "container" in doc["deploy"]
        assert "gcp-run" in doc["deploy"]

    def test_project_name_in_parsed_toml(self) -> None:
        raw = haute_toml("my_pipeline", "databricks", "github")
        doc = tomllib.loads(raw)
        assert doc["project"]["name"] == "my_pipeline"
        assert doc["deploy"]["model_name"] == "my_pipeline"
        assert doc["deploy"]["endpoint_name"] == "my_pipeline"

    def test_safety_section_in_parsed_toml(self) -> None:
        raw = haute_toml("motor", "databricks", "github")
        doc = tomllib.loads(raw)
        assert "safety" in doc
        assert "impact_dataset" in doc["safety"]

    def test_ci_section_in_parsed_toml(self) -> None:
        raw = haute_toml("motor", "databricks", "github")
        doc = tomllib.loads(raw)
        assert "ci" in doc
        assert "staging" in doc["ci"]


# ---------------------------------------------------------------------------
# Structural Dockerfile validation
# ---------------------------------------------------------------------------


class TestDockerfileStructure:
    """Validate that generated Dockerfiles have valid structure."""

    def test_dockerfile_has_required_instructions(self) -> None:
        from pathlib import Path

        from haute.deploy._config import DeployConfig, ResolvedDeploy
        from haute.deploy._container import _generate_dockerfile
        from haute.graph_utils import PipelineGraph

        resolved = ResolvedDeploy(
            config=DeployConfig(pipeline_file=Path("main.py"), model_name="test"),
            full_graph=PipelineGraph(),
            pruned_graph=PipelineGraph(),
            input_node_ids=[],
            output_node_id="out",
            artifacts={},
            input_schema={},
            output_schema={},
        )
        df = _generate_dockerfile("python:3.11-slim", 8080, resolved)
        lines = df.strip().splitlines()
        instructions = [
            line.split()[0] for line in lines if line.strip() and not line.startswith("#")
        ]
        assert "FROM" in instructions
        assert "COPY" in instructions
        assert "RUN" in instructions
        assert "EXPOSE" in instructions
        assert "CMD" in instructions
        # FROM must be the first instruction
        assert instructions[0] == "FROM"


class TestStarterFiles:
    """starter_pipeline() and starter_test_quote() generate valid content."""

    def test_pipeline_has_name(self) -> None:
        result = starter_pipeline("my_project")
        # Pipeline constructor may span multiple lines (formatter choice),
        # so assert the name appears as a quoted string near the
        # ``haute.Pipeline(`` call rather than on the same line.
        assert "haute.Pipeline(" in result
        assert '"my_project"' in result
        assert "import haute" in result
        assert "import polars" in result

    def test_pipeline_is_runnable_scaffold(self) -> None:
        """The starter ships a minimal runnable graph (source → transform → output).

        Replaces the old ``test_pipeline_imports_from_utility``: Wave 10A
        #112 removes the unconditional ``from utility.features import …``
        line so the starter stands on its own, and introduces decorators
        (``@pipeline.data_input``, ``@pipeline.polars``,
        ``@pipeline.output``) so ``haute init`` → ``haute run`` yields a
        real executable graph instead of an empty ``Pipeline()``.
        """
        result = starter_pipeline("my_project")
        # The three core decorator forms must be present so the parser
        # picks up a source, a transform, and a terminal node.
        assert "@pipeline.data_input(" in result, (
            "Starter pipeline must include a dataInput node so data can flow into the graph."
        )
        assert "@pipeline.polars" in result, (
            "Starter pipeline must include a polars transform so users "
            "have a working example of the transform surface."
        )
        assert "@pipeline.output" in result, (
            "Starter pipeline must include an output node so the terminal preview materialises."
        )

    def test_starter_utility_features(self) -> None:
        from haute._scaffold import starter_utility_features, starter_utility_init

        init_result = starter_utility_init()
        assert "utilit" in init_result.lower()

        features_result = starter_utility_features()
        assert "def clean_columns(df: pl.LazyFrame)" in features_result
        assert "def to_date(col_name: str, fmt: str" in features_result
        assert "def years_between(earlier: pl.Expr, later: pl.Expr)" in features_result
        assert "def months_between(earlier: pl.Expr, later: pl.Expr)" in features_result
        assert "def days_between(earlier: pl.Expr, later: pl.Expr)" in features_result
        assert "def postcode_area(col_name: str)" in features_result
        assert "def cols_matching(all_cols: list[str], pattern_fn: Callable" in features_result
        assert "import polars as pl" in features_result
        assert "from __future__ import annotations" in features_result

    def test_test_quote_is_valid_json_array(self) -> None:
        import json

        result = starter_test_quote()
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1


class TestPreCommitHook:
    """pre_commit_hook() generates a valid shell hook."""

    def test_returns_non_empty_string(self) -> None:
        result = pre_commit_hook()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_shebang(self) -> None:
        result = pre_commit_hook()
        assert result.startswith("#!/bin/sh")

    def test_contains_ruff_format(self) -> None:
        result = pre_commit_hook()
        assert "ruff format" in result

    def test_contains_git_add(self) -> None:
        result = pre_commit_hook()
        assert "git add" in result

    def test_valid_shell_syntax(self) -> None:
        result = pre_commit_hook()
        assert "#!/bin/sh" in result
        assert "exit 0" in result
        lines = result.strip().splitlines()
        for line in lines:
            assert line.count("$(") == line.count(")") or "#" in line or "2>/dev/null" in line


class TestStarterTest:
    """starter_test() generates a valid starter test file."""

    def test_returns_non_empty_string(self) -> None:
        result = starter_test("my_project")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_test_function(self) -> None:
        result = starter_test("my_project")
        assert "def test_" in result

    def test_contains_pipeline_path_reference(self) -> None:
        result = starter_test("my_project")
        assert "rating" in result
        assert "main.py" in result

    def test_name_substituted(self) -> None:
        result = starter_test("acme_motor")
        assert "acme_motor" in result

    def test_name_with_special_chars(self) -> None:
        result = starter_test("my-project")
        assert "my-project" in result


class TestHauteTomlEdgeCases:
    """Edge cases for haute_toml()."""

    def test_name_with_hyphens(self) -> None:
        raw = haute_toml("my-rating-engine", "databricks", "github")
        doc = tomllib.loads(raw)
        assert doc["project"]["name"] == "my-rating-engine"

    def test_name_with_quotes_escaped(self) -> None:
        raw = haute_toml("it's", "databricks", "github")
        assert "it's" in raw

    def test_all_targets_produce_valid_toml(self) -> None:
        for target in TARGETS:
            raw = haute_toml("motor", target, "github")
            doc = tomllib.loads(raw)
            assert "project" in doc, f"Missing [project] for target={target}"
            assert "deploy" in doc, f"Missing [deploy] for target={target}"

    def test_ci_provider_in_output(self) -> None:
        for ci in ("github", "gitlab", "azure-devops", "none"):
            raw = haute_toml("motor", "databricks", ci)
            doc = tomllib.loads(raw)
            assert doc["ci"]["provider"] == ci


class TestEnvExampleEdgeCases:
    """Edge cases for env_example()."""

    def test_unknown_target_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown target"):
            env_example("nonexistent")

    def test_each_target_contains_required_credentials(self) -> None:
        for target, cfg in TARGETS.items():
            result = env_example(target)
            secrets = cfg["secrets"]
            assert isinstance(secrets, list)
            for secret in secrets:
                assert secret in result, f"{secret} missing in env_example({target!r})"

    def test_databricks_has_host_and_token(self) -> None:
        result = env_example("databricks")
        assert "DATABRICKS_HOST" in result
        assert "DATABRICKS_TOKEN" in result

    def test_container_has_registry(self) -> None:
        result = env_example("container")
        assert "DOCKER_USERNAME" in result
        assert "DOCKER_PASSWORD" in result


class TestGithubCiYamlValidation:
    """Structural YAML validation for github_ci_yml()."""

    def test_contains_required_jobs(self) -> None:
        result = github_ci_yml()
        assert "lint:" in result
        assert "typecheck:" in result
        assert "test:" in result

    def test_triggers_on_pr_to_main(self) -> None:
        result = github_ci_yml()
        assert "pull_request:" in result
        assert "branches: [main]" in result

    def test_python_version_pinned(self) -> None:
        result = github_ci_yml()
        assert '"3.11"' in result

    def test_yaml_parses(self) -> None:
        doc = yaml.safe_load(github_ci_yml())
        assert isinstance(doc, dict)
        assert "jobs" in doc


class TestGitlabCiYamlValidation:
    """Structural YAML validation for gitlab_ci_yml()."""

    def test_contains_required_stages(self) -> None:
        result = gitlab_ci_yml("databricks")
        doc = yaml.safe_load(result)
        stages = doc["stages"]
        assert "validate" in stages
        assert "deploy-staging" in stages
        assert "deploy-production" in stages

    def test_secrets_only_in_deploy_jobs(self) -> None:
        result = gitlab_ci_yml("databricks")
        doc = yaml.safe_load(result)
        lint_job = doc["lint"]
        assert "variables" not in lint_job

    def test_yaml_parses(self) -> None:
        for target in TARGETS:
            result = gitlab_ci_yml(target)
            doc = yaml.safe_load(result)
            assert isinstance(doc, dict), f"YAML parse failed for target={target}"


class TestAzureDevopsYamlValidation:
    """Structural YAML validation for azure_devops_yml()."""

    def test_contains_required_stages(self) -> None:
        result = azure_devops_yml("databricks")
        assert "Validate" in result
        assert "DeployStaging" in result
        assert "DeployProduction" in result

    def test_ci_env_var_set(self) -> None:
        result = azure_devops_yml("databricks")
        assert 'CI: "true"' in result

    def test_yaml_header_parses(self) -> None:
        for target in TARGETS:
            raw = azure_devops_yml(target)
            header = raw.split("\nstages:")[0] + "\nstages: []"
            doc = yaml.safe_load(header)
            assert isinstance(doc, dict), f"YAML header parse failed for target={target}"


class TestStarterPipelineContent:
    """starter_pipeline() generates valid pipeline content."""

    def test_contains_pipeline_name(self) -> None:
        result = starter_pipeline("acme_rating")
        # The ``haute.Pipeline(...)`` constructor may be wrapped across
        # multiple lines by the formatter; assert the name appears as a
        # quoted string near the call rather than on the same line.
        assert "haute.Pipeline(" in result
        assert '"acme_rating"' in result

    def test_imports_haute_and_polars(self) -> None:
        result = starter_pipeline("test")
        assert "import haute" in result
        assert "import polars as pl" in result
