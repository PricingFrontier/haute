"""The optimiser's solver session (OPT-W01): every native call in one capped worker per solve.

These run real spawns in process mode. The server must never call price-contour
or hold a native optimiser object; a session that exceeds its cap ends only
itself; the job records why its runtime is gone, and later requests say so.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from haute._dedicated_workers import child_limit_evidence_counter, record_job_notification
from haute._native_memory_limit import JOB_OBJECT_MSG_JOB_MEMORY_LIMIT, native_memory_caps_supported
from haute._process_memory import process_rss_bytes
from haute._step_progress import StepProgress, current_job_progress_reporter
from haute.routes import _optimiser_frontier, _optimiser_service, _optimiser_session
from haute.routes._optimiser_session import (
    RUNTIME_MODE_KEY,
    RUNTIME_UNAVAILABLE_KEY,
    SESSION_KEY,
    SolverSession,
)
from tests.test_optimiser_setup_worker import _banding_parquet as _banding_for
from tests.test_optimiser_setup_worker import (
    _online_graph,
    _poll,
    _process_mode,
    _ratebook_graph,
    _scored_parquet,
    _solve,
    _solve_summary,
)

pytestmark = pytest.mark.skipif(
    not native_memory_caps_supported(), reason="needs a native memory cap"
)

_MIB = 1024 * 1024
_FRONTIER: dict[str, Any] = {
    "frontier_enabled": True,
    "frontier_ranges": {"volume": {"min": 0.5, "max": 2.0}},
}


# ── Child commands substituted for the real ones (imported by the spawned child) ──


def _signal_dir() -> Path:
    return Path(os.environ["HAUTE_TEST_SESSION_SIGNALS"])


def block_until_released(_request: Any) -> Any:
    """Announce entry, then wait until the test writes its release file."""
    reporter = current_job_progress_reporter()
    if reporter is not None:
        reporter(StepProgress(done=1, total=1000, label="entered"))
    while not (_signal_dir() / "release").exists():
        time.sleep(0.05)
    raise RuntimeError("released without a result")


def block_then_return(request: Any) -> Any:
    """Like ``block_until_released``, but return a result once released."""
    try:
        block_until_released(request)
    except RuntimeError:
        pass
    # Write into the parent-owned directory as the real apply does.
    (Path(request.artifact_dir) / "result.parquet").write_bytes(b"not adopted")
    return {"kind": "never_published", "directory": request.artifact_dir}


def notified_memory_error(_request: Any) -> Any:
    record_job_notification(JOB_OBJECT_MSG_JOB_MEMORY_LIMIT, child_limit_evidence_counter())
    raise MemoryError("the job refused an allocation")


def plain_memory_error(_request: Any) -> Any:
    raise MemoryError("an allocation failed")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    from haute._sandbox import set_project_root

    set_project_root(tmp_path)
    return tmp_path


# ── Helpers ─────────────────────────────────────────────────────────────


@pytest.fixture()
def signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "signals"
    directory.mkdir()
    monkeypatch.setenv("HAUTE_TEST_SESSION_SIGNALS", str(directory))
    return directory


@pytest.fixture()
def process_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _process_mode(monkeypatch)
    yield
    for job_id, job in _optimiser_service_store().list_jobs().items():
        session = job.get(SESSION_KEY)
        if isinstance(session, SolverSession):
            session.terminate("cancelled")


def _optimiser_service_store() -> Any:
    from haute.routes.optimiser import _store

    return _store


def _job(job_id: str) -> Any:
    return _optimiser_service_store().require_job(job_id)


def _session_of(job_id: str) -> SolverSession:
    session = _job(job_id).get(SESSION_KEY)
    assert isinstance(session, SolverSession)
    return session


def _start(client: Any, graph: dict[str, Any]) -> str:
    response = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
    assert response.status_code == 200, response.text
    return str(response.json()["job_id"])


def _gone(pid: int | None, seconds: float = 10.0) -> bool:
    assert pid is not None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process_rss_bytes(pid) is None:
            return True
        time.sleep(0.05)
    return False


def _cap_command(monkeypatch: pytest.MonkeyPatch, operation: str, growth_mb: int) -> Any:
    """Give one session operation a tiny grant, leaving setup's admission untouched."""
    real = _optimiser_session.admit_growth_grant

    def admit(**kwargs: Any) -> Any:
        if kwargs.get("operation") != operation:
            return real(**kwargs)
        key = "HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB"
        previous = os.environ.get(key)
        os.environ[key] = str(growth_mb)
        try:
            return real(**kwargs)
        finally:
            if previous is None:
                os.environ.pop(key)
            else:
                os.environ[key] = previous

    monkeypatch.setattr(_optimiser_session, "admit_growth_grant", admit)
    return real


def _poison_server_price_contour(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every server-side binding of the price-contour loader raise.

    The consumers import the loader by name, so patching its module alone would
    miss them. Spawned children re-import and are unaffected.
    """
    from haute import _price_contour

    loader = _price_contour.price_contour

    def poisoned() -> Any:
        raise AssertionError("the server process called price-contour")

    patched = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("haute") or module is None:
            continue
        for attribute, value in list(vars(module).items()):
            if value is loader:
                monkeypatch.setattr(module, attribute, poisoned)
                patched.append(f"{name}.{attribute}")
    return patched


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _walk(item)


def _apply_point(client: Any, job_id: str, point_index: int) -> Any:
    return client.post("/api/optimiser/apply", json={"job_id": job_id, "point_index": point_index})


# ── Tests ───────────────────────────────────────────────────────────────


class TestSessionSolve:
    @pytest.mark.parametrize("mode", ["online", "ratebook"])
    def test_the_server_makes_no_native_call_and_holds_no_native_object(
        self, client, project: Path, process_mode: None, monkeypatch: pytest.MonkeyPatch, mode: str
    ) -> None:
        import haute.routes._optimiser_frontier  # noqa: F401 - bind every consumer first
        import haute.routes._optimiser_input  # noqa: F401
        import haute.routes._optimiser_solver  # noqa: F401

        patched = _poison_server_price_contour(monkeypatch)
        assert {
            "haute.routes._optimiser_solver.price_contour",
            "haute.routes._optimiser_input.price_contour",
            "haute.routes._optimiser_frontier.price_contour",
        } <= set(patched)
        scored = _scored_parquet(project)
        graph = (
            _ratebook_graph(scored, _banding_for(scored))
            if mode == "ratebook"
            else _online_graph(
                scored, frontier_enabled=True, frontier_ranges={"volume": {"min": 0.5, "max": 2.0}}
            )
        )
        status = _solve(client, graph)
        assert status["status"] == "completed", status.get("message")
        job_id = next(
            jid
            for jid, job in _optimiser_service_store().list_jobs().items()
            if job.get("status") == "completed" and job.get(RUNTIME_MODE_KEY) == "session"
        )
        job = _job(job_id)
        assert isinstance(job.get(SESSION_KEY), SolverSession)
        native_types = ("QuoteGrid", "OnlineOptimiser", "RatebookOptimiser", "SolveResult")
        for key, value in job.items():
            for item in _walk(value):
                assert type(item).__name__ not in native_types, key
            assert key not in ("solver", "quote_grid", "solve_result", "ratebook_factor_contexts")

    def test_session_solve_matches_the_thread_path(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graph = _online_graph(_scored_parquet(project))
        thread_status = _solve(client, graph)
        _process_mode(monkeypatch)
        process_status = _solve(client, graph)
        assert process_status["status"] == "completed", process_status.get("message")
        assert _solve_summary(process_status) == _solve_summary(thread_status)
        assert process_status["result"]["adjustments"] == thread_status["result"]["adjustments"]


class TestSessionMemory:
    def test_a_grid_over_the_cap_ends_only_the_session(
        self, client, project: Path, process_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import polars as pl

        big = project / "big.parquet"
        n = 400_000
        pl.DataFrame(
            {
                "quote_id": [f"quote-{i // 3:09d}" for i in range(n)],
                "scenario_index": pl.Series([i % 3 for i in range(n)], dtype=pl.Int32),
                "scenario_value": pl.Series(
                    [(0.8, 1.0, 1.2)[i % 3] for i in range(n)], dtype=pl.Float32
                ),
                "expected_income": pl.Series([float(i % 97) for i in range(n)], dtype=pl.Float32),
                "volume": pl.Series([1.0 + (i % 5) / 10 for i in range(n)], dtype=pl.Float32),
            }
        ).write_parquet(big)
        real_grant = _cap_command(monkeypatch, "optimiser_solve", growth_mb=8)
        server_rss = process_rss_bytes(os.getpid())
        job_id = _start(client, _online_graph(big))
        status = _poll(client, job_id)

        assert status["status"] == "memory_limited", status
        detail = _job(job_id)["error_detail"]
        assert detail["reason"] == "solver_session_memory_limit"
        # The native cap and the RSS watchdog are sized from the same grant; either
        # may record the breach first, and the wording follows whichever did.
        assert detail["memory_evidence"] in {"cap_confirmed", "watchdog", "suspected"}
        assert detail["stage"]
        assert "8.0 MiB" in status["message"]
        if detail["memory_evidence"] == "suspected":
            assert "most likely" in status["message"]
        # The job reports the session command's own grant and cap, not setup's.
        command = _job(job_id)["solver_session_command"]
        assert command["operation"] == "optimiser_solve"
        assert command["grant_bytes"] == 8 * _MIB
        assert _job(job_id)["execution_metrics"]["memory_limit_bytes"] == 8 * _MIB
        after = process_rss_bytes(os.getpid())
        assert server_rss is not None and after is not None
        assert after - server_rss < 200 * _MIB
        monkeypatch.setattr(_optimiser_session, "admit_growth_grant", real_grant)
        recovered = _solve(client, _online_graph(_scored_parquet(project)))
        assert recovered["status"] == "completed", recovered.get("message")

    def test_a_limiter_record_makes_the_message_definite(
        self, client, project: Path, process_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_optimiser_service, "build_and_solve", notified_memory_error)
        job_id = _start(client, _online_graph(_scored_parquet(project)))
        status = _poll(client, job_id)
        assert status["status"] == "memory_limited", status
        assert _job(job_id)["error_detail"]["memory_evidence"] == "cap_confirmed"
        assert "needed more than" in status["message"]
        assert "most likely" not in status["message"]

    def test_a_memory_error_without_a_limiter_record_is_worded_as_likely(
        self, client, project: Path, process_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_optimiser_service, "build_and_solve", plain_memory_error)
        job_id = _start(client, _online_graph(_scored_parquet(project)))
        status = _poll(client, job_id)
        assert status["status"] == "memory_limited", status
        assert _job(job_id)["error_detail"]["memory_evidence"] == "suspected"
        assert "most likely" in status["message"]


class TestSessionLifecycle:
    def test_cancelling_a_solve_mid_command_ends_its_process_at_once(
        self,
        client,
        project: Path,
        process_mode: None,
        signals: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_optimiser_service, "build_and_solve", block_until_released)
        job_id = _start(client, _online_graph(_scored_parquet(project)))
        deadline = time.monotonic() + 60
        while _job(job_id).get("message") != "entered":
            assert time.monotonic() < deadline, "the session never entered its command"
            time.sleep(0.05)
        pid = _session_of(job_id).pid
        started = time.monotonic()
        response = client.post(f"/api/optimiser/solve/cancel/{job_id}")
        assert response.status_code == 200, response.text
        assert _gone(pid, 5.0)
        assert time.monotonic() - started < 5.0
        assert _job(job_id)["status"] == "cancelled"
        assert SESSION_KEY not in _job(job_id)
        # The graph/node is free again once the session is gone.
        deadline = time.monotonic() + 10
        while True:
            again = client.post(
                "/api/optimiser/solve",
                json={"graph": _online_graph(_scored_parquet(project)), "node_id": "opt"},
            )
            if again.status_code == 200 or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        assert again.status_code == 200, again.text
        (signals / "release").write_text("1")
        _poll(client, again.json()["job_id"])

    def test_dropping_the_runtime_ends_the_process_and_later_points_say_why(
        self, client, project: Path, process_mode: None
    ) -> None:
        graph = _online_graph(
            _scored_parquet(project),
            frontier_enabled=True,
            frontier_ranges={"volume": {"min": 0.5, "max": 2.0}},
        )
        job_id = _start(client, graph)
        status = _poll(client, job_id)
        assert status["status"] == "completed", status.get("message")
        pid = _session_of(job_id).pid
        _optimiser_service_store().clear_result_data(job_id, keys=(SESSION_KEY,))
        assert _gone(pid)
        assert _job(job_id)[RUNTIME_UNAVAILABLE_KEY]["reason"] == "expired"
        response = _apply_point(client, job_id, 1)
        assert response.status_code == 410, response.text
        assert response.json()["detail"]["runtime_reason"] == "expired"

    def test_a_point_apply_over_its_grant_answers_507_and_later_points_410(
        self, client, project: Path, process_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graph = _online_graph(
            _scored_parquet(project),
            frontier_enabled=True,
            frontier_ranges={"volume": {"min": 0.5, "max": 2.0}},
        )
        job_id = _start(client, graph)
        assert _poll(client, job_id)["status"] == "completed"
        monkeypatch.setattr(_optimiser_frontier, "apply_point", plain_memory_error)
        first = _apply_point(client, job_id, 1)
        assert first.status_code == 507, first.text
        assert first.json()["detail"]["memory_evidence"] == "suspected"
        second = _apply_point(client, job_id, 2)
        assert second.status_code == 410, second.text
        assert second.json()["detail"]["runtime_reason"] == "solver_session_memory_limited"
        assert "most likely" in second.json()["detail"]["message"]
        status = client.get(f"/api/optimiser/solve/status/{job_id}").json()
        assert status["status"] == "completed"

    def test_a_point_applies_and_a_recompute_runs_in_the_session(
        self, client, project: Path, process_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graph = _online_graph(
            _scored_parquet(project),
            frontier_enabled=True,
            frontier_ranges={"volume": {"min": 0.5, "max": 2.0}},
        )
        monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "thread")
        thread_job = _start(client, graph)
        assert _poll(client, thread_job)["status"] == "completed"
        thread_page = _apply_point(client, thread_job, 1)
        assert thread_page.status_code == 200, thread_page.text
        monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
        _poison_server_price_contour(monkeypatch)
        job_id = _start(client, graph)
        assert _poll(client, job_id)["status"] == "completed"
        page = _apply_point(client, job_id, 1)
        assert page.status_code == 200, page.text
        assert page.json()["preview"] == thread_page.json()["preview"]
        recompute = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": job_id,
                "threshold_ranges": {"volume": [0.6, 1.8]},
                "n_points_per_dim": 4,
            },
        )
        assert recompute.status_code == 200, recompute.text
        sweep_id = recompute.json()["job_id"]
        deadline = time.monotonic() + 60
        while True:
            sweep = client.get(f"/api/optimiser/frontier/status/{sweep_id}").json()
            if sweep["status"] != "running" or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        assert sweep["status"] == "completed", sweep
        assert _session_of(job_id).alive


# ── Stage-level memory failures through the real command and its catches ──


def _run_real_build_and_solve(request: Any) -> Any:
    from haute.routes._optimiser_session_worker import build_and_solve

    return build_and_solve(request)


def _raise_memory(*_args: Any, **_kwargs: Any) -> Any:
    raise MemoryError("this stage ran out of memory")


def adjustments_run_out(request: Any) -> Any:
    import haute.routes._optimiser_solver as solver

    solver.histogram_of_frame = _raise_memory
    return _run_real_build_and_solve(request)


def frontier_runs_out(request: Any) -> Any:
    import haute.routes._optimiser_solver as solver

    solver._compute_frontier = _raise_memory
    return _run_real_build_and_solve(request)


def solver_runs_out(request: Any) -> Any:
    import haute.routes._optimiser_solver as solver

    class _Library:
        def OnlineOptimiser(self, **_kwargs: Any) -> Any:  # noqa: N802 - the library's name
            raise MemoryError("the solver ran out of memory")

    solver.price_contour = lambda: _Library()
    return _run_real_build_and_solve(request)


class TestStageMemoryFailures:
    @pytest.mark.parametrize("command", [adjustments_run_out, frontier_runs_out, solver_runs_out])
    def test_a_stage_that_runs_out_is_never_a_completed_diagnostic(
        self,
        client,
        project: Path,
        process_mode: None,
        monkeypatch: pytest.MonkeyPatch,
        command: Any,
    ) -> None:
        monkeypatch.setattr(_optimiser_service, "build_and_solve", command)
        job_id = _start(client, _online_graph(_scored_parquet(project), **_FRONTIER))
        status = _poll(client, job_id)
        assert status["status"] == "memory_limited", status
        assert _job(job_id)["error_detail"]["memory_evidence"] in {"suspected", "cap_confirmed"}

    @pytest.mark.parametrize("stage", ["histogram_of_frame", "_compute_frontier"])
    def test_thread_mode_stages_that_run_out_end_memory_limited(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch, stage: str
    ) -> None:
        from haute.routes import _optimiser_solver

        monkeypatch.setattr(_optimiser_solver, stage, _raise_memory)
        status = _solve(client, _online_graph(_scored_parquet(project), **_FRONTIER))
        assert status["status"] == "memory_limited", status
        assert "ran out of memory" in status["message"]


def _wait_for_entry(session: SolverSession, seconds: float = 60.0) -> None:
    """Wait until the session's running command has reported that it entered."""
    deadline = time.monotonic() + seconds
    while session.stage != "entered":
        assert time.monotonic() < deadline, "the session never entered its command"
        time.sleep(0.05)


def _solved_session_job(client: Any, project: Path) -> str:
    job_id = _start(client, _online_graph(_scored_parquet(project), **_FRONTIER))
    assert _poll(client, job_id)["status"] == "completed"
    return job_id


class TestSessionCommandLifetime:
    def test_dropping_the_runtime_mid_command_waits_for_it_then_discards_it(
        self,
        client,
        project: Path,
        process_mode: None,
        signals: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import threading

        from haute.routes import _optimiser_artifacts

        job_id = _solved_session_job(client, project)
        session = _session_of(job_id)
        pid = session.pid
        created: list[Path] = []
        real_new_directory = _optimiser_artifacts._new_apply_artifact_directory

        def recording_new_directory() -> Path:
            created.append(real_new_directory())
            return created[-1]

        monkeypatch.setattr(
            _optimiser_frontier, "_new_apply_artifact_directory", recording_new_directory
        )
        monkeypatch.setattr(_optimiser_frontier, "apply_point", block_then_return)
        answers: list[Any] = []
        requester = threading.Thread(target=lambda: answers.append(_apply_point(client, job_id, 1)))
        requester.start()
        _wait_for_entry(session)
        _optimiser_service_store().clear_result_data(job_id, keys=(SESSION_KEY,))
        # Pinned by its running command: detached, but alive until the command returns.
        time.sleep(0.5)
        assert process_rss_bytes(pid) is not None
        (signals / "release").write_text("1")
        requester.join(timeout=30)
        # Its result is discarded: the runtime it would publish into is gone.
        assert answers and answers[0].status_code == 410, answers[0].text
        # The discarded apply's artifact directory went with it.
        assert len(created) == 1
        assert not created[0].exists()
        assert _gone(pid)
        assert _job(job_id)[RUNTIME_UNAVAILABLE_KEY]["reason"] == "expired"

    def test_cancelling_a_recompute_is_cooperative_and_keeps_the_session(
        self,
        client,
        project: Path,
        process_mode: None,
        signals: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job_id = _solved_session_job(client, project)
        session = _session_of(job_id)
        real_sweep = _optimiser_frontier.session_sweep
        monkeypatch.setattr(_optimiser_frontier, "session_sweep", block_until_released)
        started = client.post(
            "/api/optimiser/frontier",
            json={"job_id": job_id, "threshold_ranges": {"volume": [0.6, 1.8]}},
        )
        assert started.status_code == 200, started.text
        sweep_id = started.json()["job_id"]
        _wait_for_entry(session)
        cancelled = client.post(f"/api/optimiser/frontier/cancel/{sweep_id}")
        assert cancelled.status_code == 200, cancelled.text
        time.sleep(0.5)
        assert session.alive
        (signals / "release").write_text("1")
        monkeypatch.setattr(_optimiser_frontier, "session_sweep", real_sweep)
        deadline = time.monotonic() + 30
        while True:
            page = _apply_point(client, job_id, 1)
            if page.status_code != 409 or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        assert page.status_code == 200, page.text
        assert session.alive
        assert client.get(f"/api/optimiser/frontier/status/{sweep_id}").json()["status"] == (
            "cancelled"
        )


class TestSweepPublication:
    def test_a_recompute_whose_runtime_goes_before_it_publishes_does_not_publish(
        self, client, project: Path, process_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job_id = _solved_session_job(client, project)
        generation = _job(job_id)["frontier_generation"]
        real = _optimiser_frontier._invalidate_frontier_apply_artifact_handles

        def runtime_gone_at_publication(job: Any) -> Any:
            # Inside the publication, after the parent job was read: the runtime goes.
            _optimiser_service_store().clear_result_data(job_id, keys=(SESSION_KEY,))
            return real(job)

        monkeypatch.setattr(
            _optimiser_frontier,
            "_invalidate_frontier_apply_artifact_handles",
            runtime_gone_at_publication,
        )
        started = client.post(
            "/api/optimiser/frontier",
            json={"job_id": job_id, "threshold_ranges": {"volume": [0.6, 1.8]}},
        )
        assert started.status_code == 200, started.text
        sweep_id = started.json()["job_id"]
        deadline = time.monotonic() + 60
        while True:
            sweep = client.get(f"/api/optimiser/frontier/status/{sweep_id}").json()
            if sweep["status"] != "running" or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        assert sweep["status"] == "contract_error", sweep
        assert _job(job_id)["frontier_generation"] == generation


class TestSolveOwnership:
    def test_a_failure_before_the_session_starts_frees_the_graph_node(
        self, client, project: Path, process_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.routes import _optimiser_artifacts

        real = _optimiser_artifacts._new_apply_artifact_directory
        calls = {"n": 0}

        def full_disk_once() -> Path:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(28, "No space left on device")
            return real()

        monkeypatch.setattr(_optimiser_artifacts, "_new_apply_artifact_directory", full_disk_once)
        graph = _online_graph(_scored_parquet(project))
        first = _poll(client, _start(client, graph))
        assert first["status"] == "error", first
        retry = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert retry.status_code == 200, retry.text
        assert _poll(client, retry.json()["job_id"])["status"] == "completed"
