"""A deployed pipeline answers each quote of a request with one row, in request order (PL2-04).

Deploy validation proves it from the pruned graph (``prove_request_rows``) and checks every
lookup the deployment carries for repeated join keys over the bundled bytes
(``check_lookups``). A covered deployment scores a request of several quotes as one frame,
its answer counted as a backstop; one the proof does not cover answers it quote by quote,
each quote scored alone and answered with one row (specs/deploy).
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

from haute._types import PipelineGraph
from haute.deploy._batch_scoring import (
    BatchScoreRequest,
    prepare_batch_scoring,
    score_batch_scoring_request,
)
from haute.deploy._container import _generate_app_source, write_bundle
from haute.deploy._request_rows import (
    RequestRows,
    RowRefusal,
    prove_request_rows,
    record_digests,
    request_rows,
)
from haute.deploy._scorer import admit_deploy_execution, score_graph, score_graph_lazy
from haute.deploy._utils import build_manifest
from haute.errors import DeployError, QuoteAnswerRowsError, RequestRowCountError
from tests._deploy_helpers import make_resolved_deploy

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

ORDERED_LEFT = {"how": "left", "on": "region", "suffix": "_r", "maintainOrder": "left_right"}
# Quotes deliberately out of any key order, with a region repeated.
QUOTES = pl.DataFrame(
    {"quote_id": [7, 3, 9, 1, 5], "region": ["S", "N", "E", "N", "W"], "age": [40, 22, 35, 61, 18]}
)
#: The request schema the deployment records from its sample, as the manifest renders it.
QUOTE_SCHEMA = {name: str(dtype) for name, dtype in QUOTES.schema.items()}


def _output(fields: list[str], port: str) -> dict[str, Any]:
    return {
        "outputMapping": [
            {"source_port": port, "source_column": f, "output_path": f"$[:].{f}", "enabled": True}
            for f in fields
        ],
        "outputFormat": "json",
    }


def _node(node_id: str, node_type: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "data": {"label": node_id, "nodeType": node_type, "config": config or {}},
    }


def _lookup(node_id: str, path: str, code: str = "") -> dict[str, Any]:
    """A direct Parquet lookup at *path*: the project's file, as resolution finds it."""
    config: dict[str, Any] = {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": path,
        "arguments": {},
    }
    if code:
        config["code"] = code
    return _node(node_id, "dataInput", config)


def _edge(source: str, target: str, **handles: str) -> dict[str, Any]:
    edge: dict[str, Any] = {"id": f"{source}->{target}", "source": source, "target": target}
    edge.update(handles)
    return edge


def _quotes_edge(target: str, **handles: str) -> dict[str, Any]:
    return _edge("quotes", target, sourceHandle="quotes", **handles)


def _priced_graph(
    join: dict[str, Any],
    remap: dict[str, str],
    *,
    after: str | None = None,
    rates_code: str = "",
    fields: tuple[str, ...] = ("quote_id", "rate"),
) -> PipelineGraph:
    """Quotes -> Edge Join with ``rates`` -> [a Polars node running *after*] -> response."""
    nodes = [
        _node("quotes", "apiInput", {"path": ""}),
        _lookup("rates", remap["rates__rates.parquet"], rates_code),
        _node("priced", "edgeJoin", join),
    ]
    edges = [
        _quotes_edge("priced", targetHandle="base"),
        _edge("rates", "priced", targetHandle="join"),
    ]
    last = "priced"
    if after is not None:
        nodes.append(_node("calc", "polars", {"code": after}))
        edges.append(_edge("priced", "calc"))
        last = "calc"
    nodes.append(_node("out", "output", _output(list(fields), last)))
    edges.append(_edge(last, "out"))
    return PipelineGraph.model_validate({"nodes": nodes, "edges": edges})


@pytest.fixture
def remap(tmp_path: Path) -> dict[str, str]:
    """The bundled lookups: ``rates`` by region and ``bands`` by age band, keys unique."""
    rates = tmp_path / "rates.parquet"
    pl.DataFrame({"region": ["N", "S", "E", "W"], "rate": [1.1, 1.2, 1.3, 1.4]}).write_parquet(
        rates
    )
    bands = tmp_path / "bands.parquet"
    pl.DataFrame({"band": ["young", "adult"], "loading": [1.5, 1.0]}).write_parquet(bands)
    return {"rates__rates.parquet": str(rates), "bands__bands.parquet": str(bands)}


def _refusals(graph: PipelineGraph, remap: dict[str, str] | None = None) -> list[RowRefusal]:
    verdict, refusals, _checks = prove_request_rows(graph, ["quotes"], "out", remap or {})
    assert (verdict == "quote_by_quote") == bool(refusals)
    return list(refusals)


def _score(
    graph: PipelineGraph, remap: dict[str, str], rows: RequestRows, quotes: pl.DataFrame
) -> pl.DataFrame:
    return score_graph(
        graph=graph,
        input_df=quotes,
        input_node_ids=["quotes"],
        output_node_id="out",
        artifact_paths=remap,
        request_rows=rows,
    )


# ---------------------------------------------------------------------------
# Answered in request order
# ---------------------------------------------------------------------------


def _two_join_graph(remap: dict[str, str]) -> PipelineGraph:
    """An Edge Join and a Join step, both ordered left joins, with no ``validate``."""
    step_code = (
        "df = priced.with_columns(\n"
        "    pl.when(pl.col('age') < 25).then(pl.lit('young')).otherwise(pl.lit('adult'))"
        ".alias('band')\n"
        ")\n"
        "df = df.join(bands, left_on=['band'], right_on=['band'], how='left', "
        "suffix='_b', maintain_order='left_right')\n"
        "df = df.with_columns((pl.col('rate') * pl.col('loading')).alias('premium'))"
    )
    nodes = [
        _node("quotes", "apiInput", {"path": ""}),
        _lookup("rates", remap["rates__rates.parquet"]),
        _lookup("bands", remap["bands__bands.parquet"]),
        _node("priced", "edgeJoin", ORDERED_LEFT),
        _node("calc", "polars", {"code": step_code}),
        _node("out", "output", _output(["quote_id", "premium"], "calc")),
    ]
    edges = [
        _quotes_edge("priced", targetHandle="base"),
        _edge("rates", "priced", targetHandle="join"),
        _edge("priced", "calc"),
        _edge("bands", "calc"),
        _edge("calc", "out"),
    ]
    return PipelineGraph.model_validate({"nodes": nodes, "edges": edges})


EXPECTED = [
    {"quote_id": 7, "premium": 1.2},
    {"quote_id": 3, "premium": 1.1 * 1.5},
    {"quote_id": 9, "premium": 1.3},
    {"quote_id": 1, "premium": 1.1},
    {"quote_id": 5, "premium": 1.4 * 1.5},
]


def _assert_expected(rows: list[dict[str, Any]]) -> None:
    assert [row["quote_id"] for row in rows] == [row["quote_id"] for row in EXPECTED]
    assert [row["premium"] for row in rows] == pytest.approx([row["premium"] for row in EXPECTED])


def test_ordered_left_joins_against_unique_lookups_answer_in_request_order(remap):
    graph = _two_join_graph(remap)
    rows = request_rows(graph, ["quotes"], "out", remap)

    assert rows.multi_quote == "in_request_order"
    assert {(check.side_node_id, check.keys) for check in rows.checked_lookups} == {
        ("rates", ("region",)),
        ("bands", ("band",)),
    }
    for _ in range(5):
        _assert_expected(_score(graph, remap, rows, QUOTES).to_dicts())


def test_the_batch_worker_answers_in_request_order_and_counts(tmp_path, remap):
    graph = _two_join_graph(remap)
    rows = request_rows(graph, ["quotes"], "out", remap)
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(QUOTES.to_dicts()), encoding="utf-8")
    request = BatchScoreRequest(
        graph=graph,
        input_node_ids=["quotes"],
        output_node_id="out",
        artifact_paths=remap,
        output_fields=None,
        input_path=str(input_path),
        result_path=str(tmp_path / "result.parquet"),
        input_schema=dict(QUOTES.schema),
        request_rows=rows,
    )
    context = admit_deploy_execution(operation="deploy_quote", row_count=QUOTES.height)
    try:
        outcome = score_batch_scoring_request(request, execution_context=context)
    finally:
        context.release_admission(preserve_primary_error=False)

    assert outcome.row_count == QUOTES.height
    _assert_expected(pl.read_parquet(tmp_path / "result.parquet").to_dicts())


def test_the_databricks_model_answers_in_request_order_or_quote_by_quote(tmp_path, remap):
    from unittest.mock import MagicMock

    from haute.deploy._model_code import HauteModel

    def load(graph: PipelineGraph, rows: RequestRows) -> HauteModel:
        manifest = {
            "polars_version": pl.__version__,
            "pruned_graph": graph.model_dump(mode="json"),
            "input_node_ids": ["quotes"],
            "output_node_id": "out",
            "input_schema": QUOTE_SCHEMA,
            "output_fields": None,
            "request_rows": rows.to_manifest(),
            "artifacts": dict.fromkeys(remap, ""),
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        context = MagicMock()
        context.artifacts = {"deploy_manifest": str(manifest_path), **remap}
        model = HauteModel()
        model.load_context(context)
        return model

    graph = _two_join_graph(remap)
    covered = load(graph, request_rows(graph, ["quotes"], "out", remap))
    answer = covered.predict(MagicMock(), QUOTES.to_pandas())
    _assert_expected(pl.from_pandas(answer).to_dicts())

    # A real sort: in one frame it answers in rate order; quote by quote, in request order.
    sorted_graph = _priced_graph(ORDERED_LEFT, remap, after="df = priced.sort('rate')")
    rows = request_rows(sorted_graph, ["quotes"], "out", remap)
    assert rows.multi_quote == "quote_by_quote"
    uncovered = load(sorted_graph, rows)
    answers = pl.from_pandas(uncovered.predict(MagicMock(), QUOTES.to_pandas()))
    assert answers["quote_id"].to_list() == QUOTES["quote_id"].to_list()
    one = uncovered.predict(MagicMock(), pd.DataFrame(QUOTES.head(1).to_dicts()))
    assert len(one) == 1


# ---------------------------------------------------------------------------
# Lookups checked when the deployment is built
# ---------------------------------------------------------------------------


def test_the_build_refuses_a_lookup_holding_a_join_key_twice(tmp_path, remap):
    pl.DataFrame({"region": ["N", "N", "S"], "rate": [1.1, 9.9, 1.2]}).write_parquet(
        tmp_path / "rates.parquet"
    )

    with pytest.raises(
        DeployError, match="'rates' holds the join key \\{'region': 'N'\\} of 'priced'"
    ):
        request_rows(_priced_graph(ORDERED_LEFT, remap), ["quotes"], "out", remap)


def test_the_build_refuses_keys_repeated_only_by_post_load_code(remap):
    graph = _priced_graph(
        ORDERED_LEFT, remap, rates_code="df = df.with_columns(pl.lit('N').alias('region'))"
    )

    with pytest.raises(DeployError, match="'rates' holds the join key \\{'region': 'N'\\}"):
        request_rows(graph, ["quotes"], "out", remap)


def test_the_build_reads_a_snapshot_lookup_across_its_bundled_parts(tmp_path):
    """A CSV lookup ships as its snapshot generation's parts; a key repeated across two
    parts is found, and the parts' digests are those the bundle copies check."""
    parts = [tmp_path / "part-00000.parquet", tmp_path / "part-00001.parquet"]
    pl.DataFrame({"region": ["N", "S"], "rate": [1.1, 1.2]}).write_parquet(parts[0])
    pl.DataFrame({"region": ["E", "N"], "rate": [1.3, 9.9]}).write_parquet(parts[1])
    bundled = {f"rates__snapshot.{part.name}": str(part) for part in parts}
    lookup = _node(
        "rates",
        "dataInput",
        {
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "path": "rates.csv",
            "arguments": {"schema_overrides": {"region": "String", "rate": "Float64"}},
        },
    )
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                lookup,
                _node("priced", "edgeJoin", ORDERED_LEFT),
                _node("out", "output", _output(["quote_id", "rate"], "priced")),
            ],
            "edges": [
                _quotes_edge("priced", targetHandle="base"),
                _edge("rates", "priced", targetHandle="join"),
                _edge("priced", "out"),
            ],
        }
    )

    with pytest.raises(DeployError, match="'rates' holds the join key \\{'region': 'N'\\}"):
        request_rows(graph, ["quotes"], "out", bundled)

    pl.DataFrame({"region": ["E", "W"], "rate": [1.3, 1.4]}).write_parquet(parts[1])
    rows = request_rows(graph, ["quotes"], "out", bundled)
    assert rows.multi_quote == "in_request_order"
    assert set(record_digests({name: Path(path) for name, path in bundled.items()})) == set(bundled)


def test_null_keys_never_match_a_quote_and_are_not_counted(tmp_path, remap):
    pl.DataFrame({"region": ["N", None, None], "rate": [1.1, 2.0, 3.0]}).write_parquet(
        tmp_path / "rates.parquet"
    )

    assert request_rows(
        _priced_graph(ORDERED_LEFT, remap), ["quotes"], "out", remap
    ).multi_quote == ("in_request_order")


def test_a_drop_and_a_repeat_that_keep_the_count_are_refused(tmp_path, remap):
    """Requests ``A, B`` inner-joined to a lookup holding ``A`` twice give ``A, A``."""
    pl.DataFrame({"region": ["A", "A"], "rate": [1.0, 2.0]}).write_parquet(
        tmp_path / "rates.parquet"
    )
    graph = _priced_graph({**ORDERED_LEFT, "how": "inner"}, remap)

    assert _refusals(graph, remap) == [
        RowRefusal("priced", "priced", "join (how='inner', maintain_order='left_right')", "drops")
    ]
    with pytest.raises(DeployError, match="'rates' holds the join key \\{'region': 'A'\\}"):
        request_rows(graph, ["quotes"], "out", remap)


def test_a_join_declared_many_to_many_is_not_checked_and_answers_one_quote(tmp_path, remap):
    pl.DataFrame({"region": ["N", "N"], "rate": [1.0, 2.0]}).write_parquet(
        tmp_path / "rates.parquet"
    )

    rows = request_rows(
        _priced_graph({**ORDERED_LEFT, "validate": "m:m"}, remap), ["quotes"], "out", remap
    )

    assert rows.multi_quote == "quote_by_quote"
    assert rows.refusals[0].effect == "repeats"
    assert rows.checked_lookups == ()


def test_a_lookup_replaced_after_resolution_fails_the_bundle_copies(tmp_path, remap):
    from haute.deploy._mlflow import _stage_artifacts

    artifacts = {name: Path(path) for name, path in remap.items()}
    resolved = make_resolved_deploy(
        artifacts=artifacts, artifact_digests=record_digests(artifacts), target="container"
    )
    pl.DataFrame({"region": ["N", "N"], "rate": [1.0, 2.0]}).write_parquet(
        remap["rates__rates.parquet"]
    )

    with pytest.raises(DeployError, match="'rates__rates.parquet' changed after the deployment"):
        write_bundle(resolved, tmp_path / "build")
    assert not (tmp_path / "build" / "artifacts" / "rates__rates.parquet").exists()
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(DeployError, match="'rates__rates.parquet' changed after the deployment"):
        _stage_artifacts(resolved, staging)


def test_unchanged_artefacts_are_copied_under_their_digests(tmp_path, remap):
    artifacts = {name: Path(path) for name, path in remap.items()}
    resolved = make_resolved_deploy(artifacts=artifacts, target="container")

    write_bundle(resolved, tmp_path / "build")

    for name, path in artifacts.items():
        assert (tmp_path / "build" / "artifacts" / name).read_bytes() == path.read_bytes()


# ---------------------------------------------------------------------------
# Refused, naming node and operation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("join", "after", "operation", "effect"),
    [
        (ORDERED_LEFT, "df = priced.sort('rate')", "sort", "reorders"),
        (ORDERED_LEFT, "df = priced.filter(pl.col('rate') > 1.15)", "filter", "drops"),
        (
            ORDERED_LEFT,
            "df = priced.unpivot(on=['rate'], index='quote_id')",
            "unpivot",
            "repeats",
        ),
        (
            ORDERED_LEFT,
            "df = priced.with_columns(share=pl.col('rate') / pl.col('rate').sum())",
            "with_columns: sum",
            "mixes",
        ),
        (
            {**ORDERED_LEFT, "maintainOrder": "none"},
            None,
            "join (how='left', maintain_order='none')",
            "reorders",
        ),
        (
            {**ORDERED_LEFT, "how": "inner"},
            None,
            "join (how='inner', maintain_order='left_right')",
            "drops",
        ),
        (
            {**ORDERED_LEFT, "validate": "1:m"},
            None,
            "join (how='left', maintain_order='left_right'), validate='1:m'",
            "repeats",
        ),
        (
            {**ORDERED_LEFT, "validate": "m:m"},
            None,
            "join (how='left', maintain_order='left_right'), validate='m:m'",
            "repeats",
        ),
    ],
)
def test_the_proof_refuses_naming_node_and_operation(join, after, operation, effect, remap):
    node = "calc" if after is not None else "priced"

    assert _refusals(_priced_graph(join, remap, after=after), remap) == [
        RowRefusal(node, node, operation, effect)
    ]


def _branches(join: dict[str, Any], *, right_code: str) -> PipelineGraph:
    """The request split into two branches and joined back on ``quote_id``."""
    nodes = [
        _node("quotes", "apiInput", {"path": ""}),
        _node(
            "left", "polars", {"code": "df = quotes.with_columns((pl.col('age') * 2).alias('a2'))"}
        ),
        _node("right", "polars", {"code": right_code}),
        _node("joined", "edgeJoin", join),
        _node("out", "output", _output(["quote_id", "a2", "a3"], "joined")),
    ]
    edges = [
        _quotes_edge("left"),
        _quotes_edge("right"),
        _edge("left", "joined", targetHandle="base"),
        _edge("right", "joined", targetHandle="join"),
        _edge("joined", "out"),
    ]
    return PipelineGraph.model_validate({"nodes": nodes, "edges": edges})


REJOIN = {"how": "left", "on": "quote_id", "suffix": "_r", "maintainOrder": "left_right"}
RIGHT_ROW_LOCAL = "df = quotes.select('quote_id', (pl.col('age') * 3).alias('a3'))"


def test_a_join_of_two_parts_of_the_request_is_refused_whatever_it_declares():
    """Unique keys on both sides do not tie a row to its own quote."""
    refused = [
        RowRefusal("joined", "joined", "join (how='left', maintain_order='left_right')", "mixes")
    ]
    for join in (REJOIN, {**REJOIN, "validate": "m:1"}, {**REJOIN, "validate": "1:1"}):
        assert _refusals(_branches(join, right_code=RIGHT_ROW_LOCAL)) == refused
    grouped = "df = quotes.group_by('quote_id').agg((pl.col('age') * 3).max().alias('a3'))"
    assert [r.effect for r in _refusals(_branches(REJOIN, right_code=grouped))] == [
        "mixes",
        "mixes",
    ]

    # Why: a branch that shifts the quote's key passes Polars' ``m:1`` check and, scored
    # unchecked, answers quote 2 with quote 1's row.
    shifted = (
        "df = quotes.select((pl.col('quote_id') + 1).alias('quote_id'), "
        "(pl.col('age') * 3).alias('a3'))"
    )
    answer = score_graph(
        graph=_branches({**REJOIN, "validate": "m:1"}, right_code=shifted),
        input_df=pl.DataFrame({"quote_id": [1, 2], "region": ["N", "S"], "age": [30, 40]}),
        input_node_ids=["quotes"],
        output_node_id="out",
    )
    assert answer["a3"].to_list() == [None, 90]


def test_every_refusal_is_collected(remap):
    graph = _priced_graph(
        {**ORDERED_LEFT, "maintainOrder": "none"},
        remap,
        after="df = priced.filter(pl.col('rate') > 1).sort('rate')",
    )

    assert [(r.node_id, r.operation, r.effect) for r in _refusals(graph, remap)] == [
        ("priced", "join (how='left', maintain_order='none')", "reorders"),
    ]
    # The frame after an unordered join is lost; a later sort adds nothing new to say. A
    # filter and a sort on the quotes themselves are each named.
    graph = _priced_graph(ORDERED_LEFT, remap, after="df = priced.filter(pl.col('rate') > 1)")
    sorted_graph = PipelineGraph.model_validate(
        {
            "nodes": [
                *[n.model_dump() for n in graph.nodes if n.id != "out"],
                _node("order", "polars", {"code": "df = quotes.sort('age')"}),
                _node("both", "edgeJoin", REJOIN),
                _node("out", "output", _output(["quote_id"], "both")),
            ],
            "edges": [
                *[e.model_dump() for e in graph.edges if e.target != "out"],
                _quotes_edge("order"),
                _edge("calc", "both", targetHandle="base"),
                _edge("order", "both", targetHandle="join"),
                _edge("both", "out"),
            ],
        }
    )
    assert {(r.node_id, r.operation) for r in _refusals(sorted_graph, remap)} == {
        ("calc", "filter"),
        ("order", "sort"),
    }


def _single(node: dict[str, Any], *, handles: dict[str, str] | None = None) -> PipelineGraph:
    """Quotes -> *node* -> response."""
    return PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                node,
                _node("out", "output", _output(["quote_id"], node["id"])),
            ],
            "edges": [_quotes_edge(node["id"], **(handles or {})), _edge(node["id"], "out")],
        }
    )


def test_node_types_that_cannot_answer_in_order_are_refused(tmp_path):
    online = tmp_path / "online.json"
    online.write_text(json.dumps({"mode": "online"}), encoding="utf-8")
    cases = {
        "expand": (_node("expand", "scenarioExpander"), "Expander", "repeats"),
        "apply": (
            _node(
                "apply", "optimiserApply", {"sourceType": "file", "artifact_path": "online.json"}
            ),
            "Apply Optimisation (online)",
            "mixes",
        ),
        "served": (
            _node("served", "optimiserApply", {"sourceType": "run", "run_id": "abc"}),
            "Apply Optimisation from MLflow",
            "unproven",
        ),
        "nested": (_node("nested", "submodel", {}), "submodel", "unproven"),
    }
    for node_id, (node, operation, effect) in cases.items():
        graph = _single(node)
        verdict, refusals, _checks = prove_request_rows(
            graph, ["quotes"], "out", {"apply__online.json": str(online)}
        )
        assert verdict == "quote_by_quote", node_id
        assert list(refusals) == [RowRefusal(node_id, node_id, operation, effect)]


def test_a_quote_input_s_root_table_is_the_request_and_its_others_are_not():
    column = {"name": "x", "path": "$[:].x", "type": "int", "status": "Confirmed", "selected": True}
    drivers = {
        "path": "$[:].drivers[:]",
        "label": "drivers",
        "emit": True,
        "columns": [{**column, "path": "$[:].drivers[:].age", "name": "age"}],
    }
    tables = [
        {"path": "$[:]", "label": "quote", "emit": True, "columns": [column]},
        drivers,
        # A table that emits nothing gives no port.
        {**drivers, "label": "unread", "emit": False},
    ]
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": "quote.json", "tables": tables}),
                _node("out", "output", _output(["x"], "quote")),
            ],
            "edges": [_edge("quotes", "out", sourceHandle="quote")],
        }
    )

    assert _refusals(graph) == []
    drivers_first = graph.model_dump()
    drivers_first["edges"] = [_edge("quotes", "out", sourceHandle="drivers")]
    drivers_first["nodes"][1]["data"]["config"] = _output(["age"], "drivers")
    assert _refusals(PipelineGraph.model_validate(drivers_first)) == [
        RowRefusal("out", "out", "response", "replaces")
    ]


def test_a_constant_cross_join_keeps_each_quote():
    cross = {"how": "cross", "suffix": "_c", "maintainOrder": "left_right"}
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _node("params", "constant", {"values": [{"name": "loading", "value": "1.15"}]}),
                _node("joined", "edgeJoin", cross),
                _node("out", "output", _output(["quote_id", "loading"], "joined")),
            ],
            "edges": [
                _quotes_edge("joined", targetHandle="base"),
                _edge("params", "joined", targetHandle="join"),
                _edge("joined", "out"),
            ],
        }
    )
    rows = request_rows(graph, ["quotes"], "out", {})

    assert rows == RequestRows("in_request_order")
    answer = _score(graph, {}, rows, QUOTES)
    assert answer["quote_id"].to_list() == QUOTES["quote_id"].to_list()


def test_a_cross_join_with_several_rows_answers_one_quote_per_request(tmp_path):
    tiers = tmp_path / "tiers.parquet"
    pl.DataFrame({"tier": ["bronze", "silver", "gold"]}).write_parquet(tiers)
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("tiers", str(tiers)),
                _node(
                    "joined",
                    "edgeJoin",
                    {"how": "cross", "suffix": "_t", "maintainOrder": "left_right"},
                ),
                _node("out", "output", _output(["quote_id", "tier"], "joined")),
            ],
            "edges": [
                _quotes_edge("joined", targetHandle="base"),
                _edge("tiers", "joined", targetHandle="join"),
                _edge("joined", "out"),
            ],
        }
    )

    rows = request_rows(graph, ["quotes"], "out", {"tiers__tiers.parquet": str(tiers)})

    assert rows.refusals == (
        RowRefusal("joined", "joined", "cross join with 'tiers', which holds 3 rows", "repeats"),
    )


def test_a_workbench_input_reads_one_quote_per_request():
    sorted_quotes = _single(_node("calc", "polars", {"code": "df = quotes.sort('age')"}))
    dumped = sorted_quotes.model_dump()
    for node in dumped["nodes"]:
        if node["id"] == "quotes":
            node["data"]["nodeType"] = "workbenchInput"
    workbench = PipelineGraph.model_validate(dumped)

    assert prove_request_rows(workbench, ["quotes"], "out", {}) == ("one_quote_per_request", (), ())


# ---------------------------------------------------------------------------
# Served: as one frame or quote by quote, counted, recorded
# ---------------------------------------------------------------------------

SORTED = RequestRows("quote_by_quote", (RowRefusal("calc", "calc", "sort", "reorders"),))


def _alone(graph: PipelineGraph, remap: dict[str, str], quotes: pl.DataFrame) -> pl.DataFrame:
    """Each quote answered in a request of its own, the answers in request order."""
    return pl.concat(
        [_score(graph, remap, SORTED, quotes.slice(index, 1)) for index in range(quotes.height)]
    )


def test_a_deployment_the_proof_does_not_cover_answers_a_batch_quote_by_quote(tmp_path, remap):
    graph = _priced_graph(ORDERED_LEFT, remap, after="df = priced.sort('rate')")
    alone = _alone(graph, remap, QUOTES)

    assert _score(graph, remap, SORTED, QUOTES).equals(alone)
    # In one frame the sort would answer in rate order, not request order.
    together = _score(graph, remap, RequestRows("in_request_order"), QUOTES)
    assert together["quote_id"].to_list() != QUOTES["quote_id"].to_list()
    # The batch child answers it the same way.
    plan = prepare_batch_scoring(
        QUOTES.to_dicts(),
        graph=graph,
        input_node_ids=["quotes"],
        output_node_id="out",
        artifact_paths=remap,
        output_fields=None,
        input_schema=QUOTES.schema,
        request_rows=SORTED,
    )
    try:
        outcome = score_batch_scoring_request(
            plan.request, execution_context=plan.execution_context
        )
        assert outcome.row_count == QUOTES.height
        assert pl.read_parquet(plan.request.result_path).equals(alone)
    finally:
        plan.cleanup(primary_error=None)


def test_a_quote_answered_with_no_row_fails_a_batch_answered_quote_by_quote(remap):
    graph = _priced_graph(ORDERED_LEFT, remap, after="df = priced.filter(pl.col('age') > 30)")

    with pytest.raises(QuoteAnswerRowsError) as raised:
        _score(graph, remap, SORTED, QUOTES)

    assert raised.value.to_payload() == {
        "error_code": "quote_answer_rows",
        "message": "Quote 2 of this request of 5 quotes was answered with 0 rows. A request "
        "of several quotes is answered with one row per quote, in order, so send this quote "
        "in a request of its own.",
        "quotes": 5,
        "position": 2,
        "rows": 0,
    }
    # Sent alone, it is answered with every row it gives: none.
    assert _score(graph, remap, SORTED, QUOTES.slice(1, 1)).height == 0


def test_a_batch_answered_quote_by_quote_holds_its_caller_s_admission_to_the_end(remap):
    graph = _priced_graph(ORDERED_LEFT, remap, after="df = priced.sort('rate')")

    def answer(context: Any, *, retain: bool) -> pl.DataFrame:
        return score_graph(
            graph=graph,
            input_df=QUOTES,
            input_node_ids=["quotes"],
            output_node_id="out",
            artifact_paths=remap,
            execution_context=context,
            retain_admission_on_success=retain,
            request_rows=SORTED,
        )

    retained = admit_deploy_execution(operation="deploy_quote", row_count=QUOTES.height)
    try:
        assert answer(retained, retain=True).height == QUOTES.height
        # Held across every quote and past the last, for the caller's own conversion.
        assert not retained.lease.released
    finally:
        retained.release_admission()
    released = admit_deploy_execution(operation="deploy_quote", row_count=QUOTES.height)
    assert answer(released, retain=False).height == QUOTES.height
    assert released.lease.released


def test_a_batch_answered_quote_by_quote_is_never_one_lazy_plan(remap):
    graph = _priced_graph(ORDERED_LEFT, remap, after="df = priced.sort('rate')")

    with pytest.raises(ValueError, match="one quote at a time"):
        score_graph_lazy(
            graph=graph,
            input_df=QUOTES,
            input_node_ids=["quotes"],
            output_node_id="out",
            artifact_paths=remap,
            request_rows=SORTED,
        )


def test_two_quotes_whose_answers_coincide_are_answered_with_a_row_each(tmp_path, remap):
    """The response merges top-level objects whose fields are all equal; a batch does not."""
    graph = _two_join_graph(remap)
    rows = request_rows(graph, ["quotes"], "out", remap)
    twice = pl.concat([QUOTES.head(1), QUOTES.head(1)])

    answer = _score(graph, remap, rows, twice)

    assert answer.to_dicts() == [{"quote_id": 7, "premium": pytest.approx(1.2)}] * 2
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(twice.to_dicts()), encoding="utf-8")
    request = BatchScoreRequest(
        graph=graph,
        input_node_ids=["quotes"],
        output_node_id="out",
        artifact_paths=remap,
        output_fields=None,
        input_path=str(input_path),
        result_path=str(tmp_path / "result.parquet"),
        input_schema=dict(QUOTES.schema),
        request_rows=rows,
    )
    context = admit_deploy_execution(operation="deploy_quote", row_count=2)
    try:
        assert score_batch_scoring_request(request, execution_context=context).row_count == 2
    finally:
        context.release_admission(preserve_primary_error=False)


def _load_app(tmp_path: Path, rows: RequestRows, graph: PipelineGraph) -> Any:
    manifest = {
        "polars_version": pl.__version__,
        "pruned_graph": graph.model_dump(mode="json"),
        "input_node_ids": ["quotes"],
        "output_node_id": "out",
        "input_schema": QUOTE_SCHEMA,
        "request_rows": rows.to_manifest(),
        "artifacts": {},
        "output_fields": None,
    }
    (tmp_path / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    app_path = tmp_path / "app.py"
    app_path.write_text(_generate_app_source("motor", 8080), encoding="utf-8")
    module_name = f"_haute_rows_app_{tmp_path.name}"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)


def test_the_container_shows_the_record_and_names_a_quote_a_batch_cannot_answer(
    tmp_path, monkeypatch
):
    # No lookup, so the container needs no artefact: quotes over 30 answered, others dropped.
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _node("calc", "polars", {"code": "df = quotes.filter(pl.col('age') > 30)"}),
                _node("out", "output", _output(["quote_id", "age"], "calc")),
            ],
            "edges": [_quotes_edge("calc"), _edge("calc", "out")],
        }
    )
    module = _load_app(tmp_path, SORTED, graph)

    async def in_process(function, *args, config):
        del config
        return function(*args)

    # The batch worker runs in this process, as its spawn would run it.
    monkeypatch.setattr(module, "run_isolated_worker_async", in_process)
    client = TestClient(module.app)

    response = client.post("/quote", json=[{"quote_id": 1, "age": 30}, {"quote_id": 2, "age": 31}])
    answered = client.post("/quote", json=[{"quote_id": 1, "age": 40}, {"quote_id": 2, "age": 31}])

    assert client.get("/health").json()["request_rows"] == SORTED.to_manifest()
    assert answered.status_code == 200, answered.text
    assert answered.json()["rows"] == [{"quote_id": 1, "age": 40}, {"quote_id": 2, "age": 31}]
    # The first quote is not over 30, so it gives no row and fails the batch.
    assert response.status_code == 422, response.text
    assert response.json()["error_code"] == "quote_answer_rows"
    assert (response.json()["position"], response.json()["rows"]) == (1, 0)


def test_a_covered_deployment_fails_an_answer_whose_row_count_changes(tmp_path, remap):
    # A record claiming order for a pipeline that filters: the backstop names both counts.
    graph = _priced_graph(ORDERED_LEFT, remap, after="df = priced.filter(pl.col('rate') > 1.15)")
    claimed = RequestRows("in_request_order")

    with pytest.raises(RequestRowCountError, match="a request of 5 quotes with 3 rows"):
        _score(graph, remap, claimed, QUOTES)

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(QUOTES.to_dicts()), encoding="utf-8")
    request = BatchScoreRequest(
        graph=graph,
        input_node_ids=["quotes"],
        output_node_id="out",
        artifact_paths=remap,
        output_fields=None,
        input_path=str(input_path),
        result_path=str(tmp_path / "result.parquet"),
        input_schema=dict(QUOTES.schema),
        request_rows=claimed,
    )
    context = admit_deploy_execution(operation="deploy_quote", row_count=QUOTES.height)
    try:
        with pytest.raises(RequestRowCountError, match="5 quotes with 3 rows"):
            score_batch_scoring_request(request, execution_context=context)
    finally:
        context.release_admission(preserve_primary_error=True)


def test_a_deployment_answering_quote_by_quote_scores_its_test_quotes_one_request_each(
    tmp_path, remap
):
    from haute.deploy._validators import score_test_quotes

    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    cases = [
        {"input": quote, "expected": {"quote_id": quote["quote_id"]}} for quote in QUOTES.to_dicts()
    ]
    (quotes_dir / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    graph = _priced_graph(ORDERED_LEFT, remap, after="df = priced.sort('rate')")
    resolved = make_resolved_deploy(
        pruned_graph=graph,
        input_node_ids=["quotes"],
        output_node_id="out",
        artifacts={name: Path(path) for name, path in remap.items()},
        request_rows=SORTED,
    )

    [result] = score_test_quotes(resolved, quotes_dir)

    assert result["status"] == "ok", result["error"]
    assert result["rows"] == QUOTES.height


def test_the_manifest_records_the_proof_and_reads_it_back_strictly(remap):
    rows = request_rows(_two_join_graph(remap), ["quotes"], "out", remap)
    manifest = build_manifest(make_resolved_deploy(request_rows=rows))

    assert manifest["request_rows"] == rows.to_manifest()
    assert RequestRows.from_manifest(manifest["request_rows"]) == rows
    with pytest.raises(KeyError):
        RequestRows.from_manifest({"multi_quote": "in_request_order", "refusals": []})
    with pytest.raises(ValueError, match="exactly when it names why"):
        RequestRows("quote_by_quote")
    with pytest.raises(ValueError, match="Unknown multi_quote"):
        RequestRows("several")  # type: ignore[arg-type]


def test_the_record_describes_itself_as_haute_deploy_prints_it():
    assert RequestRows("in_request_order").describe() == (
        "Answers several quotes per request as one frame, one row per quote in request order"
    )
    assert RequestRows("one_quote_per_request").describe() == (
        "Answers one quote per request (a Workbench Input reads one quote)"
    )
    assert SORTED.describe() == (
        "Answers several quotes per request one quote at a time: 'calc' (sort) can change "
        "the quotes' order"
    )


# ---------------------------------------------------------------------------
# Every node rule
# ---------------------------------------------------------------------------


def _verdict(graph: PipelineGraph, remap: dict[str, str] | None = None, inputs=("quotes",)):
    verdict, refusals, _checks = prove_request_rows(graph, list(inputs), "out", remap or {})
    return verdict, list(refusals)


@pytest.mark.parametrize(
    ("node_type", "config"),
    [
        ("dataOutput", {}),
        ("modelling", {}),
        ("liveSwitch", {"input_scenario_map": {"quotes": "live"}}),
        ("optimiser", {}),
        ("banding", {"factors": []}),
        ("ratingStep", {"tables": []}),
        ("ratingStep", {"code": "df = df.with_columns((pl.col('age') + 1).alias('next'))"}),
        ("explore", {"code": "df = df.with_columns(pl.lit(1).alias('one'))"}),
        ("modelScore", {}),
        ("modelScore", {"code": "df = df.with_columns((pl.col('age') * 2).alias('a2'))"}),
        ("externalFile", {}),
    ],
)
def test_nodes_that_keep_each_quote_s_row(node_type, config):
    assert _verdict(_single(_node("kept", node_type, config))) == ("in_request_order", [])


@pytest.mark.parametrize(
    ("node_type", "config", "operation", "effect"),
    [
        ("ratingStep", {"code": "df = df.sort('age')"}, "sort", "reorders"),
        ("explore", {"code": "df = df.unique()"}, "unique", "drops"),
        (
            "modelScore",
            {"code": "df = df.with_columns(pl.lit(model.version).alias('v'))"},
            "with_columns: Attribute",
            "unproven",
        ),
        (
            "externalFile",
            {"code": "df = df.with_columns(score=pl.lit(obj.predict(1)))"},
            "code: with_columns",
            "unproven",
        ),
        ("polars", {}, "no code", "unproven"),
        ("polars", {"code": "df = quotes.pipe(len)"}, "code: pipe", "unproven"),
        (
            "polars",
            {"code": "df = quotes.fill_null(strategy='forward')"},
            "fill_null: strategy='forward'",
            "mixes",
        ),
        (
            "polars",
            {"code": "df = quotes.with_columns(pl.lit(helper(1)).alias('h'))"},
            "code: with_columns",
            "unproven",
        ),
        ("workbenchOutput", {}, "Workbench Output", "unproven"),
    ],
)
def test_nodes_that_cannot_keep_each_quote_s_row(node_type, config, operation, effect):
    assert _verdict(_single(_node("node", node_type, config))) == (
        "quote_by_quote",
        [RowRefusal("node", "node", operation, effect)],
    )


def test_a_fill_with_a_value_or_a_row_local_strategy_keeps_each_quote():
    for code in ("df = quotes.fill_null(0)", "df = quotes.fill_null(strategy='zero')"):
        assert _verdict(_single(_node("filled", "polars", {"code": code}))) == (
            "in_request_order",
            [],
        )


def test_a_live_switch_still_holding_two_inputs_is_not_proved():
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _node("other", "polars", {"code": "df = quotes"}),
                _node("switch", "liveSwitch", {"input_scenario_map": {"quotes": "live"}}),
                _node("out", "output", _output(["quote_id"], "switch")),
            ],
            "edges": [
                _quotes_edge("other"),
                _quotes_edge("switch"),
                _edge("other", "switch"),
                _edge("switch", "out"),
            ],
        }
    )

    assert _verdict(graph) == (
        "quote_by_quote",
        [RowRefusal("switch", "switch", "Live Switch", "unproven")],
    )


def test_a_data_input_with_an_input_of_its_own_is_not_proved():
    graph = _single(_node("odd", "dataInput", {}))

    assert _verdict(graph) == (
        "quote_by_quote",
        [RowRefusal("odd", "odd", "dataInput", "unproven")],
    )


def test_a_promoted_data_input_proves_its_post_load_code(remap):
    def graph(code: str) -> PipelineGraph:
        return PipelineGraph.model_validate(
            {
                "nodes": [
                    _lookup("rates", remap["rates__rates.parquet"], code),
                    _node("out", "output", _output(["rate"], "rates")),
                ],
                "edges": [_edge("rates", "out")],
            }
        )

    for code in ("", "df = df.with_columns((pl.col('rate') * 2).alias('r2'))"):
        assert _verdict(graph(code), inputs=("rates",)) == ("in_request_order", [])
    assert _verdict(graph("df = df.filter(pl.col('rate') > 1)"), inputs=("rates",)) == (
        "quote_by_quote",
        [RowRefusal("rates", "rates", "filter", "drops")],
    )


def test_an_optimiser_apply_in_ratebook_mode_keeps_the_input_it_rates(tmp_path):
    ratebook = tmp_path / "ratebook.json"
    ratebook.write_text(json.dumps({"mode": "ratebook"}), encoding="utf-8")
    bundled = {"apply__ratebook.json": str(ratebook)}

    def graph(ratebook_input: str) -> PipelineGraph:
        config = {
            "sourceType": "file",
            "artifact_path": "ratebook.json",
            "ratebook_input": ratebook_input,
        }
        return _single(_node("apply", "optimiserApply", config))

    assert _verdict(graph("quotes"), bundled) == ("in_request_order", [])
    assert _verdict(graph("elsewhere"), bundled) == (
        "quote_by_quote",
        [RowRefusal("apply", "apply", "Apply Optimisation input", "unproven")],
    )
    # Not bundled: its mode cannot be read.
    assert _verdict(graph("quotes")) == (
        "quote_by_quote",
        [RowRefusal("apply", "apply", "Apply Optimisation", "unproven")],
    )


@pytest.mark.parametrize(
    ("join", "operation"),
    [
        (ORDERED_LEFT, "join (how='left', maintain_order='left_right')"),
        (
            {"how": "cross", "suffix": "_s", "maintainOrder": "left"},
            "join (how='cross', maintain_order='left')",
        ),
    ],
)
def test_an_optimiser_read_from_mlflow_cannot_be_a_checked_lookup(join, operation, remap):
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("rates", remap["rates__rates.parquet"]),
                _node("served", "optimiserApply", {"sourceType": "run", "run_id": "abc"}),
                _node("priced", "edgeJoin", join),
                _node("out", "output", _output(["quote_id"], "priced")),
            ],
            "edges": [
                _edge("rates", "served"),
                _quotes_edge("priced", targetHandle="base"),
                _edge("served", "priced", targetHandle="join"),
                _edge("priced", "out"),
            ],
        }
    )

    assert _verdict(graph, remap) == (
        "quote_by_quote",
        [RowRefusal("priced", "priced", operation, "unproven")],
    )


# -- the response ------------------------------------------------------------


def _response_graph(mapping: list[dict[str, Any]], remap: dict[str, str]) -> PipelineGraph:
    return PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("rates", remap["rates__rates.parquet"]),
                _node("out", "output", {"outputMapping": mapping, "outputFormat": "json"}),
            ],
            "edges": [_quotes_edge("out"), _edge("rates", "out")],
        }
    )


def _entry(port: str, column: str, path: str) -> dict[str, Any]:
    return {"source_port": port, "source_column": column, "output_path": path, "enabled": True}


def test_a_response_takes_its_top_level_from_the_quotes(remap):
    nested = [
        _entry("quotes", "quote_id", "$[:].quote_id"),
        _entry("rates", "rate", "$[:].rates[:].rate"),
    ]
    assert _verdict(_response_graph(nested, remap), remap) == ("in_request_order", [])

    only_nested = [_entry("rates", "rate", "$[:].rates[:].rate")]
    assert _verdict(_response_graph(only_nested, remap), remap) == (
        "quote_by_quote",
        [RowRefusal("out", "out", "response top level", "replaces")],
    )
    from_lookup = [
        _entry("rates", "rate", "$[:].rate"),
        _entry("quotes", "quote_id", "$[:].quotes[:].quote_id"),
    ]
    assert _verdict(_response_graph(from_lookup, remap), remap) == (
        "quote_by_quote",
        [RowRefusal("out", "out", "response top level from rates", "replaces")],
    )
    unreadable = [_entry("quotes", "quote_id", "quote_id")]
    assert _verdict(_response_graph(unreadable, remap), remap) == (
        "quote_by_quote",
        [RowRefusal("out", "out", "response mapping", "unproven")],
    )


# -- joins -------------------------------------------------------------------


def test_an_edge_join_whose_config_does_not_build_is_not_proved(remap):
    graph = _priced_graph({"how": "left", "maintainOrder": "left_right"}, remap)

    assert _verdict(graph, remap) == (
        "quote_by_quote",
        [RowRefusal("priced", "priced", "Edge Join", "unproven")],
    )


def test_a_right_join_ordered_by_the_quotes_on_its_right_answers_in_order(remap):
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("rates", remap["rates__rates.parquet"]),
                _node(
                    "priced",
                    "edgeJoin",
                    {"how": "right", "on": "region", "suffix": "_r", "maintainOrder": "right_left"},
                ),
                _node("out", "output", _output(["quote_id", "rate"], "priced")),
            ],
            "edges": [
                _edge("rates", "priced", targetHandle="base"),
                _quotes_edge("priced", targetHandle="join"),
                _edge("priced", "out"),
            ],
        }
    )
    rows = request_rows(graph, ["quotes"], "out", remap)

    assert rows.multi_quote == "in_request_order"
    assert [(check.side_node_id, check.keys) for check in rows.checked_lookups] == [
        ("rates", ("region",))
    ]
    answer = _score(graph, remap, rows, QUOTES)
    assert answer["quote_id"].to_list() == QUOTES["quote_id"].to_list()


def test_a_join_driven_by_another_input_answers_with_its_rows(remap):
    code = (
        "df = rates.join(quotes, on='region', how='left', maintain_order='left_right')\n"
        "df = df.join(quotes, on='region', how='left', maintain_order='left_right', suffix='_2')"
    )
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("rates", remap["rates__rates.parquet"]),
                _node("calc", "polars", {"code": code}),
                _node("out", "output", _output(["quote_id"], "calc")),
            ],
            "edges": [_edge("rates", "calc"), _quotes_edge("calc"), _edge("calc", "out")],
        }
    )

    # Named once, though both joins answer with the lookup's rows.
    assert _verdict(graph, remap) == (
        "quote_by_quote",
        [RowRefusal("calc", "calc", "join (how='left', maintain_order='left_right')", "replaces")],
    )


def test_a_lookup_joined_twice_is_checked_once(remap):
    code = (
        "df = quotes.join(rates, on='region', how='left', maintain_order='left_right')\n"
        "df = df.join(rates, on='region', how='left', maintain_order='left_right', suffix='_2')"
    )
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("rates", remap["rates__rates.parquet"]),
                _node("calc", "polars", {"code": code}),
                _node("out", "output", _output(["quote_id"], "calc")),
            ],
            "edges": [_quotes_edge("calc"), _edge("rates", "calc"), _edge("calc", "out")],
        }
    )

    verdict, refusals, checks = prove_request_rows(graph, ["quotes"], "out", remap)
    assert (verdict, refusals) == ("in_request_order", ())
    assert [(check.join_node_id, check.side_node_id) for check in checks] == [("calc", "rates")]


def test_a_left_join_against_one_row_needs_no_check():
    left_on_constant = {"how": "left", "on": "loading", "suffix": "_c", "maintainOrder": "left"}
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _node("params", "constant", {"values": [{"name": "loading", "value": "1"}]}),
                _node("more", "constant", {"values": [{"name": "tax", "value": "0.1"}]}),
                _node(
                    "both", "edgeJoin", {"how": "cross", "suffix": "_b", "maintainOrder": "left"}
                ),
                _node("joined", "edgeJoin", left_on_constant),
                _node("out", "output", _output(["quote_id"], "joined")),
            ],
            "edges": [
                _edge("params", "both", targetHandle="base"),
                _edge("more", "both", targetHandle="join"),
                _quotes_edge("joined", targetHandle="base"),
                _edge("both", "joined", targetHandle="join"),
                _edge("joined", "out"),
            ],
        }
    )

    assert prove_request_rows(graph, ["quotes"], "out", {}) == ("in_request_order", (), ())


def test_input_names_the_executor_would_refuse_are_not_proved():
    graph = _single(
        _node("calc", "polars", {"code": "df = frame", "inputMapping": {"frame": "missing"}})
    )

    assert _verdict(graph) == (
        "quote_by_quote",
        [RowRefusal("calc", "calc", "input names", "unproven")],
    )


def test_a_carried_branch_the_parser_cannot_read_is_still_checked(remap):
    nodes = [
        _node("quotes", "apiInput", {"path": ""}),
        _lookup("rates", remap["rates__rates.parquet"]),
        _node("shaped", "polars", {"code": "df = rates.pipe(len)"}),
        _node("priced", "edgeJoin", ORDERED_LEFT),
        _node("out", "output", _output(["quote_id"], "priced")),
    ]
    edges = [
        _edge("rates", "shaped"),
        _quotes_edge("priced", targetHandle="base"),
        _edge("shaped", "priced", targetHandle="join"),
        _edge("priced", "out"),
    ]
    graph = PipelineGraph.model_validate({"nodes": nodes, "edges": edges})

    verdict, refusals, checks = prove_request_rows(graph, ["quotes"], "out", remap)
    assert (verdict, refusals) == ("in_request_order", ())
    assert [check.side_node_id for check in checks] == ["shaped"]


def test_a_graph_with_a_cycle_is_refused():
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _node("a", "polars", {"code": "df = quotes"}),
                _node("b", "polars", {"code": "df = a"}),
                _node("out", "output", _output(["quote_id"], "b")),
            ],
            "edges": [_quotes_edge("a"), _edge("b", "a"), _edge("a", "b"), _edge("b", "out")],
        }
    )

    with pytest.raises(DeployError, match="cycle"):
        prove_request_rows(graph, ["quotes"], "out", {})


# -- checks that leave the deployment answering one quote --------------------


def test_a_lookup_too_large_to_check_answers_one_quote_per_request(monkeypatch, remap):
    from haute.errors import GroupByExecutionUnsupportedError

    def unbounded(**_kwargs: Any) -> Any:
        raise GroupByExecutionUnsupportedError(
            "no estimate",
            node_id="rates",
            operator="group_by",
            profile="deploy_live",
            reason_code="materialisation_estimate_unavailable",
            remediation="",
            estimated_peak_bytes=None,
            headroom_bytes=None,
        )

    monkeypatch.setattr("haute.deploy._scorer.score_graph_lazy", unbounded)
    rows = request_rows(_priced_graph(ORDERED_LEFT, remap), ["quotes"], "out", remap)

    assert rows.refusals == (
        RowRefusal("priced", "priced", "join with 'rates', too large to check", "unproven"),
    )
    assert rows.checked_lookups == ()


def test_a_side_with_several_tables_is_checked_through_its_port(monkeypatch, remap):
    from haute.deploy._request_rows import LookupCheck, check_lookups

    class Plan:
        lazy_frame = {"rates": pl.LazyFrame({"region": ["N", "S"]})}

        def cleanup(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr("haute.deploy._scorer.score_graph_lazy", lambda **_kwargs: Plan())
    check = LookupCheck("priced", "priced", "source", "source", "rates", ("region",))

    assert check_lookups(PipelineGraph(), ["quotes"], [check], {}) == ((), (check,))


def test_an_empty_cross_join_side_drops_the_quotes(tmp_path):
    empty = tmp_path / "empty.parquet"
    pl.DataFrame({"tier": []}, schema={"tier": pl.String}).write_parquet(empty)
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("tiers", str(empty)),
                _node(
                    "joined", "edgeJoin", {"how": "cross", "suffix": "_t", "maintainOrder": "left"}
                ),
                _node("out", "output", _output(["quote_id"], "joined")),
            ],
            "edges": [
                _quotes_edge("joined", targetHandle="base"),
                _edge("tiers", "joined", targetHandle="join"),
                _edge("joined", "out"),
            ],
        }
    )

    rows = request_rows(graph, ["quotes"], "out", {"tiers__empty.parquet": str(empty)})

    assert rows.refusals == (
        RowRefusal("joined", "joined", "cross join with 'tiers', which holds 0 rows", "drops"),
    )


def test_a_one_row_cross_join_side_passes_its_check(tmp_path):
    single = tmp_path / "single.parquet"
    pl.DataFrame({"tax": [0.1]}).write_parquet(single)
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("tax", str(single)),
                _node(
                    "joined", "edgeJoin", {"how": "cross", "suffix": "_t", "maintainOrder": "left"}
                ),
                _node("out", "output", _output(["quote_id"], "joined")),
            ],
            "edges": [
                _quotes_edge("joined", targetHandle="base"),
                _edge("tax", "joined", targetHandle="join"),
                _edge("joined", "out"),
            ],
        }
    )

    rows = request_rows(graph, ["quotes"], "out", {"tax__single.parquet": str(single)})

    assert rows.multi_quote == "in_request_order"
    assert [(check.side_node_id, check.keys) for check in rows.checked_lookups] == [("tax", ())]


def test_a_lookup_that_cannot_be_read_fails_the_build_with_its_own_error(remap):
    # The join names a key the lookup does not hold.
    graph = _priced_graph(
        {**ORDERED_LEFT, "on": None, "leftOn": "region", "rightOn": "zone"}, remap
    )

    with pytest.raises(pl.exceptions.ColumnNotFoundError):
        request_rows(graph, ["quotes"], "out", remap)


def test_renames_drops_and_casts_keep_each_quote_s_row():
    code = (
        "df = quotes.rename({'age': 'years'}).drop('region')"
        ".cast({'years': pl.Float64}).with_columns(**{'next': pl.col('years') + 1})"
    )

    assert _verdict(_single(_node("calc", "polars", {"code": code}))) == (
        "quote_by_quote",
        [RowRefusal("calc", "calc", "with_columns: **", "unproven")],
    )
    assert _verdict(_single(_node("calc", "polars", {"code": code.split(".with_columns")[0]}))) == (
        "in_request_order",
        [],
    )


def test_an_optimiser_whose_input_has_no_name_scores_its_first_input():
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _node("opt", "optimiser", {"data_input": "quotes"}),
                _node("out", "output", _output(["quote_id"], "opt")),
            ],
            # A request input's edge without its frame handle has no input name.
            "edges": [_edge("quotes", "opt"), _edge("opt", "out")],
        }
    )

    assert _verdict(graph) == ("in_request_order", [])


def test_a_disabled_mapping_entry_is_not_read(remap):
    mapping = [
        _entry("quotes", "quote_id", "$[:].quote_id"),
        {**_entry("rates", "rate", "rate-not-a-path"), "enabled": False},
    ]

    assert _verdict(_response_graph(mapping, remap), remap) == ("in_request_order", [])


def test_a_cross_join_with_a_request_branch_is_refused():
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _node("branch", "polars", {"code": "df = quotes.select('age')"}),
                _node(
                    "joined", "edgeJoin", {"how": "cross", "suffix": "_b", "maintainOrder": "left"}
                ),
                _node("out", "output", _output(["quote_id"], "joined")),
            ],
            "edges": [
                _quotes_edge("joined", targetHandle="base"),
                _quotes_edge("branch"),
                _edge("branch", "joined", targetHandle="join"),
                _edge("joined", "out"),
            ],
        }
    )

    assert _verdict(graph) == (
        "quote_by_quote",
        [RowRefusal("joined", "joined", "join (how='cross', maintain_order='left')", "mixes")],
    )


def test_a_constant_s_one_row_is_lost_to_a_sort_and_then_checked():
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _node("params", "constant", {"values": [{"name": "loading", "value": "1"}]}),
                _node("sorted", "polars", {"code": "df = params.sort('loading')"}),
                _node(
                    "joined", "edgeJoin", {"how": "cross", "suffix": "_c", "maintainOrder": "left"}
                ),
                _node("out", "output", _output(["quote_id"], "joined")),
            ],
            "edges": [
                _edge("params", "sorted"),
                _quotes_edge("joined", targetHandle="base"),
                _edge("sorted", "joined", targetHandle="join"),
                _edge("joined", "out"),
            ],
        }
    )

    verdict, refusals, checks = prove_request_rows(graph, ["quotes"], "out", {})
    assert (verdict, refusals) == ("in_request_order", ())
    assert [(check.side_node_id, check.keys) for check in checks] == [("sorted", ())]


def test_a_carried_branch_through_any_node_is_still_a_lookup(remap):
    nodes = [
        _node("quotes", "apiInput", {"path": ""}),
        _lookup("rates", remap["rates__rates.parquet"]),
        _node("expanded", "scenarioExpander"),
        _node("priced", "edgeJoin", {**ORDERED_LEFT, "on": ["region"]}),
        _node("out", "output", _output(["quote_id"], "priced")),
    ]
    edges = [
        _edge("rates", "expanded"),
        _quotes_edge("priced", targetHandle="base"),
        _edge("expanded", "priced", targetHandle="join"),
        _edge("priced", "out"),
        # An edge from a node the pruned graph does not hold is not walked.
        _edge("ghost", "priced"),
    ]
    graph = PipelineGraph.model_validate({"nodes": nodes, "edges": edges})

    verdict, refusals, checks = prove_request_rows(graph, ["quotes"], "out", remap)
    assert (verdict, refusals) == ("in_request_order", ())
    assert [(check.side_node_id, check.keys) for check in checks] == [("expanded", ("region",))]


def test_a_response_that_is_not_the_request_s_rows_is_refused(remap):
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("quotes", "apiInput", {"path": ""}),
                _lookup("rates", remap["rates__rates.parquet"]),
                _node("out", "polars", {"code": "df = rates"}),
            ],
            "edges": [_quotes_edge("out"), _edge("rates", "out")],
        }
    )

    assert _verdict(graph, remap) == (
        "quote_by_quote",
        [RowRefusal("out", "out", "response", "replaces")],
    )


def test_operations_the_proof_cannot_pair_with_their_code_are_not_proved(monkeypatch):
    monkeypatch.setattr("haute.deploy._request_rows._frame_chain_calls", lambda _value: [])

    assert _verdict(_single(_node("calc", "polars", {"code": "df = quotes.sort('age')"}))) == (
        "quote_by_quote",
        [RowRefusal("calc", "calc", "code: code", "unproven")],
    )
