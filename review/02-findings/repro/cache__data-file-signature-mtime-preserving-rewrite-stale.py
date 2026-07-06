"""Adversarial repro: ``is_per_port_cache_valid`` serves a STALE per-port cache
after a byte-changing rewrite that preserves both ``size`` and ``mtime_ns``.

CLAIM under test
----------------
``_data_file_matches`` (src/haute/_json_shred.py:253-271) is the freshness
arbiter for ``is_per_port_cache_valid``. Its fast path is::

    if st.st_size != recorded.get("size"):
        return False
    if st.st_mtime_ns == recorded.get("mtime_ns"):
        return True            # <-- returns fresh WITHOUT hashing
    return _hash_file(data_path) == recorded.get("sha256")

So sha256 is recorded at build time but only ever consulted when mtime_ns
DRIFTS. A deploy/copy tool that rewrites DIFFERENT file content while
preserving both ``size`` and ``mtime_ns`` (rsync --times, certain in-place
editors, container layer restores) leaves the cache judged fresh while the
on-disk shredded rows still describe the OLD bytes — silent staleness at the
data-ingest boundary.

What this script proves
-----------------------
1. Build the cache for data content A ("OLD") -> parquet holds the OLD row.
2. Capture ``data.stat()``; rewrite the file with content B ("NEW") of the
   SAME byte length; ``os.utime`` to restore ``(atime_ns, mtime_ns)``.
3. ``is_per_port_cache_valid(...) is True``  (the stat gate says "fresh").
4. ``load_per_port_cache(...)`` STILL yields the OLD value, NOT "NEW" —
   so a fresh-judged cache serves stale rows for changed bytes.

Control: also rewrite with content of a DIFFERENT byte length and confirm the
size check correctly invalidates — proving the mechanism works for the case it
was designed for and is simply blind to the equal-size, equal-mtime case.

Isolation
---------
All disk I/O is under ``tempfile.TemporaryDirectory``. Synthetic JSON + config
only. No rating/, src/, tests/, or any real project file is read or written;
no project root is required by these functions.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from haute._json_shred import (
    build_per_port_cache,
    is_per_port_cache_valid,
    load_per_port_cache,
)


def _config() -> dict:
    """Minimal valid v2 config: one root-emitting table with one str column.

    The data file is a JSON array of records ``[{"value": ...}, ...]``; the
    table iterates the root array and selects the scalar ``value`` field.
    """
    return {
        "tables": [
            {
                "path": "$[*]",
                "label": "rows",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[*].value",
                        "type": "str",
                        "status": "Inferred",
                        "selected": True,
                        "levels": None,
                    },
                ],
            },
        ],
    }


def _read_single_value(cache_dir: Path, cfg: dict) -> str:
    """Materialise the cached 'rows' table and return the single value cell."""
    frames = load_per_port_cache(cache_dir, cfg)
    frame = frames["rows"].collect()
    assert frame.height == 1, f"expected exactly one cached row, got {frame.height}"
    return frame["value"][0]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data = tmp_path / "data.json"
        cache_dir = tmp_path / "cache"
        cfg = _config()

        # --- Build cache for content A -------------------------------------
        # Equal-length string values so a later swap preserves byte size.
        old_payload = b'[{"value": "OLD"}]'
        new_payload = b'[{"value": "NEW"}]'
        assert len(old_payload) == len(new_payload), "payloads must be equal length"

        data.write_bytes(old_payload)
        build_per_port_cache(data, cfg, cache_dir)

        # Sanity: the freshly-built cache reads back the OLD value and is valid.
        assert _read_single_value(cache_dir, cfg) == "OLD"
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True

        # --- mtime+size-preserving byte-changing rewrite -------------------
        st = data.stat()
        data.write_bytes(new_payload)  # different bytes, identical length
        os.utime(data, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime_ns

        after = data.stat()
        assert after.st_size == st.st_size, "size must be preserved for the repro"
        assert after.st_mtime_ns == st.st_mtime_ns, "mtime_ns must be preserved"
        # The bytes on disk really changed:
        assert data.read_bytes() == new_payload

        # --- The defect -----------------------------------------------------
        judged_valid = is_per_port_cache_valid(cache_dir, cfg, data_path=data)
        served_value = _read_single_value(cache_dir, cfg)

        print(f"is_per_port_cache_valid (after byte-change) = {judged_valid}")
        print(f'on-disk file now says            = {new_payload.decode()!r}')
        print(f"cache serves value               = {served_value!r}")

        bug_reproduced = judged_valid is True and served_value == "OLD"

        # --- Control: a size CHANGE must invalidate (mechanism works) ------
        data.write_bytes(b'[{"value": "LONGER_VALUE"}]')  # different length
        os.utime(data, ns=(st.st_atime_ns, st.st_mtime_ns))  # same mtime again
        size_change_valid = is_per_port_cache_valid(cache_dir, cfg, data_path=data)
        print(f"is_per_port_cache_valid (after size change) = {size_change_valid}")

        if not bug_reproduced:
            print(
                "REFUTED: cache did NOT serve stale rows under equal size+mtime "
                f"(valid={judged_valid}, served={served_value!r})"
            )
            return 1
        if size_change_valid is not False:
            print(
                "UNEXPECTED: size-change control did not invalidate "
                f"(valid={size_change_valid}); repro environment is suspect"
            )
            return 1

        print(
            "REPRODUCED: stat-gate judged the cache fresh after an equal-size, "
            "equal-mtime byte change, yet load_per_port_cache served the OLD "
            "shredded row ('OLD') instead of the new file content ('NEW'). "
            "The recorded sha256 was never consulted because mtime_ns matched."
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
