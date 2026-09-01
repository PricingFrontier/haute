"""Validate and atomically publish artifacts produced by training workers."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from haute._env import int_env
from haute._logging import get_logger
from haute._worker_protocol import (
    WorkerArtifactManifest,
    WorkerProtocolError,
    WorkerResultManifest,
)
from haute.modelling._evaluation import (
    EvaluationPlan,
)
from haute.schemas import (
    EvaluationReportPayload,
    TuningReportPayload,
)

logger = get_logger(component="server.modelling.train")

_WINDOWS_ARTIFACT_REPLACE_RETRIES = 3
_WINDOWS_ARTIFACT_REPLACE_RETRY_DELAY_SECONDS = 0.1


_DEFAULT_BORDER_COUNT = 128  # CatBoost border count for VRAM estimation
_DEFAULT_DEPTH = 6  # CatBoost tree depth for VRAM estimation
_TRAINING_JOB_TYPE: Literal["training"] = "training"
_DISPERSION_JOB_TYPE: Literal["dispersion_estimate"] = "dispersion_estimate"
_JOB_TYPE_KEY = "job_type"
_CORE_TRAINING_ARTIFACT_KINDS = frozenset({"model", "feature_contract"})
_EVALUATION_ARTIFACT_PATHS = {
    "evaluation_plan": "plan_path",
    "evaluation_results": "results_path",
    "evaluation_report": "report_path",
}
_TUNING_ARTIFACT_PATHS = {
    "tuning_plan": "plan_path",
    "tuning_trials": "trials_path",
    "tuning_report": "report_path",
}
_EVALUATED_TRAINING_ARTIFACT_KINDS = frozenset(
    _CORE_TRAINING_ARTIFACT_KINDS | set(_EVALUATION_ARTIFACT_PATHS)
)
_TRAINING_ARTIFACT_KINDS = frozenset(
    _EVALUATED_TRAINING_ARTIFACT_KINDS | set(_TUNING_ARTIFACT_PATHS)
)


def _max_training_artifact_bytes() -> int:
    return int_env("HAUTE_TRAIN_ARTIFACT_MAX_BYTES", 4 * 1024**3)


# Deterministic seed for the RAM/row-limit training downsample. A fixed
# constant (rather than a config knob) keeps training reproducible by default
# and matches the editor's default evaluation seed.
_TRAINING_DOWNSAMPLE_SEED = 42


class TrainingArtifactPublicationError(RuntimeError):
    """A Windows contention error prevented a training artifact replacement."""

    def __init__(self, source: Path, destination: Path, attempts: int) -> None:
        self.source = source
        self.destination = destination
        self.attempts = attempts
        super().__init__(
            "Could not publish training artifact after "
            f"{attempts} attempts: {source} -> {destination}"
        )


def _is_windows_artifact_contention(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32}


def _replace_training_artifact(source: Path, destination: Path) -> None:
    """Replace an artifact, retrying only transient Windows file contention."""
    attempts = 0
    while True:
        attempts += 1
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            retryable = sys.platform == "win32" and _is_windows_artifact_contention(exc)
            if not retryable:
                raise
            if attempts > _WINDOWS_ARTIFACT_REPLACE_RETRIES:
                error = TrainingArtifactPublicationError(source, destination, attempts)
                raise error from exc
            time.sleep(_WINDOWS_ARTIFACT_REPLACE_RETRY_DELAY_SECONDS)


def _validate_evaluation_artifact_contents(
    staged_and_final: Mapping[str, tuple[Path, Path]],
    *,
    response_fit_count: int,
) -> dict[str, Any]:
    """Validate and reconstruct the digest-linked evaluation response."""
    from haute.modelling._evaluation import (
        aggregate_evaluation_results,
        file_sha256,
        load_evaluation_report,
        load_evaluation_results,
    )

    try:
        plan_path = staged_and_final["evaluation_plan"][0]
        results_path = staged_and_final["evaluation_results"][0]
        report_path = staged_and_final["evaluation_report"][0]
        plan = EvaluationPlan.from_plain_data(json.loads(plan_path.read_bytes()))
        plan_sha256 = file_sha256(plan_path)
        results = load_evaluation_results(results_path, plan_sha256=plan_sha256)
        results_sha256 = file_sha256(results_path)
        report = load_evaluation_report(report_path)
        expected_report = aggregate_evaluation_results(
            plan,
            results,
            tuple(report.metrics),
            results_sha256=results_sha256,
        )
        if expected_report.to_plain_data() != report.to_plain_data():
            raise ValueError("evaluation report does not match the persisted plan and results")
        return {
            "schema_version": 1,
            "strategy": plan.config.strategy,
            "validation_method": plan.config.validation["method"],
            "validation_fit_count": len(plan.validation_fits),
            "fit_count": response_fit_count,
            "development_rows": len(plan.development_positions),
            "final_test_rows": len(plan.test_positions),
            "selection_fits": [fit.to_plain_data() for fit in results.fits],
            "selection_metrics": {
                metric: dict(values) for metric, values in report.metrics.items()
            },
            "plan_sha256": plan_sha256,
            "results_sha256": results_sha256,
            "summary": dict(plan.summary),
        }
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkerProtocolError(f"Training evaluation artifact set is malformed: {exc}") from exc


def _validate_tuning_artifact_contents(
    staged_and_final: Mapping[str, tuple[Path, Path]],
    *,
    evaluation_plan_sha256: str,
) -> dict[str, Any]:
    """Validate and reconstruct the digest-linked tuning response."""
    from haute.modelling._evaluation import file_sha256
    from haute.modelling._tuning import (
        build_tuning_report,
        load_tuning_plan,
        load_tuning_report,
        load_tuning_trials,
    )

    try:
        plan_path = staged_and_final["tuning_plan"][0]
        trials_path = staged_and_final["tuning_trials"][0]
        report_path = staged_and_final["tuning_report"][0]
        plan = load_tuning_plan(plan_path)
        if plan.evaluation_plan_sha256 != evaluation_plan_sha256:
            raise ValueError("tuning plan does not link to the evaluation plan")
        plan_sha256 = file_sha256(plan_path)
        trials = load_tuning_trials(trials_path, plan_sha256=plan_sha256)
        trials_sha256 = file_sha256(trials_path)
        report = load_tuning_report(report_path)
        expected_report = build_tuning_report(
            plan,
            trials,
            trials_sha256=trials_sha256,
            final_params=report.final_params,
            final_tree_count=report.final_tree_count,
        )
        if expected_report.to_plain_data() != report.to_plain_data():
            raise ValueError("tuning report does not match the persisted plan and trials")
        return {
            **report.to_plain_data(),
            "trials": [trial.to_plain_data() for trial in trials.trials],
        }
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkerProtocolError(f"Training tuning artifact set is malformed: {exc}") from exc


def _publish_training_artifacts(
    manifest: WorkerResultManifest,
    *,
    artifact_root: Path,
    output_root: Path,
    job_id: str,
    expected_model_name: str,
    expected_evaluation: EvaluationReportPayload,
    expected_tuning: TuningReportPayload | None = None,
) -> dict[str, Path]:
    """Atomically publish one complete evaluation run and optional tuning set."""
    by_kind: dict[str, WorkerArtifactManifest] = {}
    for artifact in manifest.artifacts:
        if artifact.kind in by_kind:
            raise WorkerProtocolError(f"Duplicate training artifact kind {artifact.kind!r}")
        if artifact.lifetime != "staged":
            raise WorkerProtocolError("Training artifacts must have staged lifetime")
        by_kind[artifact.kind] = artifact
    artifact_kinds = set(by_kind)
    if artifact_kinds not in (
        set(_EVALUATED_TRAINING_ARTIFACT_KINDS),
        set(_TRAINING_ARTIFACT_KINDS),
    ):
        raise WorkerProtocolError(
            "Training completion requires a model, feature contract, and complete "
            "three-artifact evaluation set, with an optional complete tuning set"
        )
    if expected_evaluation is None:
        raise WorkerProtocolError("Training response must declare evaluation artifacts")
    has_tuning_artifacts = set(_TUNING_ARTIFACT_PATHS) <= artifact_kinds
    if (expected_tuning is None) != (not has_tuning_artifacts):
        raise WorkerProtocolError("Training response and tuning artifact set disagree")

    root = artifact_root.resolve()
    destination_root = output_root.resolve()
    staged_and_final: dict[str, tuple[Path, Path]] = {}
    ordered_kinds = (
        "model",
        "feature_contract",
        "evaluation_plan",
        "evaluation_results",
        "evaluation_report",
        "tuning_plan",
        "tuning_trials",
        "tuning_report",
    )
    for kind in ordered_kinds:
        selected_artifact = by_kind.get(kind)
        if selected_artifact is None:
            continue
        relative = Path(selected_artifact.relative_path)
        if len(relative.parts) != 2 or relative.parts[0] != "output":
            raise WorkerProtocolError(
                f"Training artifact {selected_artifact.relative_path!r} is not in the staged output"
            )
        staged = (root / relative).resolve()
        final = (destination_root / relative.name).resolve()
        staged_and_final[kind] = (staged, final)

    from haute.modelling._training_job import (
        evaluation_artifact_filenames,
        model_contract_filename,
        tuning_artifact_filenames,
    )

    model_staged, _model_final = staged_and_final["model"]
    contract_staged, _contract_final = staged_and_final["feature_contract"]
    if model_staged.stem != expected_model_name:
        raise WorkerProtocolError("Training model filename does not match the requested name")
    if contract_staged.name != model_contract_filename(model_staged.stem):
        raise WorkerProtocolError("Training model and feature contract filenames do not match")
    evaluation_names = evaluation_artifact_filenames(expected_model_name)
    expected_evaluation_names = {
        "evaluation_plan": evaluation_names["plan"],
        "evaluation_results": evaluation_names["results"],
        "evaluation_report": evaluation_names["report"],
    }
    for kind, expected_name in expected_evaluation_names.items():
        staged, _final = staged_and_final[kind]
        if staged.name != expected_name:
            raise WorkerProtocolError(
                f"Training {kind} filename does not match the requested model name"
            )
    expected_evaluation_response = expected_evaluation.model_dump(
        mode="json",
        exclude_none=True,
    )
    for kind, response_field in _EVALUATION_ARTIFACT_PATHS.items():
        if expected_evaluation_response.pop(response_field) != by_kind[kind].relative_path:
            raise WorkerProtocolError(
                f"Training evaluation response path does not match the staged {kind} manifest"
            )
    artifact_evaluation_response = _validate_evaluation_artifact_contents(
        staged_and_final,
        response_fit_count=expected_evaluation_response["fit_count"],
    )
    if expected_evaluation_response != artifact_evaluation_response:
        raise WorkerProtocolError(
            "Training evaluation response does not match the staged artifact contents"
        )

    tuning_names = tuning_artifact_filenames(expected_model_name)
    if has_tuning_artifacts:
        tuning_response = cast(TuningReportPayload, expected_tuning)
        expected_tuning_names = {
            "tuning_plan": tuning_names["plan"],
            "tuning_trials": tuning_names["trials"],
            "tuning_report": tuning_names["report"],
        }
        for kind, expected_name in expected_tuning_names.items():
            staged, _final = staged_and_final[kind]
            if staged.name != expected_name:
                raise WorkerProtocolError(
                    f"Training {kind} filename does not match the requested model name"
                )
        expected_tuning_response = tuning_response.model_dump(
            mode="json",
            exclude_none=True,
        )
        for kind, response_field in _TUNING_ARTIFACT_PATHS.items():
            if expected_tuning_response.pop(response_field) != by_kind[kind].relative_path:
                raise WorkerProtocolError(
                    f"Training tuning response path does not match the staged {kind} manifest"
                )
        artifact_tuning_response = _validate_tuning_artifact_contents(
            staged_and_final,
            evaluation_plan_sha256=artifact_evaluation_response["plan_sha256"],
        )
        if expected_tuning_response != artifact_tuning_response:
            raise WorkerProtocolError(
                "Training tuning response does not match the staged artifact contents"
            )

    obsolete_names: list[str] = []
    if not has_tuning_artifacts:
        obsolete_names.extend(tuning_names.values())
    obsolete_finals = tuple((destination_root / filename).resolve() for filename in obsolete_names)
    destination_root.mkdir(parents=True, exist_ok=True)

    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        managed_finals = [
            *(final for _staged, final in staged_and_final.values()),
            *obsolete_finals,
        ]
        for final in managed_finals:
            if final.exists() or final.is_symlink():
                backup = final.with_name(f".{final.name}.{job_id}.haute-backup")
                if backup.exists() or backup.is_symlink():
                    raise FileExistsError(f"Training artifact backup already exists: {backup}")
                _replace_training_artifact(final, backup)
                backups[final] = backup
        for staged, final in staged_and_final.values():
            _replace_training_artifact(staged, final)
            published.append(final)
    except BaseException as exc:
        rollback_errors: list[BaseException] = []
        for final in reversed(published):
            try:
                if final.exists() or final.is_symlink():
                    final.unlink()
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
        for final, backup in reversed(tuple(backups.items())):
            try:
                if backup.exists() or backup.is_symlink():
                    _replace_training_artifact(backup, final)
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
        for rollback_error in rollback_errors:
            exc.add_note(f"Artifact rollback failed: {rollback_error}")
        raise

    for backup in backups.values():
        try:
            backup.unlink()
        except OSError as exc:
            logger.warning(
                "training_artifact_post_commit_cleanup_failed",
                path=str(backup),
                cleanup_kind="backup",
                error=str(exc),
                error_type=type(exc).__name__,
            )
    try:
        shutil.rmtree(root)
    except OSError as exc:
        logger.warning(
            "training_artifact_post_commit_cleanup_failed",
            path=str(root),
            cleanup_kind="staging_root",
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return {kind: final for kind, (_staged, final) in staged_and_final.items()}
