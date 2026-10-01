"""Assistant evaluation harness contracts: cases, fixtures, scoring, reports and the live runner."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from datetime import date
from fnmatch import fnmatch
from pathlib import Path
from types import MappingProxyType

import polars as pl
import pytest

from haute._sandbox import bound_project_root
from haute._types import GraphEdge, PipelineGraph
from haute.assistant._config import AssistantConfig, EgressPolicy
from haute.assistant._providers import DatabricksProvider, OpenAIProvider
from haute.assistant._render import render_pipeline_graph
from haute.routes._helpers import parse_pipeline_to_graph
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
    _golden_frame,
    canonical_tools_provider,
    load_self_test_cases,
    load_trajectory,
    main,
    prepare_fixture_models,
    run_self_test_case,
    run_self_test_cases_in_processes,
    score_turn,
    select_self_test_cases,
    self_test_config,
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
        egress="project",
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
        "schema_version": 4,
        "id": "case",
        "fixture_version": "1",
        "project_fixture": "fixture",
        "area": "steps",
        "split": "development",
        "egress": "project",
        "inapplicable_variants": [],
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

    def test_every_stated_breakpoint_is_a_value_the_golden_bands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configuration matching leaves free a key a factor does not state, so a
        breakpoint factor that adds `rightClosed: false` holds every key its case
        states. Execution catches that inverted closure only on a row that sits
        on a boundary, so each boundary a case states is a value of the column
        its golden bands."""

        covered: set[str] = set()
        for case in CASES:
            for turn in case.turns:
                goldens = {golden.node: golden for golden in turn.expectations.execution}
                for node, subset in turn.expectations.node_configs.items():
                    factors = [
                        factor
                        for factor in subset.get("factors", ())
                        if factor["banding"] == "breakpoints"
                    ]
                    if not factors:
                        continue
                    monkeypatch.chdir(PROJECTS_ROOT / case.project_fixture)
                    frame = _golden_frame(goldens[node].golden)
                    for factor in factors:
                        column = frame[factor["column"]]
                        for rule in factor["rules"]:
                            if rule["boundary"] == "":
                                continue
                            value = (
                                date.fromisoformat(rule["boundary"])
                                if column.dtype == pl.Date
                                else float(rule["boundary"])
                            )
                            assert (column == value).any(), (case.id, factor["outputColumn"], value)
                    covered.add(case.id)

        assert covered == {
            "breakpoint_age_banding",
            "motor_licence_band_refine",
            "motor_region_regroup",
            "motor_value_band_factor",
        }

    def test_two_cases_run_metadata_only_and_two_are_inapplicable_to_one_apply(self) -> None:
        """Every other case runs under the configured project's policy. The
        metadata-only pair are a list edit the blind-rewrite guard must stop and
        an added node that needs only schemas; the inapplicable pair cannot end
        as expected when the turn stops at its first saving apply."""

        assert {case.id for case in CASES if case.egress == "metadata_only"} == {
            "motor_value_band_factor_withheld",
            "breakpoint_age_banding",
        }
        assert {case.egress for case in CASES} == {"project", "metadata_only"}
        inapplicable = {case.id: case.inapplicable_variants for case in CASES}
        assert {case_id: variants for case_id, variants in inapplicable.items() if variants} == {
            "motor_explore_then_run_blocked": ("one_apply_per_turn",),
            "smoke_staged_pricing_build": ("one_apply_per_turn",),
        }

    def test_every_new_node_an_expectation_names_is_named_in_the_request(self) -> None:
        """An expectation never depends on a node name the analyst did not give:
        each node a turn's expectations name that the project does not hold yet
        is named in that turn's request or an earlier turn's, and a file path
        (`data/claims_history.parquet`) does not name a node."""

        fixtures: dict[str, set[str]] = {}
        for case in CASES:
            if case.project_fixture not in fixtures:
                root = PROJECTS_ROOT / case.project_fixture
                with bound_project_root(root):
                    graph = parse_pipeline_to_graph(root / "pipeline.py")
                fixtures[case.project_fixture] = {node.id for node in graph.nodes}
            known = set(fixtures[case.project_fixture])
            for index, turn in enumerate(case.turns, start=1):
                expected = turn.expectations
                named = (
                    {node for edge in expected.required_edges for node in edge[:2]}
                    | set(expected.node_configs)
                    | {golden.node for golden in expected.execution}
                )
                unstated = sorted(
                    node
                    for node in named - known
                    if not re.search(rf"(?<![\w/]){re.escape(node)}(?!\w|\.\w)", turn.request)
                )
                assert unstated == [], (case.id, index)
                known |= named


class TestCaseLoading:
    def test_unknown_case_key_fails_closed(self, tmp_path: Path) -> None:
        cases, projects = _write_case(tmp_path, _case_payload(unexpected=True))

        with pytest.raises(ValueError, match="closed case v4 shape"):
            load_self_test_cases(cases, projects_root=projects)

    @pytest.mark.parametrize("key", ["egress", "inapplicable_variants"])
    def test_a_case_without_its_egress_or_variants_fails_closed(
        self, tmp_path: Path, key: str
    ) -> None:
        payload = _case_payload()
        del payload[key]
        cases, projects = _write_case(tmp_path, payload)

        with pytest.raises(ValueError, match="closed case v4 shape"):
            load_self_test_cases(cases, projects_root=projects)

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"egress": "internal"}, "names an unknown egress profile"),
            ({"inapplicable_variants": ["one_apply"]}, "names unknown variant"),
            ({"inapplicable_variants": ["multi_apply"]}, "cannot name multi_apply"),
        ],
    )
    def test_an_unknown_egress_profile_or_variant_fails_case_loading(
        self, tmp_path: Path, overrides: dict[str, object], message: str
    ) -> None:
        cases, projects = _write_case(tmp_path, _case_payload(**overrides))

        with pytest.raises(ValueError, match=message):
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

    def _score(
        self,
        after_configs: dict[str, dict[str, object]],
        *,
        new_type: str = "polars",
        **overrides: object,
    ):
        node_types = {"quotes": "dataInput", "rates": "dataInput", "legacy": "polars"}
        node_types.update({node: new_type for node in after_configs if node not in node_types})
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

    def test_same_length_lists_match_element_by_element_as_subsets(self) -> None:
        """A key a list's mapping element leaves out is free, as it is in a
        mapping, so a label the request never stated is not mandatory; the
        element at each position still holds every key the case states."""

        subset = {"values": [{"field": "premium"}, {"field": "quote_id", "aggregation": "count"}]}
        saved = [
            {"field": "premium", "display_name": "Total premium"},
            {"field": "quote_id", "aggregation": "count", "display_name": "Number of quotes"},
        ]
        wrong = [saved[0], {**saved[1], "aggregation": "sum"}]

        output = {"new_type": "output", "node_configs": {"new": subset}}

        assert self._score({"new": {"values": saved}}, **output).reasons == ()
        assert self._score({"new": {"values": wrong}}, **output).reasons == (
            "configuration: node new does not hold the expected value at "
            "config.values[1].aggregation",
        )

    @pytest.mark.parametrize(
        ("actual", "path"),
        [
            pytest.param([{"field": "premium"}], "config.values", id="shorter"),
            pytest.param(
                [{"field": "premium"}, {"field": "quote_id"}, {"field": "region"}],
                "config.values",
                id="longer",
            ),
            pytest.param(
                [{"field": "quote_id"}, {"field": "premium"}],
                "config.values[0].field",
                id="reordered",
            ),
            pytest.param({"field": "premium"}, "config.values", id="not-a-list"),
            pytest.param([{"field": "premium"}, "quote_id"], "config.values[1]", id="scalar"),
            pytest.param([{"field": "premium"}, {}], "config.values[1].field", id="missing-key"),
        ],
    )
    def test_a_list_of_another_length_order_or_shape_does_not_match(
        self, actual: object, path: str
    ) -> None:
        turn = self._score(
            {"new": {"values": actual}},
            new_type="output",
            node_configs={"new": {"values": [{"field": "premium"}, {"field": "quote_id"}]}},
        )

        assert turn.reasons == (
            f"configuration: node new does not hold the expected value at {path}",
        )

    def test_lists_of_scalars_match_only_element_for_element(self) -> None:
        subset = {"new": {"selected_columns": ["quote_id", "premium"]}}
        swapped = {"new": {"selected_columns": ["premium", "quote_id"]}}

        assert self._score(subset, new_type="output", node_configs=subset).reasons == ()
        assert self._score(swapped, new_type="output", node_configs=subset).reasons == (
            "configuration: node new does not hold the expected value at "
            "config.selected_columns[0]",
        )

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
    allow_aggregate_statistics=True,
)


def _load_case(case_id: str) -> SelfTestCase:
    return next(case for case in CASES if case.id == case_id)


def _banding_replay(_config: AssistantConfig) -> TrajectoryProvider:
    return TrajectoryProvider(load_trajectory(TRAJECTORIES_ROOT / "smoke_categorical_banding.json"))


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (
            "project",
            EgressPolicy(
                trust="local",
                max_sensitivity="restricted",
                allow_project_knowledge=True,
                allow_executable_source=True,
                allow_row_samples=False,
                allow_aggregate_statistics=True,
            ),
        ),
        (
            "metadata_only",
            EgressPolicy(
                trust="local",
                max_sensitivity="internal",
                allow_project_knowledge=False,
                allow_executable_source=False,
                allow_row_samples=False,
                allow_aggregate_statistics=False,
            ),
        ),
    ],
)
def test_a_case_runs_under_its_egress_profile_at_the_invoking_trust(
    profile: str, expected: EgressPolicy
) -> None:
    invoking = _scripted_config(replace(_PERMISSIVE_EGRESS, trust="local"))

    assert self_test_config(invoking, profile).egress == expected  # type: ignore[arg-type]


async def test_scripted_provider_runs_real_disposable_mutation_flow(tmp_path: Path) -> None:
    """The case runs under its own egress profile, whatever the invoking project
    permits (here row samples, which the project profile withholds), and a
    transcript keeps what the model saw."""

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
    assert "- Highest sensitivity sent: `restricted`" in context["content"]
    assert (
        "- Saved node configuration: readable through `inspect_node`'s config part"
        in context["content"]
    )
    assert "- Project knowledge: permitted" in context["content"]
    assert "- Executable source: permitted" in context["content"]
    assert "- Column value profiles: not permitted" in context["content"]
    (entry,) = transcript
    assert entry["request"] == _load_case("smoke_categorical_banding").turns[0].request
    events = entry["events"]
    assert [event["tool"] for event in events if "tool" in event] == [
        "get_pipeline",
        "dry_run_graph_edits",
        "apply_graph_plan",
    ]
    # The project profile permits aggregate statistics, so the transcript keeps the
    # dry-run's data check as the model saw it: in thread mode, why it did not run.
    (dry_run,) = [event for event in events if event.get("tool") == "dry_run_graph_edits"]
    assert dry_run["result"]["data_check"]["reason"] == "worker_mode_unsupported"
    assert events[-1]["outcome"]["kind"] == "applied"


async def test_a_metadata_only_case_withholds_saved_configuration(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    case = _load_case("breakpoint_age_banding")
    provider = TrajectoryProvider(
        load_trajectory(TRAJECTORIES_ROOT / "breakpoint_age_banding.json")
    )

    result = await run_self_test_case(
        case,
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        work_dir=tmp_path_factory.mktemp("e"),
        provider_factory=lambda _config: provider,
    )

    assert result.reasons == ()
    assert result.egress == "metadata_only"
    assert provider.first_messages is not None
    context = provider.first_messages[-1]["content"]
    assert "- Highest sensitivity sent: `internal`" in context
    assert "- Saved node configuration: withheld" in context
    assert "- Project knowledge: not permitted" in context
    assert "- Executable source: not permitted" in context


async def test_one_apply_per_turn_ends_the_turn_at_its_first_saving_apply(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The variant ends the staged build after its first saved plan without
    another provider request, so the later stages are never saved. The case is
    marked inapplicable to the variant for that reason, so it is never run under
    it; with the mark cleared, the variant's mechanics show."""

    case = _load_case("smoke_staged_pricing_build")
    provider = TrajectoryProvider(
        load_trajectory(TRAJECTORIES_ROOT / "smoke_staged_pricing_build.json")
    )
    work_dir = tmp_path_factory.mktemp("v")

    with pytest.raises(ValueError, match="does not apply to the one_apply_per_turn variant"):
        await run_self_test_case(
            case,
            projects_root=PROJECTS_ROOT,
            config=_scripted_config(_PERMISSIVE_EGRESS),
            work_dir=work_dir,
            provider_factory=lambda _config: provider,
            variant="one_apply_per_turn",
        )
    result = await run_self_test_case(
        replace(case, inapplicable_variants=()),
        projects_root=PROJECTS_ROOT,
        config=_scripted_config(_PERMISSIVE_EGRESS),
        work_dir=work_dir,
        provider_factory=lambda _config: provider,
        variant="one_apply_per_turn",
    )

    (turn,) = result.turns
    assert (turn.telemetry.outcome, turn.telemetry.saved_changes) == ("applied", 1)
    # The plan, dry-run and apply rounds; the round after the apply never reaches the model.
    assert turn.telemetry.provider_round_trips == 3
    assert [diagnostic.name for diagnostic in turn.tool_diagnostics] == [
        "update_build_plan",
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
        allow_aggregate_statistics=False,
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


def test_canonical_tools_rebuilds_the_databricks_lane_with_the_canonical_projection() -> None:
    """The variant changes only the projection the Databricks adapter sends: the
    rebuilt provider keeps the configuration and the client it was built with."""

    config = replace(_scripted_config(_PERMISSIVE_EGRESS), provider="databricks")
    client = object()
    lane = DatabricksProvider(config, client=client)

    rebuilt = canonical_tools_provider(lane)

    assert isinstance(rebuilt, DatabricksProvider)
    assert (rebuilt.config, rebuilt.client) == (config, client)
    assert (lane.tool_projection, rebuilt.tool_projection) == ("compatible", "canonical")
    with pytest.raises(ValueError, match="measures the Databricks lane"):
        canonical_tools_provider(
            OpenAIProvider(_scripted_config(_PERMISSIVE_EGRESS), client=client)
        )


async def test_canonical_tools_refuses_another_provider_before_the_case_runs(
    tmp_path: Path,
) -> None:
    """The Anthropic and OpenAI lanes already receive the canonical projection, so
    a run under them would be a mislabelled multi_apply run."""

    def unexpected_provider(_config: AssistantConfig) -> TrajectoryProvider:
        raise AssertionError("no provider may be created for a refused variant")

    with pytest.raises(ValueError, match="measures the Databricks lane"):
        await run_self_test_case(
            _load_case("smoke_categorical_banding"),
            projects_root=PROJECTS_ROOT,
            config=_scripted_config(_PERMISSIVE_EGRESS),
            work_dir=tmp_path,
            provider_factory=unexpected_provider,
            variant="canonical_tools",
        )
    assert list(tmp_path.iterdir()) == []


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
    staged = _load_case("smoke_staged_pricing_build")
    path = write_report(
        tmp_path / "report.json", (passing, failing), _RUN, not_applicable=(staged,)
    )
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert payload["schema_version"] == 4
    case = payload["cases"][0]
    assert case["egress"] == "project"
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
            "not_applicable": 0,
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
        },
        # A case that does not apply to the run's variant neither passes nor fails.
        "multi_stage": {
            "cases": 0,
            "passed": 0,
            "not_applicable": 1,
            "metrics": dict.fromkeys(
                (
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
            ),
        },
    }
    assert payload["not_applicable"] == [
        {
            "id": "smoke_staged_pricing_build",
            "fixture_version": "1",
            "area": "multi_stage",
            "split": "development",
        }
    ]
    assert "smoke_staged_pricing_build" not in {case["id"] for case in payload["cases"]}
    assert "Join the sources" not in raw
    assert "arguments" not in raw
    assert "assistant_text" not in raw
    with pytest.raises(ValueError, match="either run or not applicable, not both: join_roles"):
        report_payload((passing,), _RUN, not_applicable=(replace(staged, id="join_roles"),))


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
    staged = _load_case("smoke_staged_pricing_build")
    before = (
        _result(_join_turn(tool_calls=2)),
        _result(_join_turn(change_cards=0), case_id="dropped"),
        _result(_join_turn(), case_id=staged.id, area=staged.area),
    )
    after = (
        _result(_join_turn(tool_calls=6, change_cards=0)),
        _result(_join_turn(), case_id="added", area="banding"),
    )
    write_report(tmp_path / "before.json", before, _RUN)
    write_report(
        tmp_path / "after.json",
        after,
        replace(_RUN, variant="one_apply_per_turn"),
        not_applicable=(staged,),
    )

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
    # The staged build passed before and does not apply to the later run's
    # variant: it is listed as not applicable, never as a flip or a dropped case.
    assert comparison["not_applicable"] == {
        "before": [],
        "after": ["smoke_staged_pricing_build"],
    }
    assert (comparison["only_before"], comparison["only_after"]) == (["dropped"], ["added"])
    joins = comparison["areas"]["joins"]
    assert joins["before"] == {"cases": 2, "passed": 1, "not_applicable": 0}
    assert joins["after"] == {"cases": 1, "passed": 0, "not_applicable": 0}
    assert joins["metrics"]["tool_calls"] == {"before": 2.0, "after": 6.0, "difference": 4.0}
    assert comparison["areas"]["banding"]["before"] is None
    assert comparison["areas"]["multi_stage"]["after"] == {
        "cases": 0,
        "passed": 0,
        "not_applicable": 1,
    }
    assert comparison["areas"]["multi_stage"]["metrics"]["tool_calls"]["difference"] is None


def test_list_prints_the_selected_cases_without_a_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["list", "--area", "multi_turn"]) == 0

    listed = json.loads(capsys.readouterr().out)["cases"]
    assert listed and all(case["area"] == "multi_turn" for case in listed)
    assert any(case["turns"] > 1 for case in listed)
    assert all(
        (case["egress"], case["inapplicable_variants"]) == ("project", []) for case in listed
    )


def test_record_refuses_a_variant_no_selected_case_applies_to(tmp_path: Path) -> None:
    """Nothing is resolved, run or written when every selected case is inapplicable."""

    with pytest.raises(ValueError, match="applies to the one_apply_per_turn variant"):
        main(
            [
                "record",
                "--case",
                "smoke_staged_pricing_build",
                "--variant",
                "one_apply_per_turn",
                "--config-root",
                str(tmp_path),
            ]
        )

    assert list(tmp_path.iterdir()) == []


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
