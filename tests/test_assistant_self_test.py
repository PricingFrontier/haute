"""Configured-provider assistant self-test harness contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from haute._types import GraphEdge, PipelineGraph
from haute.assistant._config import AssistantConfig, EgressPolicy
from haute.assistant._providers import ProviderUsage, ToolCallRequest, TurnStop
from haute.assistant._render import render_pipeline_graph
from scripts.run_assistant_self_test import (
    SelfTestCase,
    SelfTestExpectations,
    SelfTestGraph,
    SelfTestTelemetry,
    SelfTestToolDiagnostic,
    load_self_test_cases,
    run_self_test_case,
    run_self_test_cases_in_processes,
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

    @pytest.mark.parametrize("key", ["required_node_types", "forbidden_node_types"])
    def test_unknown_node_type_name_fails_case_loading(self, tmp_path: Path, key: str) -> None:
        cases = tmp_path / "cases"
        fixture = tmp_path / "projects" / "fixture"
        cases.mkdir()
        fixture.mkdir(parents=True)
        (fixture / "haute.toml").write_text('[project]\npipeline = "pipeline.py"\n')
        (fixture / "pipeline.py").write_text("import haute\npipeline = haute.Pipeline('x')\n")
        expectations: dict[str, object] = {
            "outcome": "applied",
            "required_node_types": ["polars"],
            "forbidden_node_types": ["edgeJoin"],
            "forbidden_assistant_text": [],
            "required_edges": [],
            "require_connected_graph": True,
            "max_provider_round_trips": 8,
            "max_tool_calls": 16,
            "max_failed_tool_calls": 1,
            "max_duplicate_static_reads": 1,
        }
        expectations[key] = ["polars", "polarsTransform"]
        payload = {
            "schema_version": 1,
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
    def test_edge_handles_are_read_under_the_names_the_renderer_emits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_read_graph` consumes `get_pipeline`, whose renderer names handles
        the way the graph-edit operations accept them. Reading a stale key
        yields `None` for every edge without raising, which silently scores
        every handle-qualified required edge — every Edge Join role — as
        missing no matter what the assistant built.
        """

        from scripts import run_assistant_self_test as _self_test

        monkeypatch.setattr(
            _self_test,
            "get_pipeline",
            lambda _source: {
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
                    )
                ),
            },
        )

        graph = _self_test._read_graph("main.py")

        assert set(graph.edges) == {
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
        assert (
            "changed nodes and their neighbours are not one connected component: orphan"
            in result.reasons
        )

    def test_connectivity_ignores_untouched_nodes_elsewhere_in_the_fixture(self) -> None:
        """A fixture input the request never touches stays unconnected; only the
        changed nodes and their neighbours must form one component."""

        before = _graph(node_types={"quotes": "dataInput", "rates": "dataInput"})
        after = _graph(
            node_types={"quotes": "dataInput", "rates": "dataInput", "high": "polars"},
            edges=(("quotes", "high", None),),
        )

        result = score_self_test(
            _case(required_node_types=("polars",), required_edges=(("quotes", "high", None),)),
            before=before,
            after=after,
            telemetry=_telemetry(),
            provider="databricks",
            model="served-model",
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
        )

        assert result.reasons == (
            "changed nodes and their neighbours are not one connected component: out",
        )


class _ApplyingProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.system = ""

    async def stream_turn(self, *, system, messages, tools):
        self.calls += 1
        self.system = system
        if self.calls == 1:
            yield ToolCallRequest("inspect", "get_pipeline", {})
            yield TurnStop("tool_use", ProviderUsage(input_tokens=2, output_tokens=1))
            return
        if self.calls == 2:
            yield ToolCallRequest(
                "recipe",
                "plan_recipe",
                {
                    "recipe_id": "categorical_banding",
                    "source": "quotes",
                    "name": "region_band",
                    "column": "region",
                    "output_column": "region_group",
                    "rules": [
                        {"value": "north", "assignment": "core"},
                        {"value": "south", "assignment": "core"},
                        {"value": "east", "assignment": "other"},
                        {"value": "west", "assignment": "other"},
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


async def test_scripted_provider_runs_real_disposable_mutation_flow() -> None:
    """The case runs under the harness's own egress allowances, whatever the
    invoking project permits."""

    provider = _ApplyingProvider()

    from haute.routes._helpers import pipeline_dir

    pipeline_dir.cache_clear()
    pipeline_dir()
    result = await run_self_test_case(
        _load_case("smoke_categorical_banding"),
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        provider_factory=lambda _config: provider,
    )

    assert result.passed is True, result.reasons
    assert result.telemetry.applied_plan is True
    assert "banding" in result.node_types
    assert provider.calls == 4
    assert "- Provider trust: `organization`" in provider.system
    assert "- Highest sensitivity sent: `internal`" in provider.system
    assert "- Project knowledge: not permitted" in provider.system
    assert "- Executable source: not permitted" in provider.system
    assert "- Column value profiles: not permitted" in provider.system


def _applying_provider_factory(_config: AssistantConfig) -> _ApplyingProvider:
    return _ApplyingProvider()


@pytest.mark.slow
def test_the_command_runs_each_case_in_its_own_process() -> None:
    case = _load_case("smoke_categorical_banding")

    results = run_self_test_cases_in_processes(
        (case,),
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        provider_factory=_applying_provider_factory,
    )

    assert [(result.id, result.reasons) for result in results] == [
        ("smoke_categorical_banding", ()),
    ]


async def test_an_external_provider_is_refused_before_the_case_runs() -> None:
    external = EgressPolicy(
        trust="external",
        max_sensitivity="public",
        allow_project_knowledge=False,
        allow_executable_source=False,
        allow_row_samples=False,
    )

    def unexpected_provider(_config: AssistantConfig) -> _ApplyingProvider:
        raise AssertionError("no provider may be created for a refused configuration")

    with pytest.raises(ValueError, match="external trust is public-only"):
        await run_self_test_case(
            _load_case("smoke_categorical_banding"),
            projects_root=PROJECTS_ROOT,
            config=_scripted_config(external),
            provider_factory=unexpected_provider,
        )


_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "polars_steps_corpus"
#: Each corpus item's free-code card: the snippet with its source input read as `df`.
_FREE_CODE = {
    "high_premium_quotes": (
        "# Keep high-premium quotes\ndf = df.filter(pl.col('premium') > 1500)\n"
    ),
    "underwriting_decision": (
        "# Underwriting decision\n"
        "df = df.with_columns(\n"
        "    pl.when(pl.col('age') < 18).then(pl.lit('decline'))\n"
        "    .when((pl.col('claims_count') >= 3) | (pl.col('vehicle_value') > 100000))\n"
        "    .then(pl.lit('refer'))\n"
        "    .when(pl.col('premium').is_null()).then(pl.lit('await_price'))\n"
        "    .otherwise(pl.lit('accept'))\n"
        "    .alias('decision')\n"
        ")\n"
    ),
    "attach_regional_rates": (
        "# Attach regional rates\n"
        "df = df.join(rates, on='region', how='left', validate='m:1', maintain_order='left')\n"
        "df = df.with_columns(\n"
        "    (pl.col('base_rate') * pl.col('factor')).alias('regional_benchmark')\n"
        ")\n"
    ),
}


def _reference_trajectories() -> list[tuple[str, str, list[dict[str, object]]]]:
    """Three corpus items, each authored as structured steps and as free code."""

    corpus = {
        item["id"]: item
        for item in json.loads((_CORPUS_ROOT / "corpus.json").read_text(encoding="utf-8"))
    }
    translations = json.loads((_CORPUS_ROOT / "translations.json").read_text(encoding="utf-8"))
    trajectories: list[tuple[str, str, list[dict[str, object]]]] = []
    for item_id, code in _FREE_CODE.items():
        assert corpus[item_id]["inputs"][0] == "quotes"
        structured = translations[item_id]["steps"]
        assert structured[0] == {"id": structured[0]["id"], "kind": "source", "input": "quotes"}
        free_code = [
            {"id": "start", "kind": "source", "input": "quotes"},
            {"id": "logic", "kind": "free_code", "code": code},
        ]
        trajectories.append((item_id, "structured", structured))
        trajectories.append((item_id, "free_code", free_code))
    return trajectories


class _TrajectoryProvider:
    """Dry-run one Transform with *steps* wired from its inputs, apply it, stop."""

    def __init__(self, name: str, steps: list[dict[str, object]], inputs: list[str]) -> None:
        self.ops: list[dict[str, object]] = [
            {
                "op": "add_node",
                "node_type": "polars",
                "name": name,
                "ref": "transform",
                "config": {"steps": steps},
            },
            *({"op": "add_edge", "source": source, "target": "$transform"} for source in inputs),
        ]
        self.calls = 0

    async def stream_turn(self, *, system, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield ToolCallRequest("dry", "dry_run_graph_edits", {"ops": self.ops})
            yield TurnStop("tool_use", ProviderUsage(input_tokens=2, output_tokens=1))
            return
        if self.calls == 2:
            dry_run = messages[-1]["content"]
            assert "plan_hash" in dry_run, dry_run
            yield ToolCallRequest("apply", "apply_graph_plan", {"plan_hash": dry_run["plan_hash"]})
            yield TurnStop("tool_use", ProviderUsage(input_tokens=2, output_tokens=1))
            return
        yield TurnStop("end", ProviderUsage(input_tokens=2, output_tokens=1))


async def test_six_step_corpus_trajectories_pass_in_one_harness_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six file-input cases run back to back in one process. Each binds the
    sandbox root to its own disposable copy, so no case reads a previous
    case's deleted project, and each Transform is saved with the steps its
    trajectory authored."""

    from haute._sandbox import _get_project_root
    from haute.routes._helpers import parse_pipeline_to_graph
    from scripts import run_assistant_self_test as _self_test

    saved_configs: list[dict[str, dict[str, object]]] = []
    read_graph = _self_test._read_graph

    def read_and_capture(source_file: str) -> SelfTestGraph:
        graph = read_graph(source_file)
        parsed = parse_pipeline_to_graph(Path(source_file))
        saved_configs.append({node.id: dict(node.data.config) for node in parsed.nodes})
        return graph

    monkeypatch.setattr(_self_test, "_read_graph", read_and_capture)
    root_before = _get_project_root()
    results = []
    for item_id, form, steps in _reference_trajectories():
        case = _load_case(f"smoke_corpus_{item_id}")
        inputs = [source for source, _target, _handle in case.expectations.required_edges]
        provider = _TrajectoryProvider(item_id, steps, inputs)
        result = await run_self_test_case(
            case,
            projects_root=PROJECTS_ROOT,
            config=_scripted_config(_PERMISSIVE_EGRESS),
            provider_factory=lambda _config, provider=provider: provider,
        )
        results.append((item_id, form, result))
        assert saved_configs[-1][item_id]["steps"] == steps, (item_id, form)

    assert [(item_id, form, result.reasons) for item_id, form, result in results] == [
        (item_id, form, ()) for item_id, form, _steps in _reference_trajectories()
    ]
    assert _get_project_root() == root_before


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
