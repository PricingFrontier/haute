"""Data Output to a Databricks table: validation, load SQL, the write, its errors."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

from haute._databricks_credentials import DatabricksConfigError
from haute._databricks_output import (
    STAGING_VOLUME,
    DatabricksWriteError,
    _spark_compatible,
    load_statements,
    write_databricks_output,
)
from haute._executable_names import executable_name_violations
from haute._polars_io_registry import (
    PolarsIoConfigError,
    data_output_completeness,
    data_output_group,
    validate_data_output_config,
)
from haute._registry import ensure_registry_ready
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import resolve_data_output_path, write_data_output
from tests.conftest import build_test_input_snapshot

ensure_registry_ready()

_HTTP_PATH = "/sql/1.0/warehouses/abc"
_TABLE = "workspace.haute-data.scored"


def _config(**overrides: Any) -> dict[str, Any]:
    return {
        "outputType": "databricks",
        "http_path": _HTTP_PATH,
        "table": _TABLE,
        "write_mode": "overwrite",
        "arguments": {},
        **overrides,
    }


def _output_node(node_id: str, config: dict[str, Any]) -> GraphNode:
    return GraphNode(
        id=node_id, data=NodeData(label=node_id, nodeType=NodeType.DATA_OUTPUT, config=config)
    )


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def execute(self, statement: str) -> None:
        self._connection.executed.append(statement)
        if statement.startswith("PUT "):
            local = statement.split("'")[1]
            self._connection.staged = pl.read_parquet(local)
        if self._connection.fail_on and statement.startswith(self._connection.fail_on):
            raise RuntimeError("[TABLE_OR_VIEW_NOT_FOUND]\n  The table cannot be found.")

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Connection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.executed: list[str] = []
        self.staged: pl.DataFrame | None = None
        self.fail_on = fail_on
        self.connect_kwargs: dict[str, Any] = {}

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _connect(connection: _Connection) -> Any:
    def connect(**kwargs: Any) -> _Connection:
        connection.connect_kwargs = kwargs
        return connection

    return connect


@pytest.fixture
def _connection_settings():
    with patch(
        "haute._databricks_output._connection_settings",
        return_value=("host", {"access_token": "token"}, _HTTP_PATH),
    ) as settings:
        yield settings


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_complete_config_validates_without_a_format() -> None:
    assert validate_data_output_config(_config()) == _config()
    assert data_output_group(_config()) == "databricks"


def test_write_mode_is_optional() -> None:
    config = _config()
    del config["write_mode"]
    assert validate_data_output_config(config) == config


@pytest.mark.parametrize("field", ["http_path", "table"])
def test_missing_locators_are_completeness_gaps(field: str) -> None:
    config = _config(**{field: ""})
    assert [gap.path for gap in data_output_completeness(config)] == [field]
    with pytest.raises(PolarsIoConfigError, match=field):
        validate_data_output_config(config)
    assert validate_data_output_config(config, require_complete=False) == config


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"write_mode": "merge"}, "write_mode"),
        ({"arguments": {"batch_size": 10}}, "no arguments"),
        ({"format": "parquet"}, "'format' is not valid"),
        ({"mode": "sink"}, "'mode' is not valid"),
        ({"path": "out.parquet"}, "'path' is not valid"),
    ],
)
def test_structural_errors_raise(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(PolarsIoConfigError, match=message):
        validate_data_output_config(_config(**overrides))


# ---------------------------------------------------------------------------
# Load SQL and dtypes
# ---------------------------------------------------------------------------


def test_overwrite_replaces_the_table_from_the_staged_file() -> None:
    assert load_statements(_TABLE, "/Volumes/c/s/v/f.parquet", "overwrite") == [
        "CREATE OR REPLACE TABLE `workspace`.`haute-data`.`scored` "
        "AS SELECT * FROM parquet.`/Volumes/c/s/v/f.parquet`"
    ]


def test_append_creates_a_missing_table_then_inserts_by_name() -> None:
    assert load_statements(_TABLE, "/Volumes/c/s/v/f.parquet", "append") == [
        "CREATE TABLE IF NOT EXISTS `workspace`.`haute-data`.`scored` "
        "AS SELECT * FROM parquet.`/Volumes/c/s/v/f.parquet` LIMIT 0",
        "INSERT INTO `workspace`.`haute-data`.`scored` BY NAME "
        "SELECT * FROM parquet.`/Volumes/c/s/v/f.parquet`",
    ]


def test_unknown_write_mode_is_refused() -> None:
    with pytest.raises(DatabricksConfigError, match="write_mode"):
        load_statements(_TABLE, "/Volumes/c/s/v/f.parquet", "merge")


def test_dtypes_databricks_cannot_read_are_cast() -> None:
    frame = pl.DataFrame(
        {
            "empty": pl.Series([None, None], dtype=pl.Null),
            "at": pl.Series([time(9, 30), time(17, 0)]),
            "stamp": pl.Series([datetime(2026, 1, 1), datetime(2026, 1, 2)]).cast(
                pl.Datetime("ns", "Europe/London")
            ),
            "kept": [1, 2],
        }
    )

    schema = _spark_compatible(frame.lazy()).collect_schema()

    assert schema == pl.Schema(
        {
            "empty": pl.String,
            "at": pl.String,
            "stamp": pl.Datetime("us", "Europe/London"),
            "kept": pl.Int64,
        }
    )


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_connection_settings")
def test_write_stages_loads_and_removes_over_one_connection() -> None:
    frame = pl.DataFrame({"policy_id": ["p1", "p2", "p3"], "premium": [1.0, 2.5, 3.0]})
    connection = _Connection()

    with patch("databricks.sql.connect", _connect(connection)):
        rows = write_databricks_output(frame.lazy(), _config())

    assert rows == 3
    create_volume, put, load, remove = connection.executed
    assert create_volume.startswith(
        f"CREATE VOLUME IF NOT EXISTS `workspace`.`haute-data`.`{STAGING_VOLUME}`"
    )
    staged = put.split("'")[3]
    assert staged.startswith(f"/Volumes/workspace/haute-data/{STAGING_VOLUME}/")
    assert put.endswith(" OVERWRITE")
    assert load == load_statements(_TABLE, staged, "overwrite")[0]
    assert remove == f"REMOVE '{staged}'"
    assert connection.staged is not None
    assert connection.staged.equals(frame)
    local_dir = connection.connect_kwargs["staging_allowed_local_path"]
    assert Path(put.split("'")[1]).parent == Path(local_dir)
    assert not Path(local_dir).exists()
    assert connection.connect_kwargs["http_path"] == _HTTP_PATH
    assert connection.connect_kwargs["access_token"] == "token"


@pytest.mark.usefixtures("_connection_settings")
def test_append_runs_both_load_statements() -> None:
    connection = _Connection()

    with patch("databricks.sql.connect", _connect(connection)):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config(write_mode="append"))

    assert [statement.split(" ")[0] for statement in connection.executed] == [
        "CREATE",
        "PUT",
        "CREATE",
        "INSERT",
        "REMOVE",
    ]


@pytest.mark.usefixtures("_connection_settings")
def test_a_failed_load_still_removes_the_staged_file_and_reports_the_warehouse_error() -> None:
    connection = _Connection(fail_on="CREATE OR REPLACE")

    with (
        patch("databricks.sql.connect", _connect(connection)),
        pytest.raises(DatabricksWriteError) as excinfo,
    ):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config())

    assert connection.executed[-1].startswith("REMOVE ")
    assert str(excinfo.value) == (
        f"Databricks could not write {_TABLE}: [TABLE_OR_VIEW_NOT_FOUND] The table cannot be found."
    )


def test_an_invalid_table_name_is_refused_before_connecting() -> None:
    with pytest.raises(DatabricksConfigError, match="Invalid table name"):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config(table="no_schema"))


def test_missing_credentials_name_the_data_output_node(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(DatabricksConfigError, match="http_path on the Data Output node"):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config(http_path=""))


@pytest.mark.usefixtures("_connection_settings")
def test_pipeline_write_reports_the_table_and_row_count(haute_scratch: Path) -> None:
    source = haute_scratch / "in.parquet"
    pl.DataFrame({"policy_id": ["p1", "p2"], "premium": [1.0, 2.0]}).write_parquet(source)
    input_config = {"inputType": "file", "format": "parquet", "path": str(source)}
    build_test_input_snapshot(input_config)
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="din",
                data=NodeData(label="din", nodeType=NodeType.DATA_INPUT, config=input_config),
            ),
            _output_node("dout", _config(selected_columns=["premium"])),
        ],
        edges=[GraphEdge(id="e", source="din", target="dout")],
    )
    connection = _Connection()

    with patch("databricks.sql.connect", _connect(connection)):
        result = write_data_output(graph, "dout")

    assert (result.status, result.row_count, result.path, result.format) == (
        "ok",
        2,
        _TABLE,
        "databricks",
    )
    assert result.message == f"Wrote 2 rows to {_TABLE}"
    assert connection.staged is not None
    assert connection.staged.columns == ["premium"]


# ---------------------------------------------------------------------------
# Destination identity
# ---------------------------------------------------------------------------


def test_destination_is_the_table_with_no_file() -> None:
    graph = PipelineGraph(nodes=[_output_node("dout", _config())], edges=[])
    assert resolve_data_output_path(graph, _config()) == (None, _TABLE)


def test_two_outputs_to_one_table_spelled_differently_are_refused() -> None:
    graph = PipelineGraph(
        nodes=[
            _output_node("first", _config()),
            _output_node("second", _config(table="`Workspace`.`haute-data`.Scored")),
        ],
        edges=[],
    )
    assert [v.kind for v in executable_name_violations(graph)] == ["output_destination"]


def test_outputs_to_different_tables_are_distinct() -> None:
    graph = PipelineGraph(
        nodes=[
            _output_node("first", _config()),
            _output_node("second", _config(table="workspace.haute-data.other")),
        ],
        edges=[],
    )
    assert executable_name_violations(graph) == []


# ---------------------------------------------------------------------------
# Write-output worker failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "expected_kind"),
    [
        (DatabricksConfigError("Databricks credentials are not configured"), "databricks_config"),
        (DatabricksWriteError("Databricks could not write t: denied"), "databricks_write"),
    ],
)
def test_worker_reports_databricks_failures_by_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_kind: str,
) -> None:
    from haute._execution_admission import IsolatedExecutionBudget
    from haute._execution_context import ExecutionProfile
    from haute.routes import pipeline as pipeline_route

    class _Context:
        def release_admission(self, *, preserve_primary_error: bool = False) -> None:
            return None

    def _prepare(*_args: Any, **_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(
        pipeline_route, "create_isolated_execution_context", lambda _budget: _Context()
    )
    monkeypatch.setattr(pipeline_route, "prepare_data_output", _prepare)
    budget = IsolatedExecutionBudget(
        operation="pipeline_write_output",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=1024,
        config_key="test",
        budget_policy="fixed_default",
    )

    outcome = pipeline_route._prepare_data_output_worker(
        PipelineGraph(), "sink", "batch", str(tmp_path), False, None, None, budget
    )

    assert (outcome.failure_kind, outcome.detail) == (expected_kind, str(failure))
