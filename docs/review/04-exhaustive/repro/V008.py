"""Reproduction for V008.

Claim: ``_ExprConverter`` (the AST->human-readable text renderer in
``src/haute/_expression_parser.py``) drops parentheses that are semantically
required, so the rendered ``expression_text`` re-parses (as ordinary Python)
to a DIFFERENT value than the original Polars expression.

Strategy (ISOLATED, no disk I/O, no project files): drive the *real*
``parse_expression`` with small code strings, then prove the rendered text is
both (a) the exact wrong string the finder predicted, and (b) numerically
WRONG by re-parsing it as plain Python with concrete column values and
comparing against the value the *source* AST would produce.

We assert on demonstrably wrong VALUES, not merely that "something differs".
"""

from __future__ import annotations

import ast
from typing import Any

from haute._expression_parser import parse_expression


# ---------------------------------------------------------------------------
# Tiny independent oracle: evaluate a plain-Python arithmetic/bool AST with a
# set of variable bindings. This is *standard* Python semantics, used to show
# what the rendered string actually means when read back.
# ---------------------------------------------------------------------------
def py_eval(text: str, env: dict[str, Any]) -> Any:
    """Evaluate `text` under standard Python semantics with `env` bindings."""
    return eval(compile(ast.parse(text, mode="eval"), "<repro>", "eval"), {}, dict(env))


def render(code: str, target: str) -> str:
    parsed = parse_expression(code, target)
    assert parsed is not None
    return parsed.expression_text


failures: list[str] = []


def check_case(
    name: str,
    code: str,
    target: str,
    predicted_render: str,
    source_py: str,
    env: dict[str, Any],
) -> None:
    """Render `code`, compare to the finder's predicted (buggy) string, and
    show the rendered string re-parses to a different number than `source_py`
    (the original semantics)."""
    rendered = render(code, target)
    source_val = py_eval(source_py, env)
    rendered_val = py_eval(rendered, env)
    matches_prediction = rendered == predicted_render
    semantically_wrong = rendered_val != source_val
    status = "BUG" if (matches_prediction and semantically_wrong) else "ok"
    print(f"[{status}] {name}")
    print(f"      rendered      = {rendered!r}")
    print(f"      predicted     = {predicted_render!r}  (match={matches_prediction})")
    print(f"      source_py     = {source_py!r} -> {source_val!r}")
    print(f"      rendered eval = {rendered_val!r}  (source={source_val!r})")
    if not matches_prediction:
        failures.append(f"{name}: render {rendered!r} != predicted {predicted_render!r}")
    if not semantically_wrong:
        failures.append(
            f"{name}: rendered evaluates to {rendered_val!r} which EQUALS source "
            f"{source_val!r} -- not a wrong value"
        )


# ---------------------------------------------------------------------------
# (1) Compare operand inside arithmetic: (a > b) + 1
#     Source: (a > b) + 1  -> (True/False) + 1
#     Rendered 'a > b + 1' re-parses as a > (b + 1) -> a boolean.
# ---------------------------------------------------------------------------
check_case(
    name="compare-in-binop  (a > b) + 1",
    code='df = df.with_columns(((pl.col("a") > pl.col("b")) + 1).alias("r"))',
    target="r",
    predicted_render="a > b + 1",
    source_py="(a > b) + 1",
    env={"a": 5, "b": 2},  # source: (5>2)+1 = 2 ; rendered: 5 > (2+1) = True
)

# ---------------------------------------------------------------------------
# (2) Unary-minus as power base: (-a) ** 2
#     Source: (-a) ** 2 -> positive for even power.
#     Rendered '-a ** 2' re-parses as -(a ** 2) -> sign flips.
# ---------------------------------------------------------------------------
check_case(
    name="unary-base-pow    (-a) ** 2",
    code='df = df.with_columns(((-pl.col("a")) ** 2).alias("r"))',
    target="r",
    predicted_render="-a ** 2",
    source_py="(-a) ** 2",
    env={"a": 3},  # source: (-3)**2 = 9 ; rendered: -(3**2) = -9
)

# ---------------------------------------------------------------------------
# (3) Right-associative ** : (a ** b) ** c
#     Source: (a ** b) ** c
#     Rendered 'a ** b ** c' re-parses as a ** (b ** c).
# ---------------------------------------------------------------------------
check_case(
    name="pow-assoc         (a ** b) ** c",
    code='df = df.with_columns(((pl.col("a") ** pl.col("b")) ** pl.col("c")).alias("r"))',
    target="r",
    predicted_render="a ** b ** c",
    source_py="(a ** b) ** c",
    env={"a": 2, "b": 3, "c": 2},  # source: (2**3)**2 = 64 ; rendered: 2**(3**2)=512
)

# ---------------------------------------------------------------------------
# (4) IfExp operand: (a if c else b) + 1
#     Source: (a if c else b) + 1
#     Rendered 'a if c else b + 1' re-parses as a if c else (b + 1).
# ---------------------------------------------------------------------------
check_case(
    name="ifexp-in-binop    (a if c else b) + 1",
    code='df = df.with_columns(((pl.col("a") if pl.col("c") else pl.col("b")) + 1).alias("r"))',
    target="r",
    predicted_render="a if c else b + 1",
    source_py="(a if c else b) + 1",
    # c truthy -> source: (a if c else b) + 1 = a + 1 = 101 ;
    # rendered 'a if c else b + 1' parses as a if c else (b+1) = a = 100.
    env={"a": 100, "b": 10, "c": 1},
)

# ---------------------------------------------------------------------------
# (5) BoolOp precedence: (a or b) and c
#     Source: (a or b) and c
#     Rendered 'a or b and c' re-parses as a or (b and c).
# ---------------------------------------------------------------------------
check_case(
    name="boolop-precedence (a or b) and c",
    code='df = df.with_columns(((pl.col("a") or pl.col("b")) and pl.col("c")).alias("r"))',
    target="r",
    predicted_render="a or b and c",
    source_py="(a or b) and c",
    # a truthy, c falsy: source (a or b) and c = c = 0 (falsy) ; rendered a or (b and c) = a = 1
    env={"a": 1, "b": 1, "c": 0},
)

print()
if failures:
    print("REPRO RESULT: NOT fully reproduced -- discrepancies:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
else:
    print("REPRO RESULT: REPRODUCED -- every rendered string matches the predicted")
    print("buggy output AND re-parses to a numerically different value than the source.")
