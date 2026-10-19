"""One pipeline, one order, on every surface and every run (PL2-04).

On Polars 2 the default engine keeps a join's input order only when ``maintain_order``
says so and reorders a sort's ties from run to run. Every join Haute builds states its
order and **Sort rows** is stable, so a pipeline with an Edge Join, a Join step and a sort
with ties gives the same rows in the same order from a preview, ``Pipeline.run``,
``Pipeline.score``, a Data Output write and the deploy scorer, run after run. An unpivot's
order stays undefined: its rows are the same as a set (specs/execution-engine).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import uuid
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._config_io import collect_node_configs
from haute._types import PipelineGraph
from haute.codegen import graph_to_code
from haute.deploy._scorer import score_graph
from haute.executor import _preview_cache, execute_graph, write_data_output
from tests.conftest import make_file_output_config, make_ready_file_input_config

RUNS = 20
# Enough rows to cross Polars' morsel boundaries, where an unordered streaming join
# interleaves its output; few rates, so the sort has long runs of ties.
QUOTES = 3_000
REGIONS = ["N", "S", "E", "W", "C"]
RATES = {"N": 1.1, "S": 1.2, "E": 1.1, "W": 1.3, "C": 1.2}
BANDS = {"young": 1.5, "adult": 1.0, "senior": 1.2}
COLUMNS = ["quote_id", "region", "band", "rate", "loading"]


def _quotes() -> pl.DataFrame:
    return pl.DataFrame(
        {
            # Not in key order, so a reordered answer cannot pass by accident.
            "quote_id": [(i * 7919) % QUOTES for i in range(QUOTES)],
            "region": [REGIONS[(i * 3) % len(REGIONS)] for i in range(QUOTES)],
            "band": [list(BANDS)[(i * 5) % len(BANDS)] for i in range(QUOTES)],
        }
    )


def _expected() -> pl.DataFrame:
    rows = [
        {**quote, "rate": RATES[quote["region"]], "loading": BANDS[quote["band"]]}
        for quote in _quotes().to_dicts()
    ]
    # Python's sort is stable: ties keep the quotes' order, as a stable Sort rows does.
    return pl.DataFrame(sorted(rows, key=lambda row: row["rate"]))[COLUMNS]


def _graph(root: Path) -> PipelineGraph:
    quotes = root / "quotes.parquet"
    _quotes().write_parquet(quotes)
    rates = root / "rates.parquet"
    pl.DataFrame({"region": list(RATES), "rate": list(RATES.values())}).write_parquet(rates)
    bands = root / "bands.parquet"
    pl.DataFrame({"band": list(BANDS), "loading": list(BANDS.values())}).write_parquet(bands)
    steps = [
        {"id": "s", "kind": "source", "input": "priced"},
        {
            "id": "j",
            "kind": "join",
            "input": "bands",
            "how": "left",
            "leftOn": ["band"],
            "rightOn": ["band"],
            "suffix": "_b",
            "maintainOrder": "left_right",
        },
        {
            "id": "o",
            "kind": "sort",
            "keys": [{"column": "rate", "descending": False}],
            "nullsLast": False,
        },
    ]

    def node(node_id: str, node_type: str, config: dict) -> dict:
        return {"id": node_id, "data": {"label": node_id, "nodeType": node_type, "config": config}}

    return PipelineGraph.model_validate(
        {
            "nodes": [
                node("quotes", "apiInput", {"path": str(quotes)}),
                node("rates", "dataInput", make_ready_file_input_config(rates)),
                node("bands", "dataInput", make_ready_file_input_config(bands)),
                node(
                    "priced",
                    "edgeJoin",
                    {"how": "left", "on": "region", "suffix": "_r", "maintainOrder": "left_right"},
                ),
                node("ordered", "polars", {"steps": steps}),
                node("written", "dataOutput", make_file_output_config(root / "written.parquet")),
                node(
                    "out",
                    "output",
                    {
                        "outputMapping": [
                            {
                                "source_port": "written",
                                "source_column": column,
                                "output_path": f"$[:].{column}",
                                "enabled": True,
                            }
                            for column in COLUMNS
                        ],
                        "outputFormat": "json",
                    },
                ),
            ],
            "edges": [
                {
                    "id": "q",
                    "source": "quotes",
                    "target": "priced",
                    "sourceHandle": "quotes",
                    "targetHandle": "base",
                },
                {"id": "r", "source": "rates", "target": "priced", "targetHandle": "join"},
                {"id": "p", "source": "priced", "target": "ordered"},
                {"id": "b", "source": "bands", "target": "ordered"},
                {"id": "w", "source": "ordered", "target": "written"},
                {"id": "o", "source": "written", "target": "out"},
            ],
        }
    )


def _import_generated(graph: PipelineGraph, root: Path):
    name = f"order_pipeline_{uuid.uuid4().hex}"
    for rel_path, content in collect_node_configs(graph).items():
        config_file = root / rel_path
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(content, encoding="utf-8")
    module_file = root / f"{name}.py"
    module_file.write_text(graph_to_code(graph, pipeline_name=name), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, module_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _frame(value: object) -> pl.DataFrame:
    frame = value.collect() if isinstance(value, pl.LazyFrame) else value
    assert isinstance(frame, pl.DataFrame)
    return frame.select(COLUMNS)


def test_every_surface_gives_the_same_rows_in_the_same_order_on_every_run(
    haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``Pipeline.run`` reads the Quote Input's sample inside a Haute project.
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "haute.toml").write_text('[project]\nname = "order"\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(haute_scratch)], check=True)
    graph = _graph(haute_scratch)
    expected = _expected()
    pipeline = _import_generated(graph, haute_scratch).pipeline

    for _ in range(RUNS):
        _preview_cache.clear()
        preview = execute_graph(graph, target_node_id="ordered", max_preview_rows=QUOTES)
        assert_frame_equal(pl.DataFrame(preview["ordered"].preview)[COLUMNS], expected)

        assert_frame_equal(_frame(pipeline.run(source="batch")), expected)
        assert_frame_equal(_frame(pipeline.score(_quotes())), expected)

        write_data_output(
            graph, "written", source="batch", project_root=haute_scratch, overwrite=True
        )
        assert_frame_equal(pl.read_parquet(haute_scratch / "written.parquet")[COLUMNS], expected)

        deployed = score_graph(
            graph=graph,
            input_df=_quotes(),
            input_node_ids=["quotes"],
            output_node_id="ordered",
        )
        assert_frame_equal(deployed[COLUMNS], expected)


def test_an_unpivot_gives_the_same_rows_as_a_set_on_every_run(haute_scratch: Path) -> None:
    graph = _graph(haute_scratch)
    nodes = [node.model_dump() for node in graph.nodes if node.id in {"quotes", "rates", "priced"}]
    nodes.append(
        {
            "id": "long",
            "data": {
                "label": "long",
                "nodeType": "polars",
                "config": {"code": "df = priced.unpivot(on=['region', 'band'], index='quote_id')"},
            },
        }
    )
    edges = [edge.model_dump() for edge in graph.edges if edge.target == "priced"]
    edges.append({"id": "l", "source": "priced", "target": "long"})
    unpivoted = PipelineGraph.model_validate({"nodes": nodes, "edges": edges})

    def run() -> pl.DataFrame:
        frame = score_graph(
            graph=unpivoted,
            input_df=_quotes(),
            input_node_ids=["quotes"],
            output_node_id="long",
        )
        return frame.sort(["quote_id", "variable"])

    first = run()
    assert first.height == 2 * QUOTES
    for _ in range(RUNS - 1):
        assert_frame_equal(run(), first)
