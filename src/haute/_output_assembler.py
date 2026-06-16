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
