# Specs corpus review — 2026-07-27

Full review of `specs/` (22,643 lines: 33 components × high/low + governance + roadmap index) for bugs, duplication, over-complexity, and inconsistencies. Reviewer: Fable, branch `yet-another-review`.

**Method and coverage.** All 33 high-level specs, 28 of 34 low-level specs, `README.md`, `TEMPLATE.md`, `ownership.toml`, and `roadmap/README.md` were read in full; `frontend-graph-canvas/low-level.md` was read through line 730 of 1061 (the remainder is Testing enumeration). Not fully read: `build-and-distribution/low-level.md`, `reference-pipeline/*` (both), `frontend-git-ui/low-level.md`, `frontend-trace-ui/low-level.md`, `frontend-assistant-ui/low-level.md`, and the 16 per-component roadmap files (covered via the index, the corpus-wide `> NOTE:` sweep, and targeted greps). Five findings were verified directly against source code. The mechanical accuracy ratchet (`tests/test_docs_accuracy.py`) was run and is green with an **empty baseline**, so paths/symbols/headings/links/anchors were not re-checked by hand; this review targets what the ratchet cannot see.

**Verdict.** This is an unusually strong spec corpus — the machine-checked accuracy layer, ownership ledger, honest `> NOTE:` defect flagging, and tight cross-component contract agreement (rating dtype canonicalisation, `edge_input_name`, strategy-diagnostics v1, job terminal states) are all working. The defects found are concentrated in three classes: (1) a handful of **stale or self-contradicting passages**, five confirmed against code; (2) a **systemic duplication pattern** — change-contract material pasted into both docs of a pair and never folded, already drifting; (3) **register/depth inconsistency** and ratchet-satisfying filler that dilute the corpus's otherwise high signal.

---

## HIGH findings

**H1. `Pipeline.to_graph()` control-flow description is stale — contradicted by the same document and by code.**
`specs/pipeline-config/low-level.md:107-110` (Control flow) says `to_graph()` "independently converts the same live objects… inferring each node's display type… without parameter-name inference." The same file's Edge cases (`:189-190`), the high-level spec (`high-level.md:80-82`), and the code (`src/haute/pipeline.py:653` — imports and delegates to `_build_edges`/`_build_rf_nodes`) all say the opposite: it routes through the shared static builders. The Control-flow passage predates the refactor. *Fix: rewrite Control-flow item 1 to match the delegation design.*

**H2. Assistant spec gives wrong credential-setup guidance — `.env` IS loaded by the server.**
`specs/assistant/high-level.md:66-68` and the `_config.py` row in `low-level.md` state "`haute serve` does not currently load the project `.env`, so those keys must be exported." But `src/haute/server.py:376-380` calls `_load_env(Path.cwd())` (borrowed from `haute.deploy._config`) during lifespan startup, and `specs/server-api/high-level.md:90` + low-level Startup both document that the server "loads the project's `.env`." The assistant claim is stale in both docs, and it is user-facing: it tells analysts their `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` cannot live in `.env` when they can. *Fix: correct both assistant docs (or, if there is a subtle `haute serve`-specific gap, document it precisely and reconcile server-api).*

**H3. A mangled, accidentally-committed session scratch file is enshrined in a module map.**
`specs/engineering-quality/low-level.md:10` module-maps a tracked root file literally named `CUserspriciAppDataLocalTempclaudeC--Users-prici-haute9daf718f-…scratchpadpr139.diff` — a Claude-session scratch diff whose Windows temp path was flattened into the filename — describing it as "historical evidence only." This is the spec being used to legitimise an accident instead of the accident being removed. The working tree currently has this file **deleted but uncommitted** (git status `D`); committing that deletion without removing the spec row will fail the accuracy ratchet. *Fix: delete the file and the module-map row in one commit.*

**H4. Frontend and backend disagree on the singleton node-type set (`liveSwitch`).**
Backend save validation enforces "at most one `apiInput`/`output`/`liveSwitch`" (`specs/server-api/low-level.md:234-235`). The frontend's `SINGLETON_TYPES` is documented as "(API-input, output)" in both `specs/frontend-graph-canvas/high-level.md:207-208` and `low-level.md` (`utils/nodeTypes.ts` row; duplicate-is-a-no-op edge case), with the palette relied on to prevent seconds. If the specs reflect the code, a user can place two liveSwitch nodes and only discover the violation at save time — a UX gap; if they don't, one spec is wrong. *Fix: verify `SINGLETON_TYPES` in `frontend/src/utils/nodeTypes.ts`; align code or specs.*

**H5. Tracing high-level names a protocol method that does not exist.**
`specs/tracing/high-level.md` says the trace module accepts "anything exposing `try_get(fingerprint)`" — twice (Design rationale ~:227, Failure model ~:332). The low-level spec (`:14-16`) and the code (`src/haute/trace.py:125`; no `try_get` anywhere in the file) define `PreviewReader.get()`. High-level is stale. *Fix: s/try_get/get/ in high-level.*

---

## MEDIUM findings

**M1. Systemic: change-contract material duplicated across both docs of a pair, never folded, already drifting.**
The governance model is explicit (README/TEMPLATE): approved future work lives in a labelled `## Approved change contract` section and is folded into present-tense prose on delivery. Eight sections follow this correctly. But several components instead carry *unlabelled* delivered-wave sections pasted into **both** high- and low-level docs, retaining contract language ("Non-goals:", "Focused tests cover…", "Acceptance includes…", "The 0.6 pre-1.0 release/migration notes must identify…"):
- `pipeline-config`: "Live node arity and switch behaviour", "Canonical data I/O node types", "Retained input sidecar authority" — each duplicated in high (237-301) and low (336-407), misplaced (arity under high's *Failure model* with Testing content; low's copy under *Testing* with behaviour content), and with function names in the high-level doc against the no-implementation-detail rule.
- `frontend-node-editors`, `frontend-modelling-optimiser-ui`, `frontend-preview-explore`: trailing wave sections ("Data I/O editors", "Banding-to-Rating assurance", "Canonical editor formats", "Execution diagnostics", "Optimiser canvas assurance", "Frontier-range editor") duplicated across each pair.
- Proof of the drift hazard: the json-shredding two-rename-swap `> NOTE:` is duplicated near-verbatim (`high-level.md:197-207` vs `low-level.md:255-266`) and the two copies **already disagree** on which tests exist (high omits "different-cache parallelism"; wording has diverged).
*Fix: one home per contract-derived passage — behaviour in high, mechanics/testing in low — and finish the fold for delivered waves.*

**M2. Internal contradiction in node-editors: does the API-input v1→v2 migration suite exist or not?**
`specs/frontend-node-editors/low-level.md` "Data I/O editor implementation" (~:285) says "The separate API-input v1-to-v2 migration suite **remains**…"; the same file's "Canonical editor formats" (~:327 + high-level counterpart) says pre-v2 classification is removed and "Migration-specific fixtures/tests are **deleted**." Two unfolded wave sections from different releases disagreeing in one document — a concrete instance of M1.

**M3. Internal contradiction in explore-eda on the missing-node status code.**
`specs/explore-eda/low-level.md` Control flow (`:81-82`) says `find_typed_node` "raises 400 if the node is **missing** or not Explore-typed"; the same file's Error handling (`:306-309`) and `high-level.md:162-163` say missing node → **404**, wrong type → 400. One passage is wrong.

**M4. Execution-engine high-level misattributes `ContractMismatchError` to the stable-`error_code` re-raise clause.**
`specs/execution-engine/high-level.md:90-95` groups `ContractMismatchError` under "every `HauteError` with a stable public `error_code`… always raised." Verified: the class declares no `error_code` (`src/haute/errors.py:348ff`) and is absent from the public-contract adapter (`_contract_errors.py`, and from server-api's 11-entry stable-code table). Exec low-level and server-api describe the mechanism correctly (a separate explicit re-raise). High-level should not tie it to the code-bearing clause.

**M5. "The sole silent path" overclaim in pipeline-config.**
`specs/pipeline-config/high-level.md:232-235` claims unrecognised config keys are the component's only silent path; its own low-level documents two more: the `optimiserApply` ratebook-id remap that WARNs and leaves config unchanged (`:137-139`), and discovery's silently-skipped `OSError` per candidate file (`:220-222`).

**M6. A Testing reference with a factually wrong description.**
`specs/expression-parsing/low-level.md:263`: "`tests/test_safety.py` covers expression safety and rejection of unsafe constructs." The file's actual docstring is "Safety tests — verify pipeline structural invariants" (it imports `haute.parser` and checks fixture pipelines have an output node) — nothing about expressions or unsafe constructs. This is one of the 74 filler lines (M9) proven wrong, showing the class is not harmless.

**M7. Shared-file ownership annotations are inconsistent — four consumer rows read as owned.**
Convention (used by pipeline-config, explore-eda, codegen): a consumer's module-map row says "Cross-component dependency owned by […]". Four shared files are listed with full responsibility text and **no** owner pointer, so the reader infers the wrong owner (ownership.toml disagrees):
- `src/haute/_path_resolution.py` in `execution-engine/low-level.md:9` (owner: sandbox-security)
- `src/haute/_local_security.py` in `server-api/low-level.md:8` (owner: sandbox-security)
- `src/haute/_gitignore_guard.py` in `git-integration/low-level.md:10` (owner: sandbox-security)
- `frontend/src/api/types.ts` in `optimiser/low-level.md:14` (owner: frontend-shared)

**M8. Two contradictory documented conventions for HTTP status selection.**
`specs/server-api/low-level.md:25` (`_runtime_path_errors.py`): status "selected by concrete exception type rather than message text." `specs/tracing` (high Failure model; low error table): trace `ValueError`s are mapped to 404/400/409 by **message pattern-matching**. Both are accurate descriptions of the code, but the corpus documents the string-matching approach without noting it violates the principle the path-error module was built to uphold. Worth a deliberate decision (typed trace errors, or an acknowledged exception).

**M9. 74 zero-information Testing filler lines across 18 components.**
Lines of the form "`tests/test_column_renames.py` covers column renames" (worst: engineering-quality 12, execution-engine 11, io-layer 10) sit above otherwise excellent Testing prose. They exist to satisfy the ratchet's backend-test indexing, add no information, dilute the genuinely descriptive entries, and at least one is wrong (M6). Components with zero filler (tracing, rating, submodels, git-integration, explore-eda) show the standard is achievable.

**M10. Register/depth inconsistency across the corpus.**
Two prose registers coexist: verbose narrative (execution-engine, tracing, server-api, json-shredding, frontend-graph-canvas) and telegraphic (caching 233 lines, io-layer 290, databricks-io 152, node-editors/preview-explore/modelling-optimiser-ui highs). The imbalance is not risk-weighted: io-layer — owner of the source-cache's lease/generation/quota/staging machinery — gets 290 lines while frontend-graph-canvas gets 1,649. The terse docs compress load-bearing semantics into single sentences ("Snapshot readers acquire an explicit generation lease…") that cannot answer the questions the verbose docs answer.

**M11. High/low altitude separation has eroded in the detailed specs.**
TEMPLATE: high-level carries "no implementation detail." In practice json-shredding high contains O(n log n)-vs-O(n²) analysis and `_build_lock_for`; frontend-graph-canvas high contains `saveRequestSeq` and Issue #32-35 references; pipeline-config high names `resolve_api_input_from_config`. The result is a second copy of low-level content in the high doc — the same drift surface as M1 by another route.

**M12. `score()` seed matrix never defines the unnamed-port case.**
`specs/pipeline-config/high-level.md:93-105` (mirrored in exec low) defines the bare-frame/dict seed matrix in terms of "distinct connected ports," but never states whether an edge with `source_port=None` counts as a port, nor how a `{frame_label: DataFrame}` dict could ever match a `None` port. Given input identity was this release's central contract, the edge case should be pinned.

---

## LOW findings

- **L1.** `> NOTE:` misused for resolved history: `expression-parsing/low-level.md:80-83` ("prior to this fix, `fallback_parse`… silently dropped every preserve block") documents a *fixed* bug in the convention reserved for live defects. One instance; the other ~30 NOTEs are legitimate.
- **L2.** The ~31 live-defect NOTEs (e.g. deploy quote-validation is not a gate, `output_fields` failures only at runtime, rating's silent no-op on corrupt banding sidecar, optimiser's 500-for-missing-artifact) have **no tracked inventory or guaranteed roadmap linkage** — a NOTE can live forever. The optimiser spec even parks a team question in one (`optimiser/low-level.md:566` "worth confirming with the team…"), which belongs in `roadmap/optimiser.md` as a Decision package.
- **L3.** Rename residue: "a assistant" appears 4× in `assistant/low-level.md` and 2× in `frontend-assistant-ui/high-level.md` — mechanical copilot→assistant replace.
- **L4.** Negative-space documentation: `io-layer/low-level.md:60-61,:99-100` twice describes dead, unshipped helpers ("`read_polars_input_from_config()`… not shipped"); the canonical-only contract already covers this globally.
- **L5.** `server-api/high-level.md:67-68`: "separate components not covered by this spec pass" — process language; they are covered by their own specs and should be linked like the neighbouring bullets.
- **L6.** `background-jobs/high-level.md:52-54` points readers to *tracing* for `ExecutionContext`/cancellation semantics; the owner is execution-engine.
- **L7.** Version-pinned present-tense prose: `frontend-node-editors/high-level.md` "Databricks is not displayed as an output group **in 0.7.0**" — will silently go stale (Version-semantics section says versions belong only in temporary-contract headings).
- **L8.** `build-and-distribution/high-level.md:66-67` still lists "TRIP material" among excluded docs; TRIP was removed/archived on 2026-07-26 — verify the exclusion list matches `mkdocs.yml`.
- **L9.** An untracked `.omc/` tooling-state directory sits inside `specs/` — add to `.gitignore` or relocate.
- **L10.** Owner-less pointers: pipeline-config and expression-parsing say `PipelineGraph`/`_types.py` is "owned outside this component" without naming server-api (which module-maps it) — a link would save the lookup.

## Over-complexity observations

No component's *specified design* stands out as gratuitously complex — the intricate machinery (source-cache generations/leases, supersession coordinators, worker protocol v1, trace correlation) is matched to documented failure classes, and rationale sections consistently justify it with rejected alternatives. The complexity that is real and unjustified is **editorial**: the M1 duplication, M9 filler, and M11 altitude erosion make the corpus larger and harder to maintain than its content requires. Rough estimate: folding the duplicated wave sections and deleting filler would remove 800-1,200 lines with zero information loss.

## Strengths worth preserving

- The **accuracy ratchet with an empty baseline** — every path/symbol/link/heading/ownership claim machine-verified, zero accepted debt.
- **Cross-component contract agreement** is exceptional where it matters most: the rating dtype/`normalise_rating_key` contract (rating ↔ tracing ↔ optimiser, pinned by one shared fixture matrix), `edge_input_name` identity (executor ↔ codegen ↔ deploy ↔ both frontends), strategy-diagnostics v1 vocabulary (execution-engine ↔ server-api ↔ frontend-shared, byte-identical), job terminal states and precedence (background-jobs ↔ every consumer).
- **Honest defect disclosure**: `> NOTE:` callouts, "Known gaps" in Testing sections, and documented asymmetries (`SchemaMismatchError` preview handling, deploy's apiInput-only input discovery) are exactly the culture a spec corpus needs.
- Best-in-corpus pairs to use as style references: **tracing**, **sandbox-security**, **git-integration**, **background-jobs**, **assistant** (modulo H2).

## Recommended remediation order

1. **H3** (scratch-diff file + spec row, one commit — unblocks the pending deletion already in your working tree).
2. **H1, H2, H5, M3, M4, M5** — small, surgical prose corrections, each ≤10 lines.
3. **H4, M12** — verify against code, then align (these may reveal product bugs, not just spec bugs).
4. **M1/M2** — fold the duplicated wave sections per the TEMPLATE rule; resolves M2 as a side effect.
5. **M6/M9** — delete or rewrite the 74 filler lines (mechanical; a haiku-tier pass with review).
6. **M7, M8, L1-L10** — batch of small consistency fixes; consider a ratchet rule for consumer-row ownership annotations (M7) and a NOTE-inventory check (L2).
