# Exhaustive sweep — final report

Read-only audit of `origin/main` @ `1b8eb150`. **No source code changed; tree verified clean throughout.**

## Coverage — complete
Every file, every function reviewed: **218/218 buckets, 365 files** across all groups (core, routes, modelling, deploy, cli, scripts, frontend) — see `COVERAGE-LEDGER.md`. Giant files were split by line-range so each agent went deep.

## Method
1. **Discovery** — one agent per bucket, exhaustive, deduped against the 131 prior findings → **443 candidate findings** (346 bugs, 97 simplifications).
2. **Consolidation** — `candidates.json`, coverage ledger (early drafts now under `review/_working/`).
3. **Verification** — adversarial reproduce-or-refute over the **102 high+medium candidate bugs**: executed isolated repros for backend, code-trace for frontend.

## Verification result
- **95 of 102 verified real** (93%), **7 refuted**, 0 unprovable.
- Deduped real by refined severity: **11 high, ~65 medium, ~19 low** (several high candidates were correctly down-graded on scrutiny).
- Repros (95) in `review/04-exhaustive/repro/`; full detail in `VERIFIED-BUGS.md` + `verified-bugs.json`.

### The 11 newly-verified HIGH bugs
| # | Bug | Location |
|---|---|---|
| 1 | `haute init` corrupts pyproject: unescaped dependency quotes → invalid TOML, command crashes | `cli/_init_cmd.py:269` |
| 2 | JSON key literally named `$value` collides with the scalar-array sentinel → wrong schema inference | `_json_shred.py:1199` |
| 3 | Azure DevOps scaffold emits invalid YAML (under-indented `env:` secrets) → broken pipeline | `_scaffold.py:890` |
| 4 | `_gen_constant` emits columns for empty/missing-name constant entries (codegen↔executor divergence) | `_codegen_builders.py:572` |
| 5 | `_match_source` silently drops the first user statement when a dataSource body has no recognized loader | `_code_extraction.py:481` |
| 6 | Rating-step compact↔expand asymmetry can emit a sidecar that round-trips to different values | `_rating_step_config.py:104` |
| 7 | Direct edge between children of two different submodels loses its boundary handle (dropped on flatten) | `_parser_submodels.py:173` |
| 8 | Misconfigured `modelScore` node deploys and serves as a SILENT passthrough (no model/contract bundled) | `deploy/_scorer.py:605` |
| 9 | Switching utility files discards the pending debounced save → last edit lost | `frontend/.../UtilityPanel.tsx:111` |
| 10 | Non-UTF-8 binary column crashes the entire Explore materialisation (strict cast) | `routes/_explore_service.py:331` |
| 11 | Semaphore double-release (permit leak) when limiter acquire and supersession complete in the same tick | `routes/_supersession.py:182` |

(Plus, from B039: the regex fallback parser silently drops `# haute:preserve` blocks → data loss on save.)

## Not yet verified (candidates pending)
- **191 low-severity + 53 unrated candidate bugs** — code-traced by the discovery agent, NOT through the reproduce-or-refute gate. High-quality lead list; verify before acting.
- **97 simplification candidates** — behaviour-preservation noted by the finder; the material ones (god-file extractions, dedups) warrant a confirm pass like the earlier 38 sound ones.

## Grand totals — full audit (all phases)
- **188 verified bugs** = 69 (pass 1) + 24 (pass 2) + 95 (this exhaustive pass).
- **38 sound simplifications** (verified) + 97 candidates.
- Architecture roadmap (5 structural changes), complexity baseline (97 fns > McCabe 12), coverage baseline (91.75%/92%).
- ~244 additional low/unrated candidate bugs awaiting verification.
- Every verified bug ships a reproducing script → ready-made TDD backlog.

## Artifacts (`review/04-exhaustive/`)
`VERIFIED-BUGS.md` · `verified-bugs.json` · `candidates.json` · `COVERAGE-LEDGER.md` · `repro/*.py` · `complexity-metrics.md` (per-bucket + verification journals are in `review/_working/`).
