"""V2 schema-mapping codec for API Input nodes.

The v2 shape (per MULTI_FRAME_PLAN.md, commit 3) is the only shape this
codec understands. The on-disk config under ``rating/config/<...>.json``
carries ``tables[]``; nested-array data surfaces as child tables, not
flat indexed columns. Pre-v2 config files in the wild (carrying
``flattenSchema``) are treated as empty at runtime — the user opens the
editor and clicks "Infer Tables" to populate ``tables[]`` afresh.

This module is the on-the-wire contract:

- :func:`is_v2_shape` — does the config carry the load-bearing ``tables``
  key? (Stray legacy keys alongside are tolerated and ignored.)
- :func:`parse_table_path`, :func:`parse_column_path` — navigate the
  JSONPath conventions used in ``tables[].path`` and column ``path``.
- :func:`validate_v2_schema` — raise :class:`ApiInputSchemaError` on
  any violation of the §4d invariants OR the B-guardrails added in the
  v1-removal commit: B1 (unknown column types loud-fail), B2 (sanitised
  label collision loud-fail), B3 (typed exception throughout so the
  cache route can catch specifically and return a structured 422).

What lives in v2:

- ``tables[]`` — array of table specs. Each table has:
  - ``path``: JSONPath identifying the ARRAY iteration depth. Root is
    ``"$[:]"``; nested arrays follow ``"$[:].<key>[:]"``, and an array
    nested inside a 1-1 object reaches it through object hops:
    ``"$[:].proposer.claims[:]"``. ``[:]`` is the only accepted array
    selector (a legacy ``[*]`` is rejected). The relational depth is the
    number of ``[:]`` hops — 1-1 object nesting is transparent (see below).
  - ``label``: required, unique within the apiInput, defaults to ``path``
    on inference. Becomes the frame name when the table emits in a
    multi-frame apiInput. Sanitised to a filesystem-safe form to derive
    the per-table parquet filename — labels whose sanitised forms
    collide are rejected by B2.
  - ``emit``: bool. ``true`` means this table contributes a data-frame
    at runtime.
  - ``displayPath``: optional UI alias; not load-bearing for the
    runtime.
  - ``row_id_column``: optional ``str | None`` — name of a column within
    this table to use as the row ID in the trace UI.
  - ``columns``: list of ``{name, path, type, status, selected, levels}``
    entries. ``name`` is the in-frame column name (rename target);
    ``path`` is the source JSONPath; ``type`` is one of
    ``int|float|str|bool|date`` (B1: unknown types are rejected at
    validate-time, not silently downgraded); ``status`` is
    ``"Confirmed"`` or ``"Inferred"``; ``selected`` is bool; ``levels``
    is an optional categorical-values list.

Object-nesting transparency (2026-06-17 ruling): relational depth is the
ARRAY (``[:]``) nesting depth ONLY. Nesting inside a 1-1 object does not
change the relational structure — ``$[:].a.b.c`` and ``$[:].p.q`` are
siblings (columns of the same array level), addressed via a dotted leaf;
only an array of objects descends a level. Inference therefore folds 1-1
object scalars into their enclosing array level instead of minting a table
per object.

What v2 deliberately doesn't have (per the plan):

- Cross-table column inheritance across ARRAY levels. A table's columns
  belong to its own array depth — the user explicitly adds a column to a
  child table to surface a parent value there. The §10 backlog item
  revisits this if the absence proves painful.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal, TypedDict

from haute.errors import HauteError

_V2_TABLES_KEY = "tables"

ColumnType = Literal["int", "float", "str", "bool", "date"]
ColumnStatus = Literal["Confirmed", "Inferred"]

_ALLOWED_COLUMN_TYPES: frozenset[str] = frozenset({"int", "float", "str", "bool", "date"})

# Filesystem-safe label form, shared with `_json_shred._sanitise_label`
# (which imports this regex). Lives here because v2-schema validation
# (B2: sanitised-label collision detection) is the structural use of it
# — the parquet-file-write site in `_json_shred` is the downstream
# consumer.
_FILESYSTEM_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]")


def sanitise_label_for_filesystem(label: str) -> str:
    """Map a v2 table label to a filesystem-safe parquet-filename stem.

    Replaces any non-(letter, digit, underscore, hyphen) with an
    underscore. Empty label falls back to ``"_unnamed"`` — though
    ``validate_v2_schema`` rejects empty labels earlier.

    Two labels with the same sanitised form would collide at the
    parquet-write step; B2 in :func:`validate_v2_schema` catches this
    pre-write so the second parquet doesn't silently clobber the first.
    """
    if not label:
        return "_unnamed"
    return _FILESYSTEM_SAFE_RE.sub("_", label)


class ApiInputSchemaError(HauteError):
    """Raised when a v2 apiInput schema violates an invariant or guardrail.

    Replaces the previous bare ``ValueError`` raised by
    :func:`validate_v2_schema` / :func:`parse_table_path` /
    :func:`parse_column_path`. Catching this in the JSON cache route
    lets us distinguish v2-schema problems from arbitrary other
    failures and return an HTTP 422 with a structured error body
    (``{"detail": ..., "type": "ApiInputSchemaError"}``) — the frontend
    branches on the discriminator rather than string-matching the
    detail.
    """


class ColumnV2(TypedDict, total=False):
    """One column entry inside ``tables[].columns[]``."""

    name: str
    path: str
    type: ColumnType
    status: ColumnStatus
    selected: bool
    levels: list[str | None] | None


class TableV2(TypedDict, total=False):
    """One table entry inside ``tables[]``."""

    path: str
    label: str
    displayPath: str | None
    emit: bool
    row_id_column: str | None
    columns: list[ColumnV2]


class ApiInputV2Config(TypedDict, total=False):
    """v2 apiInput config — on-disk shape under ``rating/config/<...>.json``.

    Bundle 1 sanitisation: ``removedTables`` was previously declared
    here as a parallel to the frontend's editor-side ledger; the
    feature was never wired and is dropped. See
    ``haute._types.ApiInputConfig`` for the full rationale and the
    test that pins the absence.
    """

    path: str
    contract: str
    tables: list[TableV2]


# ---------------------------------------------------------------------------
# Shape detection
# ---------------------------------------------------------------------------


def is_v2_shape(config: Any) -> bool:
    """Return True iff *config* carries a v2 ``tables[]`` array.

    Accepts ``Any`` (not just ``dict``) so unrelated payload shapes
    coming through the API surface as a clean ``False`` rather than an
    ``AttributeError``. Callers downstream get a ``False`` and then
    :func:`validate_v2_schema` raises an :class:`ApiInputSchemaError`
    with a useful message.

    Per decision D9 ("as if v1 doesn't exist"): a config that ALSO
    carries pre-v2 keys (``flattenSchema``, ``column_renames``, …) is
    still v2 if ``tables`` is present. Stray legacy keys are tolerated
    silently; the runtime reads only the v2 surface.
    """
    if not isinstance(config, dict):
        return False
    return isinstance(config.get(_V2_TABLES_KEY), list)


_JSON_API_INPUT_SUFFIXES = (".json", ".jsonl")


def is_json_api_input_path(path: str) -> bool:
    """Return whether *path* routes an apiInput through the JSON codec.

    THE runtime dispatch predicate for apiInput sources — shared by the
    executor's source builder (``haute._builders._build_api_input``) and
    the preview/trace cache-key signature
    (``haute.execution._runtime_file_signature_paths``) so the two can
    never disagree about which file an apiInput actually reads: JSON /
    JSONL paths are served from the built per-port parquet cache, every
    other extension is read directly as a flat file.
    """
    return path.lower().endswith(_JSON_API_INPUT_SUFFIXES)


# ---------------------------------------------------------------------------
# Path parsing
# ---------------------------------------------------------------------------


# A parsed path segment: the bare key plus whether it is an array iterator.
# Object (1-1) hops carry ``is_array=False`` and do NOT advance the relational
# depth; only array-of-objects hops (``is_array=True``) descend a level. This is
# the schema-inference ruling (2026-06-17): nesting inside 1-1 objects is
# relationally transparent — addressing within different objects doesn't change
# the relational structure — so ``$[:].a.b.c`` and ``$[:].p.q`` are siblings;
# only a ``[:]`` array of objects ends strictly lower.
PathSeg = tuple[str, bool]

# ``[:]`` is the canonical — and ONLY — array selector (shared with the OUTPUT
# path grammar; STATE_OF_PLAY §2: "[:] over [*] because it is explicit array
# parsing"). One selector = one canonical form: equivalence and diffing are
# exact, and external tooling has a single shape to parse. There is NO ``[*]``
# alias — inference and the editor write ``[:]``, and a legacy ``[*]`` path is
# rejected (not silently normalised) so paths never have two spellings.
_ARRAY_SELECTOR = "[:]"
_ROOT_PATHS = ("$", "$[:]")
_ROOT_PREFIXES = ("$[:].",)


def _split_path_segment(seg: str, *, path: str) -> PathSeg:
    """Split one dotted segment into ``(bare_key, is_array)``.

    ``"drivers[:]"`` -> ``("drivers", True)``; ``"profile"`` -> ``("profile",
    False)``. Empty or otherwise-malformed segments — including a legacy
    ``[*]`` or any stray ``[``/``]`` outside the whole-array ``[:]`` selector —
    raise.
    """
    if seg.endswith(_ARRAY_SELECTOR):
        bare = seg[: -len(_ARRAY_SELECTOR)]
        if not bare:
            raise ApiInputSchemaError("v2 path has an empty array segment", path=path)
        if "[" in bare or "]" in bare:
            raise ApiInputSchemaError(
                "v2 path segment has an unsupported selector "
                "(only the whole-array '[:]' is accepted)",
                segment=seg,
                path=path,
            )
        return (bare, True)
    if not seg:
        raise ApiInputSchemaError("v2 path has an empty segment", path=path)
    if "[" in seg or "]" in seg:
        raise ApiInputSchemaError(
            "v2 path segment has an unsupported selector "
            "(only the whole-array '[:]' is accepted; legacy '[*]' is not)",
            segment=seg,
            path=path,
        )
    return (seg, False)


def _parse_dollar_path(path: str) -> list[PathSeg]:
    """Parse a ``$``-rooted dotted path into ``(key, is_array)`` segments.

    The root (``"$"`` / ``"$[:]"``) is the root array iterator and parses to
    ``[]``. Each subsequent ``.``-separated segment is an object hop (bare key)
    or an array hop (``key[:]``).
    """
    if path in _ROOT_PATHS:
        return []
    for prefix in _ROOT_PREFIXES:
        if path.startswith(prefix):
            rest = path[len(prefix) :]
            return [_split_path_segment(seg, path=path) for seg in rest.split(".")]
    raise ApiInputSchemaError(
        "v2 path must start with '$[:].' (or be exactly '$' / '$[:]')",
        path=path,
    )


def array_depth(segments: Sequence[PathSeg]) -> int:
    """Relational depth of a segment list — the number of array (``[:]``) hops."""
    return sum(1 for _key, is_array in segments if is_array)


def make_table_path(segments: Sequence[PathSeg]) -> str:
    """Render ``(key, is_array)`` segments back to a ``[:]``-canonical path string.

    The inverse of :func:`parse_table_path` / :func:`_parse_dollar_path`; emits
    ``[:]`` for array hops and bare keys for object hops. ``()`` -> ``"$[:]"``.
    """
    if not segments:
        return "$[:]"
    rendered = (f"{key}{_ARRAY_SELECTOR}" if is_array else key for key, is_array in segments)
    return "$[:]." + ".".join(rendered)


def parse_table_path(path: str) -> tuple[PathSeg, ...]:
    """Parse a v2 table ``path`` into its ``(key, is_array)`` segments.

    A table sits at an ARRAY boundary: the root array (``"$[:]"`` -> ``()``) or
    a ``[:]`` array of objects, optionally reached through 1-1 object hops.

    - ``"$"`` / ``"$[:]"`` -> ``()`` (root array).
    - ``"$[:].drivers[:]"`` -> ``(("drivers", True),)``.
    - ``"$[:].proposer.claims[:]"`` ->
      ``(("proposer", False), ("claims", True))`` — an array of objects nested
      inside the 1-1 ``proposer`` object. Its relational depth is 1 (one
      ``[:]``); ``proposer`` only locates it.

    Raises :class:`ApiInputSchemaError` on a malformed path, or one that does
    not end at an array (a table must be an array boundary, never a bare object
    key — that key's leaves are columns of the enclosing array level).
    """
    segments = _parse_dollar_path(path)
    if segments and not segments[-1][1]:
        raise ApiInputSchemaError(
            "v2 table path must end at an array '[:]' — a bare object key is not a "
            "table (its leaves are columns of the enclosing array level)",
            path=path,
        )
    return tuple(segments)


def parse_column_path_full(column_path: str) -> tuple[tuple[PathSeg, ...], str]:
    """Split a v2 column path into its (locating segments, dotted leaf).

    The locating segments run up to and including the column's deepest array
    (``[:]``) hop — its relational level, possibly reached through 1-1 object
    hops; the trailing object hops form the dotted leaf resolved *within the
    node at that level* (no array crossing). The array-count of the locating
    segments is the column's relational depth.

    - ``"$[:].quote_id"``                  -> ``((), "quote_id")``
    - ``"$[:].quote_metadata.quote_id"``   -> ``((), "quote_metadata.quote_id")``
    - ``"$[:].drivers[:].driver_id"``      -> ``((("drivers", True),), "driver_id")``
    - ``"$[:].drivers[:].profile.age"``    -> ``((("drivers", True),), "profile.age")``
    - ``"$[:].proposer.claims[:].amount"`` ->
      ``((("proposer", False), ("claims", True)), "amount")``

    Raises :class:`ApiInputSchemaError` on a malformed path or one that names
    no leaf (the bare root iterator, or a path ending at ``[:]``).
    """
    if column_path in _ROOT_PATHS:
        raise ApiInputSchemaError(
            "v2 column path names no leaf field (it is the root iterator)",
            column_path=column_path,
        )
    segments = _parse_dollar_path(column_path)
    # The leaf is the maximal trailing run of object (non-array) hops; the
    # locating segments are everything up to and including the deepest array.
    last_array = -1
    for i, (_key, is_array) in enumerate(segments):
        if is_array:
            last_array = i
    locating = tuple(segments[: last_array + 1])
    leaf_segs = segments[last_array + 1 :]
    if not leaf_segs:
        raise ApiInputSchemaError(
            "v2 column path names no leaf field (it ends at an array iterator)",
            column_path=column_path,
        )
    leaf = ".".join(key for key, _is_array in leaf_segs)
    return locating, leaf


def parse_column_path(column_path: str, table_path: str) -> str:
    """Return the column's dotted leaf, after checking it sits under the table.

    A *normal* (descendant) column is sourced at its table's own array level —
    its locating segments equal the table's. An *ancestor column* (W1) is
    sourced at a proper-ancestor array level — its locating segments are a
    (segment-wise) prefix of the table's, and its leaf is distributed over the
    table's rows. Either way this returns the dotted leaf resolved within the
    source node.

    For a table at ``"$[:].drivers[:]"``: ``"$[:].drivers[:].driver_id"``
    returns ``"driver_id"``; the dotted descendant
    ``"$[:].drivers[:].profile.age"`` returns ``"profile.age"``; the ancestor
    column ``"$[:].policy_id"`` (sourced at root) returns ``"policy_id"``.

    Raises :class:`ApiInputSchemaError` unless the column's locating segments
    are a prefix of the table's. A column rooted deeper than the table, in a
    sibling branch, or reached through a different object chain is rejected.
    """
    locating, leaf = parse_column_path_full(column_path)
    table_segments = parse_table_path(table_path)
    if tuple(locating) != tuple(table_segments[: len(locating)]):
        raise ApiInputSchemaError(
            "v2 column path is neither under its table path nor under a proper ancestor of it",
            column_path=column_path,
            table_path=table_path,
        )
    return leaf


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_v2_schema(config: dict[str, Any]) -> None:
    """Raise :class:`ApiInputSchemaError` if *config* violates v2 invariants.

    Invariants (§4d):
    - ``tables`` is a list of dicts.
    - Each table has a non-empty string ``label``; labels are unique
      WITHIN the ``tables`` list.
    - Each table has a parseable ``path`` (via :func:`parse_table_path`).
    - Each table's ``columns[].name`` values are unique within that
      table.
    - Each column's ``path`` parses cleanly via
      :func:`parse_column_path`.
    - When ``row_id_column`` is non-null on a table, it references a
      column ``name`` in the same table.
    - When ``levels`` is set on a column, it's a list (with at least one
      entry). Empty list is rejected — use ``null`` for "no domain".

    Guardrails added at v1-removal time:
    - **B1** — each column's ``type`` value must be one of
      ``int|float|str|bool|date``. Today's silent downgrade to ``str``
      (in ``_json_shred.py``) loses information; loud-fail at validate
      time forces the user to correct typos before the cache build.
    - **B2** — two table labels whose filesystem-safe sanitisation
      produces the same parquet filename are rejected. Without this,
      ``build_per_port_cache`` silently overwrites: the second parquet
      clobbers the first.
    - **B3** — every error path raises :class:`ApiInputSchemaError`
      (subclass of :class:`HauteError`). The JSON cache route catches
      specifically and returns a structured 422.
    """
    if not is_v2_shape(config):
        raise ApiInputSchemaError("config is not in v2 shape (no `tables` key)")

    tables = config[_V2_TABLES_KEY]
    if not isinstance(tables, list):
        raise ApiInputSchemaError("v2 `tables` must be a list")

    seen_labels: set[str] = set()
    # B2: map sanitised(label) -> first label that produced it, so we can
    # report a useful collision message rather than just "duplicate".
    sanitised_to_label: dict[str, str] = {}

    for ti, table in enumerate(tables):
        if not isinstance(table, dict):
            raise ApiInputSchemaError(f"v2 tables[{ti}] is not a dict")
        path = table.get("path")
        if not isinstance(path, str) or not path:
            raise ApiInputSchemaError(
                f"v2 tables[{ti}].path is missing or not a non-empty string",
            )
        parse_table_path(path)  # raises on malformed
        label = table.get("label")
        if not isinstance(label, str) or not label:
            raise ApiInputSchemaError(
                f"v2 tables[{ti}].label is missing or not a non-empty string "
                "(must be unique within the apiInput)",
            )
        if label in seen_labels:
            raise ApiInputSchemaError(
                f"v2 table label {label!r} appears more than once",
            )
        seen_labels.add(label)

        # B2 — sanitised-label collision check.
        sanitised = sanitise_label_for_filesystem(label)
        prior = sanitised_to_label.get(sanitised)
        if prior is not None:
            raise ApiInputSchemaError(
                "v2 table labels collide under filesystem-safe sanitisation "
                "(both produce the same parquet filename) — pick distinct "
                "labels that differ in their letters/digits/underscores/hyphens",
                label_a=prior,
                label_b=label,
                sanitised=sanitised,
            )
        sanitised_to_label[sanitised] = label

        columns = table.get("columns", [])
        if not isinstance(columns, list):
            raise ApiInputSchemaError(
                f"v2 tables[{ti}].columns is not a list",
            )
        seen_col_names: set[str] = set()
        for ci, col in enumerate(columns):
            if not isinstance(col, dict):
                raise ApiInputSchemaError(
                    f"v2 tables[{ti}].columns[{ci}] is not a dict",
                )
            cname = col.get("name")
            if not isinstance(cname, str) or not cname:
                raise ApiInputSchemaError(
                    f"v2 tables[{ti}].columns[{ci}].name is missing or not a non-empty string",
                )
            if cname in seen_col_names:
                raise ApiInputSchemaError(
                    f"v2 tables[{ti}] (label={label!r}) has duplicate column name {cname!r}",
                )
            seen_col_names.add(cname)
            cpath = col.get("path")
            if not isinstance(cpath, str) or not cpath:
                raise ApiInputSchemaError(
                    f"v2 tables[{ti}].columns[{ci}].path is missing or not a non-empty string",
                )
            parse_column_path(cpath, path)  # raises if it doesn't sit under the table path

            # B1 — unknown column type loud-fails.
            ctype = col.get("type")
            if not isinstance(ctype, str) or ctype not in _ALLOWED_COLUMN_TYPES:
                raise ApiInputSchemaError(
                    "v2 column type must be one of int|float|str|bool|date",
                    table=label,
                    column=cname,
                    type=ctype,
                )

            levels = col.get("levels")
            if levels is not None:
                if not isinstance(levels, list):
                    raise ApiInputSchemaError(
                        f"v2 tables[{ti}].columns[{ci}].levels must be a list "
                        f"or null (got {type(levels).__name__})",
                    )
                if len(levels) == 0:
                    raise ApiInputSchemaError(
                        f"v2 tables[{ti}].columns[{ci}].levels is empty; "
                        "use null to mean 'no declared domain'",
                    )

        row_id_column = table.get("row_id_column")
        if row_id_column is not None:
            if not isinstance(row_id_column, str) or not row_id_column:
                raise ApiInputSchemaError(
                    f"v2 tables[{ti}].row_id_column must be null or a non-empty string",
                )
            if row_id_column not in seen_col_names:
                raise ApiInputSchemaError(
                    f"v2 tables[{ti}].row_id_column={row_id_column!r} does not "
                    f"match any column.name in this table",
                    column_names=sorted(seen_col_names),
                )
