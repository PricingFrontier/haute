# Sandbox Security — High-Level Specification

## Purpose

Haute pipelines run user-authored Python (polars transform nodes and preamble/utility
code) inside the local editor process; the CLI validates then imports training scripts
inside the separate CLI process. The editor also loads user-supplied model/data
artifacts (pickle, joblib) from disk. These are
attacker-shaped inputs even in a single-user local tool: a pipeline file can be
copied between machines, shared, or opened from an untrusted source, and a model
artifact can be swapped out from under the project directory. This component is the
set of defences that keep "run whatever code/data the project directory contains"
from becoming "run whatever the file system contains" or "run whatever a hostile
browser tab can smuggle through localhost."

It covers three independent threat surfaces: (1) arbitrary code execution via
`exec()` of pipeline node/preamble code and normal module import of a CLI training
script, (2) arbitrary code execution via
deserializing untrusted pickle/joblib model artifacts, and (3) a random web page
driving the local dev server through a user's browser (cross-site request/WebSocket
hijack against `localhost`). A fourth, narrower concern — keeping generated
`.gitignore` entries consistent so secrets and per-clone state never get committed —
is bundled in here because it is a small, self-contained guard module living beside
the others, not because it shares a threat model with the first three.

## Scope

In scope:
- AST-level static validation of pipeline/preamble code before `exec()` and of CLI
  training scripts before module import (`validate_user_code`), plus the restricted
  builtins namespace used only by the `exec()` paths (`safe_globals`).
- The actual `exec()` call sites for pipeline node code and its namespace assembly
  (`_exec_user_code`).
- Restricted unpickling for both raw pickle files and joblib archives
  (`safe_unpickle`, `safe_joblib_load`), and the project-root path containment check
  used before touching any such file (`validate_project_path`).
- Canonical runtime-path normalization and symlink-aware containment for eager/lazy
  execution (`_path_resolution.py`), including the context-local execution root.
- Local-session protection for the FastAPI/WebSocket server (`_local_security.py`):
  the per-process session token, trusted-Origin/trusted-Host checks, and the
  middleware/dependency wiring that enforces them.
- Shared fail-fast numeric environment-variable parsing through `_env.py` for the
  migrated request timeout, chunk/history, execution-admission/reserve, and
  preview/trace cache-budget callers. Each caller controls resolution timing: some
  accessors read lazily, while cache-budget constants resolve at module import. A few
  component-owned parsers remain separately documented; the helper is not a universal
  environment-policy layer.
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
  `__builtins__[...]` subscripting. `.format()`/`.format_map()`/`.vformat()`/
  `.get_field()`/`.format_field()` calls
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
- **`allow_imports=True` is an explicit, narrow escape hatch**, used for preamble
  source and CLI training-script validation. It disables the AST import check.
  Preamble execution also calls `safe_globals(allow_imports=True)`, restoring the
  real `__import__` in that restricted exec namespace; the training command instead
  imports the validated file as an ordinary Python module, so it runs with normal
  module builtins. Modules imported by either path (including `utility` modules) are
  not recursively AST-validated and execute in their normal module namespaces.
  These paths therefore have full import privileges (`os`, `subprocess`, …); they
  are treated as first-party project code, unlike per-node transform text.
- **Preamble exports are filtered before node code receives them.** Preamble
  execution itself retains the import privilege above, but
  `executor._is_dangerous_preamble_binding` removes exported top-level values whose
  module root is `os`, `sys`, `subprocess`, `shutil`, `signal`, `ctypes`, or
  `importlib` before the namespace becomes node-code globals. This is a direct-binding
  handoff filter, not recursive inspection of containers or closures and not a claim
  that preamble execution is sandboxed from those modules.
- **Unified data I/O does not fork the code sandbox.** Optional `dataInput` code runs
  exactly once through `_exec_user_code` after provider resolution;
  `DataOutputConfig` rejects executable code. Direct locators, source-cache
  digest/generation paths, final output destinations, and unique staging siblings
  are each independently contained and rechecked before publication. Resolved
  credentials, provider/cache objects, and leases never enter user globals, persisted
  cache metadata, or user-visible failure text.
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
- **Local API/WebSocket access is gated by configured loopback Host, exact Origin,
  and an HttpOnly session cookie.** Host middleware rejects authorities outside its
  validated allowlist and
  every forwarded/proxy header on HTTP and WebSocket scopes. The browser establishes
  its per-process credential only through `POST /api/session/bootstrap`, which
  requires an explicit HTTP(S) Origin with the same scheme, normalized loopback host,
  and effective port as the request Host. The response is no-store and places the
  credential only in an HttpOnly, SameSite=Strict cookie. Protected API calls accept
  only that cookie; an absent Origin is accepted only when the cookie is already valid.
  WebSocket handshakes always require an explicit matching Origin and the cookie.
  Query-string token transport is unsupported. `OPTIONS` skips only the token check,
  never Origin/Host checks. `HAUTE_DISABLE_LOCAL_SESSION_AUTH` remains an explicit
  local development escape hatch; the loopback/forwarded-header gate remains active.
  `HAUTE_TRUSTED_HOSTS` is a comma-separated list of normalized loopback authorities;
  entries may pin an exact port. The FastAPI app snapshots it when middleware is
  installed, invalid/non-loopback entries raise `ValueError`, and `haute serve`
  replaces any stale value with the canonical loopback hosts plus its validated
  loopback bind host before starting the server. This preserves the localhost
  Vite-proxy path for custom loopback binds without retaining a wildcard. It is
  therefore never a remote-access switch or a live per-request knob.
- **The local session token is credential-only data.** It never appears in served
  HTML or JavaScript, URLs/query strings, access-log fields, rejection reasons,
  response/error bodies, or exception text. Browser bootstrap and reconnect move it
  only through the HttpOnly cookie; users are never asked to copy it.
- **Knobs routed through `_env.py` share one fail-fast parse policy.** An unset value
  uses the supplied default (`None` for `optional_int_env`); an explicitly configured
  value must be finite and positive or raises `RuntimeError`. Lazy accessor-backed
  request timeout/chunk/history/admission settings read the environment at call time;
  preview/trace cache-size constants use the same parser at module import and are
  therefore fixed for that module instance. A few component-owned parsers remain
  outside this shared policy.
- **Direct production environment reads are statically inventoried.** An AST
  guard discovers literal `os.getenv`, `os.environ.get`, and
  `os.environ[...]` reads under `src/haute` (including string constants and
  import aliases). Positive numeric Haute knobs must use `_env.py`; the only
  direct-read exceptions are an explicit reviewed set whose string, boolean,
  credential, mapping, or non-negative semantics are not represented by those
  helpers. A new direct access fails without adding a parallel accessor test
  case.
- **`.gitignore` guard entries are idempotent and additive**: re-running the guard
  writer never duplicates an entry and never removes user-authored bytes. Existing
  content is decoded with replacement only for membership checks; missing entries are
  appended as UTF-8, so non-UTF-8 bytes already present are preserved.

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
- **Loopback-only serving is a hard product boundary.** `cli/_serve.py` rejects
  wildcard, LAN/public, and custom-hostname binds before startup. Haute has no
  reverse-proxy or shared-host mode because this UI can execute project code and
  access project files.
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
  importing a training script with ordinary module globals, and by `cli/_serve.py` for
  `ensure_local_session_token_env`/`TRUSTED_HOSTS_ENV` when starting the dev
  server and its child processes.
- Depended on by [server-api](../server-api/high-level.md): `server.py` installs
  `LocalSessionMiddleware` and `LocalTrustedHostMiddleware` ahead of every mounted
  router, and the `/ws/sync` WebSocket endpoint calls `websocket_rejection_reason`
  before `accept()`-ing a connection.
- Depended on by `haute init` (project scaffolding, via `cli/_init_cmd.py`) and by
  `_git.py`'s unborn-repo commit seed for `ensure_gitignore_guards`.
- Supplies numeric parsing helpers to `executor.py`, `trace.py`,
  `_execution_admission.py`, `assistant/_loop.py`, and the route callers
  `routes/pipeline.py`, `routes/json_cache.py`, `routes/output_assemble.py`,
  `routes/input_cache.py`, `routes/_optimiser_service.py`, and
  `routes/_train_service.py`. This component owns the parsing helpers, not the
  knobs' meanings, which belong to their respective components.

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
- **Invalid values passed through `_env.py` fail loudly.** Unset variables retain
  their documented defaults, but malformed, non-finite, zero, and negative explicit
  values raise `RuntimeError`; `optional_int_env` returns `None` only when absent.
- **Restricted joblib loading never patches process-wide state.**
  `safe_joblib_load` instantiates a private `NumpyUnpickler` subclass whose
  `find_class` applies the shared allowlist, so unrelated `joblib.load()` calls
  cannot observe a temporary class mutation.
