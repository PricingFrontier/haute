"""Assistant evaluation harness contracts: cases, fixtures, scoring, reports and the live runner."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from fnmatch import fnmatch
from pathlib import Path
from types import MappingProxyType

import pytest

from haute._types import GraphEdge, PipelineGraph
from haute.assistant._config import AssistantConfig, EgressPolicy
from haute.assistant._render import render_pipeline_graph
from scripts.assistant_eval_report import (
    RunIdentity,
    compare_report_files,
    configuration_for,
    load_support_matrix,
    report_payload,
    require_git_ignored,
    write_report,
)
from scripts.run_assistant_self_test import (
    AREAS,
    FIXTURE_MODEL_RUN_PLACEHOLDER,
    SPLITS,
    SelfTestCase,
    SelfTestEfficiency,
    SelfTestExpectations,
    SelfTestGraph,
    SelfTestResult,
    SelfTestTelemetry,
    SelfTestToolDiagnostic,
    SelfTestTurnResult,
    TrajectoryProvider,
    load_self_test_cases,
    load_trajectory,
    main,
    prepare_fixture_models,
    run_self_test_case,
    run_self_test_cases_in_processes,
    score_turn,
    select_self_test_cases,
)
from tests._source_files import source_files

EVAL_ROOT = Path(__file__).parent / "assistant_eval"
CASES_ROOT = EVAL_ROOT / "cases"
PROJECTS_ROOT = EVAL_ROOT / "projects"
TRAJECTORIES_ROOT = EVAL_ROOT / "trajectories"
CASES = load_self_test_cases(CASES_ROOT, projects_root=PROJECTS_ROOT)


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
        "saved_changes": 1,
        "change_cards": 1,
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
    }
    values.update(overrides)
    return SelfTestTelemetry(**values)  # type: ignore[arg-type]


def _expectations(**overrides: object) -> SelfTestExpectations:
    values: dict[str, object] = {
        "outcome": "applied",
        "saves": True,
        "required_node_types": ("edgeJoin",),
        "forbidden_node_types": (),
        "forbidden_assistant_text": (),
        "required_edges": (
            ("quotes", "quote_with_competitor", "base"),
            ("competitors", "quote_with_competitor", "join"),
        ),
        "require_connected_graph": True,
        "modified_nodes": (),
        "node_configs": {},
        "execution": (),
        "efficiency": None,
    }
    values.update(overrides)
    return SelfTestExpectations(**values)  # type: ignore[arg-type]


_JOIN_BEFORE = _graph(node_types={"quotes": "dataInput", "competitors": "dataInput"})
_JOIN_AFTER = _graph(
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


def _result(
    *turns: SelfTestTurnResult,
    evidence: str = "live",
    case_id: str = "join_roles",
    area: str = "joins",
) -> SelfTestResult:
    return SelfTestResult(
        id=case_id,
        fixture_version="1",
        area=area,  # type: ignore[arg-type]
        split="development",
        evidence=evidence,  # type: ignore[arg-type]
        provider="databricks" if evidence == "live" else "replay",
        model="served-model",
        turns=turns,
    )


def _case_payload(**overrides: object) -> dict[str, object]:
    expectations: dict[str, object] = {
        "outcome": "applied",
        "saves": True,
        "required_node_types": [],
        "forbidden_node_types": [],
        "required_edges": [],
        "require_connected_graph": True,
        "forbidden_assistant_text": [],
        "modified_nodes": [],
        "node_configs": {},
        "execution": [],
        "efficiency": None,
    }
    expectations.update(overrides.pop("expectations", {}))  # type: ignore[arg-type]
    payload: dict[str, object] = {
        "schema_version": 3,
        "id": "case",
        "fixture_version": "1",
        "project_fixture": "fixture",
        "area": "steps",
        "split": "development",
        "turns": [{"request": "Do it", "expectations": expectations}],
    }
    payload.update(overrides)
    return payload


def _write_case(tmp_path: Path, payload: dict[str, object]) -> tuple[Path, Path]:
    cases = tmp_path / "cases"
    fixture = tmp_path / "projects" / "fixture"
    cases.mkdir()
    fixture.mkdir(parents=True)
    (fixture / "haute.toml").write_text('[project]\npipeline = "pipeline.py"\n')
    (fixture / "pipeline.py").write_text("import haute\npipeline = haute.Pipeline('x')\n")
    (cases / "case.json").write_text(json.dumps(payload), encoding="utf-8")
    return cases, tmp_path / "projects"


class TestPortfolio:
    def test_the_portfolio_covers_every_area_in_both_splits(self) -> None:
        assert {case.area for case in CASES} == set(AREAS)
        assert {case.split for case in CASES} == set(SPLITS)
        assert len(CASES) >= 40
        holdout = [case for case in CASES if case.split == "holdout"]
        assert len(holdout) >= 10
        assert any(len(case.turns) > 1 for case in CASES)
        # A turn that saves part of a request and refuses the rest.
        assert any(
            turn.expectations.outcome == "blocked" and turn.expectations.saves
            for case in CASES
            for turn in case.turns
        )
        # New Polars logic is written steps-first, so no request dictates code
        # (a column may still be named "code").
        for case in CASES:
            for turn in case.turns:
                words = turn.request.casefold()
                assert not any(
                    phrase in words for phrase in ("polars code", "python code", "code mode")
                ), case.id

    def test_cases_and_fixtures_are_separate_from_the_teaching_assets(self) -> None:
        from haute.assistant._assets import example_index

        assert {case.id for case in CASES}.isdisjoint({name for name, _ in example_index()})
        assert not [
            path
            for path in source_files(PROJECTS_ROOT, suffix=None)
            if any(
                fnmatch(part, "*assistant*context*")
                for part in path.relative_to(PROJECTS_ROOT).parts
            )
        ]

    def test_the_harnesses_are_not_part_of_the_installed_package(self) -> None:
        import importlib.util

        for name in ("haute.assistant._self_test", "haute.assistant._evaluation"):
            assert importlib.util.find_spec(name) is None, name

    def test_the_checked_in_join_rating_and_corpus_cases_assert_exact_edges(self) -> None:
        by_id = {case.id: case for case in CASES}
        assert by_id["smoke_join_roles"].turns[0].expectations.required_edges == (
            ("nb_batch", "quote_with_competitor", "base"),
            ("competitor_insight", "quote_with_competitor", "join"),
            ("quote_with_competitor", "enriched_quotes", None),
        )
        assert by_id["smoke_rating_step"].turns[0].expectations.required_edges == (
            ("quotes", "age_rating", None),
            ("age_rating", "age_price_response", None),
        )
        assert by_id["motor_region_loading_join"].turns[0].expectations.required_edges == (
            ("base_premium", "loaded_quotes", "base"),
            ("region_loadings", "loaded_quotes", "join"),
        )


class TestCaseLoading:
    def test_unknown_case_key_fails_closed(self, tmp_path: Path) -> None:
        cases, projects = _write_case(tmp_path, _case_payload(unexpected=True))

        with pytest.raises(ValueError, match="closed case v3 shape"):
            load_self_test_cases(cases, projects_root=projects)

    @pytest.mark.parametrize("key", ["required_node_types", "forbidden_node_types"])
    def test_unknown_node_type_name_fails_case_loading(self, tmp_path: Path, key: str) -> None:
        cases, projects = _write_case(
            tmp_path, _case_payload(expectations={key: ["polars", "polarsTransform"]})
        )

        with pytest.raises(ValueError, match=f"case.json turn 1 {key} names unknown node type"):
            load_self_test_cases(cases, projects_root=projects)

    @pytest.mark.parametrize(
        ("expectations", "message"),
        [
            ({"outcome": "applied", "saves": False}, "an applied turn saves"),
            ({"outcome": "answered", "saves": True}, "an answered turn does not"),
            (
                {
                    "outcome": "blocked",
                    "saves": False,
                    "node_configs": {"quotes": {"path": "x"}},
                },
                "saves nothing, so it cannot expect node configs",
            ),
            ({"outcome": "incomplete", "saves": False}, "unknown expected outcome"),
        ],
    )
    def test_inconsistent_outcomes_fail_case_loading(
        self, tmp_path: Path, expectations: dict[str, object], message: str
    ) -> None:
        cases, projects = _write_case(tmp_path, _case_payload(expectations=expectations))

        with pytest.raises(ValueError, match=message):
            load_self_test_cases(cases, projects_root=projects)

    def test_an_unknown_area_or_split_fails_case_loading(self, tmp_path: Path) -> None:
        cases, projects = _write_case(tmp_path, _case_payload(area="pricing"))
        with pytest.raises(ValueError, match="unknown area"):
            load_self_test_cases(cases, projects_root=projects)

    def test_selection_by_id_area_and_split_keeps_portfolio_order(self) -> None:
        selected = select_self_test_cases(CASES, ("smoke_join_roles", "smoke_categorical_banding"))
        assert [case.id for case in selected] == ["smoke_categorical_banding", "smoke_join_roles"]

        banding = select_self_test_cases(CASES, areas=("banding",), splits=("holdout",))
        assert banding and all(
            (case.area, case.split) == ("banding", "holdout") for case in banding
        )
        with pytest.raises(ValueError, match="Unknown evaluation case: missing"):
            select_self_test_cases(CASES, ("missing",))
        with pytest.raises(ValueError, match="Unknown evaluation area: pricing"):
            select_self_test_cases(CASES, areas=("pricing",))


class TestFixtures:
    @pytest.mark.parametrize(
        "project",
        sorted(path.name for path in PROJECTS_ROOT.iterdir() if (path / "haute.toml").is_file()),
    )
    def test_each_fixture_is_what_the_save_path_writes(
        self, project: str, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """Parsing a copy and saving it through the transactional save service
        rewrites none of the fixture's files."""

        from haute._pipeline_recovery import load_pipeline_editor_document
        from haute._sandbox import bound_project_root
        from haute.routes._helpers import parse_pipeline_to_graph
        from haute.routes._save_pipeline import SavePipelineService

        source = PROJECTS_ROOT / project
        copy = tmp_path_factory.mktemp("f") / "p"
        shutil.copytree(source, copy)
        with bound_project_root(copy):
            graph = parse_pipeline_to_graph(copy / "pipeline.py")
            SavePipelineService(project_root=copy, pipeline_root=copy).save_graph_transactionally(
                graph=graph,
                name=graph.pipeline_name or "",
                description=graph.pipeline_description or "",
                preamble=graph.preamble,
                source_file="pipeline.py",
                base_revision=load_pipeline_editor_document(
                    copy / "pipeline.py", project_root=copy
                ).source_revision,
            )

        changed = [
            path.relative_to(source).as_posix()
            for path in source_files(source, suffix=None)
            if (copy / path.relative_to(source)).read_bytes() != path.read_bytes()
        ]
        assert changed == []
        runtime = {".haute_cache", "mlruns", ".cache"}
        written = {
            path.relative_to(copy).as_posix()
            for path in source_files(copy, suffix=None)
            if not runtime & set(path.relative_to(copy).parts)
        }
        assert written == {
            path.relative_to(source).as_posix() for path in source_files(source, suffix=None)
        }

    def test_preparing_a_copy_logs_its_model_and_points_the_node_at_the_run(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        copy = tmp_path_factory.mktemp("m") / "p"
        shutil.copytree(PROJECTS_ROOT / "motor_pricing", copy)
        sidecar = copy / "config" / "model_scoring" / "claim_frequency.json"

        assert prepare_fixture_models(copy) == ("claim_frequency",)

        run_id = json.loads(sidecar.read_text(encoding="utf-8"))["run_id"]
        assert run_id != FIXTURE_MODEL_RUN_PLACEHOLDER
        assert list((copy / "mlruns").rglob(f"{run_id}/artifacts/claim_frequency.cbm"))

    def test_a_model_without_its_placeholder_fails_preparation(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        copy = tmp_path_factory.mktemp("m") / "p"
        shutil.copytree(PROJECTS_ROOT / "motor_pricing", copy)
        sidecar = copy / "config" / "model_scoring" / "claim_frequency.json"
        sidecar.write_text(
            sidecar.read_text(encoding="utf-8").replace(FIXTURE_MODEL_RUN_PLACEHOLDER, "1" * 32),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="fixture models and Model Scoring placeholders"):
            prepare_fixture_models(copy)


class TestGraphReading:
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


class TestScoring:
    def test_accepts_applied_connected_join_with_exact_ports(self) -> None:
        turn = score_turn(
            _expectations(), before=_JOIN_BEFORE, after=_JOIN_AFTER, telemetry=_telemetry()
        )

        assert turn.passed is True
        assert turn.reasons == ()

    def test_a_turn_that_saved_and_then_blocked_is_a_saved_blocked_turn(self) -> None:
        """The typed outcome decides: a saved change followed by a blocker is
        both a saved change and a blocked outcome, never simply applied."""

        telemetry = _telemetry(outcome="blocked")

        blocked = score_turn(
            _expectations(outcome="blocked", saves=True),
            before=_JOIN_BEFORE,
            after=_JOIN_AFTER,
            telemetry=telemetry,
        )
        applied = score_turn(
            _expectations(), before=_JOIN_BEFORE, after=_JOIN_AFTER, telemetry=telemetry
        )
        unsaved = score_turn(
            _expectations(
                outcome="blocked", saves=False, required_node_types=(), required_edges=()
            ),
            before=_JOIN_BEFORE,
            after=_JOIN_AFTER,
            telemetry=telemetry,
        )

        assert blocked.reasons == ()
        assert applied.reasons == ("protocol: outcome was blocked; expected applied",)
        assert unsaved.reasons == ("protocol: saved 1 changes; expected none",)

    def test_a_turn_that_stopped_before_finishing_matches_no_expected_outcome(self) -> None:
        graph = _graph(node_types={"quotes": "polars"})

        turn = score_turn(
            _expectations(
                outcome="answered", saves=False, required_node_types=(), required_edges=()
            ),
            before=graph,
            after=graph,
            telemetry=_telemetry(outcome="incomplete", saved_changes=0, change_cards=0),
        )

        assert turn.reasons == ("protocol: outcome was incomplete; expected answered",)

    def test_null_handle_matches_an_edge_by_endpoints(self) -> None:
        after = _graph(
            node_types={"quotes": "dataInput", "age_band": "banding"},
            edges=(("quotes", "age_band", "frame"),),
        )

        turn = score_turn(
            _expectations(
                required_node_types=("banding",),
                required_edges=(("quotes", "age_band", None),),
            ),
            before=_graph(node_types={"quotes": "dataInput"}),
            after=after,
            telemetry=_telemetry(),
        )

        assert turn.reasons == ()

    @pytest.mark.parametrize(
        ("telemetry", "reason"),
        [
            (_telemetry(terminal="failed", outcome=None), "protocol: turn terminal was failed"),
            (
                _telemetry(saved_changes=0, change_cards=0),
                "protocol: saved 0 changes; expected a saved change",
            ),
            (_telemetry(change_cards=0), "protocol: 0 change cards for 1 saved changes"),
            (
                _telemetry(leaked_forbidden_text=1),
                "protocol: assistant output leaked 1 forbidden canary values",
            ),
        ],
    )
    def test_rejects_incomplete_or_unsaved_turns(
        self, telemetry: SelfTestTelemetry, reason: str
    ) -> None:
        turn = score_turn(
            _expectations(), before=_JOIN_BEFORE, after=_JOIN_AFTER, telemetry=telemetry
        )

        assert reason in turn.reasons

    def test_efficiency_limits_fail_only_a_case_that_declares_them(self) -> None:
        wasteful = _telemetry(provider_round_trips=9, tool_calls=12, failed_tool_calls=3)

        measured = score_turn(
            _expectations(), before=_JOIN_BEFORE, after=_JOIN_AFTER, telemetry=wasteful
        )
        limited = score_turn(
            _expectations(
                efficiency=SelfTestEfficiency(
                    max_provider_round_trips=3,
                    max_tool_calls=2,
                    max_failed_tool_calls=0,
                    max_duplicate_static_reads=0,
                )
            ),
            before=_JOIN_BEFORE,
            after=_JOIN_AFTER,
            telemetry=wasteful,
        )

        assert measured.reasons == ()
        assert limited.reasons == (
            "protocol: provider round trips 9 exceeded 3",
            "protocol: tool calls 12 exceeded 2",
            "protocol: failed tool calls 3 exceeded 0",
        )

    def test_a_case_sums_its_turns_and_names_its_first_failing_layer(self) -> None:
        good = score_turn(
            _expectations(), before=_JOIN_BEFORE, after=_JOIN_AFTER, telemetry=_telemetry()
        )
        bad = score_turn(
            _expectations(node_configs={"quote_with_competitor": {"how": "left"}}),
            before=_JOIN_BEFORE,
            after=_JOIN_AFTER,
            telemetry=_telemetry(tool_calls=5),
        )

        result = _result(good, bad)

        assert result.passed is False
        assert result.first_failing_layer == "configuration"
        assert result.reasons == (
            "turn 2 configuration: node quote_with_competitor does not hold the expected value "
            "at config.how",
        )
        assert result.metrics["tool_calls"] == 7

    def test_rejects_disconnected_new_nodes_and_wrong_join_port(self) -> None:
        turn = score_turn(
            _expectations(),
            before=_JOIN_BEFORE,
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
        )

        assert (
            "structure: required edge competitors -> quote_with_competitor [join] is missing"
            in turn.reasons
        )
        assert (
            "structure: changed nodes and their neighbours are not one connected component: orphan"
            in turn.reasons
        )

    def test_connectivity_ignores_untouched_nodes_elsewhere_in_the_fixture(self) -> None:
        """A fixture input the request never touches stays unconnected; only the
        changed nodes and their neighbours must form one component."""

        turn = score_turn(
            _expectations(
                required_node_types=("polars",), required_edges=(("quotes", "high", None),)
            ),
            before=_graph(node_types={"quotes": "dataInput", "rates": "dataInput"}),
            after=_graph(
                node_types={"quotes": "dataInput", "rates": "dataInput", "high": "polars"},
                edges=(("quotes", "high", None),),
                configs={"quotes": {}, "rates": {}, "high": {"steps": []}},
            ),
            telemetry=_telemetry(),
        )

        assert turn.reasons == ()

    def test_a_removed_edge_that_strands_its_endpoint_is_reported(self) -> None:
        turn = score_turn(
            _expectations(required_node_types=(), required_edges=()),
            before=_graph(
                node_types={"quotes": "dataInput", "high": "polars", "out": "output"},
                edges=(("quotes", "high", None), ("high", "out", None)),
            ),
            after=_graph(
                node_types={"quotes": "dataInput", "high": "polars", "out": "output"},
                edges=(("quotes", "high", None),),
            ),
            telemetry=_telemetry(),
        )

        assert turn.reasons == (
            "structure: changed nodes and their neighbours are not one connected component: out",
        )


class TestLayers:
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
        return score_turn(
            _expectations(
                required_node_types=(),
                required_edges=(),
                require_connected_graph=False,
                **overrides,
            ),
            before=self._BEFORE,
            after=_graph(node_types=node_types, configs=configs),
            telemetry=_telemetry(),
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
    return next(case for case in CASES if case.id == case_id)


def _banding_replay(_config: AssistantConfig) -> TrajectoryProvider:
    return TrajectoryProvider(load_trajectory(TRAJECTORIES_ROOT / "smoke_categorical_banding.json"))


async def test_scripted_provider_runs_real_disposable_mutation_flow(tmp_path: Path) -> None:
    """The case runs under the harness's own egress allowances, whatever the
    invoking project permits, and a transcript keeps what the model saw."""

    provider = _banding_replay(_scripted_config(_PERMISSIVE_EGRESS))

    from haute.routes._helpers import pipeline_dir

    pipeline_dir.cache_clear()
    pipeline_dir()
    transcript: list[dict[str, object]] = []
    result = await run_self_test_case(
        _load_case("smoke_categorical_banding"),
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        work_dir=tmp_path,
        provider_factory=lambda _config: provider,
        transcript=transcript,
    )

    assert result.passed is True, result.reasons
    assert result.evidence == "live"
    (turn,) = result.turns
    assert (turn.telemetry.outcome, turn.telemetry.saved_changes) == ("applied", 1)
    assert turn.telemetry.change_cards == 1
    assert "banding" in turn.node_types
    # Read the graph, dry-run the recipe operation, apply, then answer.
    assert turn.telemetry.provider_round_trips == 4
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
    (entry,) = transcript
    assert entry["request"] == _load_case("smoke_categorical_banding").turns[0].request
    events = entry["events"]
    assert [event["tool"] for event in events if "tool" in event] == [
        "get_pipeline",
        "dry_run_graph_edits",
        "apply_graph_plan",
    ]
    assert events[-1]["outcome"]["kind"] == "applied"


async def test_one_apply_per_turn_ends_the_turn_at_its_first_saving_apply(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The variant ends the staged build after its first saved plan without
    another provider request, so the later stages are never saved."""

    case = _load_case("smoke_staged_pricing_build")
    provider = TrajectoryProvider(
        load_trajectory(TRAJECTORIES_ROOT / "smoke_staged_pricing_build.json")
    )

    result = await run_self_test_case(
        case,
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        work_dir=tmp_path_factory.mktemp("v"),
        provider_factory=lambda _config: provider,
        variant="one_apply_per_turn",
    )

    (turn,) = result.turns
    assert (turn.telemetry.outcome, turn.telemetry.saved_changes) == ("applied", 1)
    # The dry-run round and the apply round; the round after the apply never reaches the model.
    assert turn.telemetry.provider_round_trips == 2
    assert [diagnostic.name for diagnostic in turn.tool_diagnostics] == [
        "dry_run_graph_edits",
        "apply_graph_plan",
    ]
    assert result.passed is False
    assert result.first_failing_layer == "structure"


@pytest.mark.slow
def test_record_runs_each_case_in_its_own_process_and_writes_transcripts(
    tmp_path: Path,
) -> None:
    case = _load_case("smoke_categorical_banding")
    transcripts = tmp_path / "transcripts"

    results = run_self_test_cases_in_processes(
        (case,),
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        provider_factory=_banding_replay,
        transcripts=transcripts,
    )

    assert [(result.id, result.reasons) for result in results] == [
        ("smoke_categorical_banding", ()),
    ]
    saved = json.loads((transcripts / "smoke_categorical_banding.json").read_text("utf-8"))
    assert saved["schema_version"] == 1
    assert saved["turns"][0]["request"] == case.turns[0].request


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


_RUN = RunIdentity(
    run_id="20261001T000000Z-abcdef",
    started_at="2026-10-01T00:00:00+00:00",
    variant="multi_apply",
    provider="databricks",
    model="served-model",
    configuration="databricks-qwen35-122b-a10b",
    haute_version="0.0.0",
)


def _join_turn(**telemetry: object) -> SelfTestTurnResult:
    return score_turn(
        _expectations(),
        before=_JOIN_BEFORE,
        after=_JOIN_AFTER,
        telemetry=_telemetry(**telemetry),
        tool_diagnostics=(
            SelfTestToolDiagnostic(
                name="dry_run_graph_edits",
                status="error",
                error_code="invalid_plan",
                validation_path="ops[2].target_handle",
                validation_reason="must satisfy one allowed branch",
            ),
        ),
    )


def test_report_is_redacted_and_aggregates_each_area(tmp_path: Path) -> None:
    passing = _result(_join_turn(tool_calls=2))
    failing = replace(
        _result(_join_turn(tool_calls=6, change_cards=0), case_id="join_again"),
    )
    path = write_report(tmp_path / "report.json", (passing, failing), _RUN)
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    case = payload["cases"][0]
    assert case["turns"][0]["tools"] == [
        {
            "error_code": "invalid_plan",
            "name": "dry_run_graph_edits",
            "status": "error",
            "validation_path": "ops[2].target_handle",
            "validation_reason": "must satisfy one allowed branch",
        }
    ]
    assert (case["id"], case["area"], case["split"]) == ("join_roles", "joins", "development")
    assert case["turns"][0]["outcome"] == "applied"
    assert case["turns"][0]["saved_changes"] == 1
    assert case["layers"] == dict.fromkeys(
        ("protocol", "structure", "configuration", "collateral", "editor", "execution"), True
    )
    assert payload["cases"][1]["first_failing_layer"] == "protocol"
    assert payload["evidence"] == "live"
    assert payload["run"]["configuration"] == "databricks-qwen35-122b-a10b"
    assert payload["areas"] == {
        "joins": {
            "cases": 2,
            "passed": 1,
            "metrics": {
                "provider_round_trips": 3.0,
                "tool_calls": 4.0,
                "failed_tool_calls": 0.0,
                "duplicate_static_reads": 0.0,
                "input_tokens": 10.0,
                "output_tokens": 5.0,
                "time_to_first_token_ms": 20.0,
                "time_to_validated_plan_ms": 30.0,
                "end_to_end_ms": 40.0,
            },
        }
    }
    assert "Join the sources" not in raw
    assert "arguments" not in raw
    assert "assistant_text" not in raw


def test_a_report_never_mixes_replay_and_live_evidence(tmp_path: Path) -> None:
    graph = _graph(node_types={"quotes": "dataInput"})
    live, replay = (
        _result(
            score_turn(
                _expectations(
                    outcome="answered", saves=False, required_node_types=(), required_edges=()
                ),
                before=graph,
                after=graph,
                telemetry=_telemetry(outcome="answered", saved_changes=0, change_cards=0),
            ),
            evidence=evidence,
        )
        for evidence in ("live", "replay")
    )

    assert report_payload((replay,), _RUN)["evidence"] == "replay"
    with pytest.raises(ValueError, match="exactly one evidence kind"):
        report_payload((live, replay), _RUN)
    write_report(tmp_path / "live.json", (live,), _RUN)
    write_report(tmp_path / "replay.json", (replay,), _RUN)
    with pytest.raises(ValueError, match="replay and live reports are never compared"):
        compare_report_files(tmp_path / "live.json", tmp_path / "replay.json")


def test_compare_reports_area_counts_flips_and_metric_differences(tmp_path: Path) -> None:
    before = (
        _result(_join_turn(tool_calls=2)),
        _result(_join_turn(change_cards=0), case_id="dropped"),
    )
    after = (
        _result(_join_turn(tool_calls=6, change_cards=0)),
        _result(_join_turn(), case_id="added", area="banding"),
    )
    write_report(tmp_path / "before.json", before, _RUN)
    write_report(tmp_path / "after.json", after, replace(_RUN, variant="one_apply_per_turn"))

    comparison = compare_report_files(tmp_path / "before.json", tmp_path / "after.json")

    assert comparison["after"]["variant"] == "one_apply_per_turn"
    assert comparison["flips"] == [
        {
            "id": "join_roles",
            "area": "joins",
            "before": "passed",
            "after": "failed",
            "first_failing_layer": "protocol",
        }
    ]
    assert (comparison["only_before"], comparison["only_after"]) == (["dropped"], ["added"])
    joins = comparison["areas"]["joins"]
    assert joins["before"] == {"cases": 2, "passed": 1}
    assert joins["after"] == {"cases": 1, "passed": 0}
    assert joins["metrics"]["tool_calls"] == {"before": 2.0, "after": 6.0, "difference": 4.0}
    assert comparison["areas"]["banding"]["before"] is None


def test_list_prints_the_selected_cases_without_a_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["list", "--area", "multi_turn"]) == 0

    listed = json.loads(capsys.readouterr().out)["cases"]
    assert listed and all(case["area"] == "multi_turn" for case in listed)
    assert any(case["turns"] > 1 for case in listed)


def test_transcripts_are_refused_outside_a_git_ignored_directory(tmp_path: Path) -> None:
    from haute import _git

    _git._run_git("init", "-q", cwd=tmp_path)
    (tmp_path / ".gitignore").write_text("/.haute/\n", encoding="utf-8")

    require_git_ignored(tmp_path / ".haute" / "assistant-eval" / "run" / "transcripts")
    with pytest.raises(ValueError, match="only under a Git-ignored directory"):
        require_git_ignored(tmp_path / "transcripts")


def test_the_support_matrix_attributes_a_run_to_its_configuration() -> None:
    matrix = load_support_matrix(EVAL_ROOT / "support_matrix.json")

    assert (
        configuration_for(matrix, provider="databricks", model="databricks-qwen35-122b-a10b")
        == "databricks-qwen35-122b-a10b"
    )
    assert configuration_for(matrix, provider="anthropic", model="unlisted") is None
