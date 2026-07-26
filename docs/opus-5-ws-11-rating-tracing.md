# WS-11 — Rating engine & trace explainability (full stack)

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: unassigned · Status: not started.

**Branch:** `opus5/ws-11-rating-tracing`

## Mission

The rating runtime and the explain-how-this-number-happened stack that sits on top of it:
trace execution, correlation, enrichment and the trace UI. These travel together because the
rating engine and its trace enrichment must agree rule-for-rule (`rating-3` is exactly that
disagreement), and because the enrichment payload is the trace UI's contract. The dominant
failure shape here is *confidently wrong output*: silently overwritten factors, credited
rules the runtime skipped, and backend failures rendered as ordinary null results.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| rating | 0 | 1 | 6 | 3 |
| tracing | 0 | 1 | 6 | 9 |
| frontend-trace-ui | 0 | 1 | 5 | 6 |
| cross-cutting (assigned) | 0 | 0 | 1 | 0 |
| **Total** | **0** | **3** | **18** | **18** |

## Priorities

**P1 — silently wrong premiums:**

- `rating-1` (H): two rating tables sharing an `outputColumn` silently overwrite each other
  and the combine squares the survivor (reproduced: `premium = 25.0` for a 2.0/5.0 pair).
  Reachable from the GUI. Reject duplicates eagerly in
  `_rating_step_config.expand_rating_step_config_from_sidecar`, and assert before
  `_combine_rating_output`.
- `rating-2` (M): a legacy `combinedColumn` equal to a table `outputColumn` silently
  overwrites that table's factor column.
- `rating-7` (M): a banding factor whose rules are all unusable silently produces no output
  column, with no error and no warning; `rating-10` (L): absent-factor validation runs after
  the incomplete-table guard, so a typo'd factor in an entry-less table is a silent no-op.
- `rating-4` (L): the rating miss guard is elided by Polars projection pushdown, so it does
  not fire when the spec says it must.

**P1 — trace correctness:**

- `tracing-1` (H): edge-join correlation crashes with `AttributeError` when the base parent
  is a multi-frame source — on the topology both specs call canonical — surfacing as an
  opaque HTTP 500.
- `rating-3` (M): trace enrichment credits a continuous banding rule the runtime skipped,
  violating the stated shared-contract invariant. Fix in `_trace_enrichment.py` against
  `_rating.py`'s actual rule filter.
- `frontend-trace-ui-1` (H): backend banding and model-score enrichment *failures* are
  rendered as ordinary null results — a hard exception presented to the user as a legitimate
  explanation, in the component whose only job is to explain. Render the error as an alert
  and suppress the normal rows.
- `frontend-trace-ui-6` (M): conditional branch highlighting re-parses the expression on the
  client and trusts a backend index against its own parse.

**P2 — bugs and brittleness:** `tracing-10` (waterfall membership decided by a raw-text
regex that matches comments and `==`), `tracing-6` / `tracing-7` (unguarded index makes the
documented partial-trace fallback unreachable; omission filter drops unresolved ancestors),
`seam-exec-4` (trace can never reuse the preview cache — key scopes differ),
`frontend-trace-ui-3` (object URL revoked before the download starts),
`frontend-trace-ui-5` (grouping keys on a `node_type` the backend can never emit, pinned by
27 tests), `frontend-trace-ui-11` / `-10` (unreachable branch; reset attributed to the wrong
mechanism).

**P3 — spec truth:** fold the shipped `(0.6.0)` and 0.7.0 tracing contracts and move
`TraceCorrelationUnsupportedError` into the present-tense failure sections
(`contracts-d-2`, `tracing-2`, `tracing-8`, `tracing-11`, `contracts-d-11`), correct the
key-construction and matcher descriptions (`tracing-3`, `tracing-5`, `tracing-4`), the
rating 0.6.0 fold and missing public errors (`contracts-d-1`, `rating-6`, `rating-5`,
`rating-8`), the trace-UI contract and testing sections (`contracts-c-1`,
`frontend-trace-ui-4`, `-13`, `-12`, `-9`, `-7`), and `testing-credibility-7`'s tracing half
("Known coverage gaps: none identified" while four test files are unindexed). Drop the dead
comparators and the `sys.modules` import cycle (`tracing-9`, `over-complication-7`).

## Finding inventory

High (3): `rating-1`, `tracing-1`, `frontend-trace-ui-1`.
Medium (18): `contracts-d-1`, `rating-2`, `rating-3`, `rating-5`, `rating-6`, `rating-7`,
`contracts-d-2`, `seam-exec-4`, `tracing-2`, `tracing-3`, `tracing-5`, `tracing-10`,
`contracts-c-1`, `frontend-trace-ui-3`, `frontend-trace-ui-4`, `frontend-trace-ui-5`,
`frontend-trace-ui-6`, `testing-credibility-7`.
Low (18): `rating-4`, `rating-8`, `rating-10`, `contracts-d-11`, `over-complication-7`,
`tracing-4`, `tracing-6`, `tracing-7`, `tracing-8`, `tracing-9`, `tracing-11`, `tracing-12`,
`frontend-trace-ui-7`, `frontend-trace-ui-9`, `frontend-trace-ui-10`,
`frontend-trace-ui-11`, `frontend-trace-ui-12`, `frontend-trace-ui-13`.

## File ownership (exclusive)

- `src/haute/_rating.py`, `_rating_step_config.py`, `src/haute/errors.py` rating entries
- `src/haute/trace.py`, `_trace_correlation.py`, `_trace_enrichment.py`,
  `_trace_waterfall.py`
- `frontend/src/trace/**` (`StepCard.tsx`, `CalculationHero.tsx`, `BandingDetail.tsx`,
  `NodeDetailBlock.tsx`, `bandingRows.ts`, `traceOrigins.ts`),
  `frontend/src/panels/TracePanel.tsx`, `frontend/src/panels/trace/**`,
  `frontend/src/hooks/useTracing.ts`
- `docs/specs/rating/**`, `docs/specs/tracing/**`, `docs/specs/frontend-trace-ui/**`
- Their tests (`tests/test_rating.py`, `tests/test_trace*.py`,
  `frontend/src/trace/__tests__`, `frontend/src/hooks/__tests__/useTracing.test.ts`)

## Cross-stream touchpoints

- The trace HTTP route lives in `routes/pipeline.py` (WS-04) — `tracing-1`'s 500 and
  `seam-exec-4`'s cache-key scope both surface there. Fix the trace layer here; ask WS-04 for
  any route-level change.
- `execution_facade.preview_lineage_cache_key` is WS-02/WS-03 territory — `seam-exec-4` may
  need a shared decision on preview vs trace key scope. Do not change the factory here.
- `_model_explainability.py` (WS-07) produces the model-score enrichment payload that
  `frontend-trace-ui-1` renders — coordinate the error-field shape.
- `types/guards.ts` (WS-09) types the trace payload; request guard changes there.
- `expression-parsing-1` (WS-06) is the other half of "confident wrong trace output" — the
  evaluator fix lands there; this stream should not paper over it in enrichment.

## Definition of done

- Duplicate/overwriting rating outputs are rejected with the table index and column named;
  the banding rule filter is shared or provably equivalent between runtime and trace, with a
  test that pins the agreement.
- Multi-frame edge-join correlation returns a typed error or the right frame, never
  `AttributeError`; the trace UI shows enrichment failures as alerts.
- Tracing and rating contract sections folded and deleted; Testing sections index the four
  missing trace test files.
- Baseline entries for the three components deleted; findings fixed or deferred with reasons.

## Verification

- `uv run pytest tests/test_rating.py tests/test_trace_multi_frame.py tests/test_trace_correlation_remediation.py -q`
- `npm --prefix frontend test -- src/trace/__tests__ src/panels/trace`
- `npm --prefix frontend run typecheck`; `uv run pytest tests/test_docs_accuracy.py -q`.
