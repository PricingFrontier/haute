"""Deploy validation, test-quote scoring and pruning edge cases.

``load_test_quote_file``, ``validate_deploy`` and ``score_test_quotes`` refuse malformed inputs,
and the deploy pruner keeps only the edges of the deploy graph.
"""

from __future__ import annotations

import polars as pl
import pytest

from tests._deploy_helpers import make_resolved_deploy as _make_resolved
from tests._deploy_internals_helpers import (
    PIPELINE_FILE,
    _single_frame_api_input_config,
)
from tests._project import use_project
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


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

    def test_direct_parquet_validation_does_not_require_snapshot(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """A direct Parquet source is validated in place, without cache lookup."""
        from haute.deploy._config import DeployConfig
        from haute.deploy._validators import validate_deploy

        source = tmp_path / "lookup.parquet"
        pl.DataFrame({"factor": [1.2]}).write_parquet(source)
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "lookup",
                        "data": {
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": "lookup.parquet",
                                "arguments": {},
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {"nodeType": "output", "config": make_output_config([])},
                    },
                ],
                "edges": [{"id": "e1", "source": "lookup", "target": "out"}],
            }
        )
        config = DeployConfig(pipeline_file=tmp_path / "pipeline.py", model_name="test")
        resolved = _make_resolved(
            config=config,
            pruned_graph=graph,
            input_node_ids=["lookup"],
            output_node_id="out",
            artifacts={"lookup__lookup.parquet": source},
            input_schema={"factor": "Float64"},
            output_schema={"factor": "Float64"},
        )

        def source_cache_must_not_run(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("direct Parquet validation must not access the source cache")

        use_project(tmp_path)
        monkeypatch.setattr(
            "haute._input_providers.source_cache_identity", source_cache_must_not_run
        )

        validate_deploy(resolved)

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
                    _node(
                        "switch",
                        "liveSwitch",
                        {
                            "input_scenario_map": {
                                "live_src": "live",
                                "shared": "test_batch",
                            }
                        },
                    ),
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
