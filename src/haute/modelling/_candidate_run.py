"""The candidate-run contract: what every haute training run logged to MLflow contains.

Haute logs training runs as candidates; registering and promoting a model is an
external process that compares a candidate with the current champion. Canvas
logging and scripted ``TrainingJob`` logging both build their run here, so the
contract (run name, tags, namespaced metrics, params, artifacts) is identical on
both paths. Everything in this module is pure data apart from reading the feature
contract and the git state.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from haute.errors import HauteValidationError
from haute.modelling._result_types import ModelCardMetadata, ModelDiagnostics

CANDIDATE_RUN_CONTRACT_VERSION = "1"

_TRAINING_IDENTITY_KEYS = (
    "target",
    "weight",
    "exclude",
    "feature_columns",
    "fold_column",
    "id_columns",
    "algorithm",
    "task",
    "params",
    "evaluation",
    "tuning",
    "metrics",
    "loss_function",
    "variance_power",
    "offset",
    "monotone_constraints",
    "feature_weights",
    "categorical_levels",
    "positive_class",
    "device",
)
_SELECTION_STATISTICS = ("mean", "stddev", "min", "max")
_TUNING_METRICS = (
    ("baseline", "baseline_objective"),
    ("winner", "winner_objective"),
    ("improvement", "improvement"),
)
_TUNING_PARAMS = (
    "metric",
    "direction",
    "winner_trial_index",
    "trial_count",
    "total_fit_count",
    "final_tree_count",
)
_GLM_METRICS = ("aic", "bic", "deviance", "null_deviance")


def _canonical(value: Any) -> Any:
    to_plain = getattr(value, "to_plain_data", None)
    if callable(to_plain):
        return _canonical(to_plain())
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, str | bool | int | float):
        return value
    raise TypeError(f"Training input {type(value).__name__} has no canonical JSON form")


def training_identity_sha256(training_kwargs: Mapping[str, Any]) -> str:
    """Digest of the inputs that determine what a ``TrainingJob`` trains.

    Computed from the same kwargs the canvas worker and a script pass to
    ``TrainingJob``, so one configuration has one identity on both paths.
    """
    from haute._cache import canonical_json

    identity = {key: _canonical(training_kwargs.get(key)) for key in _TRAINING_IDENTITY_KEYS}
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateProvenance:
    """Who and what produced a candidate run, captured when training completes."""

    job_id: str
    node_label: str
    trained_at: datetime
    training_identity_sha256: str
    node_id: str | None = None
    pipeline: str | None = None
    git_commit: str | None = None
    git_dirty: bool | None = None

    def __post_init__(self) -> None:
        if self.trained_at.tzinfo is None:
            raise ValueError("trained_at must be timezone-aware")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "node_label": self.node_label,
            "trained_at": self.trained_at.astimezone(UTC).isoformat(),
            "training_identity_sha256": self.training_identity_sha256,
            "node_id": self.node_id,
            "pipeline": self.pipeline,
            "git_commit": self.git_commit,
            "git_dirty": self.git_dirty,
        }

    @classmethod
    def from_plain_data(cls, data: Mapping[str, Any]) -> CandidateProvenance:
        return cls(
            job_id=str(data["job_id"]),
            node_label=str(data["node_label"]),
            trained_at=datetime.fromisoformat(str(data["trained_at"])),
            training_identity_sha256=str(data["training_identity_sha256"]),
            node_id=data.get("node_id"),
            pipeline=data.get("pipeline"),
            git_commit=data.get("git_commit"),
            git_dirty=data.get("git_dirty"),
        )


def _git_state(project_root: Path) -> tuple[str | None, bool | None]:
    from haute._git_core import _is_git_repo, _rev_parse, _run_git_ok, git_binary_available

    if not git_binary_available() or not _is_git_repo(project_root):
        return None, None
    commit = _rev_parse("HEAD", cwd=project_root)
    ok, status = _run_git_ok("status", "--porcelain", cwd=project_root)
    return commit, (bool(status.strip()) if ok else None)


def capture_provenance(
    *,
    job_id: str,
    node_label: str,
    training_identity_sha256: str,
    project_root: Path,
    node_id: str | None = None,
    pipeline_source: str | None = None,
) -> CandidateProvenance:
    """Capture provenance for a run that has just finished training."""
    root = project_root.resolve()
    pipeline: str | None = None
    if pipeline_source:
        source = Path(pipeline_source)
        source = (source if source.is_absolute() else root / source).resolve()
        if source.is_relative_to(root):
            pipeline = source.relative_to(root).as_posix()
    git_commit, git_dirty = _git_state(root)
    return CandidateProvenance(
        job_id=job_id,
        node_label=node_label,
        trained_at=datetime.now(UTC),
        training_identity_sha256=training_identity_sha256,
        node_id=node_id,
        pipeline=pipeline,
        git_commit=git_commit,
        git_dirty=git_dirty,
    )


@dataclass(frozen=True)
class CandidateArtifacts:
    """The files a candidate run publishes, keyed by kind."""

    model: Path
    feature_contract: Path
    evidence: Mapping[str, Path]

    def require_files(self) -> None:
        missing = [
            kind
            for kind, path in (
                ("model", self.model),
                ("feature_contract", self.feature_contract),
                *self.evidence.items(),
            )
            if not path.is_file()
        ]
        if missing:
            raise HauteValidationError(
                f"Candidate run artifacts are missing: {', '.join(missing)}. Retrain the model."
            )


@dataclass(frozen=True)
class CandidateRun:
    """Everything one ``log_experiment`` call publishes."""

    provenance: CandidateProvenance
    run_name: str
    tags: Mapping[str, str]
    params: Mapping[str, Any]
    metrics: Mapping[str, float]
    diagnostics: ModelDiagnostics
    metadata: ModelCardMetadata
    artifacts: CandidateArtifacts


def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise HauteValidationError(f"Candidate run metric {name} must be a finite number")
    return float(value)


def _namespaced_metrics(
    *,
    final_test_metrics: Mapping[str, Any],
    development_metrics: Mapping[str, Any],
    diagnostics: ModelDiagnostics,
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for name, value in final_test_metrics.items():
        metrics[f"final_test_{name}"] = _finite(f"final_test_{name}", value)
    for name, value in development_metrics.items():
        metrics[f"development_{name}"] = _finite(f"development_{name}", value)
    for name, summary in diagnostics.selection_metrics.items():
        if not isinstance(summary, Mapping):
            raise HauteValidationError(f"Selection metric {name} must be a summary object")
        for statistic in _SELECTION_STATISTICS:
            if statistic in summary:
                key = f"selection_{name}_{statistic}"
                metrics[key] = _finite(key, summary[statistic])
    tuning = diagnostics.tuning
    if tuning is not None:
        metric_name = str(tuning["metric"])
        for label, field in _TUNING_METRICS:
            key = f"tuning_{label}_{metric_name}"
            metrics[key] = _finite(key, tuning.get(field))
    for name in _GLM_METRICS:
        if name in diagnostics.glm_fit_statistics:
            metrics[f"glm_{name}"] = _finite(f"glm_{name}", diagnostics.glm_fit_statistics[name])
    return metrics


def build_candidate_run(
    *,
    provenance: CandidateProvenance,
    algorithm: str,
    weight: str,
    evaluation_strategy: str,
    validation_method: str,
    evaluation_config: Mapping[str, Any],
    evaluation_plan_sha256: str,
    final_params: Mapping[str, Any],
    final_test_metrics: Mapping[str, Any],
    development_metrics: Mapping[str, Any],
    diagnostics: ModelDiagnostics,
    development_rows: int,
    final_test_rows: int,
    best_iteration: int | None,
    artifacts: CandidateArtifacts,
    fit_evidence: Mapping[str, Any] | None = None,
) -> CandidateRun:
    """Build the contracted run for a trained model.

    ``development_metrics`` must be empty when a final test was reserved: those
    diagnostics are never presented alongside held-out performance.
    """
    from haute import __version__
    from haute.modelling._feature_contract import load_contract

    artifacts.require_files()
    contract = load_contract(artifacts.feature_contract)
    missing_types = [name for name in contract.features if name not in contract.feature_types]
    if not contract.features or missing_types:
        raise HauteValidationError(
            "The model's feature contract has no complete feature types; retrain the model."
            + (f" Missing types: {', '.join(missing_types)}." if missing_types else "")
        )
    if final_test_metrics and development_metrics:
        raise HauteValidationError(
            "A run with a final test must not publish development diagnostics as metrics"
        )

    metadata = ModelCardMetadata(
        algorithm=algorithm,
        task=contract.task,
        development_rows=development_rows,
        final_test_rows=final_test_rows,
        features=list(contract.features),
        evaluation_config=dict(evaluation_config),
        best_iteration=best_iteration,
        feature_types=dict(contract.feature_types),
        categorical_features=list(contract.categorical_features),
        target_name=contract.target_name,
        target_type=contract.target_type,
        offset_name=contract.offset_column or "",
        offset_type="Float64" if contract.offset_column else "",
    )

    params: dict[str, Any] = {
        "algorithm": algorithm,
        "task": contract.task,
        "target": contract.target_name,
        "weight": weight,
        "evaluation_strategy": evaluation_strategy,
        "validation_method": validation_method,
        **{f"param_{name}": value for name, value in final_params.items()},
        "development_rows": development_rows,
        "final_test_rows": final_test_rows,
        "n_features": len(contract.features),
    }
    if best_iteration is not None:
        params["best_iteration"] = best_iteration
    for name, value in (fit_evidence or {}).items():
        if value is not None:
            params[f"fit_{name}"] = value
    if diagnostics.tuning is not None:
        for field in _TUNING_PARAMS:
            if field not in diagnostics.tuning:
                raise HauteValidationError(f"tuning summary is missing {field}")
            if diagnostics.tuning[field] is not None:
                params[f"tuning_{field}"] = diagnostics.tuning[field]

    trained_at = provenance.trained_at.astimezone(UTC)
    tags = {
        "haute.contract_version": CANDIDATE_RUN_CONTRACT_VERSION,
        "haute.job_id": provenance.job_id,
        "haute.node_label": provenance.node_label,
        "haute.trained_at": trained_at.isoformat(),
        "haute.version": __version__,
        "haute.algorithm": algorithm,
        "haute.task": contract.task,
        "haute.target": contract.target_name,
        "haute.evaluation_plan_sha256": evaluation_plan_sha256,
        "haute.training_identity_sha256": provenance.training_identity_sha256,
    }
    if provenance.node_id:
        tags["haute.node_id"] = provenance.node_id
    if provenance.pipeline:
        tags["haute.pipeline"] = provenance.pipeline
    if provenance.git_commit:
        tags["haute.git_commit"] = provenance.git_commit
    if provenance.git_dirty is not None:
        tags["haute.git_dirty"] = "true" if provenance.git_dirty else "false"

    return CandidateRun(
        provenance=provenance,
        run_name=f"{provenance.node_label} · {trained_at:%Y-%m-%d %H:%M} UTC",
        tags=tags,
        params=params,
        metrics=_namespaced_metrics(
            final_test_metrics=final_test_metrics,
            development_metrics=development_metrics,
            diagnostics=diagnostics,
        ),
        diagnostics=diagnostics,
        metadata=metadata,
        artifacts=artifacts,
    )
