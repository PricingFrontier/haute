"""C4 — preview/trace cache keys must cover runtime file inputs.

Re-exporting a dataSource CSV, replacing an external file or a model
artifact, and rebuilding the apiInput JSON cache all happen out-of-band
(there is no in-GUI upload), so the graph JSON — and therefore the
structural fingerprint — does not change.  Before the fix the preview
and trace caches kept serving months-stale frames with zero indication.

These tests pin:

* preview recomputes after a dataSource file overwrite, an external
  file change, and a model-artifact replacement (file-sourced
  optimiserApply artifact — the cheapest real model fixture);
* a vanished dataSource file surfaces the execution error instead of a
  stale ok frame;
* trace recomputes after a dataSource edit and after a JSON-cache
  rebuild (the trace key previously omitted the JSON-cache state
  signature entirely);
* the stat-gated memo: unchanged files are content-hashed exactly once
  across previews (call-count pin, not timing), edited files re-hash;
* the deliberate stat-gate semantics: an mtime bump with identical
  bytes recomputes (mtime is digest material, matching the sink path's
  ``_runtime_path_fingerprint``); a byte-identical rewrite whose stat
  is fully restored is invisible — correct, because the bytes are equal;
* trace still reuses preview-cache entries when file signatures are in
  both keys (executor/trace key construction cannot drift).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import polars as pl
import pytest

from haute._json_flatten import _json_cache_dir, cache_state_signature_for_graph
from haute.executor import _preview_cache, execute_graph
from haute.trace import _cache as _trace_cache
from haute.trace import execute_trace
from tests.conftest import (
    make_edge as _edge,
)
from tests.conftest import (
    make_graph as _g,
)
from tests.conftest import (
    make_node as _n,
)
from tests.conftest import (
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Preview/trace caches are process singletons — isolate every test."""
    _preview_cache.invalidate()
    _trace_cache.invalidate()
    yield
    _preview_cache.invalidate()
    _trace_cache.invalidate()


def _write_csv(path: Path, values: list[int]) -> None:
    pl.DataFrame({"x": values}).write_csv(path)


def _bump_mtime(path: Path, seconds: float = 5.0) -> None:
    """Deterministically advance *path*'s mtime.

    Two writes inside the same timestamp granule are below the stat
    gate's resolution; a real out-of-band re-export always lands later.
    The explicit bump keeps the tests deterministic on coarse-mtime
    filesystems.
    """
    stat = path.stat()
    os.utime(path, (stat.st_atime + seconds, stat.st_mtime + seconds))


def _csv_graph(path: Path):
    return _g({"nodes": [_source_node("src", str(path))], "edges": []})


def _flat_api_input_node(nid: str, path: Path):
    """apiInput with a non-JSON path — the builder dispatches it to
    ``read_data_source``, so preview reads the raw flat file directly."""
    return _n(
        {
            "id": nid,
            "data": {"label": nid, "nodeType": "apiInput", "config": {"path": str(path)}},
        }
    )


def _databricks_graph(table: str):
    return _g(
        {
            "nodes": [
                _n(
                    {
                        "id": "db",
                        "data": {
                            "label": "db",
                            "nodeType": "dataSource",
                            "config": {"sourceType": "databricks", "table": table},
                        },
                    }
                ),
            ],
            "edges": [],
        }
    )


_V2_AMOUNT_TABLES = {
    "tables": [
        {
            "path": "$[*]",
            "label": "root",
            "emit": True,
            "row_id_column": None,
            "columns": [
                {
                    "name": "amount",
                    "path": "$[*].amount",
                    "type": "int",
                    "status": "Confirmed",
                    "selected": True,
                    "levels": None,
                },
            ],
        },
    ],
}


def _export_and_cache_amount(data: Path, amount: int) -> None:
    """Write data.json with one record and (re)build its working-layer cache."""
    from haute._json_shred import build_per_port_cache

    data.write_text(json.dumps([{"amount": amount}]), encoding="utf-8")
    build_per_port_cache(str(data), _V2_AMOUNT_TABLES, _json_cache_dir(str(data), "working"))


def _json_api_input_graph(data: Path):
    return _g(
        {
            "nodes": [
                _n(
                    {
                        "id": "api",
                        "data": {
                            "label": "api",
                            "nodeType": "apiInput",
                            "config": {"path": str(data), **_V2_AMOUNT_TABLES},
                        },
                    }
                ),
                _transform_node("t", "df = df.with_columns(y=pl.col('amount') * 2)"),
            ],
            "edges": [_edge("api", "t")],
        }
    )


# ---------------------------------------------------------------------------
# Preview: runtime file inputs invalidate the preview cache
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestPreviewRuntimeFileInvalidation:
    def test_datasource_csv_overwrite_recomputes_preview(self, tmp_path):
        """Re-exporting data.csv (same path, new rows) must not serve stale rows."""
        p = tmp_path / "data.csv"
        _write_csv(p, [1, 2])
        graph = _csv_graph(p)

        results1 = execute_graph(graph)
        assert [row["x"] for row in results1["src"].preview] == [1, 2]

        _write_csv(p, [5, 6])
        _bump_mtime(p)

        results2 = execute_graph(graph)
        assert [row["x"] for row in results2["src"].preview] == [5, 6]

    def test_external_file_change_recomputes_preview(self, tmp_path):
        """Replacing an externalFile's content must flow into the next preview."""
        data = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(data)
        factor = tmp_path / "factor.json"
        factor.write_text(json.dumps({"factor": 2}), encoding="utf-8")

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(data)),
                    _n(
                        {
                            "id": "ext",
                            "data": {
                                "label": "ext",
                                "nodeType": "externalFile",
                                "config": {
                                    "path": str(factor),
                                    "fileType": "json",
                                    "code": "df = df.with_columns(y=pl.lit(obj['factor']))",
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "ext")],
            }
        )

        results1 = execute_graph(graph, target_node_id="ext")
        assert results1["ext"].preview[0]["y"] == 2

        factor.write_text(json.dumps({"factor": 3}), encoding="utf-8")
        _bump_mtime(factor)

        results2 = execute_graph(graph, target_node_id="ext")
        assert results2["ext"].preview[0]["y"] == 3

    def test_model_artifact_replacement_recomputes_preview(self, tmp_path):
        """Replacing a model artifact out-of-band must invalidate the preview.

        Uses a file-sourced optimiserApply ratebook artifact — the
        cheapest real model fixture in the repo (pure factor lookup,
        no solver arithmetic).
        """
        scored = tmp_path / "scored.parquet"
        pl.DataFrame({"region": ["London", "Manchester"]}).write_parquet(scored)
        artifact = tmp_path / "ratebook.json"

        def _artifact(london_value: float) -> dict:
            return {
                "version": "rb_v1",
                "mode": "ratebook",
                "lambdas": {},
                "objective": "predicted_income",
                "constraints": {},
                "factor_tables": {
                    "region": [
                        {"__factor_group__": "London", "optimal_scenario_value": london_value},
                        {"__factor_group__": "Manchester", "optimal_scenario_value": 0.98},
                    ],
                },
            }

        artifact.write_text(json.dumps(_artifact(1.05)), encoding="utf-8")

        graph = _g(
            {
                "nodes": [
                    _source_node("scored", str(scored)),
                    _n(
                        {
                            "id": "apply",
                            "data": {
                                "label": "apply",
                                "nodeType": "optimiserApply",
                                "config": {
                                    "sourceType": "file",
                                    "artifact_path": str(artifact),
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("scored", "apply")],
            }
        )

        results1 = execute_graph(graph, target_node_id="apply")
        assert results1["apply"].preview[0]["region_optimised_factor"] == pytest.approx(1.05)

        artifact.write_text(json.dumps(_artifact(2.5)), encoding="utf-8")
        _bump_mtime(artifact)

        results2 = execute_graph(graph, target_node_id="apply")
        assert results2["apply"].preview[0]["region_optimised_factor"] == pytest.approx(2.5)

    def test_vanished_datasource_file_errors_loudly(self, tmp_path):
        """A deleted dataSource file must error like execution does — never
        serve the stale ok frame cached while the file still existed."""
        p = tmp_path / "data.csv"
        _write_csv(p, [1, 2])
        graph = _csv_graph(p)

        results1 = execute_graph(graph)
        assert results1["src"].status == "ok"

        p.unlink()

        results2 = execute_graph(graph)
        assert results2["src"].status == "error"
        assert results2["src"].error

    def test_flat_file_api_input_reexport_recomputes_preview(self, tmp_path):
        """A non-JSON apiInput reads the raw flat file at preview (no JSON
        cache layer exists for it), so a re-export must invalidate exactly
        like a dataSource file — the json_cache= extra alone is a constant
        ``<path-hash>:0:0`` for this shape and catches nothing."""
        p = tmp_path / "quotes.csv"
        _write_csv(p, [1, 2])
        graph = _g({"nodes": [_flat_api_input_node("api", p)], "edges": []})

        results1 = execute_graph(graph)
        assert [row["x"] for row in results1["api"].preview] == [1, 2]

        _write_csv(p, [5, 6])
        _bump_mtime(p)

        results2 = execute_graph(graph)
        assert [row["x"] for row in results2["api"].preview] == [5, 6]

    def test_databricks_fetch_data_rewrite_recomputes_preview(self, tmp_path, monkeypatch):
        """Preview consumes the LOCAL table-cache parquet that the GUI
        Fetch Data route rewrites in place.  Both lifecycle transitions
        must invalidate: the cached not-fetched error clears once the
        first fetch lands, and a re-fetch replaces stale rows."""
        from haute._databricks_io import _cache_path_for

        monkeypatch.chdir(tmp_path)  # the table cache lives under cwd/.haute_cache
        graph = _databricks_graph("cat.sch.tbl")

        results0 = execute_graph(graph)
        assert results0["db"].status == "error"  # not fetched yet

        cache_path = _cache_path_for("cat.sch.tbl")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1, 2]}).write_parquet(cache_path)

        results1 = execute_graph(graph)
        assert results1["db"].status == "ok"
        assert [row["x"] for row in results1["db"].preview] == [1, 2]

        pl.DataFrame({"x": [7, 8]}).write_parquet(cache_path)
        _bump_mtime(cache_path)

        results2 = execute_graph(graph)
        assert [row["x"] for row in results2["db"].preview] == [7, 8]


# ---------------------------------------------------------------------------
# Trace: runtime file inputs + JSON-cache state invalidate the trace cache
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestTraceRuntimeInputInvalidation:
    def test_trace_recomputes_after_datasource_edit(self, tmp_path):
        p = tmp_path / "data.csv"
        _write_csv(p, [10])
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result1 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result1.output_value == 20

        _write_csv(p, [50])
        _bump_mtime(p)

        result2 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result2.output_value == 100

    def test_flat_file_api_input_reexport_recomputes_trace(self, tmp_path):
        p = tmp_path / "quotes.csv"
        _write_csv(p, [10])
        graph = _g(
            {
                "nodes": [
                    _flat_api_input_node("api", p),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("api", "t")],
            }
        )

        result1 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result1.output_value == 20

        _write_csv(p, [50])
        _bump_mtime(p)

        result2 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result2.output_value == 100

    def test_trace_recomputes_after_json_cache_rebuild(self, tmp_path, monkeypatch):
        """Rebuilding the apiInput JSON cache must invalidate the trace cache.

        The trace key previously omitted the JSON-cache state signature,
        so a rebuild with fresh data kept serving the old trace.
        """
        monkeypatch.chdir(tmp_path)  # .haute_cache/ lives under cwd

        data = tmp_path / "data.json"
        _export_and_cache_amount(data, 10)
        graph = _json_api_input_graph(data)

        result1 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result1.output_value == 20

        _export_and_cache_amount(data, 50)
        # ms-precision meta.json mtimes are the signature's clock; advance it
        # deterministically (a rebuild within the same ms granule would tie).
        meta = _json_cache_dir(str(data), "working") / "meta.json"
        _bump_mtime(meta)

        result2 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result2.output_value == 100

    def test_trace_reuses_preview_entry_with_file_signature_present(self, tmp_path, monkeypatch):
        """Executor and trace must build identical preview keys — including
        the runtime file-signature extras — or trace silently loses its
        preview reuse and re-executes the DAG on every cold trace."""
        import haute.trace as trace_mod

        p = tmp_path / "data.csv"
        _write_csv(p, [7])
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        preview = execute_graph(graph, target_node_id="t", row_limit=1000)
        assert preview["t"].status == "ok"

        def forbidden_cold_execute(*args, **kwargs):
            raise AssertionError("trace must reuse the preview entry, not re-execute")

        monkeypatch.setattr(trace_mod, "_execute_eager_core", forbidden_cold_execute)

        result = execute_trace(
            graph,
            row_index=0,
            target_node_id="t",
            column="y",
            preview=_preview_cache,
        )
        assert result.output_value == 14

    def test_trace_reuses_preview_entry_for_json_api_input_graph(self, tmp_path, monkeypatch):
        """Hit-side pin for apiInput graphs: trace's preview-key
        reconstruction includes the json_cache= extra, so a trace right
        after a preview reuses the materialised frames.  Before the C4
        wiring the reconstruction omitted the signature and apiInput-graph
        traces always re-executed the DAG."""
        import haute.trace as trace_mod

        monkeypatch.chdir(tmp_path)

        data = tmp_path / "data.json"
        _export_and_cache_amount(data, 7)
        graph = _json_api_input_graph(data)

        preview = execute_graph(graph, target_node_id="t", row_limit=1000)
        assert preview["t"].status == "ok"

        def forbidden_cold_execute(*args, **kwargs):
            raise AssertionError("apiInput-graph trace must reuse the preview entry")

        monkeypatch.setattr(trace_mod, "_execute_eager_core", forbidden_cold_execute)

        result = execute_trace(
            graph,
            row_index=0,
            target_node_id="t",
            column="y",
            preview=_preview_cache,
        )
        assert result.output_value == 14


# ---------------------------------------------------------------------------
# Stat-gated memo: perf property (call counts, not timing) + pinned semantics
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestStatGatedFingerprintMemo:
    @pytest.fixture()
    def hash_calls(self, monkeypatch):
        """Count content hashes issued by the runtime-input signature layer."""
        import haute.execution as execution_mod

        calls: dict[str, int] = {}
        real_content_hash = execution_mod.content_hash

        def counting_content_hash(path):
            key = str(path)
            calls[key] = calls.get(key, 0) + 1
            return real_content_hash(path)

        monkeypatch.setattr(execution_mod, "content_hash", counting_content_hash)
        return calls

    def test_unchanged_file_is_hashed_once_across_previews(self, tmp_path, hash_calls):
        p = tmp_path / "data.csv"
        _write_csv(p, [1, 2])
        graph = _csv_graph(p)
        key = str(p.resolve())

        execute_graph(graph)
        assert hash_calls.get(key) == 1, "first preview must content-hash the dataSource"

        execute_graph(graph)
        assert hash_calls.get(key) == 1, "unchanged stat must not re-hash content"

        _write_csv(p, [5, 6])
        _bump_mtime(p)

        execute_graph(graph)
        assert hash_calls.get(key) == 2, "a changed stat must re-hash content"

    def test_touch_with_identical_bytes_recomputes(self, tmp_path):
        """Pinned semantics: mtime is digest material (like the sink path's
        ``_runtime_path_fingerprint``), so a touch recomputes even though
        the bytes are unchanged.  Deliberate: simplest correct stat-gate."""
        p = tmp_path / "data.csv"
        _write_csv(p, [1, 2])
        graph = _csv_graph(p)

        execute_graph(graph)
        fp_before = _preview_cache.fingerprint
        assert fp_before is not None

        _bump_mtime(p)

        execute_graph(graph)
        fp_after = _preview_cache.fingerprint
        assert fp_after != fp_before, "mtime change must produce a new preview cache key"

    def test_stat_identical_noop_rewrite_serves_cached_preview(self, tmp_path, hash_calls):
        """Pinned semantics: a rewrite that restores both bytes and stat is
        below the stat gate's resolution and serves the cached entry —
        correct, because the bytes are identical."""
        p = tmp_path / "data.csv"
        _write_csv(p, [1, 2])
        graph = _csv_graph(p)
        key = str(p.resolve())

        results1 = execute_graph(graph)
        fp_before = _preview_cache.fingerprint
        stat = p.stat()

        _write_csv(p, [1, 2])  # identical bytes
        os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # restore stat exactly

        results2 = execute_graph(graph)
        assert _preview_cache.fingerprint == fp_before
        assert hash_calls.get(key) == 1, "stat-identical rewrite must not re-hash"
        assert [row["x"] for row in results2["src"].preview] == [
            row["x"] for row in results1["src"].preview
        ]

    def test_edit_landing_mid_hash_retries_once_then_signs_new_state(self, tmp_path, monkeypatch):
        """A write that lands between the gate stat and the content hash moves
        the stat gate, so the first attempt is discarded and the retry signs
        the file's settled state — never a hash paired with a stale stat."""
        import haute.execution as execution_mod

        p = tmp_path / "data.csv"
        _write_csv(p, [1, 2])
        real_content_hash = execution_mod.content_hash
        calls = {"n": 0}

        def racing_content_hash(path):
            calls["n"] += 1
            if calls["n"] == 1:
                _write_csv(Path(path), [9, 9, 9])  # different size: gate moves
                _bump_mtime(Path(path))
            return real_content_hash(path)

        monkeypatch.setattr(execution_mod, "content_hash", racing_content_hash)

        payload = execution_mod._stat_gated_runtime_path_fingerprint(p)
        assert calls["n"] == 2
        assert payload["content_hash"] == real_content_hash(p.resolve())
        assert payload["mtime_ns"] == p.stat().st_mtime_ns

    def test_file_mutating_on_every_hash_attempt_fails_loudly(self, tmp_path, monkeypatch):
        """If the file keeps changing under the hash, the fingerprint refuses
        to guess — matching ``_utility_file_hash``'s double-stat guard."""
        import haute.execution as execution_mod

        p = tmp_path / "data.csv"
        rows = [1]
        _write_csv(p, rows)
        real_content_hash = execution_mod.content_hash

        def perpetually_racing_content_hash(path):
            rows.append(len(rows))  # size grows: gate moves on every attempt
            _write_csv(Path(path), rows)
            _bump_mtime(Path(path))
            return real_content_hash(path)

        monkeypatch.setattr(execution_mod, "content_hash", perpetually_racing_content_hash)

        with pytest.raises(RuntimeError, match="changed while hashing"):
            execution_mod._stat_gated_runtime_path_fingerprint(p)


# ---------------------------------------------------------------------------
# Unit: runtime_input_extra_keys signs exactly the runtime input classes
# ---------------------------------------------------------------------------


class TestRuntimeInputExtraKeys:
    def test_pure_inline_graph_has_no_extra_keys(self):
        from haute.execution import runtime_input_extra_keys

        graph = _g({"nodes": [_transform_node("t", "df = df")], "edges": []})
        assert runtime_input_extra_keys(graph) == ()

    def test_databricks_datasource_signs_local_table_cache(self, tmp_path, monkeypatch):
        """The input a databricks dataSource consumes at preview is the
        LOCAL table-cache parquet maintained by the Fetch Data route — its
        state (including absence) is signed.  Remote warehouse drift
        without a re-fetch is correctly out of scope: nothing local
        changes until the user fetches again."""
        from haute._databricks_io import _cache_path_for
        from haute.execution import runtime_input_extra_keys

        monkeypatch.chdir(tmp_path)
        graph = _databricks_graph("cat.sch.tbl")

        keys_unfetched = runtime_input_extra_keys(graph)
        assert keys_unfetched, "the derived table-cache path is signed even before a fetch"

        cache_path = _cache_path_for("cat.sch.tbl")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1]}).write_parquet(cache_path)
        keys_v1 = runtime_input_extra_keys(graph)
        assert keys_v1 != keys_unfetched

        pl.DataFrame({"x": [2, 3]}).write_parquet(cache_path)
        _bump_mtime(cache_path)
        keys_v2 = runtime_input_extra_keys(graph)
        assert keys_v2 != keys_v1

    def test_databricks_datasource_without_table_contributes_nothing(self):
        """A databricks dataSource with no table cannot derive a cache path;
        execution rejects the config per-node and the (malformed) config is
        already digest material in the base graph fingerprint."""
        from haute.execution import runtime_input_extra_keys

        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "db",
                            "data": {
                                "label": "db",
                                "nodeType": "dataSource",
                                "config": {"sourceType": "databricks"},
                            },
                        }
                    ),
                ],
                "edges": [],
            }
        )
        assert runtime_input_extra_keys(graph) == ()

    def test_unset_optional_path_fields_sign_only_configured_files(self, tmp_path):
        """A modelScore with only ``artifact_path`` signs that one file —
        the unset ``feature_contract_path`` is skipped, not signed as
        an empty placeholder."""
        from haute.execution import runtime_input_extra_keys

        artifact = tmp_path / "model.cbm"
        artifact.write_bytes(b"model-v1")
        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "ms",
                            "data": {
                                "label": "ms",
                                "nodeType": "modelScore",
                                "config": {"artifact_path": str(artifact)},
                            },
                        }
                    ),
                ],
                "edges": [],
            }
        )

        keys_v1 = runtime_input_extra_keys(graph)
        assert keys_v1

        artifact.write_bytes(b"model-v2")
        _bump_mtime(artifact)
        assert runtime_input_extra_keys(graph) != keys_v1

    def test_flat_file_api_input_path_is_signed(self, tmp_path):
        """Non-JSON apiInput paths are flat-file reads — signed like a
        dataSource file, mirroring the builder's dispatch predicate."""
        from haute.execution import runtime_input_extra_keys

        p = tmp_path / "quotes.csv"
        _write_csv(p, [1])
        graph = _g({"nodes": [_flat_api_input_node("api", p)], "edges": []})

        keys_v1 = runtime_input_extra_keys(graph)

        _write_csv(p, [2, 3])
        _bump_mtime(p)
        keys_v2 = runtime_input_extra_keys(graph)
        assert keys_v2 != keys_v1

    def test_json_api_input_raw_file_is_not_file_signed(self, tmp_path, monkeypatch):
        """JSON-shape apiInputs preview from the built parquet cache, so
        the raw data.json bytes are deliberately NOT file-signed: the
        json_cache= state signature is their invalidation channel (cache
        validity records the data-file signature — W2.4), and re-signing
        the raw file here would invalidate previews that still correctly
        read the unchanged parquet cache."""
        from haute.execution import runtime_input_extra_keys

        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data.json"
        data.write_text(json.dumps([{"amount": 1}]), encoding="utf-8")
        graph = _json_api_input_graph(data)

        keys_before = runtime_input_extra_keys(graph)

        data.write_text(json.dumps([{"amount": 999}]), encoding="utf-8")
        _bump_mtime(data)
        keys_after = runtime_input_extra_keys(graph)
        assert keys_after == keys_before

    def test_model_score_artifact_and_contract_are_signed(self, tmp_path):
        from haute.execution import runtime_input_extra_keys

        artifact = tmp_path / "model.cbm"
        artifact.write_bytes(b"model-v1")
        contract = tmp_path / "contract.json"
        contract.write_text(json.dumps({"features": ["age"]}), encoding="utf-8")

        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "ms",
                            "data": {
                                "label": "ms",
                                "nodeType": "modelScore",
                                "config": {
                                    "artifact_path": str(artifact),
                                    "feature_contract_path": str(contract),
                                },
                            },
                        }
                    ),
                ],
                "edges": [],
            }
        )

        keys_v1 = runtime_input_extra_keys(graph)
        assert keys_v1, "a modelScore artifact must contribute extra-key material"

        artifact.write_bytes(b"model-v2-retrained")
        _bump_mtime(artifact)
        keys_v2 = runtime_input_extra_keys(graph)
        assert keys_v2 != keys_v1

        contract.write_text(json.dumps({"features": ["age", "region"]}), encoding="utf-8")
        _bump_mtime(contract)
        keys_v3 = runtime_input_extra_keys(graph)
        assert keys_v3 != keys_v2

    def test_missing_datasource_file_keys_differently_than_present(self, tmp_path):
        from haute.execution import runtime_input_extra_keys

        p = tmp_path / "data.csv"
        _write_csv(p, [1])
        graph = _csv_graph(p)

        keys_present = runtime_input_extra_keys(graph)
        assert keys_present

        p.unlink()
        keys_missing = runtime_input_extra_keys(graph)
        assert keys_missing
        assert keys_missing != keys_present

    def test_json_cache_state_signature_is_included(self, tmp_path, monkeypatch):
        from haute.execution import runtime_input_extra_keys

        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data.json"
        data.write_text(json.dumps([{"amount": 1}]), encoding="utf-8")
        cache_dir = _json_cache_dir(str(data), "working")
        cache_dir.mkdir(parents=True)
        (cache_dir / "meta.json").write_text("{}", encoding="utf-8")

        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "api",
                            "data": {
                                "label": "api",
                                "nodeType": "apiInput",
                                "config": {"path": str(data), "tables": []},
                            },
                        }
                    ),
                ],
                "edges": [],
            }
        )

        signature = cache_state_signature_for_graph(graph)
        assert signature  # meta.json exists, so the signature is non-empty
        assert signature in runtime_input_extra_keys(graph)
