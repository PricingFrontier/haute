"""Export receipts: what a completed training job has been exported to.

A receipt is recorded on the training job when a log to MLflow or a save to a
model file succeeds, and the job status returns them, so the Export pane can say
where a result already went after a pane switch or a reload. MLflow logs carry an
operation ID: a retry with the same ID returns the recorded receipt instead of
creating a second run, and only one log of a job runs at a time.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException

from haute.routes._job_store import JobStore

MAX_RECEIPTS_PER_KIND = 20

ReceiptKind = Literal["mlflow", "model_files"]

_RECEIPTS_LOCK = threading.Lock()
_LOGS_IN_FLIGHT_LOCK = threading.Lock()
_LOGS_IN_FLIGHT: set[str] = set()


def receipt_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def export_receipts(job: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The job's receipts in wire shape (empty lists when nothing was exported)."""
    stored = job.get("export_receipts") or {}
    return {
        "mlflow": [dict(entry) for entry in stored.get("mlflow", [])],
        "model_files": [dict(entry) for entry in stored.get("model_files", [])],
    }


def record_receipt(
    store: JobStore, job_id: str, kind: ReceiptKind, receipt: Mapping[str, Any]
) -> None:
    """Append *receipt* to the job's receipts of *kind*, keeping the newest few."""
    with _RECEIPTS_LOCK:
        job = store.get_job(job_id)
        if job is None:
            # The job was evicted after the export succeeded; there is no record
            # left to annotate, and the caller still returns the outcome.
            return
        receipts = export_receipts(job)
        receipts[kind] = [*receipts[kind], dict(receipt)][-MAX_RECEIPTS_PER_KIND:]
        store.update_job(job_id, export_receipts=receipts)


def mlflow_receipt_for_operation(
    job: Mapping[str, Any], operation_id: str | None
) -> dict[str, Any] | None:
    """The recorded MLflow receipt for *operation_id*, if that operation already ran."""
    if not operation_id:
        return None
    for receipt in reversed(export_receipts(job)["mlflow"]):
        if receipt.get("operation_id") == operation_id:
            return receipt
    return None


@contextmanager
def single_flight_mlflow_log(job_id: str) -> Iterator[None]:
    """Run at most one MLflow log of a training job at a time.

    Raises:
        HTTPException: ``409`` with ``error_code`` ``mlflow_log_in_progress`` when
            another log of the same job is still running.
    """
    with _LOGS_IN_FLIGHT_LOCK:
        if job_id in _LOGS_IN_FLIGHT:
            in_progress = {
                "error_code": "mlflow_log_in_progress",
                "message": (
                    "This training result is already being logged to MLflow. Wait for that "
                    "log to finish before logging it again."
                ),
            }
            raise HTTPException(status_code=409, detail=in_progress)
        _LOGS_IN_FLIGHT.add(job_id)
    try:
        yield
    finally:
        with _LOGS_IN_FLIGHT_LOCK:
            _LOGS_IN_FLIGHT.discard(job_id)
