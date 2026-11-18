"""A stored result is checked against what its run read (CCH-06, specs/caching "Stale results").

One table changes each kind of input a result is computed from in turn and requires every
reader that input reaches to report its answer old: a node's code, a utility module the
preamble imports and one only a node's code imports, a data file beside the utility code, a
params file, a data file, the order of a node's inputs, a constant, the preamble and Haute's
own code. The readers are the cache consumers that sign a result (each enumerated, so one the
table does not drive fails it), the assistant's brief and the Workbench's form revision. The
browser's readers are held by ``frontend/src/stores/__tests__/projectInputs.test.ts``.
"""

from __future__ import annotations

import ast
import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from haute._cache import CacheConsumer, graph_fingerprint
from haute._node_snapshots import BOUNDED_SEMANTICS_CLASS, node_snapshot_signature
from haute._types import GlobalConstant, GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import preview_lineage_cache_key
from tests._project import use_project
from tests._source_files import source_files

_REPO_SRC = Path(__file__).resolve().parents[1] / "src" / "haute"


def _node(node_id: str, node_type: NodeType, config: dict[str, object]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _source(node_id: str, path: Path) -> GraphNode:
    return _node(
        node_id,
        NodeType.DATA_INPUT,
        {"input_type": "file", "format": "parquet", "mode": "scan", "path": str(path)},
    )


_TOP_IMPORT = "from utility.top import TOP\n"
_RATED_CODE = (
    "from utility.inner import INNER\n"
    "from utility import FACTOR\n"
    "df = quotes.with_columns(\n"
    "    rated=pl.col('premium') * TOP * INNER * FACTOR * global_constants.rate\n"
    ")\n"
)


def _write_utility(project: Path, *, top: int = 1, inner: int = 10, factor: int = 2) -> None:
    package = project / "utility"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "FACTOR = json.loads((Path(__file__).parent / 'rates.json').read_text())['factor']\n",
        encoding="utf-8",
    )
    (package / "top.py").write_text(f"TOP = {top}\n", encoding="utf-8")
    (package / "inner.py").write_text(f"INNER = {inner}\n", encoding="utf-8")
    (package / "rates.json").write_text(json.dumps({"factor": factor}), encoding="utf-8")


def _graph(project: Path, *, preamble: str = _TOP_IMPORT, rate: float = 1.0) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _source("quotes", project / "quotes.parquet"),
            _source("other", project / "other.parquet"),
            _node("rated", NodeType.POLARS, {"code": _RATED_CODE}),
            _node(
                "loaded",
                NodeType.EXTERNAL_FILE,
                {
                    "path": "lookup.json",
                    "file_type": "json",
                    "code": "df = df.with_columns(loaded=pl.col('premium') * obj['factor'])",
                },
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="quotes", target="rated"),
            GraphEdge(id="e2", source="rated", target="loaded"),
            GraphEdge(id="e3", source="other", target="loaded"),
        ],
        preamble=preamble,
        global_constants=[GlobalConstant(name="rate", type="float", value=rate)],
        source_file=str(project / "main.py"),
    )


@pytest.fixture()
def project(haute_scratch: Path) -> Path:
    use_project(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame({"premium": [100.0]}).write_parquet(haute_scratch / "quotes.parquet")
    pl.DataFrame({"premium": [200.0]}).write_parquet(haute_scratch / "other.parquet")
    (haute_scratch / "lookup.json").write_text(json.dumps({"factor": 1.2}), encoding="utf-8")
    _write_utility(haute_scratch)
    return haute_scratch


# ---------------------------------------------------------------------------
# The readers
# ---------------------------------------------------------------------------

Reader = Callable[[Path, PipelineGraph], object]


def _snapshot(_project: Path, graph: PipelineGraph) -> object:
    return node_snapshot_signature(
        graph,
        "loaded",
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
        enforce_contracts=True,
    )


def _preview_key(_project: Path, graph: PipelineGraph) -> object:
    return preview_lineage_cache_key(
        graph,
        target_node_id="loaded",
        source="live",
        requested_columns=None,
        initial_column_limit=None,
        row_limit=100,
        port_label=None,
        enforce_contracts=True,
        materialisation_scope="target_only",
    )


def _graph_fingerprint(_project: Path, graph: PipelineGraph) -> object:
    return graph_fingerprint(graph)


def _deploy_schema(_project: Path, graph: PipelineGraph) -> object:
    from haute.deploy._schema import deploy_schema_cache_fingerprint

    return deploy_schema_cache_fingerprint(
        graph, output_node_id="loaded", input_node_ids=["quotes"], artifact_paths=None
    )


def _brief(_project: Path, graph: PipelineGraph) -> object:
    """The brief served for *graph* under one project revision: a new one only when its key
    moved (the revision itself is held fixed, so only what the key adds to it is tested)."""
    from haute.assistant import _tools

    return _tools._cached_brief_nodes(graph, "revision")


def _form_revision(project: Path, _graph: PipelineGraph) -> object:
    from haute._workbench_config import read_workbench_config
    from haute._workbench_folder import read_folder

    return read_folder(read_workbench_config(project)).revision


READERS: dict[str, Reader] = {
    "node snapshot": _snapshot,
    "preview and trace": _preview_key,
    "graph fingerprint": _graph_fingerprint,
    "deploy schema": _deploy_schema,
    "assistant brief": _brief,
    "form revision": _form_revision,
}

#: Every cache consumer, by the reader above that drives it or why none does.
CONSUMERS: dict[CacheConsumer, str] = {
    CacheConsumer.NODE_SNAPSHOT_SIGNATURE: "node snapshot",
    CacheConsumer.PREVIEW_TRACE: "preview and trace",
    CacheConsumer.GRAPH_EXECUTION: "graph fingerprint",
    CacheConsumer.DEPLOY_SCHEMA: "deploy schema",
    # Components, each signed inside a driven identity above.
    CacheConsumer.GRAPH_STRUCTURE: "component of the graph fingerprint",
    CacheConsumer.RUNTIME_GRAPH_INPUT: "component of the snapshot and preview identities",
    # No graph or project code: a model's trained feature contract.
    CacheConsumer.MODEL_CONTRACT: "no graph input",
    # A copy of a source's bytes, proved by their own digests.
    CacheConsumer.INPUT_SNAPSHOT: "no graph input",
}


@pytest.mark.meta
def test_every_cache_consumer_is_driven_or_excluded_by_name() -> None:
    assert set(CONSUMERS) == set(CacheConsumer)
    driven = {reason for reason in CONSUMERS.values() if reason in READERS}
    assert driven == {"node snapshot", "preview and trace", "graph fingerprint", "deploy schema"}


# ---------------------------------------------------------------------------
# The changes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One kind of input changed: what it does, and the readers it reaches."""

    apply: Callable[[Path, PipelineGraph, pytest.MonkeyPatch], PipelineGraph]
    reaches: frozenset[str]


def _with_node_code(_project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch) -> PipelineGraph:
    return graph.model_copy(
        update={
            "nodes": [
                node.with_config({"code": _RATED_CODE.replace("TOP *", "TOP * 3 *")})
                if node.id == "rated"
                else node
                for node in graph.nodes
            ]
        }
    )


def _with_top_module(project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch) -> PipelineGraph:
    (project / "utility" / "top.py").write_text("TOP = 7\n", encoding="utf-8")
    return graph


def _with_inner_module(
    project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch
) -> PipelineGraph:
    (project / "utility" / "inner.py").write_text("INNER = 70\n", encoding="utf-8")
    return graph


def _with_utility_data(
    project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch
) -> PipelineGraph:
    (project / "utility" / "rates.json").write_text(json.dumps({"factor": 3}), encoding="utf-8")
    return graph


def _with_params_file(
    project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch
) -> PipelineGraph:
    from haute._workbench_params import params_files

    [name, *_] = params_files(project)
    target = project / "rating" / "files" / "params" / name
    target.write_bytes(target.read_bytes() + b"ZZ\n")
    return graph


def _with_data_file(project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch) -> PipelineGraph:
    pl.DataFrame({"premium": [300.0]}).write_parquet(project / "quotes.parquet")
    return graph


def _with_inputs_swapped(
    _project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch
) -> PipelineGraph:
    into_loaded = [edge for edge in graph.edges if edge.target == "loaded"]
    others = [edge for edge in graph.edges if edge.target != "loaded"]
    return graph.model_copy(update={"edges": [*others, *reversed(into_loaded)]})


def _with_constant(_project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch) -> PipelineGraph:
    return graph.model_copy(
        update={"global_constants": [GlobalConstant(name="rate", type="float", value=2.0)]}
    )


def _with_preamble(_project: Path, graph: PipelineGraph, _mp: pytest.MonkeyPatch) -> PipelineGraph:
    return graph.model_copy(update={"preamble": _TOP_IMPORT + "EXTRA = 1\n"})


def _with_engine_module(
    _project: Path, graph: PipelineGraph, monkeypatch: pytest.MonkeyPatch
) -> PipelineGraph:
    import haute._cache as cache
    import haute._node_snapshots as node_snapshots
    import haute.deploy._schema as deploy_schema

    for module in (cache, node_snapshots, deploy_schema):
        monkeypatch.setattr(module, "haute_code_fingerprint", lambda: "another-build")
    return graph


_RESULTS = frozenset({"node snapshot", "preview and trace", "deploy schema"})
_STRUCTURE = frozenset({"graph fingerprint"})
_PROJECT_CODE = _RESULTS | _STRUCTURE | {"assistant brief"}

CHANGES: dict[str, Change] = {
    "a node's code": Change(_with_node_code, _RESULTS | _STRUCTURE),
    "a utility module the preamble imports": Change(_with_top_module, _PROJECT_CODE),
    "a utility module only a node imports": Change(_with_inner_module, _PROJECT_CODE),
    "a data file beside the utility code": Change(_with_utility_data, _PROJECT_CODE),
    "a params file": Change(_with_params_file, frozenset({"form revision"})),
    "a data file": Change(_with_data_file, _RESULTS | {"assistant brief"}),
    "the order of a node's inputs": Change(_with_inputs_swapped, _RESULTS | _STRUCTURE),
    "a constant": Change(_with_constant, _RESULTS | _STRUCTURE),
    "the preamble": Change(_with_preamble, _PROJECT_CODE),
    "an engine module": Change(_with_engine_module, _RESULTS),
}


@pytest.fixture()
def workbench(project: Path) -> Path:
    """The project with a workbench form and a params file, which the form revision reads."""
    from tests._workbench_helpers import form_table, workbench_project

    workbench_project(project, [form_table("policy", ("state", "str"))], pipeline="rating/main.py")
    from haute._workbench_params import params_files

    if not params_files(project):
        from haute._project_layout import pipeline_files_folder

        folder = project / str(pipeline_files_folder(project)) / "params"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "states.csv").write_bytes(b"state\nNSW\nVIC\n")
    return project


@pytest.mark.parametrize("change", list(CHANGES))
def test_every_reader_an_input_reaches_reports_its_answer_old(
    workbench: Path, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.assistant import _tools

    monkeypatch.setattr(_tools, "_BRIEF_CACHE", OrderedDict())
    briefs = iter(range(1_000))
    # A brief resolved again is a new value; one served from the cache is the same.
    monkeypatch.setattr(_tools, "_brief_nodes", lambda _graph, _flat: (next(briefs),))
    graph = _graph(workbench)
    before = {name: reader(workbench, graph) for name, reader in READERS.items()}

    changed = CHANGES[change].apply(workbench, graph, monkeypatch)
    after = {name: reader(workbench, changed) for name, reader in READERS.items()}

    moved = {name for name in READERS if before[name] != after[name]}
    assert moved == CHANGES[change].reaches


# ---------------------------------------------------------------------------
# Computed again: the answer a fresh process gives
# ---------------------------------------------------------------------------


def _preview(graph: PipelineGraph, node_id: str, column: str, *, row_limit: int = 10) -> float:
    from haute.executor import execute_graph

    results = execute_graph(
        graph, target_node_id=node_id, row_limit=row_limit, target_preview_only=True
    )
    assert results[node_id].status == "ok", results[node_id].error
    return float(results[node_id].preview[0][column])


def test_a_utility_edit_is_computed_again_from_the_new_code_in_the_same_process(
    project: Path,
) -> None:
    """CCH-05 and WB-69: an import inside a node's code, under a preamble that imports
    something else, and a data file the utility code reads, each followed without a restart."""
    graph = _graph(project, preamble="X = 1\n" + _TOP_IMPORT)
    assert _preview(graph, "rated", "rated") == 100 * 1 * 10 * 2

    (project / "utility" / "inner.py").write_text("INNER = 20\n", encoding="utf-8")
    assert _preview(graph, "rated", "rated") == 100 * 1 * 20 * 2

    (project / "utility" / "inner.py").write_text("INNER = 30\n", encoding="utf-8")
    assert _preview(graph, "rated", "rated") == 100 * 1 * 30 * 2

    (project / "utility" / "rates.json").write_text(json.dumps({"factor": 5}), encoding="utf-8")
    assert _preview(graph, "rated", "rated") == 100 * 1 * 30 * 5


def test_an_undone_utility_edit_runs_the_restored_code_not_the_edit(project: Path) -> None:
    """A, then B, then A again: the compile cache holds A's namespace, and the modules
    loaded for B are put aside before it is reused, so a node's own import loads A."""
    graph = _graph(project, preamble="X = 1\n" + _TOP_IMPORT)
    assert _preview(graph, "rated", "rated") == 100 * 1 * 10 * 2

    (project / "utility" / "inner.py").write_text("INNER = 20\n", encoding="utf-8")
    assert _preview(graph, "rated", "rated") == 100 * 1 * 20 * 2

    (project / "utility" / "inner.py").write_text("INNER = 10\n", encoding="utf-8")
    # Another row limit, so the preview is computed again rather than served from the
    # entry the first one stored, and A's cached namespace is reused.
    assert _preview(graph, "rated", "rated", row_limit=11) == 100 * 1 * 10 * 2


def test_a_removed_utility_package_no_longer_runs_from_a_node_import(project: Path) -> None:
    """With the package gone the identity signs no ``utility``, so the modules loaded from it
    are put aside and the import fails as it would in a fresh process."""
    import shutil

    from haute.executor import execute_graph

    graph = _graph(project, preamble="")
    graph = graph.model_copy(
        update={
            "nodes": [
                node.with_config({"code": "from utility.top import TOP\n" + _RATED_CODE})
                if node.id == "rated"
                else node
                for node in graph.nodes
            ]
        }
    )
    assert _preview(graph, "rated", "rated") == 100 * 1 * 10 * 2

    shutil.rmtree(project / "utility")
    results = execute_graph(graph, target_node_id="rated", row_limit=11, target_preview_only=True)

    assert results["rated"].status == "error"
    assert "utility" in (results["rated"].error or "")


def test_a_pipeline_without_utility_does_not_import_another_pipelines(project: Path) -> None:
    """Pipeline A's folder has a ``utility``; pipeline B's does not, nor the project root. B
    run after A in the same process must not load A's package through a finder still bound
    to A, as a fresh process would not."""
    import shutil

    from haute.executor import execute_graph

    shutil.rmtree(project / "utility")
    (project / "a").mkdir()
    (project / "b").mkdir()
    _write_utility(project / "a")
    code = "from utility.top import TOP\n" + _RATED_CODE

    def pipeline(folder: str) -> PipelineGraph:
        graph = _graph(project, preamble="")
        return graph.model_copy(
            update={
                "source_file": str(project / folder / "main.py"),
                "nodes": [
                    node.with_config({"code": code}) if node.id == "rated" else node
                    for node in graph.nodes
                ],
            }
        )

    assert _preview(pipeline("a"), "rated", "rated") == 100 * 1 * 10 * 2

    results = execute_graph(
        pipeline("b"), target_node_id="rated", row_limit=10, target_preview_only=True
    )
    assert results["rated"].status == "error"
    assert "utility" in (results["rated"].error or "")


def test_a_node_import_of_utility_runs_and_follows_edits_with_a_blank_preamble(
    project: Path,
) -> None:
    graph = _graph(project, preamble="")
    graph = graph.model_copy(
        update={
            "nodes": [
                node.with_config({"code": "from utility.top import TOP\n" + _RATED_CODE})
                if node.id == "rated"
                else node
                for node in graph.nodes
            ]
        }
    )
    assert _preview(graph, "rated", "rated") == 100 * 1 * 10 * 2

    (project / "utility" / "top.py").write_text("TOP = 4\n", encoding="utf-8")
    assert _preview(graph, "rated", "rated") == 100 * 4 * 10 * 2


def test_a_preview_follows_the_order_of_a_nodes_inputs(project: Path) -> None:
    """CCH-04: a Load File's code computes on its first input, ``df``."""
    graph = _graph(project)
    assert _preview(graph, "loaded", "loaded") == pytest.approx(100 * 1.2)

    swapped = _with_inputs_swapped(project, graph, pytest.MonkeyPatch())
    assert _preview(swapped, "loaded", "loaded") == pytest.approx(200 * 1.2)


@pytest.mark.parametrize("node_type", list(NodeType))
def test_every_node_type_s_inputs_swapped_are_another_structure(node_type: NodeType) -> None:
    """CCH-04 for every node type: the order rule is the edges', whatever the node does with
    them, so a node type added later is held too."""
    graph = PipelineGraph(
        nodes=[
            _node("a", NodeType.POLARS, {"code": "df = pl.DataFrame({'x': [1]})"}),
            _node("b", NodeType.POLARS, {"code": "df = pl.DataFrame({'x': [2]})"}),
            _node("target", node_type, {}),
        ],
        edges=[
            GraphEdge(id="e1", source="a", target="target"),
            GraphEdge(id="e2", source="b", target="target"),
        ],
    )
    swapped = graph.model_copy(update={"edges": list(reversed(graph.edges))})

    assert graph_fingerprint(graph) != graph_fingerprint(swapped)


def test_the_brief_of_the_next_turn_follows_an_edit_to_a_utility_module(project: Path) -> None:
    """AST-05: the brief names the columns the edited helper gives."""
    from haute.assistant import _tools

    _tools._BRIEF_CACHE.clear()
    package = project / "utility"
    (package / "pricing.py").write_text(
        "import polars as pl\n"
        "def enrich(df):\n"
        "    return df.with_columns(base_premium=pl.col('premium'))\n",
        encoding="utf-8",
    )
    graph = PipelineGraph(
        nodes=[
            _source("quotes", project / "quotes.parquet"),
            _node("enriched", NodeType.POLARS, {"code": "df = enrich(quotes)"}),
        ],
        edges=[GraphEdge(id="e1", source="quotes", target="enriched")],
        preamble="from utility.pricing import enrich\n",
        source_file=str(project / "main.py"),
    )
    assert "base_premium" in str(_tools._cached_brief_nodes(graph, "revision"))

    (package / "pricing.py").write_text(
        "import polars as pl\n"
        "def enrich(df):\n"
        "    return df.with_columns(technical_premium=pl.col('premium'))\n",
        encoding="utf-8",
    )
    after = str(_tools._cached_brief_nodes(graph, "revision"))
    assert "technical_premium" in after
    assert "base_premium" not in after


# ---------------------------------------------------------------------------
# One listing of the utility tree
# ---------------------------------------------------------------------------

_LISTING_CALLS = frozenset({"rglob", "glob", "iterdir", "walk", "scandir", "listdir"})


def _utility_listings() -> list[str]:
    """Each function in ``src/haute`` that lists a folder and names the utility package."""
    found: set[str] = set()

    class Listings(ast.NodeVisitor):
        """One pass per module: a listing call counts for every function enclosing it."""

        def __init__(self, module: str, lines: list[str]) -> None:
            self.module = module
            self.lines = lines
            self.enclosing: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.enclosing.append(node)
            self.generic_visit(node)
            self.enclosing.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._function(node)

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _LISTING_CALLS:
                for function in self.enclosing:
                    text = "\n".join(self.lines[function.lineno - 1 : function.end_lineno])
                    if "utility" in text.lower():
                        found.add(f"{self.module}::{function.name}")
            self.generic_visit(node)

    for path in source_files(_REPO_SRC):
        source = path.read_text(encoding="utf-8")
        if "utility" not in source.lower():
            continue
        Listings(path.relative_to(_REPO_SRC).as_posix(), source.splitlines()).visit(
            ast.parse(source)
        )
    return sorted(found)


#: Functions that list a folder and mention ``utility`` without listing its tree, each with
#: why. Any other is a second listing of the tree beside ``utility_tree_files``.
_NOT_UTILITY_LISTINGS: dict[str, str] = {
    "deploy/_container.py::write_bundle": "checks the build folder is empty before it writes",
    "routes/submodel.py::create_node_instance": "lists a submodel's modules folder for a name",
    "routes/submodel.py::_run": "the same, run in a thread",
    "routes/utility.py::_validate_new_module_name": (
        "the Utility panel's top-level modules, for a new module's name"
    ),
    "routes/utility.py::list_utility_files": "the top-level modules the Utility panel edits",
}


@pytest.mark.meta
def test_the_utility_tree_is_listed_by_one_function() -> None:
    listings = set(_utility_listings()) - set(_NOT_UTILITY_LISTINGS)
    assert listings == {"_project_imports.py::utility_tree_files"}


def test_a_fingerprint_that_is_not_project_code_has_no_utility_generation() -> None:
    from haute._cache import utility_generation

    assert utility_generation("abc.def") == "def"
    for malformed in ("abcdef", ".def", "abc."):
        with pytest.raises(ValueError, match="not a project code fingerprint"):
            utility_generation(malformed)
