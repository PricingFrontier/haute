"""Fresh-process peak-RSS probe for one frontier auto-range job.

The parent test writes the representative fixture (a high-cardinality base
parquet and a CatBoost conversion model) once and runs this script under
``scripts.memory_smoke.run_smoke`` for each scenario count, so every
measurement starts from a clean interpreter. The job runs exactly as the
server runs it: prepared by ``_prepare_frontier_auto_range`` and executed by
``_run_frontier_auto_range_job``, which admits under the solve setup's profile,
runs its pipeline stage and reduces the frame in batches of the pipeline's
streaming chunk size. The RSS baseline is taken after preparation, immediately
before the job.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.memory_smoke import StdlibMemorySampler

#: The fixture's conversion model reads these features, in schema order.
FEATURES = ("age", "vehicle_value", "price_ratio")


def build_graph(base_path: Path, *, steps: int) -> Any:
    """Base -> scenario expander -> features -> model score -> constraints -> optimiser."""
    from haute._types import PipelineGraph

    nodes: list[dict[str, Any]] = [
        {
            "id": "source",
            "data": {
                "label": "source",
                "nodeType": "dataInput",
                "config": {
                    "inputType": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": str(base_path),
                    "arguments": {},
                },
            },
        },
        {
            "id": "premium",
            "data": {
                "label": "premium",
                "nodeType": "scenarioExpander",
                "config": {
                    "quote_id": "quote_id",
                    "column_name": "premium_multiplier",
                    "min_value": 0.8,
                    "max_value": 1.2,
                    "stepCount": steps,
                    "step_column": "scenario_index",
                    "contract": {
                        "inputs": [],
                        "outputs": ["premium_multiplier", "scenario_index"],
                    },
                },
            },
        },
        {
            "id": "features",
            "data": {
                "label": "features",
                "nodeType": "polars",
                "config": {
                    "code": (
                        "df = premium.with_columns("
                        "scenario_premium=pl.col('premium') * pl.col('premium_multiplier'),"
                        " price_ratio=pl.col('premium') * pl.col('premium_multiplier')"
                        " / pl.col('base_premium'))"
                    ),
                    "contract": {
                        "inputs": ["premium", "base_premium", "premium_multiplier"],
                        "outputs": ["scenario_premium", "price_ratio"],
                    },
                },
            },
        },
        {
            "id": "conversion_scoring",
            "data": {
                "label": "conversion_scoring",
                "nodeType": "modelScore",
                "config": {
                    "sourceType": "run",
                    "run_id": "auto-range-memory",
                    "artifact_path": "model.cbm",
                    "task": "regression",
                    "output_column": "conversion_prediction",
                    "contract": {
                        "inputs": list(FEATURES),
                        "outputs": ["conversion_prediction"],
                    },
                },
            },
        },
        {
            "id": "constraints",
            "data": {
                "label": "constraints",
                "nodeType": "polars",
                "config": {
                    "code": (
                        "df = conversion_scoring.with_columns("
                        "expected_income=pl.col('scenario_premium')"
                        " * pl.col('conversion_prediction'),"
                        " volume=pl.col('conversion_prediction'),"
                        " margin=(pl.col('scenario_premium') - pl.col('claims_cost'))"
                        " * pl.col('conversion_prediction'))"
                    ),
                    "contract": {
                        "inputs": ["scenario_premium", "claims_cost", "conversion_prediction"],
                        "outputs": ["expected_income", "volume", "margin"],
                    },
                },
            },
        },
        {
            "id": "opt",
            "data": {
                "label": "optimiser",
                "nodeType": "optimiser",
                "config": {
                    "mode": "online",
                    "objective": "expected_income",
                    "constraints": {"volume": {"min": 0.0}, "margin": {"min": 0.0}},
                    "quote_id": "quote_id",
                    "scenario_index": "scenario_index",
                    "scenario_value": "premium_multiplier",
                    "data_input": "constraints",
                },
            },
        },
    ]
    edges = [
        ("source", "premium"),
        ("premium", "features"),
        ("features", "conversion_scoring"),
        ("conversion_scoring", "constraints"),
        ("constraints", "opt"),
    ]
    return PipelineGraph.model_validate(
        {
            "nodes": nodes,
            "edges": [
                {"id": f"{source}-{target}", "source": source, "target": target}
                for source, target in edges
            ],
        }
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True, help="Fixture directory.")
    parser.add_argument("--steps", type=int, required=True, help="Scenario count per quote.")
    parser.add_argument(
        "--chunk-rows", type=int, required=True, help="The pipeline's streaming chunk size."
    )
    parser.add_argument("--output", type=Path, required=True, help="Path to write JSON results.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    # Run the job on this interpreter's thread, so the sampled process is the one
    # that executes and reduces, and the patched model loader applies.
    os.environ["HAUTE_INTERACTIVE_EXECUTION_MODE"] = "thread"

    from catboost import CatBoostRegressor

    from haute._mlflow_io import ScoringModel
    from haute._polars_utils import current_streaming_chunk_size, set_streaming_chunk_size
    from haute._sandbox import set_project_root
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    fixture = args.fixture.resolve()
    set_project_root(fixture)
    set_streaming_chunk_size(args.chunk_rows)
    model = CatBoostRegressor()
    model.load_model(str(fixture / "model.cbm"))
    scoring_model = ScoringModel(model=model, feature_names=list(FEATURES), flavor="catboost")
    body = OptimiserFrontierAutoRangeRequest(
        graph=build_graph(fixture / "base.parquet", steps=args.steps),
        node_id="opt",
    )
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job(
        {"status": "running", "job_type": "frontier_auto_range", "start_time": time.monotonic()}
    )
    sampler = StdlibMemorySampler()
    with patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model):
        _node, prepared = service._prepare_frontier_auto_range(body)
        gc.collect()
        rss_before = sampler.process_rss_bytes(os.getpid())
        if rss_before is None:
            raise RuntimeError("could not sample pre-job RSS")
        started = time.perf_counter()
        response = service._run_frontier_auto_range_job(body, job_id, **prepared)
        elapsed_seconds = time.perf_counter() - started

    payload = {
        "schema_version": 1,
        "steps": args.steps,
        "chunk_rows": current_streaming_chunk_size(),
        "ranges": {
            name: {"min": value.min, "max": value.max} for name, value in response.ranges.items()
        },
        "elapsed_seconds": elapsed_seconds,
        "rss_before_bytes": rss_before,
    }
    # ``--output`` is supplied from pytest's tmp_path by the parent harness.
    args.output.write_text(  # write-sandbox: deliberate
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    # Keep the process resident briefly so the parent sampler observes peak working set.
    time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
