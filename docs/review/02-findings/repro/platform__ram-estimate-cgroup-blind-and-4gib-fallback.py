"""Adversarial repro for claim `ram-estimate-cgroup-blind-and-4gib-fallback`.

Two falsifiable assertions are checked against the REAL production code in
``haute._ram_estimate`` (nothing under src/ or tests/ is modified):

A. ``available_ram_bytes()`` silently falls back to a FIXED 4 GiB when every
   detection path fails on a non-win32 platform — regardless of the machine's
   true RAM. We monkeypatch ``open('/proc/meminfo')`` and ``os.sysconf`` to
   raise and assert the returned value is EXACTLY 4 * 1024**3.

B. The downsample DECISION in ``estimate_safe_training_rows`` flips purely on
   that fallback value, not on real capacity. We build a real parquet source
   whose estimated peak comfortably fits a real 64 GiB host but does NOT fit
   the 4 GiB fallback. We then run the real estimator twice:
     * available = 64 GiB  -> expect was_downsampled == False (fits)
     * available = 4 GiB    -> expect was_downsampled == True, safe_row_limit
                               floored toward _MIN_SAFE_ROWS (corruption)
   The flip is driven ONLY by the value `available_ram_bytes` returns, which
   in a cgroup-limited container / sandboxed CI is the wrong number.

ISOLATION: all disk I/O is via tempfile; the graph is synthetic and in memory;
haute's project root is pointed at the tmp dir via ``haute._sandbox.set_project_root``.
No real project / rating / src / tests file is read or written.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import polars as pl

import haute._sandbox as _sandbox
from haute._ram_estimate import (
    _MIN_SAFE_ROWS,
    _BYTES_PER_COL,
    _OVERHEAD_MULTIPLIER,
    _RAM_SAFETY_FACTOR,
    available_ram_bytes,
    estimate_safe_training_rows,
)
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph

FOUR_GIB = 4 * 1024**3
SIXTY_FOUR_GIB = 64 * 1024**3


def _make_source_node(node_id: str, path: str, selected_columns: list[str]) -> GraphNode:
    return GraphNode(
        id=node_id,
        type="custom",
        position={"x": 0, "y": 0},
        data=NodeData(
            label="quotes",
            nodeType="dataSource",
            # selected_columns makes _resolve_target_columns return len(sel)
            # so we get a wide column count without writing a wide file.
            config={
                "path": path,
                "sourceType": "flat_file",
                "selected_columns": selected_columns,
            },
        ),
    )


def _make_modelling_node(node_id: str = "m1") -> GraphNode:
    return GraphNode(
        id=node_id,
        type="custom",
        position={"x": 0, "y": 200},
        data=NodeData(label="model", nodeType="modelling", config={}),
    )


def main() -> int:
    failures: list[str] = []

    # ----------------------------------------------------------------- #
    # Assertion A: fixed 4 GiB fallback on non-win32 with all paths dead #
    # ----------------------------------------------------------------- #
    # Pretend we are on a 64 GiB FreeBSD-like box where neither /proc nor
    # sysconf is available. The TRUE RAM is irrelevant: the code returns 4 GiB.
    with patch.object(sys, "platform", "freebsd13"):
        with patch("builtins.open", side_effect=OSError("proc blocked")):
            with patch("os.sysconf", side_effect=AttributeError("no sysconf"), create=True):
                fallback = available_ram_bytes()

    print(f"[A] available_ram_bytes() with all paths dead = {fallback} bytes "
          f"({fallback / 1024**3:.2f} GiB); expected {FOUR_GIB} (4.00 GiB)")
    if fallback != FOUR_GIB:
        failures.append(
            f"[A] expected fixed fallback {FOUR_GIB}, got {fallback}"
        )

    # ----------------------------------------------------------------- #
    # Assertion B: the downsample decision flips on the fallback value   #
    # ----------------------------------------------------------------- #
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _sandbox.set_project_root(tmp_path)

        # Size the dataset so peak sits BETWEEN 0.7*4GiB and 0.7*64GiB.
        #   peak = rows * cols * _BYTES_PER_COL * _OVERHEAD_MULTIPLIER
        # We make a wide column count cheaply via selected_columns and write
        # only a small (2-column) parquet whose row_count drives `total_rows`.
        n_cols = 200
        n_rows = 2_000_000  # ~16 MB on disk as 2 int columns; metadata row_count=2M
        cols = [f"f{i}" for i in range(n_cols)]

        # peak in bytes for the chosen shape (matches _estimate_peak_bytes math)
        peak = int(n_rows * n_cols * _BYTES_PER_COL * _OVERHEAD_MULTIPLIER)
        usable_64 = int(SIXTY_FOUR_GIB * _RAM_SAFETY_FACTOR)
        usable_4 = int(FOUR_GIB * _RAM_SAFETY_FACTOR)
        print(
            f"[B] shape rows={n_rows:,} cols={n_cols} -> peak={peak / 1024**3:.2f} GiB | "
            f"usable@64GiB={usable_64 / 1024**3:.2f} GiB | usable@4GiB={usable_4 / 1024**3:.2f} GiB"
        )
        # Sanity: the scenario is only meaningful if peak fits 64 but not 4.
        assert peak <= usable_64, "test misconfigured: peak should fit a real 64 GiB box"
        assert peak > usable_4, "test misconfigured: peak should NOT fit the 4 GiB fallback"

        parquet_path = tmp_path / "big.parquet"
        # Write 2M rows of two int columns only — cheap; footer reports 2M rows.
        pl.DataFrame({"a": range(n_rows), "b": range(n_rows)}).write_parquet(str(parquet_path))

        src = _make_source_node("src1", str(parquet_path), cols)
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        # Real 64 GiB host: should NOT downsample.
        with patch("haute._ram_estimate.available_ram_bytes", return_value=SIXTY_FOUR_GIB):
            res_64 = estimate_safe_training_rows(graph, target.id)

        # cgroup-limited / sandboxed CI collapses available to the 4 GiB fallback.
        with patch("haute._ram_estimate.available_ram_bytes", return_value=FOUR_GIB):
            res_4 = estimate_safe_training_rows(graph, target.id)

        print(
            f"[B] @64GiB: was_downsampled={res_64.was_downsampled} "
            f"safe_row_limit={res_64.safe_row_limit} total_rows={res_64.total_rows} "
            f"n_columns={res_64.probe_columns}"
        )
        print(
            f"[B] @4GiB : was_downsampled={res_4.was_downsampled} "
            f"safe_row_limit={res_4.safe_row_limit} total_rows={res_4.total_rows} "
            f"n_columns={res_4.probe_columns}"
        )

        # The decision must flip purely on the available-RAM value.
        if res_64.was_downsampled:
            failures.append(
                "[B] @64GiB unexpectedly downsampled — scenario does not isolate the fallback"
            )
        if not res_4.was_downsampled:
            failures.append(
                "[B] @4GiB did NOT downsample — claim that fallback flips the decision is refuted"
            )
        else:
            # The truncated training set is floored toward _MIN_SAFE_ROWS and is
            # a tiny fraction of the real 2M rows.
            if res_4.safe_row_limit is None or res_4.safe_row_limit < _MIN_SAFE_ROWS:
                failures.append(
                    f"[B] safe_row_limit {res_4.safe_row_limit} not floored to >= "
                    f"_MIN_SAFE_ROWS ({_MIN_SAFE_ROWS})"
                )
            if res_4.safe_row_limit is not None and res_4.safe_row_limit >= res_4.total_rows:
                failures.append(
                    "[B] safe_row_limit did not actually truncate the training set"
                )

    print()
    if failures:
        print("REPRO RESULT: CLAIM NOT FULLY SUBSTANTIATED")
        for f in failures:
            print("  FAIL:", f)
        return 1

    print("REPRO RESULT: CLAIM REPRODUCED")
    print(
        "  - available_ram_bytes() returns a fixed 4 GiB when detection fails "
        "(host-RAM-blind, cgroup-blind by construction: only reads /proc/meminfo "
        "MemAvailable, never cgroup limits)."
    )
    print(
        "  - estimate_safe_training_rows downsamples a 2,000,000-row source to "
        f"{_MIN_SAFE_ROWS}-floored rows SOLELY because available collapsed to 4 GiB; "
        "the identical graph is NOT downsampled at 64 GiB."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
