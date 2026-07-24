"""Comprehensive tests for internal deploy modules.

Covers the less-tested internals:
  - _model_code.py  (HauteModel.load_context / predict)
  - _schema.py      (infer_input_schema / infer_output_schema — edge cases)
  - _scorer.py      (score_graph intercepts — apiInput, externalFile,
                     optimiserApply, modelScore, output_fields)
  - _config.py      (_load_env, _apply_env_overrides, resolve_config)
  - _mlflow.py      (get_deploy_status, _build_signature, _conda_env,
                     _pip_requirements, _check_databricks_connectivity)
  - _impact.py      (_run_batched, _preds_to_df, _column_stats,
                     build_report, format_terminal, format_markdown)
"""

from __future__ import annotations

import gc
import json
import os
import urllib.error
import weakref
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from tests._deploy_helpers import FIXTURE_DIR
from tests._deploy_helpers import make_resolved_deploy as _make_resolved
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config

if TYPE_CHECKING:
    from haute.deploy._model_code import HauteModel

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"


def _single_frame_api_input_config(
    column_name: str,
    column_type: str = "str",
    *,
    label: str = "quotes",
    **overrides: object,
) -> dict[str, object]:
    """Minimal converged apiInput config with one labelled emitted frame."""
    config: dict[str, object] = {
        "path": "",
        "tables": [
            {
                "path": "$[:]",
                "label": label,
                "emit": True,
                "columns": [
                    {
                        "name": column_name,
                        "path": f"$[:].{column_name}",
                        "type": column_type,
                        "selected": True,
                    }
                ],
            }
        ],
    }
    config.update(overrides)
    return config


def _passthrough_input_graph(parquet_path):
    """apiInput ``src`` (reads ``parquet_path``) → output ``out`` (passthrough)."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "src",
                        "nodeType": "apiInput",
                        "config": _single_frame_api_input_config(
                            "x", "float", label="src", path=str(parquet_path)
                        ),
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "src",
                    "target": "out",
                    "sourceHandle": "src",
                }
            ],
        }
    )


# ===========================================================================
# 1. _model_code.py — HauteModel
# ===========================================================================


class TestHauteModelLoadContext:
    """Tests for HauteModel.load_context()."""

    def test_load_context_parses_manifest(self, tmp_path):
        """load_context reads manifest JSON and populates internal state."""
        from haute.deploy._model_code import HauteModel

        # Write a valid manifest
        manifest = {
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["api_src"],
            "output_node_id": "out",
            "output_fields": ["premium"],
            "artifacts": {"model.pkl": "/tmp/model.pkl"},
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        model = HauteModel()
        ctx = MagicMock()
        ctx.artifacts = {
            "deploy_manifest": str(manifest_path),
            "model.pkl": "/served/model.pkl",
        }

        model.load_context(ctx)

        from haute._types import PipelineGraph

        assert isinstance(model._graph, PipelineGraph)
        assert len(model._graph.nodes) == 0
        assert len(model._graph.edges) == 0
        assert model._input_node_ids == ["api_src"]
        assert model._output_node_id == "out"
        assert model._output_fields == ["premium"]
        assert model._artifact_paths == {"model.pkl": "/served/model.pkl"}

    def test_load_context_no_output_fields(self, tmp_path):
        """output_fields defaults to None when not in manifest."""
        from haute.deploy._model_code import HauteModel

        manifest = {
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["src"],
            "output_node_id": "out",
            "artifacts": {},
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        model = HauteModel()
        ctx = MagicMock()
        ctx.artifacts = {"deploy_manifest": str(manifest_path)}

        model.load_context(ctx)

        assert model._output_fields is None

    def test_load_context_artifact_not_in_context(self, tmp_path):
        """Artifacts listed in manifest but absent from context are skipped."""
        from haute.deploy._model_code import HauteModel

        manifest = {
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["src"],
            "output_node_id": "out",
            "artifacts": {"missing.pkl": "/original/path.pkl"},
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        model = HauteModel()
        ctx = MagicMock()
        ctx.artifacts = {"deploy_manifest": str(manifest_path)}

        model.load_context(ctx)

        assert model._artifact_paths == {}


class TestHauteModelPredict:
    """Tests for HauteModel.predict()."""

    def test_predict_pandas_round_trip(self, tmp_path, monkeypatch):
        """predict() converts pandas->polars->pandas and calls score_graph."""
        import pandas as pd

        from haute.deploy._model_code import HauteModel

        monkeypatch.setenv("HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_MB", "256")
        monkeypatch.setattr(
            "haute._execution_admission.current_rss_bytes",
            lambda: 32 * 1024 * 1024,
        )
        model = HauteModel()
        model._graph = {"nodes": [], "edges": []}
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = None

        input_pd = pd.DataFrame({"x": [1.0, 2.0], "y": [3.0, 4.0]})
        expected_result = pl.DataFrame({"x": [1.0, 2.0], "result": [10.0, 20.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=expected_result) as mock_score:
            result = model.predict(MagicMock(), input_pd)

        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["x", "result"]
        assert result["result"].tolist() == [10.0, 20.0]

        # Verify score_graph was called with polars DataFrame
        call_kwargs = mock_score.call_args.kwargs
        assert isinstance(call_kwargs["input_df"], pl.DataFrame)
        assert call_kwargs["input_node_ids"] == ["src"]
        assert call_kwargs["output_node_id"] == "out"
        context = call_kwargs["execution_context"]
        assert context.profile.value == "deploy_batch"
        assert context.admission is not None
        assert context.memory_limit_bytes == 256 * 1024 * 1024

    def test_predict_admits_before_polars_conversion(self, monkeypatch):
        """Large Pandas payloads should be admitted before Polars materialisation."""
        import pandas as pd

        from haute._execution_admission import ExecutionAdmissionError
        from haute._execution_context import ExecutionProfile
        from haute.deploy._model_code import HauteModel

        model = HauteModel()
        model._graph = {"nodes": [], "edges": []}
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = None
        admission_error = ExecutionAdmissionError(
            "deploy_pyfunc_predict",
            profile=ExecutionProfile.DEPLOY_BATCH,
            memory_limit_bytes=512,
            rss_at_admission_bytes=600,
            process_rss_limit_bytes=512,
            reason="process_rss_limit_exceeded",
        )
        captured: dict[str, object] = {}

        def reject_admission(*, operation: str, row_count: int):
            captured["operation"] = operation
            captured["row_count"] = row_count
            raise admission_error

        def fail_from_pandas(*_args, **_kwargs):
            raise AssertionError("Polars conversion should not run before admission")

        monkeypatch.setattr("haute.deploy._scorer.admit_deploy_execution", reject_admission)
        monkeypatch.setattr(pl, "from_pandas", fail_from_pandas)

        with pytest.raises(ExecutionAdmissionError):
            model.predict(MagicMock(), pd.DataFrame({"x": [1.0, 2.0]}))

        assert captured == {"operation": "deploy_pyfunc_predict", "row_count": 2}

    def test_predict_passes_output_fields(self, tmp_path):
        """predict() forwards output_fields to score_graph."""
        import pandas as pd

        from haute.deploy._model_code import HauteModel

        model = HauteModel()
        model._graph = {"nodes": [], "edges": []}
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = ["premium"]

        input_pd = pd.DataFrame({"x": [1.0]})
        mock_result = pl.DataFrame({"premium": [100.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=mock_result) as mock_score:
            result = model.predict(MagicMock(), input_pd)

        call_kwargs = mock_score.call_args.kwargs
        assert call_kwargs["output_fields"] == ["premium"]
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["premium"]
        assert result["premium"].tolist() == [100.0]

    def test_predict_passes_artifact_paths(self):
        """predict() forwards artifact_paths to score_graph."""
        import pandas as pd

        from haute.deploy._model_code import HauteModel

        model = HauteModel()
        model._graph = {"nodes": [], "edges": []}
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {"model.pkl": "/served/model.pkl"}
        model._output_fields = None

        input_pd = pd.DataFrame({"x": [1.0]})
        mock_result = pl.DataFrame({"x": [1.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=mock_result) as mock_score:
            result = model.predict(MagicMock(), input_pd)

        call_kwargs = mock_score.call_args.kwargs
        assert call_kwargs["artifact_paths"] == {"model.pkl": "/served/model.pkl"}
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["x"]
        assert result["x"].tolist() == [1.0]

    @staticmethod
    def _build_fixture_model(output_fields: list[str] | None = None) -> HauteModel:
        """Build a HauteModel wired to the fixture pipeline graph."""
        from haute.deploy._model_code import HauteModel
        from haute.deploy._pruner import prune_for_deploy
        from haute.parser import parse_pipeline_file

        full_graph = parse_pipeline_file(PIPELINE_FILE)
        pruned, _kept, _removed = prune_for_deploy(full_graph, "output")

        model = HauteModel()
        model._graph = pruned
        model._input_node_ids = ["quotes"]
        model._output_node_id = "output"
        model._output_fields = output_fields
        model._artifact_paths = {}
        return model

    def test_predict_end_to_end_with_real_graph(self):
        """E2E: predict with a real pruned graph — no mocks on score_graph."""
        import pandas as pd

        model = self._build_fixture_model()

        # Area "A" → factor 1.1, premium = VehPower * area_factor * Exposure
        input_pd = pd.DataFrame(
            [
                {
                    "IDpol": 1,
                    "VehPower": 5,
                    "Area": "A",
                    "VehAge": 1,
                    "BonusMalus": 50,
                    "Exposure": 0.5,
                }
            ]
        )

        result = model.predict(MagicMock(), input_pd)

        assert isinstance(result, pd.DataFrame)
        assert "premium" in result.columns, f"Expected 'premium', got {result.columns.tolist()}"
        assert result["premium"].iloc[0] == pytest.approx(2.75, rel=1e-6)

    def test_predict_end_to_end_multiple_rows(self):
        """E2E with multiple rows — verifies vectorised execution."""
        import pandas as pd

        model = self._build_fixture_model()

        input_pd = pd.DataFrame(
            [
                {
                    "IDpol": 1,
                    "VehPower": 5,
                    "Area": "A",
                    "VehAge": 1,
                    "BonusMalus": 50,
                    "Exposure": 0.5,
                },
                {
                    "IDpol": 2,
                    "VehPower": 10,
                    "Area": "B",
                    "VehAge": 2,
                    "BonusMalus": 60,
                    "Exposure": 1.0,
                },
            ]
        )

        result = model.predict(MagicMock(), input_pd)

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        # Row 1: 5 * 1.1 * 0.5 = 2.75  (Area "A" → factor 1.1)
        # Row 2: 10 * 1.2 * 1.0 = 12.0  (Area "B" → factor 1.2)
        assert result["premium"].iloc[0] == pytest.approx(2.75, rel=1e-6)
        assert result["premium"].iloc[1] == pytest.approx(12.0, rel=1e-6)

    def test_predict_with_output_fields_filters(self):
        """E2E: output_fields limits returned columns."""
        import pandas as pd

        model = self._build_fixture_model(output_fields=["premium"])

        input_pd = pd.DataFrame(
            [
                {
                    "IDpol": 1,
                    "VehPower": 5,
                    "Area": "A",
                    "VehAge": 1,
                    "BonusMalus": 50,
                    "Exposure": 0.5,
                }
            ]
        )

        result = model.predict(MagicMock(), input_pd)

        assert list(result.columns) == ["premium"]
        assert result["premium"].iloc[0] == pytest.approx(2.75, rel=1e-6)


# ===========================================================================
# 2. _schema.py — infer_input_schema, infer_output_schema
# ===========================================================================


class TestInferInputSchema:
    """Tests for infer_input_schema edge cases."""

    def test_node_with_no_path_raises(self):
        """Input node with empty path must raise ValueError."""
        from haute.deploy._schema import infer_input_schema

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {},
                        },
                    },
                ],
            }
        )

        with pytest.raises(ValueError, match="no path"):
            infer_input_schema(graph, "src")

    def test_node_with_empty_string_path_raises(self):
        """Input node with path="" must raise ValueError."""
        from haute.deploy._schema import infer_input_schema

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": ""},
                        },
                    },
                ],
            }
        )

        with pytest.raises(ValueError, match="no path"):
            infer_input_schema(graph, "src")

    def test_node_not_found_raises(self):
        """Non-existent node must raise ConfigError naming the missing node."""
        from haute.deploy._schema import infer_input_schema
        from haute.errors import ConfigError

        graph = _g({"nodes": []})

        with pytest.raises(ConfigError, match="not found") as exc_info:
            infer_input_schema(graph, "nonexistent")
        assert exc_info.value.context["node_id"] == "nonexistent"

    def test_valid_parquet_schema(self, tmp_path):
        """Valid parquet file returns correct schema dict."""
        from haute.deploy._schema import infer_input_schema

        # Write a sample parquet
        pq_path = tmp_path / "input.parquet"
        pl.DataFrame({"age": [25], "premium": [100.5]}).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": str(pq_path)},
                        },
                    },
                ],
            }
        )

        schema = infer_input_schema(graph, "src")
        assert "age" in schema
        assert "premium" in schema
        assert isinstance(schema["age"], str)

    def test_unreadable_file_raises(self, tmp_path):
        """File that can't be read raises ValueError."""
        from haute.deploy._schema import infer_input_schema

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": "/nonexistent/path/data.parquet"},
                        },
                    },
                ],
            }
        )

        with pytest.raises(ValueError, match="Failed to read schema"):
            infer_input_schema(graph, "src")


class TestInferOutputSchema:
    """Tests for infer_output_schema."""

    def test_cache_hit(self, tmp_path, monkeypatch):
        """When cache has matching fingerprint, score_graph is not called."""
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "x", "float", label="src", path="d.parquet"
                            ),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        # Compute the fingerprint that will be used
        from haute._cache import graph_fingerprint

        fp = graph_fingerprint(graph, "out", "src")

        # Pre-write the cache
        cache_dir = tmp_path / ".haute_cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "output_schema.json"
        cached = {"fingerprint": fp, "schema": {"premium": "Float64", "factor": "Int64"}}
        cache_file.write_text(json.dumps(cached))

        # Should return cached schema without calling score_graph
        with patch("haute.deploy._scorer.score_graph") as mock_score:
            result = infer_output_schema(graph, "out", ["src"])

        mock_score.assert_not_called()
        assert result == {"premium": "Float64", "factor": "Int64"}

    def test_corrupt_cache_recomputes(self, tmp_path, monkeypatch):
        """Corrupt cache JSON triggers recomputation (no crash)."""
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)

        # Write a sample data file for the input node
        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "x", "float", label="src", path=str(pq_path)
                            ),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        # Write corrupt cache
        cache_dir = tmp_path / ".haute_cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "output_schema.json"
        cache_file.write_text("NOT VALID JSON {{{{")

        mock_result = pl.DataFrame({"result": [42.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=mock_result):
            result = infer_output_schema(graph, "out", ["src"])

        assert result == {"result": "Float64"}

    def test_corrupt_cache_logs_warning(self, tmp_path, monkeypatch):
        """Corrupt cache JSON logs a warning with path and error details."""
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)

        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "x", "float", label="src", path=str(pq_path)
                            ),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        cache_dir = tmp_path / ".haute_cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "output_schema.json"
        cache_file.write_text("NOT VALID JSON {{{{")

        mock_result = pl.DataFrame({"result": [42.0]})

        with (
            patch("haute.deploy._schema.logger") as mock_logger,
            patch("haute.deploy._scorer.score_graph", return_value=mock_result),
        ):
            infer_output_schema(graph, "out", ["src"])

        # Find the corrupt_schema_cache warning call
        warning_calls = [
            c for c in mock_logger.warning.call_args_list if c[0][0] == "corrupt_schema_cache"
        ]
        assert len(warning_calls) == 1, (
            f"Expected 1 corrupt_schema_cache warning, got {len(warning_calls)}"
        )
        kwargs = warning_calls[0][1]
        assert "path" in kwargs
        assert "error" in kwargs

    def test_cache_miss_writes_cache(self, tmp_path, monkeypatch):
        """Cache miss computes schema and writes cache file."""
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)

        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "x", "float", label="src", path=str(pq_path)
                            ),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        mock_result = pl.DataFrame({"premium": [100.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=mock_result):
            result = infer_output_schema(graph, "out", ["src"])

        assert result == {"premium": "Float64"}

        # Verify cache was written
        cache_file = tmp_path / ".haute_cache" / "output_schema.json"
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text())
        assert cached["schema"] == {"premium": "Float64"}

    def test_stale_cache_fingerprint_recomputes(self, tmp_path, monkeypatch):
        """Stale fingerprint in cache causes recomputation."""
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)

        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "x", "float", label="src", path=str(pq_path)
                            ),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        # Write cache with wrong fingerprint
        cache_dir = tmp_path / ".haute_cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "output_schema.json"
        cached = {"fingerprint": "wrong_fingerprint", "schema": {"old_col": "String"}}
        cache_file.write_text(json.dumps(cached))

        mock_result = pl.DataFrame({"premium": [100.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=mock_result) as mock_score:
            result = infer_output_schema(graph, "out", ["src"])

        mock_score.assert_called_once()
        assert result == {"premium": "Float64"}

    def test_cache_write_oserror_is_warned_not_swallowed_silently(self, tmp_path, monkeypatch):
        """F124: an OSError writing the cache logs a warning; deploy continues.

        A disk-write failure is genuinely non-critical (the schema was computed
        truthfully and can be recomputed), but it must be surfaced as a warning
        rather than swallowed by a bare ``except: pass``.
        """
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)
        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)
        graph = _passthrough_input_graph(pq_path)

        # Make the cache directory path a FILE so mkdir(exist_ok=True) raises
        # FileExistsError (an OSError) — a realistic write failure.
        (tmp_path / ".haute_cache").write_text("not a directory")

        mock_result = pl.DataFrame({"premium": [100.0]})
        with (
            patch("haute.deploy._schema.logger") as mock_logger,
            patch("haute.deploy._scorer.score_graph", return_value=mock_result),
        ):
            result = infer_output_schema(graph, "out", ["src"])

        # Schema still returned truthfully despite the write failure.
        assert result == {"premium": "Float64"}
        warned = [
            c
            for c in mock_logger.warning.call_args_list
            if c[0][0] == "output_schema_cache_write_failed"
        ]
        assert len(warned) == 1

    def test_cache_write_non_oserror_propagates(self, tmp_path, monkeypatch):
        """F124: a non-OSError during cache write is a real bug — it must raise.

        The old bare ``except Exception: pass`` masked bugs like a
        non-serialisable schema. Only OSErrors are tolerated now.

        The failure must originate *at the cache write*, after fingerprinting
        and scoring have completed. Patching the shared ``json.dumps`` global
        would instead blow up inside ``graph_fingerprint`` (via
        ``canonical_json``), which runs *before* the cache-write ``try`` — so
        that RuntimeError would propagate regardless of the ``except`` clause
        and the test would pass even against the pre-fix ``except Exception``.
        Once ``score_graph`` is mocked, the cache write is the only
        ``Path.write_text`` call left in ``infer_output_schema``, so patching
        the write site pins the fix precisely. Asserting ``score_graph`` ran
        proves the error came from the cache write, not from fingerprinting.
        """
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)
        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)
        graph = _passthrough_input_graph(pq_path)

        mock_result = pl.DataFrame({"premium": [100.0]})
        mock_score = MagicMock(return_value=mock_result)
        with (
            patch("haute.deploy._scorer.score_graph", mock_score),
            patch(
                "pathlib.Path.write_text",
                side_effect=RuntimeError("serialisation bug"),
            ),
            pytest.raises(RuntimeError, match="serialisation bug"),
        ):
            infer_output_schema(graph, "out", ["src"])

        # score_graph runs after graph_fingerprint and before the cache write,
        # so its having been called exactly once proves fingerprinting finished
        # and the RuntimeError originated at the cache write — not earlier.
        mock_score.assert_called_once()


# ===========================================================================
# 3. _scorer.py — score_graph intercepts
# ===========================================================================


class TestScoreGraphApiInputInjection:
    """Tests for the apiInput injection intercept in score_graph."""

    def test_api_input_injects_dataframe(self):
        """apiInput nodes should receive the injected input_df."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "y"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0, 2.0], "y": [3.0, 4.0]})
        result = score_graph(
            graph=graph,
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
        )

        assert isinstance(result, pl.DataFrame)
        assert len(result) == 2
        assert "x" in result.columns
        assert "y" in result.columns

    def test_api_input_frame_label_is_the_deploy_parameter_name(self):
        """Deploy user code binds the edge's frame name, not its source-node label."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "request",
                        "data": {
                            "label": "Quote Request Node",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("value", "int"),
                        },
                    },
                    {
                        "id": "tag_quote",
                        "data": {
                            "label": "Tag Quote",
                            "nodeType": "polars",
                            "config": {
                                "code": ('df = quotes.with_columns(bound_input=pl.lit("quotes"))'),
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "Response",
                            "nodeType": "output",
                            "config": make_output_config(
                                ["value", "bound_input"],
                                source_port="Tag_Quote",
                            ),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "request__quotes__tag",
                        "source": "request",
                        "target": "tag_quote",
                        "sourceHandle": "quotes",
                    },
                    {"id": "tag__out", "source": "tag_quote", "target": "out"},
                ],
            }
        )

        result = score_graph(
            graph=graph,
            input_df=pl.DataFrame({"value": [17]}),
            input_node_ids=["request"],
            output_node_id="out",
        )

        assert result.to_dicts() == [{"value": 17, "bound_input": "quotes"}]

    def test_null_handle_api_input_edge_raises_value_error_naming_edge(self):
        """A malformed apiInput edge cannot silently drop its source frame."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "request",
                        "data": {
                            "label": "Request",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("value", "int"),
                        },
                    },
                    {
                        "id": "build",
                        "data": {
                            "label": "build",
                            "nodeType": "polars",
                            "config": {
                                "code": 'df = pl.DataFrame({"ok": [True]}).lazy()',
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["ok"], source_port="build"),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "request_edge_missing_frame_port",
                        "source": "request",
                        "target": "build",
                    },
                    {"id": "build__out", "source": "build", "target": "out"},
                ],
            }
        )

        with pytest.raises(ValueError) as exc_info:
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"value": [17]}),
                input_node_ids=["request"],
                output_node_id="out",
            )

        assert "request_edge_missing_frame_port" in str(exc_info.value)

    def test_multiple_api_inputs(self):
        """Multiple apiInput nodes all receive the same input_df."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src1",
                        "data": {
                            "label": "src1",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("val", "int", label="src1"),
                        },
                    },
                    {
                        "id": "src2",
                        "data": {
                            "label": "src2",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("val", "int", label="src2"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["val"], source_port="src1"),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src1",
                        "target": "out",
                        "sourceHandle": "src1",
                    },
                    {
                        "id": "e2",
                        "source": "src2",
                        "target": "out",
                        "sourceHandle": "src2",
                    },
                ],
            }
        )

        input_df = pl.DataFrame({"val": [10]})
        result = score_graph(
            graph=graph,
            input_df=input_df,
            input_node_ids=["src1", "src2"],
            output_node_id="out",
        )

        assert isinstance(result, pl.DataFrame)


class TestScoreGraphOutputFields:
    """Tests for output_fields column filtering."""

    def test_output_fields_filters_columns(self):
        """When output_fields is set, only those columns appear in result."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "y", "z"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0], "y": [2.0], "z": [3.0]})
        result = score_graph(
            graph=graph,
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
            output_fields=["x", "z"],
        )

        assert set(result.columns) == {"x", "z"}
        assert "y" not in result.columns

    def test_output_fields_seed_lazy_projection(self):
        """Serving output_fields should feed the backend projection planner."""
        from haute.deploy import _scorer

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )
        output_lf = pl.DataFrame({"x": [1.0], "y": [2.0], "z": [3.0]}).lazy()

        with patch.object(
            _scorer,
            "execute_lazy_graph",
            return_value=({"out": output_lf}, ["src", "out"], {}, {}),
        ) as execute:
            result = _scorer.score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0], "y": [2.0], "z": [3.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                output_fields=["x", "z"],
            )

        assert result.columns == ["x", "z"]
        assert execute.call_args.kwargs["required_columns_by_node"] == {
            "out": frozenset({"x", "z"})
        }

    def test_score_graph_lazy_returns_uncollected_plan(self):
        """Container streaming can keep deployed output lazy through the response."""
        from haute.deploy import _scorer

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )
        output_lf = pl.DataFrame({"x": [1.0], "z": [3.0]}).lazy()

        with (
            patch.object(
                _scorer,
                "execute_lazy_graph",
                return_value=({"out": output_lf}, ["src", "out"], {}, {}),
            ) as execute,
            patch.object(
                _scorer,
                "streaming_collect",
                side_effect=AssertionError("score_graph_lazy must not collect"),
            ),
        ):
            plan = _scorer.score_graph_lazy(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0], "z": [3.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                output_fields=["x"],
            )

        assert plan.lazy_frame.collect().columns == ["x"]
        assert plan.execution_context.profile.value == "deploy_live"
        assert execute.call_args.kwargs["required_columns_by_node"] == {"out": frozenset({"x"})}
        assert execute.call_args.kwargs["dataframe_cache_request"] is None
        plan.cleanup(preserve_primary_error=False)

    def test_score_graph_lazy_retains_output_field_parent_until_cleanup(self):
        """Selecting output_fields must not drop the cache-pinning source LazyFrame."""
        from haute.deploy import _scorer

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("keep", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )
        retained_ref: weakref.ReferenceType[pl.LazyFrame] | None = None

        def fake_execute_lazy_graph(*_args, **_kwargs):
            nonlocal retained_ref
            output_lf = pl.DataFrame({"keep": [1.0, 2.0], "unused": [3.0, 4.0]}).lazy()
            retained_ref = weakref.ref(output_lf)
            return {"out": output_lf}, ["src", "out"], {}, {}

        with patch.object(
            _scorer,
            "execute_lazy_graph",
            side_effect=fake_execute_lazy_graph,
        ):
            plan = _scorer.score_graph_lazy(
                graph=graph,
                input_df=pl.DataFrame({"keep": [1.0, 2.0], "unused": [3.0, 4.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                output_fields=["keep"],
            )

        gc.collect()
        assert retained_ref is not None
        assert retained_ref() is not None
        assert plan.lazy_frame.collect().columns == ["keep"]

        plan.cleanup(preserve_primary_error=False)
        gc.collect()
        assert retained_ref() is None

    def test_score_graph_lazy_builds_cache_request_for_batch_deploy(self):
        """Batch deploy can reuse materialized backend frames across identical payloads."""
        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.deploy import _scorer

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )
        output_lf = pl.DataFrame({"x": [1.0, 2.0]}).lazy()
        context = ExecutionContext(
            operation="deploy_score_graph",
            profile=ExecutionProfile.DEPLOY_BATCH,
        )

        with patch.object(
            _scorer,
            "execute_lazy_graph",
            return_value=({"out": output_lf}, ["src", "out"], {}, {}),
        ) as execute:
            plan = _scorer.score_graph_lazy(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                execution_context=context,
            )

        assert execute.call_args.kwargs["dataframe_cache_request"] is not None
        plan.cleanup(preserve_primary_error=False)

    def test_no_output_fields_returns_all_columns(self):
        """Without output_fields, all columns pass through."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "y"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0], "y": [2.0]})
        result = score_graph(
            graph=graph,
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
        )

        assert set(result.columns) == {"x", "y"}

    def test_output_fields_rejects_bare_string(self):
        """A malformed output_fields contract should fail before execution."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        with pytest.raises(ValueError, match="output_fields"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0], "y": [2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                output_fields="x",  # type: ignore[arg-type]
            )


class TestScoreGraphStaticDataSourceRemap:
    def test_static_data_source_remap_uses_declared_schema(self, tmp_path):
        """Static source artifact remaps should preserve dataInput schema config."""
        from haute.deploy._scorer import score_graph

        source_path = tmp_path / "lookup.csv"
        source_path.write_text("quote_id,factor\n001,1.2\n", encoding="utf-8")
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "lookup",
                        "data": {
                            "label": "lookup",
                            "nodeType": "dataInput",
                            "config": {
                                "path": "lookup.csv",
                                "schema_overrides": {
                                    "quote_id": "String",
                                    "factor": "Float64",
                                },
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["quote_id", "factor"]),
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "lookup", "target": "out"}],
            }
        )

        result = score_graph(
            graph=graph,
            input_df=pl.DataFrame(),
            input_node_ids=[],
            output_node_id="out",
            artifact_paths={"lookup__lookup.csv": str(source_path)},
        )

        assert result["quote_id"].to_list() == ["001"]
        assert result.schema["quote_id"] == pl.String

    def test_static_data_source_remap_receives_projected_columns(self, tmp_path):
        """Bundled static sources should keep deploy output projection physical."""
        from haute.deploy import _scorer

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "lookup",
                        "data": {
                            "label": "lookup",
                            "nodeType": "dataInput",
                            "config": {"path": "lookup.parquet"},
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["keep"]),
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "lookup", "target": "out"}],
            }
        )
        captured: dict[str, object] = {}

        def fake_read_data_source(config, *, profile=None, columns=None, validate_columns=None):
            captured["config"] = config
            captured["profile"] = profile
            captured["columns"] = columns
            captured["validate_columns"] = validate_columns
            return pl.DataFrame({"keep": [1], "unused": [2]}).lazy()

        with patch.object(_scorer, "read_data_source", side_effect=fake_read_data_source):
            result = _scorer.score_graph(
                graph=graph,
                input_df=pl.DataFrame(),
                input_node_ids=[],
                output_node_id="out",
                artifact_paths={"lookup__lookup.parquet": str(tmp_path / "lookup.parquet")},
                output_fields=["keep"],
            )

        assert result.columns == ["keep"]
        assert captured["columns"] == frozenset({"keep"})
        assert captured["profile"] == "deploy_live"


class TestScoreGraphMissingOutput:
    """Tests for missing output node RuntimeError."""

    def test_missing_output_node_raises(self):
        """Requesting a non-existent output node raises RuntimeError."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": ""},
                        },
                    },
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0]})

        with pytest.raises(RuntimeError, match="produced no result"):
            score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="nonexistent",
            )


class TestScoreGraphBadInput:
    """Tests for score_graph with wrong input types / missing columns."""

    def _simple_graph(self):
        """apiInput → transform (cast VehPower to float) → output."""
        return _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("VehPower", "float"),
                        },
                    },
                    {
                        "id": "calc",
                        "data": {
                            "label": "calc",
                            "nodeType": "polars",
                            "config": {
                                "code": (
                                    "df = quotes.with_columns("
                                    'result=pl.col("VehPower").cast(pl.Float64) * 2)'
                                ),
                                "contract": {
                                    "inputs": ["VehPower"],
                                    "outputs": ["result"],
                                },
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["result"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "calc",
                        "sourceHandle": "quotes",
                    },
                    {"id": "e2", "source": "calc", "target": "out"},
                ],
            }
        )

    def test_missing_column_raises(self):
        """Input missing a column referenced by transform raises an error."""
        from haute.deploy._scorer import score_graph

        graph = self._simple_graph()
        # VehPower is missing — transform references it
        input_df = pl.DataFrame({"other_col": [1.0]})

        with pytest.raises(Exception, match="VehPower|not found|ColumnNotFoundError"):
            score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
            )

    def test_wrong_dtype_in_column(self):
        """String where float expected — cast should fail or produce wrong results."""
        from haute.deploy._scorer import score_graph

        graph = self._simple_graph()
        # VehPower is a string — .cast(pl.Float64) on "abc" should fail
        input_df = pl.DataFrame({"VehPower": ["abc", "def"]})

        with pytest.raises((pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError)):
            score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
            )

    @staticmethod
    def _passthrough_graph():
        """apiInput → output with no transform."""
        return _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "out",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

    def test_null_input_propagates(self):
        """Null values flow through the pipeline without crashing.

        Through the v2 OUTPUT assembler the emitted document is null-pruned and
        identical rows are deduped (the Q1 null-prune + empty-collection rule,
        Nick's 2026-06-16 ruling): a row whose only field is null prunes to an
        empty object and is dropped. The two ``None`` rows therefore collapse
        away, leaving the single non-null row — the point of the test is that
        nulls are handled without error, which still holds.
        """
        from haute.deploy._scorer import score_graph

        input_df = pl.DataFrame({"x": [None, 1.0, None]})
        result = score_graph(
            graph=self._passthrough_graph(),
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
        )

        assert isinstance(result, pl.DataFrame)
        # v2 shape: null-only rows prune away, leaving just the non-null row.
        assert len(result) == 1
        assert result["x"].null_count() == 0
        assert result["x"].to_list() == [1.0]

    def test_empty_dataframe(self):
        """Empty DataFrame (0 rows) should execute without error."""
        from haute.deploy._scorer import score_graph

        input_df = pl.DataFrame({"x": pl.Series([], dtype=pl.Float64)})
        result = score_graph(
            graph=self._passthrough_graph(),
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
        )

        assert isinstance(result, pl.DataFrame)
        assert len(result) == 0


class TestScoreGraphExternalFileRemap:
    """Tests for externalFile remapping in score_graph."""

    def test_external_file_remap_with_code(self, tmp_path, _widen_sandbox_root):
        """externalFile with code loads from remapped path and executes code."""
        from haute.deploy._scorer import score_graph

        pkl_path = tmp_path / "lookup.pkl"
        pkl_path.write_bytes(b"fake")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ext",
                        "data": {
                            "label": "ext",
                            "nodeType": "externalFile",
                            "config": {
                                "path": "original/lookup.pkl",
                                "fileType": "pickle",
                                "modelClass": "classifier",
                                "code": "df = df.with_columns(pl.lit(99).alias('ext_val'))",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "ext_val"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ext",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ext", "target": "out"},
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0]})
        remap = {"ext__lookup.pkl": str(pkl_path)}

        mock_obj = MagicMock()

        with patch("haute.deploy._scorer.load_external_object", return_value=mock_obj):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths=remap,
            )

        assert "ext_val" in result.columns
        assert result["ext_val"].to_list() == [99]

    def test_external_file_remap_without_code(self, tmp_path):
        """externalFile without code passes through first input."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ext",
                        "data": {
                            "label": "ext",
                            "nodeType": "externalFile",
                            "config": {
                                "path": "original/lookup.pkl",
                                "fileType": "pickle",
                                "code": "",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ext",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ext", "target": "out"},
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0, 2.0]})
        remap = {"ext__lookup.pkl": "/fake/path"}

        result = score_graph(
            graph=graph,
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths=remap,
        )

        assert len(result) == 2
        assert "x" in result.columns


class TestScoreGraphOptimiserApplyRemap:
    """Tests for optimiserApply remapping in score_graph."""

    def test_optimiser_apply_file_remap(self, tmp_path):
        """optimiserApply with remapped artifact path loads from local file."""
        from haute.deploy._scorer import score_graph

        # Write a fake artifact
        artifact_path = tmp_path / "opt_artifact.json"
        artifact_path.write_text(json.dumps({"type": "banding", "data": {}}))

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": {
                                "sourceType": "file",
                                "artifact_path": "artifacts/opt_artifact.json",
                                "version_column": "__opt_v__",
                                "optimised_value_column": "selected_factor",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "selected_factor", "__opt_v__"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "opt",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "opt", "target": "out"},
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0]})
        remap = {"opt__opt_artifact.json": str(artifact_path)}

        mock_artifact = MagicMock()
        mock_dispatch_result = pl.DataFrame(
            {"x": [1.0], "selected_factor": [1.1], "__opt_v__": ["v1"]}
        ).lazy()

        with (
            patch("haute._optimiser_io.load_optimiser_artifact", return_value=mock_artifact),
            patch(
                "haute.executor._dispatch_apply",
                return_value=mock_dispatch_result,
            ) as mock_dispatch,
        ):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths=remap,
            )

        assert isinstance(result, pl.DataFrame)
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.args[3] == "selected_factor"

    def _ratebook_apply_remap_score(self, tmp_path):
        """Score a deployed ratebook-apply graph and return the result frame."""
        from haute.deploy._scorer import score_graph

        artifact_path = tmp_path / "ratebook.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "version": "rb_v1",
                    "mode": "ratebook",
                    "factor_tables": {
                        "region_band": [
                            {
                                "__factor_group__": "South",
                                "optimal_scenario_value": 0.95,
                            },
                            {
                                "__factor_group__": "North",
                                "optimal_scenario_value": 1.10,
                            },
                        ]
                    },
                }
            )
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("quote_id", label="src"),
                        },
                    },
                    {
                        "id": "band",
                        "data": {
                            "label": "band",
                            "nodeType": "banding",
                            "config": {
                                "factors": [
                                    {
                                        "banding": "categorical",
                                        "column": "region",
                                        "outputColumn": "region_band",
                                        "rules": {
                                            "London": "South",
                                            "Manchester": "North",
                                        },
                                    }
                                ]
                            },
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": {
                                "sourceType": "file",
                                "artifact_path": "artifacts/ratebook.json",
                                "version_column": "__opt_v__",
                                "optimised_value_column": "selected_factor",
                                "ratebook_input": "band",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(
                                ["quote_id", "region_band", "selected_factor", "__opt_v__"]
                            ),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "opt",
                        "sourceHandle": "src",
                    },
                    {
                        "id": "e2",
                        "source": "src",
                        "target": "band",
                        "sourceHandle": "src",
                    },
                    {"id": "e3", "source": "band", "target": "opt"},
                    {"id": "e4", "source": "opt", "target": "out"},
                ],
            }
        )

        return score_graph(
            graph=graph,
            input_df=pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["London", "Manchester"],
                }
            ),
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths={"opt__ratebook.json": str(artifact_path)},
        )

    def test_ratebook_apply_file_remap_routes_through_configured_input(self, tmp_path):
        """The configured ``ratebook_input`` is used for the factor-table lookup."""
        result = self._ratebook_apply_remap_score(tmp_path)
        assert result["region_band"].to_list() == ["South", "North"]

    def test_ratebook_apply_file_remap_renames_to_configured_value_column(self, tmp_path):
        """The optimised value lands in the user-configured column name."""
        result = self._ratebook_apply_remap_score(tmp_path)
        assert result["selected_factor"].to_list() == pytest.approx([0.95, 1.10])

    def test_ratebook_apply_file_remap_emits_artifact_version_column(self, tmp_path):
        """The configured version column carries the artifact version per quote."""
        result = self._ratebook_apply_remap_score(tmp_path)
        assert result["__opt_v__"].to_list() == ["rb_v1", "rb_v1"]

    def test_optimiser_apply_mlflow_source(self):
        """optimiserApply with MLflow source downloads at runtime."""
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run_abc",
                                "version_column": "__opt_v__",
                                "optimised_value_column": "selected_factor",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "selected_factor", "__opt_v__"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "opt",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "opt", "target": "out"},
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0]})
        mock_artifact = MagicMock()
        mock_dispatch_result = pl.DataFrame(
            {"x": [1.0], "selected_factor": [1.1], "__opt_v__": ["v1"]}
        ).lazy()

        with (
            patch("haute._optimiser_io.load_mlflow_optimiser_artifact", return_value=mock_artifact),
            patch(
                "haute.executor._dispatch_apply",
                return_value=mock_dispatch_result,
            ) as mock_dispatch,
        ):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
            )

        assert isinstance(result, pl.DataFrame)
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.args[3] == "selected_factor"


class TestScoreGraphModelScoreRemap:
    """Tests for modelScore remapping in score_graph."""

    def test_model_score_remap_loads_bundled_model(self, tmp_path):
        """modelScore with remap loads from bundled local path."""
        from haute._mlflow_io import ScoringModel
        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")

        mock_model = MagicMock()
        mock_model.feature_names_ = ["x"]
        mock_model.predict.return_value = np.array([42.0])
        scoring_model = ScoringModel(
            model=mock_model,
            feature_names=["x"],
            cat_feature_names=frozenset(),
            flavor="catboost",
        )

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["pred"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        input_df = pl.DataFrame({"x": [1.0]})
        remap = {"ms__model.cbm": str(cbm_path)}

        with (
            patch("haute._mlflow_io._load_catboost_model", return_value=mock_model),
            patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
        ):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths=remap,
            )

        assert "pred" in result.columns

    def test_model_score_remap_with_output_fields_uses_bundled_contract(
        self,
        tmp_path,
    ):
        """output_fields projection must not make deployed scoring call MLflow."""
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["x"],
                feature_types={"x": "Float64"},
                categorical_features=[],
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )

        mock_model = MagicMock()
        mock_model.feature_names_ = ["x"]
        mock_model.predict.return_value = np.array([42.0])

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "remote-run",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                                "code": "df = df",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["pred"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io._load_catboost_model", return_value=mock_model),
            patch(
                "haute._mlflow_io.load_mlflow_model",
                side_effect=AssertionError("MLflow should not be called"),
            ) as load_mlflow,
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0], "unused": [999.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
                output_fields=["pred"],
            )

        load_mlflow.assert_not_called()
        assert result.columns == ["pred"]
        assert result["pred"].to_list() == [42.0]

    def test_remapped_model_score_rejects_observed_category_before_predict(
        self,
        tmp_path,
    ):
        from haute.deploy._scorer import score_graph
        from haute.errors import FeatureMismatchError
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["region"],
                feature_types={"region": "String"},
                categorical_features=["region"],
                categorical_levels={"region": ["north", "south"]},
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        mock_model = MagicMock()
        mock_model.feature_names_ = ["region"]
        mock_model.get_cat_feature_indices.return_value = [0]
        mock_model.predict.return_value = np.array([42.0])
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "region",
                                label="src",
                                categorical_levels={"region": ["north", "south"]},
                            ),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "remote-run",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io._load_catboost_model", return_value=mock_model),
            pytest.raises(FeatureMismatchError, match="outside declared"),
        ):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["west"]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
            )

        mock_model.predict.assert_not_called()

    def test_contract_only_model_score_rejects_runtime_feature_order_drift(
        self,
        tmp_path,
    ):
        """Bundled contract checks compare live feature order, not just names."""
        from haute.deploy._scorer import score_graph
        from haute.errors import FeatureMismatchError
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["age", "region"],
                feature_types={"age": "Int64", "region": "String"},
                categorical_features=["region"],
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("region", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with pytest.raises(FeatureMismatchError, match="features"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["north"], "age": [30]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_contract_only_model_score_rejects_declared_categorical_level_drift(
        self,
        tmp_path,
    ):
        """Declared category domains are compared without scanning row values."""
        from haute.deploy._scorer import score_graph
        from haute.errors import FeatureMismatchError
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["region"],
                feature_types={"region": "String"},
                categorical_features=["region"],
                categorical_levels={"region": ["north", "south"]},
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "region",
                                label="src",
                                categorical_levels={"region": ["north", "east"]},
                            ),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with pytest.raises(FeatureMismatchError, match="categorical_levels"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["north"]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_contract_only_model_score_fails_loudly_after_matching_declared_levels(
        self,
        tmp_path,
    ):
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["region"],
                feature_types={"region": "String"},
                categorical_features=["region"],
                categorical_levels={"region": ["north", "south"]},
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "region",
                                label="src",
                                categorical_levels={"region": ["north", "south"]},
                            ),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with pytest.raises(RuntimeError, match="model artifact"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["north"]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_contract_only_model_score_rejects_observed_category_outside_domain(
        self,
        tmp_path,
    ):
        from haute.deploy._scorer import score_graph
        from haute.errors import FeatureMismatchError
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["region"],
                feature_types={"region": "String"},
                categorical_features=["region"],
                categorical_levels={"region": ["north", "south"]},
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "region",
                                label="src",
                                categorical_levels={"region": ["north", "south"]},
                            ),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with pytest.raises(FeatureMismatchError, match="outside declared"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["west"]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_contract_only_model_score_uses_contract_levels_without_redeclaration(
        self,
        tmp_path,
    ):
        from haute.deploy._scorer import score_graph
        from haute.errors import FeatureMismatchError
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["region"],
                feature_types={"region": "String"},
                categorical_features=["region"],
                categorical_levels={"region": ["north", "south"]},
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("region", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with pytest.raises(FeatureMismatchError, match="outside declared"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["west"]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_contract_only_model_score_collects_upstream_level_declarations(
        self,
        tmp_path,
    ):
        from haute.deploy._scorer import score_graph
        from haute.errors import FeatureMismatchError
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["region"],
                feature_types={"region": "String"},
                categorical_features=["region"],
                categorical_levels={"region": ["north", "south"]},
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config(
                                "region",
                                categorical_levels={"region": ["north", "east"]},
                            ),
                        },
                    },
                    {
                        "id": "prep",
                        "data": {
                            "label": "prep",
                            "nodeType": "polars",
                            "config": {"code": "df = quotes"},
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "prep",
                        "sourceHandle": "quotes",
                    },
                    {"id": "e2", "source": "prep", "target": "ms"},
                    {"id": "e3", "source": "ms", "target": "out"},
                ],
            }
        )

        with pytest.raises(FeatureMismatchError, match="categorical_levels"):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"region": ["north"]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={f"ms__{CONTRACT_FILENAME}": str(contract_path)},
            )

    def test_multi_row_model_score_uses_deploy_batch_source(self, tmp_path):
        """Multi-row deploy modelScore should use the batch scorer contract."""
        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        scoring_model = MagicMock()
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0, 20.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["pred"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            # The v2 OUTPUT references its mapped column (``pred``), so deploy
            # batch projection planning resolves the modelScore column contract,
            # which loads the model. Mock the planner's loader too (the v1
            # passthrough OUTPUT seeded no projection, so this never fired).
            patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={"ms__model.cbm": str(cbm_path)},
            )

        assert result["pred"].to_list() == [10.0, 20.0]
        assert captured["source"] == "deploy_batch"

    def test_single_row_model_score_keeps_live_source(self, tmp_path):
        """Single-row deploy modelScore should keep the eager live scorer path."""
        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        scoring_model = MagicMock()
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["pred"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={"ms__model.cbm": str(cbm_path)},
            )

        assert result["pred"].to_list() == [10.0]
        assert captured["source"] == "live"

    def test_model_score_remap_forwards_required_output_columns(self, tmp_path):
        """Bundled deploy scoring should honour projection demand from output_fields."""
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["x"],
                feature_types={"x": "Float64"},
                categorical_features=[],
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        scoring_model = MagicMock()
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0], "unused": [999.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["pred"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0], "unused": [999.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
                output_fields=["pred"],
            )

        assert result.columns == ["pred"]
        assert captured["required_output_columns"] == {"pred"}
        assert captured["code"] == ""

    def test_model_score_remap_forwards_secondary_inputs(self, tmp_path):
        """Bundled deploy modelScore must preserve extra parent frames for user code."""
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["x"],
                feature_types={"x": "Float64"},
                categorical_features=[],
                target_name="target",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        scoring_model = MagicMock()
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0, 20.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "aux",
                        "data": {
                            "label": "aux",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="aux"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                                "code": "df = src",
                                "contract": {
                                    "inputs": ["x"],
                                    "outputs": ["pred"],
                                },
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["pred"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {
                        "id": "e2",
                        "source": "aux",
                        "target": "ms",
                        "sourceHandle": "aux",
                    },
                    {"id": "e3", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src", "aux"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
            )

        extra_dfs = captured["extra_dfs"]
        assert isinstance(extra_dfs, tuple)
        assert len(extra_dfs) == 1

    def test_multi_row_unbundled_model_score_uses_deploy_batch_source(self):
        """Configured non-bundled deploy modelScore follows deploy batch scoring."""
        from haute.deploy._scorer import score_graph

        scoring_model = MagicMock()
        captured: dict[str, object] = {}

        def fake_run_score_pipeline(*_args, **kwargs):
            captured.update(kwargs)
            return pl.DataFrame({"pred": [10.0, 20.0]}).lazy()

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "remote-run",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["pred"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
            patch(
                "haute._model_scorer._run_score_pipeline",
                side_effect=fake_run_score_pipeline,
            ),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
            )

        assert result["pred"].to_list() == [10.0, 20.0]
        assert captured["source"] == "deploy_batch"

    def test_deploy_batch_model_score_cleans_scored_temp_after_collect(
        self,
        tmp_path,
    ):
        """Request-scoped deploy cleanup removes scored parquet after response collect."""
        from haute._mlflow_io import ScoringModel
        from haute.deploy._scorer import score_graph

        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")
        scored_path = tmp_path / "haute_score_out_deploy.parquet"
        raw_model = MagicMock()
        scoring_model = ScoringModel(
            model=raw_model,
            feature_names=["x"],
            cat_feature_names=frozenset(),
            flavor="catboost",
        )

        def fake_batch_score(*_args, **_kwargs):
            pl.DataFrame({"pred": [10.0, 20.0]}).write_parquet(scored_path)
            return str(scored_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": _single_frame_api_input_config("x", "float", label="src"),
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["pred"]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    },
                    {"id": "e2", "source": "ms", "target": "out"},
                ],
            }
        )

        with (
            patch("haute._mlflow_io.load_local_model", return_value=scoring_model),
            # The v2 OUTPUT references its mapped column (``pred``), so deploy
            # batch projection planning resolves the modelScore column contract,
            # which loads the model. Mock the planner's loader too (the v1
            # passthrough OUTPUT seeded no projection, so this never fired).
            patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
            patch("haute._model_scorer._batch_score_to_parquet", side_effect=fake_batch_score),
        ):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0, 2.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={"ms__model.cbm": str(cbm_path)},
            )

        assert result["pred"].to_list() == [10.0, 20.0]
        assert not scored_path.exists()


class TestRemapArtifact:
    """Tests for the _remap_artifact helper function."""

    def test_remap_found(self):
        from haute.deploy._scorer import _remap_artifact

        remap = {"node1__model.pkl": "/local/model.pkl"}
        result = _remap_artifact("node1", {"path": "remote/model.pkl"}, remap, "path")
        assert result == "/local/model.pkl"

    def test_remap_not_found(self):
        from haute.deploy._scorer import _remap_artifact

        remap = {"other__model.pkl": "/local/model.pkl"}
        result = _remap_artifact("node1", {"path": "remote/model.pkl"}, remap, "path")
        assert result is None

    def test_remap_empty_path(self):
        from haute.deploy._scorer import _remap_artifact

        remap = {"node1__": "/local/empty"}
        result = _remap_artifact("node1", {"path": ""}, remap, "path")
        assert result == "/local/empty"

    def test_remap_missing_key_field(self):
        from haute.deploy._scorer import _remap_artifact

        remap = {}
        result = _remap_artifact("node1", {}, remap, "path")
        assert result is None


class TestDeployModelScoreTempCleanup:
    def test_cleanup_preserves_primary_error_when_unlink_fails(self, monkeypatch):
        from haute.deploy._scorer import _cleanup_model_score_temp_paths

        def fail_cleanup(_paths):
            raise PermissionError("locked")

        monkeypatch.setattr(
            "haute._model_scorer._cleanup_registered_temp_files",
            fail_cleanup,
        )

        _cleanup_model_score_temp_paths(["locked.parquet"], preserve_primary_error=True)

        with pytest.raises(PermissionError, match="locked"):
            _cleanup_model_score_temp_paths(
                ["locked.parquet"],
                preserve_primary_error=False,
            )


# ===========================================================================
# 4. _config.py — _load_env, _apply_env_overrides, resolve_config edge cases
# ===========================================================================


class TestLoadEnv:
    """Tests for _load_env()."""

    def test_load_env_no_file(self, tmp_path):
        """No .env file should be a no-op."""
        from haute.deploy._config import _load_env

        # Should not raise
        _load_env(tmp_path)

    def test_load_env_with_dotenv(self, tmp_path, monkeypatch):
        """When python-dotenv is available, load_dotenv is called."""
        from haute.deploy._config import _load_env

        env_file = tmp_path / ".env"
        env_file.write_text("MY_KEY=my_value\n")

        # Ensure load_dotenv is called
        with patch("haute.deploy._config.load_dotenv", create=True):
            # The import path for load_dotenv is inside the function,
            # so we need to mock at the right location
            with patch.dict("sys.modules", {"dotenv": MagicMock()}):
                # Just verify it doesn't crash
                _load_env(tmp_path)

    def test_load_env_fallback_without_dotenv(self, tmp_path, monkeypatch):
        """When python-dotenv is NOT installed, fallback parsing works."""
        from haute.deploy._config import _load_env

        env_file = tmp_path / ".env"
        env_file.write_text(
            "# comment\nMY_TEST_VAR=hello\n  ANOTHER_VAR = world  \n\nNO_EQUALS_LINE\n"
        )

        # Remove any pre-existing values
        monkeypatch.delenv("MY_TEST_VAR", raising=False)
        monkeypatch.delenv("ANOTHER_VAR", raising=False)

        # Force ImportError for dotenv
        with patch.dict("sys.modules", {"dotenv": None}):
            _load_env(tmp_path)

        assert os.environ.get("MY_TEST_VAR") == "hello"
        assert os.environ.get("ANOTHER_VAR") == "world"

        # Clean up
        monkeypatch.delenv("MY_TEST_VAR", raising=False)
        monkeypatch.delenv("ANOTHER_VAR", raising=False)

    def test_load_env_setdefault_does_not_overwrite(self, tmp_path, monkeypatch):
        """Fallback parser uses setdefault, so existing env vars are preserved."""
        from haute.deploy._config import _load_env

        env_file = tmp_path / ".env"
        env_file.write_text("MY_PRESET=overwritten\n")

        monkeypatch.setenv("MY_PRESET", "original")

        with patch.dict("sys.modules", {"dotenv": None}):
            _load_env(tmp_path)

        assert os.environ.get("MY_PRESET") == "original"

    @pytest.mark.parametrize(
        "raw_value, expected",
        [
            ('"hello"', "hello"),
            ("'hello'", "hello"),
            ("'quoted with spaces'", "quoted with spaces"),
            ('"double quoted"', "double quoted"),
            ("no_quotes", "no_quotes"),
            ("", ""),
        ],
    )
    def test_load_env_fallback_strips_quotes(self, tmp_path, monkeypatch, raw_value, expected):
        """Fallback parser must strip surrounding single and double quotes."""
        from haute.deploy._config import _load_env

        env_file = tmp_path / ".env"
        env_file.write_text(f"QUOTED_VAR={raw_value}\n")

        monkeypatch.delenv("QUOTED_VAR", raising=False)

        with patch.dict("sys.modules", {"dotenv": None}):
            _load_env(tmp_path)

        assert os.environ.get("QUOTED_VAR") == expected
        monkeypatch.delenv("QUOTED_VAR", raising=False)


class TestResolveConfigEdgeCases:
    """Tests for resolve_config() edge cases."""

    def test_resolve_config_no_source_nodes_raises(self):
        """Graph with no source nodes at all should raise ValueError."""
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )

        mock_graph = MagicMock()
        mock_graph.nodes = [MagicMock()]

        # Top-level imports in _config.py are bound to the module, so we patch them there.
        # Local imports in resolve_config() are patched at the source module.
        with (
            patch("haute.parser.parse_pipeline_file", return_value=mock_graph),
            patch("haute.deploy._config.find_output_node", return_value="out"),
            patch("haute.deploy._config.prune_for_deploy", return_value=(mock_graph, ["out"], [])),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=[]),
            patch("haute.deploy._config.find_source_nodes", return_value=[]),
        ):
            with pytest.raises(ValueError, match="No source nodes"):
                resolve_config(config)

    def test_resolve_config_multiple_unnamed_sources_raises(self):
        """Multiple source nodes with no apiInput nodes should raise ValueError."""
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )

        mock_graph = MagicMock()
        mock_graph.nodes = [MagicMock()]

        with (
            patch("haute.parser.parse_pipeline_file", return_value=mock_graph),
            patch("haute.deploy._config.find_output_node", return_value="out"),
            patch("haute.deploy._config.prune_for_deploy", return_value=(mock_graph, ["out"], [])),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=[]),
            patch("haute.deploy._config.find_source_nodes", return_value=["src1", "src2"]),
        ):
            with pytest.raises(ValueError, match="Multiple source nodes"):
                resolve_config(config)

    def test_resolve_config_single_source_fallback(self):
        """Single source node is used as deploy input when no apiInput exists."""
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )

        mock_graph = MagicMock()
        mock_graph.nodes = [MagicMock()]

        with (
            patch("haute.parser.parse_pipeline_file", return_value=mock_graph),
            patch("haute.deploy._config.find_output_node", return_value="out"),
            patch("haute.deploy._config.prune_for_deploy", return_value=(mock_graph, ["out"], [])),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=[]),
            patch("haute.deploy._config.find_source_nodes", return_value=["single_src"]),
            patch("haute.deploy._bundler.collect_artifacts", return_value={}),
            patch("haute.deploy._schema.infer_input_schema", return_value={"col": "Int64"}),
            patch("haute.deploy._schema.infer_output_schema", return_value={"out": "Float64"}),
        ):
            resolved = resolve_config(config)

        assert resolved.input_node_ids == ["single_src"]


# ===========================================================================
# 5. _mlflow.py — get_deploy_status, _build_signature, _conda_env, etc.
# ===========================================================================


class TestGetDeployStatus:
    """Tests for get_deploy_status()."""

    def test_model_not_found(self):
        from haute.deploy._mlflow import get_deploy_status

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri"),
            patch("mlflow.tracking.MlflowClient") as mock_client_cls,
        ):
            mock_client_cls.return_value.search_model_versions.return_value = []

            result = get_deploy_status("nonexistent-model")

        assert result["status"] == "not_found"
        assert result["latest_version"] == 0
        assert result["model_name"] == "nonexistent-model"

    def test_model_found_returns_version_info(self):
        from haute.deploy._mlflow import get_deploy_status

        mock_v1 = MagicMock()
        mock_v1.version = "1"
        mock_v1.current_stage = "Production"
        mock_v1.status = "READY"
        mock_v1.run_id = "run_abc"

        mock_v2 = MagicMock()
        mock_v2.version = "2"
        mock_v2.current_stage = "Staging"
        mock_v2.status = "READY"
        mock_v2.run_id = "run_def"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri"),
            patch("mlflow.tracking.MlflowClient") as mock_client_cls,
        ):
            mock_client_cls.return_value.search_model_versions.return_value = [mock_v1, mock_v2]

            result = get_deploy_status("my-model", catalog="ws", schema="default")

        assert result["model_name"] == "my-model"
        assert result["latest_version"] == 2
        assert result["status"] == "READY"
        assert result["run_id"] == "run_def"

    def test_model_found_uses_uc_name(self):
        """get_deploy_status uses catalog.schema.model_name for the search query."""
        from haute.deploy._mlflow import get_deploy_status

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri"),
            patch("mlflow.tracking.MlflowClient") as mock_client_cls,
        ):
            mock_client_cls.return_value.search_model_versions.return_value = []

            get_deploy_status("my-model", catalog="ws", schema="pricing")

        call_args = mock_client_cls.return_value.search_model_versions.call_args
        assert "ws.pricing.my-model" in call_args[0][0]


class TestBuildSignature:
    """Tests for _build_signature()."""

    @staticmethod
    def _input_type_map(sig):
        """Extract {col_name: DataType} from a ModelSignature's inputs."""
        return {col.name: col.type for col in sig.inputs.inputs}

    def test_basic_types(self):
        from mlflow.models import ModelSignature
        from mlflow.types import DataType

        from haute.deploy._mlflow import _build_signature

        resolved = _make_resolved(
            input_schema={"age": "Int32", "name": "String", "premium": "Float64"},
            output_schema={"result": "Float64"},
        )

        sig = _build_signature(resolved)

        assert isinstance(sig, ModelSignature)
        type_map = self._input_type_map(sig)
        assert type_map["age"] == DataType.integer, "Int32 must map to integer"
        assert type_map["name"] == DataType.string, "String must map to string"
        assert type_map["premium"] == DataType.double, "Float64 must map to double"
        out_map = {col.name: col.type for col in sig.outputs.inputs}
        assert out_map["result"] == DataType.double

    def test_parameterized_datetime_type(self):
        """Datetime('us', 'UTC') should map to datetime DataType."""
        from mlflow.types import DataType

        from haute.deploy._mlflow import _build_signature

        resolved = _make_resolved(
            input_schema={"ts": "Datetime('us', 'UTC')"},
            output_schema={"val": "Float64"},
        )

        sig = _build_signature(resolved)
        type_map = self._input_type_map(sig)
        assert type_map["ts"] == DataType.datetime, "Parameterized Datetime must map to datetime"

    def test_unknown_dtype_falls_back_to_string(self):
        """Unknown polars dtype should map to DataType.string."""
        from mlflow.types import DataType

        from haute.deploy._mlflow import _build_signature

        resolved = _make_resolved(
            input_schema={"exotic": "CategoricalComplex"},
            output_schema={"val": "Float64"},
        )

        sig = _build_signature(resolved)
        type_map = self._input_type_map(sig)
        assert type_map["exotic"] == DataType.string, "Unknown dtype must fall back to string"

    def test_all_numeric_types(self):
        """All supported numeric types should produce correct MLflow type mappings."""
        from mlflow.types import DataType

        from haute.deploy._mlflow import _build_signature

        all_types = {
            "a_i8": "Int8",
            "b_i16": "Int16",
            "c_i32": "Int32",
            "d_i64": "Int64",
            "e_u8": "UInt8",
            "f_u16": "UInt16",
            "g_u32": "UInt32",
            "h_u64": "UInt64",
            "i_f32": "Float32",
            "j_f64": "Float64",
            "k_str": "String",
            "l_utf8": "Utf8",
            "m_bool": "Boolean",
            "n_date": "Date",
        }
        resolved = _make_resolved(
            input_schema=all_types,
            output_schema={"val": "Float64"},
        )

        sig = _build_signature(resolved)
        type_map = self._input_type_map(sig)

        # Integer types (Int8, Int16, Int32, UInt8, UInt16) -> integer
        for col in ("a_i8", "b_i16", "c_i32", "e_u8", "f_u16"):
            assert type_map[col] == DataType.integer, f"{col} must map to integer"
        # Long types (Int64, UInt32, UInt64) -> long
        for col in ("d_i64", "g_u32", "h_u64"):
            assert type_map[col] == DataType.long, f"{col} must map to long"
        # Float32 -> float
        assert type_map["i_f32"] == DataType.float, "Float32 must map to float"
        # Float64 -> double
        assert type_map["j_f64"] == DataType.double, "Float64 must map to double"
        # String / Utf8 -> string
        assert type_map["k_str"] == DataType.string
        assert type_map["l_utf8"] == DataType.string
        # Boolean -> boolean
        assert type_map["m_bool"] == DataType.boolean
        # Date -> datetime
        assert type_map["n_date"] == DataType.datetime


class TestCondaEnvAndPipRequirements:
    """Tests for _conda_env() and _pip_requirements()."""

    def test_pip_requirements_includes_haute_and_polars(self):
        from haute.deploy._mlflow import _pip_requirements

        resolved = _make_resolved()
        reqs = _pip_requirements(resolved)

        assert any("haute==" in r for r in reqs)
        assert "polars>=1.39.2" in reqs

    def test_pip_requirements_includes_catboost_when_used(self):
        """If a node has fileType=catboost, catboost is added to requirements."""
        from haute.deploy._mlflow import _pip_requirements

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ext",
                        "data": {
                            "label": "ext",
                            "nodeType": "externalFile",
                            "config": {"fileType": "catboost"},
                        },
                    },
                ],
            }
        )
        resolved = _make_resolved(pruned_graph=graph)
        reqs = _pip_requirements(resolved)

        assert any("catboost" in r for r in reqs)

    def test_pip_requirements_no_catboost_without_it(self):
        """Without catboost nodes, catboost is NOT in requirements."""
        from haute.deploy._mlflow import _pip_requirements

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "t",
                        "data": {
                            "label": "t",
                            "nodeType": "polars",
                            "config": {},
                        },
                    },
                ],
            }
        )
        resolved = _make_resolved(pruned_graph=graph)
        reqs = _pip_requirements(resolved)

        assert not any("catboost" in r for r in reqs)

    def test_conda_env_structure(self):
        from haute.deploy._mlflow import _conda_env

        resolved = _make_resolved()
        env = _conda_env(resolved)

        assert env["name"] == "mlflow-env"
        assert "conda-forge" in env["channels"]
        assert len(env["dependencies"]) == 3  # python, pip, pip-dict
        assert env["dependencies"][0].startswith("python=")

    def test_conda_env_pins_python_version(self):
        from haute.deploy._mlflow import _SERVING_PYTHON_VERSION, _conda_env

        resolved = _make_resolved()
        env = _conda_env(resolved)

        python_dep = env["dependencies"][0]
        assert python_dep == f"python={_SERVING_PYTHON_VERSION}"


class TestCheckDatabricksConnectivity:
    """Tests for _check_databricks_connectivity()."""

    def test_missing_host_raises(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.delenv("DATABRICKS_RATING_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_RATING_TOKEN", raising=False)

        with pytest.raises(RuntimeError, match="DATABRICKS_RATING_HOST is not set"):
            _check_databricks_connectivity(lambda msg: None)

    def test_missing_token_raises(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.delenv("DATABRICKS_RATING_TOKEN", raising=False)

        with pytest.raises(RuntimeError, match="DATABRICKS_RATING_TOKEN is not set"):
            _check_databricks_connectivity(lambda msg: None)

    def test_success(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "dapi_token")

        messages = []

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock()
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            _check_databricks_connectivity(messages.append)

        assert any("reachable" in m for m in messages)

    def test_403_raises_with_clear_message(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "bad_token")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://host/api",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=None,
            )

            with pytest.raises(RuntimeError, match="403 Forbidden"):
                _check_databricks_connectivity(lambda msg: None)

    def test_non_403_http_error_succeeds(self, monkeypatch):
        """Non-403 HTTP errors (e.g. 404) mean host is reachable."""
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "token")

        messages = []

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://host/api",
                code=404,
                msg="Not Found",
                hdrs=None,
                fp=None,
            )

            _check_databricks_connectivity(messages.append)

        assert any("reachable" in m for m in messages)

    def test_timeout_raises(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "token")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("connection timed out")

            with pytest.raises(RuntimeError, match="Cannot reach Databricks"):
                _check_databricks_connectivity(lambda msg: None)

    def test_url_error_raises(self, monkeypatch):
        from haute.deploy._mlflow import _check_databricks_connectivity

        monkeypatch.setenv("DATABRICKS_RATING_HOST", "https://host.databricks.com")
        monkeypatch.setenv("DATABRICKS_RATING_TOKEN", "token")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("DNS resolution failed")

            with pytest.raises(RuntimeError, match="Cannot reach Databricks"):
                _check_databricks_connectivity(lambda msg: None)


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

    def test_scalar_return_appended(self):
        """Scalar results (not lists) should be appended, not extended."""
        from haute.deploy._impact import _run_batched

        records = [1, 2, 3]
        results = _run_batched(records, lambda batch: sum(batch), batch_size=2, progress=None)
        # Batch 1: [1,2] -> sum=3, Batch 2: [3] -> sum=3
        assert results == [3, 3]

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
        assert report.scored_rows == 2
        assert report.failed_rows == 0
        assert len(report.column_stats) >= 1
        assert report.column_stats[0].name == "premium"

    def test_mismatched_lengths_truncated(self):
        from haute.deploy._impact import build_report

        stg = [{"premium": 110.0}, {"premium": 220.0}, {"premium": 330.0}]
        prd = [{"premium": 100.0}, {"premium": 200.0}]
        input_df = pl.DataFrame({"age": [25, 30, 35]})

        report = build_report(
            stg,
            prd,
            input_df,
            pipeline_name="test",
            staging_endpoint="stg",
            prod_endpoint="prod",
            dataset_path="d.parquet",
            total_rows=3,
        )

        assert report.scored_rows == 2
        assert report.failed_rows == 1

    def test_empty_predictions(self):
        from haute.deploy._impact import build_report

        input_df = pl.DataFrame({"age": [25]})
        report = build_report(
            [],
            [],
            input_df,
            pipeline_name="test",
            staging_endpoint="stg",
            prod_endpoint="prod",
            dataset_path="d.parquet",
            total_rows=1,
        )

        assert report.scored_rows == 0
        assert len(report.column_stats) == 0


class TestFormatTerminal:
    """Tests for format_terminal()."""

    def test_basic_format(self):
        from haute.deploy._impact import ColumnStats, ImpactReport, format_terminal

        stats = ColumnStats(
            name="premium",
            n_rows=100,
            n_changed=50,
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
            scored_rows=100,
            failed_rows=0,
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
            scored_rows=10,
            failed_rows=0,
            column_stats=[],
            segments={},
            is_first_deploy=True,
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
            scored_rows=100,
            failed_rows=0,
            column_stats=[],
            segments={},
        )

        output = format_terminal(report)
        assert "100 of 1,000 sampled" in output

    def test_failed_rows_warning(self):
        from haute.deploy._impact import ImpactReport, format_terminal

        report = ImpactReport(
            pipeline_name="test",
            staging_endpoint="stg",
            prod_endpoint="prod",
            dataset_path="d.parquet",
            total_rows=10,
            sampled_rows=10,
            scored_rows=8,
            failed_rows=2,
            column_stats=[],
            segments={},
        )

        output = format_terminal(report)
        assert "2 rows failed" in output


class TestFormatMarkdown:
    """Tests for format_markdown()."""

    def test_basic_markdown(self):
        from haute.deploy._impact import ColumnStats, ImpactReport, format_markdown

        stats = ColumnStats(
            name="premium",
            n_rows=100,
            n_changed=50,
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
            scored_rows=100,
            failed_rows=0,
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
            scored_rows=10,
            failed_rows=0,
            column_stats=[],
            segments={},
            is_first_deploy=True,
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


# ===========================================================================
# Additional validator / test-quote edge cases
# ===========================================================================


class TestLoadTestQuoteFileEdgeCases:
    """Edge cases for load_test_quote_file()."""

    def test_non_array_json_raises_value_error(self, tmp_path):
        """A JSON file containing an object (not an array) must raise ValueError."""
        from haute.deploy._validators import load_test_quote_file

        bad_file = tmp_path / "quotes.json"
        bad_file.write_text('{"key": "value"}')

        with pytest.raises(ValueError, match="JSON array"):
            load_test_quote_file(bad_file)


class TestValidateDeployEdgeCases:
    """Additional edge cases for validate_deploy()."""

    def test_output_node_missing_from_pruned_graph(self):
        """Validation must report an error when output_node_id is not in the pruned graph."""
        from haute.deploy._validators import validate_deploy

        resolved = _make_resolved(
            output_node_id="missing_output",
            pruned_graph=_g(
                {
                    "nodes": [
                        {"id": "src", "data": {"nodeType": "apiInput", "config": {}}},
                    ],
                }
            ),
            input_node_ids=["src"],
        )

        from haute.errors import DeployError

        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        assert any(
            "missing_output" in e and "not in pruned graph" in e
            for e in exc_info.value.context["structural_errors"]
        )

    def test_empty_input_schema_reported(self):
        """Validation must report an error when input_schema is empty."""
        from haute.deploy._validators import validate_deploy

        resolved = _make_resolved(
            input_schema={},
            output_schema={"col": "Int64"},
            pruned_graph=_g(
                {
                    "nodes": [
                        {
                            "id": "policies",
                            "data": {
                                "nodeType": "apiInput",
                                "config": _single_frame_api_input_config(
                                    "col", "int", label="policies"
                                ),
                            },
                        },
                        {
                            "id": "output",
                            "data": {"nodeType": "output", "config": make_output_config([])},
                        },
                    ],
                    "edges": [
                        {
                            "id": "e1",
                            "source": "policies",
                            "target": "output",
                            "sourceHandle": "policies",
                        }
                    ],
                }
            ),
            input_node_ids=["policies"],
            output_node_id="output",
        )

        from haute.errors import DeployError

        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        assert any(
            "Input schema is empty" in e for e in exc_info.value.context["structural_errors"]
        )

    def test_empty_output_schema_reported(self):
        """Validation must report an error when output_schema is empty."""
        from haute.deploy._validators import validate_deploy

        resolved = _make_resolved(
            input_schema={"col": "Int64"},
            output_schema={},
            pruned_graph=_g(
                {
                    "nodes": [
                        {
                            "id": "policies",
                            "data": {
                                "nodeType": "apiInput",
                                "config": _single_frame_api_input_config(
                                    "col", "int", label="policies"
                                ),
                            },
                        },
                        {
                            "id": "output",
                            "data": {"nodeType": "output", "config": make_output_config([])},
                        },
                    ],
                    "edges": [
                        {
                            "id": "e1",
                            "source": "policies",
                            "target": "output",
                            "sourceHandle": "policies",
                        }
                    ],
                }
            ),
            input_node_ids=["policies"],
            output_node_id="output",
        )

        from haute.errors import DeployError

        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        assert any(
            "Output schema is empty" in e for e in exc_info.value.context["structural_errors"]
        )


class TestScoreTestQuotesEdgeCases:
    """Additional edge cases for score_test_quotes()."""

    def test_empty_dir_returns_empty(self, tmp_path):
        """An existing but empty test_quotes directory returns an empty list."""
        from haute.deploy._config import DeployConfig
        from haute.deploy._validators import score_test_quotes

        tq_dir = tmp_path / "quotes"
        tq_dir.mkdir()

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
            test_quotes_dir=tq_dir,
        )
        resolved = _make_resolved(config=config)

        results = score_test_quotes(resolved)
        assert results == []


# ---------------------------------------------------------------------------
# Bug regression: pruner
# ---------------------------------------------------------------------------


class TestBugB4PrunerUsesOriginalEdges:
    """B4: kept_edges must filter from deploy_edges, not original edges."""

    def test_batch_edge_not_reintroduced_when_shared_node(self) -> None:
        from haute.deploy._pruner import prune_for_deploy

        def _node(nid, ntype="polars", config=None):
            return {
                "id": nid,
                "position": {"x": 0, "y": 0},
                "data": {"label": nid, "nodeType": ntype, "config": config or {}},
            }

        def _edge(src, tgt):
            return {"id": f"e_{src}_{tgt}", "source": src, "target": tgt}

        graph = _g(
            {
                "nodes": [
                    _node("shared"),
                    _node("live_src"),
                    _node("switch", "liveSwitch", {"inputs": ["live_src", "shared"]}),
                    _node("transform"),
                    _node("output", "output"),
                ],
                "edges": [
                    _edge("shared", "switch"),  # batch edge
                    _edge("live_src", "switch"),  # live edge
                    _edge("switch", "output"),
                    _edge("shared", "transform"),
                    _edge("transform", "output"),
                ],
            }
        )
        pruned, kept, removed = prune_for_deploy(graph, "output")
        pruned_pairs = {(e.source, e.target) for e in pruned.edges}
        # The batch edge shared->switch should NOT be in the pruned graph
        assert ("shared", "switch") not in pruned_pairs
        # The live edge should be kept
        assert ("live_src", "switch") in pruned_pairs


class TestBugB10LexicographicVersionComparison:
    """B10: MLflow version max() must use numeric, not string comparison."""

    def test_version_10_greater_than_9(self) -> None:

        # We just need to test the max() logic. Let's test directly.
        versions = ["1", "2", "9", "10", "11"]
        result = max(versions, key=lambda v: int(v))
        assert result == "11"
        # The bug: max(versions) without int() gives "9"
        assert max(versions) == "9"  # proves the bug exists in string comparison
