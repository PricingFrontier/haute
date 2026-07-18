"""
ISOLATED reproduction for NEWBUG-02.

Claim: parseBranches (frontend/src/trace/CalculationHero.tsx:73-102) mis-splits a
conditional whose `then` value itself contains the token ` when ` (a string literal,
a column like `flagged_when_high`, or a nested when/then the backend rendered inline).
The regex lookahead terminates a branch's `then` at the next ` when `/` otherwise `/end,
so the result is cut early and/or a spurious extra branch is started => the trace
waterfall renders the WRONG number of branches and may dim/highlight the wrong arm.

This is a PURE STRING-PARSING bug. The branch-splitting logic in CalculationHero.tsx
is exactly the regex below and nothing else touches it (no escaping/sanitising guard
upstream; the text comes straight from backend expression_text/substituted_text). So a
faithful port of the SAME regex with the SAME flags is a valid reproduction of the
production splitting behaviour.

JS source (verbatim):
    const regex = /when\s+(.+?)\s+then\s+(.+?)(?=\s+when\s|\s+otherwise\s|$)/gi
    while ((match = regex.exec(text)) !== null) {
      branches.push({ condition: match[1].trim(), result: match[2].trim(), isOtherwise: false })
      lastIndex = regex.lastIndex
    }
    const otherwiseMatch = text.slice(lastIndex).match(/otherwise\s+(.+)/i)
    if (otherwiseMatch) branches.push({ result: otherwiseMatch[1].trim(), isOtherwise: true })
    if (branches.length === 0) branches.push({ result: text, isOtherwise: false })

Semantics equivalence JS<->Python:
  * `.` does NOT match newline in either engine by default (no /s flag in JS, no
    re.DOTALL here). Input is single-line, so irrelevant anyway.
  * `\s`, `+?` (lazy), `(?=...)` lookahead, and the global scan via .exec loop map
    1:1 to Python re.finditer with the same pattern and re.IGNORECASE.
  * .trim() == .strip().
"""

import re
from dataclasses import dataclass


@dataclass
class Branch:
    is_otherwise: bool
    condition: str | None = None
    result: str | None = None


# EXACT port of the production regex (flags i + global; g == finditer scan).
_REGEX = re.compile(r"when\s+(.+?)\s+then\s+(.+?)(?=\s+when\s|\s+otherwise\s|$)", re.IGNORECASE)


def parse_branches(text: str) -> list[Branch]:
    """Line-for-line port of parseBranches() in CalculationHero.tsx:73-102."""
    branches: list[Branch] = []
    last_index = 0
    for m in _REGEX.finditer(text):
        branches.append(
            Branch(is_otherwise=False, condition=m.group(1).strip(), result=m.group(2).strip())
        )
        last_index = m.end()

    otherwise_match = re.search(r"otherwise\s+(.+)", text[last_index:], re.IGNORECASE)
    if otherwise_match:
        branches.append(Branch(is_otherwise=True, result=otherwise_match.group(1).strip()))

    if not branches:
        branches.append(Branch(is_otherwise=False, result=text))

    return branches


def is_branch_matched(sub_branches, result_str, idx) -> bool:
    """Port of isBranchMatched(idx) at CalculationHero.tsx:469-473."""
    if idx >= len(sub_branches):
        return False
    sub = sub_branches[idx]
    if not sub.result:
        return False
    return result_str in sub.result


def matched_flags(branches, sub_branches, result_str) -> list[bool]:
    """Port of the per-branch `matched` computation at CalculationHero.tsx:481-486
    for the case where the backend did NOT supply taken_branch_index
    (backendTakenBranchIndex is null)."""
    any_non_otherwise_matched = any(
        (not b.is_otherwise) and is_branch_matched(sub_branches, result_str, i)
        for i, b in enumerate(branches)
    )
    flags: list[bool] = []
    for i, b in enumerate(branches):
        if b.is_otherwise:
            flags.append(not any_non_otherwise_matched)
        else:
            flags.append(is_branch_matched(sub_branches, result_str, i))
    return flags


print("=" * 78)
print("CONTROL: a well-formed conditional with NO 'when' inside any result")
print("=" * 78)
control = "when age > 25 then 'senior_discount' otherwise 'standard_rate'"
cb = parse_branches(control)
for i, b in enumerate(cb):
    print(f"  [{i}] otherwise={b.is_otherwise!s:<5} cond={b.condition!r} result={b.result!r}")
# 1 when/then branch + 1 otherwise = 2 branches. Sanity that the port is faithful.
assert len(cb) == 2, f"CONTROL expected 2 branches, got {len(cb)}"
assert cb[0].condition == "age > 25"
assert cb[0].result == "'senior_discount'"
assert cb[1].is_otherwise and cb[1].result == "'standard_rate'"
print("  -> control parses correctly (2 branches). Port is faithful.\n")


print("=" * 78)
print("BUG CASE A: a `then` STRING LITERAL legitimately contains the token 'when'")
print("=" * 78)
# Realistic: backend renders a string-literal result that contains the word 'when'.
# Intended structure: exactly ONE when/then branch, plus an otherwise.
#   when status = 'open' then 'review when overdue'
#   otherwise 'no action'
text_a = "when status = 'open' then 'review when overdue' otherwise 'no action'"
ba = parse_branches(text_a)
print(f"  input: {text_a!r}")
for i, b in enumerate(ba):
    print(f"  [{i}] otherwise={b.is_otherwise!s:<5} cond={b.condition!r} result={b.result!r}")

# INTENDED: 2 branches -> ("status = 'open'" then "'review when overdue'"), otherwise "'no action'".
# ACTUAL bug: the lazy `then (.+?)` stops at the FIRST ` when ` inside the literal,
# cutting the result to just "'review" and leaving "overdue' otherwise 'no action'"
# to be (mis)consumed. Assert the SPECIFIC wrong value.
assert ba[0].condition == "status = 'open'", ba[0].condition
assert ba[0].result == "'review", (
    f"EXPECTED the bug to truncate result at the inner 'when', got {ba[0].result!r}"
)
print("\n  >>> BUG CONFIRMED (A): first branch result truncated to \"'review\"")
print("      (the legitimate value was \"'review when overdue'\").")
# Show the downstream consequence: the otherwise text is now polluted / wrong-count.
print(f"      branch count = {len(ba)} (parser lost the clean otherwise structure)")
print()


print("=" * 78)
print("BUG CASE B: a nested when/then rendered inline in a branch result")
print("=" * 78)
# Backend renders a nested conditional inline as the result of the outer branch.
# Intended OUTER structure:
#   outer when "region = 'EU'" then result = "when vat then 1.2 otherwise 1.0"
#   otherwise 1.0
# i.e. the WHOLE nested conditional is the outer branch's result, and the final
# bare "otherwise 1.0" is the OUTER else arm.
text_b = "when region = 'EU' then when vat then 1.2 otherwise 1.0 otherwise 1.0"
bb = parse_branches(text_b)
print(f"  input: {text_b!r}")
for i, b in enumerate(bb):
    print(f"  [{i}] otherwise={b.is_otherwise!s:<5} cond={b.condition!r} result={b.result!r}")

# ACTUAL (buggy) split:
#   [0] when "region = 'EU'" then "when vat then 1.2"   <- nested 'otherwise 1.0' LOST
#   [1] otherwise "1.0 otherwise 1.0"                   <- two otherwises merged/garbled
assert bb[0].condition == "region = 'EU'"
assert bb[0].result == "when vat then 1.2", (
    f"EXPECTED outer result truncated before nested ' otherwise ', got {bb[0].result!r}"
)
assert bb[-1].is_otherwise and bb[-1].result == "1.0 otherwise 1.0", (
    f"EXPECTED garbled merged otherwise '1.0 otherwise 1.0', got {bb[-1].result!r}"
)
print("\n  >>> BUG CONFIRMED (B): the outer branch result is truncated to")
print("      \"when vat then 1.2\" (nested else lost) and the final otherwise arm is")
print("      garbled to \"1.0 otherwise 1.0\" — the rendered conditional is wrong.")
print()


print("=" * 78)
print("WRONG-ARM DIMMING: data-matched highlights the WRONG branch (no backend index)")
print("=" * 78)
# Scope per the claim: backend did NOT supply taken_branch_index, so the regex match
# drives the highlight. expression_text (labels) and substituted_text (values) both
# go through the SAME parser.
#
# The TRUE taken arm is the 'gold' when/then arm; its computed value is 0.8, which the
# backend rendered as "rate scaled when vip applies 0.8" (a flag phrase containing the
# token ' when '). The lazy `then (.+?)` truncates that result at the inner ' when ',
# dropping the 0.8 — so isBranchMatched(0) flips False and the OTHERWISE arm lights up.
expr_text = "when tier = 'gold' then gold_formula otherwise base_formula"
subst_text = "when tier = 'gold' then rate scaled when vip applies 0.8 otherwise 0.0"
result_value = "0.8"  # the engine's actual result for this row -> the gold arm fired

branches = parse_branches(expr_text)
sub_branches = parse_branches(subst_text)
print(f"  substituted_text: {subst_text!r}")
for i, b in enumerate(sub_branches):
    print(f"  sub[{i}] otherwise={b.is_otherwise!s:<5} result={b.result!r}")

# The gold arm's substituted result SHOULD contain 0.8, so it should match. The bug
# truncates it to 'rate scaled', so isBranchMatched(0) is False.
gold_matches = is_branch_matched(sub_branches, result_value, 0)
print(f"\n  result_value={result_value!r}; isBranchMatched(0) [the gold arm] = {gold_matches}")
assert gold_matches is False, (
    "EXPECTED the truncation to drop 0.8 from the gold arm, flipping isBranchMatched(0) "
    f"to False; got {gold_matches}"
)

# Now the full per-branch matched/dimmed computation (CalculationHero.tsx:481-486):
flags = matched_flags(branches, sub_branches, result_value)
print(f"  matched flags per branch (gold, otherwise) = {flags}")
# TRUE answer: gold arm taken -> [True, False]. BUG yields [False, True]: the otherwise
# arm is highlighted (data-matched='true') and the genuinely-taken gold arm is dimmed.
assert flags == [False, True], (
    f"EXPECTED the bug to highlight the OTHERWISE arm and dim the taken gold arm; got {flags}"
)
otherwise_idx = next(i for i, b in enumerate(branches) if b.is_otherwise)
print(f"  >>> BUG CONFIRMED (C): data-matched='true' on branch[{otherwise_idx}] (otherwise)")
print("      while the genuinely-taken 'gold' when/then arm is dimmed (opacity 0.5).")
print("      An actuary reading the trace sees the WRONG taken branch.")
print()

print("ALL ASSERTIONS PASSED — NEWBUG-02 reproduced (display-only mis-split + wrong-arm).")
