"""Second-pass mutation witnesses for _json_shred survivors the first round
under-witnessed.

Each targets a specific Cosmic Ray survivor that a prior witness *claimed* but
did not actually kill (wrong line identity, or a test input that couldn't
distinguish the mutated operator from the original over its reachable domain).
The kill strategy for each is spelled out in its docstring.
"""

from __future__ import annotations

import io

import orjson
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred._records import _iter_root_array_values
from haute._json_shred._shred import _resolve_leaf

# ─── 546 — _resolve_leaf list descent takes cur[0], not cur[-1] ──────


def test_resolve_leaf_dotted_through_list_fails_loud() -> None:
    # W1: a dotted leaf that crosses a list no longer silently descends into
    # the first element (dropping the rest) — it raises ApiInputSchemaError
    # naming the offending leaf, so the mis-modelled array (which should be a
    # child table) can never silently lose rows.
    with pytest.raises(ApiInputSchemaError, match="claims.amount"):
        _resolve_leaf({"claims": [{"amount": 3}, {"amount": 9}]}, "claims.amount")


# ─── A root-array element's depth-0 '}' is unbalanced ────────────────


def test_root_array_rejects_an_unbalanced_close_brace() -> None:
    # At depth 0 a ']' ends the element and a '}' is unbalanced: '{}}' drives
    # depth 1 -> 0, then the bare '}' raises at the byte after it.
    with pytest.raises(orjson.JSONDecodeError, match="unexpected '}'") as raised:
        list(_iter_root_array_values(io.BytesIO(b"{}}]"), 1, 1 << 20))
    assert raised.value.pos == 4


def test_root_array_element_ends_at_the_top_level_close_bracket() -> None:
    # The companion happy path: a balanced object ends at the top-level ']'.
    assert list(_iter_root_array_values(io.BytesIO(b'{"a":1}]'), 1, 1 << 20)) == [{"a": 1}]


# ─── 1123 — is_per_port_cache_valid: schema_mode must equal "v2" exactly ──


# ─── 1128 — is_per_port_cache_valid: edited data file invalidates ────


# ─── 1019 — load_per_port_cache non-emitting skip uses continue ──────


# ─── 875 / 879 — build skip branches use continue ───────────────────
