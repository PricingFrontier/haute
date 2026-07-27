# Engineering quality roadmap

## Scope

Owns shared invariant/oracle strategy, production-shaped fixtures, regression
policy, test health, CI enforcement, type-contract generation, and
documentation accuracy.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| ROAD-CANON-01 | Active | P0 | Remove every obsolete Haute format and compatibility shim before release. |
| ROAD-TEST-05 | Active | P1 | Establish cumulative fixtures, regressions, and test-health policy. |
| AUD-QUALITY-03 | Reverify | P2 | Batch remaining quality debt under enforceable policy. |

## Planned improvements

### ROAD-CANON-01 — Prerelease canonical-only contract

**Why:** Haute has no released or external user base. Retaining migrations, deprecated aliases,
old field fallbacks, historical generated-code readers, and cleanup paths for obsolete Haute
artifacts creates multiple behavioural contracts without protecting a real user. It also hides
invalid current data behind silent conversion.

**Plan:** Inventory executable backend and frontend compatibility paths, then remove them in four
dependency-ordered groups: persisted configuration and editor formats; pipeline, sidecar, and
generated-code shapes; deploy and wire contracts; internal wrappers, aliases, warnings, and
old-artifact cleanup. Each owning component defines its one canonical representation before code
changes. Code does not recognise, read, migrate, strip, rewrite, warn about, scan, or delete a
path solely because an earlier Haute implementation produced it. Unsupported data receives only
the ordinary validation applied to the current schema; there are no legacy-specific diagnostics.

This policy does not remove compatibility with currently supported Python versions, operating
systems, browsers, third-party services, dependency versions, or explicitly current public API
aliases. Schema-version fields and typed adapters remain when they describe the current contract
rather than accepting an obsolete Haute representation.

**Acceptance:** Production source contains no executable obsolete-format migration, deprecated
Haute alias, old-key/path fallback, temporary legacy response field, or historical-artifact
housekeeping branch. Canonical inputs retain their current semantics. Migration-specific tests and
fixtures are deleted; canonical tests and maintained call sites demonstrate the surviving
contract. Backend and frontend residual scans distinguish forbidden Haute compatibility from the
allowed platform/dependency meanings above. Relevant targeted suites, static checks, and the
broad preflight are green.

**Dependencies:** Pipeline config, JSON shredding, rating, optimiser, submodels, codegen, deploy,
modelling, caching, execution, tracing, frontend node editors, graph canvas, git UI, and shared
frontend contracts own their canonical formats and errors.

**Evidence:** `src/haute/`; `frontend/src/`; `tests/`; `frontend/src/**/__tests__/`;
`specs/README.md`; owning component specifications.

### ROAD-TEST-05 — Regression, fixture, and test-health policy

**Why:** Coverage becomes less useful when fixture provenance, debt expiry, flaky/skip evidence, and mutation outcomes lack owners.

**Plan:** Define cumulative regression and fixture provenance rules, use frozen production-shaped fixtures beside minimal hand-written shapes, and publish actionable owner/expiry summaries for skips, flakes, xfails, and mutation survivors.

**Acceptance:** A user-found defect receives the smallest useful regression and fixture/matrix review; high-risk boundaries have owned health evidence; no competing harness or duplicate policy emerges.

**Dependencies:** CI configuration and feature owners.

**Evidence:** `pyproject.toml`; `tests/conftest.py`; `tests/fixtures`; `scripts/preflight.ps1`; `.github/workflows`.

### AUD-QUALITY-03 — Quality-debt policy

**Why:** Remaining CI, documentation-truth, dependency-monitoring, and static-analysis items need prioritised policy rather than disconnected edits.

**Plan:** Reverify live configuration, group related debt by owning gate, and add only measurable policy changes with an owner, expiry/review point, and enforcement path.

Include lockfile parity in that re-verification: every supported package-manager
lock must either be regenerated and checked by CI or deliberately removed from
the supported contributor workflow.

**Acceptance:** Each retained item is either executable policy or explicitly accepted risk; no stale rule or undocumented exception remains as an informal backlog. Frontend lockfiles agree on declared dependencies or the unsupported lockfile is removed with its references.

**Dependencies:** Security, CI, build/distribution, and documentation owners.

**Evidence:** `pyproject.toml`; `frontend/package.json`; `frontend/package-lock.json`; `frontend/bun.lock`; `.github/workflows`; `scripts/preflight.ps1`; `uv.lock`.

## Delivered outcomes

- Optimiser property and chunk-oracle coverage, ratebook canonicalisation
  properties, and seeded parser fuzzing with Polars differential evidence
  (`ROAD-TEST-02`–`ROAD-TEST-04`) are delivered through ordinary suites,
  including `tests/test_chunk_plan.py`, `tests/test_optimiser_contracts.py`,
  `tests/test_parser_roundtrip.py`, and
  `tests/test_codegen_roundtrip_property.py`.
- Closed backend/frontend contract vocabularies and the bug-pinning-test sweep
  (`AUD-QUALITY-01`, `AUD-QUALITY-02`) are delivered via the typed error
  hierarchy in `src/haute/errors.py`, closed vocabularies with runtime guards
  in `frontend/src/types/guards.ts`, and the corrective-regression framing of
  `tests/test_bug_regressions.py`.
- `ROAD-TEST-01` was retired as superseded: its outcome is achieved by the
  decentralised per-boundary suites plus the documentation-accuracy ratchet
  rather than by a single machine-checkable inventory artifact.
