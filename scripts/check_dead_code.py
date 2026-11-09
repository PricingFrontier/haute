"""Fail when production code has a function, method, class or import nothing calls.

vulture reads ``src/haute`` and ``databricks_app`` at 60% confidence and reports
the definitions no code names. Fields, attributes and properties are left out:
Pydantic models and dataclasses declare hundreds that only serialisation reads.
What a framework calls by convention is ignored by decorator or name pattern
below, and each remaining exception is listed in ``ALLOWLIST`` with the reason
it stays. A new finding is either deleted, with any test that only served it,
or added here with its reason in review.

Run: ``uv run python scripts/check_dead_code.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import vulture

ROOT = Path(__file__).resolve().parents[1]
PATHS = ("src/haute", "databricks_app")
# Example pipelines the assistant hands out as text; their frames are read by name.
EXCLUDE = ("src/haute/assistant/assets",)
MIN_CONFIDENCE = 60
KINDS = frozenset({"function", "method", "class", "import"})

# Called by the framework the decorator hands them to.
IGNORE_DECORATORS = (
    "@router.*",
    "@*_router.*",
    "@app.*",
    "@*.command",
    "@*.callback",
    "@field_validator",
    "@model_validator",
    "@field_serializer",
    "@model_serializer",
    "@_register",
    "@_register_codegen",
    "@event.listens_for",
)

# Called by name by a protocol or a dispatch table.
IGNORE_NAMES = (
    "visit_*",  # ast.NodeVisitor
    "_render_*",  # _polars_steps renders a step by getattr(self, f"_render_{kind}")
    "_load_*_file",  # _model_flavors loads a family's file by its function's name
    "dispatch",  # Starlette BaseHTTPMiddleware
    "on_leave",  # libcst CSTTransformer
    "get_code",  # importlib SourceFileLoader
    "seekable",  # io file protocol
    "load_dialect_impl",  # SQLAlchemy TypeDecorator
    "process_bind_param",
    "process_result_value",
    "load_context",  # mlflow PythonModel
    "_load_pyfunc",  # mlflow loader module entry point
    "after_iteration",  # CatBoost and XGBoost training callbacks
    "redirect_request",  # urllib HTTPRedirectHandler
    "_sleep_for_retry",  # azure.core RetryPolicy
    "log_artifacts",  # MLflow artifact repository, which MLflow calls
    "_download_file",
)

# path relative to the repository :: name -> why it stays.
ALLOWLIST: dict[str, str] = {
    "src/haute/__init__.py::__getattr__": "PEP 562 lazy attribute of the package.",
    "src/haute/__init__.py::__dir__": "PEP 562 companion of the lazy attributes.",
    "src/haute/server.py::create_app_from_environment": (
        "uvicorn factory string in cli/_serve.py."
    ),
    "src/haute/deploy/_response_json.py::ndjson_rows": (
        "Called by the deploy app source generated in deploy/_container.py."
    ),
    "src/haute/routes/_isolated_worker_async.py::run_isolated_worker_async": (
        "Called by the deploy app source generated in deploy/_container.py."
    ),
    "src/haute/_workbench_app.py::hosted_store": (
        "Named by the generated app entry point APP_ENTRY."
    ),
    "src/haute/schemas.py::AssistantStreamEventPayload": (
        "Read by scripts/generate_api_contracts.py for the frontend's stream contract."
    ),
    "src/haute/pipeline.py::to_graph": "Public Pipeline API for scripts and notebooks.",
    **{
        f"src/haute/pipeline.py::{decorator}": (
            "Pipeline node decorator that codegen writes into user pipeline files."
        )
        for decorator in (
            "workbench_input",
            "workbench_output",
            "data_output",
            "rating_step",
            "explore",
            "external_file",
            "live_switch",
            "modelling",
            "optimiser",
            "scenario_expander",
        )
    },
    "src/haute/_project.py::is_haute_project": "Public project-detection helper.",
    "src/haute/_model_flavors.py::model_family_fixture": (
        "Writes the family table the frontend's modelFamilies.json mirrors."
    ),
    "src/haute/modelling/_descriptors.py::capability_fixture": (
        "Writes the capability table the frontend's checked-in fixture mirrors."
    ),
    # Test seams on production state: each reads or resets the owner's state under
    # the owner's lock, which a test outside the module could not do safely.
    "src/haute/_contracts.py::get_column_contract": (
        "Contract lookup the column-contract tests read through."
    ),
    "src/haute/_data_points.py::lease_point_frame": "Lease-holding read the resolver tests use.",
    "src/haute/_dedicated_workers.py::child_limit_evidence_counter": (
        "Child-side counter the memory-limit notification tests read."
    ),
    "src/haute/_file_lock.py::owned_by_current_thread": "Ownership read under the lock's guard.",
    "src/haute/_mlflow_io.py::get_model_cache_stats": "Cache counters read under the cache lock.",
    "src/haute/_mlflow_io.py::clear_local_model_cache": "Cache reset under the cache lock.",
    "src/haute/_mlflow_utils.py::_restore_mlflow_databricks_credentials": (
        "Undoes the process-wide credential binding between tests."
    ),
    "src/haute/_node_snapshots.py::part_path": "Where a staged artifact's part is written.",
    "src/haute/_quote_store/_migrate.py::wanted_counts": (
        "The count keys plan() requires, named in its refusal."
    ),
    "src/haute/_source_cache.py::leased_generation_ids": "Lease set read under the lease lock.",
    "src/haute/deploy/_validators.py::score_test_quotes": (
        "Scores a test_quotes directory the deploy tests drive."
    ),
    "src/haute/projection.py::diagnostics_payload": "The diagnostics wire payload tests compare.",
    "src/haute/routes/_background_jobs.py::join_and_raise": (
        "Joins a supervised thread and re-raises its stored failure."
    ),
    "src/haute/routes/_job_store.py::clear_all": "Clears a namespace and its auxiliary state.",
    "src/haute/routes/_optimiser_outcomes.py::collect_quote_analysis": (
        "Leased collect over the analysis table the query-kind tests use."
    ),
    "src/haute/routes/_optimiser_outcomes.py::SegmentGroupBy": (
        "Choice query kind, measured by scripts/benchmarks/opt-v09b."
    ),
    "src/haute/routes/_optimiser_outcomes.py::FactorSegments": "Choice query kind.",
    "src/haute/routes/_optimiser_outcomes.py::TopK": (
        "Choice query kind, measured by scripts/benchmarks/opt-v09b."
    ),
    "src/haute/routes/_optimiser_outcomes.py::RowIndex": (
        "Choice query kind, measured by scripts/benchmarks/opt-v09b."
    ),
}


def findings(
    root: Path = ROOT,
    paths: tuple[str, ...] = PATHS,
    exclude: tuple[str, ...] = EXCLUDE,
    allowlist: dict[str, str] = ALLOWLIST,
) -> list[str]:
    """Each unreferenced definition outside *allowlist*, then each stale allowlist entry."""
    scanner = vulture.Vulture(
        ignore_names=list(IGNORE_NAMES),
        ignore_decorators=list(IGNORE_DECORATORS),
    )
    scanner.scavenge(
        [str(root / path) for path in paths],
        exclude=[str(root / path) for path in exclude],
    )
    found: list[str] = []
    seen: set[str] = set()
    for item in scanner.get_unused_code(min_confidence=MIN_CONFIDENCE, sort_by_size=False):
        if item.typ not in KINDS:
            continue
        relative = Path(item.filename).resolve().relative_to(root.resolve()).as_posix()
        key = f"{relative}::{item.name}"
        seen.add(key)
        if key not in allowlist:
            found.append(f"{relative}:{item.first_lineno}: unused {item.typ} {item.name!r}")
    stale = sorted(set(allowlist) - seen)
    found.extend(f"{key}: allowlisted but no longer reported; remove it" for key in stale)
    return found


def main() -> int:
    problems = findings()
    for problem in problems:
        print(problem)
    if problems:
        print(
            f"\n{len(problems)} dead-code finding(s). Delete the code (and any test that only "
            "served it), or list it in ALLOWLIST in scripts/check_dead_code.py with its reason."
        )
        return 1
    print("No unreferenced production code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
