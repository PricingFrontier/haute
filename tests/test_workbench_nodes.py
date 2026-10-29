"""The workbench nodes' tables, given from the project's form wherever the pipeline runs
(specs/workbench): a browser's graph at the routes, deploy's bundle once, a save never."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph, SubmodelDefinition
from haute._workbench_config import WorkbenchConfigError
from haute._workbench_form import WorkbenchFormError
from haute._workbench_nodes import with_form_tables
from haute.deploy._config import DeployConfig, resolve_config
from haute.deploy._utils import shipped_graph
from haute.routes._save_pipeline import SavePipelineService
from tests._workbench_helpers import tables_form, workbench_project
from tests.test_workbench_output import FORM_SAMPLE, FORM_TABLES, _priced, _write

#: The sample quote's policy as the Workbench Input's `policy` port gives it, priced under
#: the stand-in identity.
_POLICY = {"quote_reference": "PREVIEW", "quote_option": 1, "state": "NY", "limit": 1000}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A workbench project whose form defines `_priced`'s tables and sample, the working
    directory, its pipeline saved as the editor saves it."""
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    _write(_priced(), tmp_path, "main")
    workbench_project(tmp_path, FORM_TABLES, FORM_SAMPLE)
    yield tmp_path
    set_project_root(original)


def _node(nid: str, node_type: NodeType, config: dict[str, Any] | None = None) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config or {}))


def _editor_graph() -> PipelineGraph:
    """`_priced` as the editor holds it: its workbench nodes hold no tables."""
    graph = _priced()
    for node in graph.nodes:
        if node.data.nodeType in (NodeType.WORKBENCH_INPUT, NodeType.WORKBENCH_OUTPUT):
            node.data.config = {}
    return graph


def _preview_policy(client: TestClient, **body: Any) -> Any:
    answer = client.post(
        "/api/pipeline/preview",
        json={
            "graph": _editor_graph().model_dump(mode="json"),
            "node_id": "request",
            "port_label": "policy",
            **body,
        },
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_browser_graph_runs_on_the_forms_tables_read_anew_for_each_request(
    project: Path,
) -> None:
    from haute.server import app

    client = TestClient(app)
    assert _preview_policy(client)["preview"] == [_POLICY]

    # A hand edit of the form folder reaches the next request, past any cached result: the
    # sample a node runs on is in its cache key.
    workbench_project(project, FORM_TABLES, {"t_policy": [{"c_state": "CA", "c_limit": "5"}]})
    assert _preview_policy(client)["preview"] == [{**_POLICY, "state": "CA", "limit": 5}]


def test_the_form_a_pricing_posts_is_run_in_place_of_the_folders(project: Path) -> None:
    from haute.server import app

    folder = {path.name: path.read_bytes() for path in (project / "workbench").rglob("*.json")}
    unsaved = tables_form(FORM_TABLES, {"t_policy": [{"c_state": "TX", "c_limit": "7"}]})

    previewed = _preview_policy(
        TestClient(app), workbench_form=unsaved.model_dump(mode="json", by_alias=True)
    )

    assert previewed["preview"] == [{**_POLICY, "state": "TX", "limit": 7}]
    # Nothing is written.
    assert {
        path.name: path.read_bytes() for path in (project / "workbench").rglob("*.json")
    } == folder


def test_a_workbench_node_inside_a_submodel_takes_the_form_too(project: Path) -> None:
    definition = SubmodelDefinition(
        definitionId="pricing",
        file="modules/pricing.py",
        graph=PipelineGraph(nodes=[_node("nested", NodeType.WORKBENCH_INPUT)], pipeline_name="p"),
        inputPorts=[],
        outputPorts=[],
    )
    graph = PipelineGraph(nodes=[], submodels={"pricing": definition})

    given = with_form_tables(graph, project_root=project)

    nested = given.submodels["pricing"].graph.nodes[0].data.config
    assert [table["name"] for table in nested["tables"]] == ["quote_details", "policy", "items"]
    assert nested["sample"]["policy"] == {"state": "NY", "limit": 1000}
    # The graph given them is a copy: the one the request held is as it was.
    assert graph.submodels is not None
    assert graph.submodels["pricing"].graph.nodes[0].data.config == {}


def test_a_graph_without_a_workbench_node_reads_no_form(tmp_path: Path) -> None:
    graph = PipelineGraph(nodes=[_node("rates", NodeType.CONSTANT, {"values": []})])

    # No haute.toml, no form folder: nothing is read.
    assert with_form_tables(graph, project_root=tmp_path) is graph


@pytest.mark.parametrize(
    ("graph", "owner"),
    [
        (_editor_graph(), "Workbench Input"),
        (PipelineGraph(nodes=[_node("response", NodeType.WORKBENCH_OUTPUT)]), "Workbench Output"),
    ],
    ids=["input", "output"],
)
def test_a_workbench_node_in_a_project_whose_workbench_is_off_is_refused(
    project: Path, graph: PipelineGraph, owner: str
) -> None:
    from haute.server import app

    (project / "haute.toml").write_text(
        '[project]\nname = "x"\npipeline = "main.py"\n\n[workbench]\nenabled = false\n',
        encoding="utf-8",
    )
    refused = (
        f"The {owner} reads its tables from the project's workbench, which haute.toml does "
        "not enable."
    )

    with pytest.raises(WorkbenchConfigError, match=re.escape(refused)):
        with_form_tables(graph, project_root=project)
    # A preview reports it at the node, as it reports any configuration it cannot run.
    answer = TestClient(app).post(
        "/api/pipeline/preview",
        json={"graph": graph.model_dump(mode="json"), "node_id": graph.nodes[0].id},
    )
    assert answer.status_code == 200, answer.text
    assert (answer.json()["status"], answer.json()["error"]) == ("error", refused)


@pytest.mark.parametrize(
    ("node_type", "config", "says"),
    [
        (NodeType.WORKBENCH_INPUT, {"tables": []}, "removed tables field"),
        (NodeType.WORKBENCH_INPUT, {"sample": {}}, "removed sample field"),
        (NodeType.WORKBENCH_OUTPUT, {"tables": []}, "removed tables field"),
    ],
    ids=["input-tables", "input-sample", "output-tables"],
)
def test_a_save_holding_a_workbench_nodes_tables_is_refused(
    node_type: NodeType, config: dict[str, Any], says: str
) -> None:
    """The editor never sends them: a graph that does, as an older editor's, saves nothing."""
    graph = PipelineGraph(nodes=[_node("node", node_type, config)])

    with pytest.raises(HTTPException) as refused:
        SavePipelineService._validate_declared_config_keys(graph)

    assert refused.value.status_code == 400
    assert says in refused.value.detail
    assert "from the form folder" in refused.value.detail
    assert refused.value.detail.endswith("Nothing was saved.")


def test_a_connection_from_a_table_named_like_a_node_is_refused_at_save() -> None:
    """The Workbench Input holds no tables, so its connections say which it takes: one named
    like a node would bind a consumer's parameter to both, and the file would not reload."""
    graph = PipelineGraph(
        nodes=[
            _node("quote", NodeType.WORKBENCH_INPUT),
            _node("quotes", NodeType.POLARS, {"code": "df = quotes"}),
            _node("rated", NodeType.POLARS, {"code": "df = quotes"}),
        ],
        edges=[GraphEdge(id="e", source="quote", target="rated", sourceHandle="quotes")],
    )

    with pytest.raises(HTTPException) as refused:
        SavePipelineService._validate_quote_input_tables_do_not_shadow_nodes(graph)

    assert refused.value.status_code == 400
    assert "Workbench Input 'quote' has a table 'quotes' named like the node 'quotes'" in (
        refused.value.detail
    )
    # A table no connection takes binds nothing.
    graph.edges = []
    SavePipelineService._validate_quote_input_tables_do_not_shadow_nodes(graph)


def test_deploy_bundles_the_forms_tables_without_its_sample(project: Path) -> None:
    resolved = resolve_config(DeployConfig(pipeline_file=project / "main.py", model_name="m"))

    assert resolved.workbench_form is not None
    request = resolved.pruned_graph.node_map["request"].data.config
    assert [table["name"] for table in request["tables"]] == ["quote_details", "policy", "items"]
    assert request["sample"]["policy"] == {"state": "NY", "limit": 1000}
    response = resolved.pruned_graph.node_map["response"].data.config
    assert [table["name"] for table in response["tables"]] == ["pricing", "item_prices"]
    # The bundle carries the tables the deployed pipeline was validated on, never the sample.
    shipped = {
        node["id"]: node["data"]["config"] for node in shipped_graph(resolved.pruned_graph)["nodes"]
    }
    assert shipped["request"]["tables"] == request["tables"]
    assert "sample" not in shipped["request"]
    # The config files hold neither.
    assert json.loads((project / "config" / "workbench_input" / "request.json").read_text()) == {}


def _broken_form(project: Path) -> None:
    (project / "workbench" / "form.json").write_text("not json", encoding="utf-8")


def _no_form(project: Path) -> None:
    import shutil

    shutil.rmtree(project / "workbench")


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        (_broken_form, (WorkbenchFormError, "workbench/form.json is not JSON")),
        (_no_form, (WorkbenchFormError, "workbench does not exist: restore it")),
    ],
    ids=["not-json", "gone"],
)
def test_deploy_refuses_a_form_it_cannot_read_before_anything_is_built(
    project: Path, change: Any, refused: tuple[type[Exception], str]
) -> None:
    change(project)
    error, says = refused

    with pytest.raises(error, match=re.escape(says)):
        resolve_config(DeployConfig(pipeline_file=project / "main.py", model_name="m"))


def test_haute_run_runs_the_pipeline_on_the_forms_tables(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from haute.cli._run import RunConfig, handle_run

    handle_run(RunConfig(pipeline_file=project / "main.py"))

    printed = capsys.readouterr().out
    assert "✓ request" in printed and "✓ response" in printed


def test_haute_run_says_why_the_form_cannot_be_read(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from haute.cli._run import RunConfig, handle_run

    _broken_form(project)

    with pytest.raises(SystemExit):
        handle_run(RunConfig(pipeline_file=project / "main.py"))

    assert "Error reading the workbench's form: workbench/form.json is not JSON" in (
        capsys.readouterr().err
    )


def test_score_holds_a_seeded_workbench_input_to_its_form(project: Path) -> None:
    """Seeded with frames, a Workbench Input reads neither its sample nor a data file, but
    score() holds it to its form as a run does: its sidecar read and its connections' tables
    the form's."""
    import importlib.util
    import sys

    import polars as pl

    from haute.errors import ConfigError, ExecutionError

    def score() -> object:
        spec = importlib.util.spec_from_file_location("scored_main", project / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["scored_main"] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("scored_main", None)
        return module.pipeline.score(
            {
                "policy": pl.DataFrame({"state": ["TX"], "limit": [500]}),
                "items": pl.DataFrame({"item_id": ["Z"], "value": [7.0]}),
            }
        )

    score()

    # The form drops a table a connection takes.
    workbench_project(project, [table for table in FORM_TABLES if table["name"] != "items"])
    with pytest.raises(ExecutionError, match="is connected on 'items', which the workbench's"):
        score()

    # A sidecar an earlier Haute wrote, its copies in it, is refused as a run refuses it.
    workbench_project(project, FORM_TABLES, FORM_SAMPLE)
    sidecar = project / "config" / "workbench_input" / "request.json"
    sidecar.write_text(json.dumps({"tables": [], "sample": {}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="removed tables and sample fields"):
        score()
