"""Adversarial repro for claim:
  databricks-cache-windows-atomic-replace-vs-open-reader

Claim: fetch_and_cache's `tmp_path.replace(out_path)` (src/haute/_databricks_io.py:405)
can raise PermissionError on Windows when a concurrent reader holds the cache
parquet open (no FILE_SHARE_DELETE). The reader is created by
read_cached_table -> pl.scan_parquet(p) (line 437-448), which "keeps the file
open while a lazy plan is alive". Result: a complete fetch becomes a non-domain
error (re-raised by `except BaseException`) instead of a clean overwrite.

We test the precise mechanism with ASSERTions on the actual behaviour, not just
"something raised". Three independent probes:

  Probe 1: Does a held pl.scan_parquet LazyFrame (lazy plan alive, NOT collected)
           actually hold an OS handle that blocks os.replace? -- This is the
           literal mechanism the claim names.

  Probe 2: Does a held collect() result (DataFrame in memory) still hold a handle?

  Probe 3: Ground-truth: an explicit OS handle opened WITHOUT share-delete --
           confirms whether Windows os.replace onto an open dest raises at all on
           THIS machine/Python build (so a negative Probe 1/2 isn't a false
           negative from the platform simply not enforcing it).

  Probe 4: End-to-end -- drive the REAL fetch_and_cache with a mocked databricks
           cursor while a reader (per the claim) is held, and observe whether the
           fetch raises a non-domain PermissionError leaving the cache un-updated.

Isolation: tempfile only; no src/tests/rating/ touched. project root set via
haute._sandbox.set_project_root(tmp).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import polars as pl

print(f"platform={sys.platform} polars={pl.__version__}")


def _make_parquet(path: Path) -> None:
    pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).write_parquet(path)


# ---------------------------------------------------------------------------
# Probe 1 + 2 + 3: does an open reader block os.replace onto the dest?
# ---------------------------------------------------------------------------
def probe_replace_with_reader() -> dict[str, object]:
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        dest = tdp / "cache.parquet"
        _make_parquet(dest)

        # Probe 1: held lazy plan (the literal claim mechanism)
        src1 = tdp / "tmp1.parquet"
        _make_parquet(src1)
        lf = pl.scan_parquet(dest)  # lazy plan alive, NOT collected
        try:
            os.replace(src1, dest)
            results["probe1_lazy_plan_blocks_replace"] = False
        except PermissionError as e:
            results["probe1_lazy_plan_blocks_replace"] = True
            results["probe1_err"] = repr(e)
        del lf

        # Probe 2: held collected DataFrame (file already closed after collect)
        _make_parquet(dest)
        src2 = tdp / "tmp2.parquet"
        _make_parquet(src2)
        df = pl.scan_parquet(dest).collect()  # noqa: F841 - held in scope
        try:
            os.replace(src2, dest)
            results["probe2_collected_blocks_replace"] = False
        except PermissionError as e:
            results["probe2_collected_blocks_replace"] = True
            results["probe2_err"] = repr(e)
        del df

        # Probe 3: explicit OS handle WITHOUT share-delete (ground truth that the
        # platform enforces the lock at all). Python's builtin open() on Windows
        # opens WITHOUT FILE_SHARE_DELETE, so os.replace onto it should raise.
        _make_parquet(dest)
        src3 = tdp / "tmp3.parquet"
        _make_parquet(src3)
        fh = open(dest, "rb")  # no share-delete
        try:
            os.replace(src3, dest)
            results["probe3_oshandle_blocks_replace"] = False
        except PermissionError as e:
            results["probe3_oshandle_blocks_replace"] = True
            results["probe3_err"] = repr(e)
        finally:
            fh.close()

    return results


# ---------------------------------------------------------------------------
# Probe 4: end-to-end against the real fetch_and_cache with a mocked cursor.
# ---------------------------------------------------------------------------
class _FakeArrowBatch:
    def __init__(self, table):
        self._t = table
        self.num_rows = table.num_rows
        self.num_columns = table.num_columns
        self.schema = table.schema

    # fetch_and_cache passes the batch straight to ParquetWriter.write_table,
    # which needs a real pyarrow.Table, so we just return the real table.


def probe_end_to_end() -> dict[str, object]:
    import pyarrow as pa
    from unittest import mock

    import haute._sandbox as sandbox
    import haute._databricks_io as dbio

    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        sandbox.set_project_root(tdp)

        table_name = "cat.sch.tbl"
        out_path = dbio._cache_path_for(table_name, tdp)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Pre-seed an existing cache file so there is a destination to overwrite
        # and so a reader can hold it open.
        _make_parquet(out_path)

        # Build the two arrow batches the fake cursor will stream: one data batch
        # then an empty terminator (num_rows == 0).
        data_tbl = pa.table({"a": pa.array([10, 20], type=pa.int64())})
        empty_tbl = data_tbl.schema.empty_table()

        class _FakeCursor:
            def __init__(self):
                self._batches = [data_tbl, empty_tbl]
                self.rownumber = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, q):
                self._q = q

            def fetchmany_arrow(self, n):
                if self._batches:
                    b = self._batches.pop(0)
                    self.rownumber += b.num_rows
                    return b
                return empty_tbl

        class _FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return _FakeCursor()

        fake_sql = mock.MagicMock()
        fake_sql.connect.return_value = _FakeConn()

        # Hold a reader open per the claim. We test BOTH the lazy-plan form and
        # an explicit OS handle so the end-to-end result is unambiguous.
        reader_lf = dbio.read_cached_table(table_name, tdp)  # lazy plan
        explicit_handle = open(out_path, "rb")  # hard OS lock, no share-delete

        before_bytes = out_path.read_bytes()

        raised: BaseException | None = None
        with mock.patch.dict(
            "sys.modules", {"databricks": mock.MagicMock(sql=fake_sql)}
        ), mock.patch.object(
            dbio,
            "_get_credentials",
            return_value=("host", "token", "/sql/path"),
        ):
            # databricks.sql import inside fetch_and_cache resolves to fake_sql
            import databricks  # noqa: F401

            try:
                meta = dbio.fetch_and_cache(
                    table=table_name,
                    http_path="/sql/path",
                    project_root=tdp,
                    batch_size=10,
                )
                results["fetch_returned"] = True
                results["fetch_row_count"] = meta.get("row_count")
            except BaseException as e:  # noqa: BLE001 - we want to classify it
                raised = e
                results["fetch_returned"] = False
                results["raised_type"] = type(e).__name__
                results["raised_repr"] = repr(e)

        explicit_handle.close()
        del reader_lf

        after_bytes = out_path.read_bytes()
        results["cache_unchanged"] = before_bytes == after_bytes
        results["is_permission_error"] = isinstance(raised, PermissionError)
        # Is the raised error a domain (HauteError) type or a raw OSError?
        try:
            from haute.errors import HauteError

            results["is_domain_error"] = isinstance(raised, HauteError)
        except Exception:
            results["is_domain_error"] = "n/a"

    return results


def main() -> int:
    print("=== Probe 1/2/3: open reader vs os.replace ===")
    r = probe_replace_with_reader()
    for k, v in r.items():
        print(f"  {k} = {v}")

    print("=== Probe 4: end-to-end fetch_and_cache with held reader ===")
    try:
        e2e = probe_end_to_end()
        for k, v in e2e.items():
            print(f"  {k} = {v}")
    except Exception as exc:  # setup/import failure -> NOT a reproduction
        print(f"  END_TO_END_SETUP_ERROR = {exc!r}")
        e2e = {"setup_error": True}

    print("=== VERDICT ANALYSIS ===")
    platform_enforces = bool(r.get("probe3_oshandle_blocks_replace"))
    lazy_blocks = bool(r.get("probe1_lazy_plan_blocks_replace"))
    collected_blocks = bool(r.get("probe2_collected_blocks_replace"))
    print(f"  platform_enforces_lock (probe3) = {platform_enforces}")
    print(f"  lazy_scan_plan_blocks (probe1)  = {lazy_blocks}")
    print(f"  collected_df_blocks (probe2)    = {collected_blocks}")

    e2e_repro = (
        e2e.get("fetch_returned") is False
        and e2e.get("is_permission_error") is True
        and e2e.get("cache_unchanged") is True
        and e2e.get("is_domain_error") is False
    )
    print(f"  end_to_end_reproduced = {e2e_repro}")

    # The CLAIM as written says read_cached_table's lazy plan keeps the file open
    # and that alone triggers the failure. The strongest honest assessment:
    #   - If probe3 True but probe1 False: platform enforces the lock, but the
    #     SPECIFIC reader named in the claim (lazy scan_parquet) does NOT hold a
    #     handle -> the claim's stated mechanism is wrong, though an explicit
    #     concurrent OS handle (e.g. an active collect, or another tool) WOULD
    #     trigger it.
    #   - end_to_end_reproduced confirms the real code path turns a complete
    #     fetch into a raw PermissionError when a hard OS handle is held.
    if e2e_repro and platform_enforces:
        print("  RESULT: replace-vs-open-handle bug is REAL on this platform.")
        if not lazy_blocks:
            print(
                "  CAVEAT: the claim's named reader (lazy scan_parquet plan) does"
                " NOT itself hold a handle; the trigger is an actual open OS"
                " handle (active collect / external reader)."
            )
        return 0
    print("  RESULT: not reproduced as a wrong-value bug.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
