"""Isolated reproduction for BUG-3.

Claim: In TrainService.start (src/haute/routes/_train_service.py), two prep
failures both end in a 507 / ``memory_limited`` terminal, but they persist
DIFFERENT job-record fields:

  (a) raw ExecutionAdmissionError / ExecutionMemoryLimitExceededError
      (caught at lines 533-541) -> fields={"error": str(detail)} ONLY.

  (b) GPU-VRAM HTTPException(507) (caught at lines 542-556) ->
      fields includes error / error_detail / error_code / http_status_code.

So a client polling the job/status record sees error_code=="memory_limit" and
a structured error_detail for one 507 cause (b) but NOT for the other (a),
even though both are 507 memory_limited terminals carrying an identical-shaped
``to_payload()`` dict.

This repro drives the REAL JobStore + REAL JobLifecycle.transition with field
dicts constructed VERBATIM from the two except branches, using the REAL
exception types and the REAL _memory_limit_http_exception helper, then reads
the persisted records back through the REAL require_job and asserts the
asymmetry on specific values.

Fully isolated: JobStore is in-memory (no disk path); no rating/, src/, or
tests/ files are read or written.
"""

from __future__ import annotations

from fastapi import HTTPException

from haute._execution_admission import ExecutionAdmissionError, ExecutionProfile
from haute.routes._job_lifecycle import JobLifecycle
from haute.routes._job_store import JobStore

# The REAL helper that both the route and this repro use to turn an admission
# error into the 507 HTTPException whose .detail is the structured payload.
from haute.routes.pipeline import _memory_limit_http_exception


def _seed_running_job(store: JobStore) -> str:
    return store.create_job({"status": "running", "job_type": "training"})


def _persist_admission_failure(lifecycle: JobLifecycle, job_id: str) -> HTTPException:
    """Replicate src/haute/routes/_train_service.py:533-541 VERBATIM."""
    exc = ExecutionAdmissionError(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_limit_bytes=8 * 1024**3,
        rss_at_admission_bytes=7 * 1024**3,
        reason="insufficient headroom for admission",
        rss_limit_bytes=8 * 1024**3,
        process_rss_limit_bytes=8 * 1024**3,
    )
    http_exc = _memory_limit_http_exception(exc)
    lifecycle.transition(
        job_id,
        to="memory_limited",
        message=str(http_exc.detail),
        fields={"error": str(http_exc.detail)},
    )
    return http_exc


def _persist_gpu_vram_failure(lifecycle: JobLifecycle, job_id: str) -> HTTPException:
    """Replicate the 507 HTTPException branch src/.../_train_service.py:542-556 VERBATIM.

    The GPU-VRAM refusal path raises an HTTPException(507) whose .detail is a
    structured dict (mirrors how the VRAM guard builds its payload with an
    ``error_code``). The except branch persists the rich field set.
    """
    detail = {
        "error_code": "memory_limit",
        "operation": "training_pipeline",
        "reason": "insufficient GPU VRAM for training",
    }
    exc = HTTPException(status_code=507, detail=detail)
    # --- begin verbatim transcription of lines 543-556 ---
    if exc.status_code == 507:
        lifecycle.transition(
            job_id,
            to="memory_limited",
            message=str(exc.detail),
            fields={
                "error": str(exc.detail),
                "error_detail": exc.detail,
                "error_code": (
                    exc.detail.get("error_code") if isinstance(exc.detail, dict) else None
                ),
                "http_status_code": exc.status_code,
            },
        )
    # --- end verbatim transcription ---
    return exc


def main() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)

    admission_job = _seed_running_job(store)
    gpu_job = _seed_running_job(store)

    admission_http = _persist_admission_failure(lifecycle, admission_job)
    _persist_gpu_vram_failure(lifecycle, gpu_job)

    admission_rec = store.require_job(admission_job)
    gpu_rec = store.require_job(gpu_job)

    # Sanity: both terminated in the SAME 507 memory_limited state.
    assert admission_rec["status"] == "memory_limited", admission_rec["status"]
    assert gpu_rec["status"] == "memory_limited", gpu_rec["status"]
    assert admission_http.status_code == 507, admission_http.status_code

    # Sanity: the admission error's payload DOES carry error_code=="memory_limit"
    # and a structured reason -- exactly the data the route discards.
    payload = admission_http.detail
    assert isinstance(payload, dict), type(payload)
    assert payload.get("error_code") == "memory_limit", payload
    assert "reason" in payload, payload

    print("=== persisted admission/memory-ceiling 507 record (branch a) ===")
    print("  keys:", sorted(admission_rec.keys()))
    print("  error_code     :", admission_rec.get("error_code"))
    print("  error_detail   :", admission_rec.get("error_detail"))
    print("  http_status_code:", admission_rec.get("http_status_code"))

    print("=== persisted GPU-VRAM 507 record (branch b) ===")
    print("  keys:", sorted(gpu_rec.keys()))
    print("  error_code     :", gpu_rec.get("error_code"))
    print("  error_detail   :", gpu_rec.get("error_detail"))
    print("  http_status_code:", gpu_rec.get("http_status_code"))

    # ----- The bug: asymmetric persisted fields for two identical-status 507s -----

    # Branch (b) -- GPU-VRAM -- persists the structured fields a frontend reads.
    assert gpu_rec.get("error_code") == "memory_limit", gpu_rec
    assert isinstance(gpu_rec.get("error_detail"), dict), gpu_rec
    assert gpu_rec["error_detail"].get("reason") == "insufficient GPU VRAM for training"
    assert gpu_rec.get("http_status_code") == 507, gpu_rec

    # Branch (a) -- admission / memory-ceiling -- is MISSING all of them, even
    # though its http_exc.detail carried the very same error_code/reason.
    assert "error_code" not in admission_rec, (
        "BUG NOT REPRODUCED: admission path unexpectedly persisted error_code"
    )
    assert "error_detail" not in admission_rec, (
        "BUG NOT REPRODUCED: admission path unexpectedly persisted error_detail"
    )
    assert "http_status_code" not in admission_rec, (
        "BUG NOT REPRODUCED: admission path unexpectedly persisted http_status_code"
    )

    # And concretely: a frontend that branches on job['error_code']=='memory_limit'
    # gets True for the GPU 507 but None (falsy) for the admission 507.
    assert gpu_rec.get("error_code") == "memory_limit"
    assert admission_rec.get("error_code") is None
    # A frontend reading error_detail.reason crashes / misses for branch (a):
    assert gpu_rec.get("error_detail", {}).get("reason") is not None
    assert admission_rec.get("error_detail") is None

    print()
    print("REPRO_OK: two 507 'memory_limited' terminals persist asymmetric fields.")
    print("  GPU-VRAM 507     -> error_code='memory_limit', error_detail=<dict>, http_status_code=507")
    print("  admission/mem 507 -> error_code MISSING, error_detail MISSING, http_status_code MISSING")
    print("  (the admission http_exc.detail DID contain error_code='memory_limit' + reason,")
    print("   so the data exists but the route stringifies it into 'error' and drops the rest.)")


if __name__ == "__main__":
    main()
