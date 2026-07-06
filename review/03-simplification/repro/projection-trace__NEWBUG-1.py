"""Adversarial repro for NEWBUG-1.

CLAIM: enrich_steps `_col_in_code` regex `re.search(rf"\b{col}\s*=", raw_code)`
matches comparison operators (>=, <=, ==, !=), so a node that only COMPARES the
traced column in a with_columns body is wrongly treated as the column's creating
step, causing parse_expression/evaluate_expression to attribute a bogus
'calculation' to premium at the wrong node (or a spurious parse-error marker)
instead of treating it as pass-through.

This repro:
  Part A. Tests the EXACT regex from src/haute/_trace_enrichment.py:1492 against
          all four operators the claim names, for BOTH the idiomatic
          `pl.col('premium')` reference AND a bare `premium` reference.
  Part B. Drives the REAL parse_expression / evaluate_expression the way
          enrich_steps does (code = _wrap_node_code(raw_code)) for the node that
          only COMPARES premium, and asserts on the actual downstream payload to
          see whether a *bogus calculation* is attributed to premium.

Read-only: imports production functions, builds synthetic in-memory strings.
No disk I/O, no rating/src/tests writes.
"""

import re

from haute._trace_enrichment import _wrap_node_code
from haute import _expression_parser as ep


# ---- The exact regex predicate from _trace_enrichment.py:1490-1492 ----
def col_in_code(column: str, raw_code: str) -> bool:
    if column and raw_code and ".with_columns(" in raw_code:
        return bool(re.search(rf"\b{re.escape(column)}\s*=", raw_code))
    return False


COL = "premium"

# Idiomatic Polars references (pl.col('premium')) -- what real graphs contain.
idiomatic = {
    ">=": "df = df.with_columns(flag=pl.when(pl.col('premium') >= 100).then(1).otherwise(0))",
    "<=": "df = df.with_columns(flag=pl.when(pl.col('premium') <= 100).then(1).otherwise(0))",
    "==": "df = df.with_columns(flag=pl.when(pl.col('premium') == 100).then(1).otherwise(0))",
    "!=": "df = df.with_columns(flag=pl.when(pl.col('premium') != 100).then(1).otherwise(0))",
}

# Bare-name references (premium, no pl.col) -- non-idiomatic but the only shape
# that could conceivably put `premium` adjacent to whitespace-then-operator.
bare = {
    ">=": "df = df.with_columns(flag=pl.when(premium >= 100).then(1).otherwise(0))",
    "<=": "df = df.with_columns(flag=pl.when(premium <= 100).then(1).otherwise(0))",
    "==": "df = df.with_columns(flag=pl.when(premium == 100).then(1).otherwise(0))",
    "!=": "df = df.with_columns(flag=pl.when(premium != 100).then(1).otherwise(0))",
}

print("=== Part A: regex predicate (_col_in_code) ===")
print("-- idiomatic pl.col('premium') --")
idiomatic_hits = {}
for op, code in idiomatic.items():
    hit = col_in_code(COL, code)
    idiomatic_hits[op] = hit
    print(f"  {op:>2s}  _col_in_code = {hit}")

print("-- bare 'premium' --")
bare_hits = {}
for op, code in bare.items():
    hit = col_in_code(COL, code)
    bare_hits[op] = hit
    print(f"  {op:>2s}  _col_in_code = {hit}")

# The claim states >=, <=, ==, != ALL match. Test that assertion.
claim_all_four_match = all(idiomatic_hits.values())
print(f"\nclaim 'all four ops match (idiomatic)': {claim_all_four_match}")

# FACT 1: idiomatic references match for NONE of the operators (premium is
# followed by a quote, not whitespace+'=').
assert idiomatic_hits == {">=": False, "<=": False, "==": False, "!=": False}, idiomatic_hits

# FACT 2: even for bare references, ONLY '==' matches. >=, <=, != do NOT, because
# the char after the whitespace is '>','<','!' -- not the '=' the regex requires.
assert bare_hits == {">=": False, "<=": False, "==": True, "!=": False}, bare_hits
print("CONFIRMED: regex matches ONLY '==' and ONLY with a bare-name reference;")
print("           >=, <=, != never match; the idiomatic pl.col('premium') never matches.")

print("\n=== Part B: downstream effect for the one matching case (bare '==') ===")
# Take the single case where _col_in_code is True: bare `premium == 100`.
raw_code = bare["=="]
assert col_in_code(COL, raw_code) is True
code = _wrap_node_code(raw_code)

# Reproduce exactly what enrich_steps does at lines 1493-1532 for the target col.
parsed = ep.parse_expression(code, COL)
row_values = {"premium": 250.0}  # observed pass-through value of premium at node
evaluated = ep.evaluate_expression(code, COL, row_values)

print(f"parse_expression.expression_type   = {parsed.expression_type!r}")
print(f"parse_expression.expression_text   = {parsed.expression_text!r}")
print(f"parse_expression.referenced_columns= {parsed.referenced_columns!r}")
print(f"evaluate_expression.expression_type= {evaluated.expression_type!r}")
print(f"evaluate_expression.result_value   = {evaluated.result_value!r}")
print(f"evaluate_expression.substituted_text = {evaluated.substituted_text!r}")

# The CLAIMED harm is one of:
#   (a) a bogus 'calculation' attributed to premium (a wrong computed number), or
#   (b) a spurious parse-error marker.
# Check both. parse_expression NEVER raises (returns opaque), so (b) cannot occur
# via parse_expression. The node assigns alias 'flag', not 'premium', so the
# target-column search finds no match and returns an OPAQUE EMPTY expression.

# (b) no spurious parse-error marker:
assert "error" not in (parsed.__dict__ if hasattr(parsed, "__dict__") else {})
# parse_expression result is opaque with empty text -> no bogus expression text
assert parsed.expression_type == "opaque", parsed.expression_type
assert parsed.expression_text == "", repr(parsed.expression_text)
assert parsed.referenced_columns == [], parsed.referenced_columns

# (a) no bogus computed number: evaluate falls through to the pass-through value
# row_values['premium'] (250.0), i.e. it does NOT fabricate a 'flag'-style
# computed result for premium. result_value equals the observed pass-through.
assert evaluated.result_value == 250.0, evaluated.result_value
assert evaluated.expression_type == "opaque", evaluated.expression_type

print("\nCONFIRMED: even when _col_in_code wrongly fires (bare '=='), parse_expression")
print("           returns an OPAQUE EMPTY expression for premium (no bogus calc text),")
print("           and evaluate_expression returns the pass-through value 250.0 (NOT a")
print("           fabricated number) with NO parse-error marker.")
print("\nVERDICT SUPPORT: The regex's claimed broad match of >=/<=/!= is FALSE; only")
print("'==' on a non-idiomatic bare column name spuriously matches, and even then the")
print("downstream parse/evaluate degrade to opaque/pass-through -- NOT the claimed")
print("'bogus calculation' or 'spurious parse error'. No observable wrong value.")
print("\nALL ASSERTIONS PASSED")
