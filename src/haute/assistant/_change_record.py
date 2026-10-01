"""The value-free change card of a saved plan, and the compact plan views beside it.

A change card says what a plan changed, never what it changed it to: it is
built only from node ids, node types, configuration keys, step kinds and edge
endpoints. The dry-run result, the apply result and the stream event all read
these builders, so the model and the analyst see one description of a plan.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import cache

from haute._polars_steps import STEP_KINDS
from haute._types import GraphNode, PipelineGraph
from haute.assistant._catalog import capability_manifest
from haute.assistant._ops import PlanReceipt, SemanticDiff
from haute.schemas import (
    AssistantChangeEdge,
    AssistantChangeNode,
    AssistantChangeRecord,
    AssistantGraphChanges,
)

#: The most node chips, and edges each way, one card lists.
CHANGE_LIST_LIMIT = 50
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
    receipt: PlanReceipt,
    changes: AssistantGraphChanges,
    *,
    warnings: Sequence[str],
    git_sha: str | None,
    parent_sha: str | None,
) -> AssistantChangeRecord:
    """The change card of one saved plan."""

    return AssistantChangeRecord(
        summary=receipt.summary,
        assumptions=list(receipt.assumptions),
        changes=changes,
        warnings=list(warnings),
        git_sha=git_sha,
        parent_sha=parent_sha,
    )


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
    "CHANGE_LIST_LIMIT",
    "change_record",
    "evidence_summary",
    "field_words",
    "graph_changes",
]
