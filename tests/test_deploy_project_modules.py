"""A deployed pipeline carries the project's utility package (DEP-R01).

The bundle regressions score through the built bundle in a separate process
after the project's ``utility`` directory is deleted, so a passing test proves
the bundle's own copy was imported, not the one next to the pipeline.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from haute.deploy import DeployConfig, resolve_config, validate_deploy
from haute.deploy._config import ResolvedDeploy
from haute.errors import DeployError

_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "haute"
    / "assistant"
    / "assets"
    / "examples"
    / "minimal_live_quote"
)

_PIPELINE = '''\
"""A live quote scaled by a factor from the project's utility package."""

import polars as pl

import haute

{preamble}

pipeline = haute.Pipeline("utility_quote", description="Scales the quote by a utility factor.")


@pipeline.api_input(config="config/request.json")
def quote(): ...


@pipeline.polars
def scaled(quote: pl.LazyFrame) -> pl.LazyFrame:
    return quote.with_columns(fixture_value=pl.col("fixture_value") * {factor})


@pipeline.output(config="config/output.json")
def response(scaled): ...


pipeline.connect("quote", "scaled", source_port="quote")
pipeline.connect("scaled", "response")
'''

_CONTAINER_DEPLOY = (
    '\n[deploy]\ntarget = "container"\n\n[deploy.container]\nbase_image = "python:3.11.9-slim"\n'
)


def _make_project(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    preamble: str = "from utility.helpers import FACTOR",
    factor: str = "FACTOR",
    helpers: str = "FACTOR = 3\n",
) -> None:
    """Build a deployable project at *project* whose preamble imports utility."""
    monkeypatch.chdir(project.parent)
    shutil.copytree(_EXAMPLE, project)
    (project / "pipeline.py").write_text(
        _PIPELINE.format(preamble=preamble, factor=factor), encoding="utf-8"
    )
    output_config = project / "config" / "output.json"
    output_config.write_text(
        output_config.read_text(encoding="utf-8").replace(
            '"source_port":"quote"', '"source_port":"scaled"'
        ),
        encoding="utf-8",
    )
    utility = project / "utility"
    utility.mkdir()
    (utility / "__init__.py").write_text("", encoding="utf-8")
    (utility / "helpers.py").write_text(helpers, encoding="utf-8")
    toml_path = project / "haute.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8") + _CONTAINER_DEPLOY, encoding="utf-8"
    )


def _resolve(project: Path) -> ResolvedDeploy:
    return resolve_config(DeployConfig.from_toml(project / "haute.toml"))


def _run_isolated(script: str, *args: str, cwd: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-c", script, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return result.stdout.strip().splitlines()[-1]


_SCORE_THROUGH_APP = textwrap.dedent(
    """\
    import sys
    from pathlib import Path

    from fastapi.testclient import TestClient

    import app

    body = Path(sys.argv[1]).read_bytes()
    with TestClient(app.app) as client:
        response = client.post(
            "/quote", content=body, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 200, response.text
    print(response.text)
    """
)

_SCORE_THROUGH_PYFUNC = textwrap.dedent(
    """\
    import json
    import sys

    import mlflow.pyfunc
    import pandas as pd

    model = mlflow.pyfunc.load_model(sys.argv[1])
    rows = json.loads(open(sys.argv[2], encoding="utf-8").read())
    frame = pd.DataFrame(rows if isinstance(rows, list) else [rows])
    print(model.predict(frame).to_json(orient="records"))
    """
)


@pytest.mark.timeout(240)
def test_container_bundle_scores_with_its_own_utility_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.deploy._container import prepare_build_directory

    project = tmp_path / "project"
    _make_project(project, monkeypatch)
    with _resolve(project) as resolved:
        validate_deploy(resolved)
        image = tmp_path / "image"
        prepare_build_directory(resolved, image)

    dockerfile = (image / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY utility/ utility/" in dockerfile
    assert not list((image / "utility").rglob("__pycache__"))
    shutil.rmtree(project / "utility")

    output = _run_isolated(_SCORE_THROUGH_APP, str(project / "golden_request.json"), cwd=image)
    assert json.loads(output)["rows"] == [{"fixture_value": 30}]


@pytest.mark.timeout(240)
def test_pyfunc_model_scores_with_its_own_utility_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow.pyfunc

    from haute.deploy._mlflow import _pyfunc_model_arguments
    from haute.deploy._utils import build_manifest

    project = tmp_path / "project"
    _make_project(project, monkeypatch)
    model_dir = tmp_path / "model"
    with _resolve(project) as resolved:
        validate_deploy(resolved)
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(build_manifest(resolved)), encoding="utf-8")
        mlflow.pyfunc.save_model(
            path=str(model_dir), **_pyfunc_model_arguments(resolved, manifest_path)
        )

    shutil.rmtree(project / "utility")

    output = _run_isolated(
        _SCORE_THROUGH_PYFUNC,
        str(model_dir),
        str(project / "golden_request.json"),
        cwd=tmp_path,
    )
    assert json.loads(output) == [{"fixture_value": 30}]


def test_a_project_module_outside_utility_is_refused_at_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _make_project(project, monkeypatch, preamble="from local_rates import FACTOR")
    (project / "local_rates.py").write_text("FACTOR = 3\n", encoding="utf-8")

    with _resolve(project) as resolved:
        with pytest.raises(DeployError, match=r"the preamble imports project module 'local_rates'"):
            validate_deploy(resolved)


def test_a_utility_module_importing_other_project_code_is_refused_at_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _make_project(
        project,
        monkeypatch,
        helpers="from local_rates import FACTOR\n",
    )
    (project / "local_rates.py").write_text("FACTOR = 3\n", encoding="utf-8")

    with _resolve(project) as resolved:
        with pytest.raises(
            DeployError, match=r"utility/helpers\.py imports project module 'local_rates'"
        ):
            validate_deploy(resolved)


def test_a_pipeline_without_project_modules_bundles_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.deploy._container import prepare_build_directory

    project = tmp_path / "project"
    _make_project(project, monkeypatch, preamble="import math", factor="math.floor(3.5)")
    shutil.rmtree(project / "utility")

    with _resolve(project) as resolved:
        validate_deploy(resolved)
        assert resolved.project_modules.utility is None
        image = tmp_path / "image"
        prepare_build_directory(resolved, image)

    assert "utility" not in (image / "Dockerfile").read_text(encoding="utf-8")
    assert not (image / "utility").exists()


def test_a_reused_build_directory_keeps_only_this_builds_utility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale package left by an earlier build would shadow the module this
    build validated, so every build replaces the bundled utility code."""
    from haute.deploy._container import prepare_build_directory

    project = tmp_path / "project"
    _make_project(project, monkeypatch, preamble="from utility.rates import FACTOR")
    (project / "utility" / "rates").mkdir()
    (project / "utility" / "rates" / "__init__.py").write_text("FACTOR = 2\n", encoding="utf-8")
    image = tmp_path / "image"
    with _resolve(project) as resolved:
        prepare_build_directory(resolved, image)
    assert (image / "utility" / "rates" / "__init__.py").is_file()

    shutil.rmtree(project / "utility" / "rates")
    (project / "utility" / "rates.py").write_text("FACTOR = 3\n", encoding="utf-8")
    with _resolve(project) as resolved:
        prepare_build_directory(resolved, image)
    assert not (image / "utility" / "rates").exists()
    assert (image / "utility" / "rates.py").read_text(encoding="utf-8") == "FACTOR = 3\n"

    (project / "pipeline.py").write_text(
        _PIPELINE.format(preamble="import math", factor="math.floor(3.5)"), encoding="utf-8"
    )
    shutil.rmtree(project / "utility")
    with _resolve(project) as resolved:
        prepare_build_directory(resolved, image)
    assert not (image / "utility").exists()


def test_a_namespace_utility_deployed_from_its_pipeline_directory_is_bundled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _make_project(project, monkeypatch)
    (project / "utility" / "__init__.py").unlink()
    monkeypatch.chdir(project)

    with _resolve(project) as resolved:
        validate_deploy(resolved)
        assert resolved.project_modules.utility == (project / "utility").resolve()


def test_a_utility_namespace_split_across_project_directories_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _make_project(project, monkeypatch)
    (project / "utility" / "__init__.py").unlink()
    (tmp_path / "utility").mkdir()

    with pytest.raises(DeployError, match="namespace package split across 2 directories"):
        _resolve(project)


def test_a_bundled_utility_file_that_does_not_parse_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _make_project(project, monkeypatch)
    (project / "utility" / "draft.py").write_text("def unfinished(:\n", encoding="utf-8")

    with pytest.raises(DeployError, match=r"utility/draft\.py does not parse"):
        _resolve(project)


def test_a_utility_file_with_a_byte_order_mark_is_checked_like_python_imports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _make_project(project, monkeypatch, helpers="\ufeffFACTOR = 3\n")

    with _resolve(project) as resolved:
        validate_deploy(resolved)
        assert resolved.project_modules.unbundled_imports == ()
