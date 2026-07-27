# Repository instructions

Read and follow `AGENTS.md` in this repository root. It is the authoritative source for engineering priorities, GitHub access, the fix-and-tweak workflow, and the verification ladder. Its "Model and delegation budget" section is written for the Codex CLI; in Claude Code sessions apply the mapping below instead. Do not layer older agent-pair, TRIP, or review-team rules on top of these files.

# Model and delegation budget (Claude Code)

One high-capability decision-maker, two cheap Claude execution tiers, and Codex for review.

The root session runs on Fable. Keep all work that requires judgment in the root thread: interpreting requirements and resolving ambiguity; planning, decomposition, architecture, and API design; root-cause analysis and debugging strategy; test strategy, edge-case selection, and acceptance criteria; security, correctness, and product-quality judgments; inspecting worker diffs; integration decisions, final verification, and the completion decision. For routine implementation sessions that need throughput more than judgment, `/model opus` halves root cost; return to Fable for judgment-heavy work.

Subagents are bounded execution workers, not additional decision-makers. A Claude Code subagent silently inherits the root model (Fable) unless a model is pinned — never allow that. Route every spawn through a pinned tier:

- **`haiku-worker`** (Haiku, low effort) — batches of clear, repeatable grunt work: multi-file inventories, related search batches, predefined command batches, log collection, deterministic mechanical edits, compact structured summaries.
- **`sonnet-implementer`** (Sonnet, low effort) — implementing a narrow change after the root has supplied the design, test cases, and acceptance criteria. For a tightly specified task that demonstrably needs more capability, spawn the same agent with the per-call override `model: "opus"`; effort stays pinned low.
- Built-in **`Explore`** agent with the per-call `model: "haiku"` override — read-only fan-out searches.

Give each worker a self-contained prompt with exact scope, inputs, constraints, expected output, and verification command. Workers must not spawn further agents. If a worker returns `NEEDS_ROOT_JUDGMENT`, the root resolves the judgment first and may then assign a still-bounded remainder.

Agent creation has a fixed context cost, and Fable delegates readily by default — resist it. Do not spawn a worker for a task the root can finish with one or a few direct tool calls. Batch related deterministic operations into one `haiku-worker` assignment, and spawn only when the batch removes a meaningful block of execution time. Normally use one worker; never exceed two concurrent subagents outside an explicitly requested workflow.

Do not set `CLAUDE_CODE_SUBAGENT_MODEL` (it overrides per-stage routing wholesale, including workflow stages that need a stronger model). Do not enable Fast mode for routine repository work.

# Review policy: Codex reviews, Claude never reviews Claude

Every review uses a Codex model — cross-family review catches the failure modes same-family review shares. There is no TRIP process. The review surface is exactly:

- Code / PR / diff review: `codex-code-review` (checklist and approval gate live inside that skill)
- Plan, design, or spec review: `codex-plan-review`
- Second opinion on a judgment call: `codex-ask` (advisory, never gating)

Do not delegate review to Claude subagents and do not run Claude-vs-Claude review workflows; when orchestrated work surfaces findings, the review pass over those findings goes to Codex. The root still inspects every worker diff itself before accepting it (that is verification, not review) and owns the completion decision after Codex findings are resolved or rebutted.

# Workflows

Dynamic workflows are opt-in ("use a workflow" / `ultracode`). When writing one, route every stage explicitly — no stage may default to the session model: `{model: "haiku", effort: "low"}` for mechanical fan-out, `{model: "sonnet", effort: "low"}` for bounded implementation, `{model: "opus", effort: "medium"}` only for a stage that demonstrably needs it. Keep the medium size guideline (under 15 agents) unless the task genuinely calls for more.
