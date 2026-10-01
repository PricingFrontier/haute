"""Configured-provider assistant self-test harness contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from haute._types import GraphEdge, PipelineGraph
from haute.assistant._config import AssistantConfig, EgressPolicy
from haute.assistant._render import render_pipeline_graph
from scripts.run_assistant_self_test import (
    SelfTestCase,
    SelfTestExpectations,
    SelfTestGraph,
    SelfTestTelemetry,
    SelfTestToolDiagnostic,
    TrajectoryProvider,
    load_self_test_cases,
    load_trajectory,
    run_self_test_case,
    run_self_test_cases_in_processes,
    score_self_test,
    select_self_test_cases,
    self_test_report_payload,
    write_self_test_report,
)

CASES_ROOT = Path(__file__).parent / "assistant_eval" / "self_test"
PROJECTS_ROOT = Path(__file__).parent / "assistant_eval" / "projects"
TRAJECTORIES_ROOT = Path(__file__).parent / "assistant_eval" / "trajectories"


@pytest.fixture(autouse=True)
def _fresh_plan_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Copies of one fixture have identical content, so a repeated trajectory
    dry-runs to the hash of a plan an earlier replay in this process applied."""

    from haute.assistant import _tools
    from haute.assistant._ops import PlanStore

    monkeypatch.setattr(_tools, "_PLAN_STORE", PlanStore())


def _graph(
    *,
    node_types: dict[str, str],
    edges: tuple[tuple[str, str, str | None], ...] = (),
    configs: dict[str, dict[str, object]] | None = None,
) -> SelfTestGraph:
    return SelfTestGraph(
        node_types=MappingProxyType(node_types),
        edges=edges,
        configs=MappingProxyType(configs or {node: {} for node in node_types}),
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
        "modified_nodes": (),
        "node_configs": {},
        "execution": (),
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


def _expectation_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
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
        "modified_nodes": [],
        "node_configs": {},
        "execution": [],
    }
    payload.update(overrides)
    return payload


class TestSelfTestCaseLoading:
    def test_checked_in_portfolio_is_closed_and_trace_derived(self) -> None:
        cases = load_self_test_cases(CASES_ROOT, projects_root=PROJECTS_ROOT)
        by_id = {case.id: case for case in cases}

        assert set(by_id) == {
            "smoke_categorical_banding",
            "smoke_corpus_attach_regional_rates",
            "smoke_corpus_high_premium_quotes",
            "smoke_corpus_underwriting_decision",
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
            "smoke_step_edit",
        }
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
        assert {
            case.id: case.expectations.required_edges
            for case in cases
            if case.project_fixture == "polars_corpus"
        } == {
            "smoke_corpus_attach_regional_rates": (
                ("quotes", "attach_regional_rates", None),
                ("rates", "attach_regional_rates", None),
            ),
            "smoke_corpus_high_premium_quotes": (("quotes", "high_premium_quotes", None),),
            "smoke_corpus_underwriting_decision": (("quotes", "underwriting_decision", None),),
        }
        # New Polars logic is written steps-first, so no request dictates code.
        for case in cases:
            assert "code" not in case.request.casefold(), case.id
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
            "schema_version": 2,
            "id": "case",
            "fixture_version": "1",
            "project_fixture": "fixture",
            "category": "semantic",
            "request": "Do it",
            "expectations": _expectation_payload(),
            "unexpected": True,
        }
        (cases / "case.json").write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="closed self-test case v2 shape"):
            load_self_test_cases(cases, projects_root=projects)

    @pytest.mark.parametrize("key", ["required_node_types", "forbidden_node_types"])
    def test_unknown_node_type_name_fails_case_loading(self, tmp_path: Path, key: str) -> None:
        cases = tmp_path / "cases"
        fixture = tmp_path / "projects" / "fixture"
        cases.mkdir()
        fixture.mkdir(parents=True)
        (fixture / "haute.toml").write_text('[project]\npipeline = "pipeline.py"\n')
        (fixture / "pipeline.py").write_text("import haute\npipeline = haute.Pipeline('x')\n")
        expectations = _expectation_payload(
            required_node_types=["polars"], forbidden_node_types=["edgeJoin"]
        )
        expectations[key] = ["polars", "polarsTransform"]
        payload = {
            "schema_version": 2,
            "id": "case",
            "fixture_version": "1",
            "project_fixture": "fixture",
            "category": "semantic",
            "request": "Do it",
            "expectations": expectations,
        }
        (cases / "case.json").write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match=f"case.json {key} names unknown node type"):
            load_self_test_cases(cases, projects_root=tmp_path / "projects")

    def test_selection_preserves_portfolio_order_and_rejects_unknown_ids(self) -> None:
        cases = load_self_test_cases(CASES_ROOT, projects_root=PROJECTS_ROOT)
        selected = select_self_test_cases(
            cases,
            ("smoke_join_roles", "smoke_categorical_banding"),
        )
        assert [case.id for case in selected] == [
            "smoke_categorical_banding",
            "smoke_join_roles",
        ]

        with pytest.raises(ValueError, match="Unknown self-test case: missing"):
            select_self_test_cases(cases, ("missing",))


class TestSelfTestGraphReading:
    def test_edge_handles_are_read_under_the_names_the_renderer_emits(self) -> None:
        """`_read_graph` reads structure from `get_pipeline`, whose renderer
        names handles the way the graph-edit operations accept them. Reading a
        stale key yields `None` for every edge without raising, which silently
        scores every handle-qualified required edge — every Edge Join role — as
        missing no matter what the assistant built.
        """

        from scripts import run_assistant_self_test as _self_test

        _node_types, edges = _self_test._graph_structure(
            {
                "nodes": [
                    {"id": "nb_batch", "type": "dataInput"},
                    {"id": "competitor_insight", "type": "dataInput"},
                    {"id": "quote_with_competitor", "type": "edgeJoin"},
                ],
                **render_pipeline_graph(
                    PipelineGraph(
                        nodes=[],
                        edges=[
                            GraphEdge(
                                id="e1",
                                source="nb_batch",
                                target="quote_with_competitor",
                                targetHandle="base",
                            ),
                            GraphEdge(
                                id="e2",
                                source="competitor_insight",
                                target="quote_with_competitor",
                                targetHandle="join",
                            ),
                        ],
                    ),
                    egress=_PERMISSIVE_EGRESS,
                ),
            }
        )

        assert set(edges) == {
            ("nb_batch", "quote_with_competitor", "base"),
            ("competitor_insight", "quote_with_competitor", "join"),
        }


class TestSelfTestScoring:
    def test_last_explicit_multi_round_outcome_marker_wins(self) -> None:
        from scripts.run_assistant_self_test import _outcome

        graph = _graph(node_types={"quotes": "polars"})

        assert (
            _outcome(
                "I inspected the graph.NEEDS_INPUT: supply factor values.",
                applied=False,
                incomplete=False,
                before=graph,
                after=graph,
            )
            == "clarified"
        )
        assert (
            _outcome(
                "NEEDS_INPUT: an earlier question.BLOCKED: no execution tool is available.",
                applied=False,
                incomplete=False,
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
                incomplete=False,
                before=graph,
                after=changed,
            )
            == "applied"
        )

    def test_a_turn_that_stopped_before_finishing_matches_no_expected_outcome(self) -> None:
        """An unchanged graph after an `incomplete` turn is not the `unchanged`
        outcome a case can expect: the model stopped with a dry-run unfinished."""

        from scripts.run_assistant_self_test import _outcome

        graph = _graph(node_types={"quotes": "polars"})
        observed = _outcome(
            "The plan is ready.", applied=False, incomplete=True, before=graph, after=graph
        )

        assert observed == "incomplete"
        result = score_self_test(
            _case(required_node_types=(), required_edges=(), outcome="unchanged"),
            before=graph,
            after=graph,
            telemetry=_telemetry(outcome=observed, applied_plan=False, graph_updated=False),
            provider="replay",
            model="trajectory",
            evidence="replay",
        )
        assert result.passed is False
        assert any("outcome was incomplete; expected unchanged" in r for r in result.reasons)

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
            evidence="live",
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
            evidence="live",
        )

        assert result.passed is True
        assert result.reasons == ()

    @pytest.mark.parametrize(
        ("telemetry", "reason"),
        [
            (_telemetry(terminal="failed"), "protocol: turn terminal was failed"),
            (_telemetry(applied_plan=False), "protocol: expected an applied graph plan"),
            (_telemetry(failed_tool_calls=2), "protocol: failed tool calls 2 exceeded 1"),
            (
                _telemetry(duplicate_static_reads=2),
                "protocol: duplicate static reads 2 exceeded 1",
            ),
            (
                _telemetry(leaked_forbidden_text=1),
                "protocol: assistant output leaked 1 forbidden canary values",
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
            evidence="live",
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
            evidence="live",
        )

        assert result.passed is False
        assert (
            "structure: required edge competitors -> quote_with_competitor [join] is missing"
            in (result.reasons)
        )
        assert (
            "structure: changed nodes and their neighbours are not one connected component: orphan"
            in result.reasons
        )

    def test_connectivity_ignores_untouched_nodes_elsewhere_in_the_fixture(self) -> None:
        """A fixture input the request never touches stays unconnected; only the
        changed nodes and their neighbours must form one component."""

        before = _graph(node_types={"quotes": "dataInput", "rates": "dataInput"})
        after = _graph(
            node_types={"quotes": "dataInput", "rates": "dataInput", "high": "polars"},
            edges=(("quotes", "high", None),),
            configs={"quotes": {}, "rates": {}, "high": {"steps": []}},
        )

        result = score_self_test(
            _case(required_node_types=("polars",), required_edges=(("quotes", "high", None),)),
            before=before,
            after=after,
            telemetry=_telemetry(),
            provider="databricks",
            model="served-model",
            evidence="live",
        )

        assert result.passed is True, result.reasons

    def test_a_removed_edge_that_strands_its_endpoint_is_reported(self) -> None:
        before = _graph(
            node_types={"quotes": "dataInput", "high": "polars", "out": "output"},
            edges=(("quotes", "high", None), ("high", "out", None)),
        )
        after = _graph(
            node_types={"quotes": "dataInput", "high": "polars", "out": "output"},
            edges=(("quotes", "high", None),),
        )

        result = score_self_test(
            _case(required_node_types=(), required_edges=()),
            before=before,
            after=after,
            telemetry=_telemetry(),
            provider="databricks",
            model="served-model",
            evidence="live",
        )

        assert result.reasons == (
            "structure: changed nodes and their neighbours are not one connected component: out",
        )


def _scripted_config(egress: EgressPolicy) -> AssistantConfig:
    return AssistantConfig(
        provider="openai",
        model="scripted",
        base_url="https://api.openai.com/v1",
        api_key="not-used",
        max_output_tokens=1024,
        egress=egress,
        endpoint_host="api.openai.com",
    )


_PERMISSIVE_EGRESS = EgressPolicy(
    trust="organization",
    max_sensitivity="restricted",
    allow_project_knowledge=True,
    allow_executable_source=True,
    allow_row_samples=True,
)


def _load_case(case_id: str) -> SelfTestCase:
    return next(
        case
        for case in load_self_test_cases(CASES_ROOT, projects_root=PROJECTS_ROOT)
        if case.id == case_id
    )


def _banding_replay(_config: AssistantConfig) -> TrajectoryProvider:
    return TrajectoryProvider(load_trajectory(TRAJECTORIES_ROOT / "smoke_categorical_banding.json"))


async def test_scripted_provider_runs_real_disposable_mutation_flow(tmp_path: Path) -> None:
    """The case runs under the harness's own egress allowances, whatever the
    invoking project permits."""

    provider = _banding_replay(_scripted_config(_PERMISSIVE_EGRESS))

    from haute.routes._helpers import pipeline_dir

    pipeline_dir.cache_clear()
    pipeline_dir()
    result = await run_self_test_case(
        _load_case("smoke_categorical_banding"),
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        work_dir=tmp_path,
        provider_factory=lambda _config: provider,
    )

    assert result.passed is True, result.reasons
    assert result.evidence == "live"
    assert result.telemetry.applied_plan is True
    assert "banding" in result.node_types
    assert result.telemetry.provider_round_trips == 4
    assert provider.system is not None
    assert "Project egress policy" not in provider.system
    assert provider.first_messages is not None
    context = provider.first_messages[-1]
    assert context["role"] == "context"
    assert "- Provider trust: `organization`" in context["content"]
    assert "- Highest sensitivity sent: `internal`" in context["content"]
    assert "- Project knowledge: not permitted" in context["content"]
    assert "- Executable source: not permitted" in context["content"]
    assert "- Column value profiles: not permitted" in context["content"]


@pytest.mark.slow
def test_the_command_runs_each_case_in_its_own_process() -> None:
    case = _load_case("smoke_categorical_banding")

    results = run_self_test_cases_in_processes(
        (case,),
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        provider_factory=_banding_replay,
    )

    assert [(result.id, result.reasons) for result in results] == [
        ("smoke_categorical_banding", ()),
    ]


async def test_an_external_provider_is_refused_before_the_case_runs(tmp_path: Path) -> None:
    external = EgressPolicy(
        trust="external",
        max_sensitivity="public",
        allow_project_knowledge=False,
        allow_executable_source=False,
        allow_row_samples=False,
    )

    def unexpected_provider(_config: AssistantConfig) -> TrajectoryProvider:
        raise AssertionError("no provider may be created for a refused configuration")

    with pytest.raises(ValueError, match="external trust is public-only"):
        await run_self_test_case(
            _load_case("smoke_categorical_banding"),
            projects_root=PROJECTS_ROOT,
            config=_scripted_config(external),
            work_dir=tmp_path,
            provider_factory=unexpected_provider,
        )


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
        evidence="live",
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
    assert json.loads(raw)["evidence"] == "live"
    assert json.loads(raw)["cases"][0]["layers"] == {
        "protocol": True,
        "structure": True,
        "configuration": True,
        "collateral": True,
        "editor": True,
        "execution": True,
    }


def test_a_report_never_mixes_replay_and_live_evidence() -> None:
    graph = _graph(node_types={"quotes": "dataInput"})
    live, replay = (
        score_self_test(
            _case(required_node_types=(), required_edges=(), outcome="unchanged"),
            before=graph,
            after=graph,
            telemetry=_telemetry(outcome="unchanged", applied_plan=False, graph_updated=False),
            provider="databricks",
            model="served-model",
            evidence=evidence,
        )
        for evidence in ("live", "replay")
    )

    assert self_test_report_payload((replay,))["evidence"] == "replay"
    with pytest.raises(ValueError, match="exactly one evidence kind"):
        self_test_report_payload((live, replay))


class TestSelfTestLayers:
    """Configuration, collateral and editor layers score the saved configs."""

    _BEFORE = _graph(
        node_types={"quotes": "dataInput", "rates": "dataInput", "legacy": "polars"},
        configs={
            "quotes": {"path": "data/quotes.parquet"},
            "rates": {"path": "data/rates.parquet"},
            "legacy": {"code": "df = quotes"},
        },
    )

    def _score(self, after_configs: dict[str, dict[str, object]], **overrides: object):
        node_types = {"quotes": "dataInput", "rates": "dataInput", "legacy": "polars"}
        node_types.update({node: "polars" for node in after_configs if node not in node_types})
        configs = {
            "quotes": {"path": "data/quotes.parquet"},
            "rates": {"path": "data/rates.parquet"},
            "legacy": {"code": "df = quotes"},
            **after_configs,
        }
        return score_self_test(
            _case(
                required_node_types=(),
                required_edges=(),
                require_connected_graph=False,
                **overrides,
            ),
            before=self._BEFORE,
            after=_graph(node_types=node_types, configs=configs),
            telemetry=_telemetry(),
            provider="replay",
            model="trajectory",
            evidence="replay",
        )

    def test_a_config_subset_matches_recursively_and_names_the_first_differing_path(
        self,
    ) -> None:
        subset = {"steps": [{"id": "start", "kind": "source"}], "meta": {"a": 1}}
        matching = self._score(
            {"new": {"steps": [{"id": "start", "kind": "source"}], "meta": {"a": 1, "b": 2}}},
            node_configs={"new": subset},
        )
        differing = self._score(
            {"new": {"steps": [{"id": "start", "kind": "source"}], "meta": {"a": 2}}},
            node_configs={"new": subset},
        )

        assert matching.reasons == ()
        assert differing.reasons == (
            "configuration: node new does not hold the expected value at config.meta.a",
        )
        assert differing.failed_layers == ("configuration",)

    def test_a_changed_or_removed_pre_existing_node_is_collateral_unless_allowed(
        self,
    ) -> None:
        changed = {"rates": {"path": "data/other.parquet"}, "new": {"steps": []}}

        assert self._score(changed).reasons == (
            "collateral: pre-existing node rates changed its configuration",
        )
        assert self._score(changed, modified_nodes=("rates",)).reasons == ()

    def test_new_nodes_of_a_stepped_type_must_open_in_the_step_builder(self) -> None:
        result = self._score(
            {
                "coded": {"code": "df = quotes"},
                "broken": {"steps": [], "_steps_error": "unknown step kind"},
                "legacy": {"code": "df = quotes.head()"},
            },
            modified_nodes=("legacy",),
        )

        # The pre-existing code-mode node stays the analyst's to convert.
        assert result.reasons == (
            "editor: node coded is not authored as steps",
            "editor: node broken carries _steps_error",
        )
