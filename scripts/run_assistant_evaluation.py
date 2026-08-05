"""Run the credentialed assistant qualification lane with an injected live runner."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from haute.assistant._evaluation import (
    TrialRunner,
    evaluate_configuration,
    load_scenarios,
    load_support_matrix,
    run_repeated_trials,
    write_evaluation_artifacts,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run repeated live-provider assistant trials. The runner must be an "
            "async callable exposed as module:attribute and return the closed "
            "TrialObservation/TrialAttribution pair."
        )
    )
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--runner", required=True, help="Async runner as module:attribute")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _load_runner(reference: str) -> TrialRunner:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--runner must use the module:attribute form")
    candidate = getattr(importlib.import_module(module_name), attribute)
    if not callable(candidate):
        raise TypeError("configured evaluation runner is not callable")
    return cast(TrialRunner, candidate)


async def _run(args: argparse.Namespace) -> int:
    matrix = load_support_matrix(args.matrix)
    configurations = {configuration.id: configuration for configuration in matrix.configurations}
    try:
        configuration = configurations[args.configuration]
    except KeyError as exc:
        raise ValueError(f"Unknown evaluation configuration: {args.configuration}") from exc
    records = await run_repeated_trials(
        configuration,
        load_scenarios(args.scenarios),
        _load_runner(args.runner),
        projects_root=args.scenarios.parent / "projects",
    )
    report = evaluate_configuration(matrix, configuration.id, records)
    path = write_evaluation_artifacts(args.output_dir, records, report)
    print(
        json.dumps(
            {
                "configuration_id": configuration.id,
                "qualified": report.qualified,
                "report": str(path),
                "reasons": list(report.reasons),
            },
            sort_keys=True,
        )
    )
    return 0 if report.qualified else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
