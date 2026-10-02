"""Tier 0 of the assistant evaluation: reference trajectories replayed through the real tools.

Every evaluation case, of both splits, has a checked-in reference trajectory
under ``tests/assistant_eval/trajectories/`` with one recorded turn per case
turn; the step-corpus cases have one in the taught ``[source, free_code]`` form
and one as the corpus's structured translation. Each replay runs the real loop,
tools, dry-run, apply, parser and Git mutation gate in a copy of the case's
project under a short temporary directory and the case's egress profile, then
the harness scores every layer of every turn, including execution of each
turn's golden nodes against plain-Polars goldens. Replay proves the tools and
contracts, not that a model would choose the same calls.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from haute._sandbox import _get_project_root, bound_project_root
from haute.routes._helpers import parse_pipeline_to_graph
from scripts.run_assistant_self_test import (
    FIXTURE_MODEL_RUN_PLACEHOLDER,
    SelfTestGolden,
    TrajectoryDivergedError,
    TrajectoryProvider,
    load_self_test_cases,
    load_trajectories,
    load_trajectory,
    replay_config,
    replay_self_test_case,
    run_self_test_case,
)
from tests._source_files import source_files
from tests.assistant_eval._frames import frames_equal, run, synthetic_inputs

EVAL_ROOT = Path(__file__).parent / "assistant_eval"
PROJECTS_ROOT = EVAL_ROOT / "projects"
TRAJECTORIES_ROOT = EVAL_ROOT / "trajectories"
CORPUS_ROOT = Path(__file__).parent / "fixtures" / "polars_steps_corpus"
CASES = {
    case.id: case for case in load_self_test_cases(EVAL_ROOT / "cases", projects_root=PROJECTS_ROOT)
}
TRAJECTORIES = load_trajectories(TRAJECTORIES_ROOT)
CORPUS_ITEMS = ("attach_regional_rates", "high_premium_quotes", "underwriting_decision")
#: Seconds one replayed turn may take before the replay counts as hung.
TURN_TIMEOUT = 120


@pytest.fixture
def work_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A short empty directory: model and snapshot caches nest deep under a case's copy,
    and a parametrised test's own directory name would exceed Windows' path limit."""

    return tmp_path_factory.mktemp("r")


@pytest.fixture(autouse=True)
def _fresh_plan_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Copies of one fixture have identical content, so a repeated trajectory
    dry-runs to the hash of a plan an earlier replay in this process applied."""

    from haute.assistant import _tools
    from haute.assistant._ops import PlanStore

    monkeypatch.setattr(_tools, "_PLAN_STORE", PlanStore())


def _transform_steps(trajectory_id: str) -> list[dict[str, object]]:
    """The steps a corpus trajectory's dry-run writes to its Transform."""

    trajectory = next(item for item in TRAJECTORIES if item.id == trajectory_id)
    dry_run = trajectory.turns[0][0].calls[0]
    add_node = dry_run.arguments["ops"][0]
    assert add_node["op"] == "add_node" and add_node["node_type"] == "polars"
    return add_node["config"]["steps"]


def test_every_case_has_a_reference_trajectory() -> None:
    assert {trajectory.case for trajectory in TRAJECTORIES} == set(CASES)
    for item in CORPUS_ITEMS:
        case = f"smoke_corpus_{item}"
        assert {trajectory.id for trajectory in TRAJECTORIES if trajectory.case == case} == {
            case,
            f"{case}_structured",
        }


@pytest.mark.parametrize("item", CORPUS_ITEMS)
def test_corpus_trajectories_author_the_corpus_and_its_goldens(
    item: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structured trajectory writes the corpus translation, the taught one a
    free-code card, and the case golden is the corpus snippet over the project's
    files, which are the corpus's normal synthetic inputs."""

    corpus = {
        entry["id"]: entry
        for entry in json.loads((CORPUS_ROOT / "corpus.json").read_text(encoding="utf-8"))
    }
    translations = json.loads((CORPUS_ROOT / "translations.json").read_text(encoding="utf-8"))
    case = CASES[f"smoke_corpus_{item}"]

    assert _transform_steps(f"{case.id}_structured") == translations[item]["steps"]
    assert [step["kind"] for step in _transform_steps(case.id)] == ["source", "free_code"]
    (turn,) = case.turns
    (golden,) = turn.expectations.execution
    assert golden.node == item
    assert golden.golden.endswith(corpus[item]["code"] + "\n")

    inputs = synthetic_inputs(hard=False)
    monkeypatch.chdir(PROJECTS_ROOT / case.project_fixture)
    for name in corpus[item]["inputs"]:
        assert pl.read_parquet(f"data/{name}.parquet").equals(inputs[name].collect()), name
    equal, why = frames_equal(
        run(golden.golden, {}), run(corpus[item]["code"], inputs), order_free=golden.order_free
    )
    assert equal, why


@pytest.mark.parametrize(
    "trajectory",
    [
        pytest.param(
            trajectory,
            id=trajectory.id,
            marks=pytest.mark.timeout(TURN_TIMEOUT * len(trajectory.turns)),
        )
        for trajectory in TRAJECTORIES
    ],
)
async def test_replay(trajectory, work_dir: Path) -> None:
    """Each trajectory replays without divergence and passes every scoring layer of
    every turn, and leaves the sandbox project root where it found it."""

    root_before = _get_project_root()
    cwd_before = Path.cwd()

    result = await replay_self_test_case(
        CASES[trajectory.case], trajectory, projects_root=PROJECTS_ROOT, work_dir=work_dir
    )

    assert result.reasons == ()
    assert (result.evidence, result.provider, result.model) == ("replay", "replay", trajectory.id)
    assert _get_project_root() == root_before
    assert Path.cwd() == cwd_before


def _expect_null_traced_to_the_join(data: dict[str, Any]) -> None:
    """`total_incurred` first goes null at the left join, which matches 7 of 10 quotes."""

    assert data["column"]["first_null_node"] == "quote_claims"
    assert [
        (entry["node"], entry["nulls"], entry["input_nulls"]) for entry in data["column"]["lineage"]
    ] == [("claims", 0, None), ("quote_claims", 3, 0), ("loss_ratio", 3, 3)]
    join = next(node for node in data["nodes"] if node["node"] == "quote_claims")["join"]
    assert (join["base_rows"], join["matched_base_rows"]) == (10, 7)


def _expect_one_error_at_the_failing_step(data: dict[str, Any]) -> None:
    """rating_features fails in its free-code step; vehicle_bands stops on it, silently."""

    statuses = {node["node"]: node for node in data["nodes"]}
    assert [node["node"] for node in data["nodes"]] == [
        "quotes",
        "rating_features",
        "vehicle_bands",
    ]
    failed = statuses["rating_features"]
    assert (failed["status"], failed["error"]["step"]) == ("failed", {"id": "logic", "number": 2})
    assert failed["error"]["columns"] == ["vehicle_year"]
    assert statuses["vehicle_bands"] == {
        "node": "vehicle_bands",
        "status": "upstream_failed",
        "failed_node": "rating_features",
        "at_or_upstream": False,
    }
    assert [(finding["kind"], finding["node"]) for finding in data["findings"]] == [
        ("execution_failed", "rating_features")
    ]
    assert data["column"] is None


@pytest.mark.slow
@pytest.mark.timeout(TURN_TIMEOUT)
@pytest.mark.parametrize(
    ("trajectory_id", "expect"),
    [
        ("claims_null_diagnosis", _expect_null_traced_to_the_join),
        ("broken_bands_diagnosis", _expect_one_error_at_the_failing_step),
    ],
)
async def test_a_data_question_is_answered_from_one_check_in_the_preview_worker(
    trajectory_id: str,
    expect: Any,
    work_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In process mode, as the server runs, the trajectory's one `inspect_node`
    data call measures the saved lineage in the preview worker, and its result
    says what the trajectory's answer says. Thread-mode replay (``test_replay``)
    only reaches `worker_mode_unsupported`."""

    from haute.assistant._data_check import NODE_DATA_VIEW

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setenv("HAUTE_INTERACTIVE_WORKER_COUNT", "1")
    # This exercises the worker route, not native memory-cap availability.
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    trajectory = next(item for item in TRAJECTORIES if item.id == trajectory_id)
    provider = TrajectoryProvider(trajectory)
    transcript: list[dict[str, object]] = []

    result = await run_self_test_case(
        CASES[trajectory.case],
        projects_root=PROJECTS_ROOT,
        config=replay_config(trajectory),
        work_dir=work_dir,
        provider_factory=lambda _config: provider,
        evidence="replay",
        transcript=transcript,
    )
    provider.verify(result.tool_diagnostics)

    assert result.reasons == ()
    [entry] = transcript
    events = cast(list[dict[str, Any]], entry["events"])
    [call] = [event for event in events if "tool" in event]
    assert call["tool"] == "inspect_node"
    data = call["result"]["data"]
    NODE_DATA_VIEW.validate_python(data)
    assert data["outcome"] == "checked", data
    expect(data)


#: Each seeded recovery case's bug, as the data check reports it: (kind, node).
SEEDED_BUGS = {
    "recovery_boolean_banding": ("banding_all_default", "claims_band"),
    "recovery_emptied_filter": ("rows_emptied", "north_south_quotes"),
    "recovery_mistyped_join_key": ("join_unmatched", "vehicle_rated_quotes"),
    "recovery_rating_casing": ("rating_misses", "region_rating"),
    "recovery_duplicate_join_keys": ("join_validation_failed", "loaded_quotes"),
}


#: The one ordinary case whose stated value leaves an advisory finding: the corpus
#: filter keeps quotes above 1500, and no quote in the corpus inputs is.
STATED_EMPTY_FILTER = ("rows_emptied", "high_premium_quotes")


def test_the_cases_declare_their_seeded_bugs_and_stated_value_findings() -> None:
    """The seeded recovery cases each declare their bug; the corpus filter whose
    stated threshold keeps no quote declares that finding kept, as do the rating
    keys the analyst stated. Every other case expects a clean saved graph."""

    declared = {
        case.id: turn.expectations.data_findings
        for case in CASES.values()
        for turn in case.turns
        if turn.expectations.data_findings is not None
    }

    assert {case_id: findings.reported for case_id, findings in declared.items()} == {
        **{case_id: (bug,) for case_id, bug in SEEDED_BUGS.items()},
        "smoke_corpus_high_premium_quotes": (STATED_EMPTY_FILTER,),
    }
    assert {case_id: findings.kept for case_id, findings in declared.items() if findings.kept} == {
        "recovery_rating_casing": (SEEDED_BUGS["recovery_rating_casing"],),
        "smoke_corpus_high_premium_quotes": (STATED_EMPTY_FILTER,),
    }
    assert set(SEEDED_BUGS) <= {case.id for case in CASES.values() if case.area == "recovery"}


async def test_thread_mode_replay_leaves_the_data_findings_layer_unmeasured(
    work_dir: Path,
) -> None:
    """In thread mode every check reports `worker_mode_unsupported`, the model's and
    the harness's alike, so a declared layer is not measured and fails nothing."""

    trajectory = next(item for item in TRAJECTORIES if item.id == "recovery_rating_casing")

    result = await replay_self_test_case(
        CASES[trajectory.case], trajectory, projects_root=PROJECTS_ROOT, work_dir=work_dir
    )

    assert result.reasons == ()
    (turn,) = result.turns
    findings = turn.data_findings
    assert (findings.status, findings.gates, findings.recovery) == ("not_measured", True, None)
    assert [(check.outcome, check.reason) for check in findings.received] == [
        ("not_run", "worker_mode_unsupported")
    ]
    assert findings.final is not None
    assert findings.final.nodes == (("region_rating", "not_run", "worker_mode_unsupported"),)
    assert result.data_findings == "not_measured"


@pytest.mark.slow
@pytest.mark.timeout(TURN_TIMEOUT)
@pytest.mark.parametrize("trajectory_id", sorted(SEEDED_BUGS))
async def test_each_seeded_bug_is_reported_and_recovered_in_the_preview_worker(
    trajectory_id: str,
    work_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In process mode, as the server runs, the first dry-run's data check reports
    the seeded bug, and the harness's check of the saved graph finds it gone, or
    kept and shown on the change card where the analyst stated the value."""

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setenv("HAUTE_INTERACTIVE_WORKER_COUNT", "1")
    # This exercises the worker route, not native memory-cap availability.
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    trajectory = next(item for item in TRAJECTORIES if item.id == trajectory_id)
    provider = TrajectoryProvider(trajectory)
    case = CASES[trajectory.case]
    (expected,) = case.turns
    assert expected.expectations.data_findings is not None
    kept = expected.expectations.data_findings.kept

    result = await run_self_test_case(
        case,
        projects_root=PROJECTS_ROOT,
        config=replay_config(trajectory),
        work_dir=work_dir,
        provider_factory=lambda _config: provider,
        evidence="replay",
    )
    provider.verify(result.tool_diagnostics)

    assert result.reasons == ()
    (turn,) = result.turns
    findings = turn.data_findings
    first, last = findings.received[0], findings.received[-1]
    assert (first.tool, first.outcome) == ("dry_run_graph_edits", "checked")
    assert SEEDED_BUGS[trajectory_id] in first.advisory
    assert last.advisory == kept
    assert findings.final is not None and findings.final.measured
    assert findings.final.advisory == kept
    assert [card.advisory_nodes for card in findings.cards] == [tuple(node for _kind, node in kept)]
    assert (findings.status, findings.gates, findings.recovery) == ("passed", True, "recovered")
    assert (result.data_findings, result.recovery) == ("passed", "recovered")


async def test_a_tool_result_with_another_status_names_the_turn_and_round(
    tmp_path: Path,
) -> None:
    """A trajectory recording a success where the tools now refuse diverges loudly."""

    source = TRAJECTORIES_ROOT / "smoke_polars_feature_transform.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    refused = payload["turns"][0]["rounds"][0]["calls"][0]
    assert refused["result"] == {"status": "error", "error_code": "invalid_ops"}
    refused["result"] = {"status": "ok"}
    edited = tmp_path / "smoke_polars_feature_transform.json"
    edited.write_text(json.dumps(payload), encoding="utf-8")
    work_dir = tmp_path / "w"
    work_dir.mkdir()

    with pytest.raises(
        TrajectoryDivergedError,
        match=(
            r"smoke_polars_feature_transform diverged at turn 1 round 1: "
            r"dry_run_graph_edits call code_attempt returned \('error', 'invalid_ops'\)"
        ),
    ):
        await replay_self_test_case(
            CASES["smoke_polars_feature_transform"],
            load_trajectory(edited),
            projects_root=PROJECTS_ROOT,
            work_dir=work_dir,
        )


_READ_TOOLS = frozenset({"get_pipeline", "inspect_node", "find_data"})


def _first_dry_run(trajectory) -> tuple[list, dict[str, object] | None]:
    """The calls made before a trajectory's first dry-run, and that dry-run's arguments."""

    before: list = []
    for trajectory_round in trajectory.turns[0]:
        for call in trajectory_round.calls:
            if call.tool == "dry_run_graph_edits":
                return before, call.arguments
            before.append(call)
    return before, None


def _saved_configs(fixture: str) -> dict[str, dict[str, Any]]:
    """Each node's configuration as a case's project starts, read without writing."""

    root = PROJECTS_ROOT / fixture
    with bound_project_root(root):
        graph = parse_pipeline_to_graph(root / "pipeline.py")
    return {node.id: dict(node.data.config) for node in graph.nodes}


def _rewritten_nodes(ops: Sequence[Mapping[str, Any]], saved: Mapping[str, Any]) -> set[str]:
    """The nodes whose saved non-empty list or map an `update_node` of *ops* replaces.

    `update_node` replaces each key it writes whole, so restating such a key
    needs the saved entries; a node the same plan adds holds nothing saved.
    """

    added = {op["name"] for op in ops if op["op"] == "add_node"}
    return {
        op["node"]
        for op in ops
        if op["op"] == "update_node" and op["node"] not in added
        for key in op["config"]
        if isinstance(value := saved.get(op["node"], {}).get(key), (list, dict)) and value
    }


def _is_config_read(call, node: str) -> bool:
    return call.tool == "inspect_node" and call.arguments == {"node": node, "parts": ["config"]}


def _single_node_trajectories() -> list:
    """Trajectories whose first dry-run adds, updates or edits the steps of one node.

    A recovery case is not among them: diagnosing a failing node reads it first.
    """

    single = []
    for trajectory in TRAJECTORIES:
        if CASES[trajectory.case].area == "recovery":
            continue
        _before, arguments = _first_dry_run(trajectory)
        if arguments is None:
            continue
        written = [
            op for op in arguments["ops"] if op["op"] in {"add_node", "update_node", "edit_steps"}
        ]
        if len(written) == 1:
            single.append(trajectory)
    return single


def test_single_node_edits_dry_run_without_an_orientation_read() -> None:
    """The turn context's graph brief carries what a one-node edit needs, the
    step ids an `edit_steps` names included; the one read such an edit makes is
    the config of a node whose saved list or map it restates."""

    single = {trajectory.id: trajectory for trajectory in _single_node_trajectories()}
    assert {
        "smoke_polars_feature_transform",
        "smoke_step_edit",
        "motor_new_driver_flag",
        "submodel_high_risk_flag",
        "motor_value_band_factor",
    } <= set(single)
    for trajectory in single.values():
        before, arguments = _first_dry_run(trajectory)
        assert arguments is not None
        rewritten = _rewritten_nodes(
            arguments["ops"], _saved_configs(CASES[trajectory.case].project_fixture)
        )
        orientation = [
            call.tool
            for call in before
            if call.tool in _READ_TOOLS
            and not any(_is_config_read(call, node) for node in rewritten)
        ]
        assert orientation == [], trajectory.id


def test_a_rewrite_of_a_saved_list_or_map_reads_the_node_config_first() -> None:
    """A model restating a saved list or map must first read it: each turn's
    `update_node` of one follows that turn's config read of the node (earlier
    turns' tool results are compacted out of the history), unless the case's
    egress profile withholds saved configuration and the dry-run refuses the
    blind rewrite as `config_withheld`."""

    rewriting: set[str] = set()
    for trajectory in TRAJECTORIES:
        case = CASES[trajectory.case]
        saved = _saved_configs(case.project_fixture)
        for rounds in trajectory.turns:
            read: set[str] = set()
            for trajectory_round in rounds:
                for call in trajectory_round.calls:
                    if call.tool == "inspect_node" and "config" in call.arguments.get("parts", ()):
                        read.add(call.arguments["node"])
                    if call.tool != "dry_run_graph_edits":
                        continue
                    ops = call.arguments["ops"]
                    for node in _rewritten_nodes(ops, saved):
                        rewriting.add(trajectory.id)
                        if call.error_code == "config_withheld":
                            assert case.egress == "metadata_only", trajectory.id
                        else:
                            assert node in read, (trajectory.id, node)
                    if call.status == "ok":
                        for op in ops:
                            if op["op"] == "update_node":
                                saved.setdefault(op["node"], {}).update(op["config"])

    assert rewriting == {
        "motor_inception_year_rating",
        "motor_licence_band_refine",
        "motor_region_regroup",
        "motor_renewal_scenario",
        "motor_response_relativity",
        "motor_ticket_injection",
        "motor_value_band_factor",
        "motor_value_band_factor_withheld",
        "motor_value_bands_delegated",
    }


async def test_the_first_request_carries_the_columns_the_first_dry_run_reads(
    tmp_path: Path,
) -> None:
    """The feature transform reads `driver_age` from `quotes` without a read call,
    because the first provider request's turn context lists both."""

    trajectory = next(item for item in TRAJECTORIES if item.id == "smoke_polars_feature_transform")
    provider = TrajectoryProvider(trajectory)
    result = await run_self_test_case(
        CASES[trajectory.case],
        projects_root=PROJECTS_ROOT,
        config=replay_config(trajectory),
        work_dir=tmp_path,
        provider_factory=lambda _config: provider,
        evidence="replay",
    )
    provider.verify(result.tool_diagnostics)

    assert result.reasons == ()
    assert provider.first_messages is not None
    user, context = provider.first_messages
    assert (user["role"], context["role"]) == ("user", "context")
    assert "- `quotes` (Transform)" in context["content"]
    assert '"driver_age"' in context["content"]


class _RecordingProvider(TrajectoryProvider):
    """A trajectory provider that keeps the messages of every provider request."""

    def __init__(self, trajectory) -> None:
        super().__init__(trajectory)
        self.requests: list[tuple[dict[str, object], ...]] = []

    async def stream_turn(self, *, system, messages, tools):
        self.requests.append(tuple(dict(message) for message in messages))
        async for event in super().stream_turn(system=system, messages=messages, tools=tools):
            yield event


async def test_a_staged_build_saves_each_stage_in_one_turn_with_a_card_each(
    tmp_path: Path,
) -> None:
    """The source with its features, the banding, the rating and the response are
    four plans saved in one turn, each with its change card; each later stage
    reads the columns the earlier ones produced from the turn context update,
    never from a read call."""

    trajectory = next(item for item in TRAJECTORIES if item.id == "smoke_staged_pricing_build")
    provider = _RecordingProvider(trajectory)
    result = await run_self_test_case(
        CASES[trajectory.case],
        projects_root=PROJECTS_ROOT,
        config=replay_config(trajectory),
        work_dir=tmp_path,
        provider_factory=lambda _config: provider,
        evidence="replay",
    )
    provider.verify(result.tool_diagnostics)

    assert result.reasons == ()
    (turn,) = result.turns
    assert (turn.telemetry.saved_changes, turn.telemetry.change_cards) == (4, 4)
    calls = [
        call.tool for trajectory_round in trajectory.turns[0] for call in trajectory_round.calls
    ]
    assert not set(calls) & _READ_TOOLS
    # The request that plans the banding follows the first apply's round.
    banding = next(
        messages
        for messages in provider.requests
        if any(message.get("name") == "apply_graph_plan" for message in messages)
    )
    update = banding[-1]
    assert update["role"] == "context"
    assert str(update["content"]).startswith("## Turn context update\n")
    assert "- `quote_features` (" in str(update["content"])
    assert '"vehicle_value_k"' in str(update["content"])
    assert '"region"' in str(update["content"])
    assert banding[-2]["name"] == "apply_graph_plan"
    updates = [
        message
        for message in provider.requests[-1]
        if message["role"] == "context"
        and str(message["content"]).startswith("## Turn context update")
    ]
    assert len(updates) == 4


STAGED = "smoke_staged_pricing_build"
_STAGES = ("source", "banding", "rating", "response")


def _staged_payload() -> dict[str, Any]:
    return json.loads((TRAJECTORIES_ROOT / f"{STAGED}.json").read_text(encoding="utf-8"))


def _round_index(rounds: list[dict[str, Any]], call_id: str) -> int:
    return next(
        index
        for index, trajectory_round in enumerate(rounds)
        if any(call["id"] == call_id for call in trajectory_round["calls"])
    )


def _written(tmp_path: Path, payload: dict[str, Any]):
    """Load *payload* as a trajectory file, named after its id as loading requires."""

    folder = tmp_path / "t"
    folder.mkdir()
    path = folder / f"{payload['id']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_trajectory(path)


def _plan_states(events: Sequence[Mapping[str, Any]]) -> list[dict[str, tuple[bool, int]]]:
    """Each build-plan update of a transcript turn: per item, complete and its change count."""

    return [
        {
            item["id"]: (item["complete"], len(item["changes"]))
            for item in event["build_plan"]["items"]
        }
        for event in events
        if "build_plan" in event
    ]


def _applied_change_ids(events: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        event["result"]["change"]["id"]
        for event in events
        if event.get("tool") == "apply_graph_plan" and "change" in event["result"]
    ]


async def _replayed(case, trajectory, work_dir: Path, transcript: list[dict[str, object]]):
    provider = _RecordingProvider(trajectory)
    result = await run_self_test_case(
        case,
        projects_root=PROJECTS_ROOT,
        config=replay_config(trajectory),
        work_dir=work_dir,
        provider_factory=lambda _config: provider,
        evidence="replay",
        transcript=transcript,
    )
    provider.verify(result.tool_diagnostics)
    return result, provider


async def test_a_staged_build_records_each_stage_and_completes_it_only_when_claimed(
    tmp_path: Path,
) -> None:
    """The plan's four items are set first; each apply records its change against
    its item, which stays open until the model claims it, and the plan ends with
    every item complete holding the change its apply saved."""

    trajectory = next(item for item in TRAJECTORIES if item.id == STAGED)
    transcript: list[dict[str, object]] = []

    result, _provider = await _replayed(CASES[STAGED], trajectory, tmp_path, transcript)

    assert result.reasons == ()
    (turn,) = transcript
    events = turn["events"]
    states = _plan_states(events)
    assert states[0] == dict.fromkeys(_STAGES, (False, 0))
    expected = dict.fromkeys(_STAGES, (False, 0))
    for index, stage in enumerate(_STAGES):
        expected = {**expected, stage: (False, 1)}
        assert states[1 + 2 * index] == expected, stage
        expected = {**expected, stage: (True, 1)}
        assert states[2 + 2 * index] == expected, stage
    assert len(states) == 1 + 2 * len(_STAGES)
    final = [event["build_plan"] for event in events if "build_plan" in event][-1]
    assert [
        change["id"] for item in final["items"] for change in item["changes"]
    ] == _applied_change_ids(events)


async def test_an_apply_that_saves_part_of_a_stage_leaves_it_open_with_its_change(
    tmp_path: Path, work_dir: Path
) -> None:
    """The rating stage saved in two applies, the table and then its combined output:
    after the first the item is open with that change listed, after the second open
    with both, and complete only once the model claims it."""

    payload = _staged_payload()
    rounds = payload["turns"][0]["rounds"]
    rating_dry = rounds[_round_index(rounds, "rating_dry")]["calls"][-1]
    recipe = rating_dry["arguments"]["ops"][0]["arguments"]
    combined = recipe.pop("combined_outputs")
    rating_dry["arguments"]["summary"] = "Rate each quote by its region group."
    (output,) = combined
    part = [
        {
            "text": "",
            "calls": [
                {
                    "id": "combined_dry",
                    "tool": "dry_run_graph_edits",
                    "arguments": {
                        "summary": "Combine the region factor into a technical premium.",
                        "ops": [
                            {
                                "op": "update_node",
                                "node": "region_rating",
                                "config": {
                                    "combinedOutputs": [
                                        {
                                            "outputColumn": output["output_column"],
                                            "operation": output["operation"],
                                            "baseValue": float(output["base_value"]),
                                        }
                                    ]
                                },
                            }
                        ],
                    },
                    "result": {"status": "ok"},
                }
            ],
        },
        {
            "text": "",
            "calls": [
                {
                    "id": "combined_apply",
                    "tool": "apply_graph_plan",
                    "arguments": {
                        "plan_hash": {"$result": "combined_dry.plan_hash"},
                        "item": "rating",
                    },
                    "result": {"status": "ok"},
                }
            ],
        },
    ]
    after_apply = _round_index(rounds, "rating_apply") + 1
    rounds[after_apply:after_apply] = part
    transcript: list[dict[str, object]] = []

    result, _provider = await _replayed(
        CASES[STAGED], _written(tmp_path, payload), work_dir, transcript
    )

    assert result.reasons == ()
    rating = [state["rating"] for state in _plan_states(transcript[0]["events"])]
    assert rating == [
        (False, 0),
        (False, 0),
        (False, 0),
        (False, 0),
        (False, 0),
        (False, 1),
        (False, 2),
        (True, 2),
        (True, 2),
        (True, 2),
    ]


async def test_an_interrupted_build_resumes_its_open_items_on_continue(
    tmp_path: Path, work_dir: Path
) -> None:
    """A first turn that ends after two stages leaves the rating and response
    items open; on "Continue" the next turn's context lists them open and its
    history's record of the first turn names them, and the turn finishes them
    against the first turn's item ids without setting the plan again."""

    case = CASES[STAGED]
    (turn,) = case.turns
    expected = turn.expectations
    goldens = {golden.node: golden for golden in expected.execution}
    first = replace(
        turn,
        expectations=replace(
            expected,
            required_node_types=("dataInput", "polars", "banding"),
            required_edges=expected.required_edges[:2],
            node_configs={key: expected.node_configs[key] for key in ("nb_batch", "region_band")},
            execution=(goldens["quote_features"],),
        ),
    )
    second = replace(
        turn,
        request="Continue",
        expectations=replace(
            expected,
            node_configs={
                key: expected.node_configs[key] for key in ("region_rating", "quote_response")
            },
            execution=(goldens["region_rating"],),
        ),
    )
    payload = _staged_payload()
    rounds = payload["turns"][0]["rounds"]
    split = _round_index(rounds, "rating_dry")
    banding_done, rating_dry = rounds[split]["calls"]
    payload["turns"] = [
        {
            "rounds": [
                *rounds[:split],
                {"text": "", "calls": [banding_done]},
                {"text": "Saved the source and the banding; I stopped there.", "calls": []},
            ]
        },
        {"rounds": [{"text": "", "calls": [rating_dry]}, *rounds[split + 1 :]]},
    ]
    transcript: list[dict[str, object]] = []

    result, provider = await _replayed(
        replace(case, turns=(first, second)),
        _written(tmp_path, payload),
        work_dir,
        transcript,
    )

    assert result.reasons == ()
    first_turn, second_turn = transcript
    assert _plan_states(first_turn["events"])[-1] == {
        "source": (True, 1),
        "banding": (True, 1),
        "rating": (False, 0),
        "response": (False, 0),
    }
    resumed = next(
        messages
        for messages in provider.requests
        if sum(message["role"] == "user" for message in messages) == 2
    )
    record = str(resumed[1]["content"])
    assert resumed[1]["role"] == "assistant"
    assert (
        "- Build plan as this turn left it: 2 of 4 items complete; open: `rating` "
        '"Region rating", `response` "Quote response"'
    ) in record
    context = str(resumed[-1]["content"])
    assert resumed[-1]["role"] == "context"
    assert '- `source` "Data input and features": complete, 1 saved change' in context
    assert '- `rating` "Region rating": open, no saved change' in context
    assert '- `response` "Quote response": open, no saved change' in context
    assert not any(
        event.get("tool") == "update_build_plan" and "items" in event["arguments"]
        for event in second_turn["events"]
    )
    assert _plan_states(second_turn["events"])[-1] == dict.fromkeys(_STAGES, (True, 1))


async def test_a_golden_the_saved_node_does_not_reproduce_fails_the_execution_layer(
    tmp_path: Path,
) -> None:
    case = CASES["smoke_corpus_high_premium_quotes"]
    wrong = SelfTestGolden(
        node="high_premium_quotes",
        scenario="live",
        golden="df = pl.scan_parquet('data/quotes.parquet').filter(pl.col('premium') > 500)\n",
        order_free=False,
    )
    (turn,) = case.turns
    case = replace(
        case, turns=(replace(turn, expectations=replace(turn.expectations, execution=(wrong,))),)
    )
    trajectory = next(item for item in TRAJECTORIES if item.id == case.id)

    result = await replay_self_test_case(
        case, trajectory, projects_root=PROJECTS_ROOT, work_dir=tmp_path
    )

    assert result.failed_layers == ("execution",)
    assert result.first_failing_layer == "execution"
    assert result.reasons[0].startswith(
        "turn 1 execution: node high_premium_quotes does not match its golden: row counts differ"
    )


async def test_a_saved_pipeline_that_raises_under_a_golden_fails_the_execution_layer(
    tmp_path: Path, work_dir: Path
) -> None:
    """The last live run's crash: the turn ends BLOCKED with nothing saved, so the
    switch maps no input to the golden's renewal_batch scenario and running the
    saved pipeline raises. That is the turn's execution failure, with the error's
    class and message, never an exception out of the case."""

    payload = {
        "schema_version": 1,
        "id": "motor_renewal_scenario",
        "case": "motor_renewal_scenario",
        "turns": [
            {"rounds": [{"text": "BLOCKED: the renewal batch cannot be routed.", "calls": []}]}
        ],
    }

    result = await replay_self_test_case(
        CASES["motor_renewal_scenario"],
        _written(tmp_path, payload),
        projects_root=PROJECTS_ROOT,
        work_dir=work_dir,
    )

    assert "execution" in result.failed_layers
    (execution,) = [reason for reason in result.reasons if reason.startswith("turn 1 execution:")]
    assert execution.startswith(
        "turn 1 execution: node rating_features raised LiveSwitchScenarioError: "
    )
    assert "renewal_batch" in execution


@pytest.mark.parametrize(
    ("edit", "reasons"),
    [
        pytest.param(
            "df = df.select(list(reversed(df.collect_schema().names())))\n", (), id="reordered"
        ),
        pytest.param(
            "df = df.drop(df.collect_schema().names()[0])\n",
            (
                "turn 1 execution: node high_premium_quotes columns or dtypes differ "
                "from its golden",
            ),
            id="missing",
        ),
    ],
)
async def test_a_golden_matches_the_saved_node_by_column_name_in_any_order(
    edit: str, reasons: tuple[str, ...], work_dir: Path
) -> None:
    """The executed frame is compared with its golden column by column name, so a
    golden listing the same columns in another order matches, and one that lacks
    a column does not."""

    case = CASES["smoke_corpus_high_premium_quotes"]
    (turn,) = case.turns
    (golden,) = turn.expectations.execution
    edited = replace(golden, golden=golden.golden + edit)
    case = replace(
        case, turns=(replace(turn, expectations=replace(turn.expectations, execution=(edited,))),)
    )
    trajectory = next(item for item in TRAJECTORIES if item.id == case.id)

    result = await replay_self_test_case(
        case, trajectory, projects_root=PROJECTS_ROOT, work_dir=work_dir
    )

    assert result.reasons == reasons


def test_trajectory_references_must_name_an_earlier_call(tmp_path: Path) -> None:
    payload = json.loads(
        (TRAJECTORIES_ROOT / "smoke_categorical_banding.json").read_text(encoding="utf-8")
    )
    rounds = payload["turns"][0]["rounds"]
    rounds[1], rounds[2] = rounds[2], rounds[1]
    path = tmp_path / "smoke_categorical_banding.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="references 'dry.plan_hash'"):
        load_trajectory(path)


def test_the_fixture_projects_are_not_modified_by_replay() -> None:
    """Replays prepare and edit copies: no Git repository, snapshot cache, MLflow run or
    model cache appears under the checked-in projects, and the fixture model's node keeps
    its placeholder run id."""

    runtime = {".git", ".haute_cache", "mlruns", ".cache"}
    created = [
        path
        for path in source_files(PROJECTS_ROOT, suffix=None)
        if runtime & set(path.relative_to(PROJECTS_ROOT).parts)
    ]
    assert created == []
    sidecar = PROJECTS_ROOT / "motor_pricing" / "config" / "model_scoring" / "claim_frequency.json"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["run_id"] == (
        FIXTURE_MODEL_RUN_PLACEHOLDER
    )
