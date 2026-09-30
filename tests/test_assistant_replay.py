"""Tier 0 of the assistant evaluation: reference trajectories replayed through the real tools.

Every self-test case has a checked-in reference trajectory under
``tests/assistant_eval/trajectories/``; the step-corpus cases have one in the
taught ``[source, free_code]`` form and one as the corpus's structured
translation. Each replay runs the real loop, tools, dry-run, apply, parser and
Git mutation gate in a copy of the case's project under ``tmp_path``, then the
harness scores every layer, including execution of the case's golden nodes
against plain-Polars goldens. Replay proves the tools and contracts, not that a
model would choose the same calls.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest

from haute._sandbox import _get_project_root
from scripts.run_assistant_self_test import (
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
    case.id: case
    for case in load_self_test_cases(EVAL_ROOT / "self_test", projects_root=PROJECTS_ROOT)
}
TRAJECTORIES = load_trajectories(TRAJECTORIES_ROOT)
CORPUS_ITEMS = ("attach_regional_rates", "high_premium_quotes", "underwriting_decision")
#: Seconds one replay may take before it counts as hung.
CASE_TIMEOUT = 120


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
    (golden,) = case.expectations.execution
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


@pytest.mark.timeout(CASE_TIMEOUT)
@pytest.mark.parametrize("trajectory", TRAJECTORIES, ids=lambda trajectory: trajectory.id)
async def test_replay(trajectory, tmp_path: Path) -> None:
    """Each trajectory replays without divergence and passes every scoring layer,
    and leaves the sandbox project root where it found it."""

    root_before = _get_project_root()
    cwd_before = Path.cwd()

    result = await replay_self_test_case(
        CASES[trajectory.case], trajectory, projects_root=PROJECTS_ROOT, work_dir=tmp_path
    )

    assert result.reasons == ()
    assert (result.evidence, result.provider, result.model) == ("replay", "replay", trajectory.id)
    assert _get_project_root() == root_before
    assert Path.cwd() == cwd_before


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


_READ_TOOLS = frozenset(
    {"get_pipeline", "get_node_schema", "get_node_config", "get_dataset_schema", "list_datasets"}
)


def _first_dry_run(trajectory) -> tuple[list[str], dict[str, object] | None]:
    """The tools called before a trajectory's first primitive dry-run, and that dry-run."""

    before: list[str] = []
    for trajectory_round in trajectory.turns[0]:
        for call in trajectory_round.calls:
            if call.tool == "dry_run_graph_edits":
                return before, call.arguments
            before.append(call.tool)
    return before, None


def _single_node_trajectories() -> list:
    """Trajectories whose first primitive dry-run adds, updates or edits the steps of one node."""

    single = []
    for trajectory in TRAJECTORIES:
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
    step ids an `edit_steps` names included."""

    single = {trajectory.id: trajectory for trajectory in _single_node_trajectories()}
    assert {"smoke_polars_feature_transform", "smoke_step_edit"} <= set(single)
    for trajectory in single.values():
        before, _arguments = _first_dry_run(trajectory)
        assert not set(before) & _READ_TOOLS, (trajectory.id, before)


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
    assert "- `quotes` (Polars)" in context["content"]
    assert '"driver_age"' in context["content"]


async def test_a_golden_the_saved_node_does_not_reproduce_fails_the_execution_layer(
    tmp_path: Path,
) -> None:
    case = CASES["smoke_corpus_high_premium_quotes"]
    wrong = SelfTestGolden(
        node="high_premium_quotes",
        golden="df = pl.scan_parquet('data/quotes.parquet').filter(pl.col('premium') > 500)\n",
        order_free=False,
    )
    case = replace(case, expectations=replace(case.expectations, execution=(wrong,)))
    trajectory = next(item for item in TRAJECTORIES if item.id == case.id)

    result = await replay_self_test_case(
        case, trajectory, projects_root=PROJECTS_ROOT, work_dir=tmp_path
    )

    assert result.failed_layers == ("execution",)
    assert result.reasons[0].startswith(
        "execution: node high_premium_quotes does not match its golden: row counts differ"
    )


def test_trajectory_references_must_name_an_earlier_call(tmp_path: Path) -> None:
    payload = json.loads(
        (TRAJECTORIES_ROOT / "smoke_categorical_banding.json").read_text(encoding="utf-8")
    )
    rounds = payload["turns"][0]["rounds"]
    rounds[1], rounds[2] = rounds[2], rounds[1]
    path = tmp_path / "smoke_categorical_banding.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="references 'recipe.recipe_plan_hash'"):
        load_trajectory(path)


def test_the_fixture_projects_are_not_modified_by_replay() -> None:
    """Replays copy the fixtures; nothing under the checked-in projects is written."""

    # The one sidecar a fixture holds on purpose: its Transform authored as steps.
    stepped = PROJECTS_ROOT / "stepped_pricing" / "config" / "polars" / "risk_features.json"
    written = [
        path
        for path in source_files(PROJECTS_ROOT, suffix=None)
        if path.parent.name in {"polars", "banding", "rating_step"} and path != stepped
    ]
    assert written == []
    steps = json.loads(stepped.read_text(encoding="utf-8"))["steps"]
    assert [step["id"] for step in steps] == ["start", "logic"]
    assert not any(
        name.startswith(".git") for name in os.listdir(PROJECTS_ROOT / "ordinary_pricing")
    )
