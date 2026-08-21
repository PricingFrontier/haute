"""Contracts for mutation-suite sharding and serial shard execution.

These exercise the SQLite session plumbing that lets a single ``cosmic-ray init``
session be split into disjoint mutant shards, executed independently, and
recombined. The load-bearing invariant is that recombining disjoint shard
results reproduces the *exact* per-mutant result set of an unsharded run, so the
aggregated survival rate the gate checks is identical to the single-run survival
rate. We prove that here at the database level (no Cosmic Ray needed); the full
end-to-end equivalence on a real target is proven separately by running
``--shards`` against the same target unsharded.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
import yaml

from scripts.run_mutation_suite import (
    REPO_ROOT,
    _all_job_ids,
    _count_items_and_results,
    _partition_job_ids,
    _partition_pending_job_ids,
    _pending_job_ids,
    _shard_count_for_pending,
    _slice_session,
    _union_results_into,
    _validate_shard_matrix_capacity,
)

MODULE_PATH = str((REPO_ROOT / "src" / "haute" / "_json_shred.py").resolve())


def _create_session(path: Path, job_ids: list[str]) -> None:
    """Create a session DB shaped like a ``cosmic-ray init`` session."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE work_items (job_id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE mutation_specs ("
            "module_path TEXT, operator_name TEXT, operator_args TEXT, occurrence INTEGER, "
            "start_pos_row INTEGER, start_pos_col INTEGER, end_pos_row INTEGER, "
            "end_pos_col INTEGER, definition_name TEXT, job_id TEXT PRIMARY KEY, "
            "FOREIGN KEY(job_id) REFERENCES work_items(job_id))"
        )
        conn.execute(
            "CREATE TABLE work_results ("
            "worker_outcome TEXT, output TEXT, test_outcome TEXT, diff TEXT, "
            "job_id TEXT PRIMARY KEY, "
            "FOREIGN KEY(job_id) REFERENCES work_items(job_id))"
        )
        for occurrence, job_id in enumerate(job_ids):
            conn.execute("INSERT INTO work_items(job_id) VALUES (?)", (job_id,))
            conn.execute(
                "INSERT INTO mutation_specs("
                "module_path, operator_name, operator_args, occurrence, "
                "start_pos_row, start_pos_col, end_pos_row, end_pos_col, definition_name, job_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (MODULE_PATH, "core/NumberReplacer", "{}", occurrence, 1, 0, 1, 1, None, job_id),
            )
        conn.commit()
    finally:
        conn.close()


def _set_result(path: Path, job_id: str, test_outcome: str | None, worker_outcome: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO work_results"
            "(job_id, worker_outcome, output, test_outcome, diff) VALUES (?, ?, ?, ?, ?)",
            (job_id, worker_outcome, "", test_outcome, ""),
        )
        conn.commit()
    finally:
        conn.close()


def _results_map(path: Path) -> dict[str, str | None]:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT job_id, test_outcome FROM work_results").fetchall()
    finally:
        conn.close()
    return {job_id: outcome for job_id, outcome in rows}


def _survival_rate(path: Path) -> float:
    """Reimplements cosmic_ray.tools.survival_rate over completed results."""
    results = _results_map(path)
    if not results:
        return 0.0
    kills = sum(1 for outcome in results.values() if outcome != "survived")
    return (1 - kills / len(results)) * 100


# --- partition ------------------------------------------------------------


def test_partition_is_disjoint_covering_and_balanced() -> None:
    job_ids = [f"job-{i:03d}" for i in range(101)]
    buckets = _partition_job_ids(job_ids, 4)

    assert len(buckets) == 4
    flattened = [job_id for bucket in buckets for job_id in bucket]
    assert sorted(flattened) == sorted(job_ids)  # covering
    assert len(set(flattened)) == len(flattened)  # disjoint
    sizes = [len(bucket) for bucket in buckets]
    assert max(sizes) - min(sizes) <= 1  # balanced


def test_partition_is_deterministic() -> None:
    job_ids = [f"job-{i:03d}" for i in range(50)]
    assert _partition_job_ids(job_ids, 3) == _partition_job_ids(job_ids, 3)


def test_pending_partition_excludes_pragma_results_and_stays_balanced(tmp_path: Path) -> None:
    session = tmp_path / "session.sqlite"
    job_ids = [f"job-{i:03d}" for i in range(257)]
    _create_session(session, job_ids)
    pragma = set(job_ids[::5])
    for job_id in pragma:
        _set_result(session, job_id, None, "skipped")

    buckets = _partition_pending_job_ids(session, 3)

    flattened = [job_id for bucket in buckets for job_id in bucket]
    assert set(flattened) == set(job_ids) - pragma
    assert not set(flattened) & pragma
    assert max(map(len, buckets)) - min(map(len, buckets)) <= 1


def test_shard_count_scales_with_pending() -> None:
    assert _shard_count_for_pending(0, 80) == 1
    assert _shard_count_for_pending(80, 80) == 1
    assert _shard_count_for_pending(81, 80) == 2
    assert _shard_count_for_pending(766, 80) == 10  # ceil(766 / 80)
    assert _shard_count_for_pending(4_941, 48) == 103
    assert _shard_count_for_pending(10_000, 80) == 125
    assert _shard_count_for_pending(10**100, 3) == ((10**100 + 2) // 3)


@pytest.mark.parametrize("pending", [-1, True, 1.5])
def test_shard_count_rejects_invalid_pending(pending: object) -> None:
    with pytest.raises(ValueError, match="pending must be a non-negative integer"):
        _shard_count_for_pending(pending, 80)  # type: ignore[arg-type]


@pytest.mark.parametrize("cap", [0, -1, True, 1.5])
def test_shard_count_rejects_invalid_cap(cap: object) -> None:
    with pytest.raises(ValueError, match="max_pending_per_shard must be a positive integer"):
        _shard_count_for_pending(80, cap)  # type: ignore[arg-type]


def test_current_target_plan_stays_within_matrix_capacity() -> None:
    pending_and_caps = (
        (435, 80),
        (93, 80),
        (87, 80),
        (616, 80),
        (68, 80),
        (4_941, 48),
        (413, 80),
        (1_576, 80),
    )

    shard_count = sum(
        _shard_count_for_pending(pending, max_pending_per_shard)
        for pending, max_pending_per_shard in pending_and_caps
    )

    assert shard_count == 148
    _validate_shard_matrix_capacity(shard_count)


def test_shard_matrix_capacity_fails_before_github_expansion() -> None:
    _validate_shard_matrix_capacity(256)

    with pytest.raises(ValueError, match="257.*256"):
        _validate_shard_matrix_capacity(257)


# --- slice ----------------------------------------------------------------


def test_slice_keeps_only_requested_job_ids(tmp_path: Path) -> None:
    job_ids = [f"job-{i:02d}" for i in range(10)]
    src = tmp_path / "full.sqlite"
    _create_session(src, job_ids)
    _set_result(src, "job-00", "survived", "normal")

    keep = ["job-00", "job-03", "job-07"]
    dst = tmp_path / "slice.sqlite"
    _slice_session(src, dst, keep)

    assert _all_job_ids(dst) == sorted(keep)
    conn = sqlite3.connect(str(dst))
    try:
        specs = [row[0] for row in conn.execute("SELECT job_id FROM mutation_specs").fetchall()]
        results = [row[0] for row in conn.execute("SELECT job_id FROM work_results").fetchall()]
    finally:
        conn.close()
    assert sorted(specs) == sorted(keep)  # child rows pruned with their work_item
    assert results == ["job-00"]  # pragma result retained only for a kept item
    # the source session is untouched
    assert _all_job_ids(src) == sorted(job_ids)


def test_mutation_gate_runs_and_fails_when_plan_fails() -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "mutation.yml").read_text(encoding="utf-8")
    )
    gate = workflow["jobs"]["mutation"]

    assert set(gate["needs"]) == {"plan", "shard"}
    condition = re.sub(r"\s+", "", gate["if"])
    assert "!cancelled()" in condition
    assert "success()" not in condition
    assert "needs." not in condition

    steps_by_name = {step.get("name"): step for step in gate["steps"] if "name" in step}
    plan_guard = steps_by_name["Require a successful mutation plan"]
    guard_condition = re.sub(r"\s+", "", plan_guard["if"])
    assert "needs.plan.result!='success'" in guard_condition
    assert any(line.strip() == "exit 1" for line in plan_guard["run"].splitlines())


# --- union / round-trip equivalence ---------------------------------------


def test_sharded_execution_reproduces_unsharded_results(tmp_path: Path) -> None:
    """The load-bearing proof: disjoint shard results recombine to the exact
    same per-mutant result set (and therefore survival rate) as one exec.
    """
    job_ids = [f"job-{i:04d}" for i in range(200)]

    # The init+pragma base session: a deterministic subset is pragma-skipped
    # (already resulted), the rest are pending.
    base = tmp_path / "base.sqlite"
    _create_session(base, job_ids)
    pragma = {job_id for i, job_id in enumerate(job_ids) if i % 7 == 0}
    for job_id in pragma:
        _set_result(base, job_id, None, "skipped")

    # The oracle: what exec would record for each pending mutant. A handful
    # survive; the rest are killed. Independent of sharding by construction.
    def oracle_outcome(job_id: str) -> str:
        return "survived" if int(job_id.split("-")[1]) % 25 == 0 else "killed"

    pending = [job_id for job_id in job_ids if job_id not in pragma]

    # Unsharded reference run.
    unsharded = tmp_path / "unsharded.sqlite"
    _slice_session(base, unsharded, job_ids)  # full copy
    for job_id in pending:
        _set_result(unsharded, job_id, oracle_outcome(job_id), "normal")

    # Sharded run: partition ALL ids, slice each shard, exec its pending items,
    # then union every shard's results back onto a fresh copy of base.
    shard_count = 5
    shard_sessions: list[Path] = []
    for shard_index, bucket in enumerate(_partition_pending_job_ids(base, shard_count)):
        shard = tmp_path / f"shard-{shard_index}.sqlite"
        _slice_session(base, shard, bucket)
        for job_id in _pending_job_ids(shard):
            _set_result(shard, job_id, oracle_outcome(job_id), "normal")
        shard_sessions.append(shard)

    merged = tmp_path / "merged.sqlite"
    _slice_session(base, merged, job_ids)  # fresh full copy (init+pragma)
    _union_results_into(merged, shard_sessions)

    # Every mutant has exactly one result, and it matches the unsharded run.
    items, results = _count_items_and_results(merged)
    assert results == items == len(job_ids)
    assert _results_map(merged) == _results_map(unsharded)
    assert _survival_rate(merged) == _survival_rate(unsharded)


def test_union_is_idempotent_for_pragma_results(tmp_path: Path) -> None:
    job_ids = ["job-0", "job-1", "job-2"]
    base = tmp_path / "base.sqlite"
    _create_session(base, job_ids)
    _set_result(base, "job-0", None, "skipped")

    shard = tmp_path / "shard.sqlite"
    _slice_session(base, shard, ["job-0"])  # carries the pragma result forward

    merged = tmp_path / "merged.sqlite"
    _slice_session(base, merged, job_ids)
    _union_results_into(merged, [shard])
    _union_results_into(merged, [shard])  # replaying is a no-op

    assert _results_map(merged) == {"job-0": None}
