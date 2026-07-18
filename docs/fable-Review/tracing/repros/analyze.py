"""Analyze the trace payload for the self-referential calc bug + waterfall."""
from __future__ import annotations
import json, tempfile, os, sys, io
import polars as pl
from haute.graph_utils import GraphNode, NodeData, GraphEdge, PipelineGraph
from haute.trace import execute_trace, trace_result_to_dict

tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "data.parquet")
pl.DataFrame({
    "quote_id": ["q_001"], "base_rate": [100.0],
    "area_factor": [1.2], "age_factor": [0.9], "region": ["north"],
}).write_parquet(p)

def src(nid, path):
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="dataSource", config={"path": path}))
def tf(nid, label, code):
    return GraphNode(id=nid, data=NodeData(label=label, nodeType="polars", config={"code": code}))
def edge(s, t):
    return GraphEdge(id=f"e_{s}_{t}", source=s, target=t)

graph = PipelineGraph.model_validate({
    "nodes": [
        src("src", p),
        tf("base", "Base Rate", "df = df.with_columns(premium=pl.col('base_rate'))"),
        tf("area", "Area Loading", "df = df.with_columns(premium=pl.col('premium') * pl.col('area_factor'))"),
        tf("age", "Age Discount", "df = df.with_columns(premium=pl.col('premium') * pl.col('age_factor'))"),
    ],
    "edges": [edge("src", "base"), edge("base", "area"), edge("area", "age")],
})
result = execute_trace(graph, row_index=0, target_node_id="age", column="premium")
d = trace_result_to_dict(result)

out = []
out.append(f"output_value: {d['output_value']}")
out.append(f"waterfall: {json.dumps(d['waterfall'], default=str)}")
for s in d['steps']:
    c = s.get('calculation') or {}
    out.append(f"--- {s['node_id']} ({s['node_name']}) type={s['node_type']} lineage={s.get('row_lineage_type')}")
    out.append(f"    modifies_premium={'premium' in s['schema_diff']['columns_modified']} added={'premium' in s['schema_diff']['columns_added']} out_premium={s['output_values'].get('premium')}")
    if c:
        out.append(f"    substituted_text={c.get('substituted_text')!r}  result_value={c.get('result_value')!r}")
sys.stdout.write("\n".join(out) + "\n")
