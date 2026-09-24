# Sandbox Security — High-Level Specification

## Purpose

Haute pipelines run user-authored Python (polars transform nodes and preamble/utility
code) inside the local editor process; the CLI imports training scripts inside the
separate CLI process. The editor also loads user-supplied model/data
artifacts (pickle, joblib) from disk.

Project code is trusted first-party code (decision of 6 September 2026, finding F1):
node text, preambles, utility modules and training scripts run with the privileges of
the process that runs haute, and access to a project is governed by who may edit its
files, not by haute. Opening a project is running its code. What this component
defends against is therefore narrower than a hostile author, and it says so rather
than promising containment it cannot deliver: (1) accidents in project code run inside
the server, the calls that would hang it or stop it (`input()`, `exit()`/`quit()`,
`breakpoint()`), caught by an accident guard before the code runs; (2) arbitrary code execution via
deserializing untrusted pickle/joblib model artifacts, which can be swapped under a
project without editing any code; and (3) a random web page driving the local dev
server through a user's browser (cross-site request/WebSocket hijack against
`localhost`). A fourth, narrower concern — keeping generated
`.gitignore` entries consistent so secrets and per-clone state never get committed —
is bundled in here because it is a small, self-contained guard module living beside
the others, not because it shares a threat model with the first three.

## Scope

In scope:
- The accident guard run on node code and the preamble before `exec()` and on pivot
  formulas and expression steps before `eval()` (`validate_user_code`), plus the
  execution namespace those paths use (`safe_globals`).
- The actual `exec()` call sites for pipeline node code and its namespace assembly
  (`_exec_user_code`).
- Restricted unpickling for both raw pickle files and joblib archives
  (`safe_unpickle`, `safe_joblib_load`).
- The one path-containment check, `contained_path(root, path)`, and its policy. Its
  callers: the file-browse, schema, read-json, recovery-preview, save, submodel,
  utility and optimiser-save routes and the assistant's dataset tools (request
  paths), the check before deserialising a project file (`validate_project_path`),
  recovery artifact paths (`_artifact_paths.safe_path`), the save service's codegen
  output paths, SQLite locators and the MLflow settings write target.
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
- The request-shape checks around a route's path input (URL-scheme rejection,
  module-name validation) — [server-api](../server-api/high-level.md). The
  containment comparison itself is this component's `contained_path`.
- SQL-identifier and git-ref-name validation (`_TABLE_NAME_RE`,
  `_validate_ref_name`) — those live in the Databricks I/O and git-integration
  components respectively; they follow the same "reject, don't sanitize" posture
  but are separate allowlists over a different input shape.
- The CLI's own write-sandbox used by the test suite (`tests/_write_sandbox.py`)
  is test infrastructure, not part of the shipped security surface.

## Trust boundary

- **Node text is trusted project code.** The enforcement boundary is the identity
  the haute process runs as: the local user in the editor and the CLI, and the
  hosted app's own identity inside its single-tenant container (see
  [hosted-databricks-app](../hosted-databricks-app/high-level.md)). Anything that
  identity can do, project code can do.
- **Allowed operations.** Any Python or Polars: the accident guard rejects only the
  calls that would hang or stop the server. Direct file, environment and network access
  from node text is not
  confined: the injected Polars module carries the whole Python object graph
  beneath it (`pl.io.csv.functions.os` reaches the operating system), a preamble
  imports freely, and no OS-level sandbox (seccomp, Landlock, restricted token,
  network filter) exists on any supported platform. Only memory caps are enforced.
- **Project reads and writes.** Haute's own loaders and writers stay inside the
  project root (`contained_path`, the runtime path resolution below); that
  containment protects haute's persistence from confused paths, not the machine
  from the author.
- **Environment exposure.** The process environment, including any credentials
  the deployment supplies to haute itself, is visible to project code.
- **Hosted mode.** Node code runs with the app's identity in its own container;
  the platform proxy boundary adapts traffic and never contains code, and access
  to a project is governed by the workspace permissions on the app.
- **What real containment would require**, recorded so it is not implied: a
  privilege-separated worker with an explicit filesystem, credential, process and
  network policy enforced outside the Python object graph, per supported platform,
  with its own CI lane. That is a product change (no ad-hoc reads outside the
  project, no network from a transform) and is not planned.
- `tests/test_node_code_trust_boundary.py` pins the boundary through the real node
  entry point: the server-stopping calls are rejected before execution, ordinary
  Python (classes, reflection, `global`, imports) runs, and a synthetic environment
  marker and an outside-project write remain reachable.

## Behaviour

- **The accident guard catches what would hang or stop the server, and nothing
  else.** Project code run inside the server (node code through `_exec_user_code`,
  the preamble, pivot formulas and expression steps) is parsed by
  `validate_user_code` before it runs. A direct call to `input()` (it waits for
  console input the server never receives), `exit()` or `quit()` (they stop the server
  process) or `breakpoint()` (it waits for a debugger on the server's console) is
  rejected with `UnsafeCodeError`, unless the code binds that name itself. Everything
  else is ordinary Python: classes, `global`/`nonlocal`, `getattr`/`type`/`vars`,
  dunder access, `import` statements, `open` and `eval`.
- **The execution namespace is the ordinary builtins without those four calls.**
  `safe_globals` gives node code, the preamble, pivot formulas and expression steps
  the real `__import__` and `__build_class__`, so imports and class definitions run;
  an alias of a server-stopping call (`f = input`) fails with `NameError` when it runs
  instead of hanging. Each namespace runs as its own module, `haute_project_code_<n>`
  (not `__main__`, so a script's `if __name__ == "__main__":` block does not run inside
  the server), registered in `sys.modules` while its code runs and removed afterwards,
  and the code is compiled without haute's own `from __future__` imports. A class, a
  dataclass (including quoted or explicitly postponed annotations) and
  `typing.get_type_hints` called while the code runs therefore behave as in an ordinary
  module. Modules the code imports
  (including `utility` modules) execute in their normal module namespaces.
- **Training scripts are not guarded.** `haute train` imports the script as an
  ordinary module in the CLI process, where console input, a debugger and `exit()` are
  ordinary.
- **Preamble exports are filtered before node code receives them.** Preamble
  execution itself retains the import privilege above, but
  `executor._is_dangerous_preamble_binding` removes exported top-level values whose
  module root is `os`, `sys`, `subprocess`, `shutil`, `signal`, `ctypes`, or
  `importlib` before the namespace becomes node-code globals. This is a direct-binding
  handoff filter, not recursive inspection of containers or closures and not a claim
  that preamble execution is sandboxed from those modules; node code may import those
  modules itself.
- **Unified data I/O does not fork the code sandbox.** Optional `dataInput` code runs
  exactly once through `_exec_user_code` after provider resolution;
  `DataOutputConfig` rejects executable code. Direct locators, source-cache
  digest/generation paths, final output destinations, and unique staging siblings
  are each independently contained and rechecked before publication. Resolved
  credentials, provider/cache objects, and leases never enter user globals, persisted
  cache metadata, or user-visible failure text.
- **Validation results are cached per code string** in a bounded
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
  (`validate_project_path`, which resolves the configured path and checks it with
  `contained_path`). `restricted_joblib_load` is the same allowlisted
  loader without that containment, for model files Haute's own loaders locate (training
  outputs, the MLflow artifact cache, an MLflow pyfunc package).
- **Exactly two InterpretML classes are trusted:**
  `interpret.glassbox._ebm._ebm.ExplainableBoostingRegressor` and
  `...ExplainableBoostingClassifier`; no `interpret.*` prefix or other symbol is. A pinned
  0.7.8 `.ebm` references only those classes plus allowlisted joblib/NumPy scaffolding.
  scikit-learn's state hook checks versions only for `sklearn` classes, so an `.ebm` loads
  only when its feature contract records exactly the installed `interpret-core` version; any
  other or missing version fails with `ArtifactVersionMismatchError` before unpickling, and the
  loaded estimator's class, features, feature types and finite scores are validated against
  the contract before inference.
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
  never Origin/Host checks. `HAUTE_DISABLE_LOCAL_SESSION_AUTH` is an explicit
  local development escape hatch and the switch set process-wide by the hosted
  Databricks Apps entry point (`haute.hosted.create_app`); in local mode the
  loopback and forwarded-header gates remain active, while in hosted mode the
  forwarded-header gate is satisfied by header rewriting at the proxy boundary
  rather than by rejection.
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

- **One containment check.** `contained_path(root, path)` is the only comparison of
  a path against the directory it must stay in, so the policy is stated once:
  - An absolute *path* that is not lexically inside the resolved root is refused
    before anything resolves it, so a request never makes the process touch an
    outside location (a network share, a device) just to check it. Such a path is
    refused even if it would resolve back inside. A caller that holds a trusted,
    configured path (`validate_project_path`, the SQLite and MLflow checks, the save
    service's generated paths) resolves it first, so only request input meets this
    guard.
  - Otherwise the joined path is resolved: `..` segments collapse and symbolic links
    and Windows junctions are followed, so a link inside the root that points outside
    is refused and one that points inside is accepted. Callers that must refuse links
    altogether (recovery artifacts, the cache's file locks) check that themselves.
  - The resolved path must lie under the resolved root, compared component-wise:
    case-insensitively on Windows, case-sensitively elsewhere. On a case-insensitive
    macOS volume a differently-cased spelling of an inside path can be refused; it
    can never let an outside path through. (`normcase`/`commonpath` folding added
    nothing: both sides are resolved paths.)

- **An accident guard, not a sandbox.** Project code is trusted (decision of
  6 September 2026), and Polars' own module graph reaches the operating system, so a
  denylist of escape-shaped syntax protected nothing while rejecting ordinary code
  (classes, `global`, reflection). The guard keeps only the checks for mistakes that
  fail confusingly in a server: a call that waits on a console or debugger nobody is
  watching, or one that ends the process serving every open editor.
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
- **Reject, don't sanitize.** Table names and ref names are matched against an
  allowlist regex, and the accident guard's calls against a fixed list, and rejected
  outright on any mismatch, rather than attempting to strip or escape dangerous
  content. This mirrors the project-wide "loud failure over silent fallback"
  preference.
- **`hmac.compare_digest` for token comparison** closes a timing side-channel that
  a naive `==` string comparison would leave open, even though the local-network
  threat model (a same-machine browser tab, not a remote attacker) makes timing
  attacks a lower-probability vector than the Origin/Host checks it's layered with.
- **Loopback-only serving is the posture of `haute serve` and the stock app.**
  `src/haute/cli/_serve.py` rejects wildcard, LAN/public, and custom-hostname
  binds before startup because this UI can execute project code and access
  project files. The only exception is the explicit hosted deployment entry point
  in `src/haute/hosted.py`, which delegates authentication to a platform SSO proxy
  (see [hosted-databricks-app](../hosted-databricks-app/high-level.md)).
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
  `_user_exec._exec_user_code` (the guarded `exec()` path for pipeline node code)
  and `executor.py` both import `validate_user_code`/`safe_globals` directly, and
  `_builders.py`, `chunking.py`, and `_model_scorer.py`/`deploy/_scorer.py` reuse
  the same `_exec_user_code` entry point for node execution and scoring-time code
  execution.
- Depended on by [explore-eda](../explore-eda/high-level.md): `routes/_pivot_service.py`
  imports `validate_user_code` and `safe_globals` directly to validate and `eval()`
  configured pivot formulas without going through `_exec_user_code`.
- Depended on by the routes named in Scope, the assistant's dataset tools, recovery
  artifacts, the save service, `_database_io` and the MLflow settings for
  `contained_path`, and by the io-layer (`_io.py`) for `validate_project_path`,
  `safe_unpickle`, and `safe_joblib_load` when loading external model/data
  artifacts (`load_external_object`), and by `routes/optimiser.py` and
  `routes/pipeline.py` for `_get_project_root()` when resolving user-supplied
  output paths against the project root.
- Depended on by the CLI `cli/_serve.py` for
  `ensure_local_session_token_env`/`TRUSTED_HOSTS_ENV` when starting the dev
  server and its child processes.
- Depended on by [server-api](../server-api/high-level.md): `server.py` installs
  `LocalSessionMiddleware` and `LocalTrustedHostMiddleware` ahead of every mounted
  router, and the `/ws/sync` WebSocket endpoint calls `websocket_rejection_reason`
  before `accept()`-ing a connection.
- Depended on by `haute init` (project scaffolding, via `cli/_init_cmd.py`) and by
  `_git_setup.py`'s unborn-repo commit seed for `ensure_gitignore_guards`.
- Supplies numeric parsing helpers to callers across the codebase, including
  `executor.py`, `trace.py`, `_execution_admission.py`, `assistant/_loop.py`,
  `_input_preparation.py`, `_interactive_workers.py`,
  `_source_cache.py`, `_json_shred/` (`_records.py`, `_runtime_storage.py`, `_writer.py`),
  `deploy/_batch_scoring.py`, and the route modules `routes/pipeline.py`,
  `routes/json_cache.py`, `routes/output_assemble.py`, `routes/input_cache.py`,
  `routes/_node_data_service.py`, `routes/_optimiser_service.py`,
  `routes/_training_artifacts.py`,
  `routes/_training_lifecycle.py`, and `routes/_training_worker.py`. This component owns
  the parsing helpers, not the knobs' meanings, which belong to their respective components.

## Failure model

- **A guarded call raises before execution, always.** `validate_user_code` raises
  `UnsafeCodeError` (a `HauteError` subclass) for a server-stopping call; callers
  never fall back to executing the code anyway. A `SyntaxError` during the
  validation parse is wrapped as `UnsafeCodeError` with the original exception
  chained as `__cause__`, and `_exec_user_code` unwraps that specific case back
  into a plain `SyntaxError` so downstream error reporting sees the error shape a
  syntax mistake would normally produce, not a guard rejection.
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
- **Path containment failures raise `PathOutsideProjectError`** (and a NUL byte
  `InvalidPathError`) rather than returning `None`/a sentinel. The application
  handler answers a request with 403 "Cannot access paths outside the project root"
  (400 "Invalid path"); the refused path stays out of the response. A caller with
  its own contract translates it: recovery artifacts → the repair 409, codegen output
  paths → 400, SQLite locators → `ValueError`, the MLflow settings write →
  `MlflowConfigError`.
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

