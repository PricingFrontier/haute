"""Generation contracts for the cross-stack API schema pilots."""

from __future__ import annotations

import json
from pathlib import Path

from haute._estimate_calibration import CALIBRATION_MAX_BASIS_POINTS
from haute._execution_schemas import MAX_JSON_SAFE_INTEGER
from scripts.generate_api_contracts import (
    GENERATED_SCHEMA_PATH,
    build_contract_bundle,
    main,
    render_contract_bundle,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _non_null_branch(schema: dict[str, object]) -> dict[str, object]:
    alternatives = schema["anyOf"]
    assert isinstance(alternatives, list)
    branches = [
        branch
        for branch in alternatives
        if isinstance(branch, dict) and branch.get("type") != "null"
    ]
    assert len(branches) == 1
    return branches[0]


def test_committed_contract_bundle_is_current_and_byte_stable() -> None:
    first = render_contract_bundle()
    second = render_contract_bundle()

    assert first == second
    assert first.endswith("\n")
    assert GENERATED_SCHEMA_PATH.read_text(encoding="utf-8") == first
    assert json.loads(first) == build_contract_bundle()


def test_contract_bundle_contains_both_closed_pilot_roots() -> None:
    bundle = build_contract_bundle()

    assert bundle["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert bundle["type"] == "object"
    assert bundle["additionalProperties"] is False
    assert bundle["required"] == [
        "execution_strategy_diagnostic",
        "explore_charts",
    ]
    assert bundle["properties"] == {
        "execution_strategy_diagnostic": {"$ref": "#/$defs/ExecutionStrategyDiagnosticPayload"},
        "explore_charts": {"$ref": "#/$defs/ExploreChartsConfig"},
    }

    definitions = bundle["$defs"]
    assert set(definitions) == {
        "ChartAxes",
        "ChartAxisConfig",
        "ChartCategory",
        "ChartLegend",
        "ChartSecondaryAxisConfig",
        "ChartSeriesOverride",
        "ChartValueEncoding",
        "ExecutionStrategyBoundaryCollectionPayload",
        "ExecutionStrategyBoundaryPayload",
        "ExecutionStrategyDiagnosticPayload",
        "ExecutionStrategyProvenanceCollectionPayload",
        "ExecutionStrategyProvenancePayload",
        "ExecutionStrategyReasonCollectionPayload",
        "ExecutionStrategyReasonPayload",
        "ExploreChartConfig",
        "ExploreChartsConfig",
        "JsonValue",
    }
    diagnostic = definitions["ExecutionStrategyDiagnosticPayload"]
    assert diagnostic["properties"]["schema_version"]["const"] == 1
    assert diagnostic["properties"]["boundaries"]["$ref"] == (
        "#/$defs/ExecutionStrategyBoundaryCollectionPayload"
    )
    chart = definitions["ExploreChartConfig"]
    assert chart["properties"]["version"]["const"] == 1
    assert set(chart["required"]) == {
        "version",
        "id",
        "name",
        "enabled",
        "pivot_id",
        "kind",
        "orientation",
        "category",
        "value_encodings",
        "series_overrides",
        "axes",
        "legend",
    }


def test_contract_bundle_preserves_browser_safe_bounds_and_recursive_json() -> None:
    definitions = build_contract_bundle()["$defs"]

    boundary_rank = definitions["ExecutionStrategyBoundaryPayload"]["properties"][
        "topological_rank"
    ]
    assert boundary_rank == {
        "maximum": MAX_JSON_SAFE_INTEGER,
        "minimum": 0,
        "title": "Topological Rank",
        "type": "integer",
    }

    for collection_name, cap in (
        ("ExecutionStrategyBoundaryCollectionPayload", 32),
        ("ExecutionStrategyReasonCollectionPayload", 32),
        ("ExecutionStrategyProvenanceCollectionPayload", 128),
    ):
        properties = definitions[collection_name]["properties"]
        assert properties["items"]["maxItems"] == cap
        count = _non_null_branch(properties["total_count"])
        assert count["minimum"] == 0
        assert count["maximum"] == MAX_JSON_SAFE_INTEGER

    diagnostic_properties = definitions["ExecutionStrategyDiagnosticPayload"]["properties"]
    for field in (
        "estimated_peak_bytes",
        "raw_estimated_peak_bytes",
        "headroom_bytes",
    ):
        value = _non_null_branch(diagnostic_properties[field])
        assert value["minimum"] == 0
        assert value["maximum"] == MAX_JSON_SAFE_INTEGER
    calibration = _non_null_branch(
        diagnostic_properties["estimate_calibration_factor_basis_points"]
    )
    assert calibration["maximum"] == CALIBRATION_MAX_BASIS_POINTS

    reference = {"$ref": "#/$defs/JsonValue"}
    assert definitions["JsonValue"] == {
        "anyOf": [
            {"type": "null"},
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string"},
            {"items": reference, "type": "array"},
            {"additionalProperties": reference, "type": "object"},
        ]
    }


def test_frontend_contract_generators_are_direct_exact_pins() -> None:
    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))

    assert {
        name: package["devDependencies"].get(name)
        for name in ("ajv", "esbuild", "json-schema-to-typescript")
    } == {
        "ajv": "8.20.0",
        "esbuild": "0.25.12",
        "json-schema-to-typescript": "16.0.0",
    }


def test_check_mode_rejects_stale_output_without_rewriting_it(
    tmp_path: Path,
) -> None:
    stale = tmp_path / "api-contracts.schema.json"
    stale.write_text("{}\n", encoding="utf-8")

    assert main(["--check", "--output", str(stale)]) == 1
    assert stale.read_text(encoding="utf-8") == "{}\n"


def test_write_mode_creates_then_check_mode_accepts_exact_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "nested" / "api-contracts.schema.json"

    assert main(["--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == render_contract_bundle()
    assert main(["--check", "--output", str(output)]) == 0
