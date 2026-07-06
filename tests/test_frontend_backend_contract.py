"""Cross-layer contract: the frontend and backend must agree on the set of
allowed v2 column types.

The two layers hand-maintain parallel lists — ``ALLOWED_TYPES`` in
``frontend/src/panels/editors/apiInputSchema.ts`` and ``_ALLOWED_COLUMN_TYPES``
in ``haute._api_input_schema``. If they drift, the editor will accept (or
coerce) a type the backend's B1 guardrail rejects, or vice versa, producing a
confusing experience that no single-layer test would catch. This test pins
their agreement.
"""

from __future__ import annotations

import re
from pathlib import Path

from haute._api_input_schema import _ALLOWED_COLUMN_TYPES
from haute._types import NodeType

_TS_SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "src"
    / "panels"
    / "editors"
    / "apiInputSchema.ts"
)
_TS_NODE_TYPES = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "utils" / "nodeTypes.ts"
)


def test_frontend_and_backend_allowed_column_types_agree() -> None:
    ts = _TS_SCHEMA.read_text(encoding="utf-8")
    m = re.search(r"ALLOWED_TYPES[^=]*=\s*new Set\(\[(.*?)\]", ts, re.DOTALL)
    assert m is not None, "ALLOWED_TYPES set literal not found in apiInputSchema.ts"
    frontend_types = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert frontend_types, "no quoted type tokens parsed from ALLOWED_TYPES"
    assert frontend_types == set(_ALLOWED_COLUMN_TYPES), (
        f"frontend ALLOWED_TYPES {sorted(frontend_types)} != backend "
        f"_ALLOWED_COLUMN_TYPES {sorted(_ALLOWED_COLUMN_TYPES)} — the two layers "
        "must stay in lock-step or the editor and the B1 validate guardrail will "
        "disagree on which column types are valid."
    )


def test_frontend_and_backend_node_types_agree() -> None:
    ts = _TS_NODE_TYPES.read_text(encoding="utf-8")
    m = re.search(r"export const NODE_TYPES\s*=\s*\{(.*?)\}\s*as const", ts, re.DOTALL)
    assert m is not None, "NODE_TYPES object literal not found in nodeTypes.ts"
    frontend_node_types = set(re.findall(r':\s*"([^"]+)"', m.group(1)))
    assert frontend_node_types, "no quoted node type values parsed from NODE_TYPES"
    backend_node_types = {node_type.value for node_type in NodeType}
    assert frontend_node_types == backend_node_types, (
        f"frontend NODE_TYPES {sorted(frontend_node_types)} != backend "
        f"NodeType {sorted(backend_node_types)}. The React Flow wire vocabulary "
        "must match the backend enum exactly."
    )
