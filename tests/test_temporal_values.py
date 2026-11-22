"""A temporal value reaches Python only as its exact text (specs/io-layer, IOL-08).

Python's ``datetime``, ``time`` and ``timedelta`` hold microseconds; a ``Datetime("ns")``, a
``Duration("ns")`` and every ``Time`` hold nanoseconds. Each site that reads a frame's values
into Python is sent such values that differ below a microsecond here, and must keep them apart
and give back all nine digits.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._types import GraphNode, NodeData, PipelineGraph
from tests._project import project_app, use_project
from tests.conftest import make_ready_file_input_config

#: 2001-09-09 01:46:40 UTC, in nanoseconds.
_BASE = 10**18
#: Two rows 100 ns apart in each temporal kind, as their exact text.
_TEXTS = {
    "at": ["2001-09-09T01:46:40.000000100", "2001-09-09T01:46:40.000000200"],
    "clock": ["00:00:00.000000100", "00:00:00.000000200"],
    "span": ["0:00:00.000000100", "0:00:00.000000200"],
}


def _nanoseconds() -> pl.DataFrame:
    """Two rows that differ only below a microsecond, in a datetime, a time and a duration."""
    return pl.DataFrame(
        {
            "at": pl.Series([_BASE + 100, _BASE + 200], dtype=pl.Int64).cast(pl.Datetime("ns")),
            "clock": pl.Series([100, 200], dtype=pl.Int64).cast(pl.Time),
            "span": pl.Series([100, 200], dtype=pl.Int64).cast(pl.Duration("ns")),
            "amount": [5, 5],
        }
    )


def _rows() -> list[dict[str, object]]:
    return [
        {"at": at, "clock": clock, "span": span, "amount": 5}
        for at, clock, span in zip(_TEXTS["at"], _TEXTS["clock"], _TEXTS["span"], strict=True)
    ]


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_a_node_preview_shows_all_nine_digits(tmp_path: Path) -> None:
    from haute.executor import execute_graph

    path = tmp_path / "events.parquet"
    _nanoseconds().write_parquet(path)
    node = GraphNode(
        id="src",
        data=NodeData(
            label="src", nodeType="dataInput", config=make_ready_file_input_config(str(path))
        ),
    )

    result = execute_graph(PipelineGraph(nodes=[node]), target_node_id="src", max_preview_rows=10)

    assert result["src"].preview == _rows()


def test_a_file_preview_shows_all_nine_digits(tmp_path: Path) -> None:
    use_project(tmp_path)
    _nanoseconds().write_parquet(tmp_path / "events.parquet")

    answer = TestClient(project_app()).get("/api/schema", params={"path": "events.parquet"})

    assert answer.status_code == 200, answer.text
    assert answer.json()["preview"] == _rows()


# ---------------------------------------------------------------------------
# The OUTPUT document and a deployment's answers
# ---------------------------------------------------------------------------


def _output_frame() -> pl.DataFrame:
    from haute._node_apply import assemble_output_from_config

    mapping = [
        {"source_port": "events", "source_column": name, "output_path": path, "enabled": True}
        for name, path in [
            ("amount", "$[:].amount"),
            ("at", "$[:].events[:].at"),
            ("clock", "$[:].events[:].clock"),
            ("span", "$[:].events[:].span"),
        ]
    ]
    frame = assemble_output_from_config(
        _nanoseconds().lazy(), config={"output_mapping": mapping}, source_names=["events"]
    )
    return frame.collect(engine="in-memory")


_DOCUMENT = [
    {
        "amount": 5,
        "events": [
            {"at": at, "clock": clock, "span": span}
            for at, clock, span in zip(_TEXTS["at"], _TEXTS["clock"], _TEXTS["span"], strict=True)
        ],
    }
]


def test_the_output_document_keeps_objects_apart_with_nine_digits() -> None:
    from haute._output_assembler import render_output_document

    frame = _output_frame()

    assert frame.schema["events"] == pl.List(
        pl.Struct({"at": pl.Datetime("ns"), "clock": pl.Time(), "span": pl.Duration("ns")})
    )
    assert render_output_document(frame) == _DOCUMENT


def test_a_deployed_answer_carries_all_nine_digits() -> None:
    import json

    from haute.deploy._response_json import ndjson_rows, response_json, response_rows

    frame = _output_frame()

    assert json.loads(response_json(response_rows(frame))) == _DOCUMENT
    assert [json.loads(line) for line in ndjson_rows(frame).splitlines()] == _DOCUMENT


def test_an_impact_dataset_is_sent_with_all_nine_digits() -> None:
    from haute.deploy._impact import dataset_records

    assert dataset_records(_nanoseconds()) == _rows()


def test_a_dry_runs_sample_rows_are_its_exact_text() -> None:
    from haute.deploy._scorer import request_records

    assert list(request_records(_nanoseconds())) == _rows()


def test_an_expected_output_is_compared_to_the_nanosecond() -> None:
    from haute.deploy._validators import _TestQuoteCase, _validate_expected_outputs_per_case

    case = _TestQuoteCase(input={}, expected={"at": _TEXTS["at"][1]}, tolerance_pct=0.0)
    one = _nanoseconds().select("at").slice(1, 1)
    other = _nanoseconds().select("at").slice(0, 1)

    _validate_expected_outputs_per_case(cases=[case], outputs=[one])
    with pytest.raises(ValueError, match="expected-output validation failed"):
        _validate_expected_outputs_per_case(cases=[case], outputs=[other])


# ---------------------------------------------------------------------------
# A request's date-time
# ---------------------------------------------------------------------------


def test_a_requests_date_time_reaches_the_frame_whole_and_finer_is_refused() -> None:
    from haute._json_values import JsonValueError, json_value

    nanosecond = pl.Datetime("ns")
    assert json_value(_TEXTS["at"][0], nanosecond) == _BASE + 100
    assert json_value(_TEXTS["at"][1], nanosecond) == _BASE + 200
    with pytest.raises(JsonValueError) as refused:
        json_value(_TEXTS["at"][0], pl.Datetime("us"))
    assert refused.value.finer
