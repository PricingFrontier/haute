"""Isolated reproduction for V009.

Claim: `_find_with_columns_calls` collects with_columns calls via `ast.walk(tree)`,
which is BREADTH-FIRST. For a single chained expression

    df.with_columns(A).with_columns(B)

the OUTER call (B) is the *parent* node of the INNER call (A), so ast.walk yields
B BEFORE A. The consumer loops (parse_expression / _compute_result_impl) use a
"last match wins" strategy over that walk order, so the loop's "last" is the
inner/earliest call A -- the definition Polars OVERWRITES -- instead of B, the
effective definition Polars actually applies last.

Polars ground truth: B wins (a + 100). The parser claims A (a + 1). We assert the
parser reports the demonstrably WRONG value, and that the equivalent code written
as two SEPARATE statements (sibling nodes, source order preserved by ast.walk)
reports the CORRECT value -- isolating the chained form + ast.walk ordering.

ISOLATION: pure in-memory AST parsing; no disk I/O, no project root, no
rating/src/tests files. Polars is used only as an independent oracle for the
ground-truth value.
"""

import ast

import polars as pl

from haute._expression_parser import evaluate_expression, parse_expression

# --- Polars ground truth (independent oracle) -----------------------------
# Chained: with_columns(a+1 as out) THEN with_columns(a+100 as out).
# Polars applies them left-to-right; the SECOND (a+100) wins.
df = pl.DataFrame({"a": [10]})
df = df.with_columns((pl.col("a") + 1).alias("out")).with_columns(
    (pl.col("a") + 100).alias("out")
)
polars_truth = df["out"][0]
print(f"[oracle] Polars effective value of 'out' = {polars_truth}")
assert polars_truth == 110, f"sanity: Polars should yield 110, got {polars_truth}"

# --- Confirm the ast.walk ordering hypothesis directly --------------------
chained_code = (
    'df = df.with_columns((pl.col("a") + 1).alias("out"))'
    '.with_columns((pl.col("a") + 100).alias("out"))'
)
tree = ast.parse(chained_code)
walk_order = []
for node in ast.walk(tree):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "with_columns"
    ):
        dumped = ast.dump(node.args[0])
        walk_order.append("+100" if "100" in dumped else "+1")
print(f"[walk] _find_with_columns_calls order (BFS) = {walk_order}")
# BFS yields the OUTER (+100, effective) call FIRST, INNER (+1, shadowed) LAST.
assert walk_order == ["+100", "+1"], (
    f"hypothesis: walk yields outer-first; got {walk_order}"
)

# --- Parser on the CHAINED form (the bug) ---------------------------------
parsed = parse_expression(chained_code, "out")
assert parsed is not None
print(f"[chained] parse_expression.expression_text = {parsed.expression_text!r}")

evaluated = evaluate_expression(chained_code, "out", {"a": 10})
print(f"[chained] evaluate_expression.result_value   = {evaluated.result_value}")
print(f"[chained] evaluate_expression.substituted_text = {evaluated.substituted_text!r}")

# --- Parser on the SEPARATE-STATEMENT control (works correctly) -----------
separate_code = (
    'df = df.with_columns((pl.col("a") + 1).alias("out"))\n'
    'df = df.with_columns((pl.col("a") + 100).alias("out"))'
)
parsed_sep = parse_expression(separate_code, "out")
assert parsed_sep is not None
eval_sep = evaluate_expression(separate_code, "out", {"a": 10})
print(f"[control] parse_expression.expression_text = {parsed_sep.expression_text!r}")
print(f"[control] evaluate_expression.result_value = {eval_sep.result_value}")

# =========================================================================
# ASSERTIONS proving the specific WRONG value for the chained form.
# =========================================================================

# 1) The control (separate statements) MUST be correct -- proves the parser
#    is fully capable of resolving "last definition wins" when source order is
#    preserved, so any failure below is attributable to ast.walk ordering.
assert "100" in parsed_sep.expression_text, (
    f"control expression_text should reflect a+100, got {parsed_sep.expression_text!r}"
)
assert eval_sep.result_value == 110, (
    f"control result_value should be 110 (matches Polars), got {eval_sep.result_value}"
)

# 2) The CHAINED form selects the SHADOWED inner definition (a + 1) -- WRONG.
#    Expected (Polars truth): a + 100 / 110.  Actual (bug): a + 1 / 11.
assert evaluated.result_value != polars_truth, (
    "BUG NOT REPRODUCED: chained result_value matched Polars truth "
    f"({evaluated.result_value}); expected the parser to be wrong."
)
assert evaluated.result_value == 11, (
    "Expected the confidently-wrong shadowed value 11 (a+1 with a=10), "
    f"got {evaluated.result_value}."
)
assert "100" not in parsed.expression_text, (
    "Expected chained expression_text to show the WRONG shadowed def (a+1, "
    f"no '100'), got {parsed.expression_text!r}."
)

print()
print("BUG REPRODUCED:")
print(f"  Polars truth     : out = {polars_truth}  (a + 100)")
print(f"  Parser (chained) : out = {evaluated.result_value}  "
      f"({parsed.expression_text!r})  <-- WRONG, picked shadowed inner def")
print(f"  Parser (separate): out = {eval_sep.result_value}  "
      f"({parsed_sep.expression_text!r})  <-- correct control")
