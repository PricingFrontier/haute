"""Run the assistant evaluation cases live against the configured provider, or replay them.

The evaluation harness lives here, outside the installed package. Its live
runner (``python -m scripts.run_assistant_self_test record``) runs the cases
against the configured provider (live evidence); the replay suite drives the
same cases through recorded reference trajectories (replay evidence). Both
score the same layers, and neither mixes with the other in one report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import multiprocessing
import os
import secrets
import shutil
import tempfile
import time
import tomllib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast, get_args

import polars as pl

import haute
from haute import _git
from haute._git_state import write_working_branch
from haute._interactive_workers import (
    resolve_interactive_execution_mode,
    shutdown_interactive_worker_pool,
    start_interactive_worker_pool,
)
from haute._mlflow_utils import allow_file_store_if_local
from haute._native_memory_limit import native_memory_backend_scope
from haute._polars_steps import STEPPED_NODE_TYPES, is_stepped_config
from haute._sandbox import bound_project_root
from haute._types import NodeType
from haute.assistant._config import (
    AssistantConfig,
    EgressPolicy,
    ProviderTrust,
    resolve_assistant_config,
)
from haute.assistant._loop import build_system_prompt, run_turn
from haute.assistant._providers import (
    AssistantProvider,
    DatabricksProvider,
    ProviderEvent,
    ProviderUsage,
    TextDelta,
    ToolCallRequest,
    TurnStop,
    create_provider,
)
from haute.assistant._render import render_turn_context
from haute.assistant._session import SessionStore
from haute.assistant._tools import (
    TOOL_DEFINITIONS,
    build_tool_executor,
    build_turn_context,
    context_update,
    get_pipeline,
)
from haute.deploy._config import _load_env
from haute.executor import execute_graph
from haute.graph_utils import flatten_graph
from haute.routes._helpers import parse_pipeline_to_graph, pipeline_dir
from haute.schemas import AssistantTurnOutcomeKind
from scripts.assistant_eval_report import (
    METRICS,
    SELF_TEST_LAYERS,
    RunIdentity,
    SelfTestLayer,
    compare_report_files,
    configuration_for,
    load_support_matrix,
    report_payload,
    require_git_ignored,
    write_report,
    write_transcript,
)

#: An outcome a case turn may expect.
SelfTestOutcome = Literal["applied", "answered", "needs_input", "blocked"]
SelfTestTerminal = Literal["completed", "failed", "cancelled"]
SelfTestArea = Literal[
    "steps",
    "banding",
    "rating",
    "joins",
    "outputs",
    "modelling",
    "model_score",
    "optimiser",
    "submodels",
    "source_switch",
    "explore",
    "recovery",
    "read_only",
    "delegation",
    "multi_turn",
    "multi_stage",
    "clarification",
    "safety",
]
SelfTestSplit = Literal["development", "holdout"]
SelfTestEvidence = Literal["live", "replay"]
#: A configuration variant a live run can measure.
SelfTestVariant = Literal["multi_apply", "one_apply_per_turn", "canonical_tools"]
#: The named egress profile a case runs under (see ``egress_policy``).
SelfTestEgress = Literal["project", "metadata_only"]
ProviderFactory = Callable[[AssistantConfig], AssistantProvider]

AREAS: tuple[SelfTestArea, ...] = get_args(SelfTestArea)
SPLITS: tuple[SelfTestSplit, ...] = get_args(SelfTestSplit)
VARIANTS: tuple[SelfTestVariant, ...] = get_args(SelfTestVariant)
EGRESS_PROFILES: tuple[SelfTestEgress, ...] = get_args(SelfTestEgress)
#: The product's own behaviour, which tier 0 replays every case under, so no
#: case may be inapplicable to it.
_PRODUCT_VARIANT: SelfTestVariant = "multi_apply"
_OUTCOMES: tuple[SelfTestOutcome, ...] = get_args(SelfTestOutcome)
_CASE_SCHEMA_VERSION = 4
_CASE_KEYS = {
    "schema_version",
    "id",
    "fixture_version",
    "project_fixture",
    "area",
    "split",
    "egress",
    "inapplicable_variants",
    "turns",
}
_TURN_KEYS = {"request", "expectations"}
_EXPECTATION_KEYS = {
    "outcome",
    "saves",
    "required_node_types",
    "forbidden_node_types",
    "required_edges",
    "require_connected_graph",
    "forbidden_assistant_text",
    "modified_nodes",
    "node_configs",
    "execution",
    "efficiency",
}
_EFFICIENCY_KEYS = {
    "max_provider_round_trips",
    "max_tool_calls",
    "max_failed_tool_calls",
    "max_duplicate_static_reads",
}
_EDGE_KEYS = {"source", "target", "target_handle"}
_GOLDEN_KEYS = {"node", "scenario", "golden", "order_free"}
#: A golden node's executed frame must fit one preview; a larger fixture fails loudly.
_MAX_GOLDEN_ROWS = 10_000
_NODE_TYPES = frozenset(node_type.value for node_type in NodeType)
_TOOL_NAMES = frozenset(str(definition["name"]) for definition in TOOL_DEFINITIONS)
_STATIC_READ_TOOLS = frozenset({"find_data", "read_reference"})
#: The run id a fixture's Model Scoring node holds until its model is logged.
FIXTURE_MODEL_RUN_PLACEHOLDER = "0" * 32
_NO_USAGE = ProviderUsage(input_tokens=0, output_tokens=0)


@dataclass(frozen=True, slots=True)
class SelfTestGolden:
    """A node whose output, run under *scenario*, must equal plain-Polars *golden* code's ``df``."""

    node: str
    scenario: str
    golden: str
    order_free: bool


@dataclass(frozen=True, slots=True)
class SelfTestEfficiency:
    """Limits a case about efficiency holds; any other case only reports its metrics."""

    max_provider_round_trips: int
    max_tool_calls: int
    max_failed_tool_calls: int
    max_duplicate_static_reads: int


@dataclass(frozen=True, slots=True)
class SelfTestExpectations:
    outcome: SelfTestOutcome
    saves: bool
    required_node_types: tuple[str, ...]
    forbidden_node_types: tuple[str, ...]
    forbidden_assistant_text: tuple[str, ...]
    required_edges: tuple[tuple[str, str, str | None], ...]
    require_connected_graph: bool
    modified_nodes: tuple[str, ...]
    node_configs: Mapping[str, Mapping[str, Any]]
    execution: tuple[SelfTestGolden, ...]
    efficiency: SelfTestEfficiency | None


@dataclass(frozen=True, slots=True)
class SelfTestTurn:
    request: str
    expectations: SelfTestExpectations


@dataclass(frozen=True, slots=True)
class SelfTestCase:
    """One case: its project, area and split, the egress profile it runs under, the
    variants it cannot be measured under, and its turns."""

    id: str
    fixture_version: str
    project_fixture: str
    area: SelfTestArea
    split: SelfTestSplit
    egress: SelfTestEgress
    inapplicable_variants: tuple[SelfTestVariant, ...]
    turns: tuple[SelfTestTurn, ...]


@dataclass(frozen=True, slots=True)
class SelfTestGraph:
    node_types: Mapping[str, str]
    edges: tuple[tuple[str, str, str | None], ...]
    configs: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class SelfTestTelemetry:
    """What one turn did. ``outcome`` is the typed outcome kind, None when the turn
    did not complete; ``saved_changes`` counts the changes the turn saved."""

    terminal: SelfTestTerminal
    outcome: AssistantTurnOutcomeKind | None
    saved_changes: int
    change_cards: int
    provider_round_trips: int
    tool_calls: int
    failed_tool_calls: int
    duplicate_static_reads: int
    leaked_forbidden_text: int
    input_tokens: int
    output_tokens: int
    time_to_first_token_ms: float
    time_to_validated_plan_ms: float
    end_to_end_ms: float


@dataclass(frozen=True, slots=True)
class SelfTestToolDiagnostic:
    name: str
    status: Literal["ok", "error"]
    error_code: str | None
    validation_path: str | None
    validation_reason: str | None


@dataclass(frozen=True, slots=True)
class SelfTestTurnResult:
    #: Each reason is prefixed with the layer it fails, as ``"<layer>: <reason>"``.
    reasons: tuple[str, ...]
    failed_layers: tuple[SelfTestLayer, ...]
    telemetry: SelfTestTelemetry
    tool_diagnostics: tuple[SelfTestToolDiagnostic, ...]
    node_types: tuple[str, ...]
    edges: tuple[tuple[str, str, str | None], ...]

    @property
    def passed(self) -> bool:
        return not self.reasons


@dataclass(frozen=True, slots=True)
class SelfTestResult:
    id: str
    fixture_version: str
    area: SelfTestArea
    split: SelfTestSplit
    egress: SelfTestEgress
    evidence: SelfTestEvidence
    provider: str
    model: str
    turns: tuple[SelfTestTurnResult, ...]

    @property
    def passed(self) -> bool:
        return all(turn.passed for turn in self.turns)

    @property
    def reasons(self) -> tuple[str, ...]:
        """Every turn's reasons, each as ``"turn <n> <layer>: <reason>"``."""

        return tuple(
            f"turn {index} {reason}"
            for index, turn in enumerate(self.turns, start=1)
            for reason in turn.reasons
        )

    @property
    def failed_layers(self) -> tuple[SelfTestLayer, ...]:
        failed = {layer for turn in self.turns for layer in turn.failed_layers}
        return tuple(layer for layer in SELF_TEST_LAYERS if layer in failed)

    @property
    def first_failing_layer(self) -> SelfTestLayer | None:
        """The first failing layer of the first failing turn."""

        return next((turn.failed_layers[0] for turn in self.turns if turn.failed_layers), None)

    @property
    def tool_diagnostics(self) -> tuple[SelfTestToolDiagnostic, ...]:
        return tuple(diagnostic for turn in self.turns for diagnostic in turn.tool_diagnostics)

    @property
    def metrics(self) -> Mapping[str, float]:
        """Each efficiency metric summed over the case's turns."""

        return {name: sum(getattr(turn.telemetry, name) for turn in self.turns) for name in METRICS}


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _string_list(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{path} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{path} must not contain duplicates")
    return tuple(value)


def _node_type_list(value: object, path: str) -> tuple[str, ...]:
    names = _string_list(value, path)
    if unknown := sorted(set(names) - _NODE_TYPES):
        raise ValueError(f"{path} names unknown node type(s): {', '.join(unknown)}")
    return names


def _limit(value: object, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{path} must be an integer >= {minimum}")
    return value


def _safe_fixture(value: object, path: str) -> str:
    fixture = _text(value, path)
    parsed = Path(fixture)
    if (
        parsed.is_absolute()
        or parsed.drive
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError(f"{path} must be a safe relative path")
    return fixture


def _node_configs(value: object, path: str) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, dict) or any(
        not isinstance(node, str) or not node or not isinstance(subset, dict) or not subset
        for node, subset in value.items()
    ):
        raise ValueError(f"{path} must map node names to non-empty config objects")
    # Plain dicts: a case crosses into the per-case process, and a mapping proxy
    # does not pickle.
    return {node: dict(subset) for node, subset in value.items()}


def _goldens(value: object, path: str) -> tuple[SelfTestGolden, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    goldens: list[SelfTestGolden] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != _GOLDEN_KEYS:
            raise ValueError(f"{path}[{index}] is not closed")
        if type(item["order_free"]) is not bool:
            raise ValueError(f"{path}[{index}].order_free must be a boolean")
        goldens.append(
            SelfTestGolden(
                node=_text(item["node"], f"{path}[{index}].node"),
                scenario=_text(item["scenario"], f"{path}[{index}].scenario"),
                golden=_text(item["golden"], f"{path}[{index}].golden"),
                order_free=item["order_free"],
            )
        )
    if len({golden.node for golden in goldens}) != len(goldens):
        raise ValueError(f"{path} names a node twice")
    return tuple(goldens)


def _efficiency(value: object, path: str) -> SelfTestEfficiency | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _EFFICIENCY_KEYS:
        raise ValueError(f"{path} must be null or the closed efficiency limits")
    return SelfTestEfficiency(
        max_provider_round_trips=_limit(
            value["max_provider_round_trips"], f"{path}.max_provider_round_trips", minimum=1
        ),
        max_tool_calls=_limit(value["max_tool_calls"], f"{path}.max_tool_calls"),
        max_failed_tool_calls=_limit(
            value["max_failed_tool_calls"], f"{path}.max_failed_tool_calls"
        ),
        max_duplicate_static_reads=_limit(
            value["max_duplicate_static_reads"], f"{path}.max_duplicate_static_reads"
        ),
    )


def _expectations(value: object, where: str) -> SelfTestExpectations:
    if not isinstance(value, dict) or set(value) != _EXPECTATION_KEYS:
        raise ValueError(f"{where} expectations are not the closed v{_CASE_SCHEMA_VERSION} shape")
    outcome = value["outcome"]
    if outcome not in _OUTCOMES:
        raise ValueError(f"{where} has an unknown expected outcome")
    saves = value["saves"]
    if type(saves) is not bool:
        raise ValueError(f"{where} saves must be a boolean")
    if (outcome == "applied" and not saves) or (outcome == "answered" and saves):
        raise ValueError(f"{where}: an applied turn saves and an answered turn does not")
    raw_edges = value["required_edges"]
    if not isinstance(raw_edges, list):
        raise ValueError(f"{where} required_edges must be an array")
    edges: list[tuple[str, str, str | None]] = []
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, dict) or set(edge) != _EDGE_KEYS:
            raise ValueError(f"{where} required_edges[{index}] is not closed")
        source = _text(edge["source"], f"{where} edge source")
        target = _text(edge["target"], f"{where} edge target")
        target_handle = edge["target_handle"]
        if target_handle is not None and (not isinstance(target_handle, str) or not target_handle):
            raise ValueError(f"{where} edge target_handle must be string or null")
        edges.append((source, target, target_handle))
    connected = value["require_connected_graph"]
    if type(connected) is not bool:
        raise ValueError(f"{where} require_connected_graph must be a boolean")
    node_configs = _node_configs(value["node_configs"], f"{where} node_configs")
    execution = _goldens(value["execution"], f"{where} execution")
    if not saves and (node_configs or execution):
        raise ValueError(f"{where} saves nothing, so it cannot expect node configs or execution")
    return SelfTestExpectations(
        outcome=cast(SelfTestOutcome, outcome),
        saves=saves,
        required_node_types=_node_type_list(
            value["required_node_types"], f"{where} required_node_types"
        ),
        forbidden_node_types=_node_type_list(
            value["forbidden_node_types"], f"{where} forbidden_node_types"
        ),
        forbidden_assistant_text=_string_list(
            value["forbidden_assistant_text"], f"{where} forbidden_assistant_text"
        ),
        required_edges=tuple(edges),
        require_connected_graph=connected,
        modified_nodes=_string_list(value["modified_nodes"], f"{where} modified_nodes"),
        node_configs=node_configs,
        execution=execution,
        efficiency=_efficiency(value["efficiency"], f"{where} efficiency"),
    )


def _inapplicable_variants(value: object, path: str) -> tuple[SelfTestVariant, ...]:
    variants = _string_list(value, path)
    if unknown := sorted(set(variants) - set(VARIANTS)):
        raise ValueError(f"{path} names unknown variant(s): {', '.join(unknown)}")
    if _PRODUCT_VARIANT in variants:
        raise ValueError(
            f"{path} cannot name {_PRODUCT_VARIANT}: every case applies to the product's own "
            "behaviour, which tier 0 replays"
        )
    return cast(tuple[SelfTestVariant, ...], variants)


def _turns(value: object, path: Path) -> tuple[SelfTestTurn, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path.name} turns must be a non-empty array")
    turns: list[SelfTestTurn] = []
    for index, raw in enumerate(value, start=1):
        where = f"{path.name} turn {index}"
        if not isinstance(raw, dict) or set(raw) != _TURN_KEYS:
            raise ValueError(f"{where} is not closed")
        turns.append(
            SelfTestTurn(
                request=_text(raw["request"], f"{where} request"),
                expectations=_expectations(raw["expectations"], where),
            )
        )
    return tuple(turns)


def load_self_test_cases(
    root: Path,
    *,
    projects_root: Path,
) -> tuple[SelfTestCase, ...]:
    """Load the closed case portfolio and validate every disposable fixture."""

    cases: list[SelfTestCase] = []
    resolved_projects = projects_root.resolve()
    for path in sorted(root.glob("*.json")):
        raw = _object(path)
        if set(raw) != _CASE_KEYS or raw.get("schema_version") != _CASE_SCHEMA_VERSION:
            raise ValueError(f"{path.name} is not the closed case v{_CASE_SCHEMA_VERSION} shape")
        if raw["area"] not in AREAS:
            raise ValueError(f"{path.name} has an unknown area")
        if raw["split"] not in SPLITS:
            raise ValueError(f"{path.name} has an unknown split")
        if raw["egress"] not in EGRESS_PROFILES:
            raise ValueError(f"{path.name} names an unknown egress profile")
        fixture_name = _safe_fixture(raw["project_fixture"], f"{path.name} project_fixture")
        fixture = (resolved_projects / fixture_name).resolve()
        if (
            not fixture.is_relative_to(resolved_projects)
            or not fixture.is_dir()
            or any(item.is_symlink() for item in fixture.rglob("*"))
            or not (fixture / "haute.toml").is_file()
            or not (fixture / "pipeline.py").is_file()
        ):
            raise ValueError(f"evaluation project fixture is incomplete or unsafe: {fixture_name}")
        cases.append(
            SelfTestCase(
                id=_text(raw["id"], f"{path.name} id"),
                fixture_version=_text(raw["fixture_version"], f"{path.name} fixture_version"),
                project_fixture=fixture_name,
                area=cast(SelfTestArea, raw["area"]),
                split=cast(SelfTestSplit, raw["split"]),
                egress=cast(SelfTestEgress, raw["egress"]),
                inapplicable_variants=_inapplicable_variants(
                    raw["inapplicable_variants"], f"{path.name} inapplicable_variants"
                ),
                turns=_turns(raw["turns"], path),
            )
        )
    if not cases:
        raise ValueError("assistant evaluation case directory is empty")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("assistant evaluation case ids must be unique")
    return tuple(cases)


def select_self_test_cases(
    cases: Sequence[SelfTestCase],
    selected_ids: Sequence[str] = (),
    *,
    areas: Sequence[str] = (),
    splits: Sequence[str] = (),
) -> tuple[SelfTestCase, ...]:
    """Select cases by id, area and split, preserving deterministic portfolio order.

    Each filter left empty selects everything; an unknown id, area or split fails.
    """

    if unknown := sorted(set(selected_ids) - {case.id for case in cases}):
        raise ValueError(f"Unknown evaluation case: {', '.join(unknown)}")
    if unknown := sorted(set(areas) - set(AREAS)):
        raise ValueError(f"Unknown evaluation area: {', '.join(unknown)}")
    if unknown := sorted(set(splits) - set(SPLITS)):
        raise ValueError(f"Unknown evaluation split: {', '.join(unknown)}")
    return tuple(
        case
        for case in cases
        if (not selected_ids or case.id in selected_ids)
        and (not areas or case.area in areas)
        and (not splits or case.split in splits)
    )


def _disconnected_changed_nodes(before: SelfTestGraph, after: SelfTestGraph) -> tuple[str, ...]:
    """Name the changed nodes and neighbours outside their largest connected component.

    A node is changed when it is new or retyped, or is an endpoint of an added or
    removed edge. Those nodes and every node adjacent to them must form one
    component; nodes the plan never touched are not checked, so an unconnected
    input elsewhere in the fixture cannot fail a case.
    """

    adjacency: dict[str, set[str]] = {node: set() for node in after.node_types}
    for source, target, _target_handle in after.edges:
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
            adjacency[target].add(source)
    changed = {
        node
        for node, node_type in after.node_types.items()
        if before.node_types.get(node) != node_type
    }
    for source, target, _target_handle in set(before.edges) ^ set(after.edges):
        changed.update(node for node in (source, target) if node in adjacency)
    region = changed.union(*(adjacency[node] for node in changed))
    components: list[set[str]] = []
    unvisited = set(region)
    while unvisited:
        pending = [min(unvisited)]
        component: set[str] = set()
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            pending.extend((adjacency[node] & region) - component)
        components.append(component)
        unvisited -= component
    if len(components) < 2:
        return ()
    largest = max(components, key=lambda component: (len(component), sorted(component)))
    return tuple(sorted(region - largest))


def frames_equal(a: pl.DataFrame, b: pl.DataFrame, *, order_free: bool) -> tuple[bool, str]:
    """Strict equality: columns, dtypes, null patterns, values; row order unless order-free.

    Floats compare within 1e-6, with NaN equal to NaN and infinities equal by
    sign. The explanation names columns only, never a value.
    """

    def canonical(frame: pl.DataFrame) -> pl.DataFrame:
        sortable = [
            name
            for name, dtype in frame.schema.items()
            if not isinstance(dtype, (pl.List, pl.Struct))
        ]
        return frame.sort(sortable) if sortable else frame

    if a.columns != b.columns:
        return False, f"columns differ: {a.columns} vs {b.columns}"
    if a.height != b.height:
        return False, f"row counts differ: {a.height} vs {b.height}"
    ca, cb = (canonical(a), canonical(b)) if order_free else (a, b)
    for name in a.columns:
        sa, sb = ca[name], cb[name]
        if sa.dtype != sb.dtype:
            return False, f"dtype differs on {name}: {sa.dtype} vs {sb.dtype}"
        if not sa.is_null().equals(sb.is_null()):
            return False, f"null pattern differs on {name}"
        if sa.dtype.is_float():
            fa, fb = sa.fill_null(0.0), sb.fill_null(0.0)
            close = ((fa - fb).abs() < 1e-6) | (fa.is_nan() & fb.is_nan())
            close = close | (fa.is_infinite() & (fa == fb))
            if not close.fill_null(False).all():
                return False, f"values differ on {name}"
        elif not sa.equals(sb):
            return False, f"values differ on {name}"
    return True, ""


def config_digest(config: Mapping[str, Any]) -> str:
    """A node configuration's digest: SHA-256 of its canonical JSON."""

    return hashlib.sha256(
        json.dumps(
            config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _subset_mismatch(expected: object, actual: object, path: str) -> str | None:
    """The first path at which *actual* does not contain *expected*.

    Mappings match when every expected key matches recursively. Lists match
    when they have the same length and each expected element matches the
    actual element at its position, recursively, so a key the case leaves
    out of a list's mapping element is free just as it is in a mapping;
    a list of another length or with its elements in another order does not
    match. Scalars match only when equal.
    """

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return path
        for key, value in expected.items():
            if key not in actual:
                return f"{path}.{key}"
            if (mismatch := _subset_mismatch(value, actual[key], f"{path}.{key}")) is not None:
                return mismatch
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return path
        for index, (item, actual_item) in enumerate(zip(expected, actual, strict=True)):
            if (mismatch := _subset_mismatch(item, actual_item, f"{path}[{index}]")) is not None:
                return mismatch
        return None
    return None if expected == actual else path


def _protocol_reasons(
    expected: SelfTestExpectations,
    before: SelfTestGraph,
    after: SelfTestGraph,
    telemetry: SelfTestTelemetry,
) -> list[str]:
    reasons: list[str] = []
    if telemetry.terminal != "completed":
        reasons.append(f"turn terminal was {telemetry.terminal}")
    elif telemetry.outcome != expected.outcome:
        reasons.append(f"outcome was {telemetry.outcome}; expected {expected.outcome}")
    saved = telemetry.saved_changes > 0
    if saved != expected.saves:
        reasons.append(
            f"saved {telemetry.saved_changes} changes; expected a saved change"
            if expected.saves
            else f"saved {telemetry.saved_changes} changes; expected none"
        )
    if saved and before == after:
        reasons.append("the saved changes did not change the graph")
    if not saved and before != after:
        reasons.append("a turn that saved nothing changed the graph")
    if telemetry.change_cards != telemetry.saved_changes:
        reasons.append(
            f"{telemetry.change_cards} change cards for {telemetry.saved_changes} saved changes"
        )
    if telemetry.leaked_forbidden_text:
        reasons.append(
            f"assistant output leaked {telemetry.leaked_forbidden_text} forbidden canary values"
        )
    if (limits := expected.efficiency) is not None:
        for label, observed, maximum in (
            (
                "provider round trips",
                telemetry.provider_round_trips,
                limits.max_provider_round_trips,
            ),
            ("tool calls", telemetry.tool_calls, limits.max_tool_calls),
            ("failed tool calls", telemetry.failed_tool_calls, limits.max_failed_tool_calls),
            (
                "duplicate static reads",
                telemetry.duplicate_static_reads,
                limits.max_duplicate_static_reads,
            ),
        ):
            if observed > maximum:
                reasons.append(f"{label} {observed} exceeded {maximum}")
    return reasons


def _structure_reasons(
    expected: SelfTestExpectations, before: SelfTestGraph, after: SelfTestGraph
) -> list[str]:
    reasons: list[str] = []
    actual_types = set(after.node_types.values())
    if missing_types := sorted(set(expected.required_node_types) - actual_types):
        reasons.append("required node types are missing: " + ", ".join(missing_types))
    if forbidden_types := sorted(set(expected.forbidden_node_types) & actual_types):
        reasons.append("forbidden node types are present: " + ", ".join(forbidden_types))
    actual_edges = set(after.edges)
    for source, target, target_handle in expected.required_edges:
        present = (
            any(
                edge_source == source and edge_target == target
                for edge_source, edge_target, _ in actual_edges
            )
            if target_handle is None
            else (source, target, target_handle) in actual_edges
        )
        if not present:
            handle = "any" if target_handle is None else target_handle
            reasons.append(f"required edge {source} -> {target} [{handle}] is missing")
    if expected.require_connected_graph and (
        stranded := _disconnected_changed_nodes(before, after)
    ):
        reasons.append(
            "changed nodes and their neighbours are not one connected component: "
            + ", ".join(stranded)
        )
    return reasons


def _configuration_reasons(expected: SelfTestExpectations, after: SelfTestGraph) -> list[str]:
    reasons: list[str] = []
    for node, subset in expected.node_configs.items():
        if node not in after.configs:
            reasons.append(f"node {node} is missing")
        elif (mismatch := _subset_mismatch(subset, after.configs[node], "config")) is not None:
            reasons.append(f"node {node} does not hold the expected value at {mismatch}")
    return reasons


def _collateral_reasons(
    expected: SelfTestExpectations, before: SelfTestGraph, after: SelfTestGraph
) -> list[str]:
    """Nodes existing before the turn that it may not change keep their config digest."""

    reasons: list[str] = []
    for node, config in before.configs.items():
        if node in expected.modified_nodes:
            continue
        if node not in after.configs:
            reasons.append(f"pre-existing node {node} was removed")
        elif config_digest(after.configs[node]) != config_digest(config):
            reasons.append(f"pre-existing node {node} changed its configuration")
    return reasons


def _editor_reasons(before: SelfTestGraph, after: SelfTestGraph) -> list[str]:
    """New and previously stepped nodes of a stepped type open in the step builder."""

    reasons: list[str] = []
    for node, node_type_name in after.node_types.items():
        node_type = NodeType(node_type_name)
        if node_type not in STEPPED_NODE_TYPES:
            continue
        previous = before.configs.get(node)
        if previous is not None and (
            before.node_types[node] != node_type_name or not is_stepped_config(node_type, previous)
        ):
            continue
        config = after.configs[node]
        if not is_stepped_config(node_type, config):
            reasons.append(f"node {node} is not authored as steps")
        for key in ("_steps_error", "_steps_discarded"):
            if key in config:
                reasons.append(f"node {node} carries {key}")
    return reasons


def score_turn(
    expected: SelfTestExpectations,
    *,
    before: SelfTestGraph,
    after: SelfTestGraph,
    telemetry: SelfTestTelemetry,
    tool_diagnostics: Sequence[SelfTestToolDiagnostic] = (),
    execution_reasons: Sequence[str] = (),
) -> SelfTestTurnResult:
    """Score one turn by layer without retaining provider-visible content.

    *execution_reasons* come from the harness executing the turn's golden
    nodes (``execute_goldens``); every other layer is scored here.
    """

    by_layer: dict[SelfTestLayer, Sequence[str]] = {
        "protocol": _protocol_reasons(expected, before, after, telemetry),
        "structure": _structure_reasons(expected, before, after),
        "configuration": _configuration_reasons(expected, after),
        "collateral": _collateral_reasons(expected, before, after),
        "editor": _editor_reasons(before, after),
        "execution": execution_reasons,
    }
    return SelfTestTurnResult(
        reasons=tuple(
            f"{layer}: {reason}" for layer in SELF_TEST_LAYERS for reason in by_layer[layer]
        ),
        failed_layers=tuple(layer for layer in SELF_TEST_LAYERS if by_layer[layer]),
        telemetry=telemetry,
        tool_diagnostics=tuple(tool_diagnostics),
        node_types=tuple(sorted(set(after.node_types.values()))),
        edges=tuple(sorted(after.edges, key=lambda edge: (edge[0], edge[1], edge[2] or ""))),
    )


def _golden_frame(code: str) -> pl.DataFrame:
    """Run plain-Polars golden *code* in the project directory and return its ``df``."""

    namespace: dict[str, object] = {"pl": pl}
    exec(code, namespace, namespace)  # noqa: S102 - checked-in case golden, run by the harness
    frame = namespace.get("df")
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    if not isinstance(frame, pl.DataFrame):
        raise ValueError("a golden must bind df to a Polars DataFrame or LazyFrame")
    return frame


def execute_goldens(goldens: Sequence[SelfTestGolden], source_file: Path) -> tuple[str, ...]:
    """Execute each golden node of the saved pipeline and compare it with its golden.

    The harness runs this after the turn, in the project copy: the assistant
    never executes anything. Each node runs through the production preview
    engine up to that node only, under the golden's scenario, so no sink is
    ever built. A golden whose frame does not fit one preview, or whose code
    does not bind ``df``, is a broken case and raises. Columns are matched by
    name, whatever their order: the saved node's frame is reordered to the
    golden's columns before the frames are compared.
    """

    if not goldens:
        return ()
    # A preview runs the flattened graph: each submodel occurrence inlined.
    graph = flatten_graph(parse_pipeline_to_graph(source_file))
    node_ids = {node.id for node in graph.nodes}
    reasons: list[str] = []
    for golden in goldens:
        if golden.node not in node_ids:
            reasons.append(f"node {golden.node} is missing")
            continue
        expected = _golden_frame(golden.golden)
        # Fixture frames are a few rows. Without a declared worker memory cap
        # the engine refuses a boundary it cannot estimate (a join with
        # `validate` or `maintain_order`), which the preview worker runs under
        # its cap; the harness declares one, as the engine's own tests do.
        with native_memory_backend_scope("rlimit"):
            result = execute_graph(
                graph,
                target_node_id=golden.node,
                max_preview_rows=_MAX_GOLDEN_ROWS,
                source=golden.scenario,
                target_preview_only=True,
            )[golden.node]
        if result.status != "ok":
            reasons.append(f"node {golden.node} failed to execute")
            continue
        if result.preview_truncated or result.preview_columns != [
            column.name for column in result.columns
        ]:
            raise RuntimeError(f"node {golden.node}'s output does not fit one full preview")
        actual_dtypes = {column.name: column.dtype for column in result.columns}
        expected_dtypes = {name: str(dtype) for name, dtype in expected.schema.items()}
        if actual_dtypes != expected_dtypes:
            reasons.append(f"node {golden.node} columns or dtypes differ from its golden")
            continue
        actual = pl.DataFrame(
            result.preview,
            schema={column.name: expected.schema[column.name] for column in result.columns},
            orient="row",
        ).select(expected.columns)
        equal, why = frames_equal(expected, actual, order_free=golden.order_free)
        if not equal:
            reasons.append(f"node {golden.node} does not match its golden: {why}")
    return tuple(reasons)


def _saved_in_previous_round(messages: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the round the loop just executed saved a plan.

    The previous round's results are the trailing tool messages, after which
    the loop may place a turn context update.
    """

    for message in reversed(messages):
        role = message.get("role")
        if role == "context":
            continue
        if role != "tool":
            return False
        content = message.get("content")
        if (
            message.get("name") == "apply_graph_plan"
            and isinstance(content, Mapping)
            and "error" not in content
        ):
            return True
    return False


class _ObservedProvider:
    """Counts one turn's provider rounds and times its first event.

    Under the ``one_apply_per_turn`` variant it ends the turn in place of the
    round after a saving apply, without a provider request, as the assistant
    did before a turn could save several plans.
    """

    def __init__(
        self, delegate: AssistantProvider, started_at: float, variant: SelfTestVariant
    ) -> None:
        self.delegate = delegate
        self.started_at = started_at
        self.variant = variant
        self.round_trips = 0
        self.first_event_ms: float | None = None

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]:
        if self.variant == "one_apply_per_turn" and _saved_in_previous_round(messages):
            yield TurnStop("end", _NO_USAGE)
            return
        self.round_trips += 1
        async for event in self.delegate.stream_turn(
            system=system,
            messages=messages,
            tools=tools,
        ):
            if self.first_event_ms is None:
                self.first_event_ms = (time.monotonic() - self.started_at) * 1000
            yield event


class _ObservedToolExecutor:
    def __init__(
        self,
        delegate: Callable[[str, dict[str, Any]], Awaitable[Mapping[str, object]]],
        started_at: float,
        transcript: list[dict[str, object]] | None,
    ) -> None:
        self.delegate = delegate
        self.started_at = started_at
        self.transcript = transcript
        self.calls = 0
        self.failed_calls = 0
        self.duplicate_static_reads = 0
        self.validated_plan_ms: float | None = None
        self._static_calls: set[tuple[str, str]] = set()
        self.diagnostics: list[SelfTestToolDiagnostic] = []

    async def __call__(self, name: str, arguments: dict[str, Any]) -> Mapping[str, object]:
        self.calls += 1
        if name in _STATIC_READ_TOOLS:
            key = (
                name,
                json.dumps(
                    arguments,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            )
            if key in self._static_calls:
                self.duplicate_static_reads += 1
            self._static_calls.add(key)
        result = await self.delegate(name, arguments)
        if self.transcript is not None:
            self.transcript.append({"tool": name, "arguments": arguments, "result": result})
        failed = "error" in result
        raw_error = result.get("error")
        error = raw_error if isinstance(raw_error, Mapping) else {}

        def error_text(key: str) -> str | None:
            value = error.get(key)
            return value if isinstance(value, str) else None

        self.diagnostics.append(
            SelfTestToolDiagnostic(
                name=name,
                status="error" if failed else "ok",
                error_code=error_text("code"),
                validation_path=error_text("validation_path"),
                validation_reason=error_text("validation_reason"),
            )
        )
        if failed:
            self.failed_calls += 1
        if name == "dry_run_graph_edits" and not failed and self.validated_plan_ms is None:
            self.validated_plan_ms = (time.monotonic() - self.started_at) * 1000
        return result


def _read_graph(source_file: str) -> SelfTestGraph:
    """Read the saved graph: structure as the assistant's graph tool renders it, and
    each node's configuration as the parser reads it (what the editor opens)."""

    node_types, edges = _graph_structure(get_pipeline(source_file))
    configs = {
        node.id: dict(node.data.config) for node in parse_pipeline_to_graph(Path(source_file)).nodes
    }
    if set(configs) != set(node_types):
        raise RuntimeError("the graph tool and the parser disagree on the saved nodes")
    return SelfTestGraph(
        node_types=MappingProxyType(node_types),
        edges=edges,
        configs=MappingProxyType(configs),
    )


def _graph_structure(
    payload: Mapping[str, object],
) -> tuple[dict[str, str], tuple[tuple[str, str, str | None], ...]]:
    raw_nodes = payload.get("nodes")
    raw_edges = payload.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise TypeError("pipeline graph tool returned malformed nodes or edges")
    node_types: dict[str, str] = {}
    for node in raw_nodes:
        if not isinstance(node, Mapping):
            raise TypeError("pipeline graph tool returned a malformed node")
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not isinstance(node_type, str):
            raise TypeError("pipeline graph tool returned a malformed node identity")
        node_types[node_id] = node_type
    edges: list[tuple[str, str, str | None]] = []
    for edge in raw_edges:
        if not isinstance(edge, Mapping):
            raise TypeError("pipeline graph tool returned a malformed edge")
        source = edge.get("source")
        target = edge.get("target")
        # The renderer names handles the way the graph-edit operations accept
        # them, so the read shape is snake_case. A stale camelCase key returns
        # None for every edge without raising, which scores every Edge Join
        # role as missing however correctly the assistant wired it.
        target_handle = edge.get("target_handle")
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or (target_handle is not None and not isinstance(target_handle, str))
        ):
            raise TypeError("pipeline graph tool returned a malformed edge identity")
        edges.append((source, target, target_handle))
    return node_types, tuple(edges)


def _append_assistant_config(project_root: Path, config: AssistantConfig) -> None:
    path = project_root / "haute.toml"
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if "assistant" in raw:
        raise ValueError("evaluation fixture must not define [assistant]")

    def quote(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = [
        "",
        "[assistant]",
        f"provider = {quote(config.provider)}",
        f"model = {quote(config.model)}",
    ]
    if config.provider == "openai" and config.base_url is not None:
        lines.append(f"base_url = {quote(config.base_url)}")
    lines.extend(
        [
            "",
            "[assistant.egress]",
            f"trust = {quote(config.egress.trust)}",
            f"max_sensitivity = {quote(config.egress.max_sensitivity)}",
            f"allow_project_knowledge = {str(config.egress.allow_project_knowledge).lower()}",
            f"allow_executable_source = {str(config.egress.allow_executable_source).lower()}",
            f"allow_row_samples = {str(config.egress.allow_row_samples).lower()}",
            f"allow_aggregate_statistics = {str(config.egress.allow_aggregate_statistics).lower()}",
        ]
    )
    existing = path.read_text(encoding="utf-8").rstrip()
    path.write_text(existing + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def prepare_fixture_models(project_root: Path) -> tuple[str, ...]:
    """Log each fixture model into the copy's MLflow folder and point its node at the run.

    ``models/<node>.cbm`` is the artefact of the Model Scoring node ``<node>``,
    whose sidecar ``config/model_scoring/<node>.json`` holds the placeholder
    run id until the artefact is logged here, before anything reads the graph.
    A model without a placeholder, or a placeholder without a model, fails
    loudly. Returns the prepared node names.
    """

    models = sorted((project_root / "models").glob("*.cbm"))
    sidecars = sorted((project_root / "config" / "model_scoring").glob("*.json"))
    placeholder = f'"run_id": "{FIXTURE_MODEL_RUN_PLACEHOLDER}"'
    waiting = {path.stem for path in sidecars if placeholder in path.read_text(encoding="utf-8")}
    if {path.stem for path in models} != waiting:
        raise ValueError(
            "fixture models and Model Scoring placeholders disagree: "
            f"models {sorted(path.stem for path in models)}, placeholders {sorted(waiting)}"
        )
    if not models:
        return ()
    with (project_root / "haute.toml").open("rb") as handle:
        folder = tomllib.load(handle).get("mlflow", {}).get("folder")
    if not isinstance(folder, str) or not folder:
        raise ValueError("a fixture with models must set [mlflow] folder in haute.toml")
    import mlflow

    tracking_uri = (project_root / folder).as_uri()
    allow_file_store_if_local(tracking_uri, "local")
    client = mlflow.MlflowClient(tracking_uri=tracking_uri)
    experiment = client.create_experiment("fixture_models")
    for model in models:
        run_id = client.create_run(experiment).info.run_id
        client.log_artifact(run_id, str(model))
        client.set_terminated(run_id)
        sidecar = project_root / "config" / "model_scoring" / f"{model.stem}.json"
        sidecar.write_text(
            sidecar.read_text(encoding="utf-8").replace(placeholder, f'"run_id": "{run_id}"'),
            encoding="utf-8",
        )
    return tuple(model.stem for model in models)


def prepare_fixture_snapshots(project_root: Path, source_file: str) -> tuple[str, ...]:
    """Build every snapshot-backed input's snapshot, as previewing the pipeline does.

    Each Quote Input's tables are built from its example request, and each Data
    Input that executes from a snapshot rather than directly from its file is
    built from that file. Runs with the sandbox bound to the copy, so the graph
    brief and dry-runs resolve the pipeline as the editor does once the analyst
    has previewed it. Returns the prepared node names.
    """

    from haute._execution_context import ExecutionProfile
    from haute._input_providers import build_input_snapshot
    from haute._json_shred._snapshots import api_input_snapshot_source, build_api_input_tables
    from haute._polars_io_registry import data_input_is_direct
    from haute._source_cache import SourceCacheStore

    store = SourceCacheStore(project_root)
    prepared: list[str] = []
    for node in parse_pipeline_to_graph(Path(source_file)).nodes:
        config = dict(node.data.config)
        if node.data.nodeType is NodeType.API_INPUT:
            source = api_input_snapshot_source(config, project_root / str(config["path"]))
            build_api_input_tables(
                source,
                [table.label for table in source.tables],
                store=store,
                profile=ExecutionProfile.LAZY_SINK,
            )
        elif node.data.nodeType is NodeType.DATA_INPUT and not data_input_is_direct(config):
            build_input_snapshot(
                config, store=store, base_dir=project_root, profile=ExecutionProfile.PREVIEW_EAGER
            )
        else:
            continue
        prepared.append(node.id)
    return tuple(prepared)


def egress_policy(profile: SelfTestEgress, *, trust: ProviderTrust) -> EgressPolicy:
    """The egress policy a case runs under: its named *profile* at the provider's *trust*.

    ``project`` is the policy a configured project holds: saved node
    configuration (``restricted``), project knowledge, executable source and
    aggregate statistics, and no row samples. ``metadata_only`` sends internal
    pipeline metadata alone, withholding saved configuration, project
    knowledge, executable source, row samples and aggregate statistics.
    """

    if profile == "project":
        return EgressPolicy(
            trust=trust,
            max_sensitivity="restricted",
            allow_project_knowledge=True,
            allow_executable_source=True,
            allow_row_samples=False,
            allow_aggregate_statistics=True,
        )
    if profile == "metadata_only":
        return EgressPolicy(
            trust=trust,
            max_sensitivity="internal",
            allow_project_knowledge=False,
            allow_executable_source=False,
            allow_row_samples=False,
            allow_aggregate_statistics=False,
        )
    raise ValueError(f"unknown evaluation egress profile: {profile}")


def evaluation_trust(config: AssistantConfig) -> ProviderTrust:
    """The invoking project's provider trust, refused when it is external."""

    if config.egress.trust == "external":
        raise ValueError(
            "The assistant evaluation needs a local or organization provider: external trust "
            "is public-only, and a public ceiling denies the project metadata tools every "
            "case uses."
        )
    return config.egress.trust


def self_test_config(config: AssistantConfig, egress: SelfTestEgress) -> AssistantConfig:
    """Return *config* under a case's *egress* profile.

    Only the provider trust comes from the invoking project, because trust
    describes the endpoint and is validated against it. What a case may send
    is its profile's decision (``egress_policy``), whatever the invoking
    project permits: the fixtures are synthetic, so the profile chooses the
    policy being measured rather than protecting anything.
    """

    return replace(config, egress=egress_policy(egress, trust=evaluation_trust(config)))


def _run_git(project_root: Path, *arguments: str) -> None:
    try:
        _git._run_git(*arguments, cwd=project_root)
    except _git.GitError as exc:
        raise RuntimeError(f"evaluation Git setup failed during {arguments[0]}") from exc


def _initialize_mutation_gate(project_root: Path) -> None:
    branch = "codex/assistant-self-test"
    _run_git(project_root, "init", "-b", "main")
    _run_git(project_root, "config", "user.name", "Haute Assistant Self-Test")
    _run_git(project_root, "config", "user.email", "assistant-self-test@haute.local")
    _run_git(project_root, "add", "--all")
    _run_git(project_root, "commit", "-m", "Initialize assistant self-test fixture")
    _run_git(project_root, "switch", "-c", branch)
    write_working_branch(project_root, branch)


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    """Enter a fixture copy as the working directory and the sandbox project root.

    The sandbox root is process-wide and set lazily, so it is bound here and
    restored on exit; otherwise a later case would resolve its data paths
    against an earlier case's deleted copy.
    """

    previous = Path.cwd()
    pipeline_dir.cache_clear()
    os.chdir(path)
    pipeline_dir.cache_clear()
    try:
        with bound_project_root(path):
            yield
    finally:
        pipeline_dir.cache_clear()
        os.chdir(previous)
        pipeline_dir.cache_clear()


@contextmanager
def _preview_workers() -> Iterator[None]:
    """The interactive preview workers a data check runs in, as the server starts them.

    Started inside the fixture copy, so the spawned workers resolve its paths,
    and shut down with the case. A check never starts the pool itself, so
    without this every check would report ``worker_busy``. Thread mode (the
    replay suite) has no workers and leaves any pool alone.
    """

    if resolve_interactive_execution_mode() != "process":
        yield
        return
    start_interactive_worker_pool()
    try:
        yield
    finally:
        shutdown_interactive_worker_pool()


async def _run_turn(
    turn: SelfTestTurn,
    *,
    store: SessionStore,
    session_id: str,
    source_file: str,
    config: AssistantConfig,
    provider: AssistantProvider,
    system_prompt: str,
    variant: SelfTestVariant,
    transcript: list[dict[str, object]] | None,
) -> SelfTestTurnResult:
    before = _read_graph(source_file)
    session = store.lookup(session_id)
    if session is None:
        raise RuntimeError("the evaluation session must stay live between its turns")
    # The turn context the message route builds, with no selection.
    turn_context = render_turn_context(
        build_turn_context(source_file, config.egress, build_plan=session.build_plan.current)
    )
    events: list[dict[str, object]] | None = None
    if transcript is not None:
        events = []
        transcript.append({"request": turn.request, "events": events})
    started_at = time.monotonic()
    observed_provider = _ObservedProvider(provider, started_at, variant)
    # The executor the message route builds: on the session's evidence ledger and plan.
    observed_tools = _ObservedToolExecutor(
        build_tool_executor(
            source_file,
            session_id=session_id,
            evidence=session.evidence,
            plan=session.build_plan,
        ),
        started_at,
        events,
    )
    text_parts: list[str] = []
    terminal: SelfTestTerminal = "failed"
    outcome: AssistantTurnOutcomeKind | None = None
    saved_changes: int | None = None
    input_tokens = 0
    output_tokens = 0
    change_cards = 0
    async for event in run_turn(
        store,
        session_id,
        turn.request,
        provider=observed_provider,
        tools=TOOL_DEFINITIONS,
        execute_tool=observed_tools,
        system_prompt=system_prompt,
        turn_timeout=None,
        max_tool_calls=None,
        turn_context=turn_context,
        refresh_context=partial(context_update, source_file, config.egress),
    ):
        if event.type == "text_delta":
            text_parts.append(event.text)
            if events is not None:
                if events and set(events[-1]) == {"text"}:
                    events[-1]["text"] = str(events[-1]["text"]) + event.text
                else:
                    events.append({"text": event.text})
        elif event.type == "change_applied":
            change_cards += 1
        elif event.type == "build_plan_updated":
            if events is not None:
                events.append({"build_plan": event.build_plan.model_dump(mode="json")})
        elif event.type == "completed":
            terminal = "completed"
            outcome = event.outcome.kind
            saved_changes = len(event.outcome.changes)
            input_tokens = event.usage.input_tokens
            output_tokens = event.usage.output_tokens
            if events is not None:
                events.append({"outcome": event.outcome.model_dump(mode="json")})
        elif event.type == "failed":
            terminal = "failed"
        elif event.type == "cancelled":
            terminal = "cancelled"
    end_to_end_ms = (time.monotonic() - started_at) * 1000
    after = _read_graph(source_file)
    execution_reasons = execute_goldens(turn.expectations.execution, Path(source_file))
    assistant_text = "".join(text_parts)
    telemetry = SelfTestTelemetry(
        terminal=terminal,
        outcome=outcome,
        # A turn that did not complete has no outcome; the saves its card stream
        # announced are what it saved.
        saved_changes=change_cards if saved_changes is None else saved_changes,
        change_cards=change_cards,
        provider_round_trips=observed_provider.round_trips,
        tool_calls=observed_tools.calls,
        failed_tool_calls=observed_tools.failed_calls,
        duplicate_static_reads=observed_tools.duplicate_static_reads,
        leaked_forbidden_text=sum(
            canary in assistant_text for canary in turn.expectations.forbidden_assistant_text
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        time_to_first_token_ms=observed_provider.first_event_ms or end_to_end_ms,
        time_to_validated_plan_ms=observed_tools.validated_plan_ms or end_to_end_ms,
        end_to_end_ms=end_to_end_ms,
    )
    return score_turn(
        turn.expectations,
        before=before,
        after=after,
        telemetry=telemetry,
        tool_diagnostics=observed_tools.diagnostics,
        execution_reasons=execution_reasons,
    )


_CANONICAL_TOOLS_PROVIDER_ONLY = (
    "the canonical_tools variant measures the Databricks lane; the Anthropic and OpenAI "
    "lanes already receive the canonical tool projection"
)


def canonical_tools_provider(provider: AssistantProvider) -> DatabricksProvider:
    """The ``canonical_tools`` variant's provider: *provider*'s Databricks lane, on
    its own configuration and client, sending the canonical tool projection in
    place of its default compatible one. Any other provider is refused."""

    if not isinstance(provider, DatabricksProvider):
        raise ValueError(_CANONICAL_TOOLS_PROVIDER_ONLY)
    return DatabricksProvider(provider.config, client=provider.client, tool_projection="canonical")


async def run_self_test_case(
    case: SelfTestCase,
    *,
    projects_root: Path,
    config: AssistantConfig,
    work_dir: Path,
    provider_factory: ProviderFactory = create_provider,
    evidence: SelfTestEvidence = "live",
    variant: SelfTestVariant = "multi_apply",
    transcript: list[dict[str, object]] | None = None,
) -> SelfTestResult:
    """Run every turn of one case through *provider_factory*'s provider and the real tools.

    The turns run in order in one session, under the case's egress profile.
    Live evidence comes from the configured provider; replay evidence from a
    ``TrajectoryProvider`` (``replay_self_test_case``), labelled provider
    ``replay`` with the trajectory as its model. The fixture is copied into
    *work_dir*, which must be empty; the caller owns its removal. A
    *transcript* list, when given, receives each turn's request, text, tool
    calls with their payloads and outcome. A case is never run under a
    variant it is inapplicable to, and the ``canonical_tools`` variant runs only
    on a Databricks configuration.
    """

    if variant in case.inapplicable_variants:
        raise ValueError(f"case {case.id} does not apply to the {variant} variant")
    if variant == "canonical_tools" and config.provider != "databricks":
        raise ValueError(_CANONICAL_TOOLS_PROVIDER_ONLY)
    config = self_test_config(config, case.egress)
    source_fixture = (projects_root.resolve() / case.project_fixture).resolve()
    if (
        not source_fixture.is_relative_to(projects_root.resolve())
        or not source_fixture.is_dir()
        or any(path.is_symlink() for path in source_fixture.rglob("*"))
    ):
        raise ValueError(f"evaluation project fixture is unsafe: {case.project_fixture}")
    if any(work_dir.iterdir()):
        raise ValueError("the evaluation work directory must be empty")
    # A short directory name: model and snapshot caches nest deep below it.
    project_root = work_dir / "p"
    shutil.copytree(source_fixture, project_root)
    prepare_fixture_models(project_root)
    _append_assistant_config(project_root, config)
    with _working_directory(project_root), _preview_workers():
        source_file = "pipeline.py"
        prepare_fixture_snapshots(project_root, source_file)
        _initialize_mutation_gate(project_root)
        # The prompt the message route builds.
        system_prompt = build_system_prompt(source_file=source_file)
        store = SessionStore()
        session = store.create(source_file)
        provider = provider_factory(config)
        if variant == "canonical_tools":
            provider = canonical_tools_provider(provider)
        turns = [
            await _run_turn(
                turn,
                store=store,
                session_id=session.id,
                source_file=source_file,
                config=config,
                provider=provider,
                system_prompt=system_prompt,
                variant=variant,
                transcript=transcript,
            )
            for turn in case.turns
        ]
    return SelfTestResult(
        id=case.id,
        fixture_version=case.fixture_version,
        area=case.area,
        split=case.split,
        egress=case.egress,
        evidence=evidence,
        provider=config.provider if evidence == "live" else "replay",
        model=config.model,
        turns=tuple(turns),
    )


_TRAJECTORY_SCHEMA_VERSION = 1
_TRAJECTORY_KEYS = {"schema_version", "id", "case", "turns"}
_ROUND_KEYS = {"text", "calls"}
_CALL_KEYS = {"id", "tool", "arguments", "result"}


class TrajectoryDivergedError(RuntimeError):
    """A replayed tool result's status differs from the one its trajectory recorded."""


@dataclass(frozen=True, slots=True)
class TrajectoryCall:
    """One recorded tool call and the status (and error code) its result had."""

    id: str
    tool: str
    arguments: Mapping[str, Any]
    status: Literal["ok", "error"]
    error_code: str | None


@dataclass(frozen=True, slots=True)
class TrajectoryRound:
    text: str
    calls: tuple[TrajectoryCall, ...]


@dataclass(frozen=True, slots=True)
class Trajectory:
    """A reference trajectory: what a model sent, round by round, for one case."""

    id: str
    case: str
    turns: tuple[tuple[TrajectoryRound, ...], ...]


def _result_references(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        if set(value) == {"$result"}:
            yield value["$result"]
            return
        for item in value.values():
            yield from _result_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _result_references(item)


def _trajectory_call(value: object, where: str, earlier: set[str]) -> TrajectoryCall:
    if not isinstance(value, dict) or set(value) != _CALL_KEYS:
        raise ValueError(f"{where} is not a closed trajectory call")
    tool = _text(value["tool"], f"{where}.tool")
    if tool not in _TOOL_NAMES:
        raise ValueError(f"{where}.tool is not an assistant tool: {tool}")
    arguments = value["arguments"]
    if not isinstance(arguments, dict):
        raise ValueError(f"{where}.arguments must be an object")
    for reference in _result_references(arguments):
        call, _, key = reference.partition(".") if isinstance(reference, str) else ("", "", "")
        if call not in earlier or not key:
            raise ValueError(f"{where} references {reference!r}, which is not <earlier call>.<key>")
    result = value["result"]
    if not isinstance(result, dict) or result.get("status") not in {"ok", "error"}:
        raise ValueError(f"{where}.result must record status ok or error")
    if result["status"] == "ok" and set(result) != {"status"}:
        raise ValueError(f"{where}.result records only the status of a successful call")
    if result["status"] == "error" and set(result) != {"status", "error_code"}:
        raise ValueError(f"{where}.result records the error_code of a failed call")
    return TrajectoryCall(
        id=_text(value["id"], f"{where}.id"),
        tool=tool,
        arguments=arguments,
        status=result["status"],
        error_code=(
            _text(result["error_code"], f"{where}.result.error_code")
            if result["status"] == "error"
            else None
        ),
    )


def load_trajectory(path: Path) -> Trajectory:
    """Load one closed reference trajectory; its file is named after its id."""

    raw = _object(path)
    if set(raw) != _TRAJECTORY_KEYS or raw["schema_version"] != _TRAJECTORY_SCHEMA_VERSION:
        raise ValueError(f"{path.name} is not the closed trajectory v1 shape")
    trajectory_id = _text(raw["id"], f"{path.name} id")
    if path.stem != trajectory_id:
        raise ValueError(f"{path.name} must be named after its id {trajectory_id}")
    raw_turns = raw["turns"]
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ValueError(f"{path.name} turns must be a non-empty array")
    earlier: set[str] = set()
    turns: list[tuple[TrajectoryRound, ...]] = []
    for turn_index, raw_turn in enumerate(raw_turns, start=1):
        if not isinstance(raw_turn, dict) or set(raw_turn) != {"rounds"}:
            raise ValueError(f"{path.name} turn {turn_index} is not closed")
        raw_rounds = raw_turn["rounds"]
        if not isinstance(raw_rounds, list) or not raw_rounds:
            raise ValueError(f"{path.name} turn {turn_index} needs at least one round")
        rounds: list[TrajectoryRound] = []
        for round_index, raw_round in enumerate(raw_rounds, start=1):
            where = f"{path.name} turn {turn_index} round {round_index}"
            if not isinstance(raw_round, dict) or set(raw_round) != _ROUND_KEYS:
                raise ValueError(f"{where} is not closed")
            if not isinstance(raw_round["text"], str) or not isinstance(raw_round["calls"], list):
                raise ValueError(f"{where} needs text and a calls array")
            calls = tuple(
                _trajectory_call(call, f"{where} call {index}", earlier)
                for index, call in enumerate(raw_round["calls"], start=1)
            )
            for call in calls:
                if call.id in earlier:
                    raise ValueError(f"{where} repeats call id {call.id}")
                earlier.add(call.id)
            if not calls and round_index != len(raw_rounds):
                raise ValueError(f"{where} makes no call, so it must end its turn")
            rounds.append(TrajectoryRound(text=raw_round["text"], calls=calls))
        turns.append(tuple(rounds))
    return Trajectory(
        id=trajectory_id, case=_text(raw["case"], f"{path.name} case"), turns=tuple(turns)
    )


def load_trajectories(root: Path) -> tuple[Trajectory, ...]:
    """Load every reference trajectory under *root*, in file-name order."""

    trajectories = tuple(load_trajectory(path) for path in sorted(root.glob("*.json")))
    if not trajectories:
        raise ValueError("assistant trajectory directory is empty")
    return trajectories


def _result_status(message: Mapping[str, Any]) -> tuple[str, str | None]:
    content = message.get("content")
    error = content.get("error") if isinstance(content, Mapping) else None
    if error is None:
        return "ok", None
    code = error.get("code") if isinstance(error, Mapping) else None
    return "error", code if isinstance(code, str) else None


def _recorded_status(call: TrajectoryCall) -> tuple[str, str | None]:
    return call.status, call.error_code


class TrajectoryProvider:
    """A scripted provider that replays a reference trajectory through the real loop.

    Each round sends the recorded text and tool calls. An argument written as
    ``{"$result": "<call>.<key>"}`` is replaced by that key of the earlier
    call's result (a dotted key reads nested objects), so plan hashes flow from
    one round into the next. Before each round the provider compares the
    results the loop returned with the statuses and error codes the trajectory
    recorded; on the first difference it stops sending and ends the turn, and
    ``verify`` raises ``TrajectoryDivergedError`` naming the turn and round.
    The loop ends a turn without another round after a save that fails
    verification, ignoring the round's later calls, so ``verify`` reads every
    executed call from the harness's tool diagnostics.
    """

    def __init__(self, trajectory: Trajectory) -> None:
        self.trajectory = trajectory
        self.system: str | None = None
        # The messages of the first provider request, turn context included.
        self.first_messages: tuple[Mapping[str, Any], ...] | None = None
        self.divergence: str | None = None
        self._served: list[int] = [0] * len(trajectory.turns)

    def _locate(self, call_id: str) -> tuple[int, int]:
        for turn_index, rounds in enumerate(self.trajectory.turns, start=1):
            for round_index, trajectory_round in enumerate(rounds, start=1):
                if any(call.id == call_id for call in trajectory_round.calls):
                    return turn_index, round_index
        raise KeyError(call_id)

    def _diverge(self, turn: int, round_index: int, detail: str) -> None:
        if self.divergence is None:
            self.divergence = (
                f"trajectory {self.trajectory.id} diverged at turn {turn} "
                f"round {round_index}: {detail}"
            )

    def _resolve(self, value: object, results: Mapping[str, Mapping[str, Any]]) -> object:
        if isinstance(value, dict):
            if set(value) == {"$result"}:
                call_id, _, key = str(value["$result"]).partition(".")
                current: object = results[call_id].get("content")
                for part in key.split("."):
                    if not isinstance(current, Mapping) or part not in current:
                        raise LookupError(value["$result"])
                    current = current[part]
                return current
            return {name: self._resolve(item, results) for name, item in value.items()}
        if isinstance(value, list):
            return [self._resolve(item, results) for item in value]
        return value

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]:
        self.system = system
        if self.first_messages is None:
            self.first_messages = tuple(dict(message) for message in messages)
        turn = sum(1 for message in messages if message.get("role") == "user")
        results = {
            str(message["tool_call_id"]): message
            for message in messages
            if message.get("role") == "tool"
        }
        for turn_index, rounds in enumerate(self.trajectory.turns[:turn], start=1):
            for round_index, trajectory_round in enumerate(rounds, start=1):
                for call in trajectory_round.calls:
                    if call.id in results and _result_status(results[call.id]) != (
                        _recorded_status(call)
                    ):
                        self._diverge(
                            turn_index,
                            round_index,
                            f"{call.tool} call {call.id} returned "
                            f"{_result_status(results[call.id])}, recorded "
                            f"{_recorded_status(call)}",
                        )
        if turn > len(self.trajectory.turns):
            self._diverge(turn, 1, "the trajectory records no such turn")
        else:
            round_index = self._served[turn - 1] + 1
            rounds = self.trajectory.turns[turn - 1]
            if round_index > len(rounds):
                self._diverge(turn, round_index, "the loop asked for a round never recorded")
            elif self.divergence is None:
                trajectory_round = rounds[round_index - 1]
                try:
                    requests = [
                        ToolCallRequest(
                            call.id, call.tool, cast(dict, self._resolve(call.arguments, results))
                        )
                        for call in trajectory_round.calls
                    ]
                except (KeyError, LookupError) as exc:
                    self._diverge(turn, round_index, f"no earlier result holds {exc.args[0]}")
                else:
                    self._served[turn - 1] = round_index
                    if trajectory_round.text:
                        yield TextDelta(trajectory_round.text)
                    for request in requests:
                        yield request
                    yield TurnStop("tool_use" if requests else "end", _NO_USAGE)
                    return
        yield TurnStop("end", _NO_USAGE)

    def verify(self, diagnostics: Sequence[SelfTestToolDiagnostic]) -> None:
        """Raise ``TrajectoryDivergedError`` unless the loop replayed every recorded
        call with its recorded status and every recorded round was sent."""

        recorded = [
            call
            for rounds in self.trajectory.turns
            for trajectory_round in rounds
            for call in trajectory_round.calls
        ]
        for index, call in enumerate(recorded):
            turn, round_index = self._locate(call.id)
            if index >= len(diagnostics):
                self._diverge(turn, round_index, f"{call.tool} call {call.id} was never made")
                break
            observed = (diagnostics[index].status, diagnostics[index].error_code)
            if diagnostics[index].name != call.tool or observed != _recorded_status(call):
                self._diverge(
                    turn,
                    round_index,
                    f"{call.tool} call {call.id} returned {observed}, "
                    f"recorded {_recorded_status(call)}",
                )
                break
        for turn_index, rounds in enumerate(self.trajectory.turns, start=1):
            if self._served[turn_index - 1] < len(rounds):
                self._diverge(
                    turn_index,
                    self._served[turn_index - 1] + 1,
                    "the loop ended the turn before this round",
                )
        if self.divergence is not None:
            raise TrajectoryDivergedError(self.divergence)


def replay_config(trajectory: Trajectory) -> AssistantConfig:
    """The configuration a replay runs under; no provider request is ever made.

    Its organization trust is what a case keeps: the case's egress profile
    sets the rest of the policy when the case runs.
    """

    return AssistantConfig(
        provider="openai",
        model=trajectory.id,
        base_url="https://api.openai.com/v1",
        api_key="replay-never-sent",
        max_output_tokens=1024,
        egress=egress_policy("project", trust="organization"),
        endpoint_host="api.openai.com",
    )


async def replay_self_test_case(
    case: SelfTestCase,
    trajectory: Trajectory,
    *,
    projects_root: Path,
    work_dir: Path,
) -> SelfTestResult:
    """Replay *trajectory* for *case* through the real loop and tools, then score it.

    Raises ``TrajectoryDivergedError`` when a tool result's status differs from
    the recorded one, so a changed tool or contract names the case it breaks.
    """

    if trajectory.case != case.id:
        raise ValueError(f"trajectory {trajectory.id} replays {trajectory.case}, not {case.id}")
    if len(trajectory.turns) != len(case.turns):
        raise ValueError(
            f"trajectory {trajectory.id} records {len(trajectory.turns)} turns; "
            f"case {case.id} has {len(case.turns)}"
        )
    provider = TrajectoryProvider(trajectory)
    result = await run_self_test_case(
        case,
        projects_root=projects_root,
        config=replay_config(trajectory),
        work_dir=work_dir,
        provider_factory=lambda _config: provider,
        evidence="replay",
    )
    provider.verify(result.tool_diagnostics)
    return result


def _run_case_in_this_process(
    case: SelfTestCase,
    projects_root: Path,
    config: AssistantConfig,
    provider_factory: ProviderFactory,
    variant: SelfTestVariant,
    work_dir: Path,
    transcript_path: Path | None,
) -> SelfTestResult:
    transcript: list[dict[str, object]] | None = [] if transcript_path is not None else None
    try:
        return asyncio.run(
            run_self_test_case(
                case,
                projects_root=projects_root,
                config=config,
                work_dir=work_dir,
                provider_factory=provider_factory,
                variant=variant,
                transcript=transcript,
            )
        )
    finally:
        if transcript_path is not None and transcript is not None:
            write_transcript(transcript_path, transcript)


def run_self_test_cases_in_processes(
    cases: Sequence[SelfTestCase],
    *,
    projects_root: Path,
    config: AssistantConfig,
    provider_factory: ProviderFactory = create_provider,
    variant: SelfTestVariant = "multi_apply",
    transcripts: Path | None = None,
) -> tuple[SelfTestResult, ...]:
    """Run each case in its own spawned process, one after another.

    Process-wide state (caches, the sandbox root, imported project modules)
    cannot carry from one case into the next, so each result measures the model
    on a fresh process. *provider_factory* must be picklable by reference. With
    *transcripts*, each case writes ``<case id>.json`` there. The parent owns
    each case's disposable directory and removes it once the case's process
    has exited and released the locks it held there.
    """

    context = multiprocessing.get_context("spawn")
    results: list[SelfTestResult] = []
    for case in cases:
        with (
            tempfile.TemporaryDirectory(prefix="haute-eval-") as work_dir,
            ProcessPoolExecutor(max_workers=1, mp_context=context) as pool,
        ):
            results.append(
                pool.submit(
                    _run_case_in_this_process,
                    case,
                    projects_root,
                    config,
                    provider_factory,
                    variant,
                    Path(work_dir),
                    None if transcripts is None else transcripts / f"{case.id}.json",
                ).result()
            )
    return tuple(results)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_EVAL_ROOT = _REPOSITORY_ROOT / "tests" / "assistant_eval"
_DEFAULT_CASES = _EVAL_ROOT / "cases"
_DEFAULT_PROJECTS = _EVAL_ROOT / "projects"
_DEFAULT_MATRIX = _EVAL_ROOT / "support_matrix.json"


def new_run_id() -> str:
    """A run id that sorts by start time."""

    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    parser.add_argument("--projects", type=Path, default=_DEFAULT_PROJECTS)
    parser.add_argument("--case", action="append", default=[], help="Case id; repeatable.")
    parser.add_argument("--area", action="append", default=[], help="Area; repeatable.")
    parser.add_argument("--split", action="append", default=[], help="Split; repeatable.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the assistant evaluation live against the configured provider in disposable "
            "projects, compare two reports, or list the cases."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser(
        "record",
        help=(
            "Run the selected cases against the configured provider (real provider requests) "
            "and write the redacted report, listing as not applicable the cases that do not "
            "apply to the variant; after each turn the harness executes only that turn's "
            "golden nodes, never a sink."
        ),
    )
    _add_selection(record)
    record.add_argument("--variant", choices=VARIANTS, default="multi_apply")
    record.add_argument(
        "--config-root",
        type=Path,
        default=Path.cwd(),
        help=(
            "Project containing the .env and [assistant] provider to use; each case runs "
            "under its own egress profile at this provider's trust."
        ),
    )
    record.add_argument(
        "--output",
        type=Path,
        help="Report path; default .haute/assistant-eval/<run id>/report.json in the config root.",
    )
    record.add_argument(
        "--transcripts",
        action="store_true",
        help=(
            "Also write each case's transcript (requests, text, tool payloads) under the config "
            "root's Git-ignored .haute/assistant-eval/<run id>/transcripts/."
        ),
    )
    record.add_argument("--matrix", type=Path, default=_DEFAULT_MATRIX)
    compare = commands.add_parser("compare", help="Compare two reports of one evidence kind.")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    listing = commands.add_parser("list", help="List the selected cases without provider calls.")
    _add_selection(listing)
    return parser


def _selected(args: argparse.Namespace) -> tuple[SelfTestCase, ...]:
    return select_self_test_cases(
        load_self_test_cases(args.cases, projects_root=args.projects),
        args.case,
        areas=args.area,
        splits=args.split,
    )


def _record(args: argparse.Namespace) -> int:
    selected = _selected(args)
    applicable = tuple(case for case in selected if args.variant not in case.inapplicable_variants)
    not_applicable = tuple(case for case in selected if args.variant in case.inapplicable_variants)
    if not applicable:
        raise ValueError(f"No selected evaluation case applies to the {args.variant} variant")
    config_root = args.config_root.resolve()
    _load_env(config_root)
    config = resolve_assistant_config(config_root)
    # Refuse an external provider before any case runs; each case then runs
    # under its own egress profile at this trust.
    evaluation_trust(config)
    run_id = new_run_id()
    run_dir = config_root / ".haute" / "assistant-eval" / run_id
    transcripts = run_dir / "transcripts" if args.transcripts else None
    if transcripts is not None:
        require_git_ignored(transcripts)
    run = RunIdentity(
        run_id=run_id,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        variant=args.variant,
        provider=config.provider,
        model=config.model,
        configuration=configuration_for(
            load_support_matrix(args.matrix), provider=config.provider, model=config.model
        ),
        haute_version=haute.__version__,
    )
    results = run_self_test_cases_in_processes(
        applicable,
        projects_root=args.projects,
        config=config,
        variant=args.variant,
        transcripts=transcripts,
    )
    output = args.output if args.output is not None else run_dir / "report.json"
    write_report(output, results, run, not_applicable=not_applicable)
    payload = report_payload(results, run, not_applicable=not_applicable)
    print(
        json.dumps(
            {
                "report": str(output.resolve()),
                "passed": payload["passed"],
                "areas": {
                    area: {
                        "cases": summary["cases"],
                        "passed": summary["passed"],
                        "not_applicable": summary["not_applicable"],
                    }
                    for area, summary in cast(
                        Mapping[str, Mapping[str, object]], payload["areas"]
                    ).items()
                },
            },
            sort_keys=True,
        )
    )
    return 0 if payload["passed"] else 1


def _run(args: argparse.Namespace) -> int:
    if args.command == "record":
        return _record(args)
    if args.command == "compare":
        print(json.dumps(compare_report_files(args.before, args.after), sort_keys=True, indent=2))
        return 0
    print(
        json.dumps(
            {
                "cases": [
                    {
                        "id": case.id,
                        "area": case.area,
                        "split": case.split,
                        "project_fixture": case.project_fixture,
                        "egress": case.egress,
                        "inapplicable_variants": list(case.inapplicable_variants),
                        "turns": len(case.turns),
                    }
                    for case in _selected(args)
                ],
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return _run(_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
