"""A save reads from its request only the parts of a node its editor edited (CGEN-11, CGEN-13).

A node has two parts, its steps and its settings. A value that has been through a browser
has lost what JavaScript cannot hold (``1.0`` arrives as ``1``, an integer past 2**53 as
its nearest double), so a save that read every part from its request would rewrite
hand-written code and settings files nobody touched. The editor sends each part it did not
edit with the saved form the server gave it, and the save builds the node from that: an
untouched node's code is written byte for byte and its settings file is left as it was.
"""

from __future__ import annotations

import ast
import json
import types
import typing
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from haute._edited_parts import graph_as_read, with_saved_forms
from haute._types import PipelineGraph
from haute.routes._helpers import parse_pipeline_to_graph
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import DocumentRequest, SavePipelineRequest
from tests._unedited_parts import (
    RATED,
    REPO,
    SPELLED,
    BrowserDocument,
    as_javascript_holds_it,
    examples,
    files,
    follow_moves,
    make_project,
    node_at,
    pipeline_file,
    scoped,
)
from tests.conftest import current_source_revision


def _browser_save(document: BrowserDocument, graph: dict[str, Any] | None = None) -> Any:
    """Save *graph* (the document's live graph as a request sends it by default) as the
    editor does."""
    request = SavePipelineRequest.model_validate(document.save_request(graph))
    return SavePipelineService(document.root, pipeline_root=document.py_path.parent).save(request)


def _stepped(payload: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
    return [(ref, node) for ref, node in scoped(payload) if "steps" in node["data"]["config"]]


@pytest.mark.parametrize("source", [None, *examples()])
def test_a_save_of_the_untouched_document_leaves_every_file_as_it_was(
    tmp_path: Path, source: Path | None
) -> None:
    root = make_project(tmp_path, source)
    before = files(root)

    _browser_save(BrowserDocument(root, pipeline_file(root)))

    assert files(root) == before


@pytest.mark.parametrize("source", [None, *examples()])
def test_settings_read_from_a_browser_keep_every_number_the_edit_left(
    tmp_path: Path, source: Path | None
) -> None:
    """CGEN-09: every node's settings read from the request after a browser held them, each
    sent with the saved settings its edit began from, are written as each file holds them."""
    root = make_project(tmp_path, source)
    before = files(root)
    document = BrowserDocument(root, pipeline_file(root))
    sent = document.request_graph()
    read = 0
    for _ref, node in scoped(sent):
        editor = node["data"]["editor"]
        if "saved_settings" in editor:
            editor["settings_edited_from"] = editor.pop("saved_settings")
            read += 1
    assert read

    _browser_save(document, sent)

    assert files(root) == before


def _rated_file(root: Path) -> str:
    return (root / "config" / "rating_step" / "rated.json").read_text(encoding="utf-8")


def test_a_rating_step_whose_output_column_is_renamed_keeps_its_other_numbers(
    tmp_path: Path,
) -> None:
    """CGEN-09: the rename is the edit; the default, the base value and the rows a browser
    holds as whole numbers keep the spelling their file gave them."""
    root = make_project(tmp_path)
    assert '"default_value": 1.0' in _rated_file(root)
    document = BrowserDocument(root, root / "main.py")
    rated = node_at(document.live, (None, "rated"))["data"]["config"]
    rated["combined_outputs"][0]["output_column"] = "rated_premium"
    sent = document.request_graph()
    editor = node_at(sent, (None, "rated"))["data"]["editor"]
    assert "saved_settings" not in editor and "settings_edited_from" in editor

    _browser_save(document, sent)

    written = json.loads(_rated_file(root))
    assert written["combined_outputs"][0] == {
        **RATED["combined_outputs"][0],
        "output_column": "rated_premium",
    }
    assert '"default_value": 1.0' in _rated_file(root)
    assert '"base_value": 100.0' in _rated_file(root)
    assert written["tables"] == RATED["tables"]
    assert [type(value) for _, value in written["tables"][0]["rows"]] == [float, float]


def test_two_rating_tables_swapped_keep_their_numbers(tmp_path: Path) -> None:
    """Codex r4: a table moved is the saved table, its whole-number decimals and a factor
    level past 2**53 spelt as its file spells them."""
    root = make_project(tmp_path)
    tables = [
        {**RATED["tables"][0], "output_column": "band_factor"},
        {
            "factors": ["id"],
            "output_column": "id_factor",
            "default_value": 1.0,
            "rows": [[9007199254740993, 2.0]],
        },
    ]
    # Spelled from tmp_path, the project's root (`make_project`), for the sandbox lint.
    rated = tmp_path / "project" / "config" / "rating_step" / "rated.json"
    assert rated.parent.parent.parent == root
    rated.write_text(json.dumps({**RATED, "tables": tables}), encoding="utf-8")
    document = BrowserDocument(root, root / "main.py")
    _browser_save(document)
    before = _rated_file(root)
    assert "9007199254740993" in before
    document = BrowserDocument(root, root / "main.py")
    config = node_at(document.live, (None, "rated"))["data"]["config"]
    config["tables"] = list(reversed(config["tables"]))

    _browser_save(document)

    assert json.loads(_rated_file(root))["tables"] == list(reversed(tables))
    assert "9007199254740993" in _rated_file(root)
    assert '"default_value": 1.0' in _rated_file(root)


def _rating_defaults(root: Path) -> list[object]:
    """Every Rating Step table's default, as each settings file holds it."""
    found: list[object] = []
    for path in sorted((root / "config" / "rating_step").glob("*.json")):
        found.extend(
            table["default_value"] for table in json.loads(path.read_text("utf-8"))["tables"]
        )
    return found


def _graph_change(
    client: Any, document: BrowserDocument, change: str, graph: dict[str, Any]
) -> dict[str, Any]:
    """The answer to *change* of *graph*, the document as the editor holds it."""
    common = {
        "source_file": "main.py",
        "base_revision": current_source_revision(document.py_path, document.root),
        "pipeline_name": "main",
        "graph": graph,
    }
    if change == "create":
        body = {**common, "name": "grouped", "node_ids": ["rated", "explored"]}
        answer = client.post("/api/submodel/create", json=body)
    elif change == "dissolve":
        answer = client.post("/api/submodel/dissolve", json={**common, "instance_id": "grouped"})
    else:
        answer = client.post("/api/submodel/instance", json={**common, "node_id": "rated"})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_each_graph_change_names_the_nodes_it_moved(client: Any, tmp_path: Path) -> None:
    """The editor finds a moved node's saved baseline through it: Dissolve and Create
    Instance give the node a new id as well as a new place."""
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")

    created = _graph_change(client, document, "create", document.live)
    dissolved = _graph_change(client, document, "dissolve", created["graph"])
    wrapped = _graph_change(client, document, "instance", document.live)

    assert created["moved"] == [
        {"from_submodel": None, "from_node_id": name, "to_submodel": "grouped", "to_node_id": name}
        for name in ("rated", "explored")
    ]
    assert sorted(
        (move["from_submodel"], move["from_node_id"], move["to_node_id"])
        for move in dissolved["moved"]
    ) == [("grouped", name, f"submodel_runtime/grouped/{name}") for name in ("explored", "rated")]
    (move,) = wrapped["moved"]
    assert (move["from_submodel"], move["from_node_id"]) == (None, "rated")
    assert move["to_submodel"] == wrapped["definition_id"]
    held = dict(scoped(wrapped["graph"]))
    assert (move["to_submodel"], move["to_node_id"]) in held


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param(("create",), id="grouped"),
        pytest.param(("create", "dissolve"), id="grouped-then-dissolved"),
        pytest.param(("instance",), id="made-an-instance"),
    ],
)
def test_a_node_a_graph_change_moved_keeps_its_settings_numbers(
    client: Any, tmp_path: Path, changes: tuple[str, ...]
) -> None:
    """Codex r4: a moved node is found in the saved baseline through the place it came
    from, so its settings keep their saved form and the save writes `1.0`, not `1`."""
    root = make_project(tmp_path)
    assert _rating_defaults(root) == [1.0]
    document = BrowserDocument(root, root / "main.py")
    graph, moved_from = document.live, {}
    for change in changes:
        answer = _graph_change(client, document, change, graph)
        graph, moved_from = answer["graph"], follow_moves(moved_from, answer["moved"])

    _browser_save(document, document.request_graph(graph, moved_from=moved_from))

    defaults = _rating_defaults(root)
    assert defaults and all(type(value) is float for value in defaults), defaults


def test_a_moved_node_saved_then_edited_keeps_its_other_numbers(
    client: Any, tmp_path: Path
) -> None:
    """After a save the baseline holds the node where it now is, keeping its identity."""
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    answer = _graph_change(client, document, "create", document.live)
    moved_from = follow_moves({}, answer["moved"])
    _browser_save(document, document.request_graph(answer["graph"], moved_from=moved_from))
    assert _rating_defaults(root) == [1.0]

    saved = BrowserDocument(root, root / "main.py", identities=moved_from)
    rated = node_at(saved.live, ("grouped", "rated"))["data"]["config"]
    rated["combined_outputs"][0]["output_column"] = "rated_premium"
    _browser_save(saved, saved.request_graph(saved.live, moved_from=moved_from))

    assert _rating_defaults(root) == [1.0]
    assert type(_rating_defaults(root)[0]) is float
    assert '"output_column": "rated_premium"' in "".join(
        path.read_text("utf-8") for path in (root / "config" / "rating_step").glob("*.json")
    )


@pytest.mark.parametrize("then", ["dissolve", "undo"])
def test_a_moved_node_saved_then_moved_back_keeps_its_numbers(
    client: Any, tmp_path: Path, then: str
) -> None:
    """Codex r5: grouped and saved, then dissolved, or the grouping undone, and saved again.
    The node keeps the identity the load gave it, and so does the baseline the first save
    made, so it is found by it whatever its place."""
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    answer = _graph_change(client, document, "create", document.live)
    moved_from = follow_moves({}, answer["moved"])
    _browser_save(document, document.request_graph(answer["graph"], moved_from=moved_from))
    saved = BrowserDocument(root, root / "main.py", identities=moved_from)

    if then == "dissolve":
        answer = _graph_change(client, saved, "dissolve", saved.live)
        graph, moved_from = answer["graph"], follow_moves(moved_from, answer["moved"])
    else:
        # Undo restores the graph as it stood before the grouping, its nodes where they were.
        graph, moved_from = document.live, {}
    _browser_save(saved, saved.request_graph(graph, moved_from=moved_from))

    defaults = _rating_defaults(root)
    assert defaults and all(type(value) is float for value in defaults), defaults


def test_without_the_settings_an_edit_began_from_the_browsers_numbers_are_written(
    tmp_path: Path,
) -> None:
    """The control: the same edit read from the browser alone writes its whole numbers."""
    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    rated = node_at(document.live, (None, "rated"))["data"]["config"]
    rated["combined_outputs"][0]["output_column"] = "rated_premium"
    sent = document.request_graph()
    del node_at(sent, (None, "rated"))["data"]["editor"]["settings_edited_from"]

    _browser_save(document, sent)

    assert type(json.loads(_rated_file(root))["tables"][0]["default_value"]) is int


@pytest.mark.parametrize(
    ("sent", "saved", "read"),
    [
        pytest.param(1, 1.0, 1.0, id="a-whole-decimal"),
        pytest.param(9007199254740992, 9007199254740993, 9007199254740993, id="a-long-integer"),
        pytest.param(0, -0.0, -0.0, id="negative-zero"),
        pytest.param(1e20, 1e20, 1e20, id="an-exponent"),
        pytest.param(2, 1.0, 2, id="a-changed-number"),
        pytest.param(1.5, 1.5, 1.5, id="a-fraction"),
        pytest.param(1, True, 1, id="a-number-is-no-boolean"),
        pytest.param(True, 1, True, id="a-boolean-is-no-number"),
        pytest.param("1.0", 1.0, "1.0", id="text-is-no-number"),
        pytest.param(
            {"a": 1, "b": 2, "new": 3},
            {"a": 1.0, "b": 2.5, "old": 4.0},
            {"a": 1.0, "b": 2, "new": 3},
            id="an-object-by-key",
        ),
        pytest.param(
            [["new", 1], ["young", 1.2], ["experienced", 1]],
            [["young", 1.2], ["experienced", 1.0]],
            [["new", 1], ["young", 1.2], ["experienced", 1.0]],
            id="a-row-added-above",
        ),
        pytest.param(
            [["experienced", 1]],
            [["young", 1.2], ["experienced", 1.0]],
            [["experienced", 1.0]],
            id="a-row-removed",
        ),
        pytest.param(
            [["experienced", 1], ["young", 1.2]],
            [["young", 1.2], ["experienced", 1.0]],
            [["experienced", 1.0], ["young", 1.2]],
            id="rows-moved",
        ),
        pytest.param(
            [["experienced", 1], ["young", 3]],
            [["young", 1.2], ["experienced", 1.0]],
            [["experienced", 1.0], ["young", 3]],
            id="a-row-moved-and-another-changed",
        ),
        pytest.param(
            [["young", 3], ["experienced", 1]],
            [["young", 1.2], ["experienced", 1.0]],
            [["young", 3], ["experienced", 1.0]],
            id="a-row-changed",
        ),
        pytest.param(
            [{"output_column": "renamed", "base_value": 100}],
            [{"output_column": "rated_result", "base_value": 100.0}],
            [{"output_column": "renamed", "base_value": 100.0}],
            id="a-changed-element-walked-against-the-one-it-replaced",
        ),
    ],
)
def test_a_number_the_edit_left_is_spelt_as_the_settings_it_began_from(
    sent: Any, saved: Any, read: Any
) -> None:
    from haute._edited_parts import spelled_as

    got = spelled_as(sent, saved)
    assert json.dumps(got) == json.dumps(read)
    assert type(got) is type(read)


def test_an_edit_carries_one_form_of_its_settings() -> None:
    """Saved settings say the settings were not edited; the settings an edit began from say
    they were. Both, or either not an object of settings, is refused."""
    from haute._edited_parts import config_as_read

    with pytest.raises(ValueError, match="both saved settings and the settings an edit"):
        config_as_read(
            "polars",
            {"code": "x"},
            saved_code=None,
            saved_settings="{}",
            settings_edited_from="{}",
            node="'n'",
        )
    with pytest.raises(ValueError, match="settings its edit began from of node 'n' are not"):
        config_as_read(
            "polars",
            {"code": "x"},
            saved_code=None,
            saved_settings=None,
            settings_edited_from="[]",
            node="'n'",
        )
    assert config_as_read(
        "ratingStep",
        {"default": 1, "name": "x"},
        saved_code=None,
        saved_settings=None,
        settings_edited_from='{"default": 1.0, "name": "y"}',
        node="'n'",
    ) == {"default": 1.0, "name": "x"}


def test_the_hand_written_file_holds_values_the_browser_changes(tmp_path: Path) -> None:
    """The fixture is the case: its steps and its settings do not survive a browser."""
    root = make_project(tmp_path)
    graph = parse_pipeline_to_graph(root / "main.py", project_root=root)
    payload = graph.model_dump(mode="json", by_alias=True)
    held = as_javascript_holds_it(payload)

    def changed(part: str) -> set[str]:
        def text(node: dict[str, Any]) -> str:
            config = node["data"]["config"]
            if part == "steps":
                return json.dumps(config.get("steps"))
            return json.dumps({k: v for k, v in config.items() if k not in {"steps", "code"}})

        exact = {ref: text(node) for ref, node in scoped(payload)}
        return {ref[1] for ref, node in scoped(held) if text(node) != exact[ref]}

    assert {"relativity", "doubled", "one_id", "rated"} <= changed("steps")
    assert changed("settings") == {"rated"}
    assert "steps" in node_at(payload, (None, "years"))["data"]["config"]


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
    root = make_project(tmp_path)
    before = files(root)
    document = BrowserDocument(root, root / "main.py")
    sent = document.request_graph()
    for _ref, node in _stepped(sent):
        if node["data"]["nodeType"] == "polars":
            mutate(node["data"]["config"]["steps"])

    _browser_save(document, sent)

    assert files(root) == before


def test_a_setting_changed_in_transit_does_not_reach_an_unedited_nodes_file(
    tmp_path: Path,
) -> None:
    root = make_project(tmp_path)
    before = files(root)
    document = BrowserDocument(root, root / "main.py")
    sent = document.request_graph()
    rated = node_at(sent, (None, "rated"))["data"]["config"]
    rated["tables"][0]["default_value"] = 7
    rated["combined_outputs"][0]["output_column"] = "renamed"

    _browser_save(document, sent)

    assert files(root) == before


def test_an_edited_node_is_written_from_its_steps_and_no_other_node_is(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    original = (root / "main.py").read_text(encoding="utf-8")
    document = BrowserDocument(root, root / "main.py")
    steps = node_at(document.live, (None, "relativity"))["data"]["config"]["steps"]
    steps.append({"id": "added", "kind": "free_code", "code": "df = df.head(5)"})

    response = _browser_save(document)

    saved = (root / "main.py").read_text(encoding="utf-8")
    assert "df = df.head(5)" in saved
    # The edited steps are rendered, each number as it was written (CGEN-09).
    assert 'pl.lit(1.0).alias("relativity")' in saved
    for untouched in SPELLED[1:]:
        assert untouched in saved
    assert "    one_id = 365\n" in saved
    assert original != saved
    reloaded = parse_pipeline_to_graph(root / "main.py", project_root=root)
    relativity = next(n for n in reloaded.nodes if n.id == "relativity")
    assert [(p.submodel, p.node_id, p.code, p.settings) for p in response.saved_parts] == [
        (None, "relativity", relativity.data.config["code"], None)
    ]


def _make_unfinished(root: Path) -> Path:
    """*root*'s hand-written project with ``relativity`` saved holding an unfinished step:
    one that joins an input it is not connected to."""
    document = BrowserDocument(root, root / "main.py")
    node_at(document.live, (None, "relativity"))["data"]["config"]["steps"].append(
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

    response = _browser_save(document)
    assert any("incomplete step list" in warning for warning in response.warnings)
    assert response.saved_parts[0].code != ""
    sidecar = json.loads((root / "main.haute.json").read_text(encoding="utf-8"))
    assert "relativity" in sidecar["unfinished_steps"]
    return root


def test_an_unedited_node_saved_with_unfinished_steps_keeps_its_placeholder(
    tmp_path: Path,
) -> None:
    root = _make_unfinished(make_project(tmp_path))
    assert "This node's steps are incomplete" in (root / "main.py").read_text(encoding="utf-8")
    before = files(root)

    _browser_save(BrowserDocument(root, root / "main.py"))

    assert files(root) == before


def test_an_earlier_graph_is_not_given_the_files_record_of_unfinished_steps(
    tmp_path: Path,
) -> None:
    """A graph that does not descend from the file (an undo's earlier commit) carries its own
    code; the record the file holds now is not its."""
    root = make_project(tmp_path)
    earlier = parse_pipeline_to_graph(root / "main.py", project_root=root)
    before = (root / "main.py").read_text(encoding="utf-8")
    _make_unfinished(root)
    request = SavePipelineRequest.model_validate(
        {
            "name": "main",
            "graph": with_saved_forms(earlier, earlier),
            "source_file": "main.py",
            "base_revision": current_source_revision(root / "main.py", root),
        }
    )

    SavePipelineService(root).save(request)

    assert (root / "main.py").read_text(encoding="utf-8") == before


def _node(config: dict[str, Any], **editor: str) -> dict[str, Any]:
    return {"id": "t", "data": {"nodeType": "polars", "config": config, "editor": editor}}


def test_a_saved_code_on_a_node_that_holds_no_step_list_is_refused() -> None:
    graph = {"nodes": [_node({"code": "x"}, saved_code="x")]}
    with pytest.raises(ValueError, match="holds no step list"):
        graph_as_read(graph)


@pytest.mark.parametrize(
    "saved", ["[1]", '{"code": "df = df"}', '{"steps": []}', "{not json"], ids=str
)
def test_saved_settings_that_are_not_settings_are_refused(saved: str) -> None:
    from pydantic import ValidationError

    payload = {"graph": {"nodes": [_node({"code": "x"}, saved_settings=saved)]}}
    with pytest.raises(ValidationError, match="saved settings of node 't'"):
        SavePipelineRequest.model_validate({**payload, "base_revision": None})


@pytest.mark.parametrize(
    ("editor", "message"),
    [
        pytest.param({"saved_code": "x", "saved_steps": "[]"}, "both a saved code", id="both"),
        pytest.param(
            {"saved_steps": '{"id": "s"}'},
            "saved steps of node 't' are not a list",
            id="steps-list",
        ),
        pytest.param(
            {"saved_steps": "[not json"}, "saved steps of node 't' are not JSON", id="steps-json"
        ),
    ],
)
def test_saved_steps_that_are_not_one_list_are_refused(
    editor: dict[str, str], message: str
) -> None:
    steps = [{"id": "s", "kind": "source", "input": "a"}]
    with pytest.raises(ValueError, match=message):
        graph_as_read({"nodes": [_node({"steps": steps, "code": ""}, **editor)]})


def test_a_node_is_never_built_with_a_saved_form() -> None:
    """A request model that went round ``DocumentRequest`` fails: the node refuses it."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="DocumentRequest"):
        PipelineGraph.model_validate({"nodes": [_node({"code": "x"}, saved_settings="{}")]})


def test_an_empty_saved_code_leaves_the_steps_their_only_copy() -> None:
    steps = [{"id": "s", "kind": "source", "input": "a"}]
    read = graph_as_read({"nodes": [_node({"steps": steps, "code": ""}, saved_code="")]})
    assert read.graph["nodes"][0]["data"]["config"]["steps"] == steps
    assert read.edited_steps == frozenset()


@pytest.mark.parametrize(
    "graph",
    [
        pytest.param("not a graph", id="graph"),
        pytest.param({"nodes": "not a list"}, id="nodes"),
        pytest.param({"nodes": ["not a node"]}, id="node"),
        pytest.param({"nodes": [{"id": "t", "data": "not data"}]}, id="data"),
        pytest.param(
            {"nodes": [{"id": "t", "data": {"editor": {"saved_code": 3}}}]}, id="saved-form"
        ),
        pytest.param({"nodes": [{"id": "t", "data": {"config": "not a config"}}]}, id="config"),
        pytest.param({"nodes": [], "submodels": {"s": {"graph": "not a graph"}}}, id="definition"),
    ],
)
def test_a_malformed_graph_is_refused_by_the_requests_own_validation(graph: Any) -> None:
    """The parts are read before the request's shape is checked: what is not a graph is
    left for that check to refuse, never an error of the rule's own."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SavePipelineRequest.model_validate({"graph": graph, "base_revision": None})
    with pytest.raises(ValidationError):
        SavePipelineRequest.model_validate(["not a request"])


def test_a_record_that_does_not_render_is_read_for_a_node_that_carries_no_code() -> None:
    """A recorded list that does not render at all loads with no code, so only a node that
    carries none is written from it."""
    from haute._edited_parts import with_recorded_unfinished_steps

    record = [{"id": "filter-1", "kind": "filter", "match": "all", "conditions": []}]

    assert with_recorded_unfinished_steps("polars", {"code": ""}, record, edited=False) == {
        "steps": record
    }
    assert (
        with_recorded_unfinished_steps("polars", {"code": "df = x"}, record, edited=False) is None
    )


def test_a_server_side_writer_sends_saved_forms_for_what_it_did_not_change(
    tmp_path: Path,
) -> None:
    root = make_project(tmp_path)
    before = parse_pipeline_to_graph(root / "main.py", project_root=root)
    after = PipelineGraph.model_validate(before.model_dump(mode="json", by_alias=True))
    node = next(n for n in after.nodes if n.id == "doubled")
    steps = [*node.data.config["steps"], {"id": "x", "kind": "free_code", "code": "df = df"}]
    after.nodes[after.nodes.index(node)] = node.with_config({**node.data.config, "steps": steps})
    rated = next(n for n in after.nodes if n.id == "rated")
    tables = [{**rated.data.config["tables"][0], "default_value": 1}]
    after.nodes[after.nodes.index(rated)] = rated.with_config(
        {**rated.data.config, "tables": tables}
    )

    read = SavePipelineRequest.model_validate(
        {"graph": with_saved_forms(after, before), "base_revision": None}
    )

    assert read.edited_steps == {(None, "doubled")}
    # ``1`` is not ``1.0``: the writer's values are compared as their text.
    assert read.edited_settings == {(None, "rated")}
    unchanged = SavePipelineRequest.model_validate(
        {"graph": with_saved_forms(before, before), "base_revision": None}
    )
    assert unchanged.edited_steps == unchanged.edited_settings == frozenset()


def _scoped_project(tmp_path: Path, body: str, sidecar: dict[str, Any] | None = None) -> Any:
    """A degraded project whose ``source_b`` (or ``transform``) is saved on its own."""
    from haute._config_io import node_config_json
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._types import NodeType
    from tests._project import use_project

    root = tmp_path
    (root / "haute.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (root / "main.py").write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a(): ...\n' + body,
        encoding="utf-8",
        newline="\n",
    )
    ready = {"input_type": "file", "format": "parquet", "mode": "scan", "arguments": {}}
    # A key the node type does not declare leaves ``source_a`` unavailable, the document
    # degraded and the other node saved on its own.
    broken = {**ready, "path": "", "cacheMode": "snapshot"}
    (root / "a.json").write_text(json.dumps(broken), encoding="utf-8")
    # As a save writes it, so a save that keeps the node's settings keeps its bytes.
    (root / "b.json").write_text(
        node_config_json(
            NodeType.DATA_INPUT,
            {**ready, "path": "data/b.parquet", "arguments": {"n_rows": 10.0}},
            node_label="source_b",
        ),
        encoding="utf-8",
    )
    if sidecar is not None:
        (root / "main.haute.json").write_text(json.dumps(sidecar), encoding="utf-8")
    use_project(root)
    return load_pipeline_editor_document(root / "main.py", project_root=root)


def _scoped_save(
    root: Path, document: Any, authored_id: str, config: dict[str, Any], **saved: Any
) -> None:
    """Save *config* for the node *authored_id* of *document* on its own, as the editor
    sends it: after a browser has held it, with the saved forms of the parts not edited."""
    from haute._pipeline_repair_actions import apply_scoped_node_save
    from haute.schemas import PipelineNodeSaveRequest

    target = next(node for node in document.nodes if node.authored_id == authored_id)
    assert target.scoped_editable
    apply_scoped_node_save(
        project_root=root,
        request=PipelineNodeSaveRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=target.source_file,
            target_recovery_id=target.recovery_id,
            config=as_javascript_holds_it(config),
            **saved,
        ),
    )


_HOOK_B = (
    '@pipeline.data_input(config="b.json")\n'
    "def source_b(df: pl.LazyFrame) -> pl.LazyFrame:\n"
    '    df = df.with_columns(pl.lit(1.0).alias("x"))\n'
    "    return df\n"
)


def test_a_scoped_save_keeps_an_unedited_nodes_code(tmp_path: Path) -> None:
    document = _scoped_project(tmp_path, _HOOK_B)
    target = next(node for node in document.nodes if node.authored_id == "source_b")
    assert isinstance(target.config.get("steps"), list)

    _scoped_save(
        tmp_path,
        document,
        "source_b",
        {**target.config, "path": "data/other.parquet"},
        saved_code=target.editor.saved_code,
    )

    assert 'pl.lit(1.0).alias("x")' in (tmp_path / "main.py").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))["path"] == (
        "data/other.parquet"
    )


def test_a_scoped_save_keeps_an_unedited_nodes_settings_file(tmp_path: Path) -> None:
    document = _scoped_project(tmp_path, _HOOK_B)
    target = next(node for node in document.nodes if node.authored_id == "source_b")
    settings = (tmp_path / "b.json").read_bytes()
    assert b"10.0" in settings
    steps = [*target.config["steps"], {"id": "y", "kind": "free_code", "code": "df = df"}]

    _scoped_save(
        tmp_path,
        document,
        "source_b",
        {**target.config, "steps": steps},
        saved_settings=target.editor.saved_settings,
    )

    assert "df = df\n" in (tmp_path / "main.py").read_text(encoding="utf-8")
    assert (tmp_path / "b.json").read_bytes() == settings


#: The record a hand edit left behind: the steps of the code, lifted as if the node read no
#: input, so it renders as the code does but binds a variable named after the node's input.
_RECORD_WITHOUT_INPUTS = [
    {"id": "source-1", "kind": "source", "input": "source_a"},
    {
        "id": "variable-2",
        "kind": "variable",
        "name": "source_a",
        "value": {"kind": "literal", "type": "number", "value": 365},
    },
    {
        "id": "with_column-3",
        "kind": "with_column",
        "name": "years",
        "expr": {
            "type": "binary",
            "left": {"kind": "column", "name": "days"},
            "op": "/",
            "right": {"kind": "variable", "name": "source_a"},
        },
    },
]


def test_a_scoped_save_never_reads_a_record_a_hand_edit_left_behind(tmp_path: Path) -> None:
    """Working code written by hand over the placeholder leaves the record of its unfinished
    steps in ``.haute.json``; the load ignores it, and so does a scoped save of the node with
    nothing edited, even when the record renders as the code does without the node's inputs."""
    code = "\n".join(
        [
            "df = source_a",
            "source_a = 365",
            'df = df.with_columns((pl.col("days") / source_a).alias("years"))',
        ]
    )
    body = "".join(f"    {line}\n" for line in code.split("\n"))
    document = _scoped_project(
        tmp_path,
        "@pipeline.polars\n"
        f"def transform(source_a: pl.LazyFrame) -> pl.LazyFrame:\n{body}    return df\n",
        {"unfinished_steps": {"transform": _RECORD_WITHOUT_INPUTS}},
    )
    target = next(node for node in document.nodes if node.authored_id == "transform")

    _scoped_save(
        tmp_path,
        document,
        "transform",
        target.config,
        saved_code=target.editor.saved_code,
        saved_settings=target.editor.saved_settings,
    )

    saved = (tmp_path / "main.py").read_text(encoding="utf-8")
    assert "    source_a = 365\n" in saved
    assert "NotImplementedError" not in saved


def test_a_scoped_save_keeps_unfinished_steps_behind_an_inline_placeholder(
    tmp_path: Path,
) -> None:
    """A placeholder on its function's own line is the placeholder all the same: an unedited
    scoped save writes the node from its record, whose join names an input it is not
    connected to (it renders, so the loaded node carries code), and keeps it unfinished."""
    from haute._code_extraction import INCOMPLETE_STEPS_MESSAGE

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
    document = _scoped_project(
        tmp_path,
        "@pipeline.polars\n"
        "def transform(source_a: pl.LazyFrame) -> pl.LazyFrame: "
        f'raise NotImplementedError("{INCOMPLETE_STEPS_MESSAGE}")\n',
        {"unfinished_steps": {"transform": record}},
    )
    target = next(node for node in document.nodes if node.authored_id == "transform")
    assert target.config is not None and target.config.get("code")

    _scoped_save(
        tmp_path,
        document,
        "transform",
        target.config,
        saved_code=target.editor.saved_code,
        saved_settings=target.editor.saved_settings,
    )

    assert INCOMPLETE_STEPS_MESSAGE in (tmp_path / "main.py").read_text(encoding="utf-8")
    sidecar = json.loads((tmp_path / "main.haute.json").read_text(encoding="utf-8"))
    assert sidecar["unfinished_steps"] == {"transform": record}


def test_a_scoped_save_refuses_a_saved_code_for_a_node_without_steps(tmp_path: Path) -> None:
    from haute._pipeline_repair import PipelineRepairError

    document = _scoped_project(
        tmp_path, '@pipeline.data_input(config="b.json")\ndef source_b(): ...\n'
    )
    target = next(node for node in document.nodes if node.authored_id == "source_b")
    config = {key: value for key, value in target.config.items() if key != "steps"}

    with pytest.raises(PipelineRepairError, match="holds no step list") as refused:
        _scoped_save(tmp_path, document, "source_b", config, saved_code="df = df")
    assert refused.value.status_code == 422


# ---------------------------------------------------------------------------
# The guards: every writer of a pipeline's code reads its request through the rule, and
# every request model that carries the document derives from the one that applies it
# ---------------------------------------------------------------------------

#: The functions outside ``haute/codegen.py`` that generate a node's or a pipeline's code,
#: each of which takes its configs from a request read through ``DocumentRequest`` (or
#: ``PipelineNodeSaveRequest``, read through the same rule), or builds them on the server.
#: A new writer must be listed here, after it reads only the parts its request names.
CODE_WRITERS = {
    "assistant/_application.py": {"_prove_steps_survive_save"},
    "routes/_save_pipeline.py": {"_generate_code"},
    "_pipeline_repair_actions.py": {"_reset_node"},
}

_GENERATORS = {"graph_to_code", "graph_to_code_multi", "_node_to_code"}


def _writers() -> dict[str, set[str]]:
    from tests._source_files import source_files

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
def test_every_code_writer_reads_only_the_parts_its_request_names() -> None:
    from haute.schemas import PipelineNodeSaveRequest

    assert _writers() == CODE_WRITERS
    assert issubclass(SavePipelineRequest, DocumentRequest)
    assert {"saved_code", "saved_settings"} <= set(PipelineNodeSaveRequest.model_fields)


def _models_in(annotation: Any) -> list[type[Any]]:
    """The Pydantic models an annotation names, through unions, lists and mappings."""
    from pydantic import BaseModel

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    found: list[type[Any]] = []
    for argument in typing.get_args(annotation):
        found.extend(_models_in(argument))
    return found


def _carries_the_document(annotation: Any) -> bool:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(_carries_the_document(argument) for argument in typing.get_args(annotation))
    return isinstance(annotation, type) and issubclass(annotation, PipelineGraph)


def _api_routes(routes: list[Any]) -> Iterator[Any]:
    """Every API route among *routes*, through the routers they include."""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _api_routes(included.routes)


def _requests_round_the_rule(app: Any) -> list[str]:
    """Each request body model reachable from *app*'s routes that has a field carrying the
    document and does not derive from ``DocumentRequest``, and each route that takes the
    document as a body of its own."""
    offenders: set[str] = set()
    seen: set[type[Any]] = set()

    def visit(model: type[Any], where: str) -> None:
        if model in seen:
            return
        seen.add(model)
        for name, field in model.model_fields.items():
            if _carries_the_document(field.annotation) and not issubclass(model, DocumentRequest):
                offenders.add(f"{where}: {model.__name__}.{name}")
            for nested in _models_in(field.annotation):
                if not issubclass(nested, PipelineGraph):
                    visit(nested, where)

    for route in _api_routes(app.routes):
        for param in route.dependant.body_params:
            annotation = param.field_info.annotation
            if _carries_the_document(annotation):
                offenders.add(f"{route.path}: {param.name}")
            for model in _models_in(annotation):
                visit(model, route.path)
    return sorted(offenders)


@pytest.mark.meta
def test_every_request_that_carries_the_document_reads_it_through_the_rule() -> None:
    from tests._project import project_app

    app = project_app()
    assert _requests_round_the_rule(app) == []
    carried = {
        route.path
        for route in _api_routes(app.routes)
        if any(
            issubclass(model, DocumentRequest)
            for param in route.dependant.body_params
            for model in _models_in(param.field_info.annotation)
        )
    }
    assert {
        "/api/pipeline/save",
        "/api/pipeline/preview",
        "/api/pipeline/trace",
        "/api/explore/pivots/run",
        "/api/modelling/estimate",
        "/api/submodel/create",
    } <= carried


class _RoundTheRule(BaseModel):
    """A request model that carries the document without deriving from DocumentRequest."""

    graph: PipelineGraph


def test_the_guard_finds_a_request_that_goes_round_the_rule() -> None:
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/round")
    def _round(body: _RoundTheRule) -> None: ...

    assert _requests_round_the_rule(app) == ["/round: _RoundTheRule.graph"]


def test_a_repeat_that_reads_other_parts_is_another_save(tmp_path: Path) -> None:
    """Two requests that read the same graph, one sending a node's steps with an empty saved
    code (its steps kept as their only copy, not edited) and one without (its steps read),
    are two saves: a repeat under one's operation with the other's parts is refused."""
    import copy

    from haute._save_records import SaveOperationReusedError, save_request_digest

    root = make_project(tmp_path)
    document = BrowserDocument(root, root / "main.py")
    kept = document.request_graph()
    relativity = node_at(kept, (None, "relativity"))["data"]
    relativity["config"]["code"] = ""
    relativity["editor"]["saved_code"] = ""
    read = copy.deepcopy(kept)
    del node_at(read, (None, "relativity"))["data"]["editor"]["saved_code"]
    first = SavePipelineRequest.model_validate(
        {**document.save_request(kept), "operation": "operation-1"}
    )
    repeat = SavePipelineRequest.model_validate(
        {**document.save_request(read), "operation": "operation-1"}
    )
    assert first.model_dump_json() == repeat.model_dump_json()
    assert (None, "relativity") in repeat.edited_steps - first.edited_steps
    assert save_request_digest(first) != save_request_digest(repeat)

    SavePipelineService(root).save(first)
    with pytest.raises(SaveOperationReusedError):
        SavePipelineService(root).save(repeat)
