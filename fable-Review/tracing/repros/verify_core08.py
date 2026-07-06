"""CORE-08 verification: tracing a multi-frame target.

A materialised multi-frame apiInput stores ``eager_outputs[nid] =
dict[label, DataFrame]`` (src/haute/_execute_lazy.py:1920). The trace code
assumes every eager output is a ``pl.DataFrame`` and calls ``.row(...)`` /
``set(df.columns)`` on it. This script drives the exact code paths with a
dict-valued target output and records what happens.
"""

from __future__ import annotations

import traceback

import polars as pl

from haute._trace_correlation import _correlate_rows_posthoc


def show(title: str) -> None:
    print("\n" + "=" * 8 + " " + title + " " + "=" * 8)


def main() -> None:
    # A multi-frame node's eager output, exactly as line 1920 builds it.
    multi = {
        "policies": pl.DataFrame({"vehicle": ["car"], "premium": [100.0]}),
        "claims": pl.DataFrame({"claim_id": [7], "amount": [50.0]}),
    }

    # ---- Path 1: _correlate_rows_posthoc with the multi-frame node as target
    show("Path 1: _correlate_rows_posthoc(target=multi-frame)")
    eager_outputs = {"api": multi}
    order = ["api"]
    parents_of: dict[str, list[str]] = {"api": []}
    print("len(dict target) =", len(multi), "(number of frames, not rows)")
    print("row_index=0 < len -> passes the bounds check at :685")
    try:
        _correlate_rows_posthoc(
            eager_outputs,  # type: ignore[arg-type]
            order,
            parents_of,
            "api",
            0,
            node_map={},
        )
        print("NO EXCEPTION (unexpected)")
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        print(f"RAISED: {type(exc).__name__}: {exc}")
        # last two frames of the traceback
        print("traceback tail:")
        for line in tb.strip().splitlines()[-4:]:
            print("   " + line)

    # ---- Path 2: the execute_trace row-verify block (trace.py:471-475)
    show("Path 2: execute_trace row-verify block logic (trace.py:472-475)")
    target_df = eager_outputs["api"]  # a dict
    row_index = 0
    try:
        if row_index < len(target_df):  # len(dict)=2, passes
            actual_row = target_df.row(row_index, named=True)  # type: ignore[attr-defined]
            print("actual_row:", actual_row)
    except Exception as exc:  # noqa: BLE001
        print(f"RAISED: {type(exc).__name__}: {exc}")

    # ---- Path 3: multi-frame node as an ANCESTOR (downstream target)
    show("Path 3: multi-frame node as ANCESTOR of a single-frame target")
    # 'child' consumes the 'policies' frame; 'api' is its parent and is
    # materialised (dict). Correlation walks back to 'api' at :711/:741.
    child_df = pl.DataFrame({"vehicle": ["car"], "premium": [100.0]})
    eager2 = {"api": multi, "child": child_df}
    order2 = ["api", "child"]
    parents2 = {"api": [], "child": ["api"]}
    try:
        _correlate_rows_posthoc(
            eager2,  # type: ignore[arg-type]
            order2,
            parents2,
            "child",
            0,
            node_map={},
        )
        print("NO EXCEPTION (ancestor dict tolerated?)")
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        print(f"RAISED: {type(exc).__name__}: {exc}")
        for line in tb.strip().splitlines()[-4:]:
            print("   " + line)


if __name__ == "__main__":
    main()
