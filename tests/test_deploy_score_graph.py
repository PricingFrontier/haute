"""``score_graph`` request injection, output fields and input remapping.

Scoring a deployed graph injects the request into its API Input, filters the output to the
configured fields, remaps static data sources and External Files to their bundled copies, and fails
loudly on a missing output or a bad input.
"""

from __future__ import annotations

import gc
import json
import weakref
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from tests._deploy_internals_helpers import (
    _single_frame_api_input_config,
)
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config, make_ready_file_input_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


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

    @pytest.mark.parametrize(
        ("input_df", "expected"),
        [
            (
                pl.DataFrame(
                    {
                        "segment": pl.Series([], dtype=pl.String),
                        "premium": pl.Series([], dtype=pl.Float64),
                    }
                ),
                [],
            ),
            (
                pl.DataFrame({"segment": ["a"], "premium": [1.0]}),
                [{"segment": "a", "premium": 1.0}],
            ),
            (
                pl.DataFrame({"segment": ["a", "a", "b"], "premium": [1.0, 2.0, 4.0]}),
                [
                    {"segment": "a", "premium": 3.0},
                    {"segment": "b", "premium": 4.0},
                ],
            ),
        ],
        ids=["deploy-live-empty", "deploy-live", "deploy-batch"],
    )
    def test_group_by_admission_uses_injected_deploy_frame_metadata(
        self,
        input_df: pl.DataFrame,
        expected: list[dict[str, object]],
    ) -> None:
        from haute.deploy._scorer import score_graph

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {
                                "path": "",
                                "tables": [
                                    {
                                        "path": "$[:]",
                                        "label": "src",
                                        "emit": True,
                                        "columns": [
                                            {
                                                "name": "segment",
                                                "path": "$[:].segment",
                                                "type": "str",
                                                "selected": True,
                                            },
                                            {
                                                "name": "premium",
                                                "path": "$[:].premium",
                                                "type": "float",
                                                "selected": True,
                                            },
                                        ],
                                    }
                                ],
                            },
                        },
                    },
                    {
                        "id": "agg",
                        "data": {
                            "label": "agg",
                            "nodeType": "polars",
                            "config": {
                                "code": (
                                    "df = src.group_by('segment').agg("
                                    "pl.col('premium').sum().alias('premium'))"
                                )
                            },
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "agg",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        result = score_graph(
            graph=graph,
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="agg",
        )

        assert result.sort("segment").to_dicts() == expected

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
        assert execute.call_args.kwargs.get("dataframe_cache_request") is None
        plan.cleanup(preserve_primary_error=False)

    def test_score_graph_lazy_releases_supplied_context_when_preamble_compile_fails(self):
        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.deploy import _scorer
        from haute.errors import PreambleError

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
                "edges": [{"id": "e1", "source": "src", "target": "out", "sourceHandle": "src"}],
                "preamble": "broken",
            }
        )
        releases: list[str] = []
        context = ExecutionContext(
            operation="deploy-score",
            profile=ExecutionProfile.DEPLOY_LIVE,
            admission_release=lambda: releases.append("released"),
        )
        error = PreambleError("invalid preamble", source_line=7)

        with patch("haute.executor._compile_preamble", side_effect=error):
            with pytest.raises(PreambleError) as exc_info:
                _scorer.score_graph_lazy(
                    graph=graph,
                    input_df=pl.DataFrame({"x": [1.0]}),
                    input_node_ids=["src"],
                    output_node_id="out",
                    execution_context=context,
                )

        assert exc_info.value is error
        assert releases == ["released"]
        context.release_admission()
        assert releases == ["released"]

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

    def test_score_graph_lazy_builds_no_cache_request_for_batch_deploy(self):
        """Batch deploy never materialises through the dataframe execution cache."""
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

        assert execute.call_args.kwargs.get("dataframe_cache_request") is None
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
    def test_direct_parquet_source_uses_its_bundled_artifact(self, tmp_path):
        """Direct Parquet remapping is deploy-only and keeps the config canonical."""
        from haute.deploy._scorer import score_graph

        bundled_path = tmp_path / "lookup.parquet"
        pl.DataFrame({"quote_id": ["001"], "factor": [1.2]}).write_parquet(bundled_path)
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "lookup",
                        "data": {
                            "label": "lookup",
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": "original.parquet",
                                "arguments": {},
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
            artifact_paths={"lookup__original.parquet": str(bundled_path)},
        )

        assert result["quote_id"].to_list() == ["001"]
        assert "cacheMode" not in graph.node_map["lookup"].data.config

    def test_static_data_source_snapshot_preserves_canonical_config(self, tmp_path):
        """Bundled snapshots serve parquet without rewriting the source config."""
        from haute.deploy._scorer import score_graph

        source_path = tmp_path / "lookup.snapshot.parquet"
        pl.DataFrame({"quote_id": ["001"], "factor": [1.2]}).write_parquet(source_path)
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "lookup",
                        "data": {
                            "label": "lookup",
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "csv",
                                "mode": "scan",
                                "path": "lookup.csv",
                                "arguments": {
                                    "schema_overrides": {
                                        "quote_id": "String",
                                        "factor": "Float64",
                                    }
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
            artifact_paths={"lookup__snapshot.part-00000.parquet": str(source_path)},
        )

        assert result["quote_id"].to_list() == ["001"]
        assert result.schema["quote_id"] == pl.String
        assert "cacheMode" not in graph.node_map["lookup"].data.config

    def test_static_data_source_snapshot_receives_projected_columns(self, tmp_path):
        """Bundled snapshots should keep deploy output projection physical."""
        from haute.deploy import _scorer

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "lookup",
                        "data": {
                            "label": "lookup",
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "csv",
                                "mode": "scan",
                                "path": "lookup.csv",
                                "arguments": {},
                            },
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
        snapshot_path = tmp_path / "lookup.snapshot.parquet"
        pl.DataFrame({"keep": [1], "unused": [2]}).write_parquet(snapshot_path)
        result = _scorer.score_graph(
            graph=graph,
            input_df=pl.DataFrame(),
            input_node_ids=[],
            output_node_id="out",
            artifact_paths={"lookup__snapshot.part-00000.parquet": str(snapshot_path)},
            output_fields=["keep"],
        )

        assert result.columns == ["keep"]

    def test_static_data_source_snapshot_runs_post_load_code_for_a_selected_column(self, tmp_path):
        """A snapshot's post-load code may produce a ``selected_columns`` entry."""
        from haute.deploy._scorer import score_graph

        snapshot_path = tmp_path / "lookup.snapshot.parquet"
        pl.DataFrame({"quote_id": ["001"], "sale_date": ["2024-01-01"]}).write_parquet(
            snapshot_path
        )
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "lookup",
                        "data": {
                            "label": "lookup",
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "csv",
                                "mode": "scan",
                                "path": "lookup.csv",
                                "arguments": {},
                                "code": "df = df.with_columns(SaleFlag = pl.lit(1))",
                                "selected_columns": ["quote_id", "SaleFlag"],
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["quote_id", "SaleFlag"]),
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
            artifact_paths={"lookup__snapshot.part-00000.parquet": str(snapshot_path)},
        )

        assert result.columns == ["quote_id", "SaleFlag"]
        assert result["SaleFlag"].to_list() == [1]


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
                                "file_type": "pickle",
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

        with patch("haute._io.load_external_object", return_value=mock_obj):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths=remap,
            )

        assert "ext_val" in result.columns
        assert result["ext_val"].to_list() == [99]

    def test_an_unbundled_external_file_is_the_builders_reading_its_configured_path(
        self, tmp_path, _widen_sandbox_root
    ):
        """With no bundled copy, the Load File builder runs: it reads the configured file."""
        from haute.deploy._scorer import score_graph

        lookup = tmp_path / "lookup.json"
        lookup.write_text(json.dumps({"base": 10.0}))
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
                                "path": str(lookup),
                                "file_type": "json",
                                "code": "df = df.with_columns(base=pl.lit(obj['base']))",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(["x", "base"]),
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "src", "target": "ext", "sourceHandle": "src"},
                    {"id": "e2", "source": "ext", "target": "out"},
                ],
            }
        )

        result = score_graph(
            graph=graph,
            input_df=pl.DataFrame({"x": [1.0]}),
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths={"other__model.cbm": str(tmp_path / "model.cbm")},
        )

        assert result["base"].to_list() == [10.0]

    def test_external_file_code_sees_the_preamble_as_in_the_editor(
        self, tmp_path, _widen_sandbox_root
    ):
        """Deployed External File code calls a preamble helper and matches the preview."""
        from haute.deploy._scorer import score_graph
        from haute.executor import execute_graph

        lookup = tmp_path / "lookup.json"
        lookup.write_text(json.dumps({"base": 10.0}))
        rows = tmp_path / "x.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(rows)
        code = "df = df.with_columns(scaled=pl.lit(scale(obj['base'])))"
        nodes = [
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
                    "config": {"path": str(lookup), "file_type": "json", "code": code},
                },
            },
            {
                "id": "out",
                "data": {
                    "label": "out",
                    "nodeType": "output",
                    "config": make_output_config(["x", "scaled"]),
                },
            },
        ]
        edges = [
            {"id": "e1", "source": "src", "target": "ext", "sourceHandle": "src"},
            {"id": "e2", "source": "ext", "target": "out"},
        ]
        graph = _g({"nodes": nodes, "edges": edges})
        graph.preamble = "def scale(value):\n    return value * 3\n"
        preview_graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataInput",
                            "config": make_ready_file_input_config(str(rows)),
                        },
                    },
                    nodes[1],
                ],
                "edges": [{"id": "e1", "source": "src", "target": "ext"}],
            }
        )
        preview_graph.preamble = graph.preamble

        preview = execute_graph(preview_graph, "ext", source="live")["ext"]
        result = score_graph(
            graph=graph,
            input_df=pl.DataFrame({"x": [1.0]}),
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths={"ext__lookup.json": str(lookup)},
        )

        assert preview.status == "ok", preview.error
        assert [row["scaled"] for row in preview.preview] == [30.0]
        assert result["scaled"].to_list() == [30.0]

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
                                "file_type": "pickle",
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
