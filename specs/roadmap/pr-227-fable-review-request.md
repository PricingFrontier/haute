# Independent Fable 5.1 review request

The user explicitly requested an independent review by Fable 5.1 of the cache
and pipeline assessment and its proposed architecture. This authorises this
review despite the repository's normal restriction on delegating review
judgments. Do not spawn other agents or substitute another model.

Workspace: `C:/Users/prici/haute`.
PR: https://github.com/PricingFrontier/haute/pull/227.
Reviewed head: `97f3e9909998460bae5dbdfedf821eb9189581a6`.
Actual PR base: `a71c947000e46f3b9331e96ec1a034eaa3739c97`.
Local main is stale and must not be used as the PR baseline.

The user wants the most performant, robust and elegant pipeline/cache system,
with minimal RAM. Full resident data should be necessary only for modelling
and optimisation, and those representations should be minimal. They asked
whether the proposed plan will deliver a system of the absolute highest
standard. Independently assess that question; do not assume the prior review
is correct or endorse it merely because it is detailed.

Read these first:

- `specs/roadmap/pr-227-review.md`
- `specs/roadmap/pipeline-cache-memory-design.md`
- `specs/roadmap/pr-227-review-probes.py`
- `specs/roadmap/pr-227-frontend-probes.test.tsx`
- `specs/roadmap/pr-227-join-benchmark.py` and its `.json` results
- `specs/roadmap/pr-227-cache-evidence.md`
- `specs/roadmap/pr-227-materialisation-evidence.md`

Then inspect decisive actual source and existing specs/tests. Relevant areas:
`src/haute/_node_snapshots.py`, `_source_cache.py`, `_seed_plans.py`,
`_chunked_writes.py`, `_polars_utils.py`, `_data_points.py`, `_frame_profile.py`,
`_analysis_results.py`, `executor.py`, `_model_scorer.py`,
`routes/_training_preparation.py`, `routes/_optimiser_service.py`,
`modelling/_training_job.py`, `modelling/_algorithms.py`,
`frontend/src/panels/editors/banding/useBandingStats.ts`,
`frontend/src/panels/editors/rating/useRatingLevels.ts`,
`frontend/src/hooks/useNodeDataCache.ts`, and the caching/execution/IO specs.
Follow additional source only where it resolves a concrete review question.

Review objectives:

1. Validate, reject or qualify the prior findings R1–R8 with specific source
   references. Distinguish introduced defects, inherited defects, intentional
   specified behavior and unmet new requirements. Check whether reproductions
   reflect real callers rather than only artificial internal usage.
2. Assess the proposed overall approach: shared immutable Parquet generations,
   adaptive/spillable joins, typed plan capabilities, dataset handles,
   cross-process lifetime/commit protocol, resource admission, cache selection,
   analyses, and minimal training/optimiser allocations.
3. Identify better or simpler approaches and unnecessary complexity. Challenge
   proposals for a second execution engine or transactional metadata catalog.
   Do not imply one API/library guarantees bounded memory without evidence.
4. Identify missing requirements, incorrect assumptions, scalability limits,
   lifecycle/concurrency failure modes, and any important missed correctness
   issue. Explain the concrete trigger and practical effect of each finding.
5. Judge the benchmark's validity and limits, and whether the proposed
   acceptance gates would actually prove the user's requirements. Specify the
   smallest decisive experiments, not an indiscriminate full test matrix.
6. Give a prioritised recommendation: what must change in this plan, what
   should stay, what should be deferred, and what evidence is needed before
   implementation/merge. Be candid about unverified claims.

Execution boundaries:

- Read-only review. Do not edit, create, delete, stage, commit or publish files.
  The parent will save your final answer verbatim under `specs/roadmap`.
- Do not modify repository state or contact other people/services. Do not
  install tools, run model training or execute full CI/browser/perf suites.
- You may inspect files and use read-only git/ripgrep commands. Base the review
  on source, saved measurements and existing tests; label unexecuted hypotheses.
- Do not run commands that read secrets/auth files or private unrelated files.
- Preserve all existing user/parent changes. Do not spawn subagents.
- Produce the completed review rather than asking for permission to review.

Return a thorough Markdown report, starting with a clear verdict. Include a
compact R1–R8 adjudication table, prioritised findings/plan amendments with
file:line evidence, better alternatives and tradeoffs, a concrete sequencing
recommendation, and limitations. Separate confirmed defects from design
recommendations and untested hypotheses. Include source evidence sufficient
for the parent to verify your most important claims.
