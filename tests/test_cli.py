"""Tests for haute.cli - CLI command tests using click.testing.CliRunner."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Temp project with a root-level pipeline and data/."""
    data = tmp_path / "data"
    data.mkdir()
    pl.DataFrame({"x": [1, 2, 3]}).write_parquet(data / "input.parquet")

    # ``as_posix`` is required on Windows: the raw string form contains
    # ``C:\Users\...`` which Python's parser sees as an invalid
    # ``\U``-prefixed escape.  Forward slashes are accepted by both
    # Polars and os.path on every platform, so the generated pipeline
    # file loads without a ``SyntaxError`` cross-platform.
    input_path = (data / "input.parquet").as_posix()
    code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("test_cli", description="CLI test pipeline")


@pipeline.data_source(path="{input_path}")
def source() -> pl.DataFrame:
    """Read data."""
    return pl.scan_parquet("{input_path}")


@pipeline.polars
def transform(source: pl.DataFrame) -> pl.DataFrame:
    """Add a column."""
    return source.with_columns(y=pl.col("x") * 2)


pipeline.connect("source", "transform")
'''
    (tmp_path / "main.py").write_text(code)
    return tmp_path


# ---------------------------------------------------------------------------
# haute --version
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_flag(self, runner: CliRunner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output.lower()


# ---------------------------------------------------------------------------
# haute init
# ---------------------------------------------------------------------------


class TestInit:
    def test_creates_project_structure(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (tmp_path / "data").is_dir()
        assert (tmp_path / "tests" / "quotes").is_dir()
        assert (tmp_path / "haute.toml").exists()
        assert (tmp_path / ".env.example").exists()
        assert (tmp_path / ".gitignore").exists()
        assert (tmp_path / "rating" / "main.py").exists()
        assert (tmp_path / "rating" / "config").is_dir()
        assert (tmp_path / "rating" / "models").is_dir()
        assert (tmp_path / "rating" / "outputs").is_dir()
        assert (tmp_path / "pyproject.toml").exists()

        # haute.toml should reference rating/main.py
        toml_content = (tmp_path / "haute.toml").read_text()
        assert 'pipeline = "rating/main.py"' in toml_content

        # Starter pipeline should be valid Python
        py_content = (tmp_path / "rating" / "main.py").read_text()
        compile(py_content, "<test>", "exec")

        # pyproject.toml should have haute as a dependency
        pyproject_content = (tmp_path / "pyproject.toml").read_text()
        assert '"haute"' in pyproject_content

        # pyproject.toml should have dev dependency-groups for CI tools
        assert "[dependency-groups]" in pyproject_content
        assert '"ruff' in pyproject_content
        assert '"mypy' in pyproject_content
        assert '"pytest' in pyproject_content

        # pyproject.toml should have mypy config for untyped ML libraries
        assert "[tool.mypy]" in pyproject_content
        assert "catboost" in pyproject_content

        # Starter test should be generated
        test_file = tmp_path / "tests" / "test_pipeline.py"
        assert test_file.exists()
        test_content = test_file.read_text()
        assert "test_pipeline_parses" in test_content
        compile(test_content, "<test>", "exec")

        # Pre-commit hook should be generated
        hook = tmp_path / ".githooks" / "pre-commit"
        assert hook.exists()
        hook_content = hook.read_text()
        assert "ruff format" in hook_content
        assert hook.stat().st_mode & 0o755

    def test_pre_commit_hook_installed_in_git_repo(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """If .git/hooks exists, the pre-commit hook is installed there too."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        installed = tmp_path / ".git" / "hooks" / "pre-commit"
        assert installed.exists()
        assert "ruff format" in installed.read_text()
        assert installed.stat().st_mode & 0o755

    def test_works_alongside_uv_init(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """haute init should layer on top of an existing pyproject.toml from uv init."""
        monkeypatch.chdir(tmp_path)
        # Simulate uv init output
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "motor-pricing"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = []\n',
        )
        (tmp_path / "main.py").write_text("print('hello')\n")

        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        # Should pick up the project name from pyproject.toml
        toml_content = (tmp_path / "haute.toml").read_text()
        assert "motor-pricing" in toml_content

        # Should add haute to existing dependencies
        pyproject_content = (tmp_path / "pyproject.toml").read_text()
        assert '"haute"' in pyproject_content

        # Should add dev dependency-groups for CI tools
        assert "[dependency-groups]" in pyproject_content
        assert '"ruff' in pyproject_content
        assert '"mypy' in pyproject_content
        assert '"pytest' in pyproject_content

        # Root main.py from uv init should be removed
        assert not (tmp_path / "main.py").exists()

        # Starter pipeline should be created in rating/
        py_content = (tmp_path / "rating" / "main.py").read_text()
        assert "haute.Pipeline" in py_content

    def test_skips_haute_dep_if_already_present(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """If pyproject.toml already lists haute, don't duplicate it."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "my-project"\nversion = "0.1.0"\n'
            'dependencies = [\n    "haute",\n]\n',
        )
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        content = (tmp_path / "pyproject.toml").read_text()
        assert content.count('"haute"') == 1

        # Should still add dev dependency-groups even when haute dep exists
        assert "[dependency-groups]" in content
        assert '"ruff' in content

    def test_skips_dev_deps_if_already_present(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """If pyproject.toml already has [dependency-groups], don't duplicate it."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "my-project"\nversion = "0.1.0"\n'
            'dependencies = [\n    "haute",\n]\n\n'
            '[dependency-groups]\ndev = [\n    "ruff>=0.8",\n]\n',
        )
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        content = (tmp_path / "pyproject.toml").read_text()
        assert content.count("[dependency-groups]") == 1

    def test_appends_to_existing_gitignore(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """If .gitignore exists (from uv init), append haute entries."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("__pycache__/\n.venv/\n")
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        content = (tmp_path / ".gitignore").read_text()
        assert "__pycache__/" in content  # original preserved
        assert ".env" in content
        assert "*.haute.json" in content

    def test_already_initialised_fails(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 1
        assert "already" in result.output.lower()

    def test_target_flag_docker(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--target", "container"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        toml_content = (tmp_path / "haute.toml").read_text()
        assert 'target = "container"' in toml_content
        assert "[deploy.container]" in toml_content
        assert "[deploy.databricks]" not in toml_content
        env_content = (tmp_path / ".env.example").read_text()
        assert "DOCKER_" in env_content
        assert "DATABRICKS_" not in env_content

    def test_ci_github_generates_workflows(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--ci", "github"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".github" / "workflows" / "ci.yml").exists()
        assert (tmp_path / ".github" / "workflows" / "deploy-staging.yml").exists()
        assert (tmp_path / ".github" / "workflows" / "deploy-production.yml").exists()

    def test_ci_azure_devops_generates_pipeline(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--ci", "azure-devops"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        pipeline_file = tmp_path / "azure-pipelines.yml"
        assert pipeline_file.exists()
        content = pipeline_file.read_text()
        assert "trigger:" in content
        assert "Validate" in content
        assert "DeployStaging" in content
        assert "DeployProduction" in content
        assert "haute deploy" in content
        assert "haute-credentials" in content
        # Should NOT generate GitHub or GitLab CI files
        assert not (tmp_path / ".github").exists()
        assert not (tmp_path / ".gitlab-ci.yml").exists()

    def test_ci_azure_devops_with_docker_target(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["init", "--target", "container", "--ci", "azure-devops"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        content = (tmp_path / "azure-pipelines.yml").read_text()
        assert "DOCKER_USERNAME" in content
        assert "DOCKER_PASSWORD" in content
        toml_content = (tmp_path / "haute.toml").read_text()
        assert 'provider = "azure-devops"' in toml_content

    def test_ci_none_skips_workflows(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--ci", "none"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert not (tmp_path / ".github").exists()

    def test_safety_and_ci_sections_generated(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        toml_content = (tmp_path / "haute.toml").read_text()
        assert "[safety]" in toml_content
        assert "[ci]" in toml_content
        assert "[ci.staging]" in toml_content


# ---------------------------------------------------------------------------
# haute run
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_explicit_file(self, runner: CliRunner, project_dir: Path):
        pipeline_file = str(project_dir / "main.py")
        result = runner.invoke(
            cli,
            ["run", pipeline_file],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "test_cli" in result.output
        assert "2 nodes" in result.output
        # Should show per-node results
        assert "source" in result.output
        assert "transform" in result.output
        assert "rows" in result.output

    def test_run_auto_discover(
        self, runner: CliRunner, project_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(project_dir)
        result = runner.invoke(cli, ["run"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "test_cli" in result.output

    def test_run_no_pipeline_found(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["run"])
        assert result.exit_code == 1
        assert "pipeline file not found" in result.output.lower(), (
            f"Expected 'pipeline file not found' in output, got: {result.output!r}"
        )

    def test_run_file_not_found(self, runner: CliRunner):
        result = runner.invoke(cli, ["run", "/nonexistent/pipeline.py"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower(), (
            f"Expected 'not found' in output, got: {result.output!r}"
        )

    def test_run_empty_pipeline(self, runner: CliRunner, tmp_path: Path):
        """A .py file with no pipeline node functions should fail."""
        empty = tmp_path / "empty.py"
        empty.write_text("import polars as pl\nimport haute\npipeline = haute.Pipeline('e')\n")
        result = runner.invoke(cli, ["run", str(empty)])
        assert result.exit_code == 1
        assert "no" in result.output.lower() and "node" in result.output.lower()

    def test_run_reports_node_errors(self, runner: CliRunner, tmp_path: Path):
        """Pipeline with a node that errors should report the failure."""
        data = tmp_path / "data"
        data.mkdir()
        pl.DataFrame({"x": [1]}).write_parquet(data / "d.parquet")

        # Forward slashes keep the generated pipeline cross-platform:
        # a raw Windows path in an f-string produces ``C:\Users\...``
        # which the Python parser then misreads as a ``\U`` escape.
        path = (data / "d.parquet").as_posix()
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("broken")


@pipeline.data_source(path="{path}")
def source() -> pl.DataFrame:
    return pl.scan_parquet("{path}")


@pipeline.polars
def bad(source: pl.DataFrame) -> pl.DataFrame:
    return source.select("nonexistent_column")


pipeline.connect("source", "bad")
'''
        p = tmp_path / "broken.py"
        p.write_text(code)
        result = runner.invoke(cli, ["run", str(p)])
        assert result.exit_code == 1
        assert "failed" in result.output.lower() or "✗" in result.output

    def test_run_shows_output_preview(self, runner: CliRunner, project_dir: Path):
        """Successful run should show output preview from last node."""
        pipeline_file = str(project_dir / "main.py")
        result = runner.invoke(
            cli,
            ["run", pipeline_file],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # The output preview should show data from the last node
        assert "Output" in result.output or "rows" in result.output

    def test_run_shows_pipeline_name(self, runner: CliRunner, project_dir: Path):
        """Run output should display pipeline name and node count."""
        pipeline_file = str(project_dir / "main.py")
        result = runner.invoke(
            cli,
            ["run", pipeline_file],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Pipeline: test_cli" in result.output
        assert "2 nodes" in result.output

    def test_run_shows_per_node_success(self, runner: CliRunner, project_dir: Path):
        """Successful nodes should show row x col counts."""
        pipeline_file = str(project_dir / "main.py")
        result = runner.invoke(
            cli,
            ["run", pipeline_file],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # Each successful node shows rows x cols
        assert "rows" in result.output
        assert "cols" in result.output

    def test_run_parse_error(self, runner: CliRunner, tmp_path: Path):
        """A .py file that raises during parsing should give a parse error."""
        bad = tmp_path / "parse_error.py"
        bad.write_text("raise SyntaxError('deliberate')\n")
        result = runner.invoke(cli, ["run", str(bad)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_run_execute_error(self, runner: CliRunner, tmp_path: Path):
        """When execute_graph raises, the error is reported."""
        from unittest.mock import MagicMock, patch

        from haute.graph_utils import PipelineGraph

        mock_graph = MagicMock(spec=PipelineGraph)
        mock_graph.nodes = [MagicMock()]  # non-empty
        mock_graph.pipeline_name = "test"

        with (
            patch("haute._project.resolve_pipeline_file", return_value=tmp_path / "x.py"),
            patch("haute.parser.parse_pipeline_file", return_value=mock_graph),
            patch(
                "haute.executor.execute_graph",
                side_effect=RuntimeError("execution boom"),
            ),
        ):
            result = runner.invoke(cli, ["run", str(tmp_path / "x.py")])
        assert result.exit_code == 1
        assert "execution boom" in result.output.lower() or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# haute serve (smoke test only - can't test the long-running server)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# haute lint
# ---------------------------------------------------------------------------


class TestLint:
    def test_lint_valid_pipeline(
        self,
        runner: CliRunner,
        project_dir: Path,
    ):
        pipeline_file = str(project_dir / "main.py")
        result = runner.invoke(cli, ["lint", pipeline_file], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "No structural issues" in result.output

    def test_lint_auto_discover(
        self,
        runner: CliRunner,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(project_dir)
        # Create a haute.toml pointing to main.py
        (project_dir / "haute.toml").write_text(
            '[project]\nname = "test"\npipeline = "main.py"\n',
        )
        result = runner.invoke(cli, ["lint"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

    def test_lint_no_pipeline_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ):
        result = runner.invoke(cli, ["lint", str(tmp_path / "nonexistent.py")])
        assert result.exit_code == 1

    def test_lint_empty_pipeline_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ):
        empty = tmp_path / "empty.py"
        empty.write_text(
            "import haute\npipeline = haute.Pipeline('e')\n",
        )
        result = runner.invoke(cli, ["lint", str(empty)])
        assert result.exit_code == 1
        assert "No nodes" in result.output


# ---------------------------------------------------------------------------
# haute smoke (offline error paths only - live endpoint tests require network)
# ---------------------------------------------------------------------------


class TestSmoke:
    def test_smoke_no_toml_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["smoke"])
        assert result.exit_code == 1
        assert "haute.toml" in result.output.lower()

    def test_smoke_no_test_quotes_dir_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n'
            '[deploy]\nmodel_name = "t"\nendpoint_name = "t"\n',
        )
        result = runner.invoke(cli, ["smoke"])
        assert result.exit_code == 1
        assert "test quotes" in result.output.lower()

    def test_smoke_empty_test_quotes_fails(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n'
            '[deploy]\nmodel_name = "t"\nendpoint_name = "t"\n'
            '[test_quotes]\ndir = "tests/quotes"\n',
        )
        (tmp_path / "tests" / "quotes").mkdir(parents=True)
        result = runner.invoke(cli, ["smoke"])
        assert result.exit_code == 1
        assert "no .json" in result.output.lower()


class TestServe:
    def test_serve_no_frontend_no_static_fails(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Without frontend/ or built static, serve should fail with a clear message."""
        monkeypatch.chdir(tmp_path)
        # Mock STATIC_DIR to a non-existent path so we hit the error branch
        # (the real STATIC_DIR may exist from a previous npm run build)
        monkeypatch.setattr("haute.server.STATIC_DIR", tmp_path / "nonexistent_static")
        result = runner.invoke(cli, ["serve", "--no-browser"])
        assert result.exit_code == 1
        assert "frontend" in result.output.lower() or "npm" in result.output.lower()
