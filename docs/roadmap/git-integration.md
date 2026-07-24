# Git integration roadmap

## Scope

Git workflows safely manage repository state, history, remote operations, and
understandable editor feedback.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| GIT-G01–GIT-G03, GIT-G07–GIT-G08 | Active | P0 | Protect mutations, repository setup/lifecycle, and unsaved work. |
| GIT-G04–GIT-G06, GIT-G09–GIT-G14 | Active | P1 | Preserve history and make operations efficient and recoverable. |
| GIT-G15–GIT-G16 | Active | P2 | Keep documentation, fixtures, and polish aligned. |

## Planned improvements

### GIT-G01 — Repository mutation lock
**Why:** Concurrent requests can contend on Git state or report an orphaned save as successful.

**Plan:** Introduce a reentrant per-repository engine lock around every mutating operation while leaving reads concurrent.

**Acceptance:** Concurrent real-repository tests show serialized commits, no index-lock leak, and no lost successful save.

**Dependencies:** Precedes lifecycle and state-file changes.

**Evidence:** `src/haute/_git.py`; `tests/test_git.py`; `docs/specs/git-integration/high-level.md`.

### GIT-G02 — Unborn repository seeding
**Why:** Initial commits can sweep application state, credentials, and datasets into history.

**Plan:** Seed an unborn repository with an explicit safe path set and consistent ignore rules rather than `git add -A`.

**Acceptance:** Hand-initialised repository tests prove `.haute`, secrets, caches, and data remain untracked while intended files commit.

**Dependencies:** GIT-G01.

**Evidence:** `src/haute/_git.py`; `src/haute/cli.py`; `tests/test_git.py`.

### GIT-G03 — Pair lifecycle edges
**Why:** Active-pair deletion, switching, rollback, and fast-forward can leave adopted repositories inconsistent.

**Plan:** Resolve a safe fallback at operation time and make pair mutations transactional with compensating rollback.

**Acceptance:** Tests cover active default-pair deletion, rollback failure, branch switching, and partial fast-forward.

**Dependencies:** GIT-G01.

**Evidence:** `src/haute/_git.py`; `src/haute/_git_state.py`; `tests/test_git.py`.

### GIT-G04 — History integrity
**Why:** Tabbed messages corrupt parsed milestone rows and move can linearise external merges.

**Plan:** Use unambiguous Git field separators and preserve merge topology or reject unsafe move operations.

**Acceptance:** Tests retain tabbed messages/timestamps and verify merge-history invariants.

**Dependencies:** GIT-G01.

**Evidence:** `src/haute/_git.py`; `tests/test_git.py`.

### GIT-G05 — Version-label batching
**Why:** Milestone labels and context use one subprocess per history item.

**Plan:** Query labels and commit context in batched Git operations.

**Acceptance:** Structural tests assert bounded subprocess calls and unchanged labels/context.

**Dependencies:** None.

**Evidence:** `src/haute/_git.py`; `tests/test_git.py`.

### GIT-G06 — Working-branch batching
**Why:** Branch listing and panel refetches multiply Git subprocesses.

**Plan:** Batch branch metadata retrieval and remove duplicate client refetch chains.

**Acceptance:** Tests assert bounded backend calls and one client refresh per relevant event.

**Dependencies:** GIT-G05.

**Evidence:** `src/haute/_git.py`; `frontend/src`; `tests/test_git.py`.

### GIT-G07 — Backend error surfacing
**Why:** The client replaces actionable Git messages with generic HTTP text.

**Plan:** Preserve structured backend error detail through the Git client and relevant UI actions.

**Acceptance:** UI tests show protected-branch, ledger, duplicate-label, and dirty-state messages.

**Dependencies:** None.

**Evidence:** `frontend/src`; `src/haute/routes/git.py`; `frontend/src/**/*.test.tsx`.

### GIT-G08 — Dirty-switch guard
**Why:** Switching or creating-and-moving can discard unsaved editor work.

**Plan:** Detect unsaved graph edits before destructive navigation and require an explicit user decision.

**Acceptance:** UI tests cover cancel, discard, save-first, switch, and create-and-move flows.

**Dependencies:** GIT-G07.

**Evidence:** `frontend/src`; `frontend/src/**/*.test.tsx`; `docs/specs/git-integration/high-level.md`.

### GIT-G09 — Locale-independent errors
**Why:** Parsing translated Git prose makes expected failure handling unreliable.

**Plan:** Pin Git command locale and map known failures to typed domain errors.

**Acceptance:** Tests simulate recognised failures and retain safe sanitized unknown-error handling.

**Dependencies:** GIT-G01.

**Evidence:** `src/haute/_git.py`; `tests/test_git.py`.

### GIT-G10 — Status surface
**Why:** The unused status endpoint is fragile and has a login-name crash path.

**Plan:** Remove the dead route/client path, retaining only live-path fixes; reintroduce status only with a defined UI contract.

**Acceptance:** Route/client removal and live Git workflow tests pass without the endpoint.

**Dependencies:** GIT-G11.

**Evidence:** `src/haute/routes/git.py`; `src/haute/_git.py`; `frontend/src`; `tests/test_git.py`.

### GIT-G11 — Repository-state UX
**Why:** Non-repository, invalid, divergent, and detached states are hidden or mislabelled.

**Plan:** Surface distinct state labels, remediation, retry, and accurate branch context.

**Acceptance:** UI tests cover no-repository, invalid, divergent, detached, and retry states.

**Dependencies:** GIT-G07.

**Evidence:** `frontend/src`; `src/haute/routes/git.py`; `frontend/src/**/*.test.tsx`.

### GIT-G12 — Fetch off request paths
**Why:** Routine requests synchronously fetch remotes and inherit network latency.

**Plan:** Restrict fetches to deliberate operations or background refresh with explicit freshness state.

**Acceptance:** Tests prove listing/status paths do not fetch and intentional remote operations still refresh safely.

**Dependencies:** GIT-G01.

**Evidence:** `src/haute/_git.py`; `src/haute/routes/git.py`; `tests/test_git.py`.

### GIT-G13 — Show and compare robustness
**Why:** Whole-tree archives, temp cleanup, and parse failure make version views costly or misleading.

**Plan:** Limit extraction to needed artifacts, make cleanup Windows-safe, and return typed failure rather than an empty successful graph.

**Acceptance:** Tests cover large unneeded files, cleanup contention, malformed archives, and compatible tar handling.

**Dependencies:** GIT-G09.

**Evidence:** `src/haute/_git.py`; `src/haute/routes/git.py`; `tests/test_git.py`.

### GIT-G14 — Atomic state files
**Why:** Direct state-file writes can tear on crash or lose concurrent updates.

**Plan:** Use atomic replace plus the repository mutation lock for all read-modify-write state changes.

**Acceptance:** Failure-injection and concurrent-operation tests preserve valid state and both updates.

**Dependencies:** GIT-G01.

**Evidence:** `src/haute/_git_state.py`; `src/haute/_git.py`; `tests/test_git.py`.

### GIT-G15 — Documentation and fixture truth
**Why:** User promises and fixtures can diverge from supported Git behaviour.

**Plan:** Update live specifications and fixtures to match verified behaviour, deleting orphaned symbols.

**Acceptance:** Documentation examples execute against maintained fixtures and tests.

**Dependencies:** GIT-G02–GIT-G14.

**Evidence:** `docs/specs/git-integration/high-level.md`; `tests/test_git.py`; `tests/fixtures`.

### GIT-G16 — Verified polish
**Why:** Small UX, hygiene, test-gap, and CI improvements remain after behavioural work.

**Plan:** Land only individually verified low-risk improvements with focused regression tests.

**Acceptance:** Each selected change has a narrow test and no altered Git contract.

**Dependencies:** GIT-G01–GIT-G15 as applicable.

**Evidence:** `src/haute/_git.py`; `frontend/src`; `tests/test_git.py`.
