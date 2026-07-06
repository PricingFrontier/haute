# Orchestrator review notes — Phase 1

Independent review of the agent verdicts (not a re-run of the agents — my own spot-checks).

## Independent re-verification
I re-ran the repro scripts for all 8 high-severity findings on this machine. **All 8 reproduce**, and the scripts assert on specific wrong values (not setup/import artifacts). Confidence in the high set is solid.

## Severity correction — codegen passthrough cluster (findings #1, #2, #3)
The agents tagged the blast radius of the `optimiserApply`/`optimiser`/`modelling`/`scenarioExpander`/`liveSwitch` passthrough-body divergence as **"deployed/standalone"**. I traced the deploy path directly and that is **overstated**:

- **Deployment is SAFE.** `deploy/_model_code.py` (`HauteModel.predict`, line 49) loads the **pruned graph** from the manifest and calls `score_graph(graph=self._graph, ..., artifact_paths=...)` — the executor path with the real node builders (`_build_optimiser_apply`, etc.). It never executes the generated passthrough body. `deploy/_config.py:543` bundles the *graph* (via `parse_pipeline_file`), not the generated `.py`.
- **In-canvas preview/trace/batch is SAFE** — also the executor.
- **What actually breaks:** taking the exported `.py` and running it standalone via `pipeline.run()` (`pipeline.py:327`, `_scenario_ctx='batch'`) or `pipeline.score()` (`:364`, `='live'`). `Node.__call__` (`:54-68`) invokes the literal generated body with no executor dispatch, so for these node types the standalone file performs **no operation** (optimiserApply/optimiser/modelling/scenarioExpander) or routes the **wrong branch** (liveSwitch under batch).

**Correct framing:** this violates the documented *portability / "it's just Python, it still works, take it with you"* guarantee in the README — a real, high-severity promise breach — but it does **not** corrupt deployed or in-canvas prices. Treat it as "saved-file standalone equivalence", not "production mispricing".

## Trust signal — Phase 0 concurrency fears did NOT hold
The entire Phase 0 concurrency cluster was **refuted** on close inspection: the preview-cache non-atomic RMW race, the preamble-lock-on-miss race, the DataFrameExecutionCache weakref.finalize-on-GC-thread lifecycle, and the JobStore by-reference reads were each shown safe (guards/serialisation the finders missed). The execution engine's concurrency design is sturdier than the map suggested.

## In-flight branch overlap — withdrawn
The branches under active development are unpublished and not visible from local refs, so overlap cannot be assessed; the published branches previously scanned are not the active work. No branch coordination applies — remediation is deferred and read-only. Order by severity x real-world urgency x effort.
