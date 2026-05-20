"""V2 schema-mapping codec for API Input nodes.

The v2 shape (introduced by MULTI_FRAME_PLAN.md, commit 3) replaces v1's
``flattenSchema`` flat map with an explicit ``tables[]`` array. Each table
identifies an iteration depth in the JSON file and carries a column list
that names the fields extracted at that depth. A table with ``emit=true``
becomes a data-port on the API Input node — one frame per table.

This module is the on-the-wire contract: how to recognise a v2 config
(:func:`is_v2_shape`), how to migrate v1 ↔ v2 (:func:`legacy_to_v2` and
:func:`v2_to_legacy`), how to navigate a v2 schema's table paths
(:func:`parse_table_path`, :func:`parse_column_path`), and how to validate
that a v2 schema obeys the §4d invariants (:func:`validate_v2_schema`).

What lives in v2:

- ``tables[]`` — array of table specs. Each table has:
  - ``path``: JSONPath identifying the iteration depth. Root is ``"$[*]"``;
    nested arrays follow the pattern ``"$[*].<key>[*]"`` and so on.
  - ``label``: required, unique within the apiInput, defaults to ``path``
    on inference / v1 migration. Becomes the port name when the table
    emits in a multi-port apiInput.
  - ``emit``: bool. ``true`` means this table contributes a data-port at
    runtime.
  - ``displayPath``: optional UI alias; not load-bearing for the runtime.
  - ``row_id_column``: optional ``str | None`` — name of a column within
    this table to use as the row ID in the trace UI.
  - ``columns``: list of ``{name, path, type, status, selected, levels}``
    entries. ``name`` is the in-frame column name (rename target); ``path``
    is the source JSONPath; ``type`` is one of ``int|float|str|bool|date``;
    ``status`` is ``"Confirmed"`` or ``"Inferred"``; ``selected`` is bool;
    ``levels`` is an optional categorical-values list.

What v2 deliberately doesn't have (per the plan):

- Cross-table column inheritance. Each table's columns belong to its own
  iteration depth — the user explicitly adds a column to a child table if
  they want to surface a parent value there. The §10 backlog item revisits
  this if the absence proves painful.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast

_V2_TABLES_KEY = "tables"
_V1_FLATTEN_KEY = "flattenSchema"

ColumnType = Literal["int", "float", "str", "bool", "date"]
ColumnStatus = Literal["Confirmed", "Inferred"]


class ColumnV2(TypedDict, total=False):
    """One column entry inside ``tables[*].columns[*]``."""

    name: str
    path: str
    type: ColumnType
    status: ColumnStatus
    selected: bool
    levels: list[str | None] | None


class TableV2(TypedDict, total=False):
    """One table entry inside ``tables[*]``."""

    path: str
    label: str
    displayPath: str | None
    emit: bool
    row_id_column: str | None
    columns: list[ColumnV2]


class ApiInputV2Config(TypedDict, total=False):
    """v2 apiInput config — on-disk shape under ``rating/config/<...>.json``."""

    path: str
    contract: str
    tables: list[TableV2]
    removedTables: list[str]


# ---------------------------------------------------------------------------
# Shape detection
# ---------------------------------------------------------------------------


def is_v2_shape(config: dict[str, Any]) -> bool:
    """Return True iff *config* is a v2 schema mapping.

    v2 is identified by the presence of the ``tables`` key. v1 carries
    ``flattenSchema`` instead. A config with both is corrupt — we treat it
    as v1 for safety so any subsequent migration runs through the
    one-direction ``legacy_to_v2`` path.
    """
    has_tables = isinstance(config.get(_V2_TABLES_KEY), list)
    has_flatten = isinstance(config.get(_V1_FLATTEN_KEY), dict)
    return has_tables and not has_flatten


# ---------------------------------------------------------------------------
# Path parsing
# ---------------------------------------------------------------------------


def parse_table_path(path: str) -> tuple[str, ...]:
    """Convert a v2 table path into a tuple of nested keys.

    Conventions:
    - ``"$"`` and ``"$[*]"`` are equivalent root iterators; both produce ``()``.
    - ``"$[*].<key>[*]"`` is one level of array nesting; produces ``(<key>,)``.
    - ``"$[*].<key>[*].<key2>[*]"`` is two levels; produces ``(<key>, <key2>)``.

    The ``[*]`` markers denote "iterate this array"; v2 paths use them at
    every iteration depth. The function strips them and returns just the
    sequence of nested keys.

    Raises ``ValueError`` on malformed paths.
    """
    if path in ("$", "$[*]"):
        return ()
    if not path.startswith("$[*]."):
        raise ValueError(
            f"v2 table path must start with '$[*].' (or be exactly '$' / '$[*]'): {path!r}",
        )
    rest = path.removeprefix("$[*].")
    segments: list[str] = []
    for seg in rest.split("."):
        if not seg.endswith("[*]"):
            raise ValueError(
                f"v2 table path segment must end with '[*]' "
                f"(no leaf columns allowed at table level): {seg!r} in {path!r}",
            )
        bare = seg.removesuffix("[*]")
        if not bare:
            raise ValueError(f"v2 table path has empty segment in {path!r}")
        segments.append(bare)
    return tuple(segments)


def parse_column_path(column_path: str, table_path: str) -> str:
    """Return the column's leaf key relative to its containing table's path.

    For a table at ``"$[*].drivers[*]"`` and a column at
    ``"$[*].drivers[*].driver_id"``, returns ``"driver_id"``. For a column
    spanning multiple dotted segments (e.g. ``"$[*].drivers[*].profile.age"``),
    returns the dotted tail (``"profile.age"``).

    Raises ``ValueError`` if the column's path doesn't sit under the
    table's iteration depth.
    """
    if not column_path.startswith(table_path):
        raise ValueError(
            f"v2 column path {column_path!r} must start with table path {table_path!r}",
        )
    tail = column_path[len(table_path) :]
    if not tail:
        raise ValueError(
            f"v2 column path {column_path!r} equals its table path; "
            "columns must name a leaf field, not the table itself",
        )
    if not tail.startswith("."):
        raise ValueError(
            f"v2 column path {column_path!r} doesn't sit cleanly under "
            f"table path {table_path!r} (missing dot separator)",
        )
    return tail.removeprefix(".")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_v2_schema(config: dict[str, Any]) -> None:
    """Raise ``ValueError`` if *config* violates v2 invariants (§4d).

    Invariants:
    - ``tables`` is a list of dicts.
    - Each table has a non-empty string ``label``.
    - Each table has a parseable ``path`` (via :func:`parse_table_path`).
    - Labels are unique within the ``tables`` list.
    - Each table's ``columns[].name`` values are unique within that table.
    - Each column's ``path`` parses cleanly via :func:`parse_column_path`.
    - When ``row_id_column`` is non-null on a table, it references a
      column ``name`` in the same table.
    - When ``levels`` is set on a column, it's a list of strings (or null
      entries). Empty list is rejected — use ``null`` for "no domain".
    """
    if not is_v2_shape(config):
        raise ValueError("config is not in v2 shape (no `tables` key)")

    tables = config[_V2_TABLES_KEY]
    if not isinstance(tables, list):
        raise ValueError("v2 `tables` must be a list")

    seen_labels: set[str] = set()
    for ti, table in enumerate(tables):
        if not isinstance(table, dict):
            raise ValueError(f"v2 tables[{ti}] is not a dict")
        path = table.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError(f"v2 tables[{ti}].path is missing or not a non-empty string")
        parse_table_path(path)  # raises on malformed
        label = table.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(
                f"v2 tables[{ti}].label is missing or not a non-empty string "
                "(must be unique within the apiInput)",
            )
        if label in seen_labels:
            raise ValueError(f"v2 table label {label!r} appears more than once")
        seen_labels.add(label)

        columns = table.get("columns", [])
        if not isinstance(columns, list):
            raise ValueError(f"v2 tables[{ti}].columns is not a list")
        seen_col_names: set[str] = set()
        for ci, col in enumerate(columns):
            if not isinstance(col, dict):
                raise ValueError(f"v2 tables[{ti}].columns[{ci}] is not a dict")
            cname = col.get("name")
            if not isinstance(cname, str) or not cname:
                raise ValueError(
                    f"v2 tables[{ti}].columns[{ci}].name is missing or not a non-empty string",
                )
            if cname in seen_col_names:
                raise ValueError(
                    f"v2 tables[{ti}] (label={label!r}) has duplicate column name {cname!r}",
                )
            seen_col_names.add(cname)
            cpath = col.get("path")
            if not isinstance(cpath, str) or not cpath:
                raise ValueError(
                    f"v2 tables[{ti}].columns[{ci}].path is missing or not a non-empty string",
                )
            parse_column_path(cpath, path)  # raises if it doesn't sit under the table path
            levels = col.get("levels")
            if levels is not None:
                if not isinstance(levels, list):
                    raise ValueError(
                        f"v2 tables[{ti}].columns[{ci}].levels must be a list or null "
                        f"(got {type(levels).__name__})",
                    )
                if len(levels) == 0:
                    raise ValueError(
                        f"v2 tables[{ti}].columns[{ci}].levels is empty; "
                        "use null to mean 'no declared domain'",
                    )

        row_id_column = table.get("row_id_column")
        if row_id_column is not None:
            if not isinstance(row_id_column, str) or not row_id_column:
                raise ValueError(
                    f"v2 tables[{ti}].row_id_column must be null or a non-empty string",
                )
            if row_id_column not in seen_col_names:
                raise ValueError(
                    f"v2 tables[{ti}].row_id_column={row_id_column!r} does not match any "
                    f"column.name in this table (cols={sorted(seen_col_names)})",
                )


# ---------------------------------------------------------------------------
# Migration codec
# ---------------------------------------------------------------------------


def legacy_to_v2(config: dict[str, Any]) -> dict[str, Any]:
    """Translate a v1 config to v2 in-memory shape.

    Used by the ApiInputEditor's first-load migration (MULTI_FRAME_PLAN
    commit 5). v1 has ``flattenSchema`` (flat ``{leaf_path: type}`` dict)
    plus ad-hoc helper keys (``column_renames``, ``categorical_levels``,
    ``row_id_column``, ``selected_columns``, ...). v2 has ``tables[]``.

    Migration policy (§4d):
    - Produces exactly ONE table at the existing flat root (``$[*]``).
    - That table's ``label`` defaults to ``"$[*]"`` (the path) — falls back
      to the apiInput node's label is a §5 editor concern, not this
      function's.
    - ``emit=True`` so existing single-port pipelines keep working.
    - Each v1 ``flattenSchema`` leaf becomes a v2 column.
    - ``v1.column_renames`` lift into per-column ``name`` overrides.
    - ``v1.selected_columns`` lifts into per-column ``selected=True``.
    - ``v1.categorical_levels`` lift into per-column ``levels``.
    - ``v1.row_id_column`` lifts into ``tables[0].row_id_column``.
    - Type metadata (``flattenSchema`` values, ``schema_overrides``,
      ``dtypes``, ``column_dtypes``, ``schema``) collapses into the
      per-column ``type`` field.

    Orphans (renames / level entries / row_id pointing to a column that
    doesn't exist in flattenSchema) are dropped; callers should surface a
    migration banner with the dropped keys. This function does not raise
    on orphans; it simply omits them.
    """
    if is_v2_shape(config):
        return dict(config)

    flatten_schema = config.get(_V1_FLATTEN_KEY, {})
    if not isinstance(flatten_schema, dict):
        flatten_schema = {}

    column_renames = config.get("column_renames", {})
    if not isinstance(column_renames, dict):
        column_renames = {}

    selected_columns = config.get("selected_columns", [])
    if not isinstance(selected_columns, list):
        selected_columns = []
    selected_set = {str(s) for s in selected_columns}

    categorical_levels = config.get("categorical_levels", {})
    if not isinstance(categorical_levels, dict):
        categorical_levels = {}

    row_id_column = config.get("row_id_column")
    if not isinstance(row_id_column, str) or not row_id_column:
        row_id_column = None

    columns: list[ColumnV2] = []
    for leaf_path, leaf_type in flatten_schema.items():
        if not isinstance(leaf_path, str) or not isinstance(leaf_type, str):
            continue
        # v1 leaf_path is a dotted name like "policy_details.policy_number";
        # under v2 it lives under the single root table at "$[*]" with
        # column.path = "$[*].<leaf_path>".
        column_path = f"$[*].{leaf_path}"
        # name = rename target if v1 had one, else the leaf path itself.
        name = column_renames.get(leaf_path) if leaf_path in column_renames else leaf_path
        if not isinstance(name, str) or not name:
            name = leaf_path
        col: ColumnV2 = {
            "name": name,
            "path": column_path,
            "type": cast(ColumnType, leaf_type if leaf_type in {"int", "float", "str", "bool", "date"} else "str"),
            "status": "Confirmed",
            "selected": leaf_path in selected_set or not selected_set,
        }
        levels_for_col = categorical_levels.get(leaf_path)
        if isinstance(levels_for_col, list) and len(levels_for_col) > 0:
            col["levels"] = list(levels_for_col)
        columns.append(col)

    table: TableV2 = {
        "path": "$[*]",
        "label": "$[*]",
        "displayPath": None,
        "emit": True,
        "columns": columns,
    }
    # row_id_column from v1 lifts onto tables[0] if it matches a migrated column name.
    if row_id_column is not None and any(c.get("name") == row_id_column for c in columns):
        table["row_id_column"] = row_id_column

    out: dict[str, Any] = {
        "path": config.get("path", ""),
        "contract": config.get("contract", "opaque"),
        _V2_TABLES_KEY: [table],
    }
    return out


def v2_to_legacy(config: dict[str, Any]) -> dict[str, Any]:
    """Translate a v2 config to v1 in-memory shape.

    Currently unused (the runtime reads v2 directly when present), but
    available for any code path that still wants the legacy keys. Returns
    a config with ``flattenSchema`` derived from the union of all
    emit-true tables' columns. Loses the multi-table structure — this is
    intentional, since v1 has no concept of multi-port emit. Caller
    should use this only when speaking to a v1-only consumer.
    """
    if not is_v2_shape(config):
        return dict(config)

    flatten_schema: dict[str, str] = {}
    column_renames: dict[str, str] = {}
    categorical_levels: dict[str, list[str | None]] = {}
    selected_columns: list[str] = []
    row_id_column: str | None = None

    tables = config.get(_V2_TABLES_KEY, [])
    if not isinstance(tables, list):
        tables = []
    for ti, table in enumerate(tables):
        if not isinstance(table, dict):
            continue
        table_path = table.get("path")
        if not isinstance(table_path, str):
            continue
        if ti == 0 and isinstance(table.get("row_id_column"), str):
            row_id_column = table["row_id_column"]
        for col in table.get("columns", []) or []:
            if not isinstance(col, dict):
                continue
            name = col.get("name")
            path = col.get("path")
            type_ = col.get("type")
            if not isinstance(name, str) or not isinstance(path, str):
                continue
            # v1's flattenSchema key is the leaf path under root.
            try:
                leaf = parse_column_path(path, table_path)
            except ValueError:
                continue
            flatten_schema[leaf] = str(type_) if isinstance(type_, str) else "str"
            if name != leaf:
                column_renames[leaf] = name
            if col.get("selected"):
                selected_columns.append(leaf)
            levels = col.get("levels")
            if isinstance(levels, list) and len(levels) > 0:
                categorical_levels[name] = list(levels)

    out: dict[str, Any] = {
        "path": config.get("path", ""),
        "contract": config.get("contract", "opaque"),
        _V1_FLATTEN_KEY: flatten_schema,
    }
    if column_renames:
        out["column_renames"] = column_renames
    if selected_columns:
        out["selected_columns"] = selected_columns
    if categorical_levels:
        out["categorical_levels"] = categorical_levels
    if row_id_column is not None:
        out["row_id_column"] = row_id_column
    return out
