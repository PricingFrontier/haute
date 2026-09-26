"""The ``contract=`` keyword appears only when it adds information.

``specs/codegen/low-level.md`` (Testing) describes what this module pins. A
node's decorator carries a column contract only when the contract tells the
parser something it cannot derive offline from the node's settings: a Model
Score's model-derived inputs, a declared transform contract, or declared
``inputs_by_parent`` fan-in metadata. An opaque contract, or one the settings
already imply (a Data Output or an Output), is left out. A file saved without
the keyword parses back to the same effective contract, and saving the parsed
graph again reproduces the file byte for byte.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haute._config_builder import resolve_parse_time_contract
from haute._config_io import collect_node_configs
from haute._contracts import Contract, get_column_contract
from haute._graph_utils import _sanitize_func_name
from haute._mlflow_io import ScoringModel
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code
from haute.parser import parse_pipeline_file
from haute.projection import declared_inputs_by_parent, overlay_declared_contract

# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------

#: The columns the stubbed model reads: known only once the model is loaded.
MODEL_FEATURES = ["rate", "premium"]

_SOURCE = {
    "inputType": "file",
    "format": "parquet",
    "mode": "scan",
    "path": "data/policies.parquet",
    "arguments": {},
}
_SCORE = {
    "sourceType": "run",
    "run_id": "run-1",
    "artifact_path": "model.cbm",
    "task": "regression",
    "output_column": "prediction",
}
_RESPONSE = {
    "outputMapping": [
        {
            "source_port": "in",
            "source_column": column,
            "output_path": f"$[:].{column}",
            "enabled": True,
        }
        for column in ("premium", "quote_id")
    ],
    "outputFormat": "json",
}
_RESULTS = {
    "outputType": "file",
    "format": "csv",
    "mode": "sink",
    "path": "outputs/results.csv",
    "arguments": {},
}
_DECLARED = {"inputs": ["base"], "outputs": ["premium"]}
_FAN_IN = {
    "inputs": ["area", "premium", "rate"],
    "outputs": [],
    "inputs_by_parent": {"n-declared": ["area", "premium"], "n-rates": ["area", "rate"]},
}


def _node(node_id: str, label: str, node_type: NodeType, config: dict) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=label, nodeType=node_type, config=config))


def _edge(source: str, target: str) -> GraphEdge:
    return GraphEdge(id=f"{source}->{target}", source=source, target=target)


def _graph() -> PipelineGraph:
    """Node ids differ from function names, so fan-in parents must be renamed on save."""
    return PipelineGraph(
        nodes=[
            _node("n-policies", "Policies", NodeType.DATA_INPUT, _SOURCE),
            _node("n-rates", "Rates", NodeType.DATA_INPUT, {**_SOURCE, "path": "data/rates.pq"}),
            _node(
                "n-lookup",
                "Lookup",
                NodeType.EXTERNAL_FILE,
                {"path": "models/lookup.pkl", "fileType": "pickle"},
            ),
            _node("n-opaque", "Opaque Transform", NodeType.POLARS, {"code": "df = Policies"}),
            _node(
                "n-declared-opaque",
                "Declared Opaque",
                NodeType.POLARS,
                {"code": "df = Opaque_Transform", "contract": "opaque"},
            ),
            _node(
                "n-declared",
                "Declared Transform",
                NodeType.POLARS,
                {
                    "code": 'df = Declared_Opaque.with_columns(premium=pl.col("base") * 2)',
                    "contract": _DECLARED,
                },
            ),
            _node(
                "n-fan-in",
                "Fan In",
                NodeType.POLARS,
                {"code": 'df = Declared_Transform.join(Rates, on="area")', "contract": _FAN_IN},
            ),
            _node("n-score", "Score", NodeType.MODEL_SCORE, _SCORE),
            _node(
                "n-severity",
                "Severity",
                NodeType.MODEL_SCORE,
                {**_SCORE, "output_column": "severity", "code": "df = df.head(5)"},
            ),
            _node("n-response", "Response", NodeType.OUTPUT, _RESPONSE),
            _node(
                "n-response-declared",
                "Response Declared",
                NodeType.OUTPUT,
                {**_RESPONSE, "contract": {"inputs": ["premium", "quote_id"], "outputs": []}},
            ),
            _node(
                "n-response-fan-in",
                "Response Fan In",
                NodeType.OUTPUT,
                {
                    **_RESPONSE,
                    "contract": {
                        "inputs": ["premium", "quote_id"],
                        "outputs": [],
                        "inputs_by_parent": {"n-score": ["premium", "quote_id"]},
                    },
                },
            ),
            _node("n-results", "Results", NodeType.DATA_OUTPUT, _RESULTS),
            _node(
                "n-results-declared",
                "Results Declared",
                NodeType.DATA_OUTPUT,
                {**_RESULTS, "contract": {"inputs": [], "outputs": []}},
            ),
        ],
        edges=[
            _edge("n-policies", "n-opaque"),
            _edge("n-opaque", "n-declared-opaque"),
            _edge("n-declared-opaque", "n-declared"),
            _edge("n-declared", "n-fan-in"),
            _edge("n-rates", "n-fan-in"),
            _edge("n-fan-in", "n-score"),
            _edge("n-fan-in", "n-severity"),
            _edge("n-score", "n-response"),
            _edge("n-score", "n-response-declared"),
            _edge("n-score", "n-response-fan-in"),
            _edge("n-score", "n-results"),
            _edge("n-score", "n-results-declared"),
        ],
    )


def _loaded_model(**_kwargs: object) -> ScoringModel:
    return ScoringModel(
        model=MagicMock(),
        feature_names=list(MODEL_FEATURES),
        cat_feature_names=frozenset(),
        flavor="catboost",
    )


def _unreachable_model(**_kwargs: object) -> ScoringModel:
    raise OSError("the model registry cannot be reached")


@pytest.fixture(autouse=True)
def _stubbed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model Score contracts read the stub: no test needs MLflow or a network."""
    monkeypatch.setattr("haute._mlflow_io.load_mlflow_model", _loaded_model)


# ---------------------------------------------------------------------------
# Saving and reading back
# ---------------------------------------------------------------------------


def _write_save(graph: PipelineGraph, directory: Path) -> str:
    """Save *graph* as a pipeline file and its sidecars under *directory*."""
    code = graph_to_code(graph, pipeline_name="contracts")
    (directory / "main.py").write_bytes(code.encode("utf-8"))
    for rel_path, content in collect_node_configs(graph).items():
        sidecar = directory / rel_path
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_bytes(content.encode("utf-8"))
    return code


@dataclass(frozen=True)
class Saved:
    """A first save of the graph, the graph parsed back from it, and a second save."""

    graph: PipelineGraph
    code: str
    sidecars: dict[str, str]
    parsed: PipelineGraph
    second: str
    second_sidecars: dict[str, str]

    def parsed_node(self, node: GraphNode) -> GraphNode:
        name = _sanitize_func_name(node.data.label)
        return next(parsed for parsed in self.parsed.nodes if parsed.id == name)


@pytest.fixture(scope="module")
def saved(tmp_path_factory: pytest.TempPathFactory) -> Saved:
    directory = tmp_path_factory.mktemp("contracts")
    graph = _graph()
    with patch("haute._mlflow_io.load_mlflow_model", _loaded_model):
        code = _write_save(graph, directory)
        parsed = parse_pipeline_file(directory / "main.py")
        second = graph_to_code(parsed, pipeline_name="contracts")
    return Saved(
        graph=graph,
        code=code,
        sidecars=collect_node_configs(graph),
        parsed=parsed,
        second=second,
        second_sidecars=collect_node_configs(parsed),
    )


def _contract_keywords(code: str) -> dict[str, object]:
    """Each function's ``contract=`` decorator value, by function name; absent when omitted."""
    keywords: dict[str, object] = {}
    for stmt in ast.parse(code).body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        (decorator,) = stmt.decorator_list
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg == "contract":
                    keywords[stmt.name] = ast.literal_eval(keyword.value)
    return keywords


def _node_labelled(label: str) -> GraphNode:
    return next(node for node in _graph().nodes if node.data.label == label)


def _builder_contract(node: GraphNode) -> Contract:
    return Contract.from_tuple(get_column_contract(node.data.nodeType, node.data.config))


def _effective_contract(node: GraphNode) -> Contract:
    """The contract execution enforces: the builder's, its opaque sides filled by a declaration."""
    return overlay_declared_contract(node, _builder_contract(node))


# ---------------------------------------------------------------------------
# Omitted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["Policies", "Lookup", "Opaque Transform", "Declared Opaque"])
def test_keyword_is_omitted_for_an_opaque_contract(saved: Saved, label: str) -> None:
    node = _node_labelled(label)

    assert _effective_contract(node) == Contract.opaque()
    assert _sanitize_func_name(label) not in _contract_keywords(saved.code)


@pytest.mark.parametrize(
    "label",
    ["Response", "Response Declared", "Results", "Results Declared", "Severity"],
)
def test_keyword_is_omitted_for_a_contract_the_settings_imply(saved: Saved, label: str) -> None:
    """The parser derives the same contract from the node's own settings."""
    node = _node_labelled(label)
    implied = resolve_parse_time_contract(node.data.nodeType, node.data.config)

    assert _effective_contract(node) == implied
    assert implied != Contract.opaque()
    assert _sanitize_func_name(label) not in _contract_keywords(saved.code)


def test_only_the_informative_contracts_are_emitted(saved: Saved) -> None:
    assert sorted(_contract_keywords(saved.code)) == [
        "Declared_Transform",
        "Fan_In",
        "Response_Fan_In",
        "Score",
    ]
    assert '"opaque"' not in saved.code


# ---------------------------------------------------------------------------
# Kept
# ---------------------------------------------------------------------------


def test_keyword_is_kept_for_a_model_scores_model_derived_inputs(saved: Saved) -> None:
    node = _node_labelled("Score")

    # Offline, the parser knows the output column but not the model's features.
    assert resolve_parse_time_contract(node.data.nodeType, node.data.config) == Contract(
        inputs=None, outputs=frozenset({"prediction"})
    )
    assert _contract_keywords(saved.code)["Score"] == {
        "inputs": sorted(MODEL_FEATURES),
        "outputs": ["prediction"],
    }


def test_keyword_is_kept_for_a_declared_transform_contract(saved: Saved) -> None:
    assert _contract_keywords(saved.code)["Declared_Transform"] == _DECLARED


def test_keyword_is_kept_for_inputs_by_parent_naming_parents_by_function(saved: Saved) -> None:
    keywords = _contract_keywords(saved.code)

    assert keywords["Fan_In"] == {
        "inputs": ["area", "premium", "rate"],
        "outputs": [],
        "inputs_by_parent": {
            "Declared_Transform": ["area", "premium"],
            "Rates": ["area", "rate"],
        },
    }
    # The sides alone are implied by the Output's mapping; the fan-in is not.
    assert keywords["Response_Fan_In"] == {
        "inputs": ["premium", "quote_id"],
        "outputs": [],
        "inputs_by_parent": {"Score": ["premium", "quote_id"]},
    }


# ---------------------------------------------------------------------------
# Reading the file back and saving it again
# ---------------------------------------------------------------------------


def _parent_ids(graph: PipelineGraph, node: GraphNode) -> list[str]:
    return [edge.source for edge in graph.edges if edge.target == node.id]


def test_saved_file_parses_back_to_the_same_effective_contract(saved: Saved) -> None:
    names = {node.id: _sanitize_func_name(node.data.label) for node in saved.graph.nodes}
    for node in saved.graph.nodes:
        parsed = saved.parsed_node(node)

        assert _effective_contract(parsed) == _effective_contract(node), node.data.label

        declared = declared_inputs_by_parent(node, _parent_ids(saved.graph, node))
        parsed_declared = declared_inputs_by_parent(parsed, _parent_ids(saved.parsed, parsed))
        renamed = None if declared is None else {names[p]: cols for p, cols in declared.items()}
        assert parsed_declared == renamed, node.data.label


def test_second_save_is_byte_identical(saved: Saved) -> None:
    assert saved.second == saved.code
    # Reading a file back invents no contract where the keyword was omitted:
    # those nodes' sidecars are rewritten unchanged too.
    kept = set(_contract_keywords(saved.code))
    omitted = {path: text for path, text in saved.sidecars.items() if Path(path).stem not in kept}
    assert {Path(path).stem for path in omitted} == {
        "Policies",
        "Rates",
        "Lookup",
        "Severity",
        "Response",
        "Response_Declared",
        "Results",
        "Results_Declared",
    }
    assert {path: saved.second_sidecars[path] for path in omitted} == omitted


def test_second_save_without_the_model_keeps_its_inputs(
    saved: Saved, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offline, the parsed annotation still supplies the inputs the model would."""
    monkeypatch.setattr("haute._mlflow_io.load_mlflow_model", _unreachable_model)

    assert graph_to_code(saved.parsed, pipeline_name="contracts") == saved.code
