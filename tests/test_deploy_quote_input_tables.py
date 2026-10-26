"""A deployed Quote Input splits a request into its tables, as the editor does (BUG-31).

The editor shreds a Quote Input's sample through its tables, one frame per port. A
deployed pipeline now shreds each served request the same way, in memory
(``shred_request``), so each port receives its own table's rows and declared types, and
a request that does not fit the tables is refused rather than answered without a row.
A response nesting a table's rows under the quotes answers a request of several quotes
quote by quote: in one frame its rows would be placed under every quote holding their
key, so each quote is scored alone and its drivers are its own (BUG-38; specs/deploy,
specs/json-shredding).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl
import pytest
from fastapi.testclient import TestClient
from polars.testing import assert_frame_equal

from haute._api_input_schema import ApiInputSchemaError
from haute._builders import _build_node_fn
from haute._execution_admission import create_admitted_execution_context
from haute._types import PipelineGraph
from haute.deploy._container import _generate_app_source
from haute.deploy._request_rows import RowRefusal, prove_request_rows, request_rows
from haute.deploy._schema import _read_sample_row, infer_output_schema
from haute.deploy._scorer import QuoteRequest, reads_records, score_graph
from haute.execution import ExecutionProfile, execute_lazy_graph
from tests.conftest import build_test_api_input_snapshots

QUOTES: list[dict[str, Any]] = [
    {"quote_id": 7, "region": "S", "drivers": [{"age": 19}, {"age": 44}]},
    {"quote_id": 3, "region": "N", "drivers": [{"age": 61}]},
    {"quote_id": 5, "region": "E", "drivers": []},
]


def _column(name: str, path: str, type_: str) -> dict[str, Any]:
    return {"name": name, "path": path, "type": type_, "status": "Confirmed", "selected": True}


TABLES = [
    {
        "path": "$[:]",
        "label": "quote",
        "emit": True,
        "columns": [
            _column("quote_id", "$[:].quote_id", "int"),
            _column("region", "$[:].region", "str"),
        ],
    },
    {
        "path": "$[:].drivers[:]",
        "label": "drivers",
        "emit": True,
        "columns": [
            # The quote's key, carried into each of its drivers.
            _column("quote_id", "$[:].quote_id", "int"),
            _column("age", "$[:].drivers[:].age", "int"),
        ],
    },
]


def _entry(port: str, column: str, path: str) -> dict[str, Any]:
    return {"source_port": port, "source_column": column, "output_path": path, "enabled": True}


def _node(node_id: str, node_type: str, config: dict[str, Any]) -> dict[str, Any]:
    return {"id": node_id, "data": {"label": node_id, "nodeType": node_type, "config": config}}


ROW_LOCAL_BAND = (
    "df = drivers.with_columns(pl.when(pl.col('age') < 25).then(pl.lit('young'))"
    ".otherwise(pl.lit('adult')).alias('band'))"
)
NESTED = RowRefusal("out", "out", "response nests 'banded' under each quote", "unproven")


def _graph(
    sample: Path, *, tables: list[dict[str, Any]] = TABLES, band: str = ROW_LOCAL_BAND
) -> PipelineGraph:
    """Each quote answered with its drivers, banded, nested under it."""
    mapping = [
        _entry("quote", "quote_id", "$[:].quote_id"),
        _entry("quote", "region", "$[:].region"),
        _entry("banded", "quote_id", "$[:].quote_id"),
        _entry("banded", "age", "$[:].drivers[:].age"),
        _entry("banded", "band", "$[:].drivers[:].band"),
    ]
    return PipelineGraph.model_validate(
        {
            "nodes": [
                _node("request", "apiInput", {"path": str(sample), "tables": tables}),
                _node("banded", "polars", {"code": band}),
                _node("out", "output", {"outputMapping": mapping, "outputFormat": "json"}),
            ],
            "edges": [
                {"id": "q", "source": "request", "target": "out", "sourceHandle": "quote"},
                {"id": "d", "source": "request", "target": "banded", "sourceHandle": "drivers"},
                {"id": "b", "source": "banded", "target": "out"},
            ],
        }
    )


EXPECTED = [
    {
        "quote_id": 7,
        "region": "S",
        "drivers": [{"age": 19, "band": "young"}, {"age": 44, "band": "adult"}],
    },
    {"quote_id": 3, "region": "N", "drivers": [{"age": 61, "band": "adult"}]},
    # A quote with no drivers has no drivers table: a rendered answer leaves it out.
    {"quote_id": 5, "region": "E"},
]


@pytest.fixture
def sample(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    path = haute_scratch / "request.json"
    path.write_text(json.dumps(QUOTES), encoding="utf-8")
    return path


def _deployed(graph: PipelineGraph, quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from haute._output_assembler import render_output_document

    rows = request_rows(graph, ["request"], "out", {})
    answer = score_graph(
        graph=graph,
        input_df=QuoteRequest(tuple(quotes)),
        input_node_ids=["request"],
        output_node_id="out",
        request_rows=rows,
    )
    return render_output_document(answer)


def _alone(graph: PipelineGraph, quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [answer for quote in quotes for answer in _deployed(graph, [quote])]


def _together(graph: PipelineGraph, quotes: list[dict[str, Any]]) -> pl.DataFrame:
    """The quotes scored as one frame, as a deployment would if nothing stopped it."""
    return score_graph(
        graph=graph,
        input_df=QuoteRequest(tuple(quotes)),
        input_node_ids=["request"],
        output_node_id="out",
    )


def test_each_port_receives_its_own_table_and_the_answer_matches_the_editor(sample):
    graph = _graph(sample)
    assert reads_records(graph, ["request"])

    deployed = [answer for quote in QUOTES for answer in _deployed(graph, [quote])]

    assert deployed == EXPECTED
    # The editor reads the same quotes from the Quote Input's sample.
    build_test_api_input_snapshots(sample, graph.node_map["request"].data.config)
    context = create_admitted_execution_context(
        operation="quote_input_tables_reference", profile=ExecutionProfile.LAZY_SINK
    )
    try:
        outputs, _, _, _ = execute_lazy_graph(
            graph, _build_node_fn, source="live", execution_context=context
        )
        edited = outputs["out"].collect()
    finally:
        context.release_admission(preserve_primary_error=True)
    assert_frame_equal(
        score_graph(
            graph=graph,
            input_df=QuoteRequest(tuple(QUOTES)),
            input_node_ids=["request"],
            output_node_id="out",
        ),
        edited,
    )


def test_ports_receive_their_declared_types_and_one_quote_scores_alone(sample):
    graph = _graph(sample)
    table = score_graph(
        graph=graph,
        input_df=QuoteRequest((QUOTES[0],)),
        input_node_ids=["request"],
        output_node_id="banded",
    )

    assert table.schema == pl.Schema({"quote_id": pl.Int64, "age": pl.Int64, "band": pl.String})
    assert table["age"].to_list() == [19, 44]
    assert _deployed(graph, [QUOTES[1]]) == [EXPECTED[1]]


@pytest.mark.parametrize(
    ("quotes", "message"),
    [
        ([QUOTES[0], "not a quote"], "quote 1 is not an object"),
        (
            [{"quote_id": 1, "region": "N", "drivers": [{"age": 30}, "x"]}],
            "1 in 'drivers'",
        ),
        ([{"quote_id": 1, "region": "N", "drivers": {"age": 30}}], "1 in 'drivers'"),
        ([{"quote_id": 1, "region": "N", "drivers": "x"}], "1 in 'drivers'"),
        ([{"quote_id": "one", "region": "N", "drivers": []}], "column 'quote_id'"),
    ],
)
def test_a_request_that_does_not_fit_the_tables_is_refused(sample, quotes, message):
    with pytest.raises(ApiInputSchemaError, match=message):
        score_graph(
            graph=_graph(sample),
            input_df=QuoteRequest(tuple(quotes)),
            input_node_ids=["request"],
            output_node_id="out",
        )


def test_drivers_nested_under_each_quote_answer_a_batch_quote_by_quote(sample):
    graph = _graph(sample)

    assert prove_request_rows(graph, ["request"], "out", {}) == ("quote_by_quote", (NESTED,), ())
    assert _deployed(graph, QUOTES) == EXPECTED == _alone(graph, QUOTES)


def test_a_rejoin_of_a_quote_s_drivers_answers_a_batch_quote_by_quote(sample):
    rejoin = _graph(sample).model_dump()
    rejoin["nodes"].append(
        _node(
            "youngest",
            "polars",
            {"code": "df = drivers.group_by('quote_id').agg(pl.col('age').min().alias('min_age'))"},
        )
    )
    rejoin["nodes"].append(
        _node(
            "joined",
            "edgeJoin",
            {"how": "left", "on": "quote_id", "suffix": "_y", "maintainOrder": "left_right"},
        )
    )
    rejoin["nodes"] = [n for n in rejoin["nodes"] if n["id"] != "out"]
    rejoin["nodes"].append(
        _node(
            "out",
            "output",
            {
                "outputMapping": [_entry("joined", "min_age", "$[:].min_age")],
                "outputFormat": "json",
            },
        )
    )
    rejoin["edges"] = [
        {
            "id": "q",
            "source": "request",
            "target": "joined",
            "sourceHandle": "quote",
            "targetHandle": "base",
        },
        {"id": "d", "source": "request", "target": "youngest", "sourceHandle": "drivers"},
        {"id": "y", "source": "youngest", "target": "joined", "targetHandle": "join"},
        {"id": "o", "source": "joined", "target": "out"},
    ]

    assert prove_request_rows(PipelineGraph.model_validate(rejoin), ["request"], "out", {}) == (
        "quote_by_quote",
        (
            RowRefusal(
                "joined", "joined", "join (how='left', maintain_order='left_right')", "mixes"
            ),
        ),
        (),
    )


def test_a_calculation_across_a_table_s_rows_answers_each_quote_alone(sample):
    """A drivers' mean taken over a batch would mix its quotes; each is priced alone."""
    graph = _graph(sample, band="df = drivers.with_columns(pl.col('age').mean().alias('band'))")
    rows = request_rows(graph, ["request"], "out", {})

    assert rows.refusals == (NESTED,)
    batch = _deployed(graph, QUOTES[:2])
    assert batch == _alone(graph, QUOTES[:2])
    assert [[d["band"] for d in answer["drivers"]] for answer in batch] == [[31.5, 31.5], [61.0]]
    # In one frame, each quote would get the mean of all three drivers.
    together = _together(graph, QUOTES[:2])
    assert together["drivers"].list.eval(pl.element().struct.field("band")).to_list() == [
        [124 / 3, 124 / 3],
        [124 / 3],
    ]


def test_two_quotes_sharing_the_key_their_drivers_nest_by_keep_their_own_drivers(sample):
    shared = [QUOTES[0], {**QUOTES[1], "quote_id": QUOTES[0]["quote_id"]}]

    batch = _deployed(_graph(sample), shared)

    assert [answer["drivers"] for answer in batch] == [
        EXPECTED[0]["drivers"],
        EXPECTED[1]["drivers"],
    ]
    # In one frame, each quote would take the other's drivers too.
    together = _together(_graph(sample), shared)
    ages = together["drivers"].list.eval(pl.element().struct.field("age")).to_list()
    assert ages == [[19, 44, 61], [19, 44, 61]]


def test_a_key_a_node_rewrites_cannot_give_a_quote_another_s_drivers(sample):
    """A node's own column step can relabel a computed column as the key the drivers nest by."""
    graph = _graph(
        sample,
        band="df = drivers.with_columns(other_key=10 - pl.col('quote_id'), band=pl.lit('a'))",
    ).model_dump()
    graph["nodes"][1]["data"]["config"].update(
        {
            "selected_columns": ["other_key", "age", "band"],
            "column_renames": {"other_key": "quote_id"},
        }
    )
    swapped = PipelineGraph.model_validate(graph)

    # Quote by quote, a quote's drivers carry another key and nest under nothing.
    assert _deployed(swapped, QUOTES[:2]) == _alone(swapped, QUOTES[:2])
    assert all("drivers" not in answer for answer in _deployed(swapped, QUOTES[:2]))
    # In one frame, quotes 7 and 3 would each take the other's drivers.
    ages = _together(swapped, QUOTES[:2])["drivers"].list.eval(pl.element().struct.field("age"))
    assert ages.to_list() == [[61], [19, 44]]


def test_a_quote_input_without_a_root_table_cannot_answer_in_order(sample):
    graph = _graph(sample, tables=[TABLES[1]]).model_dump()
    graph["edges"] = [e for e in graph["edges"] if e["id"] != "q"]
    graph["nodes"][2]["data"]["config"]["outputMapping"] = [_entry("banded", "age", "$[:].age")]

    verdict, refusals, _checks = prove_request_rows(
        PipelineGraph.model_validate(graph), ["request"], "out", {}
    )
    assert verdict == "quote_by_quote"
    assert [refusal.node_id for refusal in refusals] == ["out"]


def test_the_dry_run_reads_the_sample_s_first_quote_into_the_tables(sample):
    graph = _graph(sample)

    assert _read_sample_row(graph, ["request"]) == QuoteRequest((QUOTES[0],))
    assert infer_output_schema(graph, "out", ["request"]) == {
        "quote_id": "Int64",
        "region": "String",
        "drivers": "List(Struct({'age': Int64, 'band': String}))",
    }


def test_an_xml_sample_s_single_driver_is_a_list_of_one_in_the_dry_run(
    haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request a deployment receives is JSON, so the sample quote takes its shape."""
    monkeypatch.chdir(haute_scratch)
    sample = haute_scratch / "request.xml"
    sample.write_text(
        "<quotes><quote><quote_id>3</quote_id><region>N</region>"
        "<drivers><age>61</age></drivers></quote></quotes>",
        encoding="utf-8",
    )
    tables = [
        {**table, "columns": [{**column, "type": "str"} for column in table["columns"]]}
        for table in TABLES
    ]
    graph = _graph(sample, tables=tables, band="df = drivers.with_columns(band=pl.lit('x'))")

    assert _read_sample_row(graph, ["request"]) == QuoteRequest(
        ({"quote_id": "3", "region": "N", "drivers": [{"age": "61"}]},)
    )
    assert infer_output_schema(graph, "out", ["request"])["drivers"] == (
        "List(Struct({'age': String, 'band': String}))"
    )


def _load_app(root: Path, graph: PipelineGraph) -> Any:
    rows = request_rows(graph, ["request"], "out", {})
    manifest = {
        "pruned_graph": graph.model_dump(mode="json"),
        "input_node_ids": ["request"],
        "output_node_id": "out",
        "request_rows": rows.to_manifest(),
        "artifacts": {},
        "output_fields": None,
    }
    (root / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    app_path = root / "app.py"
    app_path.write_text(_generate_app_source("motor", 8080), encoding="utf-8")
    module_name = f"_haute_tables_app_{root.name}"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)


def test_quote_answers_one_quote_and_several_through_their_tables(
    sample, haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
):
    container = haute_scratch / "container"
    container.mkdir()
    module = _load_app(container, _graph(sample))

    async def in_process(function, *args, config):
        del config
        return function(*args)

    # The batch worker runs in this process, as its spawn would run it.
    monkeypatch.setattr(module, "run_isolated_worker_async", in_process)
    client = TestClient(module.app)

    one = client.post("/quote", json=QUOTES[0])
    several = client.post("/quote", json=QUOTES)
    shared = client.post("/quote", json=[QUOTES[0], {**QUOTES[1], "quote_id": 7}])
    misfit = client.post("/quote", json={"quote_id": 1, "region": "N", "drivers": ["x"]})

    assert one.status_code == 200, one.text
    assert one.json()["rows"] == [EXPECTED[0]]
    assert several.status_code == 200, several.text
    assert several.json()["rows"] == EXPECTED
    assert shared.status_code == 200, shared.text
    assert [row["drivers"] for row in shared.json()["rows"]] == [
        EXPECTED[0]["drivers"],
        EXPECTED[1]["drivers"],
    ]
    assert misfit.status_code == 422
    assert misfit.json()["error_code"] == "api_input_schema_invalid"


def test_the_databricks_model_hands_the_quote_input_its_records(sample):
    from haute.deploy._model_code import _request

    graph = _graph(sample)
    frame = pd.DataFrame(QUOTES)

    request = _request(graph, ["request"], frame, {}, frozenset())

    assert isinstance(request, QuoteRequest)
    assert [record["drivers"] for record in request.records] == [q["drivers"] for q in QUOTES]


def test_a_request_given_as_a_frame_is_read_from_its_records(sample):
    from haute._output_assembler import render_output_document

    answer = score_graph(
        graph=_graph(sample),
        input_df=pl.DataFrame(QUOTES),
        input_node_ids=["request"],
        output_node_id="out",
    )

    assert render_output_document(answer) == EXPECTED


def test_an_empty_sample_gives_no_quote_to_dry_run(sample):
    sample.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="the sample holds no quote"):
        _read_sample_row(_graph(sample), ["request"])


def test_the_batch_policy_is_planned_on_the_sample_s_tables(sample):
    from haute.deploy._schema import infer_deploy_execution_policy

    policy = infer_deploy_execution_policy(
        _graph(sample), "out", ["request"], batch_runtime="hard_capped_worker"
    )

    assert (policy["profile"], policy["runtime"]) == ("deploy_batch", "hard_capped_worker")


def test_the_capped_worker_dry_run_sends_the_sample_quote_as_its_record(
    sample, monkeypatch: pytest.MonkeyPatch
):
    from haute.deploy._schema import _capped_worker_output_schema

    def in_process(function, *args, config):
        del config
        return function(*args)

    # The worker runs in this process, as its spawn would run it.
    monkeypatch.setattr("haute.deploy._schema.run_isolated_worker", in_process)
    schema = _capped_worker_output_schema(
        _graph(sample),
        QuoteRequest((QUOTES[0],)),
        output_node_id="out",
        input_node_ids=["request"],
        artifact_paths={},
    )

    assert schema["drivers"] == "List(Struct({'age': Int64, 'band': String}))"
