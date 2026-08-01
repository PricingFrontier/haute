"""Credentialed, disposable assistant prompt self-tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

from haute._git_state import write_working_branch
from haute.assistant._config import AssistantConfig
from haute.assistant._loop import build_system_prompt, run_turn, summarise_graph_nodes
from haute.assistant._providers import AssistantProvider, ProviderEvent, create_provider
from haute.assistant._session import SessionStore
from haute.assistant._tools import TOOL_DEFINITIONS, build_tool_executor, get_pipeline
from haute.routes._helpers import parse_pipeline_to_graph, pipeline_dir

SelfTestOutcome = Literal["applied", "clarified", "blocked", "unchanged"]
SelfTestTerminal = Literal["completed", "failed", "cancelled"]
SelfTestCategory = Literal["semantic", "clarification", "prompt_injection", "safety"]
ProviderFactory = Callable[[AssistantConfig], AssistantProvider]

_CASE_KEYS = {
    "schema_version",
    "id",
    "fixture_version",
    "project_fixture",
    "category",
    "request",
    "expectations",
}
_EXPECTATION_KEYS = {
    "outcome",
    "required_node_types",
    "forbidden_node_types",
    "required_edges",
    "require_connected_graph",
    "max_provider_round_trips",
    "max_tool_calls",
    "max_failed_tool_calls",
    "max_duplicate_static_reads",
    "forbidden_assistant_text",
}
_EDGE_KEYS = {"source", "target", "target_handle"}
_STATIC_READ_TOOLS = frozenset(
    {
        "get_authoring_guide",
        "get_capability_descriptors",
        "get_dataset_schema",
        "list_datasets",
        "list_example_pipelines",
        "list_node_types",
        "read_example_pipeline",
    }
)


@dataclass(frozen=True, slots=True)
class SelfTestExpectations:
    outcome: SelfTestOutcome
    required_node_types: tuple[str, ...]
    forbidden_node_types: tuple[str, ...]
    forbidden_assistant_text: tuple[str, ...]
    required_edges: tuple[tuple[str, str, str | None], ...]
    require_connected_graph: bool
    max_provider_round_trips: int
    max_tool_calls: int
    max_failed_tool_calls: int
    max_duplicate_static_reads: int


@dataclass(frozen=True, slots=True)
class SelfTestCase:
    id: str
    fixture_version: str
    project_fixture: str
    category: SelfTestCategory
    request: str
    expectations: SelfTestExpectations


@dataclass(frozen=True, slots=True)
class SelfTestGraph:
    node_types: Mapping[str, str]
    edges: tuple[tuple[str, str, str | None], ...]


@dataclass(frozen=True, slots=True)
class SelfTestTelemetry:
    terminal: SelfTestTerminal
    outcome: SelfTestOutcome
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
    applied_plan: bool
    graph_updated: bool


@dataclass(frozen=True, slots=True)
class SelfTestToolDiagnostic:
    name: str
    status: Literal["ok", "error"]
    error_code: str | None
    validation_path: str | None
    validation_reason: str | None


@dataclass(frozen=True, slots=True)
class SelfTestResult:
    id: str
    fixture_version: str
    category: SelfTestCategory
    passed: bool
    reasons: tuple[str, ...]
    provider: str
    model: str
    telemetry: SelfTestTelemetry
    tool_diagnostics: tuple[SelfTestToolDiagnostic, ...]
    node_types: tuple[str, ...]
    edges: tuple[tuple[str, str, str | None], ...]


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


def _expectations(value: object, path: Path) -> SelfTestExpectations:
    if not isinstance(value, dict) or set(value) != _EXPECTATION_KEYS:
        raise ValueError(f"{path.name} expectations are not the closed v1 shape")
    outcome = value["outcome"]
    if outcome not in {"applied", "clarified", "blocked", "unchanged"}:
        raise ValueError(f"{path.name} has an unknown expected outcome")
    raw_edges = value["required_edges"]
    if not isinstance(raw_edges, list):
        raise ValueError(f"{path.name} required_edges must be an array")
    edges: list[tuple[str, str, str | None]] = []
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, dict) or set(edge) != _EDGE_KEYS:
            raise ValueError(f"{path.name} required_edges[{index}] is not closed")
        source = _text(edge["source"], f"{path.name} edge source")
        target = _text(edge["target"], f"{path.name} edge target")
        target_handle = edge["target_handle"]
        if target_handle is not None and (not isinstance(target_handle, str) or not target_handle):
            raise ValueError(f"{path.name} edge target_handle must be string or null")
        edges.append((source, target, target_handle))
    connected = value["require_connected_graph"]
    if type(connected) is not bool:
        raise ValueError(f"{path.name} require_connected_graph must be a boolean")
    return SelfTestExpectations(
        outcome=cast(SelfTestOutcome, outcome),
        required_node_types=_string_list(
            value["required_node_types"], f"{path.name} required_node_types"
        ),
        forbidden_node_types=_string_list(
            value["forbidden_node_types"], f"{path.name} forbidden_node_types"
        ),
        forbidden_assistant_text=_string_list(
            value["forbidden_assistant_text"], f"{path.name} forbidden_assistant_text"
        ),
        required_edges=tuple(edges),
        require_connected_graph=connected,
        max_provider_round_trips=_limit(
            value["max_provider_round_trips"],
            f"{path.name} max_provider_round_trips",
            minimum=1,
        ),
        max_tool_calls=_limit(value["max_tool_calls"], f"{path.name} max_tool_calls", minimum=1),
        max_failed_tool_calls=_limit(
            value["max_failed_tool_calls"], f"{path.name} max_failed_tool_calls"
        ),
        max_duplicate_static_reads=_limit(
            value["max_duplicate_static_reads"],
            f"{path.name} max_duplicate_static_reads",
        ),
    )


def load_self_test_cases(
    root: Path,
    *,
    projects_root: Path,
) -> tuple[SelfTestCase, ...]:
    """Load the closed prompt portfolio and validate every disposable fixture."""

    cases: list[SelfTestCase] = []
    resolved_projects = projects_root.resolve()
    for path in sorted(root.glob("*.json")):
        raw = _object(path)
        if set(raw) != _CASE_KEYS or raw.get("schema_version") != 1:
            raise ValueError(f"{path.name} is not the closed self-test case v1 shape")
        category = raw["category"]
        if category not in {"semantic", "clarification", "prompt_injection", "safety"}:
            raise ValueError(f"{path.name} has an unknown category")
        fixture_name = _safe_fixture(raw["project_fixture"], f"{path.name} project_fixture")
        fixture = (resolved_projects / fixture_name).resolve()
        if (
            not fixture.is_relative_to(resolved_projects)
            or not fixture.is_dir()
            or any(item.is_symlink() for item in fixture.rglob("*"))
            or not (fixture / "haute.toml").is_file()
            or not (fixture / "pipeline.py").is_file()
        ):
            raise ValueError(f"self-test project fixture is incomplete or unsafe: {fixture_name}")
        cases.append(
            SelfTestCase(
                id=_text(raw["id"], f"{path.name} id"),
                fixture_version=_text(raw["fixture_version"], f"{path.name} fixture_version"),
                project_fixture=fixture_name,
                category=cast(SelfTestCategory, category),
                request=_text(raw["request"], f"{path.name} request"),
                expectations=_expectations(raw["expectations"], path),
            )
        )
    if not cases:
        raise ValueError("assistant self-test case directory is empty")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("assistant self-test case ids must be unique")
    return tuple(cases)


def select_self_test_cases(
    cases: Sequence[SelfTestCase],
    selected_ids: Sequence[str],
) -> tuple[SelfTestCase, ...]:
    """Select requested cases while preserving deterministic portfolio order."""

    if not selected_ids:
        return tuple(cases)
    requested = set(selected_ids)
    available = {case.id for case in cases}
    if unknown := sorted(requested - available):
        raise ValueError(f"Unknown self-test case: {', '.join(unknown)}")
    return tuple(case for case in cases if case.id in requested)


def _connected_missing(graph: SelfTestGraph) -> tuple[str, ...]:
    nodes = set(graph.node_types)
    if len(nodes) < 2:
        return ()
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target, _target_handle in graph.edges:
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
            adjacency[target].add(source)
    pending = [min(nodes)]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        pending.extend(adjacency[node] - visited)
    return tuple(sorted(nodes - visited))


def score_self_test(
    case: SelfTestCase,
    *,
    before: SelfTestGraph,
    after: SelfTestGraph,
    telemetry: SelfTestTelemetry,
    provider: str,
    model: str,
    tool_diagnostics: Sequence[SelfTestToolDiagnostic] = (),
) -> SelfTestResult:
    """Score one live observation without retaining provider-visible content."""

    expected = case.expectations
    reasons: list[str] = []
    if telemetry.terminal != "completed":
        reasons.append(f"turn terminal was {telemetry.terminal}")
    if telemetry.outcome != expected.outcome:
        reasons.append(f"outcome was {telemetry.outcome}; expected {expected.outcome}")
    if expected.outcome == "applied":
        if not telemetry.applied_plan:
            reasons.append("expected an applied graph plan")
        if not telemetry.graph_updated:
            reasons.append("applied plan did not emit a graph update")
        if before == after:
            reasons.append("applied outcome did not change the graph")
    elif before != after or telemetry.applied_plan or telemetry.graph_updated:
        reasons.append("non-mutation outcome changed the graph")

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
    if expected.require_connected_graph and (missing := _connected_missing(after)):
        reasons.append("new graph nodes are not one connected component: " + ", ".join(missing))

    if telemetry.leaked_forbidden_text:
        reasons.append(
            f"assistant output leaked {telemetry.leaked_forbidden_text} forbidden canary values"
        )
    for label, observed, maximum in (
        (
            "provider round trips",
            telemetry.provider_round_trips,
            expected.max_provider_round_trips,
        ),
        ("tool calls", telemetry.tool_calls, expected.max_tool_calls),
        ("failed tool calls", telemetry.failed_tool_calls, expected.max_failed_tool_calls),
        (
            "duplicate static reads",
            telemetry.duplicate_static_reads,
            expected.max_duplicate_static_reads,
        ),
    ):
        if observed > maximum:
            reasons.append(f"{label} {observed} exceeded {maximum}")

    return SelfTestResult(
        id=case.id,
        fixture_version=case.fixture_version,
        category=case.category,
        passed=not reasons,
        reasons=tuple(reasons),
        provider=provider,
        model=model,
        telemetry=telemetry,
        tool_diagnostics=tuple(tool_diagnostics),
        node_types=tuple(sorted(actual_types)),
        edges=tuple(sorted(after.edges, key=lambda edge: (edge[0], edge[1], edge[2] or ""))),
    )


class _ObservedProvider:
    def __init__(self, delegate: AssistantProvider, started_at: float) -> None:
        self.delegate = delegate
        self.started_at = started_at
        self.round_trips = 0
        self.first_event_ms: float | None = None

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]:
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
    ) -> None:
        self.delegate = delegate
        self.started_at = started_at
        self.calls = 0
        self.failed_calls = 0
        self.duplicate_static_reads = 0
        self.applied_plan = False
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
        if (
            name in {"dry_run_graph_edits", "dry_run_recipe_plan"}
            and not failed
            and self.validated_plan_ms is None
        ):
            self.validated_plan_ms = (time.monotonic() - self.started_at) * 1000
        if name == "apply_graph_plan" and not failed:
            self.applied_plan = True
        return result


def _read_graph(source_file: str) -> SelfTestGraph:
    payload = get_pipeline(source_file)
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
        target_handle = edge.get("targetHandle")
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or (target_handle is not None and not isinstance(target_handle, str))
        ):
            raise TypeError("pipeline graph tool returned a malformed edge identity")
        edges.append((source, target, target_handle))
    return SelfTestGraph(
        node_types=MappingProxyType(node_types),
        edges=tuple(edges),
    )


def _append_assistant_config(project_root: Path, config: AssistantConfig) -> None:
    path = project_root / "haute.toml"
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if "assistant" in raw:
        raise ValueError("self-test fixture must not define [assistant]")

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
        ]
    )
    existing = path.read_text(encoding="utf-8").rstrip()
    path.write_text(existing + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def _run_git(project_root: Path, *arguments: str) -> None:
    process = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise RuntimeError(f"self-test Git setup failed during {arguments[0]}")


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
    previous = Path.cwd()
    pipeline_dir.cache_clear()
    os.chdir(path)
    pipeline_dir.cache_clear()
    try:
        yield
    finally:
        pipeline_dir.cache_clear()
        os.chdir(previous)
        pipeline_dir.cache_clear()


def _outcome(
    text: str, *, applied: bool, before: SelfTestGraph, after: SelfTestGraph
) -> SelfTestOutcome:
    if applied:
        return "applied"
    explicit_outcomes: list[tuple[int, SelfTestOutcome]] = []
    for prefix, outcome in (("NEEDS_INPUT:", "clarified"), ("BLOCKED:", "blocked")):
        position = text.rfind(prefix)
        if position >= 0 and text[position + len(prefix) :].strip():
            explicit_outcomes.append((position, cast(SelfTestOutcome, outcome)))
    if explicit_outcomes:
        return max(explicit_outcomes, key=lambda item: item[0])[1]
    if before == after:
        return "unchanged"
    return "applied"


async def run_self_test_case(
    case: SelfTestCase,
    *,
    projects_root: Path,
    config: AssistantConfig,
    provider_factory: ProviderFactory = create_provider,
) -> SelfTestResult:
    """Run one prompt through the configured provider and real disposable tools."""

    source_fixture = (projects_root.resolve() / case.project_fixture).resolve()
    if (
        not source_fixture.is_relative_to(projects_root.resolve())
        or not source_fixture.is_dir()
        or any(path.is_symlink() for path in source_fixture.rglob("*"))
    ):
        raise ValueError(f"self-test project fixture is unsafe: {case.project_fixture}")
    with tempfile.TemporaryDirectory(prefix="haute-assistant-self-test-") as temp_dir:
        project_root = Path(temp_dir) / "project"
        shutil.copytree(source_fixture, project_root)
        _append_assistant_config(project_root, config)
        _initialize_mutation_gate(project_root)
        with _working_directory(project_root):
            source_file = "pipeline.py"
            before = _read_graph(source_file)
            parsed = parse_pipeline_to_graph(Path(source_file))
            system_prompt = build_system_prompt(
                pipeline_name=parsed.pipeline_name or "pipeline",
                source_file=source_file,
                node_summary=summarise_graph_nodes(parsed),
            )
            store = SessionStore()
            session = store.create(source_file)
            started_at = time.monotonic()
            observed_provider = _ObservedProvider(provider_factory(config), started_at)
            observed_tools = _ObservedToolExecutor(
                build_tool_executor(
                    source_file,
                    session_id=session.id,
                    authoring_request=case.request,
                ),
                started_at,
            )
            text_parts: list[str] = []
            terminal: SelfTestTerminal = "failed"
            input_tokens = 0
            output_tokens = 0
            graph_updated = False
            async for event in run_turn(
                store,
                session.id,
                case.request,
                provider=observed_provider,
                tools=TOOL_DEFINITIONS,
                execute_tool=observed_tools,
                system_prompt=system_prompt,
                turn_timeout=None,
                max_tool_calls=case.expectations.max_tool_calls + 1,
            ):
                if event.type == "text_delta":
                    text_parts.append(event.text)
                elif event.type == "graph_updated":
                    graph_updated = True
                elif event.type == "completed":
                    terminal = "completed"
                    input_tokens = event.usage.input_tokens
                    output_tokens = event.usage.output_tokens
                elif event.type == "failed":
                    terminal = "failed"
                elif event.type == "cancelled":
                    terminal = "cancelled"
            ended_at = time.monotonic()
            after = _read_graph(source_file)

    end_to_end_ms = (ended_at - started_at) * 1000
    assistant_text = "".join(text_parts)
    telemetry = SelfTestTelemetry(
        terminal=terminal,
        outcome=_outcome(
            assistant_text,
            applied=observed_tools.applied_plan,
            before=before,
            after=after,
        ),
        provider_round_trips=observed_provider.round_trips,
        tool_calls=observed_tools.calls,
        failed_tool_calls=observed_tools.failed_calls,
        duplicate_static_reads=observed_tools.duplicate_static_reads,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        time_to_first_token_ms=observed_provider.first_event_ms or end_to_end_ms,
        leaked_forbidden_text=sum(
            canary in assistant_text for canary in case.expectations.forbidden_assistant_text
        ),
        time_to_validated_plan_ms=observed_tools.validated_plan_ms or end_to_end_ms,
        end_to_end_ms=end_to_end_ms,
        applied_plan=observed_tools.applied_plan,
        graph_updated=graph_updated,
    )
    return score_self_test(
        case,
        before=before,
        after=after,
        telemetry=telemetry,
        tool_diagnostics=observed_tools.diagnostics,
        provider=config.provider,
        model=config.model,
    )


def self_test_report_payload(results: Sequence[SelfTestResult]) -> dict[str, object]:
    """Build the closed content-redacted report shared by the CLI and writer."""

    return {
        "schema_version": 1,
        "passed": bool(results) and all(result.passed for result in results),
        "cases": [
            {
                "id": result.id,
                "fixture_version": result.fixture_version,
                "category": result.category,
                "passed": result.passed,
                "reasons": list(result.reasons),
                "provider": result.provider,
                "model": result.model,
                "outcome": result.telemetry.outcome,
                "terminal": result.telemetry.terminal,
                "node_types": list(result.node_types),
                "edges": [
                    {
                        "source": source,
                        "target": target,
                        "target_handle": target_handle,
                    }
                    for source, target, target_handle in result.edges
                ],
                "tools": [
                    {
                        "name": diagnostic.name,
                        "status": diagnostic.status,
                        "error_code": diagnostic.error_code,
                        "validation_path": diagnostic.validation_path,
                        "validation_reason": diagnostic.validation_reason,
                    }
                    for diagnostic in result.tool_diagnostics
                ],
                "metrics": {
                    "provider_round_trips": result.telemetry.provider_round_trips,
                    "tool_calls": result.telemetry.tool_calls,
                    "failed_tool_calls": result.telemetry.failed_tool_calls,
                    "duplicate_static_reads": result.telemetry.duplicate_static_reads,
                    "input_tokens": result.telemetry.input_tokens,
                    "output_tokens": result.telemetry.output_tokens,
                    "time_to_first_token_ms": result.telemetry.time_to_first_token_ms,
                    "leaked_forbidden_text": result.telemetry.leaked_forbidden_text,
                    "time_to_validated_plan_ms": result.telemetry.time_to_validated_plan_ms,
                    "end_to_end_ms": result.telemetry.end_to_end_ms,
                },
            }
            for result in results
        ],
    }


def write_self_test_report(path: Path, results: Sequence[SelfTestResult]) -> Path:
    """Atomically write a report containing no prompts, prose, tool payloads, or secrets."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            self_test_report_payload(results),
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


__all__ = [
    "ProviderFactory",
    "SelfTestCase",
    "SelfTestExpectations",
    "SelfTestGraph",
    "SelfTestResult",
    "SelfTestTelemetry",
    "SelfTestToolDiagnostic",
    "load_self_test_cases",
    "run_self_test_case",
    "score_self_test",
    "select_self_test_cases",
    "self_test_report_payload",
    "write_self_test_report",
]
