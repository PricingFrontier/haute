"""Shared data types for model diagnostics and metadata.

Used by ``_model_card``, ``_mlflow_log``, and ``_training_job``
to bundle diagnostic data without 25+ positional parameters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The diagnostics a completed training result carries under the same names, on a
#: scripted ``TrainResult`` and a canvas ``TrainResponse`` alike.
_RESULT_FIELDS = (
    "feature_importance",
    "shap_summary",
    "feature_importance_loss",
    "double_lift",
    "loss_history",
    "ave_per_feature",
    "residuals_histogram",
    "residuals_stats",
    "actual_vs_predicted",
    "lorenz_curve",
    "lorenz_curve_perfect",
    "pdp_data",
    "final_test_metrics",
    "diagnostics_set",
    "glm_coefficients",
    "glm_relativities",
    "glm_fit_statistics",
    "glm_inference",
    "glm_smooth_terms",
    "glm_regularization",
    "ebm_terms",
    "tboost_tables",
)


@dataclass
class ModelDiagnostics:
    """Bundled diagnostic chart data produced during model evaluation."""

    feature_importance: list[dict[str, Any]] = field(default_factory=list)
    shap_summary: list[dict[str, float]] = field(default_factory=list)
    feature_importance_loss: list[dict[str, Any]] = field(default_factory=list)
    double_lift: list[dict[str, Any]] = field(default_factory=list)
    loss_history: list[dict[str, float]] = field(default_factory=list)
    ave_per_feature: list[dict[str, Any]] = field(default_factory=list)
    residuals_histogram: list[dict[str, Any]] = field(default_factory=list)
    residuals_stats: dict[str, float] = field(default_factory=dict)
    actual_vs_predicted: list[dict[str, float]] = field(default_factory=list)
    lorenz_curve: list[dict[str, float]] = field(default_factory=list)
    lorenz_curve_perfect: list[dict[str, float]] = field(default_factory=list)
    pdp_data: list[dict[str, Any]] = field(default_factory=list)
    final_test_metrics: dict[str, float] = field(default_factory=dict)
    selection_metrics: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] | None = None
    tuning: dict[str, Any] | None = None
    diagnostics_set: str = "development"
    # GLM-specific
    glm_coefficients: list[dict[str, Any]] = field(default_factory=list)
    glm_relativities: list[dict[str, Any]] = field(default_factory=list)
    glm_fit_statistics: dict[str, float] = field(default_factory=dict)
    glm_inference: dict[str, Any] | None = None
    glm_smooth_terms: list[dict[str, Any]] = field(default_factory=list)
    glm_regularization: dict[str, Any] | None = None
    # EBM-specific
    ebm_terms: list[dict[str, Any]] = field(default_factory=list)
    # t-boost-specific
    tboost_tables: dict[str, Any] | None = None

    @classmethod
    def from_result(
        cls,
        result: Any,
        *,
        evaluation: Mapping[str, Any],
        tuning: Mapping[str, Any] | None,
    ) -> ModelDiagnostics:
        """The diagnostics of a completed training result, for its candidate run.

        *result* is a scripted ``TrainResult`` or a canvas ``TrainResponse``;
        *evaluation* and *tuning* are its evaluation and tuning reports as plain data.
        """
        return cls(
            **{name: getattr(result, name) for name in _RESULT_FIELDS},
            selection_metrics=dict(evaluation.get("selection_metrics", {})),
            evaluation=dict(evaluation),
            tuning=dict(tuning) if tuning is not None else None,
        )


@dataclass
class ModelCardMetadata:
    """Training context metadata for model cards and MLflow logging.

    Feature-contract fields (``feature_types``, ``categorical_features``,
    ``target_name``, ``target_type``) are populated by ``TrainingJob`` and
    are what ``log_experiment`` uses to attach an ``mlflow.models.ModelSignature``
    to the logged model — any drift between training and scoring is then
    detectable from the MLflow artifact alone.
    """

    algorithm: str = ""
    task: str = ""
    development_rows: int = 0
    final_test_rows: int = 0
    features: list[str] = field(default_factory=list)
    evaluation_config: dict[str, Any] = field(default_factory=dict)
    best_iteration: int | None = None
    # Feature contract inputs for ``build_signature``.  Absent values keep
    # MLflow logging working when a training path has not populated them.
    feature_types: dict[str, str] = field(default_factory=dict)
    categorical_features: list[str] = field(default_factory=list)
    target_name: str = ""
    target_type: str = ""
    # Offset/exposure column the model was trained with ("" = none).
    # Declared in the logged ModelSignature so scoring payloads must
    # carry it — served predictions include the offset effect.
    offset_name: str = ""
    offset_type: str = ""
    # How it enters the prediction: "log" for an exposure, which the signature
    # marks optional (a missing one is a full policy term), else "identity".
    offset_link: str = ""
