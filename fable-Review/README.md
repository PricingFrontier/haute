# Fable Review

Deep engineering reviews of Haute subsystems, one folder per review area. Each folder is
self-contained: a `README.md` with the verdict, ranked fix packages, and the implementation protocol
(TDD failing-test-first, dev/reviewer pairing rules, gates); per-package files with evidence,
fix designs, and test specs; and a `CLEARED.md` of behaviours checked and found correct (do not
"fix" those).

Reviews are read-only — no source was changed. Fixes are implemented separately, package by package,
per each review's protocol.

| Area | Reviewed at | Status | Headline |
|------|-------------|--------|----------|
| [polars-backend](polars-backend/README.md) | 2026-07-06, HEAD `4fcaa8f0` | findings ready, fixes not started | Strong engine; 5 HIGH / ~15 MEDIUM verified findings — preview-cache scoping, diamond `.cache()` no-op, trace hot path, O(rows²) assembler, Windows RSS sampler |

<!-- Add future review areas as sibling folders (e.g. frontend/, git-panel/, deploy/) with the same
     structure, and a row here. -->
