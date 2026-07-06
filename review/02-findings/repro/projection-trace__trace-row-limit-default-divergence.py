"""Adversarial repro for claim `trace-row-limit-default-divergence`.

Claim: ``execute_trace``'s Python default ``row_limit=1000`` (trace.py:315)
diverges from the API/preview default of 100 (TraceRequest.row_limit and
PreviewNodeRequest.row_limit, schemas.py:301/:255). Because ``row_limit`` is
embedded in BOTH the executor preview-cache key (executor.py:902) and the
preview-reuse keys that trace.py reconstructs (trace.py:412), a direct caller
that omits ``row_limit`` reconstructs preview fingerprints embedding ``1000``
and therefore MISSES a preview warmed at ``100`` -> cold full execution, and
correlates against a different source-row sample than the user saw.

This repro is fully isolated:
  * project root is redirected to a tempdir via haute._sandbox.set_project_root
  * NO real project file (rating/, src/, tests/) is read or written
  * the graph + data are tiny synthetic in-memory objects
  * the preview cache is a recording stub implementing the PreviewReader
    protocol (try_get) — no executor singleton, no disk

It ASSERTS on specific wrong behaviour:
  (A) the default-row_limit trace never queries the warmed (row_limit=100)
      fingerprint -> the route-warmed preview cache is unreachable (cold path);
  (B) an explicit row_limit=100 trace DOES query that fingerprint and reuses
      the cached sentinel output (proving the miss in (A) is purely the
      row_limit token, not some other key component);
  (C) the two reconstructed preview fingerprints differ, and differ purely by
      the ``1000`` vs ``100`` token in the extra-key string.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import polars as pl


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="haute-trace-rowlimit-"))

    import haute._sandbox as sandbox

    sandbox.set_project_root(tmp)

    from haute._cache import graph_fingerprint
    from haute.execution import runtime_input_extra_keys
    from haute.executor import ENFORCE_CONTRACTS
    from haute.graph_utils import GraphNode, NodeData
    from haute.trace import execute_trace
    from haute._types import PipelineGraph

    # ------------------------------------------------------------------
    # Tiny synthetic graph: one polars node. Its REAL execution produces
    # the value 111 (the "cold" answer). The warmed preview snapshot below
    # carries a *sentinel* value 999 so HIT vs MISS is value-distinguishable.
    # ------------------------------------------------------------------
    node = GraphNode(
        id="only",
        data=NodeData(
            label="only",
            nodeType="polars",
            config={"code": "df = pl.DataFrame({'v': [111]}).lazy()"},
        ),
    )
    graph = PipelineGraph(nodes=[node], edges=[])

    runtime_extra = runtime_input_extra_keys(graph)
    contracts_token = int(ENFORCE_CONTRACTS)

    # The executor warms the preview cache under THIS exact key for a full
    # preview (no target_preview_only, no requested columns => empty suffix);
    # see executor.py:902-909.
    def preview_fp_for(row_limit: int) -> str:
        base = f"{row_limit}:live:contracts={contracts_token}"
        return graph_fingerprint(graph, base, *runtime_extra)

    warmed_fp_100 = preview_fp_for(100)
    reconstructed_fp_1000 = preview_fp_for(1000)

    # ------------------------------------------------------------------
    # Recording stub preview reader. Holds the sentinel snapshot ONLY under
    # the row_limit=100 fingerprint (what the route warms). Records every
    # fingerprint queried so we can prove which keys the trace looks up.
    # ------------------------------------------------------------------
    sentinel_df = pl.DataFrame({"v": [999]})  # distinct from cold value 111

    class RecordingReader:
        def __init__(self) -> None:
            self.queried: list[str] = []

        def try_get(self, fingerprint: str) -> dict[str, Any] | None:
            self.queried.append(fingerprint)
            if fingerprint == warmed_fp_100:
                return {
                    "eager_outputs": {"only": sentinel_df},
                    "order": ["only"],
                    "parents_of": {"only": []},
                    "node_map": {"only": node},
                    "source_ids": {"only"},
                }
            return None

    # ---- Call 1: DEFAULT row_limit (the claimed 1000) -----------------
    reader_default = RecordingReader()
    result_default = execute_trace(
        graph,
        target_node_id="only",
        column="v",
        preview=reader_default,
        # row_limit intentionally OMITTED -> exercises the in-code default.
    )
    default_output = float(result_default.output_value)

    # ---- Call 2: EXPLICIT row_limit=100 (route default) ---------------
    reader_100 = RecordingReader()
    result_100 = execute_trace(
        graph,
        target_node_id="only",
        column="v",
        row_limit=100,
        preview=reader_100,
    )
    output_100 = float(result_100.output_value)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    print("warmed_fp_100        =", warmed_fp_100[:16])
    print("reconstructed_fp_1000=", reconstructed_fp_1000[:16])
    print("default-call queried warmed_fp_100? ",
          warmed_fp_100 in reader_default.queried)
    print("rl=100-call queried warmed_fp_100?  ",
          warmed_fp_100 in reader_100.queried)
    print("default-call output_value =", default_output,
          "(999=reused sentinel / 111=cold exec)")
    print("rl=100-call  output_value =", output_100,
          "(999=reused sentinel / 111=cold exec)")

    # ------------------------------------------------------------------
    # (A) The default-row_limit trace must NEVER query the warmed fp ->
    #     it cannot reuse the route-warmed preview cache (cold path).
    # ------------------------------------------------------------------
    assert warmed_fp_100 not in reader_default.queried, (
        "EXPECTED the default-row_limit trace to miss the row_limit=100 "
        "preview key, but it queried it — divergence refuted."
    )
    # And it falls through to a COLD execution (real value 111, not sentinel).
    assert default_output == 111.0, (
        f"EXPECTED cold-exec value 111 from the default-row_limit trace, "
        f"got {default_output}"
    )

    # ------------------------------------------------------------------
    # (B) The explicit row_limit=100 trace DOES query the warmed fp and
    #     reuses the cached sentinel (999) — proving the miss in (A) is
    #     purely the row_limit token, not an unrelated key mismatch.
    # ------------------------------------------------------------------
    assert warmed_fp_100 in reader_100.queried, (
        "EXPECTED the row_limit=100 trace to query the warmed preview key."
    )
    assert output_100 == 999.0, (
        f"EXPECTED reused sentinel value 999 from the row_limit=100 trace, "
        f"got {output_100} — reuse path did not engage, repro setup invalid."
    )

    # ------------------------------------------------------------------
    # (C) The two reconstructed preview fingerprints differ, and differ
    #     PURELY by the 1000-vs-100 token (same graph, same suffix, same
    #     runtime keys). Prove this by swapping the token and matching.
    # ------------------------------------------------------------------
    assert warmed_fp_100 != reconstructed_fp_1000, (
        "EXPECTED row_limit=100 and row_limit=1000 to yield different preview "
        "fingerprints; they matched — divergence refuted."
    )
    # Token-swap proof: rebuilding the 100 key from the 1000 key's components
    # by only changing the leading integer reproduces warmed_fp_100 exactly.
    base_1000 = f"1000:live:contracts={contracts_token}"
    base_100_swapped = base_1000.replace("1000", "100", 1)
    swapped_fp = graph_fingerprint(graph, base_100_swapped, *runtime_extra)
    assert swapped_fp == warmed_fp_100, (
        "EXPECTED swapping only the row_limit token (1000->100) to reproduce "
        "the warmed fingerprint; it did not — the divergence is not purely the "
        "row_limit token."
    )

    print()
    print("REPRO CONFIRMED: execute_trace's default row_limit=1000 makes a "
          "direct caller miss a row_limit=100-warmed preview cache (cold path) "
          "and the divergence is purely the row_limit token.")


if __name__ == "__main__":
    main()
