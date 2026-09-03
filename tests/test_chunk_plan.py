"""Chunk-plan capability contract tests."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
import structlog

from haute.chunking import (
    ChunkCapabilityKind,
    ChunkCapabilityStatus,
    ChunkPlanRequest,
    chunk_capability_declarations,
    chunk_plan,
    classify_chunk_local_polars_code,
    validate_chunk_capability_declarations,
)
from haute.errors import (
    ChunkMemoryRiskError,
    ChunkPlanUnsupportedError,
    ChunkUserCodeUnsupportedError,
)
from haute.graph_utils import NodeType
from haute.projection import materialising_operators_by_node, prepare_graph
from tests.conftest import make_edge, make_graph, make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _node(node_id: str, node_type: str, config: dict[str, object] | None = None):
    config = dict(config or {})
    if node_type == "dataInput" and "path" in config:
        suffix = Path(str(config["path"])).suffix.lower().lstrip(".")
        formats = {
            "jsonl": "ndjson",
            "ndjson": "ndjson",
            "arrow": "ipc",
            "feather": "ipc",
            "ipc": "ipc",
        }
        config = {
            **config,
            "inputType": "file",
            "format": formats.get(suffix, suffix),
        }
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": node_type,
            "config": config,
        },
    }


def _write_projected_source(tmp_path: Path, *, extra_columns: int = 0) -> Path:
    path = tmp_path / f"projected_{extra_columns}.parquet"
    data: dict[str, list[object]] = {
        "quote_id": ["q1", "q2", "q3", "q4"],
        "premium": [100.0, 200.0, 300.0, 400.0],
    }
    for index in range(extra_columns):
        data[f"feature_{index}"] = [float(index)] * 4
    pl.DataFrame(data).write_parquet(path)
    return path


def _source_output_graph(path: Path, output_fields: list[str]):
    return make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(path)}),
                _node("out", "output", make_output_config(output_fields)),
            ],
            "edges": [make_edge("source", "out").model_dump()],
        }
    )


def test_chunk_group_by_evidence_is_receiver_aware(tmp_path: Path) -> None:
    """The chunk planner's ``has_group_by`` evidence ignores non-frame receivers."""
    path = _write_projected_source(tmp_path)

    def _prepared(code: str):
        graph = make_graph(
            {
                "nodes": [
                    _node("source", "dataInput", {"path": str(path)}),
                    _node("shape", "polars", {"code": code}),
                    _node("out", "output", make_output_config(["premium"])),
                ],
                "edges": [
                    make_edge("source", "shape").model_dump(),
                    make_edge("shape", "out").model_dump(),
                ],
            }
        )
        return prepare_graph(graph, "out", source="live")

    expression_prepared = _prepared(
        "stats = pl.col('premium').list.group_by('quote_id')\ndf = df.filter(pl.col('premium') > 0)"
    )
    assert not materialising_operators_by_node(
        expression_prepared.order,
        expression_prepared.node_map,
        relevant_edges=expression_prepared.relevant_edges,
    )

    # An unbound name may be a preamble frame, so its group-by is evidence.
    preamble_prepared = _prepared(
        "stats = lookup.group_by('quote_id')\ndf = df.filter(pl.col('premium') > 0)"
    )
    assert dict(
        materialising_operators_by_node(
            preamble_prepared.order,
            preamble_prepared.node_map,
            relevant_edges=preamble_prepared.relevant_edges,
        )
    ) == {"shape": "group_by"}

    frame_prepared = _prepared(
        "df = df.group_by('quote_id').agg(pl.col('premium').sum().alias('premium'))"
    )
    assert dict(
        materialising_operators_by_node(
            frame_prepared.order,
            frame_prepared.node_map,
            relevant_edges=frame_prepared.relevant_edges,
        )
    ) == {"shape": "group_by"}


def test_chunk_capability_registry_mentions_every_node_type() -> None:
    declarations = chunk_capability_declarations()

    assert set(declarations) == set(NodeType)
    validate_chunk_capability_declarations()


def test_chunk_capability_registry_is_immutable() -> None:
    declarations = chunk_capability_declarations()

    with pytest.raises(TypeError):
        declarations[NodeType.POLARS] = declarations[NodeType.DATA_INPUT]  # type: ignore[index]


def test_chunk_capability_registry_declares_unsupported_types_explicitly() -> None:
    declarations = chunk_capability_declarations()

    unsupported = {
        node_type
        for node_type, declaration in declarations.items()
        if declaration.status == ChunkCapabilityStatus.UNSUPPORTED
    }
    assert unsupported == {
        NodeType.API_INPUT,
        NodeType.CONSTANT,
        NodeType.DATA_OUTPUT,
        NodeType.EDGE_JOIN,
        NodeType.EXPLORE,
        NodeType.EXTERNAL_FILE,
        NodeType.LIVE_SWITCH,
        NodeType.MODELLING,
        NodeType.OPTIMISER,
        NodeType.SUBMODEL,
        NodeType.SUBMODEL_PORT,
    }
    for node_type in unsupported:
        declaration = declarations[node_type]
        assert declaration.rules == frozenset({"unsupported_v1"})
        assert declaration.note


def test_chunk_capability_registry_validation_rejects_drift() -> None:
    declarations = dict(chunk_capability_declarations())
    declarations.pop(NodeType.POLARS)

    with pytest.raises(RuntimeError, match="every node type exactly once"):
        validate_chunk_capability_declarations(declarations)

    declarations = dict(chunk_capability_declarations())
    source_declaration = declarations[NodeType.DATA_INPUT]
    declarations[NodeType.POLARS] = source_declaration

    with pytest.raises(RuntimeError, match="wrong node type"):
        validate_chunk_capability_declarations(declarations)


def test_chunk_capability_registry_validation_is_fail_loud_not_a_global_fallback() -> None:
    """An explicitly empty registry must FAIL LOUD, not silently fall back.

    Regression pin for F259: the guard was changed from
    ``declarations or _CHUNK_CAPABILITY_DECLARATIONS`` (an empty dict is falsy,
    so it was silently replaced by the module registry and never raised) to
    ``declarations is None``.  Passing ``{}`` explicitly must therefore validate
    the caller's own (empty) mapping and raise, rather than quietly validating
    the global registry by accident and reporting success.
    """
    with pytest.raises(RuntimeError, match="every node type exactly once"):
        validate_chunk_capability_declarations({})


def test_chunk_plan_accepts_v1_chunk_safe_chain():
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": "quotes.parquet"}),
                _node(
                    "banding",
                    "banding",
                    {"factors": [{"column": "age", "breaks": [25, 50]}]},
                ),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {"column": "premium", "min": 0.9, "max": 1.1, "steps": 3},
                ),
                _node("out", "output", make_output_config(["quote_id", "premium"])),
            ],
            "edges": [
                make_edge("source", "banding").model_dump(),
                make_edge("banding", "scenario").model_dump(),
                make_edge("scenario", "out").model_dump(),
            ],
        }
    )

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=17,
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )

    assert plan.chunk_size == 17
    assert plan.source_node_id == "source"
    assert plan.node_ids == ("source", "banding", "scenario", "out")
    assert plan.capabilities["source"].kind == ChunkCapabilityKind.MAP_ONLY
    assert plan.capabilities["scenario"].expands_rows is True
    assert plan.required_columns_by_node["out"] == frozenset({"quote_id", "premium"})


def test_byte_budgeted_chunk_plan_shrinks_wide_projected_schema(tmp_path: Path) -> None:
    source_path = _write_projected_source(tmp_path, extra_columns=24)
    narrow_fields = ["quote_id", "premium"]
    wide_fields = narrow_fields + [f"feature_{index}" for index in range(24)]

    narrow_plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(source_path, narrow_fields),
            target_node_id="out",
            chunk_size=None,
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": narrow_fields},
        )
    )
    wide_plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(source_path, wide_fields),
            target_node_id="out",
            chunk_size=None,
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": wide_fields},
        )
    )

    assert wide_plan.source_chunk_size < narrow_plan.source_chunk_size
    assert wide_plan.chunk_size < narrow_plan.chunk_size
    assert narrow_plan.chunk_size_policy == "byte_budget"
    assert wide_plan.chunk_size_policy == "byte_budget"
    assert narrow_plan.estimated_target_row_bytes is not None
    assert wide_plan.estimated_target_row_bytes is not None
    assert wide_plan.estimated_target_row_bytes > narrow_plan.estimated_target_row_bytes


def test_byte_budgeted_chunk_plan_ignores_unused_wide_source_columns(
    tmp_path: Path,
) -> None:
    narrow_source_path = _write_projected_source(tmp_path, extra_columns=0)
    wide_source_path = _write_projected_source(tmp_path, extra_columns=24)

    narrow_source_plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(narrow_source_path, ["quote_id", "premium"]),
            target_node_id="out",
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )
    wide_source_plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(wide_source_path, ["quote_id", "premium"]),
            target_node_id="out",
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )

    assert wide_source_plan.chunk_size == narrow_source_plan.chunk_size
    assert wide_source_plan.source_chunk_size == narrow_source_plan.source_chunk_size
    assert (
        wide_source_plan.estimated_target_row_bytes == narrow_source_plan.estimated_target_row_bytes
    )


def test_byte_budgeted_chunk_plan_accounts_for_scenario_row_expansion(
    tmp_path: Path,
) -> None:
    source_path = _write_projected_source(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {
                        "column_name": "scenario_value",
                        "min_value": 0.9,
                        "max_value": 1.1,
                        "steps": 4,
                        "step_column": "scenario_index",
                    },
                ),
                _node(
                    "out",
                    "output",
                    make_output_config(
                        [
                            "quote_id",
                            "premium",
                            "scenario_index",
                            "scenario_value",
                        ]
                    ),
                ),
            ],
            "edges": [
                make_edge("source", "scenario").model_dump(),
                make_edge("scenario", "out").model_dump(),
            ],
        }
    )

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=None,
            target_chunk_bytes=1_024,
            required_columns_by_node={
                "out": {"quote_id", "premium", "scenario_index", "scenario_value"}
            },
        )
    )

    assert plan.row_expansion_factor == 4
    assert plan.source_chunk_size == max(1, plan.chunk_size // 4)
    assert plan.source_chunk_size < plan.chunk_size


def test_byte_budgeted_chunk_plan_rejects_one_source_row_expanding_past_budget(
    tmp_path: Path,
) -> None:
    source_path = _write_projected_source(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "scenario",
                    "scenarioExpander",
                    {
                        "column_name": "scenario_value",
                        "min_value": 0.9,
                        "max_value": 1.1,
                        "steps": 100,
                        "step_column": "scenario_index",
                    },
                ),
                _node(
                    "out",
                    "output",
                    make_output_config(["quote_id", "premium", "scenario_index", "scenario_value"]),
                ),
            ],
            "edges": [
                make_edge("source", "scenario").model_dump(),
                make_edge("scenario", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(ChunkMemoryRiskError) as exc_info:
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                target_chunk_bytes=1_024,
                required_columns_by_node={
                    "out": {"quote_id", "premium", "scenario_index", "scenario_value"}
                },
            )
        )

    assert exc_info.value.estimated_target_row_bytes <= 1_024
    assert exc_info.value.estimated_minimum_chunk_bytes > 1_024
    assert exc_info.value.row_expansion_factor == 100
    assert exc_info.value.reason_code == "minimum_source_row_expansion_exceeds_budget"


def test_byte_budgeted_chunk_plan_costs_downstream_created_wide_column(
    tmp_path: Path,
) -> None:
    """The byte budget must reflect a wide column created *after* the source.

    Costing the row width from the source-only schema leaves a downstream
    ``String`` column absent, so it collapses to the ~64-byte default and the
    chunk size is picked many times too large (an OOM under-bound).  The width
    must come from the target node's real output schema.
    """
    source_path = _write_projected_source(tmp_path)  # quote_id, premium
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "widen",
                    "polars",
                    {"code": "df = source.with_columns(pl.lit('x' * 500).alias('wide'))"},
                ),
                _node("out", "output", make_output_config(["quote_id", "premium", "wide"])),
            ],
            "edges": [
                make_edge("source", "widen").model_dump(),
                make_edge("widen", "out").model_dump(),
            ],
        }
    )

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            target_chunk_bytes=8_192,
            required_columns_by_node={"out": {"quote_id", "premium", "wide"}},
        )
    )

    assert plan.estimated_target_row_bytes is not None
    # The 500-char downstream string dominates the row width; the old source-only
    # estimate could never exceed ~80 bytes for this row.
    assert plan.estimated_target_row_bytes >= 500
    assert plan.chunk_size == max(1, 8_192 // plan.estimated_target_row_bytes)


def test_byte_budgeted_chunk_plan_rejects_one_target_row_wider_than_budget(
    tmp_path: Path,
) -> None:
    source_path = _write_projected_source(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "widen",
                    "polars",
                    {"code": "df = source.with_columns(pl.lit('x' * 500).alias('wide'))"},
                ),
                _node("out", "output", make_output_config(["quote_id", "wide"])),
            ],
            "edges": [
                make_edge("source", "widen").model_dump(),
                make_edge("widen", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(ChunkMemoryRiskError) as exc_info:
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                target_chunk_bytes=128,
                required_columns_by_node={"out": {"quote_id", "wide"}},
            )
        )

    error = exc_info.value
    assert not isinstance(error, ChunkPlanUnsupportedError)
    assert error.target_node_id == "out"
    assert error.reason_code == "single_row_exceeds_budget"
    assert error.estimated_target_row_bytes > error.target_chunk_bytes == 128
    assert error.to_payload()["error_code"] == "chunk_memory_risk"


def test_byte_budget_target_build_failure_is_logged_before_reclassify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An engine failure during target-schema derivation must be logged loudly
    before it is reclassified as unsupported.

    Regression pin for the F015 fail-loud scope: ``_target_output_lazyframe``
    wraps the whole production engine and reclassifies ANY failure as
    ``ChunkPlanUnsupportedError``, routing callers to the full executor and
    quietly disabling the byte-budget OOM guard.  A genuine engine defect must
    therefore remain visible via a WARNING carrying the target node id, so it is
    distinguishable from an unsupported graph shape and not silently swallowed.
    """
    import haute.execution as execution_module

    source_path = _write_projected_source(tmp_path)

    def _boom(*args: object, **kwargs: object):
        raise RuntimeError("engine defect during planning")

    monkeypatch.setattr(execution_module, "execute_lazy_graph", _boom)

    with structlog.testing.capture_logs() as logs:  # noqa: SIM117
        with pytest.raises(ChunkPlanUnsupportedError):
            chunk_plan(
                ChunkPlanRequest(
                    graph=_source_output_graph(source_path, ["quote_id", "premium"]),
                    target_node_id="out",
                    target_chunk_bytes=1_024,
                    required_columns_by_node={"out": {"quote_id", "premium"}},
                )
            )

    warnings = [log for log in logs if log["event"] == "chunk_plan_target_output_build_failed"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["target_node_id"] == "out"
    assert warnings[0]["error_type"] == "RuntimeError"
    assert "engine defect during planning" in warnings[0]["error"]


def test_chunk_plan_uses_explicit_row_chunk_size(
    tmp_path: Path,
) -> None:
    source_path = _write_projected_source(tmp_path, extra_columns=12)

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(
                source_path,
                ["quote_id", "premium", *[f"feature_{index}" for index in range(12)]],
            ),
            target_node_id="out",
            chunk_size=17,
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )

    assert plan.chunk_size == 17
    assert plan.source_chunk_size == 17
    assert plan.chunk_size_policy == "explicit_rows"
    assert plan.target_chunk_bytes is None
    assert plan.estimated_source_row_bytes is None
    assert plan.estimated_target_row_bytes is None


def test_chunk_plan_rejects_ambiguous_row_and_byte_budget(
    tmp_path: Path,
) -> None:
    source_path = _write_projected_source(tmp_path)

    with pytest.raises(ValueError, match="Specify either chunk_size or target_chunk_bytes"):
        chunk_plan(
            ChunkPlanRequest(
                graph=_source_output_graph(source_path, ["quote_id", "premium"]),
                target_node_id="out",
                chunk_size=17,
                target_chunk_bytes=1_024,
                required_columns_by_node={"out": {"quote_id", "premium"}},
            )
        )


def test_chunk_plan_requires_a_row_or_byte_budget(tmp_path: Path) -> None:
    source_path = _write_projected_source(tmp_path)

    with pytest.raises(ValueError, match="chunk_size or target_chunk_bytes must be provided"):
        chunk_plan(
            ChunkPlanRequest(
                graph=_source_output_graph(source_path, ["quote_id", "premium"]),
                target_node_id="out",
                required_columns_by_node={"out": {"quote_id", "premium"}},
            )
        )


def test_chunk_plan_accepts_snapshot_backed_json_sources():
    """JSON inputs are snapshot-backed, so their canonical chunk form is a Parquet scan."""
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": "quotes.json"}),
                _node("out", "output", make_output_config(["quote_id"])),
            ],
            "edges": [make_edge("source", "out").model_dump()],
        }
    )

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=10,
            required_columns_by_node={"out": {"quote_id"}},
        )
    )

    assert plan.source_node_id == "source"
    assert plan.chunk_size == 10


def test_chunk_plan_requires_explicit_model_score_batch_reuse(tmp_path):
    from haute.modelling._feature_contract import build_contract, save_contract

    contract_path = tmp_path / "feature_contract.json"
    save_contract(
        build_contract(
            features=["feature"],
            feature_types={"feature": "Float64"},
            categorical_features=[],
            target_name="target",
            target_type="Float64",
            task="regression",
        ),
        contract_path,
    )
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": "quotes.parquet"}),
                _node(
                    "score",
                    "modelScore",
                    {
                        "sourceType": "run",
                        "run_id": "r1",
                        "output_column": "prediction",
                        "feature_contract_path": str(contract_path),
                    },
                ),
                _node("out", "output", make_output_config(["prediction"])),
            ],
            "edges": [
                make_edge("source", "score").model_dump(),
                make_edge("score", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="model_reuse_lifetime='batch'"):
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                chunk_size=10,
                required_columns_by_node={"out": {"prediction"}},
            )
        )

    graph.nodes[1].data.config["model_reuse_lifetime"] = "batch"
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="out",
            chunk_size=10,
            required_columns_by_node={"out": {"prediction"}},
        )
    )
    assert plan.capabilities["score"].model_reuse_lifetime == "batch"


def test_chunk_plan_rejects_opaque_rating_step_user_code():
    graph = make_graph(
        {
            "nodes": [
                _node(
                    "source",
                    "dataInput",
                    {
                        "path": "quotes.csv",
                        "arguments": {"schema": {"quote_id": "str"}},
                    },
                ),
                _node("rating", "ratingStep", {"code": "df = df.sort('quote_id')"}),
                _node("out", "output", make_output_config(["quote_id"])),
            ],
            "edges": [
                make_edge("source", "rating").model_dump(),
                make_edge("rating", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="ratingStep user code"):
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                chunk_size=10,
                required_columns_by_node={"out": {"quote_id"}},
            )
        )


# ---------------------------------------------------------------------------
# Chunk-local classifier: closed reasons, blocking operators, source locations.
#
# The classifier is a receiver-aware AST walk with no textual prefilter, so
# comments and string literals cannot change a verdict and every rejection
# names the construct that stopped the walk.
# ---------------------------------------------------------------------------


def _classify(code: str):
    return classify_chunk_local_polars_code(code, frame_names=("df",))


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("# never .sort( here\ndf = df.filter(pl.col('a') > 0)", id="comment"),
        pytest.param("df = df.filter(pl.col('a') != '.sort(')", id="string-literal"),
    ],
)
def test_comments_and_string_literals_do_not_change_eligibility(code: str) -> None:
    decision = _classify(code)
    assert decision.eligible
    assert decision.reason == "eligible"
    assert decision.blocking_operator is None


def test_classifier_reports_unsupported_frame_method() -> None:
    decision = _classify("df = df.sort('a')")
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"
    assert decision.line == 1
    assert decision.column is not None


def test_classifier_reports_unsupported_namespace_method() -> None:
    decision = _classify("df = df.with_columns(pl.col('a').list.sort().alias('b'))")
    assert not decision.eligible
    assert decision.reason == "unsupported_namespace_method"
    assert decision.blocking_operator == "list.sort"
    assert decision.line == 1


def test_classifier_admits_whitelisted_string_namespace_method() -> None:
    decision = _classify("df = df.filter(pl.col('s').str.contains('x'))")
    assert decision.eligible
    assert decision.reason == "eligible"


def test_classifier_rejects_namespace_method_with_expression_argument() -> None:
    decision = _classify("df = df.filter(pl.col('s').str.contains(pl.col('t')))")
    assert not decision.eligible
    assert decision.reason == "unsupported_call_shape"
    assert decision.blocking_operator == "contains"
    assert decision.line == 1


def test_classifier_rejects_map_elements_with_a_location() -> None:
    decision = _classify("df = df.with_columns(pl.col('a').map_elements(lambda v: v))")
    assert not decision.eligible
    assert decision.blocking_operator == "map_elements"
    assert decision.line == 1
    assert decision.column is not None


def test_classifier_reports_unsupported_statement() -> None:
    decision = _classify("for x in range(2):\n    df = df")
    assert not decision.eligible
    assert decision.reason == "unsupported_statement"
    assert decision.blocking_operator == "For"
    assert decision.line == 1


def test_classifier_reports_frame_embedded_in_expression() -> None:
    decision = _classify("df = df.with_columns(y=df)")
    assert not decision.eligible
    assert decision.reason == "frame_embedded_in_expression"
    assert decision.blocking_operator == "df"
    assert decision.line == 1


def test_classifier_reports_the_first_blocking_construct_in_source_order() -> None:
    first = _classify("df = df.sort('a')\ndf = df.with_columns(pl.col('a').list.sort())")
    assert first.reason == "unsupported_frame_method"
    assert first.blocking_operator == "sort"
    assert first.line == 1

    swapped = _classify("df = df.with_columns(pl.col('a').list.sort())\ndf = df.sort('a')")
    assert swapped.reason == "unsupported_namespace_method"
    assert swapped.blocking_operator == "list.sort"
    assert swapped.line == 1


def test_classifier_reports_the_textually_first_blocking_dict_entry() -> None:
    """AST field order visits every ``Dict`` key before any value, so a blocking
    VALUE that precedes a blocking KEY must still be the one reported."""
    code = "df = df.rename({'a': df.sort('x'), df.list.sort(): 'b'})"
    decision = _classify(code)
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"
    assert decision.line == 1
    assert decision.column == code.index("df.sort(") + 1


def test_classifier_reports_the_textually_first_ifexp_branch() -> None:
    """An ``IfExp`` stores ``test`` before the textually earlier ``body``."""
    code = "df = df.with_columns(y=df.sort('x') if df.unique() else 1)"
    decision = _classify(code)
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"
    assert decision.line == 1
    assert decision.column == code.index("df.sort(") + 1


def test_classifier_reports_the_textually_first_call_argument() -> None:
    """A blocking positional argument that follows an earlier blocking argument
    must not be reported ahead of it."""
    code = "df = df.select(df.sort('x'), df.unique())"
    decision = _classify(code)
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"
    assert decision.column == code.index("df.sort(") + 1


def test_classifier_reports_a_positional_argument_before_a_later_keyword() -> None:
    decision = _classify("df = df.select(df.sort('x'), y=df.unique())")
    assert decision.blocking_operator == "sort"

    swapped = _classify("df = df.select(df.unique(), y=df.sort('x'))")
    assert swapped.blocking_operator == "unique"


def test_chunk_plan_raises_chunk_user_code_unsupported_with_the_decision_payload() -> None:
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": "quotes.parquet"}),
                _node("xform", "polars", {"code": "df = source.sort('a')"}),
                _node("out", "output", make_output_config(["quote_id"])),
            ],
            "edges": [
                make_edge("source", "xform").model_dump(),
                make_edge("xform", "out").model_dump(),
            ],
        }
    )
    request = ChunkPlanRequest(
        graph=graph,
        target_node_id="out",
        chunk_size=10,
        required_columns_by_node={"out": {"quote_id"}},
    )
    decision = classify_chunk_local_polars_code("df = source.sort('a')", frame_names=("source",))

    with pytest.raises(ChunkUserCodeUnsupportedError) as excinfo:
        chunk_plan(request)

    payload = excinfo.value.to_payload()
    assert payload["error_code"] == "chunk_user_code_unsupported"
    assert payload["reason"] == decision.reason == "unsupported_frame_method"
    assert payload["blocking_operator"] == decision.blocking_operator == "sort"
    assert payload["line"] == decision.line
    assert payload["column"] == decision.column
    assert payload["node_id"] == "xform"

    # Callers that route to the full executor still catch the base class.
    with pytest.raises(ChunkPlanUnsupportedError):
        chunk_plan(request)


@pytest.mark.parametrize(
    "code",
    [
        "df = df.sort('premium')",
        "df = df.unique(subset=['quote_id'])",
        "df = df.reverse()",
        "df = df.top_k(5, by='premium')",
        "df = df.bottom_k(5, by='premium')",
        "df = df.explode('l')",
        "df = df.with_columns(pl.col('premium').sum().over('quote_id').alias('total'))",
        "df = df.join(df, on='quote_id')",
        "df = df.join_asof(df, on='premium')",
    ],
)
def test_every_materialisation_boundary_operator_is_rejected_in_the_chunk_suffix(
    tmp_path: Path,
    code: str,
) -> None:
    """EXEC-P07 boundaries materialise the whole frame, so no chunk suffix admits one."""
    path = _write_projected_source(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(path)}),
                _node("shape", "polars", {"code": code}),
                _node("out", "output", make_output_config(["premium"])),
            ],
            "edges": [
                make_edge("source", "shape").model_dump(),
                make_edge("shape", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(ChunkUserCodeUnsupportedError):
        chunk_plan(ChunkPlanRequest(graph=graph, target_node_id="out", chunk_size=10))


def test_the_chunk_suffix_table_covers_every_registered_boundary_method() -> None:
    """A newly admitted boundary cannot slip into the chunk suffix untested."""
    from haute._polars_operations import materialising_frame_methods

    covered = {
        "sort",
        "unique",
        "reverse",
        "top_k",
        "bottom_k",
        "explode",
        "join",
        "join_asof",
        # group_by has its own dedicated chunk-suffix rejection tests above.
        "group_by",
        "groupby",
    }
    assert materialising_frame_methods() <= covered


def test_byte_budget_output_target_plans_without_assembling_the_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXEC-P08: byte-budget planning declares ``schema_only=True`` when it builds
    the target frame. With an OUTPUT target that declaration used to be false --
    the assembler collected the whole document while the graph was being built.
    Planning now derives the target width from the derived document schema, so
    the assembler is never entered at all.

    ``LazyFrame.collect`` itself cannot be poisoned here: the *source* row-byte
    sampler legitimately collects a bounded sample. The tripwire is therefore
    ``_assemble_document``, the OUTPUT document materialisation this package
    removed from the schema-only path.
    """
    import haute._output_assembler as assembler_module

    source_path = _write_projected_source(tmp_path)

    def must_not_assemble(field_frames: object):
        raise AssertionError("schema-only chunk planning must not assemble the document")

    monkeypatch.setattr(assembler_module, "_assemble_document", must_not_assemble)

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=_source_output_graph(source_path, ["premium"]),
            target_node_id="out",
            chunk_size=None,
            target_chunk_bytes=1_024,
            required_columns_by_node={"out": ["premium"]},
        )
    )

    # Float64 -- the 8-byte fixed width the derived document schema declares.
    assert plan.chunk_size == 1_024 // 8


def _wide_output_graph(source_path: Path, output_config: dict) -> object:
    return make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "widen",
                    "polars",
                    {"code": "df = source.with_columns(pl.lit('x' * 500).alias('wide'))"},
                ),
                _node("out", "output", output_config),
            ],
            "edges": [
                make_edge("source", "widen").model_dump(),
                make_edge("widen", "out").model_dump(),
            ],
        }
    )


def test_byte_budgeted_chunk_plan_samples_flat_output_document_columns_from_the_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXEC-P08: an OUTPUT target's document is never assembled at plan time --
    before this package the sampler silently assembled the whole document to
    measure its variable-width columns. A flat document column is its source
    column renamed, so the planner samples it from the single parent plan through
    the same bounded ``limit`` and still costs the downstream-created wide column.
    """
    import haute._output_assembler as assembler_module

    def must_not_assemble(field_frames: object):
        raise AssertionError("chunk planning must not assemble the OUTPUT document")

    monkeypatch.setattr(assembler_module, "_assemble_document", must_not_assemble)
    source_path = _write_projected_source(tmp_path)  # quote_id, premium

    plan = chunk_plan(
        ChunkPlanRequest(
            graph=_wide_output_graph(
                source_path, make_output_config(["quote_id", "premium", "wide"])
            ),
            target_node_id="out",
            target_chunk_bytes=8_192,
            required_columns_by_node={"out": {"quote_id", "premium", "wide"}},
        )
    )

    assert plan.estimated_target_row_bytes is not None
    assert plan.estimated_target_row_bytes >= 500
    assert plan.chunk_size == max(1, 8_192 // plan.estimated_target_row_bytes)


def test_byte_budgeted_chunk_plan_rejects_nested_output_document_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXEC-P08: a nested document column aggregates child rows, so it has no
    bounded sample once the document is no longer assembled at plan time. The
    planner reports the target unsupported (the caller falls back to the full
    executor) instead of guessing a nominal width that would under-bound the chunk.
    """
    import haute._output_assembler as assembler_module

    def must_not_assemble(field_frames: object):
        raise AssertionError("chunk planning must not assemble the OUTPUT document")

    monkeypatch.setattr(assembler_module, "_assemble_document", must_not_assemble)
    source_path = _write_projected_source(tmp_path)  # quote_id, premium
    output_config = make_output_config(["quote_id", "wide"])
    output_config["outputMapping"][1]["output_path"] = "$[:].items[:].wide"

    with pytest.raises(ChunkPlanUnsupportedError) as exc_info:
        chunk_plan(
            ChunkPlanRequest(
                graph=_wide_output_graph(source_path, output_config),
                target_node_id="out",
                target_chunk_bytes=8_192,
                required_columns_by_node={"out": {"quote_id", "items"}},
            )
        )

    # The projection contract rejects the nested document key before the sampler
    # runs (an OUTPUT target's required columns are its parent's columns); the
    # sampler's own nested and multi-frame rejections are pinned directly below.
    assert exc_info.value.context["target_node_id"] == "out"


def test_byte_budgeted_chunk_plan_rejects_output_sampling_through_a_materialising_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A materialising operator before an explicit chunk start is supported, and
    the planner costs variable-width columns at the nominal width there because
    a sample through the operator would execute it at plan time. An OUTPUT
    target must not inherit that guess (its document is never assembled at plan
    time, so a nominal width would silently under-bound the chunk): it is a typed
    rejection, while the equivalent polars target keeps the nominal rule.
    """
    import haute._output_assembler as assembler_module

    def must_not_assemble(field_frames: object):
        raise AssertionError("chunk planning must not assemble the OUTPUT document")

    monkeypatch.setattr(assembler_module, "_assemble_document", must_not_assemble)
    source_path = _write_projected_source(tmp_path)  # quote_id, premium
    graph = make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node(
                    "agg",
                    "polars",
                    {
                        "code": (
                            "df = source.group_by('quote_id')"
                            ".agg(pl.col('premium').sum().alias('premium'))"
                        )
                    },
                ),
                _node(
                    "widen",
                    "polars",
                    {"code": "df = agg.with_columns(pl.lit('x' * 500).alias('wide'))"},
                ),
                _node("out", "output", make_output_config(["quote_id", "premium", "wide"])),
            ],
            "edges": [
                make_edge("source", "agg").model_dump(),
                make_edge("agg", "widen").model_dump(),
                make_edge("widen", "out").model_dump(),
            ],
        }
    )

    with pytest.raises(ChunkPlanUnsupportedError) as exc_info:
        chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id="out",
                chunk_start_node_id="widen",
                target_chunk_bytes=8_192,
                required_columns_by_node={"out": {"quote_id", "premium", "wide"}},
            )
        )
    error = exc_info.value
    assert error.context["target_node_id"] == "out"
    assert error.context["columns"] == ["quote_id", "wide"]
    assert "materialising operator" in str(error)

    # The same lineage targeted at the polars node keeps the planner-wide nominal
    # rule: two 64-byte variable-width guesses plus the 8-byte Float64.
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="widen",
            chunk_start_node_id="widen",
            target_chunk_bytes=8_192,
            required_columns_by_node={"widen": {"quote_id", "premium", "wide"}},
        )
    )
    assert plan.estimated_target_row_bytes == 64 + 64 + 8


def test_output_document_width_sampler_rejects_nested_and_multi_frame_documents(
    tmp_path: Path,
) -> None:
    from haute.chunking import _sample_output_document_widths

    source_path = _write_projected_source(tmp_path)  # quote_id, premium
    output_config = make_output_config(["quote_id", "wide"])
    output_config["outputMapping"][1]["output_path"] = "$[:].items[:].wide"
    graph = _wide_output_graph(source_path, output_config)
    target = next(node for node in graph.nodes if node.id == "out")
    parent = pl.LazyFrame({"quote_id": ["q1"], "wide": ["x" * 500]})

    with pytest.raises(ChunkPlanUnsupportedError) as nested:
        _sample_output_document_widths(
            target, ["widen"], {"widen": parent}, ["items"], target_node_id="out"
        )
    assert nested.value.context["columns"] == ["items"]
    assert "never assembled" in str(nested.value)

    with pytest.raises(ChunkPlanUnsupportedError) as multi:
        _sample_output_document_widths(
            target, ["a", "b"], {"a": parent, "b": parent}, ["quote_id"], target_node_id="out"
        )
    assert multi.value.context["parent_ids"] == ["a", "b"]

    widths = _sample_output_document_widths(
        target, ["widen"], {"widen": parent}, ["quote_id"], target_node_id="out"
    )
    assert widths == {"quote_id": 2}


@pytest.mark.parametrize(
    ("code", "reason", "operator"),
    [
        (
            "df = df.with_columns(pl.col('a').replace(**mapping))",
            "unsupported_call_shape",
            "replace",
        ),
        ("df = df.with_columns(pl.col('a').replace(old=[1]))", "unsupported_call_shape", "replace"),
        (
            "df = df.with_columns(pl.col('a').replace(old=values, new=[1]))",
            "unsupported_call_shape",
            "replace",
        ),
        ("df = helper(df)", "unsupported_expression", "Call"),
        ("df = unknown.str.contains('x')", "unsupported_expression", "Name"),
        ("df = df.str.contains('x')", "unsupported_namespace_method", "str.contains"),
        ("df = df.foo", "unsupported_expression", "Attribute"),
        ("df = lambda x: x", "unsupported_expression", "Lambda"),
        ("df[0] = df", "assignment_not_frame_derived", "df[0]"),
        ("df: object", "assignment_not_frame_derived", "df"),
        ("df = 1", "assignment_not_frame_derived", "df"),
        ("helper(df)", "unsupported_expression", "Call"),
        ("pl.col('a')", "unsupported_statement", "Expr"),
    ],
)
def test_classifier_rejects_unproven_public_shapes(code: str, reason: str, operator: str) -> None:
    decision = _classify(code)
    assert not decision.eligible
    assert decision.reason == reason
    assert decision.blocking_operator == operator


def test_classifier_admits_frame_derived_expression_statement() -> None:
    decision = _classify("df.filter(pl.col('a') > 0)")
    assert decision.eligible
    assert decision.reason == "eligible"


def test_chunk_classifier_defensive_helpers_preserve_their_contracts() -> None:
    import ast

    from haute.chunking import _ChunkLocalTrace, _embedded_frame_name, _source_ordered

    trace = _ChunkLocalTrace()
    first, second = ast.parse("first\nsecond").body
    trace.record("first", "one", first)
    trace.record("second", "two", second)
    assert (trace.reason, trace.blocking_operator, trace.line) == ("first", "one", 1)
    assert (
        _embedded_frame_name(
            ast.parse("other + 1", mode="eval").body, allowed_frames={"df"}, local_frames={"tmp"}
        )
        is None
    )
    left = ast.Name(id="left")
    right = ast.Name(id="right")
    assert _source_ordered((right, left)) == (right, left)
    assert classify_chunk_local_polars_code("df = df", frame_names=()).reason == "no_frame_names"


def test_target_output_lazyframe_exposes_the_projected_schema(tmp_path: Path) -> None:
    from haute.chunking import _target_output_lazyframe

    source_path = _write_projected_source(tmp_path)
    target = _target_output_lazyframe(
        ChunkPlanRequest(
            graph=_source_output_graph(source_path, ["quote_id", "premium"]),
            target_node_id="out",
            required_columns_by_node={"out": {"quote_id", "premium"}},
        )
    )
    assert target.collect_schema() == pl.Schema({"quote_id": pl.String, "premium": pl.Float64})


def test_output_width_sampler_skips_inactive_mappings_and_requires_parent(tmp_path: Path) -> None:
    from haute.chunking import _sample_output_document_widths

    source_path = _write_projected_source(tmp_path)
    config = make_output_config(["quote_id"])
    config["outputMapping"].append(
        {
            "source_port": "widen",
            "source_column": "ignored",
            "output_path": "$[:].ignored",
            "enabled": False,
        }
    )
    graph = _wide_output_graph(source_path, config)
    target = next(node for node in graph.nodes if node.id == "out")
    parent = pl.LazyFrame({"quote_id": ["q1"]})
    assert _sample_output_document_widths(
        target, ["widen"], {"widen": parent}, ["quote_id"], target_node_id="out"
    ) == {"quote_id": 2}
    with pytest.raises(ChunkPlanUnsupportedError, match="parent output frame is unavailable"):
        _sample_output_document_widths(target, ["widen"], {}, ["quote_id"], target_node_id="out")


@pytest.mark.parametrize("node_type", ["scenarioExpander", "dataInput"])
def test_chunk_plan_rejects_row_nonlocal_editor_code(node_type: str, tmp_path: Path) -> None:
    source_path = _write_projected_source(tmp_path)
    source_config: dict[str, object] = {"path": str(source_path)}
    if node_type == "dataInput":
        source_config["code"] = "df = df.sort('premium')"
        nodes = [
            _node("source", "dataInput", source_config),
            _node("out", "output", make_output_config(["premium"])),
        ]
        edges = [make_edge("source", "out").model_dump()]
        expected_node = "source"
    else:
        nodes = [
            _node("source", "dataInput", source_config),
            _node(
                "scenario",
                "scenarioExpander",
                {
                    "column_name": "factor",
                    "min_value": 1,
                    "max_value": 2,
                    "steps": 2,
                    "code": "df = df.sort('premium')",
                },
            ),
            _node("out", "output", make_output_config(["premium"])),
        ]
        edges = [
            make_edge("source", "scenario").model_dump(),
            make_edge("scenario", "out").model_dump(),
        ]
        expected_node = "scenario"
    graph = make_graph({"nodes": nodes, "edges": edges})
    with pytest.raises(ChunkUserCodeUnsupportedError) as raised:
        chunk_plan(ChunkPlanRequest(graph=graph, target_node_id="out", chunk_size=2))
    assert raised.value.context["node_id"] == expected_node
    assert raised.value.context["reason"] == "unsupported_frame_method"
