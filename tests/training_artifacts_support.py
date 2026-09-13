"""Test helpers for completed training jobs that own their artifact directories.

Canvas training publishes each job's model, feature contract and evaluation
evidence into a job-owned directory under the training artifact root and records
an artifact handle plus provenance on the job. These helpers reproduce that
published state without running a training worker.
"""

from __future__ import annotations

import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from haute.modelling._candidate_run import CandidateProvenance
from haute.modelling._training_job import (
    evaluation_artifact_filenames,
    model_contract_filename,
)
from haute.routes import _training_artifacts
from haute.routes._job_store import JobStore
from haute.schemas import TrainResponse
from tests.job_store_support import seed_job

TRAINING_IDENTITY = "a" * 64
TRAINED_AT = datetime(2026, 9, 13, 8, 30, tzinfo=UTC)


def publish_trained_job(
    store: JobStore,
    job_id: str,
    *,
    root: Path,
    model_file: Path,
    result: TrainResponse,
    config: dict[str, Any],
    contract_file: Path | None = None,
    node_label: str = "model",
    node_id: str | None = None,
) -> Path:
    """Seed a completed training job exactly as canvas publication leaves it.

    Copies the model (and its contract, from ``contract_file`` or the model's
    sibling) into a job-owned directory, writes the three evaluation evidence
    files, rewrites the result paths, and records the artifact handle and
    provenance. Returns the job's published output directory.
    """
    stem = model_file.stem
    directory = root / f"train_{job_id}"
    output = directory / "output"
    output.mkdir(parents=True)
    published_model = output / model_file.name
    shutil.copyfile(model_file, published_model)
    contract_source = contract_file or model_file.parent / model_contract_filename(stem)
    published_contract = output / model_contract_filename(stem)
    if contract_source.is_file():
        shutil.copyfile(contract_source, published_contract)
    evidence_names = evaluation_artifact_filenames(stem)
    evidence = {}
    for kind, name in (
        ("evaluation_plan", evidence_names["plan"]),
        ("evaluation_results", evidence_names["results"]),
        ("evaluation_report", evidence_names["report"]),
    ):
        (output / name).write_text("{}", encoding="utf-8")
        evidence[kind] = output / name

    assert result.evaluation is not None
    published_result = result.model_copy(
        update={
            "model_path": str(published_model),
            "evaluation": result.evaluation.model_copy(
                update={
                    "plan_path": str(evidence["evaluation_plan"]),
                    "results_path": str(evidence["evaluation_results"]),
                    "report_path": str(evidence["evaluation_report"]),
                }
            ),
        }
    )
    handle = {
        "kind": _training_artifacts.TRAINING_ARTIFACTS_HANDLE_KIND,
        "version": 1,
        "directory": str(directory),
        "files": {
            "model": published_model.name,
            "feature_contract": published_contract.name,
            **{kind: path.name for kind, path in evidence.items()},
        },
    }
    provenance = CandidateProvenance(
        job_id=job_id,
        node_label=node_label,
        trained_at=TRAINED_AT,
        training_identity_sha256=TRAINING_IDENTITY,
        node_id=node_id,
    )
    seed_job(
        store,
        job_id,
        {
            "status": "completed",
            "job_type": "training",
            "result": published_result,
            "config": config,
            "node_label": node_label,
            "created_at": time.time(),
            "artifact_handles": {_training_artifacts.TRAINING_ARTIFACTS_HANDLE_KEY: handle},
            "provenance": provenance.to_plain_data(),
        },
    )
    return output
