import argparse
import cProfile
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

from haute._project_context import ProjectContext, project_scope
from haute.modelling._stage_store import StageStore
from haute.routes.modelling import _stage_scope, read_stages
from haute.schemas import TrainRequest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--project", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--request", type=Path, required=True)
parser.add_argument("--full", action="store_true", help="Measure dense diagnostic responses")
options = parser.parse_args()
root = options.output.resolve()
root.mkdir(parents=True, exist_ok=True)
project = options.project.resolve()

body = TrainRequest.model_validate(
    {
        **json.loads(options.request.read_text()),
        "diagnostics": "full" if options.full else "compact",
    }
)
report: dict[str, Any] = {"read_stages": [], "snapshot": [], "metadata": []}
original = StageStore._load_result
loads = []


def measured_load(
    self: StageStore, key: str, record: Mapping[str, Any], **kwargs: Any
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        return original(self, key, record, **kwargs)
    finally:
        loads.append(time.perf_counter() - start)


with project_scope(ProjectContext.at(project)):
    scope = _stage_scope(body)
    with patch.object(StageStore, "_load_result", measured_load):
        for _ in range(3):
            loads.clear()
            start = time.perf_counter()
            value = read_stages(body)
            elapsed = time.perf_counter() - start
            load_times = list(loads)
            start = time.perf_counter()
            encoded = value.model_dump_json().encode()
            report["read_stages"].append(
                {
                    "seconds": elapsed,
                    "result_load_calls": len(load_times),
                    "result_load_seconds": sum(load_times),
                    "json_encode_seconds": time.perf_counter() - start,
                    "response_bytes": len(encoded),
                    "stale_reason": value.stale_reason,
                }
            )
        for name in ("snapshot", "metadata"):
            for _ in range(3):
                start = time.perf_counter()
                if name == "snapshot":
                    scope.store.snapshot(scope.key)
                else:
                    with scope.store._lock(scope.key):
                        scope.store._read(scope.key)
                report[name].append(time.perf_counter() - start)
    profile = cProfile.Profile()
    profile.runcall(read_stages, body)
    profile.dump_stats(str(root / "read-stages.prof"))
(root / "read-bench.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
