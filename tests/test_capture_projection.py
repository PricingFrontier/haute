"""Tests for capture projection — backward column analysis.

Covers:
  - get_column_contract          — builder-registered column contracts
  - prepared projection plans     — backward pass computing minimal column sets
  - capture projection in a planned lazy walk — what a planned run writes into the
    shared snapshot store at a join, fan-out, or join feeder
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._contracts import get_column_contract
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._native_memory_limit import native_memory_backend_scope
from haute._node_snapshots import NodeSnapshotStore
from haute._seed_plans import CaptureKind, SeedPlanRequest, open_resolved_seed_plan
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute.errors import ContractMismatchError
from haute.execution import execute_lazy_graph
from haute.projection import compute_prepared_plan
from tests._projection_helpers import adjacency_edges, edge_keys_for_pair, pair_value
from tests.conftest import make_output_config


def _edge_reason(plan, source, target):
    """Return the diagnostic reason for the unique ``source -> target`` edge."""
    keys = edge_keys_for_pair(plan.diagnostics.edge_reasons, source, target)
    assert len(keys) == 1
    return plan.diagnostics.edge_reasons[keys[0]]


pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _node(nid: str, node_type: NodeType, **config) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=node_type, config=config),
    )


def _source_node(nid: str) -> GraphNode:
    return _node(nid, NodeType.DATA_INPUT)


def _output_node(nid: str, fields: list[str] | None = None) -> GraphNode:
    return _node(nid, NodeType.OUTPUT, **make_output_config(fields or []))


def _banding_node(
    nid: str,
    factors: list[dict] | None = None,
) -> GraphNode:
    return _node(nid, NodeType.BANDING, factors=factors or [])


def _rating_step_node(
    nid: str,
    tables: list[dict] | None = None,
) -> GraphNode:
    return _node(
        nid,
        NodeType.RATING_STEP,
        tables=tables or [],
    )


def _model_score_node(
    nid: str,
    output_column: str = "prediction",
    code: str = "",
    source_type: str = "",
    run_id: str = "",
) -> GraphNode:
    return _node(
        nid,
        NodeType.MODEL_SCORE,
        output_column=output_column,
        code=code,
        sourceType=source_type,
        run_id=run_id,
    )


def _scenario_expander_node(
    nid: str,
    column_name: str = "",
    step_column: str = "scenario_index",
    code: str = "",
) -> GraphNode:
    return _node(
        nid,
        NodeType.SCENARIO_EXPANDER,
        column_name=column_name,
        step_column=step_column,
        code=code,
    )


def _transform_node(nid: str, *, code: str = "") -> GraphNode:
    return _node(nid, NodeType.POLARS, **({"code": code} if code else {}))


# ===========================================================================
# get_column_contract — produced and referenced columns per node type
# ===========================================================================


class TestGetColumnContract:
    """Tests for the builder-registered column contracts."""

    # -- BANDING --------------------------------------------------------

    def test_banding_produces_output_columns(self):
        produced, referenced = get_column_contract(
            NodeType.BANDING,
            {
                "factors": [
                    {"column": "age", "outputColumn": "age_band"},
                    {"column": "region", "outputColumn": "region_band"},
                ]
            },
        )
        assert produced == {"age_band", "region_band"}
        assert referenced == {"age", "region"}

    def test_banding_empty_factors(self):
        produced, referenced = get_column_contract(NodeType.BANDING, {"factors": []})
        assert produced == set()
        assert referenced == set()

    def test_banding_missing_output_column(self):
        produced, referenced = get_column_contract(
            NodeType.BANDING,
            {"factors": [{"column": "age"}]},
        )
        assert produced == set()
        assert referenced == {"age"}

    def test_banding_none_factors(self):
        produced, referenced = get_column_contract(NodeType.BANDING, {"factors": None})
        assert produced == set()
        assert referenced == set()

    def test_banding_no_config(self):
        produced, referenced = get_column_contract(NodeType.BANDING, {})
        assert produced == set()
        assert referenced == set()

    # -- RATING_STEP ----------------------------------------------------

    def test_rating_step_produces_and_references(self):
        produced, referenced = get_column_contract(
            NodeType.RATING_STEP,
            {
                "tables": [
                    {"factors": ["age", "region"], "outputColumn": "age_factor"},
                    {"factors": ["vehicle_type"], "outputColumn": "vehicle_factor"},
                ],
                "combinedOutputs": [
                    {
                        "outputColumn": "combined",
                        "operation": "multiply",
                        "baseValue": 1,
                    }
                ],
            },
        )
        assert produced == {"age_factor", "vehicle_factor", "combined"}
        assert referenced == {"age", "region", "vehicle_type"}

    def test_rating_step_no_combined(self):
        produced, _ = get_column_contract(
            NodeType.RATING_STEP,
            {"tables": [{"factors": ["x"], "outputColumn": "x_out"}]},
        )
        assert produced == {"x_out"}

    def test_rating_step_empty_combined_outputs_multiple_tables_is_noop(self):
        produced, referenced = get_column_contract(
            NodeType.RATING_STEP,
            {
                "tables": [
                    {"factors": ["age"], "outputColumn": "age_factor"},
                    {"factors": ["region"], "outputColumn": "region_factor"},
                ],
                "combinedOutputs": [],
            },
        )
        assert produced == {"age_factor", "region_factor"}
        assert referenced == {"age", "region"}

    def test_rating_step_empty_tables(self):
        produced, referenced = get_column_contract(NodeType.RATING_STEP, {"tables": []})
        assert produced == set()
        assert referenced == set()

    def test_rating_step_combined_outputs_produced(self):
        produced, referenced = get_column_contract(
            NodeType.RATING_STEP,
            {
                "tables": [
                    {"factors": ["age"], "outputColumn": "age_factor"},
                    {"factors": ["region"], "outputColumn": "region_factor"},
                ],
                "combinedOutputs": [
                    {
                        "outputColumn": "technical_premium",
                        "operation": "multiply",
                        "baseValue": 100,
                    },
                    {"outputColumn": "additive_score", "operation": "add", "baseValue": 0},
                ],
            },
        )
        assert produced == {
            "age_factor",
            "region_factor",
            "technical_premium",
            "additive_score",
        }
        assert referenced == {"age", "region"}

    def test_rating_step_empty_combined_outputs_is_noop(self):
        produced, referenced = get_column_contract(
            NodeType.RATING_STEP,
            {
                "tables": [{"factors": ["age"], "outputColumn": "age_factor"}],
                "combinedOutputs": [],
            },
        )
        assert produced == {"age_factor"}
        assert referenced == {"age"}

    def test_rating_step_combined_output_base_only_produced(self):
        produced, referenced = get_column_contract(
            NodeType.RATING_STEP,
            {
                "tables": [],
                "combinedOutputs": [
                    {
                        "outputColumn": "technical_premium",
                        "operation": "multiply",
                        "baseValue": 100,
                    },
                ],
            },
        )
        assert produced == {"technical_premium"}
        assert referenced == set()

    def test_rating_step_with_code_is_opaque(self):
        produced, referenced = get_column_contract(
            NodeType.RATING_STEP,
            {
                "tables": [{"factors": ["x"], "outputColumn": "x_factor"}],
                "code": "df = df.with_columns(pl.col('y').alias('z'))",
            },
        )
        assert produced is None
        assert referenced is None

    # -- MODEL_SCORE ----------------------------------------------------

    def test_model_score_default_output(self):
        produced, _ = get_column_contract(NodeType.MODEL_SCORE, {})
        assert produced == {"prediction"}

    def test_model_score_custom_output(self):
        produced, _ = get_column_contract(
            NodeType.MODEL_SCORE,
            {"output_column": "score"},
        )
        assert produced == {"score"}

    def test_model_score_no_source_type_opaque_referenced(self):
        _, referenced = get_column_contract(NodeType.MODEL_SCORE, {"sourceType": ""})
        assert referenced is None

    def test_model_score_with_code_opaque_referenced(self):
        _, referenced = get_column_contract(
            NodeType.MODEL_SCORE,
            {"sourceType": "run", "code": "df = df.filter(pl.col('x') > 0)"},
        )
        assert referenced is None

    def test_model_score_with_loadable_model(self):
        mock_model = MagicMock()
        mock_model.feature_names = ["feat_a", "feat_b", "feat_c"]

        with patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model):
            produced, referenced = get_column_contract(
                NodeType.MODEL_SCORE,
                {"sourceType": "run", "run_id": "abc123", "task": "regression"},
            )
        assert produced == {"prediction"}
        assert referenced == {"feat_a", "feat_b", "feat_c"}

    def test_model_score_model_load_fails(self):
        """Item #25 (fail-loud policy): a real MLflow load error must
        propagate — previously the contract-detection silently swallowed
        it and returned opaque columns, masking config/infra problems."""
        with patch("haute._mlflow_io.load_mlflow_model", side_effect=Exception("fail")):
            with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: patches with generic Exception to test propagation
                get_column_contract(
                    NodeType.MODEL_SCORE,
                    {"sourceType": "run", "run_id": "abc123", "task": "regression"},
                )

    # -- SCENARIO_EXPANDER ----------------------------------------------

    def test_scenario_expander_produces_columns(self):
        produced, referenced = get_column_contract(
            NodeType.SCENARIO_EXPANDER,
            {"column_name": "scenario_value", "step_column": "scenario_index"},
        )
        assert produced == {"scenario_value", "scenario_index"}
        assert referenced == set()

    def test_scenario_expander_with_code_opaque(self):
        produced, referenced = get_column_contract(
            NodeType.SCENARIO_EXPANDER,
            {"column_name": "val", "code": "df = df.filter(True)"},
        )
        assert produced is None
        assert referenced is None

    def test_scenario_expander_empty_column_name(self):
        produced, _ = get_column_contract(
            NodeType.SCENARIO_EXPANDER,
            {"column_name": "  ", "step_column": "idx"},
        )
        assert produced == {"idx"}

    # -- OPTIMISER_APPLY ------------------------------------------------

    def test_optimiser_apply_unconfigured_reads_nothing(self):
        # An unconfigured optimiser_apply node is a pass-through at
        # runtime (``_build_optimiser_apply`` returns ``_passthrough_fn``
        # when no artifact source is set), so the contract reports the
        # read side as ``set()`` rather than opaque.  This distinguishes
        # "declared pass-through, truly reads nothing" from "opaque
        # because the schema comes from a runtime artifact" (tested
        # below).
        produced, referenced = get_column_contract(NodeType.OPTIMISER_APPLY, {})
        assert produced == set()
        assert referenced == set()

    def test_optimiser_apply_custom_version_column_unconfigured(self):
        produced, referenced = get_column_contract(
            NodeType.OPTIMISER_APPLY,
            {"version_column": "opt_ver"},
        )
        assert produced == set()
        assert referenced == set()

    def test_optimiser_apply_custom_optimised_value_column_unconfigured(self):
        produced, referenced = get_column_contract(
            NodeType.OPTIMISER_APPLY,
            {"optimised_value_column": "selected_price_factor"},
        )
        assert produced == set()
        assert referenced == set()

    def test_optimiser_apply_with_artifact_source_is_opaque(self):
        # Once an artifact source is configured the read side is
        # honestly opaque — the column dependencies come from the
        # artifact at runtime (quote_id, scenario_index, constraints,
        # etc.) which is not introspectable without loading it.
        produced, referenced = get_column_contract(
            NodeType.OPTIMISER_APPLY,
            {"artifact_path": "/tmp/opt.json", "sourceType": "file"},
        )
        assert produced == {"__optimiser_version__"}
        assert referenced is None

    def test_optimiser_apply_with_custom_optimised_value_column(self):
        produced, referenced = get_column_contract(
            NodeType.OPTIMISER_APPLY,
            {
                "artifact_path": "/tmp/opt.json",
                "sourceType": "file",
                "optimised_value_column": "selected_price_factor",
            },
        )
        assert produced == {"__optimiser_version__", "selected_price_factor"}
        assert referenced is None

    # -- Passthrough types ----------------------------------------------

    @pytest.mark.parametrize(
        "node_type",
        [
            NodeType.OUTPUT,
            NodeType.DATA_OUTPUT,
            NodeType.LIVE_SWITCH,
            NodeType.MODELLING,
            NodeType.OPTIMISER,
            NodeType.SUBMODEL,
            NodeType.SUBMODEL_PORT,
        ],
    )
    def test_passthrough_types(self, node_type: NodeType):
        produced, referenced = get_column_contract(node_type, {})
        assert produced == set()
        assert referenced == set()

    def test_constant_empty_config(self):
        """Constant with no values config declares produced={} (no columns)."""
        produced, referenced = get_column_contract(NodeType.CONSTANT, {})
        assert produced == {"constant"}
        assert referenced == set()

    def test_constant_with_values(self):
        """Constant with named values declares those as produced columns."""
        config = {"values": [{"name": "rate", "value": "1.5"}, {"name": "fee", "value": "10"}]}
        produced, referenced = get_column_contract(NodeType.CONSTANT, config)
        assert produced == {"rate", "fee"}
        assert referenced == set()

    # -- Opaque types (no registered contract) --------------------------

    @pytest.mark.parametrize(
        "node_type",
        [NodeType.POLARS, NodeType.EXTERNAL_FILE, NodeType.API_INPUT, NodeType.DATA_INPUT],
    )
    def test_opaque_types(self, node_type: NodeType):
        produced, referenced = get_column_contract(node_type, {})
        assert produced is None
        assert referenced is None


# ===========================================================================
# Prepared projection plan — backward pass
# ===========================================================================


def _build_children_of(order, parents_of):
    """Build children_of from parents_of (as the prepared execution does)."""
    children_of = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            if pid in children_of:
                children_of[pid].append(nid)
    return children_of


def _needed_by_node(
    order,
    children_of,
    node_map,
    required_columns_by_node=None,
):
    return compute_prepared_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node,
        relevant_edges=adjacency_edges(order, children_of),
    ).needed_by_node


class TestComputeNeededColumns:
    """Tests for the backward column analysis pass."""

    def test_linear_chain_output_with_fields(self):
        """Source → Banding → Output(fields=[age_band]).

        Output needs {age_band}.  Banding creates {age_band} and reads {age}.
        So Source needs {age} — age_band is produced by banding, not needed from source.
        """
        nodes = [
            _source_node("src"),
            _banding_node("band", factors=[{"column": "age", "outputColumn": "age_band"}]),
            _output_node("out", fields=["age_band"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "band", "out"]
        parents_of = {"src": [], "band": ["src"], "out": ["band"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        assert needed["out"] == {"age_band"}
        # needed["band"] = what downstream needs from band's output = {age_band}
        assert needed["band"] == {"age_band"}
        # Banding creates {age_band}, reads {age}.
        # needed["src"] = (band_needed - band_produced) | band_referenced
        #               = ({age_band} - {age_band}) | {age} = {age}
        assert needed["src"] == {"age"}

    def test_output_without_fields_propagates_none(self):
        """Output with no fields → needs all columns → None propagates."""
        nodes = [_source_node("src"), _output_node("out", fields=[])]
        node_map = {n.id: n for n in nodes}
        order = ["src", "out"]
        parents_of = {"src": [], "out": ["src"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        assert needed["out"] is None
        assert needed["src"] is None

    def test_simple_polars_expression_projects_dependencies(self):
        """Simple POLARS expressions project exact dependencies backward."""
        nodes = [
            _source_node("src"),
            _transform_node("t", code="df = df.with_columns(pl.col('z'))"),
            _output_node("out", fields=["z"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "t", "out"]
        parents_of = {"src": [], "t": ["src"], "out": ["t"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        # needed["t"] = what downstream needs from t's output = {"z"}
        assert needed["t"] == {"z"}
        # ``with_columns(pl.col("z"))`` is dependency-extractable.
        assert needed["src"] == {"z"}

    def test_diamond_union(self):
        """Fan-out + reconverge: union of both paths.

        Source → BandA(col:a, out:a_band) → Output1(fields=[a_band, x])
        Source → BandB(col:b, out:b_band) → Output2(fields=[b_band, y])
        """
        nodes = [
            _source_node("src"),
            _banding_node("ba", factors=[{"column": "a", "outputColumn": "a_band"}]),
            _banding_node("bb", factors=[{"column": "b", "outputColumn": "b_band"}]),
            _output_node("o1", fields=["a_band", "x"]),
            _output_node("o2", fields=["b_band", "y"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "ba", "bb", "o1", "o2"]
        parents_of = {
            "src": [],
            "ba": ["src"],
            "bb": ["src"],
            "o1": ["ba"],
            "o2": ["bb"],
        }
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        # BandA: output needs {a_band, x}, band creates {a_band}, reads {a}
        #   → needs from src: {x, a}
        # BandB: output needs {b_band, y}, band creates {b_band}, reads {b}
        #   → needs from src: {y, b}
        # Source: union = {a, b, x, y}
        assert needed["src"] == {"a", "b", "x", "y"}

    def test_model_score_features_propagate(self):
        """Model features propagate upstream; output column doesn't."""
        mock_model = MagicMock()
        mock_model.feature_names = ["feat_a", "feat_b"]

        nodes = [
            _source_node("src"),
            _model_score_node(
                "ms",
                output_column="pred",
                source_type="run",
                run_id="abc123",  # required after Item #25 fail-loud check
            ),
            _output_node("out", fields=["pred", "extra_col"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "ms", "out"]
        parents_of = {"src": [], "ms": ["src"], "out": ["ms"]}
        children_of = _build_children_of(order, parents_of)

        with patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model):
            needed = _needed_by_node(order, children_of, node_map)

        # Output needs {pred, extra_col}.
        # ModelScore creates {pred}, reads {feat_a, feat_b}.
        # → needs from src: {extra_col, feat_a, feat_b}
        assert needed["src"] == {"extra_col", "feat_a", "feat_b"}

    def test_required_columns_seed_terminal_non_output(self):
        """Callers can seed exact needs for direct non-OUTPUT targets."""
        nodes = [
            _source_node("src"),
            _node("opt", NodeType.OPTIMISER),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "opt"]
        parents_of = {"src": [], "opt": ["src"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(
            order,
            children_of,
            node_map,
            required_columns_by_node={"opt": {"quote_id", "conversion_prediction"}},
        )

        assert needed["opt"] == {"quote_id", "conversion_prediction"}
        assert needed["src"] == {"quote_id", "conversion_prediction"}

    def test_required_columns_seed_unions_with_downstream_needs(self):
        """A seed on a non-terminal node supplements descendant needs."""
        nodes = [
            _source_node("src"),
            _node("mid", NodeType.LIVE_SWITCH),
            _output_node("out", fields=["a"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "mid", "out"]
        parents_of = {"src": [], "mid": ["src"], "out": ["mid"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(
            order,
            children_of,
            node_map,
            required_columns_by_node={"mid": {"b"}},
        )

        assert needed["mid"] == {"a", "b"}
        assert needed["src"] == {"a", "b"}

    def test_passthrough_chain_propagates_fields(self):
        """Chain of passthrough nodes correctly propagates OUTPUT fields."""
        nodes = [
            _source_node("src"),
            _node("sw", NodeType.LIVE_SWITCH),
            _output_node("out", fields=["a", "b"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "sw", "out"]
        parents_of = {"src": [], "sw": ["src"], "out": ["sw"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        assert needed["sw"] == {"a", "b"}
        assert needed["src"] == {"a", "b"}

    def test_terminal_non_output_returns_none(self):
        """A terminal DATA_OUTPUT (not quote OUTPUT) → needed is None."""
        nodes = [_source_node("src"), _node("sink", NodeType.DATA_OUTPUT)]
        node_map = {n.id: n for n in nodes}
        order = ["src", "sink"]
        parents_of = {"src": [], "sink": ["src"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        assert needed["sink"] is None
        assert needed["src"] is None

    def test_mixed_children_any_none_makes_parent_none(self):
        """Fan-out: one child needs None → parent needs None."""
        nodes = [
            _source_node("src"),
            _output_node("o1", fields=["x"]),
            _output_node("o2", fields=[]),  # no fields = None
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "o1", "o2"]
        parents_of = {"src": [], "o1": ["src"], "o2": ["src"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        assert needed["src"] is None

    def test_mixed_children_opaque_child_makes_parent_none(self):
        """Fan-out to opaque POLARS child + known OUTPUT → parent is None."""
        nodes = [
            _source_node("src"),
            _transform_node("t"),
            _output_node("out", fields=["x"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "t", "out"]
        parents_of = {"src": [], "t": ["src"], "out": ["src"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        # POLARS child is opaque → src needs None
        assert needed["src"] is None

    def test_empty_graph(self):
        """Single node with no children."""
        nodes = [_source_node("src")]
        node_map = {n.id: n for n in nodes}

        needed = _needed_by_node(["src"], {"src": []}, node_map)

        assert needed["src"] is None  # terminal non-OUTPUT

    def test_rating_step_subtraction(self):
        """RatingStep output column is subtracted from upstream needs.

        Source → RatingStep(factors=[col], outputColumn=col_factor) → Output(fields=[col_factor])
        Source should only need {col}, not {col_factor}.
        """
        nodes = [
            _source_node("src"),
            _rating_step_node(
                "rs",
                tables=[
                    {"factors": ["col"], "outputColumn": "col_factor"},
                ],
            ),
            _output_node("out", fields=["col_factor"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "rs", "out"]
        parents_of = {"src": [], "rs": ["src"], "out": ["rs"]}
        children_of = _build_children_of(order, parents_of)

        needed = _needed_by_node(order, children_of, node_map)

        # RatingStep creates {col_factor}, reads {col}
        # → from source: {col}
        assert needed["src"] == {"col"}


class TestUnprovableProjectionDiagnostics:
    """Cannot-prove projection cases keep a boundary and record a reason."""

    def test_projection_uses_boundary_for_opaque_fan_in_without_parent_contract(self):
        nodes = [
            _source_node("left"),
            _source_node("right"),
            _transform_node("join"),
            _output_node("out", fields=["quote_id", "premium"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["left", "right", "join", "out"]
        parents_of = {
            "left": [],
            "right": [],
            "join": ["left", "right"],
            "out": ["join"],
        }
        children_of = _build_children_of(order, parents_of)

        plan = compute_prepared_plan(
            order, children_of, node_map, relevant_edges=adjacency_edges(order, children_of)
        )

        assert plan.needed_by_node["join"] == {"quote_id", "premium"}
        assert plan.needed_by_node["left"] is None
        assert plan.needed_by_node["right"] is None

    def test_seed_blocked_by_opaque_fan_out_keeps_boundary_with_reason(self):
        nodes = [
            _source_node("src"),
            _node("mid", NodeType.LIVE_SWITCH),
            _output_node("known", fields=["a"]),
            _transform_node("opaque"),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "mid", "known", "opaque"]
        parents_of = {
            "src": [],
            "mid": ["src"],
            "known": ["mid"],
            "opaque": ["mid"],
        }
        children_of = _build_children_of(order, parents_of)

        plan = compute_prepared_plan(
            order,
            children_of,
            node_map,
            required_columns_by_node={"mid": {"a"}},
            relevant_edges=adjacency_edges(order, children_of),
        )

        assert plan.needed_by_node["mid"] is None
        reason = plan.diagnostics.node_reasons["mid"]
        assert reason.rule == "projection_seed_blocked_by_opaque_fan_out"
        assert reason.details["seeded_columns"] == ("a",)

    def test_unparseable_join_inference_keeps_boundary_with_reason(self):
        nodes = [
            _source_node("left"),
            _source_node("right"),
            _node(
                "join",
                NodeType.POLARS,
                code="df = left.join(right, on='quote_id'",
                contract={
                    "inputs": ["quote_id", "premium"],
                    "outputs": [],
                    "inputs_by_parent": {
                        "left": ["quote_id", "premium"],
                        "right": ["quote_id", "premium"],
                    },
                },
            ),
            _output_node("out", fields=["quote_id", "premium_right"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["left", "right", "join", "out"]
        parents_of = {
            "left": [],
            "right": [],
            "join": ["left", "right"],
            "out": ["join"],
        }
        children_of = _build_children_of(order, parents_of)

        plan = compute_prepared_plan(
            order, children_of, node_map, relevant_edges=adjacency_edges(order, children_of)
        )

        assert plan.needed_by_node["left"] is None
        assert plan.needed_by_node["right"] is None
        reason = _edge_reason(plan, "left", "join")
        assert reason.rule == "fan_in_join_unparsed"
        assert "join" in reason.message

    def test_dynamic_join_arguments_keep_boundary_with_reason(self):
        nodes = [
            _source_node("left"),
            _source_node("right"),
            _node(
                "join",
                NodeType.POLARS,
                code=(
                    "suffix = compute_suffix()\ndf = left.join(right, on='quote_id', suffix=suffix)"
                ),
                contract={
                    "inputs": ["quote_id", "premium"],
                    "outputs": [],
                    "inputs_by_parent": {
                        "left": ["quote_id", "premium"],
                        "right": ["quote_id", "premium"],
                    },
                },
            ),
            _output_node("out", fields=["quote_id", "premium"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["left", "right", "join", "out"]
        parents_of = {
            "left": [],
            "right": [],
            "join": ["left", "right"],
            "out": ["join"],
        }
        children_of = _build_children_of(order, parents_of)

        plan = compute_prepared_plan(
            order, children_of, node_map, relevant_edges=adjacency_edges(order, children_of)
        )

        assert plan.needed_by_node["left"] is None
        assert plan.needed_by_node["right"] is None
        reason = _edge_reason(plan, "left", "join")
        assert reason.rule == "fan_in_join_dynamic_arguments"
        assert "join" in reason.message

    def test_projection_allows_concrete_inputs_by_parent_join(self):
        nodes = [
            _source_node("left"),
            _source_node("right"),
            _node(
                "join",
                NodeType.POLARS,
                code="df = left.join(right, on='quote_id', how='left')",
                contract={
                    "inputs": ["quote_id", "left_value", "right_value"],
                    "outputs": [],
                    "inputs_by_parent": {
                        "left": ["quote_id", "left_value"],
                        "right": ["quote_id", "right_value"],
                    },
                },
            ),
            _output_node("out", fields=["quote_id", "left_value", "right_value"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["left", "right", "join", "out"]
        parents_of = {
            "left": [],
            "right": [],
            "join": ["left", "right"],
            "out": ["join"],
        }
        children_of = _build_children_of(order, parents_of)

        plan = compute_prepared_plan(
            order, children_of, node_map, relevant_edges=adjacency_edges(order, children_of)
        )

        assert pair_value(plan.edge_demands, "left", "join") == {"quote_id", "left_value"}
        assert pair_value(plan.edge_demands, "right", "join") == {"quote_id", "right_value"}

    def test_lazy_execution_required_seed_projects_fan_in(self):
        nodes = [
            _source_node("left"),
            _source_node("right"),
            _transform_node("join"),
            _output_node("out", fields=["quote_id"]),
        ]
        edges = [_e("left", "join"), _e("right", "join"), _e("join", "out")]
        graph = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            if node.id == "left":
                return node.id, lambda: pl.DataFrame({"quote_id": ["q1"]}).lazy(), True
            if node.id == "right":
                return node.id, lambda: pl.DataFrame({"quote_id": ["q1"]}).lazy(), True
            if node.id == "join":
                return node.id, lambda *dfs: dfs[0].join(dfs[1], on="quote_id"), False
            return node.id, lambda *dfs: dfs[0], False

        outputs, *_ = execute_lazy_graph(
            graph,
            build_fn,
            target_node_id="out",
            required_columns_by_node={"out": {"quote_id"}},
            execution_context=ExecutionContext(
                operation="test",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

        assert outputs["out"].collect().to_dict(as_series=False) == {"quote_id": ["q1"]}

    def test_lazy_execution_bounded_profile_without_required_seed(self):
        nodes = [
            _source_node("left"),
            _source_node("right"),
            _transform_node("join"),
            _output_node("out", fields=["quote_id"]),
        ]
        edges = [_e("left", "join"), _e("right", "join"), _e("join", "out")]
        graph = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            if node.id == "left":
                return node.id, lambda: pl.DataFrame({"quote_id": ["q1"]}).lazy(), True
            if node.id == "right":
                return node.id, lambda: pl.DataFrame({"quote_id": ["q1"]}).lazy(), True
            if node.id == "join":
                return node.id, lambda *dfs: dfs[0].join(dfs[1], on="quote_id"), False
            return node.id, lambda *dfs: dfs[0], False

        outputs, *_ = execute_lazy_graph(
            graph,
            build_fn,
            target_node_id="out",
            execution_context=ExecutionContext(
                operation="test",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

        assert outputs["out"].collect().to_dict(as_series=False) == {"quote_id": ["q1"]}


# ===========================================================================
# Integration: capture projection in a planned lazy walk
# ===========================================================================


def _run_planned(
    root: Path,
    graph: PipelineGraph,
    build_fn: Any,
    *,
    target: str,
    required: dict[str, Any] | None = None,
    source: str = "live",
    generations: dict[str, Path] | None = None,
) -> tuple[dict[str, pl.DataFrame], dict[str, list[str]], dict[str, CaptureKind]]:
    """Execute *graph* toward *target* under a seed plan, as every bounded caller runs.

    Returns the collected outputs, the columns each capture wrote into the
    shared snapshot store, and each capture's kind; *generations*, when given,
    receives each capture's published data file. The injected sources carry no
    readable metadata, so a hard worker cap bounds any join instead of an
    estimate.
    """
    store = NodeSnapshotStore(root)
    request = SeedPlanRequest(
        graph=graph,
        target_node_id=target,
        source=source,
        profile=ExecutionProfile.LAZY_SINK,
        required_columns_by_node=required,
    )
    context = create_admitted_execution_context(
        operation="capture_projection_test",
        profile=ExecutionProfile.LAZY_SINK,
    )
    try:
        with (
            native_memory_backend_scope("rlimit"),
            open_resolved_seed_plan(request, store=store) as plan,
        ):
            outputs, *_ = execute_lazy_graph(
                graph,
                build_fn,
                target_node_id=target,
                source=source,
                required_columns_by_node=required,
                execution_context=context,
                prepare_inputs=False,
                snapshot_plan=plan,
            )
            frames = {
                node_id: frame.collect()
                for node_id, frame in outputs.items()
                if isinstance(frame, pl.LazyFrame)
            }
            written: dict[str, list[str]] = {}
            for node_id, capture in plan.decision.captures.items():
                latest = store.latest_generation(capture.identity)
                if latest is not None:
                    written[node_id] = pl.read_parquet(latest.generation.data_paths[0]).columns
                    if generations is not None:
                        generations[node_id] = latest.generation.data_paths[0]
            kinds = {node_id: capture.kind for node_id, capture in plan.decision.captures.items()}
    finally:
        context.release_admission()
    return frames, written, kinds


def _output_select_build_fn(data: dict[str, list[Any]]):
    """Sources yield *data*; an OUTPUT selects its fields; ``both`` stacks its inputs."""

    def build_fn(node: GraphNode, **_kw: Any):
        if node.data.nodeType == NodeType.DATA_INPUT:
            return node.id, lambda: pl.DataFrame(data).lazy(), True
        if node.data.nodeType == NodeType.OUTPUT:
            mapping = node.data.config.get("outputMapping") or []
            fields = sorted({e["source_column"] for e in mapping if e.get("enabled", True)})
            if fields:
                return node.id, lambda *dfs, _f=fields: dfs[0].select(_f), False
        if node.id == "both":
            return node.id, lambda *dfs: pl.concat(dfs, how="diagonal_relaxed"), False
        return node.id, lambda *dfs: dfs[0], False

    return build_fn


def _both(reads: dict[str, list[str]]) -> GraphNode:
    """A single target that stacks its inputs, reading exactly *reads* from each parent.

    A plan runs toward one target, so a fan-out's branches meet here; its
    per-parent contract keeps it from widening what the branches read.
    """
    return _node(
        "both",
        NodeType.POLARS,
        contract={
            "inputs": sorted({column for columns in reads.values() for column in columns}),
            "outputs": [],
            "inputs_by_parent": reads,
        },
    )


def _reads_of(graph: PipelineGraph) -> dict[str, list[str]]:
    """The caller's demand at ``both``: exactly what its contract says it reads."""
    return {"both": list(graph.node_map["both"].data.config["contract"]["inputs"])}


def _fan_out_to_both(
    mid: GraphNode, left: GraphNode, right: GraphNode, *, reads: dict[str, list[str]]
) -> PipelineGraph:
    """``src → pre → mid → left, right → both``: lineage fanning out at ``mid``
    with a costly segment.
    """
    return PipelineGraph(
        nodes=[
            _source_node("src"),
            _rating_step_node("costly_pre"),
            mid,
            left,
            right,
            _both(reads),
        ],
        edges=[
            _e("src", "costly_pre"),
            _e("costly_pre", mid.id),
            _e(mid.id, left.id),
            _e(mid.id, right.id),
            _e(left.id, "both"),
            _e(right.id, "both"),
        ],
    )


def _wide_build_fn(node: GraphNode, source_names=None, **kwargs):
    """Build function producing a wide DataFrame (many columns)."""
    nid = node.id
    nt = node.data.nodeType

    if nt == NodeType.DATA_INPUT:
        # Source with 10 columns: key, a, b, c, d, e, f, g, h, extra
        data = {
            "key": [1, 2, 3],
            "a": [10, 20, 30],
            "b": [11, 21, 31],
            "c": [12, 22, 32],
            "d": [13, 23, 33],
            "e": [14, 24, 34],
            "f": [15, 25, 35],
            "g": [16, 26, 36],
            "h": [17, 27, 37],
            "extra": [99, 99, 99],
        }
        return nid, lambda d=data: pl.DataFrame(d).lazy(), True

    if nt == NodeType.BANDING:
        factors = node.data.config.get("factors") or []

        def banding_fn(*dfs, _factors=factors):
            lf = dfs[0]
            for f in _factors:
                col = f.get("column", "")
                out = f.get("outputColumn", "")
                if col and out:
                    lf = lf.with_columns(pl.col(col).cast(pl.Float64).alias(out))
            return lf

        return nid, banding_fn, False

    if nt == NodeType.OUTPUT:
        mapping = node.data.config.get("outputMapping") or []
        fields = sorted({e["source_column"] for e in mapping if e.get("enabled", True)})

        def output_fn(*dfs, _fields=fields):
            lf = dfs[0]
            if _fields:
                lf = lf.select(_fields)
            return lf

        return nid, output_fn, False

    if nid == "both":
        return nid, lambda *dfs: pl.concat(dfs, how="diagonal_relaxed"), False

    # Default passthrough (for join / fan-out triggers)
    def join_fn(*dfs):
        result = dfs[0]
        for df in dfs[1:]:
            result = result.join(df, on="key", how="left", suffix="_r")
        return result

    return nid, join_fn, False


class TestCaptureProjection:
    """A planned run writes each capture with only the columns the run needs there."""

    def test_cardinality_only_fanout_capture_retains_one_carrier(self, tmp_path):
        graph = _fan_out_to_both(
            _node("mid", NodeType.LIVE_SWITCH),
            _transform_node("left", code="df = df.select(pl.len().alias('row_count'))"),
            _transform_node("right", code="df = df.select(pl.len().alias('row_count'))"),
            reads={"left": ["row_count"], "right": ["row_count"]},
        )

        def build_fn(node, **_kwargs):
            if node.id == "src":
                return (
                    node.id,
                    lambda: pl.DataFrame({"a": [1, 2, 3], "wide": [4, 5, 6]}).lazy(),
                    True,
                )
            if node.id in {"mid", "costly_pre"}:
                return node.id, lambda frame: frame, False
            if node.id == "both":
                return node.id, lambda *dfs: pl.concat(dfs), False
            return (
                node.id,
                lambda frame: frame.select(pl.len().alias("row_count")),
                False,
            )

        frames, written, kinds = _run_planned(
            tmp_path, graph, build_fn, target="both", required=_reads_of(graph)
        )

        assert kinds["mid"] is CaptureKind.STRUCTURAL
        assert written["mid"] == ["a"]
        assert frames["both"]["row_count"].to_list() == [3, 3]

    def test_projection_drops_unneeded_columns(self, tmp_path):
        """A fan-out capture holds only what its consumers read: {a, b} ∪ {b, c}."""
        graph = _fan_out_to_both(
            _node("mid", NodeType.LIVE_SWITCH),
            _output_node("o1", fields=["a", "b"]),
            _output_node("o2", fields=["b", "c"]),
            reads={"o1": ["a", "b"], "o2": ["b", "c"]},
        )
        build_fn = _output_select_build_fn({"a": [1], "b": [2], "c": [3], "d": [4], "extra": [5]})

        frames, written, kinds = _run_planned(
            tmp_path, graph, build_fn, target="both", required=_reads_of(graph)
        )

        assert kinds["mid"] is CaptureKind.STRUCTURAL
        assert set(written["mid"]) == {"a", "b", "c"}
        assert set(frames["both"].columns) == {"a", "b", "c"}

    def test_simple_expression_projection_writes_only_needed_fanout_columns(self, tmp_path):
        """Expression dependency extraction narrows a fan-out capture."""
        graph = PipelineGraph(
            nodes=[
                _source_node("src"),
                _rating_step_node("costly_pre"),
                _node("mid", NodeType.LIVE_SWITCH),
                _transform_node("t", code="df = df.with_columns(pl.col('a'))"),
                _output_node("o1", fields=["a"]),
                _output_node("o2", fields=["b"]),
                _both({"o1": ["a"], "o2": ["b"]}),
            ],
            edges=[
                _e("src", "costly_pre"),
                _e("costly_pre", "mid"),
                _e("mid", "t"),
                _e("t", "o1"),
                _e("mid", "o2"),
                _e("o1", "both"),
                _e("o2", "both"),
            ],
        )
        build_fn = _output_select_build_fn({"a": [1], "b": [2], "c": [3]})

        _frames, written, kinds = _run_planned(
            tmp_path, graph, build_fn, target="both", required=_reads_of(graph)
        )

        assert kinds["mid"] is CaptureKind.STRUCTURAL
        # The POLARS child proves it needs only ``a`` while the sibling output
        # needs ``b``; the unrelated ``c`` column is not written.
        assert set(written["mid"]) == {"a", "b"}

    def test_projection_with_banding(self, tmp_path):
        """Banding creates a column; its capture holds that and what its consumers read."""
        graph = _fan_out_to_both(
            _banding_node("band", factors=[{"column": "a", "outputColumn": "a_band"}]),
            _output_node("o1", fields=["a_band"]),
            _output_node("o2", fields=["a_band", "b"]),
            reads={"o1": ["a_band"], "o2": ["a_band", "b"]},
        )

        _frames, written, kinds = _run_planned(
            tmp_path, graph, _wide_build_fn, target="both", required=_reads_of(graph)
        )

        assert kinds["band"] is CaptureKind.STRUCTURAL
        assert set(written["band"]) == {"a_band", "b"}

    def test_projection_preserves_all_without_a_concrete_demand(self, tmp_path):
        """No fields and an opaque consumer → no concrete demand → the capture keeps all."""
        graph = _fan_out_to_both(
            _node("mid", NodeType.LIVE_SWITCH),
            _output_node("o1", fields=[]),
            _output_node("o2", fields=[]),
            reads={"o1": ["a"], "o2": ["a"]},
        )
        build_fn = _output_select_build_fn({"a": [1], "b": [2], "c": [3]})

        # No caller demand: ``both`` is a terminal opaque output, so it reads
        # every column of its branches, and they of ``mid``.
        _frames, written, kinds = _run_planned(tmp_path, graph, build_fn, target="both")

        assert kinds["mid"] is CaptureKind.STRUCTURAL
        assert set(written["mid"]) == {"a", "b", "c"}

    def test_projection_with_selected_columns(self, tmp_path):
        """selected_columns narrows to {a, b, c}; the capture then to {a, b}."""
        graph = _fan_out_to_both(
            GraphNode(
                id="mid",
                data=NodeData(
                    label="mid",
                    nodeType=NodeType.LIVE_SWITCH,
                    config={"selected_columns": ["a", "b", "c"]},
                ),
            ),
            _output_node("o1", fields=["a"]),
            _output_node("o2", fields=["b"]),
            reads={"o1": ["a"], "o2": ["b"]},
        )
        build_fn = _output_select_build_fn({"a": [1], "b": [2], "c": [3], "d": [4]})

        _frames, written, kinds = _run_planned(
            tmp_path, graph, build_fn, target="both", required=_reads_of(graph)
        )

        assert kinds["mid"] is CaptureKind.STRUCTURAL
        assert set(written["mid"]) == {"a", "b"}

    def test_join_capture_projected(self, tmp_path):
        """A join's capture holds only what its consumer reads, not either side's extras."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node("j"),
                _output_node("out", fields=["key", "a", "b"]),
            ],
            edges=[_e("s1", "j"), _e("s2", "j"), _e("j", "out")],
        )

        def build_fn(node, **kw):
            nid = node.id
            if nid == "s1":
                d = {"key": [1], "a": [10], "extra1": [99]}
                return nid, lambda d=d: pl.DataFrame(d).lazy(), True
            if nid == "s2":
                d = {"key": [1], "b": [20], "extra2": [88]}
                return nid, lambda d=d: pl.DataFrame(d).lazy(), True
            if nid == "out":
                mapping = node.data.config.get("outputMapping") or []
                fields = sorted({e["source_column"] for e in mapping if e.get("enabled", True)})
                return nid, lambda *dfs, _f=fields: dfs[0].select(_f), False
            return nid, lambda *dfs: dfs[0].join(dfs[1], on="key", how="left"), False

        frames, written, kinds = _run_planned(tmp_path, g, build_fn, target="out")

        assert kinds["j"] is CaptureKind.STRUCTURAL
        assert set(written["j"]) == {"key", "a", "b"}
        assert set(frames["out"].columns) == {"key", "a", "b"}

    def test_join_feeder_captures_use_inputs_by_parent(self, tmp_path):
        """Join-feeder captures are projected with parent-specific needs."""
        nodes = [
            _source_node("left_src"),
            _source_node("right_src"),
            _rating_step_node("left_pre"),
            _rating_step_node("right_pre"),
            _node("left_mid", NodeType.LIVE_SWITCH),
            _node("right_mid", NodeType.LIVE_SWITCH),
            _node(
                "j",
                NodeType.POLARS,
                contract={
                    "inputs": ["key", "left_value", "right_value"],
                    "outputs": [],
                    "inputs_by_parent": {
                        "left_mid": ["key", "left_value"],
                        "right_mid": ["key", "right_value"],
                    },
                },
            ),
            _output_node("out", fields=["key", "left_value", "right_value"]),
        ]
        edges = [
            _e("left_src", "left_pre"),
            _e("left_pre", "left_mid"),
            _e("right_src", "right_pre"),
            _e("right_pre", "right_mid"),
            _e("left_mid", "j"),
            _e("right_mid", "j"),
            _e("j", "out"),
        ]
        g = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            nid = node.id
            if nid == "left_src":
                data = {"key": [1, 2], "left_value": [10, 20], "left_unused": [999, 999]}
                return nid, lambda d=data: pl.DataFrame(d).lazy(), True
            if nid == "right_src":
                data = {"key": [1, 2], "right_value": [100, 200], "right_unused": [888, 888]}
                return nid, lambda d=data: pl.DataFrame(d).lazy(), True
            if nid == "j":
                return nid, lambda *dfs: dfs[0].join(dfs[1], on="key", how="left"), False
            if nid == "out":
                mapping = node.data.config.get("outputMapping") or []
                fields = sorted({e["source_column"] for e in mapping if e.get("enabled", True)})
                return nid, lambda *dfs, _f=fields: dfs[0].select(_f), False
            return nid, lambda *dfs: dfs[0], False

        frames, written, kinds = _run_planned(tmp_path, g, build_fn, target="out")

        assert kinds["left_mid"] is CaptureKind.STRUCTURAL
        assert kinds["right_mid"] is CaptureKind.STRUCTURAL
        assert kinds["j"] is CaptureKind.STRUCTURAL
        assert written["left_mid"] == ["key", "left_value"]
        assert written["right_mid"] == ["key", "right_value"]
        assert written["j"] == ["key", "left_value", "right_value"]
        assert frames["out"].sort("key").to_dict(as_series=False) == {
            "key": [1, 2],
            "left_value": [10, 20],
            "right_value": [100, 200],
        }

    def test_a_read_column_missing_below_a_capture_fails_loudly(self, tmp_path):
        """A run that reads a column no node produces fails loudly, before any capture."""
        graph = _fan_out_to_both(
            _node("mid", NodeType.LIVE_SWITCH),
            _output_node("needs_present", fields=["a"]),
            _output_node("needs_missing", fields=["missing"]),
            reads={"needs_present": ["a"], "needs_missing": ["missing"]},
        )
        build_fn = _output_select_build_fn({"a": [1]})

        with pytest.raises(ContractMismatchError, match="missing"):
            _run_planned(tmp_path, graph, build_fn, target="both", required=_reads_of(graph))

    def test_capture_rejects_a_builder_that_omits_its_declared_output(self, tmp_path):
        """A produced column is validated where the capture writes it."""
        nodes = [
            _source_node("src"),
            _rating_step_node("costly_pre"),
            _banding_node(
                "mid",
                factors=[{"column": "a", "outputColumn": "band"}],
            ),
            _transform_node("left", code="df = mid.select(['band'])"),
            _transform_node("right", code="df = mid.select(['band'])"),
            _transform_node("sink", code="df = left.join(right, on='band')"),
        ]
        edges = [
            _e("src", "costly_pre"),
            _e("costly_pre", "mid"),
            _e("mid", "left"),
            _e("mid", "right"),
            _e("left", "sink"),
            _e("right", "sink"),
        ]
        graph = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **_kwargs):
            if node.id == "src":
                return node.id, lambda: pl.LazyFrame({"a": [1, 2]}), True
            if node.id in {"mid", "costly_pre"}:
                # Deliberately violate the registered banding contract so the
                # capture's runtime-schema assertion is the observer.
                return node.id, lambda frame: frame, False
            if node.id in {"left", "right"}:
                return node.id, lambda frame: frame.select("band"), False
            return node.id, lambda left, right: left.join(right, on="band"), False

        with pytest.raises(ContractMismatchError, match="lacks columns"):
            _run_planned(tmp_path, graph, build_fn, target="sink", required={"sink": {"band"}})

    def test_case_distinct_node_ids_capture_separately(self, tmp_path):
        """Case-insensitive filesystems must not alias separate graph nodes' captures."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node("join"),
                _transform_node("JOIN"),
                _transform_node("both"),
            ],
            edges=[
                _e("s1", "join"),
                _e("s2", "join"),
                _e("s1", "JOIN"),
                _e("s2", "JOIN"),
                _e("join", "both"),
                _e("JOIN", "both"),
            ],
        )

        def build_fn(node, **kwargs):
            # The two joins write different data, so an aliased generation shows.
            if node.id in {"join", "JOIN"}:
                tag = node.id

                def tagged_join(left, right, _tag=tag):
                    return left.join(right, on="key", how="left", suffix="_r").with_columns(
                        pl.lit(_tag).alias("tag")
                    )

                return node.id, tagged_join, False
            return _wide_build_fn(node, **kwargs)

        generations: dict[str, Path] = {}
        frames, _written, _kinds = _run_planned(
            tmp_path, g, build_fn, target="both", generations=generations
        )

        # Two generations whose paths differ even case-folded, each holding its
        # own node's rows.
        join_path, upper_path = generations["join"], generations["JOIN"]
        assert str(join_path).casefold() != str(upper_path).casefold()
        assert join_path.is_file() and upper_path.is_file()
        assert pl.read_parquet(join_path)["tag"].unique().to_list() == ["join"]
        assert pl.read_parquet(upper_path)["tag"].unique().to_list() == ["JOIN"]
        assert sorted(frames["both"]["tag"].to_list()) == ["JOIN"] * 3 + ["join"] * 3

    @pytest.mark.parametrize(
        "malicious_node_id",
        ["../escaped", r"..\escaped", "nested/escaped", r"nested\escaped"],
        ids=["up", "upw", "nest", "nestw"],
    )
    def test_id_not_path(self, tmp_path, malicious_node_id):
        """Capture storage treats graph node ids as data, never as path syntax.

        (Short test and root names keep the store's staging paths inside Windows
        MAX_PATH under pytest-xdist's temporary roots.)
        """
        root = tmp_path / "p"
        root.mkdir()
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node(malicious_node_id),
                _output_node("out", fields=["key", "a"]),
            ],
            edges=[
                _e("s1", malicious_node_id),
                _e("s2", malicious_node_id),
                _e(malicious_node_id, "out"),
            ],
        )

        frames, written, _kinds = _run_planned(root, g, _wide_build_fn, target="out")

        assert malicious_node_id in written
        assert not (tmp_path / "escaped.parquet").exists()
        assert not list(tmp_path.glob("escaped*"))
        assert not (root / "nested").exists()
        assert frames["out"].height == 3

    def test_live_switch_with_two_parents_is_captured(self, tmp_path):
        """A live switch that keeps both parents (unknown scenario) is a join."""
        g = PipelineGraph(
            nodes=[
                _source_node("live_in"),
                _source_node("batch_in"),
                GraphNode(
                    id="sw",
                    data=NodeData(
                        label="sw",
                        nodeType=NodeType.LIVE_SWITCH,
                        config={"input_scenario_map": {"live_in": "live", "batch_in": "batch"}},
                    ),
                ),
                _output_node("out", fields=["key"]),
            ],
            edges=[_e("live_in", "sw"), _e("batch_in", "sw"), _e("sw", "out")],
        )

        # A scenario outside the map keeps both edges.
        _frames, written, kinds = _run_planned(
            tmp_path, g, _wide_build_fn, target="out", source="unknown"
        )

        assert kinds["sw"] is CaptureKind.STRUCTURAL
        assert "sw" in written

    def test_a_parent_is_released_once_its_consumer_is_captured(self, tmp_path):
        """A parent whose only consumer is captured has its frame dropped.

        Graph:  src → t → g (group-by: a materialising capture) → out

        ``t`` is not itself captured (one child, feeding no join), so once ``g``
        is written its frame has no remaining consumer and is released.
        """
        g = PipelineGraph(
            nodes=[
                _source_node("src"),
                _transform_node("t", code="df = df.with_columns(pl.col('a') + 1)"),
                _transform_node("g", code="df = df.group_by('key').agg(pl.col('a').sum())"),
                _output_node("out", fields=["key", "a"]),
            ],
            edges=[_e("src", "t"), _e("t", "g"), _e("g", "out")],
        )

        def build_fn(node, **_kw):
            if node.id == "src":
                data = {"key": [1, 1, 2], "a": [1, 2, 3], "extra": [0, 0, 0]}
                return node.id, lambda: pl.DataFrame(data).lazy(), True
            if node.id == "t":
                return node.id, lambda frame: frame.with_columns(pl.col("a") + 1), False
            if node.id == "g":
                return node.id, lambda frame: frame.group_by("key").agg(pl.col("a").sum()), False
            return node.id, lambda frame: frame.select("key", "a"), False

        frames, written, kinds = _run_planned(tmp_path, g, build_fn, target="out")

        assert kinds["g"] is CaptureKind.MATERIALISING
        assert "t" not in kinds
        assert "t" not in frames
        assert frames["out"].sort("key").to_dict(as_series=False) == {"key": [1, 2], "a": [5, 4]}
