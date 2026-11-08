"""Readable schema maintenance, from the same plan that renders the owner's SQL.

This is a description of planned operations, not an execution result or a check of the
running release. Retained columns have different lifetimes in input and output tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from haute._quote_store._catalogue import Catalogue
from haute._quote_store._compare import Rename
from haute._quote_store._migrate import Plan, Step, StepKind, _structure_column, addition_problem
from haute._quote_store._providers import Provider
from haute._workbench_form import QUOTE_DETAILS_NAME, FormSpec

_TYPES = {"str": "Text", "int": "Integer", "float": "Decimal", "bool": "True/false", "date": "Date"}


@dataclass(frozen=True, slots=True)
class ReviewChange:
    kind: StepKind
    table: str
    column: str | None
    was: str | None
    disposition: Literal["sql", "manual", "retained"]
    description: str
    quotes: int | None


def retention_description(form: FormSpec, table_name: str | None, plan: Plan) -> str:
    """How unused values survive application writes, independently of exported additions."""
    if table_name is None:
        return (
            "Retain this unused table. Only the database owner should drop it, "
            "once nothing reads it."
        )
    if any(step.table == table_name and step.kind == "reshape table" for step in plan.steps):
        return "Preserving these values is part of the table's manual migration."
    table = next(table for table in form.data_schema.tables if table.name == table_name)
    if table.name == QUOTE_DETAILS_NAME:
        return "Retain this unused column; quote_details is updated in place, keeping its values."
    if table.has_inputs:
        return "Retain this unused column; supported values survive saves until the row is deleted."
    return (
        "Retain this unused column; this is an output-only table, so later saves clear its values."
    )


def _describe(
    step: Step, plan: Plan, form: FormSpec, provider: Provider, *, additions_only: bool
) -> ReviewChange:
    disposition: Literal["sql", "manual", "retained"] = "sql"
    match step.kind:
        case "create table":
            description = "Create this table, including its quote keys and references."
        case "add column":
            column = _structure_column(plan.recorded, step)
            description = (
                f"Add {_TYPES[column['type']]} column; existing database rows are initially empty."
            )
        case "rename column" | "rename table":
            if additions_only:
                disposition = "manual"
                description = (
                    f"SQL creates {step.column or step.table} empty "
                    f"and keeps {step.was} unchanged. "
                    "Transfer values, update any affected keys and remove the old name manually."
                )
            else:
                description = (
                    f"Rename {step.was} to {step.column or step.table}, keeping existing values."
                )
        case "retype column":
            before = _structure_column(plan.before, step)
            after = _structure_column(plan.recorded, step)
            old = before.get("sql_type", _TYPES.get(before["type"], before["type"]))
            description = f"Change {old} to {_TYPES[after['type']]}. "
            if additions_only:
                disposition = "manual"
                description += (
                    "Existing types and values are unchanged by this SQL. "
                    "Review and convert the data manually before changing the column type."
                )
            elif provider.dialect == "sqlite":
                disposition = "manual"
                description += (
                    "SQLite needs a table/column rebuild and data migration; "
                    "this SQL does not perform it."
                )
            else:
                description += (
                    "Review value conversion before running; "
                    "a value that cannot convert stops the script."
                )
        case "reshape table":
            disposition = "manual"
            description = (
                "Change the row structure or index type: rebuild the table and migrate its rows. "
                "This SQL does not perform it."
            )
        case "keep table":
            disposition = "retained"
            description = retention_description(form, None, plan)
        case "keep column":
            disposition = "retained"
            description = retention_description(form, step.table_key, plan)
    if additions_only and step.kind in (
        "create table",
        "add column",
        "rename table",
        "rename column",
    ):
        problem = addition_problem(plan, step, provider)
        if problem is not None:
            disposition = "manual"
            description = problem
    return ReviewChange(
        kind=step.kind,
        table=step.table,
        column=step.column,
        was=step.was,
        disposition=disposition,
        description=description,
        quotes=step.quotes if step.quotes is not None and step.quotes >= 0 else None,
    )


def review_plan(
    plan: Plan, form: FormSpec, provider: Provider, *, additions_only: bool = False
) -> tuple[ReviewChange, ...]:
    changes = [
        _describe(step, plan, form, provider, additions_only=additions_only) for step in plan.steps
    ]
    if plan.activity:
        changes.append(
            ReviewChange(
                kind="create table",
                table="haute_activity",
                column=None,
                was=None,
                disposition="sql",
                description="Create the quote activity table.",
                quotes=None,
            )
        )
    return tuple(changes)


def rename_problems(
    requested: Sequence[Rename],
    accepted: Sequence[Rename],
    catalogue: Catalogue,
    form: FormSpec,
) -> list[str]:
    """An existing destination needs no rename, but does not prove values were migrated."""
    problems = []
    for rename in requested:
        if rename in accepted:
            continue
        wanted = next(
            (table for table in form.data_schema.tables if table.name == rename.table), None
        )
        found = catalogue.table(rename.table)
        if (
            wanted is not None
            and any(column.name == rename.column for column in wanted.columns)
            and found is not None
            and found.column(rename.column) is not None
            and found.column(rename.was) is None
        ):
            continue
        problems.append(
            f"Cannot rename {rename.table}.{rename.was} to {rename.column} in this database. "
            "Check the source and destination before transferring data manually."
        )
    return problems
