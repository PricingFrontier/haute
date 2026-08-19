"""Read-side schema and typed state for ``.haute.json`` editor sidecars.

This is a core module so editor recovery can consume sidecar state without
importing the web layer; ``haute.routes._helpers`` re-exports these names for
route consumers and keeps the write path (``save_sidecar``) beside its
self-write tracking.
"""

from __future__ import annotations

import json as _json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from haute._io import read_user_text
from haute._logging import get_logger

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

    positions: dict[str, dict[str, float]] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=lambda: ["live"])
    active_source: str = "live"
    managed_parent: str | None = None

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


def _read_sidecar_json(sidecar: Path) -> tuple[SidecarReadState, dict[str, Any] | None, str | None]:
    """Read and JSON-decode one sidecar object without model validation.

    Returns ``(state, payload, error_type)``; ``payload`` is set only for
    ``valid``. Shared by the permissive :func:`load_sidecar` and the typed
    :func:`read_sidecar_state` so IO and JSON tolerance live in one place.
    """
    if not sidecar.exists():
        return "absent", None, None
    try:
        raw = read_user_text(sidecar)
    except OSError as exc:
        logger.warning("unreadable_sidecar", file=sidecar.name, error=str(exc))
        return "unreadable", None, type(exc).__name__
    try:
        payload = _json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Sidecar JSON must contain an object.")
    except (_json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("corrupt_sidecar", file=sidecar.name, error=str(exc))
        return "corrupt", None, type(exc).__name__
    return "valid", payload, None


def read_sidecar_state(py_path: Path) -> SidecarReadResult:
    """Read a sidecar without collapsing absent and invalid content together."""
    sidecar = py_path.with_suffix(".haute.json")
    state, payload, error_type = _read_sidecar_json(sidecar)
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
