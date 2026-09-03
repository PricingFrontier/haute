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
from haute._native_memory_limit import native_memory_backend_scope
from haute._polars_operations import OperationPolicy, OperationReceiver, operation
from haute._polars_utils import execution_collect
from haute._ram_estimate import estimate_materialisation_boundaries
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import GroupByExecutionUnsupportedError
from haute.execution import MANY_TO_MANY_JOIN_DETAIL, plan_prepared_execution_strategy
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
_OPERATION_PROBE = Path(__file__).with_name("_operation_memory_probe.py")
_OPERATION_FACT_ROWS = 1_500_000
_OPERATION_DIM_DIVISOR = 4
# Many small row groups (60 of them across the fact file, comfortably more than
# any host's thread count) keep parallel Parquet decoding from holding the whole
# file resident, so the streaming control measures streaming and not the reader.
_OPERATION_ROW_GROUP_SIZE = 25_000
# Two controls. A full-width passthrough sink is the right floor only for an
# operation whose output is the same order of magnitude as its input; for a
# tiny-output operator it is an unfairly high control, so ``scan_head`` supplies
# that floor instead.
_FULL_WIDTH_FLOOR = "scan"
_TINY_OUTPUT_FLOOR = "scan_head"
_NARROW_FLOOR = "scan_narrow"
_GAP_FLOOR = "scan_gaps"
_FULL_WIDTH_OUTPUT_FRACTION = 0.5
# Every ratio is the mean of this many interleaved (operation, control) runs.
# A single fresh-process sample drifted by about +/-20% between batches on the
# development host -- enough for a single-sample ratio to straddle a threshold --
# so pairing is what makes the lane a measurement rather than a coin toss. It
# roughly triples the lane's runtime, to about 70 seconds, which is affordable
# for an opt-in perf marker. Run the lane through pytest, one fresh process per
# run: repeating the test inside one interpreter reuses a warm page cache for the
# fixture and flatters every ratio, so an in-process repeat is not a measurement.
_PAIRED_SAMPLES = 3
# ``interpolate`` is a narrow-frame case whose ratio sits at about 1.24 against a
# 1.30 ceiling, close enough that three pairs let batch drift decide the result.
# More pairs cost time only on that one operation.
_PAIRED_SAMPLES_BY_OPERATION = {"interpolate": 5}
# A streaming policy is the safety-critical direction: an operator wrongly
# recorded as streaming is one the planner will never admit. Every streaming
# operator is bound by the full passthrough control, because a streaming
# pipeline can never need more than the passthrough pipeline: decode buffers
# plus output buffers, and a reducing operator's output buffers are smaller.
_MAX_STREAMING_FLOOR_RATIO = 1.3
# The operators whose measured memory motivated their promotion must also be
# shown not to stream, each against the control matched to its output size --
# ``scan_head`` for a reducing operator, where a full-width sink would be an
# unfairly high floor.
_DOES_NOT_STREAM_WITNESSES = frozenset({"sort", "unique", "join", "explode"})
_MIN_WITNESS_FLOOR_RATIO = 1.25
# Two boundaries cannot be witnessed by their own wide-frame measurement, each
# for a different reason, so each names the variant that does show its state and
# the control that variant is judged against:
#   over        -- a window over a wide frame is dominated by the passthrough's
#                  own buffers; its partition state dominates on a narrow frame.
#   join_asof   -- an asof join buffers its right (lookup) port and streams its
#                  left, so a wide left with a small right sits near the floor;
#                  swapping the ports puts the large frame in the buffered one.
# ``probe`` of ``None`` means the operation's own measurement is the witness.
_WITNESS_VARIANTS = {
    "over": {"probe": "over_narrow", "floor": _NARROW_FLOOR, "min_ratio": 1.5},
    "join_asof": {
        "probe": "join_asof_big_right",
        "floor": _TINY_OUTPUT_FLOOR,
        "min_ratio": _MIN_WITNESS_FLOOR_RATIO,
    },
}
# The cross join is a boundary whose memory nobody has measured, so it is
# certified through the planner's unavailable-estimate contract instead.
_CROSS_JOIN_CODE = "df = fact.join(dim, how='cross')"
# ``join`` is sized from the largest operand it holds, so the case that has to
# be shown is a join whose output outgrows every operand. ``multi`` holds three
# rows per key, so this plan emits three times the fact rows against an
# input-sized estimate. It is a variant of ``join``, not a registry name, so it
# certifies ``estimate_bounds_observation`` only -- no does-not-stream witness.
_JOIN_FANOUT_PROBE = "join_fanout"
_JOIN_FANOUT_CODE = "df = fact.join(multi, on='key', how='inner')"
_JOIN_FANOUT_ROW_FACTOR = 3
# ``explode``'s row expansion is unbounded, so its estimate is unavailable by
# construction and it is certified through the typed rejection instead.
_UNAVAILABLE_ESTIMATE_OPERATIONS = frozenset({"explode"})
_BOUNDARY_ADMISSION_BYTES = 64 * 1024 * 1024 * 1024
_OPERATION_CODE = {
    "group_by": "df = df.group_by('key').agg(pl.col('v1').sum())",
    "sort": "df = df.sort('v1')",
    "unique": "df = df.unique(subset=['key'])",
    "join": "df = fact.join(dim, on='key', how='inner', validate='m:1')",
    "join_asof": "df = fact.join_asof(dim, on='ts')",
    "explode": "df = df.explode('tags')",
    "over": "df = df.with_columns(pl.col('v1').sum().over('key'))",
    "top_k": "df = df.top_k(1000, by='v1')",
    "bottom_k": "df = df.bottom_k(1000, by='v1')",
    "reverse": "df = df.reverse()",
    "interpolate": "df = df.select('key', 'v1_gaps').interpolate()",
}
_TWO_INPUT_OPERATIONS = frozenset({"join", "join_asof"})
# Chained boundaries take the maximum operator factor, so a case whose plan
# contains a second boundary certifies that one instead. Pin the operator set
# and the factor for the asof case, whose fixtures are written pre-sorted
# precisely so it needs no leading sort.
_SINGLE_OPERATOR_FACTORS = {"join_asof": 250}
# ``over`` is an expression method; every other measured name is a frame method.
_OPERATION_RECEIVERS = {"over": OperationReceiver.EXPR}
_STREAMING_POLICIES = frozenset({OperationPolicy.ROW_LOCAL, OperationPolicy.STREAMING})
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


def _write_operation_fixtures(root: Path) -> dict[str, Any]:
    """Write the fact/dim parquets the operation probe plans against."""
    from tests.performance import _operation_memory_probe as probe

    rows = _OPERATION_FACT_ROWS
    dim_rows = rows // _OPERATION_DIM_DIVISOR
    index = pl.int_range(0, rows, eager=True)
    row_index = pl.int_range(0, pl.len())
    fact = pl.DataFrame(
        {
            "key": index % dim_rows,
            "key2": (index % 1000).cast(pl.Utf8),
            "ts": (index * 1000).cast(pl.Datetime("ms")),
            "v1": (index * 7919 % 100_003).cast(pl.Float64) / 7.0,
            "v2": (index * 104_729 % 99_991).cast(pl.Float64),
            "v3": (index % 977).cast(pl.Float64),
            "v4": (index % 13).cast(pl.Float64),
            "v5": (index % 101).cast(pl.Float64),
            "v6": (index % 3).cast(pl.Float64),
            "s1": "row-" + (index % 50_000).cast(pl.Utf8),
        }
    ).with_columns(
        # A straight line with runs of nulls punched across the row-group
        # boundaries, so ``interpolate`` has real work spanning row groups.
        v1_gaps=pl.when(
            (row_index >= probe.GAP_MARGIN)
            & (row_index < rows - probe.GAP_MARGIN)
            & ((row_index + probe.GAP_RUN // 2) % probe.GAP_PERIOD < probe.GAP_RUN)
        )
        .then(None)
        .otherwise(row_index.cast(pl.Float64) * probe.GAP_SLOPE)
        .cast(pl.Float64),
        tags=pl.concat_list(
            pl.col("v4").cast(pl.Int64),
            pl.col("v5").cast(pl.Int64),
            pl.col("v6").cast(pl.Int64),
        ),
    )
    dim_index = pl.int_range(0, dim_rows, eager=True)
    dim = pl.DataFrame(
        {
            "key": dim_index,
            "ts": (dim_index * 4000).cast(pl.Datetime("ms")),
            "d1": (dim_index % 17).cast(pl.Float64),
            "d2": "dim-" + (dim_index % 999).cast(pl.Utf8),
        }
    )
    # Both fixtures are written in ascending ``ts`` order: ``join_asof`` and
    # ``merge_sorted`` require sorted inputs, and pre-sorting them keeps a
    # leading ``sort`` out of those plans, which would otherwise dominate the
    # chained operator factor and certify the wrong operator.
    assert fact["ts"].is_sorted() and dim["ts"].is_sorted()
    fact_path = root / "operation-fact.parquet"
    dim_path = root / "operation-dim.parquet"
    fact.write_parquet(fact_path, row_group_size=_OPERATION_ROW_GROUP_SIZE)
    dim.write_parquet(dim_path, row_group_size=_OPERATION_ROW_GROUP_SIZE)
    # Three rows per dim key, so an inner join on ``key`` emits three times the
    # fact rows: the fan-out case whose output exceeds its largest operand.
    multi_index = pl.int_range(0, dim_rows * _JOIN_FANOUT_ROW_FACTOR, eager=True)
    multi = pl.DataFrame(
        {
            "key": multi_index // _JOIN_FANOUT_ROW_FACTOR,
            "m1": (multi_index % 19).cast(pl.Float64),
            "m2": "multi-" + (multi_index % 997).cast(pl.Utf8),
        }
    )
    multi_path = root / "operation-multi.parquet"
    multi.write_parquet(multi_path, row_group_size=_OPERATION_ROW_GROUP_SIZE)
    return {
        "fact_path": fact_path,
        "dim_path": dim_path,
        "multi_path": multi_path,
        "multi_rows": multi.height,
        "fact_rows": fact.height,
        "fact_columns": fact.width,
        "fact_estimated_size_bytes": fact.estimated_size(),
        "fact_row_groups": -(-fact.height // _OPERATION_ROW_GROUP_SIZE),
        "fact_gap_null_count": int(fact["v1_gaps"].null_count()),
        "dim_rows": dim.height,
        "dim_estimated_size_bytes": dim.estimated_size(),
    }


def _run_operation_memory_probe(
    tmp_path: Path,
    fixtures: dict[str, Any],
    operation_name: str,
    *,
    keep_sink: bool = False,
) -> dict[str, Any]:
    result_path = tmp_path / f"operation-{operation_name}.json"
    child_output = io.BytesIO()
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    inherited_paths = [path for path in sys.path if path]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path[:0]={inherited_paths!r};"
        f"runpy.run_path({str(_OPERATION_PROBE)!r},run_name='__main__')"
    )
    smoke = run_smoke(
        command=[
            interpreter,
            "-c",
            bootstrap,
            "--operation",
            operation_name,
            "--fact",
            str(fixtures["fact_path"]),
            "--dim",
            str(fixtures["dim_path"]),
            "--multi",
            str(fixtures["multi_path"]),
            "--output",
            str(result_path),
            *(["--keep-sink"] if keep_sink else []),
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


def _verify_interpolated_output(sink_path: Path) -> dict[str, Any]:
    """Prove the sunk output is really interpolated, in the *parent* process.

    This must not run inside the sampled child: an eager read of the whole
    result would land in the child's lifetime peak and be attributed to the
    operator. The parent reads it lazily after ``run_smoke`` has returned.
    """
    from tests.performance import _operation_memory_probe as probe

    sunk = pl.scan_parquet(sink_path)
    # ``v1_gaps`` is a straight line in the row index, and interpolate preserves
    # order and row count, so the index is recoverable here.
    summary = (
        sunk.with_row_index("row_index")
        .select(
            interior_null_count=pl.col("v1_gaps").is_null().sum(),
            sampled_rows=pl.col("row_index")
            .is_between(probe.GAP_MARGIN, probe.GAP_MARGIN + 4 * probe.GAP_PERIOD)
            .sum(),
            max_absolute_error=(
                pl.when(
                    pl.col("row_index").is_between(
                        probe.GAP_MARGIN, probe.GAP_MARGIN + 4 * probe.GAP_PERIOD
                    )
                )
                .then(
                    (
                        pl.col("v1_gaps") - pl.col("row_index").cast(pl.Float64) * probe.GAP_SLOPE
                    ).abs()
                )
                .otherwise(None)
            ).max(),
        )
        .collect()
        .to_dicts()[0]
    )
    return {
        "column": "v1_gaps",
        "interior_null_count": int(summary["interior_null_count"]),
        "sampled_rows": int(summary["sampled_rows"]),
        "max_absolute_error": float(summary["max_absolute_error"] or 0.0),
        "matches_linear_interpolation": (summary["max_absolute_error"] or 0.0) <= 1e-6,
        "verified_in": "parent",
    }


def _registered_operation_policy(operation_name: str) -> OperationPolicy:
    receiver = _OPERATION_RECEIVERS.get(operation_name, OperationReceiver.FRAME)
    registered = operation(receiver, operation_name)
    assert registered is not None, f"{operation_name} is not registered for {receiver}"
    return registered.policy


def _boundary_graph(
    operation_name: str,
    fixtures: dict[str, Any],
    *,
    code: str | None = None,
    two_input: bool | None = None,
    second_input: str = "dim",
):
    """Build the two- or three-node graph the planner admits for an operation.

    ``second_input`` names the graph's second port and selects its fixture, so a
    variant can join against a different frame than ``dim``.
    """
    from tests.conftest import make_edge, make_graph, make_ready_file_input_config

    if two_input is None:
        two_input = operation_name in _TWO_INPUT_OPERATIONS
    fact_label = "fact" if two_input else "df"
    config: dict[str, Any] = {"code": code or _OPERATION_CODE[operation_name]}
    nodes = [
        {
            "id": fact_label,
            "data": {
                "label": fact_label,
                "nodeType": "dataInput",
                "config": make_ready_file_input_config(fixtures["fact_path"]),
            },
        }
    ]
    edges = [make_edge(fact_label, "op").model_dump()]
    if two_input:
        nodes.append(
            {
                "id": second_input,
                "data": {
                    "label": second_input,
                    "nodeType": "dataInput",
                    "config": make_ready_file_input_config(fixtures[f"{second_input}_path"]),
                },
            }
        )
        edges.append(make_edge(second_input, "op").model_dump())
        # A fan-in Polars node needs a declared per-parent contract before the
        # analyser will attribute either frame root, exactly as production does.
        config["contract"] = {
            "inputs": ["key", "ts", "v1"],
            "outputs": [],
            "inputs_by_parent": {
                "fact": ["key", "ts", "v1"],
                # ``multi`` has no time axis; only ``dim`` carries ``ts``.
                second_input: ["key", "ts"] if second_input == "dim" else ["key"],
            },
        }
    nodes.append(
        {
            "id": "op",
            "data": {"label": "op", "nodeType": "polars", "config": config},
        }
    )
    return make_graph({"nodes": nodes, "edges": edges})


def _plan_boundary(
    operation_name: str,
    fixtures: dict[str, Any],
    *,
    code: str | None = None,
    two_input: bool | None = None,
    second_input: str = "dim",
):
    """Plan the operation as the executor would, under an ample admission."""
    from haute.execution import ProjectionRequest, plan_execution_strategy

    profile = ExecutionProfile.LAZY_SINK
    context = ExecutionContext(
        operation="operation_memory_certification",
        profile=profile,
        admission=ExecutionAdmission(
            operation="operation_memory_certification",
            profile=profile,
            memory_limit_bytes=_BOUNDARY_ADMISSION_BYTES,
            rss_at_admission_bytes=0,
            rss_limit_bytes=_BOUNDARY_ADMISSION_BYTES,
            headroom_bytes=_BOUNDARY_ADMISSION_BYTES,
            config_key="operation_memory_certification",
        ),
    )
    try:
        return plan_execution_strategy(
            ProjectionRequest(
                graph=_boundary_graph(
                    operation_name,
                    fixtures,
                    code=code,
                    two_input=two_input,
                    second_input=second_input,
                ),
                target_node_id="op",
                profile=profile,
            ),
            execution_context=context,
        )
    finally:
        context.release_admission()


def _control_for(
    operation_name: str, policy: OperationPolicy, rows_out: int, fact_rows: int
) -> str:
    """Return the streaming control this operation's ratio is measured against."""
    from tests.performance._operation_memory_probe import (
        GAP_COLUMN_OPERATIONS,
        NARROW_OPERATIONS,
    )

    if policy in _STREAMING_POLICIES:
        if operation_name in GAP_COLUMN_OPERATIONS:
            return _GAP_FLOOR
        return _NARROW_FLOOR if operation_name in NARROW_OPERATIONS else _FULL_WIDTH_FLOOR
    if rows_out >= fact_rows * _FULL_WIDTH_OUTPUT_FRACTION:
        return _FULL_WIDTH_FLOOR
    return _TINY_OUTPUT_FLOOR


def _paired_measurement(
    tmp_path: Path,
    fixtures: dict[str, Any],
    operation_name: str,
    *,
    control_name: str | None = None,
    choose_control: Any = None,
    keep_sink: bool = False,
) -> dict[str, Any]:
    """Measure one operation against its control as interleaved paired runs.

    A single fresh-process sample is not a stable measurement: the same control
    varied by about +/-20% between batches on the development host, enough for a
    single-sample ratio to straddle a threshold. Alternating operation and
    control runs cancels that drift. Controls are re-run inside the pairs that
    use them rather than sampled once and shared, so no two operations lean on
    the same control sample.

    The ratio is taken over the two *means*, not the medians. Peak RSS here is
    bimodal -- samples cluster around two values about 35 MiB apart, which is the
    granularity of a streaming chunk buffer rather than continuous noise -- and a
    median of three snaps to whichever mode won two of the three samples, so it
    jumps between modes instead of settling. The mean is the stable estimator of
    average cost over a discrete allocation pattern like this one. Medians are
    still recorded for information.
    """
    samples = _PAIRED_SAMPLES_BY_OPERATION.get(operation_name, _PAIRED_SAMPLES)
    runs = [_run_operation_memory_probe(tmp_path, fixtures, operation_name, keep_sink=keep_sink)]
    if control_name is None:
        control_name = choose_control(runs[0])
    control_runs: list[dict[str, Any]] = []
    for index in range(samples):
        control_runs.append(_run_operation_memory_probe(tmp_path, fixtures, control_name))
        if index < samples - 1:
            runs.append(
                _run_operation_memory_probe(tmp_path, fixtures, operation_name, keep_sink=keep_sink)
            )
    operation_samples = [run["incremental_peak_rss_bytes"] for run in runs]
    control_samples = [run["incremental_peak_rss_bytes"] for run in control_runs]
    operation_mean = statistics.fmean(operation_samples)
    control_mean = statistics.fmean(control_samples)
    assert control_mean > 0
    return {
        "operation": operation_name,
        "control": control_name,
        "samples": samples,
        "operation_samples": operation_samples,
        "control_samples": control_samples,
        "operation_mean_bytes": operation_mean,
        "control_mean_bytes": control_mean,
        "operation_median_bytes": statistics.median(operation_samples),
        "control_median_bytes": statistics.median(control_samples),
        "ratio": operation_mean / control_mean,
        "last_run": runs[-1],
    }


def test_global_operation_memory_policies_match_the_registry(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Certify each global operation's registry policy against measured peak RSS.

    The expected policy is read from ``haute._polars_operations`` at runtime, so
    the registry and this evidence lane cannot drift apart. Every operation is
    measured as the mean of three interleaved (operation, control) pairs, and
    every control is re-run inside the pairs that use it rather than sampled once
    and shared, so host drift cancels instead of accumulating into a ratio.
    """
    from haute._polars_operations import measured_operation_names
    from tests.performance._operation_memory_probe import (
        ALIASES,
        CONTROLS,
        OPERATIONS,
        WITNESS_PROBES,
    )

    # Registry completeness: every entry the registry claims memory evidence for
    # must have a plan here, so adding a measured entry without a measurement
    # fails this lane rather than silently going uncertified.
    buildable = set(OPERATIONS) | set(ALIASES)
    measured_names = measured_operation_names(OperationReceiver.FRAME) | measured_operation_names(
        OperationReceiver.EXPR
    )
    assert measured_names <= buildable, (
        "registry entries claim memory evidence with no probe plan: "
        f"{sorted(measured_names - buildable)}"
    )

    fixtures = _write_operation_fixtures(tmp_path)
    measurements: list[dict[str, Any]] = []
    failures: list[str] = []
    environment: dict[str, Any] = {}
    for operation_name in OPERATIONS:
        if operation_name in CONTROLS or operation_name in WITNESS_PROBES:
            continue
        policy = _registered_operation_policy(operation_name)
        verifies_real_work = operation_name == "interpolate"
        paired = _paired_measurement(
            tmp_path,
            fixtures,
            operation_name,
            choose_control=lambda first, name=operation_name, current=policy: _control_for(
                name, current, first["rows_out"], fixtures["fact_rows"]
            ),
            keep_sink=verifies_real_work,
        )
        measured = paired["last_run"]
        environment = environment or {
            "polars_version": measured["polars_version"],
            "polars_threads": measured["polars_threads"],
            "streaming_chunk_size": measured["streaming_chunk_size"],
        }
        incremental = paired["operation_mean_bytes"]
        ratio = paired["ratio"]
        control_name = paired["control"]
        verification = None
        if verifies_real_work:
            sink_path = Path(measured["sink_path"])
            verification = _verify_interpolated_output(sink_path)
            sink_path.unlink()
        record: dict[str, Any] = {
            "operation": operation_name,
            "policy": policy.value,
            "rows_out": measured["rows_out"],
            "incremental_peak_rss_bytes": incremental,
            "control": control_name,
            "paired": {
                key: paired[key]
                for key in (
                    "samples",
                    "operation_samples",
                    "control_samples",
                    "operation_mean_bytes",
                    "control_mean_bytes",
                    "operation_median_bytes",
                    "control_median_bytes",
                )
            },
            "ratio": ratio,
            "elapsed_seconds": measured["elapsed_seconds"],
            "estimated_peak_bytes": None,
            "estimate_state": None,
            "verification": verification,
            "checks": [],
        }

        def _check(name: str, satisfied: bool, detail: str) -> None:
            record["checks"].append({"check": name, "satisfied": satisfied, "detail": detail})
            if not satisfied:
                failures.append(f"{operation_name} ({policy.value}) {name}: {detail}")

        if verification is not None:
            _check(
                "operation_did_real_work",
                verification["interior_null_count"] == 0
                and verification["sampled_rows"] > 0
                and verification["matches_linear_interpolation"],
                "sunk output must have no nulls left and match linear interpolation: "
                f"{verification}",
            )

        if policy in _STREAMING_POLICIES:
            _check(
                "streams_within_passthrough",
                ratio <= _MAX_STREAMING_FLOOR_RATIO,
                f"paired-mean ratio={ratio:.2f} must be <= "
                f"{_MAX_STREAMING_FLOOR_RATIO} of the {control_name} control",
            )
        elif policy is OperationPolicy.MATERIALISATION_BOUNDARY:
            if operation_name in _UNAVAILABLE_ESTIMATE_OPERATIONS:
                rejected = None
                try:
                    _plan_boundary(operation_name, fixtures)
                except GroupByExecutionUnsupportedError as error:
                    rejected = error.reason_code
                record["estimate_state"] = rejected or "available"
                _check(
                    "estimate_unavailable",
                    rejected == "materialisation_estimate_unavailable",
                    f"unbounded expansion must reject as unavailable, got {rejected!r}",
                )
            else:
                try:
                    planned = _plan_boundary(operation_name, fixtures)
                except GroupByExecutionUnsupportedError as error:
                    # A mathematically unconstrained join bound can exceed any
                    # admission. The estimate is still available and is still
                    # the number that must bound the observation.
                    estimated = error.estimated_peak_bytes
                    record["estimate_state"] = error.reason_code
                    record["blocking_operator"] = error.operator
                else:
                    estimated = planned.diagnostic.estimated_peak_bytes
                    record["estimate_state"] = planned.status.value
                    record["blocking_operator"] = planned.diagnostic.blocking_operator
                    expected_factor = _SINGLE_OPERATOR_FACTORS.get(operation_name)
                    if expected_factor is not None:
                        assumptions = set(planned.diagnostic.assumptions)
                        record["boundary_assumptions"] = sorted(
                            item
                            for item in assumptions
                            if "boundary_operator" in item or "factor_basis_points" in item
                        )
                        _check(
                            "certifies_its_own_factor",
                            f"op: boundary_operators={operation_name}" in assumptions
                            and f"op: materialisation_factor_basis_points={expected_factor}"
                            in assumptions,
                            f"plan must record only {operation_name} at "
                            f"{expected_factor} basis points, got "
                            f"{record['boundary_assumptions']}",
                        )
                record["estimated_peak_bytes"] = estimated
                _check(
                    "estimate_bounds_observation",
                    isinstance(estimated, int) and estimated >= incremental,
                    f"estimate={estimated} must bound observed mean peak={incremental}",
                )
            if operation_name in _DOES_NOT_STREAM_WITNESSES:
                _check(
                    "does_not_stream",
                    ratio >= _MIN_WITNESS_FLOOR_RATIO,
                    f"paired-mean ratio={ratio:.2f} must be >= {_MIN_WITNESS_FLOOR_RATIO} "
                    f"of the {control_name} control",
                )
            variant = _WITNESS_VARIANTS.get(operation_name)
            if variant is not None:
                variant_paired = _paired_measurement(
                    tmp_path,
                    fixtures,
                    variant["probe"],
                    control_name=variant["floor"],
                )
                record["witness_variant"] = {
                    **{
                        key: variant_paired[key]
                        for key in (
                            "operation",
                            "control",
                            "samples",
                            "operation_samples",
                            "control_samples",
                            "operation_mean_bytes",
                            "control_mean_bytes",
                            "operation_median_bytes",
                            "control_median_bytes",
                            "ratio",
                        )
                    },
                    "min_ratio": variant["min_ratio"],
                    "certifies": "does_not_stream",
                    "wide_case_certifies": "estimate_bounds_observation and own factor",
                }
                _check(
                    "does_not_stream_variant",
                    variant_paired["ratio"] >= variant["min_ratio"],
                    f"{variant['probe']} paired-mean ratio={variant_paired['ratio']:.2f} "
                    f"must be >= {variant['min_ratio']} of the {variant['floor']} control",
                )
        else:
            raise AssertionError(f"{operation_name} has unmeasurable policy {policy}")

        measurements.append(record)

    # ``join`` is sized from the largest operand it holds only when a declared
    # uniqueness contract bounds its output by that operand. This variant has no
    # contract and emits three times the fact rows, which is exactly the case the
    # input-sized figure does not bound -- so it is certified through the
    # planner's policy for an unbounded join rather than against a number.
    fanout_paired = _paired_measurement(
        tmp_path,
        fixtures,
        _JOIN_FANOUT_PROBE,
        control_name=_FULL_WIDTH_FLOOR,
    )
    fanout_measured = fanout_paired["last_run"]
    fanout_incremental = fanout_paired["operation_mean_bytes"]

    def _plan_fanout():
        return _plan_boundary(
            "join",
            fixtures,
            code=_JOIN_FANOUT_CODE,
            two_input=True,
            second_input="multi",
        )

    with native_memory_backend_scope("rlimit"):
        capped_fanout = _plan_fanout()
    capped_status = capped_fanout.status.value
    capped_strategy = capped_fanout.diagnostic.strategy.value
    capped_assumptions = list(capped_fanout.diagnostic.assumptions)
    uncapped_reason: str | None = None
    uncapped_remediation = ""
    try:
        _plan_fanout()
    except GroupByExecutionUnsupportedError as error:
        uncapped_reason = error.reason_code
        uncapped_remediation = error.remediation
    expected_fanout_rows = fixtures["fact_rows"] * _JOIN_FANOUT_ROW_FACTOR
    declared_join_estimate = next(
        (
            record["estimated_peak_bytes"]
            for record in measurements
            if record["operation"] == "join"
        ),
        None,
    )
    join_fanout = {
        "operation": _JOIN_FANOUT_PROBE,
        "certifies": "join_cardinality_many_to_many policy",
        "code": _JOIN_FANOUT_CODE,
        "control": fanout_paired["control"],
        "rows_out": fanout_measured["rows_out"],
        "expected_rows_out": expected_fanout_rows,
        "row_factor": _JOIN_FANOUT_ROW_FACTOR,
        "incremental_peak_rss_bytes": fanout_incremental,
        "declared_join_estimated_peak_bytes": declared_join_estimate,
        "exceeds_declared_join_estimate": bool(
            isinstance(declared_join_estimate, int) and fanout_incremental > declared_join_estimate
        ),
        "capped_status": capped_status,
        "capped_strategy": capped_strategy,
        "capped_proof_gap": [item for item in capped_assumptions if item.startswith("proof_gap=")],
        "uncapped_reason_code": uncapped_reason,
        "paired": {
            key: fanout_paired[key]
            for key in (
                "samples",
                "operation_samples",
                "control_samples",
                "operation_mean_bytes",
                "control_mean_bytes",
                "operation_median_bytes",
                "control_median_bytes",
            )
        },
        "ratio": fanout_paired["ratio"],
    }
    if fanout_measured["rows_out"] != expected_fanout_rows:
        failures.append(
            f"join_fanout must emit {expected_fanout_rows} rows "
            f"({_JOIN_FANOUT_ROW_FACTOR}x the fact rows), got {fanout_measured['rows_out']}"
        )
    if not (
        capped_status == "warned"
        and capped_strategy == "full-width-conservative"
        and f"proof_gap=op:{MANY_TO_MANY_JOIN_DETAIL}" in capped_assumptions
    ):
        failures.append(
            "an undeclared join under a hard cap must plan warned/full-width-conservative "
            f"with proof_gap=op:{MANY_TO_MANY_JOIN_DETAIL}, got "
            f"{capped_status}/{capped_strategy} {join_fanout['capped_proof_gap']}"
        )
    if not (
        uncapped_reason == "materialisation_estimate_unavailable"
        and MANY_TO_MANY_JOIN_DETAIL in uncapped_remediation
    ):
        failures.append(
            "an undeclared join without a hard cap must reject as "
            f"materialisation_estimate_unavailable naming {MANY_TO_MANY_JOIN_DETAIL}, got "
            f"{uncapped_reason!r}: {uncapped_remediation!r}"
        )

    # A cross join is an admitted boundary whose memory nobody has measured, so
    # it is certified through the planner rather than a probe: no estimate.
    cross_join_reason = None
    try:
        _plan_boundary("join", fixtures, code=_CROSS_JOIN_CODE, two_input=True)
    except GroupByExecutionUnsupportedError as error:
        cross_join_reason = error.reason_code
    cross_join = {
        "code": _CROSS_JOIN_CODE,
        "reason_code": cross_join_reason,
        "satisfied": cross_join_reason == "materialisation_estimate_unavailable",
    }
    if not cross_join["satisfied"]:
        failures.append(
            f"cross join must reject with an unavailable estimate, got {cross_join_reason!r}"
        )

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "global_operation_memory_policies",
                "scale": "ci-operation-memory",
                "execution_profiles": [ExecutionProfile.LAZY_SINK.value],
                **environment,
                "input": {
                    "fact_rows": fixtures["fact_rows"],
                    "fact_columns": fixtures["fact_columns"],
                    "fact_estimated_size_bytes": fixtures["fact_estimated_size_bytes"],
                    "fact_row_groups": fixtures["fact_row_groups"],
                    "fact_gap_null_count": fixtures["fact_gap_null_count"],
                    "dim_rows": fixtures["dim_rows"],
                    "dim_estimated_size_bytes": fixtures["dim_estimated_size_bytes"],
                    "multi_rows": fixtures["multi_rows"],
                    "row_group_size": _OPERATION_ROW_GROUP_SIZE,
                },
                "measurements": measurements,
                "join_fanout": join_fanout,
                "cross_join": cross_join,
                "rss_contract": {
                    "paired_samples": _PAIRED_SAMPLES,
                    "paired_samples_by_operation": dict(_PAIRED_SAMPLES_BY_OPERATION),
                    "full_width_output_fraction": _FULL_WIDTH_OUTPUT_FRACTION,
                    "max_streaming_floor_ratio": _MAX_STREAMING_FLOOR_RATIO,
                    "min_witness_floor_ratio": _MIN_WITNESS_FLOOR_RATIO,
                    "does_not_stream_witnesses": sorted(_DOES_NOT_STREAM_WITNESSES),
                    "witness_variants": _WITNESS_VARIANTS,
                    "boundary_admission_bytes": _BOUNDARY_ADMISSION_BYTES,
                },
                "product_metrics": {
                    "n_collects": sum(record["paired"]["samples"] * 2 for record in measurements),
                    "n_checkpoints": 0,
                    "chunk_count": 0,
                    "output_bytes": 0,
                    "temp_disk_peak_bytes": fixtures["fact_path"].stat().st_size
                    + fixtures["dim_path"].stat().st_size
                    + fixtures["multi_path"].stat().st_size,
                },
                "admission": {"state": "isolated_process_control", "detail": None},
                "payload_bytes": 0,
            },
        )
    )
    assert not failures, "registry policy contradicted by measured peak RSS: " + "; ".join(failures)
