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

## Wave 1 — Cache-spine integrity ⏳ in progress
_json_shred conservation · chunking OOM · cache fingerprint injectivity · json_flatten Windows rename · json_cache route validation._
