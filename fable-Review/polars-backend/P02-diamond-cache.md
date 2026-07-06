# P02 — Eager diamond `.cache()` mints a distinct cache id per child (no sharing)

**Severity:** HIGH (verified experimentally) · **Effort:** S · **Silent-wrongness:** no (pure performance)

## FR-02 — `_execute_lazy.py:1819-1823` (`_execute_eager_core` input assembly)

### Evidence
```python
picked = _pick_source_frame(parent_frame, edge)
parent_lf = picked if isinstance(picked, pl.LazyFrame) else picked.lazy()
if children_count.get(pid, 0) > 1:
    parent_lf = parent_lf.cache()
input_lfs.append(parent_lf)
```
The comment above this block claims the diamond optimisation: "downstream `.collect()` re-uses the
materialised plan across branches instead of duplicating upstream work … compute src's plan once, not
twice."

**Verified against the installed Polars (>=1.39.2):** each `.cache()` *call* creates a `CACHE` node
with a fresh UUID id. Two children calling `.cache()` on the same parent LazyFrame get **two distinct
cache ids**, so within a single collect the parent plan executes once per id and each result is held
in cache memory separately. Repro (run with `PYTHONIOENCODING=utf-8`):

```python
import polars as pl
base = pl.LazyFrame({'a': range(1_000_000)}).with_columns((pl.col('a') * 2).alias('b'))
c1, c2 = base.cache(), base.cache()
plan = pl.concat([c1.select(pl.col('b').sum()), c2.select(pl.col('b').max())]).explain()
# plan contains TWO different "CACHE[id: …]" uuids  → no sharing
shared = base.cache()
plan2 = pl.concat([shared.select(pl.col('b').sum()), shared.select(pl.col('b').max())]).explain()
# plan2 contains ONE uuid appearing twice          → sharing
```

### Impact
- **Full-materialisation preview/trace** (parents are DataFrames): `.lazy().cache()` on an in-memory
  frame is pure overhead per child (a single-hit cache = one extra materialised copy held for the plan
  lifetime), with zero benefit.
- **Target-only preview** (the production route — ancestors stay lazy): a shared lazy ancestor whose
  branches reconverge at the target executes once per branch inside the target's collect, plus one
  cached copy per branch in RAM. This is the opposite of the stated intent.

### Fix design
Apply `.cache()` **once, at the producer**, not per consumer:

- In `_execute_eager_core`, when a node's output is stored lazily (`runtime_outputs[nid] = output_lf`,
  the `should_materialize == False` branch, ~:2048) and `children_count.get(nid, 0) > 1`, store
  `output_lf.cache()` instead. All children then compose from the *same* cached LazyFrame object →
  one cache id → true intra-collect sharing.
- For the multi-frame lazy-ancestor branch (`lazy_ports`, ~:1927-1936), apply the same rule per port
  frame **only if** a port can be consumed by >1 edge (check `incoming_edges` fan-out per
  `(nid, port)`; if that's not derivable cheaply, skip multi-frame — it's a source type and sources
  are cheap scans).
- Delete the per-child `.cache()` at :1821-1822 entirely. When the parent is a materialised DataFrame
  there is nothing to share (the data is already in RAM), so no `.cache()` is needed on that branch at
  all.

### TDD plan
1. Failing plan-shape test: build a diamond graph (source → A → {L, R} → target) through
   `_execute_eager_core` with `materialize_node_ids={target}` so A stays lazy; capture the target's
   pre-collect plan (expose via the collected frame's provenance or, simpler, unit-test the input
   assembly: call the code path that builds `input_lfs` for L and R and assert
   `l_plan.explain()` / `r_plan.explain()` contain the **same** `CACHE[id: …]` uuid). Today they
   differ → test fails.
   - Simplest robust assertion: regex `CACHE\[id: ([0-9a-f-]+)\]` over the final target plan
     `explain()`, assert `len(set(ids)) == 1` and `len(ids) == 2`.
2. Behavioural count test: make A a `map_batches` node with a call counter (or a scan of a temp file
   with an open-count spy); collect the target; assert the counter is 1 (currently 2).
3. Regression: full-materialisation preview path — assert no `.cache()` overhead is added when parents
   are DataFrames (plan contains zero CACHE nodes).

### Acceptance
- Diamond ancestor computed once per target collect (counter test).
- One distinct cache id in the reconverging plan.
- No CACHE nodes in fully-materialised eager execution.
- Existing executor/trace test suites pass.
