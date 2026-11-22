"""Run an isolated audit server; profiling and phase timing are opt-in diagnostics."""

import argparse
import cProfile
import functools
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi.routing import APIRoute

from haute._project_context import ProjectContext, project_scope


def instrument(endpoint: Callable[..., Any], path: str, output: Path) -> Callable[..., Any]:
    @functools.wraps(endpoint)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        profile = cProfile.Profile()
        try:
            return profile.runcall(endpoint, *args, **kwargs)
        finally:
            name = path.replace("/", "_")
            profile.dump_stats(str(output / f"request-{name}-{time.time_ns()}.prof"))

    return wrapped


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", default=8012, type=int)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument(
        "--phase-timing",
        action="store_true",
        help="Record wall-clock spans of the parent handoff and worker job (no profiler).",
    )
    args = parser.parse_args()
    if args.profile and args.phase_timing:
        parser.error("--profile and --phase-timing measure differently; use one at a time")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["HAUTE_AUDIT_OUTPUT"] = str(args.output)
    if args.profile:
        from haute.routes import _training_lifecycle

        from .audit_hooks import profile_training_worker

        _training_lifecycle._run_training_process_job = profile_training_worker
    if args.phase_timing:
        from haute.routes import _training_lifecycle

        from .audit_hooks import timed_training_worker
        from .audit_timing import install

        _training_lifecycle._run_training_process_job = timed_training_worker
        install(args.output)
    from haute.server import create_app

    os.chdir(args.project.resolve())
    context = ProjectContext.at(Path.cwd())
    with project_scope(context):
        if args.profile:
            from haute.routes.modelling import router

            # Instrument the source router before FastAPI copies its endpoints
            # into lazy included routers; app.routes may not contain APIRoutes yet.
            for route in router.routes:
                if isinstance(route, APIRoute) and "status/" not in route.path:
                    route.endpoint = instrument(route.endpoint, route.path, args.output)
                    route.dependant.call = route.endpoint
        app = create_app(context)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
