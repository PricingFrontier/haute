"""Isolated reproduction for NEWBUG-AVE-LABEL-TRUNCATION.

Claim: render_ave_feature_svg decides whether to ROTATE x labels using
`len(str(lab)) > 5` on the RAW labels, but _render_dual_axis_chart then
TRUNCATES each label to 15 chars via _truncate_label(str(label), 15).
Two distinct categorical levels that share a 15-char prefix therefore render
as the byte-identical rotated x-axis <text> element, destroying uniqueness.

This script imports the REAL production function (no rating/, no project data
touched) with two synthetic categorical levels, then asserts that the two
distinct labels produce the SAME displayed text in the emitted SVG.

Run: uv run python review/03-simplification/repro/modelling__NEWBUG-AVE-LABEL-TRUNCATION.py
"""

from __future__ import annotations

import re

from haute.modelling._charts import render_ave_feature_svg

# Two DISTINCT vehicle codes that share a 14-char prefix.
# _truncate_label keeps label[:14] + "…" once len > 15.
LEVEL_A = "MERCEDES_BENZ_C200"
LEVEL_B = "MERCEDES_BENZ_C220"

assert LEVEL_A != LEVEL_B, "precondition: the two levels are genuinely distinct"
assert len(LEVEL_A) > 15 and len(LEVEL_B) > 15, "both exceed the 15-char truncation width"
assert LEVEL_A[:14] == LEVEL_B[:14], "they share the 14-char prefix that survives truncation"

bins = [
    {"label": LEVEL_A, "exposure": 1000.0, "avg_actual": 0.10, "avg_predicted": 0.10},
    # Bin B is genuinely MIS-CALIBRATED (actual 0.40 vs predicted 0.10) — the very
    # thing an actuary needs to attribute to the CORRECT level off the x-axis.
    {"label": LEVEL_B, "exposure": 1200.0, "avg_actual": 0.40, "avg_predicted": 0.10},
]

svg = render_ave_feature_svg("vehicle_code", bins, is_categorical=True)

# Extract every x-axis <text> body that holds one of our (truncated) labels.
# Both labels begin with "MERCEDES_BENZ_" so match on that prefix.
text_bodies = re.findall(r"<text[^>]*>(MERCEDES[^<]*)</text>", svg)

print("Distinct input levels :", LEVEL_A, "|", LEVEL_B)
print("X-axis label texts     :", text_bodies)

# 1. Confirm the rotate path was taken (raw len > 5 -> any_long True): rotated
#    labels carry a transform="rotate(-45 ...)" attribute.
assert 'transform="rotate(-45' in svg, "expected rotated labels (the >5 branch)"

# 2. Both distinct levels must appear, each exactly once on the x-axis.
assert len(text_bodies) == 2, f"expected 2 x-axis label texts, got {text_bodies!r}"

# 3. THE BUG: the two displayed labels are byte-identical despite distinct inputs.
collapsed = "MERCEDES_BENZ_…"
assert text_bodies[0] == collapsed, f"bin A label = {text_bodies[0]!r}"
assert text_bodies[1] == collapsed, f"bin B label = {text_bodies[1]!r}"
assert text_bodies[0] == text_bodies[1], (
    "two DISTINCT levels collapse to the same x-axis label -> indistinguishable bins"
)

# Sanity: the full distinguishing suffix (C200 / C220) is absent from the SVG,
# so there is NO disambiguation anywhere in the rendered chart.
assert "C200" not in svg, "C200 suffix should have been truncated away"
assert "C220" not in svg, "C220 suffix should have been truncated away"

print()
print("REPRO CONFIRMED: two distinct AvE bins render the identical x-axis label")
print(f'  both display as: "{collapsed}" (suffixes C200/C220 lost, no tooltip/disambig)')
