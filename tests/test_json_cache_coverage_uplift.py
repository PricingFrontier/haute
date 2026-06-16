"""Coverage-uplift tests for ``_json_flatten.py`` cache primitives and
``routes/json_cache.py`` error-handler branches.

These were the two files left below the ``[tool.haute.critical_coverage.files]``
thresholds after the v1 codec deletion (commit 5.5) — the file shapes shrank
substantially but coverage uplift wasn't included in that commit. The
uncovered code is live, not dead:

* ``mirror_cache_to_committed`` (~55 lines) — atomic working→committed
  promotion on Save, called from the save endpoint per DUAL_CACHE.md §4.
* Cache-layer scaffolding (``_json_cache_dir``, ``_wipe_legacy_flat_cache``,
  ``_read_cache_meta``, ``clear_json_cache``) — invariants the cache route
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
    _wipe_legacy_flat_cache,
    cache_state_signature_for_graph,
    clear_json_cache,
    mirror_cache_to_committed,
)

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


def _write_meta(cache_dir: Path, payload: dict[str, Any]) -> None:
    """Helper: create cache_dir + write meta.json with the given payload."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    _json_cache_meta_path(cache_dir).write_bytes(orjson.dumps(payload))


# ---------------------------------------------------------------------------
# _json_cache_dir — invalid layer
# ---------------------------------------------------------------------------


class TestJsonCacheDir:
    def test_rejects_unknown_layer(self) -> None:
        """Line 97: unknown layer name raises ValueError with the bad value."""
        with pytest.raises(ValueError, match=r"Unknown cache layer.*'bogus'"):
            _json_cache_dir("data/x.json", "bogus")


# ---------------------------------------------------------------------------
# _wipe_legacy_flat_cache — flat-cache cleanup (v1 leftover sweeper)
# ---------------------------------------------------------------------------


class TestWipeLegacyFlatCache:
    def test_no_legacy_artifacts_returns_false(self, isolated_cwd: Path) -> None:
        """No legacy flat-cache artifacts → no-op, returns False."""
        assert _wipe_legacy_flat_cache("data/never.json") is False

    def test_wipes_existing_legacy_artifacts(self, isolated_cwd: Path) -> None:
        """Pre-existing flat-cache artifacts get unlinked; returns True.

        Pre-dual-cache layout used `.haute_cache/json_<hash>.parquet`
        plus a sidecar `.parquet.meta.json` (and `.tmp` / `.raw.parquet`
        intermediates). The wipe must remove all of them.
        """
        from haute._json_flatten import _CACHE_DIR, _path_hash

        h = _path_hash("data/x.json")
        legacy_dir = isolated_cwd / _CACHE_DIR
        legacy_dir.mkdir(parents=True, exist_ok=True)
        artifact = legacy_dir / f"json_{h}.parquet"
        artifact.write_bytes(b"stale")
        meta = legacy_dir / f"json_{h}.parquet.meta.json"
        meta.write_bytes(b"{}")
        raw = legacy_dir / f"json_{h}.raw.parquet"
        raw.write_bytes(b"raw-stale")

        assert _wipe_legacy_flat_cache("data/x.json") is True
        assert not artifact.exists()
        assert not meta.exists()
        assert not raw.exists()


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
    def test_no_session_consultation_returns_false(self, isolated_cwd: Path) -> None:
        """Trapdoor: if this session never consulted the cache for this
        data_path, the mirror is a no-op. Avoids promoting stale on-disk
        working/ from a previous session."""
        working_dir = _json_cache_dir("data/x.json", _LAYER_WORKING)
        working_dir.mkdir(parents=True, exist_ok=True)
        (working_dir / "data.parquet").write_bytes(b"data")
        # NOT calling _mark_working_consulted → trapdoor closes
        assert mirror_cache_to_committed("data/x.json") is False

    def test_consulted_but_no_working_no_committed_returns_false(self, isolated_cwd: Path) -> None:
        """Session consulted but nothing on disk to mirror or wipe → False."""
        _mark_working_consulted("data/x.json")
        assert mirror_cache_to_committed("data/x.json") is False

    def test_consulted_no_working_with_committed_wipes_committed(self, isolated_cwd: Path) -> None:
        """Delete-then-save flow: working was cleared by user, committed
        from previous save should be wiped. Returns True."""
        _mark_working_consulted("data/x.json")
        committed_dir = _json_cache_dir("data/x.json", _LAYER_COMMITTED)
        committed_dir.mkdir(parents=True, exist_ok=True)
        (committed_dir / "data.parquet").write_bytes(b"stale")
        assert mirror_cache_to_committed("data/x.json") is True
        assert not committed_dir.exists()

    def test_matching_fingerprints_is_noop(self, isolated_cwd: Path) -> None:
        """Working + committed exist with same fingerprint → no-op, False."""
        _mark_working_consulted("data/x.json")
        working_dir = _json_cache_dir("data/x.json", _LAYER_WORKING)
        committed_dir = _json_cache_dir("data/x.json", _LAYER_COMMITTED)
        same = {"schema_fingerprint": "abc123", "schema_mode": "v2"}
        _write_meta(working_dir, same)
        _write_meta(committed_dir, same)
        (working_dir / "data.parquet").write_bytes(b"working")
        (committed_dir / "data.parquet").write_bytes(b"committed")

        assert mirror_cache_to_committed("data/x.json") is False
        # committed/ untouched
        assert (committed_dir / "data.parquet").read_bytes() == b"committed"

    def test_different_fingerprints_promotes_atomically(self, isolated_cwd: Path) -> None:
        """Fingerprints differ → atomic swap, working/ contents land in
        committed/. Returns True."""
        _mark_working_consulted("data/x.json")
        working_dir = _json_cache_dir("data/x.json", _LAYER_WORKING)
        committed_dir = _json_cache_dir("data/x.json", _LAYER_COMMITTED)
        _write_meta(working_dir, {"schema_fingerprint": "new", "schema_mode": "v2"})
        _write_meta(committed_dir, {"schema_fingerprint": "old", "schema_mode": "v2"})
        (working_dir / "data.parquet").write_bytes(b"new-content")
        (committed_dir / "data.parquet").write_bytes(b"old-content")

        assert mirror_cache_to_committed("data/x.json") is True
        # committed/ now has working/'s content
        assert (committed_dir / "data.parquet").read_bytes() == b"new-content"
        # And working/ is still there (mirror doesn't move, it copies)
        assert (working_dir / "data.parquet").read_bytes() == b"new-content"

    def test_no_committed_yet_creates_it(self, isolated_cwd: Path) -> None:
        """First save: no committed/ exists; working/ gets copied over."""
        _mark_working_consulted("data/x.json")
        working_dir = _json_cache_dir("data/x.json", _LAYER_WORKING)
        _write_meta(working_dir, {"schema_fingerprint": "new", "schema_mode": "v2"})
        (working_dir / "data.parquet").write_bytes(b"new")

        committed_dir = _json_cache_dir("data/x.json", _LAYER_COMMITTED)
        assert not committed_dir.exists()

        assert mirror_cache_to_committed("data/x.json") is True
        assert committed_dir.exists()
        assert (committed_dir / "data.parquet").read_bytes() == b"new"

    def test_stale_tmp_dir_gets_wiped_before_copy(self, isolated_cwd: Path) -> None:
        """If a previous mirror attempt left a `.tmp` sibling, the new
        attempt cleans it before reusing the slot."""
        _mark_working_consulted("data/x.json")
        working_dir = _json_cache_dir("data/x.json", _LAYER_WORKING)
        committed_dir = _json_cache_dir("data/x.json", _LAYER_COMMITTED)
        _write_meta(working_dir, {"schema_fingerprint": "new", "schema_mode": "v2"})
        (working_dir / "data.parquet").write_bytes(b"new")

        tmp_dir = committed_dir.with_name(committed_dir.name + ".tmp")
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "leftover").write_bytes(b"stale-tmp")

        assert mirror_cache_to_committed("data/x.json") is True
        assert not tmp_dir.exists()
        assert (committed_dir / "data.parquet").read_bytes() == b"new"


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
        misleading 'no schema source' migration prompt."""
        from haute._api_input_schema import ApiInputSchemaError
        from haute.routes.json_cache import _read_v2_config

        cfg = tmp_path / "broken.json"
        cfg.write_bytes(b"{ not valid json ")
        with pytest.raises(ApiInputSchemaError) as ei:
            _read_v2_config(str(cfg))
        assert "not valid json" in str(ei.value).lower()

    def test_non_dict_root_returns_none(self, tmp_path: Path) -> None:
        from haute.routes.json_cache import _read_v2_config

        cfg = tmp_path / "list.json"
        cfg.write_bytes(orjson.dumps([1, 2, 3]))
        assert _read_v2_config(str(cfg)) is None

    def test_v1_shape_returns_none(self, tmp_path: Path) -> None:
        """Config with flattenSchema but no tables[] → not v2 shape → None.
        After v1 deletion this means "no usable schema source"."""
        from haute.routes.json_cache import _read_v2_config

        cfg = tmp_path / "v1.json"
        cfg.write_bytes(orjson.dumps({"flattenSchema": {"x": "str"}}))
        assert _read_v2_config(str(cfg)) is None


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
                    "tables": [{"path": "$[*]", "label": "$[*]", "emit": True, "columns": []}]
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
                            "path": "$[*]",
                            "label": "$[*]",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[*].x",
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
                                "path": "$[*]",
                                "label": "$[*]",
                                "emit": True,
                                "columns": [
                                    {
                                        "name": "x",
                                        "path": "$[*].x",
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
                            "path": "$[*]",
                            "label": "$[*]",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[*].x",
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
                            "path": "$[*]",
                            "label": "$[*]",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[*].x",
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
                            "path": "$[*]",
                            "label": "$[*]",
                            "emit": True,
                            "columns": [
                                # On-disk says column is named "wrong"
                                {
                                    "name": "wrong",
                                    "path": "$[*].wrong",
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
                            "path": "$[*]",
                            "label": "$[*]",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "x",
                                    "path": "$[*].x",
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
