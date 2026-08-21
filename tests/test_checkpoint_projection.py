"""Tests for checkpoint projection — backward column analysis.

Covers:
  - get_column_contract          — builder-registered column contracts
  - prepared projection plans     — backward pass computing minimal column sets
  - checkpoint projection in _execute_lazy — end-to-end parquet projection
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._contracts import get_column_contract
from haute._execute_lazy import _execute_lazy
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute.errors import ContractMismatchError, ProjectionImpossibleError
from haute.projection import compute_prepared_plan
from tests._projection_helpers import pair_value
from tests.conftest import make_output_config

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
    """Build children_of from parents_of (same as _execute_lazy does)."""
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
    *,
    strict_projection=False,
):
    return compute_prepared_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node,
        strict_projection=strict_projection,
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


class TestProjectionImpossibleDiagnostics:
    """Strict projection diagnostics for bounded/projection-seeded execution."""

    def test_strict_projection_uses_boundary_for_opaque_fan_in_without_parent_contract(self):
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
            order,
            children_of,
            node_map,
            strict_projection=True,
        )

        assert plan.needed_by_node["join"] == {"quote_id", "premium"}
        assert plan.needed_by_node["left"] is None
        assert plan.needed_by_node["right"] is None

    def test_non_strict_projection_allows_opaque_fan_in(self):
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

        plan = compute_prepared_plan(order, children_of, node_map)

        assert plan.needed_by_node["left"] is None
        assert plan.needed_by_node["right"] is None

    def test_strict_projection_rejects_seed_that_conflicts_with_opaque_sibling(self):
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

        with pytest.raises(ProjectionImpossibleError, match="Projection seed"):
            compute_prepared_plan(
                order,
                children_of,
                node_map,
                required_columns_by_node={"mid": {"a"}},
                strict_projection=True,
            )

    def test_strict_projection_reports_unparseable_join_inference(self):
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

        with pytest.raises(ProjectionImpossibleError, match="could not be parsed"):
            compute_prepared_plan(
                order,
                children_of,
                node_map,
                strict_projection=True,
            )

    def test_strict_projection_allows_concrete_inputs_by_parent_join(self):
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
            order,
            children_of,
            node_map,
            strict_projection=True,
        )

        assert pair_value(plan.edge_demands, "left", "join") == {"quote_id", "left_value"}
        assert pair_value(plan.edge_demands, "right", "join") == {"quote_id", "right_value"}

    def test_lazy_execution_required_seed_enables_strict_projection(self, tmp_path):
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

        outputs, *_ = _execute_lazy(
            graph,
            build_fn,
            target_node_id="out",
            checkpoint_dir=tmp_path,
            required_columns_by_node={"out": {"quote_id"}},
            execution_context=ExecutionContext(
                operation="test",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

        assert outputs["out"].collect().to_dict(as_series=False) == {"quote_id": ["q1"]}

    def test_lazy_execution_bounded_profile_is_strict_without_required_seed(
        self,
        tmp_path,
    ):
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

        outputs, *_ = _execute_lazy(
            graph,
            build_fn,
            target_node_id="out",
            checkpoint_dir=tmp_path,
            execution_context=ExecutionContext(
                operation="test",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

        assert outputs["out"].collect().to_dict(as_series=False) == {"quote_id": ["q1"]}


# ===========================================================================
# Integration: checkpoint projection in _execute_lazy
# ===========================================================================


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

    # Default passthrough (for join / fan-out triggers)
    def join_fn(*dfs):
        result = dfs[0]
        for df in dfs[1:]:
            result = result.join(df, on="key", how="left", suffix="_r")
        return result

    return nid, join_fn, False


class TestCheckpointProjection:
    """Integration tests for checkpoint projection in _execute_lazy."""

    def test_cardinality_only_fanout_checkpoint_retains_one_carrier(self, tmp_path):
        nodes = [
            _source_node("src"),
            _node("mid", NodeType.LIVE_SWITCH),
            _transform_node("left", code="df = df.select(pl.len().alias('row_count'))"),
            _transform_node("right", code="df = df.select(pl.len().alias('row_count'))"),
        ]
        graph = PipelineGraph(
            nodes=nodes,
            edges=[_e("src", "mid"), _e("mid", "left"), _e("mid", "right")],
        )

        def build_fn(node, **_kwargs):
            if node.id == "src":
                return (
                    node.id,
                    lambda: pl.DataFrame({"a": [1, 2, 3], "wide": [4, 5, 6]}).lazy(),
                    True,
                )
            if node.id == "mid":
                return node.id, lambda frame: frame, False
            return (
                node.id,
                lambda frame: frame.select(pl.len().alias("row_count")),
                False,
            )

        outputs, *_ = _execute_lazy(graph, build_fn, checkpoint_dir=tmp_path)

        assert pl.read_parquet(tmp_path / "mid.parquet").columns == ["a"]
        assert outputs["left"].collect().item() == 3
        assert outputs["right"].collect().item() == 3

    def test_projection_drops_unneeded_columns(self, tmp_path):
        """Checkpoint parquet only contains columns needed downstream.

        Source(10 cols) → mid(fan-out) → Output1(fields=[a, b])
                                       → Output2(fields=[b, c])

        mid fans out to 2 children → checkpointed.
        Needed = {a, b} ∪ {b, c} = {a, b, c}.
        """
        nodes = [
            _source_node("src"),
            _node("mid", NodeType.LIVE_SWITCH),  # passthrough, will fan out
            _output_node("o1", fields=["a", "b"]),
            _output_node("o2", fields=["b", "c"]),
        ]
        edges = [_e("src", "mid"), _e("mid", "o1"), _e("mid", "o2")]
        g = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            if node.data.nodeType == NodeType.DATA_INPUT:
                data = {"a": [1], "b": [2], "c": [3], "d": [4], "extra": [5]}
                return node.id, lambda: pl.DataFrame(data).lazy(), True
            if node.data.nodeType == NodeType.OUTPUT:
                mapping = node.data.config.get("outputMapping") or []
                fields = sorted({e["source_column"] for e in mapping if e.get("enabled", True)})
                if fields:
                    return node.id, lambda *dfs, _f=fields: dfs[0].select(_f), False
            return node.id, lambda *dfs: dfs[0], False

        outputs, *_ = _execute_lazy(g, build_fn, checkpoint_dir=tmp_path)

        # The checkpoint for mid should exist
        assert (tmp_path / "mid.parquet").exists()

        # Read the checkpoint: should only have {a, b, c}, NOT d or extra
        checkpoint_df = pl.read_parquet(tmp_path / "mid.parquet")
        assert set(checkpoint_df.columns) == {"a", "b", "c"}

        # Final outputs should still be correct
        o1 = outputs["o1"].collect()
        assert set(o1.columns) == {"a", "b"}
        o2 = outputs["o2"].collect()
        assert set(o2.columns) == {"b", "c"}

    def test_simple_expression_projection_writes_only_needed_fanout_columns(self, tmp_path):
        """Expression dependency extraction narrows fan-out checkpoints.

        Source → mid(fan-out) → POLARS(simple expression) → Output(fields=[a])
                               → Output(fields=[b])
        """
        nodes = [
            _source_node("src"),
            _node("mid", NodeType.LIVE_SWITCH),
            _transform_node("t", code="df = df.with_columns(pl.col('a'))"),
            _output_node("o1", fields=["a"]),
            _output_node("o2", fields=["b"]),
        ]
        edges = [
            _e("src", "mid"),
            _e("mid", "t"),
            _e("t", "o1"),
            _e("mid", "o2"),
        ]
        g = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            if node.data.nodeType == NodeType.DATA_INPUT:
                data = {"a": [1], "b": [2], "c": [3]}
                return node.id, lambda: pl.DataFrame(data).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        _execute_lazy(g, build_fn, checkpoint_dir=tmp_path)

        # The POLARS child now proves it needs only ``a`` while the sibling
        # output needs ``b``; the unrelated ``c`` column should be dropped.
        checkpoint_df = pl.read_parquet(tmp_path / "mid.parquet")
        assert set(checkpoint_df.columns) == {"a", "b"}

    def test_projection_with_banding(self, tmp_path):
        """Banding creates a column; only needed input columns survive checkpoint.

        Source(a, b, c, extra) → Banding(col:a, out:a_band)
        Banding fans out → Output1(fields=[a_band]) + Output2(fields=[a_band, b])

        Banding creates {a_band}, reads {a}.
        Output1 needs {a_band}, Output2 needs {a_band, b}.
        Banding needed from source: ({a_band, b} - {a_band}) | {a} = {a, b}
        Source checkpoint should have {a, b}, NOT {c, extra}.
        """
        nodes = [
            _source_node("src"),
            _banding_node("band", factors=[{"column": "a", "outputColumn": "a_band"}]),
            _output_node("o1", fields=["a_band"]),
            _output_node("o2", fields=["a_band", "b"]),
        ]
        edges = [
            _e("src", "band"),
            _e("band", "o1"),
            _e("band", "o2"),
        ]
        g = PipelineGraph(nodes=nodes, edges=edges)

        outputs, *_ = _execute_lazy(g, _wide_build_fn, checkpoint_dir=tmp_path)

        # Band fans out → checkpointed.  But the SOURCE feeds only band
        # (single child), so source is NOT checkpointed.  Band IS checkpointed.
        assert (tmp_path / "band.parquet").exists()

        # Band's checkpoint should contain: what o1 and o2 need from band's output.
        # o1 needs {a_band}, o2 needs {a_band, b}.
        # Both are passthrough OUTPUTs: produced=∅, referenced=∅.
        # Needed from band: {a_band} ∪ {a_band, b} = {a_band, b}.
        # But band needs to produce a_band, so the checkpoint contains
        # band's output projected to {a_band, b}.
        checkpoint_df = pl.read_parquet(tmp_path / "band.parquet")
        assert set(checkpoint_df.columns) == {"a_band", "b"}

    def test_no_projection_without_checkpoint_dir(self):
        """Without checkpoint_dir, no projection computation happens."""
        nodes = [
            _source_node("src"),
            _output_node("out", fields=["a"]),
        ]
        g = PipelineGraph(nodes=nodes, edges=[_e("src", "out")])

        def build_fn(node, **kw):
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"a": [1], "b": [2]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        # Should not raise, and no files created
        outputs, *_ = _execute_lazy(g, build_fn)
        df = outputs["out"].collect()
        # Without checkpoint_dir, output still has all columns (no projection)
        assert "b" in df.columns or "a" in df.columns

    def test_projection_preserves_all_when_no_output_fields(self, tmp_path):
        """OUTPUT with no fields → None needed → checkpoint keeps everything."""
        nodes = [
            _source_node("src"),
            _node("mid", NodeType.LIVE_SWITCH),
            _output_node("o1", fields=[]),
            _output_node("o2", fields=[]),
        ]
        edges = [_e("src", "mid"), _e("mid", "o1"), _e("mid", "o2")]
        g = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            if node.data.nodeType == NodeType.DATA_INPUT:
                data = {"a": [1], "b": [2], "c": [3]}
                return node.id, lambda: pl.DataFrame(data).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        _execute_lazy(g, build_fn, checkpoint_dir=tmp_path)

        checkpoint_df = pl.read_parquet(tmp_path / "mid.parquet")
        assert set(checkpoint_df.columns) == {"a", "b", "c"}

    def test_projection_with_selected_columns(self, tmp_path):
        """selected_columns + checkpoint projection compose correctly.

        Source → mid(selected_columns=[a, b, c], fan-out)
               → Output1(fields=[a])
               → Output2(fields=[b])

        selected_columns narrows to {a, b, c} first.
        Then projection narrows to {a, b} (union of output fields).
        """
        nodes = [
            _source_node("src"),
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
        ]
        edges = [_e("src", "mid"), _e("mid", "o1"), _e("mid", "o2")]
        g = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            if node.data.nodeType == NodeType.DATA_INPUT:
                data = {"a": [1], "b": [2], "c": [3], "d": [4]}
                return node.id, lambda: pl.DataFrame(data).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        _execute_lazy(g, build_fn, checkpoint_dir=tmp_path)

        # selected_columns={a,b,c} applied first, then projection to {a,b}
        checkpoint_df = pl.read_parquet(tmp_path / "mid.parquet")
        assert set(checkpoint_df.columns) == {"a", "b"}

    def test_join_checkpoint_projected(self, tmp_path):
        """Join node checkpoint only contains columns needed downstream.

        Source1(key, a, extra1) + Source2(key, b, extra2)
          → Join(on key) → Output(fields=[key, a, b])

        Join checkpoint should have {key, a, b}, not extra1/extra2.
        """
        nodes = [
            _source_node("s1"),
            _source_node("s2"),
            _transform_node("j"),  # POLARS node for join
            _output_node("out", fields=["key", "a", "b"]),
        ]
        edges = [_e("s1", "j"), _e("s2", "j"), _e("j", "out")]
        g = PipelineGraph(nodes=nodes, edges=edges)

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
                if fields:
                    return nid, lambda *dfs, _f=fields: dfs[0].select(_f), False

            # Join
            def join_fn(*dfs):
                return dfs[0].join(dfs[1], on="key", how="left")

            return nid, join_fn, False

        outputs, *_ = _execute_lazy(g, build_fn, checkpoint_dir=tmp_path)

        # j has 2 parents → checkpointed. But POLARS is opaque,
        # so needed["j"] is determined by its child OUTPUT(fields=[key,a,b]),
        # but j itself is opaque so j's parents won't be projected.
        # The checkpoint for j WILL be projected because needed["j"]
        # is computed from its children (OUTPUT).
        assert (tmp_path / "j.parquet").exists()
        checkpoint_df = pl.read_parquet(tmp_path / "j.parquet")
        assert set(checkpoint_df.columns) == {"key", "a", "b"}

        out_df = outputs["out"].collect()
        assert set(out_df.columns) == {"key", "a", "b"}

    def test_join_parent_checkpoints_use_inputs_by_parent(self, tmp_path):
        """Join-feeder checkpoints are projected with parent-specific needs."""
        nodes = [
            _source_node("left_src"),
            _source_node("right_src"),
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
            _e("left_src", "left_mid"),
            _e("right_src", "right_mid"),
            _e("left_mid", "j"),
            _e("right_mid", "j"),
            _e("j", "out"),
        ]
        g = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            nid = node.id
            if nid == "left_src":
                data = {
                    "key": [1, 2],
                    "left_value": [10, 20],
                    "left_unused": [999, 999],
                }
                return nid, lambda d=data: pl.DataFrame(d).lazy(), True
            if nid == "right_src":
                data = {
                    "key": [1, 2],
                    "right_value": [100, 200],
                    "right_unused": [888, 888],
                }
                return nid, lambda d=data: pl.DataFrame(d).lazy(), True
            if nid == "j":
                return nid, lambda *dfs: dfs[0].join(dfs[1], on="key", how="left"), False
            if nid == "out":
                mapping = node.data.config.get("outputMapping") or []
                fields = sorted({e["source_column"] for e in mapping if e.get("enabled", True)})
                return nid, lambda *dfs, _f=fields: dfs[0].select(_f), False
            return nid, lambda *dfs: dfs[0], False

        outputs, *_ = _execute_lazy(g, build_fn, checkpoint_dir=tmp_path)

        left_checkpoint = pl.read_parquet(tmp_path / "left_mid.parquet")
        right_checkpoint = pl.read_parquet(tmp_path / "right_mid.parquet")
        join_checkpoint = pl.read_parquet(tmp_path / "j.parquet")

        assert left_checkpoint.columns == ["key", "left_value"]
        assert right_checkpoint.columns == ["key", "right_value"]
        assert join_checkpoint.columns == ["key", "left_value", "right_value"]

        out_df = outputs["out"].collect().sort("key")
        assert out_df.to_dict(as_series=False) == {
            "key": [1, 2],
            "left_value": [10, 20],
            "right_value": [100, 200],
        }

    def test_checkpoint_projection_raises_when_required_column_missing(self, tmp_path):
        """Checkpoint projection should fail loudly on impossible schemas."""
        nodes = [
            _source_node("src"),
            _node("mid", NodeType.LIVE_SWITCH),
            _output_node("needs_present", fields=["a"]),
            _output_node("needs_missing", fields=["missing"]),
        ]
        edges = [
            _e("src", "mid"),
            _e("mid", "needs_present"),
            _e("mid", "needs_missing"),
        ]
        g = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **kw):
            if node.id == "src":
                return node.id, lambda: pl.DataFrame({"a": [1]}).lazy(), True
            if node.data.nodeType == NodeType.OUTPUT:
                mapping = node.data.config.get("outputMapping") or []
                fields = sorted({e["source_column"] for e in mapping if e.get("enabled", True)})
                return node.id, lambda *dfs, _f=fields: dfs[0].select(_f), False
            return node.id, lambda *dfs: dfs[0], False

        with pytest.raises(ContractMismatchError, match="missing"):
            _execute_lazy(g, build_fn, checkpoint_dir=tmp_path)

    def test_checkpoint_rejects_a_builder_that_omits_its_declared_output(self, tmp_path):
        """A produced column is validated where the structural checkpoint writes it."""
        nodes = [
            _source_node("src"),
            _banding_node(
                "mid",
                factors=[{"column": "a", "outputColumn": "band"}],
            ),
            _transform_node("left", code="df = mid.select(['band'])"),
            _transform_node("right", code="df = mid.select(['band'])"),
            _transform_node("sink", code="df = left.join(right, on='band')"),
        ]
        edges = [
            _e("src", "mid"),
            _e("mid", "left"),
            _e("mid", "right"),
            _e("left", "sink"),
            _e("right", "sink"),
        ]
        graph = PipelineGraph(nodes=nodes, edges=edges)

        def build_fn(node, **_kwargs):
            if node.id == "src":
                return node.id, lambda: pl.LazyFrame({"a": [1, 2]}), True
            if node.id == "mid":
                # Deliberately violate the registered banding contract so the
                # checkpoint's runtime-schema assertion is the observer.
                return node.id, lambda frame: frame, False
            if node.id in {"left", "right"}:
                return node.id, lambda frame: frame.select("band"), False
            return node.id, lambda left, right: left.join(right, on="band"), False

        with pytest.raises(
            ContractMismatchError,
            match="Checkpoint projection references columns missing",
        ):
            _execute_lazy(
                graph,
                build_fn,
                target_node_id="sink",
                checkpoint_dir=tmp_path,
                required_columns_by_node={"sink": {"band"}},
            )
