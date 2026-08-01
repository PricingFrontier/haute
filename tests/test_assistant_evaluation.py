"""Semantic, adversarial, and performance qualification harness (ASSIST-A08)."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "assistant_eval"


def test_matrix_and_held_out_scenarios_are_closed_versioned_and_separate_from_assets():
    from haute.assistant._assets import example_index
    from haute.assistant._evaluation import load_scenarios, load_support_matrix

    matrix = load_support_matrix(FIXTURE_ROOT / "support_matrix.json")
    scenarios = load_scenarios(FIXTURE_ROOT / "held_out")

    assert matrix.schema_version == 1
    assert matrix.configurations
    assert scenarios
    assert {scenario.id for scenario in scenarios}.isdisjoint(
        {name for name, _summary in example_index()}
    )
    assert all(scenario.fixture_version for scenario in scenarios)
    assert all(
        (FIXTURE_ROOT / "projects" / scenario.project_fixture / "pipeline.py").is_file()
        for scenario in scenarios
    )
    assert not list((FIXTURE_ROOT / "projects").rglob("*assistant*context*"))
    assert {scenario.category for scenario in scenarios} >= {
        "semantic",
        "prompt_injection",
        "sensitive_data",
        "stale_recovery",
        "clarification",
        "interruption",
        "authority",
    }
    scenario_ids = {scenario.id for scenario in scenarios}
    assert all(
        set(configuration.thresholds.min_success_rate_by_task) == scenario_ids
        for configuration in matrix.configurations
    )


def _observation(*, cold: bool = False, unauthorized: bool = False, leaks: bool = False):
    from haute.assistant._evaluation import TrialObservation

    return TrialObservation(
        node_types=("banding",),
        node_configs={
            "age_band": {
                "factors": [
                    {
                        "banding": "continuous",
                        "column": "driver_age",
                        "outputColumn": "driver_age_band",
                        "rules": [
                            {"op1": "<=", "val1": 25, "assignment": "young"},
                            {"op1": ">", "val1": 25, "assignment": "experienced"},
                        ],
                        "default": "unknown",
                    }
                ]
            }
        },
        edges=(("quotes", "age_band"),),
        postconditions_passed=True,
        unrelated_changes=0,
        clarified=True,
        recovered=True,
        unauthorized_mutation=unauthorized,
        leaked_canaries=("canary",) if leaks else (),
        provider_round_trips=2,
        tool_calls=3,
        input_tokens=100,
        output_tokens=50,
        estimated_cost=0.01,
        time_to_first_token_ms=100,
        time_to_validated_plan_ms=200,
        end_to_end_ms=300 if not cold else 500,
        cold=cold,
    )


def test_scoring_is_semantic_and_does_not_depend_on_prose_or_tool_order():
    from haute.assistant._evaluation import TrialAttribution, load_scenarios, score_trial

    scenario = next(
        scenario
        for scenario in load_scenarios(FIXTURE_ROOT / "held_out")
        if scenario.id == "heldout_continuous_banding"
    )
    attribution = TrialAttribution(
        haute_version="1.2.3",
        capability_hash="a" * 64,
        prompt_hash="b" * 64,
        provider="openai",
        model="pinned",
        model_version="2026-01-01",
        parameters={"temperature": 0},
        run_id="run-1",
        evidence="live",
    )
    record = score_trial(scenario, _observation(), attribution)

    assert record.semantic_success is True
    assert record.safety_success is True
    assert record.scenario_id == scenario.id
    assert record.fixture_version == scenario.fixture_version
    assert not hasattr(record, "prose")
    assert not hasattr(record, "tool_order")


def test_scoring_rejects_non_finite_or_malformed_runner_evidence():
    from haute.assistant._evaluation import TrialAttribution, load_scenarios, score_trial

    scenario = load_scenarios(FIXTURE_ROOT / "held_out")[0]
    attribution = TrialAttribution(
        haute_version="1",
        capability_hash="a" * 64,
        prompt_hash="b" * 64,
        provider="openai",
        model="pinned",
        model_version="1",
        parameters={},
        run_id="invalid",
        evidence="live",
    )

    with pytest.raises(ValueError, match="finite non-negative"):
        score_trial(
            scenario,
            replace(_observation(), estimated_cost=math.nan),
            attribution,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        score_trial(
            scenario,
            _observation(),
            replace(attribution, capability_hash="not-a-hash"),
        )


async def test_repeated_trials_use_isolated_projects_and_per_scenario_cold_runs():
    from haute.assistant._evaluation import (
        TrialAttribution,
        load_scenarios,
        load_support_matrix,
        run_repeated_trials,
    )

    matrix = load_support_matrix(FIXTURE_ROOT / "support_matrix.json")
    configuration = replace(matrix.configurations[0], repetitions=2)
    scenarios = load_scenarios(FIXTURE_ROOT / "held_out")[:2]
    observed: list[tuple[str, int, bool, Path]] = []

    async def runner(config, scenario, repetition, cold, project_root):
        assert (project_root / "haute.toml").is_file()
        assert (project_root / "pipeline.py").is_file()
        observed.append((scenario.id, repetition, cold, project_root))
        return _observation(cold=cold), TrialAttribution(
            haute_version="1",
            capability_hash="a" * 64,
            prompt_hash="b" * 64,
            provider=config.provider,
            model=config.model,
            model_version=config.model_version,
            parameters=config.parameters,
            run_id=f"{scenario.id}-{repetition}",
            evidence="live",
        )

    records = await run_repeated_trials(
        configuration,
        scenarios,
        runner,
        projects_root=FIXTURE_ROOT / "projects",
    )

    assert len(records) == 4
    assert [cold for _scenario, _repetition, cold, _root in observed] == [
        True,
        False,
        True,
        False,
    ]
    roots = [root for _scenario, _repetition, _cold, root in observed]
    assert len(set(roots)) == len(roots)
    assert all(not root.exists() for root in roots)


def test_zero_tolerance_safety_cannot_be_averaged_into_a_qualified_report():
    from haute.assistant._evaluation import (
        TrialAttribution,
        evaluate_configuration,
        load_scenarios,
        load_support_matrix,
        score_trial,
    )

    matrix = load_support_matrix(FIXTURE_ROOT / "support_matrix.json")
    config = replace(matrix.configurations[0], status="qualified", repetitions=1)
    matrix = replace(matrix, configurations=(config,))
    scenario = load_scenarios(FIXTURE_ROOT / "held_out")[0]
    attribution = TrialAttribution(
        haute_version="1",
        capability_hash="a" * 64,
        prompt_hash="b" * 64,
        provider=config.provider,
        model=config.model,
        model_version=config.model_version,
        parameters=config.parameters,
        run_id="unsafe",
        evidence="live",
    )
    record = score_trial(
        scenario,
        _observation(unauthorized=True, leaks=True),
        attribution,
    )
    report = evaluate_configuration(matrix, config.id, (record,))

    assert report.qualified is False
    assert report.unauthorized_mutations == 1
    assert report.leakage_events == 1
    assert any("zero-tolerance" in reason for reason in report.reasons)


def test_candidate_or_scripted_evidence_never_qualifies_even_when_metrics_pass():
    from haute.assistant._evaluation import (
        TrialAttribution,
        evaluate_configuration,
        load_scenarios,
        load_support_matrix,
        score_trial,
    )

    matrix = load_support_matrix(FIXTURE_ROOT / "support_matrix.json")
    config = replace(matrix.configurations[0], repetitions=1)
    matrix = replace(matrix, configurations=(config,))
    scenario = load_scenarios(FIXTURE_ROOT / "held_out")[0]
    attribution = TrialAttribution(
        haute_version="1",
        capability_hash="a" * 64,
        prompt_hash="b" * 64,
        provider=config.provider,
        model=config.model,
        model_version=config.model_version,
        parameters=config.parameters,
        run_id="scripted",
        evidence="scripted",
    )
    report = evaluate_configuration(
        matrix,
        config.id,
        (score_trial(scenario, _observation(), attribution),),
    )
    assert report.qualified is False
    assert any("live" in reason or "candidate" in reason for reason in report.reasons)


def test_percentiles_and_repeated_trial_counts_are_gated_per_task():
    from haute.assistant._evaluation import (
        TrialAttribution,
        evaluate_configuration,
        load_scenarios,
        load_support_matrix,
        score_trial,
    )

    matrix = load_support_matrix(FIXTURE_ROOT / "support_matrix.json")
    scenario = load_scenarios(FIXTURE_ROOT / "held_out")[0]
    base = matrix.configurations[0]
    thresholds = replace(
        base.thresholds,
        min_success_rate_by_task={scenario.id: 1.0},
        max_cold_latency_p95_ms=1_000,
        max_warm_latency_p95_ms=1_000,
    )
    config = replace(
        base,
        status="qualified",
        repetitions=2,
        thresholds=thresholds,
    )
    matrix = replace(matrix, configurations=(config,))
    attribution = TrialAttribution(
        haute_version="1",
        capability_hash="a" * 64,
        prompt_hash="b" * 64,
        provider=config.provider,
        model=config.model,
        model_version=config.model_version,
        parameters=config.parameters,
        run_id="run",
        evidence="live",
    )
    records = (
        score_trial(scenario, _observation(cold=True), attribution),
        score_trial(
            scenario,
            _observation(cold=False),
            replace(attribution, run_id="run-2"),
        ),
    )
    report = evaluate_configuration(matrix, config.id, records)

    assert report.qualified is True
    assert report.cold_latency_p95_ms == 500
    assert report.warm_latency_p95_ms == 300
    assert report.metrics["cold_latency_ms"] == {"p50": 500, "p95": 500}
    assert report.metrics["warm_latency_ms"] == {"p50": 300, "p95": 300}


def test_unexpected_trial_cannot_fall_outside_the_support_matrix_gate():
    from haute.assistant._evaluation import (
        TrialAttribution,
        evaluate_configuration,
        load_scenarios,
        load_support_matrix,
        score_trial,
    )

    matrix = load_support_matrix(FIXTURE_ROOT / "support_matrix.json")
    scenario = load_scenarios(FIXTURE_ROOT / "held_out")[0]
    base = matrix.configurations[0]
    config = replace(
        base,
        status="qualified",
        repetitions=1,
        thresholds=replace(
            base.thresholds,
            min_success_rate_by_task={"different_task": 1.0},
            max_cold_latency_p95_ms=1_000,
            max_warm_latency_p95_ms=1_000,
        ),
    )
    matrix = replace(matrix, configurations=(config,))
    record = score_trial(
        scenario,
        _observation(cold=True),
        TrialAttribution(
            haute_version="1",
            capability_hash="a" * 64,
            prompt_hash="b" * 64,
            provider=config.provider,
            model=config.model,
            model_version=config.model_version,
            parameters=config.parameters,
            run_id="unexpected",
            evidence="live",
        ),
    )

    report = evaluate_configuration(matrix, config.id, (record,))

    assert report.qualified is False
    assert any("unexpected task" in reason for reason in report.reasons)


def test_persisted_report_is_attributable_and_does_not_retain_canary_values(
    tmp_path: Path,
):
    from haute.assistant._evaluation import (
        TrialAttribution,
        evaluate_configuration,
        load_scenarios,
        load_support_matrix,
        score_trial,
        write_evaluation_artifacts,
    )

    matrix = load_support_matrix(FIXTURE_ROOT / "support_matrix.json")
    scenario = load_scenarios(FIXTURE_ROOT / "held_out")[0]
    base = matrix.configurations[0]
    config = replace(
        base,
        repetitions=1,
        thresholds=replace(
            base.thresholds,
            min_success_rate_by_task={scenario.id: 1.0},
        ),
    )
    matrix = replace(matrix, configurations=(config,))
    attribution = TrialAttribution(
        haute_version="1.2.3",
        capability_hash="a" * 64,
        prompt_hash="b" * 64,
        provider=config.provider,
        model=config.model,
        model_version=config.model_version,
        parameters=config.parameters,
        run_id="run-redacted",
        evidence="live",
    )
    record = score_trial(
        scenario,
        _observation(leaks=True),
        attribution,
    )
    report = evaluate_configuration(matrix, config.id, (record,))
    path = write_evaluation_artifacts(tmp_path, (record,), report)

    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "canary" not in raw
    assert payload["report"]["capability_hash"] == "a" * 64
    assert payload["trials"][0]["metrics"]["leakage_count"] == 1
