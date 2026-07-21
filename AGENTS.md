# Engineering priorities

- Prioritise user experience, correctness, maintainability, and engineering quality over quick fixes or band-aids.
- Build new functionality consistently with the existing codebase. Reuse and extend existing abstractions when that produces a clearer design.
- Do not add speculative or silent fallbacks. Let unexpected states fail clearly so the underlying defect can be found and fixed.
- Preserve existing user changes and avoid unrelated edits.

# Model and delegation budget

This repository uses one high-capability decision-maker and two cheaper execution tiers.

The root agent is expected to run on `gpt-5.6-sol` at `ultra`. Keep all work that requires judgment in the root thread, including:

- interpreting requirements and resolving ambiguity;
- planning, decomposition, architecture, and API design;
- root-cause analysis and debugging strategy;
- test strategy, edge-case selection, and acceptance criteria;
- security, correctness, and product-quality judgments;
- reviewing diffs and subagent results;
- integration decisions, final verification, and the completion decision.

Subagents are bounded execution workers, not additional decision-makers.

Use the project custom agent `luna_worker` for batches of clear, repeatable grunt work. Its definition in `.codex/agents/luna_worker.toml` pins `gpt-5.6-luna` at `low` reasoning and low verbosity. Suitable work includes multi-file inventories, related search batches, predefined command batches, log collection, deterministic transformations, and structured summaries. Invoke it by its custom agent name; do not override its model.

Use `gpt-5.6-terra` when a bounded worker needs stronger reasoning or tool use, especially for narrowly specified test or code implementation. Every direct `spawn_agent` call for a Terra worker must explicitly set:

- `model: "gpt-5.6-terra"`;
- `reasoning_effort: "low"` by default;
- `reasoning_effort: "medium"` only for a tightly specified implementation task that demonstrably needs more than low effort;
- `fork_turns: "none"`, or the smallest positive recent-turn count that supplies essential context. Never use `fork_turns: "all"` for a worker.

Give each worker a self-contained prompt with exact scope, inputs, constraints, expected output, and verification command. Workers must not spawn further agents.

Agent creation has a fixed context cost. Do not spawn a worker for a task the root can finish with one or a few direct tool calls. Batch related deterministic operations into one Luna assignment, and spawn only when that batch removes a meaningful block of execution time. If there is no meaningful batch, keep the work in the root thread.

Never allow a subagent to inherit the root model or reasoning effort. Do not use Sol, `high`, `xhigh`, `max`, or `ultra` for a subagent unless the user explicitly requests that exception. If `luna_worker` is unavailable or its Luna model cannot be verified, report that limitation and use an explicitly configured Terra/low worker; never claim that an inherited or unknown model is Luna. If the runtime cannot apply either worker configuration, keep the work in the root thread instead of spawning.

Do not enable Fast mode for routine repository work. Use it only when the user explicitly prioritises latency over credit consumption.

Delegate only independent, well-bounded work. Prefer Luna for:

- multi-file repository inventories and batches of related read-only searches;
- running predefined batches of tests, linters, type checks, formatters, or benchmarks;
- collecting and compactly summarising logs or command output;
- deterministic bulk or mechanical edits and transformations.

Use Terra for implementing a narrow change after the root has supplied the design, test cases, and acceptance criteria. If Luna reports `NEEDS_ROOT_JUDGMENT`, the root resolves the judgment first and may then assign a still-bounded implementation remainder to Terra.

Do not delegate planning, open-ended investigation, architecture, test design, ambiguous implementation, review, or final synthesis.

Use the fewest workers that materially reduce wall-clock time. Prefer direct tool use for a quick check, batch related grunt work into one worker, never assign duplicate work, normally use one worker, and never exceed two concurrent subagents. Do not create mandatory developer/reviewer pairs or review teams. This policy supersedes older repository plans that prescribe agent pairs or review teams unless the current user explicitly re-enables them.

# Fix and tweak workflow

1. Establish a narrow scope and preserve unrelated user changes. The root inspects the relevant code and defines expected behaviour, risks, acceptance criteria, and a verification strategy before delegating or editing.
2. For a bug, reproduce it with the smallest failing regression test before implementing the fix. For new behaviour, add the smallest non-overlapping tests that prove the acceptance criteria. Cover boundaries, invalid input, state transitions, concurrency, and past regressions only when relevant; prefer extending an existing test module or parameterisation over creating a redundant test matrix.
3. Keep small, clear fixes in the root thread. A Terra worker may implement already-specified tests or code. A Luna worker may perform a genuinely batched mechanical change whose exact transformation is already specified.
4. Work in tight red-green-refactor loops. Run the new or failing test first, make the smallest coherent implementation, rerun the targeted test, then clean up without broadening scope.
5. Inspect the actual diff and run the verification ladder below. The root must not accept a worker summary in place of reviewing its changes and evidence.
6. The root performs the final review for correctness, regressions, maintainability, consistency, and user experience. Work is complete only after the acceptance criteria are met and the relevant verification evidence is clean.

# Verification ladder

Run only the lowest sufficient level while iterating, then climb when the change's risk warrants it:

1. Run the single failing or newly added test.
2. Run the affected test module or nearest related tests, plus checks for touched files.
3. Run affected cross-stack contract, browser, concurrency, or integration tests only when the change crosses those boundaries.
4. Run the quick preflight for a wider local confidence check.
5. Run the full preflight once, near completion, for broad or high-risk changes and before pushing when practical. Do not rerun the full preflight after every edit; CI remains the final full compatibility, mutation, performance, and browser gate.

Useful commands:

- Targeted backend test: `uv run pytest tests/test_relevant.py::test_name -q`
- Targeted frontend test: `npm --prefix frontend test -- src/path/relevant.test.tsx`
- Touched Python files: `uv run ruff check <files>` and `uv run ruff format --check <files>`
- Affected backend typing: `uv run mypy src/haute/`
- Frontend static checks: `npm --prefix frontend run typecheck` and `npm --prefix frontend run lint`
- Quick preflight: `powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1 -Quick`
- Full preflight: `powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1`
