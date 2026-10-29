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
| SBX-R01 | Planned | P3 | Every containment comparison goes through the one check. |

## Planned improvements

`SBX-R01` touches files that most other packages also change, so it is
cheapest after them.

### SBX-R01 — Every containment comparison goes through the one check
**Why:** `_sandbox.contained_path` is the one containment check, with its
case, link and absolute-input policy stated in the sandbox-security
specification. The route inputs (the former route helper), the check before
deserialising, recovery artifact paths, the save service's codegen output
paths, SQLite locators and the MLflow settings write target all use it, and
`tests/test_path_containment.py` pins them. About forty other comparisons of
a path against a root still call `Path.is_relative_to` themselves, most in
modules other work owns: executor output staging, config sidecar paths, the
runtime path resolution, the recovery and repair document paths, the
pipeline revision, submodel paths, worker artifacts, the model scorer, the
MLflow artifact cache, model export and candidate runs, the deploy config,
optimiser artifacts, the static-file and watcher paths in the server, the
save service's pipeline-root checks, and the assistant. Some of them are
containment checks and some are a different predicate (classifying a path
already known to be resolved, or a lexical check on purpose).

**Plan:** For each remaining comparison, either route it through
`contained_path`, keeping the caller's own error contract, or mark it as a
different predicate with a one-line reason. The cache's file-lock helper
(`_file_lock._assert_path_ancestors_plain`) is a link policy, not a
containment comparison, and stays.

**Acceptance:** Outside `contained_path`, every `is_relative_to` or
`commonpath` comparison of a path against a root is either gone or marked as
a different predicate; each converted caller has a `..`-escape and a
symlink-escape test.

**Dependencies:** None, but most sites are in files the execution, caching,
optimiser and assistant work own.

**Evidence:** `src/haute/_sandbox.py::contained_path`;
`src/haute/executor.py`; `src/haute/_config_io.py`;
`src/haute/_path_resolution.py`; `src/haute/_pipeline_recovery.py`;
`src/haute/_pipeline_repair.py`; `src/haute/_worker_protocol.py`;
`src/haute/server.py`; `src/haute/assistant/_project_knowledge.py`.
