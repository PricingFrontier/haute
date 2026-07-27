---
name: codex-code-review
description: Iterative Codex CLI code review of the current change set — the review gate for all code changes
argument-hint: "<target> [extra context] | reset <target> | show <target>"
---

# Codex Code Review

Iterative code review via Codex CLI on uncommitted changes. Codex runs `git status -s` / `git diff HEAD` to inspect the change set and reviews it against `.claude/skills/codex-code-review/checklist.md` (plus a design/context doc when the target is a path).

Review output stays in `state/<key>.review.txt`.

State persisted under `.claude/skills/codex-code-review/state/<sanitized-target>.{thread,review.txt,events.ndjson}`. Shared scripts live under `.claude/skills/codex-plan-review/scripts/`; always export before invoking:

```bash
export STATE_DIR=".claude/skills/codex-code-review/state"
```

## Arguments

- `<target>` — auto: start if no thread, resume if exists. A free-form label for the change, or a path to a design/context doc to review against.
- `reset <target>` — drop state, next call starts fresh.
- `show <target>` — display latest review without calling Codex.

## Execution

1. **Parse `$ARGUMENTS`**: extract action (`reset`/`show`/auto) and target.

2. **Auto** — try `start.sh` first (exit code 2 = thread exists -> use `resume.sh`):
   - **Start**: `bash .claude/skills/codex-plan-review/scripts/start.sh --prompt-file .claude/skills/codex-code-review/prompts/start.tpl <target> [extra]`
   - **Resume**: `bash .claude/skills/codex-plan-review/scripts/resume.sh --prompt-file .claude/skills/codex-code-review/prompts/resume.tpl <target> [extra]`

3. **Reset**: `bash .claude/skills/codex-plan-review/scripts/reset.sh <target>`

4. **Show**: `bash .claude/skills/codex-plan-review/scripts/show.sh <target>`

5. **Parse trailing tag**:
   - `APPROVED` — propose post-convergence steps.
   - `REQUEST_CHANGES` — surface review verbatim, engage critically (read actual code at `file:line`), then fix the legitimate findings — directly in the root, or via `sonnet-implementer` for a bounded batch per the CLAUDE.md delegation policy — and push back on incorrect ones via the resume `--notes`. Re-run the affected verification after the fixes, then resume.
   - `NEEDS_REWORK` — surface to user before mass-editing.

6. **Resume** after addressing findings for incremental re-review.

## Diff Visibility

Codex uses `git status -s` / `git diff HEAD` in read-only sandbox. If those fail, pass diff inline: `DIFF="$(git diff --stat HEAD; echo '---'; git diff HEAD)"` as extra context.

## After Convergence

Surface the final verdict to the user. The root owns the completion decision once findings are resolved or rebutted.

## Notes

- Model/effort defaults live in `codex-plan-review/scripts/_common.sh` (review → gpt-5.6-sol, effort xhigh; derived from `STATE_DIR`). Adjust that one file to your preferred models, or override per run via `CODEX_MODEL` / `CODEX_EFFORT` env vars; the scripts echo the effective values.
- `--sandbox read-only`. Safe to invoke autonomously.
- Thread IDs persisted per-target (no `--last`). Concurrent reviews don't collide.
- Separate `STATE_DIR` from `codex-plan-review` — same key is fine.
- Extra context -> `{{EXTRA_PROMPT}}`. Keep short.

## Loop Shape

```
turn 1: start.sh -> REQUEST_CHANGES (Critical: A, Major: B C)
         fix A B C; re-run affected verification
turn 2: resume.sh -> REQUEST_CHANGES (A B addressed, Minor: C partial, Suggestion: D)
         fix C, optionally D; re-run affected verification
turn 3: resume.sh -> APPROVED
```
