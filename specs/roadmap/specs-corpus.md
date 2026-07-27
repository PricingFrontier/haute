# Specification corpus roadmap

## Scope

Owns the accuracy, structure, and maintainability of the specification corpus
itself: stale or self-contradicting passages, duplicated change-contract
material, Testing-reference quality, ownership-annotation consistency,
trust-boundary and build-contract agreement, register/depth balance, and
documentation governance. Product behaviour stays owned by each component
specification; where a package below reveals a code defect rather than a
documentation defect, its specification is updated before code and the
owning component remains responsible for the behavioural change. The packages
consolidate a point-in-time specification review and its independent
2026-07-27 follow-up. They do not treat that review's partial-read coverage or
line counts as proof of exhaustiveness; `SPEC-13` makes future corpus-wide
claims reproducible. Each package is self-contained and re-verifiable from
the cited files.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `SPEC-01` | Queued | P1 | The accidentally committed, mangled scratch-diff file and its module-map row are removed together without breaking the accuracy ratchet. |
| `SPEC-02` | Queued | P1 | Six code-contradicted or self-contradicting spec passages state verified behaviour. |
| `SPEC-03` | Reverify | P2 | Frontend/backend singleton enforcement and the `score()` seed matrix are verified against code and aligned. |
| `SPEC-04` | Queued | P2 | Delivered change-contract material appears exactly once per component pair, fully folded into present-tense sections. |
| `SPEC-05` | Queued | P2 | Every ratchet-satisfying Testing filler line is replaced with an accurate description; none is factually wrong. |
| `SPEC-06` | Queued | P2 | Shared-file consumer rows are ledger-correct and ratcheted, and the known narrative ownership error is corrected and pinned. |
| `SPEC-07` | Queued | P3 | The io-layer and caching specifications answer the same depth of question as the corpus's strongest specs. |
| `SPEC-08` | Queued | P4 | High-level documents carry no implementation detail that duplicates their low-level counterpart. |
| `SPEC-09` | Queued | P4 | The verified small-consistency defects and newly stale TRIP publishing references are cleared without redundant `.omc` work. |
| `SPEC-10` | Decision | P3 | `> NOTE:` callouts are classified before only genuine live defects receive enforced roadmap linkage. |
| `SPEC-11` | Queued | P1 | Package builds detect every material frontend input change and reject incomplete or mismatched static bundles. |
| `SPEC-12` | Queued | P1 | The assistant split API validates JSON and every SSE variant at runtime before data reaches UI state. |
| `SPEC-13` | Queued | P2 | Corpus-review coverage, inventory, snapshot, and complexity claims are reproducible and scope-correct. |

## Planned improvements

### SPEC-01 — Retire the committed scratch-diff artifact

**Why:** A point-in-time review scratch diff was committed under a mangled
filename (a flattened Windows temp path beginning `CUserspriciAppDataLocal…`)
and then module-mapped in the engineering-quality specification as
"historical evidence" instead of being removed. The index currently stages
both the file deletion and the corresponding module-map-row removal. The
package remains open until that coherent pair lands and the
documentation-accuracy ratchet proves that no dangling reference remains.

**Plan:**

- Land the staged file deletion and module-map-row removal in one change, and
  confirm no other document references the mangled name.
- Run the documentation-accuracy suite to prove the corpus stays green with an
  empty baseline.

**Acceptance:**

- The mangled file is absent from the tracked inventory and from every
  specification document.
- The documentation-accuracy suite passes with no new baseline entry.

**Dependencies:** None.

**Evidence:** `specs/engineering-quality/low-level.md` (module-map row naming
the mangled file), `tests/test_docs_accuracy.py`,
`tests/docs_accuracy_baseline.txt`.

### SPEC-02 — Correct code-contradicted passages

**Why:** Six passages disagree with the code or with their own document; five
were verified directly against source during the 2026-07-27 review:

- Pipeline-config low-level's Control flow describes `Pipeline.to_graph()` as
  an independent conversion "without parameter-name inference", while its own
  Edge cases section, the high-level document, and `src/haute/pipeline.py`
  (which delegates to `_build_rf_nodes`/`_build_edges`) say the opposite.
- The assistant specification states twice that `haute serve` does not load
  the project `.env`, so API keys must be exported; `src/haute/server.py`
  loads `.env` during lifespan startup and the server-api specification says
  so. This is user-facing credential guidance.
- Tracing high-level names a `try_get` protocol method twice; the protocol
  and `src/haute/trace.py` define `PreviewReader.get`.
- Execution-engine high-level lists `ContractMismatchError` under the
  stable-public-`error_code` re-raise clause; the class declares no
  `error_code` (`src/haute/errors.py`) and is absent from the public-contract
  adapter. The low-level document and server-api describe the separate
  explicit re-raise correctly.
- Pipeline-config high-level claims unrecognised config keys are "the sole
  silent path"; its own low-level documents the `optimiserApply` ratebook-id
  remap (warn and continue) and discovery's silently skipped per-candidate
  `OSError`.
- Explore-EDA low-level's Control flow says a missing node id raises 400
  while its Error handling section and the high-level document say 404.

**Plan:**

- Correct the five verified passages to state the code-confirmed behaviour,
  keeping each edit surgical.
- For the explore-EDA status-code contradiction, determine the actual
  missing-node status from the route implementation, then fix whichever
  passage is wrong.

**Acceptance:**

- Each listed passage states the verified behaviour, and no document in the
  affected pairs contradicts its counterpart on these points.
- The documentation-accuracy suite passes.

**Dependencies:** None.

**Evidence:** `specs/pipeline-config/low-level.md`,
`specs/pipeline-config/high-level.md`, `src/haute/pipeline.py`,
`specs/assistant/high-level.md`, `specs/assistant/low-level.md`,
`src/haute/server.py`, `specs/server-api/high-level.md`,
`specs/tracing/high-level.md`, `specs/tracing/low-level.md`,
`src/haute/trace.py`, `specs/execution-engine/high-level.md`,
`src/haute/errors.py`, `specs/explore-eda/low-level.md`,
`specs/explore-eda/high-level.md`, `src/haute/routes/explore.py`,
`src/haute/routes/_explore_service.py`.

### SPEC-03 — Verify singleton enforcement and the seed matrix against code

**Why:** Two documented contracts may hide product defects rather than prose
defects, so they need reproduction before correction. First, backend save
validation enforces at most one `apiInput`/`output`/`liveSwitch` per graph,
while the frontend's documented singleton set is only Api Input and Output —
if the specs reflect the code, two Live Switch nodes can be placed on the
canvas and fail only at save time. Second, the standalone `score()` seed
matrix defines acceptance in terms of "distinct connected ports" without
stating whether an unnamed port (a `None` source port) counts as a port, or
how a labelled dict seed could ever match one; input identity was a recent
release's central contract, so the edge case should be pinned rather than
left ambiguous.

**Plan:**

- Reproduce both behaviours against `HEAD`: inspect the frontend singleton
  metadata and duplicate/palette guards, and exercise `score()` seeding
  against a source connected through an unnamed port.
- Where the code is correct and a spec is wrong, fix the spec. Where the code
  has the gap, file the fix as a package with the owning component —
  [frontend-canvas](frontend-canvas.md) for the singleton guard,
  [pipeline-authoring](pipeline-authoring.md) for seed-matrix behaviour — and
  record the dependency here until this package retires.

**Acceptance:**

- Frontend and backend specifications state the same singleton set, and the
  seed matrix defines the unnamed-port case explicitly.
- Any code change lands via the owning component's spec-first flow with a
  regression test.

**Dependencies:** Possible follow-on packages in
[frontend-canvas](frontend-canvas.md) and
[pipeline-authoring](pipeline-authoring.md), created only if reproduction
confirms a code gap.

**Evidence:** `specs/server-api/low-level.md`,
`specs/frontend-graph-canvas/high-level.md`,
`specs/frontend-graph-canvas/low-level.md`, `frontend/src/utils/nodeTypes.ts`,
`specs/pipeline-config/high-level.md`, `specs/execution-engine/low-level.md`,
`src/haute/pipeline.py`.

### SPEC-04 — Fold duplicated change-contract material

**Why:** Several delivered waves were pasted into both documents of a
component pair and never folded, retaining contract residue ("Non-goals:",
"Focused tests cover…", "Acceptance includes…", release/migration-note
imperatives) inside present-tense sections — against the fold-and-remove rule
in `specs/TEMPLATE.md`. Affected: pipeline-config (three sections duplicated
across high and low, one placed under the wrong heading on each side),
frontend-node-editors, frontend-modelling-optimiser-ui, and
frontend-preview-explore (trailing wave sections in both documents), and
json-shredding, whose two-rename non-atomicity note exists in both documents
and has already drifted (the two copies disagree about which tests exist).
The unfolded sections also contradict each other: node-editors says in one
section that the API-input v1-to-v2 migration suite remains and in another
that migration-specific tests are deleted.

**Plan:**

- For each affected pair, keep behavioural content once in the high-level
  document's normal sections and mechanics/testing content once in the
  low-level document, deleting contract-residue language.
- Deduplicate the json-shredding non-atomicity note to a single accurate copy
  (low-level), with a one-line pointer from high-level if needed.
- Resolve the node-editors migration-suite contradiction against the actual
  suite (`frontend/e2e/persistence/api-input-v2-native.spec.ts`) and make
  both sections agree.

**Acceptance:**

- No normative statement in an affected pair appears in both documents; no
  present-tense section retains non-goal, acceptance, or migration-note
  contract language.
- The two same-document contradictions are resolved; the
  documentation-accuracy suite passes.

**Dependencies:** None. `SPEC-08` builds on this fold.

**Evidence:** `specs/TEMPLATE.md`, `specs/pipeline-config/high-level.md`,
`specs/pipeline-config/low-level.md`,
`specs/frontend-node-editors/high-level.md`,
`specs/frontend-node-editors/low-level.md`,
`specs/frontend-modelling-optimiser-ui/high-level.md`,
`specs/frontend-modelling-optimiser-ui/low-level.md`,
`specs/frontend-preview-explore/high-level.md`,
`specs/frontend-preview-explore/low-level.md`,
`specs/json-shredding/high-level.md`, `specs/json-shredding/low-level.md`,
`frontend/e2e/persistence/api-input-v2-native.spec.ts`.

### SPEC-05 — Replace filler Testing lines with accurate descriptions

**Why:** 74 Testing lines across 18 components restate the test filename as
its description ("covers column renames" for a column-renames test file).
They exist to satisfy the ratchet's backend-test indexing, add no
information, dilute the genuinely descriptive Testing prose beneath them, and
at least one is factually wrong: expression-parsing describes
`tests/test_safety.py` as covering "expression safety and rejection of unsafe
constructs" when the file actually verifies pipeline structural invariants of
committed fixtures. Components with zero filler (tracing, rating, submodels,
git-integration) prove the standard is achievable.

**Plan:**

- For each filler line, read the test file's docstring/contents and rewrite
  the line into a one-sentence accurate description, or merge the reference
  into existing Testing prose. References must be retained — the ratchet's
  backend-test indexing requires each cited file to stay cited — so this is
  rewrite, not deletion.
- Fix the `tests/test_safety.py` description in expression-parsing first.
- This is bounded mechanical work suitable for a batch pass with per-file
  review; the worst offenders are engineering-quality (12), execution-engine
  (11), and io-layer (10).

**Acceptance:**

- No Testing line merely restates its filename; every description matches the
  file's actual scope on spot-check.
- Backend-test indexing and the rest of the documentation-accuracy suite stay
  green.

**Dependencies:** None.

**Evidence:** `specs/expression-parsing/low-level.md`, `tests/test_safety.py`,
`specs/engineering-quality/low-level.md`,
`specs/execution-engine/low-level.md`, `specs/io-layer/low-level.md`,
`tests/test_docs_accuracy.py`.

### SPEC-06 — Make shared-file ownership annotations consistent and enforced

**Why:** The corpus convention marks a consumer's module-map row for a shared
file with "Cross-component dependency owned by […]" (pipeline-config,
explore-eda, and codegen follow it). The original review found four omissions,
but a ledger-driven sweep finds a broader class across backend, frontend, and
repository configuration rows. Examples include `_path_resolution.py` in both
execution-engine and io-layer; `_cache.py`, `_polars_utils.py`, and
`schemas.py` in Explore; `schemas.py` in MLflow, modelling, and optimiser;
`server.py`, `_save_pipeline.py`, and `pyproject.toml` in assistant;
`App.tsx`, `useUIStore.ts`, and `Toolbar.tsx` in frontend-assistant; shared
form primitives and `buildGraph.ts` in frontend-node-editors;
`ExecutionDiagnosticsSummary.tsx` in frontend-preview-explore; and
`pyproject.toml`/`frontend/package.json` in engineering-quality. The ledger
records the true primaries, but nothing forces the consumer prose to agree.

There is also a direct narrative misattribution outside module maps:
frontend-git says server-api owns `frontend/src/api/client.ts` and `ApiError`,
while frontend-shared explicitly owns that transport and error contract.

**Plan:**

- Derive the repair set from every `[[shared_file]]` entry in
  `specs/ownership.toml`, not from a hand-maintained four-row list. For every
  listed consumer that module-maps the path, make the row explicitly name and
  link the ledger's primary component.
- Correct frontend-git's high- and low-level dependency/error prose so
  frontend-shared owns `api/client.ts` and `ApiError`; retain server-api as the
  owner of backend HTTP behaviour and git-integration as the owner of the Git
  request/response contract.
- Extend the documentation-accuracy suite with a ledger-driven rule: a
  consumer module-map row for a shared path must name the declared primary,
  and a wrong primary name must fail just as a missing annotation does.
- Keep the test data independent of the live corpus so one deliberately
  missing and one deliberately wrong owner prove both failure modes.
- Add a focused semantic assertion for frontend-git's dependency split:
  `api/client.ts`/`ApiError` point to frontend-shared, Git wire contracts point
  to git-integration, and server-api is not named as the frontend transport
  owner.

**Acceptance:**

- Every consumer row derived from the ownership ledger names the correct
  primary; there is no fixed allowlist of only the initially reported rows.
- Frontend-git, frontend-shared, server-api, and git-integration agree on the
  ownership split for transport/errors versus backend and Git contracts.
- The new ratchet fixtures fail for missing and wrong owner annotations, and
  the frontend-git semantic assertion fails on the old misattribution.
- The complete documentation-accuracy suite passes on the corpus.

**Dependencies:** None.

**Evidence:** `specs/ownership.toml`, `specs/execution-engine/low-level.md`,
`specs/io-layer/low-level.md`, `specs/server-api/low-level.md`,
`specs/git-integration/low-level.md`, `specs/explore-eda/low-level.md`,
`specs/assistant/low-level.md`, `specs/frontend-assistant-ui/low-level.md`,
`specs/frontend-node-editors/low-level.md`, `specs/optimiser/low-level.md`,
`specs/frontend-preview-explore/low-level.md`,
`specs/engineering-quality/low-level.md`,
`specs/frontend-git-ui/high-level.md`,
`specs/frontend-git-ui/low-level.md`, `specs/frontend-shared/high-level.md`,
`specs/frontend-shared/low-level.md`, `tests/test_docs_accuracy.py`.

### SPEC-07 — Rebalance depth for the terse core specifications

**Why:** Two prose registers coexist in the corpus, and the imbalance is not
risk-weighted: io-layer — owner of the source-cache lease/generation/quota/
staging machinery — has about 290 lines across its pair and caching about
230, while presentation-layer components run to four or more times that. The
terse documents compress load-bearing semantics into single sentences that
cannot answer the questions the corpus's strongest specifications answer.

**Plan:**

- Rewrite the io-layer and caching pairs toward the narrative register of the
  execution-engine/tracing specs, prioritising: the complete checked
  cache-input field sets; the snapshot lease lifecycle end to end (acquire,
  retention through derived plans, release, interaction with refresh/clear);
  staging-reclamation and quota-accounting walk-throughs; and failure
  ordering at each boundary.
- Treat databricks-io the same way if gaps remain after io-layer lands.

**Acceptance:**

- Each rewritten document answers a reviewed question list covering the areas
  above, grounded in the owning modules; the documentation-accuracy suite
  passes.

**Dependencies:** None.

**Evidence:** `specs/io-layer/high-level.md`, `specs/io-layer/low-level.md`,
`specs/caching/high-level.md`, `specs/caching/low-level.md`,
`src/haute/_source_cache.py`, `src/haute/_cache.py`,
`specs/databricks-io/low-level.md`.

### SPEC-08 — Restore the high/low altitude boundary

**Why:** `specs/TEMPLATE.md` reserves implementation detail for low-level
documents, but the most detailed high-level documents have absorbed it:
json-shredding high-level carries complexity analysis and lock names,
frontend-graph-canvas high-level carries sequence-counter names and
issue-number references, pipeline-config high-level names loader helper
functions. The result is a second copy of low-level content in the high
document — the same drift surface as duplicated contract sections, by
another route.

**Plan:**

- After `SPEC-04` folds the duplicated sections, sweep the three worst
  offenders and relocate implementation-level detail to the low-level
  document (or delete it where the low-level already states it), keeping the
  behavioural claim in high-level prose.

**Acceptance:**

- The three high-level documents contain no function-level, lock-level, or
  issue-number detail that their low-level counterpart owns; behavioural
  content is unchanged.

**Dependencies:** `SPEC-04`.

**Evidence:** `specs/TEMPLATE.md`, `specs/json-shredding/high-level.md`,
`specs/frontend-graph-canvas/high-level.md`,
`specs/pipeline-config/high-level.md`.

### SPEC-09 — Clear the small-consistency batch

**Why:** The verified small, independent defects are six "a assistant"
grammar artifacts from the mechanical copilot-to-assistant rename; io-layer
twice documenting dead, unshipped helpers; server-api's "not covered by this
spec pass" process wording where component links belong; background-jobs
pointing to tracing for `ExecutionContext` semantics that execution-engine
owns; version-pinned present-tense prose ("in 0.7.0") in
frontend-node-editors; and owner-less "owned outside this component" pointers
for the canonical graph types.

The tree now stages removal of the last tracked `docs/trip/` material. Once
that removal lands, `mkdocs.yml`'s `trip/` exclusion and the
build-and-distribution specs' TRIP publishing claims become stale and belong
in this cleanup batch. The `.omc/` review candidate remains a non-defect:
`.gitignore` already ignores it. NOTE classification and the unresolved
optimiser question belong to `SPEC-10`, not this mechanical batch.

**Plan:**

- Apply the verified corrections in one batch: fix wording/grammar, link the
  true owners, delete the dead-helper mentions, replace process-language
  residue, and remove version-pinned present-tense wording without changing
  current behaviour.
- Remove `trip/` from `mkdocs.yml` and remove the corresponding TRIP
  exclusion claims from the build-and-distribution specification pair after
  the staged source removal lands.
- Preserve the existing `.omc/` ignore rule. Local ignored tooling state does
  not become a tracked specification defect merely because it is physically
  below `specs/`.

**Acceptance:**

- Every verified defect listed above is resolved and grep finds no
  "a assistant" artifact.
- No tracked TRIP material, stale `trip/` publishing exclusion, or TRIP
  exclusion claim remains; `.omc/` remains ignored without a redundant rule.
- The documentation-accuracy suite passes.

**Dependencies:** None.

**Evidence:** `specs/assistant/low-level.md`,
`specs/frontend-assistant-ui/high-level.md`,
`specs/io-layer/low-level.md`, `specs/server-api/high-level.md`,
`specs/background-jobs/high-level.md`,
`specs/frontend-node-editors/high-level.md`,
`specs/build-and-distribution/high-level.md`, `mkdocs.yml`, `.gitignore`,
`specs/build-and-distribution/low-level.md`,
`specs/pipeline-config/low-level.md`, `specs/expression-parsing/high-level.md`.

### SPEC-10 — Decide and enforce a live-defect NOTE inventory

**Why:** `specs/README.md` and `specs/TEMPLATE.md` reserve `> NOTE:` for
suspected live defects, but the current callouts are not one homogeneous
inventory. Alongside real defects (deploy's quote-validation gate gap,
runtime-only `output_fields` failures, rating's corrupted-sidecar no-op, and
optimiser's missing-artifact 500), the syntax is also used for resolved
history, rejected alternatives, accepted trade-offs, operational caveats, and
an unresolved team question. Treating every current NOTE as live debt would
pollute owning roadmaps; treating none as tracked debt lets genuine defects
live forever without an owner or retirement path.

**Plan:**

- Classify every current NOTE as one of: live defect, resolved history,
  rejected alternative/design rationale, accepted trade-off, external
  operational caveat, or unresolved decision. Record the classification
  during migration so no callout is silently dropped.
- Keep `> NOTE:` only for live defects. Fold resolved history into current
  prose or remove it; rewrite design rationale/trade-offs and operational
  caveats as ordinary appropriately headed prose; move unresolved questions
  into the owning roadmap as `Decision` packages.
- In particular, reclassify expression-parsing's "prior to this fix" history,
  tracing's never-implemented row-id alternative, codegen's global collision
  trade-off, and pipeline-config's external Azure approval caveat. Move the
  optimiser error-detail-exposure question into
  [the optimiser roadmap](optimiser.md) as a `Decision` package.
- After classification, decide the live-defect linkage mechanism: either a
  ratchet rule requiring every remaining NOTE to name an owning roadmap
  package, or a reviewed inventory file mirroring the accuracy-baseline
  pattern. Record the decision and rejected alternative.
- Implement the chosen mechanism and bring only genuine live-defect NOTEs
  into compliance, filing owning-component packages where necessary.

**Acceptance:**

- Every pre-migration NOTE has a reviewed classification and every remaining
  `> NOTE:` describes a current suspected defect, not history, rationale,
  trade-off, an operational caveat, or a question.
- The optimiser question exists as a `Decision` package; the four named
  non-defect examples no longer use live-defect syntax.
- A short linkage decision record exists. The chosen check fails on an
  unlinked live-defect fixture, does not require ordinary rationale/caveat
  prose to enter the defect inventory, and passes on the corpus.
- Every remaining live-defect NOTE is linked or explicitly accepted through
  the chosen mechanism.

**Dependencies:** Owning components accept any packages spawned from their
NOTEs.

**Evidence:** `specs/README.md`, `tests/test_docs_accuracy.py`,
`specs/TEMPLATE.md`, `specs/expression-parsing/low-level.md`,
`specs/tracing/high-level.md`, `specs/codegen/high-level.md`,
`specs/pipeline-config/low-level.md`, `specs/optimiser/low-level.md`,
`specs/roadmap/optimiser.md`,
`specs/deploy/high-level.md`, `specs/rating/low-level.md`,
`specs/optimiser/high-level.md`.

### SPEC-11 — Make packaged frontend freshness and readiness complete

**Why:** Build-and-distribution promises that a package build never emits a
wheel whose browser interface is stale relative to checked source and selected
build configuration. The implementation and low-level spec currently define a
narrower mtime set: selected extensions below `frontend/src/` plus
`vite.config.ts`, two of the three TypeScript configs, and the npm
package/lock files. That set omits material production inputs including root
`pyproject.toml` (whose version Vite embeds), `frontend/index.html`,
`frontend/public/**`, `frontend/tsconfig.node.json`, and `frontend/.npmrc`.
An input deletion is also invisible to a scan of files that still exist.
Consequently validation mode can accept a stale bundle and explicit build mode
can run `npm ci` but skip Vite on the same false freshness result.

Readiness is likewise weaker than the documented "complete output asset set":
it checks only that `index.html` exists and `assets/` contains any entry. An
index that references missing JavaScript or CSS can therefore pass while the
packaged UI is unusable.

**Plan:**

- Add an approved change contract to the build-and-distribution specification
  pair before changing the hook. Define one authoritative production-input
  inventory covering root package-version metadata, `frontend/index.html`,
  `frontend/public/**`, all build-consumed `frontend/src/**` files,
  `vite.config.ts`, every referenced `tsconfig*.json`, `package.json`,
  `package-lock.json`, and `.npmrc`; explicitly document non-input exclusions
  such as contributor docs, tests, lint, Vitest, and Playwright configuration.
- Replace or augment the maximum-mtime heuristic with a deterministic input
  manifest/fingerprint recorded with the generated bundle, so additions,
  modifications, renames, and deletions are all detectable. Do not add a
  fallback that silently accepts an absent or unreadable manifest.
- Make validation and explicit-build skip decisions use the same freshness
  proof. Explicit build mode may skip Vite only when the proof exactly matches
  the current authoritative inputs.
- Have Vite emit a machine-readable output manifest (or an equivalent
  complete dependency graph). Strengthen readiness validation by checking the
  entry document plus every local script/style/module reference and every
  recursively imported or dynamic chunk named by that manifest. Require each
  resolved artifact to stay inside the static root and exist as a regular
  file. Preserve Vite's output replacement and fail clearly on absent,
  malformed, dangling, or escaping manifest entries.
- Extend focused hook tests before implementation, then update both build
  specs from the delivered behaviour and retain package/install smoke coverage
  through engineering-quality.

**Acceptance:**

- A change to root `pyproject.toml`, `frontend/index.html`, either public SVG,
  `tsconfig.node.json`, `.npmrc`, or any other declared production input makes
  the existing bundle stale; a production-input deletion or rename does too.
- A change limited to an explicitly documented non-input does not trigger a
  rebuild.
- Validation mode rejects every stale/missing-fingerprint case. Explicit mode
  either rebuilds or skips only after the same complete proof.
- A missing direct JavaScript/CSS reference or transitive/dynamic chunk fails
  even when `assets/` contains an unrelated file; a coherent Vite output and
  dependency manifest pass.
- Focused tests cover the input matrix, additions/deletions, corrupt or absent
  manifests, dangling direct and transitive references, path escape attempts,
  validation mode, and explicit mode. The package smoke path still produces
  an installable wheel with the current browser version.

**Dependencies:** Build-and-distribution owns the hook and specification
contract; [engineering-quality](engineering-quality.md) owns the real
sdist/wheel smoke path.

**Evidence:** `specs/build-and-distribution/high-level.md`,
`specs/build-and-distribution/low-level.md`, `hatch_build.py`,
`frontend/vite.config.ts`, `frontend/index.html`, `frontend/public/`,
`frontend/tsconfig.json`, `frontend/tsconfig.app.json`,
`frontend/tsconfig.node.json`, `frontend/.npmrc`, `pyproject.toml`,
`tests/test_hatch_build.py`, `scripts/package_smoke_check.py`.

### SPEC-12 — Validate the assistant API trust boundary completely

**Why:** Frontend-shared promises that concrete JSON endpoint functions fail
loudly on payload drift and that split modules using generic transport supply
their own response contract. `frontend/src/api/assistant.ts` does not yet
supply that contract: status and session responses are trusted through generic
TypeScript casts, while the SSE parser validates only the `type`
discriminator and casts every event-specific field. A recognised
`text_delta` without a string `text`, for example, reaches the store and can
append `"undefined"` to the transcript instead of surfacing contract drift.
Malformed tool rows, fingerprints, terminal messages, and nested token usage
have equivalent paths.

**Plan:**

- Add an approved change contract to frontend-assistant-ui and reconcile
  frontend-shared's generic-transport exception with the concrete local parser
  responsibility before changing code.
- Add assistant-local runtime parsers for the status response, session
  response and every history entry, and all seven SSE variants including the
  nested usage object. Validate required fields and their primitive/container
  types; keep unknown-discriminator failure and define deliberately whether
  additional fields are tolerated.
- Call the JSON transport as `request<unknown>()`/`post<unknown>()` and parse
  before returning typed data. Parse each SSE payload fully before invoking
  `onEvent`; on failure cancel the reader and propagate a descriptive ordinary
  `Error`, preserving the store's interrupted-turn handling.
- Add focused malformed-payload matrices for missing fields, wrong primitive
  types, invalid arrays/objects, malformed nested usage/history, each known
  event type, and an unknown discriminator. Prove no partial transcript or
  activity mutation occurs for a rejected event.

**Acceptance:**

- No assistant JSON response or SSE payload becomes an exported typed value
  solely through a TypeScript assertion.
- Every required status/session/history/event field is runtime-validated
  before UI state mutation; malformed known event variants fail as loudly as
  unknown variants.
- A missing/wrong `text` can never append `"undefined"`; malformed tool,
  fingerprint, failure-message, and usage payloads likewise cannot create
  partial or misleading transcript state.
- Parser/callback failure cancels the reader, produces the documented
  interrupted state, and does not become `ApiError`, preserving the shared
  HTTP-versus-contract-error distinction.
- Focused assistant API/store tests and frontend typecheck/lint pass.

**Dependencies:** Frontend-assistant-ui owns the split endpoint and store;
frontend-shared owns generic transport and the trust-boundary convention;
assistant owns the backend wire schema.

**Evidence:** `specs/frontend-shared/high-level.md`,
`specs/frontend-shared/low-level.md`,
`specs/frontend-assistant-ui/high-level.md`,
`specs/frontend-assistant-ui/low-level.md`, `specs/assistant/low-level.md`,
`frontend/src/api/assistant.ts`, `frontend/src/api/client.ts`,
`frontend/src/stores/useAssistantStore.ts`,
`frontend/src/api/__tests__/assistant.test.ts`,
`frontend/src/stores/__tests__/useAssistantStore.test.ts`.

### SPEC-13 — Make corpus-review claims reproducible and scope-correct

**Why:** The 2026-07-27 review calls itself a full review while documenting
partial and unread files. Its arithmetic also says 28 of 34 low-level specs
were read even though the corpus has 33 low-level documents; the listed five
unread lows plus the partially read graph low imply 27 fully read lows. It
simultaneously says all high-level specs were read and that both
reference-pipeline specs were not fully read. The current corpus inventory
also moved when this 410-line roadmap was added, so bare line/file totals
without a snapshot policy became stale immediately.

The same coverage gap affects the complexity conclusion. The review says the
only unjustified complexity is editorial without reading the component
roadmaps, while the optimiser roadmap already records verified duplication,
dead code, and a solve-service god module under `OPT-P10`–`OPT-P14`. Product,
specified-design, and editorial complexity are distinct scopes and must not be
collapsed into one corpus-wide verdict.

**Plan:**

- Define a repeatable review-inventory command or small checked script that
  reports component pairs, governance documents, roadmap files, Markdown line
  totals, and the exact repository snapshot. State whether staged and
  untracked files are included; never mix inventories from different trees.
- Give every reviewed file one coverage state: full read, partial read with
  exact line/range, mechanical scan only, or unread. Derive summary counts from
  that inventory rather than typing independent totals.
- Correct `SPECS-REVIEW-2026-07-27.md` to describe its actual snapshot and
  coverage, remove the contradictory "full review" claims, and distinguish
  confirmed findings from candidate findings requiring reproduction.
- Require broad complexity conclusions to inspect the component roadmaps and
  separate specified-design complexity, current implementation complexity,
  and corpus/editorial complexity. Link existing owning packages instead of
  copying them; specifically acknowledge optimiser `OPT-P10`–`OPT-P14`.
- Add focused tests for any checked inventory helper and document the command
  in the review method or corpus-review protocol. Keep ordinary
  documentation-accuracy tests responsible for paths/links/headings rather
  than presenting them as semantic completeness proof.

**Acceptance:**

- One command reproduces the review's snapshot-aware file and line inventory,
  with component high/low counts and roadmap/governance counts reported
  separately.
- Coverage totals are calculated from per-file states and cannot claim a file
  was fully read when it is partial or unread.
- The 2026-07-27 review no longer says 28 of 34 lows, no longer claims all
  highs while excluding reference-pipeline, and no longer presents a partial
  review as exhaustive.
- Its complexity verdict distinguishes the three scopes and points to
  optimiser `OPT-P10`–`OPT-P14` as implementation-complexity work already
  owned elsewhere.
- The review records the documentation-accuracy result accurately while
  stating that a green mechanical ratchet does not prove semantic
  completeness.

**Dependencies:** [Optimiser](optimiser.md) retains ownership of
`OPT-P10`–`OPT-P14`; this package corrects only corpus-review method and
cross-links.

**Evidence:** `SPECS-REVIEW-2026-07-27.md`, `specs/ownership.toml`,
`specs/roadmap/README.md`, `specs/roadmap/optimiser.md`,
`specs/roadmap/specs-corpus.md`, `specs/README.md`, `specs/TEMPLATE.md`,
`tests/test_docs_accuracy.py`.
