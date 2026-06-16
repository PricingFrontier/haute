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

* a **table** is one source port — a polars frame the OUTPUT node consumes;
* a **field** is a destination output path the table populates (a column is
  identified 1:1 with its destination path, A2/§1.2);
* two tables carrying the **same** field is a **join constraint** (A2); a field
  in exactly one table is **private** (rides along, joins nothing).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from haute.errors import HauteError


class OutputMappingSchemaError(HauteError):
    """An OUTPUT node's v2 mapping is structurally invalid.

    The output-side analogue of
    :class:`haute._api_input_schema.ApiInputSchemaError`: raised at
    config-validation, save-time sidecar compaction, the dry-run route, and
    deploy assemble time. Routes catch it and return a structured 422 with
    ``type="OutputMappingSchemaError"`` so the frontend discriminates on the
    type, not on string-matching the message.
    """


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
