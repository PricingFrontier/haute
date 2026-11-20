"""A save writes a node's code from its steps only when its steps were edited (CGEN-11).

A stepped node's code is its program and its steps a view of it. A step list that has
been through a browser has lost what JavaScript cannot hold (``1.0`` arrives as ``1``,
an integer past 2**53 as its nearest double), so a save that rendered every list would
rewrite hand-written code nobody touched. The save renders code only for the nodes it is
told were edited; every other node is written from the code it carries, and the list it
carries is not read.
"""

from __future__ import annotations

import ast
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from haute._step_authority import graph_without_unread_steps, steps_to_render
from haute._types import PipelineGraph
from haute.routes._helpers import parse_pipeline_to_graph
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import SavePipelineRequest
from tests._project import use_project
from tests._source_files import source_files
from tests.conftest import current_source_revision

REPO = Path(__file__).resolve().parents[1]
SHIPPED_EXAMPLES = REPO / "src" / "haute" / "assistant" / "assets" / "examples"

#: Hand-written statements a step list carries through a browser with a loss: decimals
#: spelled as whole numbers, an integer past 2**53, and an input name the code rebinds
#: (``CGEN-10``: the lift accepts it, a render against the input names refuses it).
HAND_WRITTEN = '''\
"""Hand-written transforms."""

import polars as pl

import haute

pipeline = haute.Pipeline("main")


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes(): ...


@pipeline.polars
def relativity(quotes: pl.LazyFrame) -> pl.LazyFrame:
    df = quotes
    df = df.with_columns(pl.lit(1.0).alias("relativity"))
    return df


@pipeline.polars
def doubled(relativity: pl.LazyFrame) -> pl.LazyFrame:
    df = relativity
    df = df.with_columns((pl.col("value") * 2.0).alias("value"))
    df = df.with_columns(pl.col("value").fill_null(0.0))
    return df


@pipeline.polars
def one_id(doubled: pl.LazyFrame) -> pl.LazyFrame:
    df = doubled
    df = df.filter(pl.col("id") == 9007199254740993)
    return df


@pipeline.polars
def years(one_id: pl.LazyFrame) -> pl.LazyFrame:
    df = one_id
    one_id = 365
    df = df.with_columns((pl.col("days") / one_id).alias("years"))
    return df


pipeline.connect("quotes", "relativity")
pipeline.connect("relativity", "doubled")
pipeline.connect("doubled", "one_id")
pipeline.connect("one_id", "years")
'''


def _as_javascript_holds_it(value: Any) -> Any:
    """*value* after ``JSON.stringify`` and ``JSON.parse`` in a browser: every number a
    double, written as an integer whenever it is a whole number."""

    def number(token: str) -> int | float:
        held = float(token)
        return int(held) if held.is_integer() and abs(held) < 1e21 else held

    return json.loads(json.dumps(value), parse_int=number, parse_float=number)


def _files(root: Path) -> dict[str, bytes]:
    """The project's files, without git's and Haute's own state."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in source_files(root, suffix=None)
        if not {".git", ".haute"} & set(path.relative_to(root).parts)
    }


def _project(tmp_path: Path, source: Path | None = None) -> Path:
    from haute._git import set_working_branch
    from tests._git_helpers import init_repo

    root = tmp_path / "project"
    if source is None:
        root.mkdir()
        (root / "main.py").write_text(HAND_WRITTEN, encoding="utf-8", newline="\n")
        quotes = root / "config" / "data_input" / "quotes.json"
        quotes.parent.mkdir(parents=True)
        quotes.write_text(
            json.dumps({"input_type": "file", "path": "data/quotes.parquet", "format": "parquet"}),
            encoding="utf-8",
        )
    else:
        shutil.copytree(source, root, ignore=shutil.ignore_patterns("__pycache__"))
    if not (root / "haute.toml").exists():
        (root / "haute.toml").write_text("", encoding="utf-8", newline="\n")
    init_repo(root)
    set_working_branch("pricing/test-user/steps", root, create=True, cwd=root)
    use_project(root)
    # Into the layout a save writes, by a save on the server that no browser has held: what
    # a save of the same document through a browser must then leave as it is.
    py_path = _pipeline_file(root)
    graph = parse_pipeline_to_graph(py_path, project_root=root)
    SavePipelineService(root, pipeline_root=py_path.parent).save(
        SavePipelineRequest(
            name=graph.pipeline_name or "main",
            description=graph.pipeline_description or "",
            graph=graph,
            preamble=graph.preamble,
            preserved_blocks=graph.preserved_blocks,
            source_file=py_path.relative_to(root).as_posix(),
            base_revision=current_source_revision(py_path, root),
            edited_steps=[
                {"submodel": scope, "node_id": node_id} for scope, node_id in steps_to_render(graph)
            ],
        )
    )
    if source is None:
        written = py_path.read_text(encoding="utf-8")
        for spelled in ("pl.lit(1.0)", "* 2.0", "fill_null(0.0)", "== 9007199254740993"):
            assert spelled in written
        assert "    one_id = 365\n" in written
    return root


def _pipeline_file(root: Path) -> Path:
    return root / "pipeline.py" if (root / "pipeline.py").is_file() else root / "main.py"


def _browser_save(
    root: Path,
    py_path: Path,
    *,
    edit: Any = None,
    edited: list[dict[str, Any]] | None = None,
) -> Any:
    """Save the document of *py_path* as the editor sends it: the graph the load gives,
    after a browser has held it, with *edit* applied to that payload first."""
    graph = parse_pipeline_to_graph(py_path, project_root=root)
    payload = _as_javascript_holds_it(graph.model_dump(mode="json", by_alias=True))
    if edit is not None:
        edit(payload)
    rel = py_path.relative_to(root).as_posix()
    request = SavePipelineRequest.model_validate(
        {
            "name": graph.pipeline_name or "main",
            "description": graph.pipeline_description or "",
            "graph": payload,
            "preamble": graph.preamble,
            "preserved_blocks": graph.preserved_blocks,
            "source_file": rel,
            "base_revision": current_source_revision(py_path, root),
            "edited_steps": edited or [],
        }
    )
    return SavePipelineService(root, pipeline_root=py_path.parent).save(request)


def _stepped_nodes(payload: dict[str, Any]) -> Iterator[tuple[str | None, dict[str, Any]]]:
    yield from ((None, node) for node in payload["nodes"] if "steps" in node["data"]["config"])
    for definition_id, definition in (payload.get("submodels") or {}).items():
        for node in definition["graph"]["nodes"]:
            if "steps" in node["data"]["config"]:
                yield definition_id, node


def _examples() -> list[Any]:
    sources = [REPO / "examples" / "reference"]
    sources.extend(sorted(path for path in SHIPPED_EXAMPLES.iterdir() if path.is_dir()))
    return [pytest.param(source, id=source.name) for source in sources]


def _code(root: Path) -> dict[str, bytes]:
    return {name: content for name, content in _files(root).items() if name.endswith(".py")}


@pytest.mark.parametrize("source", [None, *_examples()])
def test_a_save_of_the_untouched_document_leaves_its_code_as_it_was(
    tmp_path: Path, source: Path | None
) -> None:
    root = _project(tmp_path, source)
    py_path = _pipeline_file(root)
    before = _code(root)

    _browser_save(root, py_path)

    assert _code(root) == before


def test_the_hand_written_file_holds_steps_the_browser_changes(tmp_path: Path) -> None:
    """The fixture above is the case: its steps do not survive a browser unchanged."""
    root = _project(tmp_path)
    graph = parse_pipeline_to_graph(root / "main.py", project_root=root)
    payload = graph.model_dump(mode="json", by_alias=True)
    # As text: Python holds 1 and 1.0 equal, and a renderer writes them differently.
    lists = {n["id"]: json.dumps(n["data"]["config"]["steps"]) for _s, n in _stepped_nodes(payload)}
    held = _as_javascript_holds_it(payload)
    changed = {
        node["id"]
        for _s, node in _stepped_nodes(held)
        if json.dumps(node["data"]["config"]["steps"]) != lists[node["id"]]
    }
    assert {"relativity", "doubled", "one_id"} <= changed
    assert "years" in lists


def _mutations() -> list[Any]:
    def drop_field(steps: list[dict[str, Any]]) -> None:
        last = steps[-1]
        last.pop(next(key for key in last if key not in {"id", "kind"}))

    def change_value(steps: list[dict[str, Any]]) -> None:
        steps[-1]["kind"] = "free_code"
        steps[-1]["code"] = "df = df.head(0)"

    def drop_step(steps: list[dict[str, Any]]) -> None:
        steps.pop()

    return [
        pytest.param(drop_field, id="field-removed"),
        pytest.param(change_value, id="step-changed"),
        pytest.param(drop_step, id="step-removed"),
    ]


@pytest.mark.parametrize("mutate", _mutations())
def test_steps_changed_in_transit_do_not_reach_an_unedited_nodes_code(
    tmp_path: Path, mutate: Any
) -> None:
    root = _project(tmp_path)
    before = _files(root)

    def edit(payload: dict[str, Any]) -> None:
        for _scope, node in _stepped_nodes(payload):
            if node["data"]["nodeType"] == "polars":
                mutate(node["data"]["config"]["steps"])

    _browser_save(root, root / "main.py", edit=edit)

    assert _files(root) == before


def test_an_edited_node_is_written_from_its_steps_and_no_other_node_is(tmp_path: Path) -> None:
    root = _project(tmp_path)
    original = (root / "main.py").read_text(encoding="utf-8")

    def edit(payload: dict[str, Any]) -> None:
        node = next(n for n in payload["nodes"] if n["id"] == "relativity")
        steps = node["data"]["config"]["steps"]
        steps.append({"id": "added", "kind": "free_code", "code": "df = df.head(5)"})

    response = _browser_save(
        root, root / "main.py", edit=edit, edited=[{"submodel": None, "node_id": "relativity"}]
    )

    saved = (root / "main.py").read_text(encoding="utf-8")
    assert "df = df.head(5)" in saved
    # The edited node is rendered, and with it its whole-number decimal: that is CGEN-09's.
    assert 'pl.lit(1).alias("relativity")' in saved
    for untouched in ('(pl.col("value") * 2.0)', "fill_null(0.0)", "== 9007199254740993"):
        assert untouched in saved
    assert "    one_id = 365\n" in saved
    assert original != saved
    reloaded = parse_pipeline_to_graph(root / "main.py", project_root=root)
    relativity = next(n for n in reloaded.nodes if n.id == "relativity")
    assert [(c.submodel, c.node_id, c.code) for c in response.step_code] == [
        (None, "relativity", relativity.data.config["code"])
    ]


def _make_unfinished(root: Path) -> Path:
    """*root*'s hand-written project with ``relativity`` saved holding an unfinished step:
    one that joins an input it is not connected to."""

    def edit(payload: dict[str, Any]) -> None:
        node = next(n for n in payload["nodes"] if n["id"] == "relativity")
        node["data"]["config"]["steps"].append(
            {
                "id": "j",
                "kind": "join",
                "input": "rates",
                "how": "left",
                "maintain_order": "left_right",
                "left_on": ["region"],
                "right_on": ["region"],
                "suffix": "_rate",
            }
        )

    response = _browser_save(
        root, root / "main.py", edit=edit, edited=[{"submodel": None, "node_id": "relativity"}]
    )
    assert any("incomplete step list" in warning for warning in response.warnings)
    assert response.step_code[0].code != ""
    sidecar = json.loads((root / "main.haute.json").read_text(encoding="utf-8"))
    assert "relativity" in sidecar["unfinished_steps"]
    return root


def test_an_unedited_node_saved_with_unfinished_steps_keeps_its_placeholder(
    tmp_path: Path,
) -> None:
    root = _make_unfinished(_project(tmp_path))
    assert "This node's steps are incomplete" in (root / "main.py").read_text(encoding="utf-8")
    before = _files(root)

    _browser_save(root, root / "main.py")

    assert _files(root) == before


def test_an_earlier_graph_is_not_given_the_files_record_of_unfinished_steps(
    tmp_path: Path,
) -> None:
    """A graph that does not descend from the file (an undo's earlier commit) carries its own
    code; the record the file holds now is not its."""
    root = _project(tmp_path)
    earlier = parse_pipeline_to_graph(root / "main.py", project_root=root)
    before = (root / "main.py").read_text(encoding="utf-8")
    _make_unfinished(root)
    request = SavePipelineRequest(
        name="main",
        graph=earlier,
        source_file="main.py",
        base_revision=current_source_revision(root / "main.py", root),
        edited_steps=[],
    )

    SavePipelineService(root).save(request)

    assert (root / "main.py").read_text(encoding="utf-8") == before


def test_a_name_that_holds_no_step_list_is_refused() -> None:
    graph = {"nodes": [{"id": "t", "data": {"nodeType": "polars", "config": {"code": "x"}}}]}
    with pytest.raises(ValueError, match="holds no step list"):
        graph_without_unread_steps(graph, [(None, "t")])
    with pytest.raises(ValueError, match="does not hold: 'missing'"):
        graph_without_unread_steps(graph, [(None, "missing")])


@pytest.mark.parametrize(
    "graph",
    [
        pytest.param("not a graph", id="graph"),
        pytest.param({"nodes": "not a list"}, id="nodes"),
        pytest.param({"nodes": ["not a node"]}, id="node"),
        pytest.param({"nodes": [{"id": "t", "data": "not data"}]}, id="data"),
        pytest.param({"nodes": [], "submodels": {"s": {"graph": "not a graph"}}}, id="definition"),
    ],
)
def test_a_malformed_graph_is_refused_by_the_requests_own_validation(graph: Any) -> None:
    """The steps are taken off before the request's shape is checked: what is not a graph
    is left for that check to refuse, never an error of the step rule's own."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SavePipelineRequest.model_validate({"graph": graph, "base_revision": None})
    with pytest.raises(ValidationError):
        SavePipelineRequest.model_validate(["not a request"])


@pytest.mark.parametrize(
    "name", [pytest.param("t", id="not-an-object"), pytest.param({"submodel": 3}, id="scope")]
)
def test_a_malformed_name_is_refused_by_the_requests_own_validation(name: Any) -> None:
    from pydantic import ValidationError

    payload = {"graph": {"nodes": []}, "base_revision": None, "edited_steps": [name]}
    with pytest.raises(ValidationError, match="edited_steps"):
        SavePipelineRequest.model_validate(payload)


def test_a_record_that_does_not_render_is_read_for_a_node_that_carries_no_code() -> None:
    """A recorded list that does not render at all loads with no code, so only a node that
    carries none is written from it."""
    from haute._step_authority import with_recorded_unfinished_steps

    record = [{"id": "filter-1", "kind": "filter", "match": "all", "conditions": []}]

    assert with_recorded_unfinished_steps("polars", {"code": ""}, record, edited=False) == {
        "steps": record
    }
    assert (
        with_recorded_unfinished_steps("polars", {"code": "df = x"}, record, edited=False) is None
    )


def test_a_server_side_writer_names_the_steps_it_changed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = parse_pipeline_to_graph(root / "main.py", project_root=root)
    after = PipelineGraph.model_validate(before.model_dump(mode="json", by_alias=True))
    node = next(n for n in after.nodes if n.id == "doubled")
    steps = [*node.data.config["steps"], {"id": "x", "kind": "free_code", "code": "df = df"}]
    after.nodes[after.nodes.index(node)] = node.with_config({**node.data.config, "steps": steps})

    assert steps_to_render(after, before) == [(None, "doubled")]
    assert steps_to_render(before) == []


def test_a_scoped_save_keeps_an_unedited_nodes_code(tmp_path: Path) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair_actions import apply_scoped_node_save
    from haute.schemas import PipelineNodeSaveRequest

    root = tmp_path
    (root / "haute.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (root / "main.py").write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a(): ...\n'
        '@pipeline.data_input(config="b.json")\n'
        "def source_b(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        '    df = df.with_columns(pl.lit(1.0).alias("x"))\n'
        "    return df\n",
        encoding="utf-8",
        newline="\n",
    )
    ready = {"input_type": "file", "format": "parquet", "mode": "scan", "arguments": {}}
    # A key the node type does not declare leaves ``source_a`` unavailable, the document
    # degraded and ``source_b`` saved on its own.
    broken = {**ready, "path": "", "cacheMode": "snapshot"}
    (root / "a.json").write_text(json.dumps(broken), encoding="utf-8")
    (root / "b.json").write_text(json.dumps({**ready, "path": "data/b.parquet"}), encoding="utf-8")
    use_project(root)
    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == "source_b")
    assert target.scoped_editable
    assert isinstance(target.config.get("steps"), list)
    config = _as_javascript_holds_it({**target.config, "path": "data/other.parquet"})
    request = PipelineNodeSaveRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        config=config,
    )

    apply_scoped_node_save(project_root=root, request=request)

    assert 'pl.lit(1.0).alias("x")' in (root / "main.py").read_text(encoding="utf-8")
    assert json.loads((root / "b.json").read_text(encoding="utf-8"))["path"] == "data/other.parquet"


def test_a_scoped_save_never_reads_a_record_a_hand_edit_left_behind(tmp_path: Path) -> None:
    """Working code written by hand over the placeholder leaves the record of its unfinished
    steps in ``.haute.json``; the load ignores it, and so does a scoped save of the node with
    nothing edited, even when the record renders as the code does without the node's inputs."""
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair_actions import apply_scoped_node_save
    from haute._polars_steps_lift import lift_steps
    from haute._types import NodeType
    from haute.schemas import PipelineNodeSaveRequest

    root = tmp_path
    (root / "haute.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    code = "\n".join(
        [
            "df = source_a",
            "source_a = 365",
            'df = df.with_columns((pl.col("days") / source_a).alias("years"))',
        ]
    )
    body = "".join(f"    {line}\n" for line in code.split("\n"))
    (root / "main.py").write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a(): ...\n'
        "@pipeline.polars\n"
        f"def transform(source_a: pl.LazyFrame) -> pl.LazyFrame:\n{body}    return df\n",
        encoding="utf-8",
        newline="\n",
    )
    broken = {
        "input_type": "file",
        "format": "parquet",
        "mode": "scan",
        "path": "",
        "arguments": {},
    }
    (root / "a.json").write_text(json.dumps({**broken, "cacheMode": "snapshot"}), encoding="utf-8")
    record = lift_steps(NodeType.POLARS, code)
    (root / "main.haute.json").write_text(
        json.dumps({"unfinished_steps": {"transform": record}}), encoding="utf-8"
    )
    use_project(root)
    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == "transform")
    assert target.scoped_editable
    request = PipelineNodeSaveRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        config=_as_javascript_holds_it(target.config),
    )

    apply_scoped_node_save(project_root=root, request=request)

    saved = (root / "main.py").read_text(encoding="utf-8")
    assert "    source_a = 365\n" in saved
    assert "NotImplementedError" not in saved


def test_a_scoped_save_keeps_unfinished_steps_behind_an_inline_placeholder(
    tmp_path: Path,
) -> None:
    """A placeholder on its function's own line is the placeholder all the same: an unedited
    scoped save writes the node from its record, whose join names an input it is not
    connected to (it renders, so the loaded node carries code), and keeps it unfinished."""
    from haute._code_extraction import INCOMPLETE_STEPS_MESSAGE
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair_actions import apply_scoped_node_save
    from haute.schemas import PipelineNodeSaveRequest

    root = tmp_path
    (root / "haute.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (root / "main.py").write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a(): ...\n'
        "@pipeline.polars\n"
        "def transform(source_a: pl.LazyFrame) -> pl.LazyFrame: "
        f'raise NotImplementedError("{INCOMPLETE_STEPS_MESSAGE}")\n',
        encoding="utf-8",
        newline="\n",
    )
    broken = {
        "input_type": "file",
        "format": "parquet",
        "mode": "scan",
        "path": "",
        "arguments": {},
    }
    (root / "a.json").write_text(json.dumps({**broken, "cacheMode": "snapshot"}), encoding="utf-8")
    record = [
        {"id": "source-1", "kind": "source", "input": "source_a"},
        {
            "id": "join-2",
            "kind": "join",
            "input": "rates",
            "how": "left",
            "maintain_order": "left_right",
            "left_on": ["region"],
            "right_on": ["region"],
            "suffix": "_rate",
        },
    ]
    sidecar = root / "main.haute.json"
    sidecar.write_text(json.dumps({"unfinished_steps": {"transform": record}}), encoding="utf-8")
    use_project(root)
    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == "transform")
    assert target.scoped_editable
    assert target.config is not None and target.config.get("code")
    request = PipelineNodeSaveRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        config=_as_javascript_holds_it(target.config),
    )

    apply_scoped_node_save(project_root=root, request=request)

    assert INCOMPLETE_STEPS_MESSAGE in (root / "main.py").read_text(encoding="utf-8")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["unfinished_steps"] == {
        "transform": record
    }


# ---------------------------------------------------------------------------
# The guard: every writer of a pipeline's code is one that names its edited steps
# ---------------------------------------------------------------------------

#: The functions outside ``haute/codegen.py`` that generate a node's or a pipeline's code,
#: each of which takes its configs from a request or a graph that names the nodes whose
#: steps were edited (``SavePipelineRequest.edited_steps``,
#: ``PipelineNodeSaveRequest.steps_edited``) or builds them on the server. A new writer
#: must be listed here, after it reads only the steps its writer names.
CODE_WRITERS = {
    "assistant/_application.py": {"_prove_steps_survive_save"},
    "routes/_save_pipeline.py": {"_generate_code"},
    "_pipeline_repair_actions.py": {"_reset_node"},
}

_GENERATORS = {"graph_to_code", "graph_to_code_multi", "_node_to_code"}


def _writers() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    package = REPO / "src" / "haute"
    for path in source_files(package):
        rel = path.relative_to(package).as_posix()
        if rel in {"codegen.py", "_codegen_builders.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(function):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if name in _GENERATORS:
                    found.setdefault(rel, set()).add(function.name)
    return found


@pytest.mark.meta
def test_every_code_writer_names_the_steps_it_renders() -> None:
    assert _writers() == CODE_WRITERS
    assert "edited_steps" in SavePipelineRequest.model_fields
    from haute.schemas import PipelineNodeSaveRequest

    assert "steps_edited" in PipelineNodeSaveRequest.model_fields
