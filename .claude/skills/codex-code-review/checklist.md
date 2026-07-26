# Code Review Checklist

This file is the **single source of truth** for code-review criteria. All code reviews run through `.claude/skills/codex-code-review` and apply the criteria below — referenced, not copied — so review standards cannot drift.

## Systematic Review Checklist

### 1. Functional Requirements

- [ ] Implementation logic matches requirements correctly
- [ ] Interface/API matches documented specifications
- [ ] Error scenarios handled with proper feedback
- [ ] Edge cases and boundary conditions validated

### 2. Code Quality

- [ ] Proper typing (no unjustified dynamic types; mypy-clean in `src/haute/`, tsc-clean in `frontend/`)
- [ ] DRY principle - no code duplication; shares existing functionality where it makes sense
- [ ] KISS principle - not unnecessarily complex
- [ ] Consistent, descriptive naming conventions
- [ ] Complex logic has explanatory comments (constraints the code can't show — not narration)
- [ ] Files/modules not excessively large
- [ ] Imports/includes organized, unused ones removed

### 3. Architectural Compliance

- [ ] Code follows established patterns from the `docs/specs/` component specs
- [ ] Implementation matches the change's spec deltas — no spec-code drift in either direction
- [ ] Proper separation of concerns
- [ ] Appropriate abstractions used
- [ ] Consistent with existing codebase style

### 4. Haute Design Philosophy

- [ ] Code is the source of truth: `.py` stays canonical, layout metadata in sidecar `.haute.json`, pipeline runnable without the GUI
- [ ] Single execution engine: no parallel parser/executor/codegen paths
- [ ] Same pipeline, every context: no live-mode vs batch-mode forks
- [ ] Polars-native, lazy by default: no premature `.collect()`, no pandas conversion
- [ ] Fail loud: no fallbacks or defaults that mask errors; no silently swallowed exceptions

### 5. Error Handling

- [ ] Errors are properly caught and handled — or deliberately propagated (fail-loud beats a wrong fallback)
- [ ] Error messages are clear and actionable
- [ ] No silent failures: empty catches, broad excepts masking real errors, error-shaped 200 responses
- [ ] Logging is appropriate (not too verbose, not silent)

### 6. Security (if applicable)

- [ ] Input validation implemented
- [ ] No sensitive data exposed
- [ ] Sandbox/write-guard boundaries respected (path resolution, write sandbox)
- [ ] No obvious vulnerabilities (e.g. SQL/expression injection through user-built pipelines)

### 7. Performance

- [ ] No obvious performance issues
- [ ] Resource cleanup implemented (no leaks)
- [ ] Appropriate data structures used
- [ ] No unnecessary operations in hot paths (executor, projection, lazy execution)

### 8. Test Quality

The reviewer audits tests as first-class code — a weak test is a finding, not a free pass.

- [ ] Tests assert **observable behaviour** (inputs → outputs / persisted effects), not internal wiring
- [ ] No tautological or implementation-mirroring assertions (a test that would pass against a buggy implementation is a finding)
- [ ] The spec's low-level.md **Testing** scenarios for the change are all covered; named edge cases and failure modes exercised
- [ ] Failure model is tested — where the spec says an error surfaces, a test asserts it surfaces (no test tolerates a silent fallback)
- [ ] Critical-path floor met: behaviour touching auth, deletion, persistence, sandbox/write-guards, or external request shape has at least one behavioural test
- [ ] No coverage gaming: no ignore comments, config exclusions, or lowered gates; any new `skip`/`skipif`/`xfail`/`importorskip` is registered in `tests/test_test_debt.py`
- [ ] Mock discipline: no mock tower larger than the assertions it supports where a real seam exists

---

## Issue Severity Classification

**Critical (Block Deployment)**:

- Security vulnerabilities
- Data corruption or silently wrong results
- Breaking API/interface changes
- Sandbox or auth bypasses

**Major (Require Immediate Fix)**:

- Incorrect business logic
- Significant performance degradation
- Missing error handling / silent failure paths
- Compilation/build errors

**Minor (Should Fix)**:

- Code style inconsistencies
- Missing documentation
- Code duplication
- Missing edge case handling

**Suggestions (Nice to Have)**:

- Performance optimizations
- Readability improvements
- Additional test coverage

---

## Review Completion Criteria (Approval Gate)

Minimum for approval:

- [ ] All functional requirements implemented
- [ ] No critical or major issues remaining
- [ ] Build/compilation successful
- [ ] Affected unit tests pass (the requester runs the verification ladder and reports the results)
- [ ] New logic has behavioral test coverage — coverage is never gamed (no ignore comments, no exclusions, no lowered gates; new skips/xfails must be deliberately registered in `tests/test_test_debt.py`)
- [ ] Documentation updated per project standards (affected `docs/specs/` pairs; `tests/test_docs_accuracy.py` drift guards pass)
