import polars as pl, tempfile, os
from haute.graph_utils import GraphNode, NodeData, GraphEdge, PipelineGraph
from haute.trace import execute_trace

tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "d.parquet")
# base=100; two columns a and b both equal 100 (value-equality) to probe rename false positive
pl.DataFrame({"base": [100.0], "id": [1]}).write_parquet(p)

def source(nid, path):
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="dataSource", config={"path": path}))
def poly(nid, code):
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="polars", config={"code": code}))

# --- LEAD #7: two distinct columns with EQUAL values, NO rename syntax ---
n1 = poly("mk_a", "df = df.with_columns(a=pl.col('base'))")       # a = base (=100) pure ref -> this IS a rename per _detect_rename
n2 = poly("mk_b", "df = df.with_columns(b=pl.col('base') * 1.0)") # b = base*1.0 (=100) arithmetic, not pure ref
g = PipelineGraph.model_validate({
    "nodes": [source("data", p).model_dump(), n1.model_dump(), n2.model_dump()],
    "edges": [GraphEdge(id="e1", source="data", target="mk_a").model_dump(),
              GraphEdge(id="e2", source="mk_a", target="mk_b").model_dump()],
})
res = execute_trace(g, row_index=0, target_node_id="mk_b", column="b")
for s in res.steps:
    print(f"step {s.node_id}: calc={ (s.calculation or {}).get('original_name', None) }, rename_chain={ (s.calculation or {}).get('rename_chain') }")

print("\n--- WATERFALL: multiplicative chain 100 -> *1.2 -> *1.0(no-op) -> *0.9 ---")
pl.DataFrame({"premium": [100.0], "id":[1]}).write_parquet(p)
m1 = poly("base", "df = df.with_columns(premium=pl.col('premium'))")  # carries premium
m2 = poly("uplift", "df = df.with_columns(premium=pl.col('premium') * 1.2)")
m3 = poly("region", "df = df.with_columns(premium=pl.col('premium') * 1.0)")  # no-op factor
m4 = poly("discount", "df = df.with_columns(premium=pl.col('premium') * 0.9)")
g2 = PipelineGraph.model_validate({
    "nodes": [source("data", p).model_dump(), m1.model_dump(), m2.model_dump(), m3.model_dump(), m4.model_dump()],
    "edges": [GraphEdge(id="e0", source="data", target="base").model_dump(),
              GraphEdge(id="e1", source="base", target="uplift").model_dump(),
              GraphEdge(id="e2", source="uplift", target="region").model_dump(),
              GraphEdge(id="e3", source="region", target="discount").model_dump()],
})
res2 = execute_trace(g2, row_index=0, target_node_id="discount", column="premium")
print("output_value:", res2.output_value, "(expect 100*1.2*1.0*0.9=108)")
import json
print("waterfall:", json.dumps(res2.waterfall, default=str, indent=1) if isinstance(res2.waterfall, dict) else [(e['label'], e['operation'], round(e['value'],4), round(e['cumulative'],4)) for e in res2.waterfall])
