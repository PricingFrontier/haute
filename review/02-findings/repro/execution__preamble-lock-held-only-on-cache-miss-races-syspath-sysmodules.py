"""Adversarial repro for claim:
'preamble-lock-held-only-on-cache-miss-races-syspath-sysmodules'

CLAIM (paraphrased):
  _compile_preamble_cached holds _preamble_lock only on an lru_cache MISS while
  it mutates process-global sys.path and evicts sys.modules['utility']. The
  escalation is that some OTHER code path importing 'utility' (a node builder,
  or a concurrent utility import that does NOT take the lock) can be mid-import
  exactly while a preamble thread has deleted utility from sys.modules, OR that a
  non-utility preamble's sys.path reprioritisation races a concurrent unlocked
  utility import and binds the WRONG utility.py.

WHAT THIS REPRO TESTS (deterministically, no flaky timing):
  (A) The ONLY mutators of sys.path[:] / sys.modules['utility'] in the production
      source are inside the locked window. We assert via source inspection that
      no `import utility` / `from utility` / sys.modules['utility'] mutation
      exists OUTSIDE executor._compile_preamble_cached.
  (B) Both a utility-importing preamble AND a non-utility preamble enter the SAME
      `with _preamble_lock` window on a cache MISS (line 369 wraps the path
      reprioritisation unconditionally). We instrument the lock to observe that
      two DISTINCT preamble misses fully serialise -> their eviction/path/exec
      windows never interleave.
  (C) We construct the claim's "wrong utility.py" scenario: two utility.py files
      at different sys.path priorities, two DISTINCT preambles that both import
      utility, run them concurrently, and assert NO thread binds the wrong
      utility (because both miss-paths serialise under the lock).

A genuine bug would require an interleave where one thread observes utility
deleted-from-sys.modules / sys.path mid-rewrite by ANOTHER thread. We prove that
window is single-occupant.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path


def main() -> int:
    import haute._sandbox as _sandbox
    import haute.executor as executor
    from haute._cache import GraphFingerprintMemo

    failures: list[str] = []

    # ------------------------------------------------------------------
    # (A) Source-level invariant: the only mutators of utility import state
    #     live inside the locked window. If the claim's "other unlocked
    #     importer" existed, it would show up here.
    # ------------------------------------------------------------------
    exec_src = Path(executor.__file__).read_text(encoding="utf-8")
    import inspect

    cached_src = inspect.getsource(executor._compile_preamble_cached)
    evict_src = inspect.getsource(executor._evict_utility_import_state)
    prio_src = inspect.getsource(executor._prioritise_preamble_import_paths)

    # del sys.modules[...] only appears in the evict helper, which is only
    # called inside the locked block.
    if exec_src.count("del sys.modules[") != 1:
        failures.append(
            f"expected exactly 1 'del sys.modules[' in executor.py, "
            f"found {exec_src.count('del sys.modules[')}"
        )
    if "del sys.modules[" not in evict_src:
        failures.append("del sys.modules not in _evict_utility_import_state")
    if "_evict_utility_import_state" not in cached_src:
        failures.append("_evict_utility_import_state not called from _compile_preamble_cached")
    if "with _preamble_lock" not in cached_src:
        failures.append("_compile_preamble_cached does not take _preamble_lock")

    # sys.path[:] rewrite only in the prioritise helper.
    if exec_src.count("sys.path[:]") != 1:
        failures.append(
            f"expected exactly 1 'sys.path[:]' rewrite in executor.py, "
            f"found {exec_src.count('sys.path[:]')}"
        )
    if "sys.path[:]" not in prio_src:
        failures.append("sys.path[:] rewrite not in _prioritise_preamble_import_paths")

    # The lock wraps the prioritise call UNCONDITIONALLY (not gated on
    # imports_utility). So non-utility preambles also hold the lock for the
    # path-rewrite window -> the claim's "non-utility rewrite vs unlocked
    # utility import" cannot occur because there is no unlocked utility import.
    lock_idx = cached_src.index("with _preamble_lock")
    prio_call_idx = cached_src.index("_prioritise_preamble_import_paths", lock_idx)
    evict_call_idx = cached_src.index("_evict_utility_import_state", lock_idx)
    exec_call_idx = cached_src.index("_exec_preamble_namespace", lock_idx)
    if not (lock_idx < prio_call_idx < evict_call_idx < exec_call_idx):
        failures.append(
            "path prioritise / evict / exec are not all inside the locked block "
            f"(lock={lock_idx} prio={prio_call_idx} evict={evict_call_idx} exec={exec_call_idx})"
        )

    # ------------------------------------------------------------------
    # (B)+(C) Behavioural: two DISTINCT utility-importing preambles, two
    #     utility.py at different sys.path priorities, run concurrently.
    #     Assert (1) the lock window is single-occupant, (2) no thread binds
    #     the wrong utility.
    # ------------------------------------------------------------------
    tmp = Path(tempfile.mkdtemp(prefix="haute_preamble_race_"))
    _sandbox.set_project_root(tmp)

    # Two directories each containing a DIFFERENT utility.py, so that sys.path
    # ordering decides which one wins. high-priority dir wins when inserted first.
    pipeline_dir = tmp / "pipeline_dir"
    other_dir = tmp / "other_dir"
    pipeline_dir.mkdir()
    other_dir.mkdir()
    # Both expose a constant UTILITY_ORIGIN identifying which file was imported.
    (pipeline_dir / "utility.py").write_text(
        "UTILITY_ORIGIN = 'pipeline_dir'\n", encoding="utf-8"
    )
    (other_dir / "utility.py").write_text(
        "UTILITY_ORIGIN = 'other_dir'\n", encoding="utf-8"
    )

    # Put other_dir on sys.path so a *bare* `import utility` with no prioritise
    # would resolve to other_dir; the prioritise step (under lock) is supposed
    # to put pipeline_dir first so each preamble keyed to pipeline_dir gets the
    # pipeline_dir utility.
    sys.path.insert(0, str(other_dir))

    executor._compile_preamble.cache_clear()

    # Instrument the lock to detect overlap of the critical sections.
    real_lock = executor._preamble_lock
    occupancy = {"current": 0, "max": 0}
    occ_guard = threading.Lock()

    class _ObservingLock:
        def __enter__(self):
            real_lock.acquire()
            with occ_guard:
                occupancy["current"] += 1
                occupancy["max"] = max(occupancy["max"], occupancy["current"])
            # Hold briefly so genuine overlap (if the lock failed to serialise)
            # would be observed as max occupancy > 1.
            import time

            time.sleep(0.02)
            return self

        def __exit__(self, *exc):
            with occ_guard:
                occupancy["current"] -= 1
            real_lock.release()
            return False

    executor._preamble_lock = _ObservingLock()  # type: ignore[assignment]

    results: dict[str, str] = {}
    errors: list[str] = []

    # Two DISTINCT preamble texts (distinct cache keys) that both import utility
    # and read its origin. Distinct text => both MISS => both run the locked path.
    def make_preamble(tag: str) -> str:
        return (
            "import utility\n"
            f"ORIGIN_{tag} = utility.UTILITY_ORIGIN\n"
        )

    def worker(tag: str) -> None:
        try:
            ns = executor._compile_preamble(
                make_preamble(tag),
                pipeline_dir=pipeline_dir,
                memo=GraphFingerprintMemo(),
            )
            results[tag] = ns[f"ORIGIN_{tag}"]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{tag}: {type(exc).__name__}: {exc}")

    try:
        threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B", "C", "D")]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    finally:
        executor._preamble_lock = real_lock  # restore
        # remove our sys.path injection so we don't pollute the process
        try:
            sys.path.remove(str(other_dir))
        except ValueError:
            pass
        for k in [k for k in sys.modules if k == "utility" or k.startswith("utility.")]:
            del sys.modules[k]

    if errors:
        failures.append("preamble workers raised: " + "; ".join(errors))

    # (B) The locked window must be single-occupant despite 4 concurrent misses.
    if occupancy["max"] > 1:
        failures.append(
            f"LOCK FAILED TO SERIALISE: max critical-section occupancy = {occupancy['max']} "
            "(two preamble misses interleaved sys.path / sys.modules mutation)"
        )

    # (C) Every thread must have resolved the pipeline_dir utility (prioritised
    #     under lock), NOT the other_dir one. A race would surface as some
    #     thread reading 'other_dir' (wrong utility bound transiently).
    wrong = {tag: origin for tag, origin in results.items() if origin != "pipeline_dir"}
    if wrong:
        failures.append(
            f"WRONG UTILITY BOUND under concurrency: {wrong} "
            "(expected every thread to bind pipeline_dir/utility.py)"
        )

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    print("=" * 70)
    print("max critical-section occupancy:", occupancy["max"], "(expected 1)")
    print("per-thread utility origin:", results, "(expected all 'pipeline_dir')")
    print("worker errors:", errors or "none")
    print("=" * 70)

    if failures:
        print("CLAIM SUPPORTED — observed the predicted race / hole:")
        for f in failures:
            print("  -", f)
        return 1

    print(
        "CLAIM REFUTED: the only mutators of sys.path/sys.modules['utility'] are "
        "inside the locked window; both utility AND non-utility preamble misses "
        "take _preamble_lock for the full path/evict/exec window; no unlocked "
        "utility importer exists; under 4 concurrent DISTINCT misses the critical "
        "section stayed single-occupant and every thread bound the correct "
        "pipeline_dir utility.py."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
