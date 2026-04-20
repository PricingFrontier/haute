"""Tests for Phase 5 Wave 9B — CLI architecture cleanup.

This file covers four concerns from the wave plan:

* **#106 `model_name` optional everywhere** — every CLI command that accepts
  ``--model-name`` (or a positional ``model_name``) must fall back to the
  ``[deploy].model_name`` value in ``haute.toml`` when the flag is absent.
  A ``resolve_model_name`` helper encapsulates the precedence rule.
* **#129 Single ``resolve_pipeline_file()`` helper** — the project-level
  helper lives in :mod:`haute._project` so CLI and non-CLI callers share a
  single resolution strategy.  The CLI source tree must not contain ad-hoc
  pipeline-resolution patterns.
* **#130 Extract ``handle_*(config)`` pure functions** — every CLI command
  in ``src/haute/cli/_*.py`` is split into (a) a thin Click entry point
  that parses args into a config dataclass and (b) a ``handle_*`` pure
  function that does the work.  The Click body stays short.
* **#131 ``DeployConfig.from_toml`` / ``from_cli_args``** — the legacy
  ``_load_deploy_config`` helper is replaced by two explicit classmethods
  on :class:`haute.deploy._config.DeployConfig`.

All tests are expected to fail before the dev patch lands; that is the
red phase of the TDD cycle.  Failures should carry clear messages so the
dev can tick them off one at a time.

All filesystem writes go through ``tmp_path`` and tests that change the
working directory use ``monkeypatch.chdir``.  Tests never touch real
deployment backends — ``mlflow``, the Databricks SDK, and the container
deploy path are all patched at their call sites.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers — reused across the file
# ---------------------------------------------------------------------------

# Absolute path to src/haute/cli/ — AST walks iterate its _*.py files.
_CLI_DIR = Path(__file__).resolve().parent.parent / "src" / "haute" / "cli"

# The Click-decorated command modules that belong to the wave's scope.  The
# corresponding handle function and config dataclass are named by convention:
# ``_deploy.py`` → ``handle_deploy`` + ``DeployCommandConfig`` (or the
# canonical :class:`haute.deploy._config.DeployConfig` for the deploy
# command, which already has a ``*Config`` type).  The dev agent chooses the
# exact name; tests look up by convention and fall back to AST inspection
# when the name differs.
_CLI_COMMAND_MODULES: tuple[tuple[str, str], ...] = (
    ("haute.cli._deploy", "deploy"),
    ("haute.cli._impact", "impact"),
    ("haute.cli._init_cmd", "init"),
    ("haute.cli._lint", "lint"),
    ("haute.cli._run", "run"),
    ("haute.cli._serve", "serve"),
    ("haute.cli._smoke", "smoke"),
    ("haute.cli._status", "status"),
    ("haute.cli._train", "train"),
)


def _write_toml(
    tmp_path: Path,
    *,
    model_name: str = "toml_model",
    pipeline: str = "main.py",
    extra: str = "",
) -> Path:
    """Create a minimal haute.toml under *tmp_path* and return its path.

    The caller chooses ``pipeline`` and ``model_name``; ``extra`` is
    appended verbatim so individual tests can layer additional sections
    (e.g. ``[deploy.databricks]``) without duplicating the scaffolding.
    """
    content = (
        "[project]\n"
        f'name = "demo"\n'
        f'pipeline = "{pipeline}"\n'
        "[deploy]\n"
        f'model_name = "{model_name}"\n'
    )
    if extra:
        content += extra
    toml_path = tmp_path / "haute.toml"
    toml_path.write_text(content, encoding="utf-8")
    return toml_path


def _touch_pipeline(tmp_path: Path, name: str = "main.py") -> Path:
    """Write a stub pipeline Python file and return its path."""
    path = tmp_path / name
    path.write_text("# placeholder pipeline\n", encoding="utf-8")
    return path


def _mock_resolved() -> MagicMock:
    """Build a mock ``ResolvedDeploy`` matching the shape used by ``deploy``."""
    resolved = MagicMock()
    resolved.pruned_graph.nodes = [MagicMock()]
    resolved.pruned_graph.edges = []
    resolved.removed_node_ids = []
    resolved.artifacts = {}
    resolved.input_node_ids = ["quotes"]
    resolved.output_node_id = "output"
    resolved.input_schema = {"x": "Int64"}
    resolved.output_schema = {"premium": "Float64"}
    return resolved


# ===========================================================================
# #106 — resolve_model_name helper and CLI-wide optional model_name
# ===========================================================================


class TestResolveModelNameHelper:
    """Unit tests for ``resolve_model_name(cli_arg, toml_path)``.

    The helper encodes the precedence rule used across the CLI:

        CLI flag  >  haute.toml  >  error
    """

    def test_cli_arg_wins_over_toml(self, tmp_path: Path) -> None:
        """CLI flag value always takes priority over the TOML value."""
        from haute.cli._helpers import resolve_model_name

        toml = _write_toml(tmp_path, model_name="toml_model")
        assert resolve_model_name("cli_override", toml) == "cli_override"

    def test_toml_used_when_cli_arg_none(self, tmp_path: Path) -> None:
        """A ``None`` CLI arg means 'fall back to TOML'."""
        from haute.cli._helpers import resolve_model_name

        toml = _write_toml(tmp_path, model_name="from_toml")
        assert resolve_model_name(None, toml) == "from_toml"

    def test_no_cli_no_toml_raises(self, tmp_path: Path) -> None:
        """No CLI arg AND no TOML path → clear error instructing the user."""
        from haute.cli._helpers import resolve_model_name

        # No toml_path provided at all.
        with pytest.raises((SystemExit, ValueError, RuntimeError)) as excinfo:
            resolve_model_name(None, None)
        msg = str(excinfo.value).lower()
        assert "model-name" in msg or "haute project" in msg or "haute.toml" in msg, (
            f"Error message must explain how to fix it — got {excinfo.value!r}"
        )

    def test_toml_path_points_to_missing_file_raises(self, tmp_path: Path) -> None:
        """A toml_path that doesn't exist is a programmer bug, not a fallback."""
        from haute.cli._helpers import resolve_model_name

        bogus = tmp_path / "does-not-exist.toml"
        with pytest.raises((FileNotFoundError, SystemExit, ValueError)):
            resolve_model_name(None, bogus)

    def test_toml_missing_deploy_section_raises(self, tmp_path: Path) -> None:
        """A haute.toml without a ``[deploy]`` block cannot supply model_name."""
        from haute.cli._helpers import resolve_model_name

        toml = tmp_path / "haute.toml"
        toml.write_text("[project]\nname = 'demo'\n", encoding="utf-8")
        with pytest.raises((SystemExit, ValueError, RuntimeError, KeyError)):
            resolve_model_name(None, toml)


class TestDeployModelNameOptional:
    """``haute deploy --model-name`` is optional everywhere.

    The ``deploy`` command already makes ``--model-name`` optional, but it
    must use :func:`resolve_model_name` so behaviour is consistent with
    every other CLI command.
    """

    def test_deploy_reads_model_name_from_toml(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without --model-name, deploy reads the name from haute.toml."""
        monkeypatch.chdir(tmp_path)
        _write_toml(tmp_path, model_name="my_toml_model")
        _touch_pipeline(tmp_path, "main.py")

        resolved = _mock_resolved()
        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
        ):
            result = runner.invoke(cli, ["deploy", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "my_toml_model" in result.output, (
            f"Expected 'my_toml_model' in output — got:\n{result.output}"
        )

    def test_deploy_cli_flag_wins_over_toml(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--model-name cli_override`` beats the TOML value."""
        monkeypatch.chdir(tmp_path)
        _write_toml(tmp_path, model_name="toml_model")
        _touch_pipeline(tmp_path, "main.py")

        resolved = _mock_resolved()
        with (
            patch("haute.deploy._config.resolve_config", return_value=resolved),
            patch("haute.deploy._validators.validate_deploy", return_value=[]),
            patch("haute.deploy._validators.score_test_quotes", return_value=[]),
        ):
            result = runner.invoke(
                cli,
                ["deploy", "--dry-run", "--model-name", "cli_override"],
            )

        assert result.exit_code == 0, result.output
        assert "cli_override" in result.output
        assert "toml_model" not in result.output.splitlines()[-1], (
            "CLI override must replace the TOML value in the 'Deploying pipeline' line"
        )

    def test_deploy_without_toml_or_flag_errors_with_hint(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No TOML + no flag must fail loudly with a user-facing hint."""
        monkeypatch.chdir(tmp_path)
        # No haute.toml, no --model-name.  The pipeline file resolution
        # should also fail, but the error message still needs to point
        # at the two options the user actually has.
        result = runner.invoke(cli, ["deploy", "--dry-run"])
        assert result.exit_code != 0, result.output
        msg = result.output.lower()
        # Either the model_name complaint OR the pipeline complaint is
        # acceptable — both are "run inside a project or pass the flag"
        # problems.
        assert any(
            hint in msg for hint in ("model-name", "haute.toml", "pipeline", "haute project")
        ), f"Error must explain the fix — got:\n{result.output}"


class TestStatusModelNameOptional:
    """``haute status`` takes ``model_name`` as a positional arg today.

    The arg is already optional, but must use :func:`resolve_model_name`
    so behaviour is consistent with ``deploy``.
    """

    def test_status_reads_model_name_from_toml(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without a positional arg, status uses the TOML model_name."""
        monkeypatch.chdir(tmp_path)
        _write_toml(tmp_path, model_name="my_toml_model")

        mock_info = {
            "model_name": "my_toml_model",
            "latest_version": 1,
            "status": "READY",
        }
        mock_fn = MagicMock(return_value=mock_info)
        with patch("haute.deploy._mlflow.get_deploy_status", mock_fn):
            result = runner.invoke(cli, ["status"])

        assert result.exit_code == 0, result.output
        # Resolver must have passed the TOML value through.
        mock_fn.assert_called_once_with(
            "my_toml_model",
            catalog="main",
            schema="pricing",
        )

    def test_status_cli_arg_wins_over_toml(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit positional arg replaces the TOML value."""
        monkeypatch.chdir(tmp_path)
        _write_toml(tmp_path, model_name="toml_model")

        mock_info = {
            "model_name": "cli_override",
            "latest_version": 1,
            "status": "READY",
        }
        mock_fn = MagicMock(return_value=mock_info)
        with patch("haute.deploy._mlflow.get_deploy_status", mock_fn):
            result = runner.invoke(cli, ["status", "cli_override"])

        assert result.exit_code == 0, result.output
        mock_fn.assert_called_once_with(
            "cli_override",
            catalog="main",
            schema="pricing",
        )

    def test_status_no_toml_no_arg_errors_with_hint(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without either source the user must be told how to fix it."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["status"])
        assert result.exit_code != 0
        msg = result.output.lower()
        assert any(
            h in msg for h in ("model_name", "model-name", "haute.toml", "haute project")
        ), f"Status error must mention the fix — got:\n{result.output}"


# ===========================================================================
# #129 — single resolve_pipeline_file() helper in haute._project
# ===========================================================================


class TestProjectResolvePipelineFile:
    """The canonical :func:`haute._project.resolve_pipeline_file` helper.

    Lives in ``haute._project`` so CLI and programmatic callers share one
    resolution strategy.  The helper:

    1. Returns ``<cwd>/main.py`` when invoked with ``None``.
    2. Resolves an explicit path to its absolute form.
    3. Raises :class:`FileNotFoundError` when the path doesn't exist.
    4. Looks for ``main.py`` inside a directory argument.
    5. Resolves relative paths against :func:`pathlib.Path.cwd`.
    """

    def test_none_returns_cwd_main_py(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``None`` → ``<cwd>/main.py`` (the project-wide default)."""
        from haute._project import resolve_pipeline_file

        monkeypatch.chdir(tmp_path)
        _touch_pipeline(tmp_path, "main.py")
        result = resolve_pipeline_file(None)
        assert result.resolve() == (tmp_path / "main.py").resolve()

    def test_explicit_existing_path_returns_absolute(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit path that exists → resolved absolute Path."""
        from haute._project import resolve_pipeline_file

        monkeypatch.chdir(tmp_path)
        pipeline = _touch_pipeline(tmp_path, "custom.py")
        result = resolve_pipeline_file(pipeline)
        assert result == pipeline.resolve()
        assert result.is_absolute()

    def test_missing_path_raises_filenotfound(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-existent explicit path → :class:`FileNotFoundError` w/ hint."""
        from haute._project import resolve_pipeline_file

        monkeypatch.chdir(tmp_path)
        missing = tmp_path / "nope.py"
        with pytest.raises(FileNotFoundError) as excinfo:
            resolve_pipeline_file(missing)
        assert "nope.py" in str(excinfo.value), (
            "Error message must name the missing file so the user can fix it"
        )

    def test_directory_path_finds_main_py(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Directory argument → ``<dir>/main.py``."""
        from haute._project import resolve_pipeline_file

        sub = tmp_path / "rating"
        sub.mkdir()
        expected = sub / "main.py"
        expected.write_text("# rating pipeline\n", encoding="utf-8")

        result = resolve_pipeline_file(sub)
        assert result.resolve() == expected.resolve()

    def test_directory_without_main_py_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Directory with no ``main.py`` → :class:`FileNotFoundError`."""
        from haute._project import resolve_pipeline_file

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            resolve_pipeline_file(empty_dir)

    def test_relative_path_resolved_against_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Relative path → resolved against :func:`pathlib.Path.cwd`."""
        from haute._project import resolve_pipeline_file

        monkeypatch.chdir(tmp_path)
        _touch_pipeline(tmp_path, "rel.py")
        result = resolve_pipeline_file(Path("rel.py"))
        assert result.is_absolute()
        assert result.resolve() == (tmp_path / "rel.py").resolve()


class TestCliNoAdHocPipelineResolution:
    """The CLI source tree must not contain ad-hoc pipeline resolution.

    Each commandmodule must delegate to
    :func:`haute._project.resolve_pipeline_file` (or the thin CLI wrapper
    that calls it).  We AST-walk every ``_*.py`` under ``src/haute/cli/``
    and look for patterns that would indicate the refactor is
    incomplete:

    * ``Path("pipeline.py")`` / ``Path("main.py")`` as a literal.
    * ``pipeline_file or "pipeline.py"`` / ``args.pipeline or "pipeline.py"``.
    """

    def test_no_hardcoded_main_py_path_literal(self) -> None:
        """``Path("main.py")`` / ``Path("pipeline.py")`` never appears in CLI modules.

        The project default must live inside ``resolve_pipeline_file`` —
        any literal in ``cli/`` means that helper was bypassed.
        """
        offenders: list[str] = []
        for py_file in _CLI_DIR.glob("_*.py"):
            # ``_helpers.py`` is the re-export shim; it's allowed to mention
            # the literal once during the transition, so we check every
            # other module strictly.
            tree = ast.parse(py_file.read_text(encoding="utf-8"), str(py_file))
            for node in ast.walk(tree):
                # Pattern: Path("main.py") or Path("pipeline.py")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Path"
                    and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value in ("main.py", "pipeline.py")
                ):
                    # Allow it inside _helpers.py transitionally? No — the
                    # resolve_pipeline_file helper should move to
                    # haute._project so the CLI side is purely a caller.
                    offenders.append(f"{py_file.name}:{node.lineno} → Path({node.args[0].value!r})")
        assert not offenders, (
            "Ad-hoc pipeline path literals in CLI modules — must delegate to "
            "haute._project.resolve_pipeline_file:\n  " + "\n  ".join(offenders)
        )

    def test_no_or_fallback_to_pipeline_literal(self) -> None:
        """``<anything> or "pipeline.py"`` / ``or "main.py"`` — no such fallbacks.

        The centralised helper already owns the default. A local
        ``x or "main.py"`` means somebody re-invented the fallback and
        will drift out of sync.
        """
        offenders: list[str] = []
        for py_file in _CLI_DIR.glob("_*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), str(py_file))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.BoolOp)
                    and isinstance(node.op, ast.Or)
                ):
                    for val in node.values:
                        if (
                            isinstance(val, ast.Constant)
                            and isinstance(val.value, str)
                            and val.value in ("main.py", "pipeline.py")
                        ):
                            offenders.append(
                                f"{py_file.name}:{node.lineno} → ... or {val.value!r}"
                            )
        assert not offenders, (
            "Ad-hoc ``or 'main.py'`` fallbacks in CLI modules:\n  "
            + "\n  ".join(offenders)
        )

    def test_cli_delegates_to_project_helper(self) -> None:
        """CLI modules that name ``resolve_pipeline_file`` must import it from haute._project.

        After the refactor, the single source of truth is
        ``haute._project.resolve_pipeline_file`` — not the CLI's
        ``_helpers`` shim.  If a CLI module mentions the function name
        at all, the import must come from the canonical location.

        We only check modules that currently reference the helper
        (``_lint.py``, ``_run.py``); ``_deploy.py`` may delegate through
        ``DeployConfig.from_cli_args`` instead of calling the helper
        directly, which is also acceptable.
        """
        missing: list[str] = []
        for py_file in _CLI_DIR.glob("_*.py"):
            source = py_file.read_text(encoding="utf-8")
            if "resolve_pipeline_file" not in source:
                continue
            # `_helpers.py` might keep a thin re-export wrapper; flag
            # only CLI consumer modules (not the helpers shim itself).
            if py_file.name == "_helpers.py":
                # The shim must re-export from haute._project or be gone.
                # We allow either pattern — but we forbid a standalone
                # implementation here.
                tree = ast.parse(source, str(py_file))
                has_impl = any(
                    isinstance(n, ast.FunctionDef) and n.name == "resolve_pipeline_file"
                    for n in tree.body
                )
                has_import = "from haute._project" in source
                if has_impl and not has_import:
                    missing.append(
                        "_helpers.py: defines its own resolve_pipeline_file — "
                        "must re-export from haute._project or be removed"
                    )
                continue
            if "haute._project" not in source:
                missing.append(
                    f"{py_file.name}: uses resolve_pipeline_file but doesn't import it "
                    "from haute._project (stale path through haute.cli._helpers?)"
                )
        assert not missing, (
            "CLI modules must delegate pipeline resolution to haute._project:\n  "
            + "\n  ".join(missing)
        )


# ===========================================================================
# #130 — handle_*(config) pure functions
# ===========================================================================


def _find_handle_function(module_name: str, cmd: str) -> object:
    """Return ``handle_<cmd>`` from *module_name*, failing with a hint."""
    import importlib

    mod = importlib.import_module(module_name)
    attr = f"handle_{cmd}"
    if not hasattr(mod, attr):
        raise AssertionError(
            f"{module_name} is missing the ``{attr}`` pure function. "
            f"Phase 5 Wave 9B requires every CLI command to split a "
            f"``@click.command`` entry point from a ``handle_*(config)`` "
            f"function."
        )
    return getattr(mod, attr)


class TestHandleFunctionsExist:
    """Every CLI command exposes a ``handle_<cmd>`` pure function."""

    @pytest.mark.parametrize(
        ("module_name", "cmd"),
        _CLI_COMMAND_MODULES,
        ids=[m[1] for m in _CLI_COMMAND_MODULES],
    )
    def test_handle_function_defined(self, module_name: str, cmd: str) -> None:
        """``handle_<cmd>`` is importable and callable."""
        fn = _find_handle_function(module_name, cmd)
        assert callable(fn), f"{module_name}.handle_{cmd} must be callable"

    @pytest.mark.parametrize(
        ("module_name", "cmd"),
        _CLI_COMMAND_MODULES,
        ids=[m[1] for m in _CLI_COMMAND_MODULES],
    )
    def test_handle_function_takes_single_config_argument(
        self,
        module_name: str,
        cmd: str,
    ) -> None:
        """``handle_<cmd>`` takes exactly one positional argument named ``config``.

        Uniform signatures make the dispatch layer in ``@click.command``
        handlers trivial to audit.  Keyword-only parameters with defaults
        are tolerated (dev may inject testable dependencies), but the
        first positional argument must always be ``config``.
        """
        fn = _find_handle_function(module_name, cmd)
        sig = inspect.signature(fn)
        positional = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        assert len(positional) == 1, (
            f"handle_{cmd} must take exactly one positional argument (got "
            f"{len(positional)}: {[p.name for p in positional]})"
        )
        assert positional[0].name == "config", (
            f"handle_{cmd} positional argument must be named 'config' — "
            f"got {positional[0].name!r}"
        )


class TestHandleLintCallable:
    """``handle_lint(config)`` runs without Click and reports graph issues."""

    def test_lint_success_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Clean pipeline → returns/prints success, no exception raised."""
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
        from haute.cli._lint import handle_lint

        # Build a config for lint directly.  We don't know the exact
        # dataclass type the dev chose — look it up by searching for the
        # only other public class in the module.
        import haute.cli._lint as lint_mod

        config_cls = _find_config_class(lint_mod, "lint")
        pipeline = _touch_pipeline(tmp_path, "ok.py")
        config = config_cls(pipeline_file=pipeline)

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    data=NodeData(label="a", nodeType="dataSource", config={"path": "d.parquet"}),
                ),
                GraphNode(
                    id="b",
                    data=NodeData(label="b", nodeType="polars", config={}),
                ),
            ],
            edges=[GraphEdge(id="e", source="a", target="b")],
        )

        with patch("haute.parser.parse_pipeline_file", return_value=graph):
            # ``handle_lint`` should not raise on a healthy graph.
            handle_lint(config)

    def test_lint_reports_disconnected_nodes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Disconnected node → handle_lint exits / raises with a useful message."""
        from haute._types import GraphNode, NodeData, PipelineGraph
        from haute.cli._lint import handle_lint

        import haute.cli._lint as lint_mod

        config_cls = _find_config_class(lint_mod, "lint")
        pipeline = _touch_pipeline(tmp_path, "orphan.py")
        config = config_cls(pipeline_file=pipeline)

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    data=NodeData(label="a", nodeType="dataSource", config={"path": "d.parquet"}),
                ),
                GraphNode(
                    id="b",
                    data=NodeData(label="b", nodeType="dataSource", config={"path": "d.parquet"}),
                ),
            ],
            edges=[],
        )

        with patch("haute.parser.parse_pipeline_file", return_value=graph):
            with pytest.raises(SystemExit):
                handle_lint(config)


class TestHandleStatusCallable:
    """``handle_status(config)`` forwards to mlflow without Click."""

    def test_status_calls_mlflow_with_resolved_model_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Build a status config and assert it drives the mlflow lookup."""
        monkeypatch.chdir(tmp_path)
        _write_toml(tmp_path, model_name="motor")

        import haute.cli._status as status_mod

        config_cls = _find_config_class(status_mod, "status")
        config = config_cls(model_name=None, version_only=False)

        mock_info = {"model_name": "motor", "latest_version": 7, "status": "READY"}
        mock_fn = MagicMock(return_value=mock_info)
        with patch("haute.deploy._mlflow.get_deploy_status", mock_fn):
            status_mod.handle_status(config)

        mock_fn.assert_called_once()
        args, kwargs = mock_fn.call_args
        # Either positional or keyword passing is acceptable.
        called_model = args[0] if args else kwargs.get("model_name")
        assert called_model == "motor", (
            f"handle_status must resolve the TOML model_name — got {called_model!r}"
        )


class TestHandleRunCallable:
    """``handle_run(config)`` executes a pipeline without Click."""

    def test_run_executes_and_reports(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """handle_run builds a graph, executes it, and reports per-node results."""
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
        from haute.cli._run import handle_run

        import haute.cli._run as run_mod

        config_cls = _find_config_class(run_mod, "run")
        pipeline = _touch_pipeline(tmp_path, "simple.py")
        config = config_cls(pipeline_file=pipeline)

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    data=NodeData(label="a", nodeType="dataSource", config={"path": "d.parquet"}),
                ),
            ],
            edges=[],
        )

        ok_result = MagicMock()
        ok_result.status = "ok"
        ok_result.row_count = 10
        ok_result.column_count = 4
        ok_result.preview = None

        with (
            patch("haute.parser.parse_pipeline_file", return_value=graph),
            patch("haute.executor.execute_graph", return_value={"a": ok_result}),
        ):
            # Should not raise on a healthy single-node graph.
            handle_run(config)


def _find_config_class(module: object, cmd: str) -> type:
    """Look up the ``*Config`` dataclass associated with *cmd*.

    The dev agent picks the exact class name (``LintConfig``,
    ``LintCommandConfig``, etc.).  We look up by convention and produce
    a useful error if nothing matches.
    """
    import importlib

    # Candidates in priority order.
    candidates = [
        f"{cmd.capitalize()}Config",
        f"{cmd.capitalize()}CommandConfig",
        f"{cmd.capitalize()}CliConfig",
    ]
    for name in candidates:
        if hasattr(module, name):
            return getattr(module, name)

    # Last resort — check the deploy module for DeployConfig which the
    # deploy command already uses.
    if cmd == "deploy":
        deploy_mod = importlib.import_module("haute.deploy._config")
        if hasattr(deploy_mod, "DeployConfig"):
            return deploy_mod.DeployConfig

    raise AssertionError(
        f"{module.__name__} must expose a config dataclass — tried {candidates}. "
        f"Phase 5 Wave 9B introduces a ``*Config`` dataclass per command "
        f"that the Click entry point parses args into."
    )


class TestClickBodiesAreThin:
    """The ``@click.command`` function body should be a thin dispatcher.

    The Click handler's job post-refactor is:

    1. Parse args into a ``*Config``.
    2. Call ``handle_*(config)``.

    We AST-walk each module, find the ``@click.command``-decorated
    function, and assert its body is under 20 lines and terminates with
    a call whose function name starts with ``handle_``.
    """

    @pytest.mark.parametrize(
        ("module_name", "cmd"),
        _CLI_COMMAND_MODULES,
        ids=[m[1] for m in _CLI_COMMAND_MODULES],
    )
    def test_click_body_is_short(self, module_name: str, cmd: str) -> None:
        """The Click handler body is fewer than 20 lines of real code."""
        import importlib

        mod = importlib.import_module(module_name)
        source_path = Path(mod.__file__ or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
        click_fn = _find_click_command_function(tree, cmd)
        assert click_fn is not None, (
            f"{module_name} must contain a @click.command-decorated function "
            f"named '{cmd}' or similar"
        )

        # Count non-docstring statements in the body.
        body = list(click_fn.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]

        # Build a rough "lines of code" estimate by counting the source
        # span.  A function body under 20 lines should fit comfortably
        # in max_line - min_line < 20.
        if body:
            line_span = max(s.end_lineno or s.lineno for s in body) - body[0].lineno
        else:
            line_span = 0
        assert line_span < 20, (
            f"{module_name}::{cmd} Click body is {line_span} lines — must be <20 "
            f"after extracting the handle_{cmd} pure function"
        )

    @pytest.mark.parametrize(
        ("module_name", "cmd"),
        _CLI_COMMAND_MODULES,
        ids=[m[1] for m in _CLI_COMMAND_MODULES],
    )
    def test_click_body_calls_handle_function(self, module_name: str, cmd: str) -> None:
        """The Click body contains a call to ``handle_<cmd>(...)``.

        This is the structural marker of the split: the Click entry point
        packages args and hands off to the pure function.
        """
        import importlib

        mod = importlib.import_module(module_name)
        source_path = Path(mod.__file__ or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
        click_fn = _find_click_command_function(tree, cmd)
        assert click_fn is not None

        expected = f"handle_{cmd}"
        found = False
        for node in ast.walk(click_fn):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name) and target.id == expected:
                    found = True
                    break
                if isinstance(target, ast.Attribute) and target.attr == expected:
                    found = True
                    break
        assert found, (
            f"{module_name}::{cmd} Click body must call ``{expected}(config)`` — "
            f"the Click handler should be a thin dispatcher"
        )


def _find_click_command_function(tree: ast.Module, cmd: str) -> ast.FunctionDef | None:
    """Return the ``FunctionDef`` decorated with ``@click.command`` in *tree*.

    Matches both bare ``@click.command`` and ``@click.command()`` forms.
    Returns ``None`` if no such function is found.
    """
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "command"
                and isinstance(target.value, ast.Name)
                and target.value.id == "click"
            ):
                return node
    return None


# ===========================================================================
# #131 — DeployConfig.from_toml / from_cli_args, _load_deploy_config removed
# ===========================================================================


class TestDeployConfigFromToml:
    """``DeployConfig.from_toml(path)`` loads a validated config."""

    def test_valid_file_loaded(self, tmp_path: Path) -> None:
        """A well-formed haute.toml produces a populated DeployConfig."""
        from haute.deploy._config import DeployConfig

        toml = _write_toml(tmp_path, model_name="m_toml", pipeline="main.py")
        config = DeployConfig.from_toml(toml)
        assert config.model_name == "m_toml"
        assert config.pipeline_file == Path("main.py")

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        """A non-existent toml path raises FileNotFoundError (not a silent fallback)."""
        from haute.deploy._config import DeployConfig

        with pytest.raises(FileNotFoundError):
            DeployConfig.from_toml(tmp_path / "missing.toml")

    def test_malformed_toml_raises_validation_error(self, tmp_path: Path) -> None:
        """Broken TOML syntax surfaces as a clear error — never a partial config."""
        from haute.deploy._config import DeployConfig

        bad = tmp_path / "haute.toml"
        bad.write_text("[project\nname = no_closing_bracket", encoding="utf-8")
        with pytest.raises((ValueError, Exception)) as excinfo:
            DeployConfig.from_toml(bad)
        # tomllib raises TOMLDecodeError which is a ValueError subclass.
        # Make sure the error message actually mentions the file so
        # users can find it.
        assert "haute.toml" in str(excinfo.value) or bad.name in str(excinfo.value) or \
            "TOML" in type(excinfo.value).__name__.upper() or \
            "decode" in str(excinfo.value).lower()

    def test_unknown_toml_keys_raises_validation_error(self, tmp_path: Path) -> None:
        """Typos in config keys must fail loudly, not be silently ignored."""
        from haute.deploy._config import DeployConfig

        bad = tmp_path / "haute.toml"
        bad.write_text(
            '[project]\nname = "x"\n'
            '[deploy]\nmodel_name = "m"\nendpont_name = "typo"\n',  # typo: endpont
            encoding="utf-8",
        )
        with pytest.raises(ValueError) as excinfo:
            DeployConfig.from_toml(bad)
        assert "endpont_name" in str(excinfo.value) or "unknown" in str(excinfo.value).lower()


class TestDeployConfigFromCliArgs:
    """``DeployConfig.from_cli_args(**kwargs)`` builds a config from CLI input.

    This classmethod replaces the old ``_load_deploy_config`` helper's
    no-toml code path.  It validates required fields and refuses to
    construct a half-finished config.
    """

    def test_builds_with_required_fields(self, tmp_path: Path) -> None:
        """Full required-field set → DeployConfig with those values populated."""
        from haute.deploy._config import DeployConfig

        pipeline = _touch_pipeline(tmp_path, "p.py")
        config = DeployConfig.from_cli_args(
            pipeline_file=pipeline,
            model_name="my_model",
        )
        assert config.pipeline_file.resolve() == pipeline.resolve()
        assert config.model_name == "my_model"

    def test_missing_model_name_raises(self, tmp_path: Path) -> None:
        """model_name is required; a missing value must fail loudly."""
        from haute.deploy._config import DeployConfig

        pipeline = _touch_pipeline(tmp_path, "p.py")
        with pytest.raises((TypeError, ValueError)) as excinfo:
            DeployConfig.from_cli_args(pipeline_file=pipeline)
        assert "model_name" in str(excinfo.value) or "required" in str(excinfo.value).lower()

    def test_missing_pipeline_file_raises(self, tmp_path: Path) -> None:
        """pipeline_file is required too."""
        from haute.deploy._config import DeployConfig

        with pytest.raises((TypeError, ValueError)) as excinfo:
            DeployConfig.from_cli_args(model_name="m")
        assert (
            "pipeline" in str(excinfo.value).lower()
            or "required" in str(excinfo.value).lower()
        )

    def test_accepts_optional_endpoint_suffix(self, tmp_path: Path) -> None:
        """Optional kwargs flow through to the final config."""
        from haute.deploy._config import DeployConfig

        pipeline = _touch_pipeline(tmp_path, "p.py")
        config = DeployConfig.from_cli_args(
            pipeline_file=pipeline,
            model_name="m",
            endpoint_suffix="-staging",
        )
        assert config.endpoint_suffix == "-staging"


class TestLoadDeployConfigRemoved:
    """The legacy ``_load_deploy_config`` helper must be removed.

    After #131, the two explicit classmethods ``from_toml`` and
    ``from_cli_args`` cover every code path the old helper did.  Leaving
    the legacy helper around creates two ways to build a config, which
    is exactly the drift we're trying to avoid.
    """

    def test_helpers_module_does_not_export_load_deploy_config(self) -> None:
        """``haute.cli._helpers._load_deploy_config`` is gone."""
        import haute.cli._helpers as helpers_mod

        assert not hasattr(helpers_mod, "_load_deploy_config"), (
            "The legacy _load_deploy_config helper must be removed — use "
            "DeployConfig.from_toml / DeployConfig.from_cli_args instead"
        )
