# Reproduction for V030.
#
# CLAIM: _strip_docstring (src/haute/_ast_helpers.py:203-231) swallows the
# ENTIRE function body when a single-line triple-quoted docstring is followed
# by a trailing comment on the same line (e.g.  """Doc."""  # note ).
#
# Root cause: the single-line fast-path at :221 requires
# `stripped.endswith(opening_quote)`.  With a trailing comment the line ends
# with the comment text, not `"""`, so the `else` branch sets
# in_docstring=True.  From then on the close test at :215 looks for a line
# that *contains* `"""`, but the only such line was the (already consumed)
# opener, so every remaining body line hits `continue` (:218) and is never
# appended.  Result: cleaned == [] and the user's real code is lost.
#
# METHOD (mirrors the REFUTED NEWBUG-03 oracle exactly): for each
# VALID-PYTHON function body we compute the GROUND-TRUTH "lines after the
# docstring" from the AST (ast.get_docstring + the docstring node's
# end_lineno) and compare to _strip_docstring's output.  We ALSO drive the
# real downstream consumer (extract_user_code, kind="polars") with a body
# assembled by the genuine parser helper _extract_function_bodies, to prove
# the user's code is silently dropped on a parse->extract round trip.
#
# READ-ONLY / ISOLATED: imports the real helpers; all sources are synthetic
# and in-memory; never reads or writes src/ tests/ rating/ or any project
# file; no disk I/O at all.

import ast
import sys

from haute._ast_helpers import _strip_docstring, _extract_function_bodies
from haute._code_extraction import extract_user_code

DQ = '"' * 3  # triple double-quote, assembled so THIS file stays parseable

failures: list[str] = []


def oracle_lines_after_docstring(body_src: str) -> list[str]:
    """Ground-truth lines after the leading docstring, via the AST.

    Identical contract to review/03-simplification/repro/parser__NEWBUG-03.py.
    """
    func_src = "def _f():\n" + "\n".join("    " + ln for ln in body_src.splitlines())
    func = ast.parse(func_src).body[0]  # raises if body_src is not valid Python
    assert isinstance(func, ast.FunctionDef)
    body_lines = body_src.splitlines()
    if ast.get_docstring(func, clean=False) is None:
        return body_lines  # no docstring -> all lines are user code
    return body_lines[func.body[0].end_lineno - 1 :]


# ---------------------------------------------------------------------------
# Part 1 — pure-function divergence on _strip_docstring
# ---------------------------------------------------------------------------
# A single-line docstring with a trailing comment, then two real body lines.
# This is valid Python and ast.get_docstring recognises 'Doc.' as the
# docstring, so the body the heuristic must preserve is ['x = 1', 'return x'].
BODY = '    ' + DQ + 'Doc.' + DQ + '  # trailing comment\n    x = 1\n    return x'

got = _strip_docstring(BODY.splitlines())
expected = oracle_lines_after_docstring(BODY)

print("=== Part 1: _strip_docstring vs AST oracle ===")
print("body source:")
print(BODY)
print("  AST-oracle keeps : " + repr(expected))
print("  _strip_docstring : " + repr(got))
print("  AGREES           : " + str(got == expected))
print()

# The bug prediction is precise: oracle keeps the two body lines, heuristic
# returns []. Assert on the WRONG VALUE, not merely that something differs.
if got == expected:
    failures.append("Part1: _strip_docstring matched the oracle (no bug)")
else:
    if got != []:
        failures.append(
            "Part1: _strip_docstring diverged but not in the predicted way; "
            "expected the heuristic to return [] (whole body swallowed), got "
            + repr(got)
        )
    # The lines the user wrote ('x = 1') must be present in a correct result
    # and are provably absent here.
    if any("x = 1" in ln for ln in got):
        failures.append("Part1: user line 'x = 1' survived; body not swallowed")


# ---------------------------------------------------------------------------
# Part 2 — downstream corruption through the real parser/extractor pipeline
# ---------------------------------------------------------------------------
# Assemble the function body exactly the way the parser does: parse the source
# once, hand the tree to _extract_function_bodies, then run extract_user_code.
SRC = (
    "def f(df):\n"
    "    " + DQ + "Doc." + DQ + "  # trailing comment\n"
    "    x = 1\n"
    "    return x\n"
)
tree = ast.parse(SRC)
bodies = _extract_function_bodies(SRC, tree=tree)
body_f = bodies["f"]

# extract_user_code(kind="polars") is the engine _extract_user_code wraps
# (src/haute/_code_extraction.py:998) — the path that persists user code into
# the node config box.
extracted = extract_user_code(body_f, kind="polars", param_names=("df",))

print("=== Part 2: extract_user_code(kind='polars') round trip ===")
print("body handed to extractor (from _extract_function_bodies):")
print(repr(body_f))
print("  extracted user code : " + repr(extracted))
print()

# Correct behaviour: the docstring is stripped and the user's statements
# survive, so 'x = 1' MUST appear in the extracted code. The bug drops the
# whole body and returns "".
if "x = 1" not in extracted:
    print(
        "  CORRUPTION CONFIRMED: user statement 'x = 1' was dropped; "
        "extract_user_code returned " + repr(extracted)
    )
else:
    failures.append(
        "Part2: user code survived extraction (extracted=" + repr(extracted) + ")"
    )

# Sanity control: the IDENTICAL body WITHOUT the trailing comment must extract
# correctly. If this control also failed, the divergence would not be
# attributable to the trailing comment.
SRC_OK = (
    "def f(df):\n"
    "    " + DQ + "Doc." + DQ + "\n"
    "    x = 1\n"
    "    return x\n"
)
bodies_ok = _extract_function_bodies(SRC_OK, tree=ast.parse(SRC_OK))
extracted_ok = extract_user_code(bodies_ok["f"], kind="polars", param_names=("df",))
print("  control (no trailing comment) extracted: " + repr(extracted_ok))
if "x = 1" not in extracted_ok:
    failures.append(
        "Part2-control: even without a trailing comment the body was dropped "
        "(extracted=" + repr(extracted_ok) + "); divergence is not isolated to "
        "the comment"
    )
print()


# ---------------------------------------------------------------------------
# Part 3 — the rarer BinOp variant from the finding
# ---------------------------------------------------------------------------
# `"""a""" + foo` is a BinOp, NOT a docstring per ast. The body that follows
# must be preserved in full. The heuristic, however, sees the opener line as a
# docstring start.
BODY_BINOP = '    ' + DQ + 'a' + DQ + ' + foo\n    return foo'
# Note: this requires `foo` to be defined for valid Python at parse time; we
# only need it to PARSE, so wrap with a binding in the oracle helper input.
BINOP_BODY_FOR_ORACLE = 'foo = 1\n' + DQ + 'a' + DQ + ' + foo\nreturn foo'
oracle_binop = oracle_lines_after_docstring(BINOP_BODY_FOR_ORACLE)
got_binop = _strip_docstring(BINOP_BODY_FOR_ORACLE.splitlines())

print("=== Part 3: triple-quoted BinOp first statement (not a docstring) ===")
print("body source:")
print(BINOP_BODY_FOR_ORACLE)
print("  AST-oracle keeps : " + repr(oracle_binop))
print("  _strip_docstring : " + repr(got_binop))
print("  AGREES           : " + str(got_binop == oracle_binop))
print()
# Per ast there is NO docstring here, so the oracle keeps every line. Whether
# the heuristic mishandles it is reported but not gating (the finding flags it
# as a rarer secondary variant); Part 1/2 are the load-bearing assertions.


# ---------------------------------------------------------------------------
print("=" * 64)
if failures:
    print("V030 NOT cleanly reproduced — discrepancies:")
    for f in failures:
        print("  - " + f)
    sys.exit(2)
else:
    print(
        "V030 REPRODUCED: a single-line docstring with a trailing comment makes "
        "_strip_docstring return [] (AST oracle keeps the real body), and "
        "extract_user_code silently drops the user's 'x = 1' statement while the "
        "comment-free control extracts it correctly."
    )
    sys.exit(0)
