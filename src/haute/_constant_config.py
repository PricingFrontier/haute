"""A Constant node's sidecar: a map from each constant's name to its value (READ-28).

In memory, as the editor and the engine hold them, a Constant node's ``values`` are rows of
``{"name", "value"}``, each value as typed. Its sidecar writes them as one map, a constant a
line: a value that reads as a number, and reads back to the text it was typed as, is a JSON
number (``1.45``); any other value stays as written, text included. Each runs as it ran
before: a value that reads as a number is a number, the rest as written. A row with no name,
which a run skips, is not written; two rows with one name, which a run refuses, are refused.
"""

from __future__ import annotations

import math
import re
from typing import Any

from haute.errors import NodeConfigError

__all__ = ["CONSTANTS_CONVERSION", "constants_for_sidecar", "constants_from_sidecar"]

#: The conversion a Constant sidecar still holding rows needs.
CONSTANTS_CONVERSION = "scripts/one_off/read_28_constant_maps.py"
_INTEGER_TEXT = re.compile(r"-?(?:0|[1-9]\d*)")


def _as_written(value: Any) -> Any:
    """A constant's value as its sidecar writes it: a number where it reads back to its text."""
    if not isinstance(value, str):
        return value
    if _INTEGER_TEXT.fullmatch(value):
        return int(value)
    try:
        number = float(value)
    except ValueError:
        return value
    return number if math.isfinite(number) and repr(number) == value else value


def _as_read(value: Any) -> Any:
    """A sidecar's value as the editor holds it: a number as its text."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    return repr(value) if isinstance(value, float) else str(value)


def constants_for_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """*config* with its ``values`` rows written as a name-to-value map."""
    rows = config.get("values")
    if not isinstance(rows, list):
        return config
    values: dict[str, Any] = {}
    for row in rows:
        name = row.get("name", "") if isinstance(row, dict) else ""
        if not isinstance(name, str) or not name:
            continue
        if name in values:
            raise NodeConfigError(
                f"Constant name {name!r} is used by more than one value; "
                "give each value its own name.",
                setting="values",
            )
        values[name] = _as_written(row.get("value", ""))
    return {**config, "values": values}


def constants_from_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """A Constant sidecar's ``values`` map read back into rows; rows refused, naming the
    conversion."""
    values = config.get("values")
    if values is None:
        return config
    if isinstance(values, list):
        raise NodeConfigError(
            "This Constant sidecar holds its values as rows of name and value, the form before "
            f"constants were written as a map. Convert the project's Constant sidecars with "
            f"{CONSTANTS_CONVERSION}.",
            setting="values",
        )
    if not isinstance(values, dict):
        raise NodeConfigError(
            "A Constant's values must be a map of name to value.", setting="values"
        )
    return {
        **config,
        "values": [{"name": name, "value": _as_read(value)} for name, value in values.items()],
    }
