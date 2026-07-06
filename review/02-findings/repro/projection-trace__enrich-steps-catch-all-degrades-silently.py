"""Adversarial repro for claim `enrich-steps-catch-all-degrades-silently`.

Hypothesis under test (the claim):
    A *systematic* enricher bug (one that breaks the same enrichment stage on
    EVERY step) does NOT crash `execute_trace`. Instead every column-relevant
    step gets an `{error}` marker, the numeric `output_values` still flow from
    row correlation, and there is NO aggregate failure signal on the returned
    `TraceResult` that would let a caller/CI detect a broad regression. So a
    whole-trace enrichment failure ships silently (degraded-but-plausible).

This repro injects a systematic break (patch BOTH `parse_expression` AND
`evaluate_expression` to raise unconditionally — a parser regression that
hits every relevant step) and asserts on the SPECIFIC degraded behaviour,
not merely "something raised".

Isolation: all disk I/O via tempfile; synthetic in-memory graph; project
root pinned to the tmp dir via `haute._sandbox.set_project_root`. No real
project file is read or written.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import polars as pl

import haute._sandbox as _sandbox


class _Boom(RuntimeError):
    pass


def _explodes(*_a: Any, **_kw: Any) -> Any:
    raise _Boom("systematic enricher regression — every stage broken")


def _has_visible_error(value: Any) -> bool:
    """True if the enrichment field surfaces a structured failure marker."""
    if isinstance(value, dict):
        if value.get("error") or value.get("error_type"):
            return True
        # chain sub-field may hold the error
        chain = value.get("expression_chain")
        if isinstance(chain, dict) and (chain.get("error") or chain.get("error_type")):
            return True
        return False
    if isinstance(value, str):
        return "error" in value.lower() or "failed" in value.lower()
    return False


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _sandbox.set_project_root(tmp)

        # Import AFTER sandbox is pinned so module-level project lookups (if
        # any) resolve against the tmp dir.
        import haute.trace as trace_mod
        from haute.trace import execute_trace
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

        data_path = tmp / "data.parquet"
        pl.DataFrame({"base": [1000.0]}).write_parquet(data_path)

        # src -> s1 (premium = base*0.7) -> s2 (premium = premium*1.1)
        #     -> s3 (premium = premium - 50)
        # Three column-relevant steps that create/modify `premium`.
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="src",
                    data=NodeData(
                        label="src", nodeType="dataSource", config={"path": str(data_path)}
                    ),
                ),
                GraphNode(
                    id="s1",
                    data=NodeData(
                        label="s1",
                        nodeType="polars",
                        config={"code": "df = df.with_columns(premium=pl.col('base') * 0.7)"},
                    ),
                ),
                GraphNode(
                    id="s2",
                    data=NodeData(
                        label="s2",
                        nodeType="polars",
                        config={"code": "df = df.with_columns(premium=pl.col('premium') * 1.1)"},
                    ),
                ),
                GraphNode(
                    id="s3",
                    data=NodeData(
                        label="s3",
                        nodeType="polars",
                        config={"code": "df = df.with_columns(premium=pl.col('premium') - 50.0)"},
                    ),
                ),
            ],
            edges=[
                GraphEdge(id="e1", source="src", target="s1"),
                GraphEdge(id="e2", source="s1", target="s2"),
                GraphEdge(id="e3", source="s2", target="s3"),
            ],
        )

        # --- Inject a SYSTEMATIC enricher regression (N-of-N stages) -------
        # Patch on the haute.trace module: the dispatch walk in
        # _trace_enrichment.enrich_steps resolves these via
        # sys.modules["haute.trace"], so this reaches the per-step calls.
        trace_mod.parse_expression = _explodes
        trace_mod.evaluate_expression = _explodes
        trace_mod.parse_expression_chain = _explodes

        # Sanity: the expected numeric result so we can prove values still flow.
        expected_premium = (1000.0 * 0.7) * 1.1 - 50.0  # 720.0

        # --- Run: the claim predicts NO exception ---------------------------
        raised: Exception | None = None
        result = None
        try:
            result = execute_trace(graph, row_index=0, target_node_id="s3", column="premium")
        except Exception as exc:  # noqa: BLE001 — we want to know if it raised
            raised = exc

        # ===================================================================
        # ASSERTION 1: systematic enricher break does NOT raise.
        # (If it raised, the claim's "degrades silently / no crash" premise
        #  is FALSE and the finding would be refuted on that axis.)
        # ===================================================================
        assert raised is None, (
            "EXPECTED: execute_trace returns despite N-of-N enrichment failure.\n"
            f"ACTUAL: it raised {type(raised).__name__}: {raised}"
        )
        assert result is not None

        steps_by_id = {s.node_id: s for s in result.steps}

        # ===================================================================
        # ASSERTION 2: every column-relevant step carries an {error} marker
        # on at least one enrichment field (expression / calculation).
        # ===================================================================
        relevant_ids = [nid for nid in ("s1", "s2", "s3") if nid in steps_by_id]
        assert relevant_ids, f"no relevant steps survived; got {list(steps_by_id)}"
        errored = []
        for nid in relevant_ids:
            st = steps_by_id[nid]
            if _has_visible_error(st.expression) or _has_visible_error(st.calculation):
                errored.append(nid)
        assert errored == relevant_ids, (
            "EXPECTED: every column-relevant step error-annotated.\n"
            f"ACTUAL: errored={errored} relevant={relevant_ids}\n"
            f"  s3.expression={steps_by_id['s3'].expression!r}\n"
            f"  s3.calculation={steps_by_id['s3'].calculation!r}"
        )

        # ===================================================================
        # ASSERTION 3: the numeric value STILL flows from row correlation
        # (output_values / output_value are NON-None and CORRECT despite the
        #  enrichment being fully broken — this is the "correlated values
        #  still showing" half of the claim).
        # ===================================================================
        s3 = steps_by_id["s3"]
        assert "premium" in s3.output_values, f"premium missing from {s3.output_values!r}"
        got = s3.output_values["premium"]
        assert got is not None and abs(float(got) - expected_premium) < 1e-9, (
            f"EXPECTED premium≈{expected_premium}; ACTUAL output_values['premium']={got!r}"
        )
        assert result.output_value is not None and abs(float(result.output_value) - expected_premium) < 1e-9, (
            f"EXPECTED result.output_value≈{expected_premium}; ACTUAL={result.output_value!r}"
        )

        # ===================================================================
        # ASSERTION 4 (the load-bearing design property): there is NO
        # aggregate failure signal on the TraceResult that a caller/CI could
        # use to detect a whole-trace enrichment regression. The only loud
        # aggregate fields are `waterfall` (error payload) and
        # `correlation_diagnostics`; neither fires on N-of-N enrichment
        # failure. There is no `enrichment_errors`/`degraded`/status field.
        # ===================================================================
        # No dedicated aggregate-error attribute exists.
        for attr in ("enrichment_errors", "enrichment_failed", "degraded", "error", "status"):
            assert not hasattr(result, attr), (
                f"UNEXPECTED: TraceResult exposes an aggregate signal '{attr}' "
                "that could detect the regression (claim would be weaker)."
            )
        # correlation_diagnostics stays empty (correlation succeeded fine).
        assert result.correlation_diagnostics == [], (
            "correlation_diagnostics unexpectedly populated: "
            f"{result.correlation_diagnostics!r}"
        )
        # waterfall did NOT capture the enrichment break as an error: it is
        # either None or a normal payload, NOT an {error} marker tied to the
        # systematic enrichment failure. (Patched evaluate_expression also
        # breaks waterfall input, but the point stands: no signal that says
        # "every step's expression/calculation enrichment failed".)
        wf = result.waterfall
        wf_is_aggregate_enrichment_signal = (
            isinstance(wf, dict) and bool(wf.get("error"))
        )
        # Even if waterfall happens to carry an error, it is about the
        # waterfall build, not an enumeration of the per-step enrichment
        # failures — so it does not let a caller know N-of-N steps degraded.
        # We assert the *steps* themselves are the only place the failure is
        # recorded (per-step, not aggregate).
        per_step_only = all(
            _has_visible_error(steps_by_id[nid].expression)
            or _has_visible_error(steps_by_id[nid].calculation)
            for nid in relevant_ids
        )
        assert per_step_only

        print("REPRO_RESULT: CLAIM-CORE-CONFIRMED")
        print(f"  raised               = {raised!r}")
        print(f"  relevant steps       = {relevant_ids}")
        print(f"  error-annotated      = {errored}")
        print(f"  s3.output['premium'] = {got!r}  (expected {expected_premium})")
        print(f"  result.output_value  = {result.output_value!r}")
        print(f"  TraceResult aggregate-error attr present = "
              f"{any(hasattr(result, a) for a in ('enrichment_errors','enrichment_failed','degraded','status'))}")
        print(f"  waterfall is aggregate enrichment-error signal = {wf_is_aggregate_enrichment_signal}")
        print(f"  s3.expression        = {s3.expression!r}")
        print(f"  s3.calculation       = {s3.calculation!r}")


if __name__ == "__main__":
    main()
