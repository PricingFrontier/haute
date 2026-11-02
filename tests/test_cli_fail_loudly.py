"""Fail-loudly correctness tests for Phase 1 Package 1F.

Each test here is a TDD target: it should FAIL against the pre-fix code and
PASS once the corresponding bug is corrected. The package covers six CLI
correctness issues:

- #42  ``haute init`` pyproject.toml parsing — must be TOML-aware, not
       ``str.replace``, so multiple ``dependencies = [`` occurrences and
       unusual whitespace do not corrupt the file.
- #43  ``haute init`` haute-dependency detection — string-grep ``"haute"``
       across the entire file matches comments and other packages (e.g.
       ``# using haute for X`` or ``haute-utils``); it must inspect the
       parsed ``[project].dependencies`` list instead.
- #44  ``haute status`` mlflow-missing message — should use the modern
       ``uv add 'haute[databricks]'`` idiom, matching ``_deploy.py``,
       ``_smoke.py`` and ``_impact.py``.
- #45  ``haute deploy`` CI detection — should recognise GitHub Actions
       (``GITHUB_ACTIONS``), GitLab (``GITLAB_CI``), CircleCI (``CIRCLECI``),
       Azure DevOps (``TF_BUILD``), Buildkite (``BUILDKITE``) each in
       isolation, not only the generic ``CI`` / ``TF_BUILD`` / ``GITLAB_CI``.
- #46  ``haute serve`` port-conflict detection — when the chosen port is
       already bound, emit a clear error naming the port and suggest
       ``--port`` instead of letting uvicorn crash cryptically.
- #49  ``haute impact`` silent ``prod_exists=False`` — a 404 / NotFound
       should set ``prod_exists=False``; timeouts, 500s and other
       transport errors must propagate (fail loudly).
"""

from __future__ import annotations

import socket
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute.cli import cli
from haute.cli._init_cmd import _ensure_haute_dependency
from haute.deploy._impact import ImpactEndpointError

if TYPE_CHECKING:
    from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_impact_toml(
    tmp_path: Path,
    *,
    target: str = "databricks",
    staging_url: str = "",
    prod_url: str = "",
) -> None:
    """Write a minimal haute.toml + impact dataset for ``haute impact`` tests."""
    toml = (
        f'[project]\nname = "t"\npipeline = "main.py"\n'
        f'[deploy]\nmodel_name = "test-model"\nendpoint_name = "test-ep"\n'
        f'target = "{target}"\n'
        f'[safety]\nimpact_dataset = "data/impact.parquet"\n'
        f'[ci]\nprovider = "github"\n'
        f'[ci.staging]\nendpoint_suffix = "-staging"\n'
        f'endpoint_url = "{staging_url}"\n'
        f'[ci.production]\nendpoint_url = "{prod_url}"\n'
    )
    (tmp_path / "haute.toml").write_text(toml)
    (tmp_path / ".git").mkdir(exist_ok=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    df = pl.DataFrame({"VehPower": [5, 6], "premium": [100.0, 200.0]})
    df.write_parquet(data_dir / "impact.parquet")


# ---------------------------------------------------------------------------
# #42 — pyproject.toml parsing must be TOML-aware, not ``str.replace``
# ---------------------------------------------------------------------------


class TestPyprojectParseIsTomlAware:
    """pyproject.toml must remain valid TOML after ``_ensure_haute_dependency``.

    The pre-fix code uses ``text.replace("dependencies = [", ...)`` which:
    - only handles the exact substring (whitespace variants break it),
    - mutates the *first* occurrence even when a second ``dependencies = [``
      sits inside a different table (e.g. ``[project.optional-dependencies.x]``),
    - has no awareness of TOML structure at all.

    A correct implementation uses a TOML library and only touches the
    ``[project].dependencies`` array.
    """

    def test_result_is_still_valid_toml_basic(self, tmp_path: Path) -> None:
        """Round-trip through tomllib must succeed after the command runs."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "foo"\nversion = "0.1.0"\ndependencies = [\n    "polars",\n]\n'
        )
        _ensure_haute_dependency(pyproject, "foo")
        # Must parse without raising
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        assert "haute" in data["project"]["dependencies"]
        assert "polars" in data["project"]["dependencies"]

    def test_multiple_dependencies_arrays_only_project_is_touched(self, tmp_path: Path) -> None:
        """A second ``dependencies = [`` in another table must be left alone.

        If a Poetry-style ``[tool.poetry]`` block (or any other tool-specific
        table) appears *before* ``[project]`` in the file and uses the exact
        key name ``dependencies``, the pre-fix ``str.replace(..., count=1)``
        mutates the wrong table — injecting ``"haute"`` into a tool-specific
        dependency list and leaving ``[project].dependencies`` untouched.
        """
        pyproject = tmp_path / "pyproject.toml"
        # Poetry-style block appears BEFORE [project], so its "dependencies = ["
        # is the first occurrence the str.replace() will hit.
        pyproject.write_text(
            "[tool.poetry]\n"
            'name = "foo-example"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n    "requests",\n]\n'
            "\n"
            "[project]\n"
            'name = "foo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n    "polars",\n]\n'
        )
        _ensure_haute_dependency(pyproject, "foo")
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        # haute must land in [project].dependencies
        assert "haute" in data["project"]["dependencies"]
        assert "polars" in data["project"]["dependencies"]
        # The tool.poetry dependencies list must be pristine
        assert "haute" not in data["tool"]["poetry"]["dependencies"]
        assert "requests" in data["tool"]["poetry"]["dependencies"]

    def test_unusual_whitespace_variants_do_not_duplicate(self, tmp_path: Path) -> None:
        """``dependencies=[`` (no spaces) and ``dependencies =  [`` must still work.

        Pre-fix these don't match the literal ``"dependencies = ["`` string,
        so the code falls through to the else-branch and *appends* a second
        ``[project]`` section — the resulting file has two ``[project]``
        tables and is invalid TOML.
        """
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            'name = "foo"\n'
            'version = "0.1.0"\n'
            'dependencies=[\n    "polars",\n]\n'  # no space around =
        )
        _ensure_haute_dependency(pyproject, "foo")
        # Must still parse
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        deps = data["project"]["dependencies"]
        assert "haute" in deps
        # haute must not be duplicated
        assert deps.count("haute") == 1
        # The file must have exactly one [project] table
        text = pyproject.read_text()
        assert text.count("[project]\n") == 1 or text.count("[project]") == 1

    def test_init_against_existing_pyproject_preserves_all_dependencies_keys(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end: ``haute init`` must leave every ``dependencies`` key intact.

        This is the user-facing scenario from #42: the user already has a
        pyproject.toml with multiple dependency-looking keys (project,
        optional-dependencies, tool-specific). After ``haute init`` the file
        must still be valid TOML and every pre-existing dependency must
        survive.
        """
        monkeypatch.chdir(tmp_path)
        original = (
            "[project]\n"
            'name = "my_project"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.11"\n'
            "dependencies = [\n"
            '    "polars",\n'
            '    "pydantic",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            "test = [\n"
            '    "pytest",\n'
            "]\n"
        )
        (tmp_path / "pyproject.toml").write_text(original)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        # File must still be valid TOML
        with open(tmp_path / "pyproject.toml", "rb") as fh:
            data = tomllib.load(fh)

        # All pre-existing dependencies preserved
        proj_deps = data["project"]["dependencies"]
        assert "polars" in proj_deps
        assert "pydantic" in proj_deps
        assert "haute" in proj_deps
        # Optional-dependencies preserved and untouched
        assert data["project"]["optional-dependencies"]["test"] == ["pytest"]


# ---------------------------------------------------------------------------
# #43 — haute-dependency detection must inspect parsed dependencies
# ---------------------------------------------------------------------------


class TestHauteDependencyDetectionIsStructural:
    """The ``if "haute" not in text`` check is a string grep over the entire
    file — comments, doc-strings, or packages whose names contain ``haute``
    (e.g. ``haute-utils``) all produce false positives and cause the add
    step to be silently skipped.
    """

    def test_haute_in_comment_does_not_fool_detection(self, tmp_path: Path) -> None:
        """A comment mentioning 'haute' should not block injection."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            'name = "foo"\n'
            'version = "0.1.0"\n'
            "# using haute for X — note this is a comment only\n"
            'dependencies = [\n    "polars",\n]\n'
        )
        _ensure_haute_dependency(pyproject, "foo")
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        # The comment must not have blocked the addition
        assert "haute" in data["project"]["dependencies"]

    def test_haute_substring_package_name_does_not_fool_detection(self, tmp_path: Path) -> None:
        """A dep like 'haute-utils' should not match the 'haute' check."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            'name = "foo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n    "haute-utils>=1.0",\n]\n'
        )
        _ensure_haute_dependency(pyproject, "foo")
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        # Both must be present
        deps = data["project"]["dependencies"]
        assert any(d == "haute" or d.startswith("haute==") or d.startswith("haute>") for d in deps)
        assert any(d.startswith("haute-utils") for d in deps)

    def test_actual_haute_entry_is_detected_and_not_duplicated(self, tmp_path: Path) -> None:
        """A real ``"haute"`` entry must be recognised and not duplicated."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            'name = "foo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n    "haute",\n    "polars",\n]\n'
        )
        _ensure_haute_dependency(pyproject, "foo")
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        deps = data["project"]["dependencies"]
        # Exactly one occurrence of "haute" (as opposed to "haute-*")
        exact = [d for d in deps if d == "haute"]
        assert len(exact) == 1


# ---------------------------------------------------------------------------
# #44 — ``haute status`` install instruction
# ---------------------------------------------------------------------------


class TestStatusInstallInstructionIsCorrect:
    """The install instruction for mlflow in ``haute status`` must match the
    pattern used by ``_deploy.py`` / ``_smoke.py`` / ``_impact.py``:
    ``uv add 'haute[databricks]'`` (single-quoted to survive the shell).
    """

    def test_mlflow_missing_message_uses_uv_add_haute_databricks(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n[deploy]\nmodel_name = "motor-pricing"\n'
        )
        with patch(
            "haute.deploy._mlflow.get_deploy_status",
            side_effect=ImportError("No module named 'mlflow'"),
        ):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 1
        # Must recommend ``uv add 'haute[databricks]'`` exactly
        assert "uv add 'haute[databricks]'" in result.output
        # Must NOT use the outdated ``uv pip install haute[databricks]`` form
        assert "uv pip install haute[databricks]" not in result.output


# ---------------------------------------------------------------------------
# #45 — CI detection must recognise all major providers
# ---------------------------------------------------------------------------


class TestDeployCiDetection:
    """``haute deploy`` blocks local deploys unless a CI environment is
    detected. Each supported CI provider sets at least one well-known env
    var, and deploy must honour each of them in isolation.

    Pre-fix, only ``CI``, ``TF_BUILD``, and ``GITLAB_CI`` were checked.
    That misses ``GITHUB_ACTIONS``, ``CIRCLECI`` (which relies on ``CI``
    but we want explicit support), and ``BUILDKITE``, and makes the intent
    of the check unclear.
    """

    @staticmethod
    def _clear_ci_env(monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove every CI-ish env var so each test starts from a clean slate."""
        for var in (
            "CI",
            "TF_BUILD",
            "GITLAB_CI",
            "GITHUB_ACTIONS",
            "CIRCLECI",
            "BUILDKITE",
        ):
            monkeypatch.delenv(var, raising=False)

    @staticmethod
    def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up a minimal deployable project, with a mocked deploy backend."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "t"\npipeline = "main.py"\n'
            '[deploy]\nmodel_name = "test-model"\nendpoint_name = "test-ep"\n'
            '[test_quotes]\ndir = "tests/quotes"\n'
        )
        (tmp_path / "main.py").write_text("# pipeline\n")

    def _mock_resolved(self) -> MagicMock:
        resolved = MagicMock()
        resolved.pruned_graph.nodes = [MagicMock()]
        resolved.pruned_graph.edges = []
        resolved.removed_node_ids = []
        resolved.artifacts = {}
        resolved.input_node_ids = ["q"]
        resolved.output_node_id = "out"
        resolved.input_schema = {"x": "Int64"}
        resolved.output_schema = {"p": "Float64"}
        return resolved

    @pytest.mark.parametrize(
        "env_var,value",
        [
            ("GITHUB_ACTIONS", "true"),
            ("GITLAB_CI", "true"),
            ("CIRCLECI", "true"),
            ("TF_BUILD", "True"),
            ("BUILDKITE", "true"),
        ],
    )
    def test_ci_provider_detected_in_isolation(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        env_var: str,
        value: str,
    ) -> None:
        """Setting a single CI env var must allow deploy to proceed past the gate.

        We verify this by mocking the backend and checking that deploy does
        *not* exit with the "Deploys must go through CI/CD" error. A real
        deploy still requires valid config, so we stub out the rest of the
        flow; the only thing under test here is the CI-detection branch.
        """
        self._clear_ci_env(monkeypatch)
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv(env_var, value)

        resolved = self._mock_resolved()
        deploy_result = MagicMock()
        deploy_result.build_dir = None
        deploy_result.model_name = "test-model"
        deploy_result.model_version = 1
        deploy_result.endpoint_url = "https://host/ep/invocations"
        deploy_result.model_uri = None

        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
            patch("haute.deploy.deploy_resolved", return_value=deploy_result),
        ):
            result = runner.invoke(cli, ["deploy"])

        # With the CI var set, the gate must allow deploy through. A failure
        # here means the CI detection did not recognise this env var.
        assert "must go through ci/cd" not in result.output.lower(), (
            f"{env_var}={value} should be recognised as CI, got: {result.output}"
        )

    def test_no_ci_env_blocks_deploy(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sanity check: with no CI env var set, deploy is blocked (control)."""
        self._clear_ci_env(monkeypatch)
        self._setup(tmp_path, monkeypatch)

        result = runner.invoke(cli, ["deploy"])
        assert result.exit_code == 1
        assert "ci/cd" in result.output.lower() or "dry-run" in result.output.lower()


# ---------------------------------------------------------------------------
# #46 — ``haute serve`` must detect port conflicts before launching uvicorn
# ---------------------------------------------------------------------------


class TestServePortConflictDetection:
    """When the requested port is already bound, ``haute serve`` must fail
    with a clear, actionable error that names the port and suggests the
    ``--port`` flag — not let uvicorn crash with a cryptic ``OSError``.
    """

    @pytest.fixture()
    def bound_socket(self):
        """Bind a TCP socket to 127.0.0.1:<random> so the port is occupied."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            yield port
        finally:
            s.close()

    def test_serve_fails_loudly_when_port_is_bound(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        bound_socket: int,
    ) -> None:
        """Serve must exit non-zero with a port-named error, not crash uvicorn."""
        monkeypatch.chdir(tmp_path)
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        (static / "assets").mkdir()
        port = bound_socket

        # uvicorn.run must never be called — the pre-flight check must fire first.
        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.cli._serve._source_checkout_root", return_value=None),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_uvicorn_run,
        ):
            result = runner.invoke(
                cli,
                ["serve", "--no-browser", "--host", "127.0.0.1", "--port", str(port)],
            )

        assert result.exit_code != 0, (
            f"Expected non-zero exit for bound port {port}, got output: {result.output}"
        )
        output = result.output.lower()
        # The port number must appear in the error message
        assert str(port) in result.output, (
            f"Expected port {port} to be named in the error output:\n{result.output}"
        )
        # The error must mention ``--port`` so the user knows the remedy
        assert "--port" in result.output, (
            f"Expected '--port' suggestion in the error:\n{result.output}"
        )
        # A generic indication that the port is in use / already bound
        assert any(phrase in output for phrase in ("already", "in use", "bound", "conflict")), (
            f"Expected a port-conflict phrase in the error:\n{result.output}"
        )
        # uvicorn must not have been invoked at all
        mock_uvicorn_run.assert_not_called()

    def test_serve_succeeds_when_port_is_free(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sanity check: when the port is free, serve proceeds to uvicorn."""
        monkeypatch.chdir(tmp_path)
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        (static / "assets").mkdir()

        # Pick a port that's currently free.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
        s.close()

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.cli._serve._source_checkout_root", return_value=None),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_uvicorn_run,
        ):
            result = runner.invoke(
                cli,
                [
                    "serve",
                    "--no-browser",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(free_port),
                ],
            )

        assert result.exit_code == 0, result.output
        mock_uvicorn_run.assert_called_once()


# ---------------------------------------------------------------------------
# #49 — ``haute impact`` must not silently swallow exceptions from prod
# ---------------------------------------------------------------------------


class TestImpactProdExistsFailsLoudly:
    """Production is skipped as a first deployment only on an authoritative record.

    - Databricks: the workspace's NotFound / ResourceDoesNotExist for the endpoint.
    - HTTP: no ``deploy/`` release tag on origin (DEP-R11); a 404 is never one.
    - Timeouts, 5xx, authentication and connection errors propagate so the caller
      sees the real failure.
    """

    @pytest.fixture(autouse=True)
    def _serving_pair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Impact reaches both endpoints with the serving pair, as CI provides it."""
        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://rating.example")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "rating-token")

    # ---- databricks branch ----

    def test_databricks_404_sets_prod_exists_false(self) -> None:
        """A NotFound-typed exception from prod endpoint must be treated as
        'no prod yet' — this is the correct path already and must stay working."""
        from haute.cli._impact import _impact_databricks

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.side_effect = type("NotFound", (Exception,), {})(
            "endpoint not found"
        )

        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("haute.deploy._config._load_env"),
            patch(
                "haute.deploy._impact.score_endpoint_batched",
                return_value=[{"p": 1.0}],
            ),
        ):
            staging, prod, first_deploy = _impact_databricks("stg", "prod", [{"x": 1}], 100)

        assert first_deploy == "the workspace has no production endpoint 'prod'."
        assert prod == []

    def test_databricks_resource_does_not_exist_sets_prod_exists_false(
        self,
    ) -> None:
        """Databricks raises ResourceDoesNotExist for missing endpoints."""
        from haute.cli._impact import _impact_databricks

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.side_effect = type("ResourceDoesNotExist", (Exception,), {})(
            "does not exist"
        )

        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("haute.deploy._config._load_env"),
            patch(
                "haute.deploy._impact.score_endpoint_batched",
                return_value=[{"p": 1.0}],
            ),
        ):
            _, _, first_deploy = _impact_databricks("stg", "prod", [{"x": 1}], 100)
        assert first_deploy is not None

    def test_databricks_timeout_must_raise(self) -> None:
        """A network timeout is NOT a 'no prod' signal — it must propagate."""
        from haute.cli._impact import _impact_databricks

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.side_effect = TimeoutError("read timed out")

        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("haute.deploy._config._load_env"),
            patch(
                "haute.deploy._impact.score_endpoint_batched",
                return_value=[{"p": 1.0}],
            ),
            pytest.raises(TimeoutError, match="timed out"),
        ):
            _impact_databricks("stg", "prod", [{"x": 1}], 100)

    def test_databricks_500_must_raise(self) -> None:
        """A 500 Internal Server Error is NOT a 'no prod' signal — it must propagate."""
        from haute.cli._impact import _impact_databricks

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.side_effect = RuntimeError("500 Internal Server Error")

        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("haute.deploy._config._load_env"),
            patch(
                "haute.deploy._impact.score_endpoint_batched",
                return_value=[{"p": 1.0}],
            ),
            pytest.raises(RuntimeError, match="500"),
        ):
            _impact_databricks("stg", "prod", [{"x": 1}], 100)

    def test_databricks_generic_exception_must_raise(self) -> None:
        """Any non-NotFound exception from the prod lookup must propagate."""
        from haute.cli._impact import _impact_databricks

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.get.side_effect = ConnectionError("connection refused")

        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("haute.deploy._config._load_env"),
            patch(
                "haute.deploy._impact.score_endpoint_batched",
                return_value=[{"p": 1.0}],
            ),
            pytest.raises(ConnectionError, match="refused"),
        ):
            _impact_databricks("stg", "prod", [{"x": 1}], 100)

    # ---- http branch ----
    # Production is compared once origin records a release of it: the production job
    # tags each one deploy/... A real origin, with or without such a tag, decides.

    @staticmethod
    def _project(tmp_path: Path, *, released: bool) -> Path:
        def git(cwd: Path, *args: str) -> None:
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

        origin = tmp_path / "origin.git"
        project = tmp_path / "project"
        project.mkdir()
        git(tmp_path, "init", "--bare", str(origin))
        git(project, "init", "-b", "main")
        git(project, "remote", "add", "origin", str(origin))
        if released:
            git(
                project,
                "-c",
                "user.name=CI",
                "-c",
                "user.email=ci@example",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--allow-empty",
                "-m",
                "release",
            )
            git(project, "tag", "deploy/abc1234")
            git(project, "push", "origin", "deploy/abc1234")
        return project

    def test_http_without_a_release_skips_production_whatever_its_address(
        self, tmp_path: Path
    ) -> None:
        """DEP-R11: before the first release, production is not contacted at all."""
        from haute.cli._impact import _impact_http

        project = self._project(tmp_path, released=False)
        with patch(
            "haute.deploy._impact.score_http_endpoint_batched",
            return_value=[{"p": 1.0}],
        ) as score:
            _, prod, first_deploy = _impact_http(
                "http://stg", "http://prod-typo", [{"x": 1}], 100, project
            )
        assert prod == []
        assert first_deploy == "no production release is recorded (origin has no deploy/ tag)."
        assert [call.args[0] for call in score.call_args_list] == ["http://stg"]

    @pytest.mark.parametrize(
        "failure",
        [
            ImpactEndpointError("http://prod/quote", "HTTP 404: not found", status=404),
            ImpactEndpointError("http://prod/quote", "HTTP 401: unauthorised", status=401),
            ImpactEndpointError("http://prod/quote", "no answer (timed out)"),
            ImpactEndpointError("http://prod/quote", "no answer (connection refused)"),
        ],
        ids=["404", "authentication", "timeout", "unreachable"],
    )
    def test_http_after_a_release_production_must_answer(
        self, tmp_path: Path, failure: ImpactEndpointError
    ) -> None:
        """DEP-R11: a mistyped or failing production address is never a first deploy."""
        from haute.cli._impact import _impact_http

        project = self._project(tmp_path, released=True)
        with (
            patch(
                "haute.deploy._impact.score_http_endpoint_batched",
                side_effect=[[{"p": 1.0}], failure],
            ),
            pytest.raises(ImpactEndpointError, match="http://prod/quote") as raised,
        ):
            _impact_http("http://stg", "http://prod", [{"x": 1}], 100, project)
        assert raised.value is failure

    def test_http_after_a_release_compares_production(self, tmp_path: Path) -> None:
        from haute.cli._impact import _impact_http

        project = self._project(tmp_path, released=True)
        with patch(
            "haute.deploy._impact.score_http_endpoint_batched",
            side_effect=[[{"p": 1.0}], [{"p": 2.0}]],
        ):
            staging, prod, first_deploy = _impact_http(
                "http://stg", "http://prod", [{"x": 1}], 100, project
            )
        assert (staging, prod, first_deploy) == ([{"p": 1.0}], [{"p": 2.0}], None)

    def test_http_after_a_release_without_a_production_address_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from haute.cli._impact import _impact_http

        project = self._project(tmp_path, released=True)
        with pytest.raises(SystemExit):
            _impact_http("http://stg", "", [{"x": 1}], 100, project)
        assert "[ci.production] endpoint_url" in capsys.readouterr().err

    def test_http_tags_that_cannot_be_read_fail_closed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without an origin to read, impact cannot know whether production exists."""
        from haute.cli._impact import _impact_http

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
        with pytest.raises(SystemExit):
            _impact_http("http://stg", "http://prod", [{"x": 1}], 100, project)
        assert "Could not read the release tags on origin" in capsys.readouterr().err
