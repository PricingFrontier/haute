"""Smoke contracts for core MLflow without the Databricks SQL connector."""

from __future__ import annotations

import importlib.util

import pytest


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


_HAS_CORE_MLFLOW = _module_available("mlflow")
_HAS_DATABRICKS_SQL_CONNECTOR = _module_available("databricks.sql")

pytestmark = pytest.mark.skipif(
    not _HAS_CORE_MLFLOW or _HAS_DATABRICKS_SQL_CONNECTOR,
    reason="This smoke file requires core MLflow without the Databricks extra.",
)


def test_server_import_succeeds_without_optional_extras() -> None:
    import haute.server as server

    route_paths = {route.path for route in server.app.routes}

    assert "/api/mlflow/experiments" in route_paths
    assert "/api/databricks/warehouses" in route_paths


def test_modelling_mlflow_check_reports_core_dependency_installed(client) -> None:
    resp = client.get("/api/modelling/mlflow/check")

    assert resp.status_code == 200
    assert resp.json()["mlflow_installed"] is True
    assert resp.json()["mlflow_importable"] is True


def test_core_lane_omits_the_databricks_sql_connector() -> None:
    assert not _module_available("databricks.sql")
