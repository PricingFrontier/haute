# Engineering quality roadmap

## Scope

Owns shared invariant/oracle strategy, production-shaped fixtures, regression
policy, test health, CI enforcement, type-contract generation, and
documentation accuracy.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| — | — | — | No active engineering-quality roadmap package remains. |

## Planned improvements

There are no active engineering-quality roadmap packages.

## Delivered outcomes

- `ROAD-TEST-05` defines cumulative regression and fixture-provenance rules
  and generates one deterministic actionable summary. The ratchet rejects
  unreasoned skips/xfails/flakes, an excessive Playwright retry budget,
  survivor-budget drift, and summary drift. `tests/test_test_debt.py`,
  `tests/test-health-summary.md`, `mutation/targets.json`, and
  `scripts/run_mutation_suite.py` are the maintained contract. The
  owner/review-date calendar gate originally delivered with this package was
  removed on 2026-07-27: the one-off review it deferred was performed instead
  (stale guards deleted, the `list_pipelines` path-leak xfail fixed), and
  ongoing review is event-driven through the fingerprint ratchet.
- The stale JSON-cache approved-change note is now the present-tense canonical
  contract, and assistant tool messages no longer infer a missing `is_error`
  field from historical content shape. Together with the component-owned
  canonical formats and repository residual scans, this closes the
  `ROAD-CANON-01` residual tranche without retaining a central compatibility
  backlog.
- npm is the sole supported frontend package manager; the stale Bun lockfile and
  its documentation-coverage reference were removed so CI's
  `frontend/package-lock.json` is the only dependency lock contract
  (`AUD-QUALITY-03`). The remaining concerns from that deliberately broad audit
  are already owned by executable documentation-accuracy, dependency-audit,
  static-analysis, preflight, and test-health gates; no unowned umbrella
  quality-debt package remains.
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
