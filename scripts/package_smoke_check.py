"""Installed-package smoke checks for wheel and sdist CI lanes."""

from __future__ import annotations

from importlib.resources import files


def _assert_static_assets_present() -> None:
    package_root = files("haute")
    static_dir = package_root.joinpath("static")
    assets_dir = static_dir.joinpath("assets")

    assert static_dir.is_dir(), "Installed package is missing haute/static"
    assert assets_dir.is_dir(), "Installed package is missing haute/static/assets"
    assert any(assets_dir.iterdir()), "Installed package static/assets directory is empty"


def _assert_server_routes_present() -> None:
    from haute.server import app

    schema = app.openapi()
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


def main() -> None:
    import haute
    from haute.assistant._assets import validate_example_bundles

    assert haute.__file__, "haute package did not import from an installed distribution"
    _assert_static_assets_present()
    _assert_server_routes_present()
    _assert_one_xgboost_distribution()
    _assert_model_engines()
    validate_example_bundles(execute_fast=True)
    print("package smoke ok")


if __name__ == "__main__":
    main()
