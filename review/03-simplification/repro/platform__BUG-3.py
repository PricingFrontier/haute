"""Isolated reproduction for BUG-3.

Claim: ``infer_output_schema`` (src/haute/deploy/_schema.py:144-148) wraps the
schema-cache WRITE in a bare ``except Exception: pass`` with NO logging, so a
real OSError on write (read-only / wrong-type cache dir, permission error,
interrupted write) is silently discarded -- asymmetric with the sibling
cache-READ path (line 105-106) which logs a ``corrupt_schema_cache`` warning,
and contrary to the project's fail-loud mandate.

This repro drives the REAL ``infer_output_schema`` (no reimplementation) and:
  * forces the cache write to raise a genuine OSError by placing a regular FILE
    where the code expects to create the ``.haute_cache`` directory, so
    ``cache_path.parent.mkdir(parents=True, exist_ok=True)`` raises
    NotADirectoryError / FileExistsError (both OSError subclasses);
  * captures the real structlog event stream to prove NOTHING is logged about
    the write failure;
  * asserts the function returns the schema normally (swallowed, no raise) and
    that no cache artifact was produced.

All disk I/O is confined to a tempfile dir; no rating/, src/, tests/ or real
project files are touched. score_graph is patched to a synthetic 1-row frame so
no model / MLflow is needed.

Exit 0  == bug reproduced (write error silently swallowed, zero log events).
Exit 1  == refuted (function raised, OR the write failure was logged/surfaced).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import polars as pl
import structlog

from haute.deploy._schema import infer_output_schema
from haute.graph_utils import PipelineGraph


def _build_graph(parquet_path: Path) -> PipelineGraph:
    """Minimal apiInput -> output graph, built the production way."""
    return PipelineGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "src",
                        "nodeType": "apiInput",
                        "config": {"path": str(parquet_path)},
                    },
                },
                {
                    "id": "out",
                    "data": {"label": "out", "nodeType": "output", "config": {}},
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "out"}],
        }
    )


def main() -> int:
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Run inside the tempdir because the cache path is CWD-relative
        # (".haute_cache/output_schema.json").
        os.chdir(tmp)
        try:
            # Synthetic 1-row input source.
            pq = tmp / "data.parquet"
            pl.DataFrame({"x": [1.0]}).write_parquet(pq)
            graph = _build_graph(pq)

            # Sabotage the cache directory: put a regular FILE at ".haute_cache"
            # so the code's `cache_path.parent.mkdir(...)` (line 145) raises a
            # real OSError. This models a read-only/wrong-type cache location.
            blocker = tmp / ".haute_cache"
            blocker.write_text("i am a file, not a directory")
            assert blocker.is_file()

            # Synthetic dry-run result -> output schema {"premium": Float64}.
            mock_result = pl.DataFrame({"premium": [100.0]})

            # Independently confirm the cache WRITE truly raises OSError here,
            # so we know the except-block is actually exercised (not skipped).
            cache_path = Path(".haute_cache/output_schema.json")
            write_raised: BaseException | None = None
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text("{}")
            except OSError as exc:
                write_raised = exc
            # Clean up if (unexpectedly) the probe wrote something.
            if cache_path.exists():
                cache_path.unlink()

            assert write_raised is not None and isinstance(write_raised, OSError), (
                "Setup error: the cache write did not raise OSError as designed; "
                "cannot exercise the except: pass branch."
            )
            print(f"[setup] cache write raises {type(write_raised).__name__}: {write_raised}")

            # Drive the REAL function while capturing the real structlog stream.
            with structlog.testing.capture_logs() as logs:
                with patch(
                    "haute.deploy._scorer.score_graph", return_value=mock_result
                ):
                    result = infer_output_schema(graph, "out", ["src"])

            # ---- Assertion 1: write failure was SWALLOWED (no raise). --------
            assert result == {"premium": "Float64"}, (
                f"Expected schema returned despite write failure; got {result!r}"
            )
            print(f"[A] infer_output_schema returned normally: {result}")

            # ---- Assertion 2: nothing was actually cached. -------------------
            # (.haute_cache is still our blocker file; no real cache exists.)
            assert blocker.is_file(), "blocker file unexpectedly replaced"
            print("[B] no cache artifact produced (write was a no-op)")

            # ---- Assertion 3: ZERO log events mention the write failure. -----
            # The sibling READ path logs event 'corrupt_schema_cache' at warning;
            # the WRITE path logs NOTHING. Prove the asymmetry: no warning/error
            # event referencing the cache write/path was emitted at all.
            offending = [
                e
                for e in logs
                if e.get("log_level") in {"warning", "error", "critical"}
                or "cache" in str(e.get("event", "")).lower()
            ]
            event_names = [e.get("event") for e in logs]
            print(f"[C] structlog events emitted during call: {event_names}")
            assert not offending, (
                "Write failure WAS surfaced via a log event -- claim refuted: "
                f"{offending!r}"
            )

            # Stronger: there is literally no event whose name hints at the
            # write failure (no 'corrupt'/'cache_write'/'schema_cache' sibling).
            failure_hints = [
                e
                for e in logs
                if any(
                    k in str(e.get("event", "")).lower()
                    for k in ("corrupt", "cache_write", "schema_cache", "write_fail")
                )
            ]
            assert not failure_hints, (
                f"A write-failure log event exists -- claim refuted: {failure_hints!r}"
            )

            print(
                "[D] No warning/error and no failure-named event for the swallowed "
                "OSError -> bare `except Exception: pass` confirmed (asymmetric with "
                "the read-path `corrupt_schema_cache` warning at line 106)."
            )
            print("REPRODUCED: cache-write exception silently swallowed with no log.")
            return 0
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    raise SystemExit(main())
