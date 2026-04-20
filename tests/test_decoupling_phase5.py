"""Phase 5 Wave 9E — decoupling + logging convention enforcement.

This file covers three related items:

* **#104 Trace decoupling** — ``haute.trace`` currently reaches into
  ``haute.executor._preview_cache``, a private module global.  The dev
  will replace that reach-through with explicit dependency injection
  (either a ``preview`` / ``preview_cache_reader`` parameter or a
  ``Trace.from_preview(...)`` classmethod).  Tests here enforce the new
  contract via a mix of static AST analysis and end-to-end functional
  assertions that construct a trace against a fake preview reader with
  *no* real executor involved.

* **#127 File-watcher event bus** — ``broadcast(...)`` in
  ``routes/_helpers.py`` is the single chokepoint for WebSocket dispatch
  and the file-watcher in ``server.py`` hand-builds message dicts with
  hardcoded ``type`` strings (``graph_update``, ``parse_error``).  The
  dev will introduce a minimal ``EventBus`` abstraction (``subscribe`` /
  ``publish`` / unsubscribe).  Tests pin the public contract of the bus
  so any plausible implementation passes.

* **#132 Logging convention enforcement** — The *decision* has been
  made: ``structlog`` for server / internal code, ``click.echo`` (and
  ``click.secho``) for CLI user-facing output.  No ``print()`` anywhere.
  Tests here AST-walk ``src/haute/`` to enforce this convention.  The
  convention itself is documented in the ``haute._logging`` module
  docstring — another static assertion covers that.

Every test in this file is expected to FAIL before the dev lands the
corresponding production change.  They are deliberately light on
assumptions about *where* the dev places the ``EventBus`` implementation
(it just needs to live at a ``haute`` dotted path) and *how* the trace
receives its preview snapshot (parameter name ``preview`` or
``preview_cache_reader`` or ``preview_reader`` are all accepted by the
functional tests).  Mechanical naming is left to the dev; the tests
pin behaviour.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest

import haute.trace as trace_module
from haute.graph_utils import PipelineGraph

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "haute"
CLI_ROOT = SRC_ROOT / "cli"


def _iter_py_files(root: Path, *, exclude_dirs: tuple[Path, ...] = ()) -> list[Path]:
    """Yield every ``*.py`` under *root*, skipping ``__pycache__`` and *exclude_dirs*."""
    exclude_resolved = [d.resolve() for d in exclude_dirs]
    out: list[Path] = []
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        resolved = p.resolve()
        if any(str(resolved).startswith(str(ex)) for ex in exclude_resolved):
            continue
        out.append(p)
    return out


def _parse_tree(path: Path) -> ast.Module:
    """Parse *path* and return its AST, with the filename attached for errors."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_name(call: ast.Call) -> str | None:
    """Return the ``foo`` in ``foo(...)`` or ``x.foo(...)``.  ``None`` otherwise."""
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return None


def _is_docstring_node(node: ast.stmt | ast.expr) -> bool:
    """True iff *node* is an ``Expr`` wrapping a string constant (a docstring)."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


# ===========================================================================
# #104 Trace decoupling
# ===========================================================================


class TestTraceDecoupling:
    """Trace must not reach into ``executor._preview_cache`` directly.

    The public contract we want: trace receives its preview data through
    an explicit parameter — either a snapshot dict / Pydantic model, a
    reader Protocol, or a classmethod like ``Trace.from_preview(...)``.
    All three shapes are allowed; what is forbidden is the current
    ``from haute.executor import _preview_cache`` + ``_preview_cache.try_get(...)``
    reach-through.
    """

    # -- AST-level static guards ------------------------------------------

    def test_trace_does_not_import_executor_preview_cache(self) -> None:
        """``trace.py`` must not import ``_preview_cache`` from executor.

        Importing a private (underscore-prefixed) symbol across module
        boundaries is the tell-tale sign of reach-through coupling.
        After the dev's refactor, trace receives preview data as a
        parameter instead.
        """
        tree = _parse_tree(SRC_ROOT / "trace.py")
        offenders: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "haute.executor":
                continue
            for alias in node.names:
                if alias.name == "_preview_cache":
                    offenders.append((alias.name, node.lineno))

        assert not offenders, (
            "trace.py must not import haute.executor._preview_cache — the preview "
            "snapshot should be passed in via a parameter / Protocol / classmethod. "
            f"Offenders at lines: {[ln for _, ln in offenders]}"
        )

    def test_trace_does_not_reference_preview_cache_attr_on_executor(self) -> None:
        """No attribute access of the form ``executor._preview_cache`` anywhere in trace.py.

        Catches sneaky re-acquisition of the private attribute through
        ``haute.executor`` (e.g. ``from haute import executor; executor._preview_cache``)
        even if the direct import was removed.
        """
        tree = _parse_tree(SRC_ROOT / "trace.py")
        offenders: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_preview_cache":
                offenders.append(node.lineno)
        assert not offenders, (
            "trace.py references a ``_preview_cache`` attribute — probably "
            "``executor._preview_cache``.  Pass the preview snapshot as a "
            f"parameter instead.  Offenders at lines: {offenders}"
        )

    def test_trace_module_has_no_preview_cache_global(self) -> None:
        """After the refactor, ``trace.py`` should not expose a module-level
        ``_preview_cache`` name at all — no import, no alias, no re-export."""
        assert not hasattr(trace_module, "_preview_cache"), (
            "haute.trace._preview_cache still exists.  The reach-through should "
            "be fully excised — drop the import, do not alias it."
        )

    def test_trace_does_not_import_private_executor_symbols_for_state(self) -> None:
        """Importing helpers like ``_build_node_fn`` / ``_compile_preamble`` is OK
        (they are pure functions).  Importing private *stateful* singletons from
        executor (anything matching ``*_cache`` or ``*_registry``) is not.
        """
        tree = _parse_tree(SRC_ROOT / "trace.py")
        bad: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "haute.executor":
                continue
            for alias in node.names:
                name = alias.name
                if name.endswith("_cache") or name.endswith("_registry"):
                    bad.append(name)
        assert not bad, (
            "trace.py imports private stateful singleton(s) from executor: "
            f"{bad}.  These indicate reach-through state coupling."
        )

    # -- Functional end-to-end injection ---------------------------------

    def test_execute_trace_accepts_preview_snapshot_parameter(self) -> None:
        """``execute_trace`` must accept an explicit preview parameter.

        The dev may call it ``preview``, ``preview_snapshot``,
        ``preview_cache_reader``, or ``preview_reader`` — we just check
        at least one of those names is in the signature.
        """
        sig = inspect.signature(trace_module.execute_trace)
        accepted = {"preview", "preview_snapshot", "preview_cache_reader", "preview_reader"}
        matched = accepted & set(sig.parameters)
        assert matched, (
            "execute_trace() must accept one of "
            f"{sorted(accepted)} so callers can inject a preview snapshot. "
            f"Current parameters: {sorted(sig.parameters)}"
        )

    def test_trace_renders_without_executor_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: a Trace built against a fake preview reader must render
        without touching the real ``haute.executor._preview_cache``.

        We swap ``haute.executor._preview_cache`` for an object that raises
        on every attribute access, then run the trace with a caller-provided
        preview snapshot.  If the trace still succeeds, the decoupling is
        real; if it touches the singleton, we get a loud AttributeError.
        """
        import haute.executor as executor_module
        import polars as pl

        from haute.graph_utils import GraphEdge, GraphNode, NodeData

        class _Exploding:
            """Any attribute access raises — proves the singleton is unused."""

            def __getattribute__(self, name: str) -> Any:  # pragma: no cover - raising stub
                raise AttributeError(
                    f"executor._preview_cache was accessed ({name=}) — trace is "
                    "still coupled to the executor singleton."
                )

        monkeypatch.setattr(executor_module, "_preview_cache", _Exploding(), raising=True)

        # Build a minimal single-node graph with a fake "preview snapshot"
        # that contains the output DataFrame trace will read.
        node = GraphNode(
            id="n1",
            data=NodeData(label="n1", nodeType="dataSource", config={"path": "data.parquet"}),
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        df = pl.DataFrame({"col": [1, 2, 3]})

        # Every allowed preview-parameter name — we try each.
        tried: list[str] = []
        for kw in ("preview", "preview_snapshot", "preview_cache_reader", "preview_reader"):
            if kw not in inspect.signature(trace_module.execute_trace).parameters:
                continue
            tried.append(kw)
            try:
                result = trace_module.execute_trace(
                    graph=graph,
                    target_node_id="n1",
                    row_index=0,
                    **{kw: {"eager_outputs": {"n1": df}}},
                )
            except AttributeError as exc:  # pragma: no cover - decoupling failure
                pytest.fail(
                    f"execute_trace still touched executor._preview_cache via {kw}=: {exc}"
                )
            else:
                assert result is not None
                assert result.target_node_id == "n1"
                return

        pytest.fail(
            "execute_trace does not accept any of the expected preview parameters "
            f"(tried {tried}).  See test_execute_trace_accepts_preview_snapshot_parameter."
        )

    def test_trace_respects_injected_empty_preview_reader(self) -> None:
        """If the injected preview reader returns no cached outputs, the
        trace must still be able to execute the graph from scratch — it
        cannot silently fall back to the real singleton.
        """
        import polars as pl

        from haute.graph_utils import GraphNode, NodeData

        class _EmptyReader:
            """Minimal reader protocol — always a miss."""

            def try_get(self, fingerprint: str) -> dict[str, Any] | None:
                return None

        node = GraphNode(
            id="only",
            data=NodeData(
                label="only",
                nodeType="polars",
                config={"code": "df = pl.DataFrame({'x': [1]}).lazy()"},
            ),
        )
        graph = PipelineGraph(nodes=[node], edges=[])

        # Does execute_trace accept a reader protocol?  Accept any of the
        # documented parameter names.
        sig = inspect.signature(trace_module.execute_trace)
        kw = next(
            (
                p
                for p in ("preview_cache_reader", "preview_reader", "preview", "preview_snapshot")
                if p in sig.parameters
            ),
            None,
        )
        assert kw is not None, "execute_trace does not accept a preview-injection parameter"

        # Construct an ``execute_trace`` call.  Tolerate different
        # signatures: pass the reader as the matched kw and just the
        # graph positionally.
        result = trace_module.execute_trace(graph=graph, **{kw: _EmptyReader()})
        assert result is not None
        assert isinstance(result.steps, list)
        # Basic sanity: the reader returning None should not raise and
        # should not silently use the real executor cache.
        assert result.target_node_id == "only"

        # Prove the trace's own (per-call) execution path ran — the
        # single-row output should contain our generated column.
        assert any("x" in step.output_values for step in result.steps), (
            "The trace ran but produced no row data — the injected empty reader "
            "was probably not actually the source of data."
        )


# ===========================================================================
# #127 File-watcher event bus
# ===========================================================================


def _import_event_bus() -> tuple[Any, str]:
    """Locate and import the ``EventBus`` class.

    The dev may place it at ``haute._event_bus``, ``haute.events``, or
    ``haute.routes._event_bus``.  Any of those is acceptable — what we
    pin is the public API.  Returns ``(EventBusClass, dotted_path)``.
    """
    for dotted in (
        "haute._event_bus",
        "haute.events",
        "haute.routes._event_bus",
        "haute.routes._events",
        "haute._events",
    ):
        try:
            mod = importlib.import_module(dotted)
        except ImportError:
            continue
        for attr in ("EventBus", "Bus"):
            cls = getattr(mod, attr, None)
            if cls is not None and inspect.isclass(cls):
                return cls, f"{dotted}.{attr}"
    raise ImportError(
        "Could not locate EventBus.  Expected one of "
        "haute._event_bus / haute.events / haute.routes._event_bus / "
        "haute.routes._events / haute._events."
    )


class TestEventBus:
    """Minimal pub/sub contract for the file-watcher event bus.

    Everything here is a pure unit test.  The bus may be async or
    sync — tests assert behaviour, not the call style.
    """

    def test_event_bus_is_importable(self) -> None:
        """EventBus must be importable from *some* haute path."""
        cls, dotted = _import_event_bus()
        assert cls is not None
        assert "EventBus" in dotted or "Bus" in dotted

    def test_publish_with_no_subscribers_is_noop(self) -> None:
        """Publishing to an event with no subscribers must not raise."""
        cls, _ = _import_event_bus()
        bus = cls()
        # Must not raise
        result = bus.publish("file.changed", {"path": "a.py"})
        # If the impl returns something (e.g. coroutine), it is allowed,
        # but we do not assert on it — just that the call survives.
        _ = result

    def test_subscriber_receives_payload(self) -> None:
        """A registered handler must be invoked with the published payload."""
        cls, _ = _import_event_bus()
        bus = cls()
        received: list[dict[str, Any]] = []

        def handler(payload: dict[str, Any]) -> None:
            received.append(payload)

        bus.subscribe("file.changed", handler)
        bus.publish("file.changed", {"path": "b.py"})

        assert received == [{"path": "b.py"}], (
            f"Handler was not invoked with the published payload.  Got: {received}"
        )

    def test_multiple_subscribers_all_receive(self) -> None:
        cls, _ = _import_event_bus()
        bus = cls()
        bag_a: list[Any] = []
        bag_b: list[Any] = []

        bus.subscribe("graph.update", bag_a.append)
        bus.subscribe("graph.update", bag_b.append)

        bus.publish("graph.update", {"nodes": 5})

        assert bag_a == [{"nodes": 5}]
        assert bag_b == [{"nodes": 5}]

    def test_subscribe_returns_unsubscribe_callable(self) -> None:
        """``subscribe`` must return a zero-arg callable that removes the handler."""
        cls, _ = _import_event_bus()
        bus = cls()
        calls: list[Any] = []

        unsubscribe = bus.subscribe("x", calls.append)
        assert callable(unsubscribe), (
            "subscribe() must return a zero-arg callable that unsubscribes. "
            f"Got: {type(unsubscribe).__name__}"
        )

        bus.publish("x", 1)
        assert calls == [1]

        unsubscribe()
        bus.publish("x", 2)
        assert calls == [1], (
            "Handler was called after unsubscribe — unsubscribe did not take effect."
        )

    def test_exception_in_one_handler_does_not_block_others(self) -> None:
        """Handlers are isolated: a raising handler must not prevent
        subsequent handlers from receiving the same event."""
        cls, _ = _import_event_bus()
        bus = cls()

        def bad(_: Any) -> None:
            raise RuntimeError("boom")

        good_received: list[Any] = []

        bus.subscribe("event", bad)
        bus.subscribe("event", good_received.append)

        # The bus may log or swallow the exception from ``bad`` — what
        # must not happen is the second handler being skipped.
        try:
            bus.publish("event", "payload")
        except RuntimeError:
            pytest.fail(
                "EventBus.publish() must isolate handler exceptions — the raising "
                "handler leaked out to the caller."
            )

        assert good_received == ["payload"], (
            f"Second handler was not invoked despite bad handler.  Got: {good_received}"
        )

    def test_event_type_isolation(self) -> None:
        """A handler for event type A must not receive events of type B."""
        cls, _ = _import_event_bus()
        bus = cls()

        a_events: list[Any] = []
        b_events: list[Any] = []

        bus.subscribe("a", a_events.append)
        bus.subscribe("b", b_events.append)

        bus.publish("a", 1)
        bus.publish("b", 2)

        assert a_events == [1]
        assert b_events == [2]

    def test_payload_type_hint_is_typed(self) -> None:
        """``publish``'s payload parameter must carry a non-``Any`` type annotation.

        We allow: TypedDict, dict[str, Any], a Pydantic model, a Protocol.
        We forbid: raw ``Any`` or missing annotation — those defeat the
        whole point of moving away from hand-built message dicts.
        """
        cls, _ = _import_event_bus()
        sig = inspect.signature(cls.publish)
        payload_param = None
        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue
            # The payload is the second non-self parameter in publish(type, payload).
            if name in ("event_type", "event", "type", "name"):
                continue
            payload_param = param
            break

        assert payload_param is not None, (
            f"publish() does not accept a payload parameter.  Signature: {sig}"
        )

        annotation = payload_param.annotation
        assert annotation is not inspect.Parameter.empty, (
            f"publish()'s payload parameter {payload_param.name!r} has no type "
            "annotation.  Use TypedDict or a Pydantic model so callers can't "
            "smuggle in arbitrary shapes."
        )
        # Reject bare ``Any``.
        ann_str = (
            annotation.__name__
            if hasattr(annotation, "__name__")
            else str(annotation)
        )
        assert ann_str != "Any", (
            "publish()'s payload is typed as ``Any`` — that defeats the purpose "
            "of the event-bus refactor.  Use a proper structured type."
        )


# ---------------------------------------------------------------------------
# #127 integration — file-watcher routes through the bus
# ---------------------------------------------------------------------------


class TestFileWatcherBusIntegration:
    """The file-watcher module must stop building message dicts with
    hardcoded ``type`` strings and instead publish typed events."""

    def test_server_does_not_hardcode_graph_update_type_literal(self) -> None:
        """After the refactor, ``server.py`` should not directly build
        ``{"type": "graph_update", ...}`` or ``{"type": "parse_error", ...}``
        dicts — those messages live on the bus.
        """
        tree = _parse_tree(SRC_ROOT / "server.py")
        bad_literals: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "type"
                    and isinstance(value, ast.Constant)
                    and value.value in {"graph_update", "parse_error"}
                ):
                    bad_literals.append((value.value, node.lineno))

        assert not bad_literals, (
            "server.py still builds hardcoded ``{'type': '<literal>'}`` dicts "
            f"for WebSocket messages: {bad_literals}.  Publish these through "
            "the EventBus instead."
        )

    def test_file_watcher_publishes_events(self) -> None:
        """The file-watcher must call ``bus.publish(...)``, not ``broadcast(...)``
        directly with hand-built dicts.
        """
        tree = _parse_tree(SRC_ROOT / "server.py")

        # Collect all calls inside ``_file_watcher`` (or its nested functions).
        watcher_calls: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if node.name not in ("_file_watcher", "_flush"):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    cname = _call_name(inner)
                    if cname is not None:
                        watcher_calls.append(cname)

        assert "publish" in watcher_calls, (
            "The file-watcher never calls ``publish(...)`` — it should route "
            f"events through the EventBus.  Observed calls: {sorted(set(watcher_calls))}"
        )

    @pytest.mark.asyncio
    async def test_bus_integration_round_trip(self) -> None:
        """Integration: subscribe to ``file.changed`` and assert the handler
        fires when the watcher publishes."""
        cls, _ = _import_event_bus()
        bus = cls()

        received: list[dict[str, Any]] = []

        def handler(payload: dict[str, Any]) -> None:
            received.append(payload)

        bus.subscribe("file.changed", handler)

        # Simulate the watcher publishing a file-changed event.
        result = bus.publish("file.changed", {"path": "main.py", "kind": "modified"})
        # If the impl is async, await it.
        if inspect.iscoroutine(result):
            await result

        assert received, "Handler did not receive the published file.changed event."
        assert received[0].get("path") == "main.py"


# ===========================================================================
# #132 Logging convention enforcement
# ===========================================================================


# Directories where CLI output via click.echo is legitimate.
_CLI_ONLY_DIRS = (CLI_ROOT,)


# Files we exempt from the "no ``logging.*`` calls" rule.
# ``_logging.py`` is the *only* place that is allowed to touch the stdlib
# logging module (it bridges stdlib → structlog).
_STDLIB_LOGGING_ALLOWED = {SRC_ROOT / "_logging.py"}

# These stdlib-logging attributes are configuration-only helpers.  They
# are acceptable even in server code (e.g. ``logging.getLogger('watchfiles')
# .setLevel(...)``) — they do not *emit* log records.  What we ban is
# direct emission (``logging.info`` / ``logging.error`` / ``logging.warning`` …).
_LOGGING_EMIT_ATTRS = {
    "info",
    "warning",
    "warn",
    "error",
    "critical",
    "exception",
    "debug",
    "log",
}


class TestLoggingConvention:
    """Enforce:

    * ``click.echo`` / ``click.secho`` only inside ``src/haute/cli/``.
    * No direct ``print(...)`` calls in ``src/haute/`` (ignoring
      docstrings / codegen string literals).
    * No stdlib ``logging.info`` / ``logging.error`` etc. in server
      code — use structlog via ``haute._logging.get_logger`` instead.
    * Server-side modules like ``trace.py`` / ``executor.py`` /
      ``routes/pipeline.py`` each construct a structlog logger at module
      scope.
    * ``haute._logging`` module docstring documents the convention
      (mentions ``structlog`` AND ``click.echo``) so nobody needs to
      guess after a fresh clone.
    """

    # -- click.echo / click.secho must stay in cli/ ----------------------

    def test_click_echo_forbidden_outside_cli(self) -> None:
        offenders: list[tuple[Path, int, str]] = []
        for py in _iter_py_files(SRC_ROOT, exclude_dirs=_CLI_ONLY_DIRS):
            tree = _parse_tree(py)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if (
                    isinstance(fn, ast.Attribute)
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "click"
                    and fn.attr in {"echo", "secho"}
                ):
                    offenders.append(
                        (py.relative_to(SRC_ROOT), node.lineno, f"click.{fn.attr}")
                    )
        assert not offenders, (
            "click.echo / click.secho is reserved for CLI user-facing output "
            "(src/haute/cli/).  These files use it elsewhere:\n"
            + "\n".join(f"  {p}:{ln} — {name}" for p, ln, name in offenders)
        )

    # -- No bare print() ---------------------------------------------------

    def test_no_print_calls_anywhere_in_src(self) -> None:
        """``print(...)`` is forbidden.  CLI output uses click.echo; server
        code uses structlog.  Docstrings and code-generation string literals
        (which happen to contain ``print(...)`` as *data*) are fine — the
        AST walker only finds real calls."""
        offenders: list[tuple[Path, int]] = []
        for py in _iter_py_files(SRC_ROOT):
            tree = _parse_tree(py)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id == "print":
                    offenders.append((py.relative_to(SRC_ROOT), node.lineno))
        assert not offenders, (
            "print() is forbidden in src/haute/.  Use click.echo (CLI) or "
            "structlog (server/internal) instead:\n"
            + "\n".join(f"  {p}:{ln}" for p, ln in offenders)
        )

    # -- No stdlib logging emission outside _logging.py ------------------

    def test_no_stdlib_logging_emission_in_server_code(self) -> None:
        """``logging.info(...)`` / ``logging.error(...)`` etc. are forbidden
        outside ``_logging.py``.  Config-only calls like
        ``logging.getLogger("watchfiles").setLevel(logging.WARNING)`` are OK —
        they don't emit records.
        """
        offenders: list[tuple[Path, int, str]] = []
        for py in _iter_py_files(SRC_ROOT):
            if py.resolve() in {p.resolve() for p in _STDLIB_LOGGING_ALLOWED}:
                continue
            tree = _parse_tree(py)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                # ``logging.info(...)``, ``logging.error(...)``, etc.
                if (
                    isinstance(fn, ast.Attribute)
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "logging"
                    and fn.attr in _LOGGING_EMIT_ATTRS
                ):
                    offenders.append(
                        (py.relative_to(SRC_ROOT), node.lineno, f"logging.{fn.attr}")
                    )
        assert not offenders, (
            "Direct stdlib logging emission detected — use structlog via "
            "haute._logging.get_logger() instead:\n"
            + "\n".join(f"  {p}:{ln} — {name}" for p, ln, name in offenders)
        )

    def test_no_top_level_getlogger_binding_outside_logging_py(self) -> None:
        """Server/internal code must not bind a module-level ``logger`` via
        ``logging.getLogger(...)`` — that indicates stdlib-logging usage.
        Only ``_logging.py`` itself may do this (to configure the root
        logger).  Third-party-logger suppression (e.g.
        ``logging.getLogger("watchfiles").setLevel(...)``) is still allowed
        because it doesn't assign to a module-level ``logger`` name.
        """
        offenders: list[tuple[Path, int]] = []
        for py in _iter_py_files(SRC_ROOT):
            if py.resolve() in {p.resolve() for p in _STDLIB_LOGGING_ALLOWED}:
                continue
            tree = _parse_tree(py)
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                # Look for ``logger = logging.getLogger(...)`` at module scope.
                if not any(
                    isinstance(t, ast.Name) and t.id == "logger" for t in node.targets
                ):
                    continue
                val = node.value
                if (
                    isinstance(val, ast.Call)
                    and isinstance(val.func, ast.Attribute)
                    and isinstance(val.func.value, ast.Name)
                    and val.func.value.id == "logging"
                    and val.func.attr == "getLogger"
                ):
                    offenders.append((py.relative_to(SRC_ROOT), node.lineno))
        assert not offenders, (
            "Module-level ``logger = logging.getLogger(...)`` binding found — "
            "use structlog via ``from haute._logging import get_logger`` instead:\n"
            + "\n".join(f"  {p}:{ln}" for p, ln in offenders)
        )

    # -- structlog IS used in the canonical server files -----------------

    @pytest.mark.parametrize(
        "rel",
        [
            "trace.py",
            "executor.py",
            "server.py",
            "routes/pipeline.py",
            "routes/files.py",
        ],
    )
    def test_canonical_server_files_construct_structlog_logger(self, rel: str) -> None:
        """Each of these files must have a module-level ``logger = get_logger(...)``
        (from ``haute._logging``, which is structlog underneath)."""
        path = SRC_ROOT / rel
        assert path.is_file(), f"Expected {path} to exist"
        tree = _parse_tree(path)

        has_import = False
        has_binding = False
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if node.module == "haute._logging" and any(
                    alias.name == "get_logger" for alias in node.names
                ):
                    has_import = True
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == "logger" for t in node.targets):
                    val = node.value
                    if isinstance(val, ast.Call):
                        cname = _call_name(val)
                        if cname == "get_logger":
                            has_binding = True

        assert has_import, (
            f"{rel} does not import ``get_logger`` from ``haute._logging``."
        )
        assert has_binding, (
            f"{rel} does not construct a module-level ``logger = get_logger(...)``."
        )

    # -- The convention is documented -----------------------------------

    def test_logging_convention_documented_in_module_docstring(self) -> None:
        """``haute._logging``'s module docstring must explicitly document the
        convention so new contributors don't guess.  Required keywords:

        * ``structlog`` — the chosen library for server/internal logging
        * ``click.echo`` — the chosen API for CLI user-facing output
        * A short rule stating the split (we look for "server" + "CLI").
        """
        from haute import _logging

        doc = (_logging.__doc__ or "").lower()
        assert "structlog" in doc, (
            "haute._logging module docstring must mention ``structlog`` so "
            "contributors know which library to use for server-side logging."
        )
        assert "click.echo" in doc, (
            "haute._logging module docstring must mention ``click.echo`` so "
            "contributors know which API to use for CLI user-facing output."
        )
        # A brief rule — check both 'server' and 'CLI' appear in the same
        # docstring.  Case-insensitive.
        assert "server" in doc and "cli" in doc, (
            "haute._logging module docstring must state the rule: "
            "server uses structlog, CLI uses click.echo.  The words 'server' "
            "and 'CLI' should both appear."
        )
