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
| SBX-R02 | Decision | P3 | The node-code guard matches the trusted-code decision. |

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
`HTTPException` from a helper. `safe_path` and the JSON-cache publication code
check symlinks and Windows junctions themselves, and the save service splits
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
`src/haute/_json_shred/_publication.py`;
`src/haute/routes/_save_pipeline.py::_validate_output_rel_path`;
`tests/test_path_traversal_fixes.py`.

### SBX-R02 — The node-code guard after the trusted-code decision
**Why:** Since the 6 September 2026 decision that project code is trusted, the
AST denylist and restricted builtins for node code no longer protect
anything: the specification notes that Polars' own module graph reaches the
operating system. They still reject legitimate code: class definitions,
`global` and `nonlocal`, and calls to `getattr`, `type` and `vars`.

**Plan:** Decide what the guard is for. If it is an accident guard, keep only
checks for mistakes that fail confusingly (for example, a bare `open` of a
project file) and allow ordinary Python. Keep the exact pickle and joblib
allowlist, which protects against untrusted model artifacts.

**Acceptance:** The sandbox specification states what the node-code guard
rejects and why; code using classes, `type` or `getattr` runs in a node; the
pickle allowlist tests are unchanged.

**Dependencies:** None.

**Evidence:** `src/haute/_sandbox.py::validate_user_code`;
`src/haute/_sandbox.py::safe_globals`; `src/haute/_sandbox.py::_BLOCKED_CALLS`;
`tests/test_node_code_trust_boundary.py`.
