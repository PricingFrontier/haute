"""Isolated reproduction for V097.

Claim: ``TrainService._launch_background`` registers a cancellation token in the
``CancellableJobRegistry`` (``register_latest`` at _train_service.py:1043) BEFORE
constructing the ``TrainingJob`` (line 1097). If construction raises (e.g. a
malformed ``config['split']`` makes ``SplitConfig(**split)`` raise ``TypeError``,
or invalid split sizes make ``SplitConfig.__post_init__`` raise ``ValueError``),
the exception propagates out of ``_launch_background`` BEFORE the worker thread is
started at line 1232. Neither release path runs:

  * the worker ``finally`` (release at 1227) never runs — the thread never started;
  * the thread-start ``except`` (release at 1245) is not reached — the exception
    is raised before entering the try at 1232.

``start()``'s own handlers only call ``execution_context.release_admission()`` and
``JobLifecycle.transition`` — they never touch ``self._training_jobs``. Because
``register_latest`` keys on ``(_TRAINING_JOB_TYPE, job_id)`` (the unique job id),
no later job ever supersedes the stale entry, and ``CancellableJobRegistry`` has no
TTL eviction. So ``_tokens_by_job_id`` and ``_latest_by_key`` leak unboundedly.

This script drives the REAL ``TrainService._launch_background`` (real in-memory
``JobStore``, real ``CancellableJobRegistry``) with a malformed split config and
asserts the specific wrong VALUE: after the exception, the registry STILL contains
the job's token (expected: empty after a failed launch). ISOLATION: no disk reads;
the only filesystem object is a tempfile path string handed to the (never-built)
job; ``JobStore`` is a pure in-memory dict.
"""

from __future__ import annotations

import os
import tempfile

from haute._execution_context import ExecutionCancellationToken
from haute.routes._job_store import JobStore
from haute.routes._train_service import (
    _TRAINING_JOB_TYPE,
    TrainService,
)


class _FakeExecutionContext:
    """Minimal faithful stand-in for ``ExecutionContext``.

    ``_launch_background`` only touches ``.cancellation_token`` (line 1046) on the
    leak path; ``.release_admission()`` / ``.metrics_payload()`` are reached only
    inside the worker ``finally`` and the thread-start ``except`` — neither of
    which runs when construction raises before the thread starts. We still expose
    a real ``ExecutionCancellationToken`` and record release_admission calls so we
    can prove the cleanup that DID happen excludes the registry.
    """

    def __init__(self) -> None:
        self.cancellation_token = ExecutionCancellationToken()
        self.release_admission_calls = 0

    def release_admission(self) -> None:
        self.release_admission_calls += 1

    def metrics_payload(self, **_kwargs: object) -> dict[str, object]:
        return {}


def main() -> None:
    store = JobStore()
    service = TrainService(store)

    # Create the running job row so the early ``atomic_update`` (line 1048)
    # succeeds and we reach the ``TrainingJob`` construction at line 1097.
    job_id = store.create_job(
        {
            "status": "running",
            "job_type": _TRAINING_JOB_TYPE,
            "progress": 0.0,
            "message": "Starting",
        }
    )

    # Malformed split: an unexpected key makes ``SplitConfig(**split)`` raise
    # ``TypeError`` synchronously inside ``TrainingJob.__init__`` (line 361),
    # i.e. at _train_service.py:1097 — BEFORE the thread-start try at line 1232.
    # ``target`` is present so ``build_training_job_kwargs`` (line 1092) succeeds
    # and we exercise the construction-time failure specifically.
    config = {
        "target": "y",
        "algorithm": "catboost",
        "split": {"strategy": "random", "definitely_not_a_split_field": 1},
    }

    # A tempfile path string — never read, since construction fails first. Kept
    # only to mirror the real call signature. No disk I/O is performed on it.
    fd, tmp_parquet = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)

    ctx = _FakeExecutionContext()

    raised: BaseException | None = None
    try:
        service._launch_background(
            job_id,
            "node-1",
            config,
            {"iterations": 1},  # train_params
            tmp_parquet,
            None,  # ram_warning
            None,  # total_source_rows
            execution_context=ctx,
        )
    except BaseException as exc:  # noqa: BLE001 - we assert on it below
        raised = exc
    finally:
        # The real ``_launch_background`` only unlinks tmp_parquet inside the
        # worker/thread-start paths (neither runs here), so clean it up to keep
        # the sandbox tidy. This is test hygiene, not part of the assertion.
        if os.path.exists(tmp_parquet):
            os.unlink(tmp_parquet)

    # 1) Construction must have raised (the trigger fired as predicted).
    assert raised is not None, "expected _launch_background to raise on malformed split"
    assert isinstance(raised, TypeError), f"expected TypeError, got {type(raised).__name__}: {raised}"
    assert "definitely_not_a_split_field" in str(raised), (
        f"expected SplitConfig TypeError mentioning the bad field, got: {raised!r}"
    )

    # 2) THE BUG — specific wrong VALUE. After a launch that failed before the
    # thread started, the registry must be empty (no worker exists to cancel /
    # release). Instead the token leaks: both registry maps still hold the job.
    registry = service._training_jobs
    key = (_TRAINING_JOB_TYPE, job_id)
    leaked_token = job_id in registry._tokens_by_job_id
    leaked_key = registry._latest_by_key.get(key) == job_id

    print(f"raised:                       {type(raised).__name__}: {raised}")
    print(f"release_admission_calls:      {ctx.release_admission_calls}")
    print(f"_tokens_by_job_id has job_id: {leaked_token}  (expected False)")
    print(f"_latest_by_key has key:       {leaked_key}  (expected False)")
    print(f"_tokens_by_job_id size:       {len(registry._tokens_by_job_id)}  (expected 0)")
    print(f"_latest_by_key size:          {len(registry._latest_by_key)}  (expected 0)")

    # 3) Demonstrate the unbounded-growth consequence: a SECOND distinct job
    # (different unique key) does NOT supersede or evict the stale first entry,
    # because supersession only fires on an identical key. After a second failed
    # launch, the registry holds TWO leaked entries.
    job_id2 = store.create_job(
        {"status": "running", "job_type": _TRAINING_JOB_TYPE, "progress": 0.0, "message": "Starting"}
    )
    fd2, tmp2 = tempfile.mkstemp(suffix=".parquet")
    os.close(fd2)
    try:
        try:
            service._launch_background(
                job_id2,
                "node-2",
                config,
                {"iterations": 1},
                tmp2,
                None,
                None,
                execution_context=_FakeExecutionContext(),
            )
        except TypeError:
            pass
    finally:
        if os.path.exists(tmp2):
            os.unlink(tmp2)

    grew_unbounded = len(registry._tokens_by_job_id) == 2
    print(f"_tokens_by_job_id size after 2nd failed launch: {len(registry._tokens_by_job_id)}  (expected 0)")

    # 4) The leaked job is terminal-by-failure; cancel() cannot clean it up.
    # The job row is still 'running' here (start()'s transition to 'error' lives
    # in the route, which we bypassed), so simulate the real terminal state and
    # confirm cancel() returns early WITHOUT calling release. In production,
    # start()'s except already transitioned the job to 'error', so the route's
    # cancel() guard (status != 'running' -> return early) skips release entirely.
    store.atomic_update(job_id, {"status": "error"})
    cancel_result = service.cancel(job_id)
    still_leaked_after_cancel = job_id in registry._tokens_by_job_id
    print(f"cancel() on errored job status: {cancel_result.get('status')!r}")
    print(f"still leaked after cancel():     {still_leaked_after_cancel}  (expected False)")

    # ---- Assertions that fail BECAUSE of the bug (demonstrably wrong values) ----
    assert not leaked_token, (
        "BUG NOT REPRODUCED: registry released the token after failed launch"
    )
    assert not leaked_key, (
        "BUG NOT REPRODUCED: registry released the key after failed launch"
    )
    assert not grew_unbounded, (
        "BUG NOT REPRODUCED: registry did not accumulate a second leaked entry"
    )
    assert not still_leaked_after_cancel, (
        "BUG NOT REPRODUCED: cancel() cleaned up the leaked token"
    )

    print("\nNO LEAK — registry cleaned up; V097 would be REFUTED.")


if __name__ == "__main__":
    main()
