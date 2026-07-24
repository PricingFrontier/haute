"""Smoke contracts for environments without optional MLflow or Databricks extras."""

from __future__ import annotations

import importlib.util
import time
from types import SimpleNamespace

import pytest

import haute.routes.optimiser as optimiser_routes


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


_OPTIONAL_DEPS_PRESENT = any(
    _module_available(module_name) for module_name in ("mlflow", "databricks.sdk", "databricks.sql")
)

pytestmark = pytest.mark.skipif(
    _OPTIONAL_DEPS_PRESENT,
    reason="This smoke file is for the core-only CI lane without optional extras.",
)


def test_server_import_succeeds_without_optional_extras() -> None:
    import haute.server as server

    route_paths = {route.path for route in server.app.routes}

    assert "/api/mlflow/experiments" in route_paths
    assert "/api/databricks/warehouses" in route_paths


def test_modelling_mlflow_check_reports_not_installed(client) -> None:
    resp = client.get("/api/modelling/mlflow/check")

    assert resp.status_code == 200
    assert resp.json() == {
        "mlflow_installed": False,
        "mlflow_importable": False,
        "tracking_configured": False,
        "backend": "",
        "databricks_host": "",
        "detail": "MLflow package is not installed",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/mlflow/experiments",
        "/api/mlflow/models",
        "/api/mlflow/model-versions?model_name=pricing-model",
    ],
)
def test_mlflow_discovery_routes_fail_cleanly_without_dependency(client, path: str) -> None:
    resp = client.get(path)

    assert resp.status_code == 503
    assert "mlflow is not installed" in resp.json()["detail"].lower()


def test_optimiser_mlflow_log_fails_cleanly_without_dependency(client) -> None:
    optimiser_routes._store.jobs["no_mlflow"] = {
        "status": "completed",
        "solver": object(),
        "solve_result": SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            total_constraints={},
            converged=True,
        ),
        "config": {"mode": "online"},
        "node_label": "opt",
        "created_at": time.time(),
    }

    try:
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "no_mlflow", "experiment_name": "/test"},
        )
    finally:
        optimiser_routes._store.jobs.pop("no_mlflow", None)

    assert resp.status_code == 400
    assert "mlflow" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    "path",
    [
        "/api/databricks/warehouses",
        "/api/databricks/catalogs",
        "/api/databricks/schemas?catalog=main",
        "/api/databricks/tables?catalog=main&schema=pricing",
    ],
)
def test_databricks_browsing_routes_fail_cleanly_without_sdk(client, path: str) -> None:
    resp = client.get(path)

    assert resp.status_code == 503
    assert "databricks-sdk is not installed" in resp.json()["detail"].lower()
