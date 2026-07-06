import polars as pl, tempfile, os
from haute.graph_utils import GraphNode, NodeData, GraphEdge, PipelineGraph
from haute.trace import execute_trace

tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "policies.parquet")
pl.DataFrame({"region": ["north"], "policy_id": [1]}).write_parquet(p)

def source(nid, path):
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="dataSource", config={"path": path}))

rating = GraphNode(id="rate", data=NodeData(label="Rate", nodeType="ratingStep", config={
    "tables": [{
        "name": "region_rate",
        "factors": ["region"],
        "entries": [{"region": "north", "value": 1.1}, {"region": "south", "value": 0.9}],
        "outputColumn": "rate",
        "defaultValue": None,
    }],
    "code": "df = df.with_columns(rate=pl.col('rate') * 2)",
}))
g = PipelineGraph.model_validate({
    "nodes": [source("data", p).model_dump(), rating.model_dump()],
    "edges": [GraphEdge(id="e", source="data", target="rate").model_dump()],
})
res = execute_trace(g, row_index=0, target_node_id="rate", column="rate")
step = [s for s in res.steps if s.node_id == "rate"][0]
print("ENGINE actual output rate:", step.output_values.get("rate"), "(table gave 1.1, post-code x2 = 2.2)")
nd = step.node_detail
t0 = nd["tables"][0]
print("  selected_value:", t0.get("selected_value"), " rate_value:", t0.get("rate_value"))
print("  matched:", t0.get("matched"), " status:", t0.get("status"))
print("  matched_entry:", t0.get("matched_entry"))
print("  top-level rate_value:", nd.get("rate_value"), " matched:", nd.get("matched"))
print(">> table looked up 1.1; trace selected_value/rate_value =", t0.get("rate_value"),
      "; matched_entry.value =", (t0.get("matched_entry") or {}).get("value"))
