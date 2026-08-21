"""Fresh-interpreter cached JSON execution probe for performance certification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._json_shred._cache import load_v2_api_source
from haute._polars_utils import execution_collect


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--column", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _write_output(
    output: Path,
    *,
    result: object,
    metrics: dict[str, object],
    telemetry: list[dict[str, str | int | float | bool | None]],
    profile: str,
) -> None:
    output.write_text(
        json.dumps(
            {
                "rows": result.to_dicts(),
                "cache_proof": metrics["cache_proof"],
                "requested_column_width_total": metrics["requested_column_width_total"],
                "physically_scanned_column_width_total": metrics[
                    "physically_scanned_column_width_total"
                ],
                "telemetry": telemetry,
                "profile": profile,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def main() -> int:
    args = _parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    telemetry: list[dict[str, str | int | float | bool | None]] = []
    context = ExecutionContext(
        operation="perf_restart_cache_proof",
        profile=ExecutionProfile.PREVIEW_EAGER,
        telemetry_enabled=True,
        telemetry_sink=lambda event: telemetry.append(dict(event.attributes)),
    )
    try:
        with context.stage("cached_port_load"):
            frame = load_v2_api_source(
                str(args.source),
                config,
                port_columns={args.port: frozenset(args.column)},
            )[args.port]
            result = execution_collect(frame, execution_context=context, engine="streaming")
        metrics = context.metrics_payload(status="completed")
    finally:
        context.release_admission(preserve_primary_error=True)

    _write_output(
        args.output,
        result=result,
        metrics=metrics,
        telemetry=telemetry,
        profile=context.profile.value,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
