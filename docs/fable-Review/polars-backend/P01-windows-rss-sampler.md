# P01 — Windows RSS sampler reloads DLLs on every sample

**Severity:** HIGH (interactive-latency tax on the primary platform) · **Effort:** S · **Silent-wrongness:** no (mechanical)

## FR-01 — `_execution_context.py:590-623` (`_windows_current_rss_bytes`)

### Evidence
`_windows_current_rss_bytes` does all of the following **inside the function body, per call**:

- defines `class ProcessMemoryCountersEx(ctypes.Structure)` (~594-607) — a new class object per call;
- calls `windll_factory("kernel32.dll", ...)` and `windll_factory("psapi.dll", ...)` (~615-616), where
  `windll_factory = ctypes.WinDLL`. Calling the constructor directly (unlike the cached
  `ctypes.windll.kernel32` accessor) performs a fresh `LoadLibrary` and builds a new library object
  each time;
- re-assigns `argtypes`/`restype` for `GetCurrentProcess`/`GetProcessMemoryInfo` (~618-623) per call.

`current_rss_bytes` is the default `memory_sampler` for `ExecutionContext`. It is invoked at least
twice per `stage()` (:724, :759), once per `checkpoint()` (:706), and by memory-budget checks. A
preview of a many-node graph performs **hundreds of samples per click**, each paying two
`LoadLibrary` calls plus ctypes class/signature construction. The project's stated platform is win32,
so this cost lands on every interactive operation.

### Fix design
Hoist all per-call state to module level, lazily initialised once:

- Move `ProcessMemoryCountersEx` to module scope.
- Memoise the two DLL handles and the configured function pointers on first use (a module-level
  `_WINDOWS_PSAPI_STATE: tuple | None = None` populated under a lock, or simply computed eagerly inside
  an `if sys.platform == "win32"` guard at import — either is fine, but keep the existing
  `windll_factory` injection seam working for tests: memoise **per factory** or reset the memo when a
  non-default factory is passed, so tests that inject a fake factory still exercise the path).
- Per-sample cost collapses to one struct allocation + one FFI call.

Do **not** change the fallback semantics (what happens when psapi is unavailable) — only the caching.

### TDD plan
1. Failing test: monkeypatch/spy the `windll_factory` seam, call `current_rss_bytes()` (or the
   windows sampler directly) 5×, assert the factory was invoked at most once per DLL (currently it is
   invoked 5× per DLL → test fails before the fix).
2. Keep/extend the existing sampler unit tests (there is prior coverage for the HANDLE restype fix —
   see the comment about c_int truncation at `_polars_utils.py:_malloc_trim` for the sibling pattern);
   assert the returned RSS is still a positive int after the change.
3. Non-Windows CI: the test must inject the factory (don't require real kernel32), mirroring however
   the existing tests for this function do it.

### Acceptance
- Factory called once per DLL per process (per injected factory), not per sample.
- All existing `_execution_context` tests pass unchanged.

## FR-01b (LOW, optional, same file) — Linux sampler
`_linux_current_rss_bytes` (~:574) re-opens and line-scans `/proc/self/status` per sample;
`/proc/self/statm` field 2 × page size avoids the scan. Only do this if trivially safe; keep the
status-file parse as the fallback.
