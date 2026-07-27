# WS-01 — Corpus governance & quality gates

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-01 · Status: delivered in PR #137.

**Branch:** `opus5/ws-01-corpus-governance`

## Mission

Make the spec corpus and CI gates *unable to lie again* (review Wave 0). Extend the
docs-accuracy guard from module-map-first-cells to the whole corpus, add the
contract-retirement rule, clean the roadmap layer, decide version-stamp semantics, and fix
the engineering-quality gates that currently skip or downgrade failures. This is the
highest-leverage stream: every other stream's spec work rots without it.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| cross-cutting (docs infra subset) | 0 | 2 | 5 | 1 |
| engineering-quality | 0 | 0 | 10 | 6 |
| **Total** | **0** | **2** | **15** | **7** |

## Priorities

**P1 — the guard and its ratchet** (review "Recommended sequence" Wave 0, items 1–2):

1. Extend `tests/test_docs_accuracy.py`: validate every inline-code repo path in the whole
   document (not just Module-map first cells), resolve `path::symbol` references, check link
   `#anchor`s (currently stripped at `:728`), make the required-section check heading-based
   rather than substring (`:716`), validate Testing-section file references
   (`testing-credibility-8`, `readme-coherence-7`), and validate roadmap `Evidence:` paths
   (`readme-coherence-5`).
2. Add the retirement rule (`readme-coherence-1` fix): an `## Approved change contract` may
   not name a repository path that already exists with a verb like "Add"/"moves to", and may
   not survive once its named symbols/routes/files reached the target state (heuristic +
   explicit allowlist where undecidable).
3. **Ratchet baseline:** ship the extended guard with a committed per-file baseline of all
   current violations so CI stays green. Every other stream deletes its entries as it
   reconciles. Removing an entry must be one-line trivial; adding one must require review.
4. Un-hard-code the guard's own drift traps: roadmap package set baked into the test
   (`readme-coherence-8`), node-type table check that isn't table-scoped
   (`readme-coherence-12`).

**P1 — CI gates that currently pass silently:**

- `engineering-quality-2`: mutation gate job is *skipped*, not failed, when the plan phase
  fails (`mutation.yml:133`) — make plan failure fail the gate.
- `engineering-quality-9`: four bug-class ESLint rules downgraded to warnings and
  `npm run lint` never fails on warnings — restore to errors or add `--max-warnings 0`.

**P2 — corpus rules and roadmap truth:**

- `readme-coherence-4`: delete delivered packages from `docs/roadmap/` (GIT-G01–15,
  IO-IO01–12, AUD-C06/C12, AUD-RATING-01), replace with the execution-engine.md "no active
  packages" pattern, fix the index "Start with" column. (`readme-coherence-3`: the six spec
  pointer sentences are edited by their owning streams; track completion here.)
- `readme-coherence-9`: define or retire the `## Polars backend contracts (0.6.0)` heading
  class in `TEMPLATE.md`/`README.md` with an explicit removal rule (31 occurrences).
- `readme-coherence-2`: fix `docs/specs/README.md`'s own stale contract section and anchor.
- `readme-coherence-6`: make ownership enforceable — ledger derivation from module-map cells
  only lets prose claim ownership it doesn't have; add the missing `[[shared_file]]`
  discipline (streams append their entries; this stream owns the file's structure).
- `testing-credibility-9`: root-relative test paths rule for frontend Testing sections.
- Version stamps (systemic S6, Wave 0 item 5): decide what versions mean or remove them;
  align `pyproject.toml` (0.4.0) and `src/haute/server.py:383` (0.1.0 — coordinate this one
  line with WS-04); document the decision in `TEMPLATE.md`.

**P3 — engineering-quality spec truth:** fold shipped contracts (`engineering-quality-3,
-4`, `contracts-d-6, -7, -12`), fix roadmap pointers (`engineering-quality-5`), document the
frontend coverage gate (`engineering-quality-10`), Linux-baseline claim
(`engineering-quality-12`), mutation-docs/dead-param cleanup (`engineering-quality-6`),
npm advisory identity brittleness (`engineering-quality-11`).

## Finding inventory

High: `readme-coherence-1`, `readme-coherence-4`.
Medium: `readme-coherence-2`, `readme-coherence-6`, `readme-coherence-9`,
`testing-credibility-8`, `testing-credibility-9`, `contracts-d-6`, `contracts-d-7`,
`engineering-quality-10`, `engineering-quality-2`, `engineering-quality-3`,
`engineering-quality-4`, `engineering-quality-5`, `engineering-quality-9`,
`readme-coherence-5`, `readme-coherence-7`.
Low: `readme-coherence-3`, `contracts-d-12`, `engineering-quality-11`,
`engineering-quality-12`, `engineering-quality-6`, `readme-coherence-12`,
`readme-coherence-8`.

## File ownership (exclusive)

- `tests/test_docs_accuracy.py` (+ new baseline file)
- `docs/specs/README.md`, `docs/specs/TEMPLATE.md`, `docs/specs/ownership.toml` (structure;
  other streams append entries), `docs/roadmap/**`
- `docs/specs/engineering-quality/**`
- `.github/workflows/mutation.yml`, `scripts/run_mutation_suite.py`,
  `scripts/check_dependency_audit.py`, `frontend/eslint.config.js`
- `pyproject.toml` (version field), `src/haute/server.py` version line only (WS-04 owns the
  rest of the file)

## Cross-stream touchpoints

- The ratchet baseline is the contract with all 13 other streams — publish its format in
  the PR description and keep entry removal trivial.
- `readme-coherence-3` pointer edits and `testing-credibility-9` path fixes execute inside
  each owning stream's spec pass; this stream defines the rule and verifies at the end.
- Fixing ESLint severities may surface existing warnings in files owned by WS-09/WS-10 —
  baseline them or coordinate rather than editing those files here.

## Definition of done

- Extended guard merged with baseline; guard catches all seven blind spots in the review's
  S3 table on a seeded fixture.
- Mutation gate fails (not skips) on plan failure; lint gate fails on the four rule classes.
- Roadmap layer contains no delivered packages; "Start with" cells point at open work or `—`.
- Version-stamp decision recorded in `TEMPLATE.md`; stamps aligned.
- All 24 findings fixed or explicitly deferred with reasons; engineering-quality baseline
  entries deleted.

## Verification

- `uv run pytest tests/test_docs_accuracy.py -q`
- `uv run pytest tests/test_mutation_sharding.py -q` (guard-adjacent)
- `npm --prefix frontend run lint`
- Quick preflight before hand-off (`AGENTS.md` ladder).
