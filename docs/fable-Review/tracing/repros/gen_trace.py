"""Generate a real trace payload for a multiplicative rating chain."""
from __future__ import annotations
import json, tempfile, os
import polars as pl
from haute.graph_utils import GraphNode, NodeData
from haute.trace import execute_trace, trace_result_to_dict

tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "data.parquet")
pl.DataFrame({
    "quote_id": ["q_001"],
    "base_rate": [100.0],
    "area_factor": [1.2],
    "age_factor": [0.9],
    "region": ["north"],
}).write_parquet(p)

def src(nid, path):
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="dataSource", config={"path": path}))
def tf(nid, label, code):
    return GraphNode(id=nid, data=NodeData(label=label, nodeType="polars", config={"code": code}))
def edge(s, t):
    from haute.graph_utils import GraphEdge
    return GraphEdge(id=f"e_{s}_{t}", source=s, target=t)

from haute.graph_utils import PipelineGraph
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
payload = trace_result_to_dict(result)
print(json.dumps(payload, indent=2, default=str))
