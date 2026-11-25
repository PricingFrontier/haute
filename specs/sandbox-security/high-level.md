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
  callers are every comparison of a path against a directory the path must stay in:
  request paths (the routes, the assistant's dataset tools and pipeline source, a
  repair's source identity, a model export destination, the SPA's static files),
  the check before deserialising a project file (`validate_project_path`), runtime
  paths and executor outputs, node config sidecars, submodel references, recovery
  and repair paths, the save service's paths, worker artifacts, the MLflow artifact
  cache, optimiser artifact handles, deploy's pipeline and shipped data, the
  assistant's project knowledge and session storage, SQLite locators and the MLflow
  settings write target. The low-level specification lists each with its refusal.
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
- Haute's own folder under the system temporary folder (`_temp_folder.py`), which
  every file Haute keeps outside a project in the temporary folder under a fixed name
  goes through.

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
  beneath it (`pl.config.os` reaches the operating system), a preamble
  imports freely, and no OS-level sandbox (seccomp, Landlock, restricted token,
  network filter) exists on any supported platform. Only memory caps are enforced.
- **Project reads and writes.** Haute's own loaders and writers stay inside the
  project root (`contained_path`, the runtime path resolution below); that
  containment protects haute's persistence from confused paths, not the machine
  from the author. The Files panels reach only the pipeline folder's `files/` and
  the root's `files/data/` and `files/outputs/`, never a dot-name such as `.env` or `.git`, and
  never through a link ([server-api](../server-api/high-level.md)).
- **Environment exposure.** The process environment, including any credentials
  the deployment supplies to haute itself, is visible to project code.
- **The locally served underwriter app.** `haute app serve` signs every request in as
  the analyst ([workbench](../workbench/high-level.md)), so what bounds it is the request,
  not a session: it binds loopback, and the editor's Host and forwarded-header gate, with
  the canonical loopback hosts, refuses any other host and any forwarded header before
  the request is signed in. That keeps out a page of a site whose name resolves to the
  analyst's machine, whose requests the browser counts as the page's own; a page of
  another site has its writes refused by the app's origin check and cannot read what
  the app answers. The hosted app's identity is its host's, outside this gate.
- **Hosted mode.** Node code runs with the app's identity in its own container;
  the platform proxy boundary adapts traffic and never contains code, and access
  to a project is governed by the workspace permissions on the app.
- **Pages of other sites.** A browser sends a page's requests with the cookies of the
  site they go to, so each server that changes something for a signed-in user says
  which page may ask. The three rules, side by side:
  - *The local editor* (`haute serve`): every `/api/` request and sync socket from a
    browser carries an Origin that is exactly the loopback origin of the request's own
    Host, and the session cookie, which is `SameSite=Strict`. Turning the session gate
    off (`HAUTE_DISABLE_LOCAL_SESSION_AUTH`) stops only the cookie check: an Origin that
    is sent must still be that loopback origin.
  - *The hosted editor* (`haute.hosted.create_app`): the platform's proxy signs users
    in, so the session gate is off, and the proxy boundary refuses, before any route,
    every request that changes state (any method but `GET`, `HEAD` and `OPTIONS`) and
    every sync-socket handshake whose `Origin` is not the app's own URL
    (`DATABRICKS_APP_URL`) or whose `Sec-Fetch-Site` is not `same-origin` or `none`. A
    request with neither header (a client that is not a browser) passes. This holds
    whatever the platform's sign-in cookie allows, another app on the hosting domain
    (a page of the same site) included.
  - *The underwriter app* (`haute app serve` and a hosted release): a write (any method
    but `GET`, `HEAD` and `OPTIONS`) whose `Sec-Fetch-Site` is not `same-origin` or
    `none`, or, from a browser that sends no `Sec-Fetch-Site`, whose Origin is not the
    request's Host, is refused (403). It compares with the Host because a hosted
    release is not told its own URL; it reads `Sec-Fetch-Site` first because a proxy in
    front of it may pass it another Host than the page's. It has no socket.
- **Haute's temporary folder.** The files Haute keeps outside a project (training and
  optimiser artifacts, the project mutation, form folder, output and writer locks, and
  a command's incident record outside a project) are under one folder of the user's own
  in the system temporary folder, never under a fixed name another local user could
  take first. On Linux and macOS it is `haute-<uid>`, made private to its owner (mode
  `0700`), and Haute uses it only while it is a real folder owned by the user running
  Haute and open to no one else; on Windows, whose temporary folder is the user's own,
  it is `haute`.
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
  including the Free code steps the free-code columns endpoint resolves columns for, the preamble, pivot formulas and expression steps) is parsed by
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
- **Preamble exports reach node code as written.** Every top-level binding the
  preamble makes (apart from the base namespace it starts from) becomes a node-code
  global, whatever module it came from: project code is trusted, and node code may
  import `os`, `sys`, `shutil` and the like itself, so filtering the handoff would
  protect nothing and would only make an author's helper silently disappear.
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
  non-class callable is rejected, not silently accepted. NumPy 2 renamed `numpy.core`
  to `numpy._core`; a pickle written under NumPy 1 names the reconstruction helpers by
  their old path, and each `numpy.core.<rest>` spelling of an allowed `numpy._core.<rest>`
  entry is read as that entry, resolved at its NumPy 2 path (NumPy's `numpy.core`
  stand-in warns that it is deprecated). Nothing else under `numpy.core` is allowed.
  This is a dependency's format, which the canonical-only policy keeps in scope. Every file passed to
  `safe_unpickle`/`safe_joblib_load` must first resolve inside the project root
  (`validate_project_path`, which resolves the configured path and checks it with
  `contained_path`). `restricted_joblib_load` is the same allowlisted
  loader without that containment, for model files Haute's own loaders locate (training
  outputs, the MLflow artifact cache, an MLflow pyfunc package). Neither has a fallback:
  `joblib` is a required dependency, and when it cannot be imported the import error
  propagates.
- **Exactly two InterpretML classes are trusted:**
  `interpret.glassbox._ebm._ebm.ExplainableBoostingRegressor` and
  `...ExplainableBoostingClassifier`; no `interpret.*` prefix or other symbol is. A pinned
  0.7.8 `.ebm` references only those classes plus allowlisted joblib/NumPy scaffolding.
  scikit-learn's state hook checks versions only for `sklearn` classes, so an `.ebm` loads
  only when the JSON record at its head names exactly the installed `interpret-core` version;
  the record is read before anything is unpickled, any other version fails with
  `ArtifactVersionMismatchError` and a file without the record with a `ConfigError`, and the
  loaded estimator's class, features, feature types and finite scores are validated against
  the record before inference. `restricted_joblib_load(path, *, offset=0)` unpickles the joblib
  stream that starts *offset* bytes into the file.
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
  Databricks Apps entry point (`haute.hosted.create_app`). It turns off only the
  cookie: an `/api/` request or a WebSocket handshake whose Origin is sent and is not
  the request's loopback origin is still refused, and one without an Origin passes. In
  local mode the loopback and forwarded-header gates remain active, while in hosted
  mode the forwarded-header gate is satisfied by header rewriting at the proxy boundary
  rather than by rejection, and the boundary presents the app's own page's Origin as
  the loopback origin it rewrites Host to, after refusing another site's
  (Trust boundary, Pages of other sites).
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
    refused even if it would resolve back inside. A caller that holds a trusted
    path (configured, read from project files, or made by the server) resolves it
    first, so only request input meets this guard and every other comparison is
    the resolved path against the resolved root.
  - Otherwise the joined path is resolved: `..` segments collapse and symbolic links
    and Windows junctions are followed, so a link inside the root that points outside
    is refused and one that points inside is accepted. Callers that must refuse links
    altogether (recovery artifacts, the cache's file locks) check that themselves.
  - The resolved path must lie under the resolved root, compared component-wise:
    case-insensitively on Windows, case-sensitively elsewhere. On a case-insensitive
    macOS volume a differently-cased spelling of an inside path can be refused; it
    can never let an outside path through. (`normcase`/`commonpath` folding added
    nothing: both sides are resolved paths.)
  - `is_contained(root, path)` is the same check's answer as a boolean, for a caller
    to which a path outside is not an error but simply not one of its own.
  - A comparison that is not a containment check says so where it is made: an
    `is_relative_to` or `commonpath` call outside `contained_path` carries a
    `# not contained_path: <reason>` comment on its line or the line above. Such
    calls choose between two valid roots, classify a path whose either answer is
    valid, test overlap in the other direction, or compare names that are not
    filesystem paths; the build hook's checks are marked because the build runs
    before haute can be imported. `tests/test_path_containment.py` fails on an
    unmarked call in `src/haute` or `hatch_build.py`.

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
  execution, and `_polars_steps.py` reuses it to resolve the columns after each
  Free code step for the free-code columns endpoint, which runs it in the isolated
  interactive preview worker with a short deadline, as a preview runs node code.
- Depended on by [explore-eda](../explore-eda/high-level.md): `routes/_pivot_service.py`
  imports `validate_user_code` and `safe_globals` directly to validate and `eval()`
  configured pivot formulas without going through `_exec_user_code`.
- Depended on for `contained_path` by every caller Scope names (listed in the
  low-level specification), and by the io-layer (`_io.py`) for `validate_project_path`,
  `safe_unpickle`, and `safe_joblib_load` when loading external model/data
  artifacts (`load_external_object`), and by `routes/optimiser.py` and
  `routes/pipeline.py` for the current project's root
  (`haute._project_context.current_project`) when resolving user-supplied output paths
  against the project root.
- Depended on by the CLI `cli/_serve.py` for
  `ensure_local_session_token_env`/`TRUSTED_HOSTS_ENV` when starting the dev
  server and its child processes.
- Depended on by [server-api](../server-api/high-level.md): `server.py` installs
  `LocalSessionMiddleware` and `LocalTrustedHostMiddleware` ahead of every mounted
  router, and the `/ws/sync` WebSocket endpoint calls `websocket_rejection_reason`
  before `accept()`-ing a connection.
- Depended on by [workbench](../workbench/high-level.md): the underwriter app served
  with a local principal installs `LocalTrustedHostMiddleware` with
  `DEFAULT_TRUSTED_HOSTS` ahead of its sign-in.
- Depended on by `haute init` (project scaffolding, via `cli/_init_cmd.py`) and by
  `_git_setup.py`'s unborn-repo commit seed for `ensure_gitignore_guards`.
- Depended on by [hosted-databricks-app](../hosted-databricks-app/high-level.md): its
  proxy boundary applies the hosted editor's cross-site rule (Trust boundary) and reads
  origins through `_local_security.web_origin`.
- Supplies Haute's temporary folder (`_temp_folder.haute_temp_root`) to the training
  and optimiser artifact roots ([modelling](../modelling/high-level.md),
  [optimiser](../optimiser/high-level.md)), the project mutation lock
  ([server-api](../server-api/high-level.md)), the form folder lock
  ([workbench](../workbench/high-level.md)), the Data Output destination lock and the
  writers' locks ([io-layer](../io-layer/high-level.md)) and a command's incident
  record outside a project ([cli](../cli/high-level.md)); the server's lifespan and
  `haute serve` check it before they start.
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
  (`exc._user_code_line`) extracted from the node code's own frame of the traceback,
  purely for error-message quality — it does not alter control flow.
- **Blocked pickle/joblib globals raise `pickle.UnpicklingError`** (in `_sandbox.py`)
  with a message written for the analyst who chose the file: it names the file and the
  exact rejected `module.qualname`, and says what they can do: write the file again from
  an environment with current library versions, or keep data as Parquet; for an allowed
  name whose package is not installed, install that package. It never names Haute's
  source files or allowlists, which an analyst does not change. A refusal is never a
  silent skip.
- **Path containment failures raise `PathOutsideProjectError`** (and a NUL byte
  `InvalidPathError`) rather than returning `None`/a sentinel. The application
  handler answers a request with 403 "Cannot access paths outside the project root"
  (400 "Invalid path"); the refused path stays out of the response. A caller with
  its own contract translates it: recovery artifacts → the repair 409, codegen output
  paths → 400, SQLite locators → `ValueError`, the MLflow settings write →
  `MlflowConfigError`, a worker artifact → `WorkerProtocolError`, a deploy path →
  `DeployError`; where an outside path is simply not one of the caller's (a runtime
  candidate, a stale config to remove, a project document) it is skipped, and the
  SPA serves its index page. Every caller and its contract is in the low-level
  specification.
- **Local-session/WebSocket rejections fail closed with a structured response**,
  not a silently-accepted connection: HTTP requests get a `403` JSON body from
  `LocalSessionMiddleware`, host-header mismatches get a `400` from
  `LocalTrustedHostMiddleware`, and WebSocket rejections close with code `1008`
  and a reason string before `accept()` is ever called — an unauthenticated peer
  never reaches the socket's message loop. The hosted boundary answers another
  site's request the same way, a `403` JSON body or a close with `1008` before
  accept, and logs the refusal with the header that decided it, never its value.
- **A temporary folder that is not the user's own stops Haute.** When `haute-<uid>`
  exists but is not a folder (a link included), belongs to another user, or is open to
  other users, `haute serve` ends before the server starts, the editor's and the hosted
  app's server refuse to start, and any other use refuses, each with a message naming
  the folder and what to do; Haute never moves to another name or widens what it
  trusts.
- **Invalid values passed through `_env.py` fail loudly.** Unset variables retain
  their documented defaults, but malformed, non-finite, zero, and negative explicit
  values raise `RuntimeError`; `optional_int_env` returns `None` only when absent.
- **Restricted joblib loading never patches process-wide state.**
  `safe_joblib_load` instantiates a private `NumpyUnpickler` subclass whose
  `find_class` applies the shared allowlist, so unrelated `joblib.load()` calls
  cannot observe a temporary class mutation.

