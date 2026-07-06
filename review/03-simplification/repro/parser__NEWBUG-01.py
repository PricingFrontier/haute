"""Isolated reproduction for NEWBUG-01.

Claim: in _substitute_names_in_ast, every ast.copy_location call is written
    ast.copy_location(node, new_node)
with arguments REVERSED. The documented signature is
    ast.copy_location(new_node, old_node)  -> copies position FROM old_node INTO new_node, returns new_node.

So as written:
  (a) the freshly-built substituted node (new_node) is NEVER given a source position
      (it has no lineno after the call), and
  (b) the ORIGINAL node's location is clobbered with new_node's MISSING position
      (an unintended mutation of a shared AST node), and copy_location returns the
      ORIGINAL node (which is then discarded — the function returns new_node).

This repro imports the REAL function from src and exercises the BinOp path via a
reassignment chain (expr = pl.col("base"); expr = expr * pl.col("factor")).
No project data / no disk writes / no rating/ touched.
"""

import ast
import sys

# --- 1. Establish the ground-truth semantics of ast.copy_location -------------
# copy_location(new_node, old_node): position comes from old_node (2nd arg).
old = ast.parse("x", mode="eval").body          # a Name with lineno/col_offset set
old.lineno, old.col_offset = 7, 3
old.end_lineno, old.end_col_offset = 7, 4
fresh = ast.Name(id="y", ctx=ast.Load())        # freshly built: NO position
assert not hasattr(fresh, "lineno")
returned = ast.copy_location(fresh, old)        # CORRECT order
assert returned is fresh                        # returns the FIRST arg
assert fresh.lineno == 7 and fresh.col_offset == 3
print("baseline: copy_location(new, old) -> new gets old's pos, returns new  OK")

# --- 2. Drive the REAL code under test ----------------------------------------
sys.path.insert(0, "src")
from haute._expression_parser import _substitute_names_in_ast  # noqa: E402

# Symbol table entry for `expr` = pl.col("base"); it carries a real position.
base_expr = ast.parse('pl.col("base")', mode="eval").body
assert hasattr(base_expr, "lineno"), "table value should have a position"

# The reassignment-chain value node: expr * pl.col("factor")
# Here `expr` (the BinOp.left Name) will be substituted by base_expr.
chain_value = ast.parse('expr * pl.col("factor")', mode="eval").body
assert isinstance(chain_value, ast.BinOp)
orig_binop_lineno = chain_value.lineno
orig_binop_col = chain_value.col_offset
assert orig_binop_lineno is not None

table = {"expr": base_expr}
result = _substitute_names_in_ast(chain_value, table)

# A substitution happened, so a NEW BinOp must have been returned.
assert isinstance(result, ast.BinOp)
assert result is not chain_value, "expected a freshly-built BinOp (substitution occurred)"

# --- CLAIM (a): the returned substituted node has NO source position ----------
# Because new_node was the 2nd arg (the "source"), its missing/None position was
# copied INTO the original; the new node itself was never given a position.
new_has_lineno = hasattr(result, "lineno")
print(f"substituted new BinOp has lineno attr? {new_has_lineno}")
assert new_has_lineno is False, (
    "BUG NOT REPRODUCED: substituted node unexpectedly HAS a lineno "
    "(would mean copy_location order was correct)"
)
# Concrete downstream consequence: the substituted AST cannot be compiled, and any
# future feature that ast.unparse()-with-positions / fix_missing_locations / reports
# a source span from it will read a missing field.
try:
    compile(ast.Expression(body=result), "<substituted>", "eval")
    compiled_ok = True
except TypeError as exc:
    compiled_ok = False
    compile_err = str(exc)
print(f"compile(substituted AST) succeeded? {compiled_ok}")
assert compiled_ok is False and "lineno" in compile_err, (
    "expected compile() to fail with a missing 'lineno' field on the substituted node"
)

# --- CLAIM (b): the ORIGINAL node was mutated (unintended shared-AST side effect)
# NOTE: With CPython's copy_location, lineno/col_offset are NOT overwritten when the
# source value is None (skipped), so the original keeps lineno/col_offset. BUT the
# end_lineno/end_col_offset branch copies even None values, so those ARE clobbered.
print(
    f"original BinOp lineno/col unchanged? "
    f"{(chain_value.lineno, chain_value.col_offset) == (orig_binop_lineno, orig_binop_col)}"
)
print(f"original BinOp end_lineno after mutation = {chain_value.end_lineno!r}")
print(f"original BinOp end_col_offset after mutation = {chain_value.end_col_offset!r}")
assert chain_value.end_lineno is None and chain_value.end_col_offset is None, (
    "BUG NOT REPRODUCED: original node's end_lineno/end_col_offset were not clobbered"
)

print()
print("=== NEWBUG-01 REPRODUCED ===")
print(" - Reversed copy_location args (node, new_node) confirmed via real src function.")
print(" - substituted (returned) node: lineno/col_offset MISSING -> compile() raises,")
print("   so any position-reading consumer of the substituted AST breaks.")
print(f" - original node end-span clobbered: end_lineno {1!r}->{chain_value.end_lineno!r},")
print(f"   end_col_offset 23->{chain_value.end_col_offset!r} (unintended shared-AST mutation).")
print(" Root cause: ast.copy_location(node, new_node) has arguments reversed;")
print(" correct call is ast.copy_location(new_node, node).")
