"""A relative path means one file in the editor, a run and the deployed bundle (specs/deploy).

The editor, a run and an explicit snapshot build read a relative path in a node's
settings by the one anchoring rule, the project root's file first and then the one in
the pipeline folder (``_path_resolution.resolve_node_input_path``). Deploy's readers used
to put the pipeline folder first, so with ``data/rates.parquet`` under both the project
root and ``rating/`` the preview priced from one file and the bundle shipped the other,
with nothing said. Every deploy reader now uses the one rule, and a path whose two
copies differ refuses the deploy, naming both files.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._project_context import current_project
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code_multi
from haute.deploy._bundler import collect_artifacts
from haute.deploy._config import DeployConfig, ResolvedDeploy, resolve_config
from haute.deploy._schema import _resolve_pipeline_path
from haute.deploy._scorer import _RUNTIME_PATH_NODE_TYPES, _resolve_runtime_graph_paths, score_graph
from haute.errors import DeployError
from haute.execution import _RUNTIME_INPUTS, canonical_dataframe_execution_graph
from haute.routes._save_pipeline import SavePipelineService
from tests._project import use_project
from tests.conftest import make_file_input_config, make_output_config


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    """A project whose pipeline lives in ``rating/``, as ``haute init`` lays it out.

    The project is ``tmp_path`` itself and the names are short: a snapshot's path under
    the xdist temporary folder must stay within Windows' path limit.
    """
    (tmp_path / "rating").mkdir()
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "p"\npipeline = "rating/main.py"\n', encoding="utf-8"
    )
    pl.DataFrame({"quote_id": [1, 2], "region": ["N", "S"]}).write_parquet(
        tmp_path / "rating" / "quotes.parquet"
    )
    original = current_project().root
    use_project(tmp_path)
    try:
        yield tmp_path
    finally:
        use_project(original)


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _edge(source: str, target: str, port: str | None = None) -> GraphEdge:
    return GraphEdge(id=f"{source}_{target}", source=source, target=target, sourceHandle=port)


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _model(path: Path) -> None:
    """A CatBoost model of one feature at *path*, as small as one can be."""
    from catboost import CatBoostRegressor

    path.parent.mkdir(parents=True, exist_ok=True)
    features = pl.DataFrame({"quote_id": [float(value) for value in range(8)]}).to_pandas()
    model = CatBoostRegressor(iterations=2, depth=1, verbose=0, allow_writing_files=False)
    model.fit(features, features["quote_id"])
    model.save_model(str(path))


def _rates_file(path: Path, factors: dict[str, float]) -> Path:
    """A rates table at *path*, written as CSV or Parquet by its suffix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame({"region": list(factors), "factor": list(factors.values())})
    frame.write_csv(path) if path.suffix == ".csv" else frame.write_parquet(path)
    return path


# ---------------------------------------------------------------------------
# Every path-bearing node setting
# ---------------------------------------------------------------------------

#: One case per path-bearing node setting: the node's config naming *raw* in that field,
#: the field's file written as the node's kind reads it. A model file and a feature
#: contract are copied, not loaded, before scoring, so their bytes need only differ.
_Case = tuple[Callable[[str], dict[str, Any]], str, Callable[[Path, int], None]]


def _bytes(path: Path, copy: int) -> None:
    _write(path, f"copy {copy}".encode())


def _rates(path: Path, copy: int) -> None:
    _rates_file(path, {"N": 1.0 + copy, "S": 2.0 + copy})


_CASES: dict[tuple[NodeType, str], _Case] = {
    (NodeType.API_INPUT, "path"): (
        lambda raw: {"path": raw},
        "data/q.parquet",
        lambda path, copy: pl.DataFrame({"quote_id": [copy]}).write_parquet(_write(path, b"")),
    ),
    (NodeType.DATA_INPUT, "path"): (make_file_input_config, "data/rates.parquet", _rates),
    (NodeType.EXTERNAL_FILE, "path"): (
        lambda raw: {"path": raw, "file_type": "json", "code": "df = df"},
        "data/lookup.json",
        _bytes,
    ),
    (NodeType.MODEL_SCORE, "model_path"): (
        lambda raw: {"source_type": "file", "model_path": raw},
        "models/m.cbm",
        _bytes,
    ),
    (NodeType.MODEL_SCORE, "feature_contract_path"): (
        lambda raw: {
            "source_type": "file",
            "model_path": "models/only.cbm",
            "feature_contract_path": raw,
        },
        "models/contract.json",
        _bytes,
    ),
    (NodeType.OPTIMISER_APPLY, "artifact_path"): (
        lambda raw: {"source_type": "file", "artifact_path": raw},
        "files/result.json",
        _bytes,
    ),
}


#: A config under which each node type reads its local files: a file Data Input, a model
#: or an optimiser result from a file.
_FILE_BACKED = {"input_type": "file", "source_type": "file"}


def _path_settings() -> set[tuple[NodeType, str]]:
    """Each node type's config fields naming a local file it reads, from the engine's table."""
    return {
        (node_type, field)
        for node_type, inputs in _RUNTIME_INPUTS.items()
        if inputs is not None
        for field in inputs.local_paths(_FILE_BACKED)
    }


def test_every_path_setting_has_a_case() -> None:
    """A node setting that names a file is anchored by the test below, or this fails."""
    assert set(_CASES) == _path_settings()
    for (node_type, field), (config, raw, _write_copy) in _CASES.items():
        local_paths = _RUNTIME_INPUTS[node_type]
        assert local_paths is not None
        assert field in local_paths.local_paths(config(raw)), (node_type, field)


def _setting_graph(project: Path, node_type: NodeType, config: dict[str, Any]) -> PipelineGraph:
    """The node alone, in the project's pipeline file."""
    return PipelineGraph(
        nodes=[_node("n", node_type, config)],
        edges=[],
        source_file=str(project / "rating" / "main.py"),
    )


@pytest.mark.parametrize("setting", sorted(_CASES, key=str), ids=lambda s: f"{s[0].value}.{s[1]}")
def test_every_deploy_reader_names_the_file_the_engine_reads(
    project: Path, setting: tuple[NodeType, str]
) -> None:
    """With a different copy under each folder, every reader names the project root's."""
    node_type, field = setting
    config, raw, write_copy = _CASES[setting]
    write_copy(project / raw, 1)
    write_copy(project / "rating" / raw, 2)
    _write(project / "models" / "only.cbm", b"model")
    graph = _setting_graph(project, node_type, config(raw))

    [engine_node] = canonical_dataframe_execution_graph(graph).nodes
    engine = Path(engine_node.data.config[field]).resolve()
    assert engine == (project / raw).resolve()

    read: dict[str, Path] = {"schema": Path(_resolve_pipeline_path(graph, raw)).resolve()}
    if node_type in _RUNTIME_PATH_NODE_TYPES and field == "path":
        [scored] = _resolve_runtime_graph_paths(graph, remap={}, request_input_ids=()).nodes
        read["scoring"] = Path(scored.data.config["path"]).resolve()
    if node_type is not NodeType.API_INPUT:
        bundled = collect_artifacts(graph, [], project / "rating", project_root=project)
        key = {
            "model_path": "n__m.cbm",
            "feature_contract_path": "n__feature_contract.json",
        }.get(field, f"n__{Path(raw).name}")
        read["bundle"] = bundled[key].resolve()

    assert read == dict.fromkeys(read, engine)


# ---------------------------------------------------------------------------
# What deploy ships
# ---------------------------------------------------------------------------


def _save(project: Path, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
    """Save *nodes* as the project's pipeline, ``rating/main.py``."""
    graph = PipelineGraph(nodes=nodes, edges=edges)
    files = {
        **graph_to_code_multi(graph, pipeline_name="main"),
        **SavePipelineService._collect_node_configs_recursive(graph),
    }
    for relative, content in files.items():
        target = project / "rating" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _resolve(project: Path) -> ResolvedDeploy:
    return resolve_config(
        DeployConfig(
            pipeline_file=project / "rating" / "main.py", project_dir=project, model_name="m"
        )
    )


def _deploy(project: Path, nodes: list[GraphNode], edges: list[GraphEdge]) -> ResolvedDeploy:
    """Save *nodes* as the project's pipeline and resolve its deployment."""
    _save(project, nodes, edges)
    return _resolve(project)


_QUOTES = _node("quote_input", NodeType.API_INPUT, {"path": "quotes.parquet"})
_RESPONSE = _node("quote_response", NodeType.OUTPUT, make_output_config(["quote_id", "factor"]))


def _rates_pipeline(rates: dict[str, Any]) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Each quote priced with the factor its region reads from the Data Input *rates*."""
    nodes = [
        _QUOTES,
        _node("rates", NodeType.DATA_INPUT, rates),
        _node("priced", NodeType.POLARS, {"code": "df = request.join(rates, on='region')"}),
        _RESPONSE,
    ]
    edges = [
        _edge("quote_input", "priced", "request"),
        _edge("rates", "priced"),
        _edge("priced", "quote_response"),
    ]
    return nodes, edges


def _lookup_pipeline(path: str) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Each quote priced with the factor a Load File reads from the JSON file *path*."""
    lookup = _node(
        "lookup",
        NodeType.EXTERNAL_FILE,
        {
            "path": path,
            "file_type": "json",
            "code": "df = df.with_columns(factor=pl.lit(obj['factor']))",
        },
    )
    nodes = [_QUOTES, lookup, _RESPONSE]
    edges = [_edge("quote_input", "lookup", "request"), _edge("lookup", "quote_response")]
    return nodes, edges


def _scored(resolved: ResolvedDeploy) -> dict[int, float]:
    """Each quote's factor, scored from the bundled artefacts as a deployment scores."""
    with resolved:
        result = score_graph(
            graph=resolved.pruned_graph,
            input_df=pl.DataFrame({"quote_id": [1, 2], "region": ["N", "S"]}),
            input_node_ids=resolved.input_node_ids,
            output_node_id=resolved.output_node_id,
            artifact_paths={name: str(path) for name, path in resolved.artifacts.items()},
        )
    return dict(zip(result["quote_id"].to_list(), result["factor"].to_list(), strict=True))


def _refusal(project: Path) -> str:
    """The refusal of the project's deployment, in words."""
    with pytest.raises(DeployError) as raised:
        _resolve(project).close()
    return str(raised.value)


@pytest.mark.parametrize(
    ("raw", "copies"),
    [
        ("data/rates.parquet", ["data/rates.parquet"]),
        ("data/rates.csv", ["data/rates.csv"]),
        ("data/rates", ["data/rates/north.parquet", "data/rates/south.parquet"]),
        ("data/r*.parquet", ["data/r1.parquet", "data/r2.parquet"]),
    ],
    ids=["parquet", "csv", "folder", "glob"],
)
def test_differing_data_copies_refuse_the_deploy(
    project: Path, raw: str, copies: list[str]
) -> None:
    """A Data Input whose path names a different file in each folder is refused naming both.

    A folder's or a glob's copies differ in one file only, the last it names.
    """
    for relative in copies:
        _rates_file(project / relative, {"N": 1.1, "S": 1.2})
        _rates_file(project / "rating" / relative, {"N": 1.1, "S": 1.2})
    _rates_file(project / "rating" / copies[-1], {"N": 9.9, "S": 1.2})
    _save(project, *_rates_pipeline(make_file_input_config(raw)))

    message = _refusal(project)

    assert "'rates'" in message
    assert "'path'" in message
    assert f"rating/{raw}" in message
    assert f"{raw} under the project root" in message


@pytest.mark.parametrize("setting", sorted(_CASES, key=str), ids=lambda s: f"{s[0].value}.{s[1]}")
def test_differing_copies_of_any_path_setting_refuse_the_deploy(
    project: Path, setting: tuple[NodeType, str]
) -> None:
    """Every path-bearing setting on the path to the quote output is checked.

    The copies are written after the pipeline is saved: a save derives a Model Scoring
    node's columns from its model file, so it reads a real one, and the copies that follow
    are not models. The check refuses before deploy reads any of them.
    """
    node_type, field = setting
    config, raw, write_copy = _CASES[setting]
    if node_type is NodeType.API_INPUT:
        nodes = [_node("quote_input", node_type, config(raw)), _RESPONSE]
        edges = [_edge("quote_input", "quote_response", "request")]
    elif node_type is NodeType.DATA_INPUT:
        nodes, edges = _rates_pipeline(config(raw))
    else:
        nodes = [_QUOTES, _node("n", node_type, config(raw)), _RESPONSE]
        edges = [_edge("quote_input", "n", "request"), _edge("n", "quote_response")]
    if field == "model_path":
        _model(project / raw)
    _save(project, nodes, edges)
    write_copy(project / raw, 1)
    write_copy(project / "rating" / raw, 2)
    _write(project / "models" / "only.cbm", b"model")

    message = _refusal(project)

    assert repr(field) in message
    assert f"rating/{raw}" in message
    assert f"{raw} under the project root" in message


def test_a_copy_linking_out_of_the_project_refuses_the_deploy(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Comparing the copies never reads a file outside the project: the link is refused."""
    outside = tmp_path_factory.mktemp("outside")
    _rates_file(outside / "part.parquet", {"N": 1.1, "S": 1.2})
    link = project / "data" / "rates" / "linked"
    link.parent.mkdir(parents=True)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    _rates_file(project / "rating" / "data" / "rates" / "linked" / "part.parquet", {"N": 1.1})
    _save(project, *_rates_pipeline(make_file_input_config("data/rates")))

    message = _refusal(project)

    assert "'rates'" in message
    assert "leads outside the project root" in message


def test_identical_load_file_copies_ship_the_file_the_preview_read(project: Path) -> None:
    """Identical copies pass, and the bundle carries the project root's, as the editor reads."""
    root_copy = _write(project / "data" / "lookup.json", b'{"factor": 1.5}')
    _write(project / "rating" / "data" / "lookup.json", b'{"factor": 1.5}')

    resolved = _deploy(project, *_lookup_pipeline("data/lookup.json"))

    shipped = resolved.artifacts["lookup__lookup.json"]
    assert shipped.resolve() == root_copy.resolve()
    assert shipped.read_bytes() == root_copy.read_bytes()
    assert _scored(resolved) == {1: 1.5, 2: 1.5}


def test_a_single_load_file_copy_at_the_root_ships(project: Path) -> None:
    """A Load File may ship from anywhere in the project: its only copy is the one shipped."""
    root_copy = _write(project / "data" / "lookup.json", b'{"factor": 2.5}')

    resolved = _deploy(project, *_lookup_pipeline("data/lookup.json"))

    assert resolved.artifacts["lookup__lookup.json"].read_bytes() == root_copy.read_bytes()
    assert _scored(resolved) == {1: 2.5, 2: 2.5}


def test_a_single_data_copy_in_the_pipeline_folder_ships(project: Path) -> None:
    """A path written from the pipeline folder names its file there, in preview and bundle."""
    in_folder = _rates_file(
        project / "rating" / "files" / "data" / "rates.parquet", {"N": 1.1, "S": 1.2}
    )

    resolved = _deploy(
        project, *_rates_pipeline(make_file_input_config("files/data/rates.parquet"))
    )

    assert resolved.artifacts["rates__rates.parquet"].read_bytes() == in_folder.read_bytes()
    assert _scored(resolved) == {1: 1.1, 2: 1.2}


def test_identical_data_copies_are_judged_on_the_root_copy(project: Path) -> None:
    """The editor reads the project root's copy, outside the pipeline folder, so deploy refuses it.

    The refusal names the identical copy in the pipeline folder and the path that names it.
    """
    _rates_file(project / "data" / "rates.parquet", {"N": 1.1, "S": 1.2})
    _rates_file(project / "rating" / "data" / "rates.parquet", {"N": 1.1, "S": 1.2})
    _save(project, *_rates_pipeline(make_file_input_config("data/rates.parquet")))

    message = _refusal(project)

    assert "outside the pipeline folder" in message
    assert "'rates'" in message
    assert "rating/data/rates.parquet" in message
