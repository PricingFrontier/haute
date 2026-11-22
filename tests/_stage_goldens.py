"""Frozen public stage responses: the equivalence oracle for storage changes.

`capture` drives the real service through a full staged workflow and records every
public response a client reads:
- full and compact store snapshots after each stage;
- full and compact inspection of every path point;
- each Apply result;
- an earlier-family rebuild under a still-valid later family;
- finalisation and restore-final.

`normalise` removes what legitimately differs between runs (key order, wall-clock fields and process
ids. It maps every opaque identifier (revision, job, path and digest hex strings) to a
placeholder in first-appearance order, so the structure linking identifiers is still
compared exactly.

Write the goldens with ``python -m tests._stage_goldens <output-dir>``. They are compared by
`tests/test_stage_goldens.py`.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

VALIDATIONS = {
    "holdout": None,
    "cv3": {"method": "cross_validation", "fold_count": 3},
}
FIXTURES = Path(__file__).parent / "fixtures" / "stage_goldens"
_DROPPED = {
    "fit_time",
    "fitted_at",
    "trained_at",
    "created_at",
    "elapsed_seconds",
    "owner_pid",
    "duration_seconds",
    # The byte size of the opaque stored artifact: run-dependent, and redefined by storage.
    "size",
}
_IDENTIFIER = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{64}|[0-9a-f]{32}|[0-9a-f]{12})(?![0-9a-f])")


def normalise(value: Any, root: Path) -> Any:
    names: dict[str, str] = {}
    prefix = str(root)

    def text(item: str) -> str:
        item = item.replace(prefix, "<tmp>")
        return _IDENTIFIER.sub(
            lambda match: names.setdefault(match.group(0), f"<id{len(names)}>"), item
        )

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                text(key): (
                    # Server artifact paths live in random job directories; keep the file name.
                    text(Path(entry).name)
                    if key.endswith("_path") and isinstance(entry, str) and entry.startswith("/")
                    else walk(entry)
                )
                # Sorted, so placeholder numbering never depends on key order.
                for key, entry in sorted(item.items())
                if key not in _DROPPED
            }
        if isinstance(item, list):
            return [walk(entry) for entry in item]
        if isinstance(item, str):
            return text(item)
        return item

    return walk(value)


def capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, validation: Any) -> dict[str, Any]:
    from haute.routes._job_store import JobStore
    from haute.routes._training_lifecycle import TrainService
    from haute.routes._training_paths import apply_path, inspect_path
    from haute.routes._training_stages import StageJobScope
    from haute.schemas import (
        StageJobRequest,
        StagePathApplyRequest,
        StagePathReadRequest,
        TrainRequest,
    )
    from tests._modelling_route_helpers import _make_modelling_graph
    from tests._project import use_project
    from tests._training_helpers import join_training_preparation
    from tests.conftest import make_graph
    from tests.test_staged_tboost import PARAMS
    from tests.test_tboost_family import EVALUATION, frame
    from tests.test_training_worker_protocol import _inline_protocol_runner

    use_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    # Recorded in `fit_evidence`; results are identical across thread counts.
    monkeypatch.setenv("HAUTE_TRAINING_THREADS", "1")
    # Thin response histories below the fixture's round count, so the goldens separate a
    # response's thinned top-level history from its stage snapshots' complete histories.
    monkeypatch.setenv("HAUTE_TRAIN_LOSS_HISTORY_LIMIT", "8")
    data = tmp_path / "source.parquet"
    frame(1500).write_parquet(data)
    graph = _make_modelling_graph(
        str(data),
        target="claims",
        weight="weight",
        algorithm="tboost",
        params=PARAMS,
        evaluation={
            **EVALUATION,
            "test": {"size": 0.2},
            "validation": validation or EVALUATION["validation"],
        },
        feature_columns=["region", "age"],
    )
    graph["nodes"][1]["data"]["config"].update(
        {"stages": {"path": {"min_rows": 20}}, "offset": "exposure", "loss_function": "Poisson"}
    )
    body = TrainRequest(
        graph=make_graph(graph),
        node_id="train",
        model="m1",
        stage=StageJobRequest(operation="full"),
    )
    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    scope = StageJobScope.from_request(body)
    record: dict[str, Any] = {}

    def run(name: str) -> dict[str, Any]:
        job_id = service.start(body).job_id
        join_training_preparation(job_id)
        deadline = time.monotonic() + 120
        while store.require_job(job_id)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        job = store.require_job(job_id)
        assert job["status"] == "completed", (job.get("message"), job.get("error_detail"))
        while job_id in service._stage_scopes and time.monotonic() < deadline:
            time.sleep(0.01)
        record[f"{name}/status"] = job["result"].model_dump(mode="json")
        snapshot(name)
        return job

    def snapshot(name: str) -> dict[str, Any]:
        record[f"{name}/state"] = scope.store.snapshot(scope.key)
        record[f"{name}/state-compact"] = scope.store.snapshot(scope.key, compact=True)
        return record[f"{name}/state"]

    def inspect_all(name: str, state: dict[str, Any], path: dict[str, Any]) -> None:
        for point in path["points"]:
            for diagnostics in ("full", "compact"):
                request = StagePathReadRequest(
                    **body.model_dump(exclude={"diagnostics"}),
                    current_revision=state["current"]["revision"],
                    artifact_revision=state["preview"]["revision"],
                    path_id=path["id"],
                    point_id=point["id"],
                    diagnostics=diagnostics,
                )
                record[f"{name}/inspect/{path['stage']}/{point['id']}/{diagnostics}"] = (
                    inspect_path(scope, request).model_dump(mode="json")
                )

    def apply(name: str, state: dict[str, Any], point_id: str | None = None) -> dict[str, Any]:
        staged = state["preview"]["result"]["response"]["staged"]
        path = next(p for p in staged["paths"].values() if p["id"] == staged["prepared_path"])
        inspect_all(name, state, path)
        chosen = point_id or next(
            (p["id"] for p in reversed(path["points"]) if p["status"] == "changed"),
            path["points"][0]["id"],
        )
        request = StagePathApplyRequest(
            **body.model_dump(),
            current_revision=state["current"]["revision"],
            artifact_revision=state["preview"]["revision"],
            path_id=path["id"],
            point_id=chosen,
        )
        applied = apply_path(scope, request, service.start_preview)
        record[f"{name}/apply"] = applied
        body.graph.nodes[1].data.config["stages"] = applied["current"]["result"]["response"][
            "staged"
        ]["configuration"]
        return applied["current"]

    run("full")
    applied = scope.store.snapshot(scope.key)["current"]
    for operation in ("pruned", "banded"):
        body.stage = StageJobRequest(operation=operation, parent_revision=applied["revision"])
        run(operation)
        state = scope.store.snapshot(scope.key)
        # Apply the suggested pruning point, so the rebuild below keeps the same suggestion.
        suggested = state["preview"]["result"]["response"]["staged"]["paths"]["pruned"][
            "selected_point"
        ]
        applied = apply(operation, state, suggested if operation == "pruned" else None)
    # Rebuild pruning under the applied banding chain with a new selection tolerance. The
    # later banding family stays valid and must read the rebuilt pruning family.
    stages = body.graph.nodes[1].data.config["stages"]
    selection = {**stages.get("selection", {"relative_tolerance": 0.001})}
    selection["relative_tolerance"] = selection.get("relative_tolerance", 0.001) * 1.01
    body.graph.nodes[1].data.config["stages"] = {**stages, "selection": selection}
    body.stage = StageJobRequest(operation="pruned", parent_revision=applied["revision"])
    run("rebuild")
    rebuilt_state = scope.store.snapshot(scope.key)
    staged = rebuilt_state["preview"]["result"]["response"]["staged"]
    assert set(staged["paths"]) == {"pruned", "banded"}, "the banding family must stay valid"
    inspect_all("rebuild", rebuilt_state, staged["paths"]["banded"])
    scope.store.discard(scope.key, preview_revision=rebuilt_state["preview"]["revision"])
    body.graph.nodes[1].data.config["stages"] = stages
    snapshot("discarded")
    body.stage = StageJobRequest(operation="smoothed", parent_revision=applied["revision"])
    run("smoothed")
    applied = apply("smoothed", scope.store.snapshot(scope.key))
    body.stage = StageJobRequest(operation="finalise", parent_revision=applied["revision"])
    run("final")
    restarted_store = JobStore()
    restarted = TrainService(restarted_store, protocol_runner=_inline_protocol_runner)
    restored = restarted.restore_final_stage(body)
    record["final/restore"] = restored.model_dump(mode="json")
    return normalise(record, tmp_path)


def golden_path(name: str) -> Path:
    return FIXTURES / f"{name}.json.gz"


def load_golden(name: str) -> dict[str, Any]:
    with gzip.open(golden_path(name), "rt", encoding="utf-8") as handle:
        value: dict[str, Any] = json.load(handle)
    return value


def main(output: Path) -> None:
    """Write fresh goldens by running the workflow in a temporary project per validation."""
    import tempfile

    output.mkdir(parents=True, exist_ok=True)
    for name, validation in VALIDATIONS.items():
        with tempfile.TemporaryDirectory(prefix=f"stage-golden-{name}-") as temporary:
            patch = pytest.MonkeyPatch()
            try:
                value = capture(Path(temporary), patch, validation)
            finally:
                patch.undo()
        with gzip.open(output / f"{name}.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        print(f"{name}: {len(value)} responses")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURES)
