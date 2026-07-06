# Exhaustive sweep — status & resume plan

**Paused 2026-06-20 by an account session usage limit (resets 12:20pm Europe/London).** No code changed; read-only throughout.

## Done
- **Python backend: exhaustively swept** — every function reviewed.
  - core 71/72, routes 24/24, modelling 11/11, deploy 9/9, cli 6/6, scripts 6/6 = **147/148 buckets** (only **B039** failed, a parse error — re-run it).
- **265 candidate findings** gathered (212 bugs — 18 high / 44 medium / 118 low / 32 unrated — + 53 simplifications).
  - These are **discovery candidates, code-traced but NOT yet verified** (the adversarial reproduce-or-refute gate has not run).
- Artifacts: `candidates.json` (all, structured), `EXHAUSTIVE-FINDINGS.md` (grouped), `COVERAGE-LEDGER.md` (per-bucket done/pending), `raw/*.json` (per-batch).

## Pending (88 buckets)
- **B039** (core — `src/haute/...`, re-run the one failed bucket).
- **Frontend: 3/90 done; 87 pending** — buckets B129-B218 except B131, B134, B144. See `COVERAGE-LEDGER.md` for the file list.

## To resume (after the limit resets)
1. Re-run the exhaustive discovery workflow for the **pending bucket ids** (B039 + the 87 frontend buckets). The workflow scripts are saved under the session's `workflows/scripts/`; the per-bucket manifests are in `review/04-exhaustive/buckets/`. Run frontend in 2-3 sub-batches to stay under limits.
2. **Verification pass:** run reproduce-or-refute over the candidate **bugs rated high + medium** (62 of them) — write isolated repros, separate real from refuted — and behaviour-preservation checks on the material simplifications.
3. Dedup verified survivors and fold them into `review/02-findings/catalog.md` / `new-bugs.md`; update `review/README.md` totals.
4. Re-run `.cache/consolidate.py` to regenerate the findings doc + ledger.

## Confidence note
The backend candidate list is high quality (agents dismissed non-issues with reasoning and deduped against the 131 prior findings), but the 18 high candidates still need the verification gate before being treated as confirmed — same bar as the 93 already-verified findings.
