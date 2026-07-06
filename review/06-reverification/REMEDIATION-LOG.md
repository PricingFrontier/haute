# Remediation log (Phase F)

Executing the must-fix backlog from [`REPORT.md`](REPORT.md) in the wave order of
[`../REMEDIATION-PROGRAM.md`](../REMEDIATION-PROGRAM.md). Per `CLAUDE.md`: TDD (failing test first),
one developer + one independent reviewer per cluster, no lowered gates, fail-loud over fallbacks.
All work accumulates on `code-fixes` — **no merge** (Ralph's call).

Method: each wave's file-clusters are developed by Opus agents in isolated git worktrees (TDD),
adversarially reviewed by a second Opus agent, cherry-picked into `code-fixes` only after review,
with reviewer `CHANGES_REQUIRED` issues fixed on top before the wave audit gate (ruff + mypy +
affected-subsystem tests).

---

## Wave 0 — Criticals & near-free fail-loud quick wins ✅

**Audit gate: PASS** — `ruff check .` clean · `ruff format --check .` clean · `mypy src/haute/` clean (145 files) · 1012 affected-subsystem tests pass.

| Cluster | Findings addressed | Commit(s) | Review |
|---|---|---|---|
| C0.5 dependencies | F573 (mlflow CRIT), F597/F598 (vitest CRIT), F574–F578/F599/F600 (HIGH), F601/F602, + transitives | `6947e13a` | frontend `npm audit`=0 vulns; deploy/mlflow/training suites green |
| C0.2 pipeline public API | F510/F511/F513 (HIGH run()/score()/api_input), F512/F514/F515/F516/F533/F636/F288, F469 (sim) | `a0be62f0` | APPROVE |
| C0.4 haute init TOML | F131 (HIGH quote-escaping), F142/F143/F226/F227/F228, F455/F456 (sim), F634 | `009d51f4` | APPROVE |
| C0.1 sandbox security | F737 (CRIT RCE), F059/F120/F208/F290/F735/F736/F060/F289/F740/F291/F292 | `02e30f59` + `7d48e960` | CHANGES_REQUIRED → 5 issues fixed |
| C0.3 deploy artifact identity | F055/F056/F138 (HIGH served-price), F564/F121/F124/F318/F691 | `7965ad6f` + `3c7d5370` | CHANGES_REQUIRED → invalid F124 test repinned |

**Deferrals (documented, not dropped):**
- **F577 starlette** — pin-blocked: `fastapi>=0.129,<0.130` caps starlette at 0.52.x; the ≥1.0 fix needs a fastapi major upgrade (own workstream, out of Wave 0 scope).
- **F741 sandbox project-root default** — needs a `set_project_root()` call at serve startup (serve subsystem, out of cluster); the `_sandbox.py` hook is already exposed for it.
- **F518 pipeline instance registration** — deferred by dev; tracked into the type/registry sweep.
- Sandbox residual (honest): the `pl.format` carve-out is receiver-name-based, so rebinding `pl` to a
  malicious template string re-opens a narrow `str.format` side channel — fully closing it needs
  dataflow the AST layer lacks. Noted in-code; not among the reviewer's cited vectors.

---

## Wave 1 — Cache-spine integrity ✅

**Audit gate: PASS** — `ruff check .`/`format --check .` clean · `mypy src/haute/` clean · 662 cache-subsystem tests pass.

| Cluster | Findings addressed | Commit(s) | Review |
|---|---|---|---|
| json_shred conservation | F006/F008/F011 (HIGH fingerprint-collapse / list-collapse / stale-hash), F132/F640/F153/F103/F099/F717 | `5297a30b` + `991a13b4` | CHANGES_REQUIRED → 2 minor fixed (stale docstring, empty-list over-raise narrowed) |
| chunking OOM | F015/F713/F714/F715 (HIGH target-schema byte budget), F461/F258/F259/F260/F699/F698 | `13674d39` + `3f3d0211` | CHANGES_REQUIRED → F259 test pinned, F015 catch log-and-reclassify, F698 proof strengthened |
| cache fingerprint injectivity | F163/F164/F013/F641/F563 | `d2f4d3ea` | APPROVE |
| json_flatten Windows-safe mirror | F010/F307/F012/F306 | `c45d84e4` | APPROVE |
| json_cache route validation | F053/F009/F439/F440/F505 | `85876e2f` | APPROVE |

**Deferred:** F096/F097/F098/F101/F102 — behaviour-preserving json_shred simplifications (extract shared iteration/summary helpers), held out of the fail-loud correctness commit to avoid churning the dense mutation-witness suite; folded into the Wave 6 simplification batch. F717 shred peak-memory streaming is partial (removed the extra O(file) copy; full chunked-ParquetWriter streaming is a larger architectural change flagged for follow-up).

---

## Wave 2 — Codegen/executor equivalence ✅

**Audit gate: PASS** — ruff/mypy clean · codegen+parser+executor regression green (see combined W2+W3 gate below).

The structural centrepiece. New `src/haute/_node_apply.py` holds one shared code path per behavioural NodeType (`select_live_switch_input`, `expand_scenarios_from_config`, `apply_optimiser_apply_from_config`), called by **both** the executor builders and the codegen-emitted bodies — so a saved standalone `.py` now runs the same function the canvas executor calls. `_registry` gains an `is_behavioural` flag + an import-time invariant that makes a passthrough body for a stateful type **unrepresentable**. New differential harness `tests/test_codegen_execution_equivalence.py` drives graph→codegen→import→run under source∈{live,batch} vs `execute_graph`.

| Findings addressed | Commit(s) | Review |
|---|---|---|
| F000/F001/F005 (liveSwitch/optimiserApply/scenarioExpander no-op→real), F134/F156/F558/F852/F853/F743/F094/F090/F095/F002/F003/F637/F264/F265/F266/F856 (17 more) | `f48764b8` + `45d986f9` | CHANGES_REQUIRED → 5 TDD-gap tests added (all verified fail-without-fix) |

**Deferred:** F854 (freeze final registry — fights registry-mutation tests; import-time invariants already enforce integrity), F463/F093/F271 (behaviour-preserving sims → Wave 6), F462 (reverted: the "redundant" out__ validation is load-bearing early-fail).

---

## Wave 3 — Expression-evaluator fidelity + parser structure-conservation ✅

**Audit gate: PASS** — ruff/mypy clean · 1107 expression+parser tests · 801 trace tests · combined W2+W3 codegen/parser gate **396 pass** (after fixing a real roundtrip bug the W2 guard surfaced).

| Cluster | Findings addressed | Commit(s) | Review |
|---|---|---|---|
| expression-evaluator | F030 (remove value-laundering fallback), F680/F686/F681/F679/F682/F683/F684 (Kleene/div0/pow/overflow/clip/log/horizontal vs Polars oracle) + 25 more (33 total) | `7c442749` + `c4760187` | APPROVE (3 minors; concat_str silent-coercion fixed) |
| parser structure-conservation | F135/F027/F137 + `assert_structure_conserved`, regex-fallback submodel recovery, async ParseError, param_names threading (20 total) | `ff012f09` | APPROVE |

**Cross-wave bug (W2 guard × W3 parser):** the new empty-code-multi-source transform guard surfaced a latent **roundtrip-conservation defect** — `_strip_docstring` (`_ast_helpers.py`) textually mis-scanned a docstring ending in `"` and silently dropped the entire function body. Root-caused and fixed with AST-based docstring resolution + 3 TDD tests — `7394b040`. A genuine silent-wrongness bug the remediation flushed out.

---

## Wave 4 — Rating-key & trace-correlation fidelity ⏳ in progress
_Float32 rating-key mirror/twin agreement + save↔apply property · trace-correlation scale-relative tolerance · model-scorer._
