"""Tests for Phase 1 Package 1B — trace fail-loudly sweep.

These tests drive three fail-loudly improvements in ``haute.trace``:

  * **Item #3** — 15+ silent ``except Exception`` sites inside
    ``_enrich_steps`` (and helpers).  Each site must either raise an
    :class:`haute.errors.ExecutionError`, re-raise after logging at
    WARNING level, or propagate a visible failure signal into the
    enrichment result (e.g. an ``error`` field on ``expression``,
    ``calculation``, ``node_detail``, or ``row_lineage_type``).
    Five representative sites are covered:

      - line 1196 — ``parse_expression`` failure  (``expression`` field)
      - line 1216 — ``evaluate_expression`` failure  (``calculation`` field)
      - line 1246 — ``parse_expression_chain`` failure  (``expression_chain``)
      - line 1322 — node-type enrichment failure    (``node_detail`` field)
      - line 1359 — ``detect_row_lineage_type`` failure (``row_lineage_type``)

  * **Item #4** — ``swallow_errors=True`` retry gated by a regex that
    matches ``"unable to find column"``.  A genuine column-name typo whose
    name happens to appear as a kwarg target on *another* node in the
    graph must NOT trigger the swallow-errors retry: the original error
    must propagate so the user sees the typo instead of a silent partial
    execution.

  * **Item #5** — the waterfall build wrapping ``build_waterfall`` with a
    bare ``except Exception`` that returns ``waterfall=None``.  A failure
    during waterfall construction must surface as a structured error
    field on the :class:`haute.trace.TraceResult` and log at WARNING
    level — never silently produce ``waterfall=None``.

Each test is designed to *fail loudly* before the production fix lands:
either an ``ExecutionError`` is raised, or a visible failure marker
appears on the returned structure.  After the fix, tests pass.
"""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest
import structlog.testing

from haute.errors import ExecutionError
from haute.trace import TraceResult, TraceStep, execute_trace
from tests.conftest import make_edge as _edge
from tests.conftest import make_graph as _g
from tests.conftest import make_ready_file_input_config
from tests.conftest import make_source_node as _source_node
from tests.conftest import make_transform_node as _transform_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step_by_id(result: TraceResult, node_id: str) -> TraceStep:
    """Return the ``TraceStep`` for *node_id* or raise."""
    for s in result.steps:
        if s.node_id == node_id:
            return s
    raise KeyError(f"No step with node_id={node_id!r}")


class _BoomError(RuntimeError):
    """Sentinel injected error used to prove the enrichment path ran."""


def _explodes(*_a: Any, **_kw: Any) -> Any:
    """Stand-in function that always raises :class:`_BoomError`."""
    raise _BoomError("simulated enrichment failure — payload hidden until fix")


def _visible_failure(value: Any) -> bool:
    """Return True if *value* surfaces a failure reason to the user.

    After the fail-loudly fix, enrichment sites that decline to re-raise
    must at least propagate a structured failure signal.  Accept any of:

      * a dict with an ``error``/``failure``/``reason`` key
      * a string starting with ``"error"`` or containing ``"failed"``
      * an object with an ``.error`` / ``.failure_reason`` attribute

    We deliberately reject a silent ``None`` or an empty dict — that is
    precisely the "success-with-empty-field" anti-pattern the sweep
    eliminates.
    """
    if value is None:
        return False
    if isinstance(value, dict):
        if not value:
            return False
        for key in ("error", "failure", "failure_reason", "reason"):
            if value.get(key):
                return True
        return False
    if isinstance(value, str):
        lowered = value.lower()
        return lowered.startswith("error") or "failed" in lowered or "error" in lowered
    for attr in ("error", "failure", "failure_reason"):
        if getattr(value, attr, None):
            return True
    return False


def _had_warn_or_higher(captured: list[dict[str, Any]], substring: str = "") -> bool:
    """Return True if *captured* contains a WARNING/ERROR log matching *substring*.

    Used to verify the fix routes through a visible log level rather than
    the pre-fix ``logger.debug`` calls.
    """
    for entry in captured:
        level = str(entry.get("log_level", "")).lower()
        if level not in {"warning", "error", "critical"}:
            continue
        event = str(entry.get("event", ""))
        if substring and substring not in event:
            continue
        return True
    return False


def _run_trace_loudly(
    *,
    graph: Any,
    row_index: int = 0,
    target: str,
    column: str | None,
) -> tuple[TraceResult | None, Exception | None, list[dict[str, Any]]]:
    """Run ``execute_trace`` capturing logs and swallowing ExecutionError.

    Tests use this so they can assert on either "raised ExecutionError"
    OR "visible failure field" without worrying about which branch the
    fix picked.
    """
    with structlog.testing.capture_logs() as captured:
        try:
            result = execute_trace(
                graph,
                row_index=row_index,
                target_node_id=target,
                column=column,
            )
            return result, None, list(captured)
        except ExecutionError as exc:  # noqa: TRY203 — intentional surface
            return None, exc, list(captured)


# ---------------------------------------------------------------------------
# Item #3 — silent ``except Exception`` inside _enrich_steps
# ---------------------------------------------------------------------------


class TestItem3SilentEnrichmentExcepts:
    """Five representative sites in ``trace._enrich_steps``.

    Covered line numbers (pre-fix snapshot):

      - 1196 — ``parse_expression``             (``expression``)
      - 1216 — ``evaluate_expression``          (``calculation``)
      - 1246 — ``parse_expression_chain``       (``calculation["expression_chain"]``)
      - 1322 — node-type dispatch (e.g. ``enrich_live_switch``) (``node_detail``)
      - 1359 — ``detect_row_lineage_type``      (``row_lineage_type``)

    For each site we:

      1. Build a minimal pipeline that, during trace, reliably exercises
         that specific enrichment call.
      2. Monkeypatch the called function to raise ``_BoomError``.
      3. Assert that EITHER the trace raises ``ExecutionError`` OR the
         returned step surfaces a visible failure marker (never a silent
         ``None``).
      4. Assert that the failure was logged at WARNING level or higher.
    """

    def _build_arithmetic_graph(self, tmp_path) -> Any:
        """Graph: src -> t(burn_cost = premium * 0.7)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"premium": [1000.0]}).write_parquet(p)
        return _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = df.with_columns(burn_cost=pl.col('premium') * 0.7)",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

    # --- Site 1196: parse_expression failure --------------------------------

    def test_site_1196_parse_expression_failure_is_visible(
        self,
        tmp_path,
        monkeypatch,
    ):
        """If ``parse_expression`` raises, the step must not silently omit
        the ``expression`` field.  Either an ``ExecutionError`` bubbles up
        or ``step.expression`` carries a visible failure marker.
        """
        graph = self._build_arithmetic_graph(tmp_path)
        monkeypatch.setattr("haute._trace_enrichment.parse_expression", _explodes)

        result, exc, captured = _run_trace_loudly(
            graph=graph,
            target="t",
            column="burn_cost",
        )

        if exc is not None:
            # Branch (b): re-raised as ExecutionError — ideal fail-loudly.
            return

        assert result is not None
        step = _step_by_id(result, "t")
        # Branch (c): a visible failure signal on the enrichment field.
        assert _visible_failure(step.expression), (
            "parse_expression failure was silent: step.expression = "
            f"{step.expression!r} — expected a visible failure marker"
        )
        assert _had_warn_or_higher(captured), (
            "parse_expression failure was only logged at debug level; "
            "fail-loudly fix requires WARNING or higher."
        )

    # --- Site 1216: evaluate_expression failure -----------------------------

    def test_site_1216_evaluate_expression_failure_is_visible(
        self,
        tmp_path,
        monkeypatch,
    ):
        """``evaluate_expression`` failure must not silently drop the
        ``calculation`` field.
        """
        graph = self._build_arithmetic_graph(tmp_path)

        # Keep parse_expression working so we get past the 1193 site and
        # actually reach the evaluate_expression call on line 1199.
        monkeypatch.setattr("haute._trace_enrichment.evaluate_expression", _explodes)

        result, exc, captured = _run_trace_loudly(
            graph=graph,
            target="t",
            column="burn_cost",
        )

        if exc is not None:
            return

        assert result is not None
        step = _step_by_id(result, "t")
        assert _visible_failure(step.calculation), (
            f"evaluate_expression failure was silent: step.calculation = {step.calculation!r}"
        )
        assert _had_warn_or_higher(captured), (
            "evaluate_expression failure was logged only at debug; require WARNING or higher."
        )

    # --- Site 1246: parse_expression_chain failure --------------------------

    def test_site_1246_expression_chain_failure_is_visible(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Intra-node chain parsing failure must surface a visible error.

        Before fix: failure silently omits ``calculation["expression_chain"]``
        and leaves an otherwise normal calculation dict — indistinguishable
        from "no chain detected".
        """
        # Chain scenario: margin depends on burn_cost, defined in same node.
        p = tmp_path / "data.parquet"
        pl.DataFrame({"premium": [1000.0]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = df.with_columns(\n"
                        "    burn_cost=pl.col('premium') * 0.7,\n"
                        "    margin=pl.col('premium') - pl.col('premium') * 0.7,\n"
                        ")",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        monkeypatch.setattr("haute._trace_enrichment.parse_expression_chain", _explodes)

        result, exc, captured = _run_trace_loudly(
            graph=graph,
            target="t",
            column="margin",
        )

        if exc is not None:
            return

        assert result is not None
        step = _step_by_id(result, "t")
        calc = step.calculation or {}
        chain_field = calc.get("expression_chain")
        # Either the whole calculation has a visible error, or the chain
        # sub-field carries one.
        assert _visible_failure(calc) or _visible_failure(chain_field), (
            f"parse_expression_chain failure was silent: calculation = {calc!r}"
        )
        assert _had_warn_or_higher(captured), (
            "parse_expression_chain failure was logged only at debug."
        )

    # --- Site 1322: node-type enrichment failure ----------------------------

    def test_site_1322_node_enrichment_failure_is_visible(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Node-type enrichment dispatch must fail loudly.

        Uses a ``liveSwitch`` node (simplest enrichment-typed node that
        can execute without elaborate config) and monkeypatches
        ``enrich_live_switch`` to raise.
        """
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

        p_live = tmp_path / "live.parquet"
        p_batch = tmp_path / "batch.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p_live)
        pl.DataFrame({"x": [2]}).write_parquet(p_batch)

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="live_src",
                    data=NodeData(
                        label="live_src",
                        nodeType="dataInput",
                        config=make_ready_file_input_config(p_live),
                    ),
                ),
                GraphNode(
                    id="batch_src",
                    data=NodeData(
                        label="batch_src",
                        nodeType="dataInput",
                        config=make_ready_file_input_config(p_batch),
                    ),
                ),
                GraphNode(
                    id="sw",
                    data=NodeData(
                        label="sw",
                        nodeType="liveSwitch",
                        config={
                            "input_scenario_map": {
                                "live_src": "live",
                                "batch_src": "nb_batch",
                            }
                        },
                    ),
                ),
            ],
            edges=[
                GraphEdge(id="e1", source="live_src", target="sw"),
                GraphEdge(id="e2", source="batch_src", target="sw"),
            ],
        )
        monkeypatch.setattr("haute._trace_enrichment.enrich_live_switch", _explodes)

        result, exc, captured = _run_trace_loudly(
            graph=graph,
            target="sw",
            column=None,
        )

        if exc is not None:
            return

        assert result is not None
        step = _step_by_id(result, "sw")
        assert _visible_failure(step.node_detail), (
            f"node-type enrichment failure was silent: step.node_detail = {step.node_detail!r}"
        )
        assert _had_warn_or_higher(captured), "node_enrichment failure was logged only at debug."

    # --- Site 1359: detect_row_lineage_type failure -------------------------

    def test_site_1359_row_lineage_detection_failure_is_visible(
        self,
        tmp_path,
        monkeypatch,
    ):
        """``detect_row_lineage_type`` failure must leave a visible trace."""
        graph = self._build_arithmetic_graph(tmp_path)
        monkeypatch.setattr("haute._trace_enrichment.detect_row_lineage_type", _explodes)

        result, exc, captured = _run_trace_loudly(
            graph=graph,
            target="t",
            column="burn_cost",
        )

        if exc is not None:
            return

        assert result is not None
        step = _step_by_id(result, "t")
        # row_lineage_type is a str | None; ``None`` is the pre-fix silent
        # failure mode.  Accept any non-empty string that signals an error,
        # or a structured failure payload if the field was changed to dict.
        assert _visible_failure(step.row_lineage_type), (
            "row lineage detection failure was silent: "
            f"step.row_lineage_type = {step.row_lineage_type!r}"
        )
        assert _had_warn_or_higher(captured), (
            "row_lineage_detection failure was logged only at debug."
        )

    # --- Parametrised smoke test: all 5 sites together ----------------------

    @pytest.mark.parametrize(
        "target_fn_path, _site_label",
        [
            ("haute._trace_enrichment.parse_expression", "1196_parse_expression"),
            ("haute._trace_enrichment.evaluate_expression", "1216_evaluate_expression"),
            ("haute._trace_enrichment.parse_expression_chain", "1246_parse_expression_chain"),
            (
                "haute._trace_enrichment.detect_row_lineage_type",
                "1359_detect_row_lineage_type",
            ),
        ],
    )
    def test_injected_enrichment_failure_is_never_silent(
        self,
        tmp_path,
        monkeypatch,
        target_fn_path: str,
        _site_label: str,
    ):
        """Parametrised smoke coverage: whichever enrichment function we
        break, SOME visible signal must appear.  Either the trace raises
        or the step has a non-``None`` enrichment field carrying an error
        marker.
        """
        p = tmp_path / "data.parquet"
        pl.DataFrame({"premium": [1000.0]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = df.with_columns(burn_cost=pl.col('premium') * 0.7)",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        monkeypatch.setattr(target_fn_path, _explodes)

        result, exc, captured = _run_trace_loudly(
            graph=graph,
            target="t",
            column="burn_cost",
        )

        if exc is not None:
            return

        assert result is not None, f"[{_site_label}] trace returned nothing"
        step = _step_by_id(result, "t")
        any_visible = (
            _visible_failure(step.expression)
            or _visible_failure(step.calculation)
            or _visible_failure(step.node_detail)
            or _visible_failure(step.row_lineage_type)
        )
        assert any_visible, (
            f"[{_site_label}] injected enrichment failure produced no "
            "visible signal on any step field."
        )
        assert _had_warn_or_higher(captured), (
            f"[{_site_label}] enrichment failure was logged only at debug."
        )


# ---------------------------------------------------------------------------
# Item #4 — swallow_errors regex heuristic must not mask real errors
# ---------------------------------------------------------------------------


class TestItem4SwallowErrorsRegexHeuristic:
    """``execute_trace`` currently retries ``_execute_eager_core`` with
    ``swallow_errors=True`` when:

      * ``str(exc)`` contains ``"unable to find column"`` AND
      * *some* node in the graph has ``.with_columns(<missing_col>=...)``.

    This regex heuristic produces false positives: a genuine column-name
    typo in node A, where node B happens to define the same kwarg name,
    gets retried silently.  The user then sees a partial pipeline rather
    than the actual typo error.
    """

    def test_column_typo_with_matching_kwarg_on_other_node_raises(
        self,
        tmp_path,
    ):
        """False-positive trigger: node ``a`` has a typo referencing
        ``bar`` (undefined in its input), and node ``b`` (unrelated)
        defines ``bar=...`` in its own ``.with_columns``.

        The regex heuristic currently flags this as an intra-node
        dependency error and retries with ``swallow_errors=True``,
        silently producing a partial trace.  Fail-loudly requires the
        original Polars ``ColumnNotFoundError`` (or an
        :class:`ExecutionError` wrapping it) to propagate.
        """
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    # Node 'a' references the undefined column 'bar' — a
                    # genuine typo.  After fix: must raise.
                    _transform_node(
                        "a",
                        "df = df.with_columns(foo=pl.col('bar') * 2)",
                    ),
                    # Node 'b' defines 'bar' via with_columns — this is
                    # what misleads the regex heuristic into treating
                    # a's error as an intra-node dependency.
                    _transform_node(
                        "b",
                        "df = df.with_columns(bar=pl.col('x') * 3)",
                    ),
                ],
                "edges": [_edge("src", "a"), _edge("a", "b")],
            }
        )

        with pytest.raises(Exception) as exc_info:  # noqa: PT011 - intentionally broad: fail-loud propagation check, asserts on message content not type
            execute_trace(graph, row_index=0, target_node_id="b")

        # Make sure the raised exception references the actual missing
        # column — i.e. the user learns about the typo.  A silent retry
        # returning a partial result would fail this assertion.
        msg = str(exc_info.value)
        assert "bar" in msg, (
            f"Expected raised exception to mention the missing column 'bar'; got message: {msg!r}"
        )

    def test_retry_not_triggered_by_regex_alone(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Even if a Polars error contains ``"unable to find column"``,
        the retry must not fire unless the caller explicitly opts in
        (e.g. via a ``swallow_errors=True`` kwarg on ``execute_trace``).

        We make this provable by counting how many times
        ``_execute_eager_core`` is invoked — before the fix it's called
        twice (original + retry); after the fix it is called exactly
        once for a genuine column-not-found error.
        """
        import haute.trace as tmod

        call_count = {"n": 0}
        real_fn = tmod._execute_eager_core

        def _counting(*args: Any, **kwargs: Any) -> Any:
            call_count["n"] += 1
            return real_fn(*args, **kwargs)

        monkeypatch.setattr("haute.trace._execute_eager_core", _counting)

        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "a",
                        "df = df.with_columns(foo=pl.col('bar') * 2)",
                    ),
                    _transform_node(
                        "b",
                        "df = df.with_columns(bar=pl.col('x') * 3)",
                    ),
                ],
                "edges": [_edge("src", "a"), _edge("a", "b")],
            }
        )

        with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: retry-not-triggered check, asserts on call count not type
            execute_trace(graph, row_index=0, target_node_id="b")

        assert call_count["n"] == 1, (
            "Genuine column-not-found error triggered the regex-based "
            f"retry ({call_count['n']} invocations); expected exactly 1. "
            "Any retry must be opt-in via explicit kwarg, not heuristic."
        )


# ---------------------------------------------------------------------------
# Item #5 — waterfall build must not silently return None
# ---------------------------------------------------------------------------


class TestItem5WaterfallBuildFailsLoudly:
    """Currently ``trace.py:872`` wraps the waterfall construction in a
    bare ``except Exception`` that logs at ``debug`` and returns
    ``waterfall=None``.  A failure during waterfall build is invisible
    to the user — the hero chart is simply empty.

    After fix, the failure must surface as a structured error payload
    on the ``TraceResult`` (e.g. ``waterfall={"error": "reason"}``) and
    log at WARNING level.  Silent ``None`` is a regression.
    """

    def _build_waterfall_graph(self, tmp_path) -> Any:
        """Four-step multiplicative chain that currently produces a
        waterfall: base * f1 * f2 * f3.
        """
        p = tmp_path / "data.parquet"
        pl.DataFrame({"base": [100.0]}).write_parquet(p)
        return _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "step1",
                        "df = df.with_columns(premium=pl.col('base') * 1.0)",
                    ),
                    _transform_node(
                        "step2",
                        "df = df.with_columns(premium=pl.col('premium') * 1.1)",
                    ),
                    _transform_node(
                        "step3",
                        "df = df.with_columns(premium=pl.col('premium') * 1.2)",
                    ),
                    _transform_node(
                        "step4",
                        "df = df.with_columns(premium=pl.col('premium') * 0.9)",
                    ),
                ],
                "edges": [
                    _edge("src", "step1"),
                    _edge("step1", "step2"),
                    _edge("step2", "step3"),
                    _edge("step3", "step4"),
                ],
            }
        )

    def test_waterfall_failure_surfaces_to_user(
        self,
        tmp_path,
        monkeypatch,
    ):
        """If ``build_waterfall`` raises, the ``TraceResult.waterfall``
        field must carry a visible error payload — not ``None``.
        """
        graph = self._build_waterfall_graph(tmp_path)

        # Patch the function at its source module, since trace.py imports
        # it locally at call time (``from haute._trace_waterfall import
        # build_waterfall``).
        monkeypatch.setattr(
            "haute._trace_waterfall.build_waterfall",
            _explodes,
        )

        with structlog.testing.capture_logs() as captured:
            try:
                result = execute_trace(
                    graph,
                    row_index=0,
                    target_node_id="step4",
                    column="premium",
                )
                raised = False
            except ExecutionError:
                raised = True

        if raised:
            # Branch (b): the fail-loudly fix may choose to re-raise —
            # that is acceptable because the user sees the error.
            return

        # Branch (c): visible failure payload on TraceResult.waterfall.
        assert result.waterfall is not None, (
            "waterfall=None is the silent-failure mode that item #5 "
            "targets; the fix must surface a structured error payload."
        )
        assert _visible_failure(result.waterfall), (
            "waterfall build failed but the returned value has no "
            f"visible error marker: {result.waterfall!r}"
        )
        assert _had_warn_or_higher(captured, substring="waterfall"), (
            "waterfall build failure was only logged at debug level; "
            "fail-loudly fix requires WARNING or higher."
        )

    def test_successful_waterfall_still_produces_normal_payload(
        self,
        tmp_path,
    ):
        """Regression guard: the fail-loudly fix must not break the
        happy path.  A healthy multi-step multiplicative pipeline still
        produces a non-error waterfall list.
        """
        graph = self._build_waterfall_graph(tmp_path)

        result = execute_trace(
            graph,
            row_index=0,
            target_node_id="step4",
            column="premium",
        )

        # Success path: waterfall is a list of dicts with cumulative/delta.
        assert result.waterfall is not None
        assert isinstance(result.waterfall, list)
        assert len(result.waterfall) >= 3
        # No error markers on happy path.
        assert not _visible_failure(result.waterfall[0])
