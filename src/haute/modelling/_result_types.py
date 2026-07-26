"""Shared data types for model diagnostics and metadata.

Used by ``_model_card``, ``_mlflow_log``, and ``_training_job``
to bundle diagnostic data without 25+ positional parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    holdout_metrics: dict[str, float] = field(default_factory=dict)
    diagnostics_set: str = "validation"
    # GLM-specific
    glm_coefficients: list[dict[str, Any]] = field(default_factory=list)
    glm_relativities: list[dict[str, Any]] = field(default_factory=list)
    glm_fit_statistics: dict[str, float] = field(default_factory=dict)
    glm_regularization_path: dict[str, Any] | None = None


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
    train_rows: int = 0
    validation_rows: int = 0
    holdout_rows: int = 0
    features: list[str] = field(default_factory=list)
    split_config: dict[str, Any] = field(default_factory=dict)
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
