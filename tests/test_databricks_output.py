"""Data Output to a Databricks table: validation, load SQL, the write, its errors."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest
import structlog.testing

from haute._chunked_writes import WriteRecipe
from haute._databricks_credentials import DatabricksConfigError, DatabricksCredentials
from haute._databricks_output import (
    STAGING_VOLUME,
    UPLOAD_PARALLELISM,
    UPLOAD_PART_BYTES,
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
_TABLE = "workspace.pricing-dev.scored"


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


_TOKEN = "dapi-review-only-token"
# A relative signed URL as a storage error prints it, its signature last and first.
_SIGNED = [
    "/Volumes/x/y/z.parquet?sv=2024-01-01&se=2026-01-01&sig=signed-url-secret",
    "/Volumes/x/y/z.parquet?sig=signed-url-secret&sv=2024-01-01",
]


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def execute(self, statement: str) -> None:
        self._connection.executed.append(statement)
        if self._connection.fail_on and statement.startswith(self._connection.fail_on):
            raise RuntimeError("[TABLE_OR_VIEW_NOT_FOUND]\n  The table cannot be found.")

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Connection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.executed: list[str] = []
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


class _Files:
    """The Files API: records calls, and reads the staged Parquet as uploaded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.upload_kwargs: dict[str, Any] = {}
        self.local: str | None = None
        self.staged: pl.DataFrame | None = None
        self.upload_error: Exception | None = None
        self.delete_error: Exception | None = None

    def upload_from(self, file_path: str, source_path: str, **kwargs: Any) -> None:
        self.calls.append(("upload", file_path))
        self.upload_kwargs = kwargs
        self.local = source_path
        self.staged = pl.read_parquet(source_path)
        if self.upload_error is not None:
            raise self.upload_error

    def delete(self, file_path: str) -> None:
        self.calls.append(("delete", file_path))
        if self.delete_error is not None:
            raise self.delete_error


@pytest.fixture
def files():
    credentials = DatabricksCredentials(
        workspace_host="https://host",
        server_hostname="host",
        auth_mode="pat",
        token=_TOKEN,
        client_id=None,
        client_secret=None,
    )
    fake = _Files()
    with (
        patch(
            "haute._databricks_output._resolve_connection",
            return_value=(credentials, _HTTP_PATH),
        ),
        patch(
            "haute._databricks_output.databricks_workspace_client",
            return_value=SimpleNamespace(files=fake),
        ),
    ):
        yield fake


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
        "CREATE OR REPLACE TABLE `workspace`.`pricing-dev`.`scored` "
        "AS SELECT * FROM parquet.`/Volumes/c/s/v/f.parquet`"
    ]


def test_append_creates_a_missing_table_then_inserts_by_name() -> None:
    assert load_statements(_TABLE, "/Volumes/c/s/v/f.parquet", "append") == [
        "CREATE TABLE IF NOT EXISTS `workspace`.`pricing-dev`.`scored` "
        "AS SELECT * FROM parquet.`/Volumes/c/s/v/f.parquet` LIMIT 0",
        "INSERT INTO `workspace`.`pricing-dev`.`scored` BY NAME "
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


def test_write_stages_uploads_in_bounded_parts_loads_and_removes(files: _Files) -> None:
    frame = pl.DataFrame({"policy_id": ["p1", "p2", "p3"], "premium": [1.0, 2.5, 3.0]})
    connection = _Connection()

    with patch("databricks.sql.connect", _connect(connection)):
        rows = write_databricks_output(frame.lazy(), _config())

    assert rows == 3
    create_volume, load = connection.executed
    assert create_volume.startswith(
        f"CREATE VOLUME IF NOT EXISTS `workspace`.`pricing-dev`.`{STAGING_VOLUME}`"
    )
    (_, staged), removed = files.calls
    assert staged.startswith(f"/Volumes/workspace/pricing-dev/{STAGING_VOLUME}/")
    assert removed == ("delete", staged)
    assert load == load_statements(_TABLE, staged, "overwrite")[0]
    assert files.upload_kwargs == {
        "overwrite": True,
        "part_size": UPLOAD_PART_BYTES,
        "parallelism": UPLOAD_PARALLELISM,
    }
    assert files.staged is not None
    assert files.staged.equals(frame)
    assert files.local is not None
    assert not Path(files.local).parent.exists()
    assert connection.connect_kwargs == {
        "server_hostname": "host",
        "http_path": _HTTP_PATH,
        "access_token": _TOKEN,
    }


def test_append_runs_both_load_statements(files: _Files) -> None:
    connection = _Connection()

    with patch("databricks.sql.connect", _connect(connection)):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config(write_mode="append"))

    assert [statement.split(" ")[0] for statement in connection.executed] == [
        "CREATE",
        "CREATE",
        "INSERT",
    ]
    assert [call for call, _path in files.calls] == ["upload", "delete"]


def test_a_failed_load_still_removes_the_staged_file_and_reports_the_warehouse_error(
    files: _Files,
) -> None:
    connection = _Connection(fail_on="CREATE OR REPLACE")

    with (
        patch("databricks.sql.connect", _connect(connection)),
        pytest.raises(DatabricksWriteError) as excinfo,
    ):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config())

    assert files.calls[-1][0] == "delete"
    assert str(excinfo.value) == (
        f"Databricks could not write {_TABLE}: [TABLE_OR_VIEW_NOT_FOUND] The table cannot be found."
    )


@pytest.mark.parametrize("signed", _SIGNED)
def test_a_failed_upload_removes_what_it_staged_and_reports_no_signed_url(
    files: _Files, signed: str
) -> None:
    # The storage accepted the bytes but the response failed: the file may exist.
    files.upload_error = RuntimeError(f"Max retries exceeded with url: {signed} (timed out)")
    connection = _Connection()

    with (
        patch("databricks.sql.connect", _connect(connection)),
        pytest.raises(DatabricksWriteError) as excinfo,
    ):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config())

    assert [call for call, _path in files.calls] == ["upload", "delete"]
    assert len(connection.executed) == 1  # the volume; nothing was loaded
    message = str(excinfo.value)
    assert "signed-url-secret" not in message
    assert "sig=<redacted>" in message
    assert "sv=2024-01-01" in message


def test_the_token_never_reaches_the_error(files: _Files) -> None:
    connection = _Connection()
    files.upload_error = RuntimeError(f"401 for Authorization: Bearer {_TOKEN}")

    with (
        patch("databricks.sql.connect", _connect(connection)),
        pytest.raises(DatabricksWriteError) as excinfo,
    ):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config())

    assert _TOKEN not in str(excinfo.value)


@pytest.mark.parametrize("signed", _SIGNED)
def test_a_failed_cleanup_is_logged_redacted_and_the_write_stands(
    files: _Files, signed: str
) -> None:
    files.delete_error = RuntimeError(f"403 deleting url: {signed}")
    connection = _Connection()

    with (
        patch("databricks.sql.connect", _connect(connection)),
        structlog.testing.capture_logs() as logs,
    ):
        rows = write_databricks_output(pl.LazyFrame({"x": [1, 2]}), _config())

    assert rows == 2
    (warning,) = [log for log in logs if log["event"] == "databricks_staging_cleanup_failed"]
    assert "signed-url-secret" not in warning["error"]
    assert "sig=<redacted>" in warning["error"]


def test_a_file_the_upload_never_created_is_not_a_cleanup_failure(files: _Files) -> None:
    from databricks.sdk.errors import NotFound

    files.upload_error = RuntimeError("connection reset")
    files.delete_error = NotFound("no such file")

    with (
        patch("databricks.sql.connect", _connect(_Connection())),
        structlog.testing.capture_logs() as logs,
        pytest.raises(DatabricksWriteError, match="connection reset"),
    ):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config())

    assert not [log for log in logs if log["event"] == "databricks_staging_cleanup_failed"]


def test_a_chunked_write_recipe_carries_the_spark_casts(files: _Files) -> None:
    # A filtered frame is not sliceable, so the sink takes the recipe path, which
    # proves the recipe describes the frame written: the casts must be in both.
    source = pl.LazyFrame(
        {
            "x": [1, 2, 3],
            "empty": pl.Series([None, None, None], dtype=pl.Null),
            "at": [time(9), time(10), time(11)],
        }
    )
    recipe = WriteRecipe(source, fn=lambda lf: lf.filter(pl.col("x") > 1))

    with patch("databricks.sql.connect", _connect(_Connection())):
        rows = write_databricks_output(recipe.native(), _config(), recipe=recipe)

    assert rows == 2
    assert files.staged is not None
    assert files.staged.schema == pl.Schema({"x": pl.Int64, "empty": pl.String, "at": pl.String})


def test_an_invalid_table_name_is_refused_before_connecting() -> None:
    with pytest.raises(DatabricksConfigError, match="Invalid table name"):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config(table="no_schema"))


def test_missing_credentials_name_the_data_output_node(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(DatabricksConfigError, match="http_path on the Data Output node"):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config(http_path=""))


def test_pipeline_write_reports_the_table_and_row_count(haute_scratch: Path, files: _Files) -> None:
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
    assert files.staged is not None
    assert files.staged.columns == ["premium"]


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
            _output_node("second", _config(table="`Workspace`.`pricing-dev`.Scored")),
        ],
        edges=[],
    )
    assert [v.kind for v in executable_name_violations(graph)] == ["output_destination"]


def test_outputs_to_different_tables_are_distinct() -> None:
    graph = PipelineGraph(
        nodes=[
            _output_node("first", _config()),
            _output_node("second", _config(table="workspace.pricing-dev.other")),
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


def test_a_missing_databricks_extra_is_a_config_error(files: _Files) -> None:
    with (
        patch(
            "haute._databricks_output.databricks_workspace_client",
            side_effect=ImportError("No module named 'databricks.sdk'"),
        ),
        pytest.raises(DatabricksConfigError, match=r"haute\[databricks\]"),
    ):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config())

    assert files.calls == []


def test_a_config_error_while_connecting_is_not_reported_as_a_write_failure(
    files: _Files,
) -> None:
    # The service-principal provider raises it when the SDK it needs is missing.
    def connect(**_kwargs: Any) -> _Connection:
        raise DatabricksConfigError("service-principal auth requires the databricks-sdk")

    with (
        patch("databricks.sql.connect", connect),
        pytest.raises(DatabricksConfigError, match="service-principal"),
    ):
        write_databricks_output(pl.LazyFrame({"x": [1]}), _config())
