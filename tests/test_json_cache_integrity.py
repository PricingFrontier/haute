"""Wave 2 JSON-cache integrity suite (REMEDIATION_PLAN items 2.1, 2.4-2.8).

One coherent build/validity/load rework, pinned end to end:

- **2.1 (C2)** — a successful production build (the ``/api/json-cache/build``
  route) marks the working layer as session-consulted, so Save's
  ``mirror_cache_to_committed`` actually populates ``committed/`` and a fresh
  server (or deploy) can serve from it.
- **2.4** — cache validity records a data-file signature: editing the data
  file then re-clicking "Cache as Parquet" really rebuilds instead of
  no-opping on an unchanged schema fingerprint and serving stale rows.
- **2.5** — one shared predicate (``table_is_emitting``: emit AND >=1
  selected column) used by build, validity AND load, killing the permanent
  wedge where build skips a parquet that validity then demands forever.
- **2.6** — the build is atomic (temp dir + swap) and serialized (per-cache
  lock), so a failed or concurrent rebuild can never corrupt a previously
  valid cache or stamp one schema's meta onto another's parquets.
- **2.7** — records dropped on shape mismatch (non-object JSONL lines, mixed
  arrays) are counted and surfaced in the build summary, meta.json, and the
  route responses. Zero silent loss.
- **2.8** — declared ``date`` columns reject raw JSON ints/bools instead of
  silently reinterpreting them as days-since-epoch (2024 -> 1975-07-18).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._json_flatten import (
    _clear_session,
    _is_working_consulted,
    _json_cache_dir,
    _mark_working_consulted,
    mirror_cache_to_committed,
)
from haute._json_shred import (
    build_per_port_cache,
    is_per_port_cache_valid,
    load_per_port_cache,
    load_v2_api_source,
    read_per_port_cache_meta,
    shred_to_buffers,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _col(
    name: str,
    path: str,
    *,
    type_: str = "int",
    selected: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": type_,
        "status": "Confirmed",
        "selected": selected,
        "levels": None,
    }


def _table(
    path: str,
    label: str,
    cols: list[dict[str, Any]],
    *,
    emit: bool = True,
) -> dict[str, Any]:
    return {"path": path, "label": label, "emit": emit, "row_id_column": None, "columns": cols}


def _root_cfg(*cols: dict[str, Any]) -> dict[str, Any]:
    return {"tables": [_table("$[:]", "root", list(cols))]}


def _write_json(path: Path, records: list[Any]) -> None:
    path.write_text(json.dumps(records), encoding="utf-8")


@pytest.fixture()
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Chdir into a fresh tmp dir and reset the consulted-hashes session set."""
    monkeypatch.chdir(tmp_path)
    _clear_session()
    yield tmp_path
    _clear_session()


@pytest.fixture()
def client(isolated_cwd: Path) -> TestClient:
    from haute.server import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# 2.1 (C2) — production build marks working-consulted; committed/ gets
# populated by Save and serves a fresh server.
# ---------------------------------------------------------------------------


class TestCommittedMirrorOnProductionBuild:
    def test_route_build_marks_working_consulted(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """A successful /build via the real route must mark the working layer
        as consulted, otherwise Save's mirror is dead code (C2)."""
        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))

        resp = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert resp.status_code == 200, resp.text

        assert _is_working_consulted(str(data.resolve())) is True
        # And the production save-time mirror is therefore live, not dead code:
        assert mirror_cache_to_committed(str(data.resolve())) is True
        assert _json_cache_dir(str(data.resolve()), "committed").exists()

    def test_failed_route_build_does_not_mark_consulted(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """Marking happens only on SUCCESS — a failed build must not let Save
        promote a stale previous-session working/ layer."""
        data = isolated_cwd / "data.json"
        # Type mismatch: declared int, data is a string -> build fails 422.
        _write_json(data, [{"id": "not-an-int"}])
        cfg = _root_cfg(_col("id", "$[:].id"))

        resp = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert resp.status_code == 422

        assert _is_working_consulted(str(data.resolve())) is False

    def test_http_build_save_restart_serves_from_committed(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """The full C2 repro through the real routes: build -> save ->
        simulated restart -> ``committed/`` exists and ``load_v2_api_source``
        serves from it (the documented deploy / fresh-server path)."""
        import shutil

        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 1}, {"id": 2}])
        cfg = _root_cfg(_col("id", "$[:].id"))

        build = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert build.status_code == 200, build.text

        graph = {
            "nodes": [
                {
                    "id": "api",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "quotes",
                        "nodeType": "apiInput",
                        "config": {
                            "path": "data.json",
                            "contract": "opaque",
                            "tables": cfg["tables"],
                        },
                    },
                },
            ],
            "edges": [],
        }
        save = client.post(
            "/api/pipeline/save",
            json={
                "name": "cache_mirror_pipe",
                "description": "",
                "graph": graph,
                "source_file": "cache_mirror_pipe.py",
            },
        )
        assert save.status_code == 200, save.text

        committed_dir = _json_cache_dir(str(data.resolve()), "committed")
        assert committed_dir.exists(), (
            "Save did not populate committed/ — the C2 dead-code mirror regressed"
        )

        # Simulate a fresh server process: the session marker is empty and the
        # volatile working/ layer is gone (deploy box / cleaned workspace).
        _clear_session()
        shutil.rmtree(_json_cache_dir(str(data.resolve()), "working"))

        out = load_v2_api_source(str(data.resolve()), cfg)
        assert isinstance(out, pl.LazyFrame)
        assert out.collect()["id"].to_list() == [1, 2]


# ---------------------------------------------------------------------------
# 2.4 — validity records a data-file signature
# ---------------------------------------------------------------------------


class TestDataFileSignatureValidity:
    @pytest.mark.asyncio
    async def test_status_hashes_mtime_only_drift_off_event_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A status poll may need content-hash arbitration after an mtime-only
        drift, but that large-file hash must not run on the async route thread."""
        import haute._json_shred as shred_mod
        from haute.routes.json_cache import post_json_cache_status
        from haute.schemas import JsonCacheBuildRequest

        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))
        build_per_port_cache(data, cfg, _json_cache_dir(str(data.resolve()), "working"))

        st = data.stat()
        os.utime(data, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))

        route_thread = threading.current_thread()
        hash_threads: list[threading.Thread] = []
        real_hash_file = shred_mod._hash_file

        def _tracking_hash_file(path: Path) -> str:
            hash_threads.append(threading.current_thread())
            return real_hash_file(path)

        monkeypatch.setattr(shred_mod, "_hash_file", _tracking_hash_file)

        status = await post_json_cache_status(
            JsonCacheBuildRequest(path="data.json", volatile_schema=cfg)
        )

        assert status.cached is True
        assert hash_threads, "mtime-only drift should exercise content-hash arbitration"
        assert all(thread is not route_thread for thread in hash_threads), (
            "status hashed the JSON data file on the event-loop thread"
        )

    def test_editing_data_then_rebuilding_serves_new_rows_via_route(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """The headline 2.4 repro: edit data.json, re-click build — the route
        must really rebuild (today it's a schema-fingerprint no-op serving
        stale rows with old counts)."""
        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 1}, {"id": 2}])
        cfg = _root_cfg(_col("id", "$[:].id"))

        first = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert first.status_code == 200, first.text
        assert first.json()["row_count"] == 2

        # Edit the data file (different record count AND byte length).
        _write_json(data, [{"id": 10}, {"id": 20}, {"id": 30}])

        second = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert second.status_code == 200, second.text
        assert second.json()["row_count"] == 3, (
            "re-clicking Cache as Parquet after editing data.json was a no-op — stale rows served"
        )

        out = load_v2_api_source(str(data.resolve()), cfg)
        assert isinstance(out, pl.LazyFrame)
        assert out.collect()["id"].to_list() == [10, 20, 30]

    def test_validity_false_after_data_file_edit(self, tmp_path: Path) -> None:
        data = tmp_path / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True

        _write_json(data, [{"id": 1}, {"id": 2}])
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False

    def test_validity_survives_mtime_only_change(self, tmp_path: Path) -> None:
        """A copy/touch that moves mtime but not content (deploy rsync, docker
        COPY) must NOT invalidate the cache — the content hash arbitrates, so
        the committed deploy fallback keeps working."""
        import os

        data = tmp_path / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)

        st = data.stat()
        os.utime(data, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True

    def test_validity_false_when_data_file_missing(self, tmp_path: Path) -> None:
        data = tmp_path / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)

        data.unlink()
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False

    def test_validity_false_for_legacy_meta_without_data_signature(self, tmp_path: Path) -> None:
        """A pre-W2 meta.json (no ``data_file`` key) is stale by construction —
        one-time invalidation on upgrade, the user rebuilds once."""
        data = tmp_path / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)

        meta_path = cache_dir / "meta.json"
        meta = orjson.loads(meta_path.read_bytes())
        meta.pop("data_file")
        meta_path.write_bytes(orjson.dumps(meta))
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False

    def test_meta_records_data_file_signature(self, tmp_path: Path) -> None:
        data = tmp_path / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)

        meta = read_per_port_cache_meta(cache_dir)
        assert meta is not None
        sig = meta["data_file"]
        assert sig["size"] == data.stat().st_size
        assert sig["mtime_ns"] == data.stat().st_mtime_ns
        assert isinstance(sig["sha256"], str) and len(sig["sha256"]) == 64

    def test_save_mirrors_again_after_data_only_rebuild(self, isolated_cwd: Path) -> None:
        """Same schema fingerprint, new data: the save-time mirror must NOT
        no-op (otherwise committed/ keeps the stale rows forever)."""
        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))

        working_dir = _json_cache_dir(str(data), "working")
        build_per_port_cache(data, cfg, working_dir)
        _mark_working_consulted(str(data))
        assert mirror_cache_to_committed(str(data)) is True

        # Edit data + rebuild working under the SAME schema.
        _write_json(data, [{"id": 1}, {"id": 2}])
        build_per_port_cache(data, cfg, working_dir)

        assert mirror_cache_to_committed(str(data)) is True, (
            "mirror no-opped on a data-only rebuild — committed/ kept stale rows"
        )
        committed_dir = _json_cache_dir(str(data), "committed")
        frames = load_per_port_cache(committed_dir, cfg)
        assert frames["root"].collect()["id"].to_list() == [1, 2]


# ---------------------------------------------------------------------------
# 2.5 — one shared emitting predicate (emit AND >=1 selected column)
# ---------------------------------------------------------------------------


class TestSharedEmittingPredicate:
    def _wedge_cfg(self) -> dict[str, Any]:
        """Table A emits with a selected column; table B is emit-true but has
        zero selected columns — the historical wedge shape."""
        return {
            "tables": [
                _table("$[:]", "root", [_col("id", "$[:].id")]),
                _table(
                    "$[:].drivers[:]",
                    "drivers",
                    [_col("age", "$[:].drivers[:].age", selected=False)],
                ),
            ]
        }

    def test_predicate_truth_table(self) -> None:
        from haute._json_shred import table_is_emitting

        sel = _col("a", "$[:].a")
        unsel = _col("a", "$[:].a", selected=False)
        assert table_is_emitting(_table("$[:]", "t", [sel])) is True
        assert table_is_emitting(_table("$[:]", "t", [sel], emit=False)) is False
        assert table_is_emitting(_table("$[:]", "t", [unsel])) is False
        assert table_is_emitting(_table("$[:]", "t", [])) is False
        assert table_is_emitting({"path": "$[:]", "label": "t", "emit": True}) is False
        assert table_is_emitting({"path": "$[:]", "label": "t", "emit": True, "columns": "x"}) is (
            False
        )
        assert table_is_emitting("not-a-dict") is False

    def test_unselected_columns_excluded_from_built_parquet(self, tmp_path: Path) -> None:
        """A table that IS emitting can still carry unselected columns — they
        must not leak into the parquet (build and shred agree column-wise too)."""
        data = tmp_path / "data.json"
        _write_json(data, [{"a": 1, "b": 2}])
        cfg = _root_cfg(_col("a", "$[:].a"), _col("b", "$[:].b", selected=False))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)

        out = load_per_port_cache(cache_dir, cfg)["root"].collect()
        assert out.columns == ["a"]

    def test_emit_true_zero_selected_table_does_not_wedge_cache(self, tmp_path: Path) -> None:
        """Build skips the zero-column table's parquet; validity must not
        demand it forever afterwards (the advertised remedy — re-clicking
        Cache as Parquet — could never fix it)."""
        data = tmp_path / "data.json"
        _write_json(data, [{"id": 1, "drivers": [{"age": 30}]}])
        cfg = self._wedge_cfg()
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)

        assert (cache_dir / "root.parquet").exists()
        assert not (cache_dir / "drivers.parquet").exists()
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True, (
            "validity demands a parquet the build deliberately skipped — permanent wedge"
        )

    def test_load_serves_emitting_port_despite_zero_column_emit_table(
        self, isolated_cwd: Path
    ) -> None:
        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 7, "drivers": [{"age": 30}]}])
        cfg = self._wedge_cfg()
        build_per_port_cache(data, cfg, _json_cache_dir(str(data), "working"))

        out = load_v2_api_source(str(data), cfg)
        assert isinstance(out, pl.LazyFrame)  # single EMITTING port -> bare frame
        assert out.collect()["id"].to_list() == [7]

    def test_route_wedge_shape_builds_and_reports_cached(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 1, "drivers": [{"age": 30}]}])
        cfg = self._wedge_cfg()

        build = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert build.status_code == 200, build.text

        status = client.post(
            "/api/json-cache/status",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert status.status_code == 200
        assert status.json()["cached"] is True, (
            "status reports un-cached forever for the wedge shape"
        )

    def test_all_emit_tables_unselected_loads_with_actionable_error(
        self, isolated_cwd: Path
    ) -> None:
        """When NO table is emitting, the load error must stay the actionable
        'tick at least one column' message — not the click-cache wedge."""
        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id", selected=False))
        build_per_port_cache(data, cfg, _json_cache_dir(str(data), "working"))

        with pytest.raises(RuntimeError, match="selected columns"):
            load_v2_api_source(str(data), cfg)

    def test_shred_skips_zero_selected_column_tables(self) -> None:
        """The shred walk agrees with the predicate too — no buffer is built
        for a table that can never emit a parquet."""
        cfg = self._wedge_cfg()
        buffers = shred_to_buffers([{"id": 1, "drivers": [{"age": 30}]}], cfg)
        assert set(buffers) == {"root"}


# ---------------------------------------------------------------------------
# 2.6 — atomic + serialized build
# ---------------------------------------------------------------------------


class TestAtomicSerializedBuild:
    def test_failed_rebuild_preserves_previous_valid_cache(self, tmp_path: Path) -> None:
        """A rebuild that fails partway (second table's frame build raises)
        must leave the previously valid cache byte-for-byte intact — no
        half-written parquets under the old meta."""
        data = tmp_path / "data.json"
        _write_json(data, [{"a": 1, "a2": 2, "b": "x"}])
        t1_v1 = _table("$[:]", "t1", [_col("a", "$[:].a")])
        t2_str = _table("$[:]", "t2", [_col("b", "$[:].b", type_="str")])
        cfg1 = {"tables": [t1_v1, t2_str]}
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg1, cache_dir)
        assert is_per_port_cache_valid(cache_dir, cfg1, data_path=data) is True

        # cfg2 widens t1 (extra column -> different parquet content) and breaks
        # t2 (declared int over string data -> _buffer_to_frame raises).
        t1_v2 = _table("$[:]", "t1", [_col("a", "$[:].a"), _col("a2", "$[:].a2")])
        t2_bad = _table("$[:]", "t2", [_col("b", "$[:].b", type_="int")])
        cfg2 = {"tables": [t1_v2, t2_bad]}

        from haute._api_input_schema import ApiInputSchemaError

        with pytest.raises(ApiInputSchemaError):
            build_per_port_cache(data, cfg2, cache_dir)

        # The live cache must still be EXACTLY cfg1's build.
        assert is_per_port_cache_valid(cache_dir, cfg1, data_path=data) is True
        frames = load_per_port_cache(cache_dir, cfg1)
        assert frames["t1"].collect().columns == ["a"], (
            "failed rebuild clobbered a live parquet in place — non-atomic build"
        )

    def test_crash_mid_first_build_leaves_no_half_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A first-ever build that crashes between parquet writes must not
        leave a partial cache directory behind."""
        import pyarrow.parquet as pq_mod

        data = tmp_path / "data.json"
        _write_json(data, [{"a": 1, "b": 2}])
        cfg = {
            "tables": [
                _table("$[:]", "t1", [_col("a", "$[:].a")]),
                _table("$[:]", "t2", [_col("b", "$[:].b")]),
            ]
        }
        cache_dir = tmp_path / "cache"

        real_write = pq_mod.write_table
        calls: list[str] = []

        def _explode_on_second(table: Any, where: Any, **kwargs: Any) -> None:
            calls.append(str(where))
            if len(calls) >= 2:
                raise OSError("disk full")
            real_write(table, where, **kwargs)

        monkeypatch.setattr(pq_mod, "write_table", _explode_on_second)
        with pytest.raises(OSError, match="disk full"):
            build_per_port_cache(data, cfg, cache_dir)
        monkeypatch.setattr(pq_mod, "write_table", real_write)

        assert not (cache_dir / "meta.json").exists()
        leftover = list(cache_dir.glob("*.parquet")) if cache_dir.exists() else []
        assert leftover == [], f"half-written cache left behind: {leftover}"

        # And the cache is rebuildable afterwards.
        build_per_port_cache(data, cfg, cache_dir)
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True

    def test_concurrent_builds_on_same_cache_are_serialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two simultaneous builds of the same data file (different schemas)
        must not interleave their write phases — that's how one schema's meta
        gets stamped onto another schema's parquets."""
        import pyarrow.parquet as pq_mod

        data = tmp_path / "data.json"
        _write_json(data, [{"a": i, "b": i} for i in range(50)])
        cfg_a = {"tables": [_table("$[:]", "porta", [_col("a", "$[:].a")])]}
        cfg_b = {"tables": [_table("$[:]", "portb", [_col("b", "$[:].b")])]}
        cache_dir = tmp_path / "cache"

        real_write = pq_mod.write_table
        gate = threading.Lock()
        inside = 0
        max_inside = 0

        def _tracking_write(table: Any, where: Any, **kwargs: Any) -> None:
            nonlocal inside, max_inside
            with gate:
                inside += 1
                max_inside = max(max_inside, inside)
            time.sleep(0.15)
            try:
                real_write(table, where, **kwargs)
            finally:
                with gate:
                    inside -= 1

        monkeypatch.setattr(pq_mod, "write_table", _tracking_write)

        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def _run(cfg: dict[str, Any]) -> None:
            try:
                barrier.wait(timeout=5)
                build_per_port_cache(data, cfg, cache_dir)
            except BaseException as exc:  # surfaced below
                errors.append(exc)

        threads = [
            threading.Thread(target=_run, args=(cfg_a,)),
            threading.Thread(target=_run, args=(cfg_b,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        assert max_inside == 1, "two builds wrote into the same cache directory concurrently"

        # Whoever finished last, the directory must be coherent: meta's
        # fingerprint matches a schema whose parquets are all present.
        from haute._json_shred import _v2_fingerprint

        meta = read_per_port_cache_meta(cache_dir)
        assert meta is not None
        winner = next(
            cfg for cfg in (cfg_a, cfg_b) if _v2_fingerprint(cfg) == meta["schema_fingerprint"]
        )
        assert is_per_port_cache_valid(cache_dir, winner, data_path=data) is True

    def test_leftover_build_tmp_from_crashed_build_is_cleaned(self, tmp_path: Path) -> None:
        """A `.build-tmp` sibling abandoned by a killed process must not poison
        the next build."""
        data = tmp_path / "data.json"
        _write_json(data, [{"a": 1}])
        cfg = _root_cfg(_col("a", "$[:].a"))
        cache_dir = tmp_path / "cache"

        stale_tmp = cache_dir.with_name(cache_dir.name + ".build-tmp")
        stale_tmp.mkdir(parents=True)
        (stale_tmp / "junk.parquet").write_bytes(b"stale")

        build_per_port_cache(data, cfg, cache_dir)
        assert not stale_tmp.exists()
        assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True
        assert load_per_port_cache(cache_dir, cfg)["root"].collect()["a"].to_list() == [1]

    def test_leftover_build_old_backup_is_cleaned_on_next_swap(self, tmp_path: Path) -> None:
        """A `.build-old` backup left by a crash mid-swap is removed when the
        next rebuild swaps over an existing live cache."""
        data = tmp_path / "data.json"
        _write_json(data, [{"a": 1, "b": 2}])
        cfg_a = _root_cfg(_col("a", "$[:].a"))
        cfg_b = _root_cfg(_col("b", "$[:].b"))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg_a, cache_dir)

        stale_backup = cache_dir.with_name(cache_dir.name + ".build-old")
        stale_backup.mkdir(parents=True)
        (stale_backup / "junk").write_bytes(b"stale")

        build_per_port_cache(data, cfg_b, cache_dir)  # schema change -> real swap
        assert not stale_backup.exists()
        assert is_per_port_cache_valid(cache_dir, cfg_b, data_path=data) is True

    def test_swap_restores_live_dir_when_final_rename_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the tmp→live rename fails mid-swap, the previous live dir is
        restored before the error propagates — the cache is never left missing."""
        from haute._json_shred import _swap_dir_into_place

        live = tmp_path / "cache"
        live.mkdir()
        (live / "old.parquet").write_bytes(b"old")
        tmp = tmp_path / "cache.build-tmp-deadbeef"
        tmp.mkdir()
        (tmp / "new.parquet").write_bytes(b"new")

        real_rename = Path.rename

        def _failing_rename(self: Path, target: Any) -> Any:
            if self == tmp and Path(target) == live:
                raise OSError("simulated rename failure")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", _failing_rename)
        with pytest.raises(OSError, match="simulated rename failure"):
            _swap_dir_into_place(tmp, live)
        monkeypatch.setattr(Path, "rename", real_rename)

        assert live.exists()
        assert (live / "old.parquet").read_bytes() == b"old"
        assert not tmp.exists(), "failed swap leaked the UUID build temp directory"

    def test_swap_cleans_tmp_when_live_to_backup_rename_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If live->backup fails before the swap starts, the UUID tmp dir
        must still be removed so the failed build leaves no orphan staging dir."""
        from haute._json_shred import _swap_dir_into_place

        live = tmp_path / "cache"
        live.mkdir()
        (live / "old.parquet").write_bytes(b"old")
        tmp = tmp_path / "cache.build-tmp-deadbeef"
        tmp.mkdir()
        (tmp / "new.parquet").write_bytes(b"new")

        real_rename = Path.rename

        def _failing_rename(self: Path, target: Any) -> Any:
            if self == live and Path(target).name.startswith("cache.build-old-"):
                raise OSError("simulated live-to-backup rename failure")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", _failing_rename)
        with pytest.raises(OSError, match="simulated live-to-backup rename failure"):
            _swap_dir_into_place(tmp, live)
        monkeypatch.setattr(Path, "rename", real_rename)

        assert live.exists()
        assert (live / "old.parquet").read_bytes() == b"old"
        assert not tmp.exists(), "failed live->backup rename leaked the UUID build temp directory"
        assert list(tmp_path.glob("cache.build-old-*")) == []

    def test_load_fails_loud_when_parquet_disappears_after_validity(
        self, isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a cache file disappears between validity and scan, loading must
        raise the cache-stale action message rather than KeyError or a partial
        multi-port bundle."""
        import haute._json_shred as shred_mod

        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 1}])
        cfg = _root_cfg(_col("id", "$[:].id"))
        cache_dir = _json_cache_dir(str(data), "working")
        build_per_port_cache(data, cfg, cache_dir)

        real_valid = shred_mod.is_per_port_cache_valid
        removed = False

        def _valid_then_remove(
            checked_cache_dir: str | Path,
            checked_config: dict[str, Any],
            *,
            data_path: str | Path,
        ) -> bool:
            nonlocal removed
            valid = real_valid(checked_cache_dir, checked_config, data_path=data_path)
            if valid and not removed:
                removed = True
                (Path(checked_cache_dir) / "root.parquet").unlink()
            return valid

        monkeypatch.setattr(shred_mod, "is_per_port_cache_valid", _valid_then_remove)

        with pytest.raises(RuntimeError, match="cache.*changed|Cache as Parquet"):
            load_v2_api_source(str(data), cfg)

    def test_swap_retries_transient_permission_error_for_empty_live_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Windows can transiently deny directory renames while handles settle."""
        import haute._json_shred as shred_mod
        from haute._json_shred import _swap_dir_into_place

        live = tmp_path / "cache"
        tmp = tmp_path / "cache.build-tmp-any"
        tmp.mkdir()
        (tmp / "new.parquet").write_bytes(b"new")

        real_rename = Path.rename
        attempts = 0

        def _flaky_rename(self: Path, target: Any) -> Any:
            nonlocal attempts
            if self == tmp and Path(target) == live and attempts == 0:
                attempts += 1
                raise PermissionError("transient Windows directory lock")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", _flaky_rename)
        monkeypatch.setattr(shred_mod, "_RENAME_RETRY_DELAYS_SECONDS", (0.0,))

        _swap_dir_into_place(tmp, live)

        assert attempts == 1
        assert not tmp.exists()
        assert (live / "new.parquet").read_bytes() == b"new"

    def test_builds_on_different_caches_run_in_parallel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The serialization is per cache directory, not a global bottleneck."""
        import pyarrow.parquet as pq_mod

        data_a = tmp_path / "a.json"
        data_b = tmp_path / "b.json"
        _write_json(data_a, [{"a": 1}])
        _write_json(data_b, [{"a": 2}])
        cfg = _root_cfg(_col("a", "$[:].a"))

        real_write = pq_mod.write_table
        first_inside = threading.Event()
        second_inside = threading.Event()
        overlapped: list[bool] = []

        def _waiting_write(table: Any, where: Any, **kwargs: Any) -> None:
            if not first_inside.is_set():
                first_inside.set()
                overlapped.append(second_inside.wait(timeout=5))
            else:
                second_inside.set()
            real_write(table, where, **kwargs)

        monkeypatch.setattr(pq_mod, "write_table", _waiting_write)

        threads = [
            threading.Thread(target=build_per_port_cache, args=(data_a, cfg, tmp_path / "cache_a")),
            threading.Thread(target=build_per_port_cache, args=(data_b, cfg, tmp_path / "cache_b")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert overlapped == [True], "independent cache builds were serialized against each other"


# ---------------------------------------------------------------------------
# 2.7 — dropped records are counted and surfaced
# ---------------------------------------------------------------------------


class TestSkippedRecordSurfacing:
    def test_jsonl_non_object_lines_counted_and_surfaced(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        data = isolated_cwd / "data.jsonl"
        data.write_text(
            '{"id": 1}\n5\n["not", "a", "record"]\n"just a string"\n{"id": 2}\n',
            encoding="utf-8",
        )
        cfg = _root_cfg(_col("id", "$[:].id"))

        resp = client.post(
            "/api/json-cache/build",
            json={"path": "data.jsonl", "volatile_schema": cfg},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["row_count"] == 2
        assert body["skipped_records"] == 3, "non-object JSONL lines were dropped without a count"

        status = client.post(
            "/api/json-cache/status",
            json={"path": "data.jsonl", "volatile_schema": cfg},
        )
        assert status.status_code == 200
        assert status.json()["skipped_records"] == 3

    def test_root_array_non_object_records_counted(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        data = isolated_cwd / "data.json"
        data.write_text(json.dumps([{"id": 1}, 42, [1, 2]]), encoding="utf-8")
        cfg = _root_cfg(_col("id", "$[:].id"))

        resp = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["row_count"] == 1
        assert body["skipped_records"] == 2

    def test_mixed_object_array_elements_counted_per_table(self, tmp_path: Path) -> None:
        """Non-object (and null) elements inside an object-table array lose a
        row each — every one must be counted against that table."""
        data = tmp_path / "data.json"
        _write_json(data, [{"id": 1, "drivers": [{"age": 30}, 7, "x", None]}])
        cfg = {
            "tables": [
                _table("$[:]", "root", [_col("id", "$[:].id")]),
                _table("$[:].drivers[:]", "drivers", [_col("age", "$[:].drivers[:].age")]),
            ]
        }
        cache_dir = tmp_path / "cache"
        summary = build_per_port_cache(data, cfg, cache_dir)

        skipped = summary["skipped"]
        assert skipped["records"] == 0
        assert skipped["rows_by_table"] == {"drivers": 3}
        drivers = load_per_port_cache(cache_dir, cfg)["drivers"].collect()
        assert drivers.height == 1

    def test_dict_elements_in_scalar_array_counted(self, tmp_path: Path) -> None:
        data = tmp_path / "data.json"
        _write_json(data, [{"tags": ["a", {"bad": 1}, "b"]}])
        cfg = {
            "tables": [
                _table(
                    "$[:].tags[:]",
                    "tags",
                    [_col("value", "$[:].tags[:].$value", type_="str")],
                ),
            ]
        }
        summary = build_per_port_cache(data, cfg, tmp_path / "cache")
        assert summary["skipped"]["rows_by_table"] == {"tags": 1}
        assert summary["tables"][0]["row_count"] == 2

    def test_clean_data_reports_zero_skips(self, client: TestClient, isolated_cwd: Path) -> None:
        data = isolated_cwd / "data.json"
        _write_json(data, [{"id": 1}, {"id": 2}])
        cfg = _root_cfg(_col("id", "$[:].id"))

        resp = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": cfg},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["skipped_records"] == 0
        assert body["skipped_rows"] == {}

    def test_object_json_root_is_one_record_zero_skips(self, tmp_path: Path) -> None:
        """A .json file whose root is a single object is exactly one record."""
        data = tmp_path / "data.json"
        data.write_text(json.dumps({"id": 9}), encoding="utf-8")
        cfg = _root_cfg(_col("id", "$[:].id"))
        summary = build_per_port_cache(data, cfg, tmp_path / "cache")

        assert summary["skipped"] == {"records": 0, "rows_by_table": {}}
        assert summary["tables"][0]["row_count"] == 1

    def test_scalar_json_root_counted_as_skipped_record(self, tmp_path: Path) -> None:
        """A .json file whose root is a bare scalar holds zero records — that
        is surfaced as one skipped record, not silently as an empty cache."""
        data = tmp_path / "data.json"
        data.write_text("5", encoding="utf-8")
        cfg = _root_cfg(_col("id", "$[:].id"))
        summary = build_per_port_cache(data, cfg, tmp_path / "cache")

        assert summary["skipped"] == {"records": 1, "rows_by_table": {}}
        assert summary["tables"][0]["row_count"] == 0

    def test_meta_json_round_trips_skip_counts(self, tmp_path: Path) -> None:
        data = tmp_path / "data.json"
        data.write_text(json.dumps([{"id": 1}, "shapeless"]), encoding="utf-8")
        cfg = _root_cfg(_col("id", "$[:].id"))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)

        meta = read_per_port_cache_meta(cache_dir)
        assert meta is not None
        assert meta["skipped"] == {"records": 1, "rows_by_table": {}}

    def test_noop_rebuild_still_reports_recorded_skips(self, tmp_path: Path) -> None:
        """The fingerprint no-op trapdoor must echo the recorded counts, not
        silently zero them."""
        data = tmp_path / "data.json"
        data.write_text(json.dumps([{"id": 1}, 99]), encoding="utf-8")
        cfg = _root_cfg(_col("id", "$[:].id"))
        cache_dir = tmp_path / "cache"
        build_per_port_cache(data, cfg, cache_dir)

        noop_summary = build_per_port_cache(data, cfg, cache_dir)
        assert noop_summary["skipped"] == {"records": 1, "rows_by_table": {}}


# ---------------------------------------------------------------------------
# 2.8 — date columns reject raw JSON numbers (no epoch-day reinterpretation)
# ---------------------------------------------------------------------------


class TestDateColumnsRejectJsonNumbers:
    def _date_cfg(self) -> dict[str, Any]:
        return _root_cfg(_col("start", "$[:].start", type_="date"))

    def test_int_in_date_column_rejected_loud(self, tmp_path: Path) -> None:
        """`2024` must NOT become 1975-07-18; the build fails naming the column."""
        from haute._api_input_schema import ApiInputSchemaError

        data = tmp_path / "data.json"
        _write_json(data, [{"start": 2024}])

        with pytest.raises(ApiInputSchemaError, match="start") as ei:
            build_per_port_cache(data, self._date_cfg(), tmp_path / "cache")
        assert "date" in str(ei.value)

    def test_bool_in_date_column_rejected_loud(self, tmp_path: Path) -> None:
        from haute._api_input_schema import ApiInputSchemaError

        data = tmp_path / "data.json"
        _write_json(data, [{"start": True}])

        with pytest.raises(ApiInputSchemaError, match="start"):
            build_per_port_cache(data, self._date_cfg(), tmp_path / "cache")

    def test_mixed_strings_and_ints_rejected(self, tmp_path: Path) -> None:
        from haute._api_input_schema import ApiInputSchemaError

        data = tmp_path / "data.json"
        _write_json(data, [{"start": "2024-01-15"}, {"start": 19000}])

        with pytest.raises(ApiInputSchemaError, match="start"):
            build_per_port_cache(data, self._date_cfg(), tmp_path / "cache")

    def test_iso_strings_and_nulls_build_correct_dates(self, tmp_path: Path) -> None:
        """The legitimate path keeps working: ISO-8601 strings parse, nulls stay null."""
        import datetime

        data = tmp_path / "data.json"
        _write_json(data, [{"start": "2024-01-15"}, {"start": None}])
        cache_dir = tmp_path / "cache"
        cfg = self._date_cfg()
        build_per_port_cache(data, cfg, cache_dir)

        out = load_per_port_cache(cache_dir, cfg)["root"].collect()
        assert out["start"].to_list() == [datetime.date(2024, 1, 15), None]

    def test_route_surfaces_422_naming_column(self, client: TestClient, isolated_cwd: Path) -> None:
        data = isolated_cwd / "data.json"
        _write_json(data, [{"start": 2024}])

        resp = client.post(
            "/api/json-cache/build",
            json={"path": "data.json", "volatile_schema": self._date_cfg()},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["type"] == "ApiInputSchemaError"
        assert "start" in body["detail"]
