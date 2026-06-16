"""C6 regression suite: tie-corrected Gini / Lorenz must be row-order independent.

CODE_REVIEW.md C6 (verified + reproduced): ``np.argsort(-y_pred)`` with no tie
aggregation meant that whenever predictions tie, the Gini depended on the
incoming row order (argsort tie-break = row position).  Reproduced at HEAD
before the fix:

- constant predictor on target-ascending rows scored -1.8333..., the same rows
  target-descending scored +1.0 (not even bounded to [-1, 1]);
- a 2-level predictor scored 0.7667 vs 0.3333 under a pure row permutation;
- ``compute_lorenz_curve`` produced different curves for the same data.

The fix aggregates rows by unique predicted value — each tie group contributes
ONE Lorenz segment (chord).  This is the standard tie-corrected formulation:
for binary targets it makes the normalised Gini equal ``2 * AUC - 1`` with the
trapezoidal (tie-corrected) AUC, i.e. tied pairs get half credit exactly as in
Somers' D.  The same aggregation is applied to ``compute_lorenz_curve`` so the
plotted curve and the scalar can never disagree.

Degenerate contracts pinned here:
- empty input               -> 0.0   (preserved)
- all-tied predictions      -> 0.0 exactly (constant model has no ranking power;
                                     previously anywhere in [-1.83, 1.0])
- single row                -> 0.0   (a single prediction is an all-tied
                                     predictor; previously 1.0 purely from the
                                     missing-origin area bias)
- constant target           -> 0.0   (no discrimination measurable; previously
                                     1.0 from the same bias artifact)
- zero total weight / loss  -> 0.0   (preserved)
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from haute.modelling._metrics import _gini, compute_lorenz_curve, compute_metrics

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _curve_raw_gini(curve: list[dict[str, float]]) -> float:
    """Raw (unnormalised) Gini implied by a Lorenz curve's points.

    raw = 1 - 2 * area under the curve, matching the scalar's formulation.
    """
    x = np.array([p["cum_weight_frac"] for p in curve])
    y = np.array([p["cum_actual_frac"] for p in curve])
    return float(1.0 - 2.0 * np.trapezoid(y, x))


_FINITE = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
_POSITIVE_W = st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False)


@st.composite
def _tied_dataset(draw: st.DrawFn, *, weighted: bool) -> tuple[np.ndarray, ...]:
    """(y_true, y_pred, weight, permutation) with tie-prone predictions.

    Predictions are drawn per-row from a small palette (forcing heavy ties)
    mixed with fully continuous draws (untied rows), so tie groups of every
    size — including all-tied and no-ties — are generated.
    """
    n = draw(st.integers(min_value=1, max_value=30))
    y_true = np.array(draw(st.lists(_FINITE, min_size=n, max_size=n)), dtype=float)
    palette = draw(st.lists(_FINITE, min_size=1, max_size=4))
    y_pred = np.array(
        [draw(st.one_of(st.sampled_from(palette), _FINITE)) for _ in range(n)], dtype=float
    )
    if weighted:
        weight = np.array(draw(st.lists(_POSITIVE_W, min_size=n, max_size=n)), dtype=float)
    else:
        weight = np.ones(n)
    perm = np.array(draw(st.permutations(range(n))), dtype=int)
    return y_true, y_pred, weight, perm


# ---------------------------------------------------------------------------
# 1. Row-permutation invariance (the core C6 regression)
# ---------------------------------------------------------------------------


class TestRowPermutationInvariance:
    def test_constant_predictor_row_order_divergence(self):
        """The reproduced C6 RED case: same rows, opposite target order.

        Pre-fix: target-ascending scored -1.8333333333333346 and
        target-descending scored +1.0 under an identical constant predictor.
        Post-fix both must be identical (and exactly 0.0 — no ranking power).
        """
        y_asc = np.array([1.0, 2.0, 3.0, 4.0])
        y_desc = y_asc[::-1].copy()
        const_pred = np.full(4, 7.0)

        gini_asc = _gini(y_asc, const_pred, None)
        gini_desc = _gini(y_desc, const_pred, None)

        assert gini_asc == gini_desc
        assert gini_asc == 0.0

    def test_two_level_predictor_permutation_divergence(self):
        """The reproduced C6 RED case for a coarse (2-level) predictor.

        Pre-fix: original order scored 0.7666666666666663, the permuted rows
        scored 0.3333333333333333.  Post-fix the score is order-independent.
        """
        y_true = np.array([3.0, 1.0, 2.0, 0.0, 5.0, 4.0])
        y_pred = np.array([10.0, 10.0, 10.0, 10.0, 20.0, 20.0])
        perm = np.array([3, 1, 0, 2, 5, 4])

        gini_orig = _gini(y_true, y_pred, None)
        gini_perm = _gini(y_true[perm], y_pred[perm], None)

        assert gini_orig == gini_perm

    def test_lorenz_curve_permutation_invariant(self):
        """compute_lorenz_curve got the same fix: identical curves either way."""
        y_true = np.array([3.0, 1.0, 2.0, 0.0, 5.0, 4.0])
        y_pred = np.array([10.0, 10.0, 10.0, 10.0, 20.0, 20.0])
        perm = np.array([3, 1, 0, 2, 5, 4])

        model_1, perfect_1 = compute_lorenz_curve(y_true, y_pred)
        model_2, perfect_2 = compute_lorenz_curve(y_true[perm], y_pred[perm])

        assert model_1 == model_2
        assert perfect_1 == perfect_2

    @settings(max_examples=60, deadline=None)
    @given(data=_tied_dataset(weighted=False))
    def test_gini_permutation_invariant_exact(self, data: tuple[np.ndarray, ...]):
        """gini(perm(rows)) == gini(rows) EXACTLY, for arbitrary tied data.

        Exact (bit-level) equality is intentional: the implementation sorts
        with a canonical tie-break on (y_true, weight), so the float
        accumulation order — and therefore the result — cannot depend on the
        incoming row order.
        """
        y_true, y_pred, _, perm = data
        assert _gini(y_true[perm], y_pred[perm], None) == _gini(y_true, y_pred, None)

    @settings(max_examples=60, deadline=None)
    @given(data=_tied_dataset(weighted=False))
    def test_lorenz_curve_permutation_invariant_exact(self, data: tuple[np.ndarray, ...]):
        y_true, y_pred, _, perm = data
        model_1, perfect_1 = compute_lorenz_curve(y_true, y_pred)
        model_2, perfect_2 = compute_lorenz_curve(y_true[perm], y_pred[perm])
        assert model_1 == model_2
        assert perfect_1 == perfect_2


# ---------------------------------------------------------------------------
# 2. Constant predictor -> exactly 0, any row order
# ---------------------------------------------------------------------------


class TestConstantPredictor:
    @pytest.mark.parametrize(
        "y_true",
        [
            np.array([1.0, 2.0, 3.0, 4.0]),  # ascending
            np.array([4.0, 3.0, 2.0, 1.0]),  # descending (pre-fix: +1.0)
            np.array([2.0, 4.0, 1.0, 3.0]),  # shuffled
            np.array([0.0, 0.0, 5.0, 5.0]),  # tied target levels
        ],
        ids=["ascending", "descending", "shuffled", "tied-target"],
    )
    def test_constant_predictor_is_exactly_zero(self, y_true: np.ndarray):
        assert _gini(y_true, np.full(len(y_true), 3.0), None) == 0.0

    def test_constant_predictor_weighted_is_exactly_zero(self):
        y_true = np.array([5.0, 1.0, 3.0])
        w = np.array([2.0, 7.0, 1.5])
        assert _gini(y_true, np.full(3, 0.25), w) == 0.0

    def test_constant_predictor_via_public_compute_metrics(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.full(5, 3.0)
        result = compute_metrics(y_true, y_pred, metric_names=["gini"])
        assert result["gini"] == 0.0


# ---------------------------------------------------------------------------
# 3. Known-value cases (hand-computed, tie-corrected)
# ---------------------------------------------------------------------------


class TestKnownValues:
    def test_perfect_ranking_with_tied_levels_is_one(self):
        """Predictions == targets (with tied levels) -> exactly the perfect model."""
        y = np.array([0.0, 0.0, 1.0, 1.0, 2.0])
        assert _gini(y, y.copy(), None) == pytest.approx(1.0, abs=1e-12)

    def test_reversed_ranking_is_negative_one(self):
        """Fully reversed ranking -> exactly -1 (the perfect model's mirror).

        The Lorenz curve of the reversed order is the 180-degree rotation of
        the perfect curve: L_rev(x) = 1 - L_perfect(1 - x), so
        raw_rev = -raw_perfect and the normalised Gini is exactly -1.
        Tie groups reverse symmetrically, so this holds with tied levels too.
        """
        y = np.array([0.0, 0.0, 1.0, 1.0, 2.0])
        assert _gini(y, -y, None) == pytest.approx(-1.0, abs=1e-12)

    def test_two_level_predictor_hand_computed(self):
        """2-level predictor, tie-corrected value derived by hand.

        y_pred = [10, 10, 5, 5], y_true = [3, 1, 2, 0], unit weights.

        Tie groups (descending pred):  {10: W=2, L=3+1=4}, {5: W=2, L=2+0=2}.
        Totals W=4, L=6.  Lorenz points: (0,0), (1/2, 4/6), (1, 1).
        Area  = (1/2)(0 + 2/3)(1/2) + (1/2)(2/3 + 1)(1/2) = 1/6 + 5/12 = 7/12
        raw   = 1 - 2(7/12) = -1/6
        Perfect (y unique 3,2,1,0): points (0,0),(1/4,3/6),(1/2,5/6),(3/4,1),(1,1)
        AreaP = 1/16 + 1/6 + 11/48 + 1/4 = 17/24
        rawP  = 1 - 17/12 = -5/12
        gini  = (-1/6) / (-5/12) = 2/5 = 0.4 exactly.
        """
        y_true = np.array([3.0, 1.0, 2.0, 0.0])
        y_pred = np.array([10.0, 10.0, 5.0, 5.0])
        assert _gini(y_true, y_pred, None) == pytest.approx(0.4, abs=1e-12)

    def test_binary_two_level_diagonal_is_zero(self):
        """Each tie group has the same positive rate -> Lorenz curve is the
        diagonal -> gini 0.  Cross-check via pair counting: pos/neg pairs
        (1@2 vs 0@2)=1/2, (1@2 vs 0@1)=1, (1@1 vs 0@2)=0, (1@1 vs 0@1)=1/2;
        AUC = (1/2 + 1 + 0 + 1/2)/4 = 1/2, and 2*AUC - 1 = 0.
        """
        y_true = np.array([1.0, 0.0, 1.0, 0.0])
        y_pred = np.array([2.0, 2.0, 1.0, 1.0])
        assert _gini(y_true, y_pred, None) == pytest.approx(0.0, abs=1e-12)

    @pytest.mark.parametrize(
        "y_true, y_pred, weight",
        [
            # heavy ties, unweighted
            ([1, 0, 1, 1, 0, 0, 1, 0], [3.0, 3.0, 2.0, 2.0, 2.0, 1.0, 1.0, 1.0], None),
            # ties + weights
            ([1, 1, 0, 0, 1, 0], [5.0, 2.0, 2.0, 2.0, 1.0, 1.0], [1.0, 2.0, 3.0, 0.5, 4.0, 2.5]),
            # untied sanity
            ([0, 1, 0, 1, 1], [0.1, 0.4, 0.35, 0.8, 0.6], None),
        ],
        ids=["tied-unweighted", "tied-weighted", "untied"],
    )
    def test_matches_tie_corrected_auc_relationship(self, y_true, y_pred, weight):
        """Binary targets: normalised gini == 2*AUC - 1 (tie-corrected AUC).

        sklearn's roc_auc_score is the independent oracle; its trapezoidal ROC
        gives tied pairs half credit — exactly the chord-per-tie-group
        treatment.  (Engelmann/Hayden/Tasche: accuracy ratio = 2*AUC - 1.)
        """
        from sklearn.metrics import roc_auc_score

        yt = np.array(y_true, dtype=float)
        yp = np.array(y_pred, dtype=float)
        w = None if weight is None else np.array(weight, dtype=float)
        auc = roc_auc_score(yt, yp, sample_weight=w)
        assert _gini(yt, yp, w) == pytest.approx(2.0 * auc - 1.0, abs=1e-9)

    @settings(max_examples=60, deadline=None)
    @given(data=_tied_dataset(weighted=True))
    def test_binary_gini_equals_two_auc_minus_one(self, data: tuple[np.ndarray, ...]):
        """Property: for any binary target and tied predictions,
        gini == 2*roc_auc_score - 1 (weighted, tie-corrected)."""
        from sklearn.metrics import roc_auc_score

        _, y_pred, weight, _ = data
        rng = np.random.RandomState(len(y_pred))
        y_true = rng.randint(0, 2, size=len(y_pred)).astype(float)
        assume(0.0 < y_true.sum() < len(y_true))  # both classes present

        auc = roc_auc_score(y_true, y_pred, sample_weight=weight)
        assert _gini(y_true, y_pred, weight) == pytest.approx(2.0 * auc - 1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 4. Lorenz / gini consistency: the curve's implied area IS the scalar
# ---------------------------------------------------------------------------


class TestLorenzGiniConsistency:
    def test_hand_case_curve_implies_scalar(self):
        """The 2-level hand case: ratio of raw ginis from the two returned
        curves equals the scalar (within 6-dp display rounding)."""
        y_true = np.array([3.0, 1.0, 2.0, 0.0])
        y_pred = np.array([10.0, 10.0, 5.0, 5.0])

        model_curve, perfect_curve = compute_lorenz_curve(y_true, y_pred)
        implied = _curve_raw_gini(model_curve) / _curve_raw_gini(perfect_curve)

        assert implied == pytest.approx(_gini(y_true, y_pred, None), abs=1e-4)
        assert implied == pytest.approx(0.4, abs=1e-4)

    @settings(max_examples=60, deadline=None)
    @given(data=_tied_dataset(weighted=True))
    def test_curve_implied_gini_matches_scalar(self, data: tuple[np.ndarray, ...]):
        """Property: gini == (1 - 2*area(model curve)) / (1 - 2*area(perfect curve)).

        Pins that compute_lorenz_curve and _gini share one aggregation — if
        either regressed to per-row points the implied area would diverge on
        tied fixtures.  Curve points are rounded to 6 dp for display, so the
        tolerance is the worst-case rounding propagation, not a fudge factor:
        |d area| <= 1e-6 * (1 + 2 * n_segments) per curve.
        """
        y_true, y_pred, weight, _ = data
        y_true = np.abs(y_true)  # Lorenz consistency is defined for y >= 0
        assume(float(y_true.sum()) > 1e-6)
        assume(len(np.unique(y_true)) >= 2)  # non-degenerate perfect curve

        model_curve, perfect_curve = compute_lorenz_curve(y_true, y_pred, weight)
        raw_model = _curve_raw_gini(model_curve)
        raw_perfect = _curve_raw_gini(perfect_curve)
        assume(abs(raw_perfect) > 0.05)

        # Rounding each coordinate by <=1e-6 perturbs a trapezoid area by at
        # most ~1e-6*(1 + 2*segments); raw = 1-2*area doubles it; the ratio
        # divides by |raw_perfect| and scales the numerator's share by |gini|<=1.
        area_err = 1e-6 * (1 + 2 * max(len(model_curve), len(perfect_curve)))
        tol = 4.0 * area_err / abs(raw_perfect) + 1e-9

        implied = raw_model / raw_perfect
        assert implied == pytest.approx(_gini(y_true, y_pred, weight), abs=tol)


# ---------------------------------------------------------------------------
# 5. Weighted ties
# ---------------------------------------------------------------------------


class TestWeightedTies:
    def test_weighted_tie_group_hand_computed(self):
        """Weighted tie group aggregates weights and weighted targets.

        y_pred = [10, 10, 5], y_true = [2, 0, 1], w = [1, 3, 2].

        Tie groups (descending pred):
          {10: W = 1+3 = 4, L = 2*1 + 0*3 = 2},  {5: W = 2, L = 1*2 = 2}.
        Totals W=6, L=4.  Lorenz points: (0,0), (4/6, 2/4), (1, 1).
        Area  = (1/2)(0 + 1/2)(2/3) + (1/2)(1/2 + 1)(1/3) = 1/6 + 1/4 = 5/12
        raw   = 1 - 2(5/12) = 1/6
        Perfect (unique y desc: 2 w=1, 1 w=2, 0 w=3):
          points (0,0), (1/6, 2/4), (3/6, 4/4), (1, 1)
        AreaP = (1/2)(1/2)(1/6) + (1/2)(1/2 + 1)(1/3) + (1/2)(2)(1/2) = 19/24
        rawP  = 1 - 19/12 = -7/12
        gini  = (1/6) / (-7/12) = -2/7.
        """
        y_true = np.array([2.0, 0.0, 1.0])
        y_pred = np.array([10.0, 10.0, 5.0])
        w = np.array([1.0, 3.0, 2.0])
        assert _gini(y_true, y_pred, w) == pytest.approx(-2.0 / 7.0, abs=1e-12)

    def test_weighted_permutation_divergence_fixture(self):
        """Deterministic weighted twin of the core RED case."""
        y_true = np.array([3.0, 1.0, 2.0, 0.0, 5.0, 4.0])
        y_pred = np.array([10.0, 10.0, 10.0, 10.0, 20.0, 20.0])
        w = np.array([1.0, 2.0, 0.5, 3.0, 1.5, 2.5])
        perm = np.array([3, 1, 0, 2, 5, 4])

        assert _gini(y_true[perm], y_pred[perm], w[perm]) == _gini(y_true, y_pred, w)

    @settings(max_examples=60, deadline=None)
    @given(data=_tied_dataset(weighted=True))
    def test_weighted_gini_permutation_invariant_exact(self, data: tuple[np.ndarray, ...]):
        y_true, y_pred, weight, perm = data
        assert _gini(y_true[perm], y_pred[perm], weight[perm]) == _gini(y_true, y_pred, weight)


# ---------------------------------------------------------------------------
# 6. Degenerate contracts (documented in the module docstring)
# ---------------------------------------------------------------------------


class TestDegenerateContracts:
    def test_empty_input_is_zero(self):
        assert _gini(np.array([]), np.array([]), None) == 0.0

    def test_single_row_is_zero(self):
        """One row is an all-tied predictor: no ranking power -> 0.0.

        (Pre-fix this returned 1.0 — an artifact of the area integration
        starting at the first cumulative point instead of the origin.)
        """
        assert _gini(np.array([5.0]), np.array([2.0]), None) == 0.0

    def test_constant_target_is_zero(self):
        """Constant target: every ordering is 'perfect', the perfect Lorenz
        curve collapses to one segment and no discrimination is measurable.

        (Pre-fix this returned 1.0 because raw and perfect shared the same
        missing-origin bias and the ratio of two artifacts was 1.)
        """
        assert _gini(np.array([5.0, 5.0, 5.0, 5.0]), np.array([1.0, 2.0, 3.0, 4.0]), None) == 0.0

    def test_all_zero_targets_is_zero(self):
        assert _gini(np.zeros(4), np.array([1.0, 2.0, 3.0, 4.0]), None) == 0.0

    def test_zero_total_weight_is_zero(self):
        y = np.array([1.0, 2.0, 3.0])
        assert _gini(y, y, np.zeros(3)) == 0.0

    def test_lorenz_empty_input_contract_preserved(self):
        model_curve, perfect_curve = compute_lorenz_curve(np.array([]), np.array([]))
        assert model_curve == [{"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}]
        assert perfect_curve == [{"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}]

    def test_lorenz_zero_loss_contract_preserved(self):
        model_curve, _ = compute_lorenz_curve(np.zeros(3), np.array([1.0, 2.0, 3.0]))
        assert model_curve == [
            {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0},
            {"cum_weight_frac": 1.0, "cum_actual_frac": 1.0},
        ]

    def test_lorenz_constant_predictor_is_single_chord(self):
        """All-tied predictions aggregate to one segment: (0,0) -> (1,1)."""
        model_curve, _ = compute_lorenz_curve(
            np.array([4.0, 1.0, 3.0]), np.full(3, 2.0), np.array([1.0, 2.0, 3.0])
        )
        assert model_curve == [
            {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0},
            {"cum_weight_frac": 1.0, "cum_actual_frac": 1.0},
        ]
