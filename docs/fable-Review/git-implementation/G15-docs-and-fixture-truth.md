# G15 — Documentation and fixture truth: README overclaims, orphan fixtures, doc-rot, dead symbols

**Severity: MEDIUM (README safety claims) / LOW (rest) · Confidence: CONFIRMED · Class: trust — the docs must not promise what the code doesn't do**
**Files: `README.md`, `tests/fixtures/ui_contracts/`, `src/haute/_git.py`, `frontend/src/components/BranchManager.tsx` (dialog copy)**
**Origin: U-3, U-4 (UX reviewer), T-9 (tests reviewer), E-14 (engine reviewer), primary-read candidate #14.**

## U-3 [MEDIUM] "Destructive actions create automatic backups" — not true

`delete_working_pair` (`_git.py:2585-2622`) deletes both refs (`branch -D` ×2) with a confirm
gate but **no** backup tag/ref; the discard path force-checkouts away dirty trees. The only
recovery is git's transient, unsurfaced, GC-eligible reflog. README:143 claims automatic backups —
a safety promise persona A may rely on when confirming.

**Fix (recommended: make the docs true, and sharpen the dialog).**
- README: replace the sentence with what IS true: *"Protected branches can't be overwritten.
  Archiving keeps a branch recoverable; deleting is permanent and always asks first. Switching
  between versions saves your current work first."* (the last clause becomes true again once G08
  lands — coordinate).
- Delete dialog (`BranchManager.tsx:417` area): add the steer —
  *"This can't be undone. To keep a recoverable copy, Archive instead."*
- Alternative (bigger, only if the team prefers behaviour over copy): make delete
  archive-with-timestamp under the hood. Do **not** do both; pick one and keep docs exact.

## U-4 [MEDIUM] "Submit your work for review" — no such surface exists

README:141 promises it; there is no submit endpoint (`routes/git.py` grep), no frontend surface.
The orphan fixture `tests/fixtures/ui_contracts/git_submit_response.json`
(`{compare_url, branch, pushed, push_error}`) is consumed by nothing and describes a
push-and-open-PR flow that was evidently designed and then dropped.

**Fix.** Either (a) drop the README clause and delete the fixture, or (b) build the small honest
version the fixture sketches: after a successful push, surface the forge compare/PR URL as
**"Open a review"** (derivable from the remote URL for GitHub/GitLab/Azure DevOps; hide the link
for unknown hosts). (b) is genuinely useful and small — but it is a product call; the review only
requires that the README and the code agree.

## T-9 [LOW] Seven orphan `git_*` UI-contract fixtures for routes that no longer exist

Referenced by NO test (both contract suites validate only `git_status_response`,
`git_archive_response`, `git_delete_branch_response`):

`git_create_branch_response.json` · `git_history_response.json` · `git_pull_response.json` ·
`git_revert_response.json` · `git_save_response.json` · `git_submit_response.json` ·
`git_switch_branch_response.json`

They document a removed v0 API (`/history`, `/revert`, `/pull`, `/save`, `/switch-branch`…) —
`test_routes_error_handling.py` even asserts `/history` is gone. **Delete all seven.** If G10
deletes `/status`, retire `git_status_response.json` in the same sweep.

## E-14 [LOW] Doc-rot and dead symbols in `_git.py`

- Module docstring (`_git.py:8`): "Backup safety nets — tag before destructive operations
  (revert)" — no such code exists (v0 leftover; same untruth as U-3, but aimed at maintainers).
  Rewrite the docstring bullet to describe the real safety model: guardrails, pair archiving,
  never-force-push, move-not-revert.
- `PROTECTED_BRANCHES = DEFAULT_PROTECTED_BRANCHES` (`_git.py:63`) — unused; delete.
- `ensure_repo` (`_git.py:454`) — zero callers in `src/`; delete (or mark as deliberate public
  API with a test if the CLI wants it).
- `_ahead_behind` (`_git.py:1895`, "kept for back-compat") — zero callers; delete.
- `_generate_commit_message`'s `"config" in str(p)` (`_git.py:433`) matches any path containing
  the substring anywhere (`myconfigs/x.json` → `config/x`); tighten to a real path-part check —
  cosmetic, do it in passing.

## TDD plan

1. Fixture sweep: after deletion, a repo-wide grep-guard test (or just the existing contract
   suites) proves nothing references the removed fixtures; `pytest tests/ -k contract` green.
2. If (b) is chosen for U-4: route/UI tests for the compare-URL derivation per forge + the
   unknown-host fallback (no link, no error).
3. Dead-symbol removal: `ruff`/`mypy`/full suite green; grep-guard that `ensure_repo`,
   `_ahead_behind`, `PROTECTED_BRANCHES` are gone.
4. README changes: proof-read against the shipped behaviour of G08 (save-first switching) before
   merging — the sentence must be true *at merge time*, not aspirationally.

## Notes

Mechanical batch; single batch reviewer fine. The README edits are the user-trust piece — treat
their wording as part of the safety surface, not marketing copy.
