"""C4 — preview/trace cache keys must cover runtime file inputs.

Re-exporting a direct dataInput Parquet, replacing an external file or a model
artifact, and rebuilding the apiInput JSON cache all happen out-of-band
(there is no in-GUI upload), so the graph JSON — and therefore the
structural fingerprint — does not change.  Before the fix the preview
and trace caches kept serving months-stale frames with zero indication.

These tests pin:

* preview recomputes after a dataInput file overwrite, an external
  file change, and a model-artifact replacement (file-sourced
  optimiserApply artifact — the cheapest real model fixture);
* a vanished dataInput file surfaces the execution error instead of a
  stale ok frame;
* trace recomputes after a dataInput edit, a raw JSON apiInput edit before
  any cache rebuild, and a JSON-cache rebuild (the trace key previously
  omitted the JSON-cache state signature entirely);
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

from haute._json_flatten import _json_cache_dir
from haute._types import GraphEdge
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

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Preview/trace caches are process singletons — isolate every test."""
    _preview_cache.clear()
    _trace_cache.clear()
    yield
    _preview_cache.clear()
    _trace_cache.clear()


def _write_csv(path: Path, values: list[int]) -> None:
    pl.DataFrame({"x": values}).write_csv(path)


def _write_parquet(path: Path, values: list[int]) -> None:
    pl.DataFrame({"x": values}).write_parquet(path)


def _bump_mtime(path: Path, seconds: float = 5.0) -> None:
    """Deterministically advance *path*'s mtime.

    Two writes inside the same timestamp granule are below the stat
    gate's resolution; a real out-of-band re-export always lands later.
    The explicit bump keeps the tests deterministic on coarse-mtime
    filesystems.
    """
    stat = path.stat()
    os.utime(path, (stat.st_atime + seconds, stat.st_mtime + seconds))


def _parquet_graph(path: Path):
    return _g({"nodes": [_parquet_input_node("src", path)], "edges": []})


def _parquet_input_node(nid: str, path: Path):
    return _source_node(nid, str(path))


def test_json_source_runtime_fingerprint_preserves_non_file_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directories stay on the generic path; source proofs apply only to files."""
    import haute.execution as execution_mod

    expected = {"kind": "directory"}
    calls: list[Path] = []

    def generic_fingerprint(path: Path) -> dict[str, str]:
        calls.append(path)
        return expected

    monkeypatch.setattr(execution_mod, "_runtime_path_fingerprint", generic_fingerprint)

    actual = execution_mod._json_source_runtime_path_fingerprint(tmp_path)

    assert actual is expected
    assert calls == [tmp_path.resolve()]


def test_graph_input_fingerprint_uses_canonical_json(monkeypatch, tmp_path: Path) -> None:
    from haute import _cache, execution

    seen: list[object] = []
    canonical = _cache.canonical_json

    def recording_canonical_json(payload: object) -> str:
        seen.append(payload)
        return canonical(payload)

    monkeypatch.setattr(_cache, "canonical_json", recording_canonical_json)
    first = execution.dataframe_graph_input_fingerprint(
        _parquet_graph(tmp_path / "source.parquet"), target_node_id=None, source="live"
    )
    second = execution.dataframe_graph_input_fingerprint(
        _parquet_graph(tmp_path / "source.parquet"), target_node_id=None, source="live"
    )

    assert seen
    assert first == second


def _flat_api_input_node(nid: str, path: Path):
    """apiInput with a non-JSON path — the builder dispatches it to
    ``read_data_source``, so preview reads the raw flat file directly."""
    return _n(
        {
            "id": nid,
            "data": {"label": nid, "nodeType": "apiInput", "config": {"path": str(path)}},
        }
    )


_V2_AMOUNT_TABLES = {
    "tables": [
        {
            "path": "$[:]",
            "label": "root",
            "emit": True,
            "row_id_column": None,
            "columns": [
                {
                    "name": "amount",
                    "path": "$[:].amount",
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
                _transform_node("t", "df = root.with_columns(y=pl.col('amount') * 2)"),
            ],
            "edges": [
                GraphEdge(
                    id="e_api_t",
                    source="api",
                    target="t",
                    sourceHandle="root",
                )
            ],
        }
    )


def _json_api_input_group_by_graph(data: Path):
    """JSON graph that exercises strategy estimation before runtime loading."""
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
                _transform_node(
                    "aggregate",
                    "df = root.group_by('amount').agg(pl.len().alias('rows'))",
                ),
            ],
            "edges": [
                GraphEdge(
                    id="e_api_aggregate",
                    source="api",
                    target="aggregate",
                    sourceHandle="root",
                )
            ],
        }
    )


# ---------------------------------------------------------------------------
# Preview: runtime file inputs invalidate the preview cache
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestPreviewRuntimeFileInvalidation:
    def test_direct_parquet_overwrite_recomputes_preview(self, tmp_path):
        """Re-exporting direct Parquet at the same path must not serve stale rows."""
        p = tmp_path / "data.parquet"
        _write_parquet(p, [1, 2])
        graph = _parquet_graph(p)

        results1 = execute_graph(graph)
        assert [row["x"] for row in results1["src"].preview] == [1, 2]

        _write_parquet(p, [5, 6])
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
                                    "code": "df = src.with_columns(y=pl.lit(obj['factor']))",
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
                "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
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
        """A deleted dataInput file must error like execution does — never
        serve the stale ok frame cached while the file still existed."""
        p = tmp_path / "data.parquet"
        _write_parquet(p, [1, 2])
        graph = _parquet_graph(p)

        results1 = execute_graph(graph)
        assert results1["src"].status == "ok"

        p.unlink()

        results2 = execute_graph(graph)
        assert results2["src"].status == "error"
        assert results2["src"].error

    def test_flat_file_api_input_reexport_recomputes_preview(self, tmp_path):
        """A non-JSON apiInput reads the raw flat file at preview (no JSON
        cache layer exists for it), so a re-export must invalidate exactly
        like a dataInput file — the json_cache= extra alone is a constant
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

    def test_json_api_input_raw_edit_shreds_directly_before_cache_rebuild(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Editing raw JSON invalidates preview and bypasses the stale cache."""
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data.json"
        _export_and_cache_amount(data, 10)
        graph = _json_api_input_graph(data)

        results1 = execute_graph(graph, target_node_id="t")
        assert results1["t"].status == "ok"
        assert results1["t"].preview == [{"amount": 10, "y": 20}]

        data.write_text(json.dumps([{"amount": 50}]), encoding="utf-8")
        _bump_mtime(data)

        results2 = execute_graph(graph, target_node_id="t")
        assert results2["api"].status == "ok", results2["api"].error
        assert results2["t"].status == "ok", results2["t"].error
        assert results2["t"].preview == [{"amount": 50, "y": 100}]

        # Rebuilding remains an optional prewarm and preserves the same result.
        _export_and_cache_amount(data, 50)
        _bump_mtime(data)

        results3 = execute_graph(graph, target_node_id="t")
        assert results3["t"].status == "ok"
        assert results3["t"].preview == [{"amount": 50, "y": 100}]


class TestTraceRuntimeInputInvalidation:
    def test_trace_recomputes_after_datasource_edit(self, tmp_path):
        p = tmp_path / "data.parquet"
        _write_parquet(p, [10])
        graph = _g(
            {
                "nodes": [
                    _parquet_input_node("src", p),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result1 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result1.output_value == 20

        _write_parquet(p, [50])
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
                    _transform_node("t", "df = api.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [
                    GraphEdge(
                        id="e_api_t",
                        source="api",
                        target="t",
                        sourceHandle="api",
                    )
                ],
            }
        )

        result1 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result1.output_value == 20

        _write_csv(p, [50])
        _bump_mtime(p)

        result2 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result2.output_value == 100

    def test_trace_recomputes_from_raw_json_before_cache_rebuild(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A stale parquet must not block a fresh trace from direct JSON."""
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data.json"
        _export_and_cache_amount(data, 10)
        graph = _json_api_input_graph(data)

        result1 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert result1.output_value == 20

        data.write_text(json.dumps([{"amount": 50}]), encoding="utf-8")
        _bump_mtime(data)

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

        p = tmp_path / "data.parquet"
        _write_parquet(p, [7])
        graph = _g(
            {
                "nodes": [
                    _parquet_input_node("src", p),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
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
        p = tmp_path / "data.parquet"
        _write_parquet(p, [1, 2])
        graph = _parquet_graph(p)
        key = str(p.resolve())

        execute_graph(graph)
        assert hash_calls.get(key) == 1, "first preview must content-hash the dataInput"

        execute_graph(graph)
        assert hash_calls.get(key) == 1, "unchanged stat must not re-hash content"

        _write_parquet(p, [5, 6])
        _bump_mtime(p)

        execute_graph(graph)
        assert hash_calls.get(key) == 2, "a changed stat must re-hash content"

    def test_all_runtime_path_fingerprint_surfaces_share_the_stat_gate(self, tmp_path, hash_calls):
        """Public path and graph fingerprints must not bypass the shared memo."""
        from haute.execution import (
            dataframe_graph_input_fingerprint,
            dataframe_paths_input_fingerprint,
        )

        p = tmp_path / "data.parquet"
        _write_parquet(p, [1, 2])
        key = str(p.resolve())

        dataframe_paths_input_fingerprint({"source": str(p)})
        dataframe_paths_input_fingerprint({"source": str(p)})
        assert hash_calls.get(key) == 1

        graph = _parquet_graph(p)
        dataframe_graph_input_fingerprint(graph, target_node_id=None, source="test")
        dataframe_graph_input_fingerprint(graph, target_node_id=None, source="test")
        assert hash_calls.get(key) == 1

    def test_json_preview_uses_one_authoritative_source_content_proof(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Planning, identity, and loading must reuse the cache-build SHA-256 proof."""
        import haute._json_shred as shred_mod
        import haute.execution as execution_mod

        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data.json"
        _export_and_cache_amount(data, 10)
        graph = _json_api_input_group_by_graph(data)
        resolved = data.resolve()

        shred_mod._clear_data_file_signature_memo()
        execution_mod._runtime_path_fingerprint_cache.clear()
        source_hashes = 0
        generic_hashes = 0
        real_source_hash = shred_mod._hash_file
        real_generic_hash = execution_mod.content_hash

        def counting_source_hash(path: Path) -> str:
            nonlocal source_hashes
            if path.resolve() == resolved:
                source_hashes += 1
            return real_source_hash(path)

        def counting_generic_hash(path: Path) -> str:
            nonlocal generic_hashes
            if path.resolve() == resolved:
                generic_hashes += 1
            return real_generic_hash(path)

        monkeypatch.setattr(shred_mod, "_hash_file", counting_source_hash)
        monkeypatch.setattr(execution_mod, "content_hash", counting_generic_hash)

        result = execute_graph(graph, target_node_id="aggregate")

        assert result["aggregate"].preview == [{"amount": 10, "rows": 1}]
        assert source_hashes == 0
        assert generic_hashes == 0

    def test_json_same_stat_byte_rewrite_invalidates_preview_identity(
        self,
        tmp_path,
        monkeypatch,
    ):
        """The strong JSON revision, not size/mtime, gates cached previews."""
        import haute._json_shred as shred_mod
        import haute.execution as execution_mod

        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data.json"
        _export_and_cache_amount(data, 10)
        graph = _json_api_input_graph(data)
        shred_mod._clear_data_file_signature_memo()
        execution_mod._runtime_path_fingerprint_cache.clear()

        first = execute_graph(graph, target_node_id="t")
        first_key = _preview_cache.most_recent_key
        original_stat = data.stat()

        data.write_text(json.dumps([{"amount": 20}]), encoding="utf-8")
        assert data.stat().st_size == original_stat.st_size
        os.utime(
            data,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )

        second = execute_graph(graph, target_node_id="t")

        assert first["t"].preview == [{"amount": 10, "y": 20}]
        assert second["t"].preview == [{"amount": 20, "y": 40}]
        assert _preview_cache.most_recent_key != first_key

    def test_flat_api_input_retains_generic_stat_gated_identity(self, tmp_path, hash_calls):
        p = tmp_path / "quotes.csv"
        _write_csv(p, [1, 2])
        graph = _g({"nodes": [_flat_api_input_node("api", p)], "edges": []})
        key = str(p.resolve())

        execute_graph(graph)
        execute_graph(graph)

        assert hash_calls.get(key) == 1

    def test_touch_with_identical_bytes_recomputes(self, tmp_path):
        """Pinned semantics: mtime is digest material (like the sink path's
        ``_runtime_path_fingerprint``), so a touch recomputes even though
        the bytes are unchanged.  Deliberate: simplest correct stat-gate."""
        p = tmp_path / "data.parquet"
        _write_parquet(p, [1, 2])
        graph = _parquet_graph(p)

        execute_graph(graph)
        fp_before = _preview_cache.most_recent_key
        assert fp_before is not None

        _bump_mtime(p)

        execute_graph(graph)
        fp_after = _preview_cache.most_recent_key
        assert fp_after != fp_before, "mtime change must produce a new preview cache key"

    def test_stat_identical_noop_rewrite_serves_cached_preview(self, tmp_path, hash_calls):
        """Pinned semantics: a rewrite that restores both bytes and stat is
        below the stat gate's resolution and serves the cached entry —
        correct, because the bytes are identical."""
        p = tmp_path / "data.parquet"
        _write_parquet(p, [1, 2])
        graph = _parquet_graph(p)
        key = str(p.resolve())

        results1 = execute_graph(graph)
        fp_before = _preview_cache.most_recent_key
        stat = p.stat()

        _write_parquet(p, [1, 2])  # identical bytes
        os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # restore stat exactly

        results2 = execute_graph(graph)
        assert _preview_cache.most_recent_key == fp_before
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

        with pytest.raises(RuntimeError, match="changed on disk while loading"):
            execution_mod._stat_gated_runtime_path_fingerprint(p)
