"""Chunk execution through the canonical Data Input provider contract."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import polars.testing as plt
import pytest

from haute._execution_context import ExecutionProfile
from haute._input_providers import build_input_snapshot
from haute._sandbox import set_project_root
from haute._source_cache import SourceCacheStore
from haute.chunking import (
    ChunkPlanRequest,
    ChunkRunnerRequest,
    chunk_plan,
    collect_chunked,
)
from haute.errors import ChunkPlanUnsupportedError
from haute.executor import _build_node_fn
from tests.conftest import make_edge, make_graph, make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _node(node_id: str, node_type: str, config: dict[str, object]):
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": node_type,
            "config": config,
        },
    }


def _graph(config: dict[str, object], output_fields: list[str]):
    return make_graph(
        {
            "nodes": [
                _node("input", "dataInput", config),
                _node("out", "output", make_output_config(output_fields)),
            ],
            "edges": [make_edge("input", "out").model_dump()],
        }
    )


def _run(graph, *, output_fields: list[str]) -> pl.DataFrame:
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=2,
            required_columns_by_node={"out": frozenset(output_fields)},
        )
    )
    return collect_chunked(
        ChunkRunnerRequest(
            graph=graph,
            plan=plan,
            build_node_fn=_build_node_fn,
        ),
        allow_unbounded=True,
    )


def test_direct_ndjson_input_and_row_local_editor_code_are_chunked(
    tmp_path: Path,
) -> None:
    path = tmp_path / "input.ndjson"
    pl.DataFrame({"id": [1, 2, 3, 4], "value": [10, 20, 30, 40]}).write_ndjson(path)
    config: dict[str, object] = {
        "inputType": "file",
        "format": "ndjson",
        "cacheMode": "direct",
        "path": str(path),
        "code": (
            "df = df.filter(pl.col('id') % 2 == 0)"
            ".with_columns((pl.col('value') * 2).alias('doubled'))"
        ),
    }

    result = _run(_graph(config, ["id", "doubled"]), output_fields=["id", "doubled"])

    plt.assert_frame_equal(
        result,
        pl.DataFrame({"id": [2, 4], "doubled": [40, 80]}),
    )


def test_cached_eager_only_format_runs_from_published_parquet_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    path = tmp_path / "input.json"
    pl.DataFrame({"id": [1, 2, 3], "value": ["a", "b", "c"]}).write_json(path)
    config: dict[str, object] = {
        "inputType": "file",
        "format": "json",
        "mode": "read",
        "cacheMode": "snapshot",
        "path": str(path),
    }
    build_input_snapshot(
        config,
        store=SourceCacheStore(tmp_path),
        base_dir=tmp_path,
        profile=ExecutionProfile.PREVIEW_EAGER,
    )

    result = _run(_graph(config, ["id", "value"]), output_fields=["id", "value"])

    plt.assert_frame_equal(
        result,
        pl.DataFrame({"id": [1, 2, 3], "value": ["a", "b", "c"]}),
    )


def test_direct_eager_only_format_is_rejected_before_execution(tmp_path: Path) -> None:
    config: dict[str, object] = {
        "inputType": "file",
        "format": "json",
        "mode": "read",
        "cacheMode": "direct",
        "path": str(tmp_path / "input.json"),
    }

    with pytest.raises(ChunkPlanUnsupportedError, match="bounded"):
        chunk_plan(
            ChunkPlanRequest(
                graph=_graph(config, ["id"]),
                target_node_id="out",
                chunk_size=2,
                required_columns_by_node={"out": {"id"}},
            )
        )
