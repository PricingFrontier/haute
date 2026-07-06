"""CORE-03 verification.

Finding claim: when one origin of the traced column has a PARSED expression
(non-empty referenced_columns) and another origin is OPAQUE (empty
referenced_columns) yet genuinely consumes a side-branch column, the targeted
column-relevance walk prunes the side-branch producer with no signal.

We build the finding's "most reachable" scenario end-to-end and check whether
the side-branch producer (`factor`) survives in result.steps.

Opaque origin trick: wrap the with_columns producing `premium` in `if True:`.
`_has_control_flow_wrapping_target` then makes parse_expression return an
opaque ParsedExpression (empty referenced_columns) for `premium` at that node,
while Polars still executes the block and multiplies in `factor`.
"""

from __future__ import annotations

import polars as pl

from haute.trace import execute_trace, parse_expression
from tests.conftest import make_edge as _edge
from tests.conftest import make_graph as _g
from tests.conftest import make_source_node as _source_node
from tests.conftest import make_transform_node as _transform_node


def build_graph(tmp: str, final_code: str):
    base_path = f"{tmp}/base.parquet"
    factor_path = f"{tmp}/factor.parquet"
    pl.DataFrame({"quote_id": [1], "base": [100]}).write_parquet(base_path)
    pl.DataFrame({"quote_id": [1], "factor": [1.2]}).write_parquet(factor_path)
    return _g(
        {
            "nodes": [
                _source_node("base", base_path),
                _transform_node("calc", "df = df.with_columns(premium=pl.col('base') * 10)"),
                _source_node("factor", factor_path),
                _transform_node("join", "df = calc.join(factor, on='quote_id', how='left')"),
                _transform_node("final", final_code),
                _transform_node("sink"),
            ],
            "edges": [
                _edge("base", "calc"),
                _edge("calc", "join"),
                _edge("factor", "join"),
                _edge("join", "final"),
                _edge("final", "sink"),
            ],
        }
    )


def main() -> None:
    import tempfile

    tmp = tempfile.mkdtemp()

    # ---- Sanity: confirm the two 'final' codes parse as claimed ----
    parsed_transparent = parse_expression(
        "df = df.with_columns((pl.col('premium') * pl.col('factor')).alias('premium'))",
        "premium",
    )
    opaque_code = (
        "if True:\n"
        "    df = df.with_columns((pl.col('premium') * pl.col('factor')).alias('premium'))"
    )
    parsed_opaque = parse_expression(opaque_code, "premium")
    print("== parser check ==")
    print("transparent.referenced_columns:", parsed_transparent.referenced_columns,
          "type:", parsed_transparent.expression_type)
    print("opaque.referenced_columns:", parsed_opaque.referenced_columns,
          "type:", parsed_opaque.expression_type)

    # ---- Case A: transparent 2nd origin (control) ----
    gA = build_graph(
        tmp,
        "df = df.with_columns((pl.col('premium') * pl.col('factor')).alias('premium'))",
    )
    rA = execute_trace(gA, column="premium")
    idsA = [s.node_id for s in rA.steps]
    print("\n== Case A: transparent final ==")
    print("steps:", idsA)
    print("factor present:", "factor" in idsA)
    print("final premium:", rA.steps[-1].output_values.get("premium"))

    # ---- Case B: OPAQUE 2nd origin (the finding's scenario) ----
    gB = build_graph(tmp, opaque_code)
    rB = execute_trace(gB, column="premium")
    idsB = [s.node_id for s in rB.steps]
    print("\n== Case B: OPAQUE final (finding scenario) ==")
    print("steps:", idsB)
    print("factor present:", "factor" in idsB)
    print("final premium:", rB.steps[-1].output_values.get("premium"))
    # Report per-origin expression state actually observed by the pruner
    for s in rB.steps:
        if s.node_id in ("calc", "final"):
            refs = None if s.expression is None else s.expression.get("referenced_columns")
            print(f"  origin {s.node_id}: added={s.schema_diff.columns_added} "
                  f"modified={s.schema_diff.columns_modified} refs={refs}")

    print("\n== VERDICT DATA ==")
    print("factor pruned in opaque case:", "factor" not in idsB)


if __name__ == "__main__":
    main()
