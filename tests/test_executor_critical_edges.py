"""Focused coverage for critical executor edge contracts."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute import executor
from haute.executor import (
    PreambleError,
    _compile_preamble,
    _estimate_preview_cache_entry_bytes,
    _evict_utility_import_state,
    _preamble_has_imports,
    _preview_projection_columns,
    _preview_row_limit_for_width,
    _result_order_for_target,
    execute_graph,
    write_data_output,
)
from tests.conftest import (
    make_edge as _edge,
)
from tests.conftest import (
    make_graph as _g,
)
from tests.conftest import (
    make_node as _n,
)
from tests.conftest import (
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)


def test_evict_utility_import_state_removes_file_and_package_bytecode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    utility_file = tmp_path / "utility.py"
    utility_file.write_text("VALUE = 1\n", encoding="utf-8")
    file_pycache = tmp_path / "__pycache__"
    file_pycache.mkdir()
    file_bytecode = file_pycache / "utility.cpython-313.pyc"
    file_bytecode.write_bytes(b"stale")

    utility_package = tmp_path / "utility"
    nested_pycache = utility_package / "nested" / "__pycache__"
    nested_pycache.mkdir(parents=True)
    package_bytecode = nested_pycache / "helpers.cpython-313.pyc"
    package_bytecode.write_bytes(b"stale")
    not_a_cache_dir = utility_package / "not_a_dir" / "__pycache__"
    not_a_cache_dir.parent.mkdir()
    not_a_cache_dir.write_bytes(b"not a directory")

    pipeline_dir = tmp_path / "pipeline"
    pipeline_dir.mkdir()
    (pipeline_dir / "utility.py").write_text("VALUE = 2\n", encoding="utf-8")

    _evict_utility_import_state(str(pipeline_dir))

    assert not file_bytecode.exists()
    assert not package_bytecode.exists()


def test_invalid_preamble_is_treated_as_import_sensitive() -> None:
    assert _preamble_has_imports("if True print('broken')") is True


def test_utility_error_outside_cwd_keeps_absolute_source_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "cwd"
    pipeline_dir = tmp_path / "pipeline"
    utility_dir = pipeline_dir / "utility"
    cwd.mkdir()
    utility_dir.mkdir(parents=True)
    (utility_dir / "__init__.py").write_text("", encoding="utf-8")
    bad_module = utility_dir / "bad.py"
    bad_module.write_text("raise RuntimeError('outside cwd')\n", encoding="utf-8")
    monkeypatch.chdir(cwd)

    with pytest.raises(PreambleError) as exc_info:
        _compile_preamble(
            "from utility.bad import *\n",
            force_refresh=True,
            pipeline_dir=pipeline_dir,
        )

    message = str(exc_info.value)
    assert str(bad_module) in message
    assert "outside cwd" in message


def test_non_empty_preamble_without_execution_fingerprint_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "preamble_execution_fingerprint", lambda *_, **__: None)

    with pytest.raises(RuntimeError, match="execution fingerprint"):
        _compile_preamble("VALUE = 1\n", force_refresh=True)


def test_preview_cache_size_accounting_rejects_invalid_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match="eager_outputs"):
        _estimate_preview_cache_entry_bytes({"eager_outputs": []})
    with pytest.raises(TypeError, match="expected Polars DataFrame"):
        _estimate_preview_cache_entry_bytes({"eager_outputs": {"src": object()}})

    original_estimated_size = pl.DataFrame.estimated_size
    monkeypatch.setattr(pl.DataFrame, "estimated_size", lambda self: -1)
    try:
        with pytest.raises(ValueError, match="estimated_size"):
            _estimate_preview_cache_entry_bytes(
                {"eager_outputs": {"src": pl.DataFrame({"x": [1]})}}
            )
    finally:
        monkeypatch.setattr(pl.DataFrame, "estimated_size", original_estimated_size)


def test_preview_row_limit_validation_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="max_preview_rows"):
        _preview_row_limit_for_width(-1, 1)
    with pytest.raises(ValueError, match="column_count"):
        _preview_row_limit_for_width(10, -1)

    monkeypatch.setattr(executor, "PREVIEW_MAX_CELLS", 0)
    with pytest.raises(RuntimeError, match="PREVIEW_MAX_CELLS"):
        _preview_row_limit_for_width(10, 1)

    monkeypatch.setattr(executor, "PREVIEW_MAX_CELLS", 50_000)
    assert _preview_row_limit_for_width(10, 0) == 10


def test_preview_projection_rejects_empty_column_request() -> None:
    with pytest.raises(executor.PreviewProjectionError, match="at least one column"):
        _preview_projection_columns(pl.DataFrame({"x": [1]}), [])
    with pytest.raises(executor.PreviewProjectionError, match="empty names"):
        _preview_projection_columns(pl.DataFrame({"x": [1]}), [""])
    with pytest.raises(executor.PreviewProjectionError, match="not found"):
        _preview_projection_columns(pl.DataFrame({"x": [1]}), ["missing"])

    assert _preview_projection_columns(pl.DataFrame({"x": [1], "y": [2]}), ["x", "x", "y"]) == [
        "x",
        "y",
    ]


def test_result_order_without_target_preserves_execution_order(tmp_path: Path) -> None:
    path = tmp_path / "data.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path)
    graph = _g({"nodes": [_source_node("src", str(path))], "edges": []})

    assert _result_order_for_target(graph, ["src"], None, "live") == ["src"]
    assert _result_order_for_target(graph, ["src"], "missing", "live") == []


def test_column_reference_extraction_handles_editor_config_shapes() -> None:
    refs = executor._extract_column_refs(
        {
            "selected_columns": ["selected", "", 7],
            "target": "target",
            "weight": "",
            "offset": "offset",
            "exclude": ["excluded", None, ""],
            "factors": [{"column": "band"}, {"column": ""}, "legacy"],
            "tables": [{"factors": ["rating", "", 42]}, "not-a-table"],
            "output_column": "excluded",
            "outputColumn": "selected",
        }
    )

    assert refs == {"target", "offset", "band", "rating"}


def test_empty_graph_short_circuit_respects_explicit_contract_flag() -> None:
    assert execute_graph(_g({"nodes": [], "edges": []}), enforce_contracts=False) == {}


def test_instance_schema_warning_reports_missing_original_inputs(tmp_path: Path) -> None:
    original_path = tmp_path / "original.parquet"
    instance_path = tmp_path / "instance.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(original_path)
    pl.DataFrame({"y": [2]}).write_parquet(instance_path)

    original = _transform_node("original", "df = df")
    instance = _transform_node("instance", "df = df")
    instance.data.config["instanceOf"] = "original"
    graph = _g(
        {
            "nodes": [
                _source_node("src_original", str(original_path)),
                original,
                _source_node("src_instance", str(instance_path)),
                instance,
            ],
            "edges": [
                _edge("src_original", "original"),
                _edge("src_instance", "instance"),
            ],
        }
    )

    results = execute_graph(graph)

    assert results["instance"].status == "ok"
    assert [
        (warning.column, warning.status) for warning in results["instance"].schema_warnings
    ] == [("x", "missing")]


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_execute_sink_resolves_relative_output_when_explain_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_dir = tmp_path / "pipeline"
    pipeline_dir.mkdir()
    source_path = pipeline_dir / "input.parquet"
    pl.DataFrame({"x": [1, 2]}).write_parquet(source_path)

    def explain_raises(self) -> str:
        raise RuntimeError("plan unavailable")

    monkeypatch.setattr(pl.LazyFrame, "explain", explain_raises)
    graph = _g(
        {
            "source_file": str(pipeline_dir / "pipeline.py"),
            "nodes": [
                _source_node("src", str(source_path)),
                _n(
                    {
                        "id": "sink",
                        "data": {
                            "label": "sink",
                            "nodeType": "dataOutput",
                            "config": {
                                "outputType": "file",
                                "format": "parquet",
                                "mode": "sink",
                                "path": "outputs/out.parquet",
                                "arguments": {},
                            },
                        },
                    }
                ),
            ],
            "edges": [_edge("src", "sink")],
        }
    )

    result = write_data_output(graph, "sink")

    output_path = pipeline_dir / "outputs" / "out.parquet"
    assert result.status == "ok"
    assert output_path.exists()
    assert pl.read_parquet(output_path)["x"].to_list() == [1, 2]


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_execute_sink_fails_loudly_when_lazy_execution_drops_sink_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "input.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(source_path)
    graph = _g(
        {
            "nodes": [
                _source_node("src", str(source_path)),
                _n(
                    {
                        "id": "sink",
                        "data": {
                            "label": "sink",
                            "nodeType": "dataOutput",
                            "config": {
                                "outputType": "file",
                                "format": "parquet",
                                "mode": "sink",
                                "path": str(tmp_path / "out.parquet"),
                                "arguments": {},
                            },
                        },
                    }
                ),
            ],
            "edges": [_edge("src", "sink")],
        }
    )

    monkeypatch.setattr(executor, "_execute_lazy", lambda *_, **__: ({}, [], {}, {}))

    with pytest.raises(RuntimeError, match="Failed to compute Data Output input"):
        write_data_output(graph, "sink")
