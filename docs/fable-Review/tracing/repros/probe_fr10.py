# FR-10 verification: does _build_input_sources re-evaluate shared subtrees
# once per branch (visited=set(visited) defeats memoisation)?
import haute.trace as t
from haute._trace_enrichment import _build_input_sources
from haute.trace import TraceStep, SchemaDiff

# Count evaluate_expression calls
calls = {"parse": 0, "eval": 0}
orig_parse = t.parse_expression
orig_eval = t.evaluate_expression
def counting_parse(code, col):
    calls["parse"] += 1
    return orig_parse(code, col)
def counting_eval(code, col, values, preamble_ns=None):
    calls["eval"] += 1
    return orig_eval(code, col, values, preamble_ns=preamble_ns)
t.parse_expression = counting_parse
t.evaluate_expression = counting_eval

def mk_step(nid, added, code_cols, in_vals, out_vals):
    return TraceStep(
        node_id=nid, node_name=nid, node_type="polars",
        schema_diff=SchemaDiff(columns_added=added, columns_removed=[], columns_modified=[], columns_passed=[]),
        input_values=in_vals, output_values=out_vals,
    )

# Diamond: target references A and B; A references shared S; B references shared S.
# S is created once upstream. Expect S evaluated ONCE if memoised, TWICE if not.
from haute.graph_utils import GraphNode, NodeData
def node(nid, code):
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="polars", config={"code": code}))

node_map = {
    "S": node("S", "df = df.with_columns(s=pl.col('base') + 1)"),
    "A": node("A", "df = df.with_columns(a=pl.col('s') * 2)"),
    "B": node("B", "df = df.with_columns(b=pl.col('s') * 3)"),
    "T": node("T", "df = df.with_columns(t=pl.col('a') + pl.col('b'))"),
}
base = mk_step("base_src", ["base"], [], {}, {"base": 10})
S = mk_step("S", ["s"], [], {"base": 10}, {"base": 10, "s": 11})
A = mk_step("A", ["a"], [], {"s": 11}, {"s": 11, "a": 22})
B = mk_step("B", ["b"], [], {"s": 11}, {"s": 11, "b": 33})
T = mk_step("T", ["t"], [], {"a": 22, "b": 33}, {"a": 22, "b": 33, "t": 55})
all_steps = [base, S, A, B, T]
node_map["base_src"] = node("base_src", "")

res = _build_input_sources(["a", "b"], T, all_steps, node_map, None)
print("eval calls:", calls["eval"])
print("Does 's' appear under both a and b?",
      "s" in res.get("a", {}).get("input_sources", {}),
      "s" in res.get("b", {}).get("input_sources", {}))
# If memoised across branches, 's' would be built once; the second branch would skip it.
