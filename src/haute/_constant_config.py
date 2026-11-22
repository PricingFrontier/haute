"""A Constant node's sidecar: a map from each constant's name to its value (READ-28).

In memory, as the editor and the engine hold them, a Constant node's ``values`` are rows of
``{"name", "value"}``, each value as typed. Its sidecar writes them as one map, a constant a
line, each value as it runs (``haute._typed_numbers.constant_value``, specs/frontend-shared
high-level, "Typed numbers"): canonical finite decimal text is a JSON number (``1.45``,
``5``), which reads back as the text it was typed as; a boolean stays a boolean; any other
value stays text. So a value runs as the type its sidecar holds. A value that is not finite,
or a whole number outside a 64-bit integer's range, is refused, naming the constant. The
rows are read by one rule (``named_constants``), for the sidecar and for a run alike: a row
whose name is not text or is empty, and two rows with one name, are refused, naming the row.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from haute._typed_numbers import ConstantValueError, canonical_text, constant_value
from haute.errors import NodeConfigError

__all__ = ["constant_held", "constants_for_sidecar", "constants_from_sidecar", "named_constants"]


def constant_held(
    name: str, value: Any, *, setting: str = "values"
) -> bool | int | float | str | None:
    """The value of the constant *name* as it runs and as its sidecar holds it; a value no
    column can hold is refused, naming the constant (and its row, as *setting*)."""
    try:
        return constant_value(value)
    except ConstantValueError as exc:
        raise NodeConfigError(
            f"Constant {name!r} has the value {value!r}, which is {exc}. Give it a finite "
            "number or text.",
            setting=setting,
        ) from exc


def _as_read(name: str, value: Any, index: int) -> Any:
    """A sidecar's value as the editor holds it: a number as its canonical text, a number no
    column can hold (``NaN``, ``Infinity``, ``1e400``, a whole number past a 64-bit integer)
    refused, naming the constant. A value of another shape is left for the settings model to
    refuse, naming its row."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    # A number is held as a number: constant_value returns it as it is, or refuses it.
    held = cast(int | float, constant_held(name, value, setting=f"values[{index}].value"))
    return canonical_text(held)


def named_constants(rows: list[Any]) -> dict[str, bool | int | float | str | None]:
    """Each of a Constant's ``values`` rows as its name and the value it runs as.

    A row whose name is not text or is empty, and a name two rows share, are refused, naming
    the row, rather than the row being dropped or the later value replacing the earlier.
    """
    values: dict[str, bool | int | float | str | None] = {}
    for index, row in enumerate(rows):
        name = row.get("name", "") if isinstance(row, Mapping) else None
        if not isinstance(name, str):
            raise NodeConfigError(
                f"Constant value {index + 1} has a name {name!r} that is not text; "
                "give each value a name.",
                setting=f"values[{index}].name",
            )
        if not name:
            raise NodeConfigError(
                f"Constant value {index + 1} has no name; give it a name.",
                setting=f"values[{index}].name",
            )
        if name in values:
            raise NodeConfigError(
                f"Constant name {name!r} is used by more than one value; "
                "give each value its own name.",
                setting="values",
            )
        values[name] = constant_held(name, row.get("value", ""), setting=f"values[{index}].value")
    return values


def constants_for_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """*config* with its ``values`` rows written as a name-to-value map."""
    rows = config.get("values")
    if not isinstance(rows, list):
        return config
    values = named_constants(rows)
    return {**config, "values": values}


def constants_from_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """A Constant sidecar's ``values`` map read back into rows; rows refused."""
    values = config.get("values")
    if values is None:
        return config
    if isinstance(values, list):
        raise NodeConfigError(
            "This Constant sidecar holds its values as rows of name and value, the form before "
            "constants were written as a map. Write them as a map from each constant's name "
            "to its value.",
            setting="values",
        )
    if not isinstance(values, dict):
        raise NodeConfigError(
            "A Constant's values must be a map of name to value.", setting="values"
        )
    return {
        **config,
        "values": [
            {"name": name, "value": _as_read(name, value, index)}
            for index, (name, value) in enumerate(values.items())
        ],
    }
