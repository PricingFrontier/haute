"""GET /api/io-capabilities — the canonical data I/O capability endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from haute._polars_io_registry import FORMATS_BY_NAME
from haute.schemas import IoCapabilitiesResponse


@pytest.fixture()
def client() -> TestClient:
    from haute.server import app

    return TestClient(app)


class TestIoCapabilitiesEndpoint:
    def test_returns_every_registry_format(self, client: TestClient) -> None:
        resp = client.get("/api/io-capabilities")
        assert resp.status_code == 200
        payload = IoCapabilitiesResponse.model_validate(resp.json())
        assert payload.schema_version == 1
        formats = [fmt for group in payload.groups for fmt in group.formats]
        assert {fmt.name for fmt in formats} == set(FORMATS_BY_NAME)

    def test_groups_are_ordered_and_directional(self, client: TestClient) -> None:
        payload = IoCapabilitiesResponse.model_validate(client.get("/api/io-capabilities").json())
        assert [group.name for group in payload.groups] == [
            "file",
            "database",
            "lakehouse",
            "databricks",
            "inline",
        ]
        databricks = payload.groups[3]
        assert databricks.input_available is True
        assert databricks.output_available is False
        assert databricks.formats == []
        assert databricks.cache_modes == ["snapshot"]

    def test_csv_capability_shape(self, client: TestClient) -> None:
        payload = IoCapabilitiesResponse.model_validate(client.get("/api/io-capabilities").json())
        file_group = next(group for group in payload.groups if group.name == "file")
        csv = next(fmt for fmt in file_group.formats if fmt.name == "csv")
        assert csv.input is not None and csv.output is not None
        assert csv.input.modes == ["scan", "read"]
        assert csv.input.direct_bounded is True
        assert csv.input.snapshot_build == "bounded"
        assert csv.input.cached_read is True
        assert csv.output.modes == ["sink", "write"]
        assert csv.output.native_sink is True
        assert csv.output.publication == "atomic_file"
        assert "schema_overrides" in csv.input.arguments["scan"]
        assert "separator" in csv.output.arguments["write"]
        assert csv.input.engines_missing == []
        for mode_args in csv.input.arguments.values():
            assert "storage_options" not in mode_args

    def test_engine_gated_format_reports_missing_engines(self, client: TestClient) -> None:
        payload = IoCapabilitiesResponse.model_validate(client.get("/api/io-capabilities").json())
        lakehouse = next(group for group in payload.groups if group.name == "lakehouse")
        delta = next(fmt for fmt in lakehouse.formats if fmt.name == "delta")
        assert delta.input is not None and delta.output is not None
        assert delta.input.engines_missing == ["deltalake"]
        assert delta.output.engines_missing == ["deltalake"]

    def test_read_only_and_database_shapes(self, client: TestClient) -> None:
        payload = IoCapabilitiesResponse.model_validate(client.get("/api/io-capabilities").json())
        formats = {fmt.name: fmt for group in payload.groups for fmt in group.formats}
        ods = formats["ods"]
        assert ods.input is not None and ods.output is None
        database = formats["database"]
        assert database.group == "database"
        assert database.input is not None
        assert database.input.modes == []
        assert database.input.snapshot_build == "bounded"
        assert database.input.engines_missing == []
        records = formats["records"]
        assert records.group == "inline"
        lines = formats["lines"]
        assert lines.unstable is True

    def test_removed_formats_route_is_not_served(self, client: TestClient) -> None:
        assert client.get("/api/formats").status_code == 404
