"""Shared graph builders for trace perf benchmarks.

Builds realistic pipelines backed by parquet source files so execute_trace
runs the true cold + warm paths in-process (no server).
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

SCRATCH = Path(
    r"C:\Users\prici\AppData\Local\Temp\claude\C--Users-prici-haute"
    r"\3887407c-e101-4b47-bf1f-6df135883d11\scratchpad"
)


def _src(nid: str, path: str) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="dataSource",
                     config={"path": path, "sourceType": "flat_file"}))


def _polars(nid: str, code: str) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="polars", config={"code": code}))


def _edge(src: str, tgt: str, sh: str | None = None, th: str | None = None) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt,
                     sourceHandle=sh, targetHandle=th)


def write_source(rows: int, cols: int, name: str) -> str:
    """Write a parquet file with `rows` and `cols` numeric+key columns."""
    data = {
        "policy_id": pl.arange(0, rows, eager=True),
        "region": pl.Series([f"region_{i % 8}" for i in range(rows)]),
        "premium_base": pl.Series([100.0 + i * 0.5 for i in range(rows)]),
    }
    for c in range(cols - 3):
        data[f"feat_{c}"] = pl.Series([float(i % 100) * 1.001 + c for i in range(rows)])
    df = pl.DataFrame(data)
    p = SCRATCH / name
    df.write_parquet(p)
    return str(p)


def linear_chain_graph(rows: int, cols: int, n_transforms: int) -> tuple[PipelineGraph, str, str]:
    """Source -> N with_columns transforms. Returns (graph, target_id, traced_col)."""
    src_path = write_source(rows, cols, f"src_linear_{rows}x{cols}.parquet")
    nodes = [_src("s0", src_path)]
    edges = []
    prev = "s0"
    for i in range(n_transforms):
        # each node derives a new column from premium_base + prior derived col
        if i == 0:
            code = "df = df.with_columns(factor_0 = pl.col('premium_base') * 1.1)"
        else:
            code = (
                f"df = df.with_columns("
                f"factor_{i} = pl.col('factor_{i-1}') * 1.05 + pl.col('feat_0'))"
            )
        nid = f"t{i}"
        nodes.append(_polars(nid, code))
        edges.append(_edge(prev, nid))
        prev = nid
    g = PipelineGraph(nodes=nodes, edges=edges)
    return g, prev, f"factor_{n_transforms-1}"


def diamond_join_graph(rows: int, cols: int) -> tuple[PipelineGraph, str, str]:
    """Source -> two branches -> edgeJoin -> transform. Exercises the join
    correlation path (row identity change => value matching + key-unique gate)."""
    src_path = write_source(rows, cols, f"src_diamond_{rows}x{cols}.parquet")
    nodes = [_src("s0", src_path)]
    edges = []
    # branch A: compute burn_cost
    nodes.append(_polars("a", "df = df.with_columns(burn_cost = pl.col('premium_base') * 0.7)"))
    edges.append(_edge("s0", "a"))
    # branch B: compute competitor, then sort (reordering) to force value-matching
    nodes.append(_polars("b", "df = df.with_columns(competitor = pl.col('premium_base') * 1.2)"))
    edges.append(_edge("s0", "b"))
    nodes.append(_polars("b_sorted",
                         "df = df.select(['policy_id','competitor']).sort('competitor')"))
    edges.append(_edge("b", "b_sorted"))
    # edge join a (base) + b_sorted (join) on policy_id
    nodes.append(GraphNode(id="j", data=NodeData(label="j", nodeType="edgeJoin", config={
        "baseInput": "a", "joinInput": "b_sorted", "on": ["policy_id"], "how": "left",
    })))
    edges.append(_edge("a", "j", th="base"))
    edges.append(_edge("b_sorted", "j", th="join"))
    # final transform
    nodes.append(_polars("final",
                         "df = df.with_columns(premium = pl.col('burn_cost') + pl.col('competitor'))"))
    edges.append(_edge("j", "final"))
    g = PipelineGraph(nodes=nodes, edges=edges)
    return g, "final", "premium"


if __name__ == "__main__":
    from haute.trace import execute_trace, trace_result_to_dict

    g, target, col = linear_chain_graph(1000, 20, 12)
    df_target = None
    # cold
    res = execute_trace(g, row_index=500, target_node_id=target, column=col,
                        row_values=None, preview=None)
    d = trace_result_to_dict(res)
    print("linear cold OK: steps", len(res.steps), "traced", col, "=", res.output_value)
    # warm (cache hit)
    res2 = execute_trace(g, row_index=500, target_node_id=target, column=col,
                         row_values=None, preview=None)
    print("linear warm OK: exec_ms", res2.execution_ms)

    g2, t2, c2 = diamond_join_graph(1000, 20)
    res3 = execute_trace(g2, row_index=100, target_node_id=t2, column=c2,
                         row_values=None, preview=None)
    print("diamond cold OK: steps", len(res3.steps), "traced", c2, "=", res3.output_value)
