"""Whether a deployed pipeline answers a request of several quotes in request order.

A request may hold several quotes, and the response answers quote *i* with row *i*
(``render_output_document``). :func:`prove_request_rows` proves that from the pruned
graph without running it: every node from the request input to the response keeps
exactly one row per quote, in request order, each row computed from its own quote
alone. A join against a side the deployment carries is proved by :func:`check_lookups`,
which reads that side's join keys once over the bundled artefacts. A pipeline the proof
does not cover answers one quote per request, and :func:`refuse_multi_quote` refuses a
request of several quotes naming every node and operation that stopped the proof
(specs/deploy, "Answers in request order").
"""

from __future__ import annotations

import ast
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Literal, cast, get_args

import polars as pl

from haute._api_input_schema import quote_input_reads_tables
from haute._cardinality import normalise_join_validation
from haute._column_lineage import (
    LineageOperation,
    LineageOperationKind,
    _frame_chain_calls,
    _parse_program,
    _ParseFailure,
)
from haute._edge_join import build_edge_join_kwargs, resolve_edge_join_role_indices
from haute._graph_utils import code_input_bindings, edge_input_name
from haute._hashing import HashingWriter, content_hash
from haute._json_shred._shred import table_is_emitting
from haute._logging import get_logger
from haute._node_builder import node_fn_name
from haute._polars_operations import POLARS_OPERATIONS, OperationClass
from haute._polars_selectors import preamble_selector_aliases
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph
from haute.chunking import classify_row_local_expression
from haute.errors import DeployError, MultiQuoteRequestRefusedError, RequestRowCountError

logger = get_logger(component="deploy.request_rows")

MultiQuote = Literal["in_request_order", "refused", "one_quote_per_request"]
RowEffect = Literal["drops", "repeats", "reorders", "replaces", "mixes", "unproven"]

#: What each effect does to a request's quotes, as a refusal says it.
_EFFECT_WORDS: Mapping[str, str] = {
    "drops": "can drop a quote",
    "repeats": "can repeat a quote",
    "reorders": "can change the quotes' order",
    "replaces": "answers with another input's rows",
    "mixes": "makes one quote's answer depend on the others",
    "unproven": "cannot be checked",
}


@dataclass(frozen=True, slots=True)
class RowRefusal:
    """One node and operation that stops the proof, and what it does to the quotes."""

    node_id: str
    node_label: str
    operation: str
    effect: RowEffect

    def describe(self) -> str:
        return f"{self.node_label!r} ({self.operation}) {_EFFECT_WORDS[self.effect]}"

    def to_manifest(self) -> dict[str, str]:
        return {
            "node_id": self.node_id,
            "node_label": self.node_label,
            "operation": self.operation,
            "effect": self.effect,
        }


@dataclass(frozen=True, slots=True)
class LookupCheck:
    """A join's other side, built only from what the deployment carries, checked at build.

    ``keys`` are the side's join keys, each of which must name one row; empty for a cross
    join, whose side must hold exactly one row.
    """

    join_node_id: str
    join_node_label: str
    side_node_id: str
    side_node_label: str
    side_port: str | None
    keys: tuple[str, ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "join_node_id": self.join_node_id,
            "join_node_label": self.join_node_label,
            "side_node_id": self.side_node_id,
            "side_node_label": self.side_node_label,
            "side_port": self.side_port,
            "keys": list(self.keys),
        }


@dataclass(frozen=True, slots=True)
class RequestRows:
    """How a deployment answers a request of several quotes (the manifest's ``request_rows``)."""

    multi_quote: MultiQuote
    refusals: tuple[RowRefusal, ...] = ()
    checked_lookups: tuple[LookupCheck, ...] = ()

    def __post_init__(self) -> None:
        if self.multi_quote not in get_args(MultiQuote):
            raise ValueError(f"Unknown multi_quote {self.multi_quote!r}.")
        if (self.multi_quote == "refused") != bool(self.refusals):
            raise ValueError("A deployment refuses multi-quote requests exactly when it names why.")

    def describe(self) -> str:
        """The proof's result in one sentence, as ``haute deploy`` prints it."""
        if self.multi_quote == "in_request_order":
            return "Answers several quotes per request, one row per quote in request order"
        if self.multi_quote == "one_quote_per_request":
            return "Answers one quote per request (a Workbench Input reads one quote)"
        return "Answers one quote per request: " + "; ".join(r.describe() for r in self.refusals)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "multi_quote": self.multi_quote,
            "refusals": [refusal.to_manifest() for refusal in self.refusals],
            "checked_lookups": [check.to_manifest() for check in self.checked_lookups],
        }

    @classmethod
    def from_manifest(cls, record: Mapping[str, Any]) -> RequestRows:
        """The record a manifest holds, every key required."""
        return cls(
            multi_quote=record["multi_quote"],
            refusals=tuple(
                RowRefusal(
                    node_id=item["node_id"],
                    node_label=item["node_label"],
                    operation=item["operation"],
                    effect=item["effect"],
                )
                for item in record["refusals"]
            ),
            checked_lookups=tuple(
                LookupCheck(
                    join_node_id=item["join_node_id"],
                    join_node_label=item["join_node_label"],
                    side_node_id=item["side_node_id"],
                    side_node_label=item["side_node_label"],
                    side_port=item["side_port"],
                    keys=tuple(item["keys"]),
                )
                for item in record["checked_lookups"]
            ),
        )


# ---------------------------------------------------------------------------
# Served refusal and backstop
# ---------------------------------------------------------------------------


def refuse_multi_quote(request_rows: RequestRows | None, quotes: int) -> None:
    """Refuse a request of several quotes to a deployment the proof does not cover.

    ``None`` is a dry run, never refused. A Workbench Input's reader refuses a
    multi-quote request itself.
    """
    if request_rows is None or quotes <= 1 or request_rows.multi_quote != "refused":
        return
    raise MultiQuoteRequestRefusedError(
        f"This deployment answers one quote per request, and this request holds {quotes} "
        "quotes: " + "; ".join(refusal.describe() for refusal in request_rows.refusals) + ".",
        quotes=quotes,
        refusals=[refusal.to_manifest() for refusal in request_rows.refusals],
    )


def check_answer_rows(request_rows: RequestRows | None, quotes: int, rows: int) -> None:
    """Fail a proven deployment's multi-quote answer whose row count is not the request's."""
    if request_rows is None or quotes <= 1 or request_rows.multi_quote != "in_request_order":
        return
    if rows != quotes:
        raise RequestRowCountError(
            f"The pipeline answered a request of {quotes} quotes with {rows} rows; a "
            "deployed pipeline answers each quote with one row, in request order.",
            quotes=quotes,
            rows=rows,
        )


# ---------------------------------------------------------------------------
# Artefact digests
# ---------------------------------------------------------------------------


def record_digests(artifacts: Mapping[str, Path]) -> dict[str, str]:
    """Each bundled artefact's content digest, read before anything else reads it."""
    return {name: content_hash(Path(path)) for name, path in sorted(artifacts.items())}


def copy_verified(source: Path, dest: Path, *, digest: str, name: str) -> None:
    """Copy one artefact into a bundle, its bytes hashed as written against *digest*.

    A file changed since resolution recorded its digest would ship bytes the build never
    checked; the copy is removed and the build fails naming it.
    """
    with Path(source).open("rb") as reader, Path(dest).open("wb") as target:
        writer = HashingWriter(target)
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        written = writer.hexdigest()
    if written != digest:
        Path(dest).unlink(missing_ok=True)
        raise DeployError(
            f"Artefact {name!r} changed after the deployment was resolved: {source} no "
            "longer holds the bytes the build checked. Deploy again.",
            artifact=name,
            path=str(source),
        )
    shutil.copystat(source, dest)


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


class _Rows(Enum):
    REQUEST = "request"
    """One row per quote, in request order, each computed from its own quote."""
    ONE = "one"
    """Exactly one row."""
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class _FrameFacts:
    """What the proof knows about one node's output frame."""

    rows: _Rows
    from_request: bool
    carried: bool
    """Built only from bundled artefacts, Constants and the preamble, so the build can read it."""


_REQUEST_FACTS = _FrameFacts(_Rows.REQUEST, from_request=True, carried=False)
_LOST = _FrameFacts(_Rows.OTHER, from_request=True, carried=False)

# A code box's frame operations, by what they do to a request frame's rows. ``select``,
# ``with_columns`` and ``fill_null`` keep the rows when every argument is row-local.
_KEEPS_ROWS = frozenset({"rename", "drop", "cast"})
_EXPRESSION_METHODS = frozenset({"select", "select_seq", "with_columns", "fill_null"})
_EFFECT_BY_METHOD: Mapping[str, RowEffect] = {
    "filter": "drops",
    "drop_nulls": "drops",
    "head": "drops",
    "tail": "drops",
    "limit": "drops",
    "slice": "drops",
    "top_k": "drops",
    "bottom_k": "drops",
    "unique": "drops",
    "sort": "reorders",
    "reverse": "reorders",
    "explode": "repeats",
    "unpivot": "repeats",
    "group_by.agg": "mixes",
    "shift": "mixes",
    "with_row_index": "mixes",
    "interpolate": "mixes",
    "join_asof": "mixes",
}
# A fill strategy that reads no other row.
_ROW_LOCAL_FILL_STRATEGIES = frozenset({"zero", "one"})
_JOIN_EFFECT_BY_HOW: Mapping[str, RowEffect] = {
    "inner": "drops",
    "semi": "drops",
    "anti": "drops",
    "full": "repeats",
}


@dataclass(slots=True)
class _Walk:
    graph: PipelineGraph
    input_node_ids: frozenset[str]
    artifact_paths: Mapping[str, str]
    node_map: dict[str, GraphNode]
    edges_by_target: dict[str, list[GraphEdge]]
    selector_aliases: frozenset[str]
    facts: dict[str, _FrameFacts] = field(default_factory=dict)
    port_facts: dict[str, dict[str, _FrameFacts]] = field(default_factory=dict)
    """A Quote Input's facts per table, where its ports differ."""
    refusals: list[RowRefusal] = field(default_factory=list)
    checks: list[LookupCheck] = field(default_factory=list)

    def refuse(self, node: GraphNode, operation: str, effect: RowEffect) -> _FrameFacts:
        refusal = RowRefusal(node.id, node.data.label, operation, effect)
        if refusal not in self.refusals:
            self.refusals.append(refusal)
        return _LOST

    def check(self, join: GraphNode, edge: GraphEdge, keys: tuple[str, ...]) -> None:
        side = self.node_map[edge.source]
        lookup = LookupCheck(
            join.id, join.data.label, side.id, side.data.label, edge.sourceHandle, keys
        )
        if lookup not in self.checks:
            self.checks.append(lookup)

    # -- nodes ---------------------------------------------------------------

    def edge_facts(self, edge: GraphEdge) -> _FrameFacts:
        ports = self.port_facts.get(edge.source, {})
        if edge.sourceHandle is not None and edge.sourceHandle in ports:
            return ports[edge.sourceHandle]
        return self.facts[edge.source]

    def node(self, node: GraphNode) -> _FrameFacts:
        edges = self.edges_by_target.get(node.id, [])
        inputs = [self.edge_facts(edge) for edge in edges]
        node_type = node.data.nodeType
        if node.id in self.input_node_ids:
            return self.request_input(node)
        if node_type is NodeType.CONSTANT:
            return _FrameFacts(_Rows.ONE, from_request=False, carried=True)
        if not inputs:
            # A source the request does not reach: a bundled Data Input, or code that
            # builds its own frame. The build can read it.
            return _FrameFacts(_Rows.OTHER, from_request=False, carried=True)
        request_in = any(facts.rows is _Rows.REQUEST for facts in inputs)
        first = inputs[0]
        if node_type in {NodeType.DATA_OUTPUT, NodeType.MODELLING}:
            return first
        if node_type is NodeType.LIVE_SWITCH:
            # Deploy pruning keeps only the live input's edge.
            if len(inputs) != 1:
                return (
                    self.refuse(node, "Live Switch", "unproven")
                    if request_in
                    else _lost_like(inputs)
                )
            return first
        if node_type is NodeType.OPTIMISER:
            return (
                self.named_input(node, edges, inputs, node.data.config.get("data_input")) or first
            )
        if node_type is NodeType.BANDING:
            return first
        if node_type in {NodeType.RATING_STEP, NodeType.EXPLORE}:
            # A Rating Step's lookups are deduplicated and joined ``maintain_order="left"``;
            # its code, and an Explore's, see the frame as ``df`` alone.
            code = _code(node)
            return self.code_box(node, code, {"df": (first, edges[0])}) if code else first
        if node_type is NodeType.MODEL_SCORE:
            return self.model_score(node, edges, inputs)
        if node_type is NodeType.EXTERNAL_FILE:
            code = _code(node)
            if not code:
                return first
            return self.bound_code_box(node, code, edges, inputs, alias_first_as_df=True)
        if node_type is NodeType.POLARS:
            code = _code(node)
            if not code:
                return (
                    self.refuse(node, "no code", "unproven") if request_in else _lost_like(inputs)
                )
            return self.bound_code_box(node, code, edges, inputs, alias_first_as_df=False)
        if node_type is NodeType.EDGE_JOIN:
            return self.edge_join(node, edges, inputs)
        if node_type is NodeType.OPTIMISER_APPLY:
            return self.optimiser_apply(node, edges, inputs)
        if node_type is NodeType.OUTPUT:
            return self.response(node, edges, inputs)
        if node_type is NodeType.SCENARIO_EXPANDER:
            return self.refuse(node, "Expander", "repeats") if request_in else _lost_like(inputs)
        if node_type is NodeType.WORKBENCH_OUTPUT:
            # It answers one quote per request.
            return (
                self.refuse(node, "Workbench Output", "unproven")
                if request_in
                else _lost_like(inputs)
            )
        if node_type in {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}:
            # A placeholder the deployed graph still holds (BUG-18).
            return self.refuse(node, "submodel", "unproven") if request_in else _lost_like(inputs)
        return (
            self.refuse(node, str(node_type.value), "unproven")
            if request_in
            else _lost_like(inputs)
        )

    def request_input(self, node: GraphNode) -> _FrameFacts:
        config = node.data.config
        if node.data.nodeType is NodeType.DATA_INPUT:
            # A promoted Data Input: its post-load code runs on the request.
            code = _code(node)
            if not code:
                return _REQUEST_FACTS
            return self.code_box(node, code, {"df": (_REQUEST_FACTS, None)})
        if not quote_input_reads_tables(config):
            return _REQUEST_FACTS
        # The request is shredded into the tables: the root table holds one row per
        # quote, in request order; another table holds a quote's array rows.
        ports = {
            str(table["label"]): _REQUEST_FACTS if table.get("path") == "$[:]" else _LOST
            for table in config.get("tables") or []
            if table_is_emitting(table)
        }
        self.port_facts[node.id] = ports
        return _REQUEST_FACTS if _REQUEST_FACTS in ports.values() else _LOST

    def named_input(
        self,
        node: GraphNode,
        edges: Sequence[GraphEdge],
        inputs: Sequence[_FrameFacts],
        name: object,
    ) -> _FrameFacts | None:
        if not isinstance(name, str) or not name:
            return None
        for edge, facts in zip(edges, inputs, strict=True):
            try:
                edge_name = edge_input_name(
                    edge, self.node_map[edge.source], submodels=self.graph.submodels
                )
            except (KeyError, ValueError):
                continue
            if edge_name == name:
                return facts
        return None

    def model_score(
        self, node: GraphNode, edges: Sequence[GraphEdge], inputs: Sequence[_FrameFacts]
    ) -> _FrameFacts:
        # Scoring attaches predictions to the frame it scored, its first input.
        scored = inputs[0]
        code = _code(node)
        if not code:
            return scored
        return self.bound_code_box(node, code, edges, [scored, *inputs[1:]], alias_first_as_df=True)

    def optimiser_apply(
        self, node: GraphNode, edges: Sequence[GraphEdge], inputs: Sequence[_FrameFacts]
    ) -> _FrameFacts:
        config = node.data.config
        request_in = any(facts.rows is _Rows.REQUEST for facts in inputs)
        source_type = config.get("sourceType", "")
        if source_type in {"run", "registered"}:
            # Its result is loaded from MLflow when a request is scored.
            if request_in:
                return self.refuse(node, "Apply Optimisation from MLflow", "unproven")
            return _FrameFacts(_Rows.OTHER, any(f.from_request for f in inputs), carried=False)
        mode = self.optimiser_mode(node)
        if mode == "ratebook":
            selected = self.named_input(node, edges, inputs, config.get("ratebook_input"))
            if selected is None:
                return (
                    self.refuse(node, "Apply Optimisation input", "unproven")
                    if request_in
                    else _lost_like(inputs)
                )
            # Factor-table lookups, deduplicated and joined ``maintain_order="left"``.
            return selected
        if mode == "online":
            # One row per quote id, in the id's text order (``key_prefix_python_scan``).
            return (
                self.refuse(node, "Apply Optimisation (online)", "mixes")
                if request_in
                else _lost_like(inputs)
            )
        return (
            self.refuse(node, "Apply Optimisation", "unproven")
            if request_in
            else _lost_like(inputs)
        )

    def optimiser_mode(self, node: GraphNode) -> str | None:
        from haute._optimiser_io import load_optimiser_artifact
        from haute.deploy._scorer import _remap_artifact

        path = _remap_artifact(
            node.id, node.data.config, dict(self.artifact_paths), "artifact_path"
        )
        if path is None:
            return None
        mode = load_optimiser_artifact(path).get("mode", "online")
        return mode if isinstance(mode, str) else None

    def response(
        self, node: GraphNode, edges: Sequence[GraphEdge], inputs: Sequence[_FrameFacts]
    ) -> _FrameFacts:
        from haute._output_assembler import (
            _array_prefix,
            _parse_output_path,
            is_active_mapping_entry,
        )

        from_request = any(facts.from_request for facts in inputs)
        try:
            prefixes: dict[str, tuple[str, ...]] = {}
            for entry in node.data.config.get("outputMapping") or []:
                if not is_active_mapping_entry(entry):
                    continue
                prefix = tuple(_array_prefix(_parse_output_path(entry["output_path"])))
                port = entry["source_port"]
                prefixes[port] = max(prefixes.get(port, ()), prefix, key=len)
        except Exception:  # noqa: BLE001 - the assembler refuses the mapping when it runs
            return (
                self.refuse(node, "response mapping", "unproven")
                if from_request
                else _lost_like(inputs)
            )
        top = [port for port, prefix in prefixes.items() if prefix == ()]
        if len(inputs) == 1:
            frames = {port: inputs[0] for port in prefixes}
        else:
            frames = {
                edge.sourceHandle or node_fn_name(self.node_map[edge.source]): facts
                for edge, facts in zip(edges, inputs, strict=True)
            }
        if len(top) != 1 or top[0] not in frames:
            # The top level is assembled from nested rows, not from the quotes.
            return (
                self.refuse(node, "response top level", "replaces")
                if from_request
                else _lost_like(inputs)
            )
        root = frames[top[0]]
        if root.rows is _Rows.REQUEST:
            return _REQUEST_FACTS
        if not root.from_request:
            return self.refuse(node, f"response top level from {top[0]}", "replaces")
        # The request frame was lost upstream, where it was refused.
        return _LOST

    # -- joins ---------------------------------------------------------------

    def edge_join(
        self, node: GraphNode, edges: Sequence[GraphEdge], inputs: Sequence[_FrameFacts]
    ) -> _FrameFacts:
        request_in = any(facts.rows is _Rows.REQUEST for facts in inputs)
        try:
            kwargs = build_edge_join_kwargs(node.data.config)
            base_index, join_index = resolve_edge_join_role_indices(
                [edge.targetHandle for edge in edges]
            )
        except Exception:  # noqa: BLE001 - the builder refuses the config when it runs
            return self.refuse(node, "Edge Join", "unproven") if request_in else _lost_like(inputs)
        how = str(kwargs["how"])
        order = str(kwargs["maintain_order"])
        validate = kwargs.get("validate")
        on = kwargs.get("on")
        left_keys = _key_tuple(on if on is not None else kwargs.get("left_on"))
        right_keys = _key_tuple(on if on is not None else kwargs.get("right_on"))
        base, joined = inputs[base_index], inputs[join_index]
        operation = f"join (how={how!r}, maintain_order={order!r})"
        if base.rows is not _Rows.REQUEST and joined.rows is _Rows.REQUEST:
            # Only a right join, ordered by its right side, is driven by the request here.
            return self.join(
                node,
                operation,
                driver=joined,
                other=base,
                how=how,
                order=order,
                validate=validate,
                other_keys=left_keys,
                other_edge=edges[base_index],
                driver_is_left=False,
            )
        return self.join(
            node,
            operation,
            driver=base,
            other=joined,
            how=how,
            order=order,
            validate=validate,
            other_keys=right_keys,
            other_edge=edges[join_index],
            driver_is_left=True,
        )

    def join(
        self,
        node: GraphNode,
        operation: str,
        *,
        driver: _FrameFacts,
        other: _FrameFacts,
        how: str,
        order: str,
        validate: str | None,
        other_keys: tuple[str, ...],
        other_edge: GraphEdge | None,
        driver_is_left: bool,
    ) -> _FrameFacts:
        from_request = driver.from_request or other.from_request
        carried = driver.carried and other.carried
        if driver.rows is not _Rows.REQUEST:
            if other.rows is _Rows.REQUEST:
                return self.refuse(node, operation, "replaces")
            one = driver.rows is _Rows.ONE and other.rows is _Rows.ONE and how == "cross"
            return _FrameFacts(_Rows.ONE if one else _Rows.OTHER, from_request, carried)
        declared = normalise_join_validation(validate) if validate else None
        # Polars states ``validate`` from the left side: the other side is unique under
        # ``m:1`` when the request is on the left, ``1:m`` when it is on the right.
        other_unique = {"m:1", "1:1"} if driver_is_left else {"1:m", "1:1"}
        declared_many = declared is not None and declared not in other_unique
        lookup = other.carried and not other.from_request and other_edge is not None
        # A carried lookup holding a join key twice repeats even a quote sent alone,
        # whatever else the join does, so every keyed join the request drives is checked.
        keyed_how = {"left", "inner", "full"} if driver_is_left else {"right", "inner", "full"}
        if lookup and how in keyed_how and other.rows is not _Rows.ONE and not declared_many:
            assert other_edge is not None
            self.check(node, other_edge, other_keys)
        driving_how = {"left", "cross"} if driver_is_left else {"right"}
        if how not in driving_how:
            return self.refuse(node, operation, _JOIN_EFFECT_BY_HOW.get(how, "replaces"))
        if order not in ({"left", "left_right"} if driver_is_left else {"right", "right_left"}):
            return self.refuse(node, operation, "reorders")
        if other.rows is _Rows.ONE:
            return _REQUEST_FACTS
        if other.from_request:
            # Two parts of the request joined can pair one quote with another's rows even
            # when both sides' keys are unique (a branch that shifts the quote's key), so
            # no declaration proves it.
            return self.refuse(node, operation, "mixes")
        if not lookup:
            # Read when a request is scored (an Optimiser Apply from MLflow, say).
            return self.refuse(node, operation, "unproven")
        if how == "cross":
            # Exactly one row answers each quote once; another count only repeats (or
            # drops) them, which a single quote may mean, so it is refused, not failed.
            assert other_edge is not None
            self.check(node, other_edge, ())
            return _REQUEST_FACTS
        if declared_many:
            return self.refuse(node, f"{operation}, validate={validate!r}", "repeats")
        return _REQUEST_FACTS

    # -- code boxes ----------------------------------------------------------

    def bound_code_box(
        self,
        node: GraphNode,
        code: str,
        edges: Sequence[GraphEdge],
        inputs: Sequence[_FrameFacts],
        *,
        alias_first_as_df: bool,
    ) -> _FrameFacts:
        request_in = any(facts.rows is _Rows.REQUEST for facts in inputs)
        try:
            bindings = code_input_bindings(
                node,
                edges,
                node_map=self.node_map,
                submodels=self.graph.submodels,
                alias_first_as_df=alias_first_as_df,
            )
        except Exception:  # noqa: BLE001 - the executor refuses the binding when it runs
            bindings = None
        if bindings is None:
            return (
                self.refuse(node, "input names", "unproven") if request_in else _lost_like(inputs)
            )
        by_edge = {edge.id: (facts, edge) for edge, facts in zip(edges, inputs, strict=True)}
        return self.code_box(
            node, code, {name: by_edge[edge_id] for name, edge_id in bindings.items()}
        )

    def code_box(
        self,
        node: GraphNode,
        code: str,
        named: Mapping[str, tuple[_FrameFacts, GraphEdge | None]],
    ) -> _FrameFacts:
        facts_by_name = {name: facts for name, (facts, _edge) in named.items()}
        request_in = any(facts.rows is _Rows.REQUEST for facts in facts_by_name.values())
        from_request = any(facts.from_request for facts in facts_by_name.values())
        carried = all(facts.carried for facts in facts_by_name.values())
        # ``df`` names the first input wherever the executor aliases it, beside that
        # input's own name.
        program = _parse_program(
            code,
            frozenset(named),
            True,
            self.selector_aliases,
            df_input="df" if "df" in named else None,
        )
        steps = (
            None if isinstance(program, _ParseFailure) else _program_steps(code, program.operations)
        )
        if isinstance(program, _ParseFailure) or steps is None:
            if not request_in:
                return _FrameFacts(_Rows.OTHER, from_request, carried)
            reason = (
                program.operation or program.reason
                if isinstance(program, _ParseFailure)
                else "code"
            )
            return self.refuse(node, f"code: {reason}", "unproven")
        current = facts_by_name[program.root_input]
        for operation, calls in steps:
            current = self.operation(node, operation, calls, current, named)
        return replace(
            current,
            from_request=current.from_request or from_request,
            carried=current.carried and carried,
        )

    def operation(
        self,
        node: GraphNode,
        operation: LineageOperation,
        calls: tuple[ast.Call, ...],
        current: _FrameFacts,
        named: Mapping[str, tuple[_FrameFacts, GraphEdge | None]],
    ) -> _FrameFacts:
        method = operation.method
        if operation.kind is LineageOperationKind.JOIN and method == "join":
            assert operation.right_input is not None and operation.how is not None
            other, edge = named[operation.right_input]
            validate = _literal_keyword(calls[0], "validate")
            return self.join(
                node,
                f"join (how={operation.how!r}, maintain_order={operation.maintain_order!r})",
                driver=current,
                other=other,
                how=operation.how,
                order=operation.maintain_order,
                validate=validate,
                other_keys=tuple(right for _left, right in operation.key_pairs),
                other_edge=edge,
                driver_is_left=True,
            )
        if current.rows is _Rows.OTHER:
            return current
        refusal = self.row_effect(operation, calls[0])
        if refusal is None:
            return current
        described, effect = refusal
        if current.rows is _Rows.REQUEST:
            self.refuse(node, described, effect)
        return _FrameFacts(_Rows.OTHER, current.from_request, current.carried)

    def row_effect(
        self, operation: LineageOperation, call: ast.Call
    ) -> tuple[str, RowEffect] | None:
        """Why *operation* does not keep a frame's rows, or None when it does."""
        method = operation.method
        if method in _KEEPS_ROWS:
            return None
        if method in _EXPRESSION_METHODS:
            if method == "fill_null":
                strategy = _literal_keyword(call, "strategy")
                if (
                    any(k.arg == "strategy" for k in call.keywords)
                    and strategy not in _ROW_LOCAL_FILL_STRATEGIES
                ):
                    return f"fill_null: strategy={strategy!r}", "mixes"
            blocking = _first_non_row_local(
                call, self.selector_aliases, skip={"strategy", "limit", "matches_supertype"}
            )
            if blocking is None:
                return None
            # An operation the registry knows reads other rows mixes quotes; anything
            # else the classifier cannot read (a helper's call, say) is not proven.
            effect: RowEffect = "mixes" if _reads_other_rows(blocking) else "unproven"
            return f"{method}: {blocking}", effect
        return method, _EFFECT_BY_METHOD.get(method, "unproven")


def _lost_like(inputs: Iterable[_FrameFacts]) -> _FrameFacts:
    """Any rows, from wherever the inputs came."""
    inputs = list(inputs)
    return _FrameFacts(
        _Rows.OTHER,
        from_request=any(facts.from_request for facts in inputs),
        carried=all(facts.carried for facts in inputs),
    )


def _code(node: GraphNode) -> str:
    return str(node.data.config.get("code") or "").strip()


def _key_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in cast(Iterable[object], value))


def _literal_keyword(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            return value if isinstance(value, str) else None
    return None


def _first_non_row_local(
    call: ast.Call, selector_aliases: frozenset[str], *, skip: frozenset[str] | set[str]
) -> str | None:
    """The operator in *call*'s first argument that reads other rows, or None."""
    arguments: list[ast.expr] = list(call.args)
    for keyword in call.keywords:
        if keyword.arg is None:
            return "**"
        if keyword.arg not in skip:
            arguments.append(keyword.value)
    for argument in arguments:
        decision = classify_row_local_expression(
            ast.unparse(argument), selector_aliases=selector_aliases
        )
        if not decision.eligible:
            return decision.blocking_operator or decision.reason
    return None


def _reads_other_rows(blocking: str) -> bool:
    """Whether *blocking*, a classifier's blocking operator, is a registered operation
    that is not row-local (``sum``, ``pl.len``, ``rank``)."""
    namespace, _, name = blocking.removeprefix("pl.").rpartition(".")
    return any(
        entry.name == name
        and (entry.namespace or "") == namespace
        and entry.operation_class is not OperationClass.ROW_LOCAL
        for entry in POLARS_OPERATIONS.values()
    )


def _program_steps(
    code: str, operations: Sequence[LineageOperation]
) -> list[tuple[LineageOperation, tuple[ast.Call, ...]]] | None:
    """Pair each operation ``_parse_program`` accepted with the calls it came from.

    The parser accepted the code, so its ``df = ...`` statements are frame chains in
    execution order, and a ``group_by`` and its ``agg`` are one operation. ``None`` when
    the two disagree, which the proof treats as code it cannot follow.
    """
    calls: list[ast.Call] = []
    for statement in ast.parse(code).body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == "df"
            and not isinstance(statement.value, ast.Name)
        ):
            calls.extend(_frame_chain_calls(statement.value))
    steps: list[tuple[LineageOperation, tuple[ast.Call, ...]]] = []
    index = 0
    for operation in operations:
        width = 2 if operation.method == "group_by.agg" else 1
        chunk = tuple(calls[index : index + width])
        if len(chunk) != width:
            return None
        steps.append((operation, chunk))
        index += width
    return steps if index == len(calls) else None


def prove_request_rows(
    graph: PipelineGraph,
    input_node_ids: Sequence[str],
    output_node_id: str,
    artifact_paths: Mapping[str, str],
) -> tuple[MultiQuote, tuple[RowRefusal, ...], tuple[LookupCheck, ...]]:
    """Prove the pruned *graph* answers each quote of a request with one row, in order.

    Returns the verdict, every refusal (each node and operation that stops the proof)
    and the joins whose carried side :func:`check_lookups` must read.
    """
    node_map = {node.id: node for node in graph.nodes}
    inputs = frozenset(input_node_ids)
    if any(node_map[node_id].data.nodeType is NodeType.WORKBENCH_INPUT for node_id in inputs):
        return "one_quote_per_request", (), ()
    edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        if edge.source in node_map and edge.target in node_map:
            edges_by_target.setdefault(edge.target, []).append(edge)
    walk = _Walk(
        graph=graph,
        input_node_ids=inputs,
        artifact_paths=artifact_paths,
        node_map=node_map,
        edges_by_target=edges_by_target,
        selector_aliases=preamble_selector_aliases(graph.preamble or ""),
    )
    for node_id in _topological_order(graph, node_map, edges_by_target):
        walk.facts[node_id] = walk.node(node_map[node_id])
    output = node_map[output_node_id]
    if walk.facts[output_node_id].rows is not _Rows.REQUEST and not walk.refusals:
        walk.refuse(output, "response", "replaces")
    if walk.refusals:
        return "refused", tuple(walk.refusals), tuple(walk.checks)
    return "in_request_order", (), tuple(walk.checks)


def _topological_order(
    graph: PipelineGraph,
    node_map: Mapping[str, GraphNode],
    edges_by_target: Mapping[str, Sequence[GraphEdge]],
) -> list[str]:
    remaining = {node_id: len(edges_by_target.get(node_id, ())) for node_id in node_map}
    children: dict[str, list[str]] = {}
    for target, edges in edges_by_target.items():
        for edge in edges:
            children.setdefault(edge.source, []).append(target)
    ready = [node.id for node in graph.nodes if remaining[node.id] == 0]
    order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for child in children.get(node_id, ()):
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
    if len(order) != len(node_map):
        raise DeployError("The deployed graph has a cycle.")
    return order


# ---------------------------------------------------------------------------
# Lookup checks
# ---------------------------------------------------------------------------


def check_lookups(
    graph: PipelineGraph,
    input_node_ids: Sequence[str],
    checks: Sequence[LookupCheck],
    artifact_paths: Mapping[str, str],
) -> tuple[tuple[RowRefusal, ...], tuple[LookupCheck, ...]]:
    """Read each checked side once over the bundled artefacts.

    A side holding a join key twice (no part of it null, since a null never matches a
    quote) would repeat a quote even when it is sent alone, so it fails the deployment
    with one ``DeployError`` naming every such side, its join and the key. A cross
    join's side holding other than one row, or a side the build cannot read in bounded
    memory (a group-by on it without an estimate), only leaves the deployment answering
    one quote per request: their refusals are returned with the checks that passed.
    """
    from haute._execution_context import ExecutionProfile
    from haute._polars_utils import streaming_collect
    from haute.deploy._scorer import admit_deploy_execution, score_graph_lazy
    from haute.errors import BoundedMemoryUnsupportedError

    failures: list[str] = []
    failing_sides: list[str] = []
    refusals: list[RowRefusal] = []
    passed: list[LookupCheck] = []
    for lookup in checks:
        context = admit_deploy_execution(
            operation="deploy_lookup_check", row_count=1, profile=ExecutionProfile.DEPLOY_LIVE
        )
        try:
            plan = score_graph_lazy(
                graph=graph,
                input_df=pl.DataFrame(),
                input_node_ids=list(input_node_ids),
                output_node_id=lookup.side_node_id,
                artifact_paths=dict(artifact_paths),
                execution_context=context,
            )
        except BoundedMemoryUnsupportedError:
            # ``score_graph_lazy`` released the admission.
            refusals.append(
                RowRefusal(
                    lookup.join_node_id,
                    lookup.join_node_label,
                    f"join with {lookup.side_node_label!r}, too large to check",
                    "unproven",
                )
            )
            continue
        primary_error = False
        try:
            # A source with several tables gives a frame per port.
            side: Any = plan.lazy_frame
            if isinstance(side, Mapping):
                side = side[lookup.side_port]
            if lookup.keys:
                count = "__haute_key_rows__"
                query = (
                    side.select(list(lookup.keys))
                    .filter(pl.all_horizontal(pl.col(key).is_not_null() for key in lookup.keys))
                    .group_by(list(lookup.keys))
                    .agg(pl.len().alias(count))
                    .filter(pl.col(count) > 1)
                    .head(1)
                )
                repeated = streaming_collect(query, execution_context=context)
                if repeated.height:
                    example = {key: repeated[key][0] for key in lookup.keys}
                    failing_sides.append(lookup.side_node_id)
                    failures.append(
                        f"{lookup.side_node_label!r} holds the join key {example!r} of "
                        f"{lookup.join_node_label!r} more than once, so the join would repeat a "
                        "quote: give each key one row, or declare the join's validate as "
                        "'m:m' if a quote is meant to match several rows (the deployment then "
                        "answers one quote per request)"
                    )
                else:
                    passed.append(lookup)
            else:
                rows = streaming_collect(side.select(pl.len()), execution_context=context).item()
                if rows == 1:
                    passed.append(lookup)
                else:
                    refusals.append(
                        RowRefusal(
                            lookup.join_node_id,
                            lookup.join_node_label,
                            f"cross join with {lookup.side_node_label!r}, which holds {rows} rows",
                            "repeats" if rows else "drops",
                        )
                    )
        except BaseException:
            primary_error = True
            raise
        finally:
            plan.cleanup(preserve_primary_error=primary_error)
    if failures:
        raise DeployError(
            "A lookup the deployment carries cannot answer each quote with one row: "
            + "; ".join(failures)
            + ".",
            lookups=failing_sides,
        )
    if checks:
        logger.info("deploy_lookups_checked", lookups=len(checks), refused=len(refusals))
    return tuple(refusals), tuple(passed)


def request_rows(
    graph: PipelineGraph,
    input_node_ids: Sequence[str],
    output_node_id: str,
    artifact_paths: Mapping[str, str],
) -> RequestRows:
    """The proof, its lookups checked: the record a deployment carries."""
    verdict, refusals, checks = prove_request_rows(
        graph, input_node_ids, output_node_id, artifact_paths
    )
    checked_refusals, checked = check_lookups(graph, input_node_ids, checks, artifact_paths)
    if checked_refusals:
        refusals = (*refusals, *checked_refusals)
        verdict = "refused"
    return RequestRows(multi_quote=verdict, refusals=refusals, checked_lookups=checked)
