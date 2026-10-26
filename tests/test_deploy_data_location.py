"""Deploy ships the data the quote output reads only from the pipeline folder (specs/deploy).

A file-based Data Input on the path to the quote output must live inside the pipeline
folder (``rating/``): deploy ships it, building or refreshing its snapshot first, so a
clean checkout in CI deploys it and an edited file never ships its old copy, and a
direct Parquet folder or glob ships every file it names. One outside the folder is
refused, since the deployed model could not score without it. A deployment drops every
node the quote output does not depend on, so off that path it may live anywhere.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code_multi
from haute.deploy._config import DeployConfig, ResolvedDeploy, resolve_config
from haute.deploy._scorer import score_graph
from haute.errors import DeployError
from haute.routes._save_pipeline import SavePipelineService
from tests.conftest import make_file_input_config, make_output_config


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A project whose pipeline lives in ``rating/``, as ``haute init`` lays it out.

    The project is ``tmp_path`` itself and the test names are short: a snapshot's
    path under the xdist temporary folder must stay within Windows' path limit.
    """
    root = tmp_path
    (root / "rating").mkdir(parents=True)
    (root / "haute.toml").write_text(
        '[project]\nname = "p"\npipeline = "rating/main.py"\n', encoding="utf-8"
    )
    pl.DataFrame({"quote_id": [1, 2], "region": ["N", "S"]}).write_parquet(
        root / "rating" / "quotes.parquet"
    )
    original = _get_project_root()
    monkeypatch.chdir(root)
    set_project_root(root)
    try:
        yield root
    finally:
        set_project_root(original)


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _edge(source: str, target: str, port: str | None = None) -> GraphEdge:
    return GraphEdge(id=f"{source}_{target}", source=source, target=target, sourceHandle=port)


def _deploy(
    project: Path,
    rates: dict[str, Any],
    *,
    training_path: str | None = None,
    batch_path: str | None = None,
) -> ResolvedDeploy:
    """Resolve a deployment pricing each quote with the factor its region reads from *rates*.

    *training_path* adds a branch the quote output does not read; *batch_path* routes the
    quotes through a source switch whose non-live input reads that file.
    """
    _write_pipeline(project, rates, training_path=training_path, batch_path=batch_path)
    return _resolve(project)


def _write_pipeline(
    project: Path,
    rates: dict[str, Any],
    *,
    training_path: str | None = None,
    batch_path: str | None = None,
) -> Path:
    """Write the pipeline :func:`_deploy` deploys into ``rating/`` and return its file."""
    quotes = "request" if batch_path is None else "switch"
    nodes = [
        _node("request", NodeType.API_INPUT, {"path": "quotes.parquet"}),
        _node("rates", NodeType.DATA_INPUT, rates),
        _node("priced", NodeType.POLARS, {"code": f"df = {quotes}.join(rates, on='region')"}),
        _node("out", NodeType.OUTPUT, make_output_config(["quote_id", "factor"])),
    ]
    edges = [
        _edge("request", "priced" if batch_path is None else "switch", "request"),
        _edge("rates", "priced"),
        _edge("priced", "out"),
    ]
    if batch_path is not None:
        nodes += [
            _node("batch", NodeType.DATA_INPUT, make_file_input_config(batch_path)),
            _node(
                "switch",
                NodeType.LIVE_SWITCH,
                {
                    "input_scenario_map": {"request": "live", "batch": "batch"},
                    "inputs": ["request", "batch"],
                },
            ),
        ]
        edges += [_edge("batch", "switch"), _edge("switch", "priced")]
    if training_path is not None:
        nodes += [
            _node("history", NodeType.DATA_INPUT, make_file_input_config(training_path)),
            _node("prepared", NodeType.POLARS, {"code": "df = history"}),
        ]
        edges.append(_edge("history", "prepared"))
    graph = PipelineGraph(nodes=nodes, edges=edges)
    files = {
        **graph_to_code_multi(graph, pipeline_name="main"),
        **SavePipelineService._collect_node_configs_recursive(graph),
    }
    for relative, content in files.items():
        target = project / "rating" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return project / "rating" / "main.py"


def _resolve(project: Path) -> ResolvedDeploy:
    return resolve_config(
        DeployConfig(
            pipeline_file=project / "rating" / "main.py", project_dir=project, model_name="m"
        )
    )


def _rates(path: Path, factors: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame({"region": list(factors), "factor": list(factors.values())})
    frame.write_csv(path) if path.suffix == ".csv" else frame.write_parquet(path)


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


@pytest.mark.parametrize("name", ["rates.parquet", "rates.csv"])
def test_outside_data_on_quote_path_refused(project: Path, name: str) -> None:
    """Data the quote output reads from outside the pipeline folder is refused."""
    _rates(project / "data" / name, {"N": 1.1, "S": 1.2})

    with pytest.raises(DeployError) as raised:
        _deploy(project, make_file_input_config(f"data/{name}"))

    message = str(raised.value)
    assert "'rates'" in message
    assert f"data/{name}" in message
    assert "'rating'" in message


@pytest.mark.parametrize("branch", ["training_path", "batch_path"])
def test_outside_data_off_quote_path_ok(project: Path, branch: str) -> None:
    """Data outside the pipeline folder that the quote output does not read is not shipped.

    Off the path is a branch the output never reads or a source switch's non-live input.
    """
    _rates(project / "rating" / "files" / "data" / "rates.parquet", {"N": 1.1, "S": 1.2})
    (project / "data").mkdir()
    pl.DataFrame({"quote_id": [9], "region": ["N"]}).write_csv(project / "data" / "o.csv")

    resolved = _deploy(
        project, make_file_input_config("files/data/rates.parquet"), **{branch: "data/o.csv"}
    )

    assert {"history", "batch"}.isdisjoint(node.id for node in resolved.pruned_graph.nodes)
    assert not any(name.startswith(("history", "batch")) for name in resolved.artifacts)
    assert _scored(resolved) == {1: 1.1, 2: 1.2}


def test_csv_ships_unbuilt(project: Path) -> None:
    """A CSV in the pipeline folder ships from a clean checkout, as in CI.

    No input snapshot exists until deploy builds it.
    """
    _rates(project / "rating" / "files" / "data" / "rates.csv", {"N": 1.1, "S": 1.2})

    resolved = _deploy(project, make_file_input_config("files/data/rates.csv"))

    assert any(name.startswith("rates__snapshot.part-") for name in resolved.artifacts)
    assert _scored(resolved) == {1: 1.1, 2: 1.2}


def test_edited_csv_ships_new(project: Path) -> None:
    """An edited CSV ships its new rows, not its old snapshot."""
    rates = project / "rating" / "files" / "data" / "rates.csv"
    _rates(rates, {"N": 1.1, "S": 1.2})
    _deploy(project, make_file_input_config("files/data/rates.csv")).close()

    _rates(rates, {"N": 2.1, "S": 2.2})

    assert _scored(_deploy(project, make_file_input_config("files/data/rates.csv"))) == {
        1: 2.1,
        2: 2.2,
    }


def test_parquet_glob_ships(project: Path) -> None:
    """A direct Parquet glob in the pipeline folder ships every file it matches."""
    folder = project / "rating" / "files" / "data" / "rates"
    _rates(folder / "north.parquet", {"N": 1.1})
    _rates(folder / "south.parquet", {"S": 1.2})

    resolved = _deploy(project, make_file_input_config("files/data/rates/*.parquet"))

    assert _scored(resolved) == {1: 1.1, 2: 1.2}


def test_hive_folder_ships(project: Path) -> None:
    """A partitioned Parquet folder ships with its partition column."""
    folder = project / "rating" / "files" / "data" / "rates"
    _rates(folder / "region=N" / "part.parquet", {"N": 1.1})
    _rates(folder / "region=S" / "part.parquet", {"S": 1.2})
    for part in folder.rglob("part.parquet"):
        pl.read_parquet(part).drop("region").write_parquet(part)

    resolved = _deploy(project, make_file_input_config("files/data/rates"))

    assert _scored(resolved) == {1: 1.1, 2: 1.2}


def _link(link: Path, folder: Path) -> None:
    """Make *link* a link to *folder*: a junction on Windows, which needs no privilege."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(folder), str(link))
    else:
        link.symlink_to(folder, target_is_directory=True)


@pytest.mark.parametrize(
    "path",
    ["linked/rates.parquet", "linked/*.parquet", "*/rates.parquet"],
    ids=["file", "glob", "expanded"],
)
def test_linked_outside_data_refused(project: Path, path: str) -> None:
    """A link inside the pipeline folder to data outside it does not ship that data.

    In ``expanded`` the pattern itself lies inside the folder: only a file it matches,
    reached through the link, lies outside.
    """
    _rates(project / "data" / "rates.parquet", {"N": 1.1, "S": 1.2})
    _link(project / "rating" / "files" / "data" / "linked", project / "data")

    with pytest.raises(DeployError, match="outside the pipeline folder") as raised:
        _deploy(project, make_file_input_config(f"files/data/{path}"))

    assert "'rates'" in str(raised.value)


@pytest.mark.parametrize("change", ["uncapped", "deleted"])
def test_stale_reuse_refused(project: Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    """A snapshot preparation could only reuse is not shipped.

    The file changed where no memory cap can contain a rebuild, or it is gone.
    """
    rates = project / "rating" / "files" / "data" / "rates.csv"
    _rates(rates, {"N": 1.1, "S": 1.2})
    _deploy(project, make_file_input_config("files/data/rates.csv")).close()
    if change == "deleted":
        rates.unlink()
    else:
        _rates(rates, {"N": 2.1, "S": 2.2})
        monkeypatch.setattr("haute._input_preparation.current_native_memory_backend", lambda: None)
        monkeypatch.setattr("haute._input_preparation.process_memory_caps_supported", lambda: False)

    with pytest.raises(DeployError) as raised:
        _deploy(project, make_file_input_config("files/data/rates.csv"))

    assert "'rates'" in str(raised.value)
    assert "files/data/rates.csv" in str(raised.value)


def test_scan_arguments_ship(project: Path) -> None:
    """A Parquet folder ships read with its scan's own arguments."""
    folder = project / "rating" / "files" / "data" / "rates"
    _rates(folder / "rates.parquet", {"N": 1.1, "S": 1.2})
    _rates(folder / "x_rates.parquet", {"N": 9.0, "S": 9.0})

    resolved = _deploy(
        project,
        make_file_input_config("files/data/rates", arguments={"hidden_file_prefix": "x_"}),
    )

    assert _scored(resolved) == {1: 1.1, 2: 1.2}


def test_cached_node_output_not_shipped(project: Path) -> None:
    """A node output cached when the pipeline ran in the editor is never deployed.

    The pricing node's cached output here disagrees with the data (every factor 9.0), and
    the editor would read it as that node's current output; the deployment neither
    bundles it nor reads it, and prices from the data it ships.
    """
    from haute._data_points import DataPointResolver
    from haute._execution_context import ExecutionProfile
    from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
    from haute._seed_plans import SeedPlanRequest, resolve_seed_plan
    from haute.parser import parse_pipeline_file

    _rates(project / "rating" / "files" / "data" / "rates.parquet", {"N": 1.1, "S": 1.2})
    graph = parse_pipeline_file(
        _write_pipeline(project, make_file_input_config("files/data/rates.parquet"))
    )
    store = NodeSnapshotStore(project)
    resolver = DataPointResolver(graph, source="live", store=store)
    identity = resolver.node_output_slot("priced").identity(
        resolver.node_output_signature("priced")
    )
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"quote_id": [1, 2], "region": ["N", "S"], "factor": [9.0, 9.0]}).write_parquet(
        artifact.part_path(0)
    )
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    ):
        pass
    cached = store.latest_generation(identity)
    assert cached is not None
    assert cached.lazy_frame.collect()["factor"].to_list() == [9.0, 9.0]
    cached_folder = cached.generation.metadata_path.parent.resolve()
    editor_run = resolve_seed_plan(
        SeedPlanRequest(
            graph=graph, target_node_id="out", source="live", profile=ExecutionProfile.TRAINING_PREP
        ),
        store=store,
    )
    assert "priced" in editor_run.seeds

    resolved = _resolve(project)

    assert not any(
        Path(path).resolve().is_relative_to(cached_folder) for path in resolved.artifacts.values()
    )
    assert _scored(resolved) == {1: 1.1, 2: 1.2}
