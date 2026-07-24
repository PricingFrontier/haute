# Haute — path to the highest engineering standard

> **Historical quality target.** Current candidate ownership and order live in
> the [component improvement catalogue](../roadmap/index.md). Treat unchecked
> items here as evidence to re-verify, not a second backlog.

**Premise.** The predecessor codebase-review remediation is complete. Closing that list reached
*"no known dangerous defects."* That is the floor, not the ceiling. This document is the gap
between the two: the **mechanisms, invariants, and process** that stop the same defect classes from
ever regrowing, plus the architecture/performance/observability work a defect review structurally
under-covers.

It does **not** re-list the retired findings. Where a predecessor finding was the *instance* of a
class, this document specifies the *guard* that makes the class impossible to reintroduce silently.

### The three levels

| Level | Meaning | Gets you there |
|---|---|---|
| L1 — Correct today | No known critical/high defects | Completed predecessor-review remediation |
| L2 — Self-defending | The six root-cause classes are structurally prevented from regrowing | §A–§B + §I |
| L3 — Highest standard | L2 + architecture, measured performance, test strength, observability, deliberate design, hygiene gates | this whole document |

A pricing engine's defining risk is *a wrong number presented as correct, with no signal.* The
ceiling is reached when that outcome is **structurally impossible for known input classes** and
**loudly caught for unknown ones** — proven by executable invariants in CI, not by vigilance.

---

## A. Install class-level invariants (the heart of the work)

Each of the six root causes from the predecessor review needs a structural guard. Fixing instances
without these means the class regrows the next time someone adds a node type, a config key, or an
input.

### A1. Codegen round-trip is a proven property, not a hope
The product's core promise is "the visual graph and the `.py` file are the same artifact." That must
be an enforced invariant, not a collection of example tests.

- **Property test (hypothesis / fast-check):** generate arbitrary valid graphs — every node type,
  nested submodels, and *adversarial strings* in names/configs/descriptions/user-code (quotes,
  triple-quotes, backslashes, braces, newlines, unicode, leading dashes) — and assert:
  - `parse(codegen(g)) ≈ g` (semantic equality of nodes/edges/config), and
  - `codegen(parse(codegen(g))) == codegen(g)` **byte-identical** (idempotency).
- **Corpus test:** every example/scaffold pipeline and a stored corpus of real user pipelines must
  round-trip byte-identically on every CI run.
- **Acceptance:** a single `tests/test_codegen_roundtrip_property.py` that would have failed on C1, C5,
  the brace-doubling, the docstring injection, and the paren-scanner bugs simultaneously.

### A2. One source of truth for cache-key completeness
Stale-wrong data is the worst failure mode and CODE_REVIEW found it five times. The fix is not five
patches — it is making "what feeds a node's output" enumerable and asserting the fingerprint covers it.

- Define a single `node_inputs_signature(node, graph, runtime)` that is the *only* sanctioned way to
  build a cache key, returning a structured record of every dimension: config (all keys), upstream
  node fingerprints, **edge handles**, user-code text, and **content/mtime of every file-backed input**
  (dataSource, externalFile, model artifact, JSON cache).
- **Coverage test:** reflectively enumerate every field of every node config model and every runtime
  input class; assert each appears in the signature. Adding a new config key or input type without
  wiring it into the fingerprint fails CI. This is the guard that makes C2/C3/C4 unrepeatable.
- **Mutation probe:** a test that mutates each input dimension and asserts the fingerprint changes
  (and that nothing else does).

### A3. Eager / lazy / chunked execution can never silently diverge
There are (at least) three execution paths — preview-eager, batch-lazy, chunked. Schema or value
divergence between them is silent wrongness (model-scorer double-execute, chunk-local whitelist,
preview-vs-batch dtype inference all live here).

- **Conformance test:** a node-type × shape matrix run through all three paths asserting identical
  output schema *and* values (within float tol). Empty frame, single row, nulls, the dtypes that bite.
- **Chunk-safety proof:** the chunk-local AST whitelist must be backed by a property test that, for
  every whitelisted construct, asserts chunked == full result on randomized frames. A construct without
  such a proof is not whitelisted.
- **Acceptance:** "preview schema == batch schema" holds for every node type as an executable invariant.

### A4. Backend/frontend contract is generated, not hand-maintained
`schemas.py` and `types.ts`/`guards.ts` drift because they are two hand-written copies of one contract.

- Generate the TypeScript types and runtime guards from the pydantic models (FastAPI already exposes
  OpenAPI; use `openapi-typescript` or pydantic `model_json_schema()` → codegen).
- **Drift gate:** CI regenerates and `git diff --exit-code`. Any schema change that isn't reflected in
  the committed generated types fails the build. Replaces the brittle regex-pinning meta-test.
- Every incoming payload validated at the boundary by the generated guard — no blind `as X` casts,
  including the graph nodes/edges payload (currently the most load-bearing blind cast).

### A5. The fail-loud charter is enforced, with a deliberate-exception register
"No silent fallbacks" is a rule the codebase already violates in ~15 places; a rule without a checker
is a suggestion.

- **Lint/AST gate:** flag `except Exception: pass`, bare `except:`, `.catch(() => {})`, `|| 0`/`|| ''`
  on parsed numbers, and except-blocks that neither re-raise nor log. Add `ruff` `BLE`/`TRY` rules and
  an ESLint `no-empty`/custom rule.
- **`docs/adr/deliberate-fallbacks.md`:** the *only* sanctioned exceptions, each with rationale and the
  loud signal it still emits (log + counter). Anything not on the register and flagged by the gate fails
  CI. This converts "vigilance" into "allowlist."

### A6. The numeric JSON boundary is property-tested across all dtypes
Int64>2^53 and NaN/inf were found by four reviewers because there is no codec round-trip property test.

- **Property test:** for every Polars dtype (Int64/UInt64 past 2^53, Float NaN/±inf, Decimal, Date,
  Datetime with tz + µs, Time, Duration, Categorical/Enum, List/Struct nesting, Binary, Null-typed,
  empty, zero-column), assert backend-encode → frontend-decode → re-encode is lossless and that lossy
  conversions are *tagged*, never silent.
- One canonical codec module on each side; v1/v2 negotiation and corrupt-payload behaviour covered.

### A7. The sync/watcher protocol has identity and versioning
The live loop (UI ↔ save ↔ file ↔ watcher ↔ websocket) needs a protocol, not timing heuristics.

- Add a **monotonic revision token per file**, returned by save and carried on every `graph_update`;
  the client ignores frames whose `source_file` ≠ loaded file and whose revision ≤ its own; echoes are
  suppressed by content hash; reconnect triggers a `/api/pipeline` resync; an external change while
  dirty raises an explicit conflict choice (never silent file-wins).
- **Integration harness:** a deterministic test that simulates concurrent save + external edit +
  reconnect + foreign-file change and asserts no lost update and no clobber. This is the only way to
  keep C10's class fixed.

---

## B. Pricing-engine correctness invariants (golden + property)

Beyond the round-trip/cache guards, the *numbers* need oracles. Smoke tests that assert `isfinite` are
not correctness tests.

- **Displayed price == computed price.** An end-to-end test (backend value → serialized → frontend
  `formatValue` → rendered string) asserting the rendered price equals the computed price to a defined
  precision, for representative magnitudes. Same for trace factors: displayed factors must multiply to
  the displayed total (the 2dp-truncation and waterfall-arithmetic classes).
- **Trace reconciliation.** Every waterfall must arithmetically reconcile to the node's actual output
  value; assert it, with the additive/multiplicative classification proven against the expression.
- **Known-answer (golden) tests** with hand-computable expectations for: rating lookups (hit, miss,
  default, combined-output, dtype-coerced keys); banding boundary inclusivity (left/right closed, NaN
  routing, breakpoint ordering); gini/AUC/deviance against `sklearn`/`scipy` oracles incl. ties and
  weights; GLM coefficient/relativity recovery on a designed matrix; **the optimiser against a binding
  constraint with a hand-computed optimum, asserting λ>0 and constraint satisfaction — using the real
  `price-contour`, not a mock.**
- **Property tests** for join semantics (edge-join: dtype-mismatch, non-unique keys → row-multiplication
  detected, null keys, suffix collisions, every `how`), projection/needed-columns (pruned columns never
  change results), and RAM-estimate accuracy (string widths, join fan-out).

---

## C. Test-suite strength (not coverage %)

Coverage is already gated; *strength* is the gap. CODE_REVIEW found mock-drift, tautologies, and
implementation-pinned assertions.

- **De-mock the sources of truth.** Where a dependency *is* the contract (`price-contour`, MLflow model
  loading, Polars join kwargs), test against the real thing. The optimiser's mocks have already drifted
  far enough to hide two broken apply flows.
- **Behavioural, not structural.** Replace AST-shape tests (e.g. the submodel save-lock test) and
  source-regex tests with behavioural ones (spy on real lock state; assert rendered props). Delete
  tautologies and the "superseded" dead skips.
- **Property-based testing** as a first-class tool (hypothesis backend, fast-check frontend) for codecs,
  codegen, projection, chunking, clipboard parsing.
- **Mutation testing** expanded to the critical numeric modules (`_rating`, `_metrics`, `projection`,
  `chunking`, `_json_shred`, optimiser apply) with per-module survival thresholds — coverage says a line
  ran; mutation says a bug in it would be caught.
- **Cross-platform CI matrix.** Windows is the development platform yet runs a 3-file smoke; the full
  suite (incl. `test_file_ops` win32 atomicity contracts) must run on Windows and Linux.
- **Negative/edge promises kept.** Every test file whose name promises edge cases (`adversarial`,
  `fail_loudly`, `corrupt`) must actually assert the loud failure, not a 200.

---

## D. Architecture & decomposition

Maintainability is part of the standard; large tangled modules are where the next criticals hide.

- **Split `routes/_optimiser_service.py` (4,678 lines)** along the seams already identified: artifact
  lifecycle, frontier/auto-range, ratebook factor-table serialisation, and a single terminal-transition
  helper (the exception ladder is triplicated and has already drifted).
- **Unify duplicated logic:** the two canonical-JSON encoders (`_cache.py` vs
  `_dataframe_execution_cache.py`), the three byte formatters, and the `timeAgo`/`formatRelativeTime`
  pair. The modelling SVG surface/legend duplication now goes through `ChartScaffold`; only add deeper
  chart helpers when repeated geometry or scale code warrants it.
- **Delete dead code** rather than carry it: `_build_input_kwargs` machinery with no runtime caller,
  `save_node_config`, the dead dual-cache markers once C2 is properly wired, the unreachable
  `GroupedColumnsTab` branch.
- **Budgets enforced in CI:** module line-count ceiling (e.g. 800 LOC warn / 1200 hard) and cyclomatic
  complexity caps, so no module silently grows back to 4.7k. New code over budget must split.
- **Name the execution-path duality explicitly** (eager/lazy/chunked share a documented core) so a
  contributor can't add a node that implements one path and forgets the others — §A3's conformance test
  is the backstop.

---

## E. Performance engineering — make "as performant as possible" measurable

Today performance is asserted in prose and gated weekly. The standard is a measured baseline with
per-PR regression budgets.

- **Baseline + budgets** for the operations that define UX, each with a number that fails the PR if
  breached: preview latency (cold/warm), large-graph render & drag (200–500 nodes), optimiser solve on
  1e5–1e6 quotes, trace payload size on wide frames, JSON-cache build memory. Move the existing frontend
  frame-budget benchmarks from weekly cron to per-PR gates.
- **Profile the known hotspots** CODE_REVIEW flagged and confirm the fixes with numbers: row-hash
  fingerprint string-building, per-request model reload, `/estimate` full re-execution, full-frame
  re-hash per cache key, re-render storms on hover.
- **Memory is bounded everywhere it can grow:** byte-caps (not count-caps) on the trace cache and model
  cache; an accurate RAM estimate (string widths, join fan-out); admission control on the JSON-cache
  build path.
- **Event-loop hygiene gate:** an async-lint (`ruff` `ASYNC`, or a targeted test) flagging blocking
  CPU/file/Polars work in `async def` route handlers — the save/infer/submodel handlers froze the UI.

---

## F. Observability, logging & error discipline

- **Structured logging with PII scrubbing.** Insurance cell values currently reach logs via Polars error
  text and the files route. Establish a logging contract: error *class* + column at WARNING, full detail
  (scrubbed) at DEBUG, never raw data values in aggregated logs.
- **Consistent error taxonomy.** Audit that the typed error hierarchy (`HauteError` and friends) is used
  uniformly and that HTTP status mapping is consistent; no generic 500 where a 4xx with a named cause is
  possible.
- **Timing/diagnostics** already exist for execution — formalise them into a coherent surface and ensure
  the deployed scorer returns sanitized errors (no internal paths/stack to a network-facing endpoint).

---

## G. Security & supply chain

Beyond the one local-server finding, reaching the standard means a documented posture.

- **Local server:** `TrustedHostMiddleware` localhost allowlist + per-session token on every `/api/*` and
  the websocket; WS `Origin` validation; sink/output paths confined via `validate_safe_path`. Document
  the single-user-local trust model explicitly.
- **Deserialization:** tighten the `RestrictedUnpickler` allowlist (dot-boundary anchoring, specific
  `(module, qualname)` pairs) and document the model-file trust boundary.
- **Deployed scorer (network-facing):** request-size limits pre-parse, strict input-schema validation,
  generic error bodies, pinned base *and* package versions in the generated Dockerfile, secret-shaped key
  scanning of bundles/manifests before they enter an image layer.
- **Supply chain:** a dependency-pinning policy (application pins exact, library floors+caps), lockfile
  enforcement in CI, and a documented threat model in `docs/adr/`.

---

## H. Type safety & static analysis

The package ships `py.typed`; the standard is to make that claim true and enforced.

- **`mypy --strict` for `src/haute`** (currently non-strict, tests excluded) — phase in per-module.
- **Expand ruff** to `B` (bugbear: mutable defaults, loop-var capture), `PT` (pytest anti-patterns —
  directly relevant to test quality), `S` (bandit, for a product that execs user code), `C4`, `RUF`,
  `ASYNC`, `BLE`, `TRY`.
- **Frontend:** `tsc --strict`, ESLint with `react-hooks/exhaustive-deps` as an error (stale-closure
  bugs were a recurring finding), and the no-silent-catch rule from §A5.
- All of the above are **CI gates**, not advisory.

---

## I. CI as the single enforcer

Everything above is worthless if it isn't gated. The standard is that *the only way* a regression of a
fixed class lands is if someone disables a gate in a reviewed PR. CI must enforce, on every PR:

1. Codegen idempotency + corpus round-trip (§A1)
2. Cache-fingerprint coverage + mutation probe (§A2)
3. Eager/lazy/chunked conformance (§A3)
4. Contract regeneration drift check (§A4)
5. Fail-loud lint + deliberate-fallback register (§A5)
6. Codec dtype round-trip property test (§A6)
7. Sync-protocol integration harness (§A7)
8. Pricing golden/property suites incl. real-solver optimiser (§B)
9. Mutation thresholds on critical modules (§C)
10. Windows + Linux full suite (§C)
11. Performance budgets, per-PR (§E)
12. Module-size/complexity budgets (§D)
13. `mypy --strict`, expanded ruff, `tsc --strict` (§H)

No `continue-on-error`, no `|| true`, no benchmark-only-on-cron. (The repo is already disciplined here —
extend the same discipline to the new gates.)

---

## J. Documentation, ADRs & public API surface

- **ADRs** for every load-bearing design decision so they're deliberate and discoverable: the
  eager/lazy/chunked split, the sync protocol, the cache-key contract, the rating-miss policy (§K), the
  Int64 boundary representation, the model-file trust boundary.
- **Stop leaking internals into user files:** generated pipelines call `pipeline._apply_edge_join`
  (private). Expose a public alias and emit that — generated code is de-facto public API.
- **Keep planning docs out of the published site:** the 1,300-line `EDGE_JOIN_*.md` notes sit in the
  mkdocs `docs/` tree and publish as orphan pages. Move to an internal notes location.
- **A "what feeds a price" data-lineage doc** for regulators/auditors, matching the trace UI's claims.

---

## K. Product/design decisions to make deliberately

Some CODE_REVIEW items are *judgment calls*, not bugs. The standard is that each is decided on purpose,
encoded, and tested — not left to whatever the code happens to do. Each needs an explicit ruling:

- **Rating lookup miss** → error, null, or neutral-fill? (Recommend: configurable per combined-output,
  default fail-loud with a miss counter; neutral-fill opt-in.)
- **Training downsampling** → representative sample, `head(N)`, or refuse? (Recommend: seeded reservoir
  sample; never silent `head` labelled as "sample".)
- **Edge-join `validate` default** for left/lookup joins → warn on duplicate right keys? (Recommend: yes.)
- **Int64 > 2^53 at the boundary** → string-tagged or a big-int codec? (Recommend: string with a dtype
  tag; previews are display-only.)
- **Multi-port apiInput** → finish to spec, or gate behind a flag until complete? (It is mid-rollout with
  partial metadata and port-identity issues.)
- **NaN/inf in previews** → sentinel representation and distinct rendering.

Document each as an ADR; add the test that pins the chosen behaviour.

---

## L. A second, deeper review pass

Pass one surfaced ~10 criticals in a few minutes per area — density high enough to imply more exist.
The standard requires:

- **Adversarial re-check of every MEDIUM/LOW** finding (per CLAUDE.md's "findings get adversarially
  re-checked") — confirm, refine, or discard each, with a failing test for the confirmed ones.
- **Cross-repo audit of `price-contour`** (the Rust optimiser core), which was unauditable from this repo:
  dual-update step size, argmax tie-breaking, whether `converged=True` strictly implies constraint
  satisfaction, CD order dependence.
- **Deploy-target exhaustiveness:** the container impact path was broken behind mocks; validate each
  real deploy target (Databricks/Docker/cloud) end-to-end against a real scorer.
- **A focused fuzz/property campaign** on the parser (hand-written Python that defeats the regex
  fallback) and the clipboard/paste paths.

---

## Phased roadmap

| Phase | Theme | Contents | Exit signal |
|---|---|---|---|
| 0 | Unblock | Predecessor-review merge blockers | Branch mergeable |
| 1 | Stop the bleeding | All CRITICAL/HIGH fixes, each via failing-test-first + dev/reviewer pair | L1 reached |
| 2 | Self-defending | §A invariants + §I gates 1–7 wired into CI | A fixed class cannot regrow silently (L2) |
| 3 | Prove the numbers | §B golden/property suites; de-mock optimiser & scoring (§C) | Pricing correctness is executable |
| 4 | Structure & speed | §D decomposition + budgets; §E baseline + per-PR perf gates | No 4.7k modules; perf is measured |
| 5 | Hygiene & posture | §F observability, §G security, §H types — all gated | Static/security standard enforced |
| 6 | Decide & document | §K decisions as ADRs; §J docs & API surface | No undocumented load-bearing call |
| 7 | Re-verify | §L second pass + adversarial MEDIUM/LOW re-check | Confidence that pass-two is quiet |

Phases 2–5 can run partly in parallel once Phase 1 lands. The retired predecessor-review triage
fed Phase 1.

---

## Definition of done — how you know you're at the ceiling

You are at "highest standard we can manage" when **all of these are green in CI and would fail on a
regression**, not when someone believes the code is clean:

- [ ] Codegen `parse∘codegen` semantic-equal and `codegen∘parse∘codegen` byte-identical across generated + corpus graphs, incl. adversarial strings.
- [ ] Cache-fingerprint coverage test passes; adding a config key/input without fingerprinting it fails CI.
- [ ] Eager == lazy == chunked conformance across the node-type matrix; "preview schema == batch schema" holds.
- [ ] `types.ts`/guards generated from `schemas.py`; drift check is green; no blind boundary casts.
- [ ] Fail-loud lint green; every deliberate fallback is on the register and still emits a loud signal.
- [ ] Codec round-trips losslessly (or tags loss) for every Polars dtype incl. Int64>2^53, NaN/inf.
- [ ] Sync-protocol harness proves no lost update / no clobber under concurrent + external + reconnect edits.
- [ ] "Displayed price == computed price" and "waterfall reconciles to output" are executable invariants.
- [ ] Optimiser/scoring tested against the **real** dependencies; golden tests assert constraint satisfaction and known optima.
- [ ] Mutation thresholds met on the critical numeric modules.
- [ ] Full suite green on Windows **and** Linux.
- [ ] Per-PR performance budgets enforced; no module over the size/complexity budget.
- [ ] `mypy --strict` (src), expanded ruff, `tsc --strict`, exhaustive-deps — all gating.
- [ ] Every load-bearing design decision has an ADR; generated user code calls only public API.
- [ ] Second review pass + adversarial MEDIUM/LOW re-check complete.

When that checklist is green, "highest standard" stops being a judgement and becomes a property the
build *enforces* — which is the only durable form of it.
