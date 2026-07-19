# TRIP Workflow Artifacts

Internal engineering material (mkdocs-excluded). Produced by the TRIP skills in `.claude/skills/` — a Plan → Implement → Release workflow where Claude (Fable) orchestrates, Codex Sol reviews plans and code, and Codex Luna implements batches. Adapted from [TRIP-workflow](https://github.com/PiLastDigit/TRIP-workflow).

- `plans/` — `F_x.y.z_<feature>.plan.md`, written by `/TRIP-1-plan`, reviewed by Codex before implementation
- `changelog/` — per-release notes `vx.y.z.md` plus the rolling `changelog_table.md`
- `code-review/` — `CR_vx.y.z.md`, the converged Codex code review promoted at release time
- `tests/` — optional test-session summaries from standalone `/TRIP-test` runs

The workflow is **spec-first**: feature designs are written into the `docs/specs/` component specs during planning (high-level.md for what/why, low-level.md for exact implementation detail), implementation builds to those deltas, and the release step reconciles specs with what was actually built. Plan files here only scope and order the work — the design itself always lives in `docs/specs/`.
