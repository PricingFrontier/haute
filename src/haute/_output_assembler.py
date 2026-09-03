"""OUTPUT assembler — frames + field→path mapping → one nested JSON document.

Implements the *forced* algorithm of ``notes-haute`` →
``OUTPUT_ASSEMBLY_PROPERTIES.md``: GYO reduction finds uncovered cyclic cores;
a surgical, recursive cut severs their cycle carriers; the honoured remainder
is a bag natural join; cut rows co-locate as partial objects; serialisation
follows path prefixes. The cut is **schema-determined** (axiom A4) — it depends
only on the field assignments, never on the data values.

This module is the swappable assembler behind the stable boundary
``{tables + field→path map} → JSON document`` (D13). It is deliberately
field-agnostic: every field is treated identically, with no access to data
semantics (A5).

Vocabulary (kept to tables / fields / join-constraints throughout):

* a **table** is one source frame — a polars frame the OUTPUT node consumes;
* a **field** is a destination output path the table populates (a column is
  identified 1:1 with its destination path, A2/§1.2);
* two tables carrying the **same** field is a **join constraint** (A2); a field
  in exactly one table is **private** (rides along, joins nothing).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from haute._execution_context import ExecutionContext, current_execution_context
from haute._jsonpath import _ParsedPath, parse_path
from haute._polars_utils import execution_collect
from haute.errors import HauteError

_OUTPUT_ASSEMBLY_CHECKPOINT_ROWS = 1_024


class OutputMappingSchemaError(HauteError):
    """An OUTPUT node's mapping is structurally invalid.

    The output-side analogue of
    :class:`haute._api_input_schema.ApiInputSchemaError`: raised at
    config validation, save-time sidecar writing, the dry-run route, and
    deploy assemble time. Routes catch it and return a structured 422 with
    ``type="OutputMappingSchemaError"`` so the frontend discriminates on the
    type, not on string-matching the message.
    """


class OutputNestingKeyError(OutputMappingSchemaError):
    """A row cannot be placed beneath its parent because a nesting key is null."""

    code = "output_nesting_key_null"
    error_code = code
    public_fields = ("frame", "output_path", "key")

    def __init__(
        self,
        message: str,
        *,  # pragma: no mutate
        frame: str,
        output_path: str,
        key: str,
    ) -> None:
        self.frame = frame
        self.output_path = output_path
        self.key = key
        super().__init__(
            message,
            frame=frame,
            output_path=output_path,
            key=key,
        )


# ---------------------------------------------------------------------------
# The cut planner — schema-determined (A4), OUTPUT_ASSEMBLY_PROPERTIES §3–4
# ---------------------------------------------------------------------------
#
# Everything here depends only on which table carries which field — never on
# the data. It is computed once (at save, embedded) and reused at every run.


def _gyo_residue(tables: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """One GYO reduction to α-acyclicity (PROPERTIES §3.3).

    ``tables`` maps each table id to its field set. Returns the **residue**
    after repeatedly applying the two strip rules until neither fires:

    * **Rule 1 — drop a private field** (carried by ≤ 1 table): it joins
      nothing, so it cannot be part of a cycle.
    * **Rule 2 — drop a covered table** (its remaining fields are ⊆ another
      table's): it adds no join constraint the cover does not already carry.

    An **empty** residue means the constraints are α-acyclic — nothing
    obstructs serialising them as one faithful JSON tree, so nothing is cut. A
    **non-empty** residue is a *cyclic core*: a cycle of join constraints with
    no single covering table (the obstruction of §3). GYO is confluent, so the
    residue does not depend on the order the rules fire in.

    This finds *one* round's residue; the full planner re-runs it after each
    cut, because cutting a core's carriers can un-cover tables that were
    stripped as covered, exposing a fresh core (the §4.1 recursion).
    """
    work: dict[str, set[str]] = {t: set(fs) for t, fs in tables.items()}
    changed = True
    while changed:
        changed = False

        # field → set of tables carrying it
        incidence: dict[str, set[str]] = {}
        for t, fs in work.items():
            for f in fs:
                incidence.setdefault(f, set()).add(t)

        # Rule 1 — strip private fields (≤ 1 carrier).
        for f, carriers in incidence.items():
            if len(carriers) <= 1:
                for t in carriers:
                    work[t].discard(f)
                changed = True
        if changed:
            continue

        # A table emptied by Rule 1 carries no constraint — drop it.
        emptied = [t for t, fs in work.items() if not fs]
        for t in emptied:
            del work[t]
        if emptied:
            changed = True
            continue

        # Rule 2 — strip a covered table (fields ⊆ some other table's).
        ids = list(work)
        for a in ids:
            covered = any(a != b and work[a] <= work[b] for b in ids)
            if covered:
                del work[a]
                changed = True
                break

    return {t: frozenset(fs) for t, fs in work.items() if fs}


@dataclass(frozen=True)
class _Core:
    """One uncovered cyclic core found while planning the cut (§3.3, §4.1).

    ``tables`` are the core's tables (the GYO residue). Within it the
    **all-vs-some** split of §3.3 sorts the surviving fields:

    * ``parent_keys`` — fields carried by **every** core table. They *locate*
      the core's objects under one parent (prefix nesting, §4.5) but **do not
      merge** the core tables: they are kept, not cut, yet excluded from the
      honoured join (this is why a triangle under a common key `K` stays three
      separate objects rather than collapsing on `K`).
    * ``carriers`` — fields carried by **some** (a proper subset of) core
      tables. They are the genuine cycle obstruction and are **cut** (§4.2).
    """

    tables: frozenset[str]
    parent_keys: frozenset[str]
    carriers: frozenset[str]


@dataclass(frozen=True)
class _CutPlan:
    """The schema-determined plan the executor runs against data (A4, §4.1).

    * ``cores`` — the cyclic cores, in the order the recursion found them (the
      *covered* core first, then whatever it un-covers; see Window in the
      worked examples).
    * ``cuts`` — the severed ``(table, field)`` incidences: each core carrier,
      removed at the core tables that carry it and **left live everywhere else**
      (the surgical, per-(table, field) cut of §4.2). Cutting removes the
      *join* role, not the *value* — a cut row still emits all its source
      table's fields (§4.4).
    * ``merge_residue`` — the post-cut incidence the honoured bag natural join
      runs over: every table's fields minus the carriers cut at it minus the
      parent keys of any core it belongs to. Tables sharing a residual field
      merge (transitively); a table with no shared residual field stands alone
      as a partial object. Feed it to :func:`_merge_groups`.
    """

    cores: tuple[_Core, ...]
    cuts: frozenset[tuple[str, str]]
    merge_residue: dict[str, frozenset[str]] = field(default_factory=dict)


def _plan_cut(tables: dict[str, frozenset[str]]) -> _CutPlan:
    """Recursive surgical cut → a data-independent :class:`_CutPlan` (§4.1–§4.2).

    The loop is exactly §4.1 steps 1–3: find a cyclic core (``_gyo_residue``),
    split its parent keys (all-core) from its carriers (some-core), record the
    carrier cuts at the core tables, then **re-run on the full table set** with
    those carriers removed — because cutting a covered core's carriers can
    un-cover tables that GYO stripped, exposing a fresh core (the Window
    recursion). Repeat to α-acyclicity.

    Parent keys are deliberately **not** removed from ``work``: a lone parent
    key left on the core tables reduces to a star and strips out on the next GYO
    pass, so it never spuriously re-forms a core. Every surviving core has at
    least one carrier, so each round severs at least one incidence — the
    recursion is finite.
    """
    work: dict[str, set[str]] = {t: set(fs) for t, fs in tables.items()}
    cores: list[_Core] = []
    cuts: set[tuple[str, str]] = set()

    while True:
        residue = _gyo_residue({t: frozenset(fs) for t, fs in work.items()})
        if not residue:
            break

        core_tables = frozenset(residue)
        core_fields = frozenset().union(*residue.values())
        parent_keys = frozenset(f for f in core_fields if all(f in residue[t] for t in core_tables))
        carriers = core_fields - parent_keys
        cores.append(_Core(core_tables, parent_keys, carriers))

        # Surgical cut: sever each carrier at the core tables only.
        for t in core_tables:
            for f in carriers & frozenset(work[t]):
                cuts.add((t, f))
                work[t].discard(f)

    # The honoured-join incidence: drop cut carriers and the parent keys (which
    # nest, not merge) from every table they belong to.
    parent_keys_by_table: dict[str, set[str]] = {}
    for core in cores:
        for t in core.tables:
            parent_keys_by_table.setdefault(t, set()).update(core.parent_keys)
    cut_by_table: dict[str, set[str]] = {}
    for t, f in cuts:
        cut_by_table.setdefault(t, set()).add(f)

    merge_residue = {
        t: frozenset(fs) - cut_by_table.get(t, set()) - parent_keys_by_table.get(t, set())
        for t, fs in tables.items()
    }

    return _CutPlan(cores=tuple(cores), cuts=frozenset(cuts), merge_residue=merge_residue)


def _merge_groups(residue: dict[str, frozenset[str]]) -> list[frozenset[str]]:
    """Honoured-merge groups: tables joined (transitively) by a shared field.

    Connected components of the graph whose tables are linked when they share
    any field in *residue* (a :class:`_CutPlan.merge_residue`). Each component
    is one honoured bag natural join (§4.3); a singleton is a table that joins
    nothing and stands alone as a partial object (§4.4).
    """
    parent: dict[str, str] = {t: t for t in residue}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    carriers: dict[str, list[str]] = {}
    for t, fs in residue.items():
        for f in fs:
            carriers.setdefault(f, []).append(t)
    for members in carriers.values():
        for other in members[1:]:
            union(members[0], other)

    groups: dict[str, set[str]] = {}
    for t in residue:
        groups.setdefault(find(t), set()).add(t)
    return [frozenset(g) for g in groups.values()]


# ---------------------------------------------------------------------------
# Serialisation — prefix-nest the flat frame into a JSON document (§4.5)
# ---------------------------------------------------------------------------
#
# The flat frame's columns are *output paths*; each row is a partial object.
# Serialisation rebuilds the JSON tree from the path PREFIXES (not the join
# structure): an object at ``$[:].obj[:].A`` nests inside the array
# ``$[:].obj[:]``, under the root array ``$[:]``. This is the swappable
# serialiser behind a stable boundary — a polars struct-column variant could be
# A/B-tested against it later (Q1) — and is reusable per source frame (pass one
# table's rows + its own paths) to render a per-table JSON view.


def _parse_output_path(raw: str) -> _ParsedPath:
    """Parse an output path through the shared grammar core (PATH_GRAMMAR.md).

    A thin OUTPUT-side wrapper over :func:`haute._jsonpath.parse_path`: it injects
    :class:`OutputMappingSchemaError` so a rejected selector raises the type
    OUTPUT routes discriminate on, while the grammar (the accepted subset, the
    rejections, and messages) lives once in the shared core.
    """
    return parse_path(raw, OutputMappingSchemaError)


def _set_nested(obj: dict[str, Any], keys: list[str], value: Any) -> None:
    """Set ``obj[keys[0]][keys[1]]…[keys[-1]] = value``, creating dicts en route."""
    for k in keys[:-1]:
        child = obj.get(k)
        if not isinstance(child, dict):
            child = {}
            obj[k] = child
        obj = child
    obj[keys[-1]] = value


def _array_prefix(parsed: _ParsedPath) -> tuple[str, ...]:
    """A path's position in the array tree — the names of its ``[:]`` segments."""
    return tuple(seg.name for seg in parsed.segments if seg.is_array)


def _own_subpath(parsed: _ParsedPath) -> list[str]:
    """The object-key path of a leaf *within* its own array element.

    The segment names after the last ``[:]`` — e.g. ``attrs.X`` under
    ``$[:].obj[:].attrs.X`` gives ``["attrs", "X"]``; a bare ``$[:].K`` gives
    ``["K"]``.
    """
    last_array = max((i for i, seg in enumerate(parsed.segments) if seg.is_array), default=-1)
    return [seg.name for seg in parsed.segments[last_array + 1 :]]


def _identity(value: Any) -> Any:
    """Canonicalise one leaf value into a hashable identity component.

    An object's identity is the tuple of its own leaf values, so every value that
    can reach a dict key must be hashable. Scalars (including ``None``,
    ``Decimal``, dates, ``bytes``) are already hashable and pass through
    unchanged — in particular ``None`` keeps behaving exactly as before. A
    container leaf carries Python ``list`` (``List``/``Array``) or ``dict``
    (``Struct``) values, which are canonicalised recursively into tuples.

    Struct fields arrive in the dtype's field order, which polars keeps stable
    across rows, so the ``dict`` form is **not** sorted: insertion order is
    already canonical, and preserving it keeps the identity aligned with the
    declared schema. Polars never yields sets, so no set case exists.
    """
    if isinstance(value, dict):
        return tuple((k, _identity(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_identity(v) for v in value)
    return value


def _group_rows(
    rows: list[dict[str, Any]],
    keys: list[str],
    *,  # pragma: no mutate
    on_row: Callable[[], None] | None = None,  # pragma: no mutate
) -> list[list[dict[str, Any]]]:
    """Group rows by their values at *keys* (an object's identity), order-preserving."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    order: list[tuple[Any, ...]] = []
    for r in rows:
        if on_row is not None:
            on_row()
        k = tuple(_identity(r.get(key)) for key in keys)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    return [groups[k] for k in order]


def _index_rows(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,  # pragma: no mutate
    on_row: Callable[[], None] | None = None,  # pragma: no mutate
) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    """Index rows by *keys*, preserving the source order within every bucket."""
    index: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        if on_row is not None:
            on_row()
        index.setdefault(tuple(_identity(row.get(key)) for key in keys), []).append(row)
    return index


def _prune(value: Any, *, on_value: Callable[[], None] | None = None) -> Any:  # pragma: no mutate
    """Recursively drop absent structure (the Q1 null-prune + empty-collection rule).

    An **empty collection carries no data**, so it is omitted (Nick's ruling,
    2026-06-16): this refines the round-trip invariant to equality *up to empty
    collections* (an input empty array/object does not survive the trip). Hence:

    * a null is an absent field (H3: nulls never match, so no genuine null
      reaches here) → its key is dropped;
    * an empty **array** is omitted (S21);
    * an empty **object** is omitted too — both as a dropped key and as a
      dropped array element (a co-located leftover that carried nothing). This
      supersedes the older PATH_NOTATION §3 "singular zero-row is ``{}``".
    """
    if on_value is not None:
        on_value()
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            pv = _prune(v, on_value=on_value)
            if pv is None:
                continue
            if isinstance(pv, (list, dict)) and not pv:
                continue  # omit empty collections — they carry no data
            out[k] = pv
        return out
    if isinstance(value, list):
        kept: list[Any] = []
        for v in value:
            pv = _prune(v, on_value=on_value)
            if isinstance(pv, dict) and not pv:
                continue  # drop an empty-object leftover element
            kept.append(pv)
        return kept
    return value


@dataclass(slots=True)
class _OutputAssemblyProgress:
    """Bound the distance between cancellation and RSS checks in Python assembly."""

    execution_context: ExecutionContext | None  # pragma: no mutate
    rows_since_checkpoint: int = 0

    def checkpoint(self, label: str) -> None:
        if self.execution_context is not None:
            self.execution_context.checkpoint(label=label)
        self.rows_since_checkpoint = 0

    def advance(self, label: str) -> None:
        if self.execution_context is None:
            return
        self.rows_since_checkpoint += 1
        if self.rows_since_checkpoint >= _OUTPUT_ASSEMBLY_CHECKPOINT_ROWS:
            self.checkpoint(label)


def _collect_output_frame(
    frame: pl.LazyFrame,
    execution_context: ExecutionContext | None,  # pragma: no mutate
) -> pl.DataFrame:
    """Materialise one terminal OUTPUT plan through the shared execution seam."""
    return execution_collect(
        frame,
        execution_context=execution_context,
        engine="streaming",
    )


def _rows_from_dataframe(
    frame: pl.DataFrame,
    *,  # pragma: no mutate
    progress: _OutputAssemblyProgress,
    marker_errors: dict[str, tuple[str, str]],
    label: str = "output_assembly_rows",
) -> list[dict[str, Any]]:
    """Convert once to the retained row form while checking internal null markers."""
    progress.checkpoint(label)
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        for marker, (port, key) in marker_errors.items():
            if row.pop(marker):
                raise OutputNestingKeyError(
                    "a parent-to-child nesting key cannot be null",
                    frame=port,
                    output_path=key,
                    key=key,
                )
        rows.append(row)
        progress.advance(label)
    progress.checkpoint(label)
    return rows


def _assemble_document(field_frames: dict[str, pl.LazyFrame]) -> list[Any]:
    """Assemble the nested JSON document by descending the path-prefix TREE (§4.5).

    Each source frame's columns are its output paths. A frame *emits* objects at
    its deepest array prefix and carries shallower (ancestor) keys for nesting —
    the inverse of the W1 shred, which pushed those ancestor keys down. We descend
    the array-prefix tree: at each node the emitting frame's rows become that
    level's objects (or, where several frames share the node, the cut-planned bag
    join of §4.1–4.4), and each child array is assembled **independently** and
    nested under its parent by matching the ancestor keys.

    Sibling branches are never joined — ``drivers`` and ``vehicles`` meet only at
    their shared ancestor key, which *nests*, it does not cross-multiply. A tree
    of width *w* therefore costs the **sum** of its branch sizes, not the product:
    no cross-branch blow-up (the 2×2×3 denormalisation the data model names as
    arithmetically meaningless). This is the default, swappable serialiser;
    running it on a single frame renders that frame's own JSON view.
    """
    execution_context = current_execution_context()
    progress = _OutputAssemblyProgress(execution_context)
    progress.checkpoint("output_assembly_schema")
    port_paths: dict[str, dict[str, _ParsedPath]] = {}
    for port, frame in field_frames.items():
        columns = frame.collect_schema().names()
        port_paths[port] = {column: _parse_output_path(column) for column in columns}
    progress.checkpoint("output_assembly_schema")

    all_paths: dict[str, _ParsedPath] = {c: p for pp in port_paths.values() for c, p in pp.items()}
    emit_prefix: dict[str, tuple[str, ...]] = {
        port: max((_array_prefix(p) for p in pp.values()), key=len, default=())
        for port, pp in port_paths.items()
    }

    # The array-prefix tree: every frame's emit prefix and all its ancestors.
    nodes: set[tuple[str, ...]] = set()
    for pref in emit_prefix.values():
        for i in range(len(pref) + 1):
            nodes.add(pref[:i])
    ports_at: dict[tuple[str, ...], list[str]] = {n: [] for n in nodes}
    for port, pref in emit_prefix.items():
        ports_at[pref].append(port)
    # Paths carried at or below each node — the keys available to match a child up.
    carries: dict[tuple[str, ...], set[str]] = {n: set() for n in nodes}
    for port, pref in emit_prefix.items():
        for n in nodes:
            if pref[: len(n)] == n:
                carries[n].update(port_paths[port])

    # A parent object can only nest a child under values it emits at this level
    # and the child subtree also carries. Null is not an identity: accepting it
    # would co-locate unrelated partial rows under a fabricated ``None`` key.
    # Record each required non-null check now, while the schemas still distinguish
    # a key that is absent from one frame from a key whose value is genuinely null.
    nonnull_keys_by_port: dict[str, set[str]] = {port: set() for port in field_frames}
    for parent in nodes:
        parent_own = {c for c, parsed in all_paths.items() if _array_prefix(parsed) == parent}
        for child in (n for n in nodes if len(n) == len(parent) + 1 and n[: len(parent)] == parent):
            relation_keys = tuple(sorted(parent_own & carries[child]))
            if not relation_keys:
                continue
            parent_ports = sorted(p for p, pref in emit_prefix.items() if pref == parent)
            child_ports = sorted(
                p for p, pref in emit_prefix.items() if pref[: len(child)] == child
            )
            for port in parent_ports + child_ports:
                nonnull_keys_by_port[port].update(
                    key for key in relation_keys if key in port_paths[port]
                )

    # Attach private boolean markers before same-level joins. They retain the
    # originating frame for a null-key error without collecting every source once
    # for validation and then evaluating those same lazy sources again for the join.
    marker_errors_by_port: dict[str, dict[str, tuple[str, str]]] = {}
    planned_frames: dict[str, pl.LazyFrame] = {}
    marker_index = 0
    for port, frame in field_frames.items():
        marker_errors: dict[str, tuple[str, str]] = {}
        marker_exprs: list[pl.Expr] = []
        for key in sorted(nonnull_keys_by_port[port]):
            marker = f"__haute_output_null_key_{marker_index}"
            marker_index += 1
            marker_errors[marker] = (port, key)
            marker_exprs.append(pl.col(key).is_null().alias(marker))
        marker_errors_by_port[port] = marker_errors
        planned_frames[port] = frame.with_columns(marker_exprs) if marker_exprs else frame

    # Materialise the final plan for every emitting level exactly once. A group of
    # same-level sources is joined while it is still lazy, so no member is re-read.
    rows_by_prefix: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    emitting_prefixes = list(dict.fromkeys(emit_prefix.values()))
    for prefix in emitting_prefixes:
        port_list = ports_at[prefix]
        if len(port_list) == 1:
            output_plan = planned_frames[port_list[0]]
        else:
            incidence = {port: frozenset(port_paths[port]) for port in port_list}
            output_plan = _execute_plan(
                {port: planned_frames[port] for port in port_list},
                _plan_cut(incidence),
            )
        marker_errors = {
            marker: error
            for port in port_list
            for marker, error in marker_errors_by_port[port].items()
        }
        rows_by_prefix[prefix] = _rows_from_dataframe(
            _collect_output_frame(output_plan, execution_context),
            progress=progress,
            marker_errors=marker_errors,
        )

    def children_of(prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
        return sorted(n for n in nodes if len(n) == len(prefix) + 1 and n[: len(prefix)] == prefix)

    level_rows_cache: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    indexes: dict[
        tuple[tuple[str, ...], tuple[str, ...]], dict[tuple[Any, ...], list[dict[str, Any]]]
    ] = {}

    def level_rows_for(prefix: tuple[str, ...]) -> list[dict[str, Any]]:
        if prefix in level_rows_cache:
            return level_rows_cache[prefix]
        port_list = ports_at.get(prefix, [])
        if not port_list:
            # No frame emits here: synthesise this level from the ancestor keys its
            # descendants carry (a common parent key under which a cyclic core's
            # objects nest, with no table of its own — the triangle's K).
            level_rows = []
            for emitted_prefix in emitting_prefixes:
                if emitted_prefix[: len(prefix)] == prefix and len(emitted_prefix) > len(prefix):
                    descendant_rows = rows_by_prefix[emitted_prefix]
                    level_rows.extend(descendant_rows)
                    for _ in descendant_rows:
                        progress.advance("output_assembly_build")
        else:
            level_rows = rows_by_prefix[prefix]
        level_rows_cache[prefix] = level_rows
        return level_rows

    def scoped_rows(prefix: tuple[str, ...], scope: dict[str, Any]) -> list[dict[str, Any]]:
        if not scope:
            return level_rows_for(prefix)
        keys = tuple(sorted(scope))
        cache_key = (prefix, keys)
        if cache_key not in indexes:
            index_source = level_rows_for(prefix)
            if execution_context is None:
                indexes[cache_key] = _index_rows(index_source, keys)
            else:
                indexes[cache_key] = _index_rows(
                    index_source,
                    keys,
                    on_row=lambda: progress.advance("output_assembly_build"),
                )
        return indexes[cache_key].get(tuple(_identity(scope[key]) for key in keys), [])

    def build(prefix: tuple[str, ...], scope: dict[str, Any]) -> list[dict[str, Any]]:
        rows = scoped_rows(prefix, scope)

        own = [c for c, p in all_paths.items() if _array_prefix(p) == prefix]
        objects: list[dict[str, Any]] = []
        grouped_rows = _group_rows(
            rows,
            own,
            on_row=(
                (lambda: progress.advance("output_assembly_build"))
                if execution_context is not None
                else None
            ),
        )
        for grp in grouped_rows:
            progress.advance("output_assembly_build")
            obj: dict[str, Any] = {}
            for c in own:
                _set_nested(obj, _own_subpath(all_paths[c]), grp[0].get(c))
            for child in children_of(prefix):
                child_scope = dict(scope)
                child_scope.update({c: grp[0].get(c) for c in own if c in carries[child]})
                kids = build(child, child_scope)
                if kids:
                    _set_nested(obj, [child[-1]], kids)
            objects.append(obj)
        return objects

    progress.checkpoint("output_assembly_build")
    document: list[Any] = _prune(
        build((), {}),
        on_value=(
            (lambda: progress.advance("output_assembly_build"))
            if execution_context is not None
            else None
        ),
    )
    progress.checkpoint("output_assembly_build")
    return document


# ---------------------------------------------------------------------------
# The executor — runs the plan against data (OUTPUT_ASSEMBLY_PROPERTIES §4.3–4.4)
# ---------------------------------------------------------------------------
#
# This is the only place data values enter. Each table's frame is keyed by its
# *fields* (one column per destination path — a column duplicated to several
# paths appears once per path). Output is a flat frame of assembled partial
# objects; the prefix nesting into a JSON tree (§4.5) is a later step.


def _execute_plan(
    field_frames: dict[str, pl.LazyFrame],
    plan: _CutPlan,
) -> pl.LazyFrame:
    """Run the cut plan over data → one flat frame of assembled partial objects.

    For every honoured-merge group (:func:`_merge_groups`) the member frames are
    folded together by a **bag natural join** on their shared residual fields,
    ``how="full"`` so matches **fan out** (§4.3) and non-matching rows survive as
    **co-located partials** (§4.4). The groups are then stacked by a diagonal
    concat: a field a row does not carry is left null, which serialisation reads
    as an absent field (the Q1 null-prune). The result is flat — prefix nesting
    (§4.5) is a separate step.

    The honoured remainder is α-acyclic (the cycles were cut), so a connected
    join order always exists; we fold greedily, each step joining the next table
    on whatever residual fields it shares with everything folded so far. A
    singleton group is emitted as-is: its rows stand alone as partial objects,
    still carrying **all** their source fields — the cut removed the join role,
    not the value (§4.4).
    """
    residue = plan.merge_residue
    group_frames: list[pl.LazyFrame] = []

    for group in _merge_groups(residue):
        members = sorted(group)
        acc = field_frames[members[0]]
        acc_fields = set(residue.get(members[0], frozenset()))
        pending = members[1:]

        while pending:
            # `_merge_groups` returns connected components, so every partial
            # fold must have another member that overlaps its accumulated
            # fields. A Cartesian fallback would hide a corrupt plan and could
            # amplify rows catastrophically at this materialisation boundary.
            try:
                pick = next(m for m in pending if residue.get(m, frozenset()) & acc_fields)
            except StopIteration:
                raise RuntimeError("output assembly join plan is disconnected") from None
            pending.remove(pick)
            keys = sorted(residue.get(pick, frozenset()) & acc_fields)
            nxt = field_frames[pick]
            acc = acc.join(
                nxt,
                on=keys,
                how="full",
                coalesce=True,
                maintain_order="left_right",
            )
            acc_fields |= set(residue.get(pick, frozenset()))

        group_frames.append(acc)

    if not group_frames:
        return pl.LazyFrame()
    if len(group_frames) == 1:
        return group_frames[0]
    return pl.concat(group_frames, how="diagonal")


# ---------------------------------------------------------------------------
# Public boundary — {frames + outputMapping} → JSON document (D13)
# ---------------------------------------------------------------------------


def is_active_mapping_entry(entry: dict[str, Any]) -> bool:
    """Whether a mapping entry contributes — enabled AND fully filled in.

    An entry with a blank ``source_column`` or ``output_path`` is a row still
    being built in the editor (e.g. a manually-added row before its source
    column is picked). Such an incomplete entry is SKIPPED everywhere it is
    consumed — the column contract, assembly, and validation — so a
    half-finished mapping never demands a ``""`` column (the confusing
    ``missing=['']`` contract failure) or crashes ``pl.col("")``. The editor
    surfaces the incomplete row separately; the runtime simply ignores it.
    """
    if not entry["enabled"]:
        return False
    source_column = entry.get("source_column", "")
    output_path = entry.get("output_path", "")
    return (
        isinstance(source_column, str)
        and isinstance(output_path, str)
        and bool(source_column.strip())
        and bool(output_path.strip())
    )


def validate_v2_output_mapping(mapping: list[dict[str, Any]]) -> None:
    """Validate an ``outputMapping`` structurally — schema-only, loud (A4).

    Fires on the mapping regardless of data (STATE_OF_PLAY §4 B2): the fastest,
    loudest failure for testing. Checks (raising :class:`OutputMappingSchemaError`):

    * every ``output_path`` parses in the accepted ``[:]``-only subset (§2);
    * **injectivity** — within one source frame, no two *different* columns map to
      the same path (§1.2);
    * **pairwise prefix-incomparability** — within one source frame, no two
      distinct paths are prefix-comparable (B1);
    * **one output branch per frame** — every path for one source frame has a
      prefix-comparable array prefix.

    Type-consistency across a shared path (§1.3) is **not** checked here — it
    needs the input frames' column types, which the caller supplies separately at
    assemble/save time.
    """
    by_port: dict[str, list[tuple[str, str]]] = {}
    parsed_by_path: dict[str, _ParsedPath] = {}

    def _cached_parse(path: str) -> _ParsedPath:
        if path not in parsed_by_path:
            parsed_by_path[path] = _parse_output_path(path)
        return parsed_by_path[path]

    for entry in mapping:
        if not is_active_mapping_entry(entry):
            continue
        path = entry["output_path"]
        _cached_parse(path)  # grammar — raises on a rejected selector
        by_port.setdefault(entry["source_port"], []).append((entry["source_column"], path))

    for port, entries in by_port.items():
        path_to_col: dict[str, str] = {}
        for col, path in entries:
            if path in path_to_col and path_to_col[path] != col:
                raise OutputMappingSchemaError(
                    "two columns of one source frame map to the same output path",
                    source_port=port,
                    output_path=path,
                )
            path_to_col[path] = col

        distinct = sorted(
            dict.fromkeys(path for _, path in entries),
            key=lambda path: _cached_parse(path).segments,
        )
        for a, b in zip(distinct, distinct[1:], strict=False):
            a_segments = _cached_parse(a).segments
            b_segments = _cached_parse(b).segments
            if a_segments == b_segments[: len(a_segments)]:
                raise OutputMappingSchemaError(
                    "output paths within a source frame must be pairwise "
                    "prefix-incomparable (a leaf cannot also be a container)",
                    source_port=port,
                    output_path=f"{a} vs {b}",
                )

        prefixes = [(path, _array_prefix(_cached_parse(path))) for path in distinct]
        prefixes.sort(key=lambda item: item[1])
        for (a, a_prefix), (b, b_prefix) in zip(prefixes, prefixes[1:], strict=False):
            if not (a_prefix[: len(b_prefix)] == b_prefix or b_prefix[: len(a_prefix)] == a_prefix):
                raise OutputMappingSchemaError(
                    "one source frame cannot emit into divergent array branches",
                    source_port=port,
                    output_path=f"{a} vs {b}",
                )


def assemble_output_from_mapping(
    frames: dict[str, pl.LazyFrame], mapping: list[dict[str, Any]]
) -> list[Any]:
    """Assemble the OUTPUT JSON document from source frames + an ``outputMapping``.

    The stable assembler boundary (D13): each mapping entry renames a source
    column to its destination ``output_path`` (a column duplicated to several
    paths appears once per path; disabled entries are skipped), giving one
    field-frame per source frame, which :func:`_assemble_document` nests by prefix
    into the document. Returns the document (a list of top-level objects). The
    swappable serialiser is the Python nester (Q1); a polars struct-column
    variant can replace ``_assemble_document`` behind this same boundary.
    """
    validate_v2_output_mapping(mapping)

    by_port: dict[str, list[dict[str, Any]]] = {}
    for entry in mapping:
        if not is_active_mapping_entry(entry):
            continue
        by_port.setdefault(entry["source_port"], []).append(entry)

    field_frames: dict[str, pl.LazyFrame] = {
        port: frames[port].select(
            [pl.col(e["source_column"]).alias(e["output_path"]) for e in entries]
        )
        for port, entries in by_port.items()
    }
    return _assemble_document(field_frames)


def _document_dtype(node: Any) -> pl.DataType:
    """Convert one derived nesting node into its polars dtype.

    A leaf is already a ``pl.DataType``; a ``dict`` is an object level
    (``Struct``, fields in insertion order); a ``("list", dict)`` marker is an
    array level (``List(Struct(...))``).
    """
    if isinstance(node, tuple):
        return pl.List(pl.Struct({k: _document_dtype(v) for k, v in node[1].items()}))
    if isinstance(node, dict):
        return pl.Struct({k: _document_dtype(v) for k, v in node.items()})
    assert isinstance(node, pl.DataType)
    return node


def output_document_schema(
    source_schemas: Mapping[str, pl.Schema],
    mapping: list[dict[str, Any]],
) -> pl.Schema:
    """Derive the OUTPUT document's schema from the mapping + source schemas.

    The **single schema authority** for both OUTPUT paths: the schema-only build
    returns ``pl.LazyFrame(schema=output_document_schema(...))`` without
    assembling, and the collected build declares this same schema over the
    assembled document instead of letting Python inference decide dtypes. So a
    schema-only execution and a collected execution report the identical schema,
    an all-null column keeps its source dtype, and an empty document keeps its
    columns.

    The derivation mirrors :func:`_assemble_document` exactly (it is the
    schema-level shadow of that function's ``build``):

    * a leaf's dtype is its source column's dtype;
    * the nesting node of a level is its array prefix (:func:`_array_prefix`);
      the columns *owned* by a level are those whose array prefix equals it, in
      ``all_paths`` order (ports in mapping order, columns in mapping order);
    * each owned column is placed at :func:`_own_subpath` — its keys *within*
      its own array element, so an ancestor key carried by a deeper frame for
      matching is emitted at the level it belongs to and never re-emitted
      inside the child element;
    * each child array is attached under its own final name, after the level's
      own fields, children in sorted order — where ``_set_nested`` puts it.

    Raises :class:`OutputMappingSchemaError` when the mapping is structurally
    invalid, when a referenced source port or column is absent, or when two
    entries map the same output path from source columns of different dtypes.
    """
    validate_v2_output_mapping(mapping)

    by_port: dict[str, list[dict[str, Any]]] = {}
    for entry in mapping:
        if not is_active_mapping_entry(entry):
            continue
        by_port.setdefault(entry["source_port"], []).append(entry)

    # all_paths / leaf dtypes in the assembler's order: ports in mapping order,
    # each port's columns in mapping order (the order `select(...)` aliases them).
    all_paths: dict[str, _ParsedPath] = {}
    leaf_dtypes: dict[str, pl.DataType] = {}
    port_paths: dict[str, list[str]] = {}
    for port, entries in by_port.items():
        if port not in source_schemas:
            raise OutputMappingSchemaError(
                f"OUTPUT mapping references source frame {port!r}, which no "
                f"incoming frame provides; available frames: "
                f"{sorted(source_schemas)!r}.",
                source_port=port,
            )
        schema = source_schemas[port]
        paths: list[str] = []
        for entry in entries:
            column = entry["source_column"]
            path = entry["output_path"]
            if column not in schema:
                raise OutputMappingSchemaError(
                    f"OUTPUT mapping reads column {column!r} for output path "
                    f"{path!r}, which source frame {port!r} does not provide; "
                    f"available columns: {list(schema)!r}.",
                    source_port=port,
                    output_path=path,
                )
            dtype = schema[column]
            if path in leaf_dtypes and leaf_dtypes[path] != dtype:
                raise OutputMappingSchemaError(
                    f"output path {path!r} is mapped from source columns of "
                    f"different types ({leaf_dtypes[path]!r} and {dtype!r}); "
                    f"a shared path must carry one type.",
                    source_port=port,
                    output_path=path,
                )
            leaf_dtypes[path] = dtype
            all_paths.setdefault(path, _parse_output_path(path))
            if path not in paths:
                paths.append(path)
        port_paths[port] = paths

    emit_prefix: dict[str, tuple[str, ...]] = {
        port: max((_array_prefix(all_paths[p]) for p in paths), key=len, default=())
        for port, paths in port_paths.items()
    }
    nodes: set[tuple[str, ...]] = set()
    for pref in emit_prefix.values():
        for i in range(len(pref) + 1):
            nodes.add(pref[:i])

    def level(prefix: tuple[str, ...]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for path, parsed in all_paths.items():
            if _array_prefix(parsed) == prefix:
                _set_nested(obj, _own_subpath(parsed), leaf_dtypes[path])
        children = sorted(
            n for n in nodes if len(n) == len(prefix) + 1 and n[: len(prefix)] == prefix
        )
        for child in children:
            _set_nested(obj, [child[-1]], ("list", level(child)))
        return obj

    return pl.Schema({key: _document_dtype(value) for key, value in level(()).items()})


def render_output_document(df: pl.DataFrame) -> list[Any]:
    """Render an OUTPUT node's collected frame as the response JSON document.

    ``_build_output`` returns ``pl.LazyFrame(document)`` so every render point
    (canvas preview, deploy response) handles a frame uniformly. polars stores a
    ragged document by **null-filling** it to a uniform struct schema; this
    strips that padding back out via the Q1 null-prune + empty-collection rule,
    so the rendered JSON equals the assembled document — equality "up to empty
    collections". For a flat OUTPUT (no nesting, no nulls) this is a no-op.
    """
    progress = _OutputAssemblyProgress(current_execution_context())
    rows = _rows_from_dataframe(
        df,
        progress=progress,
        marker_errors={},
        label="output_render_rows",
    )
    progress.checkpoint("output_render_prune")
    document: list[Any] = _prune(
        rows,
        on_value=(
            (lambda: progress.advance("output_render_prune"))
            if progress.execution_context is not None
            else None
        ),
    )
    progress.checkpoint("output_render_prune")
    return document
