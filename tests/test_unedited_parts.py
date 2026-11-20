"""Every request that carries the document reads from it only the parts its editor edited
(CGEN-12, CGEN-13, PCF-09).

Each shipped example's document, and a hand-written pipeline whose values a browser
changes, goes through a number and text round trip as JavaScript performs it and is sent
as the editor sends it (``tests._unedited_parts.BrowserDocument``), in three states:
nothing edited, one node edited, and a node the baseline does not hold, at the root and
inside a submodel. What the file computes is the same request sent with the graph the
server parsed, which no browser has held.
"""

from __future__ import annotations

import ast
import copy
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from haute._edited_parts import with_saved_forms
from haute.deploy._pruner import find_output_node
from tests._preview_targets import node_target, occurrence_output_target
from tests._unedited_parts import (
    BrowserDocument,
    examples,
    files,
    make_project,
    node_at,
    pipeline_file,
    scoped,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _exact(document: BrowserDocument) -> dict[str, Any]:
    """What the file computes: the graph the server parsed, which no browser has held, sent
    as its own baseline, so each node runs its file's code with its file's settings."""
    return with_saved_forms(document.graph, document.graph)


def _refusal(response: Any) -> tuple[int, str]:
    """A refusal by its status and its words, without the incident id each one is given."""
    return response.status_code, re.sub(r"incident [0-9a-f]+", "incident", response.text)


def _preview(client: Any, graph: dict[str, Any], node_id: str) -> tuple[Any, ...]:
    response = client.post(
        "/api/pipeline/preview", json={"graph": graph, "target": node_target(node_id)}
    )
    if response.status_code != 200:
        return _refusal(response)
    answer = response.json()
    columns = [(column["name"], column["dtype"]) for column in answer.get("columns") or []]
    return answer["status"], answer.get("error"), columns, answer.get("preview")


def _trace(client: Any, graph: dict[str, Any], node_id: str) -> Any:
    response = client.post(
        "/api/pipeline/trace",
        json={"graph": graph, "target": node_target(node_id), "row_index": 0},
    )
    answer = response.json()
    return response.status_code, {
        key: answer.get(key) for key in ("output_value", "row_values", "steps", "detail")
    }


def _address(node_id: str) -> dict[str, Any]:
    return {"occurrence": None, "node_id": node_id}


def _explore_point(client: Any, graph: dict[str, Any]) -> dict[str, Any]:
    """The data the Explore node reads, as the request for *graph* finds it."""
    response = client.post(
        "/api/node-data/point", json={"graph": graph, "target": _address("explored")}
    )
    assert response.status_code == 200, response.text
    answer = response.json()
    return {key: answer[key] for key in ("state", "data_version", "row_count", "size_bytes")}


def _explore(client: Any, graph: dict[str, Any]) -> dict[str, Any]:
    """Build the data the Explore node reads from the request for *graph*, and its point."""
    body = {"graph": graph, "target": _address("explored"), "refresh": True}
    response = client.post("/api/node-data/run", json=body)
    assert response.status_code == 200, response.text
    started = response.json()
    if started["status"] in {"started", "joined"}:
        deadline = time.monotonic() + 60
        while (status := client.get(f"/api/node-data/status/{started['job_id']}").json())[
            "status"
        ] not in {"completed", "error", "cancelled", "memory_limited", "timed_out"}:
            assert time.monotonic() < deadline, status
            time.sleep(0.02)
        assert status["status"] == "completed", status
    built = _explore_point(client, graph)
    assert built["state"] == "current", built
    return built


@pytest.fixture(autouse=True)
def _node_data_isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test's Explore data is its own, built within a modest budget whatever the host's
    free memory."""
    from haute.routes.node_data import _store

    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB", "1024")
    monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_MB", "1024")
    _store.clear_all()
    yield
    _store.clear_all()


def _output(document: BrowserDocument) -> str | None:
    try:
        return find_output_node(document.graph)
    except ValueError:  # no Quote Response: nothing to trace a response from
        return None


def _estimate(client: Any, graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/modelling/estimate", json={"graph": graph, "target": _address(node_id)}
    )
    assert response.status_code == 200, response.text
    answer = response.json()
    stable = ("total_rows", "bytes_per_row", "estimated_mb", "training_mb", "unavailable")
    return {key: answer[key] for key in stable}


def _root_nodes(document: BrowserDocument) -> list[str]:
    """The pipeline's own nodes a preview targets: every one but a submodel occurrence,
    whose frames are its ports'."""
    return [node.id for node in document.graph.nodes if node.data.nodeType != "submodel"]


@pytest.mark.parametrize("source", [None, *examples()])
def test_with_nothing_edited_each_request_computes_what_the_file_computes(
    client: Any, tmp_path: Path, source: Path | None
) -> None:
    root = make_project(tmp_path, source)
    document = BrowserDocument(root, pipeline_file(root))
    sent, exact = document.request_graph(), _exact(document)

    for node_id in _root_nodes(document):
        assert _preview(client, sent, node_id) == _preview(client, exact, node_id), node_id
    output = _output(document)
    if output is not None:
        assert _trace(client, sent, output) == _trace(client, exact, output)
    trained = [n.id for n in document.graph.nodes if n.data.nodeType == "modelling"]
    for node_id in trained:
        assert _estimate(client, sent, node_id) == _estimate(client, exact, node_id)
    if source is None:
        # The Explore node reads what the file's own request would: the data the browser's
        # request built is the data the file's request finds current.
        assert _explore(client, sent) == _explore_point(client, exact)
        # CGEN-12: the column a hand-written ``pl.lit(1.0)`` makes is a decimal.
        _status, _error, columns, _rows = _preview(client, sent, "relativity")
        assert dict(columns)["relativity"] == "Float64"


def test_the_requests_tell_apart_what_a_browser_held(client: Any, tmp_path: Path) -> None:
    """The fixture's comparison sees the difference the rule removes: the same requests
    sent with every part read from the browser's copy compute something else."""
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    held, exact = document.live, _exact(document)

    _status, _error, columns, _rows = _preview(client, held, "relativity")
    assert dict(columns)["relativity"] == "Int32"
    assert _explore(client, held) != _explore_point(client, exact)
    assert _estimate(client, held, "train") != _estimate(client, exact, "train")


def test_a_field_changed_in_transit_on_an_untouched_node_is_not_run(
    client: Any, tmp_path: Path
) -> None:
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    sent = document.request_graph()
    rated = node_at(sent, (None, "rated"))["data"]["config"]
    rated["tables"][0]["default_value"] = 7
    relativity = node_at(sent, (None, "relativity"))["data"]["config"]
    relativity["steps"][-1]["kind"] = "free_code"
    relativity["steps"][-1]["code"] = "df = df.head(0)"

    for node_id in ("relativity", "rated"):
        assert _preview(client, sent, node_id) == _preview(client, _exact(document), node_id)


def _functions(source: str) -> dict[str, str]:
    """Each node function of a pipeline *source*, by name, as its text."""
    tree = ast.parse(source)
    return {
        node.name: ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _edit(document: BrowserDocument, edit: Any) -> dict[str, Any]:
    live = copy.deepcopy(document.live)
    edit(live)
    return document.request_graph(live)


def _save(client: Any, document: BrowserDocument, graph: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/pipeline/save", json=document.save_request(graph))
    assert response.status_code == 200, response.text
    return response.json()


def test_a_label_changed_keeps_the_nodes_code_and_settings(client: Any, tmp_path: Path) -> None:
    """A rename moves the node's function and settings file and keeps what they hold. (A node
    it is read by would have the input name in its steps rewritten, which edits its steps:
    rendering a number of steps a browser held as it was written is CGEN-09's.)"""
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    before, functions = files(root), _functions((root / "main.py").read_text("utf-8"))

    def rename(live: dict[str, Any]) -> None:
        node_at(live, (None, "rated"))["data"]["label"] = "rated_loading"

    sent = _edit(document, rename)
    _status, _error, columns, _rows = _preview(client, sent, "rated")
    assert dict(columns)["loading"] == "Float64"
    answer = _save(client, document, sent)

    after = files(root)
    saved = _functions(after["main.py"].decode("utf-8"))
    assert saved.pop("rated_loading") == functions.pop("rated").replace(
        "def rated(", "def rated_loading("
    )
    assert saved == functions
    assert 'pl.lit(1.0).alias("loading")' in after["main.py"].decode("utf-8")
    moved = "config/rating_step/rated_loading.json"
    assert after.pop(moved) == before.pop("config/rating_step/rated.json")
    assert {name for name in after if after[name] != before.get(name)} <= {
        "main.py",
        "main.haute.json",
    }
    assert answer["saved_parts"] == []


def test_one_setting_changed_keeps_the_nodes_code(client: Any, tmp_path: Path) -> None:
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    before = files(root)

    def rename_output(live: dict[str, Any]) -> None:
        config = node_at(live, (None, "rated"))["data"]["config"]
        config["combined_outputs"][0]["output_column"] = "rated_total"

    sent = _edit(document, rename_output)
    _status, _error, columns, _rows = _preview(client, sent, "rated")
    assert dict(columns)["loading"] == "Float64"
    assert "rated_total" in dict(columns)
    answer = _save(client, document, sent)

    after = files(root)
    assert after["main.py"] == before["main.py"]
    assert b'"rated_total"' in after["config/rating_step/rated.json"]
    changed = {name for name in after if after[name] != before.get(name)}
    assert changed == {"config/rating_step/rated.json"}
    assert [(part["node_id"], part["code"]) for part in answer["saved_parts"]] == [("rated", None)]
    assert answer["saved_parts"][0]["settings"] is not None


def test_one_step_changed_keeps_the_nodes_settings_file(client: Any, tmp_path: Path) -> None:
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    before, functions = files(root), _functions((root / "main.py").read_text("utf-8"))

    def add_step(live: dict[str, Any]) -> None:
        steps = node_at(live, (None, "rated"))["data"]["config"]["steps"]
        steps.append({"id": "added", "kind": "free_code", "code": "df = df.head(2)"})

    sent = _edit(document, add_step)
    _status, _error, _columns, rows = _preview(client, sent, "rated")
    assert rows is not None and len(rows) == 2
    _save(client, document, sent)

    after = files(root)
    assert after["config/rating_step/rated.json"] == before["config/rating_step/rated.json"]
    saved = _functions(after["main.py"].decode("utf-8"))
    assert "df = df.head(2)" in saved["rated"]
    assert {name: text for name, text in saved.items() if name != "rated"} == {
        name: text for name, text in functions.items() if name != "rated"
    }


def _added(source: str) -> dict[str, Any]:
    return {
        "id": "boosted",
        "type": "pipelineNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "label": "boosted",
            "nodeType": "polars",
            "config": {
                "steps": [
                    {"id": "s1", "kind": "source", "input": source},
                    {
                        "id": "s2",
                        "kind": "free_code",
                        "code": 'df = df.with_columns(pl.lit(1.5).alias("boost"))',
                    },
                ],
                "code": "",
            },
        },
    }


def test_a_new_node_is_saved_and_run_as_sent(client: Any, tmp_path: Path) -> None:
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    before, functions = files(root), _functions((root / "main.py").read_text("utf-8"))

    def add(live: dict[str, Any]) -> None:
        live["nodes"].append(_added("doubled"))
        live["edges"].append({"id": "e-boost", "source": "doubled", "target": "boosted"})

    sent = _edit(document, add)
    _status, _error, columns, rows = _preview(client, sent, "boosted")
    assert dict(columns)["boost"] == "Float64" and rows and rows[0]["boost"] == 1.5
    _save(client, document, sent)

    saved = _functions((root / "main.py").read_text("utf-8"))
    assert 'pl.lit(1.5).alias("boost")' in saved["boosted"]
    assert {name: saved[name] for name in functions} == functions
    after = files(root)
    assert {name for name in before if after.get(name) != before[name]} <= {
        "main.py",
        "main.haute.json",
    }


def test_a_new_node_inside_a_submodel_is_saved_and_run_as_sent(client: Any, tmp_path: Path) -> None:
    source = next(p.values[0] for p in examples() if p.id == "reusable_submodel")
    root = make_project(tmp_path, source)
    document = BrowserDocument(root, pipeline_file(root))
    module = root / "modules" / "reusable_enrichment.py"
    enriched = _functions(module.read_text("utf-8"))["enriched"]
    (definition_id,) = document.live["submodels"]

    def add(live: dict[str, Any]) -> None:
        definition = live["submodels"][definition_id]
        definition["graph"]["nodes"].append(_added("enriched"))
        definition["graph"]["edges"].append(
            {"id": "e-boost", "source": "enriched", "target": "boosted"}
        )
        (port,) = definition["outputPorts"]
        port["source"]["nodeId"] = "boosted"

    sent = _edit(document, add)
    assert {ref for ref, node in scoped(sent) if node["data"]["editor"].get("saved_settings")} == {
        ref for ref, _node in scoped(document.live)
    }
    response = client.post(
        "/api/pipeline/preview",
        json={"graph": sent, "target": occurrence_output_target("enrichment", "enriched")},
    )
    assert response.status_code == 200, response.text
    columns = {column["name"]: column["dtype"] for column in response.json()["columns"]}
    rows = response.json()["preview"]
    assert columns["boost"] == "Float64" and rows and rows[0]["boost"] == 1.5
    _save(client, document, sent)

    saved = _functions(module.read_text("utf-8"))
    assert 'pl.lit(1.5).alias("boost")' in saved["boosted"]
    assert saved["enriched"] == enriched


def _unfinished_project(tmp_path: Path) -> Path:
    """The hand-written project with ``relativity`` saved, by the server, starting from an
    input it is not connected to before its ``pl.lit(1.0)``: an unfinished list, so its
    file holds the raising placeholder and its ``.haute.json`` the list, decimal and all."""
    from haute._types import PipelineGraph
    from haute.routes._save_pipeline import SavePipelineService
    from haute.schemas import SavePipelineRequest
    from tests.conftest import current_source_revision

    root = make_project(tmp_path)
    before = BrowserDocument(root, root / "main.py").graph
    after = PipelineGraph.model_validate(before.model_dump(mode="python", by_alias=True))
    node = next(n for n in after.nodes if n.id == "relativity")
    first, *rest = node.data.config["steps"]
    steps = [{**first, "input": "rates"}, *rest]
    after.nodes[after.nodes.index(node)] = node.with_config({**node.data.config, "steps": steps})
    SavePipelineService(root).save(
        SavePipelineRequest.model_validate(
            {
                "name": "main",
                "graph": with_saved_forms(after, before),
                "source_file": "main.py",
                "base_revision": current_source_revision(root / "main.py", root),
            }
        )
    )
    assert "This node's steps are incomplete" in (root / "main.py").read_text("utf-8")
    assert '"value": 1.0' in (root / "main.haute.json").read_text("utf-8")
    return root


def test_a_node_saved_unfinished_is_run_and_saved_as_its_file_holds_it(
    client: Any, tmp_path: Path
) -> None:
    """Its program is the list its ``.haute.json`` records, not the code the list renders to
    without its inputs: a preview refuses it as the file does, naming the step, and an
    untouched save leaves the record, decimal and all, as it was."""
    root = _unfinished_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    held = document.baseline[(None, "relativity")]["editor"]
    assert held.get("saved_code") is None and held.get("saved_steps")
    before = files(root)
    sent = document.request_graph()

    answer = _preview(client, sent, "relativity")
    assert answer == _preview(client, _exact(document), "relativity")
    assert "rates" in str(answer) and "error" in str(answer)
    _save(client, document, sent)

    assert files(root) == before


def test_a_node_whose_input_was_removed_reads_its_steps(client: Any, tmp_path: Path) -> None:
    """The code saved for a node's steps was rendered against its inputs: with an input gone
    its steps are read, and the run names the step that reads it."""
    from haute._types import PipelineGraph

    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")

    def unplug(graph: dict[str, Any]) -> None:
        graph["edges"] = [e for e in graph["edges"] if e["target"] != "doubled"]

    sent = _edit(document, unplug)
    assert "saved_code" not in node_at(sent, (None, "doubled"))["data"]["editor"]
    exact = PipelineGraph.model_validate(document.graph.model_dump(mode="python", by_alias=True))
    unplug_exact = exact.model_dump(mode="python", by_alias=True)
    unplug(unplug_exact)
    reference = with_saved_forms(PipelineGraph.model_validate(unplug_exact), document.graph)

    status, error, _columns, _rows = _preview(client, sent, "doubled")
    assert (status, error) == _preview(client, reference, "doubled")[:2]
    assert status == "error" and "relativity" in str(error)
