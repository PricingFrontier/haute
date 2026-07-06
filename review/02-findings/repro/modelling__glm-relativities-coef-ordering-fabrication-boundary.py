"""Adversarial repro for claim:
glm-relativities-coef-ordering-fabrication-boundary

Claim: GLMAlgorithm.relativities() / coefficients_table() fallbacks guard ONLY
on array length, never on name/coef ordering or intercept identity. If a model
returns equal-length but differently-ordered ``feature_names`` vs
``coefficients`` (with conf_int aligned to coefficients), every
relativity = exp(coef[i]) is silently attached to the WRONG feature label and
returned as a correct actuarial relativity, with no error raised.

This repro uses a STUB model (per the claim's repro_strategy) whose
``coef_table()`` and ``relativities()`` raise, forcing the fallback path.
``feature_names`` and ``coefficients`` are EQUAL LENGTH but in a deliberately
mismatched order. We assert on the SPECIFIC WRONG VALUE: the relativity that
the fallback attaches to a given feature label does not equal exp(that
feature's true coefficient), i.e. the mapping is mislabelled.

ISOLATION: no disk I/O, no project files, pure in-memory stub. No rustystats
fit required.
"""

from __future__ import annotations

import math

import numpy as np

from haute.modelling._rustystats import GLMAlgorithm


class _MismatchedModel:
    """Stub GLM result whose accessors fail, forcing the array-zip fallback.

    The TRUE model is: feature F1 has coef 0.0 (relativity 1.0), feature F2 has
    coef +2.0 (relativity ~7.39). But ``coefficients`` is returned in a
    different order than ``feature_names``:

        feature_names = ['(Intercept)', 'F1', 'F2']    # 3 entries
        coefficients  = [ intercept_c , c_F2, c_F1 ]   # 3 entries, F1/F2 SWAPPED

    Lengths are EQUAL (3 == 3) so _align_coefs_and_names does nothing and no
    length-based GLMInferenceUnavailableError fires. conf_int is aligned to the
    (mis-ordered) coefficients array, exactly as the real conf_int() would be.
    """

    def __init__(self) -> None:
        # Names in canonical order.
        self.feature_names = ["(Intercept)", "F1", "F2"]
        # Coefficients with F1 and F2 SWAPPED relative to names.
        # intercept = 0.1, then c for F2 (=2.0), then c for F1 (=0.0).
        self.coefficients = [0.1, 2.0, 0.0]
        # conf_int aligned to the coefficients array (n,2), same mis-order.
        self._conf = np.array(
            [
                [0.05, 0.15],  # intercept
                [1.90, 2.10],  # F2's interval, sitting at index 1
                [-0.10, 0.10],  # F1's interval, sitting at index 2
            ]
        )

    # Force the primary formatted accessors to fail -> fallback path.
    def relativities(self):  # noqa: D401
        raise RuntimeError("formatted relativities() accessor unavailable")

    def coef_table(self):  # noqa: D401
        raise RuntimeError("formatted coef_table() accessor unavailable")

    # Real statistic arrays exist and are co-indexed with `coefficients`
    # (i.e. with the SWAPPED order), as a real backend would return them.
    def conf_int(self):
        return self._conf

    def bse(self):
        return [0.02, 0.05, 0.05]

    def tvalues(self):
        return [5.0, 40.0, 0.0]

    def pvalues(self):
        return [1e-6, 1e-9, 1.0]

    def significance_codes(self):
        return ["***", "***", ""]


def main() -> None:
    algo = GLMAlgorithm()
    model = _MismatchedModel()

    # Ground truth the analyst would expect (names co-indexed with coefs):
    #   F1 true coef = 0.0   -> relativity exp(0.0)  = 1.0
    #   F2 true coef = 2.0   -> relativity exp(2.0)  ~ 7.389
    true_coef = {"F1": 0.0, "F2": 2.0}

    # --- relativities() fallback ---
    rels = algo.relativities(model)
    rel_by_feature = {r["feature"]: r["relativity"] for r in rels}
    print("relativities fallback output:", rels)

    # The fallback zips names[i] with coefficients[i]:
    #   names[1]='F1' gets coefficients[1]=2.0 -> relativity exp(2.0)
    #   names[2]='F2' gets coefficients[2]=0.0 -> relativity exp(0.0)=1.0
    # So 'F1' is labelled with F2's relativity and vice-versa. No error raised.
    f1_rel = rel_by_feature["F1"]
    f2_rel = rel_by_feature["F2"]

    f1_expected_correct = math.exp(true_coef["F1"])  # 1.0
    f2_expected_correct = math.exp(true_coef["F2"])  # ~7.389

    print(f"F1: reported relativity = {f1_rel:.4f}, "
          f"correct (exp(true coef F1)) = {f1_expected_correct:.4f}")
    print(f"F2: reported relativity = {f2_rel:.4f}, "
          f"correct (exp(true coef F2)) = {f2_expected_correct:.4f}")

    # ASSERT the mislabelling: F1 is reported with F2's relativity (the wrong,
    # but plausible-looking, value) and NOT with its own correct relativity.
    assert not math.isclose(f1_rel, f1_expected_correct, rel_tol=1e-9), (
        "EXPECTED MISLABEL: F1 should have been given F2's relativity, "
        f"but got its own correct value {f1_rel}"
    )
    assert math.isclose(f1_rel, f2_expected_correct, rel_tol=1e-9), (
        f"F1 reported {f1_rel}, expected it to carry F2's relativity "
        f"{f2_expected_correct} (the mislabel)"
    )
    assert math.isclose(f2_rel, f1_expected_correct, rel_tol=1e-9), (
        f"F2 reported {f2_rel}, expected it to carry F1's relativity "
        f"{f1_expected_correct} (the mislabel)"
    )

    # Confidence intervals are likewise mislabelled: F1's row carries F2's CI.
    f1_ci_lower = next(r for r in rels if r["feature"] == "F1")["ci_lower"]
    # F2's true CI lower exp(1.90) ~ 6.686; F1's own would be exp(-0.10)~0.905.
    assert math.isclose(f1_ci_lower, math.exp(1.90), rel_tol=1e-9), (
        f"F1 ci_lower={f1_ci_lower}, expected F2's CI lower exp(1.90)="
        f"{math.exp(1.90):.4f} (mislabel propagates to intervals)"
    )

    # --- coefficients_table() fallback: same mislabel, no error ---
    table = algo.coefficients_table(model)
    coef_by_feature = {r["feature"]: r["coefficient"] for r in table}
    print("coefficients_table fallback output:", table)
    assert math.isclose(coef_by_feature["F1"], 2.0, rel_tol=1e-9), (
        f"F1 coefficient reported as {coef_by_feature['F1']}, expected F2's "
        "coef 2.0 (mislabel)"
    )
    assert math.isclose(coef_by_feature["F2"], 0.0, abs_tol=1e-12), (
        f"F2 coefficient reported as {coef_by_feature['F2']}, expected F1's "
        "coef 0.0 (mislabel)"
    )

    print()
    print("REPRODUCED: equal-length but mis-ordered feature_names/coefficients "
          "are zipped positionally; relativities and coefficients are silently "
          "attached to the WRONG feature labels with NO error raised. Only "
          "length mismatches are guarded.")


if __name__ == "__main__":
    main()
