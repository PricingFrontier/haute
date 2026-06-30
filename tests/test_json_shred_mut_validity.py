"""Mutation witnesses for the v2 cache-validity boundary (W2 items 2.4/2.6).

Direct witnesses for the data-file freshness check (:func:`_data_file_matches`),
its content hash (:func:`_hash_file`), and the schema fingerprint
(:func:`_v2_fingerprint`). These gate whether a cached parquet set is served or
rebuilt; a mutation that makes them wrongly report "fresh" / "equal" would serve
stale rows silently, so each branch decision gets a discriminating witness.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from haute._json_shred import (
    _data_file_matches,
    _hash_file,
    _v2_fingerprint,
)

# ─── _hash_file — chunked content hash ─────────────────────────────


def test_hash_file_matches_sha256_of_content(tmp_path: Path) -> None:
    # A real, multi-byte file must hash to exactly sha256(content). Kills the
    # mutations that zero the read chunk size (``1 << 20`` -> ``1 // 20`` /
    # ``1 & 20`` / ``1 >> 20`` = 0 -> ``read(0)`` -> the iter sentinel fires
    # immediately -> empty hash) and the ZeroIterationForLoop (no chunks read).
    content = b"the quick brown fox jumps over the lazy dog\n" * 64
    p = tmp_path / "data.json"
    p.write_bytes(content)
    assert _hash_file(p) == hashlib.sha256(content).hexdigest()


# ─── _data_file_matches — stat-fast freshness with hash arbitration ──


def _sig(
    path: Path,
    *,
    size: int | None = None,
    mtime_ns: int | None = None,
    sha256: str | None = None,
) -> dict[str, Any]:
    """A recorded signature, each field defaulting to the file's actual value."""
    st = path.stat()
    return {
        "size": st.st_size if size is None else size,
        "mtime_ns": st.st_mtime_ns if mtime_ns is None else mtime_ns,
        "sha256": _hash_file(path) if sha256 is None else sha256,
    }


def test_data_file_matches_rejects_non_dict_signature(tmp_path: Path) -> None:
    # L271: a missing / garbled (non-dict) signature is stale. Kills False->True.
    p = tmp_path / "d.json"
    p.write_bytes(b"x")
    assert _data_file_matches("not a dict", p) is False
    assert _data_file_matches(None, p) is False


def test_data_file_matches_missing_file_is_stale_not_raising(tmp_path: Path) -> None:
    # L274/L275: ``stat()`` raising OSError (FileNotFoundError ⊂ OSError) is
    # caught and returns False. Kills the ExceptionReplacer (a narrowed/changed
    # except would let it propagate) and the False->True (calling a deleted
    # source "fresh" would serve cached rows for a file that no longer exists).
    missing = tmp_path / "gone.json"
    sig = {"size": 1, "mtime_ns": 1, "sha256": "0" * 64}
    assert _data_file_matches(sig, missing) is False


def test_data_file_matches_size_mismatch_is_stale_both_directions(tmp_path: Path) -> None:
    # L276/L277: a size mismatch is stale, and the mtime is left matching so a
    # mutant can't sneak a True via the fast path. Two directions pin the
    # operator: recorded LARGER than real kills '!=' -> '>' (5 > 10 is False);
    # recorded SMALLER kills '!=' -> '<' (5 < 2 is False). Either also kills the
    # False->True on the size-stale return.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")  # size 5, mtime recorded as real below
    assert _data_file_matches(_sig(p, size=10), p) is False
    assert _data_file_matches(_sig(p, size=2), p) is False


def test_data_file_matches_mtime_fastpath_trusts_mtime_over_hash(tmp_path: Path) -> None:
    # L278: size match + mtime match -> True WITHOUT hashing, even with a wrong
    # recorded hash. Recording a deliberately wrong hash makes the fast-path True
    # observably different from the hash-arbitration False, killing
    # '==' -> '!=', '>', '<', 'is', and AddNot (all fall through to the bad hash).
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")
    sig = _sig(p, sha256="f" * 64)  # real size+mtime, deliberately wrong hash
    assert _data_file_matches(sig, p) is True


def test_data_file_matches_recorded_mtime_older_falls_to_hash(tmp_path: Path) -> None:
    # L278 directional: recorded mtime OLDER than the file (file is "newer").
    # '==' is False -> hash arbitrates -> wrong hash -> False. '>=' / 'is not'
    # would (wrongly) take the fast-path True, so correct=False kills them.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")
    older = _sig(p, mtime_ns=p.stat().st_mtime_ns - 1_000_000, sha256="f" * 64)
    assert _data_file_matches(older, p) is False


def test_data_file_matches_recorded_mtime_newer_falls_to_hash(tmp_path: Path) -> None:
    # L278 directional: recorded mtime NEWER than the file. '==' False -> hash ->
    # wrong -> False; '<=' would take the fast-path True. Kills '==' -> '<='.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")
    newer = _sig(p, mtime_ns=p.stat().st_mtime_ns + 1_000_000, sha256="f" * 64)
    assert _data_file_matches(newer, p) is False


def test_data_file_matches_hash_arbitrates_when_mtime_moved(tmp_path: Path) -> None:
    # L280: size matches but mtime moved (deploy copy / touch) -> the content
    # hash arbitrates. A correct hash validates despite the drift; a wrong one is
    # stale. The two wrong hashes bracket the real digest lexically so '>=' and
    # '<=' are pinned too (a sha256 hex digest sorts strictly between "0"*64 and
    # "f"*64). Kills '==' -> '!=', 'is', 'is not', '>', '<', '>=', '<='.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")
    moved = p.stat().st_mtime_ns + 5_000_000  # content unchanged, mtime drifted
    assert _data_file_matches(_sig(p, mtime_ns=moved), p) is True
    assert _data_file_matches(_sig(p, mtime_ns=moved, sha256="0" * 64), p) is False
    assert _data_file_matches(_sig(p, mtime_ns=moved, sha256="f" * 64), p) is False


# ─── _v2_fingerprint — canonical content hash over the schema ──────


def _col(name: str, path: str, type_: str = "str", *, selected: bool = True) -> dict[str, Any]:
    return {"name": name, "path": path, "type": type_, "selected": selected, "levels": None}


def _table(
    path: str,
    label: str,
    columns: list[Any],
    *,
    emit: bool = True,
) -> dict[str, Any]:
    return {
        "path": path,
        "label": label,
        "emit": emit,
        "row_id_column": None,
        "columns": columns,
    }


def test_v2_fingerprint_skips_non_dict_table_without_aborting() -> None:
    # L128: ``continue`` past a non-dict table entry must NOT abort the loop — a
    # valid table AFTER the junk still contributes. Kills 'continue' -> 'break'
    # (which would drop the trailing table and collapse the fingerprint).
    valid = _table("$[:]", "root", [_col("id", "$[:].id", "int")])
    with_junk = _v2_fingerprint({"tables": ["not a table", valid]})
    without = _v2_fingerprint({"tables": [valid]})
    assert with_junk == without


def test_v2_fingerprint_skips_non_dict_column_without_aborting() -> None:
    # L132: same, one level down — a non-dict column entry is skipped, the real
    # column after it still contributes. 'break' would drop it.
    junk = _table("$[:]", "root", ["not a column", _col("id", "$[:].id", "int")])
    clean = _table("$[:]", "root", [_col("id", "$[:].id", "int")])
    assert _v2_fingerprint({"tables": [junk]}) == _v2_fingerprint({"tables": [clean]})


def test_v2_fingerprint_is_invariant_to_column_input_order() -> None:
    # L144: columns are sorted by (path, name) before hashing, so the editor's
    # row order doesn't move the fingerprint. Kills 'or' -> 'and' on the sort key
    # (``path and ""`` collapses every key to "" -> a no-op sort -> the column
    # list stays in input order -> an order-dependent, unstable fingerprint).
    cols_ab = [_col("a", "$[:].a"), _col("b", "$[:].b")]
    cols_ba = [_col("b", "$[:].b"), _col("a", "$[:].a")]
    fp_ab = _v2_fingerprint({"tables": [_table("$[:]", "root", cols_ab)]})
    fp_ba = _v2_fingerprint({"tables": [_table("$[:]", "root", cols_ba)]})
    assert fp_ab == fp_ba


def test_v2_fingerprint_is_invariant_to_table_input_order() -> None:
    # L154: tables are sorted by path before hashing. Kills 'or' -> 'and' on the
    # table sort key (same no-op-sort failure, one level up).
    t1 = _table("$[:].a[:]", "a", [_col("x", "$[:].a[:].x")], emit=False)
    t2 = _table("$[:]", "root", [_col("y", "$[:].y")])
    assert _v2_fingerprint({"tables": [t1, t2]}) == _v2_fingerprint({"tables": [t2, t1]})
