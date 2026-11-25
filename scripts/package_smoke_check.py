"""Installed-package smoke checks for wheel and sdist CI lanes."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def _assert_static_assets_present() -> None:
    package_root = files("haute")
    static_dir = package_root.joinpath("static")
    assets_dir = static_dir.joinpath("assets")

    assert static_dir.is_dir(), "Installed package is missing haute/static"
    assert assets_dir.is_dir(), "Installed package is missing haute/static/assets"
    assert any(assets_dir.iterdir()), "Installed package static/assets directory is empty"


def _assert_server_routes_present() -> None:
    import tempfile

    from haute._project_context import ProjectContext
    from haute.server import create_app

    schema = create_app(ProjectContext.at(Path(tempfile.gettempdir()))).openapi()
    route_paths = set(schema["paths"])
    expected = {
        "/api/pipeline",
        "/api/optimiser/solve",
        "/api/modelling/train",
        "/api/mlflow/experiments",
        "/api/databricks/warehouses",
    }
    missing = sorted(expected - route_paths)
    assert not missing, f"Installed app is missing expected routes: {missing}"


def _assert_within_declared_range(distribution: str, installed: str) -> None:
    """*installed* is inside the range the installed haute declares for *distribution*.

    The range is read from the wheel's own requirements, for this platform, so a
    cap moved in pyproject.toml needs no edit here.
    """
    from importlib.metadata import requires

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    wanted = canonicalize_name(distribution)
    declared = [
        requirement.specifier
        for requirement in map(Requirement, requires("haute") or ())
        if canonicalize_name(requirement.name) == wanted
        and (requirement.marker is None or requirement.marker.evaluate())
    ]
    assert len(declared) == 1, (
        f"Expected haute to declare one requirement on {distribution} here; found {declared}"
    )
    assert declared[0].contains(installed, prereleases=True), (
        f"{distribution} {installed} is outside the range haute declares ({declared[0]})"
    )


def _assert_one_xgboost_distribution() -> None:
    """``xgboost`` and ``xgboost-cpu`` install the same import package; never both."""
    from importlib.metadata import PackageNotFoundError, version

    installed = []
    for distribution in ("xgboost", "xgboost-cpu"):
        try:
            installed.append((distribution, version(distribution)))
        except PackageNotFoundError:
            continue
    assert len(installed) == 1, (
        "Expected exactly one XGBoost distribution (xgboost-cpu, or xgboost on macOS); "
        f"found {installed or 'none'}"
    )
    import xgboost

    _assert_within_declared_range(installed[0][0], xgboost.__version__)


def _assert_model_engines() -> None:
    """Every model family's engine imports from the installed wheel at its pinned line."""
    import interpret
    import lightgbm

    _assert_within_declared_range("lightgbm", lightgbm.__version__)
    # An .ebm loads only under the exact interpret-core version its contract records.
    _assert_within_declared_range("interpret-core", interpret.__version__)
    from haute.modelling._algorithms import ALGORITHM_REGISTRY

    assert set(ALGORITHM_REGISTRY) >= {"catboost", "glm", "xgboost", "lightgbm", "ebm"}


def _assert_compiled_examples_load() -> None:
    """Every teaching example loads through ``read_reference`` from a compiling install.

    pip compiles each installed ``.py`` file into a ``__pycache__`` folder beside it by
    default, as the hosted editor installs; CI installs with ``--compile-bytecode`` so the
    caches are there. Any other file a manifest does not name is still refused, naming it.
    """
    from haute.assistant import _assets
    from haute.assistant._tools import read_reference

    examples = Path(str(files("haute").joinpath("assistant", "assets", "examples")))
    assert any(examples.glob("*/__pycache__/*.pyc")), (
        "Install the wheel with bytecode compilation on (pip's default): no example holds "
        "a compiled module, so this check proves nothing"
    )
    names = [name for name, _summary in _assets.example_index()]
    batch = read_reference([f"example:{name}" for name in names])
    assert "error" not in batch, f"read_reference refused the examples: {batch['error']}"
    assert batch["count"] == len(names)

    # The loader names the file; the tool boundary reports it as an incident.
    stray = examples / names[0] / "stray.txt"
    stray.write_text("not in the manifest\n", encoding="utf-8")
    _assets._loaded_example.cache_clear()
    try:
        _assets.load_example(names[0])
    except RuntimeError as exc:
        assert str(exc).endswith("undeclared resources: stray.txt"), exc
    else:
        raise AssertionError(f"Example {names[0]!r} loaded with an undeclared file in it")
    finally:
        stray.unlink()
        _assets._loaded_example.cache_clear()


def main() -> None:
    import haute
    from haute.assistant._assets import validate_example_bundles

    assert haute.__file__, "haute package did not import from an installed distribution"
    _assert_static_assets_present()
    _assert_server_routes_present()
    _assert_one_xgboost_distribution()
    _assert_model_engines()
    validate_example_bundles(execute_fast=True)
    _assert_compiled_examples_load()
    print("package smoke ok")


if __name__ == "__main__":
    main()
