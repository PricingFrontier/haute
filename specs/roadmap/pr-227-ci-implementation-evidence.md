# PR #227 CI implementation evidence

Evidence inventory collected 2026-09-22 for head `97f3e9909998460bae5dbdfedf821eb9189581a6` (runs `35711247240`, `35711247275`). Paths below are workspace-relative unless marked absolute. No source or test files were changed.

## Browser job 106692105593

Job URL: https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105593

The log is saved at `.tmp/pr227-ci-evidence/browser-job.log`. The canvas scenario is `frontend/e2e/canvas-assurance.spec.ts:146` (`discovers mixed Banding factors and rebuilds, edits, and reloads a three-factor Rating table by keyboard`). The screenshot assertion is at line 67 and is called at line 182. The reported mismatch is 13,392 pixels, ratio 0.03, against maxDiffPixelRatio 0.02.

Exact screenshot artifacts:

- expected: `.tmp/pr227-ci-evidence/browser-artifact/test-results/canvas-assurance-frontend--9a39b-or-Rating-table-by-keyboard-chromium-retry2/mixed-banding-desktop-1440x900-linux-expected.png`
- actual: `.tmp/pr227-ci-evidence/browser-artifact/test-results/canvas-assurance-frontend--9a39b-or-Rating-table-by-keyboard-chromium-retry2/mixed-banding-desktop-1440x900-linux-actual.png`
- diff: `.tmp/pr227-ci-evidence/browser-artifact/test-results/canvas-assurance-frontend--9a39b-or-Rating-table-by-keyboard-chromium-retry2/mixed-banding-desktop-1440x900-linux-diff.png`

The Explore failure is `frontend/e2e/explore.spec.ts:50` (`uses cached Explore schema and formats a Pivot before its ordinary preview resolves`). At line 190, the locator assertion for region `Pivot 1` and its table timed out after 60 seconds. Error context and trace are at `.tmp/pr227-ci-evidence/browser-artifact/test-results/explore-Explore-cached-fie-b353f-s-ordinary-preview-resolves-chromium-retry1/error-context.md` and `.tmp/pr227-ci-evidence/browser-artifact/test-results/explore-Explore-cached-fie-b353f-s-ordinary-preview-resolves-chromium-retry1/trace.zip`.

## Frontend bundle job 106692105151

Job URL: https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105151

The log is saved at `.tmp/pr227-ci-evidence/frontend-job.log`. The bundle budget implementation and defaults are in `frontend/scripts/check-bundle-size.mjs:127-128` (default initial JS gzip budget 292 KiB) and `:398-404` (environment override and budget object). The gate is `:354-359` and the report is `:452-453`. The log reports initial entries: `index-DlQwXplu.js` 167.6 KiB gzip, `vendor-react-DUYQZtkj.js` 117.3 KiB, `vendor-ui-BYumvjMG.js` 7.8 KiB; initial total 292.8 KiB against 292 KiB. The build then reports `FAIL Frontend bundle budget`. Other logged stages (build, benchmark, typecheck, lint, tests) passed.

## Mutation job 106707678222

Job URL: https://github.com/PricingFrontier/haute/actions/runs/35711247275/job/106707678222

The job log is `.tmp/pr227-ci-evidence/mutation-job.log`; the downloaded final artifact is `.tmp/pr227-ci-evidence/mutation-final/`. Summary: registry 2 survivors / 1.82% (threshold 0.00%); json-cache 61 / 12.18% (threshold 11.00%); executor 19.39% (threshold 15.00%). Full reports:

- `.tmp/pr227-ci-evidence/mutation-final/cosmic-ray-registry/report.txt` — survivors include `_registry.py core/ReplaceTrueWithFalse 3` (job `d469eeccd26c40368ec511475d5aee7b`) and `4` (`a90fe5d1b3144b938525819357706258`).
- `.tmp/pr227-ci-evidence/mutation-final/cosmic-ray-json-cache/report.txt` — survivors include `json_cache.py core/ReplaceBinaryOperator_Sub_Add 2` (`3cbc3d1f5f404d45a38d01ac32d591bf`), `Sub_Mul 2` (`1254842bccea483bb34fa3a9c2e9bf97`), and `Sub_Div 2` (`63e0fa9c950347f0b29ac565d0582be0`).
- `.tmp/pr227-ci-evidence/mutation-final/cosmic-ray-executor/report.txt` — surviving entries include `executor.py core/ReplaceBinaryOperator_Sub_BitOr 1` (`f981cb53d23c4a6a91945e59f8deaed7`), `Sub_BitAnd 1` (`01d2dd56d73d45749749c3a30e910bab`), and `Sub_BitXor 1` (`80708c87542e427087c1f3529f65e25f`).

## Workflow ledger and matching tests

`tests/workflow_coverage.toml:999` defines `W10-S02`; its listed ExplorePreview title at `:1012` is `renders the shared cache button state of the data it reads`. The current matching test in `frontend/src/panels/__tests__/ExplorePreview.test.tsx:573` is `renders the shared cache state of the data it reads`. The same ledger entry lists additional titles at `:1013-1015`; the corresponding test file contains the current tests and no exact title match for the old `button` wording.

## Artifact manifest

Artifact metadata: `.tmp/pr227-ci-evidence/run-35711247240-artifacts.json`, `.tmp/pr227-ci-evidence/run-35711247275-artifacts.json`; browser artifact ID `10686277844`; mutation final artifact ID `10688184849`. Absolute screenshot paths for direct inspection are:

`C:\Users\prici\haute\.tmp\pr227-ci-evidence\browser-artifact\test-results\canvas-assurance-frontend--9a39b-or-Rating-table-by-keyboard-chromium-retry2\mixed-banding-desktop-1440x900-linux-actual.png`

`C:\Users\prici\haute\.tmp\pr227-ci-evidence\browser-artifact\test-results\canvas-assurance-frontend--9a39b-or-Rating-table-by-keyboard-chromium-retry2\mixed-banding-desktop-1440x900-linux-diff.png`
