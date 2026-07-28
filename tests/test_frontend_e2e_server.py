"""Contracts for the generated Playwright project fixture."""

from pathlib import Path

import polars as pl
import pytest

from haute.executor import execute_graph
from haute.parser import parse_pipeline_file
from scripts import run_frontend_e2e_server


def test_blank_scaffold_is_augmented_with_complete_browser_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rating_dir = tmp_path / "rating"
    rating_dir.mkdir()
    pipeline_path = rating_dir / "main.py"
    pipeline_path.write_text(
        'import haute\n\npipeline = haute.Pipeline("browser_fixture")\n',
        encoding="utf-8",
    )
    data_dir = rating_dir / "data"
    data_dir.mkdir()
    pl.DataFrame(
        {
            "id": [1, 2],
            "value": [11.0, 23.0],
            "proposer_age": [24, 52],
            "channel": ["direct", "broker"],
            "vehicle_age": [2, 9],
        }
    ).write_parquet(data_dir / "sample.parquet")
    monkeypatch.setattr(run_frontend_e2e_server, "E2E_PROJECT_DIR", tmp_path)
    monkeypatch.chdir(tmp_path)

    run_frontend_e2e_server._augment_starter_pipeline()

    graph = parse_pipeline_file(pipeline_path)
    assert {node.id for node in graph.nodes} == {
        "raw_rows",
        "enriched",
        "priced",
        "browser_model",
        "browser_mixed_banding",
        "browser_rating",
        "browser_optimiser_rows",
        "browser_optimiser",
        "browser_apply",
        "quotes",
    }

    results = execute_graph(graph, target_node_id="browser_mixed_banding")
    assert results["raw_rows"].status == "ok", results["raw_rows"].error
    assert results["enriched"].status == "ok", results["enriched"].error
    assert results["browser_mixed_banding"].status == "ok", results["browser_mixed_banding"].error
