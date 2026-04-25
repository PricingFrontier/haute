"""Smoke contracts for environments with MLflow / Databricks extras installed."""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


_HAS_OPTIONAL_EXTRAS = all(
    _module_available(module_name) for module_name in ("mlflow", "databricks.sdk", "databricks.sql")
)

pytestmark = pytest.mark.skipif(
    not _HAS_OPTIONAL_EXTRAS,
    reason="This smoke file is for CI lanes with MLflow and Databricks extras installed.",
)


def test_server_import_succeeds_with_optional_extras_installed() -> None:
    import haute.server as server

    route_paths = {route.path for route in server.app.routes}

    assert "/api/mlflow/experiments" in route_paths
    assert "/api/databricks/warehouses" in route_paths


def test_modelling_mlflow_check_reports_installed_with_backend(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "haute.modelling._mlflow_log.resolve_tracking_backend",
        lambda: ("sqlite:///mlruns", "local"),
    )

    resp = client.get("/api/modelling/mlflow/check")

    assert resp.status_code == 200
    assert resp.json() == {
        "mlflow_installed": True,
        "mlflow_importable": True,
        "tracking_configured": True,
        "backend": "local",
        "databricks_host": "",
        "detail": "",
    }


def test_mlflow_experiments_route_succeeds_with_installed_dependency_and_mocked_backend(
    client,
    monkeypatch,
) -> None:
    fake_mlflow = SimpleNamespace(
        search_experiments=lambda: [
            SimpleNamespace(experiment_id="42", name="pricing/dev"),
        ]
    )
    fake_client = SimpleNamespace()
    monkeypatch.setattr(
        "haute.routes.mlflow._ensure_tracking",
        lambda: (fake_mlflow, fake_client),
    )

    resp = client.get("/api/mlflow/experiments")

    assert resp.status_code == 200
    assert resp.json() == [{"experiment_id": "42", "name": "pricing/dev"}]


def test_mlflow_models_route_succeeds_with_installed_dependency_and_mocked_backend(
    client,
    monkeypatch,
) -> None:
    fake_client = SimpleNamespace(
        search_registered_models=lambda max_results, page_token=None: [
            SimpleNamespace(
                name="pricing-model",
                latest_versions=[
                    SimpleNamespace(version="3", status="READY", run_id="run-3"),
                ],
            )
        ]
    )
    monkeypatch.setattr(
        "haute.routes.mlflow._ensure_tracking",
        lambda: (SimpleNamespace(), fake_client),
    )

    resp = client.get("/api/mlflow/models")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "name": "pricing-model",
            "latest_versions": [
                {"version": "3", "status": "READY", "run_id": "run-3"},
            ],
        }
    ]


def test_mlflow_model_versions_route_succeeds_with_installed_dependency_and_mocked_backend(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "haute.routes.mlflow._ensure_tracking",
        lambda: (SimpleNamespace(), SimpleNamespace()),
    )
    monkeypatch.setattr(
        "haute.routes.mlflow.search_versions",
        lambda _client, _model_name: [
            SimpleNamespace(
                version="2",
                run_id="run-2",
                status="READY",
                creation_timestamp=2_000,
                description="second",
            ),
            SimpleNamespace(
                version="1",
                run_id="run-1",
                status="READY",
                creation_timestamp=1_000,
                description="first",
            ),
        ],
    )

    resp = client.get("/api/mlflow/model-versions", params={"model_name": "pricing-model"})

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "version": "2",
            "run_id": "run-2",
            "status": "READY",
            "creation_timestamp": 2_000,
            "description": "second",
        },
        {
            "version": "1",
            "run_id": "run-1",
            "status": "READY",
            "creation_timestamp": 1_000,
            "description": "first",
        },
    ]


def test_databricks_warehouses_route_succeeds_with_installed_sdk_and_mocked_client(
    client,
    monkeypatch,
) -> None:
    fake_warehouse = SimpleNamespace(
        id="wh-123",
        name="Pricing Warehouse",
        state=SimpleNamespace(value="RUNNING"),
        cluster_size="2X-Small",
    )
    fake_client = SimpleNamespace(
        warehouses=SimpleNamespace(list=lambda: [fake_warehouse]),
    )
    monkeypatch.setattr("haute.routes.databricks._get_databricks_client", lambda: fake_client)

    resp = client.get("/api/databricks/warehouses")

    assert resp.status_code == 200
    assert resp.json() == {
        "warehouses": [
            {
                "id": "wh-123",
                "name": "Pricing Warehouse",
                "http_path": "/sql/1.0/warehouses/wh-123",
                "state": "RUNNING",
                "size": "2X-Small",
            }
        ]
    }


def test_databricks_browsing_routes_succeed_with_installed_sdk_and_mocked_client(
    client,
    monkeypatch,
) -> None:
    fake_client = SimpleNamespace(
        catalogs=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="main", comment="Primary catalog")]
        ),
        schemas=SimpleNamespace(
            list=lambda catalog_name: [
                SimpleNamespace(name=f"{catalog_name}_pricing", comment="Pricing schema")
            ]
        ),
        tables=SimpleNamespace(
            list=lambda catalog_name, schema_name: [
                SimpleNamespace(
                    name="quotes",
                    full_name=f"{catalog_name}.{schema_name}.quotes",
                    table_type=SimpleNamespace(value="MANAGED"),
                    comment="Quote table",
                )
            ]
        ),
    )
    monkeypatch.setattr("haute.routes.databricks._get_databricks_client", lambda: fake_client)

    catalogs = client.get("/api/databricks/catalogs")
    schemas = client.get("/api/databricks/schemas", params={"catalog": "main"})
    tables = client.get(
        "/api/databricks/tables",
        params={"catalog": "main", "schema": "pricing"},
    )

    assert catalogs.status_code == 200
    assert catalogs.json() == {"catalogs": [{"name": "main", "comment": "Primary catalog"}]}
    assert schemas.status_code == 200
    assert schemas.json() == {"schemas": [{"name": "main_pricing", "comment": "Pricing schema"}]}
    assert tables.status_code == 200
    assert tables.json() == {
        "tables": [
            {
                "name": "quotes",
                "full_name": "main.pricing.quotes",
                "table_type": "MANAGED",
                "comment": "Quote table",
            }
        ]
    }


def test_databricks_fetch_route_succeeds_with_installed_connector_and_mocked_runner(
    client,
    monkeypatch,
) -> None:
    async def fake_run_blocking_with_response_timeout(*_args, **_kwargs):
        return {
            "path": ".haute_cache/main_analytics_quotes.parquet",
            "table": "main.analytics.quotes",
            "row_count": 12,
            "column_count": 3,
            "columns": {"quote_id": "Utf8", "premium": "Float64", "segment": "Utf8"},
            "size_bytes": 2048,
            "fetched_at": 1_717_171_717.0,
            "fetch_seconds": 0.42,
        }

    monkeypatch.setattr(
        "haute.routes.databricks.run_blocking_with_response_timeout",
        fake_run_blocking_with_response_timeout,
    )

    resp = client.post(
        "/api/databricks/fetch",
        json={"table": "main.analytics.quotes"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "path": ".haute_cache/main_analytics_quotes.parquet",
        "table": "main.analytics.quotes",
        "row_count": 12,
        "column_count": 3,
        "columns": {"quote_id": "Utf8", "premium": "Float64", "segment": "Utf8"},
        "size_bytes": 2048,
        "fetched_at": 1_717_171_717.0,
        "fetch_seconds": 0.42,
    }
