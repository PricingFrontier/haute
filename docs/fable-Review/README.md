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
| [optimisation](optimisation/README.md) | 2026-07-06, HEAD `2caa4134` | findings ready, fixes not started | Seriously engineered; 5 HIGH / 14 MEDIUM / 8 LOW verified — frontier apply wipes grid, non-atomic deploy save, null-constraint silent wrongness, 15^d frontier sizing, sync 10k-solve endpoints; +11 upstream (price_contour) |
| [tracing](tracing/README.md) | 2026-07-06, HEAD `220bcccd` | findings ready, fixes not started | Right architecture, trust holes at the edges; 2 CRITICAL (silent wrong-row anchor, stale trace over edited pipeline) / 7 HIGH verified — self-referential calc shows wrong numbers, multi-frame pipelines 500 on every trace, warm click 3–6× the 10 ms budget; all polars-P03 trace items re-verified still open; one reported HIGH refuted in verification |
| [io-nodes](io-nodes/README.md) | 2026-07-06, HEAD `aca58177` | findings ready, fixes not started | Good bones, dishonest edges; 5 HIGH / ~14 MEDIUM verified — OUTPUT drops null-headed fields (repro'd), apiInput infer⇒build dead-end, picker advertises unreadable `.xml` / hides `.jsonl`, hand-edits to baked source paths silently discarded, no dtype UI blocks CSV deploys; format-registry design for jsonl/IPC/xlsx/XML in IO12 |

<!-- Add future review areas as sibling folders (e.g. frontend/, git-panel/, deploy/) with the same
     structure, and a row here. -->
