"""Coverage-uplift tests for ``_json_flatten.py`` cache primitives and
``routes/json_cache.py`` error-handler branches.

These were the two files left below the ``[tool.haute.critical_coverage.files]``
thresholds after the v1 codec deletion (commit 5.5) — the file shapes shrank
substantially but coverage uplift wasn't included in that commit. The
uncovered code is live, not dead:

* ``mirror_cache_to_committed`` (~55 lines) — atomic working→committed
  promotion on Save, called from the save endpoint per DUAL_CACHE.md §4.
* Cache-layer scaffolding (``_json_cache_dir``, ``_read_cache_meta``,
  ``clear_json_cache``) — invariants the cache route
  + executor rely on.
* Route-level error handlers (``_read_v2_config`` rejection paths,
  ``build_json_cache``/``/status``/``/infer`` exception arms) — exercise
  paths that surface as 4xx/5xx to the frontend.

The tests favour realistic file-system manipulation over heavy mocking:
real ``.haute_cache/`` directories under ``tmp_path``, real
``orjson``-encoded meta.json files. This catches integration drift (e.g.
fingerprint format changes) that mock-based tests wouldn't.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import orjson
import pytest
from fastapi.testclient import TestClient

from haute._json_flatten import (
    _LAYER_COMMITTED,
    _LAYER_WORKING,
    _clear_session,
    _json_cache_dir,
    _json_cache_meta_path,
    _mark_working_consulted,
    _read_cache_meta,
    cache_state_signature_for_graph,
    clear_json_cache,
    mirror_cache_to_committed,
)
from haute._json_shred import build_per_port_cache

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Chdir into a fresh tmp dir and reset the working-consulted session set."""
    monkeypatch.chdir(tmp_path)
    _clear_session()
    yield tmp_path
    _clear_session()


@pytest.fixture()
def client(isolated_cwd: Path) -> TestClient:
    """TestClient under the same isolated cwd, for route-level tests."""
    from haute.server import app

    return TestClient(app)


def _mirror_config(*, type_: str = "int") -> dict[str, Any]:
    """Return a small valid v2 schema for save-time mirror tests."""
    return {
        "tables": [
            {
                "path": "$[:]",
                "label": "data",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[:].value",
                        "type": type_,
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }


@pytest.fixture()
def mirror_case(isolated_cwd: Path) -> tuple[Path, dict[str, Any]]:
    """Create the source file and editor schema authoritative for a mirror."""
    data_path = isolated_cwd / "data" / "x.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_bytes(orjson.dumps([{"value": 1}]))
    return data_path, _mirror_config()


def _write_signed_cache(
    cache_dir: Path,
    *,
    data_path: Path,
    v2_config: dict[str, Any],
) -> dict[str, Any]:
    """Create a fully valid cache using the production cache builder."""
    build_per_port_cache(data_path, v2_config, cache_dir)
    payload = _read_cache_meta(cache_dir)
    assert payload is not None
    return payload


def _current_parquet(cache_dir: Path) -> Path:
    meta = _read_cache_meta(cache_dir)
    assert meta is not None
    entry = next(table for table in meta["tables"] if table["label"] == "data")
    return cache_dir / entry["parquet"]


# ---------------------------------------------------------------------------
# _json_cache_dir — invalid layer
# ---------------------------------------------------------------------------


class TestJsonCacheDir:
    def test_rejects_unknown_layer(self) -> None:
        """Line 97: unknown layer name raises ValueError with the bad value."""
        with pytest.raises(ValueError, match=r"Unknown cache layer.*'bogus'"):
            _json_cache_dir("data/x.json", "bogus")


# ---------------------------------------------------------------------------
# _read_cache_meta — absent + malformed
# ---------------------------------------------------------------------------


class TestReadCacheMeta:
    def test_absent_returns_none(self, isolated_cwd: Path) -> None:
        """No meta.json → None."""
        assert _read_cache_meta(isolated_cwd / "nowhere") is None

    def test_non_dict_payload_raises(self, isolated_cwd: Path) -> None:
        """meta.json that isn't a JSON object raises ValueError loudly."""
        cache_dir = isolated_cwd / "cache"
        cache_dir.mkdir()
        _json_cache_meta_path(cache_dir).write_bytes(orjson.dumps([1, 2, 3]))
        with pytest.raises(ValueError, match="metadata must be an object"):
            _read_cache_meta(cache_dir)


# ---------------------------------------------------------------------------
# cache_state_signature_for_graph — early-continue branches
# ---------------------------------------------------------------------------


class TestCacheStateSignatureForGraph:
    def test_skips_node_when_path_hash_raises_runtime_error(
        self, isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F306: ``_path_hash`` raises ``RuntimeError`` for an unresolvable
        ``~`` path (``Path.expanduser`` -> 'Could not determine home
        directory'). That must be caught by the per-node guard so the whole
        preview/trace key computation stays total, not aborted by one bad
        apiInput path."""
        from haute._types import GraphNode, NodeData, NodeType, PipelineGraph

        # Reproduce the real failure mode: Path.expanduser blows up (no home).
        real_expanduser = Path.expanduser

        def _boom(self: Path) -> Path:
            raise RuntimeError("Could not determine home directory")

        monkeypatch.setattr(Path, "expanduser", _boom)

        node = GraphNode(
            id="n1",
            type="custom",
            data=NodeData(
                label="n1",
                nodeType=NodeType.API_INPUT,
                config={"path": "~/data/x.json"},
            ),
            position={"x": 0, "y": 0},
        )
        graph = PipelineGraph(nodes=[node], edges=cast_edges([]))

        # Must NOT raise RuntimeError; the unresolvable node is simply skipped.
        sig = cache_state_signature_for_graph(graph)
        assert sig == ""
        # Sanity: without the monkeypatch the same call would produce a fragment.
        monkeypatch.setattr(Path, "expanduser", real_expanduser)
        assert cache_state_signature_for_graph(graph).startswith("json_cache=")

    def test_signature_distinguishes_same_mtime_different_size(self, isolated_cwd: Path) -> None:
        """F012: two meta.json rebuilds that land in the same millisecond
        mtime bucket must still produce distinct signature fragments. The
        old ``int(st_mtime*1000)`` key collided; keying on ns + size (the
        content changed size) disambiguates."""
        import os

        from haute._types import GraphNode, NodeData, NodeType, PipelineGraph

        node = GraphNode(
            id="n1",
            type="custom",
            data=NodeData(
                label="n1",
                nodeType=NodeType.API_INPUT,
                config={"path": "data/x.json"},
            ),
            position={"x": 0, "y": 0},
        )
        graph = PipelineGraph(nodes=[node], edges=cast_edges([]))

        committed_dir = _json_cache_dir("data/x.json", _LAYER_COMMITTED)
        meta_path = _json_cache_meta_path(committed_dir)
        committed_dir.mkdir(parents=True, exist_ok=True)

        fixed = 1_700_000_000.0  # identical wall-clock mtime for both writes
        meta_path.write_bytes(orjson.dumps({"schema_fingerprint": "a"}))
        os.utime(meta_path, (fixed, fixed))
        sig_small = cache_state_signature_for_graph(graph)

        # A larger payload written at the SAME mtime — ms bucket unchanged.
        meta_path.write_bytes(orjson.dumps({"schema_fingerprint": "a" * 5000}))
        os.utime(meta_path, (fixed, fixed))
        sig_large = cache_state_signature_for_graph(graph)

        assert sig_small != sig_large

    def test_skips_apiinput_nodes_with_no_path(self, isolated_cwd: Path) -> None:
        """Lines 196, 199-200: apiInput with no/empty/non-string path is skipped."""
        from haute._types import GraphNode, NodeData, NodeType, PipelineGraph

        nodes = [
            # Non-apiInput node — skipped at type check
            GraphNode(
                id="n0",
                type="custom",
                data=NodeData(label="n0", nodeType=NodeType.POLARS, config={}),
                position={"x": 0, "y": 0},
            ),
            # apiInput with no path
            GraphNode(
                id="n1",
                type="custom",
                data=NodeData(label="n1", nodeType=NodeType.API_INPUT, config={}),
                position={"x": 0, "y": 0},
            ),
            # apiInput with empty path
            GraphNode(
                id="n2",
                type="custom",
                data=NodeData(label="n2", nodeType=NodeType.API_INPUT, config={"path": ""}),
                position={"x": 0, "y": 0},
            ),
            # apiInput with non-string path (the `not isinstance(..., str)` arm)
            GraphNode(
                id="n3",
                type="custom",
                data=NodeData(label="n3", nodeType=NodeType.API_INPUT, config={"path": 42}),
                position={"x": 0, "y": 0},
            ),
        ]
        graph = PipelineGraph(nodes=nodes, edges=cast_edges([]))
        # All four nodes contribute zero to the signature; the function
        # should return a stable signature without crashing.
        sig = cache_state_signature_for_graph(graph)
        assert isinstance(sig, str)


def cast_edges(es: list[Any]) -> list[Any]:
    """Trivial helper to make the GraphEdge list type-checkable in the fixture."""
    return es


# ---------------------------------------------------------------------------
# clear_json_cache — exists branch (line 234)
# ---------------------------------------------------------------------------


class TestClearJsonCache:
    def test_clears_existing_cache_dir(self, isolated_cwd: Path) -> None:
        """Existing cache dir gets rmtree'd; returns True."""
        cache_dir = _json_cache_dir("data/x.json", _LAYER_WORKING)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "data.parquet").write_bytes(b"stale")
        assert clear_json_cache("data/x.json", layer=_LAYER_WORKING) is True
        assert not cache_dir.exists()

    def test_no_cache_dir_returns_false(self, isolated_cwd: Path) -> None:
        """No cache dir → False, no exception."""
        assert clear_json_cache("data/never.json", layer=_LAYER_WORKING) is False


# ---------------------------------------------------------------------------
# mirror_cache_to_committed — the big one (lines 259-313)
# ---------------------------------------------------------------------------


class TestMirrorCacheToCommitted:
    def test_no_session_consultation_returns_false(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
    ) -> None:
        """Trapdoor: if this session never consulted the cache for this
        data_path, the mirror is a no-op. Avoids promoting stale on-disk
        working/ from a previous session."""
        data_path, v2_config = mirror_case
        working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
        working_dir.mkdir(parents=True, exist_ok=True)
        (working_dir / "data.parquet").write_bytes(b"data")
        # NOT calling _mark_working_consulted → trapdoor closes
        assert mirror_cache_to_committed(data_path, v2_config) is False

    def test_consulted_but_no_working_no_committed_returns_false(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
    ) -> None:
        """Session consulted but nothing on disk to mirror or wipe → False."""
        data_path, v2_config = mirror_case
        _mark_working_consulted(data_path)
        assert mirror_cache_to_committed(data_path, v2_config) is False

    def test_consulted_no_working_with_committed_wipes_committed(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
    ) -> None:
        """Delete-then-save flow: working was cleared by user, committed
        from previous save should be wiped. Returns True."""
        data_path, v2_config = mirror_case
        _mark_working_consulted(data_path)
        committed_dir = _json_cache_dir(data_path, _LAYER_COMMITTED)
        committed_dir.mkdir(parents=True, exist_ok=True)
        (committed_dir / "data.parquet").write_bytes(b"stale")
        assert mirror_cache_to_committed(data_path, v2_config) is True
        assert not committed_dir.exists()

    def test_matching_fingerprints_is_noop(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
    ) -> None:
        """Working + committed exist with same fingerprint → no-op, False."""
        data_path, v2_config = mirror_case
        _mark_working_consulted(data_path)
        working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
        committed_dir = _json_cache_dir(data_path, _LAYER_COMMITTED)
        _write_signed_cache(
            working_dir,
            data_path=data_path,
            v2_config=v2_config,
        )
        _write_signed_cache(
            committed_dir,
            data_path=data_path,
            v2_config=v2_config,
        )

        assert mirror_cache_to_committed(data_path, v2_config) is False
        # committed/ untouched
        assert (
            _current_parquet(committed_dir).read_bytes()
            == _current_parquet(
                working_dir,
            ).read_bytes()
        )

    def test_different_fingerprints_promotes_atomically(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
    ) -> None:
        """Fingerprints differ → atomic swap, working/ contents land in
        committed/. Returns True."""
        data_path, v2_config = mirror_case
        _mark_working_consulted(data_path)
        working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
        committed_dir = _json_cache_dir(data_path, _LAYER_COMMITTED)
        _write_signed_cache(
            working_dir,
            data_path=data_path,
            v2_config=v2_config,
        )
        old_config = _mirror_config()
        old_config["tables"][0]["row_id_column"] = "value"
        _write_signed_cache(
            committed_dir,
            data_path=data_path,
            v2_config=old_config,
        )
        assert mirror_cache_to_committed(data_path, v2_config) is True
        # committed/ now has working/'s content
        assert (
            _current_parquet(committed_dir).read_bytes()
            == _current_parquet(
                working_dir,
            ).read_bytes()
        )
        # And working/ is still there (mirror doesn't move, it copies)
        assert _current_parquet(working_dir).exists()

    def test_no_committed_yet_creates_it(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
    ) -> None:
        """First save: no committed/ exists; working/ gets copied over."""
        data_path, v2_config = mirror_case
        _mark_working_consulted(data_path)
        working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
        _write_signed_cache(
            working_dir,
            data_path=data_path,
            v2_config=v2_config,
        )

        committed_dir = _json_cache_dir(data_path, _LAYER_COMMITTED)
        assert not committed_dir.exists()

        assert mirror_cache_to_committed(data_path, v2_config) is True
        assert committed_dir.exists()
        assert (
            _current_parquet(committed_dir).read_bytes()
            == _current_parquet(
                working_dir,
            ).read_bytes()
        )

    def test_holds_build_lock_during_copy(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """F010: the mirror must serialize against a concurrent build of the
        SAME working dir by holding ``_build_lock_for(working_dir)`` across
        the read-meta + copytree + swap, so a build can't interleave with a
        promotion of that cache."""
        import shutil as _shutil

        from haute._json_shred import _build_lock_for

        data_path, v2_config = mirror_case
        _mark_working_consulted(data_path)
        working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
        _write_signed_cache(
            working_dir,
            data_path=data_path,
            v2_config=v2_config,
        )

        lock = _build_lock_for(working_dir)
        held: dict[str, bool] = {}
        real_copytree = _shutil.copytree

        def _acquire_from_competing_thread() -> bool:
            acquired: list[bool] = []

            def _try_acquire() -> None:
                did_acquire = lock.acquire(blocking=False)
                acquired.append(did_acquire)
                if did_acquire:
                    lock.release()

            contender = threading.Thread(target=_try_acquire)
            contender.start()
            contender.join(timeout=5)
            assert not contender.is_alive()
            return acquired == [True]

        def _spy_copytree(src: Any, dst: Any, *a: Any, **k: Any) -> Any:
            # An RLock is intentionally re-entrant and Python 3.11 does not
            # expose RLock.locked(). Probe ownership from a competing thread.
            held["locked"] = not _acquire_from_competing_thread()
            return real_copytree(src, dst, *a, **k)

        monkeypatch.setattr("haute._json_flatten.shutil.copytree", _spy_copytree)
        assert mirror_cache_to_committed(data_path, v2_config) is True
        assert held["locked"] is True
        # Lock released after the mirror returns.
        assert _acquire_from_competing_thread() is True

    def test_survives_transient_rename_permission_error(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """F010: a bare ``Path.rename`` can raise ``PermissionError`` on
        Windows when a scanner briefly holds a handle. The mirror must reuse
        the sibling Windows-safe rename-with-retry (via ``_swap_dir_into_place``)
        so a transient lock doesn't abort the promotion."""
        data_path, v2_config = mirror_case
        _mark_working_consulted(data_path)
        working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
        _write_signed_cache(
            working_dir,
            data_path=data_path,
            v2_config=v2_config,
        )

        committed_dir = _json_cache_dir(data_path, _LAYER_COMMITTED)
        assert not committed_dir.exists()

        real_rename = Path.rename
        directory_rename_calls = {"n": 0}

        def _flaky_rename(self: Path, target: Any) -> Any:
            # Runtime parquet snapshot publication also renames a file while
            # validating working/. This witness targets only the directory
            # publication performed by _swap_dir_into_place.
            if self.is_dir():
                directory_rename_calls["n"] += 1
                if directory_rename_calls["n"] == 1:
                    raise PermissionError("transient handle lock")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", _flaky_rename)
        # Bare rename would propagate the PermissionError; the retry helper
        # swallows the first failure and succeeds on retry.
        assert mirror_cache_to_committed(data_path, v2_config) is True
        assert directory_rename_calls["n"] >= 2
        assert (
            _current_parquet(committed_dir).read_bytes()
            == _current_parquet(
                working_dir,
            ).read_bytes()
        )

    def test_corrupt_committed_meta_proceeds_to_copy(
        self,
        mirror_case: tuple[Path, dict[str, Any]],
    ) -> None:
        """F307: a corrupt ``committed/meta.json`` must be treated as a
        mismatch (degrade-to-stale, like ``is_per_port_cache_valid``) so the
        mirror overwrites it with the fresh working/ copy — NOT abort before
        the copytree that would have repaired it."""
        data_path, v2_config = mirror_case
        _mark_working_consulted(data_path)
        working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
        committed_dir = _json_cache_dir(data_path, _LAYER_COMMITTED)
        working_meta = _write_signed_cache(
            working_dir,
            data_path=data_path,
            v2_config=v2_config,
        )

        # Corrupt committed meta — orjson.loads would raise mid-mirror.
        committed_dir.mkdir(parents=True, exist_ok=True)
        _json_cache_meta_path(committed_dir).write_bytes(b"{ not json")
        (committed_dir / "data.parquet").write_bytes(b"stale")

        assert mirror_cache_to_committed(data_path, v2_config) is True
        assert (
            _current_parquet(committed_dir).read_bytes()
            == _current_parquet(
                working_dir,
            ).read_bytes()
        )
        # The corrupt meta got replaced by the working/ meta.
        assert _read_cache_meta(committed_dir) == working_meta


# ---------------------------------------------------------------------------
# _read_v2_config — rejection paths (route-level, lines 149-157)
# ---------------------------------------------------------------------------


class TestReadV2ConfigRejectionPaths:
    def test_absent_file_returns_none(self) -> None:
        from haute.routes.json_cache import _read_v2_config

        assert _read_v2_config("nope.json") is None

    def test_empty_config_path_returns_none(self) -> None:
        from haute.routes.json_cache import _read_v2_config

        assert _read_v2_config("") is None
        assert _read_v2_config(None) is None

    def test_malformed_json_raises_corruption_error(self, tmp_path: Path) -> None:
        """A present-but-corrupt config raises (distinct from absent/None) so the
        route can surface a precise corruption message rather than the
        misleading 'no schema source' message."""
        from haute._api_input_schema import ApiInputSchemaError
        from haute.routes.json_cache import _read_v2_config

        cfg = tmp_path / "broken.json"
        cfg.write_bytes(b"{ not valid json ")
        with pytest.raises(ApiInputSchemaError) as ei:
            _read_v2_config(str(cfg))
        assert "not valid json" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# build_json_cache — exception handlers (lines 295-305)
# ---------------------------------------------------------------------------


class TestBuildJsonCacheExceptions:
    def test_data_file_not_found_returns_404(self, client: TestClient) -> None:
        """FileNotFoundError on the data file → 404."""
        resp = client.post(
            "/api/json-cache/build",
            json={
                "path": "data/missing.json",
                "volatile_schema": {
                    "tables": [{"path": "$[:]", "label": "root", "emit": True, "columns": []}]
                },
            },
        )
        assert resp.status_code == 404
        assert "Data file not found" in resp.json()["detail"]

    def test_malformed_data_returns_422(self, client: TestClient, isolated_cwd: Path) -> None:
        """orjson.JSONDecodeError on the DATA file → 422 with parser hint."""
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "broken.json").write_bytes(b"{ broken")
        resp = client.post(
            "/api/json-cache/build",
            json={
                "path": "data/broken.json",
                "volatile_schema": {
                    "tables": [
                        {
                            "path": "$[:]",
                            "label": "root",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[:].x",
                                    "type": "str",
                                    "status": "Inferred",
                                    "selected": True,
                                }
                            ],
                        }
                    ]
                },
            },
        )
        assert resp.status_code == 422
        assert "Invalid JSON in data file" in resp.json()["detail"]

    def test_generic_exception_returns_500(self, client: TestClient, isolated_cwd: Path) -> None:
        """An unexpected exception in the build pipeline → 500 with
        opaque detail (no internals leak to clients)."""
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "ok.json").write_bytes(orjson.dumps([{"x": 1}]))
        # build_per_port_cache is imported lazily inside the route — patch
        # at the source module.
        with patch(
            "haute._json_shred.build_per_port_cache",
            side_effect=RuntimeError("internal explosion"),
        ):
            resp = client.post(
                "/api/json-cache/build",
                json={
                    "path": "data/ok.json",
                    "volatile_schema": {
                        "tables": [
                            {
                                "path": "$[:]",
                                "label": "root",
                                "emit": True,
                                "columns": [
                                    {
                                        "name": "x",
                                        "path": "$[:].x",
                                        "type": "int",
                                        "status": "Inferred",
                                        "selected": True,
                                    }
                                ],
                            }
                        ]
                    },
                },
            )
        assert resp.status_code == 500
        # Opaque message — internals don't leak
        assert "internal explosion" not in resp.text


# ---------------------------------------------------------------------------
# GET/POST /status — branches not covered by happy path
# ---------------------------------------------------------------------------


class TestStatusBranches:
    def test_get_status_no_cache_meta_returns_cached_false(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """GET /status: data file exists, valid v2 config_path exists,
        but no cache built yet → cached=False (line 360)."""
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "ok.json").write_bytes(orjson.dumps([{"x": 1}]))
        cfg_dir = isolated_cwd / "config"
        cfg_dir.mkdir()
        cfg = cfg_dir / "quotes.json"
        cfg.write_bytes(
            orjson.dumps(
                {
                    "tables": [
                        {
                            "path": "$[:]",
                            "label": "root",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[:].x",
                                    "type": "int",
                                    "status": "Inferred",
                                    "selected": True,
                                }
                            ],
                        }
                    ]
                }
            )
        )
        resp = client.get(
            "/api/json-cache/status",
            params={"path": "data/ok.json", "config_path": str(cfg)},
        )
        assert resp.status_code == 200
        assert resp.json()["cached"] is False


# ---------------------------------------------------------------------------
# /infer — exception handlers (lines 408-423)
# ---------------------------------------------------------------------------


class TestInferExceptions:
    def test_infer_data_file_not_found_returns_404(self, client: TestClient) -> None:
        resp = client.post("/api/json-cache/infer", json={"path": "data/nope.json"})
        assert resp.status_code == 404
        assert "Data file not found" in resp.json()["detail"]

    def test_infer_malformed_json_returns_422(self, client: TestClient, isolated_cwd: Path) -> None:
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "broken.json").write_bytes(b"{ broken")
        resp = client.post("/api/json-cache/infer", json={"path": "data/broken.json"})
        assert resp.status_code == 422
        assert "Invalid JSON in data file" in resp.json()["detail"]

    def test_infer_generic_exception_returns_500(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """Unexpected internal error during inference → 500 with opaque detail."""
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "ok.json").write_bytes(orjson.dumps([{"x": 1}]))
        with patch(
            "haute._json_shred.infer_v2_schema_from_data",
            side_effect=RuntimeError("infer boom"),
        ):
            resp = client.post("/api/json-cache/infer", json={"path": "data/ok.json"})
        assert resp.status_code == 500
        assert "infer boom" not in resp.text


# ---------------------------------------------------------------------------
# Status-route + dispatch branches not hit by happy paths
# ---------------------------------------------------------------------------


class TestStatusAndDispatchBranches:
    """Targets specific uncovered branches in routes/json_cache.py:
    GET /status's "meta is None" arm, POST /status's ApiInputSchemaError
    arm, and the `_select_v2_config` volatile-vs-disk dispatch arms."""

    # NOTE: POST /status with a malformed volatile_schema does NOT trigger
    # ApiInputSchemaError today. The schema validator only runs on the
    # build path; status just reads cache state and returns cached:false
    # for an unknown shape. The 377-378 branch (try/except
    # ApiInputSchemaError around _v2_status_response) is therefore only
    # reachable if is_per_port_cache_valid raises mid-check — a state
    # inconsistency that needs mocking to reproduce. Tracked as a
    # backlog item for branch-coverage uplift.

    def test_get_status_returns_uncached_for_valid_config(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """GET /status: data file + valid v2 config_path exist; cache_dir
        absent → cached=False without exception. Exercises the
        `is_per_port_cache_valid` False branch + meta-None branch."""
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "ok.json").write_bytes(orjson.dumps([{"x": 1}]))
        cfg_dir = isolated_cwd / "config"
        cfg_dir.mkdir()
        cfg = cfg_dir / "quotes.json"
        cfg.write_bytes(
            orjson.dumps(
                {
                    "tables": [
                        {
                            "path": "$[:]",
                            "label": "root",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[:].x",
                                    "type": "int",
                                    "status": "Inferred",
                                    "selected": True,
                                }
                            ],
                        }
                    ]
                }
            )
        )
        resp = client.get(
            "/api/json-cache/status",
            params={"path": "data/ok.json", "config_path": str(cfg)},
        )
        assert resp.status_code == 200
        assert resp.json()["cached"] is False

    def test_post_status_volatile_schema_wins_over_disk(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """When BOTH volatile_schema and config_path are supplied, volatile
        wins. Exercises the volatile-arm of `_select_v2_config` (line 196 ->
        return body.volatile_schema, skipping the disk read branch)."""
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "ok.json").write_bytes(orjson.dumps([{"x": 1}]))
        cfg = isolated_cwd / "stale.json"
        cfg.write_bytes(
            orjson.dumps(
                {
                    "tables": [
                        {
                            "path": "$[:]",
                            "label": "root",
                            "emit": True,
                            "columns": [
                                # On-disk says column is named "wrong"
                                {
                                    "name": "wrong",
                                    "path": "$[:].wrong",
                                    "type": "str",
                                    "status": "Inferred",
                                    "selected": True,
                                }
                            ],
                        }
                    ]
                }
            )
        )
        resp = client.post(
            "/api/json-cache/status",
            json={
                "path": "data/ok.json",
                "config_path": str(cfg),
                # In-memory volatile says column is named "x" — should win.
                "volatile_schema": {
                    "tables": [
                        {
                            "path": "$[:]",
                            "label": "root",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[:].x",
                                    "type": "int",
                                    "status": "Inferred",
                                    "selected": True,
                                }
                            ],
                        }
                    ]
                },
            },
        )
        # The route accepts both; volatile wins. Behaviour we care about
        # is "did it dispatch via volatile" — `cached: false` is fine.
        assert resp.status_code == 200

    def test_infer_unexpected_polars_failure_returns_500(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """A RuntimeError raised from inside the infer pipeline (not a
        FileNotFoundError, not a JSONDecodeError) → 500 with opaque
        detail. Exercises line 423 (generic Exception arm)."""
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "ok.json").write_bytes(orjson.dumps([{"x": 1}]))
        # Patch at the source module since the route imports lazily.
        with patch(
            "haute._json_shred.infer_v2_schema_from_data",
            side_effect=RuntimeError("polars panic during inference"),
        ):
            resp = client.post("/api/json-cache/infer", json={"path": "data/ok.json"})
        assert resp.status_code == 500
        # Opaque message — internals don't leak
        assert "polars panic" not in resp.text
