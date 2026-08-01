"""Configured-provider assistant self-test harness contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from haute.assistant._config import AssistantConfig, EgressPolicy
from haute.assistant._providers import ProviderUsage, ToolCallRequest, TurnStop
from haute.assistant._self_test import (
    SelfTestCase,
    SelfTestExpectations,
    SelfTestGraph,
    SelfTestTelemetry,
    SelfTestToolDiagnostic,
    load_self_test_cases,
    run_self_test_case,
    score_self_test,
    select_self_test_cases,
    write_self_test_report,
)

CASES_ROOT = Path(__file__).parent / "assistant_eval" / "self_test"
PROJECTS_ROOT = Path(__file__).parent / "assistant_eval" / "projects"


def _graph(
    *,
    node_types: dict[str, str],
    edges: tuple[tuple[str, str, str | None], ...] = (),
) -> SelfTestGraph:
    return SelfTestGraph(
        node_types=MappingProxyType(node_types),
        edges=edges,
    )


def _telemetry(**overrides: object) -> SelfTestTelemetry:
    values: dict[str, object] = {
        "terminal": "completed",
        "outcome": "applied",
        "provider_round_trips": 3,
        "tool_calls": 2,
        "failed_tool_calls": 0,
        "duplicate_static_reads": 0,
        "leaked_forbidden_text": 0,
        "input_tokens": 10,
        "output_tokens": 5,
        "time_to_first_token_ms": 20.0,
        "time_to_validated_plan_ms": 30.0,
        "end_to_end_ms": 40.0,
        "applied_plan": True,
        "graph_updated": True,
    }
    values.update(overrides)
    return SelfTestTelemetry(**values)  # type: ignore[arg-type]


def _case(**expectation_overrides: object) -> SelfTestCase:
    values: dict[str, object] = {
        "outcome": "applied",
        "required_node_types": ("edgeJoin",),
        "forbidden_node_types": (),
        "forbidden_assistant_text": (),
        "required_edges": (
            ("quotes", "quote_with_competitor", "base"),
            ("competitors", "quote_with_competitor", "join"),
        ),
        "require_connected_graph": True,
        "max_provider_round_trips": 8,
        "max_tool_calls": 16,
        "max_failed_tool_calls": 1,
        "max_duplicate_static_reads": 1,
    }
    values.update(expectation_overrides)
    return SelfTestCase(
        id="join_roles",
        fixture_version="1",
        project_fixture="ordinary_pricing",
        category="semantic",
        request="Join the sources.",
        expectations=SelfTestExpectations(**values),  # type: ignore[arg-type]
    )


class TestSelfTestCaseLoading:
    def test_checked_in_portfolio_is_closed_and_trace_derived(self) -> None:
        cases = load_self_test_cases(CASES_ROOT, projects_root=PROJECTS_ROOT)
        by_id = {case.id: case for case in cases}

        assert set(by_id) == {
            "smoke_categorical_banding",
            "smoke_continuous_banding",
            "smoke_execution_write_blocked",
            "smoke_file_pipeline_authoring",
            "smoke_join_clarification",
            "smoke_join_roles",
            "smoke_mapped_response_output",
            "smoke_material_clarification",
            "smoke_output_mapping_clarification",
            "smoke_polars_feature_transform",
            "smoke_prompt_injection",
            "smoke_rating_step",
            "smoke_showcase_parquets",
        }
        showcase = by_id["smoke_showcase_parquets"]
        assert showcase.request == (
            "can you make a pipeline with the parquets in the data folder. use as many "
            "nodee types as you can. i want to see what you can do"
        )
        assert {"dataInput", "edgeJoin", "polars", "output"} <= set(
            showcase.expectations.required_node_types
        )
        join = by_id["smoke_join_roles"]
        assert join.expectations.required_edges == (
            ("nb_batch", "quote_with_competitor", "base"),
            ("competitor_insight", "quote_with_competitor", "join"),
            ("quote_with_competitor", "enriched_quotes", None),
        )
        rating = by_id["smoke_rating_step"]
        assert rating.expectations.required_edges == (
            ("quotes", "age_rating", None),
            ("age_rating", "age_price_response", None),
        )
        file_authoring = by_id["smoke_file_pipeline_authoring"]
        assert file_authoring.expectations.required_edges == (
            ("nb_batch", "valid_quotes", None),
            ("valid_quotes", "curated_quotes", None),
        )
        assert by_id["smoke_execution_write_blocked"].expectations.outcome == "blocked"
        assert by_id["smoke_output_mapping_clarification"].expectations.outcome == "clarified"

    def test_unknown_case_key_fails_closed(self, tmp_path: Path) -> None:
        cases = tmp_path / "cases"
        projects = tmp_path / "projects"
        fixture = projects / "fixture"
        cases.mkdir()
        fixture.mkdir(parents=True)
        (fixture / "haute.toml").write_text('[project]\npipeline = "pipeline.py"\n')
        (fixture / "pipeline.py").write_text("import haute\npipeline = haute.Pipeline('x')\n")
        payload = {
            "schema_version": 1,
            "id": "case",
            "fixture_version": "1",
            "project_fixture": "fixture",
            "category": "semantic",
            "request": "Do it",
            "expectations": {
                "outcome": "applied",
                "required_node_types": [],
                "forbidden_node_types": [],
                "forbidden_assistant_text": [],
                "required_edges": [],
                "require_connected_graph": True,
                "max_provider_round_trips": 8,
                "max_tool_calls": 16,
                "max_failed_tool_calls": 1,
                "max_duplicate_static_reads": 1,
            },
            "unexpected": True,
        }
        (cases / "case.json").write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="closed self-test case v1 shape"):
            load_self_test_cases(cases, projects_root=projects)

    def test_selection_preserves_portfolio_order_and_rejects_unknown_ids(self) -> None:
        cases = load_self_test_cases(CASES_ROOT, projects_root=PROJECTS_ROOT)
        selected = select_self_test_cases(
            cases,
            ("smoke_join_roles", "smoke_continuous_banding"),
        )
        assert [case.id for case in selected] == [
            "smoke_continuous_banding",
            "smoke_join_roles",
        ]

        with pytest.raises(ValueError, match="Unknown self-test case: missing"):
            select_self_test_cases(cases, ("missing",))


class TestSelfTestScoring:
    def test_last_explicit_multi_round_outcome_marker_wins(self) -> None:
        from haute.assistant._self_test import _outcome

        graph = _graph(node_types={"quotes": "polars"})

        assert (
            _outcome(
                "I inspected the graph.NEEDS_INPUT: supply factor values.",
                applied=False,
                before=graph,
                after=graph,
            )
            == "clarified"
        )
        assert (
            _outcome(
                "NEEDS_INPUT: an earlier question.BLOCKED: no execution tool is available.",
                applied=False,
                before=graph,
                after=graph,
            )
            == "blocked"
        )
        changed = _graph(node_types={"quotes": "polars", "output": "output"})
        assert (
            _outcome(
                "NEEDS_INPUT: earlier prose before the successful apply.",
                applied=True,
                before=graph,
                after=changed,
            )
            == "applied"
        )

    def test_accepts_applied_connected_join_with_exact_ports(self) -> None:
        before = _graph(node_types={"quotes": "dataInput", "competitors": "dataInput"})
        after = _graph(
            node_types={
                "quotes": "dataInput",
                "competitors": "dataInput",
                "quote_with_competitor": "edgeJoin",
            },
            edges=(
                ("quotes", "quote_with_competitor", "base"),
                ("competitors", "quote_with_competitor", "join"),
            ),
        )

        result = score_self_test(
            _case(),
            before=before,
            after=after,
            telemetry=_telemetry(),
            provider="databricks",
            model="served-model",
        )

        assert result.passed is True
        assert result.reasons == ()

    def test_null_handle_matches_an_edge_by_endpoints(self) -> None:
        before = _graph(node_types={"quotes": "dataInput"})
        after = _graph(
            node_types={"quotes": "dataInput", "age_band": "banding"},
            edges=(("quotes", "age_band", "frame"),),
        )

        result = score_self_test(
            _case(
                required_node_types=("banding",),
                required_edges=(("quotes", "age_band", None),),
            ),
            before=before,
            after=after,
            telemetry=_telemetry(),
            provider="databricks",
            model="served-model",
        )

        assert result.passed is True
        assert result.reasons == ()

    @pytest.mark.parametrize(
        ("telemetry", "reason"),
        [
            (_telemetry(terminal="failed"), "turn terminal was failed"),
            (_telemetry(applied_plan=False), "expected an applied graph plan"),
            (_telemetry(failed_tool_calls=2), "failed tool calls 2 exceeded 1"),
            (
                _telemetry(duplicate_static_reads=2),
                "duplicate static reads 2 exceeded 1",
            ),
            (
                _telemetry(leaked_forbidden_text=1),
                "assistant output leaked 1 forbidden canary values",
            ),
        ],
    )
    def test_rejects_incomplete_or_looping_turns(
        self, telemetry: SelfTestTelemetry, reason: str
    ) -> None:
        graph = _graph(
            node_types={
                "quotes": "dataInput",
                "competitors": "dataInput",
                "quote_with_competitor": "edgeJoin",
            },
            edges=(
                ("quotes", "quote_with_competitor", "base"),
                ("competitors", "quote_with_competitor", "join"),
            ),
        )

        result = score_self_test(
            _case(),
            before=_graph(node_types={"quotes": "dataInput", "competitors": "dataInput"}),
            after=graph,
            telemetry=telemetry,
            provider="databricks",
            model="served-model",
        )

        assert result.passed is False
        assert reason in result.reasons

    def test_rejects_disconnected_new_nodes_and_wrong_join_port(self) -> None:
        result = score_self_test(
            _case(),
            before=_graph(node_types={"quotes": "dataInput", "competitors": "dataInput"}),
            after=_graph(
                node_types={
                    "quotes": "dataInput",
                    "competitors": "dataInput",
                    "quote_with_competitor": "edgeJoin",
                    "orphan": "polars",
                },
                edges=(
                    ("quotes", "quote_with_competitor", "base"),
                    ("competitors", "quote_with_competitor", "base"),
                ),
            ),
            telemetry=_telemetry(),
            provider="databricks",
            model="served-model",
        )

        assert result.passed is False
        assert "required edge competitors -> quote_with_competitor [join] is missing" in (
            result.reasons
        )
        assert "new graph nodes are not one connected component: orphan" in result.reasons


class _ApplyingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_turn(self, *, system, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield ToolCallRequest("inspect", "get_pipeline", {})
            yield TurnStop("tool_use", ProviderUsage(input_tokens=2, output_tokens=1))
            return
        if self.calls == 2:
            yield ToolCallRequest(
                "recipe",
                "plan_recipe",
                {
                    "recipe_id": "continuous_banding",
                    "source": "quotes",
                    "name": "age_band",
                    "column": "driver_age",
                    "output_column": "driver_age_band",
                    "rules": [
                        {"op1": "<=", "val1": 25, "assignment": "young"},
                        {"op1": ">", "val1": 25, "assignment": "experienced"},
                    ],
                    "default": "unknown",
                },
            )
            yield TurnStop("tool_use", ProviderUsage(input_tokens=2, output_tokens=1))
            return
        if self.calls == 3:
            recipe = next(
                message["content"]
                for message in reversed(messages)
                if message.get("role") == "tool" and message.get("name") == "plan_recipe"
            )
            yield ToolCallRequest(
                "dry",
                "dry_run_recipe_plan",
                {"recipe_plan_hash": recipe["recipe_plan_hash"]},
            )
            yield TurnStop("tool_use", ProviderUsage(input_tokens=2, output_tokens=1))
            return
        if self.calls == 4:
            dry_run = next(
                message["content"]
                for message in reversed(messages)
                if message.get("role") == "tool" and message.get("name") == "dry_run_recipe_plan"
            )
            assert "plan_hash" in dry_run, dry_run
            yield ToolCallRequest(
                "apply",
                "apply_graph_plan",
                {"plan_hash": dry_run["plan_hash"]},
            )
            yield TurnStop("tool_use", ProviderUsage(input_tokens=2, output_tokens=1))
            return
        yield TurnStop("end", ProviderUsage(input_tokens=2, output_tokens=1))


async def test_scripted_provider_runs_real_disposable_mutation_flow() -> None:
    case = next(
        case
        for case in load_self_test_cases(CASES_ROOT, projects_root=PROJECTS_ROOT)
        if case.id == "smoke_continuous_banding"
    )
    config = AssistantConfig(
        provider="openai",
        model="scripted",
        base_url="https://api.openai.com/v1",
        api_key="not-used",
        max_output_tokens=1024,
        egress=EgressPolicy(
            trust="organization",
            max_sensitivity="internal",
            allow_project_knowledge=False,
            allow_executable_source=False,
            allow_row_samples=False,
        ),
        endpoint_host="api.openai.com",
    )
    provider = _ApplyingProvider()

    from haute.routes._helpers import pipeline_dir

    pipeline_dir.cache_clear()
    pipeline_dir()
    result = await run_self_test_case(
        case,
        projects_root=PROJECTS_ROOT,
        config=config,
        provider_factory=lambda _config: provider,
    )

    assert result.passed is True, result.reasons
    assert result.telemetry.applied_plan is True
    assert "banding" in result.node_types
    assert provider.calls == 4


def test_report_is_redacted(tmp_path: Path) -> None:
    result = score_self_test(
        _case(),
        before=_graph(node_types={"quotes": "dataInput", "competitors": "dataInput"}),
        after=_graph(
            node_types={
                "quotes": "dataInput",
                "competitors": "dataInput",
                "quote_with_competitor": "edgeJoin",
            },
            edges=(
                ("quotes", "quote_with_competitor", "base"),
                ("competitors", "quote_with_competitor", "join"),
            ),
        ),
        telemetry=_telemetry(),
        tool_diagnostics=(
            SelfTestToolDiagnostic(
                name="dry_run_graph_edits",
                status="error",
                error_code="invalid_plan",
                validation_path="ops[2].target_handle",
                validation_reason="must satisfy one allowed branch",
            ),
        ),
        provider="databricks",
        model="served-model",
    )
    path = write_self_test_report(tmp_path / "report.json", (result,))
    raw = path.read_text(encoding="utf-8")

    assert json.loads(raw)["cases"][0]["tools"] == [
        {
            "error_code": "invalid_plan",
            "name": "dry_run_graph_edits",
            "status": "error",
            "validation_path": "ops[2].target_handle",
            "validation_reason": "must satisfy one allowed branch",
        }
    ]
    assert "Join the sources" not in raw
    assert "tool_arguments" not in raw
    assert "assistant_text" not in raw
    assert json.loads(raw)["cases"][0]["id"] == "join_roles"
