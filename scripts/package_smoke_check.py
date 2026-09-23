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


def _assert_one_xgboost_distribution() -> None:
    """``xgboost`` and ``xgboost-cpu`` install the same import package; never both."""
    from importlib.metadata import PackageNotFoundError, version

    installed = []
    for distribution in ("xgboost", "xgboost-cpu"):
        try:
            installed.append(f"{distribution} {version(distribution)}")
        except PackageNotFoundError:
            continue
    assert len(installed) == 1, (
        "Expected exactly one XGBoost distribution (xgboost-cpu, or xgboost on macOS); "
        f"found {installed or 'none'}"
    )
    import xgboost

    assert xgboost.__version__.startswith("3.2."), xgboost.__version__


def main() -> None:
    import haute
    from haute.assistant._assets import validate_example_bundles

    assert haute.__file__, "haute package did not import from an installed distribution"
    _assert_static_assets_present()
    _assert_server_routes_present()
    _assert_one_xgboost_distribution()
    validate_example_bundles(execute_fast=True)
    print("package smoke ok")


if __name__ == "__main__":
    main()
