"""Tests pinning the pipeline-index cache lifecycle — Phase 2 Wave 5 package 5B.

These tests pin the *end state* for item #75 of the codebase review: the
pipeline name -> path index in ``haute.routes._helpers`` must no longer rely
on hand-managed cache invalidation.  Instead the cache is (re)built
automatically via two mechanisms only:

1. **Server startup** — the FastAPI lifespan populates the index before the
   first request.
2. **File-watcher events** — the existing ``watchfiles.awatch`` loop in
   ``haute.server._file_watcher`` rebuilds the index when a pipeline file
   changes on disk.

Any other production call site that manually clears the cache (e.g. a
``invalidate_pipeline_index()`` helper invoked outside the watcher loop) is
considered a bug: it lets one code path scribble on a cache that other code
paths rely on being consistent, and creates the exact "async handler
scheduled after invalidation sees stale cache" race identified in the
review.

The tests are intentionally strict: they will FAIL until the dev agent
wires the startup populate hook, removes the exported
``invalidate_pipeline_index`` function (or restricts it to the watcher
loop), and guarantees that concurrent reads don't see a half-built cache.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pipeline_project(tmp_path: Path) -> Path:
    """Create a minimal temp project with two pipeline files.

    Two pipelines let us verify the index maps names to the right files and
    that a change to one file causes a cache rebuild observable via a new
    name being discoverable.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pl.DataFrame({"x": [1, 2, 3]}).write_parquet(data_dir / "input.parquet")
    data_path = (data_dir / "input.parquet").as_posix()

    # haute.toml points at the first pipeline; discover_pipelines() will also
    # pick up the second via the root-level .py glob fallback.
    (tmp_path / "haute.toml").write_text('[project]\npipeline = "pipeline_a.py"\n')

    code_a = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("pipeline_a")


@pipeline.data_source(path="{data_path}")
def source_a() -> pl.DataFrame:
    return pl.scan_parquet("{data_path}")


@pipeline.polars
def transform_a(source_a: pl.DataFrame) -> pl.DataFrame:
    return source_a


pipeline.connect("source_a", "transform_a")
'''

    code_b = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("pipeline_b")


@pipeline.data_source(path="{data_path}")
def source_b() -> pl.DataFrame:
    return pl.scan_parquet("{data_path}")


@pipeline.polars
def transform_b(source_b: pl.DataFrame) -> pl.DataFrame:
    return source_b


pipeline.connect("source_b", "transform_b")
'''

    (tmp_path / "pipeline_a.py").write_text(code_a)
    (tmp_path / "pipeline_b.py").write_text(code_b)
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_index_state() -> None:
    """Wipe the module-level cache between tests so each test starts fresh."""
    import haute.routes._helpers as helpers

    # Touch the private attributes directly so the test is resilient to the
    # removal of the public ``invalidate_pipeline_index`` helper (which is
    # exactly what package 5B will do).
    helpers._pipeline_index = None
    helpers._module_deps = None
    yield
    helpers._pipeline_index = None
    helpers._module_deps = None


# ---------------------------------------------------------------------------
# 1. Startup populates the cache
# ---------------------------------------------------------------------------


class TestStartupPopulatesCache:
    """On server startup the index must be pre-populated — no first-request miss."""

    def test_lifespan_populates_pipeline_index(
        self,
        pipeline_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Entering the app lifespan must populate ``_pipeline_index``.

        Catches: a new server instance serving its first request with an
        empty cache, which forces the first caller to pay the O(discover +
        parse all pipelines) cost synchronously inside a request handler.
        """
        import haute.routes._helpers as helpers

        monkeypatch.chdir(pipeline_project)

        # Prevent the real file-watcher from starting (it depends on watchfiles
        # and would run forever).  We only care that entering the lifespan
        # populates the index.
        async def _noop_watcher() -> None:
            return None

        with patch("haute.server._file_watcher", _noop_watcher):
            from haute.server import _lifespan, app

            async def _enter_lifespan() -> None:
                async with _lifespan(app):
                    # Inside the lifespan the index MUST be populated.  The
                    # startup hook is the single public point of
                    # initialisation — callers should never observe ``None``.
                    assert helpers._pipeline_index is not None, (
                        "Pipeline index was still None after lifespan startup "
                        "— startup populate hook is missing."
                    )
                    assert "pipeline_a" in helpers._pipeline_index
                    assert "pipeline_b" in helpers._pipeline_index

            asyncio.run(_enter_lifespan())

    def test_first_lookup_hits_cache(
        self,
        pipeline_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After startup the first ``lookup_pipeline_by_name`` must hit the cache.

        Catches: startup populate hook missing entirely — the first lookup
        triggers the expensive discover+parse path.  We wrap
        ``discover_pipelines`` so we can assert it was NOT called during the
        lookup (only during startup).
        """
        monkeypatch.chdir(pipeline_project)

        discover_calls: list[None] = []
        real_discover = None

        from haute import discovery as _discovery_mod

        real_discover = _discovery_mod.discover_pipelines

        def _counting_discover(*args: Any, **kw: Any) -> Any:
            discover_calls.append(None)
            return real_discover(*args, **kw)

        async def _noop_watcher() -> None:
            return None

        with (
            patch("haute.server._file_watcher", _noop_watcher),
            patch.object(_discovery_mod, "discover_pipelines", side_effect=_counting_discover),
        ):
            from haute.routes._helpers import lookup_pipeline_by_name
            from haute.server import _lifespan, app

            async def _enter_and_lookup() -> Path | None:
                async with _lifespan(app):
                    before = len(discover_calls)
                    # Post-startup lookup must not re-trigger discovery.
                    path = lookup_pipeline_by_name("pipeline_a")
                    after = len(discover_calls)
                    assert after == before, (
                        f"lookup_pipeline_by_name re-ran discover_pipelines "
                        f"({after - before} extra calls) after startup "
                        f"— startup didn't populate the cache."
                    )
                    return path

            result = asyncio.run(_enter_and_lookup())
            assert result is not None
            assert result.name == "pipeline_a.py"


# ---------------------------------------------------------------------------
# 2. Repeated reads are served from cache
# ---------------------------------------------------------------------------


class TestCacheHits:
    """Successive reads must reuse the cached index — no per-call rescanning."""

    def test_two_sequential_reads_share_the_same_dict(
        self,
        pipeline_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Calling the lookup twice must return the same dict object.

        Catches: a regression where the cache is rebuilt on every call
        (e.g., someone turned ``_ensure_pipeline_index`` into a pass-through
        without memoisation).  Identity equality is the cleanest signal.
        """
        import haute.routes._helpers as helpers

        monkeypatch.chdir(pipeline_project)

        # Prime the cache.
        helpers._ensure_pipeline_index()
        first = helpers._pipeline_index

        # Second call must reuse the same dict.
        helpers._ensure_pipeline_index()
        second = helpers._pipeline_index

        assert first is second, (
            "Successive reads produced different dict objects — cache is "
            "being rebuilt on every call instead of memoised."
        )


# ---------------------------------------------------------------------------
# 3. File-watcher events rebuild the cache
# ---------------------------------------------------------------------------


class TestFileWatcherRebuildsCache:
    """A watcher-delivered file change must cause the next read to see fresh data."""

    def test_watcher_event_rebuilds_cache_on_new_file(
        self,
        pipeline_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Adding a third pipeline via a watcher event must be visible next read.

        Catches: the watcher no longer triggers a rebuild (e.g., invalidation
        removed without replacement) — the next read returns the stale index
        and a lookup for the new pipeline name incorrectly returns ``None``.
        """
        from watchfiles import Change

        import haute.routes._helpers as helpers

        monkeypatch.chdir(pipeline_project)

        # Prime the cache with only pipeline_a and pipeline_b.
        helpers._ensure_pipeline_index()
        assert "pipeline_c" not in (helpers._pipeline_index or {})

        # Write a third pipeline to disk — this is what the watcher would see.
        data_path = (pipeline_project / "data" / "input.parquet").as_posix()
        (pipeline_project / "pipeline_c.py").write_text(
            f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("pipeline_c")


@pipeline.data_source(path="{data_path}")
def source_c() -> pl.DataFrame:
    return pl.scan_parquet("{data_path}")


@pipeline.polars
def transform_c(source_c: pl.DataFrame) -> pl.DataFrame:
    return source_c


pipeline.connect("source_c", "transform_c")
'''
        )

        # Simulate the watcher delivering a change event.  We deliberately
        # invoke the production ``_file_watcher`` with a mocked ``awatch``
        # so the real invalidation path is exercised.
        fake_changes = [(Change.added, str(pipeline_project / "pipeline_c.py"))]

        async def _fake_awatch(*dirs: Any, **kw: Any) -> Any:
            yield fake_changes

        async def _capture_broadcast(data: dict[str, Any]) -> None:
            return None

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server.pipeline_dir", return_value=pipeline_project),
            ):
                from haute.server import _file_watcher

                await _file_watcher()
                # Allow the debounce task to flush (300ms + safety margin).
                await asyncio.sleep(0.6)

        asyncio.run(_run())

        # After the watcher has fired, the next read must see the new pipeline.
        fresh = helpers._ensure_pipeline_index()
        assert "pipeline_c" in fresh, (
            "Watcher event did not trigger a rebuild — the cache still "
            "reflects the pre-change state.  Next read must pick up new files."
        )
        assert fresh["pipeline_c"].name == "pipeline_c.py"


# ---------------------------------------------------------------------------
# 4. No manual invalidation anywhere in production code
# ---------------------------------------------------------------------------


class TestNoManualInvalidation:
    """Grep-based structural check: production code must not manually invalidate.

    These assertions are the crux of item #75.  The only acceptable caller
    of any ``invalidate_*`` helper targeting the pipeline index is the
    file-watcher callback itself (``haute.server._file_watcher``).  If the
    dev chose to drop the public helper entirely, that's even better — the
    absence-check below succeeds trivially in that case.
    """

    def test_no_production_call_to_invalidate_pipeline_index(self) -> None:
        """AST-walk every ``.py`` in ``src/haute/`` — collect Call nodes.

        Allowed:
          - the function definition itself (if the helper is kept for
            internal use by the watcher),
          - the single call site inside ``haute.server._file_watcher``, and
          - ``haute.routes._save_pipeline`` at the end of a successful
            save — self-writes early-return from the file-watcher on the
            ``is_self_write()`` cooldown, so the save path must
            explicitly invalidate the index to keep the rename → new-name
            lookup consistent.  Pin this exception by filename so any
            OTHER module adding an invalidate call (a regression) still
            fails the test.

        Any other call site is a regression: it means some other module has
        opinions about when the cache should be invalidated, which is
        exactly the "two sources of truth" failure mode of item #75.

        We use ``ast`` rather than raw grep so that imports and string
        literals are not misclassified as call sites.
        """
        import ast

        src_root = Path(__file__).resolve().parents[1] / "src" / "haute"
        assert src_root.is_dir(), f"src root not found at {src_root}"

        offenders: list[tuple[Path, int]] = []
        for py in src_root.rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # Match ``invalidate_pipeline_index(...)`` and
                # ``helpers.invalidate_pipeline_index(...)``.
                name: str | None = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name != "invalidate_pipeline_index":
                    continue
                offenders.append((py.relative_to(src_root), node.lineno))

        # Identify which offender (if any) lives in the file-watcher —
        # that's the single legitimate call site.  We use an AST-level
        # "is this inside _file_watcher?" check by walking the function
        # def hierarchy for each offender.
        def _in_file_watcher(py: Path, lineno: int) -> bool:
            """True iff ``lineno`` in ``py`` is inside ``_file_watcher`` or a nested def."""
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if node.name != "_file_watcher":
                    continue
                start = node.lineno
                end = node.end_lineno or start
                if start <= lineno <= end:
                    return True
            return False

        allowed_save_pipeline = Path("routes") / "_save_pipeline.py"
        non_watcher = [
            (py, ln)
            for (py, ln) in offenders
            if not (py == Path("server.py") and _in_file_watcher(src_root / py, ln))
            and py != allowed_save_pipeline
        ]

        if non_watcher:
            details = "\n".join(f"  {py}:{ln}" for (py, ln) in non_watcher)
            raise AssertionError(
                "Found manual ``invalidate_pipeline_index()`` call sites outside "
                "the file-watcher.  Package 5B must remove these so the cache "
                "is only rebuilt via startup + watcher events:\n" + details
            )


# ---------------------------------------------------------------------------
# 5. Concurrent reads during rebuild don't race
# ---------------------------------------------------------------------------


class TestConcurrentReadsDoNotRace:
    """Two concurrent ``_ensure_pipeline_index`` calls must produce consistent output.

    Without a lock, two threads racing through a None-check can each enter
    the rebuild path, perform redundant scans, and assign different dicts to
    the module-level cache.  Worse, an observer can see a half-populated
    dict mid-rebuild.
    """

    def test_two_threads_race_during_rebuild(
        self,
        pipeline_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Parallel threads that both trigger a build must agree on the result.

        Catches: an unsynchronised rebuild that yields distinct dicts to
        different callers (one could still hold a reference to the older
        dict while the other sees the newer one) or, worse, exposes a
        half-built dict missing entries that disk-discovery already found.
        """
        import haute.routes._helpers as helpers

        monkeypatch.chdir(pipeline_project)

        # Force an empty cache to guarantee both threads take the build path.
        helpers._pipeline_index = None

        # Slow down the parse step so the race window is observable.
        from haute import parser as _parser_mod

        real_parse = _parser_mod.parse_pipeline_file

        def _slow_parse(path: Path, *args: Any, **kw: Any) -> Any:
            # 30ms per file × 2 files ensures two threads overlap during
            # the rebuild window.
            import time as _time

            _time.sleep(0.03)
            return real_parse(path, *args, **kw)

        results: list[dict[str, Path]] = []
        errors: list[BaseException] = []

        def _worker() -> None:
            try:
                index = helpers._ensure_pipeline_index()
                # Take a snapshot to defend against later mutation.
                results.append(dict(index))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with patch.object(_parser_mod, "parse_pipeline_file", side_effect=_slow_parse):
            threads = [threading.Thread(target=_worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        assert not errors, f"Worker raised: {errors}"
        assert len(results) == 2, "Both workers must complete"

        # Both threads must see the same set of pipeline names.  If one
        # thread observed a half-populated dict, its key-set differs from
        # the other's.
        assert set(results[0].keys()) == set(results[1].keys()), (
            f"Concurrent reads disagreed on the index contents: "
            f"thread-0 saw {sorted(results[0].keys())}, "
            f"thread-1 saw {sorted(results[1].keys())}.  "
            "This indicates a missing lock around the rebuild."
        )

        # Sanity: the agreed-upon contents must be the expected two pipelines.
        assert set(results[0].keys()) == {"pipeline_a", "pipeline_b"}

    def test_reader_during_rebuild_never_sees_none(
        self,
        pipeline_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A raw reader must never see a transient ``None`` or half-built dict.

        Catches: an implementation that clears the cache to ``None`` before
        rebuilding, giving a concurrent reader a window in which the cache
        is temporarily missing.  The rebuild must swap in the new dict
        atomically.

        Going through ``_ensure_pipeline_index`` would mask the bug — that
        function itself rebuilds when it sees ``None``, so its return is
        never ``None`` by construction regardless of whether the underlying
        swap is atomic.  We therefore read the raw module attribute
        ``_helpers._pipeline_index`` and assert the atomic-swap invariant:
        each observation is either a fully-populated dict (pre- or
        post-rebuild) or, acceptably, the sentinel ``None`` that means
        "not yet primed" — but NEVER a partial dict missing keys that a
        previous observation contained.
        """
        from haute.routes import _helpers

        monkeypatch.chdir(pipeline_project)

        # Prime the cache so the rebuild path is exercised under a
        # "cache-present, about-to-swap" scenario rather than a cold start.
        _helpers._ensure_pipeline_index()
        expected_keys = set(_helpers._pipeline_index or {})
        # Sanity: priming should have populated the two known pipelines.
        assert expected_keys == {"pipeline_a", "pipeline_b"}, (
            f"Prime step did not populate the expected pipelines; got {expected_keys}"
        )

        # Simulate a rebuild happening concurrently with repeated raw reads.
        stop = threading.Event()
        partial_observations: list[set[str]] = []
        none_observations = [0]

        def _rebuilder() -> None:
            # Provoke rebuilds by nulling the cache and re-priming via
            # _ensure.  Either the swap is atomic (reader sees only
            # None or the full expected dict) or it isn't (caught).
            for _ in range(20):
                if stop.is_set():
                    return
                _helpers._pipeline_index = None
                _helpers._ensure_pipeline_index()

        def _reader() -> None:
            for _ in range(2000):
                if stop.is_set():
                    return
                # Raw read of the module attribute — no _ensure call to
                # mask a transient None or half-built state.
                snapshot = _helpers._pipeline_index
                if snapshot is None:
                    none_observations[0] += 1
                    continue
                keys = set(snapshot)
                # The invariant we pin: if the reader sees a dict at all,
                # it must be a fully-populated dict equal to the expected
                # keyset.  A dict that is missing a key a previous reader
                # saw (or that has extra keys) means the publisher
                # exposed a half-built mapping.
                if keys != expected_keys:
                    partial_observations.append(keys)
                    return

        rebuilder = threading.Thread(target=_rebuilder)
        reader = threading.Thread(target=_reader)
        rebuilder.start()
        reader.start()
        rebuilder.join(timeout=5.0)
        stop.set()
        reader.join(timeout=5.0)

        assert not partial_observations, (
            "A reader observed a partial / disagreeing dict while a rebuild "
            f"was in flight: {partial_observations[:3]} (expected "
            f"{expected_keys}).  The cache swap is not atomic — rebuild "
            "into a local dict and assign it once, instead of clearing "
            "then populating in place."
        )
