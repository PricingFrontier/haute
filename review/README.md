# Haute — engineering audit (`review/`)

A complete, **read-only** engineering audit of the `haute` insurance pricing engine. Every file and
function was reviewed across five phases plus ten quality dimensions. **No source code was changed** —
this folder is the deliverable: the findings, the evidence (runnable reproductions), and a single
prioritised remediation plan. The working tree was verified clean after every phase.

---

## ⭐ Start here

**[`REMEDIATION-PROGRAM.md`](REMEDIATION-PROGRAM.md)** — the single master plan. All 881 verified
findings synthesised into one prioritised, sequenced program: executive summary, the 7
highest-leverage moves, a 7-wave execution sequence, and a must-fix-vs-accept-risk framework.
**If you read one file, read this.**

---

## What was found (totals)

- **417 verified bugs** — 5 critical, 110 high, 257 medium, 413 low. Each reproduced or code-traced by an independent skeptic; ~45 candidates refuted.
- **93 sound, behaviour-preserving simplifications.**
- **371 findings across 10 quality dimensions** (numerical, tests, performance, types, security, CI-gaps, docs, API/DX, dependencies, frontend a11y).
- **Coverage:** every file & function (218/218 review units, 365 files). Suite baseline 91.75% / 92%.
- Every "real" finding ships a reproduction → a ready-made TDD backlog.

The single biggest theme: *a strong engine with a strong test suite that asserts the **wrong invariant**
at its most dangerous seams — structural equivalence where it needed **value** equivalence, fingerprint
**presence** where it needed **completeness**, graceful degradation where the codebase mandates
fail-loud.* The remediation is a handful of structural fixes + prevention guards, not a rewrite.

---

## How this folder is organised

### Top level — the plan
| File | What |
|---|---|
| **`REMEDIATION-PROGRAM.md`** | ⭐ master prioritised + sequenced program (read first) |
| `ARCHITECTURE-ROADMAP.md` | the 5 highest-leverage structural changes |
| `REMEDIATION-PLAN.md` | 20 root-cause clusters (earlier capstone; folded into the program) |

### Phase directories — the detail, in reading order
| Dir | Phase | Key reports |
|---|---|---|
| `00-map/` | Map | `architecture.md` (system map + ranked risk heat-map), `coverage-baseline.md` |
| `02-findings/` | Subsystem deep-dive (P1) | `catalog.md` (69 verified bugs), `orchestrator-notes.md` (my independent re-verification + severity corrections), `findings.json`, `repro/` |
| `03-simplification/` | Simplification + complexity (P2) | `simplifications.md` (38 sound), `new-bugs.md` (24 bugs), `complexity-metrics.md`, `repro/` |
| `04-exhaustive/` | Exhaustive file-by-file sweep (P3) | `FINAL-REPORT.md`, `VERIFIED-BUGS.md` (95 high/med), `COVERAGE-LEDGER.md` (proves full coverage), `candidates.json`, `repro/` |
| `05-dimensions/` | 10 quality lenses (P4) | `DIMENSIONS-REPORT.md` (371 findings, all 10 lenses) |

### Data & evidence
| Path | What |
|---|---|
| `MASTER/INDEX.md`, `MASTER/all-verified.json` | the **unified index** of all 881 findings — one normalised record each (phase, severity, file, title, fix). Query this to filter programmatically. |
| `*/repro/*.py` | **runnable reproduction scripts** — referenced by basename throughout the reports. ~180 backend repros (Python); run with `uv run python <path>`. |
| `_working/` | machine artifacts: per-file review buckets, verification journals, Phase-0 region maps + leads, superseded drafts. **Ignore unless reconstructing the audit.** |

---

## If you're a fresh agent picking this up

1. **Read [`REMEDIATION-PROGRAM.md`](REMEDIATION-PROGRAM.md) end to end** — it is the entire audit, prioritised, with `file:line` citations and pointers to every phase's detail.
2. **To act on a finding:** the program's execution sequence is organised into Waves (0–6). Pick a Wave; each item names the source `file:line`, the fix, and (for bugs) a repro under `*/repro/`.
3. **TDD remediation:** the repro *is* the failing test — make it pass. This matches the project's CLAUDE.md (failing test first, then fix).
4. **Nothing has been applied.** Remediation was deferred by the original constraint (frozen feature branches). When you remediate, follow the Wave order and the must-fix-vs-accept-risk cut line in the program.
5. **To query everything:** `MASTER/all-verified.json` is the machine-readable index of all 881 findings.

---

## How the audit was run (provenance)

Five phases, each orchestrated as multi-agent workflows, with every finding **reproduced or
code-traced by an independent skeptic** before acceptance (~45 candidates refuted):

- **P0 Map** — fan-out region mappers → architecture model + risk heat-map.
- **P1 Deep-dive** — per-subsystem find → adversarially verify (reproduce-or-refute) → 69 bugs.
- **P2 Simplification** — complexity-metric-targeted; behaviour-preservation-gated → 38 sound + 24 bugs.
- **P3 Exhaustive** — every function in 218 buckets; high/med verified by executed repro, the low tail by code-trace → 95 + 229 + the coverage ledger.
- **P4 Dimensions** — 10 quality lenses (numerical … a11y) → 371 findings.
- **Synthesis** — all 881 deduped + prioritised into `REMEDIATION-PROGRAM.md`.

All read-only; the source tree was verified clean (zero modified tracked files) after every phase.
