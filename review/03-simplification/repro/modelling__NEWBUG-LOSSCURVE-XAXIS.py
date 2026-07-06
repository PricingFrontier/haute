"""Isolated reproduction for NEWBUG-LOSSCURVE-XAXIS.

Claim: render_loss_curve_svg's x-axis label loop renders `int(tick)` on the
output of `_nice_ticks`, which truncates non-integer 'nice' tick positions
toward zero (e.g. 2.5 -> '2', 12.5 -> '12'). Two adjacent gridline labels can
collide or be numerically wrong, mis-labelling the iteration axis.

This script uses ONLY in-memory synthetic data and the public-ish module
functions. It does NOT touch src/, tests/, rating/, or any real project file.
Run: uv run python review/03-simplification/repro/modelling__NEWBUG-LOSSCURVE-XAXIS.py
"""

import re

from haute.modelling._charts import _nice_ticks, render_loss_curve_svg

failures = []

# ---------------------------------------------------------------------------
# Part A: _nice_ticks can legitimately return fractional ('.5', '.0' frac)
# tick positions for small iteration ranges. The x-axis loop renders int(tick).
# ---------------------------------------------------------------------------
print("=== Part A: _nice_ticks produces fractional ticks for small ranges ===")

# Range 0..3 with n_ticks=6 (the value render_loss_curve_svg uses).
ticks_0_3 = _nice_ticks(0, 3, 6)
print(f"_nice_ticks(0, 3, 6) = {ticks_0_3}")

# Range 7..19 (a plausible subsampled / short-run iteration domain).
ticks_7_19 = _nice_ticks(7, 19, 6)
print(f"_nice_ticks(7, 19, 6) = {ticks_7_19}")

# Demonstrate the int() truncation directly on whatever fractional ticks appear.
def fractional(ticks):
    return [t for t in ticks if t != int(t)]

for label, ticks in (("0..3", ticks_0_3), ("7..19", ticks_7_19)):
    fracs = fractional(ticks)
    if fracs:
        print(f"  range {label}: fractional ticks {fracs} -> "
              f"int() => {[int(t) for t in fracs]}")

# Find a case where int(tick) is numerically WRONG (truncates a *.5 value)
# AND collides with the integer label of an adjacent tick.
demo_ticks = ticks_0_3
int_labels = [int(t) for t in demo_ticks]
print(f"  ticks {demo_ticks} -> int labels {int_labels}")

# 0.5 -> '0' (collides with the 0.0 tick's '0'); 1.5 -> '1' (collides with 1.0).
has_half = any(abs(t - int(t)) == 0.5 for t in demo_ticks)
has_collision = len(int_labels) != len(set(int_labels))
print(f"  has *.5 tick: {has_half}; has duplicated int label (collision): {has_collision}")

if not (has_half and has_collision):
    failures.append("Part A: expected a *.5 tick that truncates and collides")

# ---------------------------------------------------------------------------
# Part B: drive the FULL render_loss_curve_svg and read the rendered x labels.
# Use a short training run (iterations 0..3) so _nice_ticks picks a 0.5 step.
# ---------------------------------------------------------------------------
print()
print("=== Part B: render_loss_curve_svg emits wrong/duplicate x labels ===")

loss_history = [
    {"iteration": 0, "train_logloss": 0.90, "eval_logloss": 0.95},
    {"iteration": 1, "train_logloss": 0.70, "eval_logloss": 0.80},
    {"iteration": 2, "train_logloss": 0.55, "eval_logloss": 0.70},
    {"iteration": 3, "train_logloss": 0.45, "eval_logloss": 0.66},
]

svg = render_loss_curve_svg(loss_history, best_iteration=3)

# Extract every <text ...>LABEL</text> whose anchor is "middle" and font-size 10
# placed on the x-axis baseline row (y = top+plot_h+16). For width/height/margin
# in this fn: height=320, margin top=30 bottom=40 -> plot_h=250 -> baseline y=296.
# NOTE: the x-axis baseline y is rendered as the integer "296" (top 30 + plot_h
# 250 + 16), not "296.0"; the per-tick x is a float like "146.7".
xlabel_pat = re.compile(
    r'<text x="[\d.]+" y="296" text-anchor="middle" font-size="10"[^>]*>([^<]+)</text>'
)
x_labels = xlabel_pat.findall(svg)
print(f"rendered x-axis tick labels: {x_labels}")

# What the labels SHOULD be (the actual numeric tick positions, formatted
# without lossy truncation). The true tick positions for 0..3:
true_ticks = _nice_ticks(0, 3, 6)
print(f"true tick positions:         {true_ticks}")

# The bug: a tick at 0.5 is rendered as '0', duplicating the '0' from tick 0.0;
# a tick at 1.5 rendered as '1' duplicates tick 1.0, etc. Assert the rendered
# labels contain a duplicate (collision) caused by truncation.
rendered_has_dupe = len(x_labels) != len(set(x_labels))
print(f"rendered labels have duplicate (collision): {rendered_has_dupe}")

# Assert specifically that a fractional tick was rendered with the WRONG value.
# Pair each true tick with the label rendered at its x position would be ideal,
# but order is preserved by _nice_ticks iteration, so compare positionally where
# both lists align in length.
mislabelled = []
if len(x_labels) == len(true_ticks):
    for lbl, t in zip(x_labels, true_ticks):
        if t != int(t):  # fractional tick
            # int(tick) truncates; the displayed label is therefore not equal to
            # the true position. e.g. t=0.5 -> '0', t=2.5 -> '2'.
            if lbl != f"{t:g}":
                mislabelled.append((t, lbl))
print(f"mislabelled fractional ticks (true_value, rendered_label): {mislabelled}")

# Concrete assertion on a specific wrong value: 0.5 must NOT render as '0.5'.
# The buggy code renders it as '0'.
if true_ticks == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
    # Position 1 is the 0.5 tick; its label under the bug is '0'.
    if len(x_labels) >= 2:
        label_for_half = x_labels[1]
        print(f"  label rendered for the 0.5 tick: '{label_for_half}' "
              f"(correct would be '0.5')")
        if label_for_half == "0":
            print("  CONFIRMED: 0.5 tick truncated to '0' (collides with 0.0 tick)")
        else:
            failures.append(
                f"Part B: expected 0.5 tick to render as '0', got '{label_for_half}'"
            )
    if not rendered_has_dupe:
        failures.append("Part B: expected duplicate x labels from truncation collision")
else:
    failures.append(
        f"Part B: setup assumption broken - _nice_ticks(0,3,6) = {true_ticks}"
    )

# ---------------------------------------------------------------------------
# Part C: a *.5 step that yields 12.5 -> '12' (the example from the claim),
# via a subsampled-style range 7..19.
# ---------------------------------------------------------------------------
print()
print("=== Part C: 12.5 -> '12' style truncation on a 7..19 range ===")
loss_history_c = [
    {"iteration": it, "train_logloss": 1.0 / (it + 1)} for it in range(7, 20)
]
svg_c = render_loss_curve_svg(loss_history_c, best_iteration=None)
x_labels_c = xlabel_pat.findall(svg_c)
ticks_c = _nice_ticks(7, 19, 6)
print(f"_nice_ticks(7, 19, 6) = {ticks_c}")
print(f"rendered x labels (7..19) = {x_labels_c}")
frac_c = [t for t in ticks_c if t != int(t)]
if frac_c:
    print(f"  fractional ticks present: {frac_c} -> int() => {[int(t) for t in frac_c]}")
    # If 12.5 is among them it renders as '12'; if 7.5 -> '7' (collides with 7).
    print("  CONFIRMED: fractional tick(s) truncated by int() on this range")

# ---------------------------------------------------------------------------
print()
if failures:
    print("REPRO RESULT: FAILED to reproduce as expected ->")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("REPRO RESULT: PASS - claim reproduced (int(tick) truncates _nice_ticks output;"
      " 0.5 rendered as '0', causing wrong/colliding x-axis labels)")
