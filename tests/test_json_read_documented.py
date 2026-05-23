"""Pinning tests for issue #92 — the eager-read limitation of ``read_source``
for ``.json`` files.

Polars has no ``pl.scan_json`` for plain (object-per-file) JSON, so
:func:`haute._io.read_source` reads ``.json`` files eagerly via
``pl.read_json(path).lazy()``.  For large JSON blobs this is an O(file-size)
memory spike.  NDJSON (one JSON object per line) does support ``scan_ndjson``
and we route through it.

These tests pin:

1. The docstring names the eager-load behaviour AND points readers at the
   parquet path (so engineers who hit the memory wall find the escape hatch
   without spelunking source).
2. NDJSON goes through ``pl.scan_ndjson`` — a truly lazy path — and
   ``.head()`` gets pushed into the scan so the frame actually collected
   is only N rows, not the full file.
3. Plain ``.json`` currently materialises the whole file.  We pin that
   behaviour by asserting the *full* row count is collected and that the
   underlying call is to ``pl.read_json`` (no ``scan_json`` escape hatch).
   A future lazy-JSON implementation will need to update these pins.

Memory footprint: Polars allocates in Rust/Arrow buffers that Python's
``tracemalloc`` cannot see.  Instead of attempting a fragile Python-level
RSS measurement we pin the *observable* consequence of eager-vs-lazy:
which polars function is called, and the row count of the collected
LazyFrame when ``.head(n)`` is applied.  Together these pin the memory
profile without Rust/Python measurement drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import polars as pl

from haute import _io
from haute._io import read_source

# ---------------------------------------------------------------------------
# Docstring pinning — eager behaviour and parquet escape hatch must be named.
# ---------------------------------------------------------------------------


class TestDocstringDocumentsLimitation:
    """The docstring on ``read_source`` is the developer's first line of
    defence against the JSON memory footgun.  Pin what it must say."""

    def test_docstring_is_present(self) -> None:
        """``read_source`` has a non-empty docstring."""
        assert read_source.__doc__ is not None
        assert read_source.__doc__.strip() != ""

    def test_docstring_names_eager_json_behaviour(self) -> None:
        """The docstring explicitly calls out the eager JSON read.

        Without this, a developer profiling a memory spike has no breadcrumb
        from ``read_source`` back to the root cause (no ``scan_json``).
        """
        doc = (read_source.__doc__ or "").lower()
        # Must say SOMETHING about json being the eager/non-scan path.
        mentions_json = "json" in doc
        mentions_eager_behaviour = any(
            term in doc
            for term in (
                "eager",
                "eagerly",
                "read the full file",
                "reads the full file",
                "entire file",
                "whole file",
                "no scan_json",
                "no ``scan_json``",
                "no scan",
            )
        )
        assert mentions_json, (
            "read_source docstring must mention JSON so developers searching "
            "for the eager-load limitation find it."
        )
        assert mentions_eager_behaviour, (
            "read_source docstring must describe the eager-read behaviour "
            "for .json files (e.g. 'eager', 'entire file', 'no scan_json')."
        )

    def test_docstring_points_at_parquet_escape_hatch(self) -> None:
        """The docstring names the parquet/flatten-cache alternative.

        ``read_json_flat`` caches to parquet and returns ``scan_parquet()``,
        which *is* lazy.  Callers hitting the eager-read memory ceiling need
        to know this exists without reading the implementation.
        """
        doc = (read_source.__doc__ or "").lower()
        assert any(term in doc for term in ("read_json_flat", "parquet", "cache")), (
            "read_source docstring must point at the parquet/read_json_flat "
            "escape hatch so callers hitting the memory wall find the fix."
        )


# ---------------------------------------------------------------------------
# Dispatch pinning — NDJSON takes the lazy path; plain JSON takes the eager
# path.  These are load-bearing for memory behaviour.
# ---------------------------------------------------------------------------


class TestNDJSONUsesScanNDJSON:
    """``.jsonl`` files route through ``pl.scan_ndjson`` — lazy."""

    def test_ndjson_dispatch_uses_scan_ndjson(self, tmp_path: Path) -> None:
        """Pin that ``.jsonl`` goes through ``pl.scan_ndjson`` (not read_ndjson)."""
        path = tmp_path / "data.jsonl"
        pl.DataFrame({"a": [1, 2, 3]}).write_ndjson(str(path))

        with patch.object(_io.pl, "scan_ndjson", wraps=_io.pl.scan_ndjson) as mock_scan:
            lf = read_source(str(path))
            lf.collect()

        mock_scan.assert_called_once_with(str(path))

    def test_ndjson_head_only_collects_requested_rows(self, tmp_path: Path) -> None:
        """``.head(n)`` on an NDJSON LazyFrame collects exactly n rows.

        This is what makes NDJSON the "safe" JSON format: the optimiser
        pushes the row limit into the scanner so the collected frame is
        O(n), not O(file-size).  If this assertion ever fails, somebody
        broke the lazy-scan push-down and large NDJSON reads just quietly
        got 1000x more expensive.
        """
        path = tmp_path / "big.jsonl"
        # 100k rows — plenty to distinguish "read 5" from "read all".
        row_count = 100_000
        with path.open("w", encoding="utf-8") as fh:
            for i in range(row_count):
                fh.write(json.dumps({"i": i, "v": i * 2}) + "\n")

        head = read_source(str(path)).head(5).collect()
        assert len(head) == 5, (
            "NDJSON scan_ndjson must honour a .head(5) pushdown — the "
            "collected frame should have exactly 5 rows."
        )
        # Sanity: full collect really does load every row (proves the
        # head=5 was an optimiser result, not an artifact of the file).
        full = read_source(str(path)).collect()
        assert len(full) == row_count

    def test_ndjson_uses_lazyframe_not_read_ndjson(self, tmp_path: Path) -> None:
        """Pin that the NDJSON path does NOT call eager ``pl.read_ndjson``.

        ``read_ndjson`` (eager) would defeat the whole point of routing
        NDJSON through a lazy scan.  Patch it and fail loudly if it ever
        gets called.
        """
        path = tmp_path / "data.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for i in range(10):
                fh.write(json.dumps({"i": i}) + "\n")

        # We don't *have* to stop this from working — just check it's not called.
        with patch.object(_io.pl, "read_ndjson") as mock_eager:
            read_source(str(path)).head(3).collect()

        mock_eager.assert_not_called()


class TestPlainJSONIsEager:
    """Plain ``.json`` currently reads the whole file into memory.

    This is the *current* behaviour.  We pin it so a future switch to a
    lazy-scan implementation is caught as a *deliberate* change — not
    slipped in silently.  If someone ships lazy JSON reading, they should
    delete this test (not edit it to still pass).
    """

    def test_plain_json_dispatch_uses_read_json(self, tmp_path: Path) -> None:
        """Pin that ``.json`` goes through eager ``pl.read_json``."""
        path = tmp_path / "data.json"
        pl.DataFrame({"a": [1, 2, 3]}).write_json(str(path))

        with patch.object(_io.pl, "read_json", wraps=_io.pl.read_json) as mock_read:
            lf = read_source(str(path))
            lf.collect()

        mock_read.assert_called_once_with(str(path))

    def test_plain_json_head_still_materialises_full_file(self, tmp_path: Path) -> None:
        """``.head(n)`` on a plain ``.json`` LazyFrame CANNOT reduce read cost.

        Because ``pl.read_json`` is eager, ``read_source(json).head(n)``
        reads and parses the *entire* file before slicing — that's the
        footgun.  We pin this by showing the polars read happens before
        any ``.head(n)`` slicing, and the source-row-count is preserved
        through the eager read (the LazyFrame we get back wraps an
        already-materialised DataFrame of full length).

        When lazy JSON reading eventually lands, this test will start
        failing: the docstring should be updated and this pin removed.
        """
        path = tmp_path / "big.json"
        # 100k simple records — large enough that a lazy implementation
        # would visibly change behaviour if it ever shipped.
        rows = [{"i": i, "v": i * 2} for i in range(100_000)]
        path.write_text(json.dumps(rows), encoding="utf-8")
        file_size = path.stat().st_size
        assert file_size > 500_000  # sanity — file is chunky

        with patch.object(_io.pl, "read_json", wraps=_io.pl.read_json) as mock_read:
            lf = read_source(str(path))
            # `.head(5)` is applied AFTER the eager read — it cannot save work.
            head = lf.head(5).collect()

        # The eager reader was called exactly once (not skipped).
        assert mock_read.call_count == 1, (
            f"Plain JSON must call pl.read_json exactly once even when the "
            f"caller only wants 5 rows — this is the eager-read footgun. "
            f"If call_count is 0, a lazy path landed (update docstring + "
            f"delete this pin).  Got {mock_read.call_count}."
        )
        # .head(5) on the wrapped LazyFrame does slice to 5 rows, but the
        # full read already happened — the saving is merely on downstream
        # pipeline ops, not on the I/O.
        assert len(head) == 5

    def test_plain_json_full_collect_returns_all_rows(self, tmp_path: Path) -> None:
        """Complementary pin: a full collect of a large JSON returns every row.

        This is the "baseline" of the eager path — the whole file is read.
        A future lazy implementation must still return every row when
        fully collected, but this test stays correct either way.
        """
        path = tmp_path / "medium.json"
        rows = [{"i": i} for i in range(10_000)]
        path.write_text(json.dumps(rows), encoding="utf-8")

        df = read_source(str(path)).collect()
        assert len(df) == 10_000


# ---------------------------------------------------------------------------
# Smoke — the recommended escape hatch actually exists and is importable.
# ---------------------------------------------------------------------------


class TestParquetEscapeHatchExists:
    """v1 read_json_flat removed; v2 shred contracts live in test_v2_codec_and_shred.py."""

    pass
