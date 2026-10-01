"""The assistant evaluation's report, comparison, transcripts and support matrix.

Development tooling outside the installed package, used by
``scripts/run_assistant_self_test.py``. A report holds one evidence kind and no
prompt, model prose, tool payload, credential, dataset value, canary value or
content digest; a transcript holds those for one live case of a synthetic
fixture and is written only on request, under a Git-ignored directory.
"""

from __future__ import annotations

import json
import os
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from haute._git_core import _run_git_process

if TYPE_CHECKING:
    from scripts.run_assistant_self_test import SelfTestCase, SelfTestResult

SelfTestLayer = Literal[
    "protocol", "structure", "configuration", "collateral", "editor", "execution"
]

#: The scoring layers, in report order.
SELF_TEST_LAYERS: tuple[SelfTestLayer, ...] = (
    "protocol",
    "structure",
    "configuration",
    "collateral",
    "editor",
    "execution",
)
#: The efficiency metrics every case reports, summed over its turns.
METRICS: tuple[str, ...] = (
    "provider_round_trips",
    "tool_calls",
    "failed_tool_calls",
    "duplicate_static_reads",
    "input_tokens",
    "output_tokens",
    "time_to_first_token_ms",
    "time_to_validated_plan_ms",
    "end_to_end_ms",
)
REPORT_SCHEMA_VERSION = 5
_SUPPORT_MATRIX_VERSION = 2


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """What one report measured: the run, its variant and the configuration."""

    run_id: str
    started_at: str
    variant: str
    provider: str
    model: str
    configuration: str | None
    haute_version: str


@dataclass(frozen=True, slots=True)
class SupportConfiguration:
    id: str
    provider: str
    model: str


def load_support_matrix(path: Path) -> tuple[SupportConfiguration, ...]:
    """Load the closed support matrix v2: the configurations reports are attributed to."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "configurations"}
        or payload["schema_version"] != _SUPPORT_MATRIX_VERSION
    ):
        raise ValueError(f"{path.name} is not the closed support matrix v2 shape")
    raw = payload["configurations"]
    if not isinstance(raw, list) or not raw:
        raise ValueError("support matrix configurations must be a non-empty array")
    configurations: list[SupportConfiguration] = []
    for index, item in enumerate(raw):
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "provider", "model"}
            or any(not isinstance(value, str) or not value for value in item.values())
        ):
            raise ValueError(f"support matrix configuration {index} is not closed")
        configurations.append(SupportConfiguration(**item))
    if len({item.id for item in configurations}) != len(configurations):
        raise ValueError("support matrix configuration ids must be unique")
    if len({(item.provider, item.model) for item in configurations}) != len(configurations):
        raise ValueError("support matrix lists one provider and model twice")
    return tuple(configurations)


def configuration_for(
    configurations: Sequence[SupportConfiguration], *, provider: str, model: str
) -> str | None:
    """The id of the listed configuration for *provider* and *model*, or None."""

    return next(
        (item.id for item in configurations if (item.provider, item.model) == (provider, model)),
        None,
    )


def _median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _turn_payload(turn: Any) -> dict[str, object]:
    telemetry = turn.telemetry
    return {
        "passed": turn.passed,
        "reasons": list(turn.reasons),
        "outcome": telemetry.outcome,
        "saved_changes": telemetry.saved_changes,
        "terminal": telemetry.terminal,
        "node_types": list(turn.node_types),
        "edges": [
            {"source": source, "target": target, "target_handle": target_handle}
            for source, target, target_handle in turn.edges
        ],
        "tools": [
            {
                "name": diagnostic.name,
                "status": diagnostic.status,
                "error_code": diagnostic.error_code,
                "validation_path": diagnostic.validation_path,
                "validation_reason": diagnostic.validation_reason,
            }
            for diagnostic in turn.tool_diagnostics
        ],
        "metrics": {name: getattr(telemetry, name) for name in METRICS},
        "leaked_forbidden_text": telemetry.leaked_forbidden_text,
    }


def report_payload(
    results: Sequence[SelfTestResult],
    run: RunIdentity,
    *,
    not_applicable: Sequence[SelfTestCase] = (),
) -> dict[str, object]:
    """Build the closed content-redacted report v5.

    One report holds one kind of evidence: replay results prove the tools and
    contracts, live results measure a model, and the two are never combined.
    The *not_applicable* cases, which do not apply to the run's variant and
    were not run, are listed apart from the results: they neither pass nor
    fail, and an area counts its cases and passes over the cases it ran. A
    crashed case fails, keeps its traceback and is counted apart, and its
    area's metric medians are taken over the cases that did not crash.
    """

    evidence = {result.evidence for result in results}
    if len(evidence) != 1:
        raise ValueError("a report holds the results of exactly one evidence kind")
    if overlap := sorted({result.id for result in results} & {case.id for case in not_applicable}):
        raise ValueError(f"a case is either run or not applicable, not both: {', '.join(overlap)}")
    cases = [
        {
            "id": result.id,
            "fixture_version": result.fixture_version,
            "area": result.area,
            "split": result.split,
            "egress": result.egress,
            "provider": result.provider,
            "model": result.model,
            "passed": result.passed,
            "layers": {layer: layer not in result.failed_layers for layer in SELF_TEST_LAYERS},
            "first_failing_layer": result.first_failing_layer,
            "reasons": list(result.reasons),
            "crash": result.crash,
            "metrics": dict(result.metrics),
            "turns": [_turn_payload(turn) for turn in result.turns],
        }
        for result in results
    ]
    areas: dict[str, dict[str, object]] = {}
    run_areas = {result.area for result in results}
    for area in sorted(run_areas | {case.area for case in not_applicable}):
        members = [result for result in results if result.area == area]
        completed = [result for result in members if result.crash is None]
        areas[area] = {
            "cases": len(members),
            "passed": sum(result.passed for result in members),
            "crashed": len(members) - len(completed),
            "not_applicable": sum(case.area == area for case in not_applicable),
            "metrics": {
                name: _median([result.metrics[name] for result in completed]) for name in METRICS
            },
        }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evidence": evidence.pop(),
        "run": {
            "run_id": run.run_id,
            "started_at": run.started_at,
            "variant": run.variant,
            "provider": run.provider,
            "model": run.model,
            "configuration": run.configuration,
            "haute_version": run.haute_version,
        },
        "passed": all(result.passed for result in results),
        "areas": areas,
        "cases": cases,
        "not_applicable": [
            {
                "id": case.id,
                "fixture_version": case.fixture_version,
                "area": case.area,
                "split": case.split,
            }
            for case in not_applicable
        ],
    }


def _write_json(path: Path, payload: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def write_report(
    path: Path,
    results: Sequence[SelfTestResult],
    run: RunIdentity,
    *,
    not_applicable: Sequence[SelfTestCase] = (),
) -> Path:
    """Atomically write a report containing no prompts, prose, tool payloads, or secrets."""

    return _write_json(path, report_payload(results, run, not_applicable=not_applicable))


def _load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError(f"{path} is not an assistant evaluation report v{REPORT_SCHEMA_VERSION}")
    return payload


def compare_reports(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, object]:
    """Compare two reports of one evidence kind per area.

    Returns each area's case, pass, crash and not-applicable counts in both reports,
    every case run in both that flipped between pass and fail with the first
    failing layer on its failing side, the cases each report lists as not
    applicable to its variant (never a flip, a pass or a failure), the cases
    only one report holds, run or not applicable, and per area the median of
    each efficiency metric in both reports and their difference.
    """

    if before["evidence"] != after["evidence"]:
        raise ValueError(
            "replay and live reports are never compared: "
            f"{before['evidence']} against {after['evidence']}"
        )
    before_cases = {case["id"]: case for case in before["cases"]}
    after_cases = {case["id"]: case for case in after["cases"]}
    before_ids = set(before_cases) | {case["id"] for case in before["not_applicable"]}
    after_ids = set(after_cases) | {case["id"] for case in after["not_applicable"]}
    flips = [
        {
            "id": case_id,
            "area": after_cases[case_id]["area"],
            "before": "passed" if before_cases[case_id]["passed"] else "failed",
            "after": "passed" if after_cases[case_id]["passed"] else "failed",
            "first_failing_layer": (
                after_cases[case_id]["first_failing_layer"]
                or before_cases[case_id]["first_failing_layer"]
            ),
        }
        for case_id in sorted(set(before_cases) & set(after_cases))
        if before_cases[case_id]["passed"] != after_cases[case_id]["passed"]
    ]
    areas: dict[str, dict[str, object]] = {}
    for area in sorted(set(before["areas"]) | set(after["areas"])):
        old = before["areas"].get(area)
        new = after["areas"].get(area)
        metrics: dict[str, dict[str, float | None]] = {}
        for name in METRICS:
            old_value = old["metrics"][name] if old else None
            new_value = new["metrics"][name] if new else None
            metrics[name] = {
                "before": old_value,
                "after": new_value,
                "difference": (
                    None if old_value is None or new_value is None else new_value - old_value
                ),
            }
        areas[area] = {
            "before": _area_counts(old),
            "after": _area_counts(new),
            "metrics": metrics,
        }
    return {
        "evidence": after["evidence"],
        "before": before["run"],
        "after": after["run"],
        "areas": areas,
        "flips": flips,
        "not_applicable": {
            "before": sorted(case["id"] for case in before["not_applicable"]),
            "after": sorted(case["id"] for case in after["not_applicable"]),
        },
        "only_before": sorted(before_ids - after_ids),
        "only_after": sorted(after_ids - before_ids),
    }


def _area_counts(area: Mapping[str, Any] | None) -> dict[str, object] | None:
    if area is None:
        return None
    return {
        "cases": area["cases"],
        "passed": area["passed"],
        "crashed": area["crashed"],
        "not_applicable": area["not_applicable"],
    }


def compare_report_files(before: Path, after: Path) -> dict[str, object]:
    return compare_reports(_load_report(before), _load_report(after))


def require_git_ignored(directory: Path) -> None:
    """Refuse unless Git ignores *directory*, so a transcript cannot be committed."""

    directory.mkdir(parents=True, exist_ok=True)
    # Exit 0: ignored; 1: not ignored; anything else: not inside a repository.
    ignored = _run_git_process(
        "check-ignore", "-q", str(directory / "transcript.json"), cwd=directory
    )
    if ignored.returncode != 0:
        raise ValueError(
            "transcripts are written only under a Git-ignored directory; "
            f"{directory} is not ignored"
        )


def write_transcript(path: Path, turns: Sequence[Mapping[str, object]]) -> Path:
    """Write one live case's local-only transcript: requests, text and tool payloads."""

    return _write_json(path, {"schema_version": 1, "turns": list(turns)})
