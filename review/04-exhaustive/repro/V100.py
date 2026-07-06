"""Isolated reproduction for V100.

Claim: ``train_status`` (src/haute/routes/modelling.py:67-68) reads
``timeout = job.get("timeout", _DEFAULT_TIMEOUT)`` then evaluates
``if start and (time.monotonic() - start) > timeout:``.  The stored
``timeout`` is taken verbatim from the modelling node's free-form
``config`` in ``_launch_background``:
``"timeout": config.get("timeout", _DEFAULT_TIMEOUT)`` (_train_service.py:1052).

``node.data.config`` is ``dict[str, Any]`` (_types.py:599) with no coercion
of ``timeout`` anywhere (_validate_config / _train_config.py / schemas.py all
ignore the key).  If a user sets ``"timeout": null`` (realistic when clearing
the field), the key is PRESENT with value ``None``.  Because
``dict.get(key, default)`` returns ``None`` (NOT the default) when the key
exists with value ``None``, the stored timeout is ``None``.  For a *running*
job ``start_time`` is always set, so the comparison branch runs on every poll
and ``(time.monotonic() - start) > None`` raises
``TypeError: '>' not supported between instances of 'float' and 'NoneType'``
-> a 500 on every ``/train/status/{job_id}`` request while the job keeps
running.

This repro exercises the REAL async ``train_status`` endpoint against the real
``JobStore`` (the module-level ``_store``).  ISOLATION: purely in-memory; no
rating/, src/, tests/, or real project files are read or written.  The store
is reset to a pristine state via ``get_job_store.cache_clear()`` so this run
cannot observe or mutate any other job state.
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

# Make the in-repo source importable without touching project data files.
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(_REPO_SRC))

from fastapi import HTTPException  # noqa: E402

from haute.routes._job_store import get_job_store  # noqa: E402
from haute.routes._train_service import _DEFAULT_TIMEOUT  # noqa: E402


def _fresh_store_and_endpoint():
    """Return a pristine training store and the real train_status coroutine fn.

    ``get_job_store.cache_clear()`` drops any cached singleton so the store we
    get is empty; re-importing ``haute.routes.modelling`` then rebinds its
    module-level ``_store`` to this same fresh instance, so the job we create
    is exactly the one the endpoint will read.
    """
    get_job_store.cache_clear()
    import importlib

    import haute.routes.modelling as modelling

    importlib.reload(modelling)
    return modelling._store, modelling.train_status


def _make_running_job(store, timeout_value) -> str:
    """Create a *running* job whose start_time is well in the past.

    ``start_time`` is set far enough back that ``time.monotonic() - start``
    is a positive float, so the comparison branch in ``train_status`` is
    actually evaluated (mirrors a job that has been running for a while).
    """
    return store.create_job(
        {
            "status": "running",
            "progress": 0.5,
            "message": "Training",
            # 10 s in the past -> elapsed is a real positive float.
            "start_time": time.monotonic() - 10.0,
            # This is exactly what _launch_background stores when the node
            # config has ``timeout: null`` (None) or a string, since
            # ``config.get("timeout", _DEFAULT_TIMEOUT)`` returns the present
            # value verbatim rather than the default.
            "timeout": timeout_value,
        }
    )


def _call(endpoint, job_id):
    """Run the async endpoint to completion, returning (result, exception)."""
    try:
        result = asyncio.run(endpoint(job_id))
        return result, None
    except BaseException as exc:  # noqa: BLE001 - characterising the failure
        return None, exc


def main() -> int:
    failures: list[str] = []

    # ----------------------------------------------------------------------
    # Load-bearing semantics: dict.get(key, default) returns None (not the
    # default) when the key is present with a null/None value.  This is the
    # entire mechanism by which a stored ``timeout: None`` survives.
    # ----------------------------------------------------------------------
    config_with_null = {"timeout": None}  # JSON ``{"timeout": null}`` -> this
    got = config_with_null.get("timeout", _DEFAULT_TIMEOUT)
    print(f"_DEFAULT_TIMEOUT = {_DEFAULT_TIMEOUT!r}")
    print(f'{{"timeout": null}}.get("timeout", _DEFAULT_TIMEOUT) -> {got!r}')
    if got is not None:
        failures.append(
            "precondition broke: dict.get on a present-but-None key did not "
            f"return None (got {got!r}); the bug mechanism assumes it does."
        )

    # ----------------------------------------------------------------------
    # Case A (the bug): timeout == None  ->  TypeError 500 on a running job.
    # ----------------------------------------------------------------------
    store, train_status = _fresh_store_and_endpoint()
    job_id_none = _make_running_job(store, None)
    result_none, err_none = _call(train_status, job_id_none)
    print("\n--- Case A: stored timeout = None (config had timeout: null) ---")
    if err_none is None:
        print(f"  train_status -> returned {type(result_none).__name__} (no error)")
    else:
        print(f"  train_status -> {type(err_none).__name__}: {err_none}")

    if not isinstance(err_none, TypeError):
        failures.append(
            "Case A: expected TypeError from '(elapsed) > None', got "
            f"{type(err_none).__name__ if err_none else 'no error'}: {err_none}"
        )
    elif "'>' not supported" not in str(err_none) or "NoneType" not in str(err_none):
        failures.append(
            "Case A: TypeError raised but message did not match the predicted "
            f"'>' / NoneType comparison failure: {err_none!r}"
        )
    else:
        print("  REPRODUCED: running-job status poll raises TypeError (-> HTTP 500)")

    # ----------------------------------------------------------------------
    # Case B (string, the report's secondary claim): timeout == "3600".
    # ----------------------------------------------------------------------
    store_s, train_status_s = _fresh_store_and_endpoint()
    job_id_str = _make_running_job(store_s, "3600")
    _result_str, err_str = _call(train_status_s, job_id_str)
    print('\n--- Case B: stored timeout = "3600" (string from config) ---')
    if err_str is None:
        print("  train_status -> returned (no error)")
    else:
        print(f"  train_status -> {type(err_str).__name__}: {err_str}")
    if not isinstance(err_str, TypeError):
        failures.append(
            "Case B: expected TypeError comparing float > str, got "
            f"{type(err_str).__name__ if err_str else 'no error'}: {err_str}"
        )
    else:
        print("  REPRODUCED: string timeout also raises TypeError on the same line")

    # ----------------------------------------------------------------------
    # Control: a valid int timeout that is NOT yet exceeded must NOT raise and
    # must NOT trip the timeout transition.  This proves the crash above is
    # caused specifically by the bad ``timeout`` value, not by unrelated setup
    # (job construction, async invocation, response building).
    # ----------------------------------------------------------------------
    store_ok, train_status_ok = _fresh_store_and_endpoint()
    # Large timeout so 10 s elapsed does not exceed it -> stays "running".
    job_id_ok = _make_running_job(store_ok, _DEFAULT_TIMEOUT)
    result_ok, err_ok = _call(train_status_ok, job_id_ok)
    print(f"\n--- Control: stored timeout = {_DEFAULT_TIMEOUT} (valid int) ---")
    if err_ok is not None:
        print(f"  train_status -> {type(err_ok).__name__}: {err_ok}")
        failures.append(
            "Control: valid int timeout unexpectedly raised "
            f"{type(err_ok).__name__}: {err_ok} — setup itself is broken, so "
            "Case A is not a clean demonstration of the timeout bug."
        )
    else:
        status = getattr(result_ok, "status", None)
        print(f"  train_status -> OK, status={status!r}")
        if status != "running":
            failures.append(
                "Control: expected the un-exceeded job to stay 'running', got "
                f"{status!r}."
            )

    # Guard against a false-positive: ensure the None-case error was NOT merely
    # a 404/HTTPException from a missing job (which would mean our setup, not
    # the comparison, produced the failure).
    if isinstance(err_none, HTTPException):
        failures.append(
            "Case A error was an HTTPException (status "
            f"{err_none.status_code}) — that is a setup artdefact, not the "
            "predicted comparison TypeError."
        )

    print()
    if failures:
        print("REPRO RESULT: claim NOT reproduced as predicted")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print("REPRO RESULT: BUG REPRODUCED — a running training job whose stored")
    print("timeout is None (from config ``timeout: null``) makes every")
    print("/train/status/{job_id} poll raise TypeError")
    print("('>' not supported between instances of 'float' and 'NoneType'),")
    print("i.e. a permanent HTTP 500, while the job keeps running. A valid int")
    print("timeout polls cleanly (control), confirming the value is the cause.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # pragma: no cover - surface unexpected harness errors
        traceback.print_exc()
        raise SystemExit(2)
