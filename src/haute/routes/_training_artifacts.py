"""Validate, own and release the artifact sets produced by training workers.

Each canvas training job writes into its own marked directory under
:func:`training_artifact_root`. Publication validates that set in place, and the
completed job record owns the directory through an artifact handle, so exports
always read the exact bytes their job trained and nothing is ever replaced.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import HTTPException

from haute._artifact_housekeeping import (
    create_owned_artifact_directory,
    reap_stale_artifact_directories,
)
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

_TRAINING_ARTIFACT_ROOT_NAME = "haute/artifacts/v1/modelling_training"
_TRAINING_ARTIFACT_DIR_PREFIX = "train_"
_TRAINING_ARTIFACT_OWNER = "modelling_training"
_TRAINING_ARTIFACT_HANDLE_VERSION = 1
TRAINING_ARTIFACTS_HANDLE_KEY = "training_artifacts"
TRAINING_ARTIFACTS_HANDLE_KIND = "modelling_training_artifacts"
_STAGED_OUTPUT_DIRNAME = "output"


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


def _validate_evaluation_artifact_contents(
    artifact_paths: Mapping[str, Path],
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
        plan_path = artifact_paths["evaluation_plan"]
        results_path = artifact_paths["evaluation_results"]
        report_path = artifact_paths["evaluation_report"]
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
    artifact_paths: Mapping[str, Path],
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
        plan_path = artifact_paths["tuning_plan"]
        trials_path = artifact_paths["tuning_trials"]
        report_path = artifact_paths["tuning_report"]
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


def _validate_training_artifacts(
    manifest: WorkerResultManifest,
    *,
    artifact_root: Path,
    expected_model_name: str,
    expected_evaluation: EvaluationReportPayload,
    expected_tuning: TuningReportPayload | None = None,
) -> dict[str, Path]:
    """Validate one complete evaluation run and optional tuning set in place.

    Returns each artifact kind's absolute path inside *artifact_root*; no file is
    moved, so the validated directory is the published, immutable set.
    """
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
    artifact_paths: dict[str, Path] = {}
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
        if staged.parent != root / _STAGED_OUTPUT_DIRNAME:
            raise WorkerProtocolError(
                f"Training artifact {selected_artifact.relative_path!r} escapes the staged output"
            )
        artifact_paths[kind] = staged

    from haute.modelling._training_job import (
        evaluation_artifact_filenames,
        model_contract_filename,
        tuning_artifact_filenames,
    )

    model_staged = artifact_paths["model"]
    contract_staged = artifact_paths["feature_contract"]
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
        if artifact_paths[kind].name != expected_name:
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
        artifact_paths,
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
            if artifact_paths[kind].name != expected_name:
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
            artifact_paths,
            evaluation_plan_sha256=artifact_evaluation_response["plan_sha256"],
        )
        if expected_tuning_response != artifact_tuning_response:
            raise WorkerProtocolError(
                "Training tuning response does not match the staged artifact contents"
            )

    return artifact_paths


# ---------------------------------------------------------------------------
# Job-owned artifact directories
# ---------------------------------------------------------------------------


def training_artifact_root() -> Path:
    """The server-owned root that holds one marked directory per training job."""
    return (Path(tempfile.gettempdir()) / _TRAINING_ARTIFACT_ROOT_NAME).resolve()


def create_training_artifact_directory() -> Path:
    """Create a marked, job-owned directory for one training run's artifacts."""
    return create_owned_artifact_directory(
        training_artifact_root(), _TRAINING_ARTIFACT_DIR_PREFIX, _TRAINING_ARTIFACT_OWNER
    )


def training_artifacts_handle(
    artifact_root: Path,
    artifact_paths: Mapping[str, Path],
) -> dict[str, Any]:
    """The job-record handle that transfers ownership of a validated artifact set."""
    root = artifact_root.resolve()
    return {
        "kind": TRAINING_ARTIFACTS_HANDLE_KIND,
        "version": _TRAINING_ARTIFACT_HANDLE_VERSION,
        "directory": str(root),
        "files": {kind: path.name for kind, path in sorted(artifact_paths.items())},
    }


@dataclass(frozen=True)
class TrainingArtifactSet:
    """The immutable files one completed training job published."""

    directory: Path
    model: Path
    feature_contract: Path
    evaluation_plan: Path
    evaluation_results: Path
    evaluation_report: Path
    tuning_plan: Path | None = None
    tuning_trials: Path | None = None
    tuning_report: Path | None = None

    def evidence_paths(self) -> dict[str, Path]:
        """Evaluation and (when tuned) tuning artifacts keyed by kind."""
        paths = {
            "evaluation_plan": self.evaluation_plan,
            "evaluation_results": self.evaluation_results,
            "evaluation_report": self.evaluation_report,
        }
        if self.tuning_plan is not None:
            paths["tuning_plan"] = self.tuning_plan
        if self.tuning_trials is not None:
            paths["tuning_trials"] = self.tuning_trials
        if self.tuning_report is not None:
            paths["tuning_report"] = self.tuning_report
        return paths

    def all_paths(self) -> tuple[Path, ...]:
        return (self.model, self.feature_contract, *self.evidence_paths().values())


def _validated_handle_directory(handle: Mapping[str, Any]) -> Path:
    if handle.get("kind") != TRAINING_ARTIFACTS_HANDLE_KIND:
        raise ValueError("Invalid training artifact handle.")
    if handle.get("version") != _TRAINING_ARTIFACT_HANDLE_VERSION:
        raise ValueError("Unsupported training artifact handle.")
    raw_directory = handle.get("directory")
    if not isinstance(raw_directory, str) or not raw_directory or "\x00" in raw_directory:
        raise ValueError("Training artifact handle has no valid directory.")
    directory_input = Path(raw_directory)
    if not directory_input.is_absolute():
        raise ValueError("Training artifact handle must use an absolute directory.")
    directory = directory_input.resolve(strict=directory_input.exists())
    if directory.parent != training_artifact_root() or not directory.name.startswith(
        _TRAINING_ARTIFACT_DIR_PREFIX
    ):
        raise ValueError("Training artifact directory is outside the training artifact root.")
    return directory


def training_artifact_set_from_handle(handle: Mapping[str, Any]) -> TrainingArtifactSet:
    """Validate a handle and return its artifact paths (which may no longer exist)."""
    directory = _validated_handle_directory(handle)
    files = handle.get("files")
    if not isinstance(files, Mapping):
        raise ValueError("Training artifact handle has no file list.")
    kinds = set(files)
    if kinds not in (set(_EVALUATED_TRAINING_ARTIFACT_KINDS), set(_TRAINING_ARTIFACT_KINDS)):
        raise ValueError("Training artifact handle does not describe a complete artifact set.")
    output = directory / _STAGED_OUTPUT_DIRNAME
    paths: dict[str, Path] = {}
    for kind, name in files.items():
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError(f"Training artifact handle has an invalid {kind} file name.")
        paths[kind] = output / name
    return TrainingArtifactSet(directory=directory, **paths)


def cleanup_training_artifacts(handle: dict[str, Any]) -> None:
    """Remove a job's artifact directory once the job no longer owns it."""
    directory = _validated_handle_directory(handle)
    if directory.exists():
        shutil.rmtree(directory)


def reap_stale_training_artifacts(stale_after_seconds: int) -> dict[str, int]:
    """Reap stale marked training directories left by a previous server process."""
    report = reap_stale_artifact_directories(
        training_artifact_root(), _TRAINING_ARTIFACT_OWNER, stale_after_seconds
    )
    logger.info("training_artifact_reap_completed", report=report)
    return report


_HOLDS_LOCK = threading.Lock()
_ARTIFACT_HOLDS: Counter[str] = Counter()


@contextmanager
def hold_training_artifacts(job_id: str) -> Iterator[None]:
    """Keep supersede pruning away from a job's artifacts while an export reads them."""
    with _HOLDS_LOCK:
        _ARTIFACT_HOLDS[job_id] += 1
    try:
        yield
    finally:
        with _HOLDS_LOCK:
            _ARTIFACT_HOLDS[job_id] -= 1
            if _ARTIFACT_HOLDS[job_id] <= 0:
                del _ARTIFACT_HOLDS[job_id]


def release_training_artifacts_unless_held(job_id: str, release: Callable[[], object]) -> bool:
    """Run *release* for a superseded job unless an export currently holds its artifacts.

    The check and the release share the hold lock, so an export either acquires its
    hold first (and the release is skipped) or starts afterwards and finds the handle
    already detached.
    """
    with _HOLDS_LOCK:
        if _ARTIFACT_HOLDS.get(job_id, 0) > 0:
            return False
        release()
        return True


_UNAVAILABLE_DETAIL = {
    "error_code": "training_artifacts_unavailable",
    "message": (
        "This training result's model files are no longer available (the node was retrained, "
        "the result expired, or the server restarted). Train the model again to export it."
    ),
}


def require_training_artifacts(job: Mapping[str, Any]) -> TrainingArtifactSet:
    """Return a completed job's complete artifact set, or fail with ``410``."""
    handles = job.get("artifact_handles")
    handle = handles.get(TRAINING_ARTIFACTS_HANDLE_KEY) if isinstance(handles, Mapping) else None
    if not isinstance(handle, Mapping):
        raise HTTPException(status_code=410, detail=_UNAVAILABLE_DETAIL)
    try:
        artifacts = training_artifact_set_from_handle(handle)
    except ValueError as exc:
        logger.warning("training_artifact_handle_invalid", error=str(exc))
        raise HTTPException(status_code=410, detail=_UNAVAILABLE_DETAIL) from None
    if not all(path.is_file() for path in artifacts.all_paths()):
        raise HTTPException(status_code=410, detail=_UNAVAILABLE_DETAIL)
    return artifacts
