"""An executable inventory of persisted configuration fields and loss witnesses.

Examples are explicit, independent of the schema being checked. New TypedDict
fields therefore fail the inventory gate until a real round-trip case is added.
Dynamic user dictionary keys are covered by the adjacent property tests.
"""

from __future__ import annotations

import types
from copy import deepcopy
from typing import Any, Required, Union, get_args, get_origin, get_type_hints, is_typeddict

import pytest

from haute._config_validation import _TYPED_DICT_BY_NODE_TYPE
from haute._graph_utils import _sanitize_func_name
from haute._types import DATA_INPUT_CONFIG_TYPES, DATA_OUTPUT_CONFIG_TYPES, GraphEdge, NodeType
from tests.test_codegen_roundtrip_property import (
    _SHARED_COLUMN_CONFIG,
    _assert_semantically_equal,
    _corpus_graphs,
    _roundtrip,
)
from tests.test_explore_charts import _chart
from tests.test_explore_pivots import _formula, _pivot


def _examples():
    graph = _corpus_graphs()[0]
    by_type = {node.data.nodeType: node for node in graph.nodes}
    for node in graph.nodes:
        node.data.config.update(deepcopy(_SHARED_COLUMN_CONFIG))
    by_type[NodeType.MODEL_SCORE].data.config.update(
        {
            "experiment_id": "experiment-17",
            "experiment_name": "Pricing experiments",
            "registered_model": "catalog.models.pricing",
            "version": "3",
            "feature_contract_path": "models/features.json",
        }
    )
    by_type[NodeType.EXTERNAL_FILE].data.config.update(
        {"fileType": "catboost", "modelClass": "regressor"}
    )
    by_type[NodeType.MODELLING].data.config.update(
        {
            "weight": "_exposure",
            "exclude": ["_identifier"],
            "params": {"iterations": 17, "depth": 3},
            "evaluation": {"method": "holdout", "test_size": 0.25},
            "tuning": {"enabled": True, "n_trials": 3},
            "mlflow_experiment": "Pricing",
            "model_name": "Frequency",
            "output_dir": "models/output",
            "row_limit": 1234,
            "terms": {"_age": {"type": "linear"}},
            "family": "poisson",
            "link": "log",
            "offset": "_log_exposure",
            "interactions": [{"factors": ["_age", "region"]}],
            "regularization": "elastic_net",
            "alpha": 0.25,
            "l1_ratio": 0.75,
            "intercept": False,
            "var_power": 1.5,
            "loss_function": "Poisson",
            "variance_power": 1.6,
            "monotone_constraints": {"_age": 1},
            "feature_weights": {"_age": 0.8},
            "fold_column": "_fold",
            "id_columns": ["_identifier"],
        }
    )
    optimiser = by_type[NodeType.OPTIMISER]
    band = by_type[NodeType.BANDING]
    scenario = by_type[NodeType.SCENARIO_EXPANDER]
    optimiser.data.config.update(
        {
            "frontier_enabled": True,
            "frontier_ranges": {"_premium": {"min": 10.0, "max": 20.0}},
            "frontier_steps": 7,
            "factor_columns": [["_age", "region"]],
            "candidate_min": 0.5,
            "candidate_max": 1.5,
            "candidate_steps": 9,
            "max_cd_iterations": 11,
            "cd_tolerance": 0.002,
            "structure_mode": "explicit",
            "data_input": _sanitize_func_name(scenario.data.label),
            "banding_source": _sanitize_func_name(band.data.label),
            "mlflow_experiment": "Optimisation",
            "model_name": "Ratebook",
        }
    )
    graph.edges.append(GraphEdge(id="banding_to_optimiser", source=band.id, target=optimiser.id))
    by_type[NodeType.OPTIMISER_APPLY].data.config.update(
        {
            "optimiser_mode": "ratebook",
            "ratebook_input": _sanitize_func_name(optimiser.data.label),
            "registered_model": "catalog.models.ratebook",
            "version": "2",
            "experiment_id": "experiment-18",
            "experiment_name": "Optimisation",
            "run_id": "run-19",
            "run_name": "Best run",
        }
    )
    by_type[NodeType.BANDING].data.config["factors"][0]["rightClosed"] = False
    by_type[NodeType.RATING_STEP].data.config["tables"][0].update(
        {
            "factorDtypes": {"score_band": {"kind": "String"}},
            "onMissing": "neutral",
        }
    )
    chart = _chart()
    chart["pivot_id"] = "pivot_1"
    by_type[NodeType.EXPLORE].data.config.update(
        {
            "pivot_formulas": [_formula()],
            "pivots": [_pivot(formulas=["formula_1"])],
            "charts": [chart],
        }
    )
    yield "all-node-fields", graph
    # Non-default presentation values make this more than a presence check:
    # a decoder that silently reconstructs its defaults must fail too.
    presentation = graph.model_copy(deep=True)
    explore = next(
        node.data.config for node in presentation.nodes if node.data.nodeType == NodeType.EXPLORE
    )
    pivot = explore["pivots"][0]
    pivot["enabled"] = False
    pivot["options"] = {
        "row_grand_totals": False,
        "column_grand_totals": False,
        "sort_by": "value_1",
    }
    pivot["rows"][0]["sort"] = "descending"
    for placement in [
        *pivot["columns"],
        *pivot["rows"],
        *pivot["values"],
        *explore["pivot_formulas"],
    ]:
        placement.update({"decimal_places": 3, "number_format": "percent", "use_grouping": False})
    pivot["values"][0].update(
        {
            "aggregation": "average",
            "reference": "claims_mean",
            "sort_rows": "descending",
            "color_scale": "low_red_high_green",
        }
    )
    chart = explore["charts"][0]
    chart.update({"orientation": "horizontal", "enabled": False})
    chart["category"].update({"include_grand_total": True, "label_rotation": 45})
    chart["value_encodings"][0].update(
        {
            "mark": "area",
            "axis": "secondary",
            "stack_group": "premiums",
            "stack_normalize": True,
            "data_labels": True,
            "markers": True,
        }
    )
    chart["series_overrides"][0].update(
        {"color": "#112233", "stack_group": "claims", "stack_normalize": True}
    )
    chart["axes"]["primary"].update(
        {"title": "Premium", "minimum": 0.25, "maximum": 99.0, "number_format": "currency_gbp"}
    )
    chart["axes"]["secondary"].update(
        {"title": "Frequency", "minimum": 1, "maximum": 10, "enabled": True}
    )
    chart["legend"] = {"visible": False, "position": "right"}
    yield "explore-non-default-presentation", presentation
    # Mutually exclusive input/output variants need separate, valid examples.
    variants = [
        (
            NodeType.DATA_INPUT,
            {
                "inputType": "database",
                "format": "database",
                "connection": "warehouse",
                "query": "SELECT _id FROM quotes",
                "arguments": {"batch_size": 64},
            },
        ),
        (
            NodeType.DATA_INPUT,
            {
                "inputType": "database",
                "format": "database",
                "uri": "sqlite:///pricing.db",
                "query": "SELECT _id FROM quotes",
                "arguments": {"batch_size": 64},
            },
        ),
        (
            NodeType.DATA_INPUT,
            {
                "inputType": "databricks",
                "http_path": "/sql/warehouses/test",
                "table": "catalog.schema.quotes",
                "query": "SELECT _id FROM catalog.schema.quotes",
                "arguments": {"batch_size": 32},
            },
        ),
        (
            NodeType.DATA_INPUT,
            {
                "inputType": "inline",
                "format": "records",
                "mode": "read",
                "records": [{"_id": 17, "nested": {"_key": "value"}}],
                "arguments": {"infer_schema_length": 1},
            },
        ),
        (
            NodeType.DATA_INPUT,
            {
                "inputType": "lakehouse",
                "format": "delta",
                "mode": "scan",
                "path": "data/lakehouse",
                "arguments": {"version": 2},
            },
        ),
        (
            NodeType.DATA_OUTPUT,
            {
                "outputType": "database",
                "format": "database",
                "mode": "write",
                "connection": "warehouse",
                "table": "quoted_results",
                "arguments": {"if_table_exists": "append"},
            },
        ),
        (
            NodeType.DATA_OUTPUT,
            {
                "outputType": "database",
                "format": "database",
                "mode": "write",
                "uri": "sqlite:///pricing.db",
                "table": "quoted_results",
                "arguments": {"if_table_exists": "append"},
            },
        ),
        (
            NodeType.DATA_OUTPUT,
            {
                "outputType": "lakehouse",
                "format": "delta",
                "mode": "write",
                "path": "outputs/lakehouse",
                "arguments": {"mode": "append"},
            },
        ),
        (
            NodeType.EDGE_JOIN,
            {
                "how": "left",
                "on": ["quote_id"],
                "suffix": "_lookup",
                "coalesce": False,
                "validate": "m:1",
                "maintainOrder": "left",
            },
        ),
    ]
    for index, (node_type, config) in enumerate(variants):
        variant = graph.model_copy(deep=True)
        node = next(node for node in variant.nodes if node.data.nodeType == node_type)
        node.data.config = {**config, "contract": "opaque", **deepcopy(_SHARED_COLUMN_CONFIG)}
        yield f"{node_type.value}-variant-{index}", variant


# These fields represent graph relationships rather than independent settings.
# Their round-trip contracts are tested with real owner/copy and boundary graphs.
_STRUCTURAL_POLICY = {
    NodeType.POLARS: {"instanceOf", "inputMapping"},
    NodeType.MODEL_SCORE: {"instanceOf", "inputMapping"},
    NodeType.SUBMODEL: {"definitionId", "alias"},
}


def _declared_paths(schema: Any, prefix: str = "") -> set[str]:
    origin = get_origin(schema)
    if origin is Required:
        return _declared_paths(get_args(schema)[0], prefix)
    if origin in (Union, types.UnionType):
        return set().union(*(_declared_paths(arg, prefix) for arg in get_args(schema)))
    if origin is list:
        return _declared_paths(get_args(schema)[0], prefix + "[]")
    if not is_typeddict(schema):
        return set()
    paths: set[str] = set()
    for key, child in get_type_hints(schema).items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        paths.update(_declared_paths(child, path))
    return paths


def _example_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, list):
        return set().union(*(_example_paths(item, prefix + "[]") for item in value))
    if not isinstance(value, dict):
        return set()
    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        paths.update(_example_paths(child, path))
    return paths


def _assert_field_inventory(examples, schemas):
    covered: dict[NodeType, set[str]] = {}
    for _, graph in examples:
        for node in graph.nodes:
            covered.setdefault(node.data.nodeType, set()).update(_example_paths(node.data.config))
    missing = {}
    for node_type, node_schemas in schemas.items():
        declared = set().union(*(_declared_paths(schema) for schema in node_schemas))
        policy = _STRUCTURAL_POLICY.get(node_type, set())
        assert policy <= declared, f"Stale structural policy for {node_type}: {policy - declared}"
        gaps = declared - covered.get(node_type, set()) - policy
        if gaps:
            missing[node_type.value] = sorted(gaps)
    assert not missing, f"Persisted fields without round-trip examples: {missing}"


def _schemas():
    return {
        **{nt: (schema,) for nt, schema in _TYPED_DICT_BY_NODE_TYPE.items()},
        NodeType.DATA_INPUT: DATA_INPUT_CONFIG_TYPES,
        NodeType.DATA_OUTPUT: DATA_OUTPUT_CONFIG_TYPES,
    }


def test_every_declared_field_has_a_roundtrip_example_or_structural_policy():
    _assert_field_inventory(list(_examples()), _schemas())


def _assert_authored_values(original, parsed):
    # This oracle deliberately does not call production normalizers: applying
    # the same lossy normalizer to both sides could conceal a persistence bug.
    for node in original.nodes:
        restored = parsed.node_map[_sanitize_func_name(node.data.label)].data.config
        for key, value in node.data.config.items():
            assert key in restored, f"{node.data.nodeType}.{key} disappeared"
            assert restored[key] == value, f"{node.data.nodeType}.{key} changed"


@pytest.mark.parametrize(
    "name,graph", list(_examples()), ids=lambda value: value if isinstance(value, str) else "graph"
)
def test_every_field_example_survives_repeated_save(name, graph):
    original = graph.model_copy(deep=True)
    first, parsed, second = _roundtrip(graph)
    _assert_authored_values(original, parsed)
    _assert_semantically_equal(original, parsed)
    assert first == second, name
    _, reparsed, third = _roundtrip(parsed)
    _assert_authored_values(parsed, reparsed)
    _assert_semantically_equal(parsed, reparsed)
    assert second == third, name
    assert graph == original, "Saving mutated the caller's configuration"


@pytest.mark.parametrize("field", ["selected_columns", "column_renames", "categorical_levels"])
def test_roundtrip_oracle_rejects_dropped_fields(field):
    graph = next(_examples())[1]
    _, parsed, _ = _roundtrip(graph)
    _assert_authored_values(graph, parsed)
    # Inject the historical loss at the parser boundary; use the very same
    # semantic oracle as the healthy round-trip test, not a separate check.
    node = next(node for node in parsed.nodes if node.data.nodeType == NodeType.EDGE_JOIN)
    del node.data.config[field]
    with pytest.raises(AssertionError, match=field):
        _assert_authored_values(graph, parsed)


def test_field_inventory_rejects_a_new_uncovered_field():
    from typing import TypedDict

    class AddedField(TypedDict):
        future_setting: str

    schemas = _schemas()
    schemas[NodeType.BANDING] = (*schemas[NodeType.BANDING], AddedField)
    with pytest.raises(AssertionError, match="future_setting"):
        _assert_field_inventory(list(_examples()), schemas)
