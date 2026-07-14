"""GET /api/formats — the dataInput/dataOutput capability endpoint.

The frontend editors render format selectors and argument forms from this
payload instead of hard-coding format knowledge; these tests pin the
payload's shape and the registry-derived facts the editors rely on.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from haute._polars_io_registry import FORMATS_BY_NAME
from haute.schemas import IoFormatsResponse


@pytest.fixture()
def client() -> TestClient:
    from haute.server import app

    return TestClient(app)


class TestFormatsEndpoint:
    def test_returns_every_registry_format(self, client: TestClient) -> None:
        resp = client.get("/api/formats")
        assert resp.status_code == 200
        payload = IoFormatsResponse.model_validate(resp.json())
        assert {f.name for f in payload.formats} == set(FORMATS_BY_NAME)

    def test_csv_capability_shape(self, client: TestClient) -> None:
        payload = IoFormatsResponse.model_validate(client.get("/api/formats").json())
        csv = next(f for f in payload.formats if f.name == "csv")
        assert csv.source_kind == "path"
        assert csv.read_available and csv.write_available
        assert csv.input_modes == ["scan", "read"]
        assert csv.output_modes == ["sink", "write"]
        assert "schema_overrides" in csv.input_arguments["scan"]
        assert "separator" in csv.output_arguments["write"]
        assert csv.read_engines_missing == []
        # Remote-IO arguments are excluded from the config surface by design.
        for mode_args in csv.input_arguments.values():
            assert "storage_options" not in mode_args

    def test_engine_gated_format_reports_missing_engines(self, client: TestClient) -> None:
        payload = IoFormatsResponse.model_validate(client.get("/api/formats").json())
        delta = next(f for f in payload.formats if f.name == "delta")
        # Core haute ships no deltalake engine; the payload must say so
        # (the editor renders the format disabled-with-reason, IO12-style).
        assert delta.read_engines_missing == ["deltalake"]
        assert delta.write_engines_missing == ["deltalake"]

    def test_read_only_and_database_shapes(self, client: TestClient) -> None:
        payload = IoFormatsResponse.model_validate(client.get("/api/formats").json())
        ods = next(f for f in payload.formats if f.name == "ods")
        assert ods.read_available and not ods.write_available
        assert ods.output_modes == []
        database = next(f for f in payload.formats if f.name == "database")
        assert database.source_kind == "database"
        records = next(f for f in payload.formats if f.name == "records")
        assert records.source_kind == "inline"
        lines = next(f for f in payload.formats if f.name == "lines")
        assert lines.unstable is True
