"""The quote list setting (specs/workbench): what the underwriter app's Quotes page shows of
each quote, checked against the sheets' schema, and each quote's projection by it.

The setting names columns by their stable ids, so a renamed column keeps its place. A quote
is projected from one revision through the form that revision was keyed into: its title and
summary fields from the values as typed into that form's one-row input tables, its headline
from that revision's response, whose output tables and columns are named as that form named
them. A column the revision's form does not have projects as blank.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from haute._cache import canonical_json
from haute._hashing import content_hash_bytes
from haute._workbench_form import Cell, FieldRef, FormSpec, QuoteList, SchemaColumn, SchemaTable

#: A quote's values as typed: by table id, rows keyed by column id.
Values = dict[str, list[dict[str, Cell]]]

_NUMBER_TYPES = frozenset({"int", "float"})


@dataclass(frozen=True, slots=True)
class Projection:
    """What the Quotes page shows of a quote, projected from a revision by the quote list
    setting of the sheets it is listed under."""

    #: The title field's value as text; None when blank, or when the setting names none.
    title: str | None = None
    #: Each summary field's value as saved, in the setting's order; None where blank.
    fields: list[Any] = field(default_factory=list)
    #: The headline output's value; None when unpriced or blank.
    headline: Any = None
    #: What identifies the setting it was projected with; a different one is reprojected.
    key: str = ""

    def summary(self) -> dict[str, Any]:
        return {"title": self.title, "fields": self.fields, "headline": self.headline}


def _found(form: FormSpec, ref: FieldRef) -> tuple[SchemaTable, SchemaColumn] | None:
    for table in form.data_schema.tables:
        if table.id == ref.table:
            for column in table.columns:
                if column.id == ref.column:
                    return table, column
    return None


def quote_list_problems(form: FormSpec) -> list[str]:
    """Each way the form's quote list setting names a column the Quotes page cannot show:
    one the schema does not have, one of a many-row table, a title or summary field of an
    output table, a headline of an input table or one that is not a number, or a summary
    field named twice."""
    setting = form.quote_list
    problems: list[str] = []

    def check(ref: FieldRef, what: str, role: str) -> None:
        found = _found(form, ref)
        if found is None:
            problems.append(
                f"The quote list's {what} names column {ref.column!r} of table "
                f"{ref.table!r}, which the schema does not have."
            )
            return
        table, column = found
        where = f"{table.name}.{column.name}"
        if table.rows != "one":
            problems.append(
                f"The quote list's {what}, {where}, is in a many-row table: a quote's list "
                "row shows one value."
            )
        if table.role != role:
            problems.append(f"The quote list's {what}, {where}, is not in an {role} table.")
        if what == "headline" and column.type not in _NUMBER_TYPES:
            problems.append(f"The quote list's headline, {where}, is not a number column.")

    if setting.title is not None:
        check(setting.title, "title", "input")
    for index, ref in enumerate(setting.fields, start=1):
        check(ref, f"summary field {index}", "input")
    if len({(ref.table, ref.column) for ref in setting.fields}) != len(setting.fields):
        problems.append("The quote list names a summary field twice.")
    if setting.headline is not None:
        check(setting.headline, "headline", "output")
    return problems


def projection_key(setting: QuoteList) -> str:
    """What identifies *setting*: a quote projected by another is projected again."""
    return content_hash_bytes(canonical_json(setting.model_dump(mode="json")).encode("utf-8"))


def _typed(form: FormSpec, inputs: Values, ref: FieldRef) -> Any:
    found = _found(form, ref)
    if found is None or found[0].role != "input" or found[0].rows != "one":
        return None
    rows = inputs.get(ref.table) or []
    value = rows[0].get(ref.column) if rows else None
    return None if value is None or value == "" else value


def _priced(form: FormSpec, response: dict[str, Any] | None, ref: FieldRef) -> Any:
    found = _found(form, ref)
    if response is None or found is None or found[0].role != "output":
        return None
    rows = response.get(found[0].name)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return rows[0].get(found[1].name)


def project(
    setting: QuoteList, saved: FormSpec, inputs: Values, response: dict[str, Any] | None
) -> Projection:
    """A quote as *setting* lists it, from a revision keyed into *saved* with *inputs* and
    priced with *response* (None when unpriced)."""
    title = None if setting.title is None else _typed(saved, inputs, setting.title)
    return Projection(
        title=None if title is None else str(title).strip() or None,
        fields=[_typed(saved, inputs, ref) for ref in setting.fields],
        headline=None if setting.headline is None else _priced(saved, response, setting.headline),
        key=projection_key(setting),
    )
