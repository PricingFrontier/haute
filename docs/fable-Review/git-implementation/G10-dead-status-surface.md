# G10 — The `/api/git/status` surface is dead, v0-flavoured, and fragile — delete it (or rebuild it honestly)

**Severity: MEDIUM · Confidence: CONFIRMED · Class: dead surface + latent crashers**
**Files: `src/haute/_git.py`, `src/haute/routes/git.py`, `frontend/src/api/client.ts`, `src/haute/schemas.py`, contract fixtures**
**Origin: E-3, E-13 (engine reviewer), P-9 (perf reviewer: "no production caller"), T-2, T-3 (tests reviewer), primary-read candidates #2/#3/#29.**

## The facts

1. **No production consumer.** `getGitStatus` (`client.ts:1217`) and `GET /api/git/status` are
   referenced only by the client function itself and tests; no component calls them (grep-verified
   by two reviewers independently). The live UI reads `GET /api/git/working-branch`
   (`useGitStore.loadStatus`).
2. **v0 semantics.** `is_read_only = is_main or (not _is_own_branch(branch, user_slug) …)`
   (`_git.py:469`) keys off the v0 `pricing/<user>/` prefix (`_git.py:398-400`) that the v1
   arbitrary-name model never produces — every legit v1 working branch would read as read-only if
   anything consumed it.
3. **Garbled parsing.** `changed_files` parses `git status --porcelain` with
   `line[3:].strip().strip('"')` (`_git.py:472-478`): a rename becomes the literal
   `"old.txt -> new.txt"`, and non-ASCII paths stay octal-escaped (`caf\303\251.py`) because only
   the quotes are stripped (reproduced; `_parse_ledger_saves` does this correctly with
   `core.quotepath=false`, `_git.py:1724-1726`). The same `line[3:]` parse in `commit_save`
   (`_git.py:742`) only feeds the auto-generated commit message (cosmetic there).
4. **Latent crasher on the live path too.** `_get_user_slug` falls back to `os.getlogin()`
   (`_git.py:339`), which raises `OSError` with no controlling terminal (daemonised server,
   containers). Reachable in production via `working_branch_status` → `_eligible_working_branches`
   → `list_branches` → `_get_user_slug` — and **only when `git config user.name` is unset**, i.e.
   exactly the first-run state → `GET /api/git/working-branch` 500s on a headless host at the one
   moment the startup modal needs it.
5. It also carries GET side effects (throttled fetch + `rev-list` per call — R-8) for nobody.

## Fix design (recommended: delete)

- Delete the route `git_status` (`routes/git.py:126-135`), the engine `get_status`, the client
  `getGitStatus`, `GitStatusResponse` (schema + TS type), and the `git_status_response.json`
  contract fixture (coordinate with G14's fixture sweep). `list_branches` keeps its other
  consumers (`_eligible_working_branches`, `working_branches`).
- Fold the one useful idea (`main_ahead` badge) into a future deliberate surface only if the UI
  ever wants it — do not keep the endpoint "just in case" (project rule: no speculative surface).
- **Keep and fix the live-path pieces regardless of the delete:**
  - `_get_user_slug`: replace `os.getlogin()` with `getpass.getuser()` (already the in-repo idiom
    at `deploy/_utils.py:18`), final fallback `"user"` — a slug is always derivable; never let a
    slug lookup 500 the readiness signal.
  - If any porcelain parse remains anywhere, it must use `-z` (NUL separation, no quoting) or
    `core.quotepath=false` + rename-aware parsing.

Alternative (only if the team wants the endpoint): rebuild `is_read_only` on the v1 model
(`branch_category` + recorded working branch), parse porcelain with `-z`, and give it a consumer.
Do not ship it dead again.

## TDD plan

1. Route-absence test: `GET /api/git/status` → 404 JSON (same style as the existing
   `test_unregistered_api_git_history_returns_404_json`).
2. `test_user_slug_headless_without_identity` — monkeypatch `_run_git_ok("config","user.name")` →
   unset and `os.getlogin` to raise `OSError`; assert `working_branch_status` still returns
   (slug derived via `getpass.getuser()`), no exception. (T-3)
3. If the endpoint is kept instead: the two T-2 real-git tests — staged rename → `changed_files`
   holds the new path (no `" -> "`); `café.py` → decoded path, not octal escapes.
4. Grep-guard: contract test asserting no orphan `GitStatusResponse` remains in schemas/TS after
   deletion.

## Notes

Deletion is the highest-quality outcome per the project's dead-code rule ("raise coverage with
behavioural tests or delete dead code"). The `getpass.getuser()` fix is live-path and must land
whichever branch is taken.
