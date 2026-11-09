"""The deployed HauteModel and deploy schema inference.

``HauteModel.load_context`` parses the bundle manifest and ``predict`` scores through it;
``infer_input_schema`` and ``infer_output_schema`` read a deployment's request and response
columns, including a Data Input read through the project's snapshot store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._project_context import current_project
from haute._types import PipelineGraph
from haute.deploy._request_rows import RequestRows
from tests._deploy_internals_helpers import (
    PIPELINE_FILE,
    _single_frame_api_input_config,
)
from tests._project import use_project
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config

if TYPE_CHECKING:
    from haute.deploy._model_code import HauteModel

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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
            "polars_version": pl.__version__,
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["api_src"],
            "output_node_id": "out",
            "input_schema": {},
            "omittable_inputs": [],
            "request_rows": {
                "multi_quote": "in_request_order",
                "refusals": [],
                "checked_lookups": [],
            },
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
            "polars_version": pl.__version__,
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["src"],
            "output_node_id": "out",
            "input_schema": {},
            "omittable_inputs": [],
            "request_rows": {
                "multi_quote": "in_request_order",
                "refusals": [],
                "checked_lookups": [],
            },
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
        """Artifacts listed in the manifest are mandatory at model load."""
        from haute.deploy._model_code import HauteModel

        manifest = {
            "polars_version": pl.__version__,
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["src"],
            "output_node_id": "out",
            "input_schema": {},
            "omittable_inputs": [],
            "request_rows": {
                "multi_quote": "in_request_order",
                "refusals": [],
                "checked_lookups": [],
            },
            "artifacts": {"missing.pkl": "/original/path.pkl"},
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        model = HauteModel()
        ctx = MagicMock()
        ctx.artifacts = {"deploy_manifest": str(manifest_path)}

        with pytest.raises(RuntimeError, match="missing.pkl"):
            model.load_context(ctx)

    def test_load_context_refuses_a_polars_other_than_the_deployed_one(self, tmp_path):
        """A Polars release can change a pipeline's results, so the model refuses to load."""
        from haute.deploy._model_code import HauteModel

        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps({"polars_version": "1.44.2"}))
        ctx = MagicMock()
        ctx.artifacts = {"deploy_manifest": str(manifest_path)}

        with pytest.raises(RuntimeError) as exc_info:
            HauteModel().load_context(ctx)

        message = str(exc_info.value)
        assert f"deployed with Polars 1.44.2 and is loading under Polars {pl.__version__}" in (
            message
        )
        assert "polars==1.44.2" in message


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
        model._graph = PipelineGraph()
        model._project = current_project()
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = None
        model._request_rows = RequestRows("in_request_order")
        model._omittable = frozenset()
        model._input_schema = {"x": pl.Float64(), "y": pl.Float64()}

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
        model._graph = PipelineGraph()
        model._project = current_project()
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = None
        model._request_rows = RequestRows("in_request_order")
        model._omittable = frozenset()
        model._input_schema = {"x": pl.Float64()}
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

    def test_predict_releases_admission_when_polars_conversion_fails(self, monkeypatch):
        """A pre-scoring conversion error must not leak the deploy reservation."""
        import pandas as pd

        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.deploy._model_code import HauteModel

        released: list[str] = []
        execution_context = ExecutionContext(
            operation="deploy_pyfunc_predict",
            profile=ExecutionProfile.DEPLOY_BATCH,
            memory_sampler=lambda: 1,
            admission_release=lambda: released.append("released"),
        )
        model = HauteModel()
        model._graph = PipelineGraph()
        model._project = current_project()
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = None
        model._request_rows = RequestRows("in_request_order")
        model._omittable = frozenset()
        model._input_schema = {"x": pl.Float64()}

        monkeypatch.setattr(
            "haute.deploy._scorer.admit_deploy_execution",
            lambda **_kwargs: execution_context,
        )
        monkeypatch.setattr(
            "haute.deploy._request_frame.frame_rows",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad pandas frame")),
        )

        with pytest.raises(ValueError, match="bad pandas frame"):
            model.predict(MagicMock(), pd.DataFrame({"x": [1.0, 2.0]}))

        assert released == ["released"]

    def test_predict_retains_admission_through_pandas_response_conversion(self):
        import pandas as pd

        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.deploy._model_code import HauteModel

        timeline: list[str] = []
        execution_context = ExecutionContext(
            operation="deploy_pyfunc_predict",
            profile=ExecutionProfile.DEPLOY_LIVE,
            memory_sampler=lambda: 1,
            admission_release=lambda: timeline.append("released"),
        )
        model = HauteModel()
        model._graph = PipelineGraph()
        model._project = current_project()
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = None
        model._request_rows = RequestRows("in_request_order")
        model._omittable = frozenset()
        model._input_schema = {"x": pl.Float64()}

        class Result:
            def to_pandas(self):
                timeline.append("to_pandas")
                return pd.DataFrame({"score": [0.5]})

        with (
            patch(
                "haute.deploy._scorer.admit_deploy_execution",
                return_value=execution_context,
            ),
            patch("haute.deploy._scorer.score_graph", return_value=Result()) as score,
        ):
            result = model.predict(MagicMock(), pd.DataFrame({"x": [1.0]}))

        assert result["score"].to_list() == [0.5]
        assert score.call_args.kwargs["retain_admission_on_success"] is True
        assert timeline == ["to_pandas", "released"]

    def test_predict_passes_output_fields(self, tmp_path):
        """predict() forwards output_fields to score_graph."""
        import pandas as pd

        from haute.deploy._model_code import HauteModel

        model = HauteModel()
        model._graph = PipelineGraph()
        model._project = current_project()
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = ["premium"]
        model._request_rows = RequestRows("in_request_order")
        model._omittable = frozenset()
        model._input_schema = {"x": pl.Float64()}

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
        model._graph = PipelineGraph()
        model._project = current_project()
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {"model.pkl": "/served/model.pkl"}
        model._output_fields = None
        model._request_rows = RequestRows("in_request_order")
        model._omittable = frozenset()
        model._input_schema = {"x": pl.Float64()}

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
        pruned, _kept, _removed = prune_for_deploy(full_graph, "quote_response")

        model = HauteModel()
        model._graph = pruned
        model._project = current_project()
        model._input_node_ids = ["quote_input"]
        model._output_node_id = "quote_response"
        model._output_fields = output_fields
        model._request_rows = RequestRows("in_request_order")
        model._omittable = frozenset()
        model._input_schema = {
            "IDpol": pl.Int64(),
            "VehPower": pl.Int64(),
            "Area": pl.String(),
            "VehAge": pl.Int64(),
            "BonusMalus": pl.Int64(),
            "Exposure": pl.Float64(),
        }
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

    def test_ndjson_api_input_uses_json_shape_reader(self):
        """The .ndjson alias follows the same deploy schema path as .jsonl."""
        from haute.deploy import _schema

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": "data.ndjson"},
                        },
                    },
                ],
            }
        )
        expected = pl.LazyFrame({"value": [1]})

        with (
            patch.object(_schema, "read_source", return_value=expected) as read_source,
            patch.object(_schema, "read_data_source") as read_data_source,
        ):
            result = _schema._read_input_source(graph, graph.nodes[0], "data.ndjson")

        assert result is expected
        read_source.assert_called_once_with("data.ndjson")
        read_data_source.assert_not_called()

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

        use_project(tmp_path)

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

        # Compute the checked deploy-schema fingerprint that will be used.
        from haute.deploy._schema import deploy_schema_cache_fingerprint

        fp = deploy_schema_cache_fingerprint(
            graph,
            output_node_id="out",
            input_node_ids=["src"],
            artifact_paths=None,
        )

        # Pre-write the cache
        cache_dir = tmp_path / ".haute_cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "output_schema.json"
        cached = {"entries": {fp: {"premium": "Float64", "factor": "Int64"}}}
        cache_file.write_text(json.dumps(cached))

        # Should return cached schema without calling score_graph
        with patch("haute.deploy._scorer.score_graph") as mock_score:
            result = infer_output_schema(graph, "out", ["src"], project_root=tmp_path)

        mock_score.assert_not_called()
        assert result == {"premium": "Float64", "factor": "Int64"}

    def test_corrupt_cache_recomputes(self, tmp_path, monkeypatch):
        """Corrupt cache JSON triggers recomputation (no crash)."""
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)

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
            result = infer_output_schema(graph, "out", ["src"], project_root=tmp_path)

        assert result == {"result": "Float64"}

    @pytest.mark.parametrize(
        ("cache_text", "error"),
        [
            ("NOT VALID JSON {{{{", "Expecting value"),
            ('{"entries": []}', "entries is a list"),
        ],
        ids=["unparseable", "entries-not-a-mapping"],
    )
    def test_corrupt_cache_logs_warning(self, tmp_path, monkeypatch, cache_text, error):
        """A cache that is not one logs a warning with path and error details."""
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)

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
        cache_file.write_text(cache_text)

        mock_result = pl.DataFrame({"result": [42.0]})

        with (
            patch("haute.deploy._schema.logger") as mock_logger,
            patch("haute.deploy._scorer.score_graph", return_value=mock_result),
        ):
            infer_output_schema(graph, "out", ["src"], project_root=tmp_path)

        # Find the corrupt_schema_cache warning call
        warning_calls = [
            c for c in mock_logger.warning.call_args_list if c[0][0] == "corrupt_schema_cache"
        ]
        assert len(warning_calls) == 1, (
            f"Expected 1 corrupt_schema_cache warning, got {len(warning_calls)}"
        )
        kwargs = warning_calls[0][1]
        assert kwargs["path"] == str(cache_file)
        assert error in kwargs["error"]

    def test_cache_miss_writes_cache(self, tmp_path, monkeypatch):
        """Cache miss computes schema and writes cache file."""
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)

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
            result = infer_output_schema(graph, "out", ["src"], project_root=tmp_path)

        assert result == {"premium": "Float64"}

        # Verify cache was written
        cache_file = tmp_path / ".haute_cache" / "output_schema.json"
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text())
        assert list(cached["entries"].values()) == [{"premium": "Float64"}]

    def test_stale_cache_fingerprint_recomputes(self, tmp_path, monkeypatch):
        """Stale fingerprint in cache causes recomputation."""
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)

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
        cached = {"entries": {"wrong_fingerprint": {"old_col": "String"}}}
        cache_file.write_text(json.dumps(cached))

        mock_result = pl.DataFrame({"premium": [100.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=mock_result) as mock_score:
            result = infer_output_schema(graph, "out", ["src"], project_root=tmp_path)

        mock_score.assert_called_once()
        assert result == {"premium": "Float64"}

    def test_cache_write_oserror_is_warned_not_swallowed_silently(self, tmp_path, monkeypatch):
        """F124: an OSError writing the cache logs a warning; deploy continues.

        A disk-write failure is genuinely non-critical (the schema was computed
        truthfully and can be recomputed), but it must be surfaced as a warning
        rather than swallowed by a bare ``except: pass``.
        """
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)
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
            result = infer_output_schema(graph, "out", ["src"], project_root=tmp_path)

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
        Once ``score_graph`` is mocked, the cache's ``atomic_write_text`` is the
        write left in ``infer_output_schema``, so patching the write site pins the
        fix precisely. Asserting ``score_graph`` ran proves the error came from the
        cache write, not from fingerprinting.
        """
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)
        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)
        graph = _passthrough_input_graph(pq_path)

        mock_result = pl.DataFrame({"premium": [100.0]})
        mock_score = MagicMock(return_value=mock_result)
        with (
            patch("haute.deploy._scorer.score_graph", mock_score),
            patch(
                "haute.deploy._schema.atomic_write_text",
                side_effect=RuntimeError("serialisation bug"),
            ),
            pytest.raises(RuntimeError, match="serialisation bug"),
        ):
            infer_output_schema(graph, "out", ["src"], project_root=tmp_path)

        # score_graph runs after graph_fingerprint and before the cache write,
        # so its having been called exactly once proves fingerprinting finished
        # and the RuntimeError originated at the cache write — not earlier.
        mock_score.assert_called_once()


# ---------------------------------------------------------------------------
# Bug regression: pruner
# ---------------------------------------------------------------------------


def test_a_data_input_s_deploy_schema_reads_through_the_project_s_snapshot_store(
    tmp_path: Path,
) -> None:
    """A deployment whose request input is a Data Input reads its schema as execution does,
    through the installed project's snapshot store, its relative path anchored there."""
    from haute.deploy._schema import infer_input_schema
    from tests.conftest import make_ready_file_input_config

    (tmp_path / "data").mkdir()
    pl.DataFrame({"area": ["A"], "exposure": [1.0]}).write_parquet(
        tmp_path / "data" / "policies.parquet"
    )
    graph = _g(
        {
            "nodes": [
                {
                    "id": "policies",
                    "data": {
                        "label": "policies",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config("data/policies.parquet"),
                    },
                }
            ],
            "edges": [],
        }
    ).model_copy(update={"source_file": str(tmp_path / "main.py")})

    assert infer_input_schema(graph, "policies") == {"area": "String", "exposure": "Float64"}
