"""Second-pass mutation witnesses for _json_shred survivors the first round
under-witnessed.

Each targets a specific Cosmic Ray survivor that a prior witness *claimed* but
did not actually kill (wrong line identity, or a test input that couldn't
distinguish the mutated operator from the original over its reachable domain).
The kill strategy for each is spelled out in its docstring.
"""

from __future__ import annotations

import orjson
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred._records import _read_root_array_value
from haute._json_shred._shred import _resolve_leaf

# ─── 546 — _resolve_leaf list descent takes cur[0], not cur[-1] ──────


def test_resolve_leaf_dotted_through_list_fails_loud() -> None:
    # W1: a dotted leaf that crosses a list no longer silently descends into
    # the first element (dropping the rest) — it raises ApiInputSchemaError
    # naming the offending leaf, so the mis-modelled array (which should be a
    # child table) can never silently lose rows.
    with pytest.raises(ApiInputSchemaError, match="claims.amount"):
        _resolve_leaf({"claims": [{"amount": 3}, {"amount": 9}]}, "claims.amount")


# ─── 486 — _read_root_array_value raises on a depth-0 '}' ────────────


def _byte_reader(rest: bytes):  # noqa: ANN202
    it = iter(rest)

    def read_byte() -> bytes:
        try:
            return bytes([next(it)])
        except StopIteration:
            return b""

    return read_byte


def test_read_root_array_value_rejects_unbalanced_close_brace() -> None:
    # ``if b == b"]"`` (L486) sits inside ``if b in {b"}", b"]"}`` at depth 0:
    # a ']' returns the value, a '}' is unbalanced and must raise "unexpected
    # '}'". Eq->GtE makes ``b >= b"]"`` true for '}' (0x7d >= ']' 0x5d), so the
    # stray brace would be accepted as a value-end and RETURNED instead of
    # raising. Input '{}}' drives depth 1 -> 0 then hits the bare '}'.
    pos = [3]
    with pytest.raises(orjson.JSONDecodeError, match="unexpected '}'"):
        _read_root_array_value(b"{", _byte_reader(b"}}"), lambda: pos[0])


def test_read_root_array_value_returns_on_top_level_close_bracket() -> None:
    # The companion happy path: a balanced object value terminates at the
    # top-level ']' delimiter, returning the buffer. (Pins that the GtE-killing
    # test above isn't just asserting "always raises".)
    value, delim = _read_root_array_value(b"{", _byte_reader(b'"a":1}]'), lambda: 0)
    assert orjson.loads(value) == {"a": 1}
    assert delim == b"]"


# ─── 1123 — is_per_port_cache_valid: schema_mode must equal "v2" exactly ──


# ─── 1128 — is_per_port_cache_valid: edited data file invalidates ────


# ─── 1019 — load_per_port_cache non-emitting skip uses continue ──────


# ─── 875 / 879 — build skip branches use continue ───────────────────
