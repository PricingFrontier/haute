"""Adversarial repro for claim:
'train-status-error-keeps-completed-terminal-reason'.

Claim: when a COMPLETED training job (status='completed', terminal_reason='completed')
is polled and its stored result contains a non-finite float, the status handler's
except branch calls store.atomic_update(... status='error', result=None,
expected_status='completed') WITHOUT updating terminal_reason. Because that bypasses
JobLifecycle.transition, the merge preserves the stale terminal_reason='completed'.
The TrainStatusResponse then reports status=='error' AND terminal_reason=='completed'
-- a self-contradicting terminal record.

This repro builds the completed-job state THE WAY PRODUCTION DOES, via
JobLifecycle.transition(to='completed') (which is the only path that sets
status='completed' for a real job, and which always stamps terminal_reason='completed').
It then drives the real FastAPI handler with TestClient and asserts on the SPECIFIC
contradictory values (status='error', terminal_reason='completed'), not merely that
'something happened'.

ISOLATION: the modelling route uses an in-memory JobStore (no disk I/O). No project
root, no real project files, no rating/ or src/ or tests/ reads. The created job is
removed in a finally block.
"""

from __future__ import annotations

import math
import os

# Mirror the test harness: Haute's server mounts trusted-host + local-session
# middleware. The conftest autouse fixture sets a session token env and routes
# TestClient through host 'localhost' with the token header. We replicate that
# here (standalone script => no conftest) so the request reaches the handler
# instead of bouncing off the security middleware. This is faithful, not a
# work-around of the code under test (the modelling handler is unchanged).
os.environ.setdefault("HAUTE_LOCAL_SESSION_TOKEN", "repro-local-session-token")

from fastapi.testclient import TestClient  # noqa: E402

from haute._local_security import SESSION_TOKEN_HEADER, local_session_token  # noqa: E402
from haute.routes._job_lifecycle import JobLifecycle  # noqa: E402
from haute.routes.modelling import _store  # noqa: E402
from haute.schemas import TrainResponse  # noqa: E402
from haute.server import app  # noqa: E402  FastAPI app mounting the modelling router


def main() -> int:
    # A running job is the precondition JobLifecycle.transition(to='completed')
    # expects (expected_status defaults to 'running'). This mirrors a real job
    # that the worker just finished.
    job_id = _store.create_job(
        {
            "status": "running",
            "progress": 0.5,
            "message": "Training",
        }
    )

    # A result that carries a non-finite metric. model_construct skips validation
    # exactly the way a diagnostic value that slipped past the worker's own
    # _assert_json_finite would end up stored.
    bad_result = TrainResponse.model_construct(
        status="completed",
        job_id=job_id,
        metrics={"auc": float("nan")},
    )

    # Drive the COMPLETED transition the production way. This is the load-bearing
    # part: JobLifecycle.transition sets BOTH status='completed' AND
    # terminal_reason='completed'.
    merged = JobLifecycle(_store).transition(
        job_id,
        to="completed",
        message="Completed",
        fields={"result": bad_result},
    )
    assert merged is not None, "transition to completed unexpectedly skipped"

    # Sanity: the stored job is a genuine terminal 'completed' record.
    stored = _store.require_job(job_id)
    assert stored["status"] == "completed", stored.get("status")
    assert stored["terminal_reason"] == "completed", stored.get("terminal_reason")
    assert isinstance(stored["result"], TrainResponse)
    assert math.isnan(stored["result"].metrics["auc"])

    try:
        client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        resp = client.get(
            f"/api/modelling/train/status/{job_id}",
            headers={"host": "localhost", SESSION_TOKEN_HEADER: local_session_token()},
        )
        assert resp.status_code == 200, (resp.status_code, resp.text)
        data = resp.json()

        status = data["status"]
        terminal_reason = data["terminal_reason"]
        result = data["result"]

        print(f"observed status           = {status!r}")
        print(f"observed terminal_reason  = {terminal_reason!r}")
        print(f"observed result           = {result!r}")
        print(f"observed message          = {data.get('message')!r}")

        # The error branch must have fired (non-finite -> status flips to error,
        # result nulled). If it did not, the bug's precondition does not hold and
        # the claim would be refuted here.
        assert status == "error", (
            f"EXPECTED status to flip to 'error' on non-finite result, got {status!r}. "
            "The non-finite guard at the status endpoint did not fire -- claim precondition unmet."
        )
        assert result is None, f"expected result nulled on error, got {result!r}"

        # THE BUG: an error record that still claims it completed.
        if terminal_reason == "completed":
            print(
                "\nREPRODUCED: inconsistent terminal record -- "
                "status='error' but terminal_reason='completed'."
            )
            return 0

        # If terminal_reason was cleared/updated to something consistent, the
        # claim is refuted.
        print(
            f"\nNOT REPRODUCED: terminal_reason={terminal_reason!r} is not the stale "
            "'completed'; the status/terminal_reason pair is self-consistent."
        )
        return 1
    finally:
        _store.jobs.pop(job_id, None)


if __name__ == "__main__":
    raise SystemExit(main())
