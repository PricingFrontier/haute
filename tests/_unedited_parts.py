"""The editor's document as a browser holds it and sends it (CGEN-13).

A browser has one kind of number: ``JSON.parse`` gives a double, written as an integer
whenever it is a whole number, so ``1.0`` comes back ``1`` and an integer past 2**53 comes
back as its nearest double. :class:`BrowserDocument` loads a pipeline as the editor does,
the live graph and the saved baseline both after that round trip, and builds what a
request that carries the document sends as ``frontend/src/utils/documentRequest.ts``
builds it: each part of a node that is as the baseline holds it is sent with the saved
form the server gave it, every other part as the browser holds it. :data:`HAND_WRITTEN` is a
pipeline whose values a browser changes, and :func:`make_project` makes a project of it or
of a shipped example in the layout a save writes.
"""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._edited_parts import SAVED_FORM_KEYS, STEPS_KEYS, NodeRef
from tests._project import use_project

REPO = Path(__file__).resolve().parents[1]
SHIPPED_EXAMPLES = REPO / "src" / "haute" / "assistant" / "assets" / "examples"

#: Hand-written nodes whose values a browser changes: decimals spelled as whole numbers
#: and an integer past 2**53 in steps, an input name the code rebinds (``CGEN-10``: the
#: lift accepts it, a render against the input names refuses it), and a Rating Step whose
#: settings file holds whole-number decimals beside a step that does.
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


@pipeline.rating_step(config="config/rating_step/rated.json")
def rated(df: pl.LazyFrame) -> pl.LazyFrame:
    df = df.with_columns(pl.lit(1.0).alias("loading"))
    return df


@pipeline.explore(config="config/explore/explored.json")
def explored(doubled): ...


@pipeline.modelling(config="config/modelling/train.json")
def train(doubled): ...


pipeline.connect("quotes", "relativity")
pipeline.connect("relativity", "doubled")
pipeline.connect("doubled", "one_id")
pipeline.connect("one_id", "years")
pipeline.connect("doubled", "rated")
pipeline.connect("doubled", "explored")
pipeline.connect("doubled", "train")
'''

#: The Rating Step's settings, whole-number decimals among them.
RATED = {
    "combined_outputs": [
        {"output_column": "rated_result", "operation": "multiply", "base_value": 100.0}
    ],
    "tables": [
        {
            "factors": ["band"],
            "output_column": "band_factor",
            "default_value": 1.0,
            "rows": [["young", 1.2], ["experienced", 1.0]],
        }
    ],
}

#: The Model Training node's settings: it trains on the column ``pl.lit(1.0)`` makes.
TRAIN = {
    "name": "relativity_model",
    "target": "value",
    "feature_columns": ["relativity", "days"],
    "algorithm": "catboost",
    "task": "regression",
    "loss_function": "RMSE",
    "params": {"iterations": 2, "depth": 1, "verbose": False},
    "evaluation": {
        "schema_version": 1,
        "strategy": "random",
        "seed": 42,
        "validation": {"method": "single", "size": 0.5},
    },
    "metrics": ["rmse"],
}

#: The spellings of the hand-written file that a browser's numbers would change.
SPELLED = ("pl.lit(1.0)", "* 2.0", "fill_null(0.0)", "== 9007199254740993")


def examples() -> list[Any]:
    """The reference pipeline and every shipped example, by folder."""
    sources = [REPO / "examples" / "reference"]
    sources.extend(sorted(path for path in SHIPPED_EXAMPLES.iterdir() if path.is_dir()))
    return [pytest.param(source, id=source.name) for source in sources]


def pipeline_file(root: Path) -> Path:
    return root / "pipeline.py" if (root / "pipeline.py").is_file() else root / "main.py"


def files(root: Path) -> dict[str, bytes]:
    """The project's files, without git's and Haute's own state."""
    from tests._source_files import source_files

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in source_files(root, suffix=None)
        if not {".git", ".haute"} & set(path.relative_to(root).parts)
    }


def make_project(tmp_path: Path, source: Path | None = None) -> Path:
    """A git project of *source* (the hand-written pipeline when ``None``), saved once by
    the server into the layout a save writes, by a save no browser has held: what a
    request of the same document through a browser must then leave as it is."""
    from haute._edited_parts import with_saved_forms
    from haute._git import set_working_branch
    from haute.routes._helpers import parse_pipeline_to_graph
    from haute.routes._save_pipeline import SavePipelineService
    from haute.schemas import SavePipelineRequest
    from tests._git_helpers import init_repo
    from tests.conftest import current_source_revision

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
        rated = root / "config" / "rating_step" / "rated.json"
        rated.parent.mkdir(parents=True)
        rated.write_text(json.dumps(RATED), encoding="utf-8")
        explored = root / "config" / "explore" / "explored.json"
        explored.parent.mkdir(parents=True)
        explored.write_text("{}", encoding="utf-8")
        train = root / "config" / "modelling" / "train.json"
        train.parent.mkdir(parents=True)
        train.write_text(json.dumps(TRAIN), encoding="utf-8")
        (root / "data").mkdir()
        pl.DataFrame(
            {
                "id": [1, 9007199254740993, 9007199254740992],
                "value": [10, None, 30],
                "days": [365, 730, 100],
                "band": ["young", "experienced", "young"],
            }
        ).write_parquet(root / "data" / "quotes.parquet")
    else:
        shutil.copytree(source, root, ignore=shutil.ignore_patterns("__pycache__"))
    if not (root / "haute.toml").exists():
        (root / "haute.toml").write_text("", encoding="utf-8", newline="\n")
    init_repo(root)
    set_working_branch("pricing/test-user/steps", root, create=True, cwd=root)
    use_project(root)
    py_path = pipeline_file(root)
    graph = parse_pipeline_to_graph(py_path, project_root=root)
    SavePipelineService(root, pipeline_root=py_path.parent).save(
        SavePipelineRequest.model_validate(
            {
                "name": graph.pipeline_name or "main",
                "description": graph.pipeline_description or "",
                "graph": with_saved_forms(graph, graph),
                "preamble": graph.preamble,
                "preserved_blocks": graph.preserved_blocks,
                "source_file": py_path.relative_to(root).as_posix(),
                "base_revision": current_source_revision(py_path, root),
            }
        )
    )
    if source is None:
        written = py_path.read_text(encoding="utf-8")
        for spelled in SPELLED:
            assert spelled in written
        assert "    one_id = 365\n" in written
        assert '"default_value": 1.0' in (root / "config" / "rating_step" / "rated.json").read_text(
            encoding="utf-8"
        )
    return root


def as_javascript_holds_it(value: Any) -> Any:
    """*value* after ``JSON.stringify`` and ``JSON.parse`` in a browser: every number a
    double, written as its shortest digits, which Python reads back as an integer whenever
    it is a whole number below 10**21 (``36028797018963952`` is written
    ``36028797018963950``)."""

    def number(token: str) -> int | float:
        held = float(token)
        return int(Decimal(repr(held))) if held.is_integer() and abs(held) < 1e21 else held

    return json.loads(json.dumps(value), parse_int=number, parse_float=number)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def scoped(payload: dict[str, Any]) -> Iterator[tuple[NodeRef, dict[str, Any]]]:
    """Every node of a graph *payload* and of its submodel definitions, by scope and id."""
    for node in payload.get("nodes") or []:
        yield (None, node["id"]), node
    for definition_id, definition in (payload.get("submodels") or {}).items():
        for node in definition["graph"]["nodes"]:
            yield (definition_id, node["id"]), node


def node_at(payload: dict[str, Any], ref: NodeRef) -> dict[str, Any]:
    return dict(scoped(payload))[ref]


class BrowserDocument:
    """The pipeline *py_path* as the editor holds it after loading it: its live graph,
    as the editor edits it, and its saved baseline, with each node's saved forms."""

    def __init__(
        self, root: Path, py_path: Path, *, identities: Mapping[NodeRef, NodeRef] | None = None
    ) -> None:
        """*identities* are those the editor's nodes kept through graph changes it saved
        (``data._savedAddress``): a save makes the baseline from the document, so each of the
        baseline's nodes keeps its identity too."""
        from haute._pipeline_recovery import load_pipeline_editor_document
        from haute.routes._helpers import parse_pipeline_to_graph

        self.root = root
        self.py_path = py_path
        self.identities: dict[NodeRef, NodeRef] = dict(identities or {})
        self.graph = parse_pipeline_to_graph(py_path, project_root=root)
        live = self.graph.model_dump(mode="json", by_alias=True)
        for _ref, node in scoped(live):
            for key in SAVED_FORM_KEYS:
                node["data"]["editor"].pop(key, None)
        #: The graph as the browser holds it, for a test to edit as the editor would.
        self.live: dict[str, Any] = as_javascript_holds_it(live)
        document = load_pipeline_editor_document(py_path, project_root=root)
        held = as_javascript_holds_it(document.model_dump(mode="json", by_alias=True))
        #: Each node's config and editor state as the baseline holds them, by scope and id.
        self.baseline: dict[NodeRef, dict[str, Any]] = {}
        for node in held["nodes"]:
            self.baseline[(None, node["recovery_id"])] = node
        for definition_id, definition in (held.get("submodels") or {}).items():
            for node in definition["graph"]["nodes"]:
                self.baseline[(definition_id, node["recovery_id"])] = node

    def request_graph(
        self,
        live: dict[str, Any] | None = None,
        *,
        moved_from: Mapping[NodeRef, NodeRef] | None = None,
    ) -> dict[str, Any]:
        """*live* (the browser's live graph by default) as a request sends it.

        *moved_from* gives, for a node a graph change moved, the place the baseline holds it
        (``data._savedAddress`` in the editor, :func:`follow_moves`): a node's baseline node
        is the one of its type at its own place, else there."""
        from haute._edited_parts import node_inputs
        from haute._polars_steps import stepped_surface_allows_input_references
        from haute._types import PipelineGraph

        sent = copy.deepcopy(self.live if live is None else live)
        inputs = node_inputs(PipelineGraph.model_validate(sent))
        held_inputs = node_inputs(self.graph)
        for ref, node in scoped(sent):
            data = node["data"]
            editor = data.setdefault("editor", {})
            for key in SAVED_FORM_KEYS:
                editor.pop(key, None)
            # A node's baseline node is the one of its type and identity, the one at its own
            # place if there are two (``documentRequest.baselineNodeFor``).
            identity = (moved_from or {}).get(ref, ref)
            matches = [
                place
                for place, found in self.baseline.items()
                if self.identities.get(place, place) == identity
                and found.get("node_type") == data.get("nodeType")
            ]
            held_ref = ref if ref in matches else (matches[0] if matches else ref)
            held = self.baseline.get(held_ref) if matches else None
            if held is None or held.get("config") is None:
                continue
            config = data.get("config") or {}
            saved = held["editor"]
            steps = config.get("steps")
            if (
                isinstance(steps, list)
                and _canonical(steps) == _canonical(held["config"].get("steps"))
                and (
                    not stepped_surface_allows_input_references(data["nodeType"])
                    or inputs[ref] == held_inputs.get(held_ref)
                )
            ):
                if isinstance(saved.get("saved_code"), str):
                    editor["saved_code"] = saved["saved_code"]
                    config["code"] = saved["saved_code"]
                elif isinstance(saved.get("saved_steps"), str):
                    editor["saved_steps"] = saved["saved_steps"]
            settings = {k: v for k, v in config.items() if k not in STEPS_KEYS}
            held_settings = {k: v for k, v in held["config"].items() if k not in STEPS_KEYS}
            if isinstance(saved.get("saved_settings"), str):
                if _canonical(settings) == _canonical(held_settings):
                    editor["saved_settings"] = saved["saved_settings"]
                else:
                    editor["settings_edited_from"] = saved["saved_settings"]
        return sent

    def save_request(self, graph: dict[str, Any] | None = None) -> dict[str, Any]:
        """The save of *graph* (the live graph as a request sends it by default)."""
        from tests.conftest import current_source_revision

        return {
            "name": self.graph.pipeline_name or "main",
            "description": self.graph.pipeline_description or "",
            "graph": self.request_graph() if graph is None else graph,
            "preamble": self.graph.preamble,
            "preserved_blocks": self.graph.preserved_blocks,
            "source_file": self.py_path.relative_to(self.root).as_posix(),
            "base_revision": current_source_revision(self.py_path, self.root),
        }


def follow_moves(
    moved_from: Mapping[NodeRef, NodeRef], moved: list[dict[str, Any]]
) -> dict[NodeRef, NodeRef]:
    """*moved_from* after a graph change whose answer *moved* the nodes it names: each moved
    node keeps the place the baseline holds it, through a second move, as
    ``documentRequest.withSavedAddresses`` does."""
    after = dict(moved_from)
    for move in moved:
        before: NodeRef = (move["from_submodel"], move["from_node_id"])
        after[(move["to_submodel"], move["to_node_id"])] = moved_from.get(before, before)
    return after
