"""Comprehensive tests for haute init command."""

from __future__ import annotations

import re
import stat
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from haute._scaffold import (
    app_env_example,
    connection_variables,
    pre_commit_hook,
    workbench_ci_files,
)
from haute._workbench_form import blank_form
from haute.cli import cli
from haute.cli._init_cmd import _ensure_haute_dependency, _toml_basic_string

if TYPE_CHECKING:
    from click.testing import CliRunner

ALL_TARGETS = [
    "databricks",
    "container",
    "azure-container-apps",
    "aws-ecs",
    "gcp-run",
]

ALL_CI_OPTIONS = ["github", "gitlab", "azure-devops", "none"]


class TestInitCreatesProjectStructure:
    def test_creates_haute_toml(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (tmp_path / "haute.toml").exists()
        content = (tmp_path / "haute.toml").read_text()
        assert "[project]" in content

    def test_a_new_project_keeps_the_starter_files_already_there(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # A folder with no haute.toml is a new project, but a pipeline or test already in it
        # is the team's: never a blank starter in its place.
        monkeypatch.chdir(tmp_path)
        pipeline = tmp_path / "rating" / "main.py"
        pipeline.parent.mkdir()
        pipeline.write_bytes(b"import haute\n\npipeline = haute.Pipeline('mine')\n")
        test = tmp_path / "tests" / "test_pipeline.py"
        test.parent.mkdir()
        test.write_bytes(b"def test_mine():\n    pass\n")

        result = runner.invoke(cli, ["init"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert pipeline.read_bytes() == b"import haute\n\npipeline = haute.Pipeline('mine')\n"
        assert test.read_bytes() == b"def test_mine():\n    pass\n"
        assert (tmp_path / "rating" / "utility" / "features.py").is_file()
        assert (tmp_path / "tests" / "quotes" / "example.json").is_file()
        assert "Already there, so kept: rating/main.py, tests/test_pipeline.py" in result.output

    def test_creates_data_directory(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert (tmp_path / "data").is_dir()

    def test_starter_has_no_node_specific_config_files(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert not (tmp_path / "rating" / "config" / "data_input" / "raw_rows.json").exists()
        assert not (tmp_path / "rating" / "config" / "quote_response" / "priced.json").exists()

    def test_does_not_create_prompts_directory(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert not (tmp_path / "prompts").exists()

    def test_creates_rating_directory(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert (tmp_path / "rating").is_dir()

    def test_creates_tests_quotes_directory(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert (tmp_path / "tests" / "quotes").is_dir()

    def test_creates_rating_init_py(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        init_py = tmp_path / "rating" / "__init__.py"
        assert init_py.exists()
        assert init_py.read_text() == ""

    def test_creates_rating_placeholder_subdirectories(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert (tmp_path / "rating" / "config").is_dir()
        # The versioned home for the data, model and parameter files the pipeline reads.
        for sub in ("data", "models", "params"):
            assert (tmp_path / "rating" / "files" / sub).is_dir()
        # Nothing used the old rating/data and rating/outputs placeholders.
        assert not (tmp_path / "rating" / "data").exists()
        assert not (tmp_path / "rating" / "outputs").exists()

    def test_creates_starter_pipeline(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        main_py = tmp_path / "rating" / "main.py"
        assert main_py.exists()
        content = main_py.read_text()
        assert "haute.Pipeline" in content
        compile(content, "<test>", "exec")

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(main_py)
        assert graph.nodes == []

    def test_creates_starter_utility_files(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        utility_dir = tmp_path / "rating" / "utility"
        assert utility_dir.is_dir()
        assert (utility_dir / "__init__.py").exists()
        assert (utility_dir / "features.py").exists()
        compile((utility_dir / "__init__.py").read_text(), "<test>", "exec")
        compile((utility_dir / "features.py").read_text(), "<test>", "exec")

    def test_creates_starter_test_file(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        test_file = tmp_path / "tests" / "test_pipeline.py"
        assert test_file.exists()
        content = test_file.read_text()
        assert "test_pipeline_parses" in content
        compile(content, "<test>", "exec")

    def test_creates_example_test_quote_json(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        quote_file = tmp_path / "tests" / "quotes" / "example.json"
        assert quote_file.exists()
        from haute.deploy._validators import load_test_quote_file

        # The deploy check's own loader reads the starter, so a new project's test
        # quotes parse before the analyst replaces them.
        assert load_test_quote_file(quote_file) == [{"id": 1, "field_a": "value", "field_b": 42}]

    def test_a_workbench_project_starts_with_no_test_quotes(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A workbench's test quotes are saved from Preview, the folder made by the first."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            cli, ["init", "--workbench", "--store", "sqlserver", "--sign-in", "password"]
        )

        assert result.exit_code == 0, result.output
        assert not (tmp_path / "tests" / "quotes").exists()
        assert (tmp_path / "tests" / "test_pipeline.py").is_file()

    def test_creates_env_example(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert (tmp_path / ".env.example").exists()
        content = (tmp_path / ".env.example").read_text()
        assert "DATABRICKS_" in content

    def test_creates_gitignore_when_none_exists(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert ".env" in content
        assert ".haute/" in content
        # <pipeline>.haute.json is stable-layer (tracked) — must NOT be ignored.
        assert "*.haute.json" not in content
        assert "__pycache__/" in content

    def test_appends_to_existing_gitignore(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("__pycache__/\n.venv/\n")
        runner.invoke(cli, ["init"], catch_exceptions=False)
        content = (tmp_path / ".gitignore").read_text()
        assert "__pycache__/" in content
        assert ".venv/" in content
        assert ".env" in content
        assert ".haute/" in content
        # <pipeline>.haute.json is stable-layer (tracked) — must NOT be ignored.
        assert "*.haute.json" not in content
        assert "# Haute" in content

    def test_gitignore_no_duplicate_entries_on_append(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text(".env\n__pycache__/\n")
        runner.invoke(cli, ["init"], catch_exceptions=False)
        content = (tmp_path / ".gitignore").read_text()
        assert content.count(".env") >= 1
        lines = [line for line in content.splitlines() if line == ".env"]
        assert len(lines) == 1

    def test_creates_pre_commit_hook_in_githooks(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        hook = tmp_path / ".githooks" / "pre-commit"
        assert hook.exists()
        assert "ruff format" in hook.read_text()

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod not meaningful on Windows")
    def test_pre_commit_hook_is_executable(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        hook = tmp_path / ".githooks" / "pre-commit"
        mode = hook.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH

    def test_installs_hook_to_git_hooks_when_git_repo(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        installed = tmp_path / ".git" / "hooks" / "pre-commit"
        assert installed.exists()
        assert "ruff format" in installed.read_text()

    def test_a_git_hook_of_the_teams_own_is_kept_and_named(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        hooks = tmp_path / ".git" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "pre-commit").write_bytes(b"#!/bin/sh\nnpx lint-staged\n")

        result = runner.invoke(cli, ["init"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert (hooks / "pre-commit").read_bytes() == b"#!/bin/sh\nnpx lint-staged\n"
        assert ".git/hooks/pre-commit is not Haute's" in result.output
        assert "(installed)" not in result.output
        assert (tmp_path / ".githooks" / "pre-commit").read_text() == pre_commit_hook()

    def test_an_older_haute_hook_is_replaced(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        hooks = tmp_path / ".git" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\n# Haute pre-commit hook - black\nblack .\n")

        result = runner.invoke(cli, ["init"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert (hooks / "pre-commit").read_text() == pre_commit_hook()
        assert "not Haute's" not in result.output

    def test_no_git_hooks_install_without_git_dir(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()

    def test_removes_root_main_py_from_uv_init(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("print('hello from uv init')\n")
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert not (tmp_path / "main.py").exists()
        assert "removed root main.py" in result.output.lower()
        assert (tmp_path / "rating" / "main.py").exists()

    def test_creates_pyproject_toml_if_missing(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert (tmp_path / "pyproject.toml").exists()
        content = (tmp_path / "pyproject.toml").read_text()
        assert '"haute"' in content


class TestInitProjectName:
    def test_project_name_inferred_from_directory(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        project_dir = tmp_path / "my_pricing_project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        toml_content = (project_dir / "haute.toml").read_text()
        assert "my_pricing_project" in toml_content

    def test_project_name_sanitized_hyphens_to_underscores(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        project_dir = tmp_path / "my-pricing-project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        toml_content = (project_dir / "haute.toml").read_text()
        assert "my_pricing_project" in toml_content

    def test_project_name_from_existing_pyproject_toml(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "motor-pricing"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = []\n',
        )
        runner.invoke(cli, ["init"], catch_exceptions=False)
        toml_content = (tmp_path / "haute.toml").read_text()
        assert "motor-pricing" in toml_content


class TestInitTargetOptions:
    @pytest.mark.parametrize("target", ["sagemaker", "azure-ml"])
    def test_future_targets_are_not_offered(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--target", target])
        assert result.exit_code == 2
        assert not (tmp_path / "haute.toml").exists()

    def test_help_labels_the_build_and_push_only_targets(self, runner: CliRunner):
        result = runner.invoke(cli, ["init", "--help"], terminal_width=200)
        assert result.exit_code == 0, result.output
        help_text = " ".join(result.output.split())
        assert "azure-container-apps, aws-ecs and gcp-run are build and push only" in help_text
        assert "pushes it when a registry is configured" in help_text
        assert "sagemaker" not in help_text
        assert "azure-ml" not in help_text

    @pytest.mark.parametrize("target", ALL_TARGETS)
    def test_target_option_succeeds(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--target", target], catch_exceptions=False)
        assert result.exit_code == 0, result.output

    @pytest.mark.parametrize("target", ALL_TARGETS)
    def test_target_appears_in_haute_toml(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--target", target], catch_exceptions=False)
        toml_content = (tmp_path / "haute.toml").read_text()
        assert f'target = "{target}"' in toml_content

    @pytest.mark.parametrize("target", ALL_TARGETS)
    def test_target_env_example_has_relevant_credentials(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--target", target], catch_exceptions=False)
        env_content = (tmp_path / ".env.example").read_text()
        assert len(env_content.strip()) > 0

    def test_databricks_env_has_databricks_credentials(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--target", "databricks"], catch_exceptions=False)
        env_content = (tmp_path / ".env.example").read_text()
        assert "DATABRICKS_" in env_content

    def test_container_env_has_docker_credentials(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--target", "container"], catch_exceptions=False)
        env_content = (tmp_path / ".env.example").read_text()
        assert "DOCKER_" in env_content
        assert "DATABRICKS_" not in env_content

    def test_container_toml_has_container_section(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--target", "container"], catch_exceptions=False)
        toml_content = (tmp_path / "haute.toml").read_text()
        assert "[deploy.container]" in toml_content
        assert "[deploy.databricks]" not in toml_content


class TestInitCIOptions:
    @pytest.mark.parametrize("ci_option", ALL_CI_OPTIONS)
    def test_ci_option_succeeds(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        ci_option: str,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--ci", ci_option], catch_exceptions=False)
        assert result.exit_code == 0, result.output

    def test_github_ci_creates_workflow_files(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "github"], catch_exceptions=False)
        workflows = tmp_path / ".github" / "workflows"
        assert (workflows / "ci.yml").exists()
        assert (workflows / "deploy-staging.yml").exists()
        assert (workflows / "deploy-production.yml").exists()

    def test_gitlab_ci_creates_gitlab_ci_yml(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "gitlab"], catch_exceptions=False)
        gitlab_file = tmp_path / ".gitlab-ci.yml"
        assert gitlab_file.exists()
        content = gitlab_file.read_text()
        assert len(content.strip()) > 0
        assert not (tmp_path / ".github").exists()
        assert not (tmp_path / "azure-pipelines.yml").exists()

    def test_azure_devops_ci_creates_azure_pipelines_yml(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "azure-devops"], catch_exceptions=False)
        pipeline_file = tmp_path / "azure-pipelines.yml"
        assert pipeline_file.exists()
        content = pipeline_file.read_text()
        assert "trigger:" in content
        assert "haute deploy" in content
        assert not (tmp_path / ".github").exists()
        assert not (tmp_path / ".gitlab-ci.yml").exists()

    def test_none_ci_creates_no_ci_files(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "none"], catch_exceptions=False)
        assert not (tmp_path / ".github").exists()
        assert not (tmp_path / ".gitlab-ci.yml").exists()
        assert not (tmp_path / "azure-pipelines.yml").exists()

    def test_ci_provider_recorded_in_haute_toml(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "azure-devops"], catch_exceptions=False)
        toml_content = (tmp_path / "haute.toml").read_text()
        assert 'provider = "azure-devops"' in toml_content

    def test_target_and_ci_combination(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["init", "--target", "container", "--ci", "azure-devops"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        content = (tmp_path / "azure-pipelines.yml").read_text()
        assert "DOCKER_USERNAME" in content or "DOCKER_PASSWORD" in content
        toml_content = (tmp_path / "haute.toml").read_text()
        assert 'target = "container"' in toml_content
        assert 'provider = "azure-devops"' in toml_content


class TestInitOutputSummary:
    def test_summary_includes_project_name(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert "my_project" in result.output

    def test_summary_includes_target_and_ci(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            cli, ["init", "--target", "container", "--ci", "gitlab"], catch_exceptions=False
        )
        assert "container" in result.output
        assert "gitlab" in result.output

    def test_summary_shows_next_steps(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert "uv sync" in result.output
        assert "haute serve" in result.output


class TestEnsureHauteDependency:
    def test_creates_pyproject_toml_if_not_exists(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        _ensure_haute_dependency(pyproject, "test_project")
        assert pyproject.exists()
        content = pyproject.read_text()
        assert '"haute"' in content
        assert 'name = "test_project"' in content
        assert 'version = "0.1.0"' in content

    def test_adds_haute_to_existing_dependencies(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "foo"\nversion = "0.1.0"\ndependencies = [\n    "polars",\n]\n'
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        assert '"haute"' in content
        assert '"polars"' in content

    def test_does_not_duplicate_if_haute_already_present(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "foo"\nversion = "0.1.0"\ndependencies = [\n    "haute",\n]\n'
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        assert content.count('"haute"') == 1

    @pytest.mark.parametrize(
        "pyproject_text",
        [
            None,
            '[project]\nname = "foo"\ndependencies = [\n    "polars",\n]\n',
            '[project]\nname = "foo"\n',
            'project.dependencies = ["polars"]\n',
            "[tool.ruff]\nline-length = 100\n",
        ],
        ids=["no-file", "dependencies", "no-dependencies-key", "dotted", "no-project-table"],
    )
    def test_adds_the_requirement_it_is_given(self, tmp_path: Path, pyproject_text: str | None):
        # A workbench project's haute comes with its quote store's driver.
        pyproject = tmp_path / "pyproject.toml"
        if pyproject_text is not None:
            pyproject.write_text(pyproject_text)
        _ensure_haute_dependency(pyproject, "foo", "haute[postgresql]")
        with open(pyproject, "rb") as fh:
            dependencies = tomllib.load(fh)["project"]["dependencies"]
        assert dependencies[0] == "haute[postgresql]"
        assert dependencies.count("haute[postgresql]") == 1

    @pytest.mark.parametrize(
        ("listed", "gains"),
        [
            ("haute>=1", "haute[azure,sqlserver]>=1"),
            (
                "haute[databricks]>=1; python_version >= '3.11'",
                'haute[azure,databricks,sqlserver]>=1; python_version >= "3.11"',
            ),
            ("haute[postgresql,azure]", "haute[azure,postgresql,sqlserver]"),
        ],
        ids=["version", "other-extra-and-marker", "the-other-database"],
    )
    @pytest.mark.parametrize(
        "layout",
        ['[project]\nname = "foo"\ndependencies = [{deps}]\n', "project.dependencies = [{deps}]\n"],
        ids=["table", "dotted"],
    )
    def test_gives_a_haute_requirement_already_there_the_extras_it_lacks(
        self, tmp_path: Path, layout: str, listed: str, gains: str
    ):
        # A project set up again as a workbench: its haute keeps its version, marker and other
        # extras, and none is taken away.
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(layout.format(deps=f'"polars", {_toml_basic_string(listed)}'))
        assert _ensure_haute_dependency(pyproject, "foo", "haute[sqlserver,azure]")
        with open(pyproject, "rb") as fh:
            assert tomllib.load(fh)["project"]["dependencies"] == ["polars", gains]

    def test_a_haute_requirement_with_the_extras_leaves_the_file_as_it_was(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        text = (
            '[project]\nname = "foo"\ndependencies = ["polars", "haute[sqlserver, azure] >= 1"]\n'
            "\n[dependency-groups]\ndev = []\n\n[tool.mypy]\nstrict = true\n"
        )
        pyproject.write_bytes(text.encode("utf-8"))
        assert not _ensure_haute_dependency(pyproject, "foo", "haute[sqlserver,azure]")
        # An automated project's plain haute takes no extra away either.
        assert not _ensure_haute_dependency(pyproject, "foo")
        assert pyproject.read_bytes() == text.encode("utf-8")

    def test_adds_dependency_groups_dev_section(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "foo"\nversion = "0.1.0"\ndependencies = []\n')
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        assert "[dependency-groups]" in content
        assert '"ruff' in content
        assert '"mypy' in content
        assert '"pytest' in content

    def test_does_not_duplicate_dependency_groups(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "foo"\nversion = "0.1.0"\n'
            'dependencies = [\n    "haute",\n]\n\n'
            '[dependency-groups]\ndev = [\n    "ruff>=0.8",\n]\n'
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        assert content.count("[dependency-groups]") == 1

    def test_adds_tool_mypy_section(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "foo"\nversion = "0.1.0"\ndependencies = []\n')
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        assert "[tool.mypy]" in content
        assert "ignore_missing_imports" in content
        assert "catboost" in content

    def test_the_mypy_block_resolves_what_the_pipeline_imports_from_its_folder(
        self, tmp_path: Path
    ):
        """`uv run mypy .`, as the generated CI runs it, follows rating/main.py's imports of
        its own utility and submodel modules, as Python does when it runs the file."""
        import subprocess

        pyproject = tmp_path / "pyproject.toml"
        _ensure_haute_dependency(pyproject, "foo")
        for package in ("rating", "rating/modules", "rating/utility"):
            (tmp_path / package).mkdir(parents=True, exist_ok=True)
            (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "rating" / "modules" / "factors.py").write_text(
            "FACTOR = 3\n", encoding="utf-8"
        )
        (tmp_path / "rating" / "utility" / "features.py").write_text("RATE = 2\n", encoding="utf-8")
        (tmp_path / "rating" / "main.py").write_text(
            "from modules import factors\n"
            "from utility.features import RATE\n\n"
            "PREMIUM: int = factors.FACTOR * RATE\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--cache-dir", str(tmp_path / ".mypy_cache"), "."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr

    def test_does_not_duplicate_tool_mypy(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "foo"\nversion = "0.1.0"\n'
            'dependencies = [\n    "haute",\n]\n\n'
            "[tool.mypy]\nignore_missing_imports = false\n"
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        assert content.count("[tool.mypy]") == 1

    def test_adds_haute_when_no_dependencies_key(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "foo"\nversion = "0.1.0"\n')
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        assert '"haute"' in content
        assert "dependencies" in content

    def test_created_pyproject_has_all_sections(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        _ensure_haute_dependency(pyproject, "new_project")
        content = pyproject.read_text()
        assert "[project]" in content
        assert 'name = "new_project"' in content
        assert 'requires-python = ">=3.11"' in content
        assert '"haute"' in content
        assert "[dependency-groups]" in content
        assert "[tool.mypy]" in content


class TestEnsureHauteDependencyTomlSafety:
    """Round-trip safety: the edit must never corrupt pyproject.toml.

    Every test writes a valid TOML file, runs the edit, then re-parses with
    :mod:`tomllib` — a corrupt result raises ``TOMLDecodeError`` and fails the
    test loudly.
    """

    def test_dependency_with_quoted_env_marker_round_trips(self, tmp_path: Path):
        # F131: an existing dependency whose value contains double quotes
        # (a PEP 508 environment marker) must be re-emitted with proper TOML
        # basic-string escaping, not naive f-string wrapping.
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "foo"\nversion = "0.1.0"\n'
            'dependencies = [\n    "numpy; python_version >= \\"3.11\\"",\n]\n',
            encoding="utf-8",
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        parsed = tomllib.loads(content)  # must not raise
        deps = parsed["project"]["dependencies"]
        assert 'numpy; python_version >= "3.11"' in deps
        assert "haute" in deps

    @pytest.mark.parametrize(
        "value",
        [
            'foo; python_version >= "3.11"',  # env marker with double quotes
            "back\\slash",  # backslash
            "tab\tchar",  # control char shorthand
            "null\x00byte",  # \uXXXX control-char escape
            'both "\\ mixed',  # quote + backslash together
        ],
    )
    def test_toml_basic_string_round_trips(self, value: str):
        # F131: whatever escaping the helper produces must be valid TOML that
        # parses back to the original value.
        emitted = _toml_basic_string(value)
        parsed = tomllib.loads(f"x = {emitted}")
        assert parsed["x"] == value

    def test_quoted_project_header_not_duplicated(self, tmp_path: Path):
        # F142: a quoted [ "project" ] header is the project table for tomllib;
        # the textual scan must recognise it and edit in place rather than
        # append a second [project] (which fails "Cannot declare project twice").
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '["project"]\nname = "foo"\nversion = "0.1.0"\ndependencies = [\n    "polars",\n]\n',
            encoding="utf-8",
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        parsed = tomllib.loads(content)  # must not raise
        deps = parsed["project"]["dependencies"]
        assert "haute" in deps
        assert "polars" in deps

    def test_dotted_project_dependencies_not_duplicated(self, tmp_path: Path):
        # F142: a top-level dotted project.dependencies key is the project table
        # for tomllib even though there is no textual [project] header. The edit
        # must rewrite the dotted array in place, not append a duplicate table.
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            'project.name = "foo"\nproject.version = "0.1.0"\nproject.dependencies = ["polars"]\n',
            encoding="utf-8",
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        parsed = tomllib.loads(content)  # must not raise
        deps = parsed["project"]["dependencies"]
        assert "haute" in deps
        assert "polars" in deps
        assert content.count("[project]") == 0

    def test_dotted_project_without_dependencies_inserts_top_level_key(
        self,
        tmp_path: Path,
    ):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            'project.name = "foo"\nproject.version = "0.1.0"\n\n'
            '[project.optional-dependencies]\nextra = ["rich"]\n',
            encoding="utf-8",
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        parsed = tomllib.loads(content)  # must not raise
        assert parsed["project"]["dependencies"] == ["haute"]
        assert parsed["project"]["optional-dependencies"]["extra"] == ["rich"]
        assert content.index("project.dependencies") < content.index(
            "[project.optional-dependencies]"
        )

    def test_dotted_project_subtable_is_not_mistaken_for_project(self, tmp_path: Path):
        # F142: a dotted subtable header [project.optional-dependencies] must
        # NOT be treated as the [project] table body.
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "foo"\nversion = "0.1.0"\n'
            'dependencies = [\n    "polars",\n]\n\n'
            '[project.optional-dependencies]\nextra = [\n    "rich",\n]\n',
            encoding="utf-8",
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        parsed = tomllib.loads(content)  # must not raise
        assert "haute" in parsed["project"]["dependencies"]
        assert "polars" in parsed["project"]["dependencies"]
        assert parsed["project"]["optional-dependencies"]["extra"] == ["rich"]

    def test_indented_dependencies_key_not_duplicated(self, tmp_path: Path):
        # F143: an indented ``dependencies = [`` inside [project] must be
        # matched and edited in place, not duplicated.
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\n  name = "foo"\n  version = "0.1.0"\n'
            '  dependencies = [\n    "polars",\n  ]\n',
            encoding="utf-8",
        )
        _ensure_haute_dependency(pyproject, "foo")
        content = pyproject.read_text()
        parsed = tomllib.loads(content)  # duplicate key would raise here
        deps = parsed["project"]["dependencies"]
        assert "haute" in deps
        assert "polars" in deps

    def test_array_of_tables_project_raises_clear_error(self, tmp_path: Path):
        # F226: [[project]] makes parsed['project'] a list; the helper must
        # fail loudly with a clear message rather than AttributeError.
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[[project]]\nname = "foo"\ndependencies = []\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="array-of-tables"):
            _ensure_haute_dependency(pyproject, "foo")

    def test_haute_where_its_extras_cannot_be_added_raises_naming_them(self, tmp_path: Path):
        # An inline [project] table has no array to rewrite: the extras are named for the
        # analyst to add, and the file is left as it was.
        pyproject = tmp_path / "pyproject.toml"
        text = 'project = { name = "foo", dependencies = ["haute>=1"] }\n'
        pyproject.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="add the azure, sqlserver extras to it yourself"):
            _ensure_haute_dependency(pyproject, "foo", "haute[sqlserver,azure]")
        assert pyproject.read_text(encoding="utf-8") == text


def _own_files(project_dir: Path) -> dict[Path, bytes]:
    """The project's own files, which a re-run never writes: rating/ (but the workbench's
    form folder) and tests/."""
    return {
        path: path.read_bytes()
        for folder in ("rating", "tests")
        for path in (project_dir / folder).rglob("*")
        if path.is_file() and "workbench" not in path.relative_to(project_dir).parts
    }


class TestInitAgain:
    """``haute init`` in a project that already has a haute.toml sets it up again: its current
    answers the defaults, haute.toml written afresh, the workflows, .env.example and the hook
    again, and nothing written in rating/ or tests/ (specs/cli)."""

    def test_the_project_is_left_and_haute_toml_written_afresh(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        first = runner.invoke(cli, ["init", "--target", "container"], catch_exceptions=False)
        assert first.exit_code == 0, first.output
        # The analyst's work: an edited pipeline, a utility module and the example quote taken
        # out, a quote of their own.
        (tmp_path / "rating" / "main.py").write_text(
            "import haute\n\npipeline = haute.Pipeline('x')\n"
        )
        (tmp_path / "rating" / "utility" / "features.py").unlink()
        (tmp_path / "tests" / "quotes" / "example.json").unlink()
        (tmp_path / "tests" / "quotes" / "renewal.json").write_text("{}\n")
        own = _own_files(tmp_path)
        # haute.toml as the team has it: the name its endpoint has, a registry, and the app's
        # MLflow settings.
        toml = tmp_path / "haute.toml"
        edited = re.sub(r'(?m)^name = ".*"$', 'name = "motor_pricing"', toml.read_text(), count=1)
        edited = edited.replace('registry = ""', 'registry = "acr.example.io/pricing"')
        toml.write_text(edited + '\n[mlflow]\ntracking_uri = "databricks"\n')

        result = runner.invoke(cli, ["init", "--ci", "gitlab"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert _own_files(tmp_path) == own
        with open(toml, "rb") as fh:
            doc = tomllib.load(fh)
        # Afresh from the answers, the target the project's own: what was set by hand is gone,
        # the name kept.
        assert doc["project"]["name"] == doc["deploy"]["endpoint_name"] == "motor_pricing"
        assert (doc["deploy"]["target"], doc["ci"]["provider"]) == ("container", "gitlab")
        assert doc["deploy"]["container"]["registry"] == ""
        assert "mlflow" not in doc
        # The previous provider's workflows pruned.
        assert (tmp_path / ".gitlab-ci.yml").is_file()
        assert not (tmp_path / ".github").exists()
        for reported in (
            "Set up Haute project 'motor_pricing' again (container + gitlab)",
            "  written    haute.toml",
            "  unchanged  pyproject.toml",
            "  unchanged  .env.example",
            "  written    .gitlab-ci.yml",
            "  removed    .github/workflows/ci.yml",
            "rating/ and tests/ are left as they are.",
            "git diff haute.toml",
            "[mlflow]",
        ):
            assert reported in result.output
        assert "Replace the pipeline's" not in result.output

    def test_at_a_terminal_enter_keeps_the_projects_answers(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.cli._init_cmd

        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--target", "aws-ecs", "--ci", "azure-devops"])
        monkeypatch.setattr(haute.cli._init_cmd, "_at_terminal", lambda: True)

        result = runner.invoke(cli, ["init"], input="\n\n\n", catch_exceptions=False)

        assert result.exit_code == 0, result.output
        # Azure DevOps is the third repository and AWS ECS the fourth target.
        assert "  4. None\nChoose [3]:" in result.output
        assert "  5. GCP Cloud Run (build and push only)\nChoose [4]:" in result.output
        with open(tmp_path / "haute.toml", "rb") as fh:
            doc = tomllib.load(fh)
        assert (doc["deploy"]["target"], doc["ci"]["provider"]) == ("aws-ecs", "azure-devops")

    @pytest.mark.parametrize(
        "toml_text",
        [
            '[project]\nname = "x"\n\n[deploy]\ntarget = "container"\n',
            '[project]\nname = "x"\n\n[deploy]\ntarget = "container"\n\n'
            '[ci]\nprovider = "bitbucket"\n',
        ],
        ids=["none", "one-not-offered"],
    )
    def test_without_a_terminal_a_question_the_project_holds_no_answer_to_is_refused(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, toml_text: str
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(toml_text)
        before = _files(tmp_path)

        refused = runner.invoke(cli, ["init"])

        assert refused.exit_code == 2
        assert "--ci" in refused.output
        assert _files(tmp_path) == before
        # Given the provider, the rest is the project's own: its target kept.
        result = runner.invoke(cli, ["init", "--ci", "none"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        with open(tmp_path / "haute.toml", "rb") as fh:
            assert tomllib.load(fh)["deploy"]["target"] == "container"

    def test_a_re_run_prunes_stale_github_ci_files(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # F228: switching CI provider on a re-run must remove the previously
        # generated provider's CI artifacts.
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "github"], catch_exceptions=False)
        assert (tmp_path / ".github" / "workflows" / "ci.yml").exists()

        result = runner.invoke(cli, ["init", "--ci", "gitlab"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".gitlab-ci.yml").exists()
        assert not (tmp_path / ".github").exists()

    def test_a_re_run_prunes_stale_gitlab_and_azure_files(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "gitlab"], catch_exceptions=False)
        runner.invoke(cli, ["init", "--ci", "azure-devops"], catch_exceptions=False)
        assert (tmp_path / "azure-pipelines.yml").exists()
        assert not (tmp_path / ".gitlab-ci.yml").exists()

        runner.invoke(cli, ["init", "--ci", "none"], catch_exceptions=False)
        assert not (tmp_path / "azure-pipelines.yml").exists()
        assert not (tmp_path / ".gitlab-ci.yml").exists()
        assert not (tmp_path / ".github").exists()

    def test_a_new_project_prunes_no_workflow_of_the_teams(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # A first init in a repository that already runs GitLab CI: the file is the team's.
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitlab-ci.yml").write_bytes(b"test:\n  script: make test\n")

        result = runner.invoke(cli, ["init", "--ci", "github"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert (tmp_path / ".gitlab-ci.yml").read_bytes() == b"test:\n  script: make test\n"


#: The flags that answer a workbench project's questions without a terminal.
WORKBENCH = ["--workbench", "--store", "sqlserver", "--sign-in", "password"]


def _files(project_dir: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in project_dir.rglob("*") if path.is_file()}


class TestInitWorkbench:
    """A workbench ``haute init`` makes a project that hosts no scoring API: its haute.toml,
    form, connections and workflows serve the underwriter app (specs/workbench)."""

    def _form(self, project_dir: Path) -> Any:
        from haute._workbench_config import read_workbench_config
        from haute._workbench_folder import read_form_document

        return read_form_document(read_workbench_config(project_dir))

    @pytest.mark.parametrize(
        ("store", "sign_in", "variables"),
        [
            ("sqlserver", "password", ["USERNAME", "PASSWORD"]),
            ("sqlserver", "entra", []),
            ("postgresql", "entra", ["USERNAME"]),
            (
                "postgresql",
                "service-principal",
                ["TENANT_ID", "CLIENT_ID", "CLIENT_SECRET", "USERNAME"],
            ),
        ],
    )
    def test_writes_the_workbench_project_and_reports_it(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        store: str,
        sign_in: str,
        variables: list[str],
    ):
        from haute._connections import read_connections
        from haute._workbench_config import read_workbench_config
        from haute.deploy._config import DeployConfig

        project_dir = tmp_path / "motor"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        result = runner.invoke(
            cli,
            ["init", "--workbench", "--store", store, "--sign-in", sign_in],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        with open(project_dir / "haute.toml", "rb") as fh:
            doc = tomllib.load(fh)
        # No scoring API: [deploy] holds the app's image alone, and no endpoint's checks.
        assert set(doc["deploy"]) == {"container"}
        assert set(doc["deploy"]["container"]) == {"registry", "port", "base_image"}
        assert "safety" not in doc and "staging" not in doc["ci"]
        # The workbench as its own reader reads it: the host, and dev, staging and prod on the
        # one database, each signed in to by the app's managed identity.
        config = read_workbench_config(project_dir)
        assert config.enabled and config.app_host == "azure-container-apps"
        # Each hosted environment's Container App, left for IT to name.
        assert [
            (each.name, each.resource_group, each.container_app) for each in config.app_environments
        ] == [
            ("staging", "", ""),
            ("prod", "", ""),
        ]
        assert [(env.name, env.provider, env.auth) for env in config.store] == [
            (name, store, "managed-identity") for name in ("dev", "staging", "prod")
        ]
        role = {"username_env": "HAUTE_STORE_USERNAME"} if store == "postgresql" else {}
        assert all(dict(env.variables) == role for env in config.store)
        DeployConfig.from_toml(project_dir / "haute.toml")
        # The analyst's sign-in in connections.toml, each environment naming its own variables,
        # which .env.example names beside the host's credentials.
        connections = read_connections(project_dir)
        assert list(connections) == ["dev", "staging", "prod"]
        for name, connection in connections.items():
            assert (connection.provider, connection.auth) == (store, sign_in)
            assert sorted(connection.variables.values()) == sorted(
                f"HAUTE_{name.upper()}_{key}" for key in variables
            )
        env = (project_dir / ".env.example").read_text(encoding="utf-8")
        assert env == app_env_example("azure-container-apps", connection_variables(project_dir))
        assert "AZURE_CLIENT_SECRET=" in env and "DATABRICKS" not in env
        # The release installs the store's driver: haute comes with its extra.
        with open(project_dir / "pyproject.toml", "rb") as fh:
            assert tomllib.load(fh)["project"]["dependencies"] == [f"haute[{store},azure]"]
        # The workbench's workflows, and no endpoint's.
        workflows = project_dir / ".github" / "workflows"
        assert sorted(path.name for path in workflows.iterdir()) == [
            "ci.yml",
            "release-production.yml",
            "release.yml",
        ]
        folder = project_dir / "rating" / "workbench"
        assert sorted(
            path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()
        ) == [
            "form.json",
            "schema/quote_details.json",
            "sheets/sheet_1.json",
            "sheets/sheet_1.layout.json",
        ]
        # Written as the folder's own saves write them: LF, so a first save rewrites nothing.
        assert b"\r\n" not in (folder / "form.json").read_bytes()
        assert self._form(project_dir).spec == blank_form("motor")
        for reported in (
            "rating/workbench/",
            "connections.toml",
            "workbench config",
            "release.yml",
        ):
            assert reported in result.output
        assert "workbench on azure-container-apps + github" in result.output
        assert "Before the first release, github needs these CI secrets" in result.output

    @pytest.mark.parametrize("ci", ["github", "gitlab", "azure-devops"])
    def test_the_workflows_publish_the_app_and_deploy_no_endpoint(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ci: str
    ):
        import yaml

        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", *WORKBENCH, "--ci", ci], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        generated = workbench_ci_files("azure-container-apps", ci, python_version="3.11")
        text = ""
        for relative, content in generated.items():
            written = (tmp_path / relative).read_text(encoding="utf-8")
            assert written == content
            yaml.safe_load(written)
            text += written
        assert "uv run haute app build\n" in text
        assert "uv run haute app build --push" in text
        assert "haute ci registry-host" in text and "$AZURE_CLIENT_SECRET" in text
        for endpoint in ("haute deploy", "haute smoke", "haute impact"):
            assert endpoint not in text
        assert "uv run haute app rollout staging --image" in text
        assert "uv run haute app rollout prod --from staging" in text

    @pytest.mark.parametrize(
        ("ci", "workflow", "entry"),
        [
            ("github", ".github/workflows/release.yml", '- "rating/**"'),
            ("azure-devops", "azure-pipelines.yml", "- rating/"),
        ],
    )
    def test_a_form_change_starts_the_release_through_the_pipeline_folder(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        ci: str,
        workflow: str,
        entry: str,
    ):
        # The form folder is in the pipeline folder, whose trigger the workflow always has.
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", *WORKBENCH, "--ci", ci], catch_exceptions=False)
        text = (tmp_path / workflow).read_text(encoding="utf-8")
        assert entry in text
        assert "forms" not in text

    def test_a_plain_init_writes_none_of_it(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        with open(tmp_path / "haute.toml", "rb") as fh:
            assert "workbench" not in tomllib.load(fh)
        assert not (tmp_path / "rating" / "workbench").exists()
        assert "rating/workbench/" not in result.output
        assert not (tmp_path / "connections.toml").exists()
        assert not (tmp_path / ".github" / "workflows" / "release.yml").exists()
        assert "HAUTE_DEV_" not in (tmp_path / ".env.example").read_text(encoding="utf-8")

    def test_a_re_run_switches_the_kind_of_project_keeping_the_form_and_connections(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        workflows = tmp_path / ".github" / "workflows"
        runner.invoke(cli, ["init", *WORKBENCH], catch_exceptions=False)
        pipeline = tmp_path / "rating" / "main.py"
        pipeline.write_text("import haute\n\npipeline = haute.Pipeline('x')\n# priced\n")
        own = _own_files(tmp_path)
        index = tmp_path / "rating" / "workbench" / "form.json"
        index.write_bytes(index.read_bytes().replace(b'"name": "', b'"name": "edited '))
        edited = index.read_bytes()
        # The team's connections, filled in and one of them signing in another way.
        connections = tmp_path / "connections.toml"
        connections.write_text(
            '[dev]\nprovider = "sqlserver"\nserver = "tcp:dev.example.net,1433"\n'
            'database = "pricing_dev"\nauth = "password"\nusername_env = "DEV_USER"\n'
            'password_env = "DEV_PASSWORD"\n',
            encoding="utf-8",
        )
        filled = connections.read_bytes()

        # Set up again as an automated project: haute.toml and the workflows are its, while
        # the form folder is the analyst's form and connections.toml the team's, and neither
        # is ever replaced by a blank one: .env.example names what the connections read.
        # The workbench has no deploy target: the other kind of project's question has no
        # default, so it is answered.
        automated = runner.invoke(
            cli, ["init", "--automated", "--target", "databricks"], catch_exceptions=False
        )
        assert automated.exit_code == 0, automated.output
        assert "Replace the pipeline's Workbench Input and Workbench Output" in automated.output
        assert _own_files(tmp_path) == own
        with open(tmp_path / "haute.toml", "rb") as fh:
            assert "workbench" not in tomllib.load(fh)
        # The workbench's extras stay: none is taken away.
        with open(tmp_path / "pyproject.toml", "rb") as fh:
            assert tomllib.load(fh)["project"]["dependencies"] == ["haute[sqlserver,azure]"]
        assert sorted(path.name for path in workflows.iterdir()) == [
            "ci.yml",
            "deploy-production.yml",
            "deploy-staging.yml",
        ]
        assert index.read_bytes() == edited
        assert connections.read_bytes() == filled
        env = (tmp_path / ".env.example").read_text(encoding="utf-8")
        assert "DEV_USER=\nDEV_PASSWORD=\n" in env and "HAUTE_DEV_USERNAME" not in env

        # And as a workbench project again: the endpoint's workflows go.
        again = runner.invoke(cli, ["init", *WORKBENCH], catch_exceptions=False)
        assert "Replace the pipeline's Quote Input and Quote Response" in again.output
        assert _own_files(tmp_path) == own
        with open(tmp_path / "haute.toml", "rb") as fh:
            assert tomllib.load(fh)["workbench"]["enabled"] is True
        assert sorted(path.name for path in workflows.iterdir()) == [
            "ci.yml",
            "release-production.yml",
            "release.yml",
        ]
        assert "haute app build" in (workflows / "ci.yml").read_text(encoding="utf-8")
        assert index.read_bytes() == edited
        assert connections.read_bytes() == filled

    def test_an_automated_project_set_up_again_as_a_workbench_keeps_its_pipeline(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute._connections import read_connections
        from haute._workbench_config import read_workbench_config

        monkeypatch.chdir(tmp_path)
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "motor"\nversion = "0.1.0"\n'
            'dependencies = [\n    "haute>=0.7",\n    "polars",\n]\n'
        )
        assert runner.invoke(cli, ["init"], catch_exceptions=False).exit_code == 0
        (tmp_path / "rating" / "main.py").write_text(
            "import haute\n\npipeline = haute.Pipeline('motor')\n# priced\n"
        )
        own = _own_files(tmp_path)

        result = runner.invoke(cli, ["init", *WORKBENCH], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert _own_files(tmp_path) == own
        assert read_workbench_config(tmp_path).enabled
        assert self._form(tmp_path).spec == blank_form("motor")
        assert {(c.provider, c.auth) for c in read_connections(tmp_path).values()} == {
            ("sqlserver", "password")
        }
        # Its haute gains the workbench's extras, keeping its version.
        with open(pyproject, "rb") as fh:
            assert tomllib.load(fh)["project"]["dependencies"] == [
                "haute[azure,sqlserver]>=0.7",
                "polars",
            ]
        for reported in (
            "  updated    pyproject.toml",
            "  updated    .github/workflows/ci.yml",
            "  written    .github/workflows/release.yml",
            "  removed    .github/workflows/deploy-staging.yml",
            "  written    rating/workbench/ (the blank form)",
            "  written    connections.toml",
            "Replace the pipeline's Quote Input and Quote Response, if it has them, by a "
            "Workbench Input and a Workbench Output.",
            "uv sync",
        ):
            assert reported in result.output

    def test_a_re_run_on_the_other_database_writes_connections_afresh(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute._connections import read_connections
        from haute._workbench_config import read_workbench_config

        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", *WORKBENCH], catch_exceptions=False)

        # Without a terminal the sign-in is the project's own: password.
        result = runner.invoke(cli, ["init", "--store", "postgresql"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert {(c.provider, c.auth) for c in read_connections(tmp_path).values()} == {
            ("postgresql", "password")
        }
        assert {env.provider for env in read_workbench_config(tmp_path).store} == {"postgresql"}
        assert "  written    connections.toml" in result.output
        env = (tmp_path / ".env.example").read_text(encoding="utf-8")
        assert env == app_env_example("azure-container-apps", connection_variables(tmp_path))

    def test_a_re_run_from_sqlite_writes_connections_afresh_asking_the_sign_in(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.cli._init_cmd
        from haute._connections import read_connections

        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", *WORKBENCH], catch_exceptions=False)
        # A workbench whose quote store is a SQLite file, which haute init does not offer: the
        # new haute.toml puts dev on PostgreSQL, whose store refuses a SQLite dev connection.
        toml = tmp_path / "haute.toml"
        toml.write_text(
            toml.read_text(encoding="utf-8").replace(
                'provider = "sqlserver"', 'provider = "sqlite"'
            ),
            encoding="utf-8",
        )
        connections = tmp_path / "connections.toml"
        connections.write_text(
            '[dev]\nprovider = "sqlite"\ndatabase = "data/quotes.db"\n', encoding="utf-8"
        )
        before = _files(tmp_path)

        # No server connection to take the sign-in from: without a terminal it is named.
        refused = runner.invoke(cli, ["init", "--store", "postgresql"])
        assert refused.exit_code == 2
        assert "--sign-in" in refused.output
        assert _files(tmp_path) == before

        monkeypatch.setattr(haute.cli._init_cmd, "_at_terminal", lambda: True)
        # A workbench, GitHub and the one host by Enter; Microsoft Entra ID picked.
        result = runner.invoke(
            cli, ["init", "--store", "postgresql"], input="\n\n\n2\n", catch_exceptions=False
        )

        assert result.exit_code == 0, result.output
        assert "How do you sign in to it?" in result.output
        assert {
            (name, each.provider, each.auth) for name, each in read_connections(tmp_path).items()
        } == {(name, "postgresql", "entra") for name in ("dev", "staging", "prod")}
        assert "  written    connections.toml" in result.output

    def test_at_a_terminal_a_re_run_on_the_same_database_keeps_connections_unasked(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.cli._init_cmd

        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", *WORKBENCH], catch_exceptions=False)
        connections = tmp_path / "connections.toml"
        connections.write_bytes(
            connections.read_bytes().replace(b'server = ""', b'server = "tcp:dev.example.net"', 1)
        )
        filled = connections.read_bytes()
        monkeypatch.setattr(haute.cli._init_cmd, "_at_terminal", lambda: True)

        # Enter on each: a workbench, on GitHub, the one host, SQL Server.
        result = runner.invoke(cli, ["init"], input="\n\n\n\n", catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert "Workbench: an app underwriters price quotes in\nChoose [2]:" in result.output
        assert "  2. PostgreSQL\nChoose [1]:" in result.output
        assert "How do you sign in to it?" not in result.output
        assert connections.read_bytes() == filled
        assert "written    connections.toml" not in result.output

    def test_a_pipeline_folder_outside_the_project_is_refused_writing_nothing(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import os

        project_dir = tmp_path / "p"
        project_dir.mkdir()
        # The pipeline folder a junction to somewhere outside the project holding no form
        # folder: the blank form would be written there.
        outside = tmp_path / "outside"
        outside.mkdir()
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(outside), str(project_dir / "rating"))
        else:
            os.symlink(outside, project_dir / "rating", target_is_directory=True)
        monkeypatch.chdir(project_dir)

        refused = runner.invoke(cli, ["init", *WORKBENCH])

        assert refused.exit_code == 1
        assert "rating/workbench/form.json is reached through a link or junction" in refused.output
        # Refused before anything is written: no pyproject.toml, no haute.toml, no form.
        assert [path.name for path in project_dir.iterdir()] == ["rating"]
        assert list(outside.iterdir()) == []

    def test_a_connections_toml_link_is_refused_writing_nothing(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import os

        project_dir = tmp_path / "p"
        project_dir.mkdir()
        # connections.toml a link to somewhere outside the project that is not there.
        outside = tmp_path / "outside"
        if os.name == "nt":
            import _winapi

            outside.mkdir()
            _winapi.CreateJunction(str(outside), str(project_dir / "connections.toml"))
            outside.rmdir()
        else:
            os.symlink(outside, project_dir / "connections.toml")
        monkeypatch.chdir(project_dir)

        refused = runner.invoke(cli, ["init", *WORKBENCH])

        assert refused.exit_code == 1
        assert "connections.toml is reached through a link or junction" in refused.output
        assert not outside.exists()
        assert not (project_dir / "haute.toml").exists()
        assert not (project_dir / "rating" / "workbench").exists()

    @pytest.mark.parametrize("flags", [WORKBENCH, []], ids=["workbench", "automated"])
    def test_a_connections_toml_that_does_not_read_is_refused_writing_nothing(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str]
    ):
        # The team's connections, whose variables .env.example names: never replaced or
        # passed over when they do not read.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "connections.toml").write_text('[dev]\nprovider = "oracle"\n')
        before = _files(tmp_path)

        refused = runner.invoke(cli, ["init", *flags])

        assert refused.exit_code == 1
        assert "connections.toml [dev].provider must be one of" in refused.output
        assert _files(tmp_path) == before


class TestInitQuestions:
    """At a terminal ``haute init`` asks what its flags leave out, each question a numbered
    list; without one a question takes its default, and a workbench project's database and
    sign-in, which have none, must be given (specs/cli)."""

    @pytest.fixture
    def at_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import haute.cli._init_cmd

        monkeypatch.setattr(haute.cli._init_cmd, "_at_terminal", lambda: True)

    def test_a_terminal_asks_each_question_in_turn_for_a_workbench_project(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at_terminal: None
    ):
        monkeypatch.chdir(tmp_path)
        # Workbench, Azure DevOps, the one host (Enter), then Enter on the database, which has
        # no default and so is asked again, PostgreSQL, and Microsoft Entra ID.
        result = runner.invoke(cli, ["init"], input="2\n3\n\n\n2\n2\n", catch_exceptions=False)

        assert result.exit_code == 0, result.output
        asked = [
            "How will this project price quotes?",
            "Where is the repository?",
            "Where will the app run?",
            "Which database holds the saved quotes?",
            "How do you sign in to it?",
        ]
        positions = [result.output.index(question) for question in asked]
        assert positions == sorted(positions)
        assert "  1. Azure Container Apps\nChoose [1]:" in result.output
        assert "Where will the API run?" not in result.output
        with open(tmp_path / "haute.toml", "rb") as fh:
            doc = tomllib.load(fh)
        assert doc["ci"]["provider"] == "azure-devops"
        assert doc["workbench"]["app"]["host"] == "azure-container-apps"
        assert {env["provider"] for env in doc["workbench"]["store"].values()} == {"postgresql"}
        assert 'auth = "entra"' in (tmp_path / "connections.toml").read_text(encoding="utf-8")
        assert (tmp_path / "azure-pipelines.yml").exists()

    def test_enter_takes_each_default_for_an_automated_project(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at_terminal: None
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], input="\n\n\n", catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert "Where will the API run?" in result.output
        assert "Which database" not in result.output
        with open(tmp_path / "haute.toml", "rb") as fh:
            doc = tomllib.load(fh)
        assert (doc["deploy"]["target"], doc["ci"]["provider"]) == ("databricks", "github")
        assert "workbench" not in doc

    def test_the_flags_answer_their_questions_and_the_rest_are_asked(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at_terminal: None
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["init", "--workbench", "--store", "sqlserver", "--ci", "gitlab"],
            input="\n3\n",
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        for answered in ("How will this project", "Where is the repository", "Which database"):
            assert answered not in result.output
        assert "Where will the app run?" in result.output
        assert "How do you sign in to it?" in result.output
        assert 'auth = "service-principal"' in (tmp_path / "connections.toml").read_text(
            encoding="utf-8"
        )

    def test_a_malformed_haute_toml_is_refused_before_any_question(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at_terminal: None
    ):
        # A re-run takes the project's current answers from haute.toml: one that does not
        # read is refused, naming the error, rather than replaced.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project\nname = "x"\n', encoding="utf-8")
        before = _files(tmp_path)

        refused = runner.invoke(cli, ["init"], input="2\n")

        assert refused.exit_code == 1
        assert "haute.toml is not valid TOML" in refused.output
        assert "How will this project" not in refused.output
        assert _files(tmp_path) == before

    def test_without_a_terminal_nothing_is_asked_and_the_defaults_are_taken(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert "How will this project" not in result.output
        assert "(databricks + github)" in result.output

    @pytest.mark.parametrize(
        ("flags", "named"),
        [
            (["--workbench"], "--store"),
            (["--workbench", "--sign-in", "entra"], "--store"),
            (["--workbench", "--store", "postgresql"], "--sign-in"),
            ([*WORKBENCH, "--target", "container"], "--target"),
            (["--store", "sqlserver"], "--store"),
            (["--automated", "--host", "azure-container-apps"], "--host"),
            (["--target", "container", "--sign-in", "entra"], "--sign-in"),
        ],
        ids=[
            "no-database",
            "sign-in-but-no-database",
            "no-sign-in",
            "target-with-workbench",
            "store-with-automated",
            "host-with-automated",
            "sign-in-with-automated",
        ],
    )
    def test_without_a_terminal_a_missing_or_misplaced_answer_is_refused_writing_nothing(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        flags: list[str],
        named: str,
    ):
        monkeypatch.chdir(tmp_path)

        refused = runner.invoke(cli, ["init", *flags])

        assert refused.exit_code == 2
        assert named in refused.output
        assert list(tmp_path.iterdir()) == []

    def test_the_choices_are_what_the_settings_accept(self):
        from haute._connections import CONNECTION_SIGN_INS
        from haute._scaffold import TARGETS, WORKBENCH_STORES
        from haute._workbench_config import APP_HOSTS, STORE_PROVIDERS
        from haute.cli._init_cmd import (
            _HOST_LABELS,
            _SIGN_IN_LABELS,
            _STORE_LABELS,
            _TARGET_LABELS,
            HOST_CHOICES,
            SIGN_IN_CHOICES,
            STORE_CHOICES,
            TARGET_CHOICES,
        )

        assert HOST_CHOICES == tuple(sorted(APP_HOSTS)) == tuple(_HOST_LABELS)
        # Each host's credentials are a target's, which its CI reads to publish.
        assert set(HOST_CHOICES) <= set(TARGETS)
        assert STORE_CHOICES == WORKBENCH_STORES == tuple(_STORE_LABELS)
        assert set(STORE_CHOICES) <= STORE_PROVIDERS
        assert set(SIGN_IN_CHOICES) == set(CONNECTION_SIGN_INS) == set(_SIGN_IN_LABELS)
        # The question lists them in this order.
        assert SIGN_IN_CHOICES == ("password", "entra", "service-principal")
        assert TARGET_CHOICES == tuple(TARGETS) == tuple(_TARGET_LABELS)


class TestInitCiRunsTheProjectsPython:
    """The generated CI installs the Python the project runs on, or ``uv sync`` refuses it."""

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            ({}, "3.11"),
            ({"pyproject.toml": '[project]\nname = "p"\nrequires-python = ">=3.13"\n'}, "3.13"),
            # A range that splits a minor (3.12.0 is out) names its lowest release.
            (
                {"pyproject.toml": '[project]\nname = "p"\nrequires-python = "~=3.12.1"\n'},
                "3.12.1",
            ),
            # Below Haute's own floor, the lowest Python Haute runs on.
            ({"pyproject.toml": '[project]\nname = "p"\nrequires-python = ">=3.9"\n'}, "3.11"),
            # .python-version decides first, as it does for uv.
            (
                {
                    "pyproject.toml": '[project]\nname = "p"\nrequires-python = ">=3.11"\n',
                    ".python-version": "3.13\n",
                },
                "3.13",
            ),
        ],
        ids=["no-pyproject", "floor", "compatible", "below-haute", "python-version-file"],
    )
    @pytest.mark.parametrize("ci_option", ["github", "gitlab", "azure-devops"])
    @pytest.mark.parametrize("workbench", [False, True], ids=["automated", "workbench"])
    def test_every_job_pins_the_projects_python(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        files: dict[str, str],
        expected: str,
        ci_option: str,
        workbench: bool,
    ):
        monkeypatch.chdir(tmp_path)
        for name, content in files.items():
            (tmp_path / name).write_text(content, encoding="utf-8")

        kind = WORKBENCH if workbench else []
        result = runner.invoke(cli, ["init", *kind, "--ci", ci_option], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        released = "release.yml" if workbench else "deploy-staging.yml"
        generated = {
            "github": [".github/workflows/ci.yml", f".github/workflows/{released}"],
            "gitlab": [".gitlab-ci.yml"],
            "azure-devops": ["azure-pipelines.yml"],
        }[ci_option]
        for relative in generated:
            text = (tmp_path / relative).read_text(encoding="utf-8")
            pins = re.findall(
                r'python-version: "([\d.]+)"|versionSpec: "([\d.]+)"|image: python:([\d.]+)\n', text
            )
            assert pins, relative
            assert {next(v for v in pin if v) for pin in pins} == {expected}, relative
