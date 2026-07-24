# Test-suite hardening roadmap

**Status:** Active

**Current as of:** 2026-07-20

## Outcome

Make high-risk behaviour fail at the boundary where it becomes incorrect, not
only after a user-visible workflow happens to expose it. The test suite should
use small canonical oracles, production-shaped fixtures, and real execution
modes to protect ordering, cardinality, dtype, state, and artifact contracts.

## Verified baseline

The quote-contiguity incident that prompted the original hardening note is
implemented. Scenario-expander output is tested through streamed parquet for
quote-contiguous, ordered scenario blocks; the optimiser boundary is tested for
the same ordering after its narrow typed projection; and production grid setup
uses only `price_contour.build_grid_from_parquet_chunked`. Default byte-budget
selection, explicit row overrides, invalid values, interleaved quote blocks,
and real Categorical quote identifiers all have focused tests.

The broader optimiser examples are also much further along than the source
note implied. Existing tests cover missing/null/non-finite inputs, narrow
projection, per-quote auto-range extrema whose extrema occur in middle
scenarios, streaming-versus-lazy auto-range agreement, selected-point state and
artifact materialisation, real selected-row objective/constraint totals, and
real ratebook solve/save/apply agreement. These are delivered examples to
generalise with canonical oracles; they are not missing features to re-plan.

The repository already has meaningful contract, parity, property, test-debt,
and mutation infrastructure: bounded-collect and streaming contracts,
projection-planner tests, Hypothesis codegen/parser round trips, a strict
skip/xfail/importorskip fingerprint budget with a review deadline, and a
sharded mutation runner with checked-in targets and survivor thresholds.
Expression parsing has broad unit and scenario coverage, including a curated
real-Polars parity suite. Optimiser and expression-parser property/fuzz suites
are the notable domain gaps; the repository-wide property suite does not cover
them.

Browser UI workflows are not owned here. The Rating Step/Banding incident and
all user-journey, visual, accessibility, and browser coverage belong to
[Frontend UI quality](frontend-ui-quality.md).

## Canonical-oracle audit

| Boundary | Verified evidence | Remaining evidence |
|---|---|---|
| Scenario expansion | Streamed parquet and the optimiser projection preserve quote contiguity, ordered indices, cardinality, and solver dtypes. | Compare collected and streamed evaluation from the same generated inputs and retain shrunk failures. |
| Optimiser projection | Narrow columns, String-to-Categorical casting, Categorical input, missing columns, null identifiers, and numeric identifiers are covered. | Generate awkward but supported schemas and prove String/Categorical business-result equivalence rather than only independent acceptance. |
| Grid construction | The sole production path is chunked; default/explicit selection, invalid configuration, interleaved blocks, and a real Categorical build are covered. | Use the real library and a separate canonical grid oracle across `n_steps`, non-multiples, one quote block, larger-than-input chunks, and undersized failures. Do not add an unchunked Haute production path for parity. |
| Solve and frontier | A real solve/apply test reconciles selected rows and totals; selected-point summaries, artifacts, save/log/apply behaviour, and stale-state rejection are extensively covered. | Add generated deterministic solve cases for selected indices, totals, absolute constraints, and repeat stability, with a small real-library confirmation set. |
| Auto-range | Middle-scenario per-quote extrema, narrow projection, baseline-free inputs, chunk splits, and streaming/lazy agreement are covered by examples. | Generalise those invariants over generated quote/scenario/chunk shapes; do not duplicate worker, planner, or scale testing. |
| Ratebook keys | Scalar/composite numeric values, strings, nulls, duplicate resolution, float solve/save/apply round trips, unseen levels, and missing canonical counts have examples. | Add generated idempotence, collision, and apply-join properties over the declared supported value domain. |
| Expression parsing | Curated malformed inputs and a curated one-row real-Polars parity suite cover many operators and recovery branches. | Add bounded malformed-source fuzzing, a declared differential grammar, and genuine multi-row partition/window evidence. |
| Test-health gates | Test-debt fingerprints/reasons, a review deadline, strict xfail policy, mutation targets, survivor budgets, sharding, and CI artifacts exist. | Extend evidence only for this track's high-risk boundaries, add owner/expiry detail where the current global review deadline is insufficient, and publish actionable skip/flaky/mutation summaries without creating a second harness. |

## Scope and ownership

This track owns backend and cross-boundary test design: explicit invariant
inventories, canonical-oracle tests, property/differential testing, regression
fixture policy, and test-health evidence. It does not own implementation work
that the tests reveal:

- browser UI workflows, visual checks, accessibility, and frontend config-shape
  matrices belong to [Frontend UI quality](frontend-ui-quality.md);
- execution fault injection, DAG fuzzing, Polars version compatibility, and
  scale/RSS evidence belong to [Backend execution hardening](backend-execution-hardening.md);
- planner semantics and physical execution-strategy decisions belong to
  [Polars execution strategy](polars-execution-strategy.md);
- isolated-worker artifact/event protocols belong to
  [Worker isolation](worker-isolation.md); and
- Edge Join interaction and browser coverage were delivered under the retired
  [Edge Join completion roadmap](edge-join-completion.md) and now belong to
  the durable frontend specifications and dedicated regression suites.

The same defect may need tests in this track and an implementation change in
another one. Keep the oracle and ownership boundary explicit rather than
creating parallel test harnesses or silently broadening this roadmap.

## Milestone 1 — Boundary-contract inventory and ratchet

Create a risk-based inventory for boundaries where a correct-looking frame,
response, or artifact can still be semantically wrong. Seed it from the matrix
above, then include the still-unmapped classes from the whole-codebase review:
lazy/checkpoint/cache/fingerprint boundaries; joins, banding, rating, live
switches, model scoring, and custom-Polars transformations; and config-sidecar,
save/load, MLflow, deploy, parquet, and JSON artifact round trips. For each
entry record the producer and consumer, invariant, canonical oracle, owning
source/test, supported execution modes, real-library or workflow fixture,
failure behaviour, and implementation-roadmap owner when a defect is found.

The inventory must distinguish an implementation detail from a required
contract. Quote contiguity, stable scenario cardinality, accepted identifier
dtypes, selected-point artifact identity, and parser/Polars agreement are
examples of contracts. A particular collection call or an obsolete unchunked
production route is not.

**Tests first**

- Add a machine-checkable ratchet that rejects a high-risk boundary without an
  owner, invariant, oracle, and at least one test in each supported execution
  mode.
- Add focused contract tests before changing an inventoried boundary; every
  test must identify the consumer-facing invariant it protects.
- Seed any generated case and retain its minimal replay fixture on failure.

**Acceptance criteria**

- Every inventoried high-risk boundary links to the source path, owning test,
  and executable modes that substantiate its status.
- The ratchet reports missing evidence as a named test failure, not an informal
  review observation.
- New boundary fixes add an invariant-level regression that would have failed
  before the specific implementation defect was known.
- Entries whose implementation belongs to another roadmap link to that owner;
  this inventory must not grow a second planner, worker protocol, browser
  harness, or execution fault framework.

## Milestone 2 — Complete the optimiser property and chunk-oracle matrix

Generalise the implemented optimiser examples using canonical data and
domain-result oracles. Where both are supported, collected and streamed
scenario expansion must agree on quote contiguity, per-quote scenario
cardinality, values, and dtypes. Generated projection cases must prove
equivalent String and Categorical business results while retaining the already
tested loud failures and narrow solver input.

Exercise every supported awkward chunk size against a canonical quote-grid
oracle: exactly one scenario block, `n_steps`, non-multiples that the library
rounds to quote boundaries, and larger-than-input boundaries. Assert the real
library's typed failure when the configured chunk is smaller than `n_steps`.
Do not reintroduce the obsolete unchunked Haute production ingestion path
merely to make a parity test possible. Build the oracle independently from
deterministic quote/scenario input and the documented grid semantics.

Add solve properties for deterministic inputs: selected scenario indices,
objective and constraint totals, absolute-constraint interpretation, and
repeated-solve stability within a stated tolerance. Generalise the already
implemented auto-range examples over quote counts, scenario counts, middle
extrema, and awkward reducer chunks, comparing the streaming and lazy modes
only where both are supported.

**Tests first**

- Add generated small quote/scenario matrices with a separately computed
  canonical oracle; retain a seed and minimal data table on failure.
- Pair direct service tests with real `price_contour` integration tests for the
  identifier, grid, and solve contracts that cross the library boundary.
- Add route-level tests only where the route selects a materially different
  execution mode, lifecycle, or artifact boundary from the service.

**Acceptance criteria**

- Every supported optimiser execution mode has a canonical-oracle contract
  test; no matrix cell relies solely on row counts, job completion, or mocked
  calls.
- All supported chunk boundaries preserve the canonical grid result, while
  undersized/unsupported boundaries fail with the documented typed error.
- Deterministic solve, totals, and absolute-constraint properties have
  replayable generated coverage and real-library confirmation; auto-range has
  replayable generated coverage against an independent reducer oracle.

## Milestone 3 — Ratebook canonicalisation properties

Generalise the existing ratebook examples over the value/dtype combinations
that affect the apply join. Generated coverage must include scalar and
composite levels, numeric-looking strings, the declared null policy, float
normalisation, idempotence, and ambiguous canonical collisions. Retain the
existing missing-count, duplicate-resolution, and real float round-trip cases
as fixed regressions. The canonical key must be compared to the actual factor
artifact and apply join, not only to a helper's string output.

**Tests first**

- Generate typed factor tables and independently derive expected canonical keys
  for supported value classes.
- For every generated valid case, round-trip through serialisation and the
  apply join; for invalid/ambiguous cases assert the named loud failure.

**Acceptance criteria**

- Canonicalisation is idempotent and preserves join identity for every
  supported factor dtype/shape.
- Ambiguity, unsupported values, and missing counts cannot produce a plausible
  but incorrect ratebook artifact.
- A failing generated example is retained as a minimal regression fixture.

## Milestone 4 — Expression-parser fuzzing and Polars differential evidence

Expand beyond curated parser cases. Fuzz malformed pipeline source around the
fallback parser's recovery boundaries, asserting either a structure-preserving
result or its documented typed `ParseError`. Fuzz malformed expression source
separately, asserting an opaque result that preserves the source—never a crash,
fabricated trace value, or silent source loss. Add seeded Polars differential
cases for supported expression subsets, including multi-row window/partition
semantics, null/Kleene behaviour, numeric edge cases, and nested conditional
expressions. Unsupported forms must be classified as unsupported/opaque rather
than compared as though they were supported.

**Tests first**

- Build source and expression generators with a declared supported grammar,
  stable seeds, shrink-to-fixture output, and explicit resource bounds.
- Compare parser/evaluator output with a real Polars frame for each generated
  supported case, including partitioned windows; test malformed source against
  the parser's documented recovery contract.
- Add an allowlist for intentional semantic differences, with a test and
  rationale for every entry; do not hide new differences behind a broad skip.

**Acceptance criteria**

- Generated malformed input cannot crash recovery or create a trustworthy-
  looking value when the parser cannot understand the expression.
- The supported expression grammar has repeatable real-Polars differential
  coverage, including multi-row window semantics.
- Every differential mismatch is either a minimized regression, an explicit
  unsupported classification, or a reviewed intentional-difference entry.

## Milestone 5 — Regression, fixture, and test-health policy

Adopt one policy for defects that escaped because mocks or tidy factories hid a
real boundary. The fix begins with the smallest invariant-level regression,
then adds a production-shaped real-library or workflow test when that was the
missing evidence. Fixtures must retain the relevant schema/config shape without
containing needless production data; factories remain useful only when their
variants cannot erase the contract under test.

Build on the existing test-debt and mutation gates. Publish measurable evidence
for this track: skipped and xfailed tests grouped by owner/reason/expiry,
quarantined flaky tests with reproduction data if quarantine is ever needed,
and mutation outcomes for the declared high-risk boundary set. A skipped test
or surviving mutation is evidence to triage, not silent coverage.

**Tests first**

- Extend `tests/test_test_debt.py` only for owner/expiry data the current
  fingerprint budget and global review deadline cannot express; do not replace
  its scanner or reviewed budget.
- Extend the existing mutation target manifest and sharded runner to the
  selected optimiser/ratebook/parser boundaries, with measured survivor
  thresholds and discriminating witnesses.
- Add fixture-validation tests that prove each retained real-shape fixture
  actually contains the variant required by its contract.
- Test additions to the policy tooling with expired, ownerless, flaky, and
  surviving-mutation examples while retaining the existing scanner/runner
  self-tests.

**Acceptance criteria**

- Every user-found or real-library defect has an invariant-level regression and
  the missing integration layer before closure.
- CI publishes the skip/xfail, flaky, and mutation evidence for this track;
  unexplained growth fails the ratchet or requires an explicit reviewed waiver.
- Fixture and regression review makes the execution mode and real config/data
  shape visible to maintainers.

## Sequencing

1. Establish the inventory and ratchet so work is driven by risk and an oracle,
   not test-count growth.
2. Complete the optimiser matrix and ratebook properties next; they share the
   quote-grid and artifact boundaries that caused the original incident.
3. Add parser fuzzing/differential coverage with the same seed-and-minimise
   discipline.
4. Land the policy and health gates after the first matrices provide meaningful
   baselines; apply them to future fixes and audit existing exceptions.

## Completion and retirement

Retire this roadmap when every inventoried high-risk boundary has an owned,
ratcheted contract; optimiser and ratebook canonical-oracle matrices cover all
supported modes; parser fuzzing and real-Polars differential checks cover the
declared grammar; and CI publishes bounded, reviewed skip/flaky/mutation
evidence. Keep the inventory, generators, fixtures, and evidence as maintained
tests and engineering documentation, not as a historical roadmap.
