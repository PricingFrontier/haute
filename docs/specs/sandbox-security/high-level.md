# Sandbox Security — High-Level Specification

## Purpose

Haute pipelines run user-authored Python (polars transform nodes, preamble/utility
code, training scripts) inside the same process as the local editor server, and the
editor loads user-supplied model/data artifacts (pickle, joblib) from disk. Both are
attacker-shaped inputs even in a single-user local tool: a pipeline file can be
copied between machines, shared, or opened from an untrusted source, and a model
artifact can be swapped out from under the project directory. This component is the
set of defences that keep "run whatever code/data the project directory contains"
from becoming "run whatever the file system contains" or "run whatever a hostile
browser tab can smuggle through localhost."

It covers three independent threat surfaces: (1) arbitrary code execution via
`exec()` of user pipeline/training code, (2) arbitrary code execution via
deserializing untrusted pickle/joblib model artifacts, and (3) a random web page
driving the local dev server through a user's browser (cross-site request/WebSocket
hijack against `localhost`). A fourth, narrower concern — keeping generated
`.gitignore` entries consistent so secrets and per-clone state never get committed —
is bundled in here because it is a small, self-contained guard module living beside
the others, not because it shares a threat model with the first three.

## Scope

In scope:
- AST-level static validation of user pipeline/training code before `exec()`
  (`validate_user_code`), and the restricted builtins namespace `exec()` runs
  against (`safe_globals`).
- The actual `exec()` call sites for pipeline node code and its namespace assembly
  (`_exec_user_code`).
- Restricted unpickling for both raw pickle files and joblib archives
  (`safe_unpickle`, `safe_joblib_load`), and the project-root path containment check
  used before touching any such file (`validate_project_path`).
- Local-session protection for the FastAPI/WebSocket server (`_local_security.py`):
  the per-process session token, trusted-Origin/trusted-Host checks, and the
  middleware/dependency wiring that enforces them.
- Lazy, fail-soft environment-variable parsing for numeric tuning knobs (`_env.py`)
  — included here because every timeout/limit that bounds sandboxed execution reads
  through it, and a knob that silently reverts to a wrong frozen value at import
  time is itself a security-relevant defect class (a limit that stops applying).
- The shared `.gitignore` guard-entry list and the idempotent writer
  (`_gitignore_guard.py`) that keeps two independent call sites from drifting.

Out of scope (owned elsewhere, linked where relevant):
- What the validated/exec'd code is allowed to *do* semantically (which node types
  exist, how a pipeline graph is compiled to code) — that is the
  [execution engine](../execution-engine/high-level.md).
- Parsing and evaluating the polars/pandas expression strings inside rating tables
  and banding rules — that's [expression-parsing](../expression-parsing/high-level.md);
  this component only gates raw Python source, not expression DSL text.
- Path-traversal guarding for HTTP route parameters (`validate_safe_path`,
  null-byte and URL-scheme rejection in file-browse/schema endpoints) — that lives
  in `routes/_helpers.py`, documented under
  [server-api](../server-api/high-level.md); this component's path guard
  (`validate_project_path`) is specifically the pre-deserialization check used by
  `safe_unpickle`/`safe_joblib_load`/`load_external_object`, not the general HTTP
  path-parameter guard.
- SQL-identifier and git-ref-name validation (`_TABLE_NAME_RE`,
  `_validate_ref_name`) — those live in the Databricks I/O and git-integration
  components respectively; they follow the same "reject, don't sanitize" posture
  but are separate allowlists over a different input shape.
- The CLI's own write-sandbox used by the test suite (`tests/_write_sandbox.py`)
  is test infrastructure, not part of the shipped security surface.

## Behaviour

- **Two independent layers gate `exec()`ed pipeline code**, and both must pass:
  a structural AST walk (`validate_user_code`) rejects known escape-shaped syntax
  *before* any code runs, and a restricted builtins/globals namespace
  (`safe_globals`) removes the dangerous callables at runtime as defence in depth
  even if a pattern slips past the AST layer. Neither layer alone is trusted to be
  complete.
- **The AST layer is allowlist-adjacent but implemented as a denylist of named
  escape primitives**: dunder attribute access to type-system/introspection
  dunders, frame/traceback/generator-frame attribute access, calls to reflection
  builtins (`getattr`, `type`, `vars`, `super`, …), `import`/`from import` (unless
  explicitly permitted), `class`/`async def` definitions, `global`/`nonlocal`, and
  `__builtins__[...]` subscripting. `.format()`/`.format_map()`/`.vformat()` calls
  are additionally restricted to a single statically-vettable string-literal
  template (with a narrow, name-shadow-aware carve-out for polars' own
  `pl.format(...)` builder), because runtime-assembled templates can smuggle dunder
  traversal past a literal-only scan.
- **Not every dunder is blocked** — only ones with a known escape or introspection
  use (`__class__`, `__globals__`, `__code__`, `__reduce__`, `__closure__`, …).
  Harmless dunders (`__init__`, `__name__`, `__doc__`, `__qualname__`,
  `__annotations__`) are left reachable; `__init__` is directly callable inside the
  sandbox (e.g. `x.__init__([4, 5])` on a list). Lambdas and nested lambdas are not
  specially restricted — their *bodies* are still walked and blocked the same as
  any other code, but the AST validator has no `visit_Lambda` gate of its own.
- **`allow_imports=True` is an explicit, narrow escape hatch**, used only for
  preamble/utility code and CLI training scripts that legitimately need to import
  project utility modules. It disables the AST import check and restores the real
  `__import__` in the exec namespace — meaning preamble content under this flag has
  full import privileges (`import os`, `import subprocess`, …). This is documented
  as an accepted trade-off, not a gap: preamble/training-script content is
  first-party project code the user already controls, not per-node pipeline
  expression text.
- **Validation results are cached per `(code, allow_imports)` pair** in a bounded
  LRU (`_validation_cache`, capped at 1024 entries) so a long-lived server
  previewing/tracing the same node repeatedly does not re-parse identical code.
  Code that fails to parse (`SyntaxError`) is never cached as safe; an evicted
  entry is transparently re-validated on next use, never assumed safe by omission.
- **Pickle/joblib loading uses an exact `(module, qualname)` two-tier allowlist**,
  not a package-prefix allowlist: one tier for vetted scaffolding *functions*
  (numpy array reconstruction, `copyreg` helpers, builtin container constructors),
  one tier for model/data *classes* that are resolved and then checked to actually
  be a `type` before being trusted. An allowlisted class entry that resolves to a
  non-class callable is rejected, not silently accepted. Every file passed to
  `safe_unpickle`/`safe_joblib_load` must first resolve inside the project root
  (`validate_project_path`) — a case-insensitive-filesystem-safe containment check,
  not a raw string-prefix check.
- **Local HTTP/WebSocket access to the dev server requires three things to align**:
  a trusted `Host` header (loopback names/addresses or an explicit allowlist via
  `HAUTE_TRUSTED_HOSTS`), a trusted `Origin` (absent origin, or loopback, or the
  same allowlist), and a per-process bearer session token compared with constant-time
  `hmac.compare_digest`. All three checks fail closed: a missing or malformed value
  is rejected, never treated as implicitly trusted. `OPTIONS` preflight requests
  skip only the token check, not the Host/Origin checks. The whole scheme can be
  disabled via `HAUTE_DISABLE_LOCAL_SESSION_AUTH` for advanced/CI use.
- **Numeric tuning knobs (timeouts, chunk sizes, history limits) are always read
  live from `os.environ` at call time**, never frozen at import, and a malformed
  value logs a warning and degrades to a documented default rather than raising —
  a bad knob must not crash server startup or the request path.
- **`.gitignore` guard entries are idempotent and additive**: re-running the guard
  writer never duplicates an entry, never removes user-authored lines, and treats
  non-UTF-8 existing content as replaceable bytes rather than raising.

## Design rationale

- **Defence in depth over a single gate.** The AST validator and the restricted
  builtins namespace independently block the same attack classes (e.g. `getattr`
  is both an AST-blocked call *and* absent from `safe_globals`'s builtins) so that
  a bug in one layer does not by itself grant an escape. Several code comments and
  test classes in this component are explicit "Gap N" write-ups of exactly this
  reasoning — a name reference to `getattr` passes the AST layer (only *calls* are
  blocked structurally), so the runtime layer additionally removes `getattr` from
  the builtins dict entirely.
- **Exact-symbol pickle allowlisting over package-prefix allowlisting.** An earlier
  design allowlisted whole trusted-looking module prefixes (`numpy.*`, `sklearn.*`).
  This was found unsafe: large ML libraries ship code-execution gadget functions
  somewhere in their tree (`numpy.testing._private.utils.runstring`,
  `pandas.core.computation.eval.eval`), and a prefix rule admits all of them
  alongside the legitimate scaffolding. The two-tier exact-pair allowlist accepts
  the ongoing maintenance cost (a new estimator class needs an explicit allowlist
  addition) in exchange for a genuine RCE boundary. Pickle payload *state* (forged
  `coef_` values inside an otherwise-allowlisted class) is explicitly out of scope
  — that is inherent to trusting a model file's data at all, distinct from
  preventing arbitrary code execution.
- **Reject, don't sanitize.** Table names, ref names, and code patterns are matched
  against an allowlist regex or blocked-construct list and rejected outright on any
  mismatch, rather than attempting to strip or escape dangerous content. This
  mirrors the project-wide "loud failure over silent fallback" preference — a false
  positive (legitimate code rejected) is preferred over a false negative (unsafe
  code silently neutralized incorrectly).
- **`hmac.compare_digest` for token comparison** closes a timing side-channel that
  a naive `==` string comparison would leave open, even though the local-network
  threat model (a same-machine browser tab, not a remote attacker) makes timing
  attacks a lower-probability vector than the Origin/Host checks it's layered with.
- **Loopback-only default bind plus a loud non-default-bind warning** (see
  `cli/_serve.py`) rather than an interactive prompt or a hard block: the tool
  needs to support intentional LAN exposure (e.g. a shared dev box) without
  making that the accidental default.
- **Lazy env-var reads over import-time constants.** A constant frozen at import
  silently ignores overrides applied afterward (programmatic server start, test
  `monkeypatch.setenv`, uvicorn reload) — this was an actual regression class, not
  a hypothetical one (see `tests/test_env_lazy_accessors.py`'s docstring), and the
  fix generalized into the shared `float_env`/`int_env`/`optional_int_env` helpers
  rather than being fixed ad hoc per call site.
- **One `.gitignore` guard-entry list, two writers.** `haute init` and the
  unborn-repo seed path (`_git.set_working_branch`) both need the same guard
  entries but run at different points in a project's lifecycle (the seed path
  can't assume an ambient `.gitignore` exists yet, since it stages the *whole*
  working tree for a root commit). A single source-of-truth tuple plus a shared
  idempotent writer keeps the two call sites from drifting apart over time.

## Interactions

- Depended on by the [execution engine](../execution-engine/high-level.md):
  `_user_exec._exec_user_code` (the sandboxed `exec()` path for pipeline node code)
  and `executor.py` both import `validate_user_code`/`safe_globals` directly, and
  `_model_scorer.py`/`deploy/_scorer.py` reuse the same `_exec_user_code` entry
  point for scoring-time code execution.
- Depended on by the io-layer (`_io.py`) for `validate_project_path`,
  `safe_unpickle`, and `safe_joblib_load` when loading external model/data
  artifacts (`load_external_object`), and by `routes/optimiser.py` and
  `routes/pipeline.py` for `_get_project_root()` when resolving user-supplied
  output paths against the project root.
- Depended on by the CLI (`cli/_train.py`) for `validate_user_code` before
  executing a training script as a module, and by `cli/_serve.py` for
  `ensure_local_session_token_env`/`TRUSTED_HOSTS_ENV` when starting the dev
  server and its child processes.
- Depended on by [server-api](../server-api/high-level.md): `server.py` installs
  `LocalSessionMiddleware` and `LocalTrustedHostMiddleware` ahead of every mounted
  router, and the `/ws/sync` WebSocket endpoint calls `websocket_rejection_reason`
  before `accept()`-ing a connection.
- Depended on by `haute init` (project scaffolding, via `cli/_init_cmd.py`) and by
  `_git.py`'s unborn-repo commit seed for `ensure_gitignore_guards`.
- Consumes numeric knobs via `_env.py` on behalf of callers across
  `routes/pipeline.py`, `routes/json_cache.py`, `routes/output_assemble.py`,
  `routes/databricks.py`, `routes/_optimiser_service.py`, and
  `routes/_train_service.py` — this component owns the parsing helpers, not the
  knobs' meanings, which belong to their respective owning components.

## Failure model

- **Unsafe code raises before execution, always.** `validate_user_code` raises
  `UnsafeCodeError` (a `HauteError` subclass) for any blocked construct; callers
  never fall back to executing the code anyway. A `SyntaxError` during the
  validation parse is wrapped as `UnsafeCodeError` with the original exception
  chained as `__cause__`, and `_exec_user_code` unwraps that specific case back
  into a plain `SyntaxError` so downstream error reporting sees the error shape a
  syntax mistake would normally produce, not a generic security rejection.
- **Runtime execution errors are re-raised, never swallowed**; `_exec_user_code`
  only annotates the exception with the offending line number
  (`exc._user_code_line`) extracted from the `<string>` frame of the traceback,
  purely for error-message quality — it does not alter control flow.
- **Blocked pickle/joblib globals raise `pickle.UnpicklingError`** (in `_sandbox.py`)
  with a message
  that names the exact rejected `(module, qualname)` and points at
  `_ALLOWED_PICKLE_CLASSES`/`_ALLOWED_PICKLE_GLOBALS` as the place to extend the
  allowlist — this is a deliberate "reject and tell the developer how to fix it,"
  not a silent skip.
- **Path containment failures raise `ValueError`** (`validate_project_path`) rather
  than returning `None`/a sentinel; every caller in this component either lets that
  propagate or wraps it into an HTTP 403 at the API boundary (see
  [server-api](../server-api/high-level.md)).
- **Local-session/WebSocket rejections fail closed with a structured response**,
  not a silently-accepted connection: HTTP requests get a `403` JSON body from
  `LocalSessionMiddleware`, host-header mismatches get a `400` from
  `LocalTrustedHostMiddleware`, and WebSocket rejections close with code `1008`
  and a reason string before `accept()` is ever called — an unauthenticated peer
  never reaches the socket's message loop.
- **Malformed environment values degrade to a default with a logged warning**
  (`_env.py`'s `float_env`/`int_env`/`optional_int_env`) rather than raising — this
  is the one deliberate exception to "fail loudly" in this component, justified
  because a bad *tuning* value (not a security gate) should not take down server
  startup or an in-flight request; the warning still makes the drift observable in
  logs.
- **The joblib monkey-patch restore is unconditional.** `safe_joblib_load` restores
  `NumpyUnpickler.find_class` in a `finally` block, so a raised
  `pickle.UnpicklingError` (or any other exception) during a load never leaves the
  process-wide patch installed for subsequent unrelated `joblib.load()` calls.
