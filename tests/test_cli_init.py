"""Comprehensive tests for haute init command."""

from __future__ import annotations

import json
import stat
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

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
    "sagemaker",
    "azure-ml",
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

    def test_haute_toml_already_exists_exits_with_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\nname = "existing"\n')
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 1
        assert "already" in result.output.lower()

    def test_creates_data_directory(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert (tmp_path / "data").is_dir()

    def test_starter_data_input_path_is_pipeline_local_and_traversal_free(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        config_path = tmp_path / "rating" / "config" / "data_input" / "raw_rows.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        source_path = Path(config["path"])

        assert source_path == Path("data/sample.parquet")
        assert ".." not in source_path.parts
        assert (tmp_path / "rating" / "data").is_dir()

    def test_creates_prompts_directory(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert (tmp_path / "prompts").is_dir()

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
        for sub in ("config", "models", "outputs"):
            assert (tmp_path / "rating" / sub).is_dir()

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
        import json

        data = json.loads(quote_file.read_text())
        assert isinstance(data, (dict, list))

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
        runner.invoke(cli, ["init"], catch_exceptions=False)
        assert not (tmp_path / "main.py").exists()
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


class TestInitForceReinit:
    def test_force_reinit_prunes_stale_github_ci_files(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # F228: switching CI provider on --force must remove the previously
        # generated provider's CI artifacts.
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "github"], catch_exceptions=False)
        assert (tmp_path / ".github" / "workflows" / "ci.yml").exists()

        result = runner.invoke(cli, ["init", "--ci", "gitlab", "--force"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".gitlab-ci.yml").exists()
        assert not (tmp_path / ".github").exists()

    def test_force_reinit_prunes_stale_gitlab_and_azure_files(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--ci", "gitlab"], catch_exceptions=False)
        runner.invoke(cli, ["init", "--ci", "azure-devops", "--force"], catch_exceptions=False)
        assert (tmp_path / "azure-pipelines.yml").exists()
        assert not (tmp_path / ".gitlab-ci.yml").exists()

        runner.invoke(cli, ["init", "--ci", "none", "--force"], catch_exceptions=False)
        assert not (tmp_path / "azure-pipelines.yml").exists()
        assert not (tmp_path / ".gitlab-ci.yml").exists()
        assert not (tmp_path / ".github").exists()
