"""The shape of a generated pipeline file.

``specs/codegen/low-level.md`` (Testing) describes what this module pins: a
corpus graph covering every configured node type as a declaration and every
code-accepting type as a hook, plus a transform, an instance and submodel
definition files, generates modules that read like a formatted, hand-written
file. They are a fixed point of ``ruff format`` at its defaults, they lint
clean, they carry no generated scaffolding, and they are laid out and ordered
as the codegen specification describes.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haute._graph_utils import _sanitize_func_name
from haute._mlflow_io import ScoringModel
from haute._standalone_nodes import CODE_NODE_TYPES
from haute._topo import topo_sort_ids
from haute._types import (
    DECORATOR_TO_NODE_TYPE,
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute.codegen import graph_to_code, graph_to_code_multi
from haute.errors import ConfigError

# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    label: str,
    node_type: NodeType,
    config: dict,
    *,
    description: str = "",
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=label, nodeType=node_type, config=config, description=description),
    )


def _edge(
    source: str,
    target: str,
    *,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=f"{source}->{target}:{source_handle}:{target_handle}",
        source=source,
        target=target,
        sourceHandle=source_handle,
        targetHandle=target_handle,
    )


def _data_input(path: str, **extra: object) -> dict:
    return {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": path,
        "arguments": {},
        **extra,
    }


_API_INPUT = {
    "path": "inputs/quote.json",
    "tables": [
        {
            "path": "$[:]",
            "label": "quotes",
            "emit": True,
            "columns": [
                {"name": "quote_id", "path": "$[:].quote_id", "type": "str", "selected": True}
            ],
        },
        {
            "path": "$[:].drivers[:]",
            "label": "drivers",
            "emit": True,
            "columns": [
                {"name": "quote_id", "path": "$[:].quote_id", "type": "str", "selected": True},
                {
                    "name": "driver_age",
                    "path": "$[:].drivers[:].driver_age",
                    "type": "int",
                    "selected": True,
                },
            ],
        },
    ],
}
_EDGE_JOIN = {
    "how": "left",
    "leftOn": ["policy_id"],
    "rightOn": ["policy_id"],
    "suffix": "_claim",
}
_LIVE_SWITCH = {
    "input_scenario_map": {"quotes": "live", "Policy_Claims": "batch"},
    "inputs": ["quotes", "Policy_Claims"],
}
_CONSTANT = {"values": [{"name": "base_rate", "value": "100"}]}
_BANDING = {
    "factors": [
        {
            "banding": "breakpoints",
            "column": "driver_age",
            "outputColumn": "age_band",
            "rules": [{"boundary": "25", "label": "young"}],
            "default": "other",
        }
    ]
}
_RATING_STEP = {
    "tables": [
        {
            "name": "Area",
            "factors": ["area"],
            "outputColumn": "area_factor",
            "defaultValue": "1.0",
            "entries": [{"area": "A", "value": "1.2"}],
        }
    ],
}
_MODEL_SCORE = {
    "sourceType": "run",
    "run_id": "run-1",
    "artifact_path": "model.cbm",
    "task": "regression",
    "output_column": "prediction",
}
_SCENARIO_EXPANDER = {
    "quote_id": "quote_id",
    "column_name": "price_step",
    "min_value": 0.9,
    "max_value": 1.1,
    "stepCount": 3,
    "step_column": "step_index",
}
_OPTIMISER = {
    "mode": "online",
    "quote_id": "quote_id",
    "scenario_index": "step_index",
    "scenario_value": "price_step",
    "objective": "profit",
    "constraints": {"loss_ratio": {"max": 0.65}},
    "max_iter": 7,
    "tolerance": 0.001,
    "chunk_size": 128,
}
_OPTIMISER_APPLY = {
    "sourceType": "file",
    "artifact_path": "artifacts/optimiser.json",
    "version_column": "__optimiser_version__",
    "optimised_value_column": "selected_price",
}
_MODELLING = {
    "name": "frequency",
    "target": "claim_count",
    "algorithm": "glm",
    "task": "regression",
    "metrics": ["rmse"],
    "feature_columns": ["driver_age"],
}
_EXTERNAL_FILE = {"path": "models/lookup.pkl", "fileType": "pickle"}
_OUTPUT = {
    "outputMapping": [
        {
            "source_port": "in",
            "source_column": "premium",
            "output_path": "$[:].premium",
            "enabled": True,
        }
    ],
    "outputFormat": "json",
}
_DATA_OUTPUT = {
    "outputType": "file",
    "format": "csv",
    "mode": "sink",
    "path": "outputs/prices.csv",
    "arguments": {},
}

#: The columns the stubbed frequency model reads, so its declaration carries a
#: contract the parser could not derive offline.
_MODEL_FEATURES = ["area_factor", "driver_age", "no_claims_years", "vehicle_group"]

# Authored code. Hooks and transforms carry it verbatim, followed by ``return df``.
_CLAIMS_CODE = (
    "# Settled claims only\n"
    'df = df.filter(pl.col("status") == "settled")\n'
    "\n"
    'df = df.with_columns(pl.col("paid").fill_null(0))'
)
_LOOKUP_CODE = "df = pl.DataFrame(obj).lazy()"
_PREPARED_CODE = 'df = Live_Or_Batch.join(Base_Rates, how="cross")'
_RATING_CODE = 'df = df.join(Territory_Lookup, on="area", how="left")'
_SEVERITY_CODE = 'df = df.with_columns((pl.col("prediction") * 1.1).alias("severity"))'
_TAGGED_CODE = 'df = df.with_columns(pl.lit("review").alias("tag"))'
_ENRICHED_CODE = 'df = df.with_columns(pl.lit(len(obj)).alias("lookup_size"))'
_EXPLORE_CODE = 'df = df.filter(pl.col("lookup_size") > 0)'
_TERRITORY_CODE = 'df = records.with_columns(pl.col("postcode").str.slice(0, 2).alias("area"))'

_TERRITORY_FILE = "modules/territory_rating.py"
_SHARED_FILE = "modules/shared/territory_rate_tables.py"


def _territory_definition() -> dict:
    """A definition one folder below the pipeline: a transform and two declarations."""
    return {
        "definitionId": "territory_rating",
        "file": _TERRITORY_FILE,
        "inputPorts": [{"name": "records", "targets": [{"nodeId": "t-prep"}]}],
        "outputPorts": [{"name": "rated", "source": {"nodeId": "t-rate"}}],
        "graph": {
            "nodes": [
                _node("t-prep", "Territory Prep", NodeType.POLARS, {"code": _TERRITORY_CODE}),
                _node("t-factors", "Territory Factors", NodeType.CONSTANT, _CONSTANT),
                _node("t-rate", "Territory Rate", NodeType.RATING_STEP, _RATING_STEP),
            ],
            "edges": [_edge("t-prep", "t-rate"), _edge("t-factors", "t-rate")],
            "pipeline_description": "Rates each territory",
        },
    }


def _shared_definition() -> dict:
    """A declaration-only definition two folders below the pipeline."""
    return {
        "definitionId": "shared_rates",
        "file": _SHARED_FILE,
        "inputPorts": [],
        "outputPorts": [{"name": "territory_rates", "source": {"nodeId": "s-table"}}],
        "graph": {
            "nodes": [_node("s-table", "Shared Rate Table", NodeType.CONSTANT, _CONSTANT)],
            "edges": [],
        },
    }


def _occurrence(node_id: str, alias: str, definition_id: str, **extra: str) -> GraphNode:
    config = {"definitionId": definition_id, "alias": alias, **extra}
    return GraphNode(
        id=node_id,
        type="submodel",
        data=NodeData(label=alias, nodeType=NodeType.SUBMODEL, config=config),
    )


def _corpus_graph() -> PipelineGraph:
    """Every configured type as a declaration and every code-accepting type as a hook.

    Also a transform, an instance, two submodel definitions (one with a
    copy occurrence), sources consumed once, twice or never, and labels long
    enough that decorators, signatures, registrations and connect calls break
    in each of the layouts ruff uses. A lone parameter or argument that
    breaks is pinned separately (``test_a_lone_parameter_or_argument_breaks_as_ruff_breaks_it``).
    """
    nodes = [
        _node("unused", "Archived Quotes", NodeType.DATA_INPUT, _data_input("data/old.parquet")),
        _node(
            "api",
            "Quote Request",
            NodeType.API_INPUT,
            _API_INPUT,
            description='The live "quote" request',
        ),
        _node(
            "pol",
            "Policies",
            NodeType.DATA_INPUT,
            _data_input("data/policies.parquet"),
            description="Policy extract\n  one row per policy\nrefreshed nightly",
        ),
        _node(
            "clm",
            "Claims Extract",
            NodeType.DATA_INPUT,
            _data_input("data/claims.parquet", code=_CLAIMS_CODE),
        ),
        _node("rates", "Base Rates", NodeType.CONSTANT, _CONSTANT),
        _node(
            "lookup",
            "Territory Lookup",
            NodeType.EXTERNAL_FILE,
            {**_EXTERNAL_FILE, "code": _LOOKUP_CODE},
        ),
        _node("join", "Policy Claims", NodeType.EDGE_JOIN, _EDGE_JOIN),
        _node("switch", "Live Or Batch", NodeType.LIVE_SWITCH, _LIVE_SWITCH),
        _node(
            "prep",
            "Prepared Frame",
            NodeType.POLARS,
            {
                "code": _PREPARED_CODE,
                "selected_columns": ['premium "gross"', "driver's age", "policy_id"],
            },
            description='Joins the "base" rates',
        ),
        _node("band", "Age Bands", NodeType.BANDING, _BANDING, description="Bands drivers by age"),
        _node("rate-d", "Area Rating", NodeType.RATING_STEP, _RATING_STEP),
        _node(
            "rate-h",
            "Area Rating Adjusted",
            NodeType.RATING_STEP,
            {**_RATING_STEP, "code": _RATING_CODE},
        ),
        _node("score-d", "Frequency Score", NodeType.MODEL_SCORE, _MODEL_SCORE),
        _node(
            "score-h",
            "Severity Score",
            NodeType.MODEL_SCORE,
            {**_MODEL_SCORE, "code": _SEVERITY_CODE},
            description="Severity uplift",
        ),
        _node("exp-d", "Price Scenarios", NodeType.SCENARIO_EXPANDER, _SCENARIO_EXPANDER),
        _node(
            "exp-h",
            "Price Scenarios Tagged For Review",
            NodeType.SCENARIO_EXPANDER,
            {**_SCENARIO_EXPANDER, "code": _TAGGED_CODE},
        ),
        _node("opt", "Optimise Price", NodeType.OPTIMISER, _OPTIMISER),
        _node("apply", "Apply Optimised Street Price", NodeType.OPTIMISER_APPLY, _OPTIMISER_APPLY),
        _node("model", "Frequency Model", NodeType.MODELLING, _MODELLING),
        _node("ext-d", "Lookup Table", NodeType.EXTERNAL_FILE, _EXTERNAL_FILE),
        _node(
            "ext-h",
            "Lookup Enriched With Territories",
            NodeType.EXTERNAL_FILE,
            {**_EXTERNAL_FILE, "code": _ENRICHED_CODE},
        ),
        _node(
            "explore-d",
            "Explore Prices",
            NodeType.EXPLORE,
            {"overview": {"dataset_snapshot": True, "schema": True}},
        ),
        _node("explore-h", "Explore Enriched Lookup", NodeType.EXPLORE, {"code": _EXPLORE_CODE}),
        _node("out", "Quote Response", NodeType.OUTPUT, _OUTPUT),
        _node("sink", "Results File", NodeType.DATA_OUTPUT, _DATA_OUTPUT),
        _node(
            "inst",
            "Prepared Frame Copy",
            NodeType.POLARS,
            {
                "instanceOf": "prep",
                "inputMapping": {"Live_Or_Batch": "Policy_Claims", "Base_Rates": "Base_Rates"},
            },
        ),
        _occurrence("occ", "territory_rating", "territory_rating"),
        _occurrence("occ-copy", "territory_rating_copy", "territory_rating", instanceOf="occ"),
        _occurrence("occ-shared", "shared_territory_rate_tables", "shared_rates"),
    ]
    edges = [
        _edge("pol", "join", target_handle="base"),
        _edge("clm", "join", target_handle="join"),
        _edge("api", "switch", source_handle="quotes"),
        _edge("join", "switch"),
        _edge("switch", "prep"),
        _edge("rates", "prep", source_handle='base "v2"'),
        _edge("prep", "band"),
        _edge("band", "rate-d"),
        _edge("band", "rate-h"),
        _edge("lookup", "rate-h"),
        _edge("rates", "rate-h"),
        _edge("occ-shared", "rate-h", source_handle="out__territory_rates"),
        _edge("rate-d", "score-d"),
        _edge("rate-h", "score-h"),
        _edge("score-d", "exp-d"),
        _edge("score-h", "exp-h"),
        _edge("exp-d", "opt"),
        _edge("opt", "apply"),
        _edge("apply", "model"),
        _edge("exp-h", "ext-d"),
        _edge("ext-d", "ext-h"),
        _edge("exp-h", "ext-h"),
        _edge("apply", "explore-d"),
        _edge("ext-h", "explore-h"),
        _edge("apply", "out"),
        _edge("apply", "sink"),
        _edge("occ", "sink", source_handle="out__rated"),
        _edge("join", "inst"),
        _edge("rates", "inst"),
        _edge("prep", "occ", target_handle="in__records"),
        _edge("join", "occ-copy", target_handle="in__records"),
    ]
    return PipelineGraph(
        nodes=nodes,
        edges=edges,
        submodels={
            "territory_rating": _territory_definition(),
            "shared_rates": _shared_definition(),
        },
    )


_PIPELINE_NAME = "motor"
_PIPELINE_DESCRIPTION = 'Prices a "quote" from policy and claims'


def _stub_model() -> ScoringModel:
    return ScoringModel(
        model=MagicMock(),
        feature_names=list(_MODEL_FEATURES),
        cat_feature_names=frozenset(),
        flavor="catboost",
    )


def _generate_corpus() -> dict[str, str]:
    """Generate the corpus offline: the model's feature columns come from a stub."""
    with patch("haute._mlflow_io.load_mlflow_model", return_value=_stub_model()):
        return graph_to_code_multi(
            _corpus_graph(),
            pipeline_name=_PIPELINE_NAME,
            description=_PIPELINE_DESCRIPTION,
            source_file="main.py",
        )


@dataclass(frozen=True)
class Module:
    """The nodes one generated file defines a function for, and what feeds them."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    inputs: dict[str, list[str]]

    def node_named(self, func_name: str) -> GraphNode:
        return next(
            node for node in self.nodes if _sanitize_func_name(node.data.label) == func_name
        )


def _input_name(edge: GraphEdge, source: GraphNode) -> str:
    """The parameter an incoming edge contributes: frame label, public port or node name."""
    if source.data.nodeType == NodeType.SUBMODEL:
        return (edge.sourceHandle or "").removeprefix("out__")
    if source.data.nodeType == NodeType.API_INPUT:
        return edge.sourceHandle or ""
    return _sanitize_func_name(source.data.label)


def _modules(graph: PipelineGraph) -> dict[str, Module]:
    """Each generated file of *graph*, keyed by its path, with the nodes it defines."""
    nodes = {node.id: node for node in graph.nodes}
    root = [node for node in graph.nodes if node.data.nodeType != NodeType.SUBMODEL]
    root_ids = {node.id for node in root}
    inputs: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.target in root_ids:
            inputs.setdefault(edge.target, []).append(_input_name(edge, nodes[edge.source]))
    modules = {
        "main.py": Module(
            nodes=root,
            edges=[e for e in graph.edges if e.source in root_ids and e.target in root_ids],
            inputs=inputs,
        )
    }
    for definition in (graph.submodels or {}).values():
        child = definition.graph
        child_nodes = {node.id: node for node in child.nodes}
        child_inputs: dict[str, list[str]] = {}
        for port in definition.input_ports:
            for target in port.targets:
                child_inputs.setdefault(target.node_id, []).append(port.name)
        for edge in child.edges:
            child_inputs.setdefault(edge.target, []).append(
                _input_name(edge, child_nodes[edge.source])
            )
        modules[definition.file] = Module(
            nodes=list(child.nodes), edges=list(child.edges), inputs=child_inputs
        )
    return modules


@dataclass(frozen=True)
class Corpus:
    """The generated modules, keyed by path, and a directory holding them."""

    files: dict[str, str]
    root: Path
    modules: dict[str, Module]

    @property
    def paths(self) -> list[str]:
        return sorted(self.files)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Corpus:
    files = _generate_corpus()
    root = tmp_path_factory.mktemp("layout")
    for rel_path, text in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return Corpus(files=files, root=root, modules=_modules(_corpus_graph()))


# ---------------------------------------------------------------------------
# Reading generated source
# ---------------------------------------------------------------------------


def _functions(text: str) -> list[ast.FunctionDef]:
    return [stmt for stmt in ast.parse(text).body if isinstance(stmt, ast.FunctionDef)]


def _dotted(expr: ast.expr) -> str:
    """``a.b.c`` for a name or attribute chain, ``""`` for anything else."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        owner = _dotted(expr.value)
        return f"{owner}.{expr.attr}" if owner else ""
    return ""


def _decorator_name(function: ast.FunctionDef) -> str:
    """The registry method a node function is decorated with: ``data_input``, ``polars``, ..."""
    (decorator,) = function.decorator_list
    callee = decorator.func if isinstance(decorator, ast.Call) else decorator
    return _dotted(callee).split(".", 1)[1]


def _statements_after_docstring(function: ast.FunctionDef) -> list[ast.stmt]:
    return (
        function.body[1:] if ast.get_docstring(function, clean=False) is not None else function.body
    )


def _is_declaration(function: ast.FunctionDef) -> bool:
    rest = _statements_after_docstring(function)
    return not rest or (
        len(rest) == 1
        and isinstance(rest[0], ast.Expr)
        and isinstance(rest[0].value, ast.Constant)
        and rest[0].value.value is Ellipsis
    )


def _dump(statements: list[ast.stmt]) -> list[str]:
    return [ast.dump(statement) for statement in statements]


def _parameters(function: ast.FunctionDef) -> list[tuple[str, str | None]]:
    """``(name, annotation)`` for each positional parameter."""
    return [
        (arg.arg, None if arg.annotation is None else ast.unparse(arg.annotation))
        for arg in function.args.args
    ]


def _refers_to_pl(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.Name) and node.id == "pl" for node in ast.walk(tree))


def _imports(tree: ast.Module) -> list[str]:
    return [
        ast.unparse(stmt) for stmt in tree.body if isinstance(stmt, (ast.Import, ast.ImportFrom))
    ]


# ---------------------------------------------------------------------------
# ruff, as a user runs it on a saved project: its defaults, no configuration
# ---------------------------------------------------------------------------


def _ruff(command: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", command, "--isolated", "--no-cache", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


#: ``ruff check`` rule families the codegen specification names. flake8-return
#: (``RET``) is named too but not checked here: its RET504 reports every hook
#: and transform whose code ends by assigning ``df``, because the generated
#: body returns ``df`` on the next line.
RUFF_RULE_FAMILIES = {
    "pycodestyle": "E,W",
    "pyflakes": "F",
    "isort": "I",
    "pyupgrade": "UP",
    "bugbear": "B",
    "comprehensions": "C4",
    "simplify": "SIM",
    "pie": "PIE",
    "empty-docstring": "D419",
}

#: Configured node types: every type but the transform and the submodel containers.
CONFIGURED_TYPES = frozenset(NodeType) - {
    NodeType.POLARS,
    NodeType.SUBMODEL,
    NodeType.SUBMODEL_PORT,
}


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


def test_corpus_covers_every_declaration_and_hook_type(corpus: Corpus) -> None:
    declarations: set[NodeType] = set()
    hooks: set[NodeType] = set()
    decorators: set[str] = set()
    for text in corpus.files.values():
        for function in _functions(text):
            decorator = _decorator_name(function)
            decorators.add(decorator)
            if decorator in {"instance", "polars"}:
                continue
            kind = declarations if _is_declaration(function) else hooks
            kind.add(DECORATOR_TO_NODE_TYPE[decorator])

    assert declarations == CONFIGURED_TYPES
    assert hooks == CODE_NODE_TYPES
    assert {"instance", "polars"} <= decorators
    assert sorted(corpus.files) == ["main.py", _SHARED_FILE, _TERRITORY_FILE]


def test_generation_is_deterministic(corpus: Corpus) -> None:
    assert _generate_corpus() == corpus.files


# ---------------------------------------------------------------------------
# Formatted like a hand-written file
# ---------------------------------------------------------------------------


def test_modules_are_a_fixed_point_of_ruff_format(corpus: Corpus) -> None:
    result = _ruff("format", "--diff", *corpus.paths, cwd=corpus.root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_modules_are_laid_out_as_ruff_lays_out_unformatted_source(corpus: Corpus) -> None:
    """Every break is the one ruff derives from the line width alone.

    With magic trailing commas ignored, ruff lays each call, signature and
    collection out afresh, so a sequence the layout broke although it fits, or
    kept flat although it does not, would be reformatted.
    """
    result = _ruff(
        "format",
        "--config",
        "format.skip-magic-trailing-comma = true",
        "--diff",
        *corpus.paths,
        cwd=corpus.root,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("family", sorted(RUFF_RULE_FAMILIES))
def test_modules_pass_ruff_check(corpus: Corpus, family: str) -> None:
    result = _ruff(
        "check",
        "--select",
        RUFF_RULE_FAMILIES[family],
        "--output-format",
        "concise",
        *corpus.paths,
        cwd=corpus.root,
    )

    assert result.returncode == 0, result.stdout + result.stderr


#: Long decorators, signatures, registrations and connect calls in the corpus,
#: each in one of the layouts ruff uses: arguments on one indented line of
#: their own, or one per line with a trailing comma (a dict or list value that
#: does not fit then puts one entry per line).
BROKEN_CALLS = {
    "decorator, keyword on an indented line": (
        "@pipeline.optimiser_apply(\n"
        '    config="config/apply_optimisation/Apply_Optimised_Street_Price.json"\n'
        ")\n"
        "def Apply_Optimised_Street_Price(Optimise_Price): ...\n"
    ),
    "decorator, keywords on an indented line": (
        "@pipeline.edge_join(\n"
        '    how="left", left_on=["policy_id"], right_on=["policy_id"], suffix="_claim"\n'
        ")\n"
        "def Policy_Claims(Policies, Claims_Extract): ...\n"
    ),
    "decorator, one keyword per line": (
        "@pipeline.model_score(\n"
        '    config="config/model_scoring/Frequency_Score.json",\n'
        "    contract={\n"
        '        "inputs": ["area_factor", "driver_age", "no_claims_years", "vehicle_group"],\n'
        '        "outputs": ["prediction"],\n'
        "    },\n"
        ")\n"
        "def Frequency_Score(Area_Rating): ...\n"
    ),
    "decorator, instance mapping": (
        "@pipeline.instance(\n"
        '    of="Prepared_Frame",\n'
        '    inputMapping={"Live_Or_Batch": "Policy_Claims", "Base_Rates": "Base_Rates"},\n'
        ")\n"
        "def Prepared_Frame_Copy(Policy_Claims, Base_Rates): ...\n"
    ),
    "signature, parameters on an indented line": (
        "def Prepared_Frame(\n"
        "    Live_Or_Batch: pl.LazyFrame, Base_Rates: pl.LazyFrame\n"
        ") -> pl.LazyFrame:\n"
    ),
    "signature, one parameter per line": (
        "def Area_Rating_Adjusted(\n"
        "    df: pl.LazyFrame,\n"
        "    Territory_Lookup: pl.LazyFrame,\n"
        "    Base_Rates: pl.LazyFrame,\n"
        "    territory_rates: pl.LazyFrame,\n"
        ") -> pl.LazyFrame:\n"
    ),
    "signature, keyword-only object": (
        "def Lookup_Enriched_With_Territories(\n"
        "    Lookup_Table: pl.LazyFrame, Price_Scenarios_Tagged_For_Review: pl.LazyFrame, *, obj\n"
        ") -> pl.LazyFrame:\n"
    ),
    "constructor, arguments on an indented line": (
        "pipeline = haute.Pipeline(\n"
        '    "motor", description=\'Prices a "quote" from policy and claims\'\n'
        ")\n"
    ),
    "registration, arguments on an indented line": (
        "pipeline.submodel(\n"
        '    "modules/shared/territory_rate_tables.py", "shared_territory_rate_tables"\n'
        ")\n"
    ),
    "registration, one argument per line": (
        "pipeline.submodel(\n"
        '    "modules/territory_rating.py",\n'
        '    "territory_rating_copy",\n'
        '    instance_of="territory_rating",\n'
        ")\n"
    ),
    "connect, flat": 'pipeline.connect("Quote_Request", "Live_Or_Batch", source_port="quotes")\n',
    "connect, arguments on an indented line": (
        "pipeline.connect(\n"
        '    "Price_Scenarios_Tagged_For_Review", "Lookup_Enriched_With_Territories"\n'
        ")\n"
    ),
    "connect, one argument per line": (
        "pipeline.connect(\n"
        '    "shared_territory_rate_tables",\n'
        '    "Area_Rating_Adjusted",\n'
        '    source_port="territory_rates",\n'
        ")\n"
    ),
}


@pytest.mark.parametrize("snippet", BROKEN_CALLS.values(), ids=list(BROKEN_CALLS))
def test_long_decorators_signatures_and_calls_break_as_ruff_breaks_them(
    corpus: Corpus, snippet: str
) -> None:
    assert snippet in corpus.files["main.py"]


#: Graphs whose one long parameter or one long argument has to break, and the
#: layout ruff gives each: a signature's lone parameter takes a trailing comma,
#: a call's lone argument does not.
LONE_BREAKS = {
    "transform parameter": (
        PipelineGraph(
            nodes=[
                _node(
                    "src",
                    "Quarterly Policy Extract With Adjustments",
                    NodeType.DATA_INPUT,
                    _data_input("d.parquet"),
                ),
                _node(
                    "tx",
                    "Calculate Adjusted Premium",
                    NodeType.POLARS,
                    {"code": "df = Quarterly_Policy_Extract_With_Adjustments"},
                ),
            ],
            edges=[_edge("src", "tx")],
        ),
        "def Calculate_Adjusted_Premium(\n"
        "    Quarterly_Policy_Extract_With_Adjustments: pl.LazyFrame,\n"
        ") -> pl.LazyFrame:\n",
    ),
    "declaration parameter": (
        PipelineGraph(
            nodes=[
                _node(
                    "src",
                    "Quarterly Policy Extract With Late Adjustments",
                    NodeType.DATA_INPUT,
                    _data_input("d.parquet"),
                ),
                _node(
                    "sink",
                    "Quarterly Results Written Back To The Store",
                    NodeType.DATA_OUTPUT,
                    {
                        "outputType": "file",
                        "format": "csv",
                        "mode": "sink",
                        "path": "out.csv",
                        "arguments": {},
                    },
                ),
            ],
            edges=[_edge("src", "sink")],
        ),
        "def Quarterly_Results_Written_Back_To_The_Store(\n"
        "    Quarterly_Policy_Extract_With_Late_Adjustments,\n"
        "): ...\n",
    ),
    "decorator argument": (
        PipelineGraph(
            nodes=[
                _node("src", "Quotes", NodeType.DATA_INPUT, _data_input("d.parquet")),
                _node(
                    "ex",
                    "Explore Quotes",
                    NodeType.EXPLORE,
                    {
                        "overview": {
                            "dataset_snapshot": True,
                            "data_quality": True,
                            "numeric_summary": False,
                            "categorical_summary": True,
                            "schema": True,
                        }
                    },
                ),
            ],
            edges=[_edge("src", "ex")],
        ),
        "@pipeline.explore(\n"
        "    overview={\n"
        '        "dataset_snapshot": True,\n'
        '        "data_quality": True,\n'
        '        "numeric_summary": False,\n'
        '        "categorical_summary": True,\n'
        '        "schema": True,\n'
        "    }\n"
        ")\n",
    ),
}


@pytest.mark.parametrize("case", sorted(LONE_BREAKS))
def test_a_lone_parameter_or_argument_breaks_as_ruff_breaks_it(case: str, tmp_path: Path) -> None:
    graph, expected = LONE_BREAKS[case]
    code = graph_to_code(graph, pipeline_name="p")
    (tmp_path / "main.py").write_bytes(code.encode("utf-8"))

    assert expected in code
    for options in ((), ("--config", "format.skip-magic-trailing-comma = true")):
        result = _ruff("format", *options, "--diff", "main.py", cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr


def test_definition_files_below_the_pipeline_record_the_way_back_last(corpus: Corpus) -> None:
    ways_back: dict[str, str | None] = {}
    for path, text in corpus.files.items():
        (construction,) = [stmt for stmt in ast.parse(text).body if isinstance(stmt, ast.Assign)]
        call = construction.value
        assert isinstance(call, ast.Call)
        last = call.keywords[-1] if call.keywords else None
        is_way_back = last is not None and last.arg == "pipeline_dir"
        ways_back[path] = ast.literal_eval(last.value) if is_way_back else None

    assert ways_back == {"main.py": None, _TERRITORY_FILE: "..", _SHARED_FILE: "../.."}


# ---------------------------------------------------------------------------
# No generated scaffolding
# ---------------------------------------------------------------------------


#: What a module's own statements may call: construction, a node decorator, a
#: submodel registration or a connection.
_MODULE_CALLS = frozenset(
    {"haute.Pipeline", "haute.Submodel", "pipeline.submodel", "pipeline.connect"}
    | {"submodel.connect"}
    | {
        f"{receiver}.{name}"
        for receiver in ("pipeline", "submodel")
        for name in DECORATOR_TO_NODE_TYPE
    }
)


def _module_level_calls(tree: ast.Module) -> Iterator[ast.Call]:
    """Every call outside a function body, decorators included."""
    for stmt in tree.body:
        roots: list[ast.AST] = (
            list(stmt.decorator_list) + [stmt.args] if isinstance(stmt, ast.FunctionDef) else [stmt]
        )
        for root in roots:
            yield from (node for node in ast.walk(root) if isinstance(node, ast.Call))


_MODULE_TITLES = {
    "main.py": f"Pipeline: {_PIPELINE_NAME}",
    _TERRITORY_FILE: "Submodel: territory_rating",
    _SHARED_FILE: "Submodel: shared_rates",
}


def test_modules_carry_no_loader_private_import_file_expression_or_empty_docstring(
    corpus: Corpus,
) -> None:
    for path, text in corpus.files.items():
        tree = ast.parse(text)
        receiver = "pipeline" if path == "main.py" else "submodel"

        assert ast.get_docstring(tree, clean=False) == _MODULE_TITLES[path]
        assert _imports(tree) in (["import haute"], ["import haute", "import polars as pl"]), path
        assert not any(
            isinstance(node, ast.Name) and node.id == "__file__" for node in ast.walk(tree)
        )
        for function in _functions(text):
            docstring = ast.get_docstring(function, clean=False)
            assert docstring is None or docstring.strip(), (path, function.name)
        # Beyond its docstring and imports, a module constructs its object,
        # defines one function per node, and registers and wires them.
        for stmt in tree.body[1:]:
            if isinstance(stmt, (ast.Import, ast.FunctionDef)):
                continue
            if isinstance(stmt, ast.Assign):
                assert [ast.unparse(target) for target in stmt.targets] == [receiver]
                assert isinstance(stmt.value, ast.Call)
                assert _dotted(stmt.value.func) == f"haute.{receiver.title()}"
                continue
            assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call), (
                path,
                ast.unparse(stmt),
            )
        for call in _module_level_calls(tree):
            assert _dotted(call.func) in _MODULE_CALLS, (path, ast.unparse(call))


def test_declarations_have_no_annotations_and_the_description_is_the_whole_body(
    corpus: Corpus,
) -> None:
    described = undescribed = 0
    for path, module in corpus.modules.items():
        text = corpus.files[path]
        lines = text.splitlines()
        for function in _functions(text):
            node = module.node_named(function.name)
            declared = node.data.config.get("instanceOf") or (
                node.data.nodeType in CONFIGURED_TYPES and not node.data.config.get("code")
            )
            if not declared:
                continue
            params = _parameters(function)

            assert [name for name, _ in params] == module.inputs.get(node.id, []), function.name
            assert all(annotation is None for _, annotation in params), function.name
            assert not function.args.kwonlyargs and function.args.vararg is None
            assert function.returns is None, function.name
            if node.data.description:
                described += 1
                assert len(function.body) == 1, function.name
                assert ast.get_docstring(function) == node.data.description
            else:
                undescribed += 1
                assert [ast.unparse(stmt) for stmt in function.body] == ["..."], function.name
                # Printed as ruff keeps a stub: the body on the ``def`` line.
                assert lines[function.body[0].lineno - 1].endswith("): ..."), function.name

    assert described >= 3 and undescribed >= 10


def _expected_body(node: GraphNode, inputs: list[str]) -> str:
    """The statements a hook or transform holds after its docstring."""
    code = str(node.data.config["code"])
    if node.data.nodeType == NodeType.EXTERNAL_FILE and inputs:
        code = f"df = {inputs[0]}\n{code}"
    return f"{code}\nreturn df"


def test_hooks_and_transforms_hold_only_the_authored_code(corpus: Corpus) -> None:
    """The body is the user's code and ``return df``, plus an External File's binding."""
    kinds: set[str] = set()
    for path, module in corpus.modules.items():
        for function in _functions(corpus.files[path]):
            node = module.node_named(function.name)
            if node.data.config.get("instanceOf") or not node.data.config.get("code"):
                continue
            inputs = module.inputs.get(node.id, [])
            params = _parameters(function)
            if node.data.nodeType == NodeType.POLARS:
                kinds.add("transform")
                assert params == [(name, "pl.LazyFrame") for name in inputs]
            elif node.data.nodeType == NodeType.EXTERNAL_FILE:
                kinds.add("external file hook")
                assert params == [(name, "pl.LazyFrame") for name in inputs]
                assert [(arg.arg, arg.annotation) for arg in function.args.kwonlyargs] == [
                    ("obj", None)
                ]
            else:
                kinds.add("hook")
                assert params == [("df", "pl.LazyFrame")] + [
                    (name, "pl.LazyFrame") for name in inputs[1:]
                ]
            assert ast.unparse(function.returns) == "pl.LazyFrame"  # type: ignore[arg-type]
            assert _dump(_statements_after_docstring(function)) == _dump(
                ast.parse(_expected_body(node, inputs)).body
            ), function.name
            assert (ast.get_docstring(function) or "") == node.data.description

    assert kinds == {"transform", "external file hook", "hook"}


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def test_corpus_imports_polars_only_where_the_module_refers_to_pl(corpus: Corpus) -> None:
    imports_polars = {}
    for path, text in corpus.files.items():
        tree = ast.parse(text)
        imports_polars[path] = "import polars as pl" in _imports(tree)
        without_imports = ast.Module(
            body=[s for s in tree.body if not isinstance(s, (ast.Import, ast.ImportFrom))],
            type_ignores=[],
        )
        assert imports_polars[path] == _refers_to_pl(without_imports), path

    assert imports_polars == {"main.py": True, _TERRITORY_FILE: True, _SHARED_FILE: False}


def _declarations_only(*, preamble: str = "") -> str:
    """A module of declarations whose strings and docstrings mention ``pl``."""
    graph = PipelineGraph(
        nodes=[
            _node(
                "src",
                "Quotes",
                NodeType.DATA_INPUT,
                _data_input("data/quotes.parquet"),
                description="Read with pl.scan_parquet",
            ),
            _node(
                "explore",
                "Explore Quotes",
                NodeType.EXPLORE,
                {"selected_columns": ["pl", "pl.col"]},
            ),
        ],
        edges=[_edge("src", "explore")],
    )
    return graph_to_code(graph, pipeline_name="declarations", preamble=preamble)


def test_a_module_of_declarations_does_not_import_polars() -> None:
    code = _declarations_only()

    assert _imports(ast.parse(code)) == ["import haute"]
    assert 'selected_columns=["pl", "pl.col"]' in code


def test_a_preamble_that_uses_pl_keeps_the_polars_import() -> None:
    code = _declarations_only(preamble='THRESHOLD = pl.lit(0.5).alias("threshold")')

    assert _imports(ast.parse(code)) == ["import haute", "import polars as pl"]
    assert code.index("import polars as pl") < code.index("THRESHOLD = pl.lit(0.5)")


# ---------------------------------------------------------------------------
# Strings
# ---------------------------------------------------------------------------


def _generated_strings(text: str) -> Iterator[tuple[str, str, str]]:
    """``(statement kind, value, literal)`` for each string outside a function body."""
    for stmt in ast.parse(text).body[1:]:
        if isinstance(stmt, ast.FunctionDef):
            roots: list[ast.AST] = list(stmt.decorator_list)
            kind = "decorator"
        elif isinstance(stmt, (ast.Assign, ast.Expr)) and isinstance(stmt.value, ast.Call):
            roots = [stmt.value]
            kind = _dotted(stmt.value.func)
        else:
            continue
        for root in roots:
            for node in ast.walk(root):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    literal = ast.get_source_segment(text, node)
                    assert literal is not None
                    yield kind, node.value, literal


def test_a_string_containing_a_double_quote_takes_single_quotes(corpus: Corpus) -> None:
    """Double quotes, unless single ones need fewer escapes, as ruff writes strings."""
    single_quoted: set[str] = set()
    for text in corpus.files.values():
        for kind, value, literal in _generated_strings(text):
            quote = "'" if value.count('"') > value.count("'") else '"'
            assert literal[0] == literal[-1] == quote, literal
            if quote == "'":
                single_quoted.add(kind)

    # A description, a decorator value and a connect port each held one.
    assert single_quoted == {"haute.Pipeline", "decorator", "pipeline.connect"}
    assert '"driver\'s age"' in corpus.files["main.py"]


# ---------------------------------------------------------------------------
# Emission order
# ---------------------------------------------------------------------------


def _function_order(text: str) -> list[str]:
    return [function.name for function in _functions(text)]


def test_each_input_less_source_is_emitted_directly_before_its_first_consumer() -> None:
    def source(node_id: str, label: str) -> GraphNode:
        return _node(node_id, label, NodeType.DATA_INPUT, _data_input(f"data/{node_id}.parquet"))

    def step(node_id: str, label: str, code: str) -> GraphNode:
        return _node(node_id, label, NodeType.POLARS, {"code": code})

    graph = PipelineGraph(
        nodes=[
            source("a", "Source A"),
            source("b", "Source B"),
            source("u", "Unused Source"),
            _node("c", "Source C", NodeType.CONSTANT, _CONSTANT),
            source("d1", "Source D One"),
            source("d2", "Source D Two"),
            step("t1", "Step One", "df = Source_B"),
            step("t2", "Step Two", "df = Step_One.join(Source_A, on='id')"),
            step("t3", "Step Three", "df = Step_Two.join(Source_C, how='cross')"),
            step("t4", "Step Four", "df = pl.concat([Source_D_Two, Step_Three, Source_D_One])"),
        ],
        edges=[
            _edge("b", "t1"),
            _edge("t1", "t2"),
            _edge("a", "t2"),
            _edge("c", "t3"),
            _edge("t2", "t3"),
            _edge("d2", "t4"),
            _edge("t3", "t4"),
            _edge("d1", "t4"),
            _edge("c", "t4"),
        ],
    )

    code = graph_to_code(graph, pipeline_name="order")

    assert _function_order(code) == [
        # Consumed by nothing: keeps its topological place.
        "Unused_Source",
        "Source_B",
        "Step_One",
        "Source_A",
        "Step_Two",
        # Consumed twice: before its first consumer only.
        "Source_C",
        "Step_Three",
        # One consumer's sources in that consumer's edge order.
        "Source_D_Two",
        "Source_D_One",
        "Step_Four",
    ]
    # The connections keep the graph's edge order.
    wiring = code[code.index("# Wire nodes together") :].splitlines()[1:]
    assert wiring[:3] == [
        'pipeline.connect("Source_B", "Step_One")',
        'pipeline.connect("Step_One", "Step_Two")',
        'pipeline.connect("Source_A", "Step_Two")',
    ]


def test_corpus_modules_follow_the_emission_order(corpus: Corpus) -> None:
    """Sources move down to their first consumer; everything else keeps topological order.

    Instances come last, in graph order.
    """
    for path, module in corpus.modules.items():
        names = {node.id: _sanitize_func_name(node.data.label) for node in module.nodes}
        instances = [node.id for node in module.nodes if node.data.config.get("instanceOf")]
        topological = [
            node_id
            for node_id in topo_sort_ids([node.id for node in module.nodes], module.edges)
            if node_id not in instances
        ]
        parents: dict[str, list[str]] = {}
        for edge in module.edges:
            if edge.source not in instances and edge.target not in instances:
                parents.setdefault(edge.target, []).append(edge.source)
        consumed = {source for sources in parents.values() for source in sources}
        moved = {
            node_id for node_id in topological if node_id not in parents and node_id in consumed
        }
        emitted = [
            next(n for n, name in names.items() if name == function)
            for function in _function_order(corpus.files[path])
        ]
        originals, tail = emitted[: len(topological)], emitted[len(topological) :]

        assert tail == instances, path
        assert [n for n in originals if n not in moved] == [
            n for n in topological if n not in moved
        ], path
        for position, node_id in enumerate(originals):
            if node_id in moved:
                continue
            run_start = position
            while run_start > 0 and originals[run_start - 1] in moved:
                run_start -= 1
            first_consumed_here = [
                parent
                for parent in dict.fromkeys(parents.get(node_id, []))
                if parent in moved and parent not in originals[:run_start]
            ]
            assert originals[run_start:position] == first_consumed_here, (path, names[node_id])

    main = _function_order(corpus.files["main.py"])
    # Consumed by nothing, so it keeps its topological place at the top.
    assert main[0] == "Archived_Quotes"
    # Read where it is first used, not at the top of the file.
    assert main.index("Territory_Lookup") + 1 == main.index("Area_Rating_Adjusted")


# ---------------------------------------------------------------------------
# The transform output declaration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "declared"),
    [
        pytest.param("df = quotes.head(5)", False, id="assignment"),
        pytest.param("df: pl.LazyFrame = quotes.head(5)", False, id="annotated-assignment"),
        pytest.param("for df in [quotes]:\n    pass", False, id="loop-target"),
        pytest.param("if (df := quotes.head(5)) is None:\n    pass", False, id="named-expression"),
        pytest.param(
            "if quotes is None:\n    df = quotes\nelse:\n    df = quotes.head(1)",
            False,
            id="branch",
        ),
        pytest.param("global df\nprint(quotes)", False, id="global-declaration"),
        pytest.param("print(quotes)", True, id="no-binding"),
        pytest.param("result = quotes.head(5)", True, id="other-name"),
        pytest.param("print(df)", True, id="read-only"),
        pytest.param(
            "def helper():\n    df = quotes\n    return df\n\n\nhelper()",
            True,
            id="nested-function-binding",
        ),
        pytest.param("class Holder:\n    df = quotes", True, id="class-binding"),
    ],
)
def test_transform_output_declaration_appears_only_when_the_code_does_not_make_df_local(
    code: str, declared: bool
) -> None:
    graph = PipelineGraph(
        nodes=[
            _node("src", "quotes", NodeType.DATA_INPUT, _data_input("data/quotes.parquet")),
            _node("tx", "Transform", NodeType.POLARS, {"code": code}),
        ],
        edges=[_edge("src", "tx")],
    )

    generated = graph_to_code(graph, pipeline_name="declaration")
    body = generated[generated.index("def Transform(") :].split("\n", 1)[1]

    expected_code = "\n".join(f"    {line}" if line else "" for line in code.splitlines())
    declaration = "    df: pl.LazyFrame\n" if declared else ""
    assert body.startswith(f"{declaration}{expected_code}\n    return df\n")


# ---------------------------------------------------------------------------
# ``df`` names the frame a code-accepting node produces
# ---------------------------------------------------------------------------


_CODE_TYPE_CONFIGS = {
    NodeType.DATA_INPUT: _data_input("data/quotes.parquet"),
    NodeType.EXTERNAL_FILE: _EXTERNAL_FILE,
    NodeType.RATING_STEP: _RATING_STEP,
    NodeType.MODEL_SCORE: _MODEL_SCORE,
    NodeType.SCENARIO_EXPANDER: _SCENARIO_EXPANDER,
    NodeType.EXPLORE: {},
}


def _fed_by_df(node_type: NodeType, config: dict) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node("upstream", "df", NodeType.DATA_INPUT, _data_input("data/df.parquet")),
            _node("target", "Target Node", node_type, config),
        ],
        edges=[_edge("upstream", "target")],
    )


@pytest.mark.parametrize("with_code", [False, True], ids=["declaration", "hook"])
@pytest.mark.parametrize("node_type", sorted(CODE_NODE_TYPES), ids=str)
def test_an_input_named_df_on_a_code_accepting_node_is_a_config_error(
    node_type: NodeType, with_code: bool
) -> None:
    config = dict(_CODE_TYPE_CONFIGS[node_type])
    if with_code:
        config["code"] = "df = df.head(1)"

    # Offline: a Model Score that got past the check must not reach for a model.
    with (
        patch("haute._mlflow_io.load_mlflow_model", side_effect=OSError("offline")),
        pytest.raises(ConfigError, match="Input name 'df' is reserved") as excinfo,
    ):
        graph_to_code(_fed_by_df(node_type, config), pipeline_name="reserved")

    assert excinfo.value.context["node_id"] == "target"
    assert excinfo.value.context["node_label"] == "Target Node"


def test_an_api_frame_named_df_on_a_hook_is_a_config_error() -> None:
    api = {
        "path": "inputs/request.json",
        "tables": [
            {
                "path": "$[:]",
                "label": "df",
                "emit": True,
                "columns": [
                    {"name": "quote_id", "path": "$[:].quote_id", "type": "str", "selected": True}
                ],
            }
        ],
    }
    graph = PipelineGraph(
        nodes=[
            _node("api", "Request", NodeType.API_INPUT, api),
            _node("rate", "Rate", NodeType.RATING_STEP, {**_RATING_STEP, "code": "df = df"}),
        ],
        edges=[_edge("api", "rate", source_handle="df")],
    )

    with pytest.raises(ConfigError, match="Input name 'df' is reserved"):
        graph_to_code(graph, pipeline_name="reserved")


@pytest.mark.parametrize(
    ("node_type", "config"),
    [
        (NodeType.BANDING, _BANDING),
        (NodeType.OUTPUT, _OUTPUT),
        (NodeType.DATA_OUTPUT, _DATA_OUTPUT),
    ],
    ids=["banding", "output", "dataOutput"],
)
def test_a_node_type_without_code_takes_an_input_named_df_as_any_other(
    node_type: NodeType, config: dict
) -> None:
    code = graph_to_code(_fed_by_df(node_type, config), pipeline_name="plain")

    assert "\ndef Target_Node(df): ...\n" in code
