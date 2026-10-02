"""The session's build plan on its own: items, claims, recorded changes and undo."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from haute.assistant._build_plan import BuildPlan, BuildPlanError, build_plan_view
from haute.schemas import AssistantBuildPlanItem

STAGES = [
    {"id": "source", "title": "Data input and features"},
    {"id": "banding", "title": "Region banding"},
]


def _state(plan: BuildPlan) -> list[tuple[str, bool, list[tuple[str, bool]]]]:
    assert plan.current is not None
    return [
        (item.id, item.complete, [(change.id, change.undone) for change in item.changes])
        for item in plan.current.items
    ]


def test_items_start_a_plan_whose_items_are_open_with_no_change() -> None:
    plan = BuildPlan()

    plan.update(items=STAGES, complete=None)

    assert _state(plan) == [("source", False, []), ("banding", False, [])]


def test_a_claim_needs_a_saved_change_and_a_refusal_changes_nothing() -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)
    before = plan.current

    with pytest.raises(BuildPlanError) as refused:
        plan.update(items=None, complete="source")

    assert refused.value.code == "plan_item_unsaved"
    assert refused.value.where == {"field": "complete", "item": "source"}
    assert "apply_graph_plan" in refused.value.fix
    assert plan.current is before

    plan.record_change("source", "c1")
    assert _state(plan)[0] == ("source", False, [("c1", False)])
    plan.update(items=None, complete="source")
    assert _state(plan)[0] == ("source", True, [("c1", False)])


def test_items_and_a_failing_claim_in_one_call_apply_together_or_not_at_all() -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)
    before = plan.current

    with pytest.raises(BuildPlanError):
        plan.update(items=[*STAGES, {"id": "rating", "title": "Rating"}], complete="rating")

    assert plan.current is before


def test_a_revision_of_an_unfinished_plan_carries_each_item_by_id() -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)
    plan.record_change("source", "c1")
    plan.update(items=None, complete="source")
    plan.record_change("banding", "c2")

    plan.update(
        items=[
            {"id": "source", "title": "Source"},
            {"id": "rating", "title": "Region rating"},
        ],
        complete=None,
    )

    assert plan.current is not None
    assert [item.title for item in plan.current.items] == ["Source", "Region rating"]
    assert _state(plan) == [("source", True, [("c1", False)]), ("rating", False, [])]


def test_items_after_a_finished_plan_start_a_new_plan() -> None:
    """A later build that reuses an id never inherits a finished item."""

    plan = BuildPlan()
    plan.update(items=[{"id": "source", "title": "Source"}], complete=None)
    plan.record_change("source", "c1")
    plan.update(items=None, complete="source")

    plan.update(items=[{"id": "source", "title": "Second source"}], complete=None)

    assert _state(plan) == [("source", False, [])]


@pytest.mark.parametrize(
    ("items", "complete", "code", "where"),
    [
        pytest.param(None, None, "empty_plan_update", None, id="empty"),
        pytest.param(
            [*STAGES, {"id": "source", "title": "Again"}],
            None,
            "duplicate_plan_item",
            {"field": "items[2].id"},
            id="duplicate",
        ),
        pytest.param(None, "sorce", "unknown_plan_item", {"field": "complete"}, id="unknown"),
    ],
)
def test_refused_updates_name_their_code_and_leave_the_plan(
    items: list[dict[str, str]] | None,
    complete: str | None,
    code: str,
    where: dict[str, object] | None,
) -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)
    before = plan.current

    with pytest.raises(BuildPlanError) as refused:
        plan.update(items=items, complete=complete)

    assert (refused.value.code, refused.value.where) == (code, where)
    assert refused.value.fix
    assert plan.current is before


def test_an_unknown_item_names_the_plan_ids_and_the_close_one() -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)

    with pytest.raises(BuildPlanError) as refused:
        plan.require_item("sorce")

    assert refused.value.code == "unknown_plan_item"
    assert refused.value.where == {"field": "item"}
    assert refused.value.fields == {"valid_ids": ["source", "banding"], "did_you_mean": ["source"]}


def test_an_item_without_a_plan_says_to_set_the_items_first() -> None:
    with pytest.raises(BuildPlanError) as refused:
        BuildPlan().require_item("source")

    assert refused.value.code == "unknown_plan_item"
    assert "update_build_plan" in refused.value.fix


def test_a_call_that_changes_nothing_keeps_the_same_snapshot() -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)
    plan.record_change("source", "c1")
    plan.update(items=None, complete="source")
    complete = plan.current

    plan.update(items=None, complete="source")
    plan.update(items=STAGES, complete=None)
    plan.undo("not-listed")

    assert plan.current is complete


def test_an_undo_marks_the_change_and_reopens_an_item_left_without_a_live_one() -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)
    plan.record_change("source", "c1")
    plan.update(items=None, complete="source")
    plan.record_change("banding", "c2")
    plan.record_change("banding", "c3")
    plan.update(items=None, complete="banding")

    plan.undo("c1")
    plan.undo("c3")

    assert _state(plan) == [
        ("source", False, [("c1", True)]),
        ("banding", True, [("c2", False), ("c3", True)]),
    ]


def test_the_same_change_saved_again_after_an_undo_is_live_again() -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)
    plan.record_change("source", "c1")
    plan.undo("c1")

    plan.record_change("source", "c1")

    assert _state(plan)[0] == ("source", False, [("c1", False)])


def test_a_complete_item_needs_a_live_change() -> None:
    with pytest.raises(ValidationError, match="complete without a saved change"):
        AssistantBuildPlanItem.model_validate(
            {"id": "source", "title": "Source", "complete": True, "changes": []}
        )


def test_the_view_counts_live_and_undone_changes() -> None:
    plan = BuildPlan()
    plan.update(items=STAGES, complete=None)
    plan.record_change("source", "c1")
    plan.record_change("source", "c2")
    plan.undo("c1")
    assert plan.current is not None

    assert build_plan_view(plan.current) == [
        {
            "id": "source",
            "title": "Data input and features",
            "complete": False,
            "changes": 1,
            "undone": 1,
        },
        {"id": "banding", "title": "Region banding", "complete": False, "changes": 0},
    ]
