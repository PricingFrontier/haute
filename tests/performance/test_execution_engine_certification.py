"""Reproducible performance certification for execution-engine hardening."""

from __future__ import annotations

import io
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._json_flatten import _json_cache_dir
from haute._json_shred import build_per_port_cache, load_v2_api_source
from haute._polars_utils import execution_collect
from scripts.memory_smoke import run_smoke

pytestmark = [pytest.mark.perf, pytest.mark.usefixtures("_widen_sandbox_root")]

_PROBE = Path(__file__).with_name("_execution_engine_probe.py")
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


def test_unchanged_source_signature_reuses_one_complete_content_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Certify the warm path against the exact full-hash control it replaces."""
    import haute._json_shred as shred_mod

    source_path = tmp_path / "signature-source.jsonl"
    block = bytes(range(256)) * 4_096
    assert len(block) == 1024 * 1024
    with source_path.open("wb") as stream:
        for _ in range(_SIGNATURE_SOURCE_BYTES // len(block)):
            stream.write(block)
    assert source_path.stat().st_size == _SIGNATURE_SOURCE_BYTES
    assert shred_mod._strong_file_revision(source_path) is not None

    shred_mod._clear_data_file_signature_memo()
    real_hash_file = shred_mod._hash_file
    source_hashes = 0

    def counting_hash_file(path: Path) -> str:
        nonlocal source_hashes
        if path.resolve() == source_path.resolve():
            source_hashes += 1
        return real_hash_file(path)

    monkeypatch.setattr(shred_mod, "_hash_file", counting_hash_file)
    cold_started = time.perf_counter_ns()
    expected = shred_mod._data_file_signature(source_path)
    cold_ns = time.perf_counter_ns() - cold_started
    warm_ns: list[int] = []
    for _ in range(_SIGNATURE_WARM_SAMPLES):
        started = time.perf_counter_ns()
        assert shred_mod._data_file_signature(source_path) == expected
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
                "snapshot_released": not snapshots[0].exists(),
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
