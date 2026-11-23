All three previous findings are addressed:

- **P2.1:** [Store schema 4](/home/ralph/suite/haute/src/haute/modelling/_stage_store.py:55) excludes versions 2 and 3 before hydration. The regression test covers both.
- **P2.2:** [published_path](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:774) consistently controls the published draft and evidence fallback, while retained pruning operations still determine their own evidence.
- **P3:** [savedStageSettings](/home/ralph/suite/haute/frontend/src/modelling/stageSettings.ts:104) restores the disclosure, including pruning’s recorded path. The component test checks the displayed values.

Independent read-only probes passed for old-store response validation and banding, smoothing, pruning-identity and retained-pruning evidence selection. No new correctness regressions found. I reviewed the new tests; I did not rerun the full suites.

One non-blocking **P3** documentation correction: [low-level.md:3361](/home/ralph/suite/haute/specs/modelling/low-level.md:3361) still lists store schema 3; it should say 4.

No files were modified.

APPROVED