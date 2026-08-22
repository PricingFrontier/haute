"""Reproducible performance certification for execution-engine hardening."""

from __future__ import annotations

import hashlib
import io
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest

from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._json_flatten import _json_cache_dir
from haute._json_shred._cache import build_per_port_cache, load_v2_api_source
from haute._polars_utils import execution_collect
from haute._ram_estimate import estimate_materialisation_boundaries
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import GroupByExecutionUnsupportedError
from haute.execution import plan_prepared_execution_strategy
from haute.executor import _preview_cache, execute_graph
from scripts.memory_smoke import run_smoke

pytestmark = [pytest.mark.perf, pytest.mark.usefixtures("_widen_sandbox_root")]

_PROBE = Path(__file__).with_name("_execution_engine_probe.py")
_RESTART_PROBE = Path(__file__).with_name("_execution_restart_probe.py")
_STRUCTURED_CACHE_PROBE = Path(__file__).with_name("_structured_cache_memory_probe.py")
_RESILIENCE_PROBE = Path(__file__).with_name("_execution_resilience_probe.py")
_WIDE_ROWS = 50_000
_WIDE_COLUMNS = 256
_SELECTED_WIDE_COLUMNS = ("row_id", "value_000", "value_001", "value_002")
_MAX_PROJECTED_RSS_FRACTION = 0.35
_PROJECTED_RSS_ALLOWANCE_BYTES = 16 * 1024 * 1024
_MIN_FULL_FRAME_RSS_FRACTION = 0.65
_API_ROWS = 5_000
_API_UNUSED_COLUMNS = 64
_DIRECT_JSONL_ROWS = 20_000
_DIRECT_JSONL_UNUSED_COLUMNS = 63
_SIGNATURE_SOURCE_BYTES = 32 * 1024 * 1024
_SIGNATURE_WARM_SAMPLES = 9
_MAX_SIGNATURE_WARM_FRACTION = 0.05
_ARTIFACT_PROOF_BYTES = 32 * 1024 * 1024
_ARTIFACT_WARM_SAMPLES = 9
_MAX_ARTIFACT_WARM_FRACTION = 0.05
_PREVIEW_HIT_WARM_SAMPLES = 9
_MAX_PREVIEW_HIT_WARM_FRACTION = 0.50
_OPTIMISATION_MATERIALITY_FRACTION = 0.20
_STREAM_CACHE_SMALL_ROWS = 10_000
_STREAM_CACHE_LARGE_ROWS = 120_000
_STREAM_CACHE_PAYLOAD_BYTES = 512
_STREAM_CACHE_GROWTH_ALLOWANCE_BYTES = 32 * 1024 * 1024
_STREAM_CACHE_MAX_GROWTH_FACTOR = 1.35
_RESILIENCE_SCALES = {
    "ci": {"calls": 120, "replacements": 8, "timeout_seconds": 120},
    "1m": {"calls": 2_000, "replacements": 100, "timeout_seconds": 900},
    "10m": {"calls": 10_000, "replacements": 1_000, "timeout_seconds": 1_700},
}


def _run_execution_resilience_probe(tmp_path: Path) -> dict[str, Any]:
    output = tmp_path / "resilience.json"
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    inherited_paths = [path for path in sys.path if path]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [*inherited_paths, environment.get("PYTHONPATH", "")]
    )
    scale = environment.get("HAUTE_POLARS_PERF_SCALE", "ci")
    if scale not in _RESILIENCE_SCALES:
        raise ValueError(f"Unsupported resilience scale: {scale}")
    scale_contract = _RESILIENCE_SCALES[scale]
    completed = subprocess.run(
        [
            interpreter,
            str(_RESILIENCE_PROBE),
            "--mode",
            scale,
            "--root",
            str(tmp_path / "work"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=scale_contract["timeout_seconds"],
        check=False,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def test_fresh_process_execution_resilience_certificate(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    evidence = _run_execution_resilience_probe(tmp_path)
    scale = os.environ.get("HAUTE_POLARS_PERF_SCALE", "ci")
    scale_contract = _RESILIENCE_SCALES[scale]
    soak = evidence["worker_soak"]
    assert evidence["mode"] == scale
    assert soak["calls"] == scale_contract["calls"]
    assert soak["replacements"] == scale_contract["replacements"]
    assert soak["unique_worker_pids"] == scale_contract["replacements"] + 1
    assert soak["plateau"]["rss_growth_bytes"] <= soak["plateau"]["rss_growth_limit_bytes"]
    assert soak["plateau"]["resource_growth"] <= soak["plateau"]["resource_growth_limit"]
    assert (
        soak["plateau"]["after_close_resource_delta"]
        <= soak["plateau"]["after_close_resource_delta_limit"]
    )
    assert evidence["cache"]["enospc_preserved_old"] is True
    assert len(evidence["cache"]["phases"]) == 5
    request.node.user_properties.append(
        ("haute_perf_evidence", {"scenario": "execution_resilience_certificate", **evidence})
    )


def test_extreme_many_to_many_join_skew_is_estimated_and_rejected_without_execution(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """A finite 10^18 join proof must reach admission without materialising it."""
    import haute._ram_estimate as estimate_mod

    left = GraphNode(id="left", data=NodeData(label="left", nodeType=NodeType.API_INPUT, config={}))
    right = GraphNode(
        id="right", data=NodeData(label="right", nodeType=NodeType.API_INPUT, config={})
    )
    joined = GraphNode(
        id="joined",
        data=NodeData(
            label="joined",
            nodeType=NodeType.EDGE_JOIN,
            config={
                "baseInput": "left",
                "joinInput": "right",
                "how": "inner",
                "on": ["id"],
                "validate": "m:m",
            },
        ),
    )
    boundary = GraphNode(
        id="boundary",
        data=NodeData(
            label="boundary",
            nodeType=NodeType.POLARS,
            config={"code": "df.group_by('id').agg(pl.len())"},
        ),
    )
    graph = PipelineGraph(
        nodes=[left, right, joined, boundary],
        edges=[
            GraphEdge(id="left-join", source="left", target="joined", targetHandle="base"),
            GraphEdge(id="right-join", source="right", target="joined", targetHandle="join"),
            GraphEdge(id="join-boundary", source="joined", target="boundary"),
        ],
    )
    metadata_type = estimate_mod._DetailedSourceMetadata
    metadata = metadata_type(
        1_000_000_000,
        2,
        {"id": "Int64", "value": "Int64"},
        {"id": "id", "value": "value"},
        {"id": 8, "value": 8},
        16,
    )
    monkeypatch.setattr(estimate_mod, "_detailed_source_metadata_for_node", lambda _node: metadata)
    index = estimate_mod._EstimateGraphIndex.build(graph, "live")
    cardinality = index.resolve_cardinality("joined")
    assert cardinality.output_rows == cardinality.peak_rows == 10**18
    # Estimate the join boundary directly: this exercises the cardinality and
    # materialisation APIs without executing or materialising the skewed join.
    [(_, estimate)] = list(estimate_materialisation_boundaries(graph, ["joined"]))
    assert estimate.estimated_peak_bytes is not None and estimate.estimated_peak_bytes > 1024
    context = ExecutionContext(
        operation="extreme_skew",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission=ExecutionAdmission(
            operation="extreme_skew",
            profile=ExecutionProfile.PREVIEW_EAGER,
            admitted=True,
            memory_limit_bytes=1024,
            headroom_bytes=1024,
            rss_at_admission_bytes=0,
            rss_limit_bytes=None,
            config_key="certificate",
        ),
    )
    with pytest.raises(GroupByExecutionUnsupportedError) as rejected:
        plan_prepared_execution_strategy(
            ["left", "right", "joined", "boundary"],
            {"left": ["joined"], "right": ["joined"], "joined": ["boundary"], "boundary": []},
            {node.id: node for node in graph.nodes},
            profile=ExecutionProfile.PREVIEW_EAGER,
            execution_context=context,
            materialisation_estimate=estimate,
            relevant_edges=graph.edges,
        )
    assert rejected.value.reason_code == "materialisation_exceeds_headroom"
    assert rejected.value.estimated_peak_bytes is not None
    assert any(
        "cardinality_output_upper_bound=1000000000000000000" in item
        for item in estimate.assumptions
    )
    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "extreme_many_to_many_join_skew",
                "cardinality": cardinality.output_rows,
                "estimate_bytes": estimate.estimated_peak_bytes,
                "admission_rejection": rejected.value.reason_code,
                "cardinality_evidence": cardinality.evidence,
            },
        )
    )


def _write_wide_parquet(path: Path) -> list[str]:
    columns = ["row_id", *[f"value_{index:03d}" for index in range(_WIDE_COLUMNS - 1)]]
    base = pl.DataFrame({"row_id": range(_WIDE_ROWS)}).lazy()
    wide = base.with_columns(
        *[
            ((pl.col("row_id") * (index + 3)) % 1_000_003).cast(pl.Float64).alias(column)
            for index, column in enumerate(columns[1:])
        ]
    )
    wide.select(columns).sink_parquet(path, compression="zstd")
    return columns


def _run_collection_probe(
    tmp_path: Path,
    parquet_path: Path,
    *,
    mode: str,
) -> dict[str, Any]:
    result_path = tmp_path / f"{mode}-probe.json"
    child_output = io.BytesIO()
    # On Windows a virtual-environment ``python.exe`` can be a redirector
    # process.  Sampling that PID measures the tiny redirector rather than the
    # interpreter doing the Polars work.  Launch the base interpreter directly
    # and explicitly carry over the parent's import paths so the sampled PID is
    # the workload process on every platform.
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    inherited_paths = [path for path in sys.path if path]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path[:0]={inherited_paths!r};"
        f"runpy.run_path({str(_PROBE)!r},run_name='__main__')"
    )
    smoke = run_smoke(
        command=[
            interpreter,
            "-c",
            bootstrap,
            "--parquet",
            str(parquet_path),
            "--mode",
            mode,
            "--columns",
            *_SELECTED_WIDE_COLUMNS,
            "--output",
            str(result_path),
        ],
        enable_tracemalloc=False,
        poll_interval_seconds=0.005,
        child_output=child_output,
    )
    assert smoke["exit_code"] == 0, child_output.getvalue().decode(errors="replace")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    baseline_rss = result["rss_before_bytes"]
    peak_rss = smoke["child_peak_rss_bytes"]
    assert isinstance(baseline_rss, int) and baseline_rss > 0
    assert isinstance(peak_rss, int) and peak_rss >= baseline_rss
    sample_count = smoke["child_rss_sample_count"]
    assert isinstance(sample_count, int) and sample_count >= 2
    return {
        **result,
        "child_peak_rss_bytes": peak_rss,
        "incremental_peak_rss_bytes": peak_rss - baseline_rss,
        "rss_sample_count": sample_count,
        "wall_seconds": smoke["elapsed_seconds"],
    }


def _column(name: str, path: str) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": "int",
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }


def _table(path: str, label: str, columns: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "path": path,
        "label": label,
        "emit": True,
        "row_id_column": None,
        "columns": columns,
    }


def _write_cached_api_fixture(path: Path) -> dict[str, Any]:
    root_columns = [_column("quote_id", "$[:].quote_id")]
    root_columns.extend(
        _column(f"root_unused_{index:03d}", f"$[:].root_unused_{index:03d}")
        for index in range(_API_UNUSED_COLUMNS)
    )
    claim_columns = [
        _column("quote_id", "$[:].quote_id"),
        _column("amount_paid", "$[:].claims[:].amount_paid"),
    ]
    claim_columns.extend(
        _column(
            f"claim_unused_{index:03d}",
            f"$[:].claims[:].claim_unused_{index:03d}",
        )
        for index in range(_API_UNUSED_COLUMNS)
    )
    with path.open("wb") as stream:
        for row in range(_API_ROWS):
            record = {
                "quote_id": row,
                **{f"root_unused_{index:03d}": row + index for index in range(_API_UNUSED_COLUMNS)},
                "claims": [
                    {
                        "amount_paid": row % 101,
                        **{
                            f"claim_unused_{index:03d}": row + index
                            for index in range(_API_UNUSED_COLUMNS)
                        },
                    }
                ],
            }
            stream.write(orjson.dumps(record))
            stream.write(b"\n")
    return {
        "tables": [
            _table("$[:]", "quote_info", root_columns),
            _table("$[:].claims[:]", "claims", claim_columns),
        ]
    }


def _write_direct_jsonl_fixture(path: Path) -> dict[str, Any]:
    columns = [_column("id", "$[:].id")]
    columns.extend(
        _column(f"value_{index:03d}", f"$[:].value_{index:03d}")
        for index in range(_DIRECT_JSONL_UNUSED_COLUMNS)
    )
    with path.open("wb") as stream:
        for row in range(_DIRECT_JSONL_ROWS):
            record = {
                "id": row,
                **{
                    f"value_{index:03d}": row + index
                    for index in range(_DIRECT_JSONL_UNUSED_COLUMNS)
                },
            }
            stream.write(orjson.dumps(record))
            stream.write(b"\n")
    return {"tables": [_table("$[:]", "rows", columns)]}


def _snapshot_parquets(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.parquet") if ".runtime-snapshots" in path.parts)


def _cached_generation_digest(cache_dir: Path) -> str:
    digest = hashlib.sha256()
    for artifact in sorted(path for path in cache_dir.rglob("*") if path.is_file()):
        digest.update(artifact.relative_to(cache_dir).as_posix().encode())
        digest.update(artifact.read_bytes())
    return digest.hexdigest()


def _run_restart_cache_probe(
    tmp_path: Path,
    source_path: Path,
    config_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    inherited_paths = [path for path in sys.path if path]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path[:0]={inherited_paths!r};"
        f"runpy.run_path({str(_RESTART_PROBE)!r},run_name='__main__')"
    )
    completed = subprocess.run(
        [
            interpreter,
            "-c",
            bootstrap,
            "--source",
            str(source_path),
            "--config",
            str(config_path),
            "--port",
            "rows",
            "--column",
            "id",
            "--column",
            "amount",
            "--output",
            str(output_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(output_path.read_text(encoding="utf-8"))


def _string_leaves(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_string_leaves(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_string_leaves(item) for item in value))
    return set()


def test_fresh_process_restart_reuses_cache_proof_and_safe_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Certify a committed cached port survives independent interpreter restarts."""
    monkeypatch.chdir(tmp_path)
    source_path = tmp_path / "restart-source.jsonl"
    records = [
        {"id": 1, "amount": 10, "ignored": 100},
        {"id": 2, "amount": 20, "ignored": 200},
        {"id": 3, "amount": 30, "ignored": 300},
    ]
    source_path.write_bytes(b"".join(orjson.dumps(row) + b"\n" for row in records))
    config = {
        "tables": [
            _table(
                "$[:]",
                "rows",
                [
                    _column("id", "$[:].id"),
                    _column("amount", "$[:].amount"),
                    _column("ignored", "$[:].ignored"),
                ],
            )
        ]
    }
    config_path = tmp_path / "restart-config.json"
    config_path.write_bytes(orjson.dumps(config))
    cache_dir = _json_cache_dir(source_path, "committed")
    build_per_port_cache(source_path, config, cache_dir)
    assert not _json_cache_dir(source_path, "working").exists()
    generation_before = _cached_generation_digest(cache_dir)
    cache_artifact_bytes = sum(path.stat().st_size for path in cache_dir.glob("*.parquet"))

    first = _run_restart_cache_probe(
        tmp_path, source_path, config_path, tmp_path / "restart-first.json"
    )
    assert _cached_generation_digest(cache_dir) == generation_before
    assert not list(tmp_path.rglob(".runtime-snapshots/*/.owner.json"))
    second = _run_restart_cache_probe(
        tmp_path, source_path, config_path, tmp_path / "restart-second.json"
    )
    assert _cached_generation_digest(cache_dir) == generation_before
    assert not list(tmp_path.rglob(".runtime-snapshots/*/.owner.json"))

    expected_rows = [{"id": row["id"], "amount": row["amount"]} for row in records]
    assert first["rows"] == second["rows"] == expected_rows
    for result in (first, second):
        assert result["cache_proof"] == {
            "hits": 1,
            "misses": 1,
            "direct_fallbacks": 0,
            "miss_reason_counts": {
                "artifact_integrity_schema_failure": 0,
                "metadata_source_mismatch": 0,
                "proof_unavailable": 1,
                "unreadable_artifact": 0,
            },
        }
        assert len(result["telemetry"]) == 1
        terminal = result["telemetry"][0]
        assert terminal["cache_proof_hits"] == 1
        assert terminal["cache_proof_misses"] == 1
        assert terminal["cache_direct_fallbacks"] == 0
        assert terminal["requested_column_width_total"] in (None, 2)
        assert terminal["physically_scanned_column_width_total"] in (None, 2)
        sensitive_values = {"restart-source", str(source_path), "id", "amount", "rows"}
        assert not (sensitive_values & set(terminal))
        assert not (sensitive_values & _string_leaves(terminal))

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "execution_engine_restart_cache_proof_telemetry",
                "scale": "ci-restart",
                "input": {"rows": len(records), "source_bytes": source_path.stat().st_size},
                "product_metrics": {
                    "cache_proof_hits": first["cache_proof"]["hits"],
                    "cache_proof_misses": first["cache_proof"]["misses"],
                    "cache_direct_fallbacks": first["cache_proof"]["direct_fallbacks"],
                    "cache_artifact_bytes": cache_artifact_bytes,
                },
                "payload_bytes": len(orjson.dumps(first["rows"])),
                "execution_profile": first["profile"],
            },
        )
    )


def test_unchanged_source_signature_reuses_one_complete_content_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Certify the warm path against the exact full-hash control it replaces."""
    from haute._json_shred import _source_proof

    source_path = tmp_path / "signature-source.jsonl"
    block = bytes(range(256)) * 4_096
    assert len(block) == 1024 * 1024
    with source_path.open("wb") as stream:
        for _ in range(_SIGNATURE_SOURCE_BYTES // len(block)):
            stream.write(block)
    assert source_path.stat().st_size == _SIGNATURE_SOURCE_BYTES
    assert _source_proof._strong_file_revision(source_path) is not None

    _source_proof._clear_data_file_signature_memo()
    real_hash_file = _source_proof._hash_file
    source_hashes = 0

    def counting_hash_file(path: Path) -> str:
        nonlocal source_hashes
        if path.resolve() == source_path.resolve():
            source_hashes += 1
        return real_hash_file(path)

    monkeypatch.setattr(_source_proof, "_hash_file", counting_hash_file)
    cold_started = time.perf_counter_ns()
    expected = _source_proof._data_file_signature(source_path)
    cold_ns = time.perf_counter_ns() - cold_started
    warm_ns: list[int] = []
    for _ in range(_SIGNATURE_WARM_SAMPLES):
        started = time.perf_counter_ns()
        assert _source_proof._data_file_signature(source_path) == expected
        warm_ns.append(time.perf_counter_ns() - started)

    warm_median_ns = int(statistics.median(warm_ns))
    assert source_hashes == 1
    assert warm_median_ns <= cold_ns * _MAX_SIGNATURE_WARM_FRACTION

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "execution_engine_source_signature_proof_reuse",
                "scale": "ci-32mib-source",
                "execution_profiles": [ExecutionProfile.PREVIEW_EAGER.value],
                "input": {
                    "source_bytes": _SIGNATURE_SOURCE_BYTES,
                    "warm_samples": _SIGNATURE_WARM_SAMPLES,
                },
                "cold_full_hash_ns": cold_ns,
                "warm_revision_hit_median_ns": warm_median_ns,
                "speedup": cold_ns / warm_median_ns if warm_median_ns else None,
                "warm_fraction": warm_median_ns / cold_ns if cold_ns else None,
                "warm_fraction_contract": _MAX_SIGNATURE_WARM_FRACTION,
                "product_metrics": {
                    "source_hashes": source_hashes,
                    "n_collects": 0,
                    "n_checkpoints": 0,
                    "chunk_count": 0,
                    "output_bytes": 0,
                    "temp_disk_peak_bytes": 0,
                },
                "admission": {"state": "not_required", "detail": None},
                "payload_bytes": 0,
            },
        )
    )


def test_unchanged_cached_artifact_reuses_one_complete_content_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Certify bounded verified-snapshot reuse against a full-hash control."""

    from haute._json_shred import _runtime_storage, _source_proof

    cache_dir = tmp_path / "cache"
    artifact_path = tmp_path / "artifact.parquet"
    block = bytes(range(256)) * 4_096
    expected_digest = hashlib.sha256()
    with artifact_path.open("wb") as stream:
        for _ in range(_ARTIFACT_PROOF_BYTES // len(block)):
            stream.write(block)
            expected_digest.update(block)
    recorded_signature = {
        "size": _ARTIFACT_PROOF_BYTES,
        "sha256": expected_digest.hexdigest(),
    }
    assert artifact_path.stat().st_size == _ARTIFACT_PROOF_BYTES
    assert _source_proof._strong_file_revision(artifact_path) is not None

    _runtime_storage._cleanup_runtime_snapshot_dirs()
    real_signature = _source_proof._file_content_signature
    artifact_hashes = 0

    def counting_signature(path: Path) -> dict[str, Any]:
        nonlocal artifact_hashes
        artifact_hashes += 1
        return real_signature(path)

    monkeypatch.setattr(_source_proof, "_file_content_signature", counting_signature)
    cold_started = time.perf_counter_ns()
    cold_snapshot = _runtime_storage._snapshot_cache_artifact(
        cache_dir,
        artifact_path,
        recorded_signature,
    )
    cold_ns = time.perf_counter_ns() - cold_started
    assert cold_snapshot is not None
    _runtime_storage._release_runtime_snapshot(cold_snapshot)

    warm_ns: list[int] = []
    for _ in range(_ARTIFACT_WARM_SAMPLES):
        started = time.perf_counter_ns()
        warm_snapshot = _runtime_storage._snapshot_cache_artifact(
            cache_dir,
            artifact_path,
            recorded_signature,
        )
        warm_ns.append(time.perf_counter_ns() - started)
        assert warm_snapshot == cold_snapshot
        _runtime_storage._release_runtime_snapshot(warm_snapshot)

    warm_median_ns = int(statistics.median(warm_ns))
    cache_stats = _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()
    assert artifact_hashes == 1
    assert cache_stats == {
        "entries": 1,
        "bytes": _ARTIFACT_PROOF_BYTES,
        "inflight": 0,
    }
    assert warm_median_ns <= cold_ns * _MAX_ARTIFACT_WARM_FRACTION
    assert cold_snapshot.exists()
    _runtime_storage._cleanup_runtime_snapshot_dirs()
    assert not cold_snapshot.exists()

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "execution_engine_cached_artifact_proof_reuse",
                "scale": "ci-32mib-artifact",
                "execution_profiles": [ExecutionProfile.PREVIEW_EAGER.value],
                "input": {
                    "artifact_bytes": _ARTIFACT_PROOF_BYTES,
                    "warm_samples": _ARTIFACT_WARM_SAMPLES,
                },
                "cold_full_hash_ns": cold_ns,
                "warm_revision_hit_median_ns": warm_median_ns,
                "speedup": cold_ns / warm_median_ns if warm_median_ns else None,
                "warm_fraction": warm_median_ns / cold_ns if cold_ns else None,
                "warm_fraction_contract": _MAX_ARTIFACT_WARM_FRACTION,
                "verified_snapshot_cache": cache_stats,
                "product_metrics": {
                    "artifact_hashes": artifact_hashes,
                    "n_collects": 0,
                    "n_checkpoints": 0,
                    "chunk_count": 0,
                    "output_bytes": 0,
                    "temp_disk_peak_bytes": _ARTIFACT_PROOF_BYTES,
                },
                "admission": {"state": "not_required", "detail": None},
                "payload_bytes": 0,
            },
        )
    )


def test_cached_json_target_preview_uses_one_authoritative_source_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Certify that planning, preview identity, and loading share JSON proof."""
    import haute.execution as execution_mod
    from haute._json_shred import _source_proof

    monkeypatch.chdir(tmp_path)
    source_path = tmp_path / "source.json"
    records = [
        {"amount": 10, "premium": 3},
        {"amount": 10, "premium": 7},
        {"amount": 20, "premium": 5},
        {"amount": 20, "premium": 11},
    ]
    source_path.write_bytes(orjson.dumps(records))
    config = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [
                    _column("amount", "$[:].amount"),
                    _column("premium", "$[:].premium"),
                ],
            )
        ]
    }
    build_per_port_cache(source_path, config, _json_cache_dir(source_path, "working"))
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="api",
                data=NodeData(
                    label="api",
                    nodeType=NodeType.API_INPUT,
                    config={"path": str(source_path), **config},
                ),
            ),
            GraphNode(
                id="aggregate",
                data=NodeData(
                    label="aggregate",
                    nodeType=NodeType.POLARS,
                    config={
                        "code": (
                            "df = root.group_by('amount').agg("
                            "pl.col('premium').sum().alias('premium_total')).sort('amount')"
                        )
                    },
                ),
            ),
        ],
        edges=[
            GraphEdge(
                id="e_api_aggregate",
                source="api",
                target="aggregate",
                sourceHandle="root",
            )
        ],
    )
    resolved = source_path.resolve()
    _source_proof._clear_data_file_signature_memo()
    execution_mod._runtime_path_fingerprint_cache.clear()
    _preview_cache.clear()
    source_hashes = 0
    generic_hashes = 0
    real_source_hash = _source_proof._hash_file
    real_generic_hash = execution_mod.content_hash
    import haute.projection as projection_mod

    real_execution_prepare = execution_mod.prepare_graph
    real_projection_prepare = projection_mod.prepare_graph
    prepare_calls = 0
    prepare_elapsed_ns = 0

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

    def timed_prepare(prepare):
        def wrapped(*args: Any, **kwargs: Any):
            nonlocal prepare_calls, prepare_elapsed_ns
            started = time.perf_counter_ns()
            try:
                return prepare(*args, **kwargs)
            finally:
                prepare_calls += 1
                prepare_elapsed_ns += time.perf_counter_ns() - started

        return wrapped

    monkeypatch.setattr(_source_proof, "_hash_file", counting_source_hash)
    monkeypatch.setattr(execution_mod, "content_hash", counting_generic_hash)
    monkeypatch.setattr(execution_mod, "prepare_graph", timed_prepare(real_execution_prepare))
    monkeypatch.setattr(projection_mod, "prepare_graph", timed_prepare(real_projection_prepare))
    headroom_bytes = 64 * 1024 * 1024
    context = ExecutionContext(
        operation="perf_json_source_proof_target_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission=ExecutionAdmission(
            operation="perf_json_source_proof_target_preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=headroom_bytes,
            rss_at_admission_bytes=0,
            rss_limit_bytes=headroom_bytes,
            headroom_bytes=headroom_bytes,
            config_key="perf",
        ),
    )
    started = time.perf_counter_ns()
    try:
        result = execute_graph(
            graph,
            target_node_id="aggregate",
            target_preview_only=True,
            execution_context=context,
        )
        elapsed_ns = time.perf_counter_ns() - started
        metrics = context.metrics_payload(status="completed")
    finally:
        context.release_admission()

    assert result["aggregate"].preview == [
        {"amount": 10, "premium_total": 10},
        {"amount": 20, "premium_total": 16},
    ]
    assert source_hashes == 0
    assert generic_hashes == 0
    assert prepare_calls == 3
    # Even treating every preparation call as removable gives the candidate
    # its most favourable possible comparison. It still must clear the same
    # 20% end-to-end materiality gate as every other engine optimisation.
    maximum_prepare_fraction = prepare_elapsed_ns / elapsed_ns
    assert maximum_prepare_fraction < _OPTIMISATION_MATERIALITY_FRACTION

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "execution_engine_cached_json_target_preview_source_proof",
                "scale": "ci-small-cached-json",
                "execution_profiles": [ExecutionProfile.PREVIEW_EAGER.value],
                "input": {
                    "rows": len(records),
                    "source_bytes": source_path.stat().st_size,
                },
                "elapsed_ns": elapsed_ns,
                "source_sha256_hashes": source_hashes,
                "persisted_cache_build_source_proof_reused": True,
                "generic_runtime_xxhash_calls": generic_hashes,
                "execution_profile": ExecutionProfile.PREVIEW_EAGER.value,
                "request_local_graph_preparation": {
                    "calls": prepare_calls,
                    "elapsed_ns": prepare_elapsed_ns,
                    "maximum_theoretical_fraction": maximum_prepare_fraction,
                    "materiality_gate": _OPTIMISATION_MATERIALITY_FRACTION,
                    "decision": "no_change",
                },
                "product_metrics": {
                    "n_collects": metrics["n_collects"],
                    "n_checkpoints": metrics["n_checkpoints"],
                    "chunk_count": metrics["chunk_count"],
                    "output_bytes": 0,
                    "temp_disk_peak_bytes": 0,
                },
                "admission": {"state": "direct_context", "detail": None},
                "payload_bytes": len(orjson.dumps(result["aggregate"].preview)),
            },
        )
    )


def test_preview_cache_hit_reuses_strategy_without_planning_or_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Certify that a complete preview hit is lookup/serialization work only."""

    import haute.execution as execution_mod
    import haute.executor as executor_mod
    from haute._json_shred import _source_proof

    monkeypatch.chdir(tmp_path)
    source_path = tmp_path / "preview-hit.json"
    source_path.write_bytes(orjson.dumps([{"amount": 10}, {"amount": 20}]))
    config = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [_column("amount", "$[:].amount")],
            )
        ]
    }
    build_per_port_cache(source_path, config, _json_cache_dir(source_path, "working"))
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="api",
                data=NodeData(
                    label="api",
                    nodeType=NodeType.API_INPUT,
                    config={"path": str(source_path), **config},
                ),
            )
        ],
        edges=[],
    )
    _source_proof._clear_data_file_signature_memo()
    execution_mod._runtime_path_fingerprint_cache.clear()
    _preview_cache.clear()
    real_plan = executor_mod.execution_facade.plan_execution_strategy
    plan_calls = 0

    def counting_plan(*args: Any, **kwargs: Any):
        nonlocal plan_calls
        plan_calls += 1
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(
        executor_mod.execution_facade,
        "plan_execution_strategy",
        counting_plan,
    )

    cold_context = ExecutionContext(
        operation="perf_preview_cache_cold",
        profile=ExecutionProfile.PREVIEW_EAGER,
        telemetry_enabled=False,
    )
    cold_started = time.perf_counter_ns()
    cold_result = execute_graph(
        graph,
        target_node_id="api",
        target_preview_only=True,
        requested_preview_columns=["amount"],
        port_label="root",
        execution_context=cold_context,
    )
    cold_ns = time.perf_counter_ns() - cold_started
    producing_strategy = cold_context.projection_plan
    assert producing_strategy is not None
    cold_metrics = cold_context.metrics_payload(status="completed")
    cold_context.release_admission()

    warm_ns: list[int] = []
    for sample in range(_PREVIEW_HIT_WARM_SAMPLES):
        context = ExecutionContext(
            operation=f"perf_preview_cache_hit_{sample}",
            profile=ExecutionProfile.PREVIEW_EAGER,
            telemetry_enabled=False,
        )
        started = time.perf_counter_ns()
        warm_result = execute_graph(
            graph,
            target_node_id="api",
            target_preview_only=True,
            requested_preview_columns=["amount"],
            port_label="root",
            execution_context=context,
        )
        warm_ns.append(time.perf_counter_ns() - started)
        assert context.projection_plan is producing_strategy
        context.release_admission()

    warm_median_ns = int(statistics.median(warm_ns))
    assert cold_result["api"].preview == [{"amount": 10}, {"amount": 20}]
    assert warm_result["api"].preview == cold_result["api"].preview
    assert plan_calls == 1
    assert warm_median_ns <= cold_ns * _MAX_PREVIEW_HIT_WARM_FRACTION

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "execution_engine_preview_hit_strategy_reuse",
                "scale": "ci-small-cached-json",
                "execution_profiles": [ExecutionProfile.PREVIEW_EAGER.value],
                "input": {
                    "rows": 2,
                    "warm_samples": _PREVIEW_HIT_WARM_SAMPLES,
                },
                "cold_preview_ns": cold_ns,
                "warm_hit_median_ns": warm_median_ns,
                "speedup": cold_ns / warm_median_ns if warm_median_ns else None,
                "warm_fraction": warm_median_ns / cold_ns if cold_ns else None,
                "warm_fraction_contract": _MAX_PREVIEW_HIT_WARM_FRACTION,
                "strategy_planner_calls": plan_calls,
                "product_metrics": {
                    "n_collects": cold_metrics["n_collects"],
                    "n_checkpoints": cold_metrics["n_checkpoints"],
                    "chunk_count": cold_metrics["chunk_count"],
                    "output_bytes": 0,
                    "temp_disk_peak_bytes": sum(
                        path.stat().st_size
                        for path in _json_cache_dir(source_path, "working").glob("*.parquet")
                    ),
                },
                "admission": {"state": "direct_context", "detail": None},
                "payload_bytes": len(orjson.dumps(warm_result["api"].preview)),
            },
        )
    )


def test_wide_parquet_projection_has_bounded_incremental_rss(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    parquet_path = tmp_path / "wide.parquet"
    columns = _write_wide_parquet(parquet_path)
    projected = _run_collection_probe(tmp_path, parquet_path, mode="projected")
    full = _run_collection_probe(tmp_path, parquet_path, mode="full")

    assert projected["semantic_summary"] == full["semantic_summary"]
    assert projected["rows"] == full["rows"] == _WIDE_ROWS
    assert projected["width"] == len(_SELECTED_WIDE_COLUMNS)
    assert full["width"] == len(columns)
    assert (
        f"PROJECT {len(_SELECTED_WIDE_COLUMNS)}/{len(columns)} COLUMNS"
        in projected["optimized_plan"]
    )
    assert f"PROJECT */{len(columns)} COLUMNS" in full["optimized_plan"]
    assert projected["estimated_size_bytes"] < full["estimated_size_bytes"] / 32

    full_delta = full["incremental_peak_rss_bytes"]
    projected_delta = projected["incremental_peak_rss_bytes"]
    assert full_delta >= full["estimated_size_bytes"] * _MIN_FULL_FRAME_RSS_FRACTION
    assert projected_delta <= (
        full_delta * _MAX_PROJECTED_RSS_FRACTION + _PROJECTED_RSS_ALLOWANCE_BYTES
    )

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "execution_engine_wide_parquet_projection",
                "scale": "ci-wide",
                "execution_profiles": [ExecutionProfile.LAZY_SINK.value],
                "input": {
                    "rows": _WIDE_ROWS,
                    "columns": len(columns),
                    "selected_columns": len(_SELECTED_WIDE_COLUMNS),
                    "total_bytes": parquet_path.stat().st_size,
                },
                "projected": projected,
                "full_width_control": full,
                "rss_contract": {
                    "max_projected_fraction": _MAX_PROJECTED_RSS_FRACTION,
                    "allowance_bytes": _PROJECTED_RSS_ALLOWANCE_BYTES,
                    "min_full_frame_fraction": _MIN_FULL_FRAME_RSS_FRACTION,
                },
                "product_metrics": {
                    "n_collects": 2,
                    "n_checkpoints": 0,
                    "chunk_count": 0,
                    "output_bytes": projected["estimated_size_bytes"],
                    "temp_disk_peak_bytes": parquet_path.stat().st_size,
                },
                "admission": {"state": "isolated_process_control", "detail": None},
                "payload_bytes": len(json.dumps(projected["semantic_summary"])),
            },
        )
    )


def test_cached_api_port_projection_is_physical_and_snapshot_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    monkeypatch.chdir(tmp_path)
    source_path = tmp_path / "wide-api.jsonl"
    config = _write_cached_api_fixture(source_path)
    cache_dir = _json_cache_dir(source_path, "working")
    cache_summary = build_per_port_cache(source_path, config, cache_dir)
    complete_claim_width = len(config["tables"][1]["columns"])
    context = ExecutionContext(
        operation="perf_cached_api_projection",
        profile=ExecutionProfile.TRAINING_PREP,
    )

    with context.stage("cached_api_projection"):
        frames = load_v2_api_source(
            str(source_path),
            config,
            port_columns={"claims": frozenset({"quote_id", "amount_paid"})},
        )
        assert list(frames) == ["claims"]
        frame = frames["claims"]
        assert frame.collect_schema().names() == ["quote_id", "amount_paid"]
        explain = frame.explain(optimized=True)
        result = execution_collect(frame, execution_context=context, engine="streaming")
    snapshots = _snapshot_parquets(tmp_path)
    assert len(snapshots) == 1
    assert f"PROJECT 2/{complete_claim_width} COLUMNS" in explain
    assert result.shape == (_API_ROWS, 2)
    metrics = context.metrics_payload(status="completed")
    context.release_admission()
    assert snapshots[0].exists()
    from haute._json_shred import _runtime_storage

    _runtime_storage._cleanup_runtime_snapshot_dirs()
    assert not snapshots[0].exists()

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "execution_engine_cached_api_port_projection",
                "scale": "ci-wide-port",
                "execution_profiles": [ExecutionProfile.TRAINING_PREP.value],
                "input": {
                    "rows": _API_ROWS,
                    "ports": len(config["tables"]),
                    "complete_claim_width": complete_claim_width,
                    "selected_claim_width": result.width,
                    "total_bytes": source_path.stat().st_size,
                },
                "cache_tables": cache_summary["tables"],
                "optimized_plan": explain,
                "snapshot_count_before_release": len(snapshots),
                "snapshot_retained_by_bounded_verification_cache": True,
                "snapshot_released_on_cache_cleanup": not snapshots[0].exists(),
                "product_metrics": {
                    "n_collects": metrics["n_collects"],
                    "n_checkpoints": metrics["n_checkpoints"],
                    "chunk_count": metrics["chunk_count"],
                    "output_bytes": result.estimated_size(),
                    "temp_disk_peak_bytes": sum(
                        path.stat().st_size for path in cache_dir.glob("*.parquet")
                    ),
                },
                "admission": {"state": "direct_context", "detail": None},
                "payload_bytes": len(orjson.dumps(result.head(1).to_dicts())),
            },
        )
    )


def test_direct_jsonl_projection_has_bounded_checkpoint_distance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    monkeypatch.chdir(tmp_path)
    source_path = tmp_path / "wide-direct.jsonl"
    config = _write_direct_jsonl_fixture(source_path)
    context = ExecutionContext(
        operation="perf_direct_jsonl_projection",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    requested = frozenset({"id", "value_000"})

    with context.stage("direct_jsonl_projection"):
        frame = load_v2_api_source(
            str(source_path),
            config,
            port_columns={"rows": requested},
        )["rows"]
        result = execution_collect(frame, execution_context=context, engine="streaming")
    metrics = context.metrics_payload(status="completed")
    context.release_admission()

    assert result.shape == (_DIRECT_JSONL_ROWS, len(requested))
    assert result.columns == ["id", "value_000"]
    assert not _json_cache_dir(source_path, "working").exists()
    minimum_checkpoints = _DIRECT_JSONL_ROWS // 1_024
    assert metrics["n_checkpoints"] >= minimum_checkpoints

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "execution_engine_direct_jsonl_projection",
                "scale": "ci-wide-jsonl",
                "execution_profiles": [ExecutionProfile.TRAINING_PREP.value],
                "input": {
                    "rows": _DIRECT_JSONL_ROWS,
                    "complete_width": len(config["tables"][0]["columns"]),
                    "selected_width": result.width,
                    "total_bytes": source_path.stat().st_size,
                },
                "minimum_checkpoint_count": minimum_checkpoints,
                "product_metrics": {
                    "n_collects": metrics["n_collects"],
                    "n_checkpoints": metrics["n_checkpoints"],
                    "chunk_count": metrics["chunk_count"],
                    "output_bytes": result.estimated_size(),
                    "temp_disk_peak_bytes": 0,
                },
                "admission": {"state": "direct_context", "detail": None},
                "payload_bytes": len(orjson.dumps(result.head(1).to_dicts())),
            },
        )
    )


def _write_streaming_cache_fixture(
    path: Path,
    *,
    source_format: str,
    rows: int,
) -> None:
    payload = "x" * _STREAM_CACHE_PAYLOAD_BYTES
    with path.open("wb") as stream:
        if source_format == "json":
            stream.write(b"[")
            for row in range(rows):
                if row:
                    stream.write(b",")
                stream.write(orjson.dumps({"id": str(row), "payload": payload}))
            stream.write(b"]")
            return
        if source_format != "xml":
            raise ValueError(f"unsupported structured cache fixture format {source_format!r}")
        stream.write(b"<records>")
        payload_bytes = payload.encode()
        for row in range(rows):
            stream.write(b"<record><id>")
            stream.write(str(row).encode())
            stream.write(b"</id><payload>")
            stream.write(payload_bytes)
            stream.write(b"</payload></record>")
        stream.write(b"</records>")


def _run_streaming_cache_probe(
    tmp_path: Path,
    *,
    source: Path,
    rows: int,
    label: str,
) -> dict[str, Any]:
    result_path = tmp_path / f"{label}-result.json"
    cache_path = tmp_path / f"{label}-cache"
    child_output = io.BytesIO()
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    inherited_paths = [path for path in sys.path if path]
    bootstrap = (
        "import os,runpy,sys;"
        "os.environ['HAUTE_JSON_DIRECT_SPILL_MAX_ROWS']='512';"
        "os.environ['HAUTE_JSON_DIRECT_SPILL_MAX_BYTES']='2097152';"
        f"sys.path[:0]={inherited_paths!r};"
        f"runpy.run_path({str(_STRUCTURED_CACHE_PROBE)!r},run_name='__main__')"
    )
    smoke = run_smoke(
        command=[
            interpreter,
            "-c",
            bootstrap,
            "--source",
            str(source),
            "--cache",
            str(cache_path),
            "--rows",
            str(rows),
            "--output",
            str(result_path),
        ],
        enable_tracemalloc=False,
        poll_interval_seconds=0.005,
        child_output=child_output,
    )
    assert smoke["exit_code"] == 0, child_output.getvalue().decode(errors="replace")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    baseline = result["rss_before_bytes"]
    peak = smoke["child_peak_rss_bytes"]
    assert isinstance(baseline, int) and baseline > 0
    assert isinstance(peak, int) and peak >= baseline
    assert smoke["child_rss_sample_count"] >= 2
    return {
        **result,
        "child_peak_rss_bytes": peak,
        "incremental_peak_rss_bytes": peak - baseline,
        "rss_sample_count": smoke["child_rss_sample_count"],
        "wall_seconds": smoke["elapsed_seconds"],
    }


@pytest.mark.parametrize("source_format", ["json", "xml"])
def test_persistent_structured_cache_build_has_bounded_growth_rss(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    source_format: str,
) -> None:
    suffix = ".json" if source_format == "json" else ".xml"
    small_source = tmp_path / f"small{suffix}"
    large_source = tmp_path / f"large{suffix}"
    _write_streaming_cache_fixture(
        small_source,
        source_format=source_format,
        rows=_STREAM_CACHE_SMALL_ROWS,
    )
    _write_streaming_cache_fixture(
        large_source,
        source_format=source_format,
        rows=_STREAM_CACHE_LARGE_ROWS,
    )

    small = _run_streaming_cache_probe(
        tmp_path,
        source=small_source,
        rows=_STREAM_CACHE_SMALL_ROWS,
        label=f"{source_format}-small",
    )
    large = _run_streaming_cache_probe(
        tmp_path,
        source=large_source,
        rows=_STREAM_CACHE_LARGE_ROWS,
        label=f"{source_format}-large",
    )

    assert large["source_bytes"] >= small["source_bytes"] * 8
    assert large["incremental_peak_rss_bytes"] <= (
        small["incremental_peak_rss_bytes"] * _STREAM_CACHE_MAX_GROWTH_FACTOR
        + _STREAM_CACHE_GROWTH_ALLOWANCE_BYTES
    )

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "persistent_structured_cache_bounded_memory",
                "scale": f"ci-growing-{source_format}",
                "execution_profiles": [ExecutionProfile.LAZY_SINK.value],
                "input": {
                    "format": source_format,
                    "small_rows": _STREAM_CACHE_SMALL_ROWS,
                    "large_rows": _STREAM_CACHE_LARGE_ROWS,
                    "payload_bytes_per_row": _STREAM_CACHE_PAYLOAD_BYTES,
                },
                "small": small,
                "large": large,
                "rss_contract": {
                    "max_growth_factor": _STREAM_CACHE_MAX_GROWTH_FACTOR,
                    "allowance_bytes": _STREAM_CACHE_GROWTH_ALLOWANCE_BYTES,
                },
                "product_metrics": {
                    "n_collects": 0,
                    "n_checkpoints": 0,
                    "chunk_count": 0,
                    "output_bytes": large["cache_bytes"],
                    "temp_disk_peak_bytes": large["cache_bytes"],
                },
                "admission": {"state": "isolated_process_control", "detail": None},
                "payload_bytes": 0,
            },
        )
    )
