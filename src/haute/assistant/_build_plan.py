"""The session's build plan: the stages a multi-stage request is built in.

The plan is the chat's checklist. Each item carries two separate facts: the
changes saved against it, which Haute records when an apply naming the item
commits, and whether it is complete, which only the model can claim and which
is accepted only while the item has a saved change that is not undone.

Every change replaces the plan's snapshot whole, so a caller sees a change by
identity, and a call that changes nothing keeps the same snapshot. The plan
reads nothing from the project.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence

from haute.schemas import AssistantBuildPlan, AssistantBuildPlanChange, AssistantBuildPlanItem


class BuildPlanError(Exception):
    """A refused build-plan update or item reference, returned to the model as a tool error.

    `where` locates the refused field (and item), `fix` is one concrete
    correction, and `fields` are extra error fields such as `valid_ids`.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        fix: str,
        where: Mapping[str, object] | None = None,
        **fields: object,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.where = None if where is None else dict(where)
        self.fields = fields


def _live_changes(item: AssistantBuildPlanItem) -> int:
    """How many changes recorded against *item* the analyst has not undone."""

    return sum(not change.undone for change in item.changes)


def _item(
    plan: AssistantBuildPlan | None,
    item_id: str,
    *,
    field: str,
) -> AssistantBuildPlanItem:
    """The plan's item *item_id*, or the `unknown_plan_item` refusal at *field*."""

    if plan is None:
        raise BuildPlanError(
            "unknown_plan_item",
            f"No build plan is set, so it has no item {item_id!r}.",
            where={"field": field},
            fix="Set the plan's items with update_build_plan first.",
        )
    ids = [item.id for item in plan.items]
    for item in plan.items:
        if item.id == item_id:
            return item
    close = difflib.get_close_matches(item_id, ids, n=3, cutoff=0.6)
    fields: dict[str, object] = {"valid_ids": ids}
    if close:
        fields["did_you_mean"] = close
    raise BuildPlanError(
        "unknown_plan_item",
        f"The build plan has no item {item_id!r}.",
        where={"field": field},
        fix=f"Use {close[0]!r}, an item of the plan." if close else "Use one of valid_ids.",
        **fields,
    )


def _with_item(
    plan: AssistantBuildPlan,
    item: AssistantBuildPlanItem,
    *,
    complete: bool,
    changes: Sequence[AssistantBuildPlanChange],
) -> AssistantBuildPlan:
    """*plan* with *item* rebuilt, validated, from its new completion and changes."""

    rebuilt = AssistantBuildPlanItem(
        id=item.id, title=item.title, complete=complete, changes=list(changes)
    )
    return AssistantBuildPlan(
        items=[rebuilt if entry.id == item.id else entry for entry in plan.items]
    )


class BuildPlan:
    """One session's build plan, live on the session and persisted with it.

    `current` is the plan's snapshot, None until the model sets one. A
    refused call raises `BuildPlanError` and leaves `current` as it was.
    """

    __slots__ = ("_current",)

    def __init__(self, current: AssistantBuildPlan | None = None) -> None:
        self._current = current

    @property
    def current(self) -> AssistantBuildPlan | None:
        return self._current

    def _replace(self, plan: AssistantBuildPlan) -> AssistantBuildPlan:
        """Make *plan* current, keeping the same snapshot when it is equal; return it."""

        if self._current is None or plan != self._current:
            self._current = plan
        return self._current

    def update(
        self,
        *,
        items: Sequence[Mapping[str, str]] | None,
        complete: str | None,
    ) -> AssistantBuildPlan:
        """Set or revise the plan's *items*, then claim the item *complete*, all or nothing.

        While the current plan has an open item, *items* revise it: an id it
        holds keeps its changes and completion under the new title, a new id
        starts open with no change, and an id left out is dropped. With no
        plan, or a finished one, *items* start a new plan. The claim is checked
        against the resulting plan.
        """

        if items is None and complete is None:
            raise BuildPlanError(
                "empty_plan_update",
                "update_build_plan needs items, complete or both.",
                fix=(
                    "Send the plan's items to set them, or an item id in complete to "
                    "mark that stage complete."
                ),
            )
        plan = self._current if items is None else self._planned(items)
        if complete is not None:
            plan = self._claimed(plan, complete)
        if plan is None:  # pragma: no cover - items or a claim always yields a plan
            raise RuntimeError("a build plan update must leave a plan")
        return self._replace(plan)

    def _planned(self, items: Sequence[Mapping[str, str]]) -> AssistantBuildPlan:
        ids = [item["id"] for item in items]
        for index, item_id in enumerate(ids):
            if item_id in ids[:index]:
                raise BuildPlanError(
                    "duplicate_plan_item",
                    f"Item id {item_id!r} appears more than once in items.",
                    where={"field": f"items[{index}].id"},
                    fix="Give each item its own id.",
                )
        current = self._current
        carried = (
            {}
            if current is None or all(item.complete for item in current.items)
            else {item.id: item for item in current.items}
        )
        return AssistantBuildPlan(
            items=[
                AssistantBuildPlanItem(
                    id=item["id"],
                    title=item["title"],
                    complete=item["id"] in carried and carried[item["id"]].complete,
                    changes=list(carried[item["id"]].changes) if item["id"] in carried else [],
                )
                for item in items
            ]
        )

    @staticmethod
    def _claimed(plan: AssistantBuildPlan | None, item_id: str) -> AssistantBuildPlan:
        item = _item(plan, item_id, field="complete")
        if plan is None:  # pragma: no cover - `_item` refuses a missing plan
            raise RuntimeError("a claimed item belongs to a plan")
        if item.complete:
            return plan
        if not _live_changes(item):
            raise BuildPlanError(
                "plan_item_unsaved",
                f"Item {item_id!r} has no saved change recorded against it, so it cannot be "
                "complete.",
                where={"field": "complete", "item": item_id},
                fix=(
                    f"Apply the plan that builds this stage with item {item_id!r} in "
                    "apply_graph_plan, then mark it complete."
                ),
            )
        return _with_item(plan, item, complete=True, changes=item.changes)

    def require_item(self, item_id: str) -> None:
        """Refuse an apply's *item_id* the plan does not hold, before the apply saves."""

        _item(self._current, item_id, field="item")

    def record_change(self, item_id: str, change_id: str) -> None:
        """Record the committed change *change_id* against item *item_id*.

        A change already listed (the same plan saved again after an undo) is
        marked not undone rather than listed twice. Recording never completes
        an item.
        """

        plan = self._current
        item = (
            None
            if plan is None
            else next((entry for entry in plan.items if entry.id == item_id), None)
        )
        if plan is None or item is None:
            raise RuntimeError(f"build plan item {item_id!r} was checked before its apply saved")
        if any(change.id == change_id for change in item.changes):
            changes = [
                AssistantBuildPlanChange(id=change.id, undone=False)
                if change.id == change_id
                else change
                for change in item.changes
            ]
        else:
            changes = [*item.changes, AssistantBuildPlanChange(id=change_id, undone=False)]
        self._replace(_with_item(plan, item, complete=item.complete, changes=changes))

    def undo(self, change_id: str) -> None:
        """Mark *change_id* undone wherever it is listed, reopening a complete item it leaves
        without a change that is not undone."""

        plan = self._current
        if plan is None:
            return
        items: list[AssistantBuildPlanItem] = []
        for item in plan.items:
            changes = [
                AssistantBuildPlanChange(id=change.id, undone=True)
                if change.id == change_id
                else change
                for change in item.changes
            ]
            items.append(
                AssistantBuildPlanItem(
                    id=item.id,
                    title=item.title,
                    complete=item.complete and any(not change.undone for change in changes),
                    changes=changes,
                )
            )
        self._replace(AssistantBuildPlan(items=items))


def build_plan_view(plan: AssistantBuildPlan) -> list[dict[str, object]]:
    """The plan as the tool result shows the model: per item its state and change counts."""

    view: list[dict[str, object]] = []
    for item in plan.items:
        entry: dict[str, object] = {
            "id": item.id,
            "title": item.title,
            "complete": item.complete,
            "changes": _live_changes(item),
        }
        undone = len(item.changes) - _live_changes(item)
        if undone:
            entry["undone"] = undone
        view.append(entry)
    return view


__all__ = ["BuildPlan", "BuildPlanError", "build_plan_view"]
