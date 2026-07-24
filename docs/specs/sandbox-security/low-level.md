# Sandbox Security — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/_sandbox.py` | AST validation of user code (`validate_user_code`), restricted `exec()` globals (`safe_globals`), project-root path containment (`validate_project_path`), and the restricted pickle/joblib unpicklers (`safe_unpickle`, `safe_joblib_load`). |
| `src/haute/_user_exec.py` | The single `exec()` call site for pipeline node code (`_exec_user_code`): namespace assembly, validation call, execution, and traceback line annotation. |
| `src/haute/_local_security.py` | Local-session protection for the FastAPI/WebSocket server: session-token generation/comparison, trusted-Host middleware, trusted-Origin checks, HTTP middleware and WebSocket pre-accept rejection helper. |
| `src/haute/_gitignore_guard.py` | The shared `.gitignore` guard-entry tuple and the idempotent append-if-missing writer (`ensure_gitignore_guards`) used by both `haute init` and the unborn-repo commit seed. |
| `src/haute/_env.py` | Lazy, fail-soft environment-variable parsing helpers (`float_env`, `int_env`, `optional_int_env`) used by selected request-timeout, optimiser chunk/partition, solver-timeout, training-history, and assistant-loop accessors. Other numeric environment settings use component-owned parsers. |

## Key types and data structures

- **`UnsafeCodeError`** (`_sandbox.py`) — a `HauteError` subclass raised by
  `validate_user_code`/`_ASTValidator` for any blocked construct or unparseable
  code. Carries the original `SyntaxError` as `__cause__` when validation failed
  because the code could not be parsed at all.
- **`_ASTValidator(ast.NodeVisitor)`** (`_sandbox.py`) — the structural gate.
  Constructed per validation call with `allow_imports: bool` and
  `polars_alias_shadowed: bool` (whether user code rebinds the name `pl` anywhere,
  computed by `_bound_names`). Overrides `visit_Attribute`, `visit_Call`,
  `visit_Subscript`, `visit_Import`, `visit_ImportFrom`, `visit_ClassDef`,
  `visit_AsyncFunctionDef`, `visit_Global`, `visit_Nonlocal`; everything else falls
  through to `generic_visit` (i.e. is implicitly allowed, including `visit_Lambda`
  — there is no override for it).
- **Blocklist frozensets** (`_sandbox.py`, module-level constants):
  `_BLOCKED_BUILTINS` (removed from the runtime `exec()` namespace),
  `_BLOCKED_ATTRS` (dunder attribute names rejected by `visit_Attribute`),
  `_BLOCKED_FRAME_ATTRS` (non-dunder frame/traceback/generator attribute names,
  e.g. `tb_frame`, `f_globals`, `gi_frame`), `_BLOCKED_CALLS` (bare-name calls
  rejected by `visit_Call`). These four lists are independent — a name can appear
  in one without appearing in another, and each is exercised by its own test class
  in `tests/test_sandbox.py`.
- **`_FORMAT_DUNDER_FIELD`** (compiled regex) and **`_FORMAT_METHOD_NAMES`**
  (`{"format", "format_map", "vformat", "get_field", "format_field"}`) — drive
  `_ASTValidator._check_format_call`, the guard against dunder traversal via
  `str.format`-family calls.
- **`_ALLOWED_PICKLE_GLOBALS: frozenset[tuple[str, str]]`** — exact
  `(module, qualname)` pairs for scaffolding *functions* (numpy reconstruction
  helpers, `copyreg` helpers, `_codecs.encode`, pandas block/index reconstruction
  helpers, builtin container/scalar constructors). Returned verbatim when matched.
- **`_ALLOWED_PICKLE_CLASSES: frozenset[tuple[str, str]]`** — exact
  `(module, qualname)` pairs for model/data *classes* (numpy `dtype`/`ndarray`,
  pandas `DataFrame`/`Series`/`Index`/`RangeIndex`/`BlockManager`/
  `SingleBlockManager`, polars `DataFrame`/`Series`, sklearn/catboost/lightgbm/
  xgboost estimator classes, `joblib.numpy_pickle.NumpyArrayWrapper`). Resolved
  and then checked with `isinstance(obj, type)` before being trusted.
- **`_RestrictedUnpickler(pickle.Unpickler)`** — overrides `find_class` to call
  `_resolve_allowed_global`.
- **`_validation_cache: LRUCache[tuple[str, bool], bool]`** — bounded at
  `_VALIDATION_CACHE_MAX_SIZE = 1024`, keyed on `(code, allow_imports)`, backed by
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
- **`LocalSessionMiddleware(BaseHTTPMiddleware)`** — gates every `/api/*` HTTP
  request behind Origin-then-token checks (skips `/api/session`'s prefix like any
  other `/api/*` route — it enforces before endpoint code runs).
- **`GITIGNORE_GUARD_ENTRIES: tuple[str, ...]`** (`_gitignore_guard.py`) — the
  ordered, fixed set of paths every project's `.gitignore` must contain:
  `.env`, `.haute/`, `impact_report.md`, `.haute_cache/`, `mlruns/`, `data/`,
  `.venv/`.

## Control flow

**Pipeline code execution (`_user_exec._exec_user_code`)**
1. Build `local_ns` — seeds `pl` (polars), positional source dataframes under
   `src_names`, an `orig`→`inst` name-mapping fallback via
   `build_instance_mapping` (execution-engine helper), a `df` alias for the first
   source, then any `extra_ns` overrides.
2. Call `validate_user_code(code)` (imports disabled by default). On
   `UnsafeCodeError` whose `__cause__` is a `SyntaxError`, re-raise the bare
   `SyntaxError` instead — this normalizes the error type callers see for a plain
   typo versus a genuine security rejection.
3. `exec(code, safe_globals(pl=pl, **extra_ns), local_ns)` — globals and locals
   are deliberately separate dicts (globals restricted, locals seeded with the
   dataframes) so name resolution inside `exec`'d code still sees the source
   variables.
4. On any exception, walk `exc.__traceback__` in reverse for the last frame whose
   `filename == "<string>"` and stash its `lineno` on `exc._user_code_line` before
   re-raising unchanged.
5. Read `local_ns.get("df", ...)` as the result and coerce an eager
   `pl.DataFrame` to `.lazy()`. Other values are returned unchanged despite the
   `_Frame` annotation (`# type: ignore` at the return); the execution engine's
   node-output check then raises `TypeError` if user code assigned `df` to a
   non-Polars value.

**Preamble and training-script distinction.** `executor._compile_preamble` calls
`validate_user_code(..., allow_imports=True)` and then `exec()`s the preamble with
`safe_globals(allow_imports=True)`, so its own AST is still checked but imports are
allowed. `cli/_train.py` also validates with `allow_imports=True`, then executes the
file through `importlib`'s ordinary `exec_module()` path rather than `safe_globals`.
In both cases, imported module source is outside the recursive scope of
`validate_user_code`; a `utility` module executes with normal module builtins.

**AST validation (`_sandbox.validate_user_code` → `_validate_user_code_cached`)**
1. Look up `(code, allow_imports)` in `_validation_cache`; return immediately on a
   hit (cache stores `True` only for code that validated clean).
2. `_try_parse_code(code)` — `ast.parse`; on `SyntaxError`, wrap as
   `UnsafeCodeError` with the original chained as `__cause__` (not cached).
3. Compute `polars_alias_shadowed = "pl" in _bound_names(tree)` —
   `_bound_names` conservatively collects every name bound anywhere in the tree
   (assignment targets, function/class defs, function args, import aliases —
   with a carve-out so `import polars as pl` itself does not count as shadowing —
   `except ... as name`, and the newer `match` binding forms `MatchAs`/
   `MatchStar`/`MatchMapping.rest`). This makes the `pl.format(...)` carve-out in
   `_check_format_call` a static-but-conservative decision: any binding of `pl`
   anywhere in the module, even one never reached by the `pl.format` call site,
   disables the carve-out for the whole validation pass.
4. `_ASTValidator(allow_imports=..., polars_alias_shadowed=...).visit(tree)` — a
   single full-tree walk; the first blocked construct encountered raises and
   aborts the walk (no attempt to collect all violations).
5. On a clean walk, `_validation_cache.put((code, allow_imports), True)`.

**`.format()`-family call guard (`_ASTValidator._check_format_call`)** — triggered
from `visit_Call` whenever `node.func` is an `ast.Attribute` whose `.attr` is one
of `_FORMAT_METHOD_NAMES`. Receiver shapes:
- `ast.Name` equal to `"pl"` and not `polars_alias_shadowed` → allowed
  unconditionally (trusted as the polars module's `pl.format(...)` builder).
- `ast.Constant` holding a `str` → allowed unless `_FORMAT_DUNDER_FIELD` matches
  (a `{...}` field containing `.` or `[` followed by a `__`-bracketed dunder name)
  anywhere in the literal.
- Anything else (`BinOp` concatenation, a name-bound template variable, a call
  result, an f-string, …) → always rejected — the validator cannot statically
  vet what the template will be at runtime.

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
3. `safe_unpickle` opens the file and drives `_RestrictedUnpickler(f).load()`.
   `_RestrictedUnpickler.find_class(module, name)` calls
   `_resolve_allowed_global(super().find_class, module, name)`.
4. `_resolve_allowed_global(resolver, module, name)`: if `(module, name)` is in
   `_ALLOWED_PICKLE_GLOBALS`, call `resolver(module, name)` and return it as-is.
   Else if `(module, name)` is in `_ALLOWED_PICKLE_CLASSES`, call `resolver(...)`
   and return it only if `isinstance(obj, type)`; otherwise raise
   `_blocked_pickle_error(...)` with an "expected an allowlisted class" reason.
   Else raise `_blocked_pickle_error(..., "not in the allowlist")`.
5. `safe_joblib_load`: acquires `_joblib_lock` (module-level `threading.Lock`),
   captures `NumpyUnpickler.find_class` as `original_find_class` **inside** the
   lock (see Edge cases below), monkey-patches `NumpyUnpickler.find_class` with a
   closure that routes through `_resolve_allowed_global(lambda m, n:
   original_find_class(self, m, n), module, name)`, calls `joblib.load(validated)`,
   and restores `original_find_class` in a `finally` — all while holding the lock,
   so the patch-call-restore sequence is atomic with respect to other
   `safe_joblib_load` callers. If `joblib.numpy_pickle.NumpyUnpickler` cannot be
   imported at all, logs a warning and falls back to `safe_unpickle(validated)`
   (still project-root-validated, still going through the same
   `_resolve_allowed_global` gate).

**Local session enforcement (per-request, `server.py` + `_local_security.py`)**
1. `LocalTrustedHostMiddleware` runs first (outermost of the two added
   middlewares — Starlette applies middleware in reverse registration order, and
   `add_middleware(LocalTrustedHostMiddleware, ...)` is registered *after*
   `LocalSessionMiddleware`, so it wraps/executes before it). If
   `allow_any` (`*` appears in the startup-time allowed-host list) or the scope type
   isn't `http`/`websocket`, passes through unconditionally. Otherwise normalizes
   the `Host` header and checks it against the normalized allowlist (supporting
   `*.suffix` wildcard patterns); a mismatch returns a plain-text `400` before the
   request reaches routing.
2. `LocalSessionMiddleware.dispatch`: short-circuits (calls `call_next`
   immediately) if `local_session_auth_disabled()` or the path doesn't start with
   `/api/`. Otherwise: `_is_local_origin(headers)` first (an absent `Origin`
   header is treated as trusted — same-origin browser navigation and non-browser
   clients don't send one); a foreign origin returns `403` immediately. `OPTIONS`
   requests then pass through (CORS preflight has no credentials to check).
   Every other method requires `_token_matches(headers[SESSION_TOKEN_HEADER])`,
   which strips whitespace, rejects empty, and compares via
   `hmac.compare_digest` inside a `try/except TypeError` (guards against a
   non-`str` header value reaching `compare_digest`); a mismatch returns `403`.
3. `websocket_rejection_reason(headers, query_params)` (called from
   `server.ws_sync` before `websocket.accept()`) mirrors the HTTP dispatch order:
   auth-disabled short-circuit, then Origin, then token — but the token may come
   from either the `x-haute-session-token` header *or* the
   `haute_session_token` query parameter (`_query_token`), since browser
   `WebSocket` clients cannot set arbitrary headers.

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
- **`joblib` monkey-patch capture must happen inside the lock, not before it.**
  An earlier version captured `original_find_class` before acquiring
  `_joblib_lock`; under concurrent calls, a thread could capture a *different*
  thread's already-installed restricted shim as "the original" and later restore
  that shim permanently instead of the true original, silently disabling the
  restriction for every subsequent `joblib.load()` in the process. The fix moved
  the capture inside the lock (`tests/test_sandbox.py::TestJoblibMonkeyPatchThreadSafety`
  pins this with a 4-thread barrier test and a sentinel-`find_class`
  determinism test).
- **A `pl.format(...)` carve-out that stops applying the moment `pl` is
  reassigned anywhere in the module** — not just before the call site
  lexically. This is intentionally conservative (a false rejection is preferred
  over risking a template smuggled through a shadowed `pl`).
- **`ast.parse` does not constant-fold string concatenation**, so
  `('{0.' + '__globals__}').format(g)` or a name-bound `tmpl = '{0.__globals__}';
  tmpl.format(g)` would defeat a literal-only scan of the *first argument* to
  `.format`. The guard instead inspects the *receiver* of the `.format` call
  (the string/expression `.format` is called *on*) and requires it to be a bare
  string literal, rejecting any non-literal receiver outright rather than trying
  to prove a concatenation or variable is safe.
- **The AST validator has no `visit_Lambda`.** Lambda definitions (including
  nested lambdas) pass validation unconditionally; only their *bodies* are
  walked and subject to the same call/attribute blocks as any other expression.
  This is documented behavior (`tests/test_sandbox.py::TestLambdaAllowedInSandbox`),
  not a gap needing a fix — a lambda is just another callable object, and the
  restrictions apply uniformly to what it's allowed to *do* when called.
- **`type`, `getattr`, `vars`, `dir`, `hasattr`, `setattr`, `delattr` are blocked
  at both layers independently.** The AST layer blocks *calling* these names;
  a bare reference (`fn = getattr`) passes AST validation because only
  `ast.Call` nodes are inspected, not `ast.Name` references. The runtime layer
  closes this gap by omitting these names from `_SAFE_BUILTINS` entirely, so
  `fn = getattr` still raises `NameError` at `exec()` time once `fn` is looked up
  from the restricted globals.
- **`__closure__` is blocked, but `__init__`/`__name__`/`__doc__`/`__qualname__`/
  `__annotations__` are not.** The blocklist is scoped to dunders with a known
  escape-relevant use (type traversal, frame access, closure-cell extraction),
  not every dunder — an unlisted dunder is reachable and callable
  (`tests/test_sandbox.py::TestNonBlockedDunders`).
- **`safe_globals` never returns an aliased mutable namespace.** Each call builds
  a fresh `inner` dict copied from `_SAFE_BUILTINS`, sets `inner["__builtins__"] =
  inner` (so nested scopes like comprehensions resolve names correctly), then
  copies *that* into the returned `ns` — so mutating one exec call's builtins
  (e.g. code that somehow rebinds a name in `__builtins__`) cannot leak into a
  subsequent `safe_globals()` call's namespace.
- **`safe_joblib_load`'s `ImportError` fallback still enforces the allowlist** —
  it degrades to `safe_unpickle`, not to unrestricted `joblib.load`.
- **`_bound_names` treats `import polars as pl` as not-shadowing** (it is the
  trusted binding the carve-out exists for) but treats every *other* way of
  binding the name `pl` — assignment, function parameter, `except ... as pl`,
  `match`/`case` binding forms, a function or class literally named `pl` — as
  shadowing.
- **A validated-but-evicted cache entry is not "trusted by omission."**
  `_validation_cache` is bounded at 1024 entries; when it's flooded past that
  size, older entries (including ones already proven safe) are evicted and
  re-validated from scratch on next use rather than assumed safe — there is no
  code path that treats "not in cache" as "known safe."
- **IPv6 `Host`/`Origin` parsing handles the bracketed literal form
  correctly**, including rejecting malformed variants (`[::1]evil`,
  `[::1]:notaport`, a bracketed non-IPv6 literal like `[localhost]`) rather than
  silently accepting or crashing — `_normalise_host_value` returns `""` (never
  matches) for any of these, and `_origin_host` catches `ValueError` from
  `urlsplit` the same way.

## Error handling

- `UnsafeCodeError` (extends `HauteError`) — raised by `validate_user_code`/
  `_ASTValidator` for every blocked construct and for unparseable code (chaining
  the `SyntaxError` as `__cause__`). Propagates uncaught through
  `_exec_user_code` except for the syntax-error case, which is unwrapped back to
  a plain `SyntaxError` before re-raising. `cli/_train.py` catches
  `UnsafeCodeError` at the top level and exits the process with `SystemExit(1)`
  and a user-facing message.
- `pickle.UnpicklingError` — raised by `_blocked_pickle_error` (a small factory
  producing a uniform message naming the rejected symbol and the two allowlist
  constants to extend) for every rejected pickle/joblib global. Not caught inside
  this component; propagates to the caller (`_io.load_external_object` and its
  callers).
- `ValueError` — raised by `validate_project_path` for any path resolving outside
  the project root (including the `commonpath` mixed-root case). Not caught
  inside this component.
- Local-session/host failures never raise into application code — they are
  short-circuited as HTTP `400`/`403` `JSONResponse`s (or a plain-text `400` for
  the trusted-host check) or a WebSocket close before `accept()`; there is no
  exception type associated with a rejection at this layer.
- `_env.py`'s parsers never raise — a malformed value is caught internally
  (`ValueError` from `float()`/`int()`) and converted into a logged warning plus
  the default return value.
- `ensure_gitignore_guards` does not raise on non-UTF-8 existing `.gitignore`
  content (`errors="replace"` on read); ordinary filesystem errors (permission
  denied, disk full) propagate uncaught.

## Testing

- `tests/test_sandbox.py` — the primary suite for `_sandbox.py`. `TestSafeGlobals`
  and `TestValidateUserCode` cover the happy-path/blocked-construct matrix for
  both layers exhaustively (one test per blocked builtin/attr/call). A large
  "Adversarial sandbox-escape regression tests" section (`TestTypeBypass`,
  `TestSubclassWalking`, `TestFormatStringExploitation`,
  `TestExceptionTracebackExploit`, `TestGeneratorFrameAccess`,
  `TestDecoratorFrameCapture`, `TestListComprehensionScopeLeaking`,
  `TestLambdaGetattr`, `TestPickleWithinExec`, `TestImportViaBuiltinsDict`)
  each encode a named, real CPython sandbox-escape technique and assert it is
  blocked — these are regression pins, not exploratory tests. A "Gap analysis"
  section (`TestJoblibFindClassWeakerThanPickle`, `TestPickleAllowlistDotAnchoring`,
  `TestJoblibMonkeyPatchThreadSafety`, `TestLambdaAllowedInSandbox`,
  `TestAllowImportsPrivilegeEscalation`, `TestBoundedValidationCache`,
  `TestNonBlockedDunders`) documents specific historical findings, several with
  both a "still vulnerable" characterization test and a "FIX: now blocked" pin
  post-remediation, kept side by side deliberately as living documentation of
  what changed and why.
- `tests/test_sandbox_coverage_gaps.py` — targets the *accept* arms of
  `_RestrictedUnpickler.find_class`/the joblib shim specifically (the exhaustive
  suite above concentrates on the *reject* arms): exact 2-tuple accepts for both
  the `_ALLOWED_PICKLE_GLOBALS` and `_ALLOWED_PICKLE_CLASSES` tiers, the joblib
  `ImportError`→`safe_unpickle` fallback, and `match`/`case`-bound `pl` alias
  shadowing (`MatchStar`, `MatchMapping.rest`) not covered by the main suite's
  simpler shadowing tests.

  > NOTE: this test file's own docstrings/comments describe some of these
  > accept-arm cases as "prefix match" (e.g. `module.startswith(prefix)`), a
  > leftover from an earlier package-prefix allowlist design. The current
  > `_resolve_allowed_global` in `_sandbox.py` has no prefix-matching logic at
  > all — both `_ALLOWED_PICKLE_GLOBALS` and `_ALLOWED_PICKLE_CLASSES` are
  > exact `(module, qualname)` tuple lookups; the tests still pass and still
  > pin the accept arms correctly, but the "prefix" terminology in the test
  > file's comments is stale.
- `tests/test_user_exec_imports.py` — a structural regression pin (not a
  behavioral test of `_exec_user_code` itself): asserts no file under `src/haute`
  imports `_exec_user_code` from `haute.executor` (the old location) and that
  `haute.executor` does not re-export it, keeping the single canonical import
  path (`haute._user_exec`) from silently regressing after the module split.
- `tests/test_env_lazy_accessors.py` — unit tests for the three `_env.py` parse
  helpers directly, plus a parametrized sweep (`_ACCESSOR_CASES`) over every
  known call site across the codebase that wraps a knob in a lazy accessor
  function, asserting each honours a post-import env override and degrades to
  its default on a malformed value. This is the regression suite for the
  "frozen at import" defect class described in the file's docstring.
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
  (`execute_sink`/`ValueError` match) as a companion to the HTTP-level checks.
- No dedicated file tests `_local_security.py`'s pure functions
  (`_normalise_host_value`, `_origin_host`, `_token_matches`) in isolation
  outside of the end-to-end `TestClient` coverage in `test_security_gaps.py`
  and references from `tests/test_server.py`/`tests/conftest.py`; coverage of
  the IPv6-parsing edge cases specifically comes through the parametrized
  malformed-host/origin cases there rather than unit tests of the helpers
  directly.

> NOTE: `test_env_lazy_accessors.py`'s `_ACCESSOR_CASES` table is a manually
> maintained parallel list of migrated lazy-knob call sites; a new knob added
> elsewhere in the codebase that forgets to use `haute._env`'s helpers (or
> forgets a corresponding entry here) would not be caught by this test file
> itself — the regression protection is only as complete as the table's upkeep.

## Approved change contract — 0.7.0 unified data-I/O security

The implementation plan is
[`F_0.7.0_data-io-convergence.plan.md`](../../trip/plans/F_0.7.0_data-io-convergence.plan.md).

- `_builders._build_data_input` invokes `_user_exec._exec_user_code` exactly once for non-empty
  input code, after provider dispatch and before returning the frame. No provider-specific
  `exec()` or expanded `safe_globals` path is added. Config validation rejects a `code` key on
  `DataOutputConfig`.
- File/lakehouse provider adapters validate direct locators against the project root before
  open. Credential-free raw SQLite input/output URIs resolve relative to the pipeline and are
  subject to the same project-root containment; named connection values stay provider-owned.
  `SourceCacheStore` derives paths only beneath its validated project cache root from a checked
  digest/generation id. Unified output execution validates the final destination and unique
  sibling staging path before writer invocation and rechecks containment before replace.
- Add caller-level tests in the I/O/cache/executor suites for project escape, symlink swaps,
  malformed digest/generation ids, staging-path containment, and secret-free user namespaces.
  Extend sandbox tests only for the retained input-code entry point; the underlying AST/global
  allow-list contract does not fork by provider.
