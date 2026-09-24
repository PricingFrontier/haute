# Engineering priorities

- Prioritise user experience, correctness, maintainability, and engineering quality over quick fixes or band-aids.
- Build new functionality consistently with the existing codebase. Reuse and extend existing abstractions when that produces a clearer design.
- Do not add speculative or silent fallbacks. Let unexpected states fail clearly so the underlying defect can be found and fixed.
- Preserve existing user changes and avoid unrelated edits.
- Make sure changes to functionality are defined in specs before changing code.

# GitHub access

- In the managed sandbox, GitHub CLI credential storage may be inaccessible and
  `gh auth status` can falsely report that the active token is invalid.
- Always run `gh auth status` and GitHub network operations outside the sandbox
  (using the normal escalation mechanism) before concluding that authentication
  is missing or invalid.
- If a sandboxed GitHub authentication check fails, immediately repeat it
  outside the sandbox. Do not ask the user to re-authenticate based only on the
  sandboxed result.

# Models and delegation

- Claude Code sessions run on Opus 5.5 for all work: judgment, implementation,
  and any subagent. There are no cheaper worker tiers to route through.
- Keep work in the main session. Spawn a subagent only for independent work
  that saves meaningful wall-clock time, such as a broad read-only search. Do
  not spawn one for a task a few direct tool calls can finish.
- Give a subagent a self-contained prompt: exact scope, inputs, constraints,
  expected output, and verification command. Subagents do not spawn further
  agents, and never run more than two at once.
- The main session inspects every subagent diff and its evidence itself, and
  owns planning, test design, integration, and the completion decision.
- Do not enable Fast mode for routine repository work. Use it only when the user
  explicitly prioritises latency over cost.

# Gemini grunt work through the Antigravity CLI

When the user directs grunt work to Gemini, run it as bounded batches through
the Antigravity CLI instead of a Codex or Claude worker:
`agy -p "<prompt>" --model gemini-3.8-flash-high --effort high --mode plan
--print-timeout 10m`, or the Gemini 3.8 variant the user names. Inventories,
reference verification, and predefined command batches use read-only plan
mode; an implementation assignment runs with `--mode accept-edits`, names the
files it owns, and must preserve other contributors' changes. Each prompt is
self-contained: exact files, expected output format, constraints, and the
verification command. Workers do not spawn workers or decide architecture, test
oracles, acceptance criteria, or completion. The root reviews the actual source,
diffs, and execution evidence and spot-checks decisive claims itself. If Gemini
is unavailable, report it; do not fall back to another model silently.

# Fix and tweak workflow

1. Establish a narrow scope and preserve unrelated user changes. Inspect the relevant code and define expected behaviour, risks, acceptance criteria, and a verification strategy before editing.
2. For a bug, reproduce it with the smallest failing regression test before implementing the fix. For new behaviour, add the smallest non-overlapping tests that prove the acceptance criteria. Cover boundaries, invalid input, state transitions, concurrency, and past regressions only when relevant; prefer extending an existing test module or parameterisation over creating a redundant test matrix.
3. Keep fixes in the main session; delegate only as described in "Models and delegation".
4. Work in tight red-green-refactor loops. Run the new or failing test first, make the smallest coherent implementation, rerun the targeted test, then clean up without broadening scope.
5. Inspect the actual diff and run the affected tests and checks below once the change is complete. Never accept a subagent summary in place of reviewing its changes and evidence.
6. Before the PR, follow "Before a pull request" below. Work is complete only after the acceptance criteria are met, the review findings are resolved, and CI is green.

# Targeted verification

Run only the affected tests locally:

1. While fixing a bug or adding a test, run that single test.
2. When the change is complete, run the affected test modules and the checks for
   touched files once. Do not rerun them after every small edit, and do not widen
   the run to neighbouring modules "to be safe".
3. Run cross-stack contract, browser, concurrency, or integration tests only when
   the change crosses those boundaries.

Never run the full backend (`uv run pytest tests`) or frontend (`npm --prefix frontend test`)
suite locally unless the user asks for it. CI is the full compatibility, mutation,
performance, coverage, build, and browser gate.

# Before a pull request

1. Run the affected tests and checks for the whole change (above).
2. Have Codex review the whole branch diff once (`codex-code-review`). Resolve or
   rebut each finding, rerun only the tests the fixes touch, and resume the same
   review thread to confirm. Do not review each commit or package separately.
3. Open the PR, or push to the existing one, then watch its checks with `gh`
   until they finish. On a failure, read the failing job's log, reproduce only
   that test locally if the cause is unclear, fix it, and push again. Rerun an
   environmental flake with `gh run rerun <id> --failed` instead of pushing a
   change.

Useful commands:

- Targeted backend test: `uv run pytest tests/test_relevant.py::test_name -q`
- Targeted frontend test: `npm --prefix frontend test -- src/path/relevant.test.tsx`
- Touched Python files: `uv run ruff check <files>` and `uv run ruff format --check <files>`
- Affected backend typing: `uv run mypy src/haute/`
- Frontend static checks: `npm --prefix frontend run typecheck` and `npm --prefix frontend run lint`
