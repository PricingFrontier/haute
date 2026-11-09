"""The deploy impact report.

Impact analysis scores in batches against local and HTTP endpoints, turns predictions into frames,
computes column statistics and segment breakdowns, and formats the report for the terminal and
Markdown.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ===========================================================================
# 6. _impact.py — analysis helpers
# ===========================================================================


class TestRunBatched:
    """Tests for _run_batched."""

    def test_basic_batching(self):
        from haute.deploy._impact import _run_batched

        records = list(range(10))
        results = _run_batched(records, lambda batch: batch, batch_size=3, progress=None)
        assert results == list(range(10))

    def test_batch_size_larger_than_records(self):
        from haute.deploy._impact import _run_batched

        records = [1, 2, 3]
        results = _run_batched(records, lambda batch: batch, batch_size=100, progress=None)
        assert results == [1, 2, 3]

    def test_a_scalar_answers_a_one_quote_batch_only(self):
        """DEP-R11: a scalar is one quote's prediction; for two quotes it is refused."""
        from haute.deploy._impact import ImpactAlignmentError, _run_batched

        assert _run_batched([3], lambda batch: sum(batch), batch_size=2, progress=None) == [3]
        with pytest.raises(ImpactAlignmentError, match="single value"):
            _run_batched([1, 2, 3], lambda batch: sum(batch), batch_size=2, progress=None)

    def test_progress_called(self):
        from haute.deploy._impact import _run_batched

        messages = []
        records = list(range(5))
        _run_batched(records, lambda b: b, batch_size=2, progress=messages.append)
        assert len(messages) == 3  # ceil(5/2) = 3

    def test_empty_records(self):
        from haute.deploy._impact import _run_batched

        results = _run_batched([], lambda b: b, batch_size=10, progress=None)
        assert results == []


class TestPredsToDF:
    """Tests for _preds_to_df."""

    def test_dict_predictions(self):
        from haute.deploy._impact import _preds_to_df

        preds = [{"premium": 100.0}, {"premium": 200.0}]
        df = _preds_to_df(preds)
        assert "premium" in df.columns
        assert len(df) == 2

    def test_list_predictions(self):
        from haute.deploy._impact import _preds_to_df

        preds = [[1.0, 2.0], [3.0, 4.0]]
        df = _preds_to_df(preds)
        assert len(df) == 2
        assert len(df.columns) == 2
        assert df.columns == ["output_0", "output_1"]

    def test_scalar_predictions(self):
        from haute.deploy._impact import _preds_to_df

        preds = [100.0, 200.0, 300.0]
        df = _preds_to_df(preds)
        assert "prediction" in df.columns
        assert len(df) == 3

    def test_empty_predictions(self):
        from haute.deploy._impact import _preds_to_df

        df = _preds_to_df([])
        assert len(df) == 0


class TestColumnStats:
    """Tests for _column_stats."""

    def test_basic_stats(self):
        from haute.deploy._impact import _column_stats

        stg = pl.Series([110.0, 220.0, 330.0])
        prd = pl.Series([100.0, 200.0, 300.0])

        stats = _column_stats(stg, prd, "premium")

        assert stats.name == "premium"
        assert stats.n_rows == 3
        assert stats.n_changed == 3  # all changed
        assert stats.mean_change_pct > 0  # staging is higher
        assert stats.staging_mean > stats.prod_mean

    def test_identical_values(self):
        from haute.deploy._impact import _column_stats

        stg = pl.Series([100.0, 200.0])
        prd = pl.Series([100.0, 200.0])

        stats = _column_stats(stg, prd, "premium")

        assert stats.n_changed == 0
        assert abs(stats.mean_change_pct) < 1e-6

    def test_total_premium_change_pct(self):
        from haute.deploy._impact import _column_stats

        stg = pl.Series([110.0])
        prd = pl.Series([100.0])

        stats = _column_stats(stg, prd, "premium")

        assert abs(stats.total_premium_change_pct - 10.0) < 0.1


class TestBuildReport:
    """Tests for build_report."""

    def test_basic_report(self):
        from haute.deploy._impact import build_report

        stg = [{"premium": 110.0}, {"premium": 220.0}]
        prd = [{"premium": 100.0}, {"premium": 200.0}]
        input_df = pl.DataFrame({"age": [25, 30]})

        report = build_report(
            stg,
            prd,
            input_df,
            pipeline_name="test",
            staging_endpoint="stg-ep",
            prod_endpoint="prod-ep",
            dataset_path="data.parquet",
            total_rows=2,
        )

        assert report.pipeline_name == "test"
        assert report.sampled_rows == 2
        assert len(report.column_stats) >= 1
        assert report.column_stats[0].name == "premium"

    def test_a_count_other_than_the_quotes_is_refused(self):
        """DEP-R11: one endpoint answering fewer rows never shifts the pairs."""
        from haute.deploy._impact import ImpactAlignmentError, build_report

        stg = [{"premium": 110.0}, {"premium": 220.0}, {"premium": 330.0}]
        prd = [{"premium": 100.0}, {"premium": 200.0}]
        input_df = pl.DataFrame({"age": [25, 30, 35]})

        with pytest.raises(ImpactAlignmentError, match="staging answered 3 and production 2"):
            build_report(
                stg,
                prd,
                input_df,
                pipeline_name="test",
                staging_endpoint="stg",
                prod_endpoint="prod",
                dataset_path="d.parquet",
                total_rows=3,
            )

    def test_empty_predictions(self):
        from haute.deploy._impact import build_report

        input_df = pl.DataFrame({"age": []}, schema={"age": pl.Int64})
        report = build_report(
            [],
            [],
            input_df,
            pipeline_name="test",
            staging_endpoint="stg",
            prod_endpoint="prod",
            dataset_path="d.parquet",
            total_rows=0,
        )

        assert report.sampled_rows == 0
        assert len(report.column_stats) == 0


class TestFormatTerminal:
    """Tests for format_terminal()."""

    def test_basic_format(self):
        from haute.deploy._impact import ColumnStats, ImpactReport, format_terminal

        stats = ColumnStats(
            name="premium",
            n_rows=100,
            n_changed=50,
            n_from_zero=0,
            mean_change_pct=5.0,
            median_change_pct=4.0,
            max_increase_pct=20.0,
            max_decrease_pct=-10.0,
            p5=-8.0,
            p25=-2.0,
            p75=8.0,
            p95=15.0,
            staging_mean=110.0,
            prod_mean=100.0,
            total_premium_change_pct=10.0,
        )

        report = ImpactReport(
            pipeline_name="motor-pricing",
            staging_endpoint="stg-ep",
            prod_endpoint="prod-ep",
            dataset_path="data.parquet",
            total_rows=100,
            sampled_rows=100,
            column_stats=[stats],
            segments={},
        )

        output = format_terminal(report)

        assert "motor-pricing" in output
        assert "premium" in output
        assert "IMPACT REPORT" in output

    def test_first_deploy_message(self):
        from haute.deploy._impact import ImpactReport, format_terminal

        report = ImpactReport(
            pipeline_name="test",
            staging_endpoint="stg",
            prod_endpoint="prod",
            dataset_path="d.parquet",
            total_rows=10,
            sampled_rows=10,
            column_stats=[],
            segments={},
            first_deploy="no production release is recorded.",
        )

        output = format_terminal(report)
        assert "First deployment" in output

    def test_sampled_rows_indicator(self):
        from haute.deploy._impact import ImpactReport, format_terminal

        report = ImpactReport(
            pipeline_name="test",
            staging_endpoint="stg",
            prod_endpoint="prod",
            dataset_path="d.parquet",
            total_rows=1000,
            sampled_rows=100,
            column_stats=[],
            segments={},
        )

        output = format_terminal(report)
        assert "100 of 1,000 sampled" in output


class TestFormatMarkdown:
    """Tests for format_markdown()."""

    def test_basic_markdown(self):
        from haute.deploy._impact import ColumnStats, ImpactReport, format_markdown

        stats = ColumnStats(
            name="premium",
            n_rows=100,
            n_changed=50,
            n_from_zero=0,
            mean_change_pct=5.0,
            median_change_pct=4.0,
            max_increase_pct=20.0,
            max_decrease_pct=-10.0,
            p5=-8.0,
            p25=-2.0,
            p75=8.0,
            p95=15.0,
            staging_mean=110.0,
            prod_mean=100.0,
            total_premium_change_pct=10.0,
        )

        report = ImpactReport(
            pipeline_name="motor-pricing",
            staging_endpoint="stg-ep",
            prod_endpoint="prod-ep",
            dataset_path="data.parquet",
            total_rows=100,
            sampled_rows=100,
            column_stats=[stats],
            segments={},
        )

        output = format_markdown(report)

        assert "# Impact Report" in output
        assert "motor-pricing" in output
        assert "premium" in output
        assert "|" in output  # markdown table

    def test_first_deploy_markdown(self):
        from haute.deploy._impact import ImpactReport, format_markdown

        report = ImpactReport(
            pipeline_name="test",
            staging_endpoint="stg",
            prod_endpoint="prod",
            dataset_path="d.parquet",
            total_rows=10,
            sampled_rows=10,
            column_stats=[],
            segments={},
            first_deploy="no production release is recorded.",
        )

        output = format_markdown(report)
        assert "First deployment" in output
        assert "# Impact Report" in output


class TestSegmentBreakdown:
    """Tests for _segment_breakdown."""

    def test_no_categorical_columns(self):
        from haute.deploy._impact import _segment_breakdown

        stg_df = pl.DataFrame({"premium": [100.0, 200.0]})
        prd_df = pl.DataFrame({"premium": [100.0, 200.0]})
        input_df = pl.DataFrame({"x": [1.0, 2.0]})  # all numeric

        result = _segment_breakdown(stg_df, prd_df, input_df, "premium")
        assert result == {}

    def test_output_col_not_in_staging(self):
        from haute.deploy._impact import _segment_breakdown

        stg_df = pl.DataFrame({"other": [100.0]})
        prd_df = pl.DataFrame({"other": [100.0]})
        input_df = pl.DataFrame({"area": ["A"]})

        result = _segment_breakdown(stg_df, prd_df, input_df, "premium")
        assert result == {}


class TestScoreEndpointBatched:
    """Tests for score_endpoint_batched (mocked)."""

    def test_calls_ws_query(self):
        from haute.deploy._impact import score_endpoint_batched

        mock_ws = MagicMock()
        mock_resp = MagicMock()
        mock_resp.predictions = [100.0, 200.0]
        mock_ws.serving_endpoints.query.return_value = mock_resp

        records = [{"x": 1}, {"x": 2}]
        results = score_endpoint_batched(mock_ws, "my-ep", records, batch_size=10)

        assert results == [100.0, 200.0]
        mock_ws.serving_endpoints.query.assert_called_once()

    def test_batches_correctly(self):
        from haute.deploy._impact import score_endpoint_batched

        mock_ws = MagicMock()

        def fake_query(name, dataframe_records):
            resp = MagicMock()
            resp.predictions = [42.0] * len(dataframe_records)
            return resp

        mock_ws.serving_endpoints.query.side_effect = fake_query

        records = [{"x": i} for i in range(5)]
        results = score_endpoint_batched(mock_ws, "ep", records, batch_size=2)

        assert len(results) == 5
        assert mock_ws.serving_endpoints.query.call_count == 3  # 2+2+1


class TestScoreHttpEndpointBatched:
    """Tests for score_http_endpoint_batched (mocked)."""

    def test_sends_post_to_quote_endpoint(self):
        from haute.deploy._impact import score_http_endpoint_batched

        records = [{"x": 1}]

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps([{"premium": 100.0}]).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            results = score_http_endpoint_batched(
                "http://localhost:8080",
                records,
                batch_size=10,
            )

        assert results == [{"premium": 100.0}]

    def test_http_error_raises(self):
        from haute.deploy._impact import score_http_endpoint_batched

        records = [{"x": 1}]

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_err = urllib.error.HTTPError(
                url="http://localhost:8080/quote",
                code=500,
                msg="Server Error",
                hdrs=None,
                fp=None,
            )
            mock_err.read = lambda: b"internal error"
            mock_urlopen.side_effect = mock_err

            with pytest.raises(RuntimeError, match="HTTP 500"):
                score_http_endpoint_batched(
                    "http://localhost:8080",
                    records,
                    batch_size=10,
                )


# ===========================================================================
# 7. Formatter helpers
# ===========================================================================


class TestFormatHelpers:
    """Tests for _fmt_pct, _fmt_num, _fmt_int."""

    def test_fmt_pct_positive(self):
        from haute.deploy._impact import _fmt_pct

        assert _fmt_pct(5.123) == "+5.1%"

    def test_fmt_pct_negative(self):
        from haute.deploy._impact import _fmt_pct

        assert _fmt_pct(-3.678) == "-3.7%"

    def test_fmt_pct_zero(self):
        from haute.deploy._impact import _fmt_pct

        assert _fmt_pct(0.0) == "0.0%"

    def test_fmt_num(self):
        from haute.deploy._impact import _fmt_num

        assert _fmt_num(1234.5) == "1,234.50"

    def test_fmt_int(self):
        from haute.deploy._impact import _fmt_int

        assert _fmt_int(1000000) == "1,000,000"
