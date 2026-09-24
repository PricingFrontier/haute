# Sandbox security roadmap

## Scope

Path containment, the node-code guard, and restricted unpickling. Current
behaviour is specified in
[the sandbox-security specification](../sandbox-security/high-level.md).
These packages come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SBX-R01 | Planned | P3 | One path-containment check serves every caller. |

## Planned improvements

### SBX-R01 — One path-containment check
**Why:** Containment is implemented five times. The two general checks make
the same containment comparison: the route helper `validate_safe_path`
resolves both paths and uses `Path.is_relative_to`, and `validate_project_path`
resolves and compares `normcase`-folded paths with `commonpath`. Both compare
resolved paths, so `..` segments and symlinks are collapsed first and neither
lets an escape through, and `normcase` folds case only on Windows, where
`Path` comparison is already case-insensitive. `validate_project_path`'s
docstring nevertheless justifies its comparison with a case-variant bypass
that cannot happen for resolved paths. They differ in one guard:
`validate_safe_path` first refuses an absolute input that is lexically
outside the project, before resolving it, so an absolute path that only
resolves back inside (`<outside>/../<project>/file`) is refused there and
accepted by `validate_project_path`. `validate_safe_path` also raises an
`HTTPException` from a helper. `safe_path` and the file-lock helper's plain-path
checks check symlinks and Windows junctions themselves, and the save service splits
path parts to reject traversal on its own. No escape is known; the cost is
five places to keep a security check right.

**Plan:** Keep one containment function that resolves and then compares
common paths, with its case and symlink or reparse-point policy stated.
Decide whether the unresolved absolute-path guard stays, and state the
decision, so no former caller changes behaviour silently. The function raises
a domain error that the route layer maps to 400 or 403. Route every caller
through it and correct the docstring's rationale.

**Acceptance:** One containment implementation remains; under test, on every
former caller's route, a `..` escape and a symlink escape are rejected and an
in-project path is accepted; an absolute input that only resolves back inside
the project is treated as the specification states; the sandbox-security
specification states the case, link and absolute-input policy; the
path-traversal suites pass.

**Dependencies:** `API-R01` (server API) maps the domain error.

**Evidence:** `src/haute/routes/_helpers.py::validate_safe_path`;
`src/haute/_sandbox.py::validate_project_path`;
`src/haute/_artifact_paths.py::safe_path`;
`src/haute/_file_lock.py::_assert_path_ancestors_plain`;
`src/haute/routes/_save_pipeline.py::_validate_output_rel_path`;
`tests/test_path_traversal_fixes.py`.
