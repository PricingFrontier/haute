# Engineering quality roadmap

## Scope

Owns shared invariant/oracle strategy, production-shaped fixtures, regression
policy, test health, CI enforcement, type-contract generation, and
documentation accuracy.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| ROAD-TEST-01 | Active | P0 | Ratchet high-risk boundary contracts with named evidence. |
| ROAD-TEST-02 | Active | P0 | Complete optimiser property and chunk-oracle coverage. |
| ROAD-TEST-03 | Active | P0 | Prove ratebook canonicalisation across dtype boundaries. |
| ROAD-TEST-04 | Active | P1 | Add seeded parser fuzzing and Polars differential evidence. |
| ROAD-TEST-05 | Active | P1 | Establish cumulative fixtures, regressions, and test-health policy. |
| AUD-QUALITY-01 | Reverify | P1 | Close backend/frontend stringly-typed contract vocabularies. |
| AUD-QUALITY-02 | Reverify | P1 | Invert tests that pin known defects before fixing them. |
| AUD-QUALITY-03 | Reverify | P2 | Batch remaining quality debt under enforceable policy. |

## Planned improvements

### ROAD-TEST-01 — Boundary-contract inventory and ratchet

**Why:** Correct-looking frames, responses, and artifacts can fail semantic contracts at boundaries without a named oracle.

**Plan:** Maintain a machine-checkable inventory of high-risk producer/consumer boundaries with invariant, oracle, owner, source/test, supported modes, fixture, and failure behaviour. Seed generated cases and retain minimal replay fixtures.

**Acceptance:** Every inventoried boundary has evidence for each supported mode; missing owner/invariant/oracle/evidence fails a named ratchet; fixes add consumer-facing invariant regressions without duplicating feature harnesses.

**Dependencies:** Feature owners supply behaviour and smallest regressions.

**Evidence:** `tests/test_execution_context.py`; `tests/test_projection_planner.py`; `tests/test_codegen_roundtrip_property.py`; `tests/test_routes_hygiene.py`; `tests/conftest.py`.

### ROAD-TEST-02 — Optimiser properties and chunk oracle

**Why:** Existing examples need generated canonical-oracle coverage across supported identifiers, chunks, solve results, and auto-range shapes.

**Plan:** Generate small deterministic quote/scenario matrices; independently calculate grid, selected index, totals, absolute constraints, and reducer results; pair service properties with real `price_contour` integration tests.

**Acceptance:** Each supported mode has an oracle contract; supported chunk boundaries preserve canonical output while undersized bounds fail typed; generated failures are replayable and confirmed at the library boundary.

**Dependencies:** Execution engine owns planner and chunk implementation.

**Evidence:** `tests/test_chunk_plan.py`; `tests/test_chunk_runner.py`; `tests/test_optimiser_contracts.py`; `tests/test_optimiser_routes.py`; `tests/test_streaming_chunk_size_threading.py`.

### ROAD-TEST-03 — Ratebook canonicalisation properties

**Why:** Save/apply identity must survive scalar/composite values, supported dtypes, null policy, float normalisation, and ambiguity.

**Plan:** Generate typed factor tables and independent expected keys; round-trip valid cases through serialisation and apply joins; retain fixed regressions for missing counts, duplicate resolution, and float round trips.

**Acceptance:** Canonicalisation is idempotent and preserves join identity; ambiguity, unsupported values, and missing counts fail loudly; each generated failure leaves a minimal fixture.

**Dependencies:** Optimiser owns ratebook behaviour.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_contracts.py`; `tests/test_optimiser_service_validation.py`; `tests/fixtures/golden/optimiser_artifact_ratebook.json`.

### ROAD-TEST-04 — Parser fuzzing and Polars differential evidence

**Why:** Curated parser cases do not establish recovery and expression semantics across malformed or multi-row inputs.

**Plan:** Seed malformed pipeline/expression fuzzing that yields structure-preserving output, typed `ParseError`, or opaque source-preserving output; differentially test a declared supported Polars grammar including windows, partitions, null/Kleene, numeric edges, and nested conditionals.

**Acceptance:** Generated input never crashes, fabricates a trace value, or silently loses source; unsupported forms are classified; replayable multi-row differential failures identify the semantic gap.

**Dependencies:** Parser and trace components own implementation fixes.

**Evidence:** `src/haute/execution.py`; `src/haute/trace.py`; `tests/test_codegen_roundtrip_property.py`; `tests/test_trace_w4_fixes.py`; `tests/test_trace_waterfall.py`.

### ROAD-TEST-05 — Regression, fixture, and test-health policy

**Why:** Coverage becomes less useful when fixture provenance, debt expiry, flaky/skip evidence, and mutation outcomes lack owners.

**Plan:** Define cumulative regression and fixture provenance rules, use frozen production-shaped fixtures beside minimal hand-written shapes, and publish actionable owner/expiry summaries for skips, flakes, xfails, and mutation survivors.

**Acceptance:** A user-found defect receives the smallest useful regression and fixture/matrix review; high-risk boundaries have owned health evidence; no competing harness or duplicate policy emerges.

**Dependencies:** CI configuration and feature owners.

**Evidence:** `pyproject.toml`; `tests/conftest.py`; `tests/fixtures`; `scripts/preflight.ps1`; `.github/workflows`.

### AUD-QUALITY-01 — Closed contract vocabularies

**Why:** Reverify whether duplicated string literals still allow typo-routed backend/frontend contract branches.

**Plan:** Inventory public discriminators and generate or encode a single typed closed vocabulary at cross-stack boundaries; test serialization and unsupported values.

**Acceptance:** Reverified public vocabularies have one authoritative representation; invalid values fail at the boundary; generated artifacts stay checked for drift.

**Dependencies:** Owning feature APIs and frontend types.

**Evidence:** `src/haute/schemas.py`; `src/haute/errors.py`; `frontend/src/types`; `frontend/src/api/client.ts`; `frontend/src/api/__tests__/client.contract.test.ts`.

### AUD-QUALITY-02 — Bug-pinning tests

**Why:** A test that asserts defective behaviour blocks the corrective regression it should enable.

**Plan:** Reverify candidate assertions against current behaviour; delete obsolete tests or invert each confirmed bug-pinning test before the implementation fix, retaining the intended user contract.

**Acceptance:** No confirmed test encodes a known defect as success; replacement regressions fail on the original defect and name the correct contract.

**Dependencies:** Respective feature owner resolves intended behaviour.

**Evidence:** `tests/test_bugfixes.py`; `tests/test_bug_regressions.py`; `frontend/src/__tests__/adversarial/resilience.test.ts`; `frontend/src/__tests__/App.integration.test.tsx`.

### AUD-QUALITY-03 — Quality-debt policy

**Why:** Remaining CI, documentation-truth, dependency-monitoring, and static-analysis items need prioritised policy rather than disconnected edits.

**Plan:** Reverify live configuration, group related debt by owning gate, and add only measurable policy changes with an owner, expiry/review point, and enforcement path.

Include lockfile parity in that re-verification: every supported package-manager
lock must either be regenerated and checked by CI or deliberately removed from
the supported contributor workflow.

**Acceptance:** Each retained item is either executable policy or explicitly accepted risk; no stale rule or undocumented exception remains as an informal backlog. Frontend lockfiles agree on declared dependencies or the unsupported lockfile is removed with its references.

**Dependencies:** Security, CI, build/distribution, and documentation owners.

**Evidence:** `pyproject.toml`; `frontend/package.json`; `frontend/package-lock.json`; `frontend/bun.lock`; `.github/workflows`; `scripts/preflight.ps1`; `uv.lock`.
