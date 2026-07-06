# P09 — Optimiser-apply trace re-applies the entire portfolio to explain one clicked quote

**Severity:** MEDIUM (trace latency) · **Effort:** M · **Silent-wrongness:** no

## FO-21 — `_optimiser_apply_explainability.py:168-187` (`_explain_online`)

### Evidence
```python
df = _prepare_online_apply_frame(parent_frame, artifact)      # collects the FULL parent frame
applier = ApplyOptimiser(lambdas=lambdas, ...)
explained = applier.with_explainer_columns(df)                # full apply + joins + group_by
quote_rows = explained.filter(
    pl.col(qid_col).cast(pl.Utf8) == str(quote_id_value)      # …then keeps ONE quote
).sort(step_col)
```
`with_explainer_columns` internally runs `self.apply(df)` — ingesting every row into a Rust
QuoteGrid — plus a left-join for `selected` and a join + `group_by(maintain_order=True)` for
`is_baseline` (`price_contour/apply.py:250-407`), all over the full parent frame. It is invoked
per traced cell (`_trace_enrichment.py:1809`) with no memoisation, and
`load_optimiser_artifact` deep-copies the artifact JSON on every call (`_optimiser_io.py:74`).
Tracing three optimiser columns on one row = three full-portfolio applies.

### Impact
The README promises warm trace clicks are instant ("Every click after that pulls from cache").
For pipelines containing an OPTIMISER_APPLY node, each click on its outputs pays
O(portfolio × scenarios) Rust ingest + two joins — seconds on realistic books — where
O(scenarios-for-one-quote) suffices.

### Fix design
Filter to the clicked quote **before** the apply:
`parent_frame.filter(pl.col(qid_col).cast(pl.Utf8) == str(quote_id_value))` ahead of
`_prepare_online_apply_frame`. Both explainer columns are per-quote computations — `selected`
is the per-quote argmax and `is_baseline` groups by quote id (`apply.py:250-407`) — so a
single-quote slice yields identical values for that quote (assert this in the parity test
rather than trusting the argument). Keep the existing loud no-candidates error when the filter
returns empty (`:188-191`). Optional second step: memoise `(artifact digest, node_id) →
applier` within a trace request to skip the per-click deep-copy; measure before adding.

Apply the same pre-filter to the ratebook trace path only if it shows the same shape — its
input-row match already pushes predicates into Polars and was cleared as efficient
(`_optimiser_apply_explainability.py:474-545`).

## TDD plan (failing tests first)
1. `tests/test_optimiser_apply_trace_enrichment.py::test_online_trace_only_applies_clicked_quote`
   — N-quote parent frame; spy on `ApplyOptimiser.with_explainer_columns` input height; assert
   it receives only the clicked quote's rows (currently N×scenarios), and the trace payload is
   byte-identical to the full-frame result restricted to that quote (parity assertion, run the
   old path in the test for comparison).
2. Edge parity: quote at frame boundaries, single-scenario quote, Categorical quote_id — payload
   equality between sliced and full computation.
3. Regression: unknown quote_id still raises the existing
   `no optimiser candidates found for quote_id=…` error.

### Acceptance
- Trace enrichment cost for OPTIMISER_APPLY cells is O(one quote), not O(portfolio).
- Trace payloads bit-identical to today's for the same click.
- Existing `test_optimiser_apply_trace_enrichment.py` suite passes.
