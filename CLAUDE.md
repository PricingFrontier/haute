# Repository instructions

Read and follow `AGENTS.md` in this repository root. It is the authoritative source for engineering priorities, GitHub access, models and delegation, the fix-and-tweak workflow, targeted verification, and the steps before a pull request. Do not layer older agent-pair, TRIP, or review-team rules on top of these files.

# Model

Run Claude Code sessions on Opus 5.5 (`/model opus`); it is the best value for both judgment and implementation. Subagents inherit it; do not pin cheaper tiers or set `CLAUDE_CODE_SUBAGENT_MODEL`. When to delegate is in AGENTS.md's "Models and delegation". Do not enable Fast mode for routine repository work.

# Review policy: Codex reviews once per pull request

Reviews use a Codex model, because a review from another model family catches failures that a same-family review shares. There is no TRIP process. The review surface is exactly:

- Code / PR / diff review: `codex-code-review`, run once over the whole branch diff before the PR is opened or updated (AGENTS.md "Before a pull request"). Do not review each commit or package separately.
- Plan, design, or spec review: `codex-plan-review`
- Second opinion on a judgment call: `codex-ask` (advisory, never gating)

Do not delegate review to Claude subagents and do not run Claude-vs-Claude review workflows. The main session still inspects every subagent diff itself (that is verification, not review) and owns the completion decision after Codex findings are resolved or rebutted.

# Tests and CI

Run only the affected tests locally, as AGENTS.md's "Targeted verification" describes, and never the full backend or frontend suite unless the user asks; this applies to subagents too. Push and let CI run the full suite. Watch the PR's checks with a `Monitor` on `gh pr checks`, not a sleep loop. On red, read the failing job's log with `gh`, reproduce only that test locally if the cause is unclear, fix it, and push again until CI is green.

# Workflows

Dynamic workflows are opt-in ("use a workflow" / `ultracode`). Stages run on Opus 5.5; set effort per stage (`low` for mechanical fan-out, `medium` for bounded implementation). Keep the medium size guideline (under 15 agents) unless the task genuinely calls for more.
