"""V028 repro: ``_assert_finite_glm_record`` silently accepts a required GLM
field that is ``None``/missing, so a malformed RustyStats contribution record
raises ``AssertionError`` (the bare ``assert`` at line 459) instead of the
module-contract ``ModelExplanationError``.  Downstream, the sole production
caller ``enrich_model_score`` only catches ``ModelExplanationError`` and so
falls through to its broad ``except Exception``, degrading the trace to a
generic 'model score enrichment failed' detail with ``prediction_value=None``
and ``error_type='AssertionError'``.

ISOLATION: no disk I/O, no project root, no rating/, src/, tests/, or real
project files.  We build a tiny in-memory fake RustyStats scoring model whose
``predict_contributions`` returns a record with ``sum_contributions=None`` and
patch ``haute._mlflow_io.load_mlflow_model`` so the real code path runs against
the synthetic model.

The assertions pin the *wrong behaviour*: (1) ``_as_float(None, strict=True)``
returns ``None`` rather than raising; (2) ``_assert_finite_glm_record`` does
NOT raise on the malformed record; (3) the public explain call raises
``AssertionError`` (not ``ModelExplanationError``); (4) ``enrich_model_score``
emits the generic broad-handler detail with ``error_type='AssertionError'``.
"""

from __future__ import annotations

import numpy as np

from haute import _model_explainability, _mlflow_io, _trace_enrichment
from haute._model_explainability import (
    ModelExplanationError,
    _as_float,
    _assert_finite_glm_record,
    explain_rustystats_glm_prediction,
)


class _FakeRawGlm:
    """Stands in for a loaded RustyStats GLM raw model.

    ``predict_contributions`` returns a single record that is well-formed
    EXCEPT that the required numeric field ``sum_contributions`` is ``None``
    (e.g. a null leaked through the engine's records serialisation).  Every
    other required field is finite so the only thing that should reject the
    record is the finiteness/presence validation.
    """

    def predict_contributions(self, row_frame, **_kwargs):
        return [
            {
                "base_value": 0.5,
                "sum_contributions": None,  # <-- malformed: missing/null
                "prediction_from_contributions": 1.5,
                "prediction_value": 1.5,
                "family": "gaussian",
                "link": "identity",
                "output_space": "linear_predictor",
                "prediction_space": "response",
                "contributions": [
                    {"feature": "x", "contribution": 1.0},
                ],
            }
        ]

    def predict(self, row_frame):
        return np.asarray([1.5], dtype=float)


class _FakeScoringModel:
    flavor = "rustystats"
    feature_names = ("x",)

    def __init__(self) -> None:
        self.raw_model = _FakeRawGlm()


def _make_record_with_none_sum() -> dict:
    return {
        "base_value": 0.5,
        "sum_contributions": None,
        "prediction_from_contributions": 1.5,
        "prediction_value": 1.5,
        "contributions": [{"feature": "x", "contribution": 1.0}],
    }


def main() -> None:
    # ------------------------------------------------------------------
    # (1) Root cause: _as_float short-circuits None BEFORE the strict
    #     branch, so strict=True does NOT raise for None.
    # ------------------------------------------------------------------
    result = _as_float(None, field_name="sum_contributions", strict=True)
    print("1) _as_float(None, strict=True) ->", repr(result))
    assert result is None, (
        "BUG NOT REPRODUCED: expected _as_float(None, strict=True) to return "
        f"None (silently), got {result!r}"
    )

    # ------------------------------------------------------------------
    # (2) The validation guard whose very name/docstring promises finiteness
    #     does NOT reject a record whose required field is None.
    # ------------------------------------------------------------------
    record = _make_record_with_none_sum()
    guard_raised: Exception | None = None
    try:
        _assert_finite_glm_record(record)
    except Exception as exc:  # noqa: BLE001 - we want to observe what (if any) raises
        guard_raised = exc
    print("2) _assert_finite_glm_record(None sum) raised ->", repr(guard_raised))
    assert guard_raised is None, (
        "BUG NOT REPRODUCED: _assert_finite_glm_record was expected to silently "
        f"accept the malformed record, but it raised {guard_raised!r}"
    )

    # ------------------------------------------------------------------
    # (3) The public explain function raises AssertionError, NOT the
    #     documented ModelExplanationError, for the malformed record.
    # ------------------------------------------------------------------
    model = _FakeScoringModel()
    raised: BaseException | None = None
    try:
        explain_rustystats_glm_prediction(model, {"x": 1.0}, prediction_value=1.5)
    except BaseException as exc:  # noqa: BLE001 - capture exact type
        raised = exc
    print("3) explain_rustystats_glm_prediction raised ->", type(raised).__name__, repr(raised))
    assert raised is not None, "expected the explain call to raise on the malformed record"
    assert isinstance(raised, AssertionError), (
        "BUG NOT REPRODUCED: expected AssertionError (the bare assert), got "
        f"{type(raised).__name__}: {raised!r}"
    )
    assert not isinstance(raised, ModelExplanationError), (
        "If this were ModelExplanationError the contract would hold; it is not."
    )

    # ------------------------------------------------------------------
    # (4) End-to-end caller degradation. ``enrich_model_score`` only catches
    #     ModelExplanationError, so the AssertionError escapes to its broad
    #     ``except Exception``, producing the generic failure detail with
    #     prediction_value=None and error_type='AssertionError'.
    # ------------------------------------------------------------------
    original_loader = _mlflow_io.load_mlflow_model
    _mlflow_io.load_mlflow_model = lambda **_kwargs: _FakeScoringModel()
    try:
        config = {
            "sourceType": "run",
            "run_id": "fake-run",
            "artifact_path": "model.rsglm",
            "task": "regression",
            "output_column": "prediction",
            "feature_columns": ["x"],
        }
        input_row = {"x": 1.0}
        output_row = {"prediction": 1.5}
        detail = _trace_enrichment.enrich_model_score(config, input_row, output_row)
    finally:
        _mlflow_io.load_mlflow_model = original_loader

    print("4) enrich_model_score detail ->", detail)

    # Sanity: confirm the config gate actually selects the rustystats path so
    # the AssertionError really does originate from the code under test.
    assert _model_explainability._config_requests_supported_explanation(config), (
        "precondition: the config must be a supported explanation config"
    )

    # The broad handler signature: generic message + AssertionError type +
    # prediction_value forced to None + empty feature_columns.
    assert detail.get("error_type") == "AssertionError", (
        "BUG NOT REPRODUCED: expected the broad ``except Exception`` handler to "
        f"report error_type='AssertionError', got {detail.get('error_type')!r} "
        f"(full detail: {detail!r})"
    )
    assert "model score enrichment failed" in str(detail.get("error", "")), (
        "BUG NOT REPRODUCED: expected the generic broad-handler message, got "
        f"{detail.get('error')!r}"
    )
    assert detail.get("prediction_value") is None, (
        "expected the broad handler to null out prediction_value, got "
        f"{detail.get('prediction_value')!r}"
    )
    # The clean path (had the guard raised ModelExplanationError) would instead
    # have produced status='error' WITH method/type metadata and NO generic
    # 'enrichment failed' text. Confirm we did NOT get that clean shape.
    assert "explanation" not in detail, (
        "expected the broad handler (no 'explanation' key), but got a structured "
        f"explanation: {detail!r}"
    )

    print(
        "\nV028 REPRODUCED: a RustyStats contribution record with "
        "sum_contributions=None passes _assert_finite_glm_record untouched, then "
        "trips the bare `assert sum_contributions is not None` -> AssertionError "
        "(not ModelExplanationError). enrich_model_score's narrow catch misses it "
        "and the broad handler degrades the trace to "
        "error_type='AssertionError', prediction_value=None."
    )


if __name__ == "__main__":
    main()
