# Execution engine roadmap

## Scope

Owns the Polars operation registry's memory policies, materialisation admission,
and the performance-lane evidence that certifies them. Current behaviour is
specified in [the execution-engine specification](../execution-engine/high-level.md)
and its [low-level companion](../execution-engine/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXEC-P09 | Planned | P2 | Certify the memory policy of expression-level `shift`, `diff`, and `pct_change` with measured evidence. |

## Planned improvements

### EXEC-P09 — Certify expression-level neighbouring-row memory policies

**Why:** The registry records the frame method `df.shift(n)` and the expression
method `pl.col(...).shift(n)` separately. Polars 1.44 executes both through the
same streaming shift node, whose buffered state now grows with its input (the
node's queued-frame storage changed from memory-manager tokens to spillable
frames between Polars 1.39.3 and 1.44.2). The
performance lane measured the frame form and it is now a certified
materialisation boundary. The expression form, the usual way to build a lag
column, still carries the `streaming` policy with no memory evidence:
`_order_dependent_expr("shift", "reads neighbouring rows")`. Polars lowers
`expr.diff(n)` to `expr - expr.shift(n)`, so `diff` shares the node. `pct_change`
is also recorded as streaming without evidence and its lowering has not been
checked. The registry's rule is that a streaming policy is the safety-critical
direction: an operator wrongly recorded as streaming is never admitted against a
memory limit, so a large lag computation relies only on the worker memory cap.

**Evidence gathered so far:** incremental peak RSS in MiB on Linux (WSL,
Polars 1.44.2, `POLARS_MAX_THREADS=4`, three fresh-process samples each, sunk to
Parquet through the streaming engine, 13-column fact fixture with 25,000-row row
groups):

| Plan | 1.5M rows | 6M rows |
|---|---|---|
| Full-width scan (control) | 156–161 | 216–225 |
| Full width + `pl.col("v1").shift(1)` lag column | 189–213 (1.25x) | 321–510 (1.8x) |
| `select(pl.all().shift(1))` | 211–227 (1.37x) | 421–571 (2.3x) |
| Two-column scan (control) | 56–59 | 93–96 |
| Two columns + lag column | 73–75 (1.27x) | 125–131 (1.37x) |

For comparison, the frame method measured 1.46x the scan control on the CI perf
runner at 1.5M rows (1.07x on Polars 1.39.3) and 2.2x at 6M rows on the same
Linux host, and the same frame-method plan stays near 1.15x on Windows. The
growth is scheduling-dependent (wide sample spread at 6M rows), so a single
row count cannot tell a constant buffer from growth, and a fixed floor ratio
certifies the platform as much as the operator.

**Plan:**

1. Add expression probes to `tests/performance/_operation_memory_probe.py`: a
   lag column (`with_columns(pl.col("v1").shift(1).alias(...))`), a difference
   column (`diff`), and a percentage-change column (`pct_change`) over the
   full-width fixture, each paired against the `scan` control, plus the
   two-column variants against `scan_narrow`.
2. Measure each at two row counts on the Linux reference runner (for example
   1.5M and 6M rows) so the report shows whether the extra memory is constant
   or grows. Record the paired samples in the perf report.
3. Set each expression's registry policy from that evidence and record
   `memory_evidence="measured"`:
   - within the passthrough ceiling at both sizes: keep `streaming` with the
     measurement cited in the note;
   - growing with the input: make it a `materialisation_boundary` recorded at
     the expression level, as `over` is, so the containing Polars node becomes
     the boundary with that expression as its operator.
4. **Decision before implementing a boundary:** the boundary estimate is
   whole-frame (rows x width x 3.0 x factor). A lag column measured far below
   that, so choose between accepting the conservative whole-frame estimate
   (consistent with `over`, but may warn about or refuse large previews and
   runs that would fit) and introducing an estimate scaled to the shifted
   columns' width (tighter, but a new estimator path that needs its own
   certification). Record the choice in the execution-engine specification.
5. Update the execution-engine specifications first, then the registry, the
   certification lane, the strategy contract, chunk-suffix, operator-factor,
   and boundary-equivalence tests, and regenerate the compatibility corpus.

**Acceptance:**

- The performance lane measures the expression forms of `shift`, `diff`, and
  `pct_change` at two row counts, and every one of those registry entries cites
  its evidence class.
- A streaming entry stays within the lane's passthrough ceiling at both sizes;
  a boundary entry's estimate bounds its observed peak, with its factor derived
  from the evidence.
- For any expression that becomes a boundary, a Polars node using it plans
  `materialisation-boundary` with that expression as `blocking_operator` under
  every `ExecutionProfile`, is rejected in a chunk suffix, and produces the same
  rows, order, and schema as plain Polars through the planned executor.
- The estimate-basis decision and each expression's policy are documented in
  the execution-engine specification, and the compatibility corpus reflects the
  new policies.

**Dependencies:** Builds on the frame-method `shift` reclassification in PR #218
(`a5723b74`, `395962fb`); start after that PR merges.

**Evidence:** `src/haute/_polars_operations.py` (`_order_dependent_expr` entries
for `shift`, `diff`, `pct_change`; frame `shift` boundary entry);
`tests/performance/test_execution_engine_certification.py`
(`test_global_operation_memory_policies_match_the_registry`,
`_MAX_STREAMING_FLOOR_RATIO`, `_OPERATION_RECEIVERS`);
`tests/performance/_operation_memory_probe.py`;
`tests/test_polars_backend_strategy_contract.py`; `tests/test_chunk_plan.py`;
`tests/test_ram_estimate.py`; `tests/test_boundary_operator_equivalence.py`;
`tests/polars_compatibility_corpus.json` (`expr_shift`); Polars 1.44.2
`crates/polars-stream/src/physical_plan/lower_expr.rs` (`Shift` node, `diff`
lowered to `expr - expr.shift(offset)`) and `crates/polars-stream/src/nodes/shift.rs`
(queued frames moved from memory-manager tokens to `SpillFrame` between 1.39.3
and 1.44.2); PR #218 perf run `34915505016`.
