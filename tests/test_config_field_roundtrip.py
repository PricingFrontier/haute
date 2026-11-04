"""An executable inventory of persisted configuration fields and loss witnesses.

Examples are explicit, independent of the schema being checked. New TypedDict
fields therefore fail the inventory gate until a real round-trip case is added.
Dynamic user dictionary keys are covered by the adjacent property tests.
"""

from __future__ import annotations

import json
import types
from copy import deepcopy
from typing import Any, Required, Union, get_args, get_origin, get_type_hints

import pytest
from typing_extensions import is_typeddict

from haute._config_io import config_path_for_node
from haute._config_validation import FORM_GIVEN_KEYS
from haute._graph_utils import _sanitize_func_name
from haute._node_config_models import NODE_CONFIG_MODELS, shape_variants
from haute._types import GraphEdge, NodeType
from haute.codegen import graph_to_code_multi
from haute.errors import ConfigError
from haute.parser import parse_pipeline_file
from tests.test_codegen_roundtrip_property import (
    _SHARED_COLUMN_CONFIG,
    _assert_semantically_equal,
    _corpus_graphs,
    _roundtrip,
    _write_configs_recursive,
)
from tests.test_explore_charts import _chart
from tests.test_explore_pivots import _formula, _pivot

_EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}
_TUNING = {
    "schema_version": 1,
    "trial_count": 5,
    "seed": 7,
    "metric": "gini",
    "search_space": {"depth": [4, 6]},
}


def _modelling(**fields: Any) -> dict[str, Any]:
    """A Model Training config the editor writes: the corpus's target and features."""
    return {
        "name": "model {variant}",
        "target": "claim_count",
        "task": "regression",
        "feature_columns": ["premium (gross)", "rate_factor"],
        "evaluation": deepcopy(_EVALUATION),
        **fields,
    }


def _examples():
    graph = _corpus_graphs()[0]
    by_type = {node.data.nodeType: node for node in graph.nodes}
    for node in graph.nodes:
        node.data.config.update(deepcopy(_SHARED_COLUMN_CONFIG))
    by_type[NodeType.MODEL_SCORE].data.config.update(
        {
            "mlflow_destination": "server",
            "experiment_id": "experiment-17",
            "experiment_name": "Pricing experiments",
            "registered_model": "catalog.models.pricing",
            "version": "3",
            "model_path": "models/frequency.cbm",
            "feature_contract_path": "models/features.json",
        }
    )
    by_type[NodeType.EXTERNAL_FILE].data.config.update({"file_type": "joblib"})
    quotes = by_type[NodeType.API_INPUT].data.config["tables"][0]
    quotes.update({"display_path": "quotes[]", "row_id_column": "quote_id"})
    quote_id, premium = quotes["columns"]
    quote_id.update({"status": "Confirmed", "origin": "manual", "key": True})
    premium.update({"status": "Inferred", "origin": "inferred", "key": False})
    quotes["columns"] += [
        {
            "name": "region",
            "path": "$[:].region",
            "type": "str",
            "status": "Confirmed",
            "selected": True,
            "levels": ["north", None],
        },
        {
            "name": "inception",
            "path": "$[:].inception",
            "type": "date",
            "status": "Confirmed",
            "selected": True,
            "format": "%d/%m/%Y",
        },
    ]
    # The corpus trains a GLM; the tree-model, GPU and classifier fields are in
    # the variants below, since one node cannot hold them all and stay valid.
    by_type[NodeType.MODELLING].data.config.update(
        {
            "mlflow_destination": "server",
            "weight": "_exposure",
            "evaluation": deepcopy(_EVALUATION),
            "refit_on_development": False,
            "mlflow_experiment": "Pricing",
            "output_dir": "models/output",
            "model_export_path": "output/frequency.rsglm",
            "row_limit": 1234,
            "terms": {"_age": {"type": "linear"}},
            "family": "negbinomial",
            "theta": 2.5,
            "link": "log",
            "offset": "_log_exposure",
            "interactions": [{"factors": ["_age", "region"]}],
            "regularization": "elastic_net",
            "alpha": 0.25,
            "l1_ratio": 0.75,
            "cv_folds": 4,
            "cv_selection": "1se",
            "cv_seed": 7,
            "max_iter": 50,
            "tol": 1e-6,
            "intercept": False,
            "fold_column": "_fold",
            "id_columns": ["_identifier"],
        }
    )
    optimiser = by_type[NodeType.OPTIMISER]
    band = by_type[NodeType.BANDING]
    scenario = by_type[NodeType.SCENARIO_EXPANDER]
    optimiser.data.config.update(
        {
            "mlflow_destination": "databricks",
            "frontier_ranges": {"_premium": {"min": 10.0, "max": 20.0}},
            "frontier_steps": 7,
            "factor_columns": [["_age", "region"]],
            "max_cd_iterations": 11,
            "cd_tolerance": 0.002,
            "data_input": _sanitize_func_name(scenario.data.label),
            "banding_source": _sanitize_func_name(band.data.label),
            "analysis_input": _sanitize_func_name(band.data.label),
            "analysis_columns": ["region"],
            "mlflow_experiment": "Optimisation",
            "result_export_path": "rating/optimiser_result.json",
        }
    )
    graph.edges.append(GraphEdge(id="banding_to_optimiser", source=band.id, target=optimiser.id))
    by_type[NodeType.OPTIMISER_APPLY].data.config.update(
        {
            "mlflow_destination": "databricks",
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
    by_type[NodeType.BANDING].data.config["factors"][0]["right_closed"] = False
    by_type[NodeType.RATING_STEP].data.config["tables"][0].update(
        {
            "factor_dtypes": {"score_band": {"kind": "String"}},
            "on_missing": "neutral",
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
                "input_type": "database",
                "format": "database",
                "connection": "warehouse",
                "query": "SELECT _id FROM quotes",
                "arguments": {"batch_size": 64},
            },
        ),
        (
            NodeType.DATA_INPUT,
            {
                "input_type": "database",
                "format": "database",
                "uri": "sqlite:///pricing.db",
                "query": "SELECT _id FROM quotes",
                "arguments": {"batch_size": 64},
            },
        ),
        (
            NodeType.DATA_INPUT,
            {
                "input_type": "databricks",
                "http_path": "/sql/warehouses/test",
                "table": "catalog.schema.quotes",
                "query": "SELECT _id FROM catalog.schema.quotes",
                "arguments": {"batch_size": 32},
            },
        ),
        (
            NodeType.DATA_INPUT,
            {
                "input_type": "inline",
                "format": "records",
                "mode": "read",
                "records": [{"_id": 17, "nested": {"_key": "value"}}],
                "arguments": {"infer_schema_length": 1},
                # Post-load steps: the sidecar owns them and codegen renders
                # them after the load scaffold (`df = df.head(2)`).
                "steps": [{"id": "l", "kind": "limit", "n": 2}],
            },
        ),
        (
            NodeType.DATA_INPUT,
            {
                "input_type": "lakehouse",
                "format": "delta",
                "mode": "scan",
                "path": "data/lakehouse",
                "arguments": {"version": 2},
            },
        ),
        (
            NodeType.DATA_OUTPUT,
            {
                "output_type": "database",
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
                "output_type": "database",
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
                "output_type": "lakehouse",
                "format": "delta",
                "mode": "write",
                "path": "outputs/lakehouse",
                "arguments": {"mode": "append"},
            },
        ),
        (
            NodeType.DATA_OUTPUT,
            {
                "output_type": "databricks",
                "http_path": "/sql/1.0/warehouses/test",
                "table": "catalog.schema.scored",
                "write_mode": "append",
                "arguments": {},
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
                "maintain_order": "left",
            },
        ),
        # Frame-mode step lists: the sidecar owns them and codegen renders
        # them after each surface's scaffold (`df = df.head(2)`).
        (
            NodeType.EXTERNAL_FILE,
            {
                "path": "models/external.pkl",
                "file_type": "pickle",
                "steps": [{"id": "l", "kind": "limit", "n": 2}],
            },
        ),
        (
            NodeType.RATING_STEP,
            {
                "tables": [
                    {
                        "factors": ["score_band"],
                        "output_column": "rate_factor",
                        "default_value": "1.0",
                        "entries": [{"score_band": "low", "value": "1.25"}],
                    }
                ],
                "combined_outputs": [],
                "steps": [{"id": "l", "kind": "limit", "n": 2}],
            },
        ),
        (
            NodeType.MODEL_SCORE,
            {
                "source_type": "run",
                "run_id": "run-1",
                "artifact_path": "models/score.cbm",
                "task": "regression",
                "output_column": "prediction",
                "steps": [{"id": "l", "kind": "limit", "n": 2}],
            },
        ),
        (
            NodeType.SCENARIO_EXPANDER,
            {
                "quote_id": "quote_id",
                "column_name": "scenario_value",
                "min_value": 0.0,
                "max_value": 1.0,
                "step_count": 3,
                "step_column": "step_index",
                "steps": [{"id": "l", "kind": "limit", "n": 2}],
            },
        ),
        # A Tweedie GLM reporting robust standard errors, which a penalty would void.
        (
            NodeType.MODELLING,
            _modelling(
                algorithm="glm",
                metrics=["gini", "tweedie_deviance"],
                terms={"_age": {"type": "linear"}},
                family="tweedie",
                var_power=1.5,
                link="log",
                robust_standard_errors="HC1",
            ),
        ),
        # A tree model: its own parameters and loss, tuning and feature controls.
        (
            NodeType.MODELLING,
            _modelling(
                algorithm="catboost",
                metrics=["gini", "tweedie_deviance"],
                params={"iterations": 17, "learning_rate": 0.1},
                tuning=deepcopy(_TUNING),
                loss_function="Tweedie",
                variance_power=1.6,
                monotone_constraints={"_age": 1},
                feature_weights={"_age": 0.8},
            ),
        ),
        # A GPU device belongs to a family that trains on one.
        (
            NodeType.MODELLING,
            _modelling(algorithm="xgboost", loss_function="RMSE", device="gpu"),
        ),
        # A binary classifier names the label it trains as positive.
        (
            NodeType.MODELLING,
            _modelling(
                algorithm="catboost",
                task="classification",
                metrics=["auc", "logloss"],
                loss_function="Logloss",
                positive_class="claim",
            ),
        ),
        # Explore keeps its steps in the decorator beside its cards, so the
        # round trip has to carry both.
        (
            NodeType.EXPLORE,
            {
                "steps": [{"id": "l", "kind": "limit", "n": 2}],
                "pivot_formulas": [_formula()],
                "pivots": [_pivot(formulas=["formula_1"])],
            },
        ),
    ]
    for index, (node_type, config) in enumerate(variants):
        variant = graph.model_copy(deep=True)
        position = next(
            i for i, node in enumerate(variant.nodes) if node.data.nodeType == node_type
        )
        # Replace through the validated helper so a stepped example carries
        # its materialised `code` exactly as the parsed graph will.
        variant.nodes[position] = variant.nodes[position].with_config(
            {**config, "contract": "opaque", **deepcopy(_SHARED_COLUMN_CONFIG)}
        )
        yield f"{node_type.value}-variant-{index}", variant
    # A stepped transform's steps live only in its code (READ-22): the save renders them
    # into the body and the load rebuilds them, each id its kind and place, so the steps
    # carry the ids a load gives them and must survive save -> parse.
    variant = graph.model_copy(deep=True)
    polars = next(node for node in variant.nodes if node.data.nodeType == NodeType.POLARS)
    upstream_id = next(edge.source for edge in variant.edges if edge.target == polars.id)
    upstream = next(node for node in variant.nodes if node.id == upstream_id)
    stepped = polars.with_config(
        {
            "steps": [
                {
                    "id": "source-1",
                    "kind": "source",
                    "input": _sanitize_func_name(upstream.data.label),
                },
                {"id": "limit-2", "kind": "limit", "n": 5},
            ],
            "contract": "opaque",
            **deepcopy(_SHARED_COLUMN_CONFIG),
        }
    )
    variant.nodes[variant.nodes.index(polars)] = stepped
    yield "polars-steps", variant
    # A transform written as code keeps its keyword-only parameters in its signature.
    variant = graph.model_copy(deep=True)
    polars = next(node for node in variant.nodes if node.data.nodeType == NodeType.POLARS)
    assert "steps" not in polars.data.config
    polars.data.config["keyword_parameters"] = [
        {"name": "loading", "default": 1.05},
        {"name": "region", "default": None},
        {"name": "label", "default": "rated"},
    ]
    yield "polars-keyword-parameters", variant
    # A registered source names a version or an alias, never both.
    for node_type in (NodeType.MODEL_SCORE, NodeType.OPTIMISER_APPLY):
        variant = graph.model_copy(deep=True)
        node = next(node for node in variant.nodes if node.data.nodeType == node_type)
        node.data.config.pop("version", None)
        node.data.config["alias"] = "champion"
        yield f"{node_type.value}-alias", variant


# These fields represent graph relationships rather than independent settings.
# Their round-trip contracts are tested with real owner/copy and boundary graphs.
_STRUCTURAL_POLICY = {
    NodeType.POLARS: {"input_mapping"},
    NodeType.MODEL_SCORE: {"input_mapping"},
    NodeType.SUBMODEL: {"definition_id", "alias", "instance_of"},
}

# Keys every shape inherits from NodeConfigBase. ``selected_columns``,
# ``column_renames`` and an opaque ``contract`` are exercised on every type above.
# ``input_mapping`` is a graph relationship (test_rename_stable_binding.py,
# test_parser_conservation.py), and a declared column contract is checked against,
# and on a config-driven type replaced by, the contract its builder derives at
# parse, its parent keys being node ids (test_column_contracts_adoption.py,
# test_codegen.py).
_UNIVERSAL_POLICY = {
    "input_mapping",
    "contract.inputs",
    "contract.outputs",
    "contract.inputs_by_parent",
}

# Submodel containers are budgeted out of the root-decorator corpus
# (test_submodel_container_types_are_explicitly_budgeted); their multi-file
# round trip is test_parser_conservation.py's.
_CONTAINER_POLICY = {"selected_columns", "column_renames", "contract"}
_CONTAINER_TYPES = frozenset({NodeType.SUBMODEL, NodeType.SUBMODEL_PORT})


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
        assert _UNIVERSAL_POLICY <= declared, f"Stale universal policy for {node_type}"
        policy = policy | _UNIVERSAL_POLICY
        if node_type in _CONTAINER_TYPES:
            policy = policy | _CONTAINER_POLICY
        # A workbench node is given these from the form where it runs; none is persisted.
        given = FORM_GIVEN_KEYS.get(node_type, frozenset())
        gaps = declared - covered.get(node_type, set()) - policy - given
        if gaps:
            missing[node_type.value] = sorted(gaps)
    assert not missing, f"Persisted fields without round-trip examples: {missing}"


def _schemas():
    return {nt: shape_variants(model.shape) for nt, model in NODE_CONFIG_MODELS.items()}


def test_every_declared_field_has_a_roundtrip_example_or_structural_policy():
    _assert_field_inventory(list(_examples()), _schemas())


#: An API Input column's settings at the value inference gives them: its sidecar leaves
#: them out and the load gives them back (READ-42). Written out here, not imported, so the
#: oracle does not lean on the code it checks. A column given no "selected" is not
#: selected, and the save writes it so.
_API_INPUT_COLUMN_DEFAULTS = {
    "status": "Inferred",
    "selected": False,
    "levels": None,
    "origin": "inferred",
    "key": False,
}


def _authored(node_type, key, value):
    """*value* as the load gives it back, by the rules this oracle encodes."""
    if node_type == NodeType.API_INPUT and key == "tables":
        return [
            {
                **table,
                "columns": [
                    {**_API_INPUT_COLUMN_DEFAULTS, **column} for column in table["columns"]
                ],
            }
            for table in value
        ]
    return value


def _assert_authored_values(original, parsed):
    # This oracle deliberately does not call production normalizers: applying
    # the same lossy normalizer to both sides could conceal a persistence bug.
    # It encodes two equivalences, both rules of the file format. An absent
    # contract and "opaque" mean the same, so codegen omits an opaque contract
    # and a node whose settings live in its decorator parses back without one.
    # An API Input column given no "status", "levels", "origin" or "key" reads
    # back with the value inference gives it, and one given no "selected" as not
    # selected.
    for node in original.nodes:
        restored = parsed.node_map[_sanitize_func_name(node.data.label)].data.config
        for key, value in node.data.config.items():
            if key == "contract" and value == "opaque":
                assert restored.get(key, "opaque") == "opaque", (
                    f"{node.data.nodeType}.{key} changed"
                )
                continue
            assert key in restored, f"{node.data.nodeType}.{key} disappeared"
            expected = _authored(node.data.nodeType, key, value)
            assert restored[key] == expected, f"{node.data.nodeType}.{key} changed"


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


def test_theta_and_glm_controls_survive_a_sidecar_write():
    graph = _corpus_graphs()[0]
    node = next(node for node in graph.nodes if node.data.nodeType == NodeType.MODELLING)
    controls = {
        "algorithm": "glm",
        "family": "negbinomial",
        "theta": 2.5,
        "link": "log",
        "terms": {"_age": {"type": "bs", "df": 5}},
        "regularization": "ridge",
        "cv_folds": 4,
        "cv_selection": "1se",
        "cv_seed": 7,
        "max_iter": 50,
        "tol": 1e-6,
    }
    node.data.config.update(deepcopy(controls))

    _first, parsed, _second = _roundtrip(graph)

    restored = parsed.node_map[_sanitize_func_name(node.data.label)].data.config
    assert {key: restored.get(key) for key in controls} == controls


def _modelling_node(graph):
    return next(node for node in graph.nodes if node.data.nodeType == NodeType.MODELLING)


def test_removed_exclude_field_is_refused_on_save_not_round_tripped():
    """Features are opt-in: a modelling config still carrying ``exclude`` is
    refused by name rather than saved and silently carried forward."""
    graph = next(_examples())[1]
    _modelling_node(graph).data.config["exclude"] = ["_identifier"]

    with pytest.raises(ConfigError, match="removed exclude field") as raised:
        _roundtrip(graph)

    assert raised.value.context["removed_config_keys"] == ["exclude"]


def test_removed_exclude_field_is_refused_when_a_saved_sidecar_carries_it(tmp_path):
    """A hand-edited sidecar that still names ``exclude`` fails to load by name."""
    graph = next(_examples())[1]
    for rel_path, content in graph_to_code_multi(
        graph, pipeline_name="capstone_roundtrip", source_file="main.py"
    ).items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _write_configs_recursive(graph, tmp_path)
    sidecar = tmp_path / config_path_for_node(
        NodeType.MODELLING, _sanitize_func_name(_modelling_node(graph).data.label), tmp_path
    ).relative_to(tmp_path)
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    stored["exclude"] = ["_identifier"]
    sidecar.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(ConfigError, match="removed exclude field") as raised:
        parse_pipeline_file(tmp_path / "main.py")

    assert raised.value.context["removed_config_keys"] == ["exclude"]


@pytest.mark.parametrize("field", ["selected_columns", "column_renames"])
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
