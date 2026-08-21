# Test Health Summary

Generated deterministically from the live test-debt scanners and mutation targets. Regenerate with `uv run python tests/test_test_debt.py --write-summary`.

| Signal | Count / max survivor rate | Enforcement/source |
| --- | ---: | --- |
| Backend skip/skipif | 66 | Backend AST scanner |
| Backend importorskip | 63 | Backend AST scanner |
| Backend xfail | 1 | Backend AST scanner |
| Backend flaky | 0 | Backend AST scanner (zero-budget fingerprint ratchet) |
| Frontend marker debt | 1 | Frontend source scanner |
| Playwright CI retries | 2 | frontend/playwright.config.ts: process.env.CI ? 2 : 0 |
| Mutation `executor` | 15.00% | mutation/targets.json max_survival_rate |
| Mutation `job-store` | 6.00% | mutation/targets.json max_survival_rate |
| Mutation `json-cache` | 11.00% | mutation/targets.json max_survival_rate |
| Mutation `json-shred` | 5.00% | mutation/targets.json max_survival_rate |
| Mutation `jsonpath` | 4.00% | mutation/targets.json max_survival_rate |
| Mutation `output-assembler` | 10.00% | mutation/targets.json max_survival_rate |
| Mutation `path-resolution` | 5.00% | mutation/targets.json max_survival_rate |
| Mutation `registry` | 0.00% | mutation/targets.json max_survival_rate |
