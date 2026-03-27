"""Tests for haute.deploy._impact - impact analysis logic."""

from __future__ import annotations

import polars as pl
import pytest

from haute.deploy._impact import (
    ColumnStats,
    ImpactReport,
    SegmentRow,
    _column_stats,
    _preds_to_df,
    _segment_breakdown,
    build_report,
    format_markdown,
    format_terminal,
    score_endpoint_batched,
)


class TestPredsToDF:
    """Normalise various prediction formats into DataFrames."""

    def test_list_of_dicts(self) -> None:
        preds = [{"price": 100.0}, {"price": 200.0}]
        df = _preds_to_df(preds)
        assert df.shape == (2, 1)
        assert df.columns == ["price"]

    def test_list_of_lists(self) -> None:
        preds = [[1.0, 2.0], [3.0, 4.0]]
        df = _preds_to_df(preds)
        assert df.shape == (2, 2)
        assert df.columns == ["output_0", "output_1"]

    def test_list_of_scalars(self) -> None:
        preds = [10.0, 20.0, 30.0]
        df = _preds_to_df(preds)
        assert df.shape == (3, 1)
        assert df.columns == ["prediction"]

    def test_empty_list(self) -> None:
        df = _preds_to_df([])
        assert df.shape == (0, 0)


class TestColumnStats:
    """Change statistics for a single output column."""

    def test_no_change(self) -> None:
        stg = pl.Series("a", [100.0, 200.0, 300.0])
        prd = pl.Series("a", [100.0, 200.0, 300.0])
        stats = _column_stats(stg, prd, "price")
        assert stats.n_changed == 0
        assert stats.mean_change_pct == pytest.approx(0.0, abs=1e-4)
        assert stats.total_premium_change_pct == pytest.approx(0.0, abs=1e-4)

    def test_uniform_increase(self) -> None:
        prd = pl.Series("a", [100.0, 200.0, 400.0])
        stg = pl.Series("a", [110.0, 220.0, 440.0])  # +10% everywhere
        stats = _column_stats(stg, prd, "price")
        assert stats.n_changed == 3
        assert stats.mean_change_pct == pytest.approx(10.0, abs=0.1)
        assert stats.median_change_pct == pytest.approx(10.0, abs=0.1)
        assert stats.max_increase_pct == pytest.approx(10.0, abs=0.1)
        assert stats.max_decrease_pct == pytest.approx(10.0, abs=0.1)
        assert stats.total_premium_change_pct == pytest.approx(10.0, abs=0.1)

    def test_mixed_changes(self) -> None:
        prd = pl.Series("a", [100.0, 200.0])
        stg = pl.Series("a", [120.0, 180.0])  # +20%, -10%
        stats = _column_stats(stg, prd, "price")
        assert stats.n_changed == 2
        assert stats.max_increase_pct == pytest.approx(20.0, abs=0.1)
        assert stats.max_decrease_pct == pytest.approx(-10.0, abs=0.1)
        assert stats.staging_mean == pytest.approx(150.0)
        assert stats.prod_mean == pytest.approx(150.0)


class TestSegmentBreakdown:
    """Segment analysis by categorical columns."""

    def test_groups_by_string_column(self) -> None:
        stg_df = pl.DataFrame({"price": [110.0, 220.0, 105.0, 210.0] * 5})
        prd_df = pl.DataFrame({"price": [100.0, 200.0, 100.0, 200.0] * 5})
        input_df = pl.DataFrame({"region": ["A", "A", "B", "B"] * 5})
        segs = _segment_breakdown(stg_df, prd_df, input_df, "price")
        assert "region" in segs
        assert len(segs["region"]) == 2
        # A has +10% avg, B has +5% avg - A should be first (sorted by abs)
        assert segs["region"][0].value == "A"
        assert segs["region"][0].mean_change_pct == pytest.approx(10.0, abs=0.5)

    def test_skips_high_cardinality(self) -> None:
        stg_df = pl.DataFrame({"price": list(range(100))})
        prd_df = pl.DataFrame({"price": list(range(100))})
        # 100 unique values - too high cardinality for a segment
        input_df = pl.DataFrame({"id": [str(i) for i in range(100)]})
        segs = _segment_breakdown(stg_df, prd_df, input_df, "price")
        assert "id" not in segs

    def test_skips_small_groups(self) -> None:
        stg_df = pl.DataFrame({"price": [110.0, 220.0, 330.0]})
        prd_df = pl.DataFrame({"price": [100.0, 200.0, 300.0]})
        input_df = pl.DataFrame({"region": ["A", "B", "C"]})
        # Each group has < 10 rows, should be filtered out
        segs = _segment_breakdown(stg_df, prd_df, input_df, "price")
        assert segs == {} or all(len(rows) == 0 for rows in segs.values())


class TestBuildReport:
    """End-to-end report building from predictions."""

    def test_basic_report(self) -> None:
        stg = [{"price": 110.0}, {"price": 220.0}]
        prd = [{"price": 100.0}, {"price": 200.0}]
        inp = pl.DataFrame({"region": ["A", "B"]})
        report = build_report(
            stg,
            prd,
            inp,
            pipeline_name="test",
            staging_endpoint="test-staging",
            prod_endpoint="test",
            dataset_path="data/policies.parquet",
            total_rows=1000,
        )
        assert report.scored_rows == 2
        assert len(report.column_stats) == 1
        assert report.column_stats[0].name == "price"
        assert report.column_stats[0].mean_change_pct == pytest.approx(10.0, abs=0.1)
        assert report.is_first_deploy is False

    def test_mismatched_lengths_truncates(self) -> None:
        stg = [{"price": 110.0}, {"price": 220.0}, {"price": 330.0}]
        prd = [{"price": 100.0}, {"price": 200.0}]
        inp = pl.DataFrame({"region": ["A", "B", "C"]})
        report = build_report(
            stg,
            prd,
            inp,
            pipeline_name="test",
            staging_endpoint="s",
            prod_endpoint="p",
            dataset_path="d",
            total_rows=3,
        )
        assert report.scored_rows == 2

    def test_mixed_changes_captured(self) -> None:
        prd = [{"price": 100.0}, {"price": 100.0}]
        stg = [{"price": 130.0}, {"price": 105.0}]  # +30%, +5%
        inp = pl.DataFrame({"x": ["a", "b"]})
        report = build_report(
            stg,
            prd,
            inp,
            pipeline_name="test",
            staging_endpoint="s",
            prod_endpoint="p",
            dataset_path="d",
            total_rows=2,
        )
        assert report.column_stats[0].max_increase_pct == pytest.approx(30.0, abs=0.1)


class TestScoreEndpointBatched:
    """Batched scoring via mock WorkspaceClient."""

    def test_batches_correctly(self) -> None:
        from unittest.mock import MagicMock

        def _side_effect(*, name, dataframe_records):
            """Return one prediction per input record so merged result is verifiable."""
            resp = MagicMock()
            resp.predictions = [{"price": float(r["x"])} for r in dataframe_records]
            return resp

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.query.side_effect = _side_effect

        records = [{"x": i} for i in range(5)]
        preds = score_endpoint_batched(mock_ws, "ep", records, batch_size=2)

        # 5 records / 2 per batch = ceil(5/2) = 3 calls
        assert mock_ws.serving_endpoints.query.call_count == 3
        # All 5 input records must produce 5 predictions
        assert len(preds) == 5
        # Verify predictions correspond to correct input values (order preserved)
        assert preds[0]["price"] == 0.0
        assert preds[1]["price"] == 1.0
        assert preds[4]["price"] == 4.0

    def test_non_list_predictions_appended(self) -> None:
        """When predictions is not a list, it should be appended as a single item."""
        from unittest.mock import MagicMock

        def _side_effect(*, name, dataframe_records):
            resp = MagicMock()
            resp.predictions = 42.0  # scalar, not a list
            return resp

        mock_ws = MagicMock()
        mock_ws.serving_endpoints.query.side_effect = _side_effect

        records = [{"x": i} for i in range(3)]
        preds = score_endpoint_batched(mock_ws, "ep", records, batch_size=2)

        assert mock_ws.serving_endpoints.query.call_count == 2
        assert preds == [42.0, 42.0]


def _make_impact_report(**overrides) -> ImpactReport:
    """Build an ImpactReport with sensible defaults for formatting tests."""
    defaults = dict(
        pipeline_name="test-model",
        staging_endpoint="test-model-staging",
        prod_endpoint="test-model",
        dataset_path="data/policies.parquet",
        total_rows=100000,
        sampled_rows=10000,
        scored_rows=10000,
        failed_rows=0,
        column_stats=[
            ColumnStats(
                name="price",
                n_rows=10000,
                n_changed=8000,
                mean_change_pct=2.3,
                median_change_pct=1.8,
                max_increase_pct=18.7,
                max_decrease_pct=-4.2,
                p5=-2.1,
                p25=0.5,
                p75=3.4,
                p95=7.2,
                staging_mean=548.57,
                prod_mean=536.12,
                total_premium_change_pct=2.3,
            )
        ],
        segments={},
        is_first_deploy=False,
    )
    defaults.update(overrides)
    return ImpactReport(**defaults)


class TestFormatTerminal:
    """Terminal report formatting."""

    def test_contains_key_metrics(self) -> None:
        report = _make_impact_report(
            segments={
                "Region": [
                    SegmentRow("Ile-de-France", 2341, 4.1, 572.34, 549.87),
                    SegmentRow("Picardie", 423, -0.3, 501.23, 502.78),
                ]
            },
        )
        text = format_terminal(report)
        assert "IMPACT REPORT" in text
        assert "test-model" in text
        assert "+2.3%" in text
        assert "+18.7%" in text
        assert "-4.2%" in text
        assert "Ile-de-France" in text

    def test_first_deploy(self) -> None:
        report = _make_impact_report(is_first_deploy=True, column_stats=[], segments={})
        text = format_terminal(report)
        assert "First deployment" in text


class TestFormatMarkdown:
    """Markdown report formatting (GitHub Step Summary)."""

    def test_contains_markdown_tables(self) -> None:
        report = _make_impact_report()
        md = format_markdown(report)
        assert "# Impact Report" in md
        assert "| Metric | Value |" in md
        assert "| P5 | P25 | P50 | P75 | P95 |" in md

    def test_first_deploy_markdown(self) -> None:
        report = _make_impact_report(is_first_deploy=True, column_stats=[], segments={})
        md = format_markdown(report)
        assert "First deployment" in md
        assert "| Metric" not in md

    def test_segment_table(self) -> None:
        report = _make_impact_report(
            segments={
                "Region": [
                    SegmentRow("North", 500, 3.2, 110.0, 106.6),
                ]
            }
        )
        md = format_markdown(report)
        assert "### Segment: Region" in md
        assert "North" in md
