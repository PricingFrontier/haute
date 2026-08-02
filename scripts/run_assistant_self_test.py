"""Run a selected prompt portfolio against the configured assistant provider."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from haute.assistant._config import resolve_assistant_config
from haute.assistant._self_test import (
    load_self_test_cases,
    run_self_test_case,
    select_self_test_cases,
    self_test_report_payload,
    write_self_test_report,
)
from haute.deploy._config import _load_env

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CASES = _REPOSITORY_ROOT / "tests" / "assistant_eval" / "self_test"
_DEFAULT_PROJECTS = _REPOSITORY_ROOT / "tests" / "assistant_eval" / "projects"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run disposable live-provider assistant smoke cases. This diagnostic lane "
            "makes real provider requests but never runs the resulting pipeline."
        )
    )
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    parser.add_argument("--projects", type=Path, default=_DEFAULT_PROJECTS)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Case id to run; repeat for a selection. All cases run when omitted.",
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        default=Path.cwd(),
        help="Project containing the .env and [assistant] settings to use.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list", action="store_true", help="List case ids without provider calls.")
    return parser


async def _run(args: argparse.Namespace) -> int:
    cases = load_self_test_cases(args.cases, projects_root=args.projects)
    selected = select_self_test_cases(cases, args.case)
    if args.list:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "cases": [
                        {
                            "id": case.id,
                            "category": case.category,
                            "project_fixture": case.project_fixture,
                        }
                        for case in selected
                    ],
                },
                sort_keys=True,
            )
        )
        return 0

    config_root = args.config_root.resolve()
    _load_env(config_root)
    config = resolve_assistant_config(config_root)
    results = []
    for case in selected:
        results.append(
            await run_self_test_case(
                case,
                projects_root=args.projects,
                config=config,
            )
        )
    if args.output is not None:
        write_self_test_report(args.output, results)
    payload = self_test_report_payload(results)
    if args.output is not None:
        payload["report"] = str(args.output.resolve())
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
