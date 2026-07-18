# Reproduction / DISPROOF for NEWBUG-03.
#
# CLAIM: _strip_docstring (src/haute/_ast_helpers.py:203-231) mis-detects the
# docstring terminator because its close test `opening_quote in stripped`
# (line 215) uses SUBSTRING containment, so a body line that merely CONTAINS
# the 3-char triple-quote run (or the OPPOSITE quote style) makes the
# terminator fire early/late, silently swallowing real user code as docstring
# and corrupting the persisted code box on round-trip.
#
# METHOD: For each synthetic, VALID-PYTHON function body we compute the GROUND
# TRUTH set of lines that come after the real docstring using the AST
# (ast.get_docstring + the docstring node's end_lineno -- the exact path the
# claim says is correct and available), then compare it to _strip_docstring's
# output. If the heuristic ever diverges on valid Python and that divergence
# drops/keeps the wrong lines, the bug is REAL.
#
# UPSTREAM FACT: parser.py:144 does ast.parse(source) (raises on invalid
# Python) and _extract_function_bodies slices bodies out of validated AST
# nodes -- so _strip_docstring ONLY ever receives valid-Python body text.
# Inside valid Python a triple-quote run can NEVER appear inside a docstring
# body of the SAME quote (it would terminate the literal), and the close test
# keys on the SAME opening_quote, so the opposite-quote run never triggers it.
#
# Read-only: imports the real helper; synthetic in-memory sources only; never
# touches src/ tests/ rating/ or real project files.

import ast
import sys

from haute._ast_helpers import _strip_docstring

DQ = '"' * 3  # triple double-quote, assembled so THIS file stays parseable
SQ = "'" * 3  # triple single-quote


def oracle_after_docstring(body_src):
    """Ground-truth lines after the leading docstring, via the AST."""
    func_src = "def _f():\n" + "\n".join("    " + ln for ln in body_src.splitlines())
    func = ast.parse(func_src).body[0]  # raises if body_src is not valid Python
    assert isinstance(func, ast.FunctionDef)
    body_lines = body_src.splitlines()
    if ast.get_docstring(func, clean=False) is None:
        return body_lines  # no docstring -> all lines are user code
    # First statement is the docstring; lines strictly after its end are code.
    return body_lines[func.body[0].end_lineno - 1 :]


def check(name, body_src):
    got = _strip_docstring(body_src.splitlines())
    expected = oracle_after_docstring(body_src)
    ok = got == expected
    print("--- " + name + " ---")
    print(body_src)
    print("  AST-oracle keeps : " + repr(expected))
    print("  _strip_docstring : " + repr(got))
    print("  AGREES           : " + str(ok) + "\n")
    return ok


CASES = {
    # Claim core: """ docstring, then user code whose first line carries the
    # OPPOSITE triple-quote run as a string literal.
    "mixed-quote-after": DQ + "Docstring one.\nstill doc.\n" + DQ
    + "\nmarker = " + SQ + " opposite-quote literal " + SQ + "\nreturn marker",

    # """ docstring, then user code whose line contains the SAME triple run
    # as a substring -- carried inside a single-quoted literal so the body is
    # itself valid Python (the only way a """ substring can legally sit on a
    # later line without re-opening a literal).
    "substring-after": DQ + "First.\nsecond.\n" + DQ
    + "\nsep = '" + DQ + "' + tail\nreturn sep",

    # Inverse: ''' docstring, then user code containing the """ run.
    "inverse-quotes": SQ + "Doc A.\nDoc B.\n" + SQ
    + "\nq = " + DQ + " later triple double " + DQ + "\nreturn q",

    # NOT a docstring: first stmt is a triple-quoted ASSIGNMENT (all code).
    "assign-not-docstring": "x = " + DQ + "hello" + DQ + "\nreturn x",

    # One-line docstring fast-path (line 221), then real code.
    "oneline-fastpath": DQ + "one liner" + DQ + "\nreturn 1",

    # Adjacent triple-quoted literals: only the FIRST is the docstring.
    "adjacent-concat": DQ + "part one " + DQ + "\n" + DQ + "part two" + DQ + "\nreturn 7",

    # Docstring then user code that opens a fresh MULTI-LINE triple string.
    "user-multiline-after": DQ + "Doc." + DQ
    + "\nq = " + DQ + "\nmulti\nline\n" + DQ + "\nreturn q",

    # """ docstring whose BODY contains the opposite run on its own line
    # (must NOT close early).
    "opposite-run-in-body": DQ + "intro\n" + SQ + " opp run on its own line " + SQ
    + "\nstill doc\n" + DQ + "\nreturn 9",

    # ''' docstring whose body contains the """ run; user code after also
    # leads with the opposite run.
    "embedded-opposite": SQ + "doc with embedded " + DQ + " inside\nmore " + DQ
    + " text\n" + SQ + "\n" + DQ + "userval" + DQ + "\nreturn 0",

    # Indented multi-line terminator (close test uses .strip()).
    "indented-terminator": DQ + "Doc start.\n   body.\n   " + DQ + "\nreturn 2",
}

results = {name: check(name, src) for name, src in CASES.items()}

print("=" * 64)
print("SUMMARY (True = _strip_docstring matches the AST ground truth):")
for k, v in results.items():
    print("  " + k.ljust(24) + ": " + str(v))

disagreements = [k for k, v in results.items() if not v]
print()
if disagreements:
    print("BUG REPRODUCED: heuristic diverges from AST oracle on: " + repr(disagreements))
    sys.exit(0)
else:
    print(
        "CLAIM REFUTED: _strip_docstring matched the AST ground truth on EVERY "
        "valid-Python case, including every mixed-quote and substring-containment "
        "scenario named in the claim. No code is swallowed; no round-trip corruption."
    )
    sys.exit(1)
