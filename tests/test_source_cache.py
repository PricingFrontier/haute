"""Provider-neutral input snapshot cache contracts."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._execution_context import ExecutionProfile
from haute._source_cache import (
    SourceCacheBuildContext,
    SourceCacheCorruptError,
    SourceCacheIdentity,
    SourceCacheQuotaExceededError,
    SourceCacheStore,
)


@dataclass
class _LazyBuilder:
    frame: pl.LazyFrame
    calls: int = 0

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        context.checkpoint()
        self.calls += 1
        return self.frame


def _identity(**descriptor: object) -> SourceCacheIdentity:
    return SourceCacheIdentity(provider="file", descriptor=descriptor)


def _context() -> SourceCacheBuildContext:
    return SourceCacheBuildContext(
        profile=ExecutionProfile.LAZY_SINK,
        build_class="bounded",
    )


def test_identity_is_versioned_canonical_and_order_independent() -> None:
    left = SourceCacheIdentity(
        provider="database",
        descriptor={
            "query": "SELECT * FROM policies",
            "connection": "DATABASE_URL",
            "arguments": {"batch_size": 1000, "schema": {"id": "int64"}},
        },
    )
    right = SourceCacheIdentity(
        provider="database",
        descriptor={
            "arguments": {"schema": {"id": "int64"}, "batch_size": 1000},
            "connection": "DATABASE_URL",
            "query": "SELECT * FROM policies",
        },
    )

    assert left.digest == right.digest
    payload = json.loads(left.canonical_bytes)
    assert payload["schema_version"] == 1
    assert payload["provider"] == "database"


@pytest.mark.parametrize(
    "descriptor",
    [
        {"token": "secret"},
        {"nested": {"password": "secret"}},
        {"uri": "postgresql://alice:secret@db.example/pricing"},
        {"uri": "postgresql://db.example/pricing?access_token=secret"},
    ],
)
def test_identity_refuses_secret_bearing_material(descriptor: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="secret|credential"):
        SourceCacheIdentity(provider="database", descriptor=descriptor)


def test_build_publishes_immutable_generation_and_lease_reads_it(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    expected = pl.DataFrame({"id": [1, 2], "value": ["a", "b"]})

    generation = store.build(
        identity,
        _LazyBuilder(expected.lazy()),
        context=_context(),
        source_signature="sha256:source-v1",
    )

    assert generation.data_path.exists()
    assert generation.metadata_path.exists()
    assert generation.data_path.parent.name == generation.generation_id
    assert generation.data_path.parent.parent.name == "generations"
    metadata = json.loads(generation.metadata_path.read_text(encoding="utf-8"))
    assert metadata["identity_digest"] == identity.digest
    assert metadata["identity"] == identity.payload
    assert metadata["source_signature"] == "sha256:source-v1"
    assert metadata["data_sha256"]
    assert metadata["size_bytes"] == generation.data_path.stat().st_size

    with store.lease(identity) as leased:
        assert leased.generation_id == generation.generation_id
        assert_frame_equal(leased.lazy_frame.collect(), expected)


def test_generation_validation_is_independent_of_canonical_metadata_key_order(
    tmp_path: Path,
) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/nonalphabetical.parquet", format="parquet")
    expected = pl.DataFrame({"z_value": [1], "a_value": [2]})

    generation = store.build(
        identity,
        _LazyBuilder(expected.lazy()),
        context=_context(),
    )

    metadata = json.loads(generation.metadata_path.read_text(encoding="utf-8"))
    assert list(metadata["columns"]) == ["a_value", "z_value"]
    with store.lease(identity) as leased:
        assert leased.lazy_frame.collect().columns == ["z_value", "a_value"]


def test_admitted_context_covers_snapshot_read_write_and_publication_checkpoints(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, str]] = []

    class FakeExecutionContext:
        def checkpoint(self, *, label: str) -> None:
            events.append(("checkpoint", label))

        @contextlib.contextmanager
        def stage(self, name: str) -> Iterator[None]:
            events.append(("stage_enter", name))
            try:
                yield
            finally:
                events.append(("stage_exit", name))

    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.xlsx", format="excel")
    context = SourceCacheBuildContext(
        profile=ExecutionProfile.PREVIEW_EAGER,
        build_class="admitted_eager",
        execution_context=FakeExecutionContext(),  # type: ignore[arg-type]
    )

    store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=context,
    )

    assert ("stage_enter", "input_snapshot_read") in events
    assert ("stage_exit", "input_snapshot_read") in events
    assert ("stage_enter", "input_snapshot_write") in events
    assert ("stage_exit", "input_snapshot_write") in events
    assert events.count(("checkpoint", "input_snapshot_build")) >= 5
    assert events.index(("stage_exit", "input_snapshot_write")) < len(events) - 1


def test_status_reports_fresh_stale_and_unknown(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.csv", format="csv")
    store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
        source_signature="sha256:v1",
    )

    assert store.status(identity, source_signature="sha256:v1").freshness == "fresh"
    assert store.status(identity, source_signature="sha256:v2").freshness == "stale"
    assert store.status(identity).freshness == "unknown"


def test_corrupt_current_generation_fails_without_fallback(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    generation = store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )
    corrupt_path = tmp_path / generation.data_path.relative_to(tmp_path)
    corrupt_path.write_bytes(b"not parquet")

    with pytest.raises(SourceCacheCorruptError):
        store.open_generation(identity)
    assert store.status(identity).state == "corrupt"


def test_open_generation_does_not_rehash_published_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )

    monkeypatch.setattr(
        "haute._source_cache._sha256_file",
        lambda _path: pytest.fail("ordinary generation open rehashed the full artifact"),
    )

    with store.lease(identity) as generation:
        assert generation.lazy_frame.collect()["id"].to_list() == [1]


def test_generation_digest_is_verified_once_per_stable_process_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _source_cache

    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    generation = store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )
    store._verified_generations.clear()
    real_sha256_file = _source_cache._sha256_file
    hashed: list[Path] = []

    def record_hash(path: Path) -> str:
        hashed.append(path)
        return real_sha256_file(path)

    monkeypatch.setattr(_source_cache, "_sha256_file", record_hash)

    with store.lease(identity):
        pass
    with store.lease(identity):
        pass

    assert hashed == [generation.data_path]


def test_transient_generation_access_error_is_not_reported_as_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    generation = store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )
    original_read_text = Path.read_text

    def fail_metadata_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == generation.metadata_path:
            raise PermissionError("temporarily locked")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_metadata_read)

    with pytest.raises(PermissionError, match="temporarily locked"):
        store.open_generation(identity)


@pytest.mark.parametrize("generation_id", ["../../outside", "not-a-generation-id"])
def test_current_pointer_rejects_malformed_generation_ids(
    tmp_path: Path,
    generation_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    pointer = tmp_path / store.identity_path(identity).relative_to(tmp_path) / "current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {
                "identity_digest": identity.digest,
                "generation_id": generation_id,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        store,
        "_metadata_from_path",
        lambda *_args, **_kwargs: pytest.fail(
            "malformed generation id reached filesystem path construction"
        ),
    )

    with pytest.raises(SourceCacheCorruptError):
        store.open_generation(identity)


def test_failed_refresh_preserves_previous_current_generation(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    first = store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )

    class _FailingBuilder:
        def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
            context.checkpoint()
            raise RuntimeError("connector failed")

    with pytest.raises(RuntimeError, match="connector failed"):
        store.build(identity, _FailingBuilder(), context=_context(), refresh=True)

    current = store.open_generation(identity)
    assert current.generation_id == first.generation_id
    assert current.lazy_frame.collect()["id"].to_list() == [1]
    assert not any(store.identity_path(identity).glob(".staging-*"))


def test_failed_staged_generation_validation_preserves_previous_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    first = store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )
    original_metadata_from_path = store._metadata_from_path

    def reject_new_generation(
        candidate_identity: SourceCacheIdentity,
        generation_id: str,
    ):
        if generation_id != first.generation_id:
            raise SourceCacheCorruptError("staged generation did not validate")
        return original_metadata_from_path(candidate_identity, generation_id)

    monkeypatch.setattr(store, "_metadata_from_path", reject_new_generation)
    with pytest.raises(SourceCacheCorruptError, match="did not validate"):
        store.build(
            identity,
            _LazyBuilder(pl.DataFrame({"id": [2]}).lazy()),
            context=_context(),
            refresh=True,
        )
    monkeypatch.setattr(store, "_metadata_from_path", original_metadata_from_path)

    current = store.open_generation(identity)
    assert current.generation_id == first.generation_id
    assert current.lazy_frame.collect()["id"].to_list() == [1]


def test_same_identity_build_is_single_flight(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    entered = threading.Event()
    release = threading.Event()

    class _BlockingBuilder:
        calls = 0

        def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5)
            return pl.DataFrame({"id": [1]}).lazy()

    builder = _BlockingBuilder()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(store.build, identity, builder, context=_context())
        assert entered.wait(timeout=5)
        second = pool.submit(store.build, identity, builder, context=_context())
        release.set()
        first_generation = first.result(timeout=5)
        second_generation = second.result(timeout=5)

    assert builder.calls == 1
    assert second_generation.generation_id == first_generation.generation_id


def test_different_identities_build_concurrently(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    barrier = threading.Barrier(2)

    class _BarrierBuilder:
        def __init__(self, value: int) -> None:
            self.value = value

        def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
            barrier.wait(timeout=5)
            return pl.DataFrame({"id": [self.value]}).lazy()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(
            store.build,
            _identity(path="a.parquet"),
            _BarrierBuilder(1),
            context=_context(),
        )
        b = pool.submit(
            store.build,
            _identity(path="b.parquet"),
            _BarrierBuilder(2),
            context=_context(),
        )
        assert a.result(timeout=5).generation_id != b.result(timeout=5).generation_id


def test_clear_retires_leased_generation_until_release(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    generation = store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1, 2]}).lazy()),
        context=_context(),
    )

    with store.lease(identity) as leased:
        store.clear(identity)
        assert store.status(identity).state == "missing"
        assert generation.data_path.exists()
        assert leased.lazy_frame.collect()["id"].to_list() == [1, 2]

    assert not generation.data_path.parent.exists()


def test_leases_are_shared_across_store_instances_for_the_same_root(tmp_path: Path) -> None:
    reader_store = SourceCacheStore(tmp_path)
    publisher_store = SourceCacheStore(tmp_path)
    identity = _identity(path="data/input.parquet", format="parquet")
    generation = publisher_store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1, 2]}).lazy()),
        context=_context(),
    )

    with reader_store.lease(identity) as leased:
        publisher_store.clear(identity)
        assert publisher_store.status(identity).state == "missing"
        assert generation.data_path.exists()
        assert leased.lazy_frame.collect()["id"].to_list() == [1, 2]

    assert not generation.data_path.parent.exists()


def test_build_rejects_publication_that_exceeds_store_quota(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path, max_bytes=1)
    identity = _identity(path="data/input.parquet", format="parquet")

    with pytest.raises(SourceCacheQuotaExceededError):
        store.build(
            identity,
            _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
            context=_context(),
        )

    assert store.status(identity).state == "missing"
    assert not any(store.identity_path(identity).glob(".staging-*"))
    assert not any((store.identity_path(identity) / "generations").glob("*"))


def test_generation_quota_rejects_another_current_snapshot(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path, max_bytes=1_000_000, max_generations=2)
    oldest = _identity(path="oldest.parquet")
    middle = _identity(path="middle.parquet")
    newest = _identity(path="newest.parquet")

    store.build(
        oldest,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )
    store.build(
        middle,
        _LazyBuilder(pl.DataFrame({"id": [2]}).lazy()),
        context=_context(),
    )
    with pytest.raises(
        SourceCacheQuotaExceededError,
        match="existing snapshots are kept.*Clear an unused Data Input snapshot",
    ):
        store.build(
            newest,
            _LazyBuilder(pl.DataFrame({"id": [3]}).lazy()),
            context=_context(),
        )

    assert store.status(oldest).state == "ready"
    assert store.status(middle).state == "ready"
    assert store.status(newest).state == "missing"


def test_generation_quota_reclaims_an_unleased_superseded_generation(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path, max_bytes=1_000_000, max_generations=1)
    identity = _identity(path="refreshable.parquet")
    first = store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )

    second = store.build(
        identity,
        _LazyBuilder(pl.DataFrame({"id": [2]}).lazy()),
        context=_context(),
        refresh=True,
    )

    assert second.generation_id != first.generation_id
    assert not first.data_path.parent.exists()
    assert store.open_generation(identity).lazy_frame.collect()["id"].to_list() == [2]


def test_generation_quota_never_evicts_a_leased_snapshot(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path, max_bytes=1_000_000, max_generations=1)
    pinned = _identity(path="pinned.parquet")
    replacement = _identity(path="replacement.parquet")
    store.build(
        pinned,
        _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
        context=_context(),
    )

    with store.lease(pinned):
        with pytest.raises(SourceCacheQuotaExceededError):
            store.build(
                replacement,
                _LazyBuilder(pl.DataFrame({"id": [2]}).lazy()),
                context=_context(),
            )
        assert store.status(pinned).state == "ready"

    with pytest.raises(SourceCacheQuotaExceededError):
        store.build(
            replacement,
            _LazyBuilder(pl.DataFrame({"id": [2]}).lazy()),
            context=_context(),
        )
    assert store.status(pinned).state == "ready"
    assert store.status(replacement).state == "missing"


def test_store_startup_preserves_unproven_cross_process_staging_and_generations(
    tmp_path: Path,
) -> None:
    identity_root = tmp_path / ".haute_cache" / "inputs" / "abandoned"
    staging = identity_root / ".staging-dead"
    generation = identity_root / "generations" / "unpublished"
    staging.mkdir(parents=True)
    generation.mkdir(parents=True)
    (staging / "partial.parquet").write_bytes(b"partial")
    (generation / "data.parquet").write_bytes(b"unpublished")

    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from haute._source_cache import SourceCacheStore; "
                f"SourceCacheStore({str(tmp_path)!r})"
            ),
        ],
        check=True,
    )

    assert staging.exists()
    assert generation.exists()


def test_store_startup_reclaims_only_stale_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = tmp_path / ".haute_cache" / "inputs" / "identity"
    stale = inputs / ".staging-stale"
    recent = inputs / ".staging-recent"
    generation = inputs / "generations" / str(uuid.uuid4())
    stale.mkdir(parents=True)
    recent.mkdir(parents=True)
    generation.mkdir(parents=True)
    stale_file = stale / "data.parquet"
    recent_file = recent / "data.parquet"
    stale_file.write_bytes(b"stale")
    recent_file.write_bytes(b"recent")
    (generation / "data.parquet").write_bytes(b"published")
    old = time.time() - 120
    os.utime(stale_file, (old, old))
    os.utime(stale, (old, old))
    monkeypatch.setenv("HAUTE_INPUT_CACHE_STAGING_MAX_AGE_SECONDS", "60")

    SourceCacheStore(tmp_path)

    assert not stale.exists()
    assert recent.exists()
    assert generation.exists()


def test_store_startup_preserves_staging_when_reclamation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / ".haute_cache" / "inputs" / "identity" / ".staging-temporarily-locked"
    staging.mkdir(parents=True)
    partial = staging / "data.parquet"
    partial.write_bytes(b"partial")
    old = time.time() - 120
    os.utime(partial, (old, old))
    os.utime(staging, (old, old))
    monkeypatch.setenv("HAUTE_INPUT_CACHE_STAGING_MAX_AGE_SECONDS", "60")

    def fail_reclamation(_path: Path) -> None:
        raise PermissionError("temporarily locked")

    monkeypatch.setattr("haute._source_cache.shutil.rmtree", fail_reclamation)

    SourceCacheStore(tmp_path)

    assert staging.exists()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_store_rejects_non_positive_or_non_finite_staging_age(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("HAUTE_INPUT_CACHE_STAGING_MAX_AGE_SECONDS", value)

    with pytest.raises(RuntimeError, match="finite number greater than 0"):
        SourceCacheStore(tmp_path)


def test_retained_staging_bytes_count_against_publication_quota(tmp_path: Path) -> None:
    staging = tmp_path / ".haute_cache" / "inputs" / "other" / ".staging-live"
    staging.mkdir(parents=True)
    (staging / "data.parquet").write_bytes(b"x" * 1_000)
    store = SourceCacheStore(tmp_path, max_bytes=1_000)
    identity = _identity(path="new.parquet")

    with pytest.raises(SourceCacheQuotaExceededError):
        store.build(
            identity,
            _LazyBuilder(pl.DataFrame({"id": [1]}).lazy()),
            context=_context(),
        )

    assert staging.exists()


# ---------------------------------------------------------------------------
# Polars dtype spellings are a persisted format
# ---------------------------------------------------------------------------

# ``str(dtype)`` is what a source-cache generation stores in its metadata
# (compared verbatim on read; a mismatch reports the generation as corrupt)
# and what ``haute._polars_dtypes.dtype_to_spec`` falls back to for scalar
# dtypes (read back through ``getattr(pl, key)``). Polars documents neither
# repr as stable. Generated on polars 1.39.3. If an entry moves, every
# existing source-cache generation reports as corrupt on the new polars and a
# node config carrying the old spelling may fail to parse: that is a
# migration decision to take, not a table to regenerate. The zoned Datetime
# is the entry most likely to move.
_POLARS_DTYPE_SPELLINGS: tuple[tuple[pl.DataType | type[pl.DataType], str], ...] = (
    (pl.Int8, "Int8"),
    (pl.Int16, "Int16"),
    (pl.Int32, "Int32"),
    (pl.Int64, "Int64"),
    (pl.UInt8, "UInt8"),
    (pl.UInt16, "UInt16"),
    (pl.UInt32, "UInt32"),
    (pl.UInt64, "UInt64"),
    (pl.Float32, "Float32"),
    (pl.Float64, "Float64"),
    (pl.Boolean, "Boolean"),
    (pl.String, "String"),
    (pl.Date, "Date"),
    (pl.Time, "Time"),
    (pl.Datetime("us"), "Datetime(time_unit='us', time_zone=None)"),
    (pl.Datetime("ms", "Europe/London"), "Datetime(time_unit='ms', time_zone='Europe/London')"),
    (pl.Duration("us"), "Duration(time_unit='us')"),
    (pl.Decimal(10, 2), "Decimal(precision=10, scale=2)"),
    (pl.List(pl.Int64), "List(Int64)"),
    (pl.Struct({"a": pl.Int64, "b": pl.String}), "Struct({'a': Int64, 'b': String})"),
    (pl.Categorical, "Categorical"),
    (pl.Enum(["x", "y"]), "Enum(categories=['x', 'y'])"),
)


def test_polars_dtype_spellings_match_the_persisted_golden_table() -> None:
    moved = [
        f"{expected!r} -> {str(dtype)!r}"
        for dtype, expected in _POLARS_DTYPE_SPELLINGS
        if str(dtype) != expected
    ]
    assert not moved, (
        f"polars {pl.__version__} changed str() of a dtype: {moved}. Every existing "
        "source-cache generation will now report as corrupt and a node config carrying "
        "the old spelling may fail to parse. Decide the migration deliberately; do not "
        "regenerate this table."
    )
