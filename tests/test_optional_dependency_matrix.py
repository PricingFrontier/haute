"""Smoke contracts for environments without optional MLflow or Databricks extras."""

from __future__ import annotations

import importlib.util
import time
from types import SimpleNamespace

import pytest

import haute.routes.optimiser as optimiser_routes

_OPTIONAL_DEPS_PRESENT = any(
    importlib.util.find_spec(module_name) is not None
    for module_name in ("mlflow", "databricks.sdk", "databricks.sql")
)

pytestmark = pytest.mark.skipif(
    _OPTIONAL_DEPS_PRESENT,
    reason="This smoke file is for the core-only CI lane without optional extras.",
)


def test_modelling_mlflow_check_reports_not_installed(client) -> None:
    resp = client.get("/api/modelling/mlflow/check")

    assert resp.status_code == 200
    assert resp.json() == {
        "mlflow_installed": False,
        "backend": None,
        "databricks_host": "",
    }


def test_mlflow_discovery_routes_fail_cleanly_without_dependency(client) -> None:
    resp = client.get("/api/mlflow/experiments")

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


def test_databricks_catalog_routes_fail_cleanly_without_sdk(client) -> None:
    resp = client.get("/api/databricks/warehouses")

    assert resp.status_code == 503
    assert "databricks-sdk is not installed" in resp.json()["detail"].lower()


def test_databricks_fetch_fails_cleanly_without_sql_connector(client) -> None:
    resp = client.post(
        "/api/databricks/fetch",
        json={"table": "main.analytics.quotes"},
    )

    assert resp.status_code == 400
    assert "databricks-sql-connector is not installed" in resp.json()["detail"].lower()
