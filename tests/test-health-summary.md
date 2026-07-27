# Test Health Summary

Generated deterministically from the live test-debt scanners, test-health policy, and mutation targets. Regenerate with `uv run python tests/test_test_debt.py --write-summary`.

| Signal | Count / max survivor rate | Owner | Review by | Enforcement/source |
| --- | ---: | --- | --- | --- |
| Backend skip/skipif | 47 | engineering-quality | 2026-10-25 | Backend AST scanner |
| Backend importorskip | 63 | engineering-quality | 2026-10-25 | Backend AST scanner |
| Backend xfail | 2 | engineering-quality | 2026-10-25 | Backend AST scanner |
| Backend flaky | 0 | engineering-quality | 2026-10-25 | Backend AST scanner (zero-budget fingerprint ratchet) |
| Frontend marker debt | 0 | frontend-shared | 2026-10-25 | Frontend source scanner |
| Playwright CI retries | 2 | frontend-canvas | 2026-10-25 | frontend/playwright.config.ts: process.env.CI ? 2 : 0 |
| Mutation `executor` | 15.00% | execution-engine | 2026-10-25 | mutation/targets.json max_survival_rate |
| Mutation `job-store` | 6.00% | background-jobs | 2026-10-25 | mutation/targets.json max_survival_rate |
| Mutation `json-cache` | 11.00% | json-shredding | 2026-10-25 | mutation/targets.json max_survival_rate |
| Mutation `json-shred` | 5.00% | json-shredding | 2026-10-25 | mutation/targets.json max_survival_rate |
| Mutation `jsonpath` | 4.00% | json-shredding | 2026-10-25 | mutation/targets.json max_survival_rate |
| Mutation `output-assembler` | 10.00% | output-assembly | 2026-10-25 | mutation/targets.json max_survival_rate |
| Mutation `path-resolution` | 5.00% | sandbox-security | 2026-10-25 | mutation/targets.json max_survival_rate |
| Mutation `registry` | 0.00% | pipeline-config | 2026-10-25 | mutation/targets.json max_survival_rate |
