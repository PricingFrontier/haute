"""Concurrency correctness tests for server / routes (Phase 1 Package 1C).

Covers:
  - #6  ws_clients set mutated without lock — concurrent connects/disconnects
        must not corrupt the set.
  - #28 JobStore.update_job is not atomic — all mutation paths must route
        through atomic_update so readers never see a partial state.
  - #30 Sync I/O blocks the async event loop — a long-running async route
        must not stall a second, lightweight request.

Every test asserts a structural/behavioural invariant that fails before the
fix and passes after.  Concurrency tests avoid timing-only assertions and
instead drive the hazard deterministically (barriers, monkeypatched sleeps,
etc.) so they fail loudly on regressions.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

# ---------------------------------------------------------------------------
# #6 — ws_clients set mutated without a lock
# ---------------------------------------------------------------------------


@pytest.fixture()
def _isolated_ws_clients():
    """Ensure ws_clients starts empty and is restored after each test."""
    from haute.routes._helpers import ws_clients

    snapshot = set(ws_clients)
    ws_clients.clear()
    yield ws_clients
    ws_clients.clear()
    ws_clients.update(snapshot)


class TestWsClientsConcurrentMutation:
    """Hammer ws_clients from multiple threads/tasks; verify no item is lost.

    Production hazard: two WebSocket connections arrive on different workers
    (or one arrives while broadcast() is iterating).  ``set.add`` and
    ``set.discard`` are individually GIL-atomic, but a *read-then-modify*
    iteration pattern (``for ws in list(ws_clients): ... ws_clients.discard(ws)``)
    can race with concurrent add/discard.  The fix: route mutations through
    an explicit lock so ``broadcast`` sees a consistent snapshot.
    """

    def test_concurrent_adds_and_discards_preserve_invariant(
        self,
        _isolated_ws_clients,
    ) -> None:
        """Launch N adders and N discarders; no exception, no leftover junk."""
        from haute.routes._helpers import ws_clients

        n_threads = 32
        barrier = threading.Barrier(n_threads * 2)
        sentinels = [object() for _ in range(n_threads)]
        errors: list[BaseException] = []

        def adder(s: object) -> None:
            try:
                barrier.wait()
                # Every thread adds then removes its own sentinel
                ws_clients.add(s)
                ws_clients.discard(s)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def other(s: object) -> None:
            try:
                barrier.wait()
                # Another thread tries to remove the same sentinel concurrently
                ws_clients.discard(s)
                ws_clients.add(s)
                ws_clients.discard(s)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=adder, args=(s,), daemon=True) for s in sentinels] + [
            threading.Thread(target=other, args=(s,), daemon=True) for s in sentinels
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent mutation raised: {errors}"
        # After all threads finish, every sentinel must be discarded.
        for s in sentinels:
            assert s not in ws_clients, f"sentinel {s!r} leaked into ws_clients"

    def test_broadcast_safe_against_concurrent_discard(
        self,
        _isolated_ws_clients,
    ) -> None:
        """broadcast() iterates ws_clients; concurrent discards must not
        raise or skip live clients.  ``broadcast`` already snapshots via
        ``list(ws_clients)``; this test verifies that remains the case AND
        that the mutation is guarded so no set-level corruption occurs.
        """
        from unittest.mock import AsyncMock, MagicMock

        from haute.routes._helpers import broadcast, ws_clients

        # Build 100 fake clients
        clients = []
        for _ in range(100):
            m = MagicMock()
            m.send_text = AsyncMock()
            clients.append(m)
            ws_clients.add(m)

        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def mutator() -> None:
            try:
                barrier.wait()
                # Discard every other client while broadcast() is iterating
                for i, c in enumerate(clients):
                    if i % 2:
                        ws_clients.discard(c)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def broadcaster() -> None:
            try:
                barrier.wait()
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(broadcast({"type": "test"}))
                finally:
                    loop.close()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t_mut = threading.Thread(target=mutator, daemon=True)
        t_bc = threading.Thread(target=broadcaster, daemon=True)
        t_mut.start()
        t_bc.start()
        t_mut.join(timeout=5)
        t_bc.join(timeout=5)

        assert not errors, f"broadcast/discard race raised: {[type(e).__name__ for e in errors]}"

    def test_ws_clients_mutation_uses_explicit_lock(self) -> None:
        """Structural: ``ws_clients`` mutation paths must be guarded by
        an explicit lock (``asyncio.Lock`` / ``threading.Lock``) so the
        code remains safe under:
          - Multi-worker deployments where GIL semantics differ
          - PyPy / free-threaded CPython (3.13 ``--disable-gil``)
          - Future refactors that introduce compound read-modify-write
            patterns (e.g. ``if ws in ws_clients: ws_clients.discard(ws)``)

        We accept any of these fix shapes:
          1. A module-level ``ws_clients_lock`` / ``_ws_clients_lock``
             exposed from ``haute.routes._helpers``.
          2. ``ws_clients`` replaced with a thread-safe container that
             has an internal lock.
          3. A module-level lock in ``haute.server`` used by
             ``ws_sync`` and ``broadcast``.
        """
        import haute.routes._helpers as helpers
        import haute.server as server

        helpers_lock = getattr(helpers, "ws_clients_lock", None) or getattr(
            helpers, "_ws_clients_lock", None
        )
        server_lock = getattr(server, "ws_clients_lock", None) or getattr(
            server, "_ws_clients_lock", None
        )
        container_lock = getattr(helpers.ws_clients, "_lock", None)
        assert helpers_lock is not None or server_lock is not None or container_lock is not None, (
            "#6 requires an explicit lock for ws_clients mutation — "
            "expose `ws_clients_lock` on haute.routes._helpers or "
            "haute.server, or wrap the set in a locked container."
        )

    def test_concurrent_broadcast_while_client_disconnects(
        self,
        _isolated_ws_clients,
    ) -> None:
        """Reproduce the async corruption pattern: broadcast iterates
        ws_clients and may try to discard dead clients, while a
        disconnect handler runs concurrently and discards the same
        client.  Both ``.discard`` paths must be synchronized (or the
        iteration snapshot must be lock-protected) so no exception is
        raised and the invariant holds.

        A proper fix exposes a shared lock or uses a thread-safe
        container; we verify by stress-testing the pattern.
        """
        from unittest.mock import AsyncMock, MagicMock

        from haute.routes._helpers import broadcast, ws_clients

        # 50 clients, half of which are "dead" (raise on send_text)
        dead_clients = []
        live_clients = []
        for i in range(50):
            m = MagicMock()
            if i % 2:
                m.send_text = AsyncMock(side_effect=RuntimeError("closed"))
                dead_clients.append(m)
            else:
                m.send_text = AsyncMock()
                live_clients.append(m)
            ws_clients.add(m)

        errors: list[BaseException] = []
        barrier = threading.Barrier(3)

        def disconnector() -> None:
            """Mimic ws_sync finally block: discards clients mid-broadcast."""
            try:
                barrier.wait()
                for c in dead_clients:
                    ws_clients.discard(c)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def broadcaster(n: int) -> None:
            try:
                barrier.wait()
                loop = asyncio.new_event_loop()
                try:
                    for _ in range(n):
                        loop.run_until_complete(broadcast({"type": "upd"}))
                finally:
                    loop.close()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=disconnector, daemon=True),
            threading.Thread(target=broadcaster, args=(5,), daemon=True),
            threading.Thread(target=broadcaster, args=(5,), daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, (
            "#6: concurrent broadcast + disconnect raised: "
            f"{[type(e).__name__ + ': ' + str(e) for e in errors]}"
        )
        # All dead clients should be gone; live ones still present
        for c in dead_clients:
            assert c not in ws_clients, "#6: dead client leaked back into ws_clients"
        for c in live_clients:
            assert c in ws_clients, "#6: live client was accidentally removed"


# ---------------------------------------------------------------------------
# #28 — JobStore.update_job not atomic — all mutation paths must use atomic_update
# ---------------------------------------------------------------------------


class TestJobStoreAtomicUpdateEnforced:
    """Enforce that ``update_job`` delegates to ``atomic_update`` or that
    every caller in the routes package has migrated to ``atomic_update``.

    Production hazard: background workers (train, optimiser) call
    ``update_job`` from threads while the main thread reads via
    ``get_job``.  Non-atomic ``dict.update`` exposes partial state to
    readers and can lose writes when two threads update the same job.
    """

    def test_update_job_delegates_to_atomic_update(self) -> None:
        """After fix, ``update_job`` must swap a new dict instead of mutating.

        Observationally: a reader holding the old reference must NOT see
        the new fields — because the stored object is a new dict.
        """
        from haute.routes._job_store import JobStore

        store = JobStore()
        job_id = store.create_job({"status": "running", "progress": 0.0})
        old_ref = store.get_job(job_id)
        store.update_job(job_id, status="completed", progress=1.0)
        new_ref = store.get_job(job_id)
        assert old_ref is not new_ref, (
            "update_job must be atomic (build a new dict and swap) — "
            "currently mutates the existing dict in-place."
        )
        # Old reference stays at the pre-update state
        assert old_ref["status"] == "running"
        assert old_ref["progress"] == 0.0
        # New reference has the update
        assert new_ref["status"] == "completed"
        assert new_ref["progress"] == 1.0

    def test_update_job_never_exposes_partial_multi_key_state(self) -> None:
        """Reader thread must never see inconsistent coupled fields.

        Writer updates ``step`` and ``progress`` together; reader reads
        both and checks that ``progress == step / 100`` within tolerance.
        With a proper atomic swap the invariant holds on every read.
        """
        from haute.routes._job_store import JobStore

        store = JobStore()
        job_id = store.create_job({"status": "running", "step": 0, "progress": 0.0})

        inconsistencies: list[tuple[int, float]] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                job = store.get_job(job_id)
                if job is None:
                    continue
                step = job.get("step", 0)
                progress = job.get("progress", 0.0)
                expected = step / 100.0
                if abs(progress - expected) > 1e-9:
                    inconsistencies.append((step, progress))

        def writer() -> None:
            for i in range(1, 201):
                store.update_job(job_id, step=i, progress=i / 100.0)

        reader_t = threading.Thread(target=reader, daemon=True)
        writer_t = threading.Thread(target=writer)
        reader_t.start()
        writer_t.start()
        writer_t.join(timeout=10)
        stop.set()
        reader_t.join(timeout=5)

        assert not inconsistencies, (
            f"update_job exposed {len(inconsistencies)} partial states "
            f"(first: {inconsistencies[:3]}); callers must use atomic_update."
        )

    def test_update_job_is_safe_two_writers_same_job(self) -> None:
        """Two writers updating the same job with disjoint keys must both
        survive (no lost update).  The current dict.update mutation can
        lose writes because Python's GIL releases between bytecode ops,
        so two threads can both read the same base dict and each write
        back only their own merge.

        After the fix (atomic swap), both updates survive because each
        swap is linearised via the GIL-atomic ``dict.__setitem__``.
        """
        from haute.routes._job_store import JobStore

        store = JobStore()
        job_id = store.create_job({"status": "running", "counter": 0})
        barrier = threading.Barrier(2)

        n_ops = 500

        def writer_a() -> None:
            barrier.wait()
            for i in range(n_ops):
                store.update_job(job_id, key_a=i)

        def writer_b() -> None:
            barrier.wait()
            for i in range(n_ops):
                store.update_job(job_id, key_b=i)

        ta = threading.Thread(target=writer_a)
        tb = threading.Thread(target=writer_b)
        ta.start()
        tb.start()
        ta.join(timeout=10)
        tb.join(timeout=10)

        final = store.get_job(job_id)
        assert final is not None
        # Both keys must be present after both writers finished
        assert "key_a" in final, "#28: writer A's updates were lost"
        assert "key_b" in final, "#28: writer B's updates were lost"
        # Both must reflect the last write
        assert final["key_a"] == n_ops - 1
        assert final["key_b"] == n_ops - 1

    def test_routes_migrate_to_atomic_update(self) -> None:
        """Structural: every ``update_job`` call in the routes package
        must be migrated to ``atomic_update``, OR ``update_job`` itself
        must delegate to ``atomic_update``.

        Either fix removes the non-atomic mutation path.  We check for
        both: if ``update_job`` no longer mutates in place (verified
        behaviourally above by the ``old_ref is not new_ref`` test), any
        remaining callers are safe.  If ``update_job`` is still a
        shallow wrapper over ``dict.update``, then no route should be
        calling it.
        """
        import inspect

        from haute.routes._job_store import JobStore

        # Inspect the body of JobStore.update_job — if it calls
        # ``atomic_update``, the behaviour is equivalent for callers.
        body = inspect.getsource(JobStore.update_job)
        delegates_to_atomic = "atomic_update" in body
        if delegates_to_atomic:
            return  # callers can stay on update_job

        # Otherwise: no route module is allowed to call .update_job(...)
        import haute.routes as routes_pkg

        routes_dir = Path(routes_pkg.__file__).resolve().parent
        offenders: list[str] = []
        for f in routes_dir.rglob("*.py"):
            if f.name == "_job_store.py":
                continue
            txt = f.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(txt.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ".update_job(" in line:
                    offenders.append(f"{f.relative_to(routes_dir.parent)}:{i}")

        assert not offenders, (
            "#28: update_job is still an in-place mutation, and the "
            f"following routes still call it: {offenders}"
        )


# ---------------------------------------------------------------------------
# #30 — Sync I/O blocks async event loop
# ---------------------------------------------------------------------------


class TestAsyncRouteDoesNotBlockEventLoop:
    """Async routes that perform disk I/O must offload blocking work to a
    thread pool so a second lightweight request returns promptly.

    Production hazard: ``GET /api/schema`` on a large parquet calls
    ``lf.collect_schema()`` and ``lf.head(5).collect()`` directly inside
    an ``async def`` handler.  While one request stalls the single event
    loop, no other async request can make progress.
    """

    def test_slow_schema_read_does_not_block_concurrent_request(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fire two concurrent requests on a single asyncio event loop
        (via ``httpx.AsyncClient`` + ``ASGITransport``).  One hits a
        patched slow ``polars.scan_parquet`` that sleeps ~0.8 s; the
        other reads a small schema.  After the
        fix, the fast request's round-trip latency (measured from the
        VERY start of the test, not from when the loop unblocks) is
        small because the blocking work was offloaded to a thread pool.
        Before the fix, the fast request is held until the slow handler
        returns, because the sync I/O blocks the single event loop.
        """
        import httpx

        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pq = data_dir / "big.parquet"
        pl.DataFrame({"x": list(range(10))}).write_parquet(pq)
        small = data_dir / "small.parquet"
        pl.DataFrame({"y": [1]}).write_parquet(small)

        original = pl.scan_parquet
        slow_target = pq.resolve()

        def slow_scan(path, *args, **kwargs):
            if Path(path).resolve() == slow_target:
                # Blocking sync sleep — if the handler doesn't offload,
                # this stalls the event loop and delays every other task.
                time.sleep(0.8)
            return original(path, *args, **kwargs)

        async def _run() -> dict[str, tuple[float, int]]:
            from haute.server import app

            results: dict[str, tuple[float, int]] = {}
            start = time.monotonic()

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

                async def slow_req() -> None:
                    r = await ac.get(
                        "/api/schema",
                        params={"path": "data/big.parquet"},
                    )
                    results["slow"] = (time.monotonic() - start, r.status_code)

                async def fast_req() -> None:
                    # Head-start: wait briefly so the slow request is
                    # already inside the blocking sleep by the time we
                    # dispatch the fast one.
                    await asyncio.sleep(0.1)
                    r = await ac.get(
                        "/api/schema",
                        params={"path": "data/small.parquet"},
                    )
                    results["fast"] = (time.monotonic() - start, r.status_code)

                await asyncio.gather(slow_req(), fast_req())
            return results

        with patch("polars.scan_parquet", side_effect=slow_scan):
            results = asyncio.run(_run())

        slow_dur, slow_status = results["slow"]
        fast_dur, fast_status = results["fast"]

        assert slow_status == 200, f"slow request failed: status={slow_status}"
        assert fast_status == 200, f"fast request failed: status={fast_status}"
        # The slow request must still take ~0.8 s total.
        assert slow_dur >= 0.6, (
            f"test invariant: slow request should take >= 0.6 s "
            f"(got {slow_dur:.3f}s) — patch may not be active"
        )
        # After the fix, the fast request finishes well before the slow
        # one (allowing 0.3 s of scheduling slack).  Before the fix,
        # fast_dur ~= slow_dur because the loop is blocked.
        assert fast_dur < slow_dur - 0.3, (
            f"#30: sync I/O blocked the event loop — "
            f"slow_total={slow_dur:.3f}s, fast_total={fast_dur:.3f}s "
            f"(fast should be much less than slow)"
        )

    def test_schema_route_offloads_blocking_reads(self) -> None:
        """Structural: haute.routes.files.get_schema must offload work
        via run_in_threadpool (or asyncio.to_thread).

        This catches regressions where someone reintroduces a direct
        ``lf.collect_schema()`` in the async handler.
        """
        import inspect

        from haute.routes import files

        src = inspect.getsource(files.get_schema)
        assert ("run_in_threadpool" in src) or ("to_thread" in src), (
            "#30: get_schema must call run_in_threadpool / asyncio.to_thread "
            "so the event loop is not blocked on disk I/O."
        )
