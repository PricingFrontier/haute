# Setup CI Reminder Hook

Add a PostToolUse hook to `.claude/settings.local.json` that reminds Claude to run all CI checks before claiming they pass.

## What it does

After every `Write` or `Edit` to a file under `src/`, `frontend/src/`, or `tests/`, the hook injects a context reminder listing every CI step from `.github/workflows/ci.yml`. This prevents Claude from claiming "CI will pass" without actually running all checks.

## Setup

Add this to `.claude/settings.local.json` (create the file if it doesn't exist). Merge with any existing settings — don't replace them.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python -c \"\nimport sys, json\nd = json.load(sys.stdin)\nf = d.get('tool_input',{}).get('file_path','') or d.get('tool_response',{}).get('filePath','')\nif '/src/' in f or '/frontend/src/' in f or '/tests/' in f:\n    msg = f'WARNING: File changed. Before claiming CI passes, run ALL checks from .github/workflows/ci.yml: (1) uv run ruff check src/ (2) uv run mypy src/haute/ (3) uv run pytest tests/ --cov=src/haute --cov-branch --cov-fail-under=85 [backend] AND (4) cd frontend && npm run build (5) npm run lint (6) npm test [frontend]. Do NOT claim CI will pass without running every step.'\n    json.dump({'hookSpecificOutput':{'hookEventName':'PostToolUse','additionalContext':msg}}, sys.stdout)\nelse:\n    print('{}')\n\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

## Why this exists

Claude repeatedly claimed CI would pass without running all checks — skipping mypy (caught a missing type annotation) and frontend tests (caught 2 failures from hiding the Frontier tab). The hook ensures the reminder is injected into context automatically, not reliant on memory.

## Verify it works

After adding, edit any file in `src/` and check that the warning appears in Claude's context. You can review active hooks via `/hooks`.
