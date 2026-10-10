"""Expected-output deploy validation for golden test quotes."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from haute.deploy._config import DeployConfig, ResolvedDeploy
from haute.deploy._project_modules import ProjectModules
from haute.deploy._scorer import QuoteRequest
from haute.deploy._validators import load_test_quote_file, score_test_quotes, validate_deploy
from haute.errors import DeployError
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _resolved_with_quotes(
    test_quotes_dir: Path,
    *,
    input_schema: dict[str, str] | None = None,
    output_schema: dict[str, str] | None = None,
    request_input: NodeType = NodeType.API_INPUT,
) -> ResolvedDeploy:
    inp = GraphNode(
        id="api_in",
        data=NodeData(label="api_in", nodeType=request_input, config={}),
    )
    out = GraphNode(
        id="output",
        data=NodeData(label="output", nodeType=NodeType.OUTPUT, config={}),
    )
    graph = PipelineGraph(
        nodes=[inp, out],
        edges=[GraphEdge(id="e1", source="api_in", target="output")],
    )
    return ResolvedDeploy(
        config=DeployConfig(
            pipeline_file=Path("main.py"),
            model_name="pricing-model",
            test_quotes_dir=test_quotes_dir,
        ),
        full_graph=graph,
        pruned_graph=graph,
        input_node_ids=["api_in"],
        output_node_id="output",
        artifacts={},
        input_schema=input_schema or {"VehPower": "Int64", "Area": "String"},
        output_schema=output_schema or {"premium": "Float64"},
        project_modules=ProjectModules(utility=None, unbundled_imports=()),
    )


def test_load_test_quote_file_unwraps_golden_rows_and_strips_metadata(
    tmp_path: Path,
) -> None:
    quote_file = tmp_path / "golden.json"
    _write_json(
        quote_file,
        [
            {
                "input": {"VehPower": 7, "Area": "C", "_debug_note": "strip me"},
                "expected": {"premium": 548.57},
                "tolerance_pct": 0.01,
                "_description": "representative motor quote",
            }
        ],
    )

    assert load_test_quote_file(quote_file) == [{"VehPower": 7, "Area": "C"}]


def test_load_test_quote_file_unwraps_input_without_expected_output(
    tmp_path: Path,
) -> None:
    quote_file = tmp_path / "single-input-field.json"
    _write_json(quote_file, [{"input": {"raw": 1}, "_note": "strip me"}])

    assert load_test_quote_file(quote_file) == [{"raw": 1}]


def test_score_test_quotes_passes_expected_outputs_within_tolerance(
    tmp_path: Path,
) -> None:
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {
                "input": {"VehPower": 7, "Area": "C"},
                "expected": {"premium": 548.57, "tier": "standard"},
                "tolerance_pct": 0.01,
            },
            {
                "input": {"VehPower": 9, "Area": "D"},
                "expected": {"premium": 701.0, "approved": True},
                "tolerance_pct": 0,
            },
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame(
            {
                "premium": [550.0, 701.0],
                "tier": ["standard", "ignored-for-row-2"],
                "approved": [False, True],
            }
        )
        [result] = score_test_quotes(resolved)

    assert result["status"] == "ok"
    scored_input = score_graph.call_args.kwargs["input_df"]
    assert scored_input.to_dicts() == [
        {"VehPower": 7, "Area": "C"},
        {"VehPower": 9, "Area": "D"},
    ]


def test_score_test_quotes_scores_a_workbench_inputs_cases_one_request_each(
    tmp_path: Path,
) -> None:
    """A Workbench Input reads one quote per request, so each case is its own request and
    the outputs are stacked in the file's order for the expected-output comparison."""
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {"input": {"policy": {"limit": 7}}, "expected": {"premium": 70.0}},
            {"input": {"policy": {"limit": 9}}, "expected": {"premium": 90.0}},
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir, request_input=NodeType.WORKBENCH_INPUT)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.side_effect = lambda **kwargs: pl.DataFrame(
            {"premium": [kwargs["input_df"].records[0]["policy"]["limit"] * 10.0]}
        )
        [result] = score_test_quotes(resolved)

    assert (result["status"], result["rows"]) == ("ok", 2)
    # Each case as its request was sent, for the Workbench Input to read and type itself.
    assert [call.kwargs["input_df"] for call in score_graph.call_args_list] == [
        QuoteRequest(({"policy": {"limit": 7}},)),
        QuoteRequest(({"policy": {"limit": 9}},)),
    ]

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"premium": [70.0]})
        [drifted] = score_test_quotes(resolved)

    assert drifted["status"] == "error"
    assert "row 1 column 'premium'" in str(drifted["error"])

    # Each case answers with one row of its own: two rows for the first case and none for
    # the second are two failures, never one request's rows making up for another's.
    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.side_effect = [
            pl.DataFrame({"premium": [70.0, 90.0]}),
            pl.DataFrame({"premium": []}, schema={"premium": pl.Float64}),
        ]
        [uneven] = score_test_quotes(resolved)

    assert uneven["status"] == "error"
    assert "row 0: expected one output row, 2 output row(s)" in str(uneven["error"])
    assert "row 1: expected one output row, 0 output row(s)" in str(uneven["error"])


def _workbench_response(premium: float, ok: bool) -> pl.DataFrame:
    """One request's response as a Workbench Output gives it: its tables nested."""
    return pl.DataFrame(
        {
            "pricing": [{"premium": premium, "referral": "none"}],
            "layers": [[{"layer": 1, "ok": ok}, {"layer": 2, "ok": True}]],
        }
    )


def test_score_test_quotes_walks_into_a_workbench_outputs_tables(tmp_path: Path) -> None:
    """The tolerance reaches a number inside a table, and a boolean there is held to a
    boolean, as they are for a flat response's columns."""
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {
                "input": {"policy": {"limit": 7}},
                "expected": {
                    "pricing": {"premium": 100.0},
                    "layers": [{"layer": 1, "ok": True}, {"layer": 2}],
                },
                "tolerance_pct": 0.01,
            }
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir, request_input=NodeType.WORKBENCH_INPUT)

    def scored(premium: float, ok: bool) -> dict:
        with patch("haute.deploy._validators.score_graph") as score_graph:
            score_graph.return_value = _workbench_response(premium, ok)
            [result] = score_test_quotes(resolved)
        return result

    assert scored(100.5, True)["status"] == "ok"
    drifted = scored(102.0, True)
    assert drifted["status"] == "error"
    assert (
        "row 0 column 'pricing.premium' outside tolerance: expected=100.0 actual=102.0"
        in (drifted["error"])
    )
    # A number for a boolean inside a table: `1 == True` in Python, but not here.
    _write_json(
        quotes_dir / "golden.json",
        [{"input": {"policy": {"limit": 7}}, "expected": {"layers": [{"layer": 1, "ok": 1}, {}]}}],
    )
    assert (
        "row 0 column 'layers[0].ok' mismatch: expected=1 actual=True"
        in scored(100.0, True)["error"]
    )
    # A key the table does not have, and a list of another length.
    _write_json(
        quotes_dir / "golden.json",
        [{"input": {"policy": {"limit": 7}}, "expected": {"pricing": {"discount": 5}}}],
    )
    assert (
        "row 0 column 'pricing.discount' mismatch: expected=5 actual='<missing>'"
        in (scored(100.0, True)["error"])
    )
    _write_json(
        quotes_dir / "golden.json",
        [{"input": {"policy": {"limit": 7}}, "expected": {"layers": [{"layer": 1}]}}],
    )
    assert "row 0 column 'layers' mismatch" in scored(100.0, True)["error"]


def test_validate_deploy_lets_a_workbench_quote_leave_a_table_out(tmp_path: Path) -> None:
    """A Quote Input's cases must hold every column; a Workbench Input's quote need not
    hold every table, and its reader says what does not fit when the quote is scored."""
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(quotes_dir / "golden.json", [{"input": {"policy": {"limit": 7}}}])
    resolved = _resolved_with_quotes(
        quotes_dir,
        request_input=NodeType.WORKBENCH_INPUT,
        input_schema={
            "policy": "Struct({'limit': Int64})",
            "items": "List(Struct({'value': Float64}))",
        },
        output_schema={"pricing": "Struct({'premium': Float64})"},
    )

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"pricing": [{"premium": 70.0}]})
        [result] = validate_deploy(resolved)

    assert result["status"] == "ok"
    assert score_graph.call_args.kwargs["input_df"] == QuoteRequest(({"policy": {"limit": 7}},))

    # The Quote Input's rule stands for a Quote Input.
    flat = _resolved_with_quotes(quotes_dir)
    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"premium": [70.0]})
        with pytest.raises(DeployError) as refused:
            validate_deploy(flat)
    assert any(
        "missing required input column(s) ['Area', 'VehPower']" in error
        for error in refused.value.context["test_quote_errors"]
    )


def test_score_test_quotes_reports_expected_output_drift(tmp_path: Path) -> None:
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {
                "input": {"VehPower": 7, "Area": "C"},
                "expected": {"premium": 100.0},
                "tolerance_pct": 0.01,
            }
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"premium": [102.25]})
        [result] = score_test_quotes(resolved)

    assert result["status"] == "error"
    assert "golden.json" not in result["error"]
    assert "row 0" in result["error"]
    assert "premium" in result["error"]
    assert "expected=100.0" in result["error"]
    assert "actual=102.25" in result["error"]
    assert "tolerance_pct=0.01" in result["error"]


def test_score_test_quotes_reports_boolean_numeric_mismatch(tmp_path: Path) -> None:
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {
                "input": {"VehPower": 7, "Area": "C"},
                "expected": {"approved": True},
            }
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"approved": [1]})
        [result] = score_test_quotes(resolved)

    assert result["status"] == "error"
    assert "approved" in result["error"]
    assert "expected=True" in result["error"]
    assert "actual=1" in result["error"]


def test_score_test_quotes_accepts_decimal_actual_within_tolerance(tmp_path: Path) -> None:
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {
                "input": {"VehPower": 7, "Area": "C"},
                "expected": {"premium": 100.0},
                "tolerance_pct": 0.01,
            }
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"premium": [Decimal("100.50")]})
        [result] = score_test_quotes(resolved)

    assert result["status"] == "ok"


def test_score_test_quotes_reports_large_integer_zero_tolerance_drift(
    tmp_path: Path,
) -> None:
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {
                "input": {"VehPower": 7, "Area": "C"},
                "expected": {"premium_cents": 9007199254740993},
                "tolerance_pct": 0,
            }
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"premium_cents": [9007199254740992]})
        [result] = score_test_quotes(resolved)

    assert result["status"] == "error"
    assert "premium_cents" in result["error"]
    assert "expected=9007199254740993" in result["error"]
    assert "actual=9007199254740992" in result["error"]


def test_score_test_quotes_reports_missing_expected_output_column(
    tmp_path: Path,
) -> None:
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {
                "input": {"VehPower": 7, "Area": "C"},
                "expected": {"premium": 100.0},
                "tolerance_pct": 0.01,
            }
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"other": [100.0]})
        [result] = score_test_quotes(resolved)

    assert result["status"] == "error"
    assert "missing expected output column 'premium'" in result["error"]
    assert "available columns ['other']" in result["error"]


def test_score_test_quotes_reports_row_count_mismatch_for_expected_outputs(
    tmp_path: Path,
) -> None:
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {"input": {"VehPower": 7, "Area": "C"}, "expected": {"premium": 100.0}},
            {"input": {"VehPower": 9, "Area": "D"}, "expected": {"premium": 110.0}},
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"premium": [100.0]})
        [result] = score_test_quotes(resolved)

    assert result["status"] == "error"
    assert "row count mismatch" in result["error"]
    assert "2 expected row(s)" in result["error"]
    assert "1 output row(s)" in result["error"]


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"input": ["not", "an", "object"], "expected": {"premium": 1}}, "input.*object"),
        ({"input": {"VehPower": 7}, "expected": ["premium"]}, "expected.*object"),
        ({"input": {"VehPower": 7}, "expected": None}, "expected.*object"),
        (
            {"input": {"VehPower": 7}, "expected": {"premium": 1}, "tolerance_pct": -0.1},
            "tolerance_pct.*non-negative",
        ),
        (
            {"input": {"VehPower": 7}, "expected": {"premium": 1}, "tolerance_pct": "1%"},
            "tolerance_pct.*number",
        ),
        (
            {"input": {"VehPower": 7}, "expected": {"premium": 1}, "tolerance_pct": True},
            "tolerance_pct.*number",
        ),
        (
            # F691: tolerance_pct is a raw fraction (0.01 == 1%); a value above
            # 1 (an operator writing 5 meaning "5%") is a 500% footgun.
            {"input": {"VehPower": 7}, "expected": {"premium": 1}, "tolerance_pct": 5},
            "tolerance_pct.*fraction",
        ),
        (
            {"input": {"VehPower": 7}, "expected": {"premium": 1}, "surprise": "typo"},
            "unknown.*surprise",
        ),
        ({"expected": {"premium": 1}}, "input.*object"),
        ({"tolerance_pct": 0.01}, "input.*object"),
        ({"input": {"VehPower": 7}, "tolerance_pct": 0.01}, "expected.*object"),
    ],
)
def test_load_test_quote_file_rejects_malformed_golden_rows(
    tmp_path: Path,
    row: dict[str, object],
    message: str,
) -> None:
    quote_file = tmp_path / "bad.json"
    _write_json(quote_file, [row])

    with pytest.raises(ValueError, match=message):
        load_test_quote_file(quote_file)


def test_validate_deploy_blocks_expected_output_drift(tmp_path: Path) -> None:
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    _write_json(
        quotes_dir / "golden.json",
        [
            {
                "input": {"VehPower": 7, "Area": "C"},
                "expected": {"premium": 100.0},
                "tolerance_pct": 0.01,
            }
        ],
    )
    resolved = _resolved_with_quotes(quotes_dir)

    with patch("haute.deploy._validators.score_graph") as score_graph:
        score_graph.return_value = pl.DataFrame({"premium": [102.25]})
        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)

    assert any(
        "golden.json" in err
        and "premium" in err
        and "expected=100.0" in err
        and "actual=102.25" in err
        for err in exc_info.value.context["test_quote_errors"]
    )
