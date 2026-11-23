"""A temporal value reaches Python only as its exact text (specs/io-layer).

Python's ``datetime``, ``time`` and ``timedelta`` hold microseconds; a ``Datetime("ns")``, a
``Duration("ns")`` and every ``Time`` hold nanoseconds. Each site that reads a frame's values
into Python is sent such values that differ below a microsecond here, and must keep them apart
and give back all nine digits.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._types import GraphNode, NodeData, PipelineGraph
from tests._project import project_app, use_project
from tests._source_files import source_files
from tests.conftest import make_ready_file_input_config

_REPO = Path(__file__).resolve().parents[1]

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


# ---------------------------------------------------------------------------
# The schema dry run, the traced formula and a Rating Step's misses
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_a_deploy_schema_dry_run_sends_its_sample_with_all_nine_digits(monkeypatch) -> None:
    from haute.deploy import _batch_scoring
    from haute.deploy._schema import _capped_worker_output_schema

    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                {"id": "quotes", "data": {"label": "quotes", "nodeType": "apiInput", "config": {}}},
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "output_mapping": [
                                {
                                    "source_port": "quotes",
                                    "source_column": "at",
                                    "output_path": "$[:].at",
                                    "enabled": True,
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "quotes", "target": "out", "sourceHandle": "quotes"}],
        }
    )
    sent: list[list[dict[str, object]]] = []
    prepare = _batch_scoring.prepare_batch_scoring

    def recorded(rows, **kwargs):  # type: ignore[no-untyped-def]
        sent.append(list(rows))
        return prepare(rows, **kwargs)

    monkeypatch.setattr(_batch_scoring, "prepare_batch_scoring", recorded)

    schema = _capped_worker_output_schema(
        graph,
        _nanoseconds().select("at").slice(0, 1),
        output_node_id="out",
        input_node_ids=["quotes"],
        artifact_paths=None,
    )

    assert sent == [[{"at": _TEXTS["at"][0]}]]
    assert schema == {"at": str(pl.Datetime("ns"))}


def test_a_traced_formulas_result_keeps_all_nine_digits() -> None:
    from haute._expression_parser import evaluate_expression

    row = _nanoseconds().slice(0, 1)
    code = "df = df.with_columns(later=pl.col('at') + pl.duration(nanoseconds=100))"

    evaluated = evaluate_expression(code, "later", {"at": _TEXTS["at"][0]}, row=row)

    assert evaluated.result_value == _TEXTS["at"][1]


def test_a_rating_steps_missing_temporal_keys_are_logged_with_all_nine_digits() -> None:
    import structlog.testing

    from haute._rating import apply_rating_step_from_config

    config = {
        "tables": [
            {
                "factors": ["at"],
                "output_column": "at_factor",
                "default_value": "",
                "entries": [{"at": _TEXTS["at"][0], "value": 1.1}],
            }
        ],
        "combined_outputs": [],
    }
    with structlog.testing.capture_logs() as logs:
        rated = apply_rating_step_from_config(_nanoseconds().select("at").lazy(), config)
        assert rated.collect()["at_factor"].to_list() == [1.1, None]

    # A factor is keyed by its text (``rating_key_of``), which Polars writes whole.
    misses = [log for log in logs if log["event"] == "rating_table_lookup_misses"]
    assert [miss["missing_keys"] for miss in misses] == [[{"at": "2001-09-09 01:46:40.000000200"}]]


def test_a_numpy_date_time_in_a_hand_built_table_keeps_its_nanoseconds() -> None:
    import numpy as np

    from haute.deploy._model_code import _plain

    table = {"events": [{"at": np.datetime64(_TEXTS["at"][0], "ns")}, {"at": np.datetime64("NaT")}]}

    assert _plain(table) == {"events": [{"at": _TEXTS["at"][0]}, {"at": None}]}


# ---------------------------------------------------------------------------
# Every module that reads a frame's values into Python is driven or exempted
# ---------------------------------------------------------------------------

#: The calls that read a frame's (or a series') values into Python objects, and
#: ``to_dict(as_series=False)``, which does too.
_READING_METHODS = frozenset(
    {"iter_rows", "to_dicts", "rows", "row", "rows_by_key", "to_list", "item"}
)

_HERE = "tests/test_temporal_values.py"
_TRACED = "tests/test_trace_integration.py::test_each_row_is_traced_to_its_own_rows_and_level"
_TRACED_SPANS = (
    "tests/test_trace_integration.py::test_rows_told_apart_by_a_time_or_a_duration_are_traced_apart"
)
_FACTOR_LEVELS = (
    "tests/test_optimiser_segments.py::test_nanosecond_factor_levels_100_ns_apart_are_two_levels"
)
_GROUPS = (
    "tests/test_training_evaluation.py"
    "::test_group_values_apart_below_a_microsecond_stay_separate_groups"
)

#: Each module that reads values into Python and the tests that send it datetime, time and
#: duration values differing below a microsecond, which it must keep apart and give back whole.
_DRIVEN: dict[str, tuple[str, ...]] = {
    "src/haute/_temporal_text.py": (
        "tests/test_temporal_text.py::test_exact_text_writes_every_temporal_value_and_leaves_the_rest",
        "tests/test_temporal_text.py::test_a_datetimes_text_reads_back_as_its_own_value",
    ),
    "src/haute/executor.py": (f"{_HERE}::test_a_node_preview_shows_all_nine_digits",),
    "src/haute/routes/files.py": (f"{_HERE}::test_a_file_preview_shows_all_nine_digits",),
    "src/haute/_output_assembler.py": (
        f"{_HERE}::test_the_output_document_keeps_objects_apart_with_nine_digits",
        "tests/test_output_assembler.py"
        "::test_a_limited_preview_keeps_every_child_of_a_parent_keyed_below_a_microsecond",
    ),
    "src/haute/deploy/_response_json.py": (
        f"{_HERE}::test_a_deployed_answer_carries_all_nine_digits",
    ),
    "src/haute/deploy/_impact.py": (
        f"{_HERE}::test_an_impact_dataset_is_sent_with_all_nine_digits",
    ),
    "src/haute/deploy/_model_code.py": (
        f"{_HERE}::test_a_numpy_date_time_in_a_hand_built_table_keeps_its_nanoseconds",
    ),
    "src/haute/_trace_correlation.py": (_TRACED, _TRACED_SPANS),
    "src/haute/trace.py": (_TRACED, _TRACED_SPANS),
    "src/haute/_rating.py": (
        _TRACED,
        f"{_HERE}::test_a_rating_steps_missing_temporal_keys_are_logged_with_all_nine_digits",
    ),
    "src/haute/_frame_profile.py": (
        "tests/test_frame_profile.py"
        "::test_temporal_values_the_column_holds_apart_are_counted_and_shown_apart",
    ),
    "src/haute/routes/_pivot_service.py": (
        "tests/test_explore_pivot_routes.py"
        "::test_a_pivot_keeps_the_rows_of_values_that_differ_below_a_microsecond",
        "tests/test_explore_pivot_routes.py"
        "::test_a_min_or_max_of_a_nanosecond_value_keeps_its_nine_digits",
    ),
    "src/haute/modelling/_evaluation.py": (
        "tests/test_evaluation.py"
        "::test_a_key_python_holds_exactly_keys_and_plans_as_its_python_value",
        _GROUPS,
    ),
    "src/haute/modelling/_training_job.py": (
        _GROUPS,
        "tests/test_training_evaluation.py"
        "::test_dates_apart_below_a_microsecond_stay_separate_dates",
    ),
    "src/haute/assistant/_tools.py": (
        "tests/test_assistant_node_schema.py"
        "::test_a_temporal_bound_shows_all_nine_digits_of_a_nanosecond_value",
    ),
    "src/haute/routes/_optimiser_solver.py": (_FACTOR_LEVELS,),
    "src/haute/routes/_optimiser_outcomes.py": (_FACTOR_LEVELS,),
    "src/haute/routes/_optimiser_segments.py": (
        _FACTOR_LEVELS,
        "tests/test_optimiser_segments.py::test_times_within_one_second_are_their_own_levels",
        "tests/test_optimiser_segments.py"
        "::test_a_zoned_datetime_is_ordered_by_its_instant_and_keeps_its_label",
    ),
    "src/haute/_optimiser_apply_explainability.py": (
        "tests/test_optimiser_apply_trace_enrichment.py"
        "::test_ratebook_trace_keys_factor_levels_100_ns_apart_by_their_own_values",
    ),
    "src/haute/routes/_optimiser_quotes.py": (
        "tests/test_optimiser_quotes.py"
        "::test_an_analysis_columns_nanosecond_values_keep_all_nine_digits",
    ),
}

_COUNTS = "reads row, null or byte counts, integers"
_WORKBENCH = (
    "a Workbench table's columns are Integer, Decimal, Text, True/false or Date "
    "(COLUMN_DTYPES), never a datetime, time or duration"
)
#: Each module that reads values into Python but never a datetime, time or duration, and why.
_NOT_DRIVEN: dict[str, str] = {
    "src/haute/_binning.py": (
        "bins a column as Float64 numbers, reading only the bins' extent and counts"
    ),
    "src/haute/_chunked_writes.py": "reads row counts and the rows each chunk expects, integers",
    "src/haute/_databricks_output.py": _COUNTS,
    "src/haute/_graph_walker.py": (
        "reads one row of a measuring walk's aggregates: counts, rule claims and misses"
    ),
    "src/haute/_mlflow_io.py": "reads a classifier's two class labels from numpy scalars",
    "src/haute/_model_explainability.py": "reads a GLM's contribution records, a number per term",
    "src/haute/_polars_dtypes.py": "reads an integer type's least and greatest value",
    "src/haute/_polars_utils.py": (
        "reads row counts and quote ids cast to text (key_prefix_python_scan)"
    ),
    "src/haute/_workbench_import.py": (
        "reads a workbook's cells through openpyxl's worksheet, never a frame: a date cell is "
        "a datetime of milliseconds, which a Date column takes the day of (_native)"
    ),
    "src/haute/_workbench_input.py": _WORKBENCH,
    "src/haute/_workbench_output.py": _WORKBENCH,
    "src/haute/_workbench_params.py": "reads a params file's allowed values as stripped text",
    "src/haute/deploy/_batch_scoring.py": _COUNTS,
    "src/haute/deploy/_request_rows.py": _COUNTS,
    "src/haute/routes/_banding_stats.py": "reads totals and each rule's claimed rows, integers",
    "src/haute/routes/_explore_relationships.py": (
        "reads statistics of numeric features (is_numeric) and their weights"
    ),
    "src/haute/routes/_rating_levels.py": _COUNTS,
    "src/haute/modelling/_metrics.py": (
        "reads categorical features cast to text, and a dependence grid as physical integers "
        "it casts back to the feature's type"
    ),
    "src/haute/modelling/_native_encoding.py": "reads categorical features cast to text",
    "src/haute/modelling/_shap.py": (
        "reads categorical features cast to text and numeric ones as the model's Float64"
    ),
    "src/haute/modelling/_split.py": (
        "the constructor-only split seam, which no product path reaches (MDL-05 removes it)"
    ),
    "src/haute/modelling/_target_check.py": "reads a boolean aggregate",
    "src/haute/routes/_optimiser_service.py": (
        "reads Float32 extrema and sums, a contiguity flag, hashes and quote ids cast to text"
    ),
    "src/haute/routes/_optimiser_input.py": (
        "reads validation counts, the largest scenario index and quote ids cast to text"
    ),
    "src/haute/routes/_optimiser_frontier.py": _COUNTS,
    "src/haute/routes/_frontier_point_summary.py": "reads price-contour's frontier points, numbers",
    "src/haute/routes/_point_analysis.py": "reads rating keys, text written by rating_key_of",
}


#: Every call of src/haute that reads values into Python, by module, then by the function
#: it sits in and its method, with how many there are. A call added anywhere, inside a
#: module already listed too, changes this, and the test below names it: route it through
#: ``haute._temporal_text`` or, if it never reads a datetime, time or duration value, pin
#: it here, its module driven or exempted above.
_READING_CALLS: dict[str, dict[str, int]] = {
    "src/haute/_binning.py": {
        "equal_width_bins:item": 3,
        "equal_width_bins:iter_rows": 1,
    },
    "src/haute/_chunked_writes.py": {
        "_ChunkJoin._write_split:to_list": 1,
        "_ChunkJoin.write:item": 1,
        "row_count:item": 1,
    },
    "src/haute/_databricks_output.py": {
        "write_databricks_output:item": 1,
    },
    "src/haute/_frame_profile.py": {
        "_build_frame_stats:row": 1,
        "_build_histograms:row": 1,
        "_count_unique_rows:item": 2,
    },
    "src/haute/_graph_walker.py": {
        "_Walk._collect_row:row": 1,
    },
    "src/haute/_mlflow_io.py": {
        "_python_scalar:item": 1,
    },
    "src/haute/_model_explainability.py": {
        "_single_glm_contribution_record:to_dicts": 1,
    },
    "src/haute/_optimiser_apply_explainability.py": {
        "_explain_ratebook:row": 1,
        "_python_match_ratebook_input_row:iter_rows": 1,
    },
    "src/haute/_output_assembler.py": {
        "_rows_from_dataframe:iter_rows": 1,
    },
    "src/haute/_polars_dtypes.py": {
        "_integer_range:row": 1,
    },
    "src/haute/_polars_utils.py": {
        "bounded_collect_batches:item": 1,
        "key_prefix_python_scan.produce:to_list": 1,
    },
    "src/haute/_rating.py": {
        "_apply_rating_miss_guard._check:to_dicts": 1,
        "_duration_key_series:to_list": 1,
        "_parsed_text:to_list": 1,
        "_text_as_number:to_list": 5,
        "_typed_keys:to_list": 5,
    },
    "src/haute/_temporal_text.py": {
        "exact_row:row": 1,
        "exact_rows:to_dicts": 1,
    },
    "src/haute/_trace_correlation.py": {
        "RowScopeResolver.count_rows:item": 1,
        "_candidate_payload:to_list": 1,
        "_identical_candidate_count:row": 1,
        "_match_rows_vectorized:item": 1,
    },
    "src/haute/_workbench_import.py": {
        "_excel_rows:iter_rows": 2,
    },
    "src/haute/_workbench_input.py": {
        "check_request_keys:to_list": 1,
    },
    "src/haute/_workbench_output.py": {
        "_fitted:to_list": 1,
        "workbench_response.produce:to_dicts": 1,
    },
    "src/haute/_workbench_params.py": {
        "param_file_from_bytes:to_list": 1,
    },
    "src/haute/assistant/_tools.py": {
        "_column_profile:row": 1,
        "_column_profile:to_dicts": 1,
    },
    "src/haute/deploy/_batch_scoring.py": {
        "_parquet_row_count:item": 1,
    },
    "src/haute/deploy/_impact.py": {
        "_segment_breakdown:iter_rows": 1,
    },
    "src/haute/deploy/_model_code.py": {
        "_plain:item": 1,
    },
    "src/haute/deploy/_request_rows.py": {
        "check_lookups:item": 1,
    },
    "src/haute/deploy/_response_json.py": {
        "first_non_finite:to_list": 1,
    },
    "src/haute/executor.py": {
        "prepare_data_output:item": 1,
    },
    "src/haute/modelling/_evaluation.py": {
        "evaluation_key_values:to_list": 4,
    },
    "src/haute/modelling/_metrics.py": {
        "_ave_categorical_bins:iter_rows": 1,
        "compute_pdp:to_list": 2,
    },
    "src/haute/modelling/_native_encoding.py": {
        "encode_frame:to_list": 2,
        "fit_categorical_levels:to_list": 1,
    },
    "src/haute/modelling/_shap.py": {
        "_beeswarm_feature:to_list": 2,
        "_categorical_curve:to_list": 1,
    },
    "src/haute/modelling/_split.py": {
        "_group_mask:to_list": 2,
    },
    "src/haute/modelling/_target_check.py": {
        "_has_fractional_values:item": 1,
    },
    "src/haute/modelling/_training_job.py": {
        "TrainingJob._prepare_data:item": 1,
        "TrainingJob._require_positive_log_link_offset:item": 1,
        "TrainingJob._resolve_class_labels:to_list": 1,
    },
    "src/haute/routes/_banding_stats.py": {
        "BandingStatsService._compute:item": 2,
        "BandingStatsService._with_rule_counts:iter_rows": 1,
    },
    "src/haute/routes/_explore_relationships.py": {
        "ExploreRelationshipsService._between:item": 1,
        "ExploreRelationshipsService._categorical_levels:item": 7,
        "ExploreRelationshipsService._categorical_levels:to_list": 2,
        "ExploreRelationshipsService._compute:item": 8,
        "ExploreRelationshipsService._key_check:item": 2,
        "ExploreRelationshipsService._numeric_levels:iter_rows": 1,
    },
    "src/haute/routes/_frontier_point_summary.py": {
        "frontier_point_rows:to_dicts": 1,
    },
    "src/haute/routes/_optimiser_frontier.py": {
        "OptimiserFrontierService._point_apply_estimate:item": 1,
    },
    "src/haute/routes/_optimiser_input.py": {
        "AutoRangeValueCheck.add:row": 1,
        "AutoRangeValueCheck.raise_if_invalid:item": 1,
        "_non_finite_detail_from_counts:item": 2,
        "_null_value_detail_from_counts:item": 1,
        "estimate_input_metrics:row": 1,
        "forecast_solve_memory_from_input:item": 1,
        "quote_layout_fault:item": 2,
        "scenario_step_count:item": 1,
        "validate_input_value_contracts:item": 1,
    },
    "src/haute/routes/_optimiser_outcomes.py": {
        "ChoiceQueryService._run:item": 1,
        "_key_fingerprint:row": 1,
        "_require_constant_within_quote:item": 1,
        "_require_constant_within_quote:row": 1,
        "histogram_of_frame:item": 1,
        "write_quote_analysis:row": 1,
    },
    "src/haute/routes/_optimiser_quotes.py": {
        "QuotePage.run:item": 1,
    },
    "src/haute/routes/_optimiser_segments.py": {
        "AnalysisSegments._numeric:iter_rows": 1,
        "AnalysisSegments._numeric:row": 1,
        "_categorical_levels:iter_rows": 1,
        "segments_response:to_dicts": 1,
    },
    "src/haute/routes/_optimiser_service.py": {
        "_ScenarioFrontierRangeAccumulator._carry_batch:item": 4,
        "_ScenarioFrontierRangeAccumulator._carry_batch:row": 1,
        "_ScenarioFrontierRangeAccumulator._fallback_totals:row": 1,
        "_ScenarioFrontierRangeAccumulator._row_values:row": 1,
        "_ScenarioFrontierRangeAccumulator.add_batch:item": 1,
    },
    "src/haute/routes/_optimiser_solver.py": {
        "_counted_level_keys:to_list": 2,
        "_json_records:to_dicts": 1,
        "frontier_point_history_payload:to_dicts": 1,
        "ratebook_factor_level_labels:to_list": 1,
    },
    "src/haute/routes/_pivot_service.py": {
        "PivotService._cardinalities:item": 2,
        "PivotService._member_options:item": 1,
        "_exact_rows:iter_rows": 1,
    },
    "src/haute/routes/_point_analysis.py": {
        "most_common_values:item": 1,
        "most_common_values:iter_rows": 1,
    },
    "src/haute/routes/_rating_levels.py": {
        "RatingLevelsService._compute:item": 2,
    },
    "src/haute/routes/files.py": {
        "_read_schema_blocking:item": 1,
    },
    "src/haute/trace.py": {
        "_assemble_steps:row": 1,
        "_typed_input_row:row": 1,
    },
}


def _method_read(node: ast.Call) -> str | None:
    """The method a call reads values into Python with, if it does."""
    if not isinstance(node.func, ast.Attribute):
        return None
    method = node.func.attr
    if method in _READING_METHODS:
        return method
    if method == "to_dict" and any(keyword.arg == "as_series" for keyword in node.keywords):
        return method
    return None


def _reading_calls(source: str) -> Counter[str]:
    """Each call of *source* reading values into Python, as ``<function>:<method>`` (the
    dotted path of classes and functions it sits in, ``<module>`` at the top), counted."""
    found: Counter[str] = Counter()

    def walk(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, f"{scope}.{child.name}" if scope else child.name)
                continue
            if isinstance(child, ast.Call) and (method := _method_read(child)) is not None:
                found[f"{scope or '<module>'}:{method}"] += 1
            walk(child, scope)

    walk(ast.parse(source), "")
    return found


def _inventory() -> dict[str, dict[str, int]]:
    root = _REPO / "src" / "haute"
    found = {}
    for path in source_files(root):
        calls = _reading_calls(path.read_text(encoding="utf-8"))
        if calls:
            found[path.relative_to(_REPO).as_posix()] = dict(sorted(calls.items()))
    return found


@pytest.mark.meta
def test_every_call_reading_values_into_python_is_pinned() -> None:
    found = _inventory()
    changes = sorted(
        f"{module} {call}: {pinned.get(call, 0)} pinned, {held.get(call, 0)} found"
        for module in set(found) | set(_READING_CALLS)
        for held, pinned in [(found.get(module, {}), _READING_CALLS.get(module, {}))]
        for call in set(held) | set(pinned)
        if held.get(call, 0) != pinned.get(call, 0)
    )
    assert not changes, (
        "A call reading values into Python was added, moved or removed. Route a new one "
        "through haute._temporal_text, or pin it in _READING_CALLS if it never reads a "
        f"datetime, time or duration value: {changes}"
    )


@pytest.mark.meta
def test_every_module_reading_values_into_python_is_driven_or_exempted() -> None:
    readers = set(_READING_CALLS)
    undriven = sorted(readers - set(_DRIVEN) - set(_NOT_DRIVEN))
    assert not undriven, (
        "Each module that reads a frame's values into Python gets a test sending it datetime, "
        "time and duration values that differ below a microsecond (_DRIVEN), or an entry in "
        f"_NOT_DRIVEN saying why it never reads one: {undriven}"
    )
    stale = sorted((set(_DRIVEN) | set(_NOT_DRIVEN)) - readers)
    assert not stale, f"These modules no longer read values into Python: {stale}"
    assert not set(_DRIVEN) & set(_NOT_DRIVEN)


def _test_names(path: Path) -> set[str]:
    return {
        node.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def test_every_driver_named_is_a_test() -> None:
    named = {driver for drivers in _DRIVEN.values() for driver in drivers}
    missing = sorted(
        driver
        for driver in named
        if driver.split("::")[-1] not in _test_names(_REPO / driver.split("::")[0])
    )
    assert missing == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("frame.to_dicts()", {"<module>:to_dicts": 1}),
        ("frame.row(0, named=True)", {"<module>:row": 1}),
        ("def f(s):\n    return s.to_list() + s.to_list()", {"f:to_list": 2}),
        ("class C:\n    def g(self, f):\n        return f.item()", {"C.g:item": 1}),
        ("frame.to_dict(as_series=False)", {"<module>:to_dict": 1}),
        ("exact_rows(frame)", {}),
        ("frame.to_dict()", {}),
    ],
)
def test_a_reading_call_is_found_by_its_function_and_method(
    source: str, found: dict[str, int]
) -> None:
    assert _reading_calls(source) == found


def test_a_reading_call_added_to_a_listed_module_or_function_is_caught() -> None:
    module = "src/haute/routes/_banding_stats.py"
    source = (_REPO / module).read_text(encoding="utf-8")
    added = source + "\n\ndef extra(frame):\n    return frame.to_dicts()\n"

    assert dict(_reading_calls(source)) == _READING_CALLS[module]
    assert dict(_reading_calls(added)) != _READING_CALLS[module]
    # A second call inside a function already pinned changes its count.
    one = "def f(frame):\n    return frame.item()\n"
    two = "def f(frame):\n    frame.to_dicts()\n    return frame.item()\n"
    assert _reading_calls(one) != _reading_calls(two)
