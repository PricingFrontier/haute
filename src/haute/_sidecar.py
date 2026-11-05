"""Read-side schema and typed state for ``.haute.json`` editor sidecars.

This is a core module so editor recovery can consume sidecar state without
importing the web layer; ``haute.routes._helpers`` re-exports these names for
route consumers and keeps the write path (``save_sidecar``) beside its
self-write tracking.
"""

from __future__ import annotations

import json as _json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from haute._logging import get_logger
from haute._node_specs import node_spec
from haute._polars_steps import STEPS_FROM_CODE
from haute._types import GraphNode, NodeType
from haute.errors import ParseError

logger = get_logger(component="sidecar")


class SidecarModel(BaseModel):
    """On-disk schema for the ``.haute.json`` sidecar file.

    The sidecar carries editor-state that doesn't belong in the pipeline
    ``.py`` source-of-truth:

    * ``positions`` — canvas (x, y) co-ordinates per sanitised node id,
      so the layout survives label renames.
    * ``sources`` — ordered list of available data sources for this
      pipeline (``"live"`` is always first).
    * ``active_source`` — which source is currently selected in the UI.
    * ``unfinished_steps`` — a transform's whole step list, by node, while one of
      its steps is unfinished (READ-22): its body is then the raising
      placeholder, and the steps live nowhere else until they are complete.

    Every optional field has a sensible default so sparse sidecars still
    parse.  That current-shape defaulting contract is pinned by
    ``tests/test_routes_hygiene.py::TestSidecarDefaults``.

    Write path: ``save_sidecar`` constructs a ``SidecarModel`` and
    serialises via :meth:`model_dump_json`, excluding defaults so a
    freshly-saved pipeline with ``sources=["live"]`` does not bloat the
    file with redundant state (see
    ``tests/test_route_helpers.py::test_default_source_not_saved``).
    Read path: ``load_sidecar``/``parse_pipeline_to_graph`` still parses
    as plain JSON today, but consumers may upgrade to
    :meth:`model_validate_json` for typed access.
    """

    #: Whole pixels as written (READ-11); an older sidecar's fractional ones still read.
    positions: dict[str, dict[str, int | float]] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=lambda: ["live"])
    active_source: str = "live"
    managed_parent: str | None = None
    unfinished_steps: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _active_source_must_be_in_sources(self) -> SidecarModel:
        if self.active_source not in self.sources:
            raise ValueError(
                f"active_source={self.active_source!r} is not in sources={self.sources!r}"
            )
        return self


SidecarReadState = Literal["absent", "valid", "corrupt", "unreadable"]


@dataclass(frozen=True, slots=True)
class SidecarReadResult:
    """Typed, side-effect-free state of one editor position sidecar."""

    path: Path
    state: SidecarReadState
    data: SidecarModel | None = None
    error_type: str | None = None


def with_unfinished_steps(nodes: list[GraphNode], unfinished: Any) -> list[GraphNode]:
    """*nodes* with each stepped node saved with an unfinished step list given its steps
    from the sidecar (READ-22, READ-44): one whose body is the placeholder, which loads
    with no code and no steps. An entry for any other node is ignored; the next save
    drops it. Every loader of a pipeline file applies it, the editor's document included."""
    if not isinstance(unfinished, dict) or not unfinished:
        return nodes
    given: list[GraphNode] = []
    for node in nodes:
        steps = unfinished.get(node.id)
        config = node.data.config
        if (
            node.data.nodeType in STEPS_FROM_CODE
            and isinstance(steps, list)
            and not config.get("code")
            and "steps" not in config
        ):
            given.append(node.with_config({**config, "steps": steps}))
        else:
            given.append(node)
    return given


def unfinished_step_list(
    node: GraphNode, input_names: Sequence[str]
) -> list[dict[str, Any]] | None:
    """*node*'s step list when a save writes it as the raising placeholder and keeps the list
    in ``.haute.json`` (READ-22, READ-44), else None: one that cannot be rendered, or not
    against *input_names*, the inputs connected to it, as codegen renders it and the save
    warns of it. A step naming an input that is not connected renders on its own, so the
    node's ``steps_error`` alone would not say so. Every save that writes the file decides
    by it, a whole save, one node's and Recover's alike."""
    from haute._builders import stepped_code_problem
    from haute._polars_steps import step_input_names

    steps = node.data.config.get("steps")
    if node.data.nodeType not in STEPS_FROM_CODE or not isinstance(steps, list):
        return None
    if node.data.editor.steps_error is not None:
        return steps
    node_type = NodeType(node.data.nodeType)
    problem = stepped_code_problem(
        node.data.config, node_type, step_input_names(node_type, list(input_names))
    )
    return None if problem is None else steps


def missing_unfinished_steps(node_type: NodeType | str, held: bool) -> bool:
    """Whether a node whose body is the unfinished-steps placeholder, given *held* (its list
    is in the file's ``.haute.json``), must be refused: one whose code runs on a frame it
    was given (any stepped node but a Transform) would, with no code and no steps, pass
    that frame on with its steps skipped. A Transform with no code fails when run."""
    return not held and node_spec(node_type).code != "transform"


def missing_unfinished_steps_message(node_id: str, py_path: Path) -> str:
    """The refusal of a placeholder whose unfinished steps the file's ``.haute.json`` does
    not hold, as the parse and the editor's recovery both give it."""
    return (
        f"Node {node_id!r} is saved with unfinished steps, which wait in "
        f"{py_path.with_suffix('.haute.json').name}, but that file does not hold them, so "
        "running it would skip them. Write its steps again, or replace its body with `...` "
        "to start afresh."
    )


def with_saved_unfinished_steps(
    nodes: list[GraphNode],
    placeholders: set[str],
    py_path: Path,
    *,
    read_bytes: Callable[[Path], bytes] = Path.read_bytes,
) -> list[GraphNode]:
    """*nodes*, parsed from *py_path*, given the unfinished step lists its ``.haute.json``
    holds (READ-22, READ-44), as every load applies them: ``haute run``, a deploy and the
    editor then fail such a node naming its unfinished step. *placeholders* are the nodes
    whose body is the placeholder those lists are saved with; one the file does not hold
    is refused (:func:`missing_unfinished_steps`). *read_bytes* reads the file; editor
    recovery passes its load's capture."""
    read = read_sidecar_state(py_path, read_bytes=read_bytes)
    held = read.data.unfinished_steps if read.data is not None else {}
    given = with_unfinished_steps(nodes, held)
    for node in given:
        if node.id in placeholders and missing_unfinished_steps(
            node.data.nodeType, "steps" in node.data.config
        ):
            raise ParseError(
                missing_unfinished_steps_message(node.id, py_path),
                node_id=node.id,
                source_file=str(py_path),
            )
    return given


def _read_sidecar_json(
    sidecar: Path, read_bytes: Callable[[Path], bytes] = Path.read_bytes
) -> tuple[SidecarReadState, dict[str, Any] | None, str | None]:
    """Read and JSON-decode one sidecar object without model validation.

    Returns ``(state, payload, error_type)``; ``payload`` is set only for
    ``valid``. Shared by the permissive :func:`load_sidecar` and the typed
    :func:`read_sidecar_state` so IO and JSON tolerance live in one place.
    *read_bytes* reads the file; editor recovery passes its load's capture.
    """
    try:
        raw = read_bytes(sidecar)
    except FileNotFoundError:
        return "absent", None, None
    except OSError as exc:
        logger.warning("unreadable_sidecar", file=sidecar.name, error=str(exc))
        return "unreadable", None, type(exc).__name__
    try:
        # Strict UTF-8, so a sidecar that is not is corrupt rather than decoded
        # with replacement characters a save would write back.
        payload = _json.loads(raw.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Sidecar JSON must contain an object.")
    except (_json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("corrupt_sidecar", file=sidecar.name, error=str(exc))
        return "corrupt", None, type(exc).__name__
    return "valid", payload, None


def read_sidecar_state(
    py_path: Path, *, read_bytes: Callable[[Path], bytes] = Path.read_bytes
) -> SidecarReadResult:
    """Read a sidecar without collapsing absent and invalid content together."""
    sidecar = py_path.with_suffix(".haute.json")
    state, payload, error_type = _read_sidecar_json(sidecar, read_bytes)
    if state != "valid" or payload is None:
        return SidecarReadResult(path=sidecar, state=state, error_type=error_type)
    try:
        data = SidecarModel.model_validate(payload)
        raw_positions = payload.get("positions", {})
        if raw_positions != _normalise_sidecar_positions(raw_positions):
            raise ValueError("Sidecar positions must contain finite x/y coordinates.")
    except (TypeError, ValueError) as exc:
        logger.warning("corrupt_sidecar", file=sidecar.name, error=str(exc))
        return SidecarReadResult(
            path=sidecar,
            state="corrupt",
            error_type=type(exc).__name__,
        )
    return SidecarReadResult(path=sidecar, state="valid", data=data)


def _normalise_sidecar_sources(raw_sources: Any) -> list[str] | None:
    if not isinstance(raw_sources, list):
        return None

    seen: set[str] = set()
    cleaned: list[str] = []
    saw_live = False
    for value in raw_sources:
        if not isinstance(value, str):
            continue
        source = value.strip()
        if not source:
            continue
        if source == "live":
            saw_live = True
            continue
        if source in seen:
            continue
        seen.add(source)
        cleaned.append(source)

    if not cleaned and not saw_live:
        return None
    return ["live", *cleaned]


def _normalise_sidecar_positions(raw_positions: Any) -> dict[str, dict[str, float]]:
    if not isinstance(raw_positions, dict):
        return {}

    positions: dict[str, dict[str, float]] = {}
    for node_id, position in raw_positions.items():
        if not isinstance(node_id, str) or not isinstance(position, dict):
            continue
        x = position.get("x")
        y = position.get("y")
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            continue
        if not isinstance(y, (int, float)) or isinstance(y, bool):
            continue
        xf = float(x)
        yf = float(y)
        if not math.isfinite(xf) or not math.isfinite(yf):
            continue
        positions[node_id] = {"x": xf, "y": yf}
    return positions
