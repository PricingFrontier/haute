# Sandbox Security — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/_sandbox.py` | The accident guard for project code (`validate_user_code`), the execution namespace (`safe_globals`), project-root path containment (`validate_project_path`), and the restricted pickle/joblib unpicklers (`safe_unpickle`, `safe_joblib_load`). |
| `src/haute/_user_exec.py` | The single dynamic-execution call site for pipeline node code (`_exec_user_code`): namespace assembly, validation call, execution, and traceback line annotation. |
| `src/haute/_local_security.py` | Local-session protection for the FastAPI/WebSocket server: session-token generation/comparison, exact authority parsing, loopback/forwarded-header middleware, HttpOnly-cookie bootstrap policy, HTTP middleware, and WebSocket pre-accept rejection helper. |
| `src/haute/_path_resolution.py` | Cross-platform runtime path normalization, project/pipeline candidate resolution, symlink-aware containment, and the context-local execution root shared by eager/lazy builders. |
| `src/haute/_gitignore_guard.py` | The shared `.gitignore` guard-entry tuple and the idempotent append-if-missing writer (`ensure_gitignore_guards`) used by both `haute init` and the unborn-repo commit seed. |
| `src/haute/_env.py` | Fail-fast positive numeric environment-variable parsing helpers (`float_env`, `int_env`, `optional_int_env`) used by request-timeout, concurrency, source-cache limits, optimiser chunk/partition, solver-timeout, training-history, assistant-loop, execution-admission, and preview/trace cache-budget accessors. Callers choose lazy call-time or deliberate process-wide import-time resolution. Component-owned parsers remain only where positive-numeric semantics do not apply. |

## Key types and data structures

- **`UnsafeCodeError`** (`_sandbox.py`) — a `HauteError` subclass raised by
  `validate_user_code` for a server-stopping call or unparseable code. Carries the
  original `SyntaxError` as `__cause__` when validation failed because the code could
  not be parsed at all.
- **`ArtifactVersionMismatchError`** (`_sandbox.py`) — a `HauteError` subclass raised
  when a persisted scikit-learn estimator's `_sklearn_version` does not match the
  runtime environment's installed version.
- **`_AccidentGuard(ast.NodeVisitor)`** (`_sandbox.py`) — constructed per
  validation call with the names the code binds (`_bound_names`). Overrides only
  `visit_Call`: a call whose callee is a bare `ast.Name` in `_SERVER_STOPPING_CALLS`
  and not bound by the code raises `UnsafeCodeError`; every other construct is allowed.
- **`_SERVER_STOPPING_CALLS: dict[str, str]`** (`_sandbox.py`) — `input`, `exit`,
  `quit` and `breakpoint`, each mapped to the reason the rejection message gives.
  **`_EXEC_BUILTINS`** is `vars(builtins)` without those four names, copied by every
  `safe_globals` call, which also sets `__name__` to a numbered
  `haute_project_code_<n>` (**`PROJECT_CODE_MODULE`** is the prefix), unique per
  namespace so concurrent executions never resolve each other's names.
- **`project_code_module(namespace)`** (`_sandbox.py`) — a context manager that
  registers the namespace in `sys.modules` under its `__name__` (as a stand-in object
  whose `__dict__` is the namespace) while node code or the preamble runs, and removes
  it afterwards. `dataclasses` and `typing.get_type_hints` resolve string annotations
  through that entry; removing it keeps a long-lived server from accumulating modules.
- **`compile_project_code(code)`** (`_sandbox.py`) — compiles node code and the
  preamble for `exec()` with `dont_inherit=True`, so haute's own `from __future__
  import annotations` does not postpone the project's annotations (a dataclass
  `ClassVar` default and `get_type_hints` on an imported type work as in a module).
- **`_ALLOWED_PICKLE_GLOBALS: frozenset[tuple[str, str]]`** — exact
  `(module, qualname)` pairs for scaffolding *functions* (NumPy 2 `_core`
  reconstruction helpers, `copyreg` helpers, `_codecs.encode`, pandas block/index
  reconstruction helpers, builtin container/scalar constructors). Returned verbatim
  when matched; the pre-NumPy-2 `numpy.core` pickle layout is not retained.
- **`_ALLOWED_PICKLE_CLASSES: frozenset[tuple[str, str]]`** — exact
  `(module, qualname)` pairs for model/data *classes* (numpy `dtype`/`ndarray`,
  pandas `DataFrame`/`Series`/`Index`/`RangeIndex`/`BlockManager`/
  `SingleBlockManager`, polars `DataFrame`/`Series`, sklearn/catboost/lightgbm/
  xgboost estimator classes, `joblib.numpy_pickle.NumpyArrayWrapper`). Resolved
  and then checked with `isinstance(obj, type)` before being trusted.
- **`_RestrictedUnpickler(pickle.Unpickler)`** — overrides `find_class` to call
  `_resolve_allowed_global`.
- **`_validation_cache: LRUCache[str, bool]`** — bounded at
  `_VALIDATION_CACHE_MAX_SIZE = 1024`, keyed on the code string, backed by
  the shared `haute._lru_cache.LRUCache` primitive (also used by the caching
  component's fingerprint cache — see
  [caching](../caching/low-level.md)).
- **`_PROJECT_ROOT: Path | None`** (module-level, `_sandbox.py`) — lazily set to
  `Path.cwd().resolve()` on first use by `_get_project_root()`; overridable via
  `set_project_root()` (used by tests and the CLI).
- **`_BOOT_SESSION_TOKEN`** (`_local_security.py`) — a `secrets.token_urlsafe(32)`
  generated once at module import, used as the fallback session token when
  `HAUTE_LOCAL_SESSION_TOKEN` is unset.
- **`LocalTrustedHostMiddleware`** (plain ASGI callable, not Starlette's
  `BaseHTTPMiddleware`) — reimplements trusted-host checking with correct
  bracketed-IPv6 `Host` header parsing (Starlette's built-in
  `TrustedHostMiddleware` does not handle `[::1]:port` forms).
- **`LocalSessionMiddleware(BaseHTTPMiddleware)`** — exposes one credential-free
  path, `POST /api/session/bootstrap`, only to an explicit matching Origin; every
  other `/api/*` request requires the session cookie plus a trusted Origin
  or an already-valid credential for absent-Origin non-browser calls.
- **`GITIGNORE_GUARD_ENTRIES: tuple[str, ...]`** (`_gitignore_guard.py`) — the
  ordered, fixed set of paths every project's `.gitignore` must contain:
  `.env`, `.haute/`, `impact_report.md`, `.haute_cache/`, `mlruns/`, `data/`,
  `.venv/`.

## Control flow

**Pipeline code execution (`_user_exec._exec_user_code`)**
1. Build the local dataframe bindings — seeds `pl` (polars), positional source
   dataframes under `src_names`, and an `orig`→`inst` name-mapping fallback via
   `build_instance_mapping` (execution-engine helper). `df` is NOT pre-bound
   for polars transforms: inputs are only
   their named bindings and `df` is the output the code must assign. The name
   `df` is therefore reserved and rejected as a polars input, while a preamble
   binding named `df` is hidden from node code. Callers whose code box operates
   on one implicit frame named `df` — external files, explore, and post-code
   hooks — opt in explicitly with `alias_first_input_as_df=True`.
2. Call `validate_user_code(code)`. On `UnsafeCodeError` whose `__cause__` is a
   `SyntaxError`, re-raise the bare `SyntaxError` instead — this normalizes the error
   type callers see for a plain typo versus a guard rejection.
3. Build one fresh execution namespace from `safe_globals(pl=pl, **extra_ns)` and
   `exec` `compile_project_code(code)` in it inside `project_code_module`
   and then overlay the local dataframe bindings before calling `exec`. Input
   names therefore take precedence over same-named preamble globals and remain
   visible inside comprehensions and nested helpers, matching normal generated
   function semantics. The reserved `df` key is omitted from `extra_ns` so it
   cannot masquerade as a transform output.
4. On any exception, walk `exc.__traceback__` in reverse for the last frame whose
   `filename == "<string>"` and stash its `lineno` on `exc._user_code_line` before
   re-raising unchanged.
5. Read `execution_ns["df"]` as the result and coerce an eager
   `pl.DataFrame` to `.lazy()`. If the executed code never assigned `df`,
   raise `ExecutionError` naming the node's available inputs — never silently
   pass an input through as the result. Other values are returned unchanged
   despite the `_Frame` annotation (`# type: ignore` at the return); the
   execution engine's node-output check then raises `TypeError` if user code
   assigned `df` to a non-Polars value.

**Preamble and training-script distinction.** `executor._compile_preamble` calls
`validate_user_code(preamble)` and then `exec()`s `compile_project_code(preamble)`
with `safe_globals(pl=pl)` inside `project_code_module`, the same guard, compilation and
namespace as node code. `cli/_train.py`
executes the training script through `importlib`'s ordinary `exec_module()` path in
the CLI process without the guard. Imported module source is never validated; a
`utility` module executes with normal module builtins.
After preamble execution, the executor exports only names absent from the base
namespace and rejects direct bindings for dangerous module roots via
`_is_dangerous_preamble_binding`. Module objects, functions, and classes originating
from `os`, `sys`, `subprocess`, `shutil`, `signal`, `ctypes`, or `importlib` are
filtered before node-code namespace assembly. The check is deliberately shallow: it
does not recursively inspect containers/closures and does not constrain what the
preamble itself may execute, and node code may import those modules itself.

**Accident guard (`_sandbox.validate_user_code`)**
1. Look up `code` in `_validation_cache`; return immediately on a hit (the cache
   stores `True` only for accepted code).
2. `_try_parse_code(code)` — `ast.parse`; on `SyntaxError`, wrap as
   `UnsafeCodeError` with the original chained as `__cause__` (not cached).
3. `_AccidentGuard(_bound_names(tree)).visit(tree)` — a single full-tree walk; the
   first server-stopping call raises `UnsafeCodeError` naming the call and why it
   cannot run in the server. `_bound_names` collects every name bound anywhere in the
   tree (assignment targets, function/class definitions, arguments, import aliases,
   `except ... as name`, and the `match` binding forms `MatchAs`/`MatchStar`/
   `MatchMapping.rest`), so a call to the code's own `input` is not the builtin and
   passes.
4. On a clean walk, `_validation_cache.put(code, True)`.

**Restricted pickle/joblib load**
1. `safe_unpickle(path)` / `safe_joblib_load(path)` call
   `validate_project_path(path)` first — raises `ValueError` before any file I/O
   if the resolved path escapes the project root.
2. `validate_project_path`: `Path(path).resolve()`, then
   `os.path.commonpath([os.path.normcase(root), os.path.normcase(resolved)])`
   compared against the normcased root string. A `ValueError` from
   `commonpath` (different drives / mixed absolute-relative roots) is caught and
   treated as "not contained," not re-raised — both paths converge on the same
   `ValueError("... outside the project root ...")`.
3. `safe_unpickle` opens the file and drives `_RestrictedUnpickler(f).load()`
   inside `_estimator_version_mismatch_is_an_error()`. That context manager
   promotes scikit-learn's `InconsistentVersionWarning` — which
   `BaseEstimator.__setstate__` emits, and only emits, when the pickled
   `_sklearn_version` differs from the installed one — to
   `ArtifactVersionMismatchError`, carrying scikit-learn's own message with both
   versions. The filter is scoped to the load by `warnings.catch_warnings`, so
   process-wide filter state is untouched. A missing `sklearn` package means no
   promotion; any other failure importing it propagates.
   `_RestrictedUnpickler.find_class(module, name)` calls
   `_resolve_allowed_global(super().find_class, module, name)`.
4. `_resolve_allowed_global(resolver, module, name)`: if `(module, name)` is in
   `_ALLOWED_PICKLE_GLOBALS`, resolve it through `_resolve_installed` and return
   it as-is. Else if `(module, name)` is in `_ALLOWED_PICKLE_CLASSES`, resolve it
   the same way and return it only if `isinstance(obj, type)`; otherwise raise
   `_blocked_pickle_error(...)` with an "expected an allowlisted class" reason.
   Else raise `_blocked_pickle_error(..., "not in the allowlist")`.
   `_resolve_installed` calls `resolver(module, name)`; a `ModuleNotFoundError`
   whose `.name` is the entry's own top-level package (LightGBM and XGBoost are
   allowlisted but not installed everywhere) becomes
   `_blocked_pickle_error(..., "allowlisted, but `<package>` is not installed in
   this environment")`, and a `ModuleNotFoundError` deeper in the tree — a
   broken install — propagates unchanged.
5. `safe_joblib_load` defines a private `NumpyUnpickler` subclass whose
   `find_class` routes through `_resolve_allowed_global`, then uses joblib's
   file-object validation/decompression context and instantiates that subclass
   directly, driving its `load()` inside the same
   `_estimator_version_mismatch_is_an_error()` promotion as `safe_unpickle`. It
   never mutates `NumpyUnpickler.find_class` process-wide. Haute
   declares `joblib>=1.5,<2` directly because this restricted path requires the
   private file-object validator and native-byte-order constructor contract
   introduced in joblib 1.5. A missing `joblib` package logs a warning and falls
   back to `safe_unpickle(validated)`; an installed joblib missing those private
   APIs, or exposing an incompatible unpickler constructor, raises `RuntimeError`.
   If joblib's file-object validator reports a legacy string-backed persistence
   format, the loader raises `ValueError` rather than bypassing the restricted
   subclass.

**Local session enforcement (per-request, `server.py` + `_local_security.py`)**
1. `LocalTrustedHostMiddleware` runs first (outermost of the two added
   middlewares — Starlette applies middleware in reverse registration order, and
   `add_middleware(LocalTrustedHostMiddleware, ...)` is registered *after*
   `LocalSessionMiddleware`, so it wraps/executes before it). Non-HTTP/WebSocket
   scopes pass through. HTTP/WebSocket requests carrying `Forwarded` or any
   `X-Forwarded-*` authority/address header return `400`; a malformed or
   Host not present in the validated loopback `allowed_hosts` configuration does
   the same. Entries without a port allow any port on that exact host; entries
   with a port require it exactly. There is no wildcard/remote allow mode.
2. `LocalSessionMiddleware.dispatch`: short-circuits if auth is explicitly disabled
   or the path is not `/api/*`. `POST /api/session/bootstrap` and `OPTIONS` require
   `_origin_state(...) == "trusted"`; bootstrap then reaches the route that writes
   the token only as an HttpOnly, SameSite=Strict cookie. Other requests reject an
   untrusted Origin, then require `_request_token_matches` (constant-time match
   against the HttpOnly session cookie). A missing Origin is
   accepted only after that credential check succeeds.
3. `websocket_rejection_reason(headers, scope_scheme=...)` runs before
   `websocket.accept()`: auth-disabled short-circuit, explicit exact matching
   Origin, then the session cookie. Browser WebSockets receive the HttpOnly cookie
   automatically; URL query parameters are never read.

**Runtime local-file containment (`_path_resolution.py` + execution core)**
1. `canonical_dataframe_execution_graph` derives the execution root from the
   configured sandbox project root (`_sandbox._get_project_root()`), which is
   cwd in normal CLI use. If the graph names an absolute pipeline outside that
   project, the pipeline's parent is the explicit root instead. A source path
   lexically inside the configured project that resolves outside through a
   symlink is rejected rather than treated as an external selection. HTTP
   routes separately require submitted `source_file` values to remain inside
   the configured project, so only direct/operator-controlled execution can
   opt into the external-pipeline root.
2. Every local runtime input field (`apiInput`/file or lakehouse `dataInput`,
   `externalFile`, model-score feature-contract files, and file-sourced
   optimiser artifacts) passes through `resolve_runtime_file_path(...,
   enforce_project_root=True)`. Separator normalization happens before `Path`
   construction and `resolve()` follows symlinks before containment is checked.
   A path whose components include a DOS device name is rejected as
   `MalformedRuntimePathError` on **every** platform, using the same
   `is_windows_reserved_filename` predicate as the save-time guards. Windows
   resolves such a component to the device rather than to a file in its
   directory, so the path leaves its root and previously surfaced as a
   misleading containment failure, while the identical configured path was an
   ordinary file on Linux and macOS. Rejecting everywhere keeps one project
   behaving the same on every machine instead of failing only after it moves.
   The match is on the exact stem before the first dot, so `CONTRACT.json` and
   `COM10.json` remain ordinary names.
3. `_execute_lazy` and `_execute_eager_core` run under
   `runtime_project_root_scoped`. The decorator accepts the declared `graph`
   argument positionally or by keyword and rejects a missing/non-`PipelineGraph`
   value before opening the scope. `_builders._resolve_runtime_data_path` repeats
   the containment check against that context-local root at the final read seam.
   Database/Databricks/named-provider identifiers are not local path fields and
   retain their provider-owned external-resource semantics.
4. `_builders._build_data_input` invokes `_exec_user_code` exactly once for non-empty
   code after provider resolution. `DataOutputConfig` rejects `code`. Source-cache
   paths are derived only from validated digest/generation identifiers under the
   cache root; output execution validates both final and unique staging paths and
   rechecks containment before replace. Resolved secrets and live provider/cache
   objects are excluded from the user namespace, persisted metadata, and surfaced
   errors.

**`.gitignore` guard writer (`ensure_gitignore_guards`)**
1. If `project_dir/.gitignore` doesn't exist, write all `GITIGNORE_GUARD_ENTRIES`
   newline-joined (plus trailing newline) and return the full list as "added."
2. Else read existing content with `errors="replace"` (never raises on non-UTF-8
   bytes), split into a `set` of lines, and compute `missing` as the ordered
   subset of `GITIGNORE_GUARD_ENTRIES` not already present.
3. If anything is missing, append `"\n# Haute\n" + "\n".join(missing) + "\n"` to
   the file; return `missing` (empty list means byte-identical no-op).

## Edge cases and invariants

- **Case-insensitive filesystem path containment.** `validate_project_path` folds
  both the root and the resolved path through `os.path.normcase` before computing
  `commonpath`, specifically so a case-variant traversal (`PROJECT/../SECRET` on
  NTFS/APFS) cannot slip past a case-sensitive string-prefix check while still
  resolving to the same real file on disk. On case-sensitive POSIX filesystems
  `normcase` is the identity, so behavior is unchanged there.
- **Restricted joblib loading is instance-scoped.** The restricted subclass
  preserves concurrent safety without a lock and without exposing a temporary
  process-wide shim to unrelated joblib callers.
- **The guard inspects calls by bare name only.** An alias (`f = input; f()`)
  passes the walk, and the namespace omits the four names, so the alias raises
  `NameError` when it runs. Reaching them deliberately (`import builtins`) is not an
  accident and is not stopped.
- **`safe_globals` never returns an aliased mutable namespace.** Each call builds
  a fresh `inner` dict copied from `_EXEC_BUILTINS`, copies its public names into the
  returned `ns` and sets `ns["__builtins__"] = inner` (so nested scopes like
  comprehensions resolve names correctly) — so mutating one exec call's builtins
  cannot leak into a subsequent `safe_globals()` call's namespace.
- **`safe_joblib_load` falls back only when the top-level `joblib` package is
  absent.** That path degrades to `safe_unpickle`, not unrestricted loading.
  An installed joblib with missing/incompatible private APIs fails loudly.
- **A validated-but-evicted cache entry is not "trusted by omission."**
  `_validation_cache` is bounded at 1024 entries; when it's flooded past that
  size, older entries (including ones already proven safe) are evicted and
  re-validated from scratch on next use rather than assumed safe — there is no
  code path that treats "not in cache" as "known safe."
- **IPv6 `Host`/`Origin` parsing handles the bracketed literal form
  correctly**, including rejecting malformed variants (`[::1]evil`,
  `[::1]:notaport`, a bracketed non-IPv6 literal like `[localhost]`) rather than
  silently accepting or crashing. `_normalise_authority` and
  `_origin_authority` return `None` for those inputs and catch `urlsplit` port
  failures.

## Error handling

- `UnsafeCodeError` (extends `HauteError`) — raised by `validate_user_code` for a
  server-stopping call and for unparseable code (chaining the `SyntaxError` as
  `__cause__`). Propagates uncaught through `_exec_user_code` except for the
  syntax-error case, which is unwrapped back to a plain `SyntaxError` before
  re-raising.
- `pickle.UnpicklingError` — raised by `_blocked_pickle_error` (a small factory
  producing a uniform message naming the rejected symbol and the two allowlist
  constants to extend) for every rejected pickle/joblib global. Not caught inside
  this component; propagates to the caller (`_io.load_external_object` and its
  callers).
- `ValueError` — raised by `validate_project_path` for any path resolving outside
  the project root (including the `commonpath` mixed-root case), and by
  `safe_joblib_load` when joblib exposes a legacy string-backed persistence format
  that cannot use the restricted unpickler. Not caught inside this component.
- `RuntimeError` — raised when installed joblib lacks the private
  `NumpyUnpickler`/file-object validation APIs required to enforce the allowlist,
  or when its private unpickler constructor is incompatible with the supported
  joblib 1.x contract.
- Local-session/host failures never raise into application code — they are
  short-circuited as HTTP `400`/`403` `JSONResponse`s (or a plain-text `400` for
  the trusted-host check) or a WebSocket close before `accept()`; there is no
  exception type associated with a rejection at this layer.
- `_env.py` returns a default only for an unset variable. Explicit malformed,
  non-finite, zero, or negative values raise `RuntimeError`; an optional integer
  returns `None` only when the variable is absent.
- `ArtifactVersionMismatchError` (extends `HauteError`, defined in `_sandbox.py`) —
  raised by `_estimator_version_mismatch_is_an_error` during `safe_unpickle` or
  `safe_joblib_load` when a persisted scikit-learn estimator's `_sklearn_version`
  mismatches the installed library version. Propagates uncaught to the caller
  (`_io.load_external_object`).
- `ExecutionError` (extends `HauteError`, imported from `haute.errors`) — raised
  by `_exec_user_code` when the input name `'df'` conflicts with the reserved
  output name for polars node code, or when node code finishes without assigning
  its result to `'df'`. Propagates to the node executor or pipeline runner.
- `ensure_gitignore_guards` does not raise on non-UTF-8 existing `.gitignore`
  content (`errors="replace"` on read); ordinary filesystem errors (permission
  denied, disk full) propagate uncaught.

## Testing

- `tests/test_node_code_trust_boundary.py` — the ENG-T04 witnesses through the real
  `_exec_user_code` entry point: `input()`, `exit()`, `quit()` and `breakpoint()` are
  rejected before execution with a sentinel file untouched, ordinary Python (a class,
  `getattr`, `type`, `vars`, `global`, an import) runs in a node, a Polars transform
  runs, and (pinning the accepted trust decision, not a defect) a synthetic
  environment marker and an outside-project write remain reachable through the
  injected Polars module.

- `tests/test_host_binding.py` verifies loopback-only host validation, CLI/config precedence, trusted-host middleware, token non-exposure, and loopback URL formatting.

- `tests/test_sandbox.py` — the primary suite for `_sandbox.py`. `TestAccidentGuard`
  pins the guard: each server-stopping call is rejected, and classes,
  `global`/`nonlocal`, `getattr`/`type`/`vars`, imports, dunder access, `open` and a
  code-defined `input` pass and run. `TestSafeGlobals`, `TestSafeGlobalsIsolation` and
  `TestValidateUserCode` cover the namespace and the parse/cache behaviour;
  `TestBoundedValidationCache` pins the bounded cache. A "Gap analysis" section
  (`TestJoblibFindClassWeakerThanPickle`, `TestPickleAllowlistDotAnchoring`,
  `TestJoblibMonkeyPatchThreadSafety`) and the pickle classes pin the restricted
  unpickler.
- `tests/test_sandbox_coverage_gaps.py` — targets the *accept* arms of
  `_RestrictedUnpickler.find_class`/the restricted joblib subclass specifically (the exhaustive
  suite above concentrates on the *reject* arms): exact 2-tuple accepts for both
  the `_ALLOWED_PICKLE_GLOBALS` and `_ALLOWED_PICKLE_CLASSES` tiers, the top-level
  joblib-package-absent → `safe_unpickle` fallback, and installed-private-API
  failure.

- `tests/test_user_exec_imports.py` — a structural regression pin (not a
  behavioral test of `_exec_user_code` itself): asserts no file under `src/haute`
  imports `_exec_user_code` from `haute.executor` (the old location) and that
  `haute.executor` does not re-export it, keeping the single canonical import
  path (`haute._user_exec`) from silently regressing after the module split.
- `tests/test_env_lazy_accessors.py` — unit tests for the three `_env.py` parse
  helpers directly, plus a parametrized behavioural sweep over lazy accessors
  and an AST-derived inventory of direct production environment reads. The AST
  guard resolves literal module constants and common `os` import aliases,
  requires positive numeric Haute knobs to use `_env.py`, and permits only a
  reviewed explicit exception set for semantics those helpers do not own. A
  new direct read therefore fails without updating a manually parallel list.
- `tests/test_gitignore_guard.py` — pins the guard-entry list's required
  contents (including the explicit "stable-layer JSON must NOT be ignored"
  assertion) and exercises `ensure_gitignore_guards`'s four behaviors: create,
  append-missing-only, no-op-when-complete, and non-UTF-8 tolerance.
- `tests/test_security_gaps.py::TestW8bLocalSessionProtection` — end-to-end
  `TestClient`/`websocket_connect` coverage of `_local_security.py` through the
  real FastAPI app: missing/empty token, foreign Origin, non-ASCII token bytes,
  malformed IPv6 Host/Origin variants, and a same-file check that a legitimate
  relative sink path *inside* the project root is allowed through once
  authenticated (proving the guards aren't over-broad). Also covers the
  project-root path-escape rejection at the executor level
  (`write_data_output`/`ValueError` match) as a companion to the HTTP-level checks.
- `tests/test_local_security.py` is the dedicated local-browser boundary suite:
  exact bootstrap Origin/Host matching, HttpOnly/no-store cookie creation,
  absent/mismatched/forwarded rejection, cookie-authenticated API/WebSocket
  success, absent-Origin WebSocket and query-token rejection, and a secret-corpus
  assertion over the served SPA and rejection surfaces.
- `tests/test_path_resolution.py`, `tests/test_path_resolution_properties.py`,
  `tests/test_execute_lazy_paths.py`, and the nested-input regressions cover
  direct eager/lazy containment, HTTP source confinement across pipeline,
  modelling, and optimiser route families, submodel flatten-before-validation,
  mixed separators, absolute/traversal/symlink escapes, and the explicitly
  selected direct-execution external-pipeline root exception.
- `tests/test_property.py` — metamorphic runtime-path properties: adding `.`
  components never changes the resolved file, parent traversal is always
  rejected, and DOS device names are rejected identically on every platform
  while names merely sharing a device prefix still resolve. The generated
  component strategy excludes device names, so the equivalence property states
  what it claims rather than colliding with that rejection.
- `tests/test_path_traversal_advanced.py` — adversarial config/model path resolution:
  symlink escapes, Windows mixed separators, null bytes, overlong paths,
  `config_path_for_node`, function-name lookup, and project-path edge cases.
- `tests/test_path_traversal_fixes.py` — route/config traversal regressions for
  optimiser save, submodel lookup/create/dissolve, dissolve-sidecar files, and the
  shared `validate_safe_path` boundary.
- `tests/test_write_sandbox_guard.py` and `tests/test_write_sandbox_lint.py` — prove
  restricted tests cannot write outside their per-test roots and statically enforce
  guarded filesystem APIs. `tests/_write_sandbox.py::STRICT_FILES` is the maintained
  converted-module index, and the CI `platform-smoke` lane exercises this guard on
  supported operating systems.
- Caller-level I/O/cache/executor regressions cover input/output traversal, symlink
  swaps, malformed cache digest/generation identifiers, staging-path containment,
  exactly-once input-code execution, output-code rejection, and secret-free
  namespaces/failures.

## Canonical sandbox payload

Under the [canonical-only format policy](../README.md#canonical-only-format-policy),
the sandbox accepts only the current generated payload namespace. Historical module aliases and
shim globals are removed; allow-list and containment behavior for the current payload is unchanged.
