import argparse
import cProfile
import json
import time
from pathlib import Path
from typing import Any

from haute._project_context import ProjectContext, project_scope
from haute.routes.modelling import _stage_scope, apply_stage_path, read_stage_path
from haute.schemas import StagePathApplyRequest, StagePathReadRequest, TrainRequest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--project", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--request", type=Path, required=True)
parser.add_argument("--full", action="store_true", help="Measure dense diagnostic responses")
parser.add_argument("--stage", choices=["pruned", "banded", "smoothed"], default="smoothed")
parser.add_argument(
    "--apply",
    action="store_true",
    help="Re-apply the selected point (changes this disposable project)",
)
options = parser.parse_args()
root = options.output.resolve()
root.mkdir(parents=True, exist_ok=True)
project = options.project.resolve()

args = {
    **json.loads(options.request.read_text()),
    "diagnostics": "full" if options.full else "compact",
}
report: dict[str, Any] = {"path_read": []}
with project_scope(ProjectContext.at(project)):
    scope = _stage_scope(TrainRequest.model_validate(args))
    state = scope.store.snapshot(scope.key)
    current = state["current"]
    path = current["result"]["response"]["staged"]["paths"][options.stage]
    selected = next(p for p in path["points"] if p["id"] == path["selected_point"])
    body = StagePathReadRequest.model_validate(
        {
            **args,
            "current_revision": current["revision"],
            "artifact_revision": current["revision"],
            "path_id": path["id"],
            "point_id": selected["id"],
        }
    )
    for _ in range(3):
        start = time.perf_counter()
        value = read_stage_path(body)
        elapsed = time.perf_counter() - start
        start = time.perf_counter()
        encoded = value.model_dump_json().encode()
        report["path_read"].append(
            {
                "seconds": elapsed,
                "json_encode_seconds": time.perf_counter() - start,
                "bytes": len(encoded),
            }
        )
    profile = cProfile.Profile()
    profile.runcall(read_stage_path, body)
    profile.dump_stats(str(root / "path-read.prof"))
    if options.apply:
        apply_body = StagePathApplyRequest.model_validate(
            {**body.model_dump(), "prepare_next": False}
        )
        profile = cProfile.Profile()
        start = time.perf_counter()
        result = profile.runcall(apply_stage_path, apply_body)
        report["apply_profiled_seconds"] = time.perf_counter() - start
        profile.dump_stats(str(root / "path-apply.prof"))
(root / "path-bench.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
