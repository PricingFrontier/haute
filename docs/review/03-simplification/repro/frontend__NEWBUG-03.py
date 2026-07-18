"""
Isolated reproduction for NEWBUG-03.

Claim: `failSolveJob` with NO prior cached result fabricates a synthetic
SolveResult {status:'error', total_objective:0, baseline_objective:0,
constraints:{}, lambdas:{}, converged:false} and stores it in
`solveResults[nodeId]`. `getOptimiserPreview` only returns null when the entry
is *absent* -- it does NOT null on `.error` -- so App.tsx renders
<OptimiserPreview> for that synthetic entry. OptimiserPreview has NO error
branch; it unconditionally renders a status summary / collapsed-meta of
"Not converged | Objective: 0" for what was actually a *failed* solve.

This is a read-only, in-memory port of the EXACT TypeScript logic from:
  - frontend/src/stores/useNodeResultsStore.ts:753-790  (failSolveJob synthetic fallback)
  - frontend/src/stores/useNodeResultsStore.ts:235-244   (buildOptimiserPreview -> drops `error`)
  - frontend/src/stores/useNodeResultsStore.ts:1090-1096  (getOptimiserPreview: null ONLY on absence)
  - frontend/src/App.tsx:612-621                          (render decision: `if (optPreview)`)
  - frontend/src/panels/OptimiserPreview.tsx:368-372,386  (status summary / collapsedMeta)
  - frontend/src/utils/formatValue.ts:39-44               (formatNumber)
CONTROL ports the CORRECT guard the codebase already has elsewhere:
  - frontend/src/panels/OptimiserConfig.tsx:228           (solveResult = cached.error ? null : cached.result)

No project files, no rating/, no src/, no tests/ are read or written.
Run with: uv run python review/03-simplification/repro/frontend__NEWBUG-03.py
"""

from __future__ import annotations
import math
from typing import Optional


# ── formatNumber  (formatValue.ts:39-44) ────────────────────────────
def format_number(n: float) -> str:
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    if float(n).is_integer():
        return str(int(n))
    return f"{n:.4f}"


# ── Synthetic SolveResult written by failSolveJob (store.ts:761) ─────
def synthetic_error_solve_result() -> dict:
    # Verbatim object literal from useNodeResultsStore.ts:761-762.
    return {
        "status": "error",
        "total_objective": 0,
        "baseline_objective": 0,
        "constraints": {},
        "baseline_constraints": {},
        "lambdas": {},
        "converged": False,
    }


# ── failSolveJob (store.ts:753-790), reduced to the cache write ──────
def fail_solve_job(state: dict, node_id: str, error: str) -> None:
    """Mirror of the `nextCached` construction in failSolveJob.

    Key line (store.ts:760):  ...(s.solveResults[nodeId] ?? { synthetic })
    When there is NO prior result, the synthetic all-zero error result is used.
    """
    job = state["solveJobs"].get(node_id)
    if job is None:  # store.ts:756  if (!job) return s
        return
    del state["solveJobs"][node_id]

    prior = state["solveResults"].get(node_id)
    if prior is not None:
        base = {"result": prior["result"], "originalResult": prior["originalResult"]}
    else:
        # store.ts:761-762 — the fabricated placeholder.
        base = {
            "result": synthetic_error_solve_result(),
            "originalResult": synthetic_error_solve_result(),
        }

    next_cached = {
        **base,
        "terminalStatus": None,
        "jobId": job["jobId"],
        "configHash": job["configHash"],
        "constraints": job["constraints"],
        "nodeLabel": job["nodeLabel"],
        "frontier": None,
        "selectedPointIndex": None,
        "error": error,  # store.ts:771
    }
    state["solveResults"][node_id] = next_cached


# ── buildOptimiserPreview (store.ts:235-244) ────────────────────────
def build_optimiser_preview(cached: dict) -> dict:
    # NOTE: `error` is NOT copied into the preview payload. The
    # OptimiserPreviewData type (OptimiserPreview.tsx:47-54) has no `error`
    # field, so the panel literally cannot see the failure.
    return {
        "result": cached["result"],
        "jobId": cached["jobId"],
        "constraints": cached["constraints"],
        "nodeLabel": cached["nodeLabel"],
        "frontier": cached["frontier"],
        "selectedPointIndex": cached["selectedPointIndex"],
    }


# ── getOptimiserPreview (store.ts:1090-1096) ────────────────────────
def get_optimiser_preview(state: dict, node_id: str) -> Optional[dict]:
    cached = state["solveResults"].get(node_id)
    if cached is None:  # store.ts:1092 — null ONLY on absence, NOT on .error
        return None
    return build_optimiser_preview(cached)


# ── App.tsx:612-621 render decision + panel status summary ───────────
def render_bottom_panel(state: dict, active_node_id: str) -> dict:
    """Returns {'component': 'OptimiserPreview'|'DataPreview'|..., ...}."""
    opt_preview = get_optimiser_preview(state, active_node_id) if active_node_id else None
    if opt_preview:  # App.tsx:613 — no `.error` check here
        result = opt_preview["result"]
        # OptimiserPreview.tsx:368-372 statusSummary
        status_summary = " | ".join(
            s for s in [
                "Converged" if result["converged"] else "Not converged",
                # iterationSummary/quotes omitted (None for this result)
            ] if s
        )
        # OptimiserPreview.tsx:386 collapsedMeta
        collapsed_meta = (
            f"{'Converged' if result['converged'] else 'Not converged'} | "
            f"Objective: {format_number(result['total_objective'])}"
        )
        return {
            "component": "OptimiserPreview",
            "statusSummary": status_summary,
            "collapsedMeta": collapsed_meta,
            "renderedResultStatus": result["status"],
        }
    return {"component": "DataPreview"}


# ── CONTROL: the guard the codebase ALREADY has (OptimiserConfig.tsx:228) ──
def optimiser_config_solve_result(cached: Optional[dict]) -> Optional[dict]:
    # const solveResult = cached?.error ? null : (cached?.result ?? null)
    if cached is None:
        return None
    if cached.get("error"):
        return None
    return cached["result"]


def main() -> None:
    NODE = "opt_1"

    # A solve is started, then fails BEFORE producing any result, and there is
    # NO previously cached result for this node (the exact NEWBUG-03 path,
    # e.g. OptimiserConfig.tsx:354-355 startup-failure).
    state = {
        "solveJobs": {
            NODE: {
                "jobId": "startup-failure:opt_1",
                "configHash": "h1",
                "constraints": {},
                "nodeLabel": "Optimise prices",
            }
        },
        "solveResults": {},  # <-- no prior result
    }

    fail_solve_job(state, NODE, "Solver failed to start: infeasible constraints")

    cached = state["solveResults"][NODE]
    print("[store] cached.error           =", repr(cached["error"]))
    print("[store] cached.result.status   =", repr(cached["result"]["status"]))
    print("[store] cached.result.converged=", cached["result"]["converged"])
    print("[store] cached.result.total_objective =", cached["result"]["total_objective"])

    # 1) getOptimiserPreview returns a NON-null preview for the failed solve.
    preview = get_optimiser_preview(state, NODE)
    assert preview is not None, (
        "EXPECTED BUG: getOptimiserPreview returned a preview for a FAILED "
        "solve with no prior result, but it was None (bug would be absent)."
    )
    assert "error" not in preview, (
        "Preview payload unexpectedly carried `error` — the panel could then "
        "render an error state; bug shape changed."
    )
    print("[preview] is None              =", preview is None)
    print("[preview] carries 'error' key  =", "error" in preview)

    # 2) App.tsx renders OptimiserPreview (NOT a pure error/DataPreview state).
    panel = render_bottom_panel(state, NODE)
    print("[panel] component              =", panel["component"])
    print("[panel] statusSummary          =", repr(panel["statusSummary"]))
    print("[panel] collapsedMeta          =", repr(panel["collapsedMeta"]))

    assert panel["component"] == "OptimiserPreview", (
        f"EXPECTED BUG: failed solve should NOT render the full result panel, "
        f"but App.tsx rendered {panel['component']!r}."
    )
    # The specific WRONG rendered values: a failed solve shown as a real,
    # non-error result row reading objective 0 / not converged.
    assert panel["statusSummary"] == "Not converged", panel["statusSummary"]
    assert panel["collapsedMeta"] == "Not converged | Objective: 0", panel["collapsedMeta"]
    assert panel["renderedResultStatus"] == "error", panel["renderedResultStatus"]
    print(
        "\nREPRODUCED: a FAILED solve (status='error') with no prior result is "
        "rendered by OptimiserPreview as a real result row\n"
        "            subtitle='Not converged', collapsed='Not converged | Objective: 0' "
        "(objective fabricated as 0), with NO error styling."
    )

    # 3) CONTROL — the guard the codebase already applies in OptimiserConfig.
    #    Same cached entry, correct handling: result is suppressed on .error.
    config_result = optimiser_config_solve_result(cached)
    assert config_result is None, (
        "CONTROL FAILED: OptimiserConfig.tsx:228 guard should null the result "
        "on .error; if this fires, the asymmetry premise is wrong."
    )
    print(
        "\nCONTROL: the SAME store entry, run through the guard the codebase "
        "ALREADY has\n         (OptimiserConfig.tsx:228 `cached.error ? null : "
        "cached.result`), yields result=None.\n         The bug is that "
        "getOptimiserPreview / App.tsx omit this identical guard.\n"
    )

    # Sanity: format_number(0) really is the literal "0".
    assert format_number(0) == "0", format_number(0)

    print("ALL ASSERTIONS HELD — NEWBUG-03 reproduced (display defect).")


if __name__ == "__main__":
    main()
