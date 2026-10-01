"""The value-free change card of a saved plan, and the compact plan views beside it.

A change card says what a plan changed, never what it changed it to: it is
built only from node ids, node types, configuration keys, step kinds and edge
endpoints. The dry-run result, the apply result and the stream event all read
these builders, so the model and the analyst see one description of a plan.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from typing import TYPE_CHECKING, Any

from haute._polars_steps import STEP_KINDS
from haute._types import GraphNode, PipelineGraph
from haute.assistant._catalog import capability_manifest
from haute.assistant._ops import PlanReceipt, SemanticDiff
from haute.schemas import (
    ASSISTANT_MAX_DATA_FINDINGS,
    AssistantChangeDataCheck,
    AssistantChangeEdge,
    AssistantChangeNode,
    AssistantChangeRecord,
    AssistantDataFinding,
    AssistantGraphChanges,
)

if TYPE_CHECKING:
    # The data check imports the application layer, which imports this module.
    from haute.assistant._data_check import DataCheckResult, FindingsVisibility

#: The most node chips, and edges each way, one card lists.
CHANGE_LIST_LIMIT = 50
#: The longest change headline, the Git commit subject of an apply, in characters.
CHANGE_HEADLINE_LIMIT = 100
#: A configuration-change key naming one step: `steps[<id>]` or `steps[<id>].<field>`.
_STEP_KEY = re.compile(r"^steps\[(?P<id>[^\]]+)\]")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@cache
def _palette_names() -> Mapping[str, str]:
    return {node.id: node.display_name for node in capability_manifest().nodes}


def field_words(key: str) -> str:
    """A configuration key in plain lower-case words: `outputColumn` reads `output column`."""

    return " ".join(_CAMEL_BOUNDARY.sub(" ", key).replace("_", " ").lower().split())


def _step_kinds(node: GraphNode) -> list[str] | None:
    """The node's step kinds in order, or None for a node without a step list."""

    steps = node.data.config.get("steps")
    if not isinstance(steps, list):
        return None
    return [
        kind
        if isinstance(step, Mapping)
        and isinstance(kind := step.get("kind"), str)
        and kind in STEP_KINDS
        else "unknown"
        for step in steps
    ]


def _pairs(identities: Sequence[tuple[str, str, str | None, str | None]]) -> list[tuple[str, str]]:
    return list(dict.fromkeys((source, target) for source, target, _out, _in in identities))


def _edges(pairs: Sequence[tuple[str, str]]) -> list[AssistantChangeEdge]:
    return [AssistantChangeEdge(source=source, target=target) for source, target in pairs]


def graph_changes(
    before: PipelineGraph, after: PipelineGraph, diff: SemanticDiff
) -> AssistantGraphChanges:
    """The node chips and edges of *diff* between *before* and *after*.

    Chips follow the after graph's order, removed nodes last in the before
    graph's order. A node the plan added lists no fields: all of it is new. A
    renamed node is one chip, not a removal and an addition, and an edge that
    only follows a rename is not listed.
    """

    changes = diff.complete
    renamed_from = {new: old for old, new in changes.nodes_renamed}
    current_id = {old: new for old, new in changes.nodes_renamed}
    fields: dict[str, list[str]] = {}
    steps_changed: dict[str, set[str]] = {}
    for identity in changes.config_changes:
        raw_node, _, key = identity.partition(":")
        node_id = current_id.get(raw_node, raw_node)
        step = _STEP_KEY.match(key)
        if step is not None:
            steps_changed.setdefault(node_id, set()).add(step["id"])
            continue
        words = field_words(key)
        if words not in fields.setdefault(node_id, []):
            fields[node_id].append(words)
    added = set(changes.nodes_added) - renamed_from.keys()
    removed = set(changes.nodes_removed) - current_id.keys()
    changed = {current_id.get(node, node) for node in changes.nodes_updated}
    changed.update(fields, steps_changed)
    names = _palette_names()

    chips: list[AssistantChangeNode] = []
    for node in after.nodes:
        if node.id in added:
            chips.append(
                AssistantChangeNode(
                    id=node.id,
                    type=names[node.data.nodeType.value],
                    change="added",
                    steps=_step_kinds(node),
                )
            )
        elif node.id in renamed_from or node.id in changed:
            chips.append(
                AssistantChangeNode(
                    id=node.id,
                    type=names[node.data.nodeType.value],
                    change="renamed" if node.id in renamed_from else "changed",
                    renamed_from=renamed_from.get(node.id),
                    fields=fields.get(node.id, []),
                    steps=_step_kinds(node),
                    steps_changed=len(steps_changed.get(node.id, ())),
                )
            )
    chips.extend(
        AssistantChangeNode(id=node.id, type=names[node.data.nodeType.value], change="removed")
        for node in before.nodes
        if node.id in removed
    )
    added_pairs = _pairs(changes.edges_added)
    removed_pairs = _pairs(changes.edges_removed)
    followed = {
        pair
        for pair in removed_pairs
        if (current_id.get(pair[0], pair[0]), current_id.get(pair[1], pair[1])) in added_pairs
    }
    renamed_pairs = {(current_id.get(s, s), current_id.get(t, t)) for s, t in followed}
    edges_added = _edges([pair for pair in added_pairs if pair not in renamed_pairs])
    edges_removed = _edges([pair for pair in removed_pairs if pair not in followed])
    return AssistantGraphChanges(
        nodes=chips[:CHANGE_LIST_LIMIT],
        edges_added=edges_added[:CHANGE_LIST_LIMIT],
        edges_removed=edges_removed[:CHANGE_LIST_LIMIT],
        preamble_changed=diff.preamble_changed,
        truncated=any(
            len(items) > CHANGE_LIST_LIMIT for items in (chips, edges_added, edges_removed)
        ),
    )


def change_record(
    plan_hash: str,
    receipt: PlanReceipt,
    changes: AssistantGraphChanges,
    *,
    warnings: Sequence[str],
    git_sha: str | None,
    parent_sha: str | None,
    revision: str,
    data_check: AssistantChangeDataCheck | None,
) -> AssistantChangeRecord:
    """The change card of one saved plan, identified by the plan's hash."""

    return AssistantChangeRecord(
        id=plan_hash,
        summary=receipt.summary,
        assumptions=list(receipt.assumptions),
        changes=changes,
        warnings=list(warnings),
        git_sha=git_sha,
        parent_sha=parent_sha,
        revision=revision,
        data_check=data_check,
    )


# ---------------------------------------------------------------------------
# The data check on a change card
# ---------------------------------------------------------------------------

#: Why a check did not run, as the card's "Data not checked:" line says it.
_NOT_RUN_WORDS: dict[str, str] = {
    "worker_mode_unsupported": "previews run inside the server, where a check cannot be stopped",
    "not_schema_tier": "the change has nothing to run",
    "no_checkable_nodes": "no changed node could be checked",
    "admission_refused": "the server had no memory to spare for it",
    "worker_busy": "the preview worker was busy",
    "deadline": "it took longer than 30 seconds",
    "memory_limited": "it needed more memory than a preview may use",
    "superseded": "a newer check replaced it",
    "superseded_by_preview": "a preview needed its worker",
    "cancelled": "the assistant was stopped",
    "source_changed": "an input changed while it ran",
    "internal_error": "the check failed unexpectedly",
}
#: The most nodes a "not checked" line names before counting the rest.
_NOT_CHECKED_NAMED = 5


def _not_checked_words(record: Mapping[str, Any]) -> str:
    """Why one changed node was not checked, with the step that would let it be."""

    blocking = record["blocking_node"]
    reason = record["reason"]
    if reason == "submodel":
        return "it is a submodel"
    if reason == "sink_only":
        return "it produces no data"
    if reason == "artifact_in_lineage":
        return f"it reads {blocking}, a Load File the check never opens"
    if reason == "artifact_not_local":
        if record["remedy"] is not None:
            return f"preview {blocking} first to cache its model"
        return f"{blocking} loads its model or artifact from MLflow"
    if reason == "input_not_prepared":
        return f"preview the input {blocking} first"
    if reason == "node_cap":
        return "only the first 8 changed nodes are checked"
    raise ValueError(f"unknown not-checked reason {reason!r}")


def _named_nodes(records: Sequence[Mapping[str, Any]]) -> str:
    named = [f"{record['node']} ({_not_checked_words(record)})" for record in records]
    rest = len(named) - _NOT_CHECKED_NAMED
    text = ", ".join(named[:_NOT_CHECKED_NAMED])
    return text if rest <= 0 else f"{text} and {rest} more"


def _not_checked_line(check: Mapping[str, Any]) -> str | None:
    """One line on what the check did not look at, or ``None`` when it looked at it all.

    A node that produces no data is named only when nothing was checked: a
    Quote Response or Data Output has nothing to measure.
    """

    if check["outcome"] == "not_run":
        if check["reason"] == "no_checkable_nodes" and check["nodes"]:
            return f"Data not checked: {_named_nodes(check['nodes'])}."
        return f"Data not checked: {_NOT_RUN_WORDS[check['reason']]}."
    skipped = [
        record
        for record in check["nodes"]
        if record["status"] == "not_checked" and record["reason"] != "sink_only"
    ]
    return f"Not checked: {_named_nodes(skipped)}." if skipped else None


def _rows(count: int) -> str:
    return f"{count:,} row" if count == 1 else f"{count:,} rows"


def _percent(share: float) -> str:
    """A share in percent to one decimal place, never rounded to 0% or 100%."""

    if 0 < share < 0.001:
        return "under 0.1%"
    if 0.999 < share < 1:
        return "over 99.9%"
    return f"{share * 100:.1f}".removesuffix(".0") + "%"


def _port(finding: Mapping[str, Any]) -> str:
    return "" if finding["port"] is None else f" in {finding['port']}"


def _error_words(finding: Mapping[str, Any]) -> str:
    """An execution failure by its type and where it raised, never its text."""

    error = finding["error"]
    where = ""
    if error["step"] is not None:
        where = f" in step {error['step']['number']}"
    elif error["line"] is not None:
        where = f" on line {error['line']}"
    if finding["at_or_upstream"]:
        return f"Failed to run, here or upstream: {error['type']}{where}."
    return f"Failed to run: {error['type']}{where}."


def _join_validation_words(finding: Mapping[str, Any]) -> str:
    duplicates = finding["duplicate_key_tuples"]
    sides = ("base", "join") if finding["side"] == "both" else (finding["side"],)
    repeats = " and ".join(f"{duplicates[side]:,} keys repeat on the {side} side" for side in sides)
    return f"The join's {finding['validate']} check fails: {repeats}."


def _rules_words(finding: Mapping[str, Any]) -> str:
    rules = [str(position + 1) for position in finding["rules"]]
    label = "Rule" if len(rules) == 1 and not finding["rules_omitted"] else "Rules"
    listed = ", ".join(rules)
    if finding["rules_omitted"]:
        listed += f" and {finding['rules_omitted']} more"
    return f"{label} {listed} of {finding['output_column']} matched no row."


#: Each finding kind in plain words, built from its counts and names only.
_FINDING_WORDS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "execution_failed": _error_words,
    "rows_emptied": lambda f: f"Produced no rows{_port(f)} from {_rows(f['input_rows'])} of input.",
    "banding_all_default": lambda f: (
        f"All {_rows(f['rows'])} fell into the default band of {f['output_column']}."
    ),
    "banding_mostly_default": lambda f: (
        f"{f['defaulted']:,} of {_rows(f['rows'])} ({_percent(f['share'])}) fell into "
        f"the default band of {f['output_column']}."
    ),
    "banding_rules_unclaimed": _rules_words,
    "rating_misses": lambda f: (
        f"{f['missed']:,} of {_rows(f['rows'])} ({_percent(f['share'])}) had no entry "
        f"in the table for {f['output_column']}."
    ),
    "rating_entries_unused": lambda f: (
        f"{f['unused_entries']:,} of the {f['entries']:,} entries in the table for "
        f"{f['output_column']} matched no row."
    ),
    "join_unmatched": lambda f: (
        f"None of the {f['base_rows']:,} base rows matched any of the {f['join_rows']:,} join rows."
    ),
    "join_partial": lambda f: (
        f"{f['matched_base_rows']:,} of {f['base_rows']:,} base rows "
        f"({_percent(f['share'])}) matched a join row."
    ),
    "join_validation_failed": _join_validation_words,
    "join_fan_out": lambda f: (
        f"The join turned {_rows(f['base_rows'])} into {f['output_rows']:,}: "
        f"{f['duplicate_key_tuples']['join']:,} keys repeat on the join side."
    ),
    "column_all_null": lambda f: f"{f['column']} is empty in all {_rows(f['rows'])}{_port(f)}.",
    "column_mostly_null": lambda f: (
        f"{f['column']} is empty in {f['nulls']:,} of {_rows(f['rows'])}{_port(f)} "
        f"({_percent(f['share'])})."
    ),
}


def finding_words(finding: Mapping[str, Any], *, row_bound: int) -> str:
    """One finding in plain words; a finding from a cut frame says so."""

    text = _FINDING_WORDS[finding["kind"]](finding)
    if finding["truncated"]:
        text += f" Measured on the first {row_bound:,} rows."
    return text


def change_data_check(
    check: DataCheckResult | None, visibility: FindingsVisibility | None
) -> AssistantChangeDataCheck | None:
    """The card's view of a plan's stored data check, under its *visibility*.

    ``None`` when no check was attempted, and when its findings describe
    another graph, which no card shows. Findings of another scenario are
    hidden, leaving the scenario they describe.
    """

    if check is None or visibility is None or visibility == "other_graph":
        return None
    stored = check.check
    findings: list[Mapping[str, Any]] = (
        list(stored["findings"]) if stored["outcome"] == "checked" else []
    )
    if visibility == "other_scenario":
        return AssistantChangeDataCheck(
            visibility=visibility,
            outcome=stored["outcome"],
            scenario=stored["scenario"],
            findings=[],
            findings_omitted=0,
            not_checked=None,
        )
    shown = findings[:ASSISTANT_MAX_DATA_FINDINGS]
    return AssistantChangeDataCheck(
        visibility=visibility,
        outcome=stored["outcome"],
        scenario=stored["scenario"],
        findings=[
            AssistantDataFinding(
                severity=finding["severity"],
                node=finding["node"],
                text=finding_words(finding, row_bound=stored["row_bound"]),
            )
            for finding in shown
        ],
        findings_omitted=len(findings) - len(shown) + int(stored.get("findings_omitted", 0)),
        not_checked=_not_checked_line(stored),
    )


def change_headline(summary: str) -> str:
    """A change's summary on one line: the Git commit message of its save.

    The whitespace is collapsed, and a line longer than `CHANGE_HEADLINE_LIMIT`
    is cut at the last word boundary that leaves room for a closing ellipsis
    within the limit, or inside a single word longer than that.
    """

    line = " ".join(summary.split())
    if len(line) <= CHANGE_HEADLINE_LIMIT:
        return line
    head = line[: CHANGE_HEADLINE_LIMIT - 1]
    if line[len(head)] != " ":
        boundary = head.rfind(" ")
        if boundary > 0:
            head = head[:boundary]
    return head.rstrip() + "\N{HORIZONTAL ELLIPSIS}"


def touched_node_ids(records: Sequence[AssistantChangeRecord]) -> tuple[str, ...]:
    """Every node id the records name, once each, in the order they name them.

    A chip names its node and a renamed node's earlier id; an added or removed
    edge names both endpoints. Ids a later save removed are included, so a
    caller can tell what no longer exists.
    """

    ids: dict[str, None] = {}
    for record in records:
        for chip in record.changes.nodes:
            if chip.renamed_from is not None:
                ids[chip.renamed_from] = None
            ids[chip.id] = None
        for edge in (*record.changes.edges_added, *record.changes.edges_removed):
            ids[edge.source] = None
            ids[edge.target] = None
    return tuple(ids)


def evidence_summary(evidence: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Verification evidence reduced to what the model acts on.

    How many schemas resolved, and (only when there are any) the inputs whose
    schema was inferred from their file and the tables whose schema was taken
    from their declared contract, without the schema digests and postcondition
    results the server compares.
    """

    def kind(name: str) -> list[Mapping[str, object]]:
        return [item for item in evidence if item.get("kind") == name]

    summary: dict[str, object] = {"schemas_resolved": len(kind("node_schema_resolved"))}
    inferred = [str(item["node"]) for item in kind("input_schema_inferred")]
    declared = [f"{item['node']}.{item['table']}" for item in kind("input_schema_declared")]
    if inferred:
        summary["inputs_inferred"] = inferred
    if declared:
        summary["tables_declared"] = declared
    return summary


__all__ = [
    "CHANGE_HEADLINE_LIMIT",
    "CHANGE_LIST_LIMIT",
    "change_data_check",
    "change_headline",
    "change_record",
    "evidence_summary",
    "field_words",
    "finding_words",
    "graph_changes",
    "touched_node_ids",
]
