"""Dual-cache behavioural contract tests (working/ vs committed/ layers).

Covers the 8 scenarios from the dual-cache plan:

  1. SET-UP — known state after cache + save: both layers in sync.
  2. Volatile cache changes with in-session schema edits; stable unchanged.
  3. Save mirrors working/ → committed/; working/ unchanged.
  4. Delete only affects volatile (working/); committed/ untouched.
  5. Save in cache-deleted state removes committed/ (no-cache promotion).
  6. Cross-restart vulnerability is closed (CACHEING_ACCESS_MODEL.md pattern).
  7. No-op trapdoors (cache fingerprint match; save fingerprint match).
  8. Read-precedence at execute time (emitter prefers working/ else committed/).

Setup pattern: each test starts from a fresh project laid out by
``_setup_project`` (one apiInput JSON node, one cached data file). State
between tests is reset by the autouse ``_clear_dual_cache_session``
fixture in conftest.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from haute._json_flatten import (
    _LAYER_COMMITTED,
    _LAYER_WORKING,
    _clear_session,
    _committed_cache_data_path,
    _is_working_consulted,
    _json_cache_dir,
    _schema_fingerprint,
    _working_cache_data_path,
    build_json_cache,
    mirror_cache_to_committed,
)


# ---------------------------------------------------------------------------
# Project fixture
# ---------------------------------------------------------------------------


_SCHEMA_S1: dict[str, str] = {"quote_id": "str", "premium": "float"}
_SCHEMA_S2: dict[str, object] = {
    "quote_id": "str",
    "premium": "float",
    "extra": "str",
}


def _write_data_file(path: Path) -> None:
    """Write a small JSONL file with fields covering both S1 and S2 schemas."""
    path.write_text(
        '\n'.join(
            json.dumps(rec)
            for rec in (
                {"quote_id": "q-1", "premium": 100.0, "extra": "alpha"},
                {"quote_id": "q-2", "premium": 200.5, "extra": "beta"},
            )
        )
        + "\n",
        encoding="utf-8",
    )


def _setup_project(root: Path, *, schema: dict[str, object]) -> dict[str, object]:
    """Create a minimal project under *root* with one apiInput node.

    Returns the resolved data and config paths, plus the schema used.
    """
    pipeline_dir_path = root / "rating"
    pipeline_dir_path.mkdir(parents=True, exist_ok=True)
    config_dir = pipeline_dir_path / "config" / "quote_input"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir = root / "data" / "quotes"
    data_dir.mkdir(parents=True, exist_ok=True)

    data_file = data_dir / "sample_quote.jsonl"
    _write_data_file(data_file)

    quotes_config = config_dir / "quotes.json"
    quotes_config.write_text(
        json.dumps({"path": "data/quotes/sample_quote.jsonl", "flattenSchema": schema}),
        encoding="utf-8",
    )

    pipeline_file = pipeline_dir_path / "main.py"
    pipeline_file.write_text(
        textwrap.dedent(
            '''\
            """Pipeline: regression"""

            import polars as pl
            import haute

            pipeline = haute.Pipeline("regression")


            @pipeline.api_input(
                config="config/quote_input/quotes.json",
                contract="opaque",
            )
            def quotes() -> pl.LazyFrame:
                """quotes node"""
                from pathlib import Path
                from haute._json_flatten import read_json_flat
                return read_json_flat(
                    Path(__file__).parent.parent
                    / "data/quotes/sample_quote.jsonl",
                    config_path="config/quote_input/quotes.json",
                )
            '''
        ),
        encoding="utf-8",
    )

    haute_toml = root / "haute.toml"
    haute_toml.write_text(
        textwrap.dedent(
            """\
            [project]
            name = "regression"
            pipeline = "rating/main.py"
            """
        ),
        encoding="utf-8",
    )

    return {
        "data_file": data_file,
        "data_rel": "data/quotes/sample_quote.jsonl",
        "config_file": quotes_config,
        "config_rel": "config/quote_input/quotes.json",
        "pipeline_file": pipeline_file,
        "schema": schema,
    }


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Fresh project + isolated cache dir for each test."""
    monkeypatch.chdir(tmp_path)
    info = _setup_project(tmp_path, schema=_SCHEMA_S1)

    from haute.routes._helpers import pipeline_dir as _pd

    _pd.cache_clear()
    return info


@pytest.fixture()
def client(project: dict[str, object]) -> TestClient:
    """A TestClient pointed at the project fixture's pipeline."""
    from haute.server import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api_cache_build(client: TestClient, data_rel: str, schema: dict | None) -> dict:
    body: dict[str, object] = {"path": data_rel}
    if schema is not None:
        body["flatten_schema"] = schema
    resp = client.post("/api/json-cache/build", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _api_save(client: TestClient) -> dict:
    """Issue POST /api/pipeline/save for the project pipeline.

    Builds the graph payload by fetching the current parsed graph and
    re-submitting it. Mirrors the frontend's save flow.
    """
    graph_resp = client.get("/api/pipeline")
    assert graph_resp.status_code == 200, graph_resp.text
    graph = graph_resp.json()
    body = {
        "graph": graph,
        "sources": graph.get("sources") or ["live"],
        "active_source": graph.get("active_source") or "live",
        "source_file": "rating/main.py",
        "name": "regression",
        "description": graph.get("description") or "regression",
    }
    resp = client.post("/api/pipeline/save", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _api_cache_delete(client: TestClient, data_rel: str) -> dict:
    resp = client.delete(f"/api/json-cache?path={data_rel}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _api_cache_status(
    client: TestClient,
    data_rel: str,
    *,
    schema: dict | None = None,
) -> dict:
    body: dict[str, object] = {"path": data_rel}
    if schema is not None:
        body["flatten_schema"] = schema
    resp = client.post("/api/json-cache/status", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _read_meta(cache_dir: Path) -> dict | None:
    meta_path = cache_dir / "meta.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _read_parquet_metadata(parquet_path: Path) -> dict[bytes, bytes]:
    md = pq.read_metadata(str(parquet_path)).metadata
    return dict(md) if md else {}


def _set_schema_in_config(config_file: Path, schema: dict) -> None:
    """Rewrite the on-disk apiInput config to use *schema*."""
    cfg = json.loads(config_file.read_text(encoding="utf-8"))
    cfg["flattenSchema"] = schema
    config_file.write_text(json.dumps(cfg), encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: SET-UP
# ---------------------------------------------------------------------------


class TestSetUp:
    """After cache + save the two layers are in sync with the in-memory view."""

    def test_known_state_after_cache_and_save(
        self,
        client: TestClient,
        project: dict[str, object],
    ) -> None:
        data_file: Path = project["data_file"]
        schema = project["schema"]

        # Step 1+2: cache the data.
        result = _api_cache_build(client, project["data_rel"], schema)
        assert result["row_count"] == 2

        # Step 3: save.
        _api_save(client)

        working = _json_cache_dir(data_file, _LAYER_WORKING)
        committed = _json_cache_dir(data_file, _LAYER_COMMITTED)
        assert (working / "data.parquet").exists()
        assert (committed / "data.parquet").exists()

        # Fingerprints in meta.json match the resolved schema.
        wmeta = _read_meta(working)
        cmeta = _read_meta(committed)
        expected_fp = _schema_fingerprint(schema)
        assert wmeta is not None and wmeta["schema_fingerprint"] == expected_fp
        assert cmeta is not None and cmeta["schema_fingerprint"] == expected_fp

        # Parquet KV-metadata carries the schema for single-file robustness.
        pq_meta_w = _read_parquet_metadata(working / "data.parquet")
        pq_meta_c = _read_parquet_metadata(committed / "data.parquet")
        assert b"haute.flatten_schema" in pq_meta_w
        assert b"haute.flatten_schema" in pq_meta_c
        assert (
            json.loads(pq_meta_w[b"haute.flatten_schema"].decode())
            == json.loads(pq_meta_c[b"haute.flatten_schema"].decode())
            == schema
        )

        # Working and committed parquets are byte-identical after save (mirror).
        assert (working / "data.parquet").read_bytes() == (
            committed / "data.parquet"
        ).read_bytes()


# ---------------------------------------------------------------------------
# Test 2: volatile changes with schema; stable unchanged
# ---------------------------------------------------------------------------


class TestVolatileTracksSchemaChanges:
    """Cache after schema edit produces a working/ that diverges from committed/."""

    def test_volatile_reflects_in_memory_stable_reflects_save(
        self,
        client: TestClient,
        project: dict[str, object],
        tmp_path: Path,
    ) -> None:
        data_file: Path = project["data_file"]
        config_file: Path = project["config_file"]
        schema_s1 = project["schema"]

        # Bring the project to the post-save baseline (test 1's terminal state).
        _api_cache_build(client, project["data_rel"], schema_s1)
        _api_save(client)

        committed = _json_cache_dir(data_file, _LAYER_COMMITTED)
        working = _json_cache_dir(data_file, _LAYER_WORKING)

        # Snapshot the committed layer to a scratch dir for later comparison.
        scratch = tmp_path / "scratch_stable"
        shutil.copytree(committed, scratch)

        # Schema edit (simulating an in-session edit).
        _set_schema_in_config(config_file, _SCHEMA_S2)
        _api_cache_build(client, project["data_rel"], _SCHEMA_S2)

        # ASSERT (2.4) — stable cache agrees with the snapshot (last-save state).
        assert (committed / "data.parquet").read_bytes() == (
            scratch / "data.parquet"
        ).read_bytes()
        assert _read_meta(committed) == _read_meta(scratch)

        # ASSERT (2.5) — volatile cache metadata fingerprint == in-memory S2 fingerprint.
        wmeta = _read_meta(working)
        assert wmeta is not None
        assert wmeta["schema_fingerprint"] == _schema_fingerprint(_SCHEMA_S2)

        # ASSERT (2.6) — parquet footer describes the shape of the cached data.
        pq_meta = _read_parquet_metadata(working / "data.parquet")
        assert b"haute.flatten_schema" in pq_meta
        embedded = json.loads(pq_meta[b"haute.flatten_schema"].decode())
        assert embedded == _SCHEMA_S2
        # And the parquet columns match the schema's declared leaves.
        cols = pl.scan_parquet(working / "data.parquet").collect_schema().names()
        assert set(cols) == set(_SCHEMA_S2.keys())

        # ASSERT (2.7) — volatile and stable disagree.
        assert _read_meta(working) != _read_meta(committed)
        assert (working / "data.parquet").read_bytes() != (
            committed / "data.parquet"
        ).read_bytes()


# ---------------------------------------------------------------------------
# Test 3: save mirrors working → committed without changing working
# ---------------------------------------------------------------------------


class TestSaveMirrors:
    """Save copies working/ into committed/ byte-for-byte; working/ unchanged."""

    def test_save_synchronises_committed_to_working(
        self,
        client: TestClient,
        project: dict[str, object],
        tmp_path: Path,
    ) -> None:
        data_file: Path = project["data_file"]
        config_file: Path = project["config_file"]

        _api_cache_build(client, project["data_rel"], project["schema"])
        _api_save(client)
        _set_schema_in_config(config_file, _SCHEMA_S2)
        _api_cache_build(client, project["data_rel"], _SCHEMA_S2)

        working = _json_cache_dir(data_file, _LAYER_WORKING)
        committed = _json_cache_dir(data_file, _LAYER_COMMITTED)

        # Snapshot the working layer to a scratch dir before save.
        scratch_volatile = tmp_path / "scratch_volatile"
        shutil.copytree(working, scratch_volatile)

        _api_save(client)

        # ASSERT (3.3) — working unchanged.
        assert (working / "data.parquet").read_bytes() == (
            scratch_volatile / "data.parquet"
        ).read_bytes()
        assert _read_meta(working) == _read_meta(scratch_volatile)

        # ASSERT (3.4) — committed bytes-equal to working.
        assert (working / "data.parquet").read_bytes() == (
            committed / "data.parquet"
        ).read_bytes()
        assert _read_meta(working) == _read_meta(committed)


# ---------------------------------------------------------------------------
# Test 4: delete only affects volatile
# ---------------------------------------------------------------------------


class TestDeleteOnlyAffectsVolatile:
    """The DELETE endpoint removes working/ only; committed/ is untouched."""

    def test_delete_removes_working_only(
        self,
        client: TestClient,
        project: dict[str, object],
        tmp_path: Path,
    ) -> None:
        data_file: Path = project["data_file"]
        config_file: Path = project["config_file"]

        _api_cache_build(client, project["data_rel"], project["schema"])
        _api_save(client)
        _set_schema_in_config(config_file, _SCHEMA_S2)
        _api_cache_build(client, project["data_rel"], _SCHEMA_S2)
        _api_save(client)  # so both layers reflect S2

        working = _json_cache_dir(data_file, _LAYER_WORKING)
        committed = _json_cache_dir(data_file, _LAYER_COMMITTED)

        # Snapshot committed/ before deletion for the assert.
        scratch = tmp_path / "scratch_stable"
        shutil.copytree(committed, scratch)

        _api_cache_delete(client, project["data_rel"])

        # ASSERT (4.2) — working/ does not exist (directory absent).
        assert not working.exists()
        # ASSERT (4.3) — committed/ unchanged.
        assert (committed / "data.parquet").read_bytes() == (
            scratch / "data.parquet"
        ).read_bytes()
        assert _read_meta(committed) == _read_meta(scratch)


# ---------------------------------------------------------------------------
# Test 5: save in cache-deleted state removes committed/
# ---------------------------------------------------------------------------


class TestSaveAfterDeletePromotesAbsence:
    """Save after delete promotes "no cache" — committed/ becomes empty too."""

    def test_save_after_delete_removes_committed(
        self,
        client: TestClient,
        project: dict[str, object],
    ) -> None:
        data_file: Path = project["data_file"]

        _api_cache_build(client, project["data_rel"], project["schema"])
        _api_save(client)

        committed = _json_cache_dir(data_file, _LAYER_COMMITTED)
        working = _json_cache_dir(data_file, _LAYER_WORKING)
        assert _is_working_consulted(str(data_file))

        _api_cache_delete(client, project["data_rel"])

        # Delete clears working/ but preserves the consulted flag — the
        # user is still in the same process and remains authoritative for
        # this data file. So the next save sees "consulted=True, working
        # absent" and propagates the absence to committed/.
        assert _is_working_consulted(str(data_file))
        assert not working.exists()
        assert committed.exists()  # delete didn't touch committed/

        _api_save(client)

        # ASSERT (5.2) — committed/ removed (save mirrored the absence).
        assert not committed.exists(), (
            "Save should propagate the cache-deleted state to committed/"
        )
        # ASSERT (5.3) — working/ still absent.
        assert not working.exists()


# ---------------------------------------------------------------------------
# Test 6: vulnerability-pattern regression (cross-restart)
# ---------------------------------------------------------------------------


class TestCrossRestartVulnerability:
    """CACHEING_ACCESS_MODEL.md vulnerability is closed by the dual-cache split."""

    def test_restart_ignores_working_falls_through_to_committed(
        self,
        client: TestClient,
        project: dict[str, object],
    ) -> None:
        data_file: Path = project["data_file"]
        config_file: Path = project["config_file"]

        # Bring the project to a baseline: both layers reflect S1.
        _api_cache_build(client, project["data_rel"], project["schema"])
        _api_save(client)
        assert _is_working_consulted(str(data_file))

        # Edit schema in-memory to S2, re-cache. Working/ now has S2.
        # Committed/ still has S1.
        _set_schema_in_config(config_file, _SCHEMA_S2)
        _api_cache_build(client, project["data_rel"], _SCHEMA_S2)

        working = _json_cache_dir(data_file, _LAYER_WORKING)
        committed = _json_cache_dir(data_file, _LAYER_COMMITTED)
        assert b"haute.flatten_schema" in _read_parquet_metadata(working / "data.parquet")
        assert _read_meta(working)["schema_fingerprint"] == _schema_fingerprint(_SCHEMA_S2)
        assert _read_meta(committed)["schema_fingerprint"] == _schema_fingerprint(
            project["schema"]
        )

        # Simulate a server restart: working/ on disk survives, but the
        # process-local consulted set is cleared.
        _clear_session()
        assert not _is_working_consulted(str(data_file))

        # ASSERT (7) — status reflects working/ (not consulted, reports cached=False).
        # The on-disk schema mapping JSON would have to be reverted to S1 for the
        # editor to show S1; the JSON file currently reflects whatever's in
        # config_file. The test simulates the editor reading the saved schema
        # by querying status with S1, which is what the editor would do after a
        # restart (it reloads the saved on-disk config).
        _set_schema_in_config(config_file, project["schema"])
        status = _api_cache_status(client, project["data_rel"], schema=project["schema"])
        assert status["cached"] is True, (
            "status with S1 schema should hit committed/ post-restart"
        )
        # Status's cache path is in committed/, not working/.
        assert _LAYER_COMMITTED in status["path"], status["path"]

        # ASSERT (8) — committed/<hash>/data.parquet still has S1.
        committed_meta = _read_meta(committed)
        assert committed_meta["schema_fingerprint"] == _schema_fingerprint(project["schema"])

        # ASSERT (9) — working/<hash>/data.parquet still exists on disk (lost
        # work preserved for potential future recovery), but with S2 contents,
        # unreachable by the emitter post-restart.
        assert (working / "data.parquet").exists()
        embedded = json.loads(
            _read_parquet_metadata(working / "data.parquet")[
                b"haute.flatten_schema"
            ].decode()
        )
        assert embedded == _SCHEMA_S2


# ---------------------------------------------------------------------------
# Test 7: no-op trapdoors
# ---------------------------------------------------------------------------


class TestNoOpTrapdoors:
    """Cache trapdoor (working/) and save trapdoor (committed/) skip rebuilds."""

    def test_cache_noop_when_fingerprint_matches(
        self,
        client: TestClient,
        project: dict[str, object],
    ) -> None:
        data_file: Path = project["data_file"]

        _api_cache_build(client, project["data_rel"], project["schema"])
        working = _json_cache_dir(data_file, _LAYER_WORKING)
        first_mtime = (working / "data.parquet").stat().st_mtime

        # Re-cache with same schema → no rebuild, mtime unchanged.
        # The trapdoor uses fingerprint equality, not byte equality, so a
        # rebuild would touch mtime even if the bytes were identical.
        _api_cache_build(client, project["data_rel"], project["schema"])
        assert (working / "data.parquet").stat().st_mtime == first_mtime

        # Change schema → rebuild fires, mtime advances.
        _api_cache_build(client, project["data_rel"], _SCHEMA_S2)
        assert (working / "data.parquet").stat().st_mtime > first_mtime

    def test_save_noop_when_layers_already_in_sync(
        self,
        client: TestClient,
        project: dict[str, object],
        tmp_path: Path,
    ) -> None:
        data_file: Path = project["data_file"]
        config_file: Path = project["config_file"]

        _api_cache_build(client, project["data_rel"], project["schema"])
        _api_save(client)
        committed = _json_cache_dir(data_file, _LAYER_COMMITTED)
        first_mtime = (committed / "data.parquet").stat().st_mtime

        # Re-save with no edits in between → cache portion is a no-op.
        _api_save(client)
        assert (committed / "data.parquet").stat().st_mtime == first_mtime

        # Edit schema then re-cache then save → committed/ rebuilt (via mirror).
        _set_schema_in_config(config_file, _SCHEMA_S2)
        _api_cache_build(client, project["data_rel"], _SCHEMA_S2)
        _api_save(client)
        assert (committed / "data.parquet").stat().st_mtime > first_mtime


# ---------------------------------------------------------------------------
# Test 8: read precedence at execute time
# ---------------------------------------------------------------------------


class TestReadPrecedence:
    """The emitter prefers working/ if consulted; else falls through to committed/."""

    def test_emitter_reads_working_then_committed_then_raises(
        self,
        client: TestClient,
        project: dict[str, object],
    ) -> None:
        from haute._json_flatten import cache_layer_if_valid

        data_file: Path = project["data_file"]
        config_file: Path = project["config_file"]

        # Both layers populated with S1.
        _api_cache_build(client, project["data_rel"], project["schema"])
        _api_save(client)

        result = cache_layer_if_valid(str(data_file), schema=project["schema"])
        assert result is not None
        _, layer = result
        # Working/ is preferred when consulted.
        assert layer == _LAYER_WORKING

        # Re-cache S2 with the new schema persisted on disk. Working/ has S2,
        # committed/ still has S1.
        _set_schema_in_config(config_file, _SCHEMA_S2)
        _api_cache_build(client, project["data_rel"], _SCHEMA_S2)

        # Emitter for S2 → working/ (which has S2).
        result_s2 = cache_layer_if_valid(str(data_file), schema=_SCHEMA_S2)
        assert result_s2 is not None
        assert result_s2[1] == _LAYER_WORKING
        assert result_s2[0] == _working_cache_data_path(data_file)

        # Emitter for S1 → working/ S2 fingerprint mismatch, falls through to committed/.
        result_s1 = cache_layer_if_valid(str(data_file), schema=project["schema"])
        assert result_s1 is not None
        assert result_s1[1] == _LAYER_COMMITTED
        assert result_s1[0] == _committed_cache_data_path(data_file)

        # Delete working/ → emitter falls through to committed/ for S1.
        # Consulted flag persists across delete; precedence still checks
        # working/ first (now invalid) before falling through.
        _api_cache_delete(client, project["data_rel"])
        result_after = cache_layer_if_valid(str(data_file), schema=project["schema"])
        assert result_after is not None
        assert result_after[1] == _LAYER_COMMITTED
        # S2 has no valid layer (committed/ has S1, working/ is gone).
        assert cache_layer_if_valid(str(data_file), schema=_SCHEMA_S2) is None

        # Save with deleted cache → committed/ also removed (test 5).
        _api_save(client)
        # Now neither layer is valid for any schema.
        assert cache_layer_if_valid(str(data_file), schema=project["schema"]) is None
        assert cache_layer_if_valid(str(data_file), schema=_SCHEMA_S2) is None


# ---------------------------------------------------------------------------
# Direct invariants on the mirror function (not requiring a TestClient)
# ---------------------------------------------------------------------------


class TestMirrorDirectInvariants:
    """Direct tests on mirror_cache_to_committed for tricky edge cases."""

    def test_mirror_noop_when_consulted_set_does_not_contain_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stale on-disk working/ from a previous session is NOT promoted."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        _write_data_file(data_file)

        # Pretend a previous session left a working/ behind (no session mark).
        cache_dir = _json_cache_dir(str(data_file), _LAYER_WORKING)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "data.parquet").write_bytes(b"stale")
        (cache_dir / "meta.json").write_text(
            json.dumps({"schema_mode": "inferred", "schema_fingerprint": "deadbeef"})
        )
        assert not _is_working_consulted(str(data_file))

        changed = mirror_cache_to_committed(str(data_file))

        assert changed is False
        # Committed/ stays absent — stale working/ wasn't promoted.
        assert not _json_cache_dir(str(data_file), _LAYER_COMMITTED).exists()

    def test_mirror_no_op_when_fingerprints_match(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        _write_data_file(data_file)
        build_json_cache(str(data_file), schema=_SCHEMA_S1)
        mirror_cache_to_committed(str(data_file))

        committed = _json_cache_dir(str(data_file), _LAYER_COMMITTED)
        first_mtime = (committed / "data.parquet").stat().st_mtime

        changed = mirror_cache_to_committed(str(data_file))
        assert changed is False
        assert (committed / "data.parquet").stat().st_mtime == first_mtime

    def test_mirror_removes_committed_when_working_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        _write_data_file(data_file)
        build_json_cache(str(data_file), schema=_SCHEMA_S1)
        mirror_cache_to_committed(str(data_file))

        committed = _json_cache_dir(str(data_file), _LAYER_COMMITTED)
        working = _json_cache_dir(str(data_file), _LAYER_WORKING)
        assert committed.exists() and working.exists()
        assert _is_working_consulted(str(data_file))

        # Simulate the DELETE endpoint: working/ removed, consulted preserved.
        shutil.rmtree(working)

        changed = mirror_cache_to_committed(str(data_file))
        assert changed is True
        assert not committed.exists()
