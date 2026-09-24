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
2. Have Codex review the whole branch diff once ("Code review with Codex" below).
   Resolve or rebut each finding, rerun only the tests the fixes touch, and
   resume the same review thread to confirm. Do not review each commit or
   package separately.
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

# Code review with Codex

Reviews use Codex, a different model family from the author, in a read-only
sandbox: `gpt-6-astra` at `xhigh` effort. Keep review state (event log, thread id,
review text) in a scratch directory outside the repository.

- **Start:** `codex exec --json --sandbox read-only --skip-git-repo-check -c model=gpt-6-astra -c model_reasoning_effort=xhigh "<prompt>" < /dev/null > review.ndjson`.
  The thread id is in the `thread.started` event; the review is the text of the
  last `item.completed` event whose item type is `agent_message`.
- **Resume after fixes:** `codex exec --sandbox read-only -c model=gpt-6-astra -c model_reasoning_effort=xhigh resume <thread-id> --json --skip-git-repo-check "<prompt>" < /dev/null`.
  Say what changed and why any finding was rebutted, and ask Codex to state for
  each prior finding whether it is addressed, flag new issues, and not re-flag
  a finding rebutted with a reason.
- **The prompt** names the diff (`git diff origin/main...HEAD` for a branch),
  the intent or package entries, the specifications changed, and the
  verification already run with its results. It asks for a review against the
  checklist below, citing `file:line`, with a severity for each finding and one
  final tag on its own line: `APPROVED`, `REQUEST_CHANGES`, or `NEEDS_REWORK`
  (structural problems).

Review priorities, in order: correctness (wrong results, data loss, silent
failure); security and safety; conformance to the changed specifications, where
drift between spec and code in either direction is a finding; test quality;
practical concerns (performance on real inputs, messages a user can act on).
Not priorities: documentation compliance for its own sake when the change
updates the document, environment limitations, annotation style beyond what
mypy and tsc require, theoretical edge cases real inputs do not produce, and
repeating a finding already addressed or rebutted.

Checklist:

1. **Function:** the logic matches the requirements and specifications; error
   scenarios and edge cases are handled.
2. **Code quality:** typed (mypy-clean in `src/haute/`, tsc-clean in `frontend/`);
   no duplication, and existing helpers are reused; no needless complexity;
   clear names; comments explain constraints rather than narrate; no unused
   imports; no oversized modules.
3. **Architecture:** follows the patterns in the `specs/` component
   specifications; the code and the specification deltas agree; concerns are
   separated.
4. **Haute design:** the `.py` file is canonical, with layout in the `.haute.json`
   sidecar and pipelines runnable without the GUI; one parser, executor and
   codegen path; the same pipeline in every context; Polars-native and lazy (no
   premature `.collect()`, no pandas); fail loud, with no fallback or default
   that masks an error.
5. **Error handling:** deliberate propagation beats a wrong fallback; messages
   are clear and actionable; no empty catches, broad excepts that hide real
   errors, or error-shaped 200 responses; logging is neither noisy nor silent.
6. **Security:** input is validated; no sensitive data is exposed; path
   resolution and the write sandbox are respected; user-built pipelines cannot
   inject SQL or expressions.
7. **Performance:** resources are cleaned up; data structures fit the job;
   nothing unnecessary runs in hot paths (executor, projection, lazy execution).
8. **Tests, reviewed as code:** they assert observable behaviour rather than
   wiring, and a test that would pass against a buggy implementation is a Major
   finding; the specification's Testing scenarios and failure modes are covered;
   behaviour touching authentication, deletion, persistence, the sandbox, or an
   external request shape has a behavioural test; no coverage gaming (ignore
   comments, exclusions, lowered gates), and a new skip or xfail is registered in
   `tests/test_test_debt.py`; no tower of mocks where a real seam exists.

Severity: **Critical** blocks a merge (security hole, data corruption or silently
wrong results, breaking interface change, sandbox or auth bypass); **Major** must
be fixed (wrong logic, significant slowdown, missing error handling or a silent
failure path, build errors); **Minor** should be fixed (style, missing
documentation, duplication, a missed edge case); **Suggestion** is optional.

Approval gate: the requirements are implemented; no Critical or Major finding is
open; the build passes; the affected tests pass; new logic has behavioural
tests; and the affected specifications are updated, with
`tests/test_docs_accuracy.py` passing.

Handling a review: read the code at each cited `file:line`; fix the legitimate
findings; rebut an incorrect one with the reason; weigh each finding's cost
against its risk and push back on one that grows the scope without matching
risk. Rerun only the tests the fixes touch, then resume the thread once. Bring
`NEEDS_REWORK` to the user before making large changes.

**Plan or specification review** uses the same command. Codex reviews the
specification deltas (`git diff origin/main -- specs/`) together with the plan
for correctness (fail loud, one execution engine, Polars lazy), whether a
developer can build it from the low-level specifications without guessing,
whether an engineer independent of the implementer could write the failing tests
from the Testing scenarios, agreement between the high-level and low-level
specifications, and practical risks. Findings are tagged P1 (blocks
implementation) or P2, and the review ends with a tag.

**A second opinion** on a design choice, a stuck bug, or a conclusion about to be
presented also uses the same command. The prompt gives the question and your
draft position and asks Codex to disagree where warranted, separating what it
verified in the repository from what it inferred, and to end with a short bottom
line. It is advisory: no tags, and nothing is gated on the answer. Don't use it
for a question only the user can answer. When Codex disagrees, tell the user.
